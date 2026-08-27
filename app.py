#!/usr/bin/env python3
"""Investor Lab local API and web server. Zero third-party dependencies."""

from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4
from zoneinfo import ZoneInfo

from investor_lab.portfolio_math import (
    asset_multiplier as _asset_multiplier,
    calculate_positions,
    position_value_micros as _position_value_micros,
)
from investor_lab.api_contract import API_CONTRACT_VERSION, contract_document
from investor_lab.market_quality import (
    assess_daily_bars,
    compare_prices,
    intraday_coverage,
    option_snapshot_quality,
)
from investor_lab.security import (
    CONTENT_SECURITY_POLICY,
    RequestRateLimiter,
    append_security_event,
    client_address,
    identity_hash,
    read_security_events,
    record_login_event,
)


def _configure_tls_ca_environment(
    platform_name: str | None = None, ca_bundle: Path | None = None
) -> None:
    """Use the maintained macOS trust bundle when python.org lacks its own."""
    if (platform_name or sys.platform) != "darwin" or os.environ.get("SSL_CERT_FILE"):
        return
    bundle = ca_bundle or Path("/etc/ssl/cert.pem")
    if bundle.is_file():
        os.environ["SSL_CERT_FILE"] = str(bundle)


_configure_tls_ca_environment()


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "investor-lab.sqlite3"
DEFAULT_WEB_ROOT = ROOT / "web"
SCALE = Decimal("1000000")
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,31}$")
OCC_OPTION_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
SCHEMA_VERSION = 17
APP_VERSION = "0.2.0"
DECISION_MODEL_VERSION = "decision-v4.1"
STRATEGY_FREEZE_PROTOCOL = "full-context-v1"
PORTFOLIO_CALCULATION_VERSION = "portfolio-v2-option-contract-multiplier"
SESSION_DAYS = 30
LOGIN_WINDOW_MINUTES = 15
LOGIN_ATTEMPT_LIMIT = 8
PASSWORD_HASH_SEMAPHORE = threading.BoundedSemaphore(4)
STATIC_ASSETS = {
    "/assets/app.css": ("app.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/design-system.css": ("design-system.css", "text/css; charset=utf-8"),
    "/assets/design-system.js": ("design-system.js", "text/javascript; charset=utf-8"),
    "/assets/investor-lab-ui.css": ("investor-lab-ui.css", "text/css; charset=utf-8"),
    "/assets/investor-lab-ui.js": ("investor-lab-ui.js", "text/javascript; charset=utf-8"),
    "/assets/investor-lab-logo.png": ("investor-lab-logo.png", "image/png"),
}
ALPHA_VANTAGE_KEYCHAIN_SERVICE = "org.investorlab.alpha-vantage"
ALPACA_KEY_ID_KEYCHAIN_SERVICE = "org.investorlab.alpaca-key-id"
ALPACA_SECRET_KEYCHAIN_SERVICE = "org.investorlab.alpaca-secret-key"
NEW_YORK = ZoneInfo("America/New_York")
DB_MAINTENANCE_LOCK = threading.Lock()
SCHEDULER_STATE: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "last_cycle_at": None,
    "last_error": None,
}


class InputError(ValueError):
    pass


class ApiError(Exception):
    def __init__(
        self, status: int, message: str, headers: dict[str, str] | None = None
    ):
        super().__init__(message)
        self.status = status
        self.headers = headers or {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def future_iso(days: int) -> str:
    value = datetime.now(timezone.utc) + timedelta(days=days)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_york_date(value: str) -> date:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise InputError("Stored timestamp is not valid ISO-8601.") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(NEW_YORK).date()


def normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise InputError("Symbol must be 1-32 letters, numbers, dots, or hyphens.")
    return symbol


def normalize_email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise InputError("Enter a valid email address.")
    return email


def validate_password(value: Any) -> str:
    password = str(value or "")
    if len(password) < 12 or len(password) > 128:
        raise InputError("Password must be 12-128 characters.")
    if password.lower() == password or not any(character.isdigit() for character in password):
        raise InputError("Password must include an uppercase letter and a number.")
    return password


def validate_display_name(value: Any) -> str:
    name = str(value or "").strip()
    if not 1 <= len(name) <= 80:
        raise InputError("Display name must be 1-80 characters.")
    return name


def to_micros(value: Any, field: str) -> int:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise InputError(f"{field} must be a number.") from None
    scaled = decimal_value * SCALE
    if (
        not decimal_value.is_finite()
        or decimal_value <= 0
        or scaled != scaled.to_integral_value()
    ):
        raise InputError(f"{field} must be positive with at most 6 decimal places.")
    return int(scaled)


def decimal_string(micros: int) -> str:
    value = Decimal(micros) / SCALE
    return format(value.normalize(), "f")


def to_nonnegative_micros(value: Any, field: str) -> int:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise InputError(f"{field} must be a number.") from None
    scaled = decimal_value * SCALE
    if not decimal_value.is_finite() or decimal_value < 0 or scaled != scaled.to_integral_value():
        raise InputError(f"{field} must be zero or greater with at most 6 decimal places.")
    return int(scaled)


def decimal_parameter(
    value: Any, field: str, *, minimum: Decimal, maximum: Decimal
) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise InputError(f"{field} must be a number.") from None
    if not result.is_finite() or result < minimum or result > maximum:
        raise InputError(f"{field} must be between {minimum} and {maximum}.")
    return result


def validate_hypothesis(value: Any) -> str:
    hypothesis = str(value or "").strip()
    if not 1 <= len(hypothesis) <= 500:
        raise InputError("Hypothesis must be 1-500 characters.")
    return hypothesis


def open_db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_db(path) as db:
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version == 0:
            db.executescript(
                """
            CREATE TABLE IF NOT EXISTS watchlist (
                symbol TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                asset_type TEXT NOT NULL CHECK (asset_type IN ('equity', 'option')),
                side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
                quantity_micros INTEGER NOT NULL CHECK (quantity_micros > 0),
                price_micros INTEGER NOT NULL CHECK (price_micros > 0),
                executed_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS trades_symbol_time
                ON trades(symbol, executed_at, id);
            PRAGMA user_version = 1;
                """
            )
            version = 1
        if version == 1:
            _migrate_v1_to_v2(db)
            version = 2
        if version == 2:
            _migrate_v2_to_v3(db)
            version = 3
        if version == 3:
            _migrate_v3_to_v4(db)
            version = 4
        if version == 4:
            _migrate_v4_to_v5(db)
            version = 5
        if version == 5:
            _migrate_v5_to_v6(db)
            version = 6
        if version == 6:
            _migrate_v6_to_v7(db)
            version = 7
        if version == 7:
            _migrate_v7_to_v8(db)
            version = 8
        if version == 8:
            _migrate_v8_to_v9(db)
            version = 9
        if version == 9:
            _migrate_v9_to_v10(db)
            version = 10
        if version == 10:
            _migrate_v10_to_v11(db)
            version = 11
        if version == 11:
            _migrate_v11_to_v12(db)
            version = 12
        if version == 12:
            _migrate_v12_to_v13(db)
            version = 13
        if version == 13:
            _migrate_v13_to_v14(db)
            version = 14
        if version == 14:
            _migrate_v14_to_v15(db)
            version = 15
        if version == 15:
            _migrate_v15_to_v16(db)
            version = 16
        if version == 16:
            _migrate_v16_to_v17(db)
            version = 17
        if version != SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported database schema version {version}.")


def _migrate_v1_to_v2(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        ALTER TABLE watchlist RENAME TO watchlist_v1_archive;
        ALTER TABLE trades RENAME TO trades_v1_archive;

        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            password_salt BLOB NOT NULL,
            password_hash BLOB NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            csrf_token TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE TABLE devices (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            platform TEXT NOT NULL CHECK (platform IN ('web', 'ios')),
            last_revision INTEGER NOT NULL DEFAULT 0 CHECK (last_revision >= 0),
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            UNIQUE(user_id, id)
        );

        CREATE TABLE watchlist (
            id TEXT PRIMARY KEY,
            user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, symbol)
        );

        CREATE TABLE trades (
            id TEXT PRIMARY KEY,
            user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            asset_type TEXT NOT NULL CHECK (asset_type IN ('equity', 'option')),
            side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
            quantity_micros INTEGER NOT NULL CHECK (quantity_micros > 0),
            price_micros INTEGER NOT NULL CHECK (price_micros > 0),
            executed_at TEXT NOT NULL
        );

        CREATE TABLE sync_events (
            revision INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            operation TEXT NOT NULL CHECK (operation IN ('upsert', 'delete', 'bootstrap')),
            payload_json TEXT,
            changed_at TEXT NOT NULL
        );

        CREATE TABLE failed_logins (
            key_hash TEXT NOT NULL,
            attempted_at TEXT NOT NULL
        );

        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );

        INSERT INTO watchlist(id, user_id, symbol, created_at)
            SELECT 'legacy-watch-' || rowid, NULL, symbol, created_at
            FROM watchlist_v1_archive;
        INSERT INTO trades(id, user_id, symbol, asset_type, side, quantity_micros, price_micros, executed_at)
            SELECT id, NULL, symbol, asset_type, side, quantity_micros, price_micros, executed_at
            FROM trades_v1_archive;

        CREATE INDEX watchlist_user_time ON watchlist(user_id, created_at, symbol);
        CREATE INDEX trades_user_symbol_time ON trades(user_id, symbol, executed_at, id);
        CREATE INDEX sync_events_user_revision ON sync_events(user_id, revision);
        CREATE INDEX sessions_user_expiry ON sessions(user_id, expires_at);
        CREATE INDEX failed_logins_key_time ON failed_logins(key_hash, attempted_at);

        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (2, 'accounts-and-sync', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 2;
        COMMIT;
        """
    )


def _migrate_v2_to_v3(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE market_daily (
            symbol TEXT NOT NULL,
            trading_date TEXT NOT NULL,
            open_micros INTEGER NOT NULL CHECK (open_micros > 0),
            high_micros INTEGER NOT NULL CHECK (high_micros > 0),
            low_micros INTEGER NOT NULL CHECK (low_micros > 0),
            close_micros INTEGER NOT NULL CHECK (close_micros > 0),
            volume INTEGER NOT NULL CHECK (volume >= 0),
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY(symbol, trading_date, source)
        );
        CREATE INDEX market_daily_symbol_date
            ON market_daily(symbol, trading_date DESC);
        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (3, 'daily-market-cache', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 3;
        COMMIT;
        """
    )


def _migrate_v3_to_v4(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE research_plans (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('day_trade', 'options')),
            symbol TEXT NOT NULL,
            hypothesis TEXT NOT NULL,
            inputs_json TEXT NOT NULL,
            analysis_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX research_plans_user_kind_time
            ON research_plans(user_id, kind, created_at DESC, id);
        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (4, 'research-planning-ledger', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 4;
        COMMIT;
        """
    )


def _migrate_v4_to_v5(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE journal_entries (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('note', 'review', 'lesson')),
            setup_tag TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('open', 'win', 'loss', 'scratch', 'na')),
            discipline_score INTEGER CHECK (discipline_score BETWEEN 1 AND 5),
            created_at TEXT NOT NULL
        );
        CREATE INDEX journal_entries_user_time
            ON journal_entries(user_id, created_at DESC, id);
        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (5, 'journal-and-derived-risk', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 5;
        COMMIT;
        """
    )


def _migrate_v5_to_v6(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE price_alerts (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('above', 'below')),
            threshold_micros INTEGER NOT NULL CHECK (threshold_micros > 0),
            is_triggered INTEGER NOT NULL DEFAULT 0 CHECK (is_triggered IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, symbol, direction, threshold_micros)
        );
        CREATE INDEX price_alerts_user_time
            ON price_alerts(user_id, created_at DESC, id);
        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (6, 'price-alert-crossings', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 6;
        COMMIT;
        """
    )


def _migrate_v6_to_v7(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE investor_profiles (
            user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            strategy_style TEXT NOT NULL CHECK (strategy_style IN ('balanced', 'growth', 'value', 'income', 'momentum')),
            time_horizon TEXT NOT NULL CHECK (time_horizon IN ('day', 'swing', 'long_term')),
            paper_account_micros INTEGER NOT NULL CHECK (paper_account_micros > 0),
            max_position_percent_micros INTEGER NOT NULL CHECK (max_position_percent_micros > 0),
            risk_per_trade_percent_micros INTEGER NOT NULL CHECK (risk_per_trade_percent_micros > 0),
            minimum_reward_risk_micros INTEGER NOT NULL CHECK (minimum_reward_risk_micros > 0),
            daily_loss_limit_micros INTEGER NOT NULL CHECK (daily_loss_limit_micros > 0),
            options_defined_risk_only INTEGER NOT NULL CHECK (options_defined_risk_only IN (0, 1)),
            updated_at TEXT NOT NULL
        );
        INSERT INTO investor_profiles(
            user_id, strategy_style, time_horizon, paper_account_micros,
            max_position_percent_micros, risk_per_trade_percent_micros,
            minimum_reward_risk_micros, daily_loss_limit_micros,
            options_defined_risk_only, updated_at
        )
        SELECT id, 'balanced', 'swing', 25000000000, 10000000, 500000,
               2000000, 300000000, 1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
        FROM users;
        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (7, 'investor-profile-and-export', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 7;
        COMMIT;
        """
    )


def _migrate_v7_to_v8(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE plan_reviews (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            plan_id TEXT NOT NULL REFERENCES research_plans(id) ON DELETE CASCADE,
            decision TEXT NOT NULL CHECK (decision IN ('followed', 'skipped', 'invalidated', 'expired')),
            outcome TEXT NOT NULL CHECK (outcome IN ('open', 'win', 'loss', 'scratch', 'na')),
            discipline_score INTEGER CHECK (discipline_score BETWEEN 1 AND 5),
            note TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX plan_reviews_user_plan_time
            ON plan_reviews(user_id, plan_id, created_at DESC, id);
        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (8, 'plan-review-loop', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 8;
        COMMIT;
        """
    )


def _migrate_v8_to_v9(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        ALTER TABLE sessions ADD COLUMN device_id TEXT;
        CREATE INDEX sessions_user_device ON sessions(user_id, device_id);
        CREATE TABLE portfolio_imports (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            fingerprint TEXT NOT NULL,
            filename TEXT NOT NULL,
            row_count INTEGER NOT NULL CHECK (row_count > 0),
            created_at TEXT NOT NULL,
            UNIQUE(user_id, fingerprint)
        );
        CREATE INDEX portfolio_imports_user_time
            ON portfolio_imports(user_id, created_at DESC, id);
        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (9, 'imports-device-sessions-and-health', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 9;
        COMMIT;
        """
    )


def _migrate_v9_to_v10(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE decision_runs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            model_version TEXT NOT NULL,
            context_hash TEXT NOT NULL,
            signal TEXT NOT NULL CHECK (signal IN (
                'buy_candidate', 'watch', 'avoid', 'hold', 'reduce',
                'sell_review', 'data_required', 'refresh_required'
            )),
            score INTEGER CHECK (score BETWEEN 0 AND 100),
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, symbol, model_version, context_hash)
        );
        CREATE INDEX decision_runs_user_symbol_time
            ON decision_runs(user_id, symbol, created_at DESC, id);
        CREATE TABLE decision_settings (
            user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            auto_refresh_enabled INTEGER NOT NULL DEFAULT 0 CHECK (auto_refresh_enabled IN (0, 1)),
            refresh_interval_hours INTEGER NOT NULL DEFAULT 24 CHECK (refresh_interval_hours BETWEEN 12 AND 168),
            last_refresh_at TEXT,
            updated_at TEXT NOT NULL
        );
        INSERT INTO decision_settings(user_id, auto_refresh_enabled, refresh_interval_hours, updated_at)
            SELECT id, 0, 24, strftime('%Y-%m-%dT%H:%M:%SZ', 'now') FROM users;
        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (10, 'decision-engine-history-and-scheduling', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 10;
        COMMIT;
        """
    )


def _migrate_v10_to_v11(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE sec_cache (
            cache_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        );
        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (11, 'sec-edgar-fundamentals', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 11;
        COMMIT;
        """
    )


def _migrate_v11_to_v12(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE strategy_templates (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            technical_weight INTEGER NOT NULL CHECK (technical_weight BETWEEN 0 AND 100),
            fundamental_weight INTEGER NOT NULL CHECK (fundamental_weight BETWEEN 0 AND 100),
            valuation_weight INTEGER NOT NULL CHECK (valuation_weight BETWEEN 0 AND 100),
            portfolio_weight INTEGER NOT NULL CHECK (portfolio_weight BETWEEN 0 AND 100),
            fee_slippage_bps INTEGER NOT NULL CHECK (fee_slippage_bps BETWEEN 0 AND 500),
            is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, name)
        );
        CREATE INDEX strategy_templates_user_time
            ON strategy_templates(user_id, updated_at DESC, id);
        CREATE UNIQUE INDEX strategy_templates_one_active
            ON strategy_templates(user_id) WHERE is_active = 1;
        ALTER TABLE plan_reviews ADD COLUMN actual_entry_micros INTEGER;
        ALTER TABLE plan_reviews ADD COLUMN actual_exit_micros INTEGER;
        ALTER TABLE plan_reviews ADD COLUMN screenshot_data_url TEXT;
        ALTER TABLE plan_reviews ADD COLUMN execution_note TEXT NOT NULL DEFAULT '';
        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (12, 'strategy-portfolio-and-day-trade-suite', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 12;
        COMMIT;
        """
    )


def _migrate_v12_to_v13(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE intraday_bars (
            symbol TEXT NOT NULL,
            bar_timestamp TEXT NOT NULL,
            timeframe TEXT NOT NULL CHECK (timeframe IN ('1Min', '5Min')),
            open_micros INTEGER NOT NULL CHECK (open_micros > 0),
            high_micros INTEGER NOT NULL CHECK (high_micros > 0),
            low_micros INTEGER NOT NULL CHECK (low_micros > 0),
            close_micros INTEGER NOT NULL CHECK (close_micros > 0),
            volume INTEGER NOT NULL CHECK (volume >= 0),
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY(symbol, bar_timestamp, timeframe, source)
        );
        CREATE INDEX intraday_bars_symbol_time
            ON intraday_bars(symbol, timeframe, bar_timestamp DESC);
        CREATE TABLE option_chain_snapshots (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            feed TEXT NOT NULL,
            underlying_price_micros INTEGER,
            atm_iv_percent_micros INTEGER,
            result_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        );
        CREATE INDEX option_chain_snapshots_user_symbol_time
            ON option_chain_snapshots(user_id, symbol, fetched_at DESC, id);
        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (13, 'data-quality-validation-options-intraday', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 13;
        COMMIT;
        """
    )


def _migrate_v13_to_v14(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE data_collection_runs (
            id TEXT PRIMARY KEY,
            user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
            job_type TEXT NOT NULL CHECK (job_type IN ('watchlist_refresh', 'intraday_scan', 'manual_backup')),
            status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'partial', 'failed')),
            requested_count INTEGER NOT NULL DEFAULT 0 CHECK (requested_count >= 0),
            completed_count INTEGER NOT NULL DEFAULT 0 CHECK (completed_count >= 0),
            result_json TEXT NOT NULL DEFAULT '{}',
            error_text TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE INDEX data_collection_runs_user_time
            ON data_collection_runs(user_id, started_at DESC, id);
        CREATE TABLE market_adjustments (
            symbol TEXT NOT NULL,
            trading_date TEXT NOT NULL,
            adjusted_close_micros INTEGER NOT NULL CHECK (adjusted_close_micros > 0),
            dividend_micros INTEGER NOT NULL CHECK (dividend_micros >= 0),
            split_coefficient_micros INTEGER NOT NULL CHECK (split_coefficient_micros > 0),
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY(symbol, trading_date, source)
        );
        CREATE INDEX market_adjustments_symbol_date
            ON market_adjustments(symbol, trading_date DESC);
        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (14, 'automation-validation-and-operations', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 14;
        COMMIT;
        """
    )


def _migrate_v14_to_v15(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        ALTER TABLE data_collection_runs RENAME TO data_collection_runs_v14;
        CREATE TABLE data_collection_runs (
            id TEXT PRIMARY KEY,
            user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'partial', 'failed')),
            requested_count INTEGER NOT NULL DEFAULT 0 CHECK (requested_count >= 0),
            completed_count INTEGER NOT NULL DEFAULT 0 CHECK (completed_count >= 0),
            result_json TEXT NOT NULL DEFAULT '{}',
            error_text TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            finished_at TEXT
        );
        INSERT INTO data_collection_runs
            SELECT * FROM data_collection_runs_v14;
        DROP TABLE data_collection_runs_v14;
        CREATE INDEX data_collection_runs_user_time
            ON data_collection_runs(user_id, started_at DESC, id);

        CREATE TABLE strategy_versions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            template_id TEXT REFERENCES strategy_templates(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            version_number INTEGER NOT NULL CHECK (version_number > 0),
            config_hash TEXT NOT NULL,
            config_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            activated_at TEXT,
            UNIQUE(user_id, name, version_number)
        );
        CREATE INDEX strategy_versions_user_time
            ON strategy_versions(user_id, created_at DESC, id);
        INSERT INTO strategy_versions(
            id, user_id, template_id, name, version_number, config_hash,
            config_json, created_at, activated_at
        )
        SELECT
            lower(hex(randomblob(16))), user_id, id, name, 1,
            lower(hex(randomblob(32))),
            json_object(
                'technical_weight', technical_weight,
                'fundamental_weight', fundamental_weight,
                'valuation_weight', valuation_weight,
                'portfolio_weight', portfolio_weight,
                'fee_slippage_bps', fee_slippage_bps
            ), created_at, CASE WHEN is_active = 1 THEN updated_at ELSE NULL END
        FROM strategy_templates;

        CREATE TABLE paper_account_snapshots (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            account_status TEXT NOT NULL,
            equity_micros INTEGER,
            cash_micros INTEGER,
            buying_power_micros INTEGER,
            position_count INTEGER NOT NULL DEFAULT 0,
            open_order_count INTEGER NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        );
        CREATE INDEX paper_account_snapshots_user_time
            ON paper_account_snapshots(user_id, fetched_at DESC, id);

        CREATE TABLE day_trade_alert_states (
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            alert_key TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('ready', 'waiting', 'blocked')),
            last_notified_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, alert_key)
        );

        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (15, 'strategy-versions-recovery-paper-sync-and-live-monitoring', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 15;
        COMMIT;
        """
    )


def _migrate_v15_to_v16(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE paper_order_controls (
            user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
            max_order_notional_micros INTEGER NOT NULL DEFAULT 1000000000
                CHECK (max_order_notional_micros > 0),
            daily_loss_limit_micros INTEGER NOT NULL DEFAULT 300000000
                CHECK (daily_loss_limit_micros > 0),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE paper_order_intents (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            client_order_id TEXT NOT NULL,
            broker_order_id TEXT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
            order_type TEXT NOT NULL CHECK (order_type IN ('market', 'limit', 'stop', 'stop_limit')),
            time_in_force TEXT NOT NULL CHECK (time_in_force IN ('day', 'gtc')),
            quantity_micros INTEGER NOT NULL CHECK (quantity_micros > 0),
            limit_price_micros INTEGER,
            stop_price_micros INTEGER,
            estimated_notional_micros INTEGER NOT NULL CHECK (estimated_notional_micros > 0),
            status TEXT NOT NULL CHECK (status IN (
                'submitting', 'accepted', 'new', 'partially_filled', 'filled',
                'cancel_pending', 'canceled', 'replaced', 'rejected', 'failed'
            )),
            request_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, client_order_id)
        );
        CREATE INDEX paper_order_intents_user_time
            ON paper_order_intents(user_id, created_at DESC, id);

        CREATE TABLE scanner_presets (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            symbols_json TEXT NOT NULL,
            filters_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, name)
        );
        CREATE INDEX scanner_presets_user_time
            ON scanner_presets(user_id, updated_at DESC, id);

        CREATE TABLE notification_rules (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN (
                'decision', 'filing', 'earnings', 'option_expiration',
                'day_trade', 'data_stale'
            )),
            symbol TEXT,
            config_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            last_triggered_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX notification_rules_user_time
            ON notification_rules(user_id, updated_at DESC, id);

        CREATE TABLE research_reports (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            period TEXT NOT NULL CHECK (period IN ('daily', 'weekly')),
            report_date TEXT NOT NULL,
            content_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, period, report_date)
        );
        CREATE INDEX research_reports_user_time
            ON research_reports(user_id, report_date DESC, id);

        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (16, 'paper-execution-and-research-command-center', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 16;
        COMMIT;
        """
    )


def _migrate_v16_to_v17(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'member'
            CHECK (role IN ('owner', 'member'));
        UPDATE users SET role = 'owner' WHERE id = (
            SELECT id FROM users ORDER BY created_at, id LIMIT 1
        );
        ALTER TABLE sessions ADD COLUMN client_type TEXT NOT NULL DEFAULT 'web'
            CHECK (client_type IN ('web', 'ios'));
        UPDATE sessions SET client_type = COALESCE(
            (SELECT platform FROM devices WHERE devices.id = sessions.device_id),
            'web'
        );
        DELETE FROM sessions WHERE device_id IS NULL;
        CREATE INDEX sessions_client_type ON sessions(user_id, client_type, expires_at);
        INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (17, 'session-transport-owner-and-device-binding', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        PRAGMA user_version = 17;
        COMMIT;
        """
    )


def _hash_password(password: str, salt: bytes) -> bytes:
    with PASSWORD_HASH_SEMAPHORE:
        return hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
        )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _serialize_user(row: sqlite3.Row) -> dict[str, str]:
    return {
        "id": row["id"], "email": row["email"], "display_name": row["display_name"],
        "role": row["role"],
    }


def _append_sync_event(
    db: sqlite3.Connection,
    user_id: str,
    entity_type: str,
    entity_id: str,
    operation: str,
    payload: dict[str, Any] | None,
) -> int:
    cursor = db.execute(
        "INSERT INTO sync_events(user_id, entity_type, entity_id, operation, payload_json, changed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, entity_type, entity_id, operation, json.dumps(payload) if payload else None, now_iso()),
    )
    return int(cursor.lastrowid)


def latest_revision(db: sqlite3.Connection, user_id: str) -> int:
    row = db.execute(
        "SELECT COALESCE(MAX(revision), 0) AS revision FROM sync_events WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return int(row["revision"])


def _session_device(payload: dict[str, Any], client_type: str) -> tuple[str, str]:
    device_id = str(payload.get("device_id") or f"{client_type}-{uuid4()}").strip()
    device_name = str(payload.get("device_name") or f"{client_type.upper()} device").strip()
    if not DEVICE_ID_RE.fullmatch(device_id):
        raise InputError("Device ID must be 8-128 safe characters.")
    if not 1 <= len(device_name) <= 80:
        raise InputError("Device name must be 1-80 characters.")
    return device_id, device_name


def register_user(path: Path, payload: dict[str, Any], allow_additional: bool = False) -> tuple[dict[str, str], dict[str, str]]:
    email = normalize_email(payload.get("email"))
    password = validate_password(payload.get("password"))
    display_name = validate_display_name(payload.get("display_name"))
    client_type = str(payload.get("client") or "web").lower()
    if client_type not in {"web", "ios"}:
        raise InputError("Client must be web or ios.")
    device_id, device_name = _session_device(payload, client_type)
    user_id = str(uuid4())
    salt = secrets.token_bytes(16)
    password_hash = _hash_password(password, salt)
    created_at = now_iso()
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        count = int(db.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        if count and not allow_additional:
            raise ApiError(403, "Additional account registration is disabled on this local server.")
        role = "owner" if count == 0 else "member"
        try:
            db.execute(
                "INSERT INTO users(id, email, display_name, password_salt, password_hash, created_at, role) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, email, display_name, salt, password_hash, created_at, role),
            )
        except sqlite3.IntegrityError:
            raise ApiError(409, "An account with that email already exists.") from None
        db.execute(
            "INSERT INTO investor_profiles(user_id, strategy_style, time_horizon, "
            "paper_account_micros, max_position_percent_micros, "
            "risk_per_trade_percent_micros, minimum_reward_risk_micros, "
            "daily_loss_limit_micros, options_defined_risk_only, updated_at) "
            "VALUES (?, 'balanced', 'swing', 25000000000, 10000000, 500000, "
            "2000000, 300000000, 1, ?)",
            (user_id, created_at),
        )
        db.execute(
            "INSERT INTO decision_settings(user_id, auto_refresh_enabled, "
            "refresh_interval_hours, updated_at) VALUES (?, 0, 24, ?)",
            (user_id, created_at),
        )
        claimed_watchlist = db.execute(
            "UPDATE watchlist SET user_id = ? WHERE user_id IS NULL", (user_id,)
        ).rowcount
        claimed_trades = db.execute(
            "UPDATE trades SET user_id = ? WHERE user_id IS NULL", (user_id,)
        ).rowcount
        _append_sync_event(
            db,
            user_id,
            "account",
            user_id,
            "bootstrap",
            {"claimed_watchlist": claimed_watchlist, "claimed_trades": claimed_trades},
        )
    user = {"id": user_id, "email": email, "display_name": display_name, "role": role}
    return user, create_session(path, user_id, client_type, device_id, device_name)


def _login_rate_key(address: str, email: str) -> str:
    return hashlib.sha256(f"{address}|{email}".encode("utf-8")).hexdigest()


def _login_rate_keys(address: str, email: str) -> tuple[str, str, str]:
    return (
        hashlib.sha256(f"address:{address}".encode("utf-8")).hexdigest(),
        hashlib.sha256(f"email:{email}".encode("utf-8")).hexdigest(),
        _login_rate_key(address, email),
    )


def login_user(path: Path, payload: dict[str, Any], address: str) -> tuple[dict[str, str], dict[str, str]]:
    email = normalize_email(payload.get("email"))
    password = str(payload.get("password") or "")
    client_type = str(payload.get("client") or "web").lower()
    if client_type not in {"web", "ios"}:
        raise InputError("Client must be web or ios.")
    device_id, device_name = _session_device(payload, client_type)
    address_key, email_key, pair_key = _login_rate_keys(address, email)
    rate_keys = (address_key, email_key, pair_key)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=LOGIN_WINDOW_MINUTES)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    with open_db(path) as db:
        db.execute("DELETE FROM failed_logins WHERE attempted_at < ?", (cutoff,))
        counts = {
            str(row["key_hash"]): int(row["attempts"])
            for row in db.execute(
                "SELECT key_hash, COUNT(*) AS attempts FROM failed_logins "
                "WHERE key_hash IN (?, ?, ?) GROUP BY key_hash",
                rate_keys,
            ).fetchall()
        }
        if any(counts.get(key, 0) >= LOGIN_ATTEMPT_LIMIT for key in rate_keys):
            record_login_event(
                path,
                successful=False,
                user_id=str(row["id"]) if (row := db.execute(
                    "SELECT id FROM users WHERE email = ?", (email,)
                ).fetchone()) else None,
                email=email,
                address=address,
                device_id=device_id,
                client_type=client_type,
                rate_limited=True,
            )
            raise ApiError(429, "Too many login attempts. Try again later.")
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    salt = bytes(row["password_salt"]) if row else b"InvestorLabAuth!"
    candidate_hash = _hash_password(password, salt)
    valid = bool(row) and hmac.compare_digest(candidate_hash, bytes(row["password_hash"]))
    if not valid:
        attempted_at = now_iso()
        with open_db(path) as db:
            db.executemany(
                "INSERT INTO failed_logins(key_hash, attempted_at) VALUES (?, ?)",
                [(key, attempted_at) for key in rate_keys],
            )
        record_login_event(
            path,
            successful=False,
            user_id=str(row["id"]) if row else None,
            email=email,
            address=address,
            device_id=device_id,
            client_type=client_type,
        )
        raise ApiError(401, "Invalid email or password.")
    assert row is not None
    user = _serialize_user(row)
    with open_db(path) as db:
        db.execute(
            "DELETE FROM failed_logins WHERE key_hash IN (?, ?)",
            (email_key, pair_key),
        )
    login_security = record_login_event(
        path,
        successful=True,
        user_id=user["id"],
        email=email,
        address=address,
        device_id=device_id,
        client_type=client_type,
    )
    session = create_session(path, user["id"], client_type, device_id, device_name)
    session["unusual_login"] = login_security["unusual_login"]
    if login_security["security_notice"]:
        session["security_notice"] = login_security["security_notice"]
    return user, session


def create_session(
    path: Path,
    user_id: str,
    client_type: str = "web",
    device_id: str | None = None,
    device_name: str | None = None,
) -> dict[str, str]:
    if client_type not in {"web", "ios"}:
        raise InputError("Client must be web or ios.")
    if device_id is None:
        device_id = f"{client_type}-{uuid4()}"
    if not DEVICE_ID_RE.fullmatch(device_id):
        raise InputError("Device ID must be 8-128 safe characters.")
    device_name = str(device_name or f"{client_type.upper()} device").strip()
    if not 1 <= len(device_name) <= 80:
        raise InputError("Device name must be 1-80 characters.")
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(24)
    created_at = now_iso()
    expires_at = future_iso(SESSION_DAYS)
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (created_at,))
        db.execute(
            "INSERT INTO devices(id, user_id, name, platform, created_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "name = excluded.name, last_seen_at = excluded.last_seen_at "
            "WHERE devices.user_id = excluded.user_id AND devices.platform = excluded.platform",
            (device_id, user_id, device_name, client_type, created_at, created_at),
        )
        device = db.execute(
            "SELECT id FROM devices WHERE id = ? AND user_id = ? AND platform = ?",
            (device_id, user_id, client_type),
        ).fetchone()
        if not device:
            raise ApiError(409, "That device ID belongs to another account or client type.")
        db.execute(
            "INSERT INTO sessions(token_hash, user_id, csrf_token, created_at, expires_at, "
            "last_seen_at, device_id, client_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _token_hash(token), user_id, csrf_token, created_at, expires_at,
                created_at, device_id, client_type,
            ),
        )
    return {"access_token": token, "csrf_token": csrf_token, "expires_at": expires_at}


def authenticate_session(path: Path, token: str) -> tuple[dict[str, str], str, str, str]:
    if not token or len(token) > 256:
        raise ApiError(401, "Authentication required.")
    digest = _token_hash(token)
    current = now_iso()
    with open_db(path) as db:
        row = db.execute(
            "SELECT users.id, users.email, users.display_name, users.role, "
            "sessions.csrf_token, sessions.client_type "
            "FROM sessions JOIN users ON users.id = sessions.user_id "
            "WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
            (digest, current),
        ).fetchone()
        if not row:
            raise ApiError(401, "Authentication required.")
        db.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (current, digest))
    return _serialize_user(row), row["csrf_token"], digest, row["client_type"]


def delete_session(path: Path, token_hash: str) -> None:
    with open_db(path) as db:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def require_owner(user: dict[str, str]) -> None:
    if user.get("role") != "owner":
        raise ApiError(403, "This operation is restricted to the local vault owner.")


def _verify_user_password(path: Path, user_id: str, password: str) -> sqlite3.Row:
    with open_db(path) as db:
        row = db.execute(
            "SELECT id, password_salt, password_hash FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not row:
        raise ApiError(404, "Account was not found.")
    candidate = _hash_password(password, bytes(row["password_salt"]))
    if not hmac.compare_digest(candidate, bytes(row["password_hash"])):
        raise ApiError(403, "Password is incorrect.")
    return row


def change_password(path: Path, user_id: str, payload: dict[str, Any]) -> dict[str, bool]:
    current_password = str(payload.get("current_password") or "")
    new_password = validate_password(payload.get("new_password"))
    _verify_user_password(path, user_id, current_password)
    if hmac.compare_digest(current_password, new_password):
        raise InputError("New password must be different from the current password.")
    salt = secrets.token_bytes(16)
    password_hash = _hash_password(new_password, salt)
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
            (salt, password_hash, user_id),
        )
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        _append_sync_event(
            db, user_id, "account_security", user_id, "upsert",
            {"password_changed_at": now_iso()},
        )
    return {"password_changed": True, "reauth_required": True}


def logout_all(path: Path, user_id: str, payload: dict[str, Any]) -> dict[str, bool]:
    _verify_user_password(path, user_id, str(payload.get("current_password") or ""))
    with open_db(path) as db:
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return {"logged_out_all": True}


def _watchlist_rows(db: sqlite3.Connection, user_id: str) -> list[dict[str, str]]:
    rows = db.execute(
        "SELECT symbol, created_at FROM watchlist WHERE user_id = ? ORDER BY created_at, symbol",
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _user_symbols_from_db(db: sqlite3.Connection, user_id: str) -> list[str]:
    return [
        str(row["symbol"])
        for row in db.execute(
            "SELECT symbol FROM watchlist WHERE user_id = ? "
            "UNION SELECT symbol FROM trades WHERE user_id = ? "
            "UNION SELECT symbol FROM decision_runs WHERE user_id = ? "
            "UNION SELECT symbol FROM research_plans WHERE user_id = ? "
            "ORDER BY symbol",
            (user_id, user_id, user_id, user_id),
        ).fetchall()
    ]


def list_watchlist(path: Path, user_id: str) -> list[dict[str, str]]:
    with open_db(path) as db:
        return _watchlist_rows(db, user_id)


def add_watchlist(path: Path, user_id: str, raw_symbol: Any) -> dict[str, str]:
    symbol = normalize_symbol(raw_symbol)
    created_at = now_iso()
    item_id = str(uuid4())
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            "INSERT INTO watchlist(id, user_id, symbol, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, symbol) DO NOTHING",
            (item_id, user_id, symbol, created_at),
        )
        row = db.execute(
            "SELECT id, symbol, created_at FROM watchlist WHERE user_id = ? AND symbol = ?",
            (user_id, symbol),
        ).fetchone()
        result = {"symbol": row["symbol"], "created_at": row["created_at"]}
        if cursor.rowcount:
            _append_sync_event(db, user_id, "watchlist", row["id"], "upsert", result)
    return result


def remove_watchlist(path: Path, user_id: str, raw_symbol: Any) -> bool:
    symbol = normalize_symbol(raw_symbol)
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT id FROM watchlist WHERE user_id = ? AND symbol = ?", (user_id, symbol)
        ).fetchone()
        if not row:
            return False
        cursor = db.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND symbol = ?", (user_id, symbol)
        )
        _append_sync_event(db, user_id, "watchlist", row["id"], "delete", {"symbol": symbol})
    return cursor.rowcount > 0


def _position_rows(db: sqlite3.Connection, user_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT symbol, asset_type, side, quantity_micros, price_micros "
        "FROM trades WHERE user_id = ? ORDER BY rowid",
        (user_id,),
    ).fetchall()


def _portfolio_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    positions = calculate_positions(_position_rows(db, user_id))
    serialized = []
    total_realized = 0
    for state in sorted(
        positions.values(), key=lambda item: (str(item["symbol"]), str(item["asset_type"]))
    ):
        realized = int(state["realized_pnl_micros"])
        total_realized += realized
        if int(state["quantity_micros"]) == 0 and realized == 0:
            continue
        serialized.append(
            {
                "symbol": state["symbol"],
                "asset_type": state["asset_type"],
                "quantity": decimal_string(int(state["quantity_micros"])),
                "average_cost": decimal_string(int(state["average_cost_micros"])),
                "realized_pnl": decimal_string(realized),
            }
        )
    return {"positions": serialized, "realized_pnl": decimal_string(total_realized)}


def portfolio(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        return _portfolio_from_db(db, user_id)


def _trade_rows(db: sqlite3.Connection, user_id: str, limit: int = 20) -> list[dict[str, str]]:
    rows = db.execute(
        "SELECT id, symbol, asset_type, side, quantity_micros, price_micros, executed_at "
        "FROM trades WHERE user_id = ? ORDER BY rowid DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "symbol": row["symbol"],
            "asset_type": row["asset_type"],
            "side": row["side"],
            "quantity": decimal_string(row["quantity_micros"]),
            "price": decimal_string(row["price_micros"]),
            "executed_at": row["executed_at"],
        }
        for row in rows
    ]


def list_trades(path: Path, user_id: str, limit: int = 20) -> list[dict[str, str]]:
    with open_db(path) as db:
        return _trade_rows(db, user_id, limit)


def record_trade(path: Path, user_id: str, payload: dict[str, Any]) -> dict[str, str]:
    symbol = normalize_symbol(payload.get("symbol"))
    side = str(payload.get("side", "")).lower()
    asset_type = str(payload.get("asset_type", "equity")).lower()
    if side not in {"buy", "sell"}:
        raise InputError("Side must be buy or sell.")
    if asset_type not in {"equity", "option"}:
        raise InputError("Asset type must be equity or option.")
    quantity = to_micros(payload.get("quantity"), "Quantity")
    price = to_micros(payload.get("price"), "Price")
    trade_id = str(uuid4())
    executed_at = now_iso()

    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        positions = calculate_positions(_position_rows(db, user_id))
        held = int(positions.get((symbol, asset_type), {}).get("quantity_micros", 0))
        if side == "sell" and quantity > held:
            raise InputError(
                f"Cannot sell {decimal_string(quantity)} {symbol}; "
                f"only {decimal_string(held)} held."
            )
        db.execute(
            "INSERT INTO trades(id, user_id, symbol, asset_type, side, quantity_micros, "
            "price_micros, executed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (trade_id, user_id, symbol, asset_type, side, quantity, price, executed_at),
        )
        result = {
            "id": trade_id,
            "symbol": symbol,
            "asset_type": asset_type,
            "side": side,
            "quantity": decimal_string(quantity),
            "price": decimal_string(price),
            "executed_at": executed_at,
        }
        _append_sync_event(db, user_id, "trade", trade_id, "upsert", result)
    return result


def _parse_portfolio_csv(payload: dict[str, Any]) -> dict[str, Any]:
    filename = str(payload.get("filename") or "portfolio.csv").strip()
    csv_text = payload.get("csv_text")
    if not 1 <= len(filename) <= 255:
        raise InputError("Filename must be 1-255 characters.")
    if not isinstance(csv_text, str) or not csv_text.strip():
        raise InputError("Choose a non-empty CSV file.")
    if len(csv_text.encode("utf-8")) > 750_000:
        raise InputError("CSV file must be 750 KB or smaller.")

    try:
        reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
        raw_fields = reader.fieldnames
    except csv.Error as error:
        raise InputError(f"CSV could not be read: {error}.") from None
    if not raw_fields:
        raise InputError("CSV must include a header row.")
    field_map = {str(field or "").strip().lower(): field for field in raw_fields}
    if "symbol" not in field_map or "quantity" not in field_map:
        raise InputError("CSV headers must include symbol and quantity.")
    cost_key = "average_cost" if "average_cost" in field_map else "price" if "price" in field_map else None
    if not cost_key:
        raise InputError("CSV headers must include average_cost (or price).")

    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    try:
        for line_number, raw in enumerate(reader, start=2):
            if len(rows) >= 500:
                raise InputError("CSV can contain at most 500 positions.")
            symbol = normalize_symbol(raw.get(field_map["symbol"]))
            asset_type = str(raw.get(field_map.get("asset_type", "")) or "equity").strip().lower()
            if asset_type not in {"equity", "option"}:
                raise InputError(f"Row {line_number}: asset_type must be equity or option.")
            key = (symbol, asset_type)
            if key in seen:
                raise InputError(f"Row {line_number}: duplicate position {symbol} ({asset_type}).")
            seen.add(key)
            quantity = to_micros(raw.get(field_map["quantity"]), f"Row {line_number} quantity")
            average_cost = to_micros(raw.get(field_map[cost_key]), f"Row {line_number} average cost")
            rows.append(
                {
                    "symbol": symbol,
                    "quantity": decimal_string(quantity),
                    "average_cost": decimal_string(average_cost),
                    "asset_type": asset_type,
                }
            )
    except csv.Error as error:
        raise InputError(f"CSV could not be read: {error}.") from None
    if not rows:
        raise InputError("CSV must include at least one position.")

    canonical_rows = sorted(rows, key=lambda row: (row["symbol"], row["asset_type"]))
    canonical = json.dumps(canonical_rows, sort_keys=True, separators=(",", ":"))
    total_cost = sum(
        Decimal(row["quantity"])
        * Decimal(row["average_cost"])
        * Decimal(_asset_multiplier(row["asset_type"]))
        for row in rows
    )
    return {
        "filename": filename,
        "row_count": len(rows),
        "total_cost": format(total_cost.normalize(), "f"),
        "rows": rows,
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "warning": "Importing appends buy trades. Review the preview before confirming.",
    }


def preview_portfolio_import(payload: dict[str, Any]) -> dict[str, Any]:
    return _parse_portfolio_csv(payload)


def _portfolio_import_rows(
    db: sqlite3.Connection, user_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            "SELECT id, fingerprint, filename, row_count, created_at FROM portfolio_imports "
            "WHERE user_id = ? ORDER BY created_at DESC, id LIMIT ?",
            (user_id, limit),
        ).fetchall()
    ]


def list_portfolio_imports(path: Path, user_id: str) -> list[dict[str, Any]]:
    with open_db(path) as db:
        return _portfolio_import_rows(db, user_id)


def import_portfolio_csv(path: Path, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    preview = _parse_portfolio_csv(payload)
    import_id = str(uuid4())
    created_at = now_iso()
    trades: list[dict[str, str]] = []
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        duplicate = db.execute(
            "SELECT id FROM portfolio_imports WHERE user_id = ? AND fingerprint = ?",
            (user_id, preview["fingerprint"]),
        ).fetchone()
        if duplicate:
            raise ApiError(409, "This portfolio CSV was already imported.")
        for row in preview["rows"]:
            trade_id = str(uuid4())
            trade = {
                "id": trade_id,
                "symbol": row["symbol"],
                "asset_type": row["asset_type"],
                "side": "buy",
                "quantity": row["quantity"],
                "price": row["average_cost"],
                "executed_at": created_at,
            }
            db.execute(
                "INSERT INTO trades(id, user_id, symbol, asset_type, side, quantity_micros, "
                "price_micros, executed_at) VALUES (?, ?, ?, ?, 'buy', ?, ?, ?)",
                (
                    trade_id,
                    user_id,
                    row["symbol"],
                    row["asset_type"],
                    to_micros(row["quantity"], "Quantity"),
                    to_micros(row["average_cost"], "Average cost"),
                    created_at,
                ),
            )
            _append_sync_event(db, user_id, "trade", trade_id, "upsert", trade)
            trades.append(trade)
        db.execute(
            "INSERT INTO portfolio_imports(id, user_id, fingerprint, filename, row_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                import_id,
                user_id,
                preview["fingerprint"],
                preview["filename"],
                preview["row_count"],
                created_at,
            ),
        )
        result = {
            "id": import_id,
            "fingerprint": preview["fingerprint"],
            "filename": preview["filename"],
            "row_count": preview["row_count"],
            "created_at": created_at,
            "trades": trades,
        }
        _append_sync_event(db, user_id, "portfolio_import", import_id, "upsert", result)
    return result


def _day_trade_worksheet(payload: dict[str, Any]) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    symbol = normalize_symbol(payload.get("symbol"))
    direction = str(payload.get("direction") or "").lower()
    if direction not in {"long", "short"}:
        raise InputError("Direction must be long or short.")
    hypothesis = validate_hypothesis(payload.get("hypothesis"))
    account_size = to_micros(payload.get("account_size"), "Account size")
    entry = to_micros(payload.get("entry"), "Entry")
    stop = to_micros(payload.get("stop"), "Stop")
    target = to_micros(payload.get("target"), "Target")
    daily_loss_limit = to_micros(payload.get("daily_loss_limit"), "Daily loss limit")
    current_daily_loss = to_nonnegative_micros(
        payload.get("current_daily_loss", "0"), "Current daily loss"
    )
    risk_percent = decimal_parameter(
        payload.get("risk_percent"),
        "Risk percent",
        minimum=Decimal("0.01"),
        maximum=Decimal("10"),
    )
    max_position_percent = decimal_parameter(
        payload.get("max_position_percent", "100"),
        "Maximum position percent",
        minimum=Decimal("0.1"),
        maximum=Decimal("100"),
    )
    minimum_reward_risk = decimal_parameter(
        payload.get("minimum_reward_risk", "2"),
        "Minimum reward/risk",
        minimum=Decimal("0.1"),
        maximum=Decimal("20"),
    )
    live_levels: dict[str, Any] = {}
    for key, label in (
        ("premarket_high", "Premarket high"), ("premarket_low", "Premarket low"),
        ("vwap", "VWAP"), ("opening_range_high", "Opening-range high"),
        ("opening_range_low", "Opening-range low"), ("support", "Support"),
        ("resistance", "Resistance"),
    ):
        raw = payload.get(key)
        live_levels[key] = decimal_string(to_micros(raw, label)) if raw not in {None, ""} else None
    halt_status = str(payload.get("halt_status") or "unknown").lower()
    if halt_status not in {"clear", "halted", "unknown"}:
        raise InputError("Halt status must be clear, halted, or unknown.")
    setup_key = str(payload.get("setup_key") or "manual").strip().lower()
    if setup_key not in {"manual", "opening_range_breakout", "vwap_pullback", "premarket_momentum"}:
        raise InputError("Setup key is not supported.")

    if direction == "long":
        if not stop < entry < target:
            raise InputError("A long worksheet requires stop < entry < target.")
        per_share_risk = entry - stop
        per_share_reward = target - entry
    else:
        if not target < entry < stop:
            raise InputError("A short worksheet requires target < entry < stop.")
        per_share_risk = stop - entry
        per_share_reward = entry - target

    requested_budget = int(Decimal(account_size) * risk_percent / Decimal("100"))
    remaining_daily_capacity = max(daily_loss_limit - current_daily_loss, 0)
    effective_budget = min(requested_budget, remaining_daily_capacity)
    risk_based_share_ceiling = effective_budget // per_share_risk
    allocation_budget = int(Decimal(account_size) * max_position_percent / Decimal("100"))
    allocation_share_ceiling = allocation_budget // entry
    maximum_whole_shares = min(risk_based_share_ceiling, allocation_share_ceiling)
    planned_risk = maximum_whole_shares * per_share_risk
    planned_reward = maximum_whole_shares * per_share_reward
    notional = maximum_whole_shares * entry
    reward_risk = Decimal(per_share_reward) / Decimal(per_share_risk)
    blocked_reasons = []
    if remaining_daily_capacity == 0:
        blocked_reasons.append("The supplied daily loss limit has no remaining capacity.")
    if risk_based_share_ceiling < 1:
        blocked_reasons.append("The supplied risk budget is smaller than one share of stop risk.")
    if allocation_share_ceiling < 1:
        blocked_reasons.append("The supplied maximum position is smaller than one share at entry.")
    if halt_status == "halted":
        blocked_reasons.append("The symbol is listed on the current Nasdaq trade-halt feed.")
    plan_status = "blocked" if blocked_reasons else "ready_for_manual_review"
    risk_binding = (
        "daily_loss_limit" if remaining_daily_capacity < requested_budget else "risk_per_trade"
    )
    binding_constraint = (
        "max_position" if allocation_share_ceiling < risk_based_share_ceiling else risk_binding
    )

    inputs = {
        "direction": direction,
        "account_size": decimal_string(account_size),
        "entry": decimal_string(entry),
        "stop": decimal_string(stop),
        "target": decimal_string(target),
        "risk_percent": format(risk_percent.normalize(), "f"),
        "max_position_percent": format(max_position_percent.normalize(), "f"),
        "daily_loss_limit": decimal_string(daily_loss_limit),
        "current_daily_loss": decimal_string(current_daily_loss),
        "minimum_reward_risk": format(minimum_reward_risk.normalize(), "f"),
        "key_levels": live_levels,
        "halt_status": halt_status,
        "setup_key": setup_key,
    }
    analysis = {
        "plan_status": plan_status,
        "mode": "indicative_non_executable",
        "per_share_risk": decimal_string(per_share_risk),
        "per_share_reward": decimal_string(per_share_reward),
        "requested_risk_budget": decimal_string(requested_budget),
        "remaining_daily_capacity": decimal_string(remaining_daily_capacity),
        "effective_risk_budget": decimal_string(effective_budget),
        "maximum_position_value": decimal_string(allocation_budget),
        "risk_based_share_ceiling": risk_based_share_ceiling,
        "allocation_share_ceiling": allocation_share_ceiling,
        "maximum_whole_shares": maximum_whole_shares,
        "binding_constraint": binding_constraint,
        "planned_risk_at_maximum": decimal_string(planned_risk),
        "planned_reward_at_maximum": decimal_string(planned_reward),
        "notional_at_maximum": decimal_string(notional),
        "reward_risk": format(reward_risk.quantize(Decimal("0.01")), "f"),
        "meets_reward_risk_floor": reward_risk >= minimum_reward_risk,
        "blocked_reasons": blocked_reasons,
        "key_levels": live_levels,
        "invalidation_level": decimal_string(stop),
        "halt_status": halt_status,
        "setup_key": setup_key,
        "data_freshness": (
            "User-entered plan enriched with the latest saved IEX real-time levels and Nasdaq halt check."
            if any(live_levels.values()) else "User-entered values; refresh the live plan for IEX levels and Nasdaq halt status."
        ),
        "blocked_actions": ["order placement", "routing", "signing", "submission"],
        "disclaimer": "This is a planning worksheet, not investment or trading advice. Review venue rules and make any trading decisions yourself.",
    }
    return symbol, hypothesis, inputs, analysis


def _option_payoff_micros(legs: list[dict[str, Any]], underlying_micros: int) -> int:
    total = 0
    for leg in legs:
        intrinsic = (
            max(underlying_micros - leg["strike_micros"], 0)
            if leg["right"] == "call"
            else max(leg["strike_micros"] - underlying_micros, 0)
        )
        direction = 1 if leg["side"] == "buy" else -1
        total += direction * (intrinsic - leg["premium_micros"]) * leg["quantity"] * 100
    return total


def _options_worksheet(payload: dict[str, Any]) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    symbol = normalize_symbol(payload.get("symbol"))
    strategy = str(payload.get("strategy") or "").lower()
    allowed = {
        "long_call", "long_put", "bull_call_spread", "bear_put_spread",
        "long_straddle", "iron_condor",
    }
    if strategy not in allowed:
        raise InputError("Strategy must be a supported single-leg, vertical, straddle, or iron-condor structure.")
    hypothesis = validate_hypothesis(payload.get("hypothesis"))
    expiration_text = str(payload.get("expiration") or "").strip()
    try:
        expiration = date.fromisoformat(expiration_text)
    except ValueError:
        raise InputError("Expiration must be a valid YYYY-MM-DD date.") from None
    if expiration < datetime.now(timezone.utc).date():
        raise InputError("Expiration cannot be in the past.")
    try:
        quantity = int(payload.get("quantity"))
    except (TypeError, ValueError):
        raise InputError("Quantity must be a whole number.") from None
    if not 1 <= quantity <= 1000:
        raise InputError("Quantity must be between 1 and 1000 contracts.")

    primary_strike = to_micros(payload.get("primary_strike"), "Primary strike")
    primary_premium = to_micros(payload.get("primary_premium"), "Primary premium")
    secondary_strike = None
    secondary_premium = None
    tertiary_strike = None
    tertiary_premium = None
    quaternary_strike = None
    quaternary_premium = None
    if strategy in {"bull_call_spread", "bear_put_spread", "long_straddle", "iron_condor"}:
        secondary_strike = to_micros(payload.get("secondary_strike"), "Secondary strike")
        secondary_premium = to_micros(payload.get("secondary_premium"), "Secondary premium")
    if strategy == "iron_condor":
        tertiary_strike = to_micros(payload.get("tertiary_strike"), "Third strike")
        tertiary_premium = to_micros(payload.get("tertiary_premium"), "Third premium")
        quaternary_strike = to_micros(payload.get("quaternary_strike"), "Fourth strike")
        quaternary_premium = to_micros(payload.get("quaternary_premium"), "Fourth premium")

    multiplier = 100 * quantity
    legs: list[dict[str, Any]]
    breakevens: list[int]
    net_premium_label = "net debit"
    if strategy == "long_call":
        net_debit_per_share = primary_premium
        max_loss = primary_premium * multiplier
        max_profit = None
        breakeven = primary_strike + primary_premium
        breakevens = [breakeven]
        legs = [{"side": "buy", "right": "call", "strike_micros": primary_strike, "premium_micros": primary_premium, "quantity": quantity}]
    elif strategy == "long_put":
        if primary_premium >= primary_strike:
            raise InputError("Long put premium must be lower than its strike.")
        net_debit_per_share = primary_premium
        max_loss = primary_premium * multiplier
        max_profit = (primary_strike - primary_premium) * multiplier
        breakeven = primary_strike - primary_premium
        breakevens = [breakeven]
        legs = [{"side": "buy", "right": "put", "strike_micros": primary_strike, "premium_micros": primary_premium, "quantity": quantity}]
    elif strategy == "bull_call_spread":
        assert secondary_strike is not None and secondary_premium is not None
        if secondary_strike <= primary_strike:
            raise InputError("Bull call short strike must be above the long strike.")
        net_debit_per_share = primary_premium - secondary_premium
        width = secondary_strike - primary_strike
        if not 0 < net_debit_per_share < width:
            raise InputError("Bull call net debit must be positive and lower than the strike width.")
        max_loss = net_debit_per_share * multiplier
        max_profit = (width - net_debit_per_share) * multiplier
        breakeven = primary_strike + net_debit_per_share
        breakevens = [breakeven]
        legs = [
            {"side": "buy", "right": "call", "strike_micros": primary_strike, "premium_micros": primary_premium, "quantity": quantity},
            {"side": "sell", "right": "call", "strike_micros": secondary_strike, "premium_micros": secondary_premium, "quantity": quantity},
        ]
    elif strategy == "bear_put_spread":
        assert secondary_strike is not None and secondary_premium is not None
        if secondary_strike >= primary_strike:
            raise InputError("Bear put short strike must be below the long strike.")
        net_debit_per_share = primary_premium - secondary_premium
        width = primary_strike - secondary_strike
        if not 0 < net_debit_per_share < width:
            raise InputError("Bear put net debit must be positive and lower than the strike width.")
        max_loss = net_debit_per_share * multiplier
        max_profit = (width - net_debit_per_share) * multiplier
        breakeven = primary_strike - net_debit_per_share
        breakevens = [breakeven]
        legs = [
            {"side": "buy", "right": "put", "strike_micros": primary_strike, "premium_micros": primary_premium, "quantity": quantity},
            {"side": "sell", "right": "put", "strike_micros": secondary_strike, "premium_micros": secondary_premium, "quantity": quantity},
        ]
    elif strategy == "long_straddle":
        assert secondary_strike is not None and secondary_premium is not None
        if secondary_strike > primary_strike:
            raise InputError("The put strike must be at or below the call strike for a long volatility structure.")
        net_debit_per_share = primary_premium + secondary_premium
        max_loss = net_debit_per_share * multiplier
        max_profit = None
        breakevens = [
            max(0, secondary_strike - net_debit_per_share),
            primary_strike + net_debit_per_share,
        ]
        breakeven = breakevens[0]
        legs = [
            {"side": "buy", "right": "call", "strike_micros": primary_strike, "premium_micros": primary_premium, "quantity": quantity},
            {"side": "buy", "right": "put", "strike_micros": secondary_strike, "premium_micros": secondary_premium, "quantity": quantity},
        ]
    else:
        assert secondary_strike is not None and secondary_premium is not None
        assert tertiary_strike is not None and tertiary_premium is not None
        assert quaternary_strike is not None and quaternary_premium is not None
        if not primary_strike < secondary_strike < tertiary_strike < quaternary_strike:
            raise InputError("Iron condor strikes must be long put < short put < short call < long call.")
        credit = secondary_premium + tertiary_premium - primary_premium - quaternary_premium
        maximum_width = max(secondary_strike - primary_strike, quaternary_strike - tertiary_strike)
        if not 0 < credit < maximum_width:
            raise InputError("Iron condor net credit must be positive and lower than its widest wing.")
        net_debit_per_share = -credit
        net_premium_label = "net credit"
        max_loss = (maximum_width - credit) * multiplier
        max_profit = credit * multiplier
        breakevens = [secondary_strike - credit, tertiary_strike + credit]
        breakeven = breakevens[0]
        legs = [
            {"side": "buy", "right": "put", "strike_micros": primary_strike, "premium_micros": primary_premium, "quantity": quantity},
            {"side": "sell", "right": "put", "strike_micros": secondary_strike, "premium_micros": secondary_premium, "quantity": quantity},
            {"side": "sell", "right": "call", "strike_micros": tertiary_strike, "premium_micros": tertiary_premium, "quantity": quantity},
            {"side": "buy", "right": "call", "strike_micros": quaternary_strike, "premium_micros": quaternary_premium, "quantity": quantity},
        ]

    strikes = [int(leg["strike_micros"]) for leg in legs]
    point_values = {0, *strikes, *breakevens, max(strikes) * 3 // 2}
    payoff_points = [
        {
            "underlying": decimal_string(point),
            "profit_loss": decimal_string(_option_payoff_micros(legs, point)),
        }
        for point in sorted(point_values)
    ]
    serialized_legs = [
        {
            "side": leg["side"],
            "right": leg["right"],
            "strike": decimal_string(leg["strike_micros"]),
            "premium": decimal_string(leg["premium_micros"]),
            "quantity": leg["quantity"],
        }
        for leg in legs
    ]
    inputs = {
        "strategy": strategy,
        "expiration": expiration_text,
        "quantity": quantity,
        "legs": serialized_legs,
    }
    analysis = {
        "plan_status": "ready_for_manual_review",
        "mode": "indicative_non_executable",
        "net_debit": decimal_string(net_debit_per_share * multiplier),
        "net_premium_label": net_premium_label,
        "max_loss": decimal_string(max_loss),
        "max_profit": decimal_string(max_profit) if max_profit is not None else None,
        "max_profit_label": decimal_string(max_profit) if max_profit is not None else "Unlimited",
        "breakeven": decimal_string(breakeven),
        "breakevens": [decimal_string(value) for value in breakevens],
        "risk_defined": True,
        "expiration_days": (expiration - datetime.now(timezone.utc).date()).days,
        "legs": serialized_legs,
        "payoff_points": payoff_points,
        "data_freshness": "User-entered premiums; no option-chain, Greeks, IV, spread, or open-interest feed.",
        "blocked_actions": ["order placement", "routing", "signing", "submission"],
        "disclaimer": "This is a planning worksheet, not investment or trading advice. Review venue rules and make any trading decisions yourself.",
    }
    return symbol, hypothesis, inputs, analysis


def _plan_rows(
    db: sqlite3.Connection, user_id: str, kind: str | None = None, limit: int | None = 30
) -> list[dict[str, Any]]:
    parameters: list[Any] = [user_id]
    where = "user_id = ?"
    if kind is not None:
        if kind not in {"day_trade", "options"}:
            raise InputError("Plan kind must be day_trade or options.")
        where += " AND kind = ?"
        parameters.append(kind)
    query = (
        "SELECT id, kind, symbol, hypothesis, inputs_json, analysis_json, created_at "
        f"FROM research_plans WHERE {where} ORDER BY rowid DESC"
    )
    if limit is not None:
        query += " LIMIT ?"
        parameters.append(max(1, min(limit, 100)))
    rows = db.execute(query, parameters).fetchall()
    return [
        {
            "id": row["id"],
            "kind": row["kind"],
            "symbol": row["symbol"],
            "hypothesis": row["hypothesis"],
            "inputs": json.loads(row["inputs_json"]),
            "analysis": json.loads(row["analysis_json"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def list_research_plans(
    path: Path, user_id: str, kind: str | None = None, limit: int = 30
) -> list[dict[str, Any]]:
    with open_db(path) as db:
        return _plan_rows(db, user_id, kind, limit)


def record_research_plan(
    path: Path, user_id: str, kind: str, payload: dict[str, Any]
) -> dict[str, Any]:
    if kind == "day_trade":
        symbol, hypothesis, inputs, analysis = _day_trade_worksheet(payload)
    elif kind == "options":
        symbol, hypothesis, inputs, analysis = _options_worksheet(payload)
    else:
        raise InputError("Plan kind must be day_trade or options.")
    plan_id = str(uuid4())
    created_at = now_iso()
    result = {
        "id": plan_id,
        "kind": kind,
        "symbol": symbol,
        "hypothesis": hypothesis,
        "inputs": inputs,
        "analysis": analysis,
        "created_at": created_at,
    }
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO research_plans(id, user_id, kind, symbol, hypothesis, inputs_json, "
            "analysis_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                plan_id,
                user_id,
                kind,
                symbol,
                hypothesis,
                json.dumps(inputs, separators=(",", ":")),
                json.dumps(analysis, separators=(",", ":")),
                created_at,
            ),
        )
        _append_sync_event(db, user_id, "research_plan", plan_id, "upsert", result)
    return result


def _plan_review_rows(
    db: sqlite3.Connection,
    user_id: str,
    plan_id: str | None = None,
    limit: int | None = 50,
) -> list[dict[str, Any]]:
    where = "r.user_id = ?"
    parameters: list[Any] = [user_id]
    if plan_id is not None:
        where += " AND r.plan_id = ?"
        parameters.append(plan_id)
    query = (
        "SELECT r.id, r.plan_id, p.kind, p.symbol, r.decision, r.outcome, "
        "r.discipline_score, r.note, r.actual_entry_micros, r.actual_exit_micros, "
        "r.screenshot_data_url, r.execution_note, r.created_at, p.inputs_json, p.analysis_json "
        "FROM plan_reviews r "
        "JOIN research_plans p ON p.id = r.plan_id AND p.user_id = r.user_id "
        f"WHERE {where} ORDER BY r.rowid DESC"
    )
    if limit is not None:
        query += " LIMIT ?"
        parameters.append(max(1, min(limit, 200)))
    results = []
    for row in db.execute(query, parameters).fetchall():
        item = dict(row)
        inputs = json.loads(item.pop("inputs_json"))
        analysis = json.loads(item.pop("analysis_json"))
        actual_entry = item.pop("actual_entry_micros")
        actual_exit = item.pop("actual_exit_micros")
        item["actual_entry"] = decimal_string(actual_entry) if actual_entry is not None else None
        item["actual_exit"] = decimal_string(actual_exit) if actual_exit is not None else None
        item["has_screenshot"] = bool(item.get("screenshot_data_url"))
        planned_entry = _metric_decimal(inputs.get("entry"))
        actual_entry_decimal = Decimal(actual_entry) / SCALE if actual_entry is not None else None
        item["execution_deviation_percent"] = (
            _percent((actual_entry_decimal / planned_entry - 1) * 100)
            if planned_entry and actual_entry_decimal is not None else None
        )
        if actual_entry is not None and actual_exit is not None and item["kind"] == "day_trade":
            direction = inputs.get("direction")
            move = (
                Decimal(actual_exit - actual_entry) / SCALE
                if direction == "long" else Decimal(actual_entry - actual_exit) / SCALE
            )
            quantity = Decimal(int(analysis.get("maximum_whole_shares") or 0))
            item["realized_pnl"] = format((move * quantity).quantize(Decimal("0.01")), "f")
            planned_risk = _metric_decimal(analysis.get("planned_risk_at_maximum"))
            item["realized_r_multiple"] = (
                format((move * quantity / planned_risk).quantize(Decimal("0.01")), "f")
                if planned_risk else None
            )
        else:
            item["realized_pnl"] = None
            item["realized_r_multiple"] = None
        results.append(item)
    return results


def record_plan_review(
    path: Path, user_id: str, plan_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    decision = str(payload.get("decision") or "").lower()
    outcome = str(payload.get("outcome") or "na").lower()
    if decision not in {"followed", "skipped", "invalidated", "expired"}:
        raise InputError("Decision must be followed, skipped, invalidated, or expired.")
    if outcome not in {"open", "win", "loss", "scratch", "na"}:
        raise InputError("Outcome must be open, win, loss, scratch, or na.")
    if decision == "followed" and outcome == "na":
        raise InputError("A followed plan must record an open or resolved outcome.")
    if decision != "followed" and outcome != "na":
        raise InputError("Only a followed plan can record a trading outcome.")
    note = str(payload.get("note") or "").strip()
    if not 1 <= len(note) <= 2000:
        raise InputError("Plan review note must be 1-2000 characters.")
    raw_score = payload.get("discipline_score")
    discipline_score = None
    if raw_score not in {None, ""}:
        try:
            discipline_score = int(raw_score)
        except (TypeError, ValueError):
            raise InputError("Discipline score must be a whole number from 1 to 5.") from None
        if not 1 <= discipline_score <= 5:
            raise InputError("Discipline score must be a whole number from 1 to 5.")
    actual_entry = None
    actual_exit = None
    if payload.get("actual_entry") not in {None, ""}:
        actual_entry = to_micros(payload.get("actual_entry"), "Actual entry")
    if payload.get("actual_exit") not in {None, ""}:
        actual_exit = to_micros(payload.get("actual_exit"), "Actual exit")
    if decision != "followed" and (actual_entry is not None or actual_exit is not None):
        raise InputError("Only a followed plan can record actual entry or exit prices.")
    if actual_exit is not None and actual_entry is None:
        raise InputError("Record the actual entry before the actual exit.")
    screenshot_data_url = str(payload.get("screenshot_data_url") or "").strip() or None
    if screenshot_data_url is not None:
        match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=\r\n]+)", screenshot_data_url)
        if not match:
            raise InputError("Chart screenshot must be a PNG or JPEG data URL.")
        try:
            screenshot_bytes = base64.b64decode(match.group(2), validate=True)
        except (ValueError, binascii.Error):
            raise InputError("Chart screenshot contains invalid base64 data.") from None
        if not screenshot_bytes or len(screenshot_bytes) > 750_000:
            raise InputError("Chart screenshot must be between 1 byte and 750 KB.")
    execution_note = str(payload.get("execution_note") or "").strip()
    if len(execution_note) > 1000:
        raise InputError("Execution deviation note must be 1000 characters or fewer.")

    with open_db(path) as db:
        plan = db.execute(
            "SELECT id, kind, symbol FROM research_plans WHERE id = ? AND user_id = ?",
            (plan_id, user_id),
        ).fetchone()
        if not plan:
            raise ApiError(404, "Planning worksheet was not found.")
        result = {
            "id": str(uuid4()),
            "plan_id": plan["id"],
            "kind": plan["kind"],
            "symbol": plan["symbol"],
            "decision": decision,
            "outcome": outcome,
            "discipline_score": discipline_score,
            "note": note,
            "actual_entry": decimal_string(actual_entry) if actual_entry is not None else None,
            "actual_exit": decimal_string(actual_exit) if actual_exit is not None else None,
            "screenshot_data_url": screenshot_data_url,
            "has_screenshot": screenshot_data_url is not None,
            "execution_note": execution_note,
            "created_at": now_iso(),
        }
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO plan_reviews(id, user_id, plan_id, decision, outcome, "
            "discipline_score, note, actual_entry_micros, actual_exit_micros, screenshot_data_url, "
            "execution_note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result["id"], user_id, plan_id, decision, outcome,
                discipline_score, note, actual_entry, actual_exit, screenshot_data_url,
                execution_note, result["created_at"],
            ),
        )
        hydrated = _plan_review_rows(db, user_id, limit=1)[0]
        _append_sync_event(db, user_id, "plan_review", result["id"], "upsert", hydrated)
    return hydrated


def _plan_review_center_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    plans = _plan_rows(db, user_id, limit=None)
    reviews = _plan_review_rows(db, user_id, limit=None)
    latest: dict[str, dict[str, Any]] = {}
    for review in reviews:
        latest.setdefault(review["plan_id"], review)
    counts = {name: 0 for name in ("followed", "skipped", "invalidated", "expired")}
    for review in latest.values():
        counts[review["decision"]] += 1
    process_decisions = counts["followed"] + counts["skipped"] + counts["invalidated"]
    option_attention = []
    today = datetime.now(timezone.utc).date()
    for plan in plans:
        if plan["kind"] != "options":
            continue
        review = latest.get(plan["id"])
        terminal = review and (
            review["decision"] in {"skipped", "invalidated", "expired"}
            or review["outcome"] in {"win", "loss", "scratch"}
        )
        if terminal:
            continue
        expiration = date.fromisoformat(plan["inputs"]["expiration"])
        days = (expiration - today).days
        urgency = "passed" if days < 0 else "today" if days == 0 else "soon" if days <= 7 else "approaching" if days <= 30 else "scheduled"
        option_attention.append(
            {
                "plan_id": plan["id"],
                "symbol": plan["symbol"],
                "hypothesis": plan["hypothesis"],
                "strategy": plan["inputs"]["strategy"],
                "expiration": expiration.isoformat(),
                "days_remaining": days,
                "urgency": urgency,
                "decision": review["decision"] if review else "unreviewed",
                "outcome": review["outcome"] if review else "na",
            }
        )
    option_attention.sort(key=lambda item: (item["expiration"], item["symbol"], item["plan_id"]))
    return {
        "total_plans": len(plans),
        "awaiting_review": len(plans) - len(latest),
        "active_followed": sum(
            review["decision"] == "followed" and review["outcome"] == "open"
            for review in latest.values()
        ),
        "reviewed_plans": len(latest),
        "decision_counts": counts,
        "follow_through_percent": (
            format(
                (Decimal(counts["followed"]) * 100 / Decimal(process_decisions)).quantize(Decimal("0.1")),
                "f",
            )
            if process_decisions else None
        ),
        "option_attention": option_attention[:20],
        "recent_reviews": reviews[:20],
        "scope": "Self-recorded plan decisions and saved option expirations; no position or brokerage status is inferred.",
    }


def plan_review_center(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        return _plan_review_center_from_db(db, user_id)


def _day_trade_guardrails_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    today = datetime.now(timezone.utc).astimezone(NEW_YORK).date()
    business_days = []
    cursor = today
    while len(business_days) < 5:
        if cursor.weekday() < 5:
            business_days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    start = business_days[-1]
    start_utc = datetime.combine(
        date.fromisoformat(start), time.min, tzinfo=NEW_YORK
    ).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    rows = db.execute(
        "SELECT symbol, side, executed_at FROM trades WHERE user_id = ? AND asset_type = 'equity' "
        "AND executed_at >= ? ORDER BY executed_at, rowid",
        (user_id, start_utc),
    ).fetchall()
    grouped: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        key = (_new_york_date(str(row["executed_at"])).isoformat(), str(row["symbol"]))
        grouped.setdefault(key, {"buy": 0, "sell": 0})[str(row["side"])] += 1
    day_trade_details = []
    for (trading_date, symbol), counts in grouped.items():
        estimated = min(counts["buy"], counts["sell"])
        if estimated:
            day_trade_details.append(
                {"trading_date": trading_date, "symbol": symbol, "estimated_round_trips": estimated}
            )
    estimated_day_trades = sum(item["estimated_round_trips"] for item in day_trade_details)
    total_orders = len(rows)
    ratio = Decimal(estimated_day_trades) * 100 / Decimal(total_orders) if total_orders else Decimal(0)
    pdt_threshold = estimated_day_trades >= 4 and ratio > 6
    profile = _investor_profile_from_db(db, user_id)
    account_size = Decimal(profile["paper_account_size"])
    reviews = _plan_review_rows(db, user_id, limit=None)
    day_reviews = [item for item in reviews if item["kind"] == "day_trade" and item["decision"] == "followed"]
    consecutive_losses = 0
    for review in day_reviews:
        if review["outcome"] == "loss":
            consecutive_losses += 1
        elif review["outcome"] in {"win", "scratch"}:
            break
    today_loss = sum((
        max(-Decimal(item["realized_pnl"]), Decimal(0))
        for item in day_reviews
        if _new_york_date(item["created_at"]) == today and item.get("realized_pnl") is not None
    ), Decimal(0))
    daily_limit = Decimal(profile["daily_loss_limit"])
    stop_conditions = []
    if consecutive_losses >= 3:
        stop_conditions.append("Three or more consecutive self-recorded day-trade losses.")
    if today_loss >= daily_limit:
        stop_conditions.append("Today's recorded realized loss reached the saved daily loss limit.")
    return {
        "window_start": start,
        "window_end": business_days[0],
        "business_days": list(reversed(business_days)),
        "estimated_day_trades": estimated_day_trades,
        "day_trade_ratio_percent": _percent(ratio),
        "total_equity_orders": total_orders,
        "pdt_threshold_reached": pdt_threshold,
        "legacy_pdt_estimate": {
            "threshold_reached": pdt_threshold,
            "below_25000": account_size < Decimal("25000"),
            "applies_only_if_broker_uses_legacy_rules": True,
        },
        "intraday_margin_status": "broker_data_required",
        "regulatory_transition": {
            "new_rule_effective": "2026-06-04",
            "broker_transition_deadline": "2027-10-20",
            "broker_confirmation_required": True,
        },
        "paper_account_value": profile["paper_account_size"],
        "below_25000": account_size < Decimal("25000"),
        "consecutive_losses": consecutive_losses,
        "recorded_loss_today": format(today_loss.quantize(Decimal("0.01")), "f"),
        "daily_loss_limit": profile["daily_loss_limit"],
        "stop_conditions": stop_conditions,
        "stop_triggered": bool(stop_conditions),
        "details": sorted(day_trade_details, key=lambda item: (item["trading_date"], item["symbol"]), reverse=True),
        "scope": "FINRA intraday margin rules are in a broker transition through October 20, 2027. The PDT count is a legacy-rule estimate only; confirm your broker's current margin method. This app cannot see broker equity, intraday margin deficits, holidays, or outside trades.",
    }


def _journal_rows(
    db: sqlite3.Connection, user_id: str, limit: int | None = 50
) -> list[dict[str, Any]]:
    query = (
        "SELECT id, symbol, kind, setup_tag, title, body, outcome, discipline_score, "
        "created_at FROM journal_entries WHERE user_id = ? ORDER BY rowid DESC"
    )
    parameters: list[Any] = [user_id]
    if limit is not None:
        query += " LIMIT ?"
        parameters.append(max(1, min(limit, 200)))
    rows = db.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def list_journal_entries(path: Path, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with open_db(path) as db:
        return _journal_rows(db, user_id, limit)


def record_journal_entry(
    path: Path, user_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    symbol = normalize_symbol(payload.get("symbol"))
    kind = str(payload.get("kind") or "").lower()
    outcome = str(payload.get("outcome") or "na").lower()
    if kind not in {"note", "review", "lesson"}:
        raise InputError("Journal kind must be note, review, or lesson.")
    if outcome not in {"open", "win", "loss", "scratch", "na"}:
        raise InputError("Outcome must be open, win, loss, scratch, or na.")
    if kind != "review" and outcome != "na":
        raise InputError("Only review entries can record a trading outcome.")
    setup_tag = str(payload.get("setup_tag") or "untagged").strip().lower()
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not 1 <= len(setup_tag) <= 40 or not re.fullmatch(r"[a-z0-9][a-z0-9 _-]*", setup_tag):
        raise InputError("Setup tag must be 1-40 lowercase letters, numbers, spaces, underscores, or hyphens.")
    if not 1 <= len(title) <= 120:
        raise InputError("Journal title must be 1-120 characters.")
    if not 1 <= len(body) <= 4000:
        raise InputError("Journal body must be 1-4000 characters.")
    raw_score = payload.get("discipline_score")
    discipline_score = None
    if raw_score not in {None, ""}:
        try:
            discipline_score = int(raw_score)
        except (TypeError, ValueError):
            raise InputError("Discipline score must be a whole number from 1 to 5.") from None
        if not 1 <= discipline_score <= 5:
            raise InputError("Discipline score must be a whole number from 1 to 5.")

    result = {
        "id": str(uuid4()),
        "symbol": symbol,
        "kind": kind,
        "setup_tag": setup_tag,
        "title": title,
        "body": body,
        "outcome": outcome,
        "discipline_score": discipline_score,
        "created_at": now_iso(),
    }
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO journal_entries(id, user_id, symbol, kind, setup_tag, title, body, "
            "outcome, discipline_score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result["id"],
                user_id,
                symbol,
                kind,
                setup_tag,
                title,
                body,
                outcome,
                discipline_score,
                result["created_at"],
            ),
        )
        _append_sync_event(db, user_id, "journal_entry", result["id"], "upsert", result)
    return result


def _review_stats_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    rows = db.execute(
        "SELECT kind, outcome, discipline_score, setup_tag FROM journal_entries "
        "WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    reviews = [row for row in rows if row["kind"] == "review"]
    counts = {
        outcome: sum(row["outcome"] == outcome for row in reviews)
        for outcome in ("win", "loss", "scratch", "open")
    }
    resolved = counts["win"] + counts["loss"] + counts["scratch"]
    decisive = counts["win"] + counts["loss"]
    scores = [int(row["discipline_score"]) for row in reviews if row["discipline_score"]]
    tags: dict[str, int] = {}
    for row in reviews:
        tags[row["setup_tag"]] = tags.get(row["setup_tag"], 0) + 1
    return {
        "entries": len(rows),
        "reviews": len(reviews),
        "resolved_reviews": resolved,
        **counts,
        "win_rate_percent": (
            format((Decimal(counts["win"]) * 100 / Decimal(decisive)).quantize(Decimal("0.1")), "f")
            if decisive
            else None
        ),
        "average_discipline_score": (
            format((Decimal(sum(scores)) / Decimal(len(scores))).quantize(Decimal("0.1")), "f")
            if scores
            else None
        ),
        "setup_counts": [
            {"tag": tag, "count": count}
            for tag, count in sorted(tags.items(), key=lambda item: (-item[1], item[0]))
        ],
        "scope": "Self-recorded journal outcomes; excludes open and scratch reviews from win rate.",
    }


def review_stats(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        return _review_stats_from_db(db, user_id)


def _portfolio_risk_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    positions = calculate_positions(_position_rows(db, user_id))
    profile = _investor_profile_from_db(db, user_id)
    account_size = Decimal(profile["paper_account_size"])
    latest_rows = db.execute(
        "SELECT symbol, close_micros, trading_date FROM market_daily AS bars "
        "WHERE trading_date = (SELECT MAX(trading_date) FROM market_daily "
        "WHERE symbol = bars.symbol)"
    ).fetchall()
    latest = {row["symbol"]: row for row in latest_rows}
    exposures = []
    for state in positions.values():
        quantity = int(state["quantity_micros"])
        if quantity <= 0:
            continue
        market_row = latest.get(state["symbol"]) if state["asset_type"] == "equity" else None
        price = int(market_row["close_micros"]) if market_row else int(state["average_cost_micros"])
        value = _position_value_micros(quantity, price, str(state["asset_type"]))
        cached_company = _sec_cached(db, f"fundamentals:{state['symbol']}") or {}
        company_profile = cached_company.get("company_profile") or {}
        sector = str(company_profile.get("industry") or "Unclassified")
        exposures.append(
            {
                "symbol": state["symbol"],
                "asset_type": state["asset_type"],
                "reference_price": decimal_string(price),
                "reference_source": "cached_daily_close" if market_row else "average_cost_fallback",
                "reference_date": market_row["trading_date"] if market_row else None,
                "exposure": decimal_string(value),
                "exposure_micros": value,
                "sector": sector,
            }
        )
    gross = sum(item["exposure_micros"] for item in exposures)
    for item in exposures:
        weight = Decimal(item["exposure_micros"]) * 100 / Decimal(gross) if gross else Decimal(0)
        item["weight_percent"] = format(weight.quantize(Decimal("0.01")), "f")
        account_weight = (
            Decimal(item["exposure_micros"]) * 100 / (account_size * SCALE)
            if account_size else Decimal(0)
        )
        item["account_weight_percent"] = format(account_weight.quantize(Decimal("0.01")), "f")
        item["over_max_position"] = account_weight > Decimal(profile["max_position_percent"])
        del item["exposure_micros"]
    exposures.sort(key=lambda item: Decimal(item["weight_percent"]), reverse=True)
    top_weight = Decimal(exposures[0]["weight_percent"]) if exposures else Decimal(0)
    concentration = (
        "No open positions"
        if not exposures
        else "Single-name dominated"
        if top_weight >= 50
        else "Concentrated"
        if top_weight >= 25
        else "Distributed"
    )
    sectors: dict[str, Decimal] = {}
    for item in exposures:
        sectors[item["sector"]] = sectors.get(item["sector"], Decimal(0)) + Decimal(item["exposure"])
    gross_dollars = Decimal(gross) / SCALE
    sector_exposure = [
        {
            "sector": sector,
            "exposure": format(value.normalize(), "f"),
            "weight_percent": _percent(value * 100 / gross_dollars) if gross else "0.00",
        }
        for sector, value in sectors.items()
    ]
    sector_exposure.sort(key=lambda item: Decimal(item["weight_percent"]), reverse=True)

    return_maps: dict[str, dict[str, Decimal]] = {}
    for item in exposures:
        rows = db.execute(
            "SELECT trading_date, close_micros FROM market_daily WHERE symbol = ? "
            "ORDER BY trading_date DESC LIMIT 61",
            (item["symbol"],),
        ).fetchall()
        ordered = list(reversed(rows))
        return_maps[item["symbol"]] = {
            str(ordered[index]["trading_date"]): (
                Decimal(int(ordered[index]["close_micros"]))
                / Decimal(int(ordered[index - 1]["close_micros"])) - 1
            )
            for index in range(1, len(ordered))
        }
    correlations = []
    symbols = sorted(return_maps)
    for left_index, left in enumerate(symbols):
        for right in symbols[left_index + 1:]:
            common = sorted(set(return_maps[left]) & set(return_maps[right]))
            if len(common) < 20:
                continue
            xs = [float(return_maps[left][key]) for key in common]
            ys = [float(return_maps[right][key]) for key in common]
            x_mean, y_mean = sum(xs) / len(xs), sum(ys) / len(ys)
            numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
            denominator = math.sqrt(
                sum((x - x_mean) ** 2 for x in xs) * sum((y - y_mean) ** 2 for y in ys)
            )
            if denominator:
                correlations.append(
                    {
                        "left": left, "right": right,
                        "correlation": format(Decimal(str(numerator / denominator)).quantize(Decimal("0.01")), "f"),
                        "observations": len(common),
                    }
                )
    correlations.sort(key=lambda item: abs(Decimal(item["correlation"])), reverse=True)

    tech_words = ("software", "semiconductor", "technology", "computer", "internet", "electronic")
    scenarios = []
    for key, label, market_shock, tech_shock, option_shock in (
        ("market_down_5", "Market -5%", Decimal("-5"), Decimal("-5"), Decimal("-10")),
        ("market_down_10", "Market -10%", Decimal("-10"), Decimal("-10"), Decimal("-20")),
        ("technology_drawdown", "Technology drawdown", Decimal("-3"), Decimal("-15"), Decimal("-18")),
        ("volatility_spike", "Volatility spike", Decimal("-4"), Decimal("-7"), Decimal("-12")),
    ):
        loss = Decimal(0)
        details = []
        for item in exposures:
            shock = option_shock if item["asset_type"] == "option" else (
                tech_shock if any(word in item["sector"].lower() for word in tech_words) else market_shock
            )
            impact = Decimal(item["exposure"]) * shock / 100
            loss += impact
            details.append({"symbol": item["symbol"], "shock_percent": format(shock, "f"), "impact": format(impact.quantize(Decimal("0.01")), "f")})
        scenarios.append(
            {
                "key": key, "label": label,
                "estimated_impact": format(loss.quantize(Decimal("0.01")), "f"),
                "account_impact_percent": _percent(loss * 100 / account_size) if account_size else "0.00",
                "details": details,
            }
        )
    return {
        "gross_exposure": decimal_string(gross),
        "position_count": len(exposures),
        "largest_weight_percent": format(top_weight.quantize(Decimal("0.01")), "f"),
        "concentration_label": concentration,
        "positions": exposures,
        "sectors": sector_exposure,
        "correlations": correlations[:10],
        "stress_scenarios": scenarios,
        "max_position_percent": profile["max_position_percent"],
        "over_limit_count": sum(item["over_max_position"] for item in exposures),
        "live_price_count": sum(item["reference_source"] == "cached_daily_close" for item in exposures),
        "fallback_price_count": sum(item["reference_source"] == "average_cost_fallback" for item in exposures),
        "disclaimer": "Descriptive exposure and mechanical shock scenarios only. Correlations use up to 60 cached daily returns; scenarios are not forecasts.",
    }


def _portfolio_performance_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    positions = calculate_positions(_position_rows(db, user_id))
    profile = _investor_profile_from_db(db, user_id)
    decision_center = _decision_center_from_db(db, user_id)
    decisions = {item["symbol"]: item for item in decision_center["latest"]}
    latest_rows = db.execute(
        "SELECT symbol, close_micros, trading_date FROM market_daily AS bars "
        "WHERE trading_date = (SELECT MAX(trading_date) FROM market_daily "
        "WHERE symbol = bars.symbol)"
    ).fetchall()
    latest = {row["symbol"]: row for row in latest_rows}

    starting_cash = int(Decimal(profile["paper_account_size"]) * SCALE)
    cash = starting_cash
    for row in _position_rows(db, user_id):
        value = _position_value_micros(
            int(row["quantity_micros"]), int(row["price_micros"]), str(row["asset_type"])
        )
        cash += value if row["side"] == "sell" else -value

    items = []
    total_cost = 0
    market_value = 0
    unrealized = 0
    realized = 0
    for state in positions.values():
        quantity = int(state["quantity_micros"])
        state_realized = int(state["realized_pnl_micros"])
        realized += state_realized
        if quantity <= 0:
            continue
        market_row = latest.get(state["symbol"]) if state["asset_type"] == "equity" else None
        reference_price = (
            int(market_row["close_micros"]) if market_row else int(state["average_cost_micros"])
        )
        cost = _position_value_micros(
            quantity, int(state["average_cost_micros"]), str(state["asset_type"])
        )
        value = _position_value_micros(
            quantity, reference_price, str(state["asset_type"])
        )
        item_unrealized = value - cost
        total_cost += cost
        market_value += value
        unrealized += item_unrealized
        decision = decisions.get(str(state["symbol"]))
        items.append(
            {
                "symbol": state["symbol"],
                "asset_type": state["asset_type"],
                "quantity": decimal_string(quantity),
                "average_cost": decimal_string(int(state["average_cost_micros"])),
                "reference_price": decimal_string(reference_price),
                "reference_source": "cached_daily_close" if market_row else "average_cost_fallback",
                "reference_date": market_row["trading_date"] if market_row else None,
                "cost_basis": decimal_string(cost),
                "market_value": decimal_string(value),
                "unrealized_pnl": decimal_string(item_unrealized),
                "unrealized_percent": _percent(
                    Decimal(item_unrealized) * 100 / Decimal(cost) if cost else Decimal(0)
                ),
                "realized_pnl": decimal_string(state_realized),
                "decision_signal": decision["signal"] if decision else None,
                "decision_label": decision["signal_label"] if decision else None,
                "decision_score": decision["score"] if decision else None,
            }
        )
    items.sort(key=lambda item: Decimal(item["market_value"]), reverse=True)
    total_pnl = realized + unrealized
    estimated_equity = cash + market_value
    return {
        "starting_paper_cash": decimal_string(starting_cash),
        "estimated_cash": decimal_string(cash),
        "open_cost_basis": decimal_string(total_cost),
        "market_value": decimal_string(market_value),
        "unrealized_pnl": decimal_string(unrealized),
        "realized_pnl": decimal_string(realized),
        "total_pnl": decimal_string(total_pnl),
        "total_return_percent": _percent(
            Decimal(total_pnl) * 100 / Decimal(starting_cash) if starting_cash else Decimal(0)
        ),
        "estimated_account_value": decimal_string(estimated_equity),
        "positions": items,
        "pricing": {
            "cached_close_count": sum(item["reference_source"] == "cached_daily_close" for item in items),
            "cost_fallback_count": sum(item["reference_source"] == "average_cost_fallback" for item in items),
        },
        "history": _portfolio_history_from_db(db, user_id),
        "disclaimer": "Paper performance uses the profile account size as starting cash, cached daily closes for equities, and cost basis when no market price is available.",
    }


def _portfolio_history_from_db(db: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    trade_rows = db.execute(
        "SELECT symbol, asset_type, side, quantity_micros, price_micros, executed_at "
        "FROM trades WHERE user_id = ? ORDER BY executed_at, rowid",
        (user_id,),
    ).fetchall()
    if not trade_rows:
        return []
    profile = _investor_profile_from_db(db, user_id)
    starting_cash = int(Decimal(profile["paper_account_size"]) * SCALE)
    symbols = sorted({str(row["symbol"]) for row in trade_rows})
    placeholders = ",".join("?" for _ in symbols)
    market_rows = db.execute(
        f"SELECT symbol, trading_date, close_micros FROM market_daily WHERE symbol IN ({placeholders}) "
        "ORDER BY trading_date, symbol",
        symbols,
    ).fetchall()
    first_date = min(str(row["executed_at"])[:10] for row in trade_rows)
    dates = sorted(
        {str(row["trading_date"]) for row in market_rows if str(row["trading_date"]) >= first_date}
        | {str(row["executed_at"])[:10] for row in trade_rows}
    )[-365:]
    prices_by_symbol: dict[str, list[tuple[str, int]]] = {symbol: [] for symbol in symbols}
    for row in market_rows:
        prices_by_symbol[str(row["symbol"])].append(
            (str(row["trading_date"]), int(row["close_micros"]))
        )
    points = []
    for current_date in dates:
        included = [row for row in trade_rows if str(row["executed_at"])[:10] <= current_date]
        if not included:
            continue
        cash = starting_cash
        for row in included:
            value = _position_value_micros(
                int(row["quantity_micros"]), int(row["price_micros"]), str(row["asset_type"])
            )
            cash += value if row["side"] == "sell" else -value
        states = calculate_positions(included)
        market_value = 0
        open_cost = 0
        unrealized = 0
        realized = sum(int(state["realized_pnl_micros"]) for state in states.values())
        for state in states.values():
            quantity = int(state["quantity_micros"])
            if quantity <= 0:
                continue
            price = int(state["average_cost_micros"])
            if state["asset_type"] == "equity":
                eligible = [value for bar_date, value in prices_by_symbol[str(state["symbol"])] if bar_date <= current_date]
                if eligible:
                    price = eligible[-1]
            cost = _position_value_micros(
                quantity, int(state["average_cost_micros"]), str(state["asset_type"])
            )
            value = _position_value_micros(quantity, price, str(state["asset_type"]))
            open_cost += cost
            market_value += value
            unrealized += value - cost
        equity = cash + market_value
        points.append(
            {
                "trading_date": current_date,
                "equity": decimal_string(equity),
                "cash": decimal_string(cash),
                "market_value": decimal_string(market_value),
                "realized_pnl": decimal_string(realized),
                "unrealized_pnl": decimal_string(unrealized),
                "total_pnl": decimal_string(realized + unrealized),
            }
        )
    return points


def _portfolio_actions_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    screener = _watchlist_screener_from_db(db, user_id)
    risk = _portfolio_risk_from_db(db, user_id)
    actions = []
    for item in screener["items"]:
        action = (
            "reduce_review" if item["signal"] in {"reduce", "sell_review"}
            else "add_candidate" if item["signal"] == "buy_candidate"
            else "insufficient_data" if item["freshness"] != "current" or item["signal"] in {"data_required", "refresh_required"}
            else "monitor"
        )
        actions.append(
            {
                "symbol": item["symbol"], "action": action,
                "label": {
                    "reduce_review": "Reduce / exit review",
                    "add_candidate": "Paper add candidate",
                    "insufficient_data": "Refresh required",
                    "monitor": "Monitor",
                }[action],
                "reason": item["signal_label"] if action != "insufficient_data" else "Market evidence is missing or stale.",
                "score": item["score"], "account_percent": item["account_percent"],
            }
        )
    for position in risk["positions"]:
        if position["over_max_position"] and not any(
            position["asset_type"] == "equity"
            and item["symbol"] == position["symbol"]
            and item["action"] == "reduce_review"
            for item in actions
        ):
            actions.append(
                {
                    "symbol": position["symbol"], "asset_type": position["asset_type"],
                    "action": "risk_exceeded",
                    "label": "Position limit exceeded",
                    "reason": f"{position['account_weight_percent']}% of account exceeds the {risk['max_position_percent']}% limit.",
                    "score": None, "account_percent": position["account_weight_percent"],
                }
            )
    priority = {"risk_exceeded": 0, "reduce_review": 1, "add_candidate": 2, "insufficient_data": 3, "monitor": 4}
    actions.sort(key=lambda item: (priority[item["action"]], item["symbol"]))
    return {
        "actions": actions,
        "counts": {key: sum(item["action"] == key for item in actions) for key in priority},
        "scope": "Mechanical queue from saved position limits, cached data, and the latest transparent decision. It never submits an order.",
    }


def rebalance_portfolio(
    path: Path, user_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise InputError("Rebalance targets must be a non-empty list.")
    targets: dict[str, Decimal] = {}
    for raw in raw_targets:
        if not isinstance(raw, dict):
            raise InputError("Each rebalance target must be an object.")
        symbol = normalize_symbol(raw.get("symbol"))
        if symbol in targets:
            raise InputError(f"Duplicate rebalance target for {symbol}.")
        targets[symbol] = decimal_parameter(
            raw.get("target_percent"), f"{symbol} target percent",
            minimum=Decimal("0"), maximum=Decimal("100"),
        )
    total_target = sum(targets.values(), Decimal(0))
    if total_target > 100:
        raise InputError("Target percentages cannot exceed 100%.")
    with open_db(path) as db:
        performance = _portfolio_performance_from_db(db, user_id)
        positions = {
            item["symbol"]: item
            for item in performance["positions"]
            if item["asset_type"] == "equity"
        }
        account_value = Decimal(performance["estimated_account_value"])
        rows = []
        for symbol, target_percent in targets.items():
            position = positions.get(symbol)
            if position:
                price = Decimal(position["reference_price"])
                current_value = Decimal(position["market_value"])
                current_quantity = Decimal(position["quantity"])
            else:
                market = db.execute(
                    "SELECT close_micros FROM market_daily WHERE symbol = ? "
                    "ORDER BY trading_date DESC, fetched_at DESC LIMIT 1",
                    (symbol,),
                ).fetchone()
                if not market:
                    raise InputError(f"Refresh {symbol} daily bars before calculating a rebalance.")
                price = Decimal(decimal_string(int(market["close_micros"])))
                current_value = Decimal(0)
                current_quantity = Decimal(0)
            target_value = account_value * target_percent / 100
            delta_value = target_value - current_value
            share_delta = delta_value / price if price else Decimal(0)
            rows.append(
                {
                    "symbol": symbol,
                    "target_percent": format(target_percent.normalize(), "f"),
                    "reference_price": format(price.normalize(), "f"),
                    "current_quantity": format(current_quantity.normalize(), "f"),
                    "current_value": format(current_value.quantize(Decimal("0.01")), "f"),
                    "target_value": format(target_value.quantize(Decimal("0.01")), "f"),
                    "value_adjustment": format(delta_value.quantize(Decimal("0.01")), "f"),
                    "share_adjustment": format(share_delta.quantize(Decimal("0.0001")), "f"),
                    "action": "paper_buy" if share_delta > 0 else "paper_sell" if share_delta < 0 else "none",
                }
            )
    return {
        "account_value": format(account_value.quantize(Decimal("0.01")), "f"),
        "target_total_percent": format(total_target.normalize(), "f"),
        "cash_target_percent": format((Decimal(100) - total_target).normalize(), "f"),
        "rows": rows,
        "mode": "simulation_only",
        "disclaimer": "Share adjustments are a paper calculation using cached reference prices; no orders are created or routed.",
    }


def portfolio_risk(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        return _portfolio_risk_from_db(db, user_id)


def _price_alert_rows(db: sqlite3.Connection, user_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT a.*, m.close_micros AS latest_micros, m.trading_date, m.source "
        "FROM price_alerts a LEFT JOIN market_daily m ON m.rowid = ("
        "SELECT md.rowid FROM market_daily md WHERE md.symbol = a.symbol "
        "ORDER BY md.trading_date DESC, md.fetched_at DESC, md.source LIMIT 1) "
        "WHERE a.user_id = ? ORDER BY a.created_at DESC, a.id DESC",
        (user_id,),
    ).fetchall()


def _serialize_price_alert(row: sqlite3.Row) -> dict[str, Any]:
    latest_micros = row["latest_micros"]
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "direction": row["direction"],
        "threshold": decimal_string(row["threshold_micros"]),
        "is_triggered": bool(row["is_triggered"]),
        "latest_price": decimal_string(latest_micros) if latest_micros is not None else None,
        "trading_date": row["trading_date"],
        "source": row["source"],
        "created_at": row["created_at"],
    }


def _evaluate_price_alerts(db: sqlite3.Connection, user_id: str) -> None:
    for row in _price_alert_rows(db, user_id):
        latest = row["latest_micros"]
        if latest is None:
            continue
        met = latest >= row["threshold_micros"] if row["direction"] == "above" else latest <= row["threshold_micros"]
        changed_at = now_iso()
        if met and not row["is_triggered"]:
            updated = db.execute(
                "UPDATE price_alerts SET is_triggered = 1, updated_at = ? "
                "WHERE id = ? AND user_id = ? AND is_triggered = 0",
                (changed_at, row["id"], user_id),
            )
            if updated.rowcount:
                _append_sync_event(
                    db,
                    user_id,
                    "price_alert_trigger",
                    str(uuid4()),
                    "upsert",
                    {
                        "alert_id": row["id"],
                        "symbol": row["symbol"],
                        "direction": row["direction"],
                        "threshold": decimal_string(row["threshold_micros"]),
                        "observed_price": decimal_string(latest),
                        "trading_date": row["trading_date"],
                        "source": row["source"],
                        "triggered_at": changed_at,
                    },
                )
        elif not met and row["is_triggered"]:
            db.execute(
                "UPDATE price_alerts SET is_triggered = 0, updated_at = ? "
                "WHERE id = ? AND user_id = ? AND is_triggered = 1",
                (changed_at, row["id"], user_id),
            )


def _alert_center_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    _evaluate_price_alerts(db, user_id)
    trigger_rows = db.execute(
        "SELECT payload_json, changed_at FROM sync_events "
        "WHERE user_id = ? AND entity_type = 'price_alert_trigger' "
        "ORDER BY revision DESC LIMIT 20",
        (user_id,),
    ).fetchall()
    triggers = []
    for row in trigger_rows:
        payload = json.loads(row["payload_json"])
        payload.setdefault("triggered_at", row["changed_at"])
        triggers.append(payload)
    return {
        "rules": [_serialize_price_alert(row) for row in _price_alert_rows(db, user_id)],
        "recent_triggers": triggers,
        "freshness": "end_of_day",
        "disclaimer": "Alerts compare cached closes with thresholds you set; they are not live quotes or trade instructions.",
    }


def alert_center(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        return _alert_center_from_db(db, user_id)


def create_price_alert(path: Path, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    symbol = normalize_symbol(payload.get("symbol"))
    direction = str(payload.get("direction") or "").lower()
    if direction not in {"above", "below"}:
        raise InputError("Alert direction must be above or below.")
    threshold_micros = to_micros(payload.get("threshold"), "Threshold")
    alert_id = str(uuid4())
    created_at = now_iso()
    with open_db(path) as db:
        if db.execute("SELECT COUNT(*) FROM price_alerts WHERE user_id = ?", (user_id,)).fetchone()[0] >= 50:
            raise ApiError(409, "A local account can keep up to 50 active price alerts.")
        try:
            db.execute(
                "INSERT INTO price_alerts(id, user_id, symbol, direction, threshold_micros, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (alert_id, user_id, symbol, direction, threshold_micros, created_at, created_at),
            )
        except sqlite3.IntegrityError:
            raise ApiError(409, "That price alert already exists.") from None
        _append_sync_event(
            db,
            user_id,
            "price_alert",
            alert_id,
            "upsert",
            {"id": alert_id, "symbol": symbol, "direction": direction, "threshold": decimal_string(threshold_micros), "created_at": created_at},
        )
        _evaluate_price_alerts(db, user_id)
        row = next(row for row in _price_alert_rows(db, user_id) if row["id"] == alert_id)
        return _serialize_price_alert(row)


def delete_price_alert(path: Path, user_id: str, alert_id: str) -> bool:
    if not 1 <= len(alert_id) <= 64:
        raise InputError("Invalid alert ID.")
    with open_db(path) as db:
        row = db.execute(
            "SELECT symbol, direction, threshold_micros FROM price_alerts WHERE id = ? AND user_id = ?",
            (alert_id, user_id),
        ).fetchone()
        if not row:
            return False
        db.execute("DELETE FROM price_alerts WHERE id = ? AND user_id = ?", (alert_id, user_id))
        _append_sync_event(
            db,
            user_id,
            "price_alert",
            alert_id,
            "delete",
            {"id": alert_id, "symbol": row["symbol"], "direction": row["direction"], "threshold": decimal_string(row["threshold_micros"])},
        )
    return True


def _serialize_investor_profile(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "strategy_style": row["strategy_style"],
        "time_horizon": row["time_horizon"],
        "paper_account_size": decimal_string(row["paper_account_micros"]),
        "max_position_percent": decimal_string(row["max_position_percent_micros"]),
        "risk_per_trade_percent": decimal_string(row["risk_per_trade_percent_micros"]),
        "minimum_reward_risk": decimal_string(row["minimum_reward_risk_micros"]),
        "daily_loss_limit": decimal_string(row["daily_loss_limit_micros"]),
        "options_defined_risk_only": bool(row["options_defined_risk_only"]),
        "updated_at": row["updated_at"],
        "scope": "User-supplied planning defaults; not a suitability assessment or recommendation.",
    }


def _investor_profile_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM investor_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        raise RuntimeError("Investor profile is missing for this account.")
    return _serialize_investor_profile(row)


def investor_profile(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        return _investor_profile_from_db(db, user_id)


def update_investor_profile(
    path: Path, user_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    strategy_style = str(payload.get("strategy_style") or "").lower()
    time_horizon = str(payload.get("time_horizon") or "").lower()
    if strategy_style not in {"balanced", "growth", "value", "income", "momentum"}:
        raise InputError("Strategy style is invalid.")
    if time_horizon not in {"day", "swing", "long_term"}:
        raise InputError("Time horizon is invalid.")
    defined_risk_only = payload.get("options_defined_risk_only")
    if not isinstance(defined_risk_only, bool):
        raise InputError("Options risk preference must be true or false.")

    values = {
        "paper_account_micros": decimal_parameter(
            payload.get("paper_account_size"), "Paper account size",
            minimum=Decimal("100"), maximum=Decimal("1000000000"),
        ),
        "max_position_percent_micros": decimal_parameter(
            payload.get("max_position_percent"), "Maximum position percent",
            minimum=Decimal("0.1"), maximum=Decimal("100"),
        ),
        "risk_per_trade_percent_micros": decimal_parameter(
            payload.get("risk_per_trade_percent"), "Risk per trade percent",
            minimum=Decimal("0.01"), maximum=Decimal("10"),
        ),
        "minimum_reward_risk_micros": decimal_parameter(
            payload.get("minimum_reward_risk"), "Minimum reward/risk",
            minimum=Decimal("0.1"), maximum=Decimal("20"),
        ),
        "daily_loss_limit_micros": decimal_parameter(
            payload.get("daily_loss_limit"), "Daily loss limit",
            minimum=Decimal("0.01"), maximum=Decimal("1000000000"),
        ),
    }
    scaled = {name: int(value * SCALE) for name, value in values.items()}
    updated_at = now_iso()
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE investor_profiles SET strategy_style = ?, time_horizon = ?, "
            "paper_account_micros = ?, max_position_percent_micros = ?, "
            "risk_per_trade_percent_micros = ?, minimum_reward_risk_micros = ?, "
            "daily_loss_limit_micros = ?, options_defined_risk_only = ?, updated_at = ? "
            "WHERE user_id = ?",
            (
                strategy_style,
                time_horizon,
                scaled["paper_account_micros"],
                scaled["max_position_percent_micros"],
                scaled["risk_per_trade_percent_micros"],
                scaled["minimum_reward_risk_micros"],
                scaled["daily_loss_limit_micros"],
                int(defined_risk_only),
                updated_at,
                user_id,
            ),
        )
        result = _investor_profile_from_db(db, user_id)
        _append_sync_event(db, user_id, "investor_profile", user_id, "upsert", result)
    return result


def _device_rows(db: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            "SELECT id, name, platform, last_revision, created_at, last_seen_at "
            "FROM devices WHERE user_id = ? ORDER BY last_seen_at DESC, id",
            (user_id,),
        ).fetchall()
    ]


def list_devices(path: Path, user_id: str) -> list[dict[str, Any]]:
    with open_db(path) as db:
        return _device_rows(db, user_id)


def delete_device(path: Path, user_id: str, device_id: str, token_hash: str) -> bool:
    device_id = str(device_id or "").strip()
    if not DEVICE_ID_RE.fullmatch(device_id):
        raise InputError("Invalid device ID.")
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            "SELECT device_id FROM sessions WHERE token_hash = ? AND user_id = ?",
            (token_hash, user_id),
        ).fetchone()
        if current and current["device_id"] == device_id:
            raise ApiError(409, "Sign out on this device instead of removing it.")
        row = db.execute(
            "SELECT name, platform FROM devices WHERE id = ? AND user_id = ?",
            (device_id, user_id),
        ).fetchone()
        if not row:
            return False
        db.execute(
            "DELETE FROM sessions WHERE user_id = ? AND device_id = ?", (user_id, device_id)
        )
        db.execute("DELETE FROM devices WHERE id = ? AND user_id = ?", (device_id, user_id))
        _append_sync_event(
            db,
            user_id,
            "device",
            device_id,
            "delete",
            {"name": row["name"], "platform": row["platform"]},
        )
    return True


def delete_account(path: Path, user_id: str, payload: dict[str, Any]) -> dict[str, bool]:
    if str(payload.get("confirmation") or "") != "DELETE":
        raise InputError('Type "DELETE" exactly to confirm account deletion.')
    password = str(payload.get("password") or "")
    with open_db(path) as db:
        row = db.execute(
            "SELECT password_salt, password_hash FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            raise ApiError(404, "Account was not found.")
        candidate = _hash_password(password, bytes(row["password_salt"]))
        if not hmac.compare_digest(candidate, bytes(row["password_hash"])):
            raise ApiError(403, "Password is incorrect.")
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"deleted": True}


def _start_collection_run(
    path: Path, user_id: str | None, job_type: str, requested_count: int
) -> str:
    run_id = str(uuid4())
    with open_db(path) as db:
        db.execute(
            "INSERT INTO data_collection_runs(id, user_id, job_type, status, requested_count, "
            "started_at) VALUES (?, ?, ?, 'running', ?, ?)",
            (run_id, user_id, job_type, max(0, requested_count), now_iso()),
        )
    return run_id


def _finish_collection_run(
    path: Path,
    run_id: str,
    status: str,
    completed_count: int,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    with open_db(path) as db:
        db.execute(
            "UPDATE data_collection_runs SET status = ?, completed_count = ?, result_json = ?, "
            "error_text = ?, finished_at = ? WHERE id = ?",
            (
                status,
                max(0, completed_count),
                json.dumps(result or {}, separators=(",", ":")),
                error[:1000],
                now_iso(),
                run_id,
            ),
        )


def _collection_runs_from_db(
    db: sqlite3.Connection, user_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT id, job_type, status, requested_count, completed_count, result_json, "
        "error_text, started_at, finished_at FROM data_collection_runs "
        "WHERE user_id = ? ORDER BY started_at DESC, id DESC LIMIT ?",
        (user_id, max(1, min(limit, 100))),
    ).fetchall()
    return [
        {
            **{key: row[key] for key in (
                "id", "job_type", "status", "requested_count", "completed_count",
                "error_text", "started_at", "finished_at",
            )},
            "result": json.loads(row["result_json"]),
        }
        for row in rows
    ]


def _backup_directory(path: Path) -> Path:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def _backup_files(path: Path) -> list[Path]:
    return sorted(
        (
            item for item in _backup_directory(path).glob("investor-lab-*.sqlite3")
            if re.fullmatch(r"investor-lab-[A-Za-z0-9-]+\.sqlite3", item.name)
        ),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def _backup_retention() -> int:
    try:
        return max(1, min(int(os.environ.get("INVESTORLAB_BACKUP_RETENTION", "30")), 365))
    except ValueError:
        return 30


def _prune_database_backups(path: Path, keep: int = 30) -> int:
    removed = 0
    for backup in _backup_files(path)[max(1, min(keep, 365)):]:
        backup.unlink()
        for suffix in ("-wal", "-shm"):
            backup.with_name(backup.name + suffix).unlink(missing_ok=True)
        removed += 1
    return removed


def create_database_backup(
    path: Path, user_id: str | None, job_type: str = "manual_backup"
) -> dict[str, Any]:
    backup_dir = _backup_directory(path)
    filename = f"investor-lab-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}.sqlite3"
    backup_path = backup_dir / filename
    run_id = _start_collection_run(path, user_id, job_type, 1)
    try:
        with open_db(path) as source, sqlite3.connect(backup_path) as destination:
            source.backup(destination)
            destination.execute("PRAGMA journal_mode = DELETE")
            integrity = str(destination.execute("PRAGMA quick_check").fetchone()[0])
        if integrity != "ok":
            raise ApiError(500, f"Backup integrity check returned {integrity}.")
        result = {
            "filename": filename,
            "size_bytes": backup_path.stat().st_size,
            "created_at": now_iso(),
            "integrity": integrity,
            "restore_status": "verified; restore requires an explicit maintenance action",
            "job_type": job_type,
        }
        result["pruned_backups"] = _prune_database_backups(
            path, _backup_retention()
        )
        _finish_collection_run(path, run_id, "completed", 1, result)
        return result
    except Exception as error:
        if backup_path.exists():
            backup_path.unlink()
        _finish_collection_run(path, run_id, "failed", 0, error=str(error))
        raise


def list_database_backups(path: Path) -> list[dict[str, Any]]:
    results = []
    for backup in _backup_files(path)[:100]:
        try:
            with sqlite3.connect(f"file:{backup}?mode=ro&immutable=1", uri=True) as db:
                integrity = str(db.execute("PRAGMA quick_check").fetchone()[0])
                schema_version = int(db.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.Error as error:
            integrity = f"error: {error}"
            schema_version = 0
        results.append({
            "filename": backup.name,
            "size_bytes": backup.stat().st_size,
            "modified_at": datetime.fromtimestamp(backup.stat().st_mtime, timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"),
            "integrity": integrity,
            "schema_version": schema_version,
            "restorable": integrity == "ok" and schema_version == SCHEMA_VERSION,
        })
    return results


def restore_database_backup(
    path: Path, user_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    filename = str(payload.get("filename") or "").strip()
    confirmation = str(payload.get("confirmation") or "").strip()
    if not re.fullmatch(r"investor-lab-[A-Za-z0-9-]+\.sqlite3", filename):
        raise InputError("Select a generated Investor Lab backup.")
    if confirmation != f"RESTORE {filename}":
        raise InputError(f"Type RESTORE {filename} to confirm recovery.")
    source_path = _backup_directory(path) / filename
    if not source_path.is_file():
        raise ApiError(404, "Backup was not found.")
    with DB_MAINTENANCE_LOCK:
        with sqlite3.connect(f"file:{source_path}?mode=ro&immutable=1", uri=True) as source:
            integrity = str(source.execute("PRAGMA quick_check").fetchone()[0])
            schema_version = int(source.execute("PRAGMA user_version").fetchone()[0])
            account = source.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if integrity != "ok":
            raise InputError(f"Backup integrity check returned {integrity}.")
        if schema_version != SCHEMA_VERSION:
            raise InputError(
                f"Backup schema {schema_version} cannot replace the active schema {SCHEMA_VERSION}."
            )
        if not account:
            raise InputError("Backup does not contain the signed-in account.")
        safety_backup = create_database_backup(path, user_id, "manual_backup")
        with sqlite3.connect(f"file:{source_path}?mode=ro&immutable=1", uri=True) as source, open_db(path) as destination:
            source.backup(destination)
            restored_integrity = str(destination.execute("PRAGMA quick_check").fetchone()[0])
        if restored_integrity != "ok":
            raise ApiError(500, "Restored database failed its integrity check.")
    return {
        "restored": True,
        "filename": filename,
        "integrity": restored_integrity,
        "schema_version": schema_version,
        "safety_backup": safety_backup["filename"],
        "restored_at": now_iso(),
        "session_notice": "Refresh Web and iOS after restore; records now match the selected snapshot.",
    }


def run_scheduled_backup(path: Path) -> dict[str, Any] | None:
    today = datetime.now(timezone.utc).date().isoformat()
    with open_db(path) as db:
        completed = db.execute(
            "SELECT 1 FROM data_collection_runs WHERE job_type = 'scheduled_backup' "
            "AND status = 'completed' AND substr(started_at, 1, 10) = ? LIMIT 1",
            (today,),
        ).fetchone()
        owner = db.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
    if completed or not owner:
        return None
    return create_database_backup(path, str(owner["id"]), "scheduled_backup")


def system_health(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        user_row = db.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        is_owner = bool(user_row and user_row["role"] == "owner")
        user_symbols = _user_symbols_from_db(db, user_id)
        placeholders = ",".join("?" for _ in user_symbols)
        quick_check = str(db.execute("PRAGMA quick_check").fetchone()[0])
        counts = {
            "watchlist": int(
                db.execute("SELECT COUNT(*) FROM watchlist WHERE user_id = ?", (user_id,)).fetchone()[0]
            ),
            "trades": int(
                db.execute("SELECT COUNT(*) FROM trades WHERE user_id = ?", (user_id,)).fetchone()[0]
            ),
            "plans": int(
                db.execute("SELECT COUNT(*) FROM research_plans WHERE user_id = ?", (user_id,)).fetchone()[0]
            ),
            "journal_entries": int(
                db.execute("SELECT COUNT(*) FROM journal_entries WHERE user_id = ?", (user_id,)).fetchone()[0]
            ),
            "alerts": int(
                db.execute("SELECT COUNT(*) FROM price_alerts WHERE user_id = ?", (user_id,)).fetchone()[0]
            ),
            "imports": int(
                db.execute("SELECT COUNT(*) FROM portfolio_imports WHERE user_id = ?", (user_id,)).fetchone()[0]
            ),
            "decisions": int(
                db.execute("SELECT COUNT(*) FROM decision_runs WHERE user_id = ?", (user_id,)).fetchone()[0]
            ),
            "devices": int(
                db.execute("SELECT COUNT(*) FROM devices WHERE user_id = ?", (user_id,)).fetchone()[0]
            ),
            "paper_orders": int(
                db.execute("SELECT COUNT(*) FROM paper_order_intents WHERE user_id = ?", (user_id,)).fetchone()[0]
            ),
            "scanner_presets": int(
                db.execute("SELECT COUNT(*) FROM scanner_presets WHERE user_id = ?", (user_id,)).fetchone()[0]
            ),
            "notification_rules": int(
                db.execute("SELECT COUNT(*) FROM notification_rules WHERE user_id = ?", (user_id,)).fetchone()[0]
            ),
            "research_reports": int(
                db.execute("SELECT COUNT(*) FROM research_reports WHERE user_id = ?", (user_id,)).fetchone()[0]
            ),
        }
        paper_control = _paper_order_control_from_db(db, user_id)
        market_rows = (
            db.execute(
                f"SELECT symbol, COUNT(*) AS bar_count, MAX(trading_date) AS latest_trading_date, "
                f"MAX(fetched_at) AS fetched_at FROM market_daily WHERE symbol IN ({placeholders}) "
                "GROUP BY symbol ORDER BY symbol",
                user_symbols,
            ).fetchall()
            if user_symbols else []
        )
        adjustment_count = (
            int(db.execute(
                f"SELECT COUNT(*) FROM market_adjustments WHERE symbol IN ({placeholders})",
                user_symbols,
            ).fetchone()[0])
            if user_symbols else 0
        )
        collection_runs = _collection_runs_from_db(db, user_id, 10)
        paper_snapshot = db.execute(
            "SELECT fetched_at, account_status, position_count, open_order_count "
            "FROM paper_account_snapshots WHERE user_id = ? ORDER BY fetched_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    today = date.today()
    symbols = []
    for row in market_rows:
        latest_date = date.fromisoformat(row["latest_trading_date"])
        symbols.append(
            {
                **dict(row),
                "is_stale": (today - latest_date).days > 7,
            }
        )
    backups = _backup_files(path) if is_owner else []
    latest_backup = None
    if backups:
        backup = backups[0]
        latest_backup = {
            "filename": backup.name,
            "size_bytes": backup.stat().st_size,
            "modified_at": datetime.fromtimestamp(backup.stat().st_mtime, timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        }
    database_size = sum(
        candidate.stat().st_size
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
        if candidate.exists()
    )
    backup_age_hours = (
        round((datetime.now(timezone.utc).timestamp() - backups[0].stat().st_mtime) / 3600, 1)
        if backups else None
    )
    stale_count = sum(bool(item["is_stale"]) for item in symbols)
    alpha_key, alpha_source = _alpha_vantage_api_key()
    alpaca_key, alpaca_secret, alpaca_source = _alpaca_credentials()
    security_audit = read_security_events(path, user_id=user_id, limit=100)
    unusual_logins = sum(bool(item.get("unusual")) for item in security_audit["events"])
    gateway_mode = os.environ.get("INVESTORLAB_ACCESS_GATEWAY", "").lower()
    gateway_ready = gateway_mode != "cloudflare" or os.environ.get("INVESTORLAB_TRUST_PROXY") == "1"
    checks = [
        {"key": "database", "status": "pass" if quick_check == "ok" else "fail", "detail": f"SQLite quick_check: {quick_check}."},
        {
            "key": "backup",
            "status": "pass" if not is_owner or (backup_age_hours is not None and backup_age_hours <= 48) else "attention",
            "detail": (
                "Backup maintenance is available only to the workspace owner."
                if not is_owner
                else f"Latest verified backup is {backup_age_hours} hours old."
                if backup_age_hours is not None
                else "No backup exists yet."
            ),
        },
        {"key": "market_cache", "status": "pass" if symbols and stale_count == 0 else "attention", "detail": f"{len(symbols)} symbols cached; {stale_count} stale."},
        {"key": "daily_provider", "status": "pass" if alpha_key else "attention", "detail": f"Alpha Vantage configured from {alpha_source}." if alpha_key else "Alpha Vantage credentials are not configured."},
        {"key": "intraday_provider", "status": "pass" if alpaca_key and alpaca_secret else "attention", "detail": f"Alpaca Paper/IEX configured from {alpaca_source}." if alpaca_key and alpaca_secret else "Alpaca Paper/IEX credentials are not configured."},
        {"key": "scheduler", "status": "pass" if SCHEDULER_STATE["running"] else "attention", "detail": f"Scheduler last cycle: {SCHEDULER_STATE['last_cycle_at']}." if SCHEDULER_STATE["last_cycle_at"] else "Scheduler has not completed a cycle in this process."},
        {"key": "public_url", "status": "pass" if os.environ.get("INVESTORLAB_PUBLIC_URL", "").strip() else "attention", "detail": "Stable HTTPS URL configured." if os.environ.get("INVESTORLAB_PUBLIC_URL", "").strip() else "No public URL configured."},
        {"key": "paper_account", "status": "pass" if paper_snapshot else "attention", "detail": f"Latest read-only paper snapshot: {paper_snapshot['fetched_at']}." if paper_snapshot else "Paper account has not been synchronized."},
        {"key": "paper_order_routing", "status": "pass", "detail": "Alpaca Paper routing is enabled behind explicit acknowledgement and local limits." if paper_control["enabled"] else "Alpaca Paper routing is safely locked."},
        {
            "key": "security_audit",
            "status": "pass" if security_audit["invalid_lines"] == 0 else "attention",
            "detail": f"{len(security_audit['events'])} recent events; {unusual_logins} unusual successful logins; {security_audit['invalid_lines']} invalid audit lines.",
        },
        {
            "key": "access_gateway",
            "status": "pass" if gateway_ready else "fail",
            "detail": (
                "Cloudflare Access identity binding is active."
                if gateway_mode == "cloudflare" and gateway_ready
                else "Cloudflare Access requires INVESTORLAB_TRUST_PROXY=1."
                if gateway_mode == "cloudflare"
                else "Direct local authentication is active."
            ),
        },
    ]
    return {
        "app_version": APP_VERSION,
        "status": "healthy" if all(item["status"] == "pass" for item in checks) else "attention",
        "checked_at": now_iso(),
        "schema_version": SCHEMA_VERSION,
        "database": {
            "integrity": quick_check,
            "size_bytes": database_size,
            "latest_backup": latest_backup,
            "backup_count": len(backups),
            "backup_retention": _backup_retention(),
            "backup_access": "owner" if is_owner else "owner_only",
        },
        "account_counts": counts,
        "market_cache": {
            "symbol_count": len(symbols),
            "bar_count": sum(int(row["bar_count"]) for row in symbols),
            "adjustment_count": adjustment_count,
            "symbols": symbols,
        },
        "automation": {
            "scheduler_running": bool(SCHEDULER_STATE["running"]),
            "scheduler_started_at": SCHEDULER_STATE["started_at"],
            "scheduler_last_cycle_at": SCHEDULER_STATE["last_cycle_at"],
            "scheduler_last_error": SCHEDULER_STATE["last_error"],
            "public_url": os.environ.get("INVESTORLAB_PUBLIC_URL", "").strip() or None,
            "adjusted_history_enabled": os.environ.get("INVESTORLAB_ADJUSTED_DAILY") == "1",
            "intraday_collection_enabled": os.environ.get("INVESTORLAB_INTRADAY_COLLECTION") == "1",
            "option_collection_enabled": os.environ.get("INVESTORLAB_OPTION_COLLECTION", "1") == "1",
            "recent_runs": collection_runs,
            "scheduled_backup_enabled": True,
            "scheduled_reports_enabled": True,
        },
        "security_audit": {
            "recent_event_count": len(security_audit["events"]),
            "unusual_login_count": unusual_logins,
            "invalid_line_count": security_audit["invalid_lines"],
            "gateway_mode": gateway_mode or "local",
        },
        "checks": checks,
        "paper_account": dict(paper_snapshot) if paper_snapshot else None,
        "release_readiness": {
            "testflight": "archive tooling ready; App Store Connect upload not performed",
            "remote_notifications": "local notifications active; APNs provider credentials not configured",
            "broker_execution": "Alpaca Paper only; disabled by default behind explicit acknowledgement and local risk controls",
        },
    }


def run_system_health_check(path: Path, user_id: str) -> dict[str, Any]:
    run_id = _start_collection_run(path, user_id, "health_check", 9)
    try:
        result = system_health(path, user_id)
        passed = sum(item["status"] == "pass" for item in result["checks"])
        status = "completed" if passed == len(result["checks"]) else "partial"
        _finish_collection_run(path, run_id, status, passed, {"checks": result["checks"]})
        return result
    except Exception as error:
        _finish_collection_run(path, run_id, "failed", 0, error=str(error))
        raise


def export_account_data(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        user = db.execute(
            "SELECT id, email, display_name, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not user:
            raise ApiError(404, "Account was not found.")
        events = [
            {
                **dict(row),
                "payload": json.loads(row["payload_json"]) if row["payload_json"] else None,
            }
            for row in db.execute(
                "SELECT revision, entity_type, entity_id, operation, payload_json, changed_at "
                "FROM sync_events WHERE user_id = ? ORDER BY revision",
                (user_id,),
            ).fetchall()
        ]
        for event in events:
            event.pop("payload_json", None)
        return {
            "format": "investor-lab-account-export",
            "format_version": 1,
            "schema_version": SCHEMA_VERSION,
            "exported_at": now_iso(),
            "account": dict(user),
            "investor_profile": _investor_profile_from_db(db, user_id),
            "strategy_templates": _strategy_templates_from_db(db, user_id),
            "strategy_versions": _strategy_versions_from_db(db, user_id),
            "paper_account": _paper_account_from_db(db, user_id),
            "devices": _device_rows(db, user_id),
            "watchlist": _watchlist_rows(db, user_id),
            "watchlist_research": _watchlist_research_from_db(db, user_id),
            "portfolio": _portfolio_from_db(db, user_id),
            "trades": _trade_rows(db, user_id, 1_000_000),
            "plans": _plan_rows(db, user_id, limit=None),
            "plan_reviews": _plan_review_rows(db, user_id, limit=None),
            "journal_entries": _journal_rows(db, user_id, limit=None),
            "alerts": _alert_center_from_db(db, user_id),
            "portfolio_imports": _portfolio_import_rows(db, user_id, 1_000_000),
            "decision_settings": _decision_settings_from_db(db, user_id),
            "decision_runs": _decision_rows(db, user_id, limit=1_000_000),
            "paper_order_control": _paper_order_control_from_db(db, user_id),
            "paper_orders": [
                _serialize_paper_order(row)
                for row in db.execute(
                    "SELECT * FROM paper_order_intents WHERE user_id = ? ORDER BY created_at DESC, id DESC",
                    (user_id,),
                ).fetchall()
            ],
            "scanner_presets": _scanner_preset_rows(db, user_id),
            "notification_rules": [
                {
                    "id": row["id"], "kind": row["kind"], "symbol": row["symbol"],
                    "config": json.loads(row["config_json"]), "enabled": bool(row["enabled"]),
                    "last_triggered_at": row["last_triggered_at"],
                    "created_at": row["created_at"], "updated_at": row["updated_at"],
                }
                for row in db.execute(
                    "SELECT * FROM notification_rules WHERE user_id = ? ORDER BY updated_at DESC",
                    (user_id,),
                ).fetchall()
            ],
            "research_reports": [
                json.loads(row["content_json"])
                for row in db.execute(
                    "SELECT content_json FROM research_reports WHERE user_id = ? "
                    "ORDER BY report_date DESC, created_at DESC",
                    (user_id,),
                ).fetchall()
            ],
            "sync_events": events,
        }


def snapshot(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        alerts = _alert_center_from_db(db, user_id)
        plan_center = _plan_review_center_from_db(db, user_id)
        sec_events = _sec_event_center_from_db(db, user_id)
        return {
            "as_of": now_iso(),
            "revision": latest_revision(db, user_id),
            "investor_profile": _investor_profile_from_db(db, user_id),
            "strategy_templates": _strategy_templates_from_db(db, user_id),
            "strategy_versions": _strategy_versions_from_db(db, user_id),
            "paper_account": _paper_account_from_db(db, user_id),
            "devices": _device_rows(db, user_id),
            "watchlist": _watchlist_rows(db, user_id),
            "watchlist_research": _watchlist_research_from_db(db, user_id),
            "portfolio": _portfolio_from_db(db, user_id),
            "portfolio_risk": _portfolio_risk_from_db(db, user_id),
            "portfolio_performance": _portfolio_performance_from_db(db, user_id),
            "portfolio_actions": _portfolio_actions_from_db(db, user_id),
            "recent_trades": _trade_rows(db, user_id),
            "recent_imports": _portfolio_import_rows(db, user_id),
            "recent_plans": _plan_rows(db, user_id),
            "plan_review_center": plan_center,
            "day_trade_guardrails": _day_trade_guardrails_from_db(db, user_id),
            "journal_entries": _journal_rows(db, user_id),
            "review_stats": _review_stats_from_db(db, user_id),
            "alerts": alerts,
            "decision_center": _decision_center_from_db(db, user_id),
            "watchlist_screener": _watchlist_screener_from_db(db, user_id),
            "sec_events": sec_events,
            "earnings_calendar": _earnings_calendar_for_user(
                db, user_id, _sec_cached(db, "earnings-calendar:3month")
            ),
            "daily_briefing": _daily_briefing_from_db(
                db, user_id, alerts=alerts, plan_center=plan_center, sec_events=sec_events
            ),
        }


def sync_feed(path: Path, user_id: str, since: int, limit: int = 200) -> dict[str, Any]:
    if not 0 <= since <= 2**63 - 1:
        raise InputError("Sync cursor must be between 0 and 9223372036854775807.")
    limit = max(1, min(limit, 500))
    with open_db(path) as db:
        _evaluate_price_alerts(db, user_id)
        rows = db.execute(
            "SELECT revision, entity_type, entity_id, operation, payload_json, changed_at "
            "FROM sync_events WHERE user_id = ? AND revision > ? ORDER BY revision LIMIT ?",
            (user_id, since, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        cursor = int(rows[-1]["revision"]) if rows else since
        latest = latest_revision(db, user_id)
        events = [
            {
                "revision": row["revision"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "operation": row["operation"],
                "payload": json.loads(row["payload_json"]) if row["payload_json"] else None,
                "changed_at": row["changed_at"],
            }
            for row in rows
        ]
    return {
        "as_of": now_iso(),
        "from_revision": since,
        "cursor": cursor,
        "latest_revision": latest,
        "has_more": has_more,
        "events": events,
        "snapshot": snapshot(path, user_id),
    }


def register_device(
    path: Path, user_id: str, token_hash: str, payload: dict[str, Any]
) -> dict[str, Any]:
    device_id = str(payload.get("device_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    platform = str(payload.get("platform") or "").lower()
    if not DEVICE_ID_RE.fullmatch(device_id):
        raise InputError("Device ID must be 8-128 safe characters.")
    if not 1 <= len(name) <= 80:
        raise InputError("Device name must be 1-80 characters.")
    if platform not in {"web", "ios"}:
        raise InputError("Platform must be web or ios.")
    current = now_iso()
    with open_db(path) as db:
        session = db.execute(
            "SELECT device_id, client_type FROM sessions WHERE token_hash = ? AND user_id = ?",
            (token_hash, user_id),
        ).fetchone()
        if not session:
            raise ApiError(401, "Authentication required.")
        if session["device_id"] != device_id or session["client_type"] != platform:
            raise ApiError(409, "A session cannot be rebound to another device or client type.")
        db.execute(
            "INSERT INTO devices(id, user_id, name, platform, created_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
            "last_seen_at = excluded.last_seen_at WHERE devices.user_id = excluded.user_id "
            "AND devices.platform = excluded.platform",
            (device_id, user_id, name, platform, current, current),
        )
        row = db.execute(
            "SELECT id, name, platform, last_revision, last_seen_at FROM devices "
            "WHERE id = ? AND user_id = ?",
            (device_id, user_id),
        ).fetchone()
        if not row:
            raise ApiError(409, "That device ID belongs to another account.")
    return dict(row)


def acknowledge_sync(path: Path, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    device_id = str(payload.get("device_id") or "").strip()
    try:
        revision = int(payload.get("revision"))
    except (TypeError, ValueError):
        raise InputError("Revision must be an integer.") from None
    if not DEVICE_ID_RE.fullmatch(device_id) or revision < 0:
        raise InputError("Invalid device ID or revision.")
    with open_db(path) as db:
        if revision > latest_revision(db, user_id):
            raise InputError("Revision is newer than the server state.")
        cursor = db.execute(
            "UPDATE devices SET last_revision = MAX(last_revision, ?), last_seen_at = ? "
            "WHERE id = ? AND user_id = ?",
            (revision, now_iso(), device_id, user_id),
        )
        if not cursor.rowcount:
            raise ApiError(404, "Register this device before acknowledging sync.")
    return {"device_id": device_id, "revision": revision}


def market_status(path: Path, user_id: str) -> dict[str, Any]:
    _, configuration_source = _alpha_vantage_api_key()
    _, _, alpaca_source = _alpaca_credentials()
    with open_db(path) as db:
        symbols = _user_symbols_from_db(db, user_id)
        placeholders = ",".join("?" for _ in symbols)
        row = (
            db.execute(
                f"SELECT COUNT(DISTINCT symbol) AS symbols, MAX(fetched_at) AS last_refresh "
                f"FROM market_daily WHERE source = 'alpha_vantage' "
                f"AND symbol IN ({placeholders})",
                symbols,
            ).fetchone()
            if symbols else {"symbols": 0, "last_refresh": None}
        )
    return {
        "provider": "Alpha Vantage",
        "configured": configuration_source != "unconfigured",
        "configuration_source": configuration_source,
        "setup_available": sys.platform == "darwin",
        "freshness": "end_of_day",
        "history_mode": os.environ.get("INVESTORLAB_MARKET_HISTORY", "compact"),
        "adjusted_daily_enabled": os.environ.get("INVESTORLAB_ADJUSTED_DAILY") == "1",
        "cached_symbols": int(row["symbols"]),
        "last_refresh": row["last_refresh"],
        "realtime": {
            "provider": "Alpaca Market Data",
            "configured": alpaca_source != "unconfigured",
            "configuration_source": alpaca_source,
            "feed": "iex",
            "scope": "Real-time IEX-only feed on the free Basic plan; it is not the full consolidated US market.",
        },
    }


def data_source_readiness(path: Path, user_id: str) -> dict[str, Any]:
    status = market_status(path, user_id)
    today = date.today()
    with open_db(path) as db:
        symbols = _user_symbols_from_db(db, user_id)
        placeholders = ",".join("?" for _ in symbols)
        coverage = (
            db.execute(
                f"SELECT symbol, COUNT(*) AS bars, MAX(trading_date) AS latest_date, "
                f"MAX(fetched_at) AS last_refresh FROM market_daily "
                f"WHERE symbol IN ({placeholders}) GROUP BY symbol ORDER BY symbol",
                symbols,
            ).fetchall()
            if symbols else []
        )
        paper = db.execute(
            "SELECT account_status, fetched_at FROM paper_account_snapshots "
            "WHERE user_id = ? ORDER BY fetched_at DESC, id DESC LIMIT 1", (user_id,),
        ).fetchone()
        option_row = db.execute(
            "SELECT COUNT(*) AS snapshots, MAX(fetched_at) AS last_refresh "
            "FROM option_chain_snapshots WHERE user_id = ?", (user_id,),
        ).fetchone()
        sec_row = db.execute(
            "SELECT COUNT(*) AS records, MAX(fetched_at) AS last_refresh FROM sec_cache"
        ).fetchone()
        control = db.execute(
            "SELECT enabled FROM paper_order_controls WHERE user_id = ?", (user_id,)
        ).fetchone()
    eligible = 0
    latest_market_date = None
    for row in coverage:
        latest_market_date = max(latest_market_date or str(row["latest_date"]), str(row["latest_date"]))
        try:
            age_days = (today - date.fromisoformat(str(row["latest_date"]))).days
        except ValueError:
            continue
        if int(row["bars"]) >= 60 and age_days <= 7:
            eligible += 1
    alpha_ready = bool(status["configured"])
    alpaca_ready = bool(status["realtime"]["configured"])
    paper_synced = paper is not None
    paper_orders_enabled = bool(control and control["enabled"])
    next_steps = []
    if not alpha_ready:
        next_steps.append("Save and test an Alpha Vantage key for current end-of-day research.")
    if not alpaca_ready:
        next_steps.append("Save and test Alpaca Paper credentials for IEX, options, and Paper account sync.")
    elif not paper_synced:
        next_steps.append("Synchronize the Alpaca Paper account before testing Paper orders.")
    if alpha_ready and not eligible:
        next_steps.append("Refresh at least one symbol with 60 current daily bars before generating an actionable decision.")
    if not next_steps:
        next_steps.append("Core data sources are ready; keep Paper order routing locked until a deliberate test order.")
    providers = [
        {
            "key": "alpha_vantage", "label": "Alpha Vantage EOD",
            "configured": alpha_ready, "status": "connected" if alpha_ready else "setup_required",
            "detail": f"{status['cached_symbols']} cached symbols; latest {latest_market_date or 'none'}.",
            "last_data_at": status["last_refresh"],
            "capabilities": ["daily OHLCV", "earnings calendar", "decision refresh"],
            "cost": "Personal free key; application cache defaults to 12 hours.",
        },
        {
            "key": "alpaca_paper", "label": "Alpaca Paper + IEX",
            "configured": alpaca_ready, "status": "connected" if alpaca_ready else "setup_required",
            "detail": f"Paper account {'synchronized' if paper_synced else 'not synchronized'}; {int(option_row['snapshots'])} option snapshots.",
            "last_data_at": paper["fetched_at"] if paper else option_row["last_refresh"],
            "capabilities": ["IEX observations", "option snapshots", "Paper account and orders"],
            "cost": "Alpaca Basic/Paper credentials; no Investor Lab routing fee.",
        },
        {
            "key": "sec_edgar", "label": "SEC EDGAR", "configured": True, "status": "public",
            "detail": f"Public read-only access; {int(sec_row['records'])} cached records.",
            "last_data_at": sec_row["last_refresh"],
            "capabilities": ["company facts", "filings", "filing comparison"],
            "cost": "No account, API key, or API fee.",
        },
    ]
    overall = "fully_connected" if alpha_ready and alpaca_ready and paper_synced else "research_ready" if alpha_ready or eligible else "setup_required"
    return {
        "generated_at": now_iso(), "overall": overall, "providers": providers,
        "ready_for": {
            "cached_research": bool(coverage), "current_eod_refresh": alpha_ready,
            "actionable_decisions": eligible > 0, "sec_research": True,
            "realtime_day_trade": alpaca_ready, "live_option_chain": alpaca_ready,
            "paper_account": paper_synced, "paper_orders": alpaca_ready and paper_synced,
        },
        "coverage": {
            "cached_symbols": len(coverage), "decision_ready_symbols": eligible,
            "latest_market_date": latest_market_date,
            "option_snapshots": int(option_row["snapshots"]),
        },
        "paper_orders_enabled": paper_orders_enabled, "next_steps": next_steps,
        "scope": "Derived from saved credential presence and local cache coverage. Connection tests are user-triggered and never place an order.",
    }


def test_data_source_connection(
    path: Path, user_id: str, user_email: str, payload: dict[str, Any]
) -> dict[str, Any]:
    source = str(payload.get("source") or "").strip().lower()
    symbol = normalize_symbol(payload.get("symbol") or "SPY")
    if source == "alpha_vantage":
        api_key, configuration_source = _alpha_vantage_api_key()
        if not api_key:
            raise ApiError(409, "Save an Alpha Vantage key before testing the connection.")
        rows = _alpha_vantage_daily(symbol, api_key)
        return {
            "source": source, "provider": "Alpha Vantage", "connected": True,
            "configuration_source": configuration_source, "tested_at": now_iso(),
            "symbol": symbol, "observations": len(rows),
            "latest_data_date": max(str(row[1]) for row in rows),
            "scope": "Read-only credential test. The returned bars are not stored and one provider request is used.",
        }
    if source == "alpaca_paper":
        key_id, secret, configuration_source = _alpaca_credentials()
        if not key_id or not secret:
            raise ApiError(409, "Save Alpaca Paper credentials before testing the connection.")
        account = _alpaca_trading_json("/v2/account", {}, key_id, secret)
        if not isinstance(account, dict) or not account.get("status"):
            raise ApiError(502, "Alpaca Paper returned a malformed account response.")
        return {
            "source": source, "provider": "Alpaca Paper", "connected": True,
            "configuration_source": configuration_source, "tested_at": now_iso(),
            "account_status": str(account["status"]),
            "trading_blocked": bool(account.get("trading_blocked") or account.get("account_blocked")),
            "scope": "Read-only GET /v2/account test against paper-api.alpaca.markets; no order endpoint is called.",
        }
    if source == "sec_edgar":
        with open_db(path) as db:
            cik, company = _sec_company(symbol, db, user_email)
        return {
            "source": source, "provider": "SEC EDGAR", "connected": True,
            "tested_at": now_iso(), "symbol": symbol, "company": company, "cik": cik,
            "scope": "Public read-only lookup; no SEC account or API key is used.",
        }
    raise InputError("Data source must be alpha_vantage, alpaca_paper, or sec_edgar.")


SEC_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
SEC_QUARTERLY_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A"}
SEC_FACTS = {
    "revenue": (("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "Revenue"), "USD", True),
    "net_income": (("NetIncomeLoss", "ProfitLoss"), "USD", True),
    "operating_cash_flow": (("NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"), "USD", True),
    "capital_expenditure": (("PaymentsToAcquirePropertyPlantAndEquipment", "PurchaseOfPropertyPlantAndEquipment"), "USD", True),
    "assets": (("Assets",), "USD", False),
    "liabilities": (("Liabilities",), "USD", False),
    "equity": (("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "Equity"), "USD", False),
    "diluted_eps": (("EarningsPerShareDiluted", "DilutedEarningsLossPerShare"), "USD/shares", True),
    "dividends_per_share": (("CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid", "DividendsPerShare"), "USD/shares", True),
    "diluted_shares": (("WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfShareOutstandingBasicAndDiluted"), "shares", True),
}


def _sec_cached(db: sqlite3.Connection, key: str, max_age: timedelta | None = None) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT payload_json, fetched_at FROM sec_cache WHERE cache_key = ?", (key,)
    ).fetchone()
    if not row:
        return None
    fetched_at = datetime.fromisoformat(row["fetched_at"].replace("Z", "+00:00"))
    if max_age is not None and datetime.now(timezone.utc) - fetched_at >= max_age:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except json.JSONDecodeError:
        raise ApiError(500, "The local SEC cache is corrupted.") from None
    if not isinstance(payload, dict):
        raise ApiError(500, "The local SEC cache is malformed.")
    return payload


def _store_sec_cache(db: sqlite3.Connection, key: str, payload: dict[str, Any]) -> None:
    db.execute(
        "INSERT INTO sec_cache(cache_key, payload_json, fetched_at) VALUES (?, ?, ?) "
        "ON CONFLICT(cache_key) DO UPDATE SET payload_json=excluded.payload_json, fetched_at=excluded.fetched_at",
        (key, json.dumps(payload, separators=(",", ":")), now_iso()),
    )


def _sec_json(url: str, contact_email: str, size_limit: int = 25_000_000) -> dict[str, Any]:
    contact = normalize_email(os.environ.get("INVESTORLAB_SEC_CONTACT") or contact_email)
    request = Request(
        url,
        headers={
            "User-Agent": f"Investor Lab {contact}",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read(size_limit + 1)
    except HTTPError as error:
        if error.code == 404:
            raise ApiError(404, "SEC EDGAR has no public company facts for this symbol.") from None
        raise ApiError(429 if error.code == 429 else 502, f"SEC EDGAR request failed with HTTP {error.code}.") from None
    except (URLError, TimeoutError) as error:
        reason = getattr(error, "reason", str(error))
        raise ApiError(502, f"SEC EDGAR request failed: {reason}.") from None
    if len(raw) > size_limit:
        raise ApiError(502, "SEC EDGAR returned an unexpectedly large response.")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(502, "SEC EDGAR returned invalid JSON.") from None
    if not isinstance(payload, dict):
        raise ApiError(502, "SEC EDGAR returned a malformed response.")
    return payload


def _sec_company(symbol: str, db: sqlite3.Connection, contact_email: str) -> tuple[str, str]:
    tickers = _sec_cached(db, "company-tickers", timedelta(days=7))
    if tickers is None:
        tickers = _sec_json("https://www.sec.gov/files/company_tickers.json", contact_email, 5_000_000)
        _store_sec_cache(db, "company-tickers", tickers)
    for item in tickers.values():
        if isinstance(item, dict) and str(item.get("ticker") or "").upper() == symbol:
            try:
                cik = f"{int(item['cik_str']):010d}"
            except (KeyError, TypeError, ValueError):
                break
            return cik, str(item.get("title") or symbol)
    raise ApiError(404, f"SEC EDGAR has no public-company match for {symbol}.")


def _sec_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite():
        return None
    return int(number) if number == number.to_integral_value() else float(number)


def _annual_fact_map(
    company_facts: dict[str, Any], tags: tuple[str, ...], unit: str, duration: bool
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    facts = company_facts.get("facts")
    if not isinstance(facts, dict):
        return selected
    for namespace_index, namespace in enumerate(("us-gaap", "ifrs-full")):
        concepts = facts.get(namespace)
        if not isinstance(concepts, dict):
            continue
        for tag_index, tag in enumerate(tags):
            concept = concepts.get(tag)
            units = concept.get("units") if isinstance(concept, dict) else None
            entries = units.get(unit) if isinstance(units, dict) else None
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("form") not in SEC_ANNUAL_FORMS:
                    continue
                end = str(entry.get("end") or "")
                try:
                    end_date = date.fromisoformat(end)
                    if duration:
                        start_date = date.fromisoformat(str(entry.get("start") or ""))
                        if not 300 <= (end_date - start_date).days <= 430:
                            continue
                except ValueError:
                    continue
                value = _sec_number(entry.get("val"))
                if value is None:
                    continue
                rank = (
                    str(entry.get("filed") or ""),
                    -namespace_index,
                    -tag_index,
                )
                if end not in selected or rank > selected[end]["_rank"]:
                    selected[end] = {
                        "value": value,
                        "fiscal_year": entry.get("fy") or end_date.year,
                        "filed": entry.get("filed"),
                        "accession": entry.get("accn"),
                        "_rank": rank,
                    }
    for entry in selected.values():
        entry.pop("_rank", None)
    return selected


def _quarterly_fact_map(
    company_facts: dict[str, Any], tags: tuple[str, ...], unit: str, duration: bool
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    facts = company_facts.get("facts")
    if not isinstance(facts, dict):
        return selected
    for namespace_index, namespace in enumerate(("us-gaap", "ifrs-full")):
        concepts = facts.get(namespace)
        if not isinstance(concepts, dict):
            continue
        for tag_index, tag in enumerate(tags):
            concept = concepts.get(tag)
            units = concept.get("units") if isinstance(concept, dict) else None
            entries = units.get(unit) if isinstance(units, dict) else None
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("form") not in SEC_QUARTERLY_FORMS:
                    continue
                if str(entry.get("fp") or "").upper() not in {"Q1", "Q2", "Q3", "Q4"}:
                    continue
                end = str(entry.get("end") or "")
                try:
                    end_date = date.fromisoformat(end)
                    if duration:
                        start_date = date.fromisoformat(str(entry.get("start") or ""))
                        if not 60 <= (end_date - start_date).days <= 120:
                            continue
                except ValueError:
                    continue
                value = _sec_number(entry.get("val"))
                if value is None:
                    continue
                rank = (str(entry.get("filed") or ""), -namespace_index, -tag_index)
                if end not in selected or rank > selected[end]["_rank"]:
                    selected[end] = {
                        "value": value,
                        "fiscal_year": entry.get("fy") or end_date.year,
                        "fiscal_period": str(entry.get("fp") or ""),
                        "filed": entry.get("filed"),
                        "accession": entry.get("accn"),
                        "_rank": rank,
                    }
    for entry in selected.values():
        entry.pop("_rank", None)
    return selected


def _sec_filings(submissions: dict[str, Any], cik: str) -> list[dict[str, str]]:
    recent = submissions.get("filings", {}).get("recent", {})
    if not isinstance(recent, dict):
        return []
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filed_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    documents = recent.get("primaryDocument", [])
    filings = []
    for index, form in enumerate(forms if isinstance(forms, list) else []):
        if form not in {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K"}:
            continue
        try:
            accession = str(accessions[index])
            document = str(documents[index])
            filed = str(filed_dates[index])
            report_date = str(report_dates[index])
        except (IndexError, TypeError):
            continue
        accession_path = accession.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/"
        url = base + document if re.fullmatch(r"[A-Za-z0-9_.-]+", document) else base
        kind, title, priority = {
            "10-K": ("annual_results", "Annual report filed", "review"),
            "10-K/A": ("annual_results", "Annual report amendment filed", "review"),
            "10-Q": ("quarterly_results", "Quarterly report filed", "review"),
            "10-Q/A": ("quarterly_results", "Quarterly report amendment filed", "review"),
            "8-K": ("material_update", "Material current report filed", "attention"),
        }[str(form)]
        filings.append(
            {
                "accession": accession,
                "form": str(form),
                "filed": filed,
                "report_date": report_date,
                "url": url,
                "kind": kind,
                "title": title,
                "priority": priority,
            }
        )
        if len(filings) == 60:
            break
    selected = filings[:12]
    annual = [item for item in filings if item["form"] == "10-K"]
    for item in annual[:2]:
        if item not in selected:
            selected.append(item)
    return selected


class _SECVisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self.ignored_depth += 1
        elif tag.lower() in {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self.ignored_depth:
            self.ignored_depth -= 1
        elif tag.lower() in {"p", "div", "tr", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


def _sec_document_text(url: str, contact_email: str, size_limit: int = 6_000_000) -> str:
    contact = normalize_email(os.environ.get("INVESTORLAB_SEC_CONTACT") or contact_email)
    request = Request(
        url,
        headers={"User-Agent": f"Investor Lab {contact}", "Accept": "text/html,text/plain"},
    )
    try:
        with urlopen(request, timeout=25) as response:
            raw = response.read(size_limit + 1)
    except HTTPError as error:
        raise ApiError(502, f"SEC filing document request failed with HTTP {error.code}.") from None
    except (URLError, TimeoutError) as error:
        reason = getattr(error, "reason", str(error))
        raise ApiError(502, f"SEC filing document request failed: {reason}.") from None
    if len(raw) > size_limit:
        raise ApiError(502, "SEC filing document was unexpectedly large.")
    parser = _SECVisibleTextParser()
    try:
        parser.feed(raw.decode("utf-8", errors="replace"))
    except (UnicodeError, ValueError):
        raise ApiError(502, "SEC filing document could not be parsed.") from None
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", "".join(parser.parts))).strip()


def _filing_section(text: str, start_pattern: str, end_patterns: tuple[str, ...]) -> str:
    starts = list(re.finditer(start_pattern, text, flags=re.IGNORECASE))
    candidates = []
    for start in starts:
        end_positions = []
        for pattern in end_patterns:
            match = re.search(pattern, text[start.end():], flags=re.IGNORECASE)
            if match:
                end_positions.append(start.end() + match.start())
        end = min(end_positions) if end_positions else min(len(text), start.end() + 120_000)
        section = text[start.end():end].strip()
        if len(section.split()) >= 80:
            candidates.append(section)
    return max(candidates, key=len, default="")


def _section_wording_change(previous: str, current: str) -> dict[str, Any]:
    def sentences(value: str) -> list[str]:
        return [
            re.sub(r"\s+", " ", item).strip()
            for item in re.split(r"(?<=[.!?])\s+|\n+", value)
            if 35 <= len(re.sub(r"\s+", " ", item).strip()) <= 320
        ]

    before = sentences(previous)
    after = sentences(current)
    before_keys = {re.sub(r"[^a-z0-9 ]", "", item.lower()) for item in before}
    after_keys = {re.sub(r"[^a-z0-9 ]", "", item.lower()) for item in after}
    union = before_keys | after_keys
    similarity = (len(before_keys & after_keys) * 100 / len(union)) if union else 100
    return {
        "available": bool(previous and current),
        "similarity_percent": f"{similarity:.1f}",
        "previous_words": len(previous.split()),
        "current_words": len(current.split()),
        "added": [item for item in after if re.sub(r"[^a-z0-9 ]", "", item.lower()) not in before_keys][:3],
        "removed": [item for item in before if re.sub(r"[^a-z0-9 ]", "", item.lower()) not in after_keys][:3],
    }


def _sec_filing_comparison(filings: list[dict[str, str]], contact_email: str) -> dict[str, Any]:
    annual = [item for item in filings if item.get("form") == "10-K"]
    if len(annual) < 2:
        return {"available": False, "reason": "Two comparable 10-K filings are required."}
    current, previous = annual[0], annual[1]
    try:
        current_text = _sec_document_text(current["url"], contact_email)
        previous_text = _sec_document_text(previous["url"], contact_email)
    except ApiError as error:
        return {"available": False, "reason": str(error)}
    sections = {
        "risk_factors": _section_wording_change(
            _filing_section(previous_text, r"\bITEM\s+1A\.?\s+RISK\s+FACTORS\b", (r"\bITEM\s+1B\b", r"\bITEM\s+2\b")),
            _filing_section(current_text, r"\bITEM\s+1A\.?\s+RISK\s+FACTORS\b", (r"\bITEM\s+1B\b", r"\bITEM\s+2\b")),
        ),
        "management_discussion": _section_wording_change(
            _filing_section(previous_text, r"\bITEM\s+7\.?\s+MANAGEMENT(?:'S|’S)?\s+DISCUSSION", (r"\bITEM\s+7A\b", r"\bITEM\s+8\b")),
            _filing_section(current_text, r"\bITEM\s+7\.?\s+MANAGEMENT(?:'S|’S)?\s+DISCUSSION", (r"\bITEM\s+7A\b", r"\bITEM\s+8\b")),
        ),
    }
    available = any(section["available"] for section in sections.values())
    return {
        "available": available,
        "reason": None if available else "Comparable Risk Factors or MD&A sections were not found.",
        "current": current,
        "previous": previous,
        "sections": sections,
    }


def _sec_fundamental_changes(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> dict[str, Any]:
    previous_filings = {
        str(item.get("accession") or item.get("url") or "")
        for item in (previous or {}).get("filings", [])
        if isinstance(item, dict)
    }
    new_filings = [
        item for item in current.get("filings", [])
        if str(item.get("accession") or item.get("url") or "") not in previous_filings
    ] if previous else []
    previous_metrics = (previous or {}).get("metrics") or {}
    current_metrics = current.get("metrics") or {}
    metric_changes = []
    for key in (
        "revenue", "net_income", "free_cash_flow", "diluted_eps",
        "dividends_per_share", "liabilities_to_assets_percent",
    ):
        before = previous_metrics.get(key)
        after = current_metrics.get(key)
        if previous and before != after:
            metric_changes.append({"key": key, "before": before, "after": after})
    period_changed = bool(
        previous and previous.get("period_end") != current.get("period_end")
    )
    return {
        "detected": bool(new_filings or metric_changes or period_changed),
        "period_changed": period_changed,
        "previous_period_end": previous.get("period_end") if previous else None,
        "new_filings": new_filings,
        "metric_changes": metric_changes,
        "summary": (
            f"{len(new_filings)} new SEC filing(s) and {len(metric_changes)} changed annual metric(s)."
            if new_filings or metric_changes
            else "No SEC filing or annual metric changes detected."
        ),
    }


def _ratio_percent(numerator: int | float | None, denominator: int | float | None) -> str | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return _percent(Decimal(str(numerator)) * 100 / Decimal(str(denominator)))


def _fundamental_metrics_from_history(history: list[dict[str, Any]]) -> dict[str, Any]:
    latest = history[-1]
    previous = history[-2] if len(history) > 1 else None
    metrics = {
        key: latest.get(key)
        for key in (
            *SEC_FACTS.keys(),
            "free_cash_flow",
            "net_margin_percent",
            "liabilities_to_assets_percent",
        )
    }
    metrics["revenue_growth_percent"] = (
        _ratio_percent(latest["revenue"] - previous["revenue"], previous["revenue"])
        if previous and latest.get("revenue") is not None and previous.get("revenue") not in {None, 0}
        else None
    )
    metrics["net_income_growth_percent"] = (
        _ratio_percent(latest["net_income"] - previous["net_income"], abs(previous["net_income"]))
        if previous and latest.get("net_income") is not None and previous.get("net_income") not in {None, 0}
        else None
    )
    return metrics


def _fundamental_periods(
    facts: dict[str, dict[str, dict[str, Any]]], period_ends: list[str], *, quarterly: bool
) -> list[dict[str, Any]]:
    history = []
    for end in reversed(period_ends):
        revenue_fact = facts["revenue"].get(end, {})
        row = {
            "fiscal_year": revenue_fact.get("fiscal_year"),
            "fiscal_period": revenue_fact.get("fiscal_period") if quarterly else "FY",
            "period_end": end,
            "filed": max(
                (str(facts[name].get(end, {}).get("filed") or "") for name in SEC_FACTS),
                default="",
            ) or None,
        }
        for name in SEC_FACTS:
            row[name] = facts[name].get(end, {}).get("value")
        operating_cash_flow = row["operating_cash_flow"]
        capital_expenditure = row["capital_expenditure"]
        row["free_cash_flow"] = (
            operating_cash_flow - capital_expenditure
            if operating_cash_flow is not None and capital_expenditure is not None
            else None
        )
        row["net_margin_percent"] = _ratio_percent(row["net_income"], row["revenue"])
        row["liabilities_to_assets_percent"] = _ratio_percent(row["liabilities"], row["assets"])
        history.append(row)
    return history


def _quarterly_trends(history: list[dict[str, Any]]) -> dict[str, Any]:
    latest = history[-1] if history else None
    previous = history[-2] if len(history) > 1 else None
    prior_year = None
    if latest and isinstance(latest.get("fiscal_year"), int):
        prior_year = next(
            (
                item for item in reversed(history[:-1])
                if item.get("fiscal_period") == latest.get("fiscal_period")
                and item.get("fiscal_year") == latest.get("fiscal_year") - 1
            ),
            None,
        )

    def growth(metric: str, comparison: dict[str, Any] | None) -> str | None:
        if not latest or not comparison:
            return None
        current = latest.get(metric)
        before = comparison.get(metric)
        if current is None or before in {None, 0}:
            return None
        return _ratio_percent(current - before, abs(before))

    return {
        "latest_period_end": latest.get("period_end") if latest else None,
        "revenue_qoq_percent": growth("revenue", previous),
        "revenue_yoy_percent": growth("revenue", prior_year),
        "net_income_qoq_percent": growth("net_income", previous),
        "net_income_yoy_percent": growth("net_income", prior_year),
        "free_cash_flow_qoq_percent": growth("free_cash_flow", previous),
        "free_cash_flow_yoy_percent": growth("free_cash_flow", prior_year),
    }


def _company_profile(submissions: dict[str, Any], cik: str, company_name: str) -> dict[str, Any]:
    addresses = submissions.get("addresses") if isinstance(submissions.get("addresses"), dict) else {}
    business = addresses.get("business") if isinstance(addresses.get("business"), dict) else {}
    return {
        "name": str(submissions.get("name") or company_name),
        "cik": cik,
        "entity_type": submissions.get("entityType"),
        "sic": str(submissions.get("sic") or "") or None,
        "industry": submissions.get("sicDescription"),
        "exchange": next(iter(submissions.get("exchanges") or []), None),
        "fiscal_year_end": submissions.get("fiscalYearEnd"),
        "website": submissions.get("website"),
        "investor_website": submissions.get("investorWebsite"),
        "location": ", ".join(
            str(value) for value in (business.get("city"), business.get("stateOrCountry")) if value
        ) or None,
    }


def _build_fundamentals(
    symbol: str,
    cik: str,
    company_name: str,
    company_facts: dict[str, Any],
    submissions: dict[str, Any],
) -> dict[str, Any]:
    facts = {
        name: _annual_fact_map(company_facts, tags, unit, duration)
        for name, (tags, unit, duration) in SEC_FACTS.items()
    }
    quarterly_facts = {
        name: _quarterly_fact_map(company_facts, tags, unit, duration)
        for name, (tags, unit, duration) in SEC_FACTS.items()
    }
    period_ends = sorted(facts["revenue"], reverse=True)[:5]
    if not period_ends:
        return {
            "available": False,
            "symbol": symbol,
            "provider": "SEC EDGAR",
            "cik": cik,
            "company_name": company_name,
            "reason": "SEC EDGAR returned no comparable annual revenue facts for this company.",
            "fetched_at": now_iso(),
            "annual_history": [],
            "filings": [],
        }
    history = _fundamental_periods(facts, period_ends, quarterly=False)
    quarterly_history = _fundamental_periods(
        quarterly_facts, sorted(quarterly_facts["revenue"], reverse=True)[:8], quarterly=True
    )
    latest = history[-1]
    metrics = _fundamental_metrics_from_history(history)
    filings = _sec_filings(submissions, cik)
    return {
        "available": True,
        "data_version": 4,
        "symbol": symbol,
        "provider": "SEC EDGAR",
        "cik": cik,
        "company_name": str(company_facts.get("entityName") or company_name),
        "fiscal_year": latest["fiscal_year"],
        "period_end": latest["period_end"],
        "metrics": metrics,
        "annual_history": history,
        "quarterly_history": quarterly_history,
        "quarterly_trends": _quarterly_trends(quarterly_history),
        "company_profile": _company_profile(submissions, cik, company_name),
        "filings": filings,
        "source_url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
        "fetched_at": now_iso(),
        "scope": "Company-reported annual and quarterly XBRL facts from SEC EDGAR; values may be restated and are not normalized guidance or forecasts.",
    }


def _valuation_number(value: Decimal | None) -> str | None:
    return format(value.quantize(Decimal("0.01")), "f") if value is not None and value.is_finite() else None


def _fundamentals_with_valuation(db: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("available"):
        return payload
    symbol = str(payload.get("symbol") or "")
    bar = db.execute(
        "SELECT trading_date, close_micros FROM market_daily WHERE symbol = ? "
        "ORDER BY trading_date DESC LIMIT 1", (symbol,),
    ).fetchone()
    if not bar:
        return {**payload, "valuation": {"available": False, "reason": "Refresh market data to calculate valuation."}}
    price = Decimal(bar["close_micros"]) / SCALE
    quarters = list(payload.get("quarterly_history") or [])
    annual = list(payload.get("annual_history") or [])
    if len(quarters) >= 4:
        latest_periods = quarters[-4:]
        revenue = sum((Decimal(str(item["revenue"])) for item in latest_periods if item.get("revenue") is not None), Decimal(0))
        free_cash_flow = sum((Decimal(str(item["free_cash_flow"])) for item in latest_periods if item.get("free_cash_flow") is not None), Decimal(0))
        diluted_eps = sum((Decimal(str(item["diluted_eps"])) for item in latest_periods if item.get("diluted_eps") is not None), Decimal(0))
        dividends = sum((Decimal(str(item["dividends_per_share"])) for item in latest_periods if item.get("dividends_per_share") is not None), Decimal(0))
        shares = next((Decimal(str(item["diluted_shares"])) for item in reversed(latest_periods) if item.get("diluted_shares") not in {None, 0}), None)
        basis = "Trailing four reported quarters"
    else:
        latest = annual[-1] if annual else {}
        revenue = Decimal(str(latest["revenue"])) if latest.get("revenue") is not None else Decimal(0)
        free_cash_flow = Decimal(str(latest["free_cash_flow"])) if latest.get("free_cash_flow") is not None else Decimal(0)
        diluted_eps = Decimal(str(latest["diluted_eps"])) if latest.get("diluted_eps") is not None else Decimal(0)
        dividends = Decimal(str(latest["dividends_per_share"])) if latest.get("dividends_per_share") is not None else Decimal(0)
        shares = Decimal(str(latest["diluted_shares"])) if latest.get("diluted_shares") not in {None, 0} else None
        basis = "Latest reported fiscal year"
    market_cap = price * shares if shares is not None else None
    pe = price / diluted_eps if diluted_eps > 0 else None
    ps = market_cap / revenue if market_cap is not None and revenue > 0 else None
    pfcf = market_cap / free_cash_flow if market_cap is not None and free_cash_flow > 0 else None
    yield_percent = dividends * 100 / price if price > 0 else None
    history = []
    for period in annual:
        filed = period.get("filed") or period.get("period_end")
        historical_bar = db.execute(
            "SELECT trading_date, close_micros FROM market_daily WHERE symbol = ? AND trading_date <= ? "
            "ORDER BY trading_date DESC LIMIT 1", (symbol, filed),
        ).fetchone()
        if not historical_bar:
            continue
        historical_price = Decimal(historical_bar["close_micros"]) / SCALE
        eps = Decimal(str(period["diluted_eps"])) if period.get("diluted_eps") not in {None, 0} else None
        history.append({
            "period_end": period["period_end"],
            "price_date": historical_bar["trading_date"],
            "pe": _valuation_number(historical_price / eps) if eps and eps > 0 else None,
        })
    observed_pe = sorted(Decimal(item["pe"]) for item in history if item.get("pe"))
    pe_range = None
    if observed_pe:
        pe_range = {
            "low": _valuation_number(observed_pe[0]),
            "median": _valuation_number(observed_pe[len(observed_pe) // 2]),
            "high": _valuation_number(observed_pe[-1]),
            "observations": len(observed_pe),
        }
    return {
        **payload,
        "valuation": {
            "available": True,
            "price": decimal_string(int(bar["close_micros"])),
            "price_date": bar["trading_date"],
            "basis": basis,
            "pe": _valuation_number(pe),
            "price_to_sales": _valuation_number(ps),
            "price_to_fcf": _valuation_number(pfcf),
            "dividend_yield_percent": _valuation_number(yield_percent),
            "historical_pe_range": pe_range,
            "history": history,
        },
    }


def fundamental_research(path: Path, raw_symbol: Any) -> dict[str, Any]:
    symbol = normalize_symbol(raw_symbol)
    with open_db(path) as db:
        cached = _sec_cached(db, f"fundamentals:{symbol}")
    if cached is None:
        return {
            "available": False,
            "symbol": symbol,
            "provider": "SEC EDGAR",
            "reason": "Refresh SEC fundamentals to cache the latest annual filings.",
            "annual_history": [],
            "quarterly_history": [],
            "filings": [],
        }
    with open_db(path) as db:
        return {**_fundamentals_with_valuation(db, cached), "cache_hit": True}


def refresh_fundamentals(
    path: Path, raw_symbol: Any, contact_email: str, cache_hours: int = 24
) -> dict[str, Any]:
    symbol = normalize_symbol(raw_symbol)
    key = f"fundamentals:{symbol}"
    with open_db(path) as db:
        previous = _sec_cached(db, key)
        cached = _sec_cached(db, key, timedelta(hours=cache_hours))
        if cached is not None and cached.get("data_version") == 4:
            return {
                **_fundamentals_with_valuation(db, cached),
                "cache_hit": True,
                "changes": _sec_fundamental_changes(cached, cached),
            }
        cik, company_name = _sec_company(symbol, db, contact_email)
    company_facts = _sec_json(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", contact_email
    )
    submissions = _sec_json(f"https://data.sec.gov/submissions/CIK{cik}.json", contact_email)
    result = _build_fundamentals(symbol, cik, company_name, company_facts, submissions)
    if result.get("available"):
        result["filing_comparison"] = _sec_filing_comparison(result["filings"], contact_email)
    changes = _sec_fundamental_changes(previous, result)
    with open_db(path) as db:
        _store_sec_cache(db, key, result)
        enriched = _fundamentals_with_valuation(db, result)
    return {**enriched, "cache_hit": False, "changes": changes}


def refresh_fundamentals_and_decision(
    path: Path, user_id: str, raw_symbol: Any, contact_email: str
) -> dict[str, Any]:
    result = refresh_fundamentals(path, raw_symbol, contact_email)
    decision = generate_decision(path, user_id, raw_symbol)
    return {**result, "decision": decision}


def _alpha_vantage_api_key() -> tuple[str, str]:
    environment_key = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
    if environment_key:
        return environment_key, "environment"
    if sys.platform != "darwin":
        return "", "unconfigured"
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", ALPHA_VANTAGE_KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", "unconfigured"
    key = result.stdout.strip() if result.returncode == 0 else ""
    return (key, "keychain") if key else ("", "unconfigured")


def configure_market_data(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = str(payload.get("api_key") or "").strip()
    if not 8 <= len(api_key) <= 128 or not api_key.isalnum():
        raise InputError("Alpha Vantage API key must be 8-128 letters or numbers.")
    if sys.platform != "darwin":
        raise ApiError(501, "Keychain setup is available only on the local Mac server.")
    try:
        result = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-a",
                "alpha-vantage",
                "-s",
                ALPHA_VANTAGE_KEYCHAIN_SERVICE,
                "-U",
                "-w",
            ],
            input=api_key + "\n",
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ApiError(500, "The Mac Keychain could not save the market-data key.") from None
    if result.returncode != 0:
        raise ApiError(500, "The Mac Keychain could not save the market-data key.")
    return {"provider": "Alpha Vantage", "configured": True, "configuration_source": "keychain"}


def _keychain_value(service: str) -> str:
    if sys.platform != "darwin":
        return ""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _alpaca_credentials() -> tuple[str, str, str]:
    key_id = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if key_id and secret:
        return key_id, secret, "environment"
    key_id = _keychain_value(ALPACA_KEY_ID_KEYCHAIN_SERVICE)
    secret = _keychain_value(ALPACA_SECRET_KEYCHAIN_SERVICE)
    return (key_id, secret, "keychain") if key_id and secret else ("", "", "unconfigured")


def configure_realtime_data(payload: dict[str, Any]) -> dict[str, Any]:
    key_id = str(payload.get("api_key_id") or "").strip()
    secret = str(payload.get("api_secret_key") or "").strip()
    if not 8 <= len(key_id) <= 128 or not re.fullmatch(r"[A-Za-z0-9_-]+", key_id):
        raise InputError("Alpaca API key ID must be 8-128 safe characters.")
    if not 16 <= len(secret) <= 256 or not re.fullmatch(r"[A-Za-z0-9_-]+", secret):
        raise InputError("Alpaca secret key must be 16-256 safe characters.")
    if sys.platform != "darwin":
        raise ApiError(501, "Keychain setup is available only on the local Mac server.")
    for account, service, value in (
        ("alpaca-key-id", ALPACA_KEY_ID_KEYCHAIN_SERVICE, key_id),
        ("alpaca-secret", ALPACA_SECRET_KEYCHAIN_SERVICE, secret),
    ):
        try:
            result = subprocess.run(
                [
                    "security", "add-generic-password", "-a", account,
                    "-s", service, "-U", "-w",
                ],
                input=value + "\n", capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ApiError(500, "The Mac Keychain could not save the real-time data credentials.") from None
        if result.returncode != 0:
            raise ApiError(500, "The Mac Keychain could not save the real-time data credentials.")
    return {
        "provider": "Alpaca Market Data", "configured": True,
        "configuration_source": "keychain", "feed": "iex",
    }


def _alpaca_json(path: str, parameters: dict[str, Any], key_id: str, secret: str) -> dict[str, Any]:
    url = "https://data.alpaca.markets" + path
    if parameters:
        url += "?" + urlencode(parameters)
    request = Request(
        url,
        headers={
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
            "User-Agent": "InvestorLab/0.6",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read(8_000_001)
    except HTTPError as error:
        status = 401 if error.code in {401, 403} else 429 if error.code == 429 else 502
        raise ApiError(status, f"Alpaca Market Data request failed with HTTP {error.code}.") from None
    except (URLError, TimeoutError) as error:
        reason = getattr(error, "reason", str(error))
        raise ApiError(502, f"Alpaca Market Data request failed: {reason}.") from None
    if len(raw) > 8_000_000:
        raise ApiError(502, "Alpaca Market Data returned an unexpectedly large response.")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(502, "Alpaca Market Data returned invalid JSON.") from None
    if not isinstance(payload, dict):
        raise ApiError(502, "Alpaca Market Data returned a malformed response.")
    return payload


def _alpaca_trading_json(
    path: str, parameters: dict[str, Any], key_id: str, secret: str
) -> Any:
    url = "https://paper-api.alpaca.markets" + path
    if parameters:
        url += "?" + urlencode(parameters)
    request = Request(
        url,
        headers={
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
            "User-Agent": "InvestorLab/0.7",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read(2_000_001)
    except HTTPError as error:
        status = 401 if error.code in {401, 403} else 429 if error.code == 429 else 502
        raise ApiError(status, f"Alpaca Paper API request failed with HTTP {error.code}.") from None
    except (URLError, TimeoutError) as error:
        reason = getattr(error, "reason", str(error))
        raise ApiError(502, f"Alpaca Paper API request failed: {reason}.") from None
    if len(raw) > 2_000_000:
        raise ApiError(502, "Alpaca Paper API returned an unexpectedly large response.")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(502, "Alpaca Paper API returned invalid JSON.") from None


def _paper_money_micros(value: Any) -> int | None:
    number = _metric_decimal(value)
    return round(number * SCALE) if number is not None else None


def _paper_account_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT result_json FROM paper_account_snapshots WHERE user_id = ? "
        "ORDER BY fetched_at DESC, id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not row:
        return {
            "available": False,
            "configured": bool(_alpaca_credentials()[0]),
            "read_only": True,
            "reason": "Synchronize the Alpaca Paper account to load positions, orders, and fills.",
        }
    return json.loads(row["result_json"])


def paper_account(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        return _paper_account_from_db(db, user_id)


def sync_paper_account(path: Path, user_id: str) -> dict[str, Any]:
    key_id, secret, source = _alpaca_credentials()
    if not key_id or not secret:
        raise ApiError(503, "Save Alpaca Paper credentials before synchronizing the account.")
    run_id = _start_collection_run(path, user_id, "paper_account_sync", 4)
    try:
        account_payload = _alpaca_trading_json("/v2/account", {}, key_id, secret)
        positions_payload = _alpaca_trading_json("/v2/positions", {}, key_id, secret)
        orders_payload = _alpaca_trading_json(
            "/v2/orders", {"status": "all", "limit": 100, "direction": "desc"}, key_id, secret
        )
        fills_payload = _alpaca_trading_json(
            "/v2/account/activities/FILL", {"page_size": 100, "direction": "desc"}, key_id, secret
        )
        if not isinstance(account_payload, dict) or not isinstance(positions_payload, list) or not isinstance(orders_payload, list) or not isinstance(fills_payload, list):
            raise ApiError(502, "Alpaca Paper API returned a malformed account response.")

        account = {
            key: account_payload.get(key) for key in (
                "id", "account_number", "status", "currency", "cash", "equity",
                "last_equity", "portfolio_value", "buying_power", "regt_buying_power",
                "daytrading_buying_power", "multiplier", "daytrade_count",
                "pattern_day_trader", "trading_blocked", "transfers_blocked", "account_blocked",
            )
        }
        positions = [
            {key: item.get(key) for key in (
                "asset_id", "symbol", "exchange", "asset_class", "side", "qty",
                "avg_entry_price", "market_value", "cost_basis", "unrealized_pl",
                "unrealized_plpc", "current_price", "lastday_price", "change_today",
            )}
            for item in positions_payload if isinstance(item, dict)
        ]
        orders = [
            {key: item.get(key) for key in (
                "id", "client_order_id", "symbol", "asset_class", "side", "type",
                "time_in_force", "status", "qty", "filled_qty", "filled_avg_price",
                "limit_price", "stop_price", "created_at", "updated_at", "filled_at",
            )}
            for item in orders_payload if isinstance(item, dict)
        ]
        fills = [
            {key: item.get(key) for key in (
                "id", "activity_type", "transaction_time", "symbol", "qty", "price",
                "side", "order_id", "cum_qty", "leaves_qty",
            )}
            for item in fills_payload if isinstance(item, dict)
        ]
        result = {
            "available": True,
            "configured": True,
            "read_only": True,
            "paper_order_routing_available": True,
            "provider": "Alpaca Paper Trading API",
            "configuration_source": source,
            "account": account,
            "positions": positions,
            "orders": orders,
            "fills": fills,
            "fetched_at": now_iso(),
            "scope": "This synchronized mirror is read-only. Separate Alpaca Paper-only order endpoints remain locked by default behind explicit acknowledgement, notional, daily-loss, and idempotency controls.",
        }
        snapshot_id = str(uuid4())
        with open_db(path) as db:
            db.execute(
                "INSERT INTO paper_account_snapshots(id, user_id, provider, account_status, "
                "equity_micros, cash_micros, buying_power_micros, position_count, "
                "open_order_count, result_json, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot_id, user_id, "alpaca_paper", str(account.get("status") or "unknown"),
                    _paper_money_micros(account.get("equity")), _paper_money_micros(account.get("cash")),
                    _paper_money_micros(account.get("buying_power")), len(positions),
                    sum(str(item.get("status")) not in {"filled", "canceled", "expired", "rejected"} for item in orders),
                    json.dumps(result, separators=(",", ":")), result["fetched_at"],
                ),
            )
            _append_sync_event(db, user_id, "paper_account_snapshot", snapshot_id, "upsert", result)
        _finish_collection_run(path, run_id, "completed", 4, {"positions": len(positions), "orders": len(orders), "fills": len(fills)})
        return result
    except Exception as error:
        _finish_collection_run(path, run_id, "failed", 0, error=str(error))
        raise


def _alpaca_trading_mutation(
    path: str, method: str, payload: dict[str, Any] | None, key_id: str, secret: str
) -> dict[str, Any]:
    if not path.startswith("/v2/orders"):
        raise ApiError(500, "Paper execution is restricted to Alpaca order endpoints.")
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8") if payload is not None else None
    request = Request(
        "https://paper-api.alpaca.markets" + path,
        data=raw_body,
        method=method,
        headers={
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "InvestorLab/0.8",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read(2_000_001)
    except HTTPError as error:
        status = 400 if error.code in {400, 404, 422} else 401 if error.code in {401, 403} else 429 if error.code == 429 else 502
        raise ApiError(status, f"Alpaca Paper order request failed with HTTP {error.code}.") from None
    except (URLError, TimeoutError) as error:
        reason = getattr(error, "reason", str(error))
        raise ApiError(502, f"Alpaca Paper order request failed: {reason}.") from None
    if len(raw) > 2_000_000:
        raise ApiError(502, "Alpaca Paper order response was unexpectedly large.")
    if not raw:
        return {}
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(502, "Alpaca Paper order endpoint returned invalid JSON.") from None
    if not isinstance(result, dict):
        raise ApiError(502, "Alpaca Paper order endpoint returned a malformed response.")
    return result


def _paper_order_control_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT enabled, max_order_notional_micros, daily_loss_limit_micros, updated_at "
        "FROM paper_order_controls WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        profile = _investor_profile_from_db(db, user_id)
        return {
            "enabled": False,
            "max_order_notional": "1000",
            "daily_loss_limit": profile["daily_loss_limit"],
            "updated_at": None,
        }
    return {
        "enabled": bool(row["enabled"]),
        "max_order_notional": decimal_string(int(row["max_order_notional_micros"])),
        "daily_loss_limit": decimal_string(int(row["daily_loss_limit_micros"])),
        "updated_at": row["updated_at"],
    }


def paper_order_control(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        control = _paper_order_control_from_db(db, user_id)
        guard = _day_trade_guardrails_from_db(db, user_id)
    return {
        **control,
        "recorded_loss_today": guard["recorded_loss_today"],
        "stop_triggered": guard["stop_triggered"],
        "paper_endpoint": "https://paper-api.alpaca.markets",
        "real_account_supported": False,
        "required_acknowledgement": not control["enabled"],
        "scope": "Alpaca Paper only. Enabling and submitting require a checkbox acknowledgement; every order also uses a unique client order ID and local risk checks.",
    }


def _paper_action_acknowledged(
    payload: dict[str, Any], legacy_confirmation: str
) -> bool:
    if payload.get("acknowledged") is True:
        return True
    confirmation = str(payload.get("confirmation") or "").strip()
    return confirmation.casefold() == legacy_confirmation.casefold()


def update_paper_order_control(
    path: Path, user_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    enabled = payload.get("enabled") is True
    if enabled and not _paper_action_acknowledged(payload, "ENABLE PAPER ORDERS"):
        raise InputError("Check the Paper-only acknowledgement before enabling order routing.")
    maximum = to_micros(payload.get("max_order_notional", "1000"), "Maximum order notional")
    daily = to_micros(payload.get("daily_loss_limit", "300"), "Daily loss limit")
    if maximum > 1_000_000 * int(SCALE):
        raise InputError("Maximum paper order notional cannot exceed $1,000,000.")
    updated = now_iso()
    with open_db(path) as db:
        db.execute(
            "INSERT INTO paper_order_controls(user_id, enabled, max_order_notional_micros, "
            "daily_loss_limit_micros, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET enabled = excluded.enabled, "
            "max_order_notional_micros = excluded.max_order_notional_micros, "
            "daily_loss_limit_micros = excluded.daily_loss_limit_micros, updated_at = excluded.updated_at",
            (user_id, int(enabled), maximum, daily, updated),
        )
        _append_sync_event(
            db, user_id, "paper_order_control", user_id, "upsert",
            {"enabled": enabled, "max_order_notional": decimal_string(maximum), "updated_at": updated},
        )
    return paper_order_control(path, user_id)


def _serialize_paper_order(row: sqlite3.Row) -> dict[str, Any]:
    result = json.loads(row["result_json"]) if row["result_json"] else {}
    return {
        "id": row["id"], "client_order_id": row["client_order_id"],
        "broker_order_id": row["broker_order_id"], "symbol": row["symbol"],
        "side": row["side"], "order_type": row["order_type"],
        "time_in_force": row["time_in_force"],
        "quantity": decimal_string(int(row["quantity_micros"])),
        "limit_price": decimal_string(int(row["limit_price_micros"])) if row["limit_price_micros"] else None,
        "stop_price": decimal_string(int(row["stop_price_micros"])) if row["stop_price_micros"] else None,
        "estimated_notional": decimal_string(int(row["estimated_notional_micros"])),
        "status": row["status"], "broker_status": result.get("status"),
        "filled_qty": result.get("filled_qty"), "filled_avg_price": result.get("filled_avg_price"),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def list_paper_orders(path: Path, user_id: str, limit: int = 100) -> dict[str, Any]:
    with open_db(path) as db:
        rows = db.execute(
            "SELECT * FROM paper_order_intents WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (user_id, max(1, min(limit, 500))),
        ).fetchall()
        control = _paper_order_control_from_db(db, user_id)
    return {"control": control, "orders": [_serialize_paper_order(row) for row in rows], "paper_only": True}


def _paper_reference_price(db: sqlite3.Connection, user_id: str, symbol: str) -> int | None:
    row = db.execute(
        "SELECT close_micros FROM market_daily WHERE symbol = ? ORDER BY trading_date DESC, fetched_at DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if row:
        return int(row["close_micros"])
    paper = _paper_account_from_db(db, user_id)
    for position in paper.get("positions") or []:
        if position.get("symbol") == symbol:
            return _paper_money_micros(position.get("current_price"))
    return None


def submit_paper_order(path: Path, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    symbol = normalize_symbol(payload.get("symbol"))
    side = str(payload.get("side") or "").lower()
    order_type = str(payload.get("order_type") or "").lower()
    time_in_force = str(payload.get("time_in_force") or "day").lower()
    if side not in {"buy", "sell"}:
        raise InputError("Paper order side must be buy or sell.")
    if order_type not in {"market", "limit", "stop", "stop_limit"}:
        raise InputError("Paper order type must be market, limit, stop, or stop_limit.")
    if time_in_force not in {"day", "gtc"}:
        raise InputError("Time in force must be day or gtc.")
    quantity = to_micros(payload.get("quantity"), "Quantity")
    limit_price = to_micros(payload.get("limit_price"), "Limit price") if order_type in {"limit", "stop_limit"} else None
    stop_price = to_micros(payload.get("stop_price"), "Stop price") if order_type in {"stop", "stop_limit"} else None
    client_order_id = str(payload.get("client_order_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", client_order_id):
        raise InputError("Client order ID must be 8-64 letters, numbers, underscores, or hyphens.")
    if not _paper_action_acknowledged(payload, f"PAPER {symbol}"):
        raise InputError("Check the order acknowledgement before submitting this simulated order.")
    with open_db(path) as db:
        existing = db.execute(
            "SELECT * FROM paper_order_intents WHERE user_id = ? AND client_order_id = ?",
            (user_id, client_order_id),
        ).fetchone()
        if existing:
            return {**_serialize_paper_order(existing), "idempotent_replay": True}
        control = _paper_order_control_from_db(db, user_id)
        if not control["enabled"]:
            raise ApiError(409, "Paper order routing is disabled in the command center.")
        guard = _day_trade_guardrails_from_db(db, user_id)
        if guard["stop_triggered"] or Decimal(guard["recorded_loss_today"]) >= Decimal(control["daily_loss_limit"]):
            raise ApiError(409, "The local daily-loss control blocks new paper orders.")
        paper = _paper_account_from_db(db, user_id)
        if not paper.get("available"):
            raise ApiError(409, "Synchronize the Alpaca Paper account before submitting an order.")
        account = paper.get("account") or {}
        if account.get("trading_blocked") or account.get("account_blocked") or str(account.get("status")) != "ACTIVE":
            raise ApiError(409, "The synchronized Alpaca Paper account is not active for trading.")
        reference = limit_price or stop_price or _paper_reference_price(db, user_id, symbol)
        if reference is None:
            raise ApiError(409, "A cached reference price is required for the local notional check.")
        asset_type = "option" if _parse_occ_option_symbol(symbol) else "equity"
        estimated_notional = _position_value_micros(quantity, reference, asset_type)
        maximum = to_micros(control["max_order_notional"], "Maximum paper order notional")
        if estimated_notional > maximum:
            raise ApiError(409, f"Estimated paper order value exceeds the ${control['max_order_notional']} limit.")
        if side == "sell":
            position = next((item for item in paper.get("positions") or [] if item.get("symbol") == symbol and item.get("side") == "long"), None)
            held = _metric_decimal(position.get("qty")) if position else None
            if held is None or Decimal(quantity) / SCALE > held:
                raise ApiError(409, "Paper sell quantity exceeds the synchronized long position; short orders are blocked.")
        intent_id = str(uuid4())
        created = now_iso()
        broker_payload: dict[str, Any] = {
            "symbol": symbol, "side": side, "type": order_type,
            "time_in_force": time_in_force, "qty": decimal_string(quantity),
            "client_order_id": client_order_id,
        }
        if limit_price is not None:
            broker_payload["limit_price"] = decimal_string(limit_price)
        if stop_price is not None:
            broker_payload["stop_price"] = decimal_string(stop_price)
        db.execute(
            "INSERT INTO paper_order_intents VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'submitting', ?, '{}', ?, ?)",
            (intent_id, user_id, client_order_id, symbol, side, order_type, time_in_force,
             quantity, limit_price, stop_price, estimated_notional, json.dumps(broker_payload), created, created),
        )
    key_id, secret, _ = _alpaca_credentials()
    if not key_id or not secret:
        raise ApiError(503, "Save Alpaca Paper credentials before submitting an order.")
    try:
        broker = _alpaca_trading_mutation("/v2/orders", "POST", broker_payload, key_id, secret)
        raw_status = str(broker.get("status") or "accepted")
        status = raw_status if raw_status in {"accepted", "new", "partially_filled", "filled", "cancel_pending", "canceled", "replaced", "rejected"} else "accepted"
        with open_db(path) as db:
            db.execute(
                "UPDATE paper_order_intents SET broker_order_id = ?, status = ?, result_json = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (broker.get("id"), status, json.dumps(broker), now_iso(), intent_id, user_id),
            )
            _append_sync_event(db, user_id, "paper_order", intent_id, "upsert", {"symbol": symbol, "status": status, "paper_only": True})
            row = db.execute("SELECT * FROM paper_order_intents WHERE id = ?", (intent_id,)).fetchone()
        assert row is not None
        return _serialize_paper_order(row)
    except Exception:
        with open_db(path) as db:
            db.execute("UPDATE paper_order_intents SET status = 'failed', updated_at = ? WHERE id = ?", (now_iso(), intent_id))
        raise


def cancel_paper_order(path: Path, user_id: str, order_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with open_db(path) as db:
        row = db.execute("SELECT * FROM paper_order_intents WHERE id = ? AND user_id = ?", (order_id, user_id)).fetchone()
    if not row:
        raise ApiError(404, "Paper order was not found.")
    if not _paper_action_acknowledged(payload, f"CANCEL PAPER {row['symbol']}"):
        raise InputError(f"Type CANCEL PAPER {row['symbol']} to confirm.")
    if not row["broker_order_id"]:
        raise ApiError(409, "This order has no broker order ID to cancel.")
    key_id, secret, _ = _alpaca_credentials()
    if not key_id or not secret:
        raise ApiError(503, "Alpaca Paper credentials are not configured.")
    _alpaca_trading_mutation(f"/v2/orders/{row['broker_order_id']}", "DELETE", None, key_id, secret)
    with open_db(path) as db:
        db.execute("UPDATE paper_order_intents SET status = 'canceled', updated_at = ? WHERE id = ?", (now_iso(), order_id))
        _append_sync_event(db, user_id, "paper_order", order_id, "upsert", {"symbol": row["symbol"], "status": "canceled"})
        updated = db.execute("SELECT * FROM paper_order_intents WHERE id = ?", (order_id,)).fetchone()
    assert updated is not None
    return _serialize_paper_order(updated)


def replace_paper_order(path: Path, user_id: str, order_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with open_db(path) as db:
        row = db.execute("SELECT * FROM paper_order_intents WHERE id = ? AND user_id = ?", (order_id, user_id)).fetchone()
    if not row:
        raise ApiError(404, "Paper order was not found.")
    if not _paper_action_acknowledged(payload, f"REPLACE PAPER {row['symbol']}"):
        raise InputError(f"Type REPLACE PAPER {row['symbol']} to confirm.")
    if not row["broker_order_id"] or row["status"] in {"filled", "canceled", "rejected", "failed"}:
        raise ApiError(409, "Only an active broker paper order can be replaced.")
    replacement: dict[str, Any] = {}
    quantity = int(row["quantity_micros"])
    limit_price = row["limit_price_micros"]
    if payload.get("quantity") not in {None, ""}:
        quantity = to_micros(payload.get("quantity"), "Quantity")
        replacement["qty"] = decimal_string(quantity)
    if payload.get("limit_price") not in {None, ""}:
        limit_price = to_micros(payload.get("limit_price"), "Limit price")
        replacement["limit_price"] = decimal_string(limit_price)
    if not replacement:
        raise InputError("Provide a replacement quantity or limit price.")
    with open_db(path) as db:
        control = _paper_order_control_from_db(db, user_id)
        reference = int(limit_price or row["stop_price_micros"] or _paper_reference_price(db, user_id, row["symbol"]) or 0)
    estimated = round(Decimal(quantity) * Decimal(reference) / SCALE)
    if estimated <= 0 or estimated > to_micros(control["max_order_notional"], "Maximum paper order notional"):
        raise ApiError(409, "Replacement exceeds the saved paper order limit.")
    key_id, secret, _ = _alpaca_credentials()
    if not key_id or not secret:
        raise ApiError(503, "Alpaca Paper credentials are not configured.")
    broker = _alpaca_trading_mutation(f"/v2/orders/{row['broker_order_id']}", "PATCH", replacement, key_id, secret)
    with open_db(path) as db:
        db.execute(
            "UPDATE paper_order_intents SET broker_order_id = ?, quantity_micros = ?, limit_price_micros = ?, "
            "estimated_notional_micros = ?, status = 'replaced', result_json = ?, updated_at = ? WHERE id = ?",
            (broker.get("id") or row["broker_order_id"], quantity, limit_price, estimated, json.dumps(broker), now_iso(), order_id),
        )
        _append_sync_event(db, user_id, "paper_order", order_id, "upsert", {"symbol": row["symbol"], "status": "replaced"})
        updated = db.execute("SELECT * FROM paper_order_intents WHERE id = ?", (order_id,)).fetchone()
    assert updated is not None
    return _serialize_paper_order(updated)


def _scanner_preset_rows(db: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT * FROM scanner_presets WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
        (user_id,),
    ).fetchall()
    return [
        {
            "id": row["id"], "name": row["name"],
            "symbols": json.loads(row["symbols_json"]),
            "filters": json.loads(row["filters_json"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def list_scanner_presets(path: Path, user_id: str) -> list[dict[str, Any]]:
    with open_db(path) as db:
        return _scanner_preset_rows(db, user_id)


def _normalize_scanner_filters(payload: dict[str, Any]) -> dict[str, Any]:
    filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else payload
    signals = filters.get("signals") or []
    if not isinstance(signals, list) or any(item not in {"buy_candidate", "watch", "hold", "reduce", "sell_review", "avoid", "data_required", "refresh_required"} for item in signals):
        raise InputError("Scanner signals must be a list of supported decision states.")
    return {
        "minimum_score": int(decimal_parameter(filters.get("minimum_score", 0), "Minimum score", minimum=Decimal(0), maximum=Decimal(100))),
        "minimum_price": format(decimal_parameter(filters.get("minimum_price", 0), "Minimum price", minimum=Decimal(0), maximum=Decimal(1_000_000)).normalize(), "f"),
        "maximum_price": format(decimal_parameter(filters.get("maximum_price", 1_000_000), "Maximum price", minimum=Decimal(0), maximum=Decimal(1_000_000)).normalize(), "f"),
        "minimum_average_volume": int(decimal_parameter(filters.get("minimum_average_volume", 0), "Minimum average volume", minimum=Decimal(0), maximum=Decimal(100_000_000_000))),
        "minimum_change_percent": format(decimal_parameter(filters.get("minimum_change_percent", -100), "Minimum change", minimum=Decimal(-100), maximum=Decimal(10_000)).normalize(), "f"),
        "maximum_change_percent": format(decimal_parameter(filters.get("maximum_change_percent", 10_000), "Maximum change", minimum=Decimal(-100), maximum=Decimal(10_000)).normalize(), "f"),
        "current_data_only": filters.get("current_data_only") is True,
        "signals": signals,
    }


def save_scanner_preset(path: Path, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not 1 <= len(name) <= 60:
        raise InputError("Scanner preset name must be 1-60 characters.")
    raw_symbols = payload.get("symbols") or []
    if not isinstance(raw_symbols, list) or len(raw_symbols) > 250:
        raise InputError("Scanner preset symbols must be a list of at most 250 tickers.")
    symbols = list(dict.fromkeys(normalize_symbol(item) for item in raw_symbols))
    filters = _normalize_scanner_filters(payload)
    preset_id = str(uuid4())
    timestamp = now_iso()
    with open_db(path) as db:
        existing = db.execute(
            "SELECT id, created_at FROM scanner_presets WHERE user_id = ? AND name = ?",
            (user_id, name),
        ).fetchone()
        if existing:
            preset_id = str(existing["id"])
            db.execute(
                "UPDATE scanner_presets SET symbols_json = ?, filters_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(symbols), json.dumps(filters), timestamp, preset_id),
            )
            created_at = existing["created_at"]
        else:
            db.execute(
                "INSERT INTO scanner_presets VALUES (?, ?, ?, ?, ?, ?, ?)",
                (preset_id, user_id, name, json.dumps(symbols), json.dumps(filters), timestamp, timestamp),
            )
            created_at = timestamp
        _append_sync_event(db, user_id, "scanner_preset", preset_id, "upsert", {"name": name, "symbols": symbols, "filters": filters})
    return {"id": preset_id, "name": name, "symbols": symbols, "filters": filters, "created_at": created_at, "updated_at": timestamp}


def delete_scanner_preset(path: Path, user_id: str, preset_id: str) -> bool:
    with open_db(path) as db:
        deleted = db.execute("DELETE FROM scanner_presets WHERE id = ? AND user_id = ?", (preset_id, user_id)).rowcount
        if deleted:
            _append_sync_event(db, user_id, "scanner_preset", preset_id, "delete", None)
    return bool(deleted)


def run_universe_scanner(path: Path, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    preset_id = str(payload.get("preset_id") or "").strip()
    with open_db(path) as db:
        preset = db.execute("SELECT * FROM scanner_presets WHERE id = ? AND user_id = ?", (preset_id, user_id)).fetchone() if preset_id else None
        raw_symbols = payload.get("symbols")
        if raw_symbols is None and preset:
            raw_symbols = json.loads(preset["symbols_json"])
        if raw_symbols == []:
            raw_symbols = None
        if raw_symbols is not None:
            if not isinstance(raw_symbols, list) or len(raw_symbols) > 250:
                raise InputError("Scanner symbols must be a list of at most 250 tickers.")
            symbols = list(dict.fromkeys(normalize_symbol(item) for item in raw_symbols))
        else:
            symbols = [str(row[0]) for row in db.execute(
                "SELECT symbol FROM watchlist WHERE user_id = ? ORDER BY symbol LIMIT 250",
                (user_id,),
            ).fetchall()]
        filters = json.loads(preset["filters_json"]) if preset and payload.get("filters") is None else _normalize_scanner_filters(payload)
        decisions = {}
        for item in _decision_rows(db, user_id, limit=10_000):
            decisions.setdefault(item["symbol"], item)
        rows = []
        today = date.today()
        for symbol in symbols:
            bars = db.execute(
                "SELECT trading_date, close_micros, volume FROM market_daily WHERE symbol = ? ORDER BY trading_date DESC, fetched_at DESC LIMIT 21",
                (symbol,),
            ).fetchall()
            if not bars:
                continue
            latest = bars[0]
            close = Decimal(int(latest["close_micros"])) / SCALE
            prior = Decimal(int(bars[1]["close_micros"])) / SCALE if len(bars) > 1 else close
            change = (close / prior - 1) * 100 if prior else Decimal(0)
            average_volume = round(sum(int(item["volume"]) for item in bars) / len(bars))
            age_days = (today - date.fromisoformat(str(latest["trading_date"]))).days
            decision = decisions.get(symbol)
            signal = decision["signal"] if decision else "data_required"
            score = int(decision["score"]) if decision and decision.get("score") is not None else 0
            if score < int(filters["minimum_score"]):
                continue
            if filters["signals"] and signal not in filters["signals"]:
                continue
            if not Decimal(filters["minimum_price"]) <= close <= Decimal(filters["maximum_price"]):
                continue
            if average_volume < int(filters["minimum_average_volume"]):
                continue
            if not Decimal(filters["minimum_change_percent"]) <= change <= Decimal(filters["maximum_change_percent"]):
                continue
            if filters["current_data_only"] and age_days > 7:
                continue
            rows.append({
                "symbol": symbol, "signal": signal,
                "signal_label": decision["signal_label"] if decision else "Data required",
                "score": score if decision else None, "close": format(close.normalize(), "f"),
                "change_percent": _percent(change), "average_volume_20d": average_volume,
                "trading_date": latest["trading_date"], "data_age_days": age_days,
                "freshness": "current" if age_days <= 7 else "stale",
            })
    priority = {"sell_review": 0, "reduce": 1, "buy_candidate": 2, "watch": 3, "hold": 4, "avoid": 5, "refresh_required": 6, "data_required": 7}
    rows.sort(key=lambda item: (priority.get(item["signal"], 9), -(item["score"] or -1), item["symbol"]))
    return {
        "generated_at": now_iso(), "universe_size": len(symbols), "matched": len(rows),
        "rows": rows[:250], "filters": filters, "preset_id": preset_id or None,
        "cost_model": "Cached symbols only; running a scan makes no paid market-data request.",
        "scope": "Ranks up to 250 cached US symbols. Missing decisions remain data-required rather than receiving fabricated scores.",
    }


def create_notification_rule(path: Path, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("kind") or "").lower()
    if kind not in {"decision", "filing", "earnings", "option_expiration", "day_trade", "data_stale"}:
        raise InputError("Select a supported notification rule type.")
    symbol = normalize_symbol(payload.get("symbol")) if payload.get("symbol") else None
    config = payload.get("config") or {}
    if not isinstance(config, dict):
        raise InputError("Notification rule config must be an object.")
    threshold = int(decimal_parameter(config.get("threshold", 7), "Rule threshold", minimum=Decimal(0), maximum=Decimal(10_000)))
    signal = str(config.get("signal") or "buy_candidate")
    if signal not in {"buy_candidate", "watch", "hold", "reduce", "sell_review", "avoid"}:
        raise InputError("Notification decision signal is not supported.")
    normalized = {"threshold": threshold, "signal": signal, "quiet_start": str(config.get("quiet_start") or "22:00"), "quiet_end": str(config.get("quiet_end") or "07:00")}
    rule_id = str(uuid4())
    timestamp = now_iso()
    with open_db(path) as db:
        db.execute("INSERT INTO notification_rules VALUES (?, ?, ?, ?, ?, 1, NULL, ?, ?)", (rule_id, user_id, kind, symbol, json.dumps(normalized), timestamp, timestamp))
        _append_sync_event(db, user_id, "notification_rule", rule_id, "upsert", {"kind": kind, "symbol": symbol, "config": normalized, "enabled": True})
    return {"id": rule_id, "kind": kind, "symbol": symbol, "config": normalized, "enabled": True, "created_at": timestamp, "updated_at": timestamp}


def delete_notification_rule(path: Path, user_id: str, rule_id: str) -> bool:
    with open_db(path) as db:
        deleted = db.execute("DELETE FROM notification_rules WHERE id = ? AND user_id = ?", (rule_id, user_id)).rowcount
        if deleted:
            _append_sync_event(db, user_id, "notification_rule", rule_id, "delete", None)
    return bool(deleted)


def notification_center(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        rows = db.execute("SELECT * FROM notification_rules WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)).fetchall()
        decisions = {item["symbol"]: item for item in _decision_center_from_db(db, user_id)["latest"]}
        decision_settings_row = _decision_settings_from_db(db, user_id)
        plan_center = _plan_review_center_from_db(db, user_id)
        sec = _sec_event_center_from_db(db, user_id)
        earnings = _earnings_calendar_for_user(db, user_id, _sec_cached(db, "earnings-calendar:3month"))
        screener = _watchlist_screener_from_db(db, user_id)
        watchlist_count = int(db.execute(
            "SELECT COUNT(*) FROM watchlist WHERE user_id = ?", (user_id,)
        ).fetchone()[0])
        failure_cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=48)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        failed_runs = db.execute(
            "SELECT job_type, status, error_text, started_at FROM data_collection_runs "
            "WHERE user_id = ? AND started_at >= ? AND status IN ('failed', 'partial') "
            "ORDER BY started_at DESC LIMIT 10",
            (user_id, failure_cutoff),
        ).fetchall()
    alerts = []
    serialized = []
    for row in rows:
        config = json.loads(row["config_json"])
        symbol = row["symbol"]
        active = False
        detail = "Waiting for matching evidence."
        if row["kind"] == "decision" and symbol in decisions:
            decision = decisions[symbol]
            active = decision["signal"] == config["signal"] or (decision.get("score") or 0) >= config["threshold"]
            detail = f"{decision['signal_label']} · score {decision.get('score') or '—'}."
        elif row["kind"] == "filing":
            matching = [item for item in sec["events"] if item["is_recent"] and (not symbol or item["symbol"] == symbol)]
            active, detail = bool(matching), f"{len(matching)} SEC filing(s) in the last seven days."
        elif row["kind"] == "earnings":
            matching = [item for item in earnings.get("events", []) if (not symbol or item["symbol"] == symbol) and 0 <= int(item.get("days_until", 9999)) <= config["threshold"]]
            active, detail = bool(matching), f"{len(matching)} earnings event(s) within {config['threshold']} days."
        elif row["kind"] == "option_expiration":
            matching = [item for item in plan_center["option_attention"] if (not symbol or item["symbol"] == symbol) and int(item["days_remaining"]) <= config["threshold"]]
            active, detail = bool(matching), f"{len(matching)} option plan(s) within {config['threshold']} DTE."
        elif row["kind"] == "day_trade":
            matching = [item for item in screener["items"] if (not symbol or item["symbol"] == symbol) and item["signal"] == "buy_candidate"]
            active, detail = bool(matching), f"{len(matching)} cached setup candidate(s)."
        elif row["kind"] == "data_stale":
            matching = [item for item in screener["items"] if (not symbol or item["symbol"] == symbol) and (item["data_age_days"] or 9999) >= config["threshold"]]
            active, detail = bool(matching), f"{len(matching)} symbol(s) at least {config['threshold']} days stale."
        item = {"id": row["id"], "kind": row["kind"], "symbol": symbol, "config": config, "enabled": bool(row["enabled"]), "active": active, "detail": detail, "updated_at": row["updated_at"]}
        serialized.append(item)
        if active and row["enabled"]:
            alerts.append(item)
    operational_alerts = []

    def operational(key: str, kind: str, detail: str) -> None:
        operational_alerts.append({
            "id": f"operation:{key}", "kind": kind, "symbol": None,
            "config": {}, "enabled": True, "active": True,
            "detail": detail, "updated_at": now_iso(), "operational": True,
        })

    alpha_key, _ = _alpha_vantage_api_key()
    alpaca_key, alpaca_secret, _ = _alpaca_credentials()
    if decision_settings_row["auto_refresh_enabled"] and not alpha_key:
        operational(
            "alpha-vantage", "data_source",
            "Automatic daily decisions are blocked until an Alpha Vantage key is saved.",
        )
    if os.environ.get("INVESTORLAB_INTRADAY_COLLECTION") == "1" and not (alpaca_key and alpaca_secret):
        operational(
            "alpaca", "data_source",
            "Intraday and option collection are paused until Alpaca Paper/IEX credentials are saved.",
        )
    if watchlist_count < 5:
        operational(
            "symbol-pool", "validation_gap",
            f"Validation coverage has {watchlist_count} of 5 required symbols.",
        )
    if plan_center["awaiting_review"]:
        operational(
            "plan-review", "plan_review",
            f"{plan_center['awaiting_review']} saved paper plan(s) await a followed or skipped review.",
        )
    failed_job_types: set[str] = set()
    for run in failed_runs:
        job_type = str(run["job_type"])
        if job_type in failed_job_types:
            continue
        failed_job_types.add(job_type)
        operational(
            f"run-{job_type}", "collection_failure",
            f"{job_type.replace('_', ' ')} was {run['status']} at {run['started_at']}: "
            f"{run['error_text'] or 'some requested items did not complete'}",
        )
    alerts.extend(operational_alerts)
    return {
        "rules": serialized, "operational_alerts": operational_alerts,
        "active_alerts": alerts, "active_count": len(alerts),
        "delivery": "Web notifications while open and iOS local notifications after sync.",
        "background_delivery": False,
        "scope": "Rules are evaluated against cached evidence. APNs remote delivery is not enabled in this local-first build.",
    }


def option_scenario(payload: dict[str, Any]) -> dict[str, Any]:
    spot = decimal_parameter(payload.get("spot"), "Spot price", minimum=Decimal("0.000001"), maximum=Decimal(1_000_000))
    days = int(decimal_parameter(payload.get("days_to_expiration", 30), "Days to expiration", minimum=Decimal(0), maximum=Decimal(3650)))
    quoted_days = int(
        decimal_parameter(
            payload.get("quoted_days_to_expiration", days),
            "Quoted days to expiration",
            minimum=Decimal(0),
            maximum=Decimal(3650),
        )
    )
    if quoted_days < days:
        raise InputError("Quoted days to expiration cannot be less than remaining days.")
    iv_shift = decimal_parameter(payload.get("iv_shift_percent", 0), "IV shift", minimum=Decimal(-95), maximum=Decimal(500))
    raw_legs = payload.get("legs")
    if not isinstance(raw_legs, list) or not 1 <= len(raw_legs) <= 6:
        raise InputError("An option scenario requires 1-6 legs.")
    legs = []
    for index, raw in enumerate(raw_legs, start=1):
        if not isinstance(raw, dict):
            raise InputError(f"Option leg {index} must be an object.")
        right = str(raw.get("right") or "").lower()
        side = str(raw.get("side") or "").lower()
        if right not in {"call", "put"} or side not in {"buy", "sell"}:
            raise InputError(f"Option leg {index} requires call/put and buy/sell.")
        strike = decimal_parameter(raw.get("strike"), f"Leg {index} strike", minimum=Decimal("0.000001"), maximum=Decimal(1_000_000))
        premium = decimal_parameter(raw.get("premium"), f"Leg {index} premium", minimum=Decimal(0), maximum=Decimal(1_000_000))
        quantity = int(decimal_parameter(raw.get("quantity", 1), f"Leg {index} quantity", minimum=Decimal(1), maximum=Decimal(1000)))
        legs.append({"right": right, "side": side, "strike": strike, "premium": premium, "quantity": quantity})
    low = decimal_parameter(payload.get("minimum_spot", spot * Decimal("0.6")), "Minimum scenario spot", minimum=Decimal("0.000001"), maximum=Decimal(1_000_000))
    high = decimal_parameter(payload.get("maximum_spot", spot * Decimal("1.4")), "Maximum scenario spot", minimum=low, maximum=Decimal(1_000_000))
    points = []
    expiration_values = []
    current_values = []
    for index in range(25):
        underlying = low + (high - low) * Decimal(index) / Decimal(24)
        expiration_pnl = Decimal(0)
        modeled_pnl = Decimal(0)
        for leg in legs:
            intrinsic = max(underlying - leg["strike"], Decimal(0)) if leg["right"] == "call" else max(leg["strike"] - underlying, Decimal(0))
            entry_intrinsic = max(spot - leg["strike"], Decimal(0)) if leg["right"] == "call" else max(leg["strike"] - spot, Decimal(0))
            entry_extrinsic = max(leg["premium"] - entry_intrinsic, Decimal(0))
            direction = Decimal(1 if leg["side"] == "buy" else -1)
            time_factor = (
                Decimal(str(math.sqrt(days / quoted_days)))
                if quoted_days > 0 else Decimal(0)
            )
            time_value = entry_extrinsic * time_factor * max(
                Decimal("0.05"), Decimal(1) + iv_shift / 100
            )
            expiration_pnl += direction * (intrinsic - leg["premium"]) * leg["quantity"] * 100
            modeled_pnl += direction * (intrinsic + time_value - leg["premium"]) * leg["quantity"] * 100
        expiration_values.append(expiration_pnl)
        current_values.append(modeled_pnl)
        points.append({"underlying": format(underlying.quantize(Decimal("0.01")), "f"), "expiration_pnl": format(expiration_pnl.quantize(Decimal("0.01")), "f"), "modeled_pnl": format(modeled_pnl.quantize(Decimal("0.01")), "f")})
    breakevens = []
    for previous, current in zip(points, points[1:]):
        left, right = Decimal(previous["expiration_pnl"]), Decimal(current["expiration_pnl"])
        if left == 0:
            breakevens.append(previous["underlying"])
        elif left * right < 0:
            x1, x2 = Decimal(previous["underlying"]), Decimal(current["underlying"])
            estimate = x1 + (Decimal(0) - left) * (x2 - x1) / (right - left)
            breakevens.append(format(estimate.quantize(Decimal("0.01")), "f"))
    net_delta = Decimal(0)
    net_theta = Decimal(0)
    for leg in legs:
        moneyness = (spot - leg["strike"]) / max(spot, Decimal("0.01"))
        call_delta = min(Decimal("0.95"), max(Decimal("0.05"), Decimal("0.5") + moneyness * 4))
        delta = call_delta if leg["right"] == "call" else call_delta - 1
        direction = Decimal(1 if leg["side"] == "buy" else -1)
        net_delta += direction * delta * leg["quantity"] * 100
        entry_intrinsic = max(spot - leg["strike"], Decimal(0)) if leg["right"] == "call" else max(leg["strike"] - spot, Decimal(0))
        entry_extrinsic = max(leg["premium"] - entry_intrinsic, Decimal(0))
        if days > 0 and quoted_days > 0:
            net_theta += (
                -direction
                * entry_extrinsic
                * leg["quantity"]
                * 100
                / (Decimal(2) * Decimal(str(math.sqrt(days * quoted_days))))
            )
    return {
        "spot": format(spot.normalize(), "f"), "days_to_expiration": days,
        "quoted_days_to_expiration": quoted_days,
        "iv_shift_percent": format(iv_shift.normalize(), "f"), "legs": [{**item, "strike": format(item["strike"].normalize(), "f"), "premium": format(item["premium"].normalize(), "f")} for item in legs],
        "payoff_points": points, "breakevens": list(dict.fromkeys(breakevens)),
        "sampled_max_profit": format(max(expiration_values).quantize(Decimal("0.01")), "f"),
        "sampled_max_loss": format(min(expiration_values).quantize(Decimal("0.01")), "f"),
        "modeled_delta_shares": format(net_delta.quantize(Decimal("0.01")), "f"),
        "modeled_theta_per_day": format(net_theta.quantize(Decimal("0.01")), "f"),
        "assignment_risk": any(item["side"] == "sell" and ((item["right"] == "call" and spot > item["strike"]) or (item["right"] == "put" and spot < item["strike"])) for item in legs),
        "scope": "Expiration payoff is deterministic. Pre-expiration P/L and theta decay entry extrinsic value from quoted DTE; delta remains a simplified scenario, not an executable quote or pricing model.",
    }


def strategy_comparison(path: Path, user_id: str, raw_symbol: Any = None) -> dict[str, Any]:
    with open_db(path) as db:
        if raw_symbol:
            symbol = normalize_symbol(raw_symbol)
        else:
            first = db.execute("SELECT symbol FROM watchlist WHERE user_id = ? ORDER BY created_at LIMIT 1", (user_id,)).fetchone()
            symbol = str(first["symbol"]) if first else "SPY"
        rows = _decision_market_rows(db, symbol)
        benchmark = _decision_market_rows(db, "SPY")
        profile = _investor_profile_from_db(db, user_id)
        fundamentals = _sec_cached(db, f"fundamentals:{symbol}") or {}
        history = list(fundamentals.get("annual_history") or [])
        versions = _strategy_versions_from_db(db, user_id)[:12]
        decision_runs = _decision_rows(db, user_id, limit=1_000_000)
    comparisons = []
    if not versions:
        versions = [{
            "id": "profile-default", "name": f"{profile['strategy_style']} default",
            "version_number": 1, "config_hash": "profile",
            "created_at": profile.get("updated_at"),
            "profile_default": True,
            "config": {"technical_weight": 60, "fundamental_weight": 25, "valuation_weight": 0, "portfolio_weight": 15, "fee_slippage_bps": 10},
        }]
    for version in versions:
        config = version["config"]
        template = None if version.get("profile_default") else {
            "id": version["id"], "name": version["name"],
            "technical_weight": int(config["technical_weight"]),
            "fundamental_weight": int(config["fundamental_weight"]),
            "valuation_weight": int(config.get("valuation_weight", 0)),
            "portfolio_weight": int(config["portfolio_weight"]),
            "fee_slippage_bps": int(config["fee_slippage_bps"]),
            "version_id": version["id"], "version_number": version["version_number"],
            "config_hash": version["config_hash"],
        }
        strategy_frozen_at = _strategy_context_frozen_at(
            decision_runs,
            DECISION_MODEL_VERSION,
            profile["strategy_style"],
            profile["time_horizon"],
            template,
        )
        backtest = _walk_forward_backtest(
            rows, profile["strategy_style"], profile["time_horizon"], history,
            int(config["fee_slippage_bps"]), template, benchmark,
            include_sensitivity=False, strategy_frozen_at=strategy_frozen_at,
        )
        holdout = backtest.get("out_of_sample") or {}
        comparisons.append({
            "version_id": version["id"], "name": version["name"],
            "version_number": version["version_number"], "config_hash": version["config_hash"],
            "available": backtest.get("available", False),
            "strategy_return_percent": backtest.get("strategy_return_percent"),
            "relative_to_spy_percent": backtest.get("relative_to_spy_percent"),
            "max_drawdown_percent": backtest.get("max_drawdown_percent"),
            "win_rate_percent": backtest.get("win_rate_percent"),
            "completed_trades": backtest.get("completed_trades", 0),
            "average_cost_bps": backtest.get("average_modeled_cost_bps_per_side"),
            "out_of_sample_available": holdout.get("available", False),
            "out_of_sample_sessions": holdout.get("sessions", 0),
            "out_of_sample_return_percent": holdout.get("strategy_return_percent"),
            "out_of_sample_relative_to_spy_percent": holdout.get("relative_to_spy_percent"),
            "out_of_sample_reason": holdout.get("reason"),
            "reason": backtest.get("reason"),
        })
    return {
        "symbol": symbol, "comparisons": comparisons, "leader_version_id": None,
        "selection_rule": "No automatic leader is selected from holdout results. Using a holdout to choose a winner turns it into model-selection data.",
        "scope": "Point-in-time walk-forward research with per-version freeze-date checks. Comparison is descriptive and does not auto-activate a strategy.",
    }


def portfolio_intelligence(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        portfolio_state = _portfolio_from_db(db, user_id)
        risk = _portfolio_risk_from_db(db, user_id)
        performance = _portfolio_performance_from_db(db, user_id)
        profile = _investor_profile_from_db(db, user_id)
        trades = _trade_rows(db, user_id, 10_000)
        spy = _decision_market_rows(db, "SPY")
    paper_capital = Decimal(profile["paper_account_size"])
    gross = Decimal(risk["gross_exposure"])
    cash_estimate = max(Decimal(0), paper_capital - gross)
    spy_return = None
    if len(spy) >= 2:
        spy_return = (Decimal(int(spy[-1]["close_micros"])) / Decimal(int(spy[0]["close_micros"])) - 1) * 100
    buys = [item for item in trades if item["side"] == "buy"]
    sells = [item for item in trades if item["side"] == "sell"]
    recent_loss_symbols = set()
    for symbol in {item["symbol"] for item in trades}:
        symbol_trades = [item for item in trades if item["symbol"] == symbol]
        if any(item["side"] == "sell" for item in symbol_trades[-5:]):
            recent_loss_symbols.add(symbol)
    return {
        "paper_capital": format(paper_capital.quantize(Decimal("0.01")), "f"),
        "gross_exposure": risk["gross_exposure"],
        "cash_estimate": format(cash_estimate.quantize(Decimal("0.01")), "f"),
        "invested_percent": _percent(gross * 100 / paper_capital) if paper_capital else "0.00",
        "largest_position_percent": risk.get("largest_weight_percent", "0"),
        "sector_exposure": risk.get("sectors", []),
        "stress_scenarios": risk.get("stress_scenarios", []),
        "correlations": risk.get("correlations", []),
        "performance": performance,
        "benchmark": {"symbol": "SPY", "cached_full_period_return_percent": _percent(spy_return) if spy_return is not None else None},
        "tax_lot_review": {
            "buy_entries": len(buys), "sell_entries": len(sells),
            "symbols_with_recent_sales": sorted(recent_loss_symbols),
            "warning": "This ledger does not determine tax basis, wash sales, holding period, or replacement shares across outside accounts. Review broker tax lots before any tax decision.",
        },
        "scope": "Uses the local paper ledger, latest cached marks, and user-entered starting capital. Cash and tax fields are planning estimates.",
    }


def data_quality_center(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        user_symbols = _user_symbols_from_db(db, user_id)
        placeholders = ",".join("?" for _ in user_symbols)
        market = (
            db.execute(
                f"SELECT symbol, COUNT(*) AS bars, MIN(trading_date) AS first_date, "
                f"MAX(trading_date) AS latest_date, MAX(fetched_at) AS fetched_at, "
                f"GROUP_CONCAT(DISTINCT source) AS sources "
                f"FROM market_daily WHERE symbol IN ({placeholders}) "
                "GROUP BY symbol ORDER BY symbol",
                user_symbols,
            ).fetchall()
            if user_symbols else []
        )
        intraday = (
            int(db.execute(
                f"SELECT COUNT(DISTINCT symbol || ':' || substr(bar_timestamp, 1, 10)) "
                f"FROM intraday_bars WHERE symbol IN ({placeholders})",
                user_symbols,
            ).fetchone()[0])
            if user_symbols else 0
        )
        intraday_rows = (
            db.execute(
                f"SELECT symbol, bar_timestamp, source, fetched_at FROM intraday_bars "
                f"WHERE symbol IN ({placeholders}) AND timeframe = '1Min' "
                "ORDER BY symbol, bar_timestamp",
                user_symbols,
            ).fetchall()
            if user_symbols else []
        )
        option_snapshots = int(db.execute("SELECT COUNT(*) FROM option_chain_snapshots WHERE user_id = ?", (user_id,)).fetchone()[0])
        option_rows = db.execute(
            "SELECT symbol, result_json, fetched_at FROM option_chain_snapshots "
            "WHERE user_id = ? ORDER BY fetched_at DESC, id DESC LIMIT 100",
            (user_id,),
        ).fetchall()
        adjustments = (
            int(db.execute(
                f"SELECT COUNT(*) FROM market_adjustments WHERE symbol IN ({placeholders})",
                user_symbols,
            ).fetchone()[0])
            if user_symbols else 0
        )
        last_runs = _collection_runs_from_db(db, user_id, 20)
    current = date.today()
    symbols = []
    for row in market:
        age = (current - date.fromisoformat(str(row["latest_date"]))).days
        symbols.append({**dict(row), "age_days": age, "status": "current" if age <= 7 else "stale"})
    now_ny = datetime.now(timezone.utc).astimezone(NEW_YORK)
    intraday_groups: dict[tuple[str, date], list[datetime]] = {}
    for row in intraday_rows:
        timestamp = datetime.fromisoformat(
            str(row["bar_timestamp"]).replace("Z", "+00:00")
        ).astimezone(NEW_YORK)
        if (9, 30) <= (timestamp.hour, timestamp.minute) < (16, 0):
            intraday_groups.setdefault((str(row["symbol"]), timestamp.date()), []).append(timestamp)
    intraday_quality = [
        {
            "symbol": symbol,
            "session_date": session_date.isoformat(),
            **intraday_coverage(timestamps, session_date=session_date, as_of=now_ny),
        }
        for (symbol, session_date), timestamps in sorted(intraday_groups.items())
    ]
    latest_options: dict[str, dict[str, Any]] = {}
    for row in option_rows:
        symbol = str(row["symbol"])
        if symbol in latest_options:
            continue
        payload = json.loads(str(row["result_json"]))
        latest_options[symbol] = {
            "symbol": symbol,
            "fetched_at": row["fetched_at"],
            **option_snapshot_quality(payload.get("contracts") or []),
        }
    option_quality = list(latest_options.values())
    failures = [item for item in last_runs if item["status"] in {"failed", "partial"}]
    return {
        "generated_at": now_iso(),
        "summary": {
            "symbols": len(symbols), "daily_bars": sum(int(item["bars"]) for item in symbols),
            "stale_symbols": sum(item["status"] == "stale" for item in symbols),
            "intraday_sessions": intraday, "option_snapshots": option_snapshots,
            "corporate_actions": adjustments, "recent_failed_runs": len(failures),
            "intraday_missing_minutes": sum(item["missing_minutes"] for item in intraday_quality),
            "partial_intraday_sessions": sum(item["status"] != "ready" for item in intraday_quality),
            "option_crossed_markets": sum(item["crossed_markets"] for item in option_quality),
            "option_wide_spreads": sum(item["wide_spreads"] for item in option_quality),
        },
        "symbols": symbols,
        "intraday_quality": intraday_quality,
        "option_quality": option_quality,
        "recent_runs": last_runs,
        "recent_failures": failures,
        "provider_status": market_status(path, user_id),
        "policies": {
            "daily_cache_minutes": os.environ.get("INVESTORLAB_MARKET_CACHE_MINUTES", "720"),
            "adjusted_history": os.environ.get("INVESTORLAB_ADJUSTED_DAILY") == "1",
            "intraday_collection": os.environ.get("INVESTORLAB_INTRADAY_COLLECTION") == "1",
            "maximum_cached_scan_universe": 250,
        },
        "scope": "Shows stored coverage and collection outcomes. It never treats missing or stale data as a valid signal.",
    }


def research_copilot(path: Path, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    symbol = normalize_symbol(payload.get("symbol"))
    question = str(payload.get("question") or "Summarize the evidence and risks.").strip()
    if not 1 <= len(question) <= 500:
        raise InputError("Copilot question must be 1-500 characters.")
    with open_db(path) as db:
        decision = next((item for item in _decision_center_from_db(db, user_id)["latest"] if item["symbol"] == symbol), None)
        market = _market_research_from_db(db, symbol)
        fundamentals = _sec_cached(db, f"fundamentals:{symbol}") or {"available": False}
        position = next((
            item for item in _portfolio_risk_from_db(db, user_id)["positions"]
            if item["symbol"] == symbol and item["asset_type"] == "equity"
        ), None)
    evidence = []
    if decision:
        evidence.append({"source": "Decision engine", "as_of": decision["created_at"], "fact": f"{decision['signal_label']} with score {decision.get('score') or '—'}/100.", "reference": f"decision:{decision['id']}"})
    if market.get("available"):
        evidence.append({"source": "Cached daily market data", "as_of": market.get("trading_date"), "fact": f"Close {market.get('latest_close')}; {market.get('state_label')}.", "reference": f"market:{symbol}"})
    if fundamentals.get("available"):
        metrics = fundamentals.get("metrics") or {}
        evidence.append({"source": "SEC company facts", "as_of": fundamentals.get("fetched_at"), "fact": f"Latest fiscal evidence includes revenue {metrics.get('revenue') or 'not available'} and net income {metrics.get('net_income') or 'not available'}.", "reference": fundamentals.get("source_url")})
    def decision_statements(primary_key: str, legacy_key: str) -> list[str]:
        if not decision:
            return []
        def normalized(key: str) -> list[str]:
            values = decision.get(key)
            if not isinstance(values, list):
                return []
            return [item.strip() for item in values if isinstance(item, str) and item.strip()]

        return normalized(primary_key) or normalized(legacy_key)

    thesis = decision_statements("evidence", "reasons")[:3]
    if not thesis:
        thesis = ["Run or refresh a decision to create a rules-based thesis."]
    risks = decision_statements("counter_evidence", "risks")[:4]
    if position:
        risks.append(f"Current paper exposure is {position['weight_percent']}% of marked portfolio exposure.")
    if not fundamentals.get("available"):
        risks.append("SEC fundamental evidence is missing or not refreshed.")
    if not market.get("available"):
        risks.append("Daily price evidence is missing.")
    return {
        "symbol": symbol, "question": question,
        "answer": f"{symbol} currently reads as {decision['signal_label'].lower() if decision else 'data required'}. The conclusion is limited to the cited cached evidence; refresh missing inputs before changing a paper plan.",
        "thesis": thesis, "counter_thesis": list(dict.fromkeys(risks))[:5],
        "checklist": ["Verify the latest trading date.", "Open the cited SEC filing or company-facts source.", "Compare the saved entry, stop, target, and position cap.", "Record the decision and later outcome in the journal."],
        "evidence": evidence,
        "engine": "Local evidence composer; no external LLM call or API charge.",
        "scope": "Every statement is composed from stored deterministic results. It does not invent prices, filings, or forecasts.",
    }


def generate_research_report(path: Path, user_id: str, period: str = "daily") -> dict[str, Any]:
    if period not in {"daily", "weekly"}:
        raise InputError("Report period must be daily or weekly.")
    today = date.today()
    report_date = (today - timedelta(days=today.weekday())).isoformat() if period == "weekly" else today.isoformat()
    with open_db(path) as db:
        briefing = _daily_briefing_from_db(db, user_id)
        portfolio_state = _portfolio_performance_from_db(db, user_id)
        review = _review_stats_from_db(db, user_id)
        validation = None
    if period == "weekly":
        validation = validation_dashboard(path, user_id, 60)
    content = {
        "period": period, "report_date": report_date, "generated_at": now_iso(),
        "calculation_version": PORTFOLIO_CALCULATION_VERSION,
        "headline": briefing["headline"], "summary": briefing["summary"],
        "priority_tasks": briefing["tasks"][:10], "portfolio_performance": portfolio_state,
        "review_stats": review, "validation": validation,
        "next_session_checklist": [
            "Resolve critical risk and stale-data items before reviewing new candidates.",
            "Confirm entry, stop, target, and maximum paper order value.",
            "Record skipped and followed plans so the validation sample remains honest.",
        ],
        "scope": "Generated from the local ledger and cached evidence; it is a research record, not an account statement.",
    }
    report_id = str(uuid4())
    with open_db(path) as db:
        existing = db.execute("SELECT id FROM research_reports WHERE user_id = ? AND period = ? AND report_date = ?", (user_id, period, report_date)).fetchone()
        if existing:
            report_id = str(existing["id"])
            db.execute("UPDATE research_reports SET content_json = ?, created_at = ? WHERE id = ?", (json.dumps(content), content["generated_at"], report_id))
        else:
            db.execute("INSERT INTO research_reports VALUES (?, ?, ?, ?, ?, ?)", (report_id, user_id, period, report_date, json.dumps(content), content["generated_at"]))
        _append_sync_event(db, user_id, "research_report", report_id, "upsert", {"period": period, "report_date": report_date, "headline": content["headline"]})
    return {"id": report_id, **content}


def list_research_reports(path: Path, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with open_db(path) as db:
        rows = db.execute("SELECT id, content_json FROM research_reports WHERE user_id = ? ORDER BY report_date DESC, created_at DESC LIMIT ?", (user_id, max(1, min(limit, 100)))).fetchall()
    reports = []
    for row in rows:
        content = json.loads(row["content_json"])
        content.setdefault("calculation_version", "legacy-pre-v0.1.6")
        reports.append({"id": row["id"], **content})
    return reports


def research_command_center(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        briefing = _daily_briefing_from_db(db, user_id)
        preset_count = int(db.execute("SELECT COUNT(*) FROM scanner_presets WHERE user_id = ?", (user_id,)).fetchone()[0])
        rule_count = int(db.execute("SELECT COUNT(*) FROM notification_rules WHERE user_id = ? AND enabled = 1", (user_id,)).fetchone()[0])
        report_count = int(db.execute("SELECT COUNT(*) FROM research_reports WHERE user_id = ?", (user_id,)).fetchone()[0])
        versions = len(_strategy_versions_from_db(db, user_id))
    control = paper_order_control(path, user_id)
    quality = data_quality_center(path, user_id)
    notifications = notification_center(path, user_id)
    return {
        "generated_at": now_iso(), "briefing": briefing,
        "paper_execution": control,
        "counts": {"scanner_presets": preset_count, "notification_rules": rule_count, "reports": report_count, "strategy_versions": versions},
        "data_quality": quality["summary"], "active_notifications": notifications["active_alerts"],
        "capabilities": [
            {"key": "paper_execution", "status": "enabled" if control["enabled"] else "locked", "cost": "Alpaca Paper account"},
            {"key": "universe_scanner", "status": "ready", "cost": "No scan API charge; cached data"},
            {"key": "options_scenarios", "status": "ready", "cost": "Local calculation"},
            {"key": "strategy_comparison", "status": "ready", "cost": "Local calculation"},
            {"key": "research_copilot", "status": "ready", "cost": "No LLM API charge"},
            {"key": "local_notifications", "status": "ready", "cost": "No APNs server"},
        ],
        "scope": "Private research and Alpaca Paper workflow. Real-money broker routing is not implemented.",
    }


def market_clock(path: Path) -> dict[str, Any]:
    now_ny = datetime.now(timezone.utc).astimezone(NEW_YORK)
    key_id, secret, source = _alpaca_credentials()
    if not key_id or not secret:
        weekday = now_ny.weekday() < 5
        minutes = now_ny.hour * 60 + now_ny.minute
        phase = "regular" if weekday and 570 <= minutes < 960 else "premarket" if weekday and 240 <= minutes < 570 else "closed"
        return {
            "available": True,
            "is_open": phase == "regular",
            "session_phase": phase,
            "timestamp": now_ny.isoformat(timespec="seconds"),
            "next_open": None,
            "next_close": None,
            "calendar": [],
            "source": "Local weekday/session estimate",
            "configuration_source": "unconfigured",
            "warning": "Holiday and early-close handling requires configured Alpaca credentials.",
        }
    with open_db(path) as db:
        cached = _sec_cached(db, "alpaca-market-clock", timedelta(seconds=30))
        if cached is not None:
            return {**cached, "cache_hit": True}
    clock = _alpaca_trading_json("/v2/clock", {}, key_id, secret)
    if not isinstance(clock, dict):
        raise ApiError(502, "Alpaca market clock returned a malformed response.")
    calendar = _alpaca_trading_json(
        "/v2/calendar",
        {"start": now_ny.date().isoformat(), "end": (now_ny.date() + timedelta(days=10)).isoformat()},
        key_id,
        secret,
    )
    if not isinstance(calendar, list):
        raise ApiError(502, "Alpaca market calendar returned a malformed response.")
    minutes = now_ny.hour * 60 + now_ny.minute
    today_text = now_ny.date().isoformat()
    is_trading_day = any(
        isinstance(item, dict) and str(item.get("date")) == today_text
        for item in calendar
    )
    phase = (
        "regular" if bool(clock.get("is_open"))
        else "premarket" if is_trading_day and 240 <= minutes < 570
        else "closed"
    )
    result = {
        "available": True,
        "is_open": bool(clock.get("is_open")),
        "session_phase": phase,
        "timestamp": clock.get("timestamp") or now_ny.isoformat(timespec="seconds"),
        "next_open": clock.get("next_open"),
        "next_close": clock.get("next_close"),
        "calendar": [
            {key: item.get(key) for key in ("date", "open", "close", "session_open", "session_close")}
            for item in calendar[:7] if isinstance(item, dict)
        ],
        "source": "Alpaca Trading API clock and calendar",
        "configuration_source": source,
        "warning": None,
    }
    with open_db(path) as db:
        _store_sec_cache(db, "alpaca-market-clock", result)
    return {**result, "cache_hit": False}


def _parse_occ_option_symbol(contract_symbol: str) -> dict[str, Any] | None:
    match = OCC_OPTION_RE.fullmatch(contract_symbol.upper())
    if not match:
        return None
    root, raw_expiration, right, raw_strike = match.groups()
    try:
        expiration = datetime.strptime(raw_expiration, "%y%m%d").date()
    except ValueError:
        return None
    return {
        "root": root,
        "expiration": expiration,
        "right": "call" if right == "C" else "put",
        "strike": Decimal(int(raw_strike)) / Decimal(1000),
    }


def _option_chain_candidates(
    contracts: list[dict[str, Any]], underlying: Decimal | None
) -> list[dict[str, Any]]:
    eligible = [
        item for item in contracts
        if item["liquid"] and 7 <= item["days_to_expiration"] <= 60
    ]
    if not eligible:
        return []
    candidates = []
    for right in ("call", "put"):
        side = [item for item in eligible if item["right"] == right]
        if not side:
            continue
        side.sort(
            key=lambda item: (
                item["days_to_expiration"],
                abs(Decimal(item["strike"]) - underlying) if underlying is not None else Decimal(0),
                Decimal(item["spread_percent"]),
            )
        )
        long_leg = side[0]
        premium = Decimal(long_leg["ask"])
        strike = Decimal(long_leg["strike"])
        candidates.append(
            {
                "strategy": f"long_{right}",
                "label": f"Long {right}",
                "expiration": long_leg["expiration"],
                "days_to_expiration": long_leg["days_to_expiration"],
                "legs": [{"action": "buy", **{key: long_leg[key] for key in ("contract_symbol", "right", "strike")}}],
                "net_debit_per_share": format(premium.normalize(), "f"),
                "maximum_loss_per_contract": format((premium * 100).quantize(Decimal("0.01")), "f"),
                "maximum_profit": "unlimited" if right == "call" else format((max(Decimal(0), strike - premium) * 100).quantize(Decimal("0.01")), "f"),
                "breakeven": format((strike + premium if right == "call" else strike - premium).quantize(Decimal("0.0001")), "f"),
                "liquidity_note": f"Observed indicative spread {long_leg['spread_percent']}%; volume {long_leg['volume']}.",
            }
        )
    by_expiration: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in eligible:
        by_expiration.setdefault((item["expiration"], item["right"]), []).append(item)
    for strategy, right in (("bull_call_spread", "call"), ("bear_put_spread", "put")):
        groups = [items for (expiration, item_right), items in by_expiration.items() if item_right == right]
        groups.sort(key=lambda items: min(item["days_to_expiration"] for item in items))
        selected = None
        for items in groups:
            strikes = sorted(items, key=lambda item: Decimal(item["strike"]))
            anchor = min(
                range(len(strikes)),
                key=lambda index: abs(Decimal(strikes[index]["strike"]) - underlying)
                if underlying is not None else index,
            )
            if strategy == "bull_call_spread" and anchor + 1 < len(strikes):
                selected = strikes[anchor], strikes[anchor + 1]
                break
            if strategy == "bear_put_spread" and anchor > 0:
                selected = strikes[anchor], strikes[anchor - 1]
                break
        if not selected:
            continue
        long_leg, short_leg = selected
        debit = Decimal(long_leg["ask"]) - Decimal(short_leg["bid"])
        width = abs(Decimal(long_leg["strike"]) - Decimal(short_leg["strike"]))
        if debit <= 0 or debit >= width:
            continue
        breakeven = (
            Decimal(long_leg["strike"]) + debit
            if right == "call" else Decimal(long_leg["strike"]) - debit
        )
        candidates.append(
            {
                "strategy": strategy,
                "label": "Bull call debit spread" if right == "call" else "Bear put debit spread",
                "expiration": long_leg["expiration"],
                "days_to_expiration": long_leg["days_to_expiration"],
                "legs": [
                    {"action": "buy", **{key: long_leg[key] for key in ("contract_symbol", "right", "strike")}},
                    {"action": "sell", **{key: short_leg[key] for key in ("contract_symbol", "right", "strike")}},
                ],
                "net_debit_per_share": format(debit.quantize(Decimal("0.0001")), "f"),
                "maximum_loss_per_contract": format((debit * 100).quantize(Decimal("0.01")), "f"),
                "maximum_profit": format(((width - debit) * 100).quantize(Decimal("0.01")), "f"),
                "breakeven": format(breakeven.quantize(Decimal("0.0001")), "f"),
                "liquidity_note": f"Both legs pass the 20% indicative-spread gate; width {format(width.normalize(), 'f')}.",
            }
        )
    expirations = sorted({item["expiration"] for item in eligible})
    if expirations:
        nearest = expirations[0]
        calls = sorted(
            [item for item in eligible if item["expiration"] == nearest and item["right"] == "call"],
            key=lambda item: abs(Decimal(item["strike"]) - underlying) if underlying is not None else Decimal(item["strike"]),
        )
        puts = sorted(
            [item for item in eligible if item["expiration"] == nearest and item["right"] == "put"],
            key=lambda item: abs(Decimal(item["strike"]) - underlying) if underlying is not None else Decimal(item["strike"]),
        )
        if calls and puts:
            call, put = calls[0], puts[0]
            debit = Decimal(call["ask"]) + Decimal(put["ask"])
            lower = Decimal(put["strike"]) - debit
            upper = Decimal(call["strike"]) + debit
            candidates.append({
                "strategy": "long_straddle",
                "label": "Long volatility straddle",
                "expiration": nearest,
                "days_to_expiration": call["days_to_expiration"],
                "legs": [
                    {"action": "buy", **{key: call[key] for key in ("contract_symbol", "right", "strike")}},
                    {"action": "buy", **{key: put[key] for key in ("contract_symbol", "right", "strike")}},
                ],
                "net_debit_per_share": format(debit.quantize(Decimal("0.0001")), "f"),
                "maximum_loss_per_contract": format((debit * 100).quantize(Decimal("0.01")), "f"),
                "maximum_profit": "unlimited upside; substantial downside potential",
                "breakeven": f"{format(lower.quantize(Decimal('0.0001')), 'f')} / {format(upper.quantize(Decimal('0.0001')), 'f')}",
                "liquidity_note": "Both long legs pass the indicative liquidity gate; time decay works against the position.",
            })
        strike_sorted_puts = sorted(
            [item for item in eligible if item["expiration"] == nearest and item["right"] == "put"],
            key=lambda item: Decimal(item["strike"]),
        )
        strike_sorted_calls = sorted(
            [item for item in eligible if item["expiration"] == nearest and item["right"] == "call"],
            key=lambda item: Decimal(item["strike"]),
        )
        if underlying is not None:
            lower_puts = [item for item in strike_sorted_puts if Decimal(item["strike"]) < underlying]
            upper_calls = [item for item in strike_sorted_calls if Decimal(item["strike"]) > underlying]
            if len(lower_puts) >= 2 and len(upper_calls) >= 2:
                long_put, short_put = lower_puts[-2], lower_puts[-1]
                short_call, long_call = upper_calls[0], upper_calls[1]
                credit = Decimal(short_put["bid"]) + Decimal(short_call["bid"]) - Decimal(long_put["ask"]) - Decimal(long_call["ask"])
                width = max(
                    Decimal(short_put["strike"]) - Decimal(long_put["strike"]),
                    Decimal(long_call["strike"]) - Decimal(short_call["strike"]),
                )
                if Decimal(0) < credit < width:
                    candidates.append({
                        "strategy": "iron_condor",
                        "label": "Defined-risk iron condor",
                        "expiration": nearest,
                        "days_to_expiration": short_call["days_to_expiration"],
                        "legs": [
                            {"action": "buy", **{key: long_put[key] for key in ("contract_symbol", "right", "strike")}},
                            {"action": "sell", **{key: short_put[key] for key in ("contract_symbol", "right", "strike")}},
                            {"action": "sell", **{key: short_call[key] for key in ("contract_symbol", "right", "strike")}},
                            {"action": "buy", **{key: long_call[key] for key in ("contract_symbol", "right", "strike")}},
                        ],
                        "net_debit_per_share": format((-credit).quantize(Decimal("0.0001")), "f"),
                        "maximum_loss_per_contract": format(((width - credit) * 100).quantize(Decimal("0.01")), "f"),
                        "maximum_profit": format((credit * 100).quantize(Decimal("0.01")), "f"),
                        "breakeven": f"{format((Decimal(short_put['strike']) - credit).quantize(Decimal('0.0001')), 'f')} / {format((Decimal(short_call['strike']) + credit).quantize(Decimal('0.0001')), 'f')}",
                        "liquidity_note": "All four legs pass the indicative liquidity gate; assignment and pin risk remain near expiration.",
                    })
    return candidates


def _option_filters(query: dict[str, list[str]]) -> dict[str, Any]:
    def integer(name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(query.get(name, [str(default)])[0])
        except ValueError:
            raise InputError(f"{name} must be an integer.") from None
        if not minimum <= value <= maximum:
            raise InputError(f"{name} must be between {minimum} and {maximum}.")
        return value

    right = str(query.get("right", ["all"])[0]).lower()
    if right not in {"all", "call", "put"}:
        raise InputError("right must be all, call, or put.")
    delta_min = decimal_parameter(query.get("delta_min", ["-1"])[0], "delta_min", minimum=Decimal("-1"), maximum=Decimal("1"))
    delta_max = decimal_parameter(query.get("delta_max", ["1"])[0], "delta_max", minimum=Decimal("-1"), maximum=Decimal("1"))
    if delta_min > delta_max:
        raise InputError("delta_min cannot exceed delta_max.")
    return {
        "min_dte": integer("min_dte", 0, 0, 730),
        "max_dte": integer("max_dte", 120, 0, 730),
        "right": right,
        "min_volume": integer("min_volume", 0, 0, 100_000_000),
        "max_spread_percent": decimal_parameter(query.get("max_spread_percent", ["100"])[0], "max_spread_percent", minimum=Decimal("0"), maximum=Decimal("1000")),
        "delta_min": delta_min,
        "delta_max": delta_max,
        "liquid_only": str(query.get("liquid_only", ["false"])[0]).lower() in {"1", "true", "yes"},
    }


def _option_analytics(
    contracts: list[dict[str, Any]], underlying: Decimal | None, portfolio: dict[str, Any]
) -> dict[str, Any]:
    iv_contracts = [item for item in contracts if item.get("implied_volatility_percent") is not None]
    term_structure = []
    for expiration in sorted({item["expiration"] for item in iv_contracts}):
        items = [item for item in iv_contracts if item["expiration"] == expiration]
        atm = min(
            items,
            key=lambda item: abs(Decimal(item["strike"]) - underlying) if underlying is not None else Decimal(item["strike"]),
        )
        term_structure.append({
            "expiration": expiration,
            "days_to_expiration": atm["days_to_expiration"],
            "atm_iv_percent": atm["implied_volatility_percent"],
        })
    smile = []
    if term_structure:
        nearest = term_structure[0]["expiration"]
        smile = [
            {"strike": item["strike"], "right": item["right"], "iv_percent": item["implied_volatility_percent"], "delta": item.get("delta")}
            for item in iv_contracts if item["expiration"] == nearest
        ][:80]
    skew = None
    if term_structure:
        nearest = term_structure[0]["expiration"]
        calls = [item for item in iv_contracts if item["expiration"] == nearest and item["right"] == "call" and item.get("delta") is not None]
        puts = [item for item in iv_contracts if item["expiration"] == nearest and item["right"] == "put" and item.get("delta") is not None]
        if calls and puts:
            call = min(calls, key=lambda item: abs(Decimal(str(item["delta"])) - Decimal("0.25")))
            put = min(puts, key=lambda item: abs(Decimal(str(item["delta"])) + Decimal("0.25")))
            skew = {
                "expiration": nearest,
                "call_25_delta_iv_percent": call["implied_volatility_percent"],
                "put_25_delta_iv_percent": put["implied_volatility_percent"],
                "put_minus_call_points": _percent(Decimal(put["implied_volatility_percent"]) - Decimal(call["implied_volatility_percent"])),
            }
    by_contract = {item["contract_symbol"]: item for item in contracts}
    exposures = {"delta_shares": Decimal(0), "gamma": Decimal(0), "theta_per_day": Decimal(0), "vega": Decimal(0)}
    matched_positions = 0
    for position in portfolio.get("positions", []):
        if position.get("asset_type") != "option" or position.get("symbol") not in by_contract:
            continue
        contract = by_contract[position["symbol"]]
        quantity = Decimal(str(position["quantity"])) * 100
        matched_positions += 1
        for source_key, target_key in (("delta", "delta_shares"), ("gamma", "gamma"), ("theta", "theta_per_day"), ("vega", "vega")):
            if contract.get(source_key) is not None:
                exposures[target_key] += Decimal(str(contract[source_key])) * quantity
    return {
        "term_structure": term_structure,
        "nearest_expiration_smile": smile,
        "twenty_five_delta_skew": skew,
        "portfolio_greeks": {
            "matched_positions": matched_positions,
            **{key: _percent(value) for key, value in exposures.items()},
            "scope": "Indicative snapshot Greeks multiplied by the current paper option quantity and 100-share contract multiplier.",
        },
    }


def _apply_option_filters(result: dict[str, Any], filters: dict[str, Any]) -> dict[str, Any]:
    contracts = []
    for item in result.get("contracts") or []:
        spread = Decimal(str(item["spread_percent"])) if item.get("spread_percent") is not None else Decimal("999999")
        delta = Decimal(str(item["delta"])) if item.get("delta") is not None else None
        if not filters["min_dte"] <= int(item["days_to_expiration"]) <= filters["max_dte"]:
            continue
        if filters["right"] != "all" and item["right"] != filters["right"]:
            continue
        if int(item["volume"]) < filters["min_volume"] or spread > filters["max_spread_percent"]:
            continue
        if delta is not None and not filters["delta_min"] <= delta <= filters["delta_max"]:
            continue
        if filters["liquid_only"] and not item["liquid"]:
            continue
        contracts.append(item)
    allowed_contracts = {item["contract_symbol"] for item in contracts}
    candidates = [
        item for item in result.get("candidates") or []
        if all(leg.get("contract_symbol") in allowed_contracts for leg in item.get("legs") or [])
    ]
    return {
        **result,
        "contracts": contracts,
        "candidates": candidates,
        "filtered_contract_count": len(contracts),
        "filters": {key: format(value, "f") if isinstance(value, Decimal) else value for key, value in filters.items()},
    }


def option_chain(
    path: Path, user_id: str, raw_symbol: Any, filters: dict[str, Any] | None = None
) -> dict[str, Any]:
    symbol = normalize_symbol(raw_symbol)
    active_filters = filters or _option_filters({})
    key_id, secret, source = _alpaca_credentials()
    if not key_id or not secret:
        return {
            "available": False, "configured": False, "symbol": symbol,
            "provider": "Alpaca Market Data", "feed": "indicative",
            "reason": "Save personal Alpaca Market Data credentials to load option snapshots.",
            "data_scope": "Option-chain snapshots are not configured.",
        }
    with open_db(path) as db:
        cached = db.execute(
            "SELECT result_json, fetched_at FROM option_chain_snapshots "
            "WHERE user_id = ? AND symbol = ? ORDER BY fetched_at DESC, id DESC LIMIT 1",
            (user_id, symbol),
        ).fetchone()
        if cached:
            fetched_at = datetime.fromisoformat(str(cached["fetched_at"]).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - fetched_at < timedelta(seconds=60):
                return _apply_option_filters(
                    {**json.loads(cached["result_json"]), "cache_hit": True}, active_filters
                )
        market = _market_research_from_db(db, symbol, include_history=False)
        underlying = Decimal(str(market["latest_close"])) if market.get("available") else None
        portfolio_state = _portfolio_from_db(db, user_id)
        previous_iv = [
            Decimal(int(row["atm_iv_percent_micros"])) / SCALE
            for row in db.execute(
                "SELECT atm_iv_percent_micros FROM option_chain_snapshots "
                "WHERE user_id = ? AND symbol = ? AND atm_iv_percent_micros IS NOT NULL "
                "ORDER BY fetched_at DESC LIMIT 100",
                (user_id, symbol),
            ).fetchall()
        ]
    snapshots: dict[str, Any] = {}
    next_page_token = None
    pages_loaded = 0
    for _ in range(5):
        parameters: dict[str, Any] = {"feed": "indicative", "limit": 1000}
        if next_page_token:
            parameters["page_token"] = next_page_token
        payload = _alpaca_json(
            f"/v1beta1/options/snapshots/{symbol}", parameters, key_id, secret
        )
        page_snapshots = payload.get("snapshots")
        if isinstance(page_snapshots, dict):
            snapshots.update(page_snapshots)
        pages_loaded += 1
        next_page_token = payload.get("next_page_token")
        if not next_page_token:
            break
    today = datetime.now(timezone.utc).astimezone(NEW_YORK).date()
    contracts = []
    for contract_symbol, snapshot in snapshots.items():
        if not isinstance(snapshot, dict):
            continue
        parsed = _parse_occ_option_symbol(str(contract_symbol))
        if not parsed or parsed["root"] != symbol:
            continue
        dte = (parsed["expiration"] - today).days
        if dte < 0 or dte > 120:
            continue
        quote = snapshot.get("latestQuote") if isinstance(snapshot.get("latestQuote"), dict) else {}
        trade = snapshot.get("latestTrade") if isinstance(snapshot.get("latestTrade"), dict) else {}
        daily = snapshot.get("dailyBar") if isinstance(snapshot.get("dailyBar"), dict) else {}
        greeks = snapshot.get("greeks") if isinstance(snapshot.get("greeks"), dict) else {}
        bid = _metric_decimal(quote.get("bp")) or Decimal(0)
        ask = _metric_decimal(quote.get("ap")) or Decimal(0)
        mid = (bid + ask) / 2 if bid > 0 and ask >= bid else None
        spread_percent = ((ask - bid) * 100 / mid) if mid and mid > 0 else None
        iv = _metric_decimal(snapshot.get("impliedVolatility"))
        volume = int(daily.get("v") or 0)
        liquid = bool(bid > 0 and ask > bid and spread_percent is not None and spread_percent <= 20 and volume >= 1)
        contracts.append(
            {
                "contract_symbol": str(contract_symbol),
                "expiration": parsed["expiration"].isoformat(),
                "days_to_expiration": dte,
                "right": parsed["right"],
                "strike": format(parsed["strike"].normalize(), "f"),
                "bid": format(bid.normalize(), "f"),
                "ask": format(ask.normalize(), "f"),
                "mid": format(mid.quantize(Decimal("0.0001")), "f") if mid is not None else None,
                "last": str(trade.get("p")) if trade.get("p") is not None else None,
                "spread_percent": _percent(spread_percent) if spread_percent is not None else None,
                "volume": volume,
                "open_interest": snapshot.get("openInterest"),
                "implied_volatility_percent": _percent(iv * 100) if iv is not None else None,
                "delta": greeks.get("delta"),
                "gamma": greeks.get("gamma"),
                "theta": greeks.get("theta"),
                "vega": greeks.get("vega"),
                "liquid": liquid,
            }
        )
    contracts.sort(key=lambda item: (item["expiration"], item["right"], Decimal(item["strike"])))
    atm_contracts = sorted(
        (item for item in contracts if item["implied_volatility_percent"] is not None),
        key=lambda item: (
            item["days_to_expiration"],
            abs(Decimal(item["strike"]) - underlying) if underlying is not None else Decimal(0),
        ),
    )
    atm_iv = Decimal(atm_contracts[0]["implied_volatility_percent"]) if atm_contracts else None
    iv_history = previous_iv + ([atm_iv] if atm_iv is not None else [])
    iv_percentile = None
    if atm_iv is not None and len(iv_history) >= 2:
        iv_percentile = Decimal(sum(value <= atm_iv for value in iv_history)) * 100 / Decimal(len(iv_history))
    expected_move = None
    expected_move_dte = None
    if underlying is not None and atm_iv is not None and atm_contracts:
        expected_move_dte = max(1, int(atm_contracts[0]["days_to_expiration"]))
        expected_move = underlying * (atm_iv / 100) * Decimal(str(math.sqrt(expected_move_dte / 365)))
    calendar = earnings_calendar(path, user_id)
    earnings_event = next(
        (item for item in calendar.get("events", []) if item.get("symbol") == symbol), None
    )
    candidates = _option_chain_candidates(contracts, underlying)
    if earnings_event:
        for candidate in candidates:
            candidate["earnings_risk"] = {
                "report_date": earnings_event["report_date"],
                "before_expiration": earnings_event["report_date"] <= candidate["expiration"],
                "note": "The estimated earnings date can change; re-check it before opening or holding through the event.",
            }
    result = {
        "available": bool(contracts),
        "configured": True,
        "symbol": symbol,
        "provider": "Alpaca Market Data",
        "configuration_source": source,
        "feed": "indicative",
        "underlying_price": format(underlying.normalize(), "f") if underlying is not None else None,
        "summary": {
            "contracts": len(contracts),
            "calls": sum(item["right"] == "call" for item in contracts),
            "puts": sum(item["right"] == "put" for item in contracts),
            "liquid_contracts": sum(item["liquid"] for item in contracts),
            "expirations": len({item["expiration"] for item in contracts}),
            "atm_iv_percent": _percent(atm_iv) if atm_iv is not None else None,
            "iv_percentile": _percent(iv_percentile) if iv_percentile is not None else None,
            "expected_move": format(expected_move.quantize(Decimal("0.0001")), "f") if expected_move is not None else None,
            "expected_move_days": expected_move_dte,
        },
        "quality": option_snapshot_quality(contracts),
        "contracts": contracts,
        "candidates": candidates,
        "analytics": _option_analytics(contracts, underlying, portfolio_state),
        "earnings_event": earnings_event,
        "pages_loaded": pages_loaded,
        "next_page_token": next_page_token,
        "fetched_at": now_iso(),
        "data_scope": "Alpaca indicative option snapshots, up to five 1,000-contract pages and 120 days. Liquidity requires bid/ask, <=20% quoted spread, and observed daily volume. No orders are routed.",
    }
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        snapshot_id = str(uuid4())
        db.execute(
            "INSERT INTO option_chain_snapshots(id, user_id, symbol, feed, underlying_price_micros, "
            "atm_iv_percent_micros, result_json, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot_id, user_id, symbol, "indicative",
                int(underlying * SCALE) if underlying is not None else None,
                int(atm_iv * SCALE) if atm_iv is not None else None,
                json.dumps(result), result["fetched_at"],
            ),
        )
        stale_ids = [
            row["id"] for row in db.execute(
                "SELECT id FROM option_chain_snapshots WHERE user_id = ? AND symbol = ? "
                "ORDER BY fetched_at DESC, id DESC LIMIT -1 OFFSET 100",
                (user_id, symbol),
            ).fetchall()
        ]
        if stale_ids:
            db.executemany("DELETE FROM option_chain_snapshots WHERE id = ?", [(item,) for item in stale_ids])
    return _apply_option_filters({**result, "cache_hit": False}, active_filters)


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _nasdaq_halts(path: Path) -> dict[str, dict[str, Any]]:
    with open_db(path) as db:
        cached = _sec_cached(db, "nasdaq-current-halts", timedelta(seconds=60))
        if cached is not None:
            return dict(cached.get("halts") or {})
    request = Request(
        "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts",
        headers={"User-Agent": "InvestorLab/0.6", "Accept": "application/rss+xml, application/xml"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read(2_000_001)
    except (HTTPError, URLError, TimeoutError) as error:
        reason = getattr(error, "reason", str(error))
        raise ApiError(502, f"Nasdaq halt feed request failed: {reason}.") from None
    if len(raw) > 2_000_000:
        raise ApiError(502, "Nasdaq halt feed returned an unexpectedly large response.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ApiError(502, "Nasdaq halt feed returned invalid XML.") from None
    halts: dict[str, dict[str, Any]] = {}
    for item in root.iter():
        if _xml_local_name(item.tag) != "item":
            continue
        fields = {
            _xml_local_name(child.tag): (child.text or "").strip()
            for child in item.iter() if child is not item
        }
        symbol = str(fields.get("issuesymbol") or fields.get("title") or "").strip().upper()
        if SYMBOL_RE.fullmatch(symbol):
            halts[symbol] = {
                "halted": True,
                "reason_code": fields.get("reasoncode") or None,
                "halt_date": fields.get("haltdate") or None,
                "halt_time": fields.get("halttime") or None,
                "resumption_date": fields.get("resumptiondate") or None,
                "resumption_quote_time": fields.get("resumptionquotetime") or None,
                "resumption_trade_time": fields.get("resumptiontradetime") or None,
                "source": "Nasdaq Trader current halt RSS",
            }
    with open_db(path) as db:
        _store_sec_cache(db, "nasdaq-current-halts", {"halts": halts})
    return halts


def _aggregate_intraday_bars(
    bars: list[dict[str, Any]], minutes: int = 5
) -> list[dict[str, Any]]:
    buckets: dict[datetime, list[dict[str, Any]]] = {}
    for bar in bars:
        timestamp = bar["timestamp"].replace(
            minute=(bar["timestamp"].minute // minutes) * minutes,
            second=0,
            microsecond=0,
        )
        buckets.setdefault(timestamp, []).append(bar)
    return [
        {
            "timestamp": timestamp,
            "open": items[0]["open"],
            "high": max(item["high"] for item in items),
            "low": min(item["low"] for item in items),
            "close": items[-1]["close"],
            "volume": sum(item["volume"] for item in items),
        }
        for timestamp, items in sorted(buckets.items())
    ]


def _store_intraday_bars(
    db: sqlite3.Connection, symbol: str, bars: list[dict[str, Any]]
) -> dict[str, int]:
    fetched_at = now_iso()
    counts = {}
    for timeframe, items in (("1Min", bars), ("5Min", _aggregate_intraday_bars(bars))):
        rows = [
            (
                symbol,
                item["timestamp"].astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                timeframe,
                int(item["open"] * SCALE),
                int(item["high"] * SCALE),
                int(item["low"] * SCALE),
                int(item["close"] * SCALE),
                int(item["volume"]),
                "alpaca_iex",
                fetched_at,
            )
            for item in items
        ]
        db.executemany(
            "INSERT INTO intraday_bars(symbol, bar_timestamp, timeframe, open_micros, high_micros, "
            "low_micros, close_micros, volume, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol, bar_timestamp, timeframe, source) DO UPDATE SET "
            "open_micros=excluded.open_micros, high_micros=excluded.high_micros, low_micros=excluded.low_micros, "
            "close_micros=excluded.close_micros, volume=excluded.volume, fetched_at=excluded.fetched_at",
            rows,
        )
        counts[timeframe] = len(rows)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds").replace("+00:00", "Z")
    db.execute("DELETE FROM intraday_bars WHERE symbol = ? AND bar_timestamp < ?", (symbol, cutoff))
    return counts


def _day_trade_setups(
    price: Decimal | None,
    vwap: Decimal | None,
    opening_high: Decimal | None,
    opening_low: Decimal | None,
    premarket_high: Decimal | None,
    premarket_low: Decimal | None,
    relative_volume: Decimal | None,
    spread_percent: Decimal | None,
    session_volume: int,
    session_phase: str,
    halt: dict[str, Any],
    guardrails: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    blocked_reasons = []
    if halt.get("halted"):
        blocked_reasons.append("Nasdaq halt is active.")
    if guardrails and guardrails.get("stop_triggered"):
        blocked_reasons.extend(guardrails.get("stop_conditions") or ["Saved daily stop is active."])
    if session_phase != "regular":
        blocked_reasons.append("The regular session is not open.")
    if spread_percent is not None and spread_percent > Decimal("0.50"):
        blocked_reasons.append("Observed IEX spread exceeds 0.50%.")
    if relative_volume is not None and relative_volume < Decimal("0.50"):
        blocked_reasons.append("Observed relative volume is below 0.50x.")
    if session_volume < 1_000:
        blocked_reasons.append("Observed IEX session volume is below 1,000 shares.")

    def setup(
        key: str,
        label: str,
        direction: str | None,
        entry: Decimal | None,
        stop: Decimal | None,
        score: int,
        evidence: list[str],
    ) -> dict[str, Any]:
        valid = direction is not None and entry is not None and stop is not None and entry != stop
        risk = abs(entry - stop) if valid else None
        target = (
            entry + risk * 2 if valid and direction == "long" else
            entry - risk * 2 if valid else None
        )
        return {
            "key": key,
            "label": label,
            "status": "blocked" if blocked_reasons else "ready" if valid and score >= 70 else "watch",
            "direction": direction,
            "score": score,
            "entry": format(entry.quantize(Decimal("0.0001")), "f") if entry is not None else None,
            "stop": format(stop.quantize(Decimal("0.0001")), "f") if stop is not None else None,
            "target": format(target.quantize(Decimal("0.0001")), "f") if target is not None else None,
            "reward_risk": "2.00" if target is not None else None,
            "evidence": evidence,
            "blocked_reasons": blocked_reasons,
        }

    rvol_bonus = 15 if relative_volume is not None and relative_volume >= Decimal("1.5") else 5 if relative_volume is not None and relative_volume >= 1 else 0
    orb_direction = (
        "long" if price is not None and opening_high is not None and price > opening_high else
        "short" if price is not None and opening_low is not None and price < opening_low else None
    )
    orb_entry = opening_high if orb_direction == "long" else opening_low if orb_direction == "short" else price
    orb_stop = opening_low if orb_direction == "long" else opening_high if orb_direction == "short" else None
    vwap_direction = "long" if price is not None and vwap is not None and price >= vwap else "short" if price is not None and vwap is not None else None
    vwap_risk = max(price * Decimal("0.005"), abs((opening_high or price) - (opening_low or price))) if price is not None else None
    vwap_stop = price - vwap_risk if price is not None and vwap_risk is not None and vwap_direction == "long" else price + vwap_risk if price is not None and vwap_risk is not None and vwap_direction == "short" else None
    momentum_direction = (
        "long" if price is not None and premarket_high is not None and price > premarket_high else
        "short" if price is not None and premarket_low is not None and price < premarket_low else None
    )
    momentum_stop = premarket_high if momentum_direction == "long" else premarket_low if momentum_direction == "short" else None
    return [
        setup(
            "opening_range_breakout", "Opening range breakout", orb_direction, orb_entry,
            orb_stop, (75 if orb_direction else 45) + rvol_bonus,
            ["Price must close beyond the first five-minute range.", "Higher relative volume strengthens confirmation."],
        ),
        setup(
            "vwap_pullback", "VWAP pullback", vwap_direction, price, vwap_stop,
            (65 if vwap_direction else 35) + rvol_bonus,
            ["Direction follows the observed side of session VWAP.", "Stop distance uses the larger of 0.5% or the opening-range width."],
        ),
        setup(
            "premarket_momentum", "Premarket momentum continuation", momentum_direction, price,
            momentum_stop, (75 if momentum_direction else 40) + rvol_bonus,
            ["Price must clear the observed premarket high or low.", "The broken premarket level becomes the invalidation reference."],
        ),
    ]


def _day_trade_replay(bars: list[dict[str, Any]], current_date: date) -> dict[str, Any]:
    sessions: dict[date, list[dict[str, Any]]] = {}
    for bar in bars:
        session_date = bar["timestamp"].date()
        if session_date >= current_date:
            continue
        if (9, 30) <= (bar["timestamp"].hour, bar["timestamp"].minute) < (16, 0):
            sessions.setdefault(session_date, []).append(bar)
    outcomes = []
    for session_date, regular in sorted(sessions.items()):
        opening = [
            bar for bar in regular
            if (bar["timestamp"].hour, bar["timestamp"].minute) < (9, 35)
        ]
        if not opening:
            continue
        high = max(item["high"] for item in opening)
        low = min(item["low"] for item in opening)
        trigger_index = None
        direction = None
        entry = None
        for index, bar in enumerate(regular):
            if (bar["timestamp"].hour, bar["timestamp"].minute) < (9, 35):
                continue
            if bar["close"] > high:
                trigger_index, direction, entry = index, "long", bar["close"]
                break
            if bar["close"] < low:
                trigger_index, direction, entry = index, "short", bar["close"]
                break
        if trigger_index is None or direction is None or entry is None:
            outcomes.append({"session_date": session_date.isoformat(), "outcome": "no_trigger"})
            continue
        stop = low if direction == "long" else high
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        target = entry + risk * 2 if direction == "long" else entry - risk * 2
        outcome = "open_to_close"
        exit_price = regular[-1]["close"]
        for bar in regular[trigger_index + 1:]:
            target_hit = bar["high"] >= target if direction == "long" else bar["low"] <= target
            stop_hit = bar["low"] <= stop if direction == "long" else bar["high"] >= stop
            if target_hit and stop_hit:
                outcome, exit_price = "ambiguous", bar["close"]
                break
            if target_hit:
                outcome, exit_price = "target", target
                break
            if stop_hit:
                outcome, exit_price = "stop", stop
                break
        multiple = ((exit_price - entry) / risk) * (1 if direction == "long" else -1)
        outcomes.append(
            {
                "session_date": session_date.isoformat(), "direction": direction,
                "entry": format(entry.normalize(), "f"), "stop": format(stop.normalize(), "f"),
                "target": format(target.normalize(), "f"), "outcome": outcome,
                "realized_r_multiple": _percent(multiple),
            }
        )
    triggered = [item for item in outcomes if item["outcome"] != "no_trigger"]
    decisive = [item for item in triggered if item["outcome"] in {"target", "stop"}]
    return {
        "available": bool(outcomes),
        "sessions": len(outcomes),
        "triggered_sessions": len(triggered),
        "target_hits": sum(item["outcome"] == "target" for item in outcomes),
        "stop_hits": sum(item["outcome"] == "stop" for item in outcomes),
        "target_hit_rate_percent": (
            _percent(Decimal(sum(item["outcome"] == "target" for item in decisive)) * 100 / Decimal(len(decisive)))
            if decisive else None
        ),
        "average_r_multiple": (
            _percent(sum(Decimal(item["realized_r_multiple"]) for item in triggered) / Decimal(len(triggered)))
            if triggered else None
        ),
        "outcomes": list(reversed(outcomes[-10:])),
        "reason": None if outcomes else "At least one prior session of minute bars is required.",
        "scope": "Replays one deterministic 09:30-09:35 opening-range breakout per cached prior session; same-bar target and stop hits are ambiguous.",
    }


def day_trade_session_replay(
    path: Path, raw_symbol: Any, session_date_text: str | None = None
) -> dict[str, Any]:
    symbol = normalize_symbol(raw_symbol)
    selected: date | None = None
    if session_date_text:
        try:
            selected = date.fromisoformat(session_date_text)
        except ValueError:
            raise InputError("Replay date must be YYYY-MM-DD.") from None
    with open_db(path) as db:
        rows = db.execute(
            "SELECT bar_timestamp, open_micros, high_micros, low_micros, close_micros, volume "
            "FROM intraday_bars WHERE symbol = ? AND timeframe = '1Min' "
            "ORDER BY bar_timestamp",
            (symbol,),
        ).fetchall()
    parsed = []
    for row in rows:
        timestamp = datetime.fromisoformat(str(row["bar_timestamp"]).replace("Z", "+00:00")).astimezone(NEW_YORK)
        if (9, 30) <= (timestamp.hour, timestamp.minute) < (16, 0):
            parsed.append({
                "timestamp": timestamp,
                "open": Decimal(int(row["open_micros"])) / SCALE,
                "high": Decimal(int(row["high_micros"])) / SCALE,
                "low": Decimal(int(row["low_micros"])) / SCALE,
                "close": Decimal(int(row["close_micros"])) / SCALE,
                "volume": int(row["volume"]),
            })
    available_dates = sorted({item["timestamp"].date() for item in parsed}, reverse=True)
    if selected is None:
        selected = available_dates[0] if available_dates else None
    session = [item for item in parsed if selected and item["timestamp"].date() == selected]
    if not selected or not session:
        return {
            "available": False, "symbol": symbol,
            "available_dates": [item.isoformat() for item in available_dates[:30]],
            "reason": "No complete cached regular-session minute bars are available for that date.",
        }
    opening = [
        bar for bar in session
        if (bar["timestamp"].hour, bar["timestamp"].minute) < (9, 35)
    ]
    if not opening:
        return {
            "available": False, "symbol": symbol,
            "available_dates": [item.isoformat() for item in available_dates[:30]],
            "reason": "No cached 09:30-09:35 opening-range bars are available for that date.",
        }
    opening_high = max(item["high"] for item in opening)
    opening_low = min(item["low"] for item in opening)
    trigger_index = None
    direction = None
    entry = None
    for index, bar in enumerate(session):
        if (bar["timestamp"].hour, bar["timestamp"].minute) < (9, 35):
            continue
        if bar["close"] > opening_high:
            trigger_index, direction, entry = index, "long", bar["close"]
            break
        if bar["close"] < opening_low:
            trigger_index, direction, entry = index, "short", bar["close"]
            break
    stop = target = exit_price = None
    outcome = "no_trigger"
    exit_index = None
    if trigger_index is not None and direction and entry is not None:
        stop = opening_low if direction == "long" else opening_high
        risk = abs(entry - stop)
        target = entry + risk * 2 if direction == "long" else entry - risk * 2
        outcome = "open_to_close"
        exit_price = session[-1]["close"]
        exit_index = len(session) - 1
        for index, bar in enumerate(session[trigger_index + 1:], start=trigger_index + 1):
            target_hit = bar["high"] >= target if direction == "long" else bar["low"] <= target
            stop_hit = bar["low"] <= stop if direction == "long" else bar["high"] >= stop
            if target_hit and stop_hit:
                outcome, exit_price, exit_index = "ambiguous", bar["close"], index
                break
            if target_hit:
                outcome, exit_price, exit_index = "target", target, index
                break
            if stop_hit:
                outcome, exit_price, exit_index = "stop", stop, index
                break
    realized_r = None
    mae_r = None
    mfe_r = None
    if entry is not None and stop is not None and exit_price is not None:
        risk = abs(entry - stop)
        realized_r = ((exit_price - entry) / risk) * (1 if direction == "long" else -1)
        observed = session[trigger_index : (exit_index + 1 if exit_index is not None else len(session))]
        if observed and risk > 0:
            if direction == "long":
                mae_r = (min(item["low"] for item in observed) - entry) / risk
                mfe_r = (max(item["high"] for item in observed) - entry) / risk
            else:
                mae_r = (entry - max(item["high"] for item in observed)) / risk
                mfe_r = (entry - min(item["low"] for item in observed)) / risk

    def timeframe_bars(minutes: int) -> list[dict[str, Any]]:
        buckets: dict[datetime, list[dict[str, Any]]] = {}
        for bar in session:
            bucket_start = bar["timestamp"].replace(
                minute=(bar["timestamp"].minute // minutes) * minutes,
                second=0,
                microsecond=0,
            )
            buckets.setdefault(bucket_start, []).append(bar)
        grouped = []
        for bucket_start in sorted(buckets):
            bucket = buckets[bucket_start]
            grouped.append({
                "timestamp": bucket_start.isoformat(timespec="seconds"),
                "open": format(bucket[0]["open"].normalize(), "f"),
                "high": format(max(item["high"] for item in bucket).normalize(), "f"),
                "low": format(min(item["low"] for item in bucket).normalize(), "f"),
                "close": format(bucket[-1]["close"].normalize(), "f"),
                "volume": sum(item["volume"] for item in bucket),
            })
        return grouped

    cumulative_value = Decimal(0)
    cumulative_volume = 0
    vwap_points = []
    for item in session:
        typical = (item["high"] + item["low"] + item["close"]) / 3
        cumulative_value += typical * item["volume"]
        cumulative_volume += item["volume"]
        vwap_points.append({
            "timestamp": item["timestamp"].isoformat(timespec="seconds"),
            "vwap": format((cumulative_value / max(cumulative_volume, 1)).quantize(Decimal("0.0001")), "f"),
        })
    return {
        "available": True,
        "symbol": symbol,
        "session_date": selected.isoformat(),
        "available_dates": [item.isoformat() for item in available_dates[:30]],
        "opening_range_high": format(opening_high.normalize(), "f"),
        "opening_range_low": format(opening_low.normalize(), "f"),
        "direction": direction,
        "entry": format(entry.normalize(), "f") if entry is not None else None,
        "stop": format(stop.normalize(), "f") if stop is not None else None,
        "target": format(target.normalize(), "f") if target is not None else None,
        "outcome": outcome,
        "realized_r_multiple": _percent(realized_r) if realized_r is not None else None,
        "maximum_adverse_excursion_r": _percent(mae_r) if mae_r is not None else None,
        "maximum_favorable_excursion_r": _percent(mfe_r) if mfe_r is not None else None,
        "trigger_index": trigger_index,
        "exit_index": exit_index,
        "bars": [
            {
                "timestamp": item["timestamp"].isoformat(timespec="seconds"),
                "open": format(item["open"].normalize(), "f"),
                "high": format(item["high"].normalize(), "f"),
                "low": format(item["low"].normalize(), "f"),
                "close": format(item["close"].normalize(), "f"),
                "volume": item["volume"],
            }
            for item in session
        ],
        "timeframes": {"1m": timeframe_bars(1), "5m": timeframe_bars(5), "15m": timeframe_bars(15)},
        "vwap": vwap_points,
        "scope": "Minute-by-minute playback with 1/5/15-minute candles, VWAP, opening range, and setup MAE/MFE using cached IEX bars.",
    }


def realtime_day_trade_plan(
    path: Path,
    raw_symbol: Any,
    user_id: str | None = None,
    clock_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    symbol = normalize_symbol(raw_symbol)
    key_id, secret, source = _alpaca_credentials()
    if not key_id or not secret:
        return {
            "available": False, "symbol": symbol, "configured": False,
            "provider": "Alpaca Market Data", "feed": "iex",
            "reason": "Save personal Alpaca Market Data credentials to load IEX real-time prices and intraday levels.",
            "halt": {
                "halted": False,
                "reason_code": None,
                "source": "Nasdaq Trader current halt RSS",
            },
            "data_scope": "IEX real-time plan is not configured.",
        }
    cache_key = f"alpaca-live-plan:{user_id or 'shared'}:{symbol}"
    with open_db(path) as db:
        cached = _sec_cached(db, cache_key, timedelta(seconds=20))
        if cached is not None:
            return {**cached, "cache_hit": True}
    snapshot_payload = _alpaca_json(
        f"/v2/stocks/{symbol}/snapshot", {"feed": "iex"}, key_id, secret
    )
    now_ny = datetime.now(timezone.utc).astimezone(NEW_YORK)
    history_start = datetime.combine(
        now_ny.date() - timedelta(days=10), time(4, 0), tzinfo=NEW_YORK
    ).astimezone(timezone.utc)
    start = history_start.isoformat(timespec="seconds").replace("+00:00", "Z")
    bars_payload = _alpaca_json(
        f"/v2/stocks/{symbol}/bars",
        {"timeframe": "1Min", "start": start, "limit": 10000, "adjustment": "raw", "feed": "iex", "sort": "asc"},
        key_id, secret,
    )
    bars = bars_payload.get("bars")
    if not isinstance(bars, list):
        bars = []
    parsed = []
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        try:
            timestamp = datetime.fromisoformat(str(bar["t"]).replace("Z", "+00:00")).astimezone(NEW_YORK)
            parsed.append(
                {
                    "timestamp": timestamp, "open": Decimal(str(bar["o"])),
                    "high": Decimal(str(bar["h"])), "low": Decimal(str(bar["l"])),
                    "close": Decimal(str(bar["c"])), "volume": int(bar["v"]),
                }
            )
        except (KeyError, InvalidOperation, TypeError, ValueError):
            continue
    session_date = max((bar["timestamp"].date() for bar in parsed), default=now_ny.date())
    session = [bar for bar in parsed if bar["timestamp"].date() == session_date]
    premarket = [bar for bar in session if (4, 0) <= (bar["timestamp"].hour, bar["timestamp"].minute) < (9, 30)]
    regular = [bar for bar in session if (9, 30) <= (bar["timestamp"].hour, bar["timestamp"].minute) < (16, 0)]
    coverage = intraday_coverage(
        (bar["timestamp"] for bar in regular),
        session_date=session_date,
        as_of=now_ny,
    )
    opening_range = [bar for bar in regular if (bar["timestamp"].hour, bar["timestamp"].minute) < (9, 35)]
    total_volume = sum(bar["volume"] for bar in regular)
    vwap_numerator = sum(
        ((bar["high"] + bar["low"] + bar["close"]) / 3) * bar["volume"] for bar in regular
    )
    latest_regular_time = max(
        ((bar["timestamp"].hour, bar["timestamp"].minute) for bar in regular),
        default=(9, 30),
    )
    prior_sessions: dict[date, int] = {}
    for bar in parsed:
        bar_time = (bar["timestamp"].hour, bar["timestamp"].minute)
        if (
            bar["timestamp"].date() != session_date
            and (9, 30) <= bar_time < (16, 0)
            and bar_time <= latest_regular_time
        ):
            prior_sessions[bar["timestamp"].date()] = prior_sessions.get(bar["timestamp"].date(), 0) + bar["volume"]
    average_prior_volume = (
        Decimal(sum(prior_sessions.values())) / Decimal(len(prior_sessions))
        if prior_sessions else None
    )
    snap = snapshot_payload
    latest_trade = snap.get("latestTrade") if isinstance(snap.get("latestTrade"), dict) else {}
    latest_quote = snap.get("latestQuote") if isinstance(snap.get("latestQuote"), dict) else {}
    daily_bar = snap.get("dailyBar") if isinstance(snap.get("dailyBar"), dict) else {}
    previous_bar = snap.get("prevDailyBar") if isinstance(snap.get("prevDailyBar"), dict) else {}
    price = _metric_decimal(latest_trade.get("p")) or _metric_decimal(daily_bar.get("c"))
    bid = _metric_decimal(latest_quote.get("bp"))
    ask = _metric_decimal(latest_quote.get("ap"))
    halt = _nasdaq_halts(path).get(symbol, {"halted": False, "source": "Nasdaq Trader current halt RSS"})
    with open_db(path) as db:
        stored_bars = _store_intraday_bars(db, symbol, parsed)
        guardrails = _day_trade_guardrails_from_db(db, user_id) if user_id else None

    vwap_value = vwap_numerator / total_volume if total_volume else None
    opening_high_value = max((item["high"] for item in opening_range), default=None)
    opening_low_value = min((item["low"] for item in opening_range), default=None)
    premarket_high_value = max((item["high"] for item in premarket), default=None)
    premarket_low_value = min((item["low"] for item in premarket), default=None)
    relative_volume_value = Decimal(total_volume) / average_prior_volume if average_prior_volume else None
    quote_mid = (bid + ask) / 2 if bid is not None and ask is not None and ask >= bid else None
    spread_percent_value = (
        (ask - bid) * 100 / quote_mid if quote_mid is not None and quote_mid > 0 else None
    )
    exchange_clock = clock_data or market_clock(path)
    session_phase = str(exchange_clock.get("session_phase") or "closed")
    setups = _day_trade_setups(
        price, vwap_value, opening_high_value, opening_low_value,
        premarket_high_value, premarket_low_value, relative_volume_value,
        spread_percent_value, total_volume, session_phase,
        halt, guardrails,
    )
    replay = _day_trade_replay(parsed, session_date)

    def level(values: list[dict[str, Any]], key: str, operation: Any) -> str | None:
        return format(operation(item[key] for item in values).quantize(Decimal("0.0001")), "f") if values else None

    support_values = [value for value in (_metric_decimal(previous_bar.get("l")), _metric_decimal(daily_bar.get("l"))) if value is not None]
    resistance_values = [value for value in (_metric_decimal(previous_bar.get("h")), _metric_decimal(daily_bar.get("h"))) if value is not None]
    result = {
        "available": price is not None,
        "configured": True,
        "symbol": symbol,
        "provider": "Alpaca Market Data",
        "configuration_source": source,
        "feed": "iex",
        "session_date": session_date.isoformat(),
        "latest_price": format(price.normalize(), "f") if price is not None else None,
        "bid": format(bid.normalize(), "f") if bid is not None else None,
        "ask": format(ask.normalize(), "f") if ask is not None else None,
        "spread": format((ask - bid).quantize(Decimal("0.0001")), "f") if ask is not None and bid is not None else None,
        "spread_percent": _percent(spread_percent_value) if spread_percent_value is not None else None,
        "session_phase": session_phase,
        "latest_trade_at": latest_trade.get("t"),
        "premarket_high": level(premarket, "high", max),
        "premarket_low": level(premarket, "low", min),
        "vwap": format((vwap_numerator / total_volume).quantize(Decimal("0.0001")), "f") if total_volume else None,
        "opening_range_high": level(opening_range, "high", max),
        "opening_range_low": level(opening_range, "low", min),
        "support": format(min(support_values).quantize(Decimal("0.0001")), "f") if support_values else None,
        "resistance": format(max(resistance_values).quantize(Decimal("0.0001")), "f") if resistance_values else None,
        "session_volume": total_volume,
        "session_volume_scope": "regular_session",
        "relative_volume": (
            format(relative_volume_value.quantize(Decimal("0.01")), "f")
            if relative_volume_value is not None else None
        ),
        "gap_percent": (
            _percent((price / _metric_decimal(previous_bar.get("c")) - 1) * 100)
            if price is not None and _metric_decimal(previous_bar.get("c")) else None
        ),
        "halt": halt,
        "risk_gate": {
            "blocked": any(item["status"] == "blocked" for item in setups),
            "reasons": sorted({reason for item in setups for reason in item["blocked_reasons"]}),
        },
        "setups": setups,
        "replay": replay,
        "stored_bars": stored_bars,
        "fetched_at": now_iso(),
        "data_quality": {
            **coverage,
            "source": "alpaca_iex",
            "latest_observation_at": latest_trade.get("t"),
        },
        "data_scope": "IEX-only real-time observations on Alpaca Basic. VWAP, volume, and relative volume use regular-session bars through the latest observed minute and may differ from consolidated SIP data.",
        "cache_seconds": 20,
    }
    with open_db(path) as db:
        _store_sec_cache(db, cache_key, result)
    return {**result, "cache_hit": False}


def day_trade_scanner(path: Path, user_id: str, limit: int = 12) -> dict[str, Any]:
    limit = max(1, min(limit, 30))
    with open_db(path) as db:
        symbols = [
            str(row["symbol"]) for row in db.execute(
                "SELECT symbol FROM watchlist WHERE user_id = ? ORDER BY created_at LIMIT ?",
                (user_id, limit),
            ).fetchall()
        ]
    clock = market_clock(path)
    rows = []
    errors = []
    for symbol in symbols:
        try:
            plan = realtime_day_trade_plan(path, symbol, user_id, clock)
        except (ApiError, InputError) as error:
            errors.append({"symbol": symbol, "error": str(error)})
            continue
        if not plan.get("available"):
            errors.append({"symbol": symbol, "error": str(plan.get("reason") or "Live data unavailable.")})
            continue
        setups = list(plan.get("setups") or [])
        best = max(setups, key=lambda item: int(item.get("score") or 0), default=None)
        ready = [item for item in setups if item.get("status") == "ready"]
        gap = _metric_decimal(plan.get("gap_percent")) or Decimal(0)
        rvol = _metric_decimal(plan.get("relative_volume")) or Decimal(0)
        spread = _metric_decimal(plan.get("spread_percent"))
        rank_score = Decimal(len(ready) * 100 + (int(best.get("score") or 0) if best else 0)) + min(abs(gap), Decimal(20)) * 2 + min(rvol, Decimal(10)) * 5
        if spread is not None:
            rank_score -= min(spread, Decimal(10)) * 10
        rows.append({
            "symbol": symbol,
            "latest_price": plan.get("latest_price"),
            "gap_percent": plan.get("gap_percent"),
            "relative_volume": plan.get("relative_volume"),
            "spread_percent": plan.get("spread_percent"),
            "session_volume": plan.get("session_volume"),
            "halted": bool((plan.get("halt") or {}).get("halted")),
            "risk_gate": plan.get("risk_gate"),
            "best_setup": best,
            "ready_setup_count": len(ready),
            "rank_score": _percent(rank_score),
            "fetched_at": plan.get("fetched_at"),
        })
    rows.sort(key=lambda item: Decimal(item["rank_score"]), reverse=True)
    alert_candidates = [
        {
            "key": f"{item['symbol']}:{item['best_setup']['key']}",
            "symbol": item["symbol"],
            "setup": item["best_setup"],
            "message": f"{item['symbol']} {item['best_setup']['label']} is ready at the saved risk gate.",
        }
        for item in rows
        if item["ready_setup_count"] and item.get("best_setup")
    ]
    new_alerts = []
    with open_db(path) as db:
        current = now_iso()
        for item in rows:
            setup = item.get("best_setup")
            if not setup:
                continue
            alert_key = f"{item['symbol']}:{setup['key']}"
            state = "ready" if setup.get("status") == "ready" else "blocked" if setup.get("status") == "blocked" else "waiting"
            previous = db.execute(
                "SELECT state FROM day_trade_alert_states WHERE user_id = ? AND alert_key = ?",
                (user_id, alert_key),
            ).fetchone()
            if state == "ready" and (not previous or previous["state"] != "ready"):
                candidate = next((candidate for candidate in alert_candidates if candidate["key"] == alert_key), None)
                if candidate:
                    new_alerts.append(candidate)
            db.execute(
                "INSERT INTO day_trade_alert_states(user_id, alert_key, state, last_notified_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id, alert_key) DO UPDATE SET "
                "state=excluded.state, last_notified_at=CASE WHEN excluded.state='ready' AND day_trade_alert_states.state!='ready' "
                "THEN excluded.last_notified_at ELSE day_trade_alert_states.last_notified_at END, updated_at=excluded.updated_at",
                (user_id, alert_key, state, current if state == "ready" else None, current),
            )
    return {
        "generated_at": now_iso(),
        "market_clock": clock,
        "symbols_requested": len(symbols),
        "symbols_available": len(rows),
        "rows": rows,
        "alert_candidates": alert_candidates,
        "new_alert_candidates": new_alerts,
        "errors": errors,
        "scope": "Ranks the synchronized watchlist from observed IEX data and deterministic setup rules. A ready state is a paper-planning alert, not an order signal.",
    }


def _alpha_vantage_daily(symbol: str, api_key: str) -> list[tuple[Any, ...]]:
    output_size = os.environ.get("INVESTORLAB_MARKET_HISTORY", "compact").strip().lower()
    if output_size not in {"compact", "full"}:
        raise ApiError(500, "INVESTORLAB_MARKET_HISTORY must be compact or full.")
    url = "https://www.alphavantage.co/query?" + urlencode(
        {
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": output_size,
            "apikey": api_key,
        }
    )
    try:
        request = Request(url, headers={"User-Agent": "InvestorLab/0.3"})
        with urlopen(request, timeout=15) as response:
            raw = response.read(5_000_001)
    except (HTTPError, URLError, TimeoutError) as error:
        reason = getattr(error, "reason", str(error))
        raise ApiError(502, f"Market data request failed: {reason}.") from None
    if len(raw) > 5_000_000:
        raise ApiError(502, "Market data response was unexpectedly large.")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(502, "Market data provider returned invalid JSON.") from None
    provider_message = payload.get("Error Message") or payload.get("Note") or payload.get("Information")
    if provider_message:
        status = 429 if payload.get("Note") or payload.get("Information") else 422
        raise ApiError(status, str(provider_message)[:500])
    series = payload.get("Time Series (Daily)")
    if not isinstance(series, dict) or not series:
        raise ApiError(502, "Market data provider returned no daily bars.")

    fetched_at = now_iso()
    rows = []
    for trading_date, values in series.items():
        if not isinstance(values, dict):
            raise ApiError(502, "Market data provider returned a malformed daily bar.")
        try:
            datetime.strptime(trading_date, "%Y-%m-%d")
            volume = int(values["5. volume"])
            open_micros = to_micros(values["1. open"], "Open")
            high_micros = to_micros(values["2. high"], "High")
            low_micros = to_micros(values["3. low"], "Low")
            close_micros = to_micros(values["4. close"], "Close")
            if low_micros > min(open_micros, close_micros) or high_micros < max(
                open_micros, close_micros
            ):
                raise ValueError
            row = (
                symbol,
                trading_date,
                open_micros,
                high_micros,
                low_micros,
                close_micros,
                volume,
                "alpha_vantage",
                fetched_at,
            )
        except (KeyError, TypeError, ValueError, InputError):
            raise ApiError(502, "Market data provider returned a malformed daily bar.") from None
        if volume < 0:
            raise ApiError(502, "Market data provider returned invalid volume.")
        rows.append(row)
    return rows


def _alpha_vantage_adjustments(symbol: str, api_key: str) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    output_size = os.environ.get("INVESTORLAB_MARKET_HISTORY", "compact").strip().lower()
    if output_size not in {"compact", "full"}:
        raise ApiError(500, "INVESTORLAB_MARKET_HISTORY must be compact or full.")
    url = "https://www.alphavantage.co/query?" + urlencode(
        {
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": symbol,
            "outputsize": output_size,
            "apikey": api_key,
        }
    )
    try:
        request = Request(url, headers={"User-Agent": "InvestorLab/0.7"})
        with urlopen(request, timeout=20) as response:
            raw = response.read(12_000_001)
    except (HTTPError, URLError, TimeoutError) as error:
        reason = getattr(error, "reason", str(error))
        raise ApiError(502, f"Adjusted market data request failed: {reason}.") from None
    if len(raw) > 12_000_000:
        raise ApiError(502, "Adjusted market data response was unexpectedly large.")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(502, "Adjusted market data provider returned invalid JSON.") from None
    provider_message = payload.get("Error Message") or payload.get("Note") or payload.get("Information")
    if provider_message:
        status = 429 if payload.get("Note") or payload.get("Information") else 422
        raise ApiError(status, str(provider_message)[:500])
    series = payload.get("Time Series (Daily)")
    if not isinstance(series, dict) or not series:
        raise ApiError(502, "Adjusted market data provider returned no daily bars.")
    fetched_at = now_iso()
    market_rows: list[tuple[Any, ...]] = []
    adjustment_rows: list[tuple[Any, ...]] = []
    for trading_date, values in series.items():
        if not isinstance(values, dict):
            raise ApiError(502, "Adjusted market data provider returned a malformed daily bar.")
        try:
            datetime.strptime(trading_date, "%Y-%m-%d")
            open_micros = to_micros(values["1. open"], "Open")
            high_micros = to_micros(values["2. high"], "High")
            low_micros = to_micros(values["3. low"], "Low")
            close_micros = to_micros(values["4. close"], "Close")
            adjusted_close_micros = to_micros(values["5. adjusted close"], "Adjusted close")
            volume = int(values["6. volume"])
            dividend_micros = to_nonnegative_micros(values.get("7. dividend amount", "0"), "Dividend")
            split_micros = to_micros(values.get("8. split coefficient", "1"), "Split coefficient")
            if close_micros <= 0 or volume < 0 or low_micros > min(open_micros, close_micros) or high_micros < max(open_micros, close_micros):
                raise ValueError
            adjustment_factor = Decimal(adjusted_close_micros) / Decimal(close_micros)
            adjusted_open_micros = max(1, round(Decimal(open_micros) * adjustment_factor))
            adjusted_high_micros = max(1, round(Decimal(high_micros) * adjustment_factor))
            adjusted_low_micros = max(1, round(Decimal(low_micros) * adjustment_factor))
        except (KeyError, TypeError, ValueError, InputError):
            raise ApiError(502, "Adjusted market data provider returned a malformed daily bar.") from None
        market_rows.append(
            (
                symbol, trading_date, adjusted_open_micros, adjusted_high_micros,
                adjusted_low_micros, adjusted_close_micros, volume, "alpha_vantage", fetched_at,
            )
        )
        adjustment_rows.append((symbol, trading_date, adjusted_close_micros, dividend_micros, split_micros, "alpha_vantage", fetched_at))
    return market_rows, adjustment_rows


def search_securities(path: Path, raw_query: Any, contact_email: str) -> dict[str, Any]:
    query = str(raw_query or "").strip()
    if not 1 <= len(query) <= 80:
        raise InputError("Search query must be 1-80 characters.")
    query_upper = query.upper()
    query_lower = query.lower()
    with open_db(path) as db:
        tickers = _sec_cached(db, "company-tickers", timedelta(days=7))
        if tickers is None:
            tickers = _sec_json(
                "https://www.sec.gov/files/company_tickers.json", contact_email, 5_000_000
            )
            _store_sec_cache(db, "company-tickers", tickers)
    results = []
    for item in tickers.values():
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("ticker") or "").upper()
        name = str(item.get("title") or "").strip()
        if not symbol or not name:
            continue
        if symbol == query_upper:
            rank, match = 0, "exact symbol"
        elif symbol.startswith(query_upper):
            rank, match = 1, "symbol prefix"
        elif name.lower().startswith(query_lower):
            rank, match = 2, "company prefix"
        elif query_lower in name.lower():
            rank, match = 3, "company name"
        else:
            continue
        results.append({
            "symbol": symbol,
            "name": name,
            "cik": f"{int(item.get('cik_str')):010d}",
            "match": match,
            "provider": "SEC EDGAR",
            "_rank": rank,
        })
    results.sort(key=lambda item: (item["_rank"], len(item["symbol"]), item["symbol"]))
    for item in results:
        item.pop("_rank", None)
    return {"query": query, "results": results[:12], "provider": "SEC EDGAR"}


def _alpha_vantage_earnings_calendar(api_key: str) -> list[dict[str, Any]]:
    url = "https://www.alphavantage.co/query?" + urlencode(
        {"function": "EARNINGS_CALENDAR", "horizon": "3month", "apikey": api_key}
    )
    try:
        request = Request(url, headers={"User-Agent": "InvestorLab/0.4"})
        with urlopen(request, timeout=25) as response:
            raw = response.read(12_000_001)
    except (HTTPError, URLError, TimeoutError) as error:
        reason = getattr(error, "reason", str(error))
        raise ApiError(502, f"Earnings calendar request failed: {reason}.") from None
    if len(raw) > 12_000_000:
        raise ApiError(502, "Earnings calendar response was unexpectedly large.")
    text = raw.decode("utf-8", errors="replace").strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            raise ApiError(502, "Earnings calendar provider returned invalid data.") from None
        message = payload.get("Error Message") or payload.get("Note") or payload.get("Information")
        raise ApiError(429 if message else 502, str(message or "Earnings calendar is unavailable.")[:500])
    events = []
    today = date.today()
    for row in csv.DictReader(io.StringIO(text)):
        symbol = str(row.get("symbol") or "").strip().upper()
        report_date = str(row.get("reportDate") or "").strip()
        if not SYMBOL_RE.fullmatch(symbol):
            continue
        try:
            event_date = date.fromisoformat(report_date)
        except ValueError:
            continue
        events.append({
            "symbol": symbol,
            "name": str(row.get("name") or symbol).strip(),
            "report_date": report_date,
            "fiscal_date_ending": str(row.get("fiscalDateEnding") or "").strip() or None,
            "estimate": str(row.get("estimate") or "").strip() or None,
            "currency": str(row.get("currency") or "").strip() or None,
            "days_until": (event_date - today).days,
        })
    events.sort(key=lambda item: (item["report_date"], item["symbol"]))
    return events


def _earnings_calendar_for_user(
    db: sqlite3.Connection, user_id: str, payload: dict[str, Any] | None
) -> dict[str, Any]:
    symbols = {item["symbol"] for item in _watchlist_rows(db, user_id)}
    if payload is None:
        return {
            "available": False,
            "events": [],
            "reason": "Refresh the earnings calendar after adding watchlist symbols.",
            "provider": "Alpha Vantage",
        }
    events = [item for item in payload.get("events", []) if item.get("symbol") in symbols]
    return {
        "available": True,
        "events": events,
        "fetched_at": payload.get("fetched_at"),
        "provider": "Alpha Vantage",
        "scope": "Upcoming estimated earnings dates for current watchlist symbols; dates can change.",
    }


def earnings_calendar(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        return _earnings_calendar_for_user(db, user_id, _sec_cached(db, "earnings-calendar:3month"))


def refresh_earnings_calendar(
    path: Path, user_id: str, api_key: str, cache_hours: int = 12
) -> dict[str, Any]:
    if not api_key:
        raise ApiError(503, "Save an Alpha Vantage key before refreshing the earnings calendar.")
    with open_db(path) as db:
        cached = _sec_cached(db, "earnings-calendar:3month", timedelta(hours=cache_hours))
        if cached is not None:
            return {**_earnings_calendar_for_user(db, user_id, cached), "cache_hit": True}
    payload = {"events": _alpha_vantage_earnings_calendar(api_key), "fetched_at": now_iso()}
    with open_db(path) as db:
        _store_sec_cache(db, "earnings-calendar:3month", payload)
        return {**_earnings_calendar_for_user(db, user_id, payload), "cache_hit": False}


def _percent(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _sma_scenario(closes: list[int]) -> dict[str, Any] | None:
    if len(closes) < 51:
        return None
    equity = Decimal(1)
    peak = Decimal(1)
    max_drawdown = Decimal(0)
    changes = 0
    previous_position = False
    for index in range(50, len(closes)):
        # ponytail: O(n*window) is deliberate for the 100-bar free feed; prefix sums if expanded.
        position = sum(closes[index - 20 : index]) / 20 > sum(closes[index - 50 : index]) / 50
        if position != previous_position:
            changes += 1
            previous_position = position
        if position:
            equity *= Decimal(closes[index]) / Decimal(closes[index - 1])
            peak = max(peak, equity)
            max_drawdown = min(max_drawdown, equity / peak - 1)
    buy_hold = Decimal(closes[-1]) / Decimal(closes[49]) - 1
    return {
        "name": "20/50 SMA historical scenario",
        "strategy_return_percent": _percent((equity - 1) * 100),
        "buy_hold_return_percent": _percent(buy_hold * 100),
        "max_drawdown_percent": _percent(max_drawdown * 100),
        "position_changes": changes,
        "assumption": "Close-to-close, long-or-cash; excludes fees, tax, spread, and slippage.",
    }


def _adjusted_history_available(
    db: sqlite3.Connection, symbol: str, rows: list[sqlite3.Row]
) -> bool:
    if not rows:
        return False
    first_date = min(str(row["trading_date"]) for row in rows)
    last_date = max(str(row["trading_date"]) for row in rows)
    adjusted = int(
        db.execute(
            "SELECT COUNT(*) FROM market_daily md JOIN market_adjustments ma "
            "ON ma.symbol = md.symbol AND ma.trading_date = md.trading_date "
            "AND ma.source = md.source AND ma.adjusted_close_micros = md.close_micros "
            "AND ma.fetched_at = md.fetched_at "
            "WHERE md.symbol = ? AND md.source = 'alpha_vantage' "
            "AND md.trading_date BETWEEN ? AND ?",
            (symbol, first_date, last_date),
        ).fetchone()[0]
    )
    return adjusted >= len(rows)


def _market_data_quality(
    rows: list[sqlite3.Row], *, historically_adjusted: bool = False
) -> dict[str, Any]:
    return assess_daily_bars(rows, historically_adjusted=historically_adjusted)


def _market_research_from_db(
    db: sqlite3.Connection, symbol: str, *, include_history: bool = True
) -> dict[str, Any]:
    rows = db.execute(
        "SELECT trading_date, open_micros, high_micros, low_micros, close_micros, "
        "volume, fetched_at FROM market_daily "
        "WHERE symbol = ? AND source = 'alpha_vantage' ORDER BY trading_date DESC LIMIT 100",
        (symbol,),
    ).fetchall()
    if not rows:
        return {
            "available": False,
            "symbol": symbol,
            "provider": "Alpha Vantage",
            "reason": "No cached daily bars. Configure the server API key, then refresh.",
            "data_quality": _market_data_quality([]),
        }

    rows = list(reversed(rows))
    closes = [int(row["close_micros"]) for row in rows]
    latest = rows[-1]
    sma20 = round(sum(closes[-20:]) / min(20, len(closes)))
    sma50 = round(sum(closes[-50:]) / min(50, len(closes)))
    if len(closes) < 50:
        state, label = "insufficient", "Building history"
        explanation = "Fifty daily bars are required for the 20/50 trend comparison."
    elif closes[-1] > sma20 > sma50:
        state, label = "bullish_alignment", "Bullish alignment"
        explanation = "Close is above the 20-day average, which is above the 50-day average."
    elif closes[-1] < sma20 < sma50:
        state, label = "bearish_alignment", "Bearish alignment"
        explanation = "Close is below the 20-day average, which is below the 50-day average."
    else:
        state, label = "mixed", "Mixed structure"
        explanation = "Price and moving averages are not aligned in one direction."
    change = Decimal(0)
    if len(closes) > 1:
        change = (Decimal(closes[-1]) / Decimal(closes[-2]) - 1) * 100
    returns = [float(Decimal(closes[index]) / Decimal(closes[index - 1]) - 1) for index in range(1, len(closes))]
    annualized_volatility = Decimal(0)
    if len(returns) > 1:
        mean = sum(returns) / len(returns)
        variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
        annualized_volatility = Decimal(str(math.sqrt(variance) * math.sqrt(252) * 100))
    peak = closes[0]
    max_drawdown = Decimal(0)
    for close in closes:
        peak = max(peak, close)
        max_drawdown = min(max_drawdown, Decimal(close) / Decimal(peak) - 1)
    volumes = [int(row["volume"]) for row in rows]
    average_volume = round(sum(volumes) / len(volumes))
    data_quality = _market_data_quality(
        rows,
        historically_adjusted=_adjusted_history_available(db, symbol, rows),
    )
    result = {
        "available": True,
        "symbol": symbol,
        "provider": "Alpha Vantage",
        "freshness": "end_of_day",
        "trading_date": latest["trading_date"],
        "fetched_at": latest["fetched_at"],
        "observations": len(rows),
        "latest_close": decimal_string(closes[-1]),
        "change_percent": _percent(change),
        "sma_20": decimal_string(sma20),
        "sma_50": decimal_string(sma50),
        "state": state,
        "state_label": label,
        "explanation": explanation,
        "historical_scenario": _sma_scenario(closes),
        "range_stats": {
            "period_label": f"{len(rows)} trading days",
            "high_close": decimal_string(max(closes)),
            "low_close": decimal_string(min(closes)),
            "period_return_percent": _percent(
                (Decimal(closes[-1]) / Decimal(closes[0]) - 1) * 100
            ),
            "max_drawdown_percent": _percent(max_drawdown * 100),
            "annualized_volatility_percent": _percent(annualized_volatility),
            "average_volume": average_volume,
            "latest_volume": volumes[-1],
            "latest_volume_vs_average_percent": _percent(
                Decimal(volumes[-1]) * 100 / Decimal(average_volume)
                if average_volume else Decimal(0)
            ),
        },
        "data_quality": data_quality,
        "disclaimer": "Research evidence, not individualized investment advice or an order signal.",
    }
    if include_history:
        result["bars"] = [
            {
                "trading_date": row["trading_date"],
                "open": decimal_string(row["open_micros"]),
                "high": decimal_string(row["high_micros"]),
                "low": decimal_string(row["low_micros"]),
                "close": decimal_string(row["close_micros"]),
                "volume": int(row["volume"]),
            }
            for row in rows
        ]
    return result


def _watchlist_research_from_db(
    db: sqlite3.Connection, user_id: str
) -> list[dict[str, Any]]:
    return [
        _market_research_from_db(db, item["symbol"], include_history=False)
        for item in _watchlist_rows(db, user_id)
    ]


def _decision_market_rows(db: sqlite3.Connection, symbol: str) -> list[sqlite3.Row]:
    return list(
        reversed(
            db.execute(
                "SELECT trading_date, open_micros, high_micros, low_micros, close_micros, volume, fetched_at FROM market_daily "
                "WHERE symbol = ? AND source = 'alpha_vantage' "
                "ORDER BY trading_date DESC LIMIT 100",
                (symbol,),
            ).fetchall()
        )
    )


def _decision_factor_set(rows: list[sqlite3.Row]) -> dict[str, Any] | None:
    if len(rows) < 51:
        return None
    closes = [int(row["close_micros"]) for row in rows]
    volumes = [int(row["volume"]) for row in rows]
    sma20 = round(sum(closes[-20:]) / 20)
    sma50 = round(sum(closes[-50:]) / 50)
    trend_score = (18 if closes[-1] > sma20 else 0) + (17 if sma20 > sma50 else 0)

    momentum = (Decimal(closes[-1]) / Decimal(closes[-21]) - 1) * 100
    if momentum >= 5:
        momentum_score = 20
    elif momentum > 0:
        momentum_score = 14
    elif momentum > -5:
        momentum_score = 7
    else:
        momentum_score = 0

    recent_closes = closes[-60:]
    peak = recent_closes[0]
    drawdown = Decimal(0)
    for close in recent_closes:
        peak = max(peak, close)
        drawdown = min(drawdown, Decimal(close) / Decimal(peak) - 1)
    drawdown_percent = drawdown * 100
    if drawdown_percent >= -5:
        drawdown_score = 10
    elif drawdown_percent >= -10:
        drawdown_score = 7
    elif drawdown_percent >= -20:
        drawdown_score = 3
    else:
        drawdown_score = 0

    returns = [
        float(Decimal(recent_closes[index]) / Decimal(recent_closes[index - 1]) - 1)
        for index in range(1, len(recent_closes))
    ]
    mean = sum(returns) / len(returns)
    variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    volatility = Decimal(str(math.sqrt(variance) * math.sqrt(252) * 100))
    if volatility <= 20:
        volatility_score = 10
    elif volatility <= 35:
        volatility_score = 7
    elif volatility <= 55:
        volatility_score = 3
    else:
        volatility_score = 0

    average_volume = round(sum(volumes[-20:]) / 20)
    volume_percent = (
        Decimal(volumes[-1]) * 100 / Decimal(average_volume)
        if average_volume else Decimal(0)
    )
    volume_score = 10 if volume_percent >= 120 else 6 if volume_percent >= 80 else 3
    factors = [
        {
            "key": "trend",
            "label": "Trend structure",
            "score": trend_score,
            "max_score": 35,
            "value": f"Close {decimal_string(closes[-1])} · SMA20 {decimal_string(sma20)} · SMA50 {decimal_string(sma50)}",
        },
        {
            "key": "momentum",
            "label": "20-day momentum",
            "score": momentum_score,
            "max_score": 20,
            "value": f"{_percent(momentum)}%",
        },
        {
            "key": "drawdown",
            "label": "60-day drawdown",
            "score": drawdown_score,
            "max_score": 10,
            "value": f"{_percent(drawdown_percent)}%",
        },
        {
            "key": "volatility",
            "label": "Annualized volatility",
            "score": volatility_score,
            "max_score": 10,
            "value": f"{_percent(volatility)}%",
        },
        {
            "key": "volume",
            "label": "Latest volume / 20-day average",
            "score": volume_score,
            "max_score": 10,
            "value": f"{_percent(volume_percent)}%",
        },
    ]
    return {
        "technical_score": sum(int(factor["score"]) for factor in factors),
        "factors": factors,
        "sma_20_micros": sma20,
        "sma_50_micros": sma50,
        "momentum_percent": momentum,
        "drawdown_percent": drawdown_percent,
        "volatility_percent": volatility,
        "volume_percent": volume_percent,
    }


def _decision_price_plan(
    rows: list[sqlite3.Row],
    factor_set: dict[str, Any] | None,
    profile: dict[str, Any],
    signal: str,
) -> dict[str, Any]:
    if signal in {"data_required", "refresh_required"} or factor_set is None or len(rows) < 20:
        return {
            "available": False,
            "reason": "Refresh at least 60 current daily bars before calculating price levels.",
        }
    true_ranges = []
    for index in range(max(1, len(rows) - 14), len(rows)):
        high = int(rows[index]["high_micros"])
        low = int(rows[index]["low_micros"])
        previous_close = int(rows[index - 1]["close_micros"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    if not true_ranges:
        return {"available": False, "reason": "ATR requires recent high, low, and close data."}
    atr = max(1, round(sum(true_ranges) / len(true_ranges)))
    reference = int(rows[-1]["close_micros"])
    sma20 = int(factor_set["sma_20_micros"])
    sma50 = int(factor_set["sma_50_micros"])
    buy_zone_low = min(reference, max(sma20, reference - atr))
    prior_high = max(int(row["high_micros"]) for row in rows[-21:-1])
    breakout_trigger = max(reference, prior_high) + max(1, round(atr / 10))
    stop_candidate = max(sma50, buy_zone_low - atr)
    risk_stop = min(reference - max(1, round(atr / 4)), stop_candidate)
    risk_per_share = max(1, reference - risk_stop)
    minimum_rr = Decimal(str(profile["minimum_reward_risk"]))
    target_one = reference + round(Decimal(risk_per_share) * minimum_rr)
    target_two = reference + round(Decimal(risk_per_share) * (minimum_rr + 1))
    action = {
        "buy_candidate": "Buy candidate: review the pullback zone or breakout trigger before a paper entry.",
        "watch": "Watch: price levels are ready, but the score has not reached the buy gate.",
        "avoid": "Avoid for now: do not treat the calculated levels as an entry signal.",
        "hold": "Hold: use the risk stop and targets to review the existing paper position.",
        "reduce": "Reduce review: the latest close is the current paper reduction reference.",
        "sell_review": "Sell / exit review: the latest close is the current exit-review reference; profit targets are inactive.",
    }.get(signal, "Review the current signal before using these scenario levels.")
    return {
        "available": True,
        "method": "ATR / moving-average risk-reward plan",
        "reference_price": decimal_string(reference),
        "buy_zone_low": decimal_string(buy_zone_low),
        "buy_zone_high": decimal_string(reference),
        "breakout_trigger": decimal_string(breakout_trigger),
        "risk_stop": decimal_string(risk_stop),
        "target_1": decimal_string(target_one),
        "target_2": decimal_string(target_two),
        "atr_14": decimal_string(atr),
        "risk_per_share": decimal_string(risk_per_share),
        "minimum_reward_risk": format(minimum_rr.normalize(), "f"),
        "targets_active": signal not in {"avoid", "reduce", "sell_review"},
        "action": action,
        "formula": [
            "Buy zone = max(SMA20, latest close - 1 ATR) through the latest close.",
            "Breakout trigger = the greater of latest close or prior 20-session high, plus 0.1 ATR.",
            "Risk stop = the greater of SMA50 or one ATR below the buy zone, capped below the reference price.",
            "Target 1 = reference price + saved minimum reward/risk multiplied by per-share risk.",
            "Target 2 = reference price + one additional R beyond Target 1.",
        ],
        "disclaimer": "Scenario prices from cached end-of-day bars are not forecasts, order instructions, or guaranteed execution prices.",
    }


STRATEGY_DEFINITIONS = {
    "balanced": {
        "label": "Balanced",
        "technical": 60,
        "fundamental": 25,
        "technical_mix": {"trend": 35, "momentum": 20, "drawdown": 15, "volatility": 15, "volume": 15},
        "fundamental_mix": {"growth": 25, "profitability": 25, "cash_flow": 25, "balance_sheet": 25},
    },
    "growth": {
        "label": "Growth",
        "technical": 40,
        "fundamental": 45,
        "technical_mix": {"trend": 35, "momentum": 25, "drawdown": 15, "volatility": 13, "volume": 12},
        "fundamental_mix": {"growth": 45, "profitability": 30, "cash_flow": 15, "balance_sheet": 10},
    },
    "value": {
        "label": "Value",
        "technical": 35,
        "fundamental": 50,
        "technical_mix": {"trend": 30, "momentum": 17, "drawdown": 20, "volatility": 23, "volume": 10},
        "fundamental_mix": {"valuation": 40, "profitability": 20, "cash_flow": 20, "balance_sheet": 20},
    },
    "income": {
        "label": "Income",
        "technical": 30,
        "fundamental": 55,
        "technical_mix": {"trend": 27, "momentum": 13, "drawdown": 20, "volatility": 27, "volume": 13},
        "fundamental_mix": {"income": 40, "cash_flow": 20, "profitability": 15, "balance_sheet": 25},
    },
    "momentum": {
        "label": "Momentum",
        "technical": 75,
        "fundamental": 10,
        "technical_mix": {"trend": 40, "momentum": 29, "drawdown": 9, "volatility": 8, "volume": 14},
        "fundamental_mix": {"growth": 40, "profitability": 20, "cash_flow": 20, "balance_sheet": 20},
    },
}


def _allocate_weights(total: int, proportions: dict[str, int]) -> dict[str, int]:
    denominator = sum(proportions.values())
    allocated = {key: total * value // denominator for key, value in proportions.items()}
    remainder = total - sum(allocated.values())
    order = sorted(proportions, key=lambda key: (total * proportions[key]) % denominator, reverse=True)
    for key in order[:remainder]:
        allocated[key] += 1
    return allocated


def _weighted_factor(factor: dict[str, Any], weight: int, category: str) -> dict[str, Any]:
    maximum = max(1, int(factor["max_score"]))
    score = (int(factor["score"]) * weight + maximum // 2) // maximum
    return {**factor, "score": score, "max_score": weight, "category": category}


def _metric_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def realtime_quote(path: Path, raw_symbol: Any) -> dict[str, Any]:
    symbol = normalize_symbol(raw_symbol)
    key_id, secret, source = _alpaca_credentials()
    if not key_id or not secret:
        return {
            "available": False,
            "configured": False,
            "symbol": symbol,
            "provider": "Alpaca Market Data",
            "feed": "iex",
            "reason": "Save Alpaca credentials to show the latest IEX trade price.",
        }
    cache_key = f"alpaca-quote:{symbol}"
    with open_db(path) as db:
        cached = _sec_cached(db, cache_key, timedelta(seconds=20))
        if cached is not None:
            return {**cached, "cache_hit": True}
    snapshot = _alpaca_json(
        f"/v2/stocks/{symbol}/snapshot", {"feed": "iex"}, key_id, secret
    )
    latest_trade = snapshot.get("latestTrade")
    latest_quote = snapshot.get("latestQuote")
    daily_bar = snapshot.get("dailyBar")
    latest_trade = latest_trade if isinstance(latest_trade, dict) else {}
    latest_quote = latest_quote if isinstance(latest_quote, dict) else {}
    daily_bar = daily_bar if isinstance(daily_bar, dict) else {}
    price = _metric_decimal(latest_trade.get("p")) or _metric_decimal(daily_bar.get("c"))
    bid = _metric_decimal(latest_quote.get("bp"))
    ask = _metric_decimal(latest_quote.get("ap"))
    now_ny = datetime.now(timezone.utc).astimezone(NEW_YORK)
    session_minutes = now_ny.hour * 60 + now_ny.minute
    session_phase = (
        "regular"
        if now_ny.weekday() < 5 and 570 <= session_minutes < 960
        else "premarket"
        if now_ny.weekday() < 5 and 240 <= session_minutes < 570
        else "closed"
    )
    result = {
        "available": price is not None,
        "configured": True,
        "symbol": symbol,
        "provider": "Alpaca Market Data",
        "configuration_source": source,
        "feed": "iex",
        "latest_price": format(price.normalize(), "f") if price is not None else None,
        "bid": format(bid.normalize(), "f") if bid is not None else None,
        "ask": format(ask.normalize(), "f") if ask is not None else None,
        "latest_trade_at": latest_trade.get("t"),
        "session_phase": session_phase,
        "fetched_at": now_iso(),
        "reason": None if price is not None else "Alpaca returned no latest IEX trade price.",
        "scope": "Latest observed IEX trade from Alpaca Basic; it may differ from the consolidated SIP quote.",
    }
    with open_db(path) as db:
        _store_sec_cache(db, cache_key, result)
    return {**result, "cache_hit": False}


def _higher_is_better(value: Decimal | None, bands: tuple[tuple[Decimal, int], ...]) -> int:
    if value is None:
        return 0
    for threshold, score in bands:
        if value >= threshold:
            return score
    return 0


def _fundamental_raw_factors(
    metrics: dict[str, Any] | None, latest_close_micros: int | None
) -> dict[str, dict[str, Any]]:
    metrics = metrics or {}
    revenue_growth = _metric_decimal(metrics.get("revenue_growth_percent"))
    income_growth = _metric_decimal(metrics.get("net_income_growth_percent"))
    margin = _metric_decimal(metrics.get("net_margin_percent"))
    leverage = _metric_decimal(metrics.get("liabilities_to_assets_percent"))
    revenue = _metric_decimal(metrics.get("revenue"))
    free_cash_flow = _metric_decimal(metrics.get("free_cash_flow"))
    eps = _metric_decimal(metrics.get("diluted_eps"))
    dividends = _metric_decimal(metrics.get("dividends_per_share"))
    price = Decimal(latest_close_micros) / SCALE if latest_close_micros else None
    cash_margin = free_cash_flow * 100 / revenue if free_cash_flow is not None and revenue not in {None, 0} else None
    pe_ratio = price / eps if price is not None and eps is not None and eps > 0 else None
    dividend_yield = dividends * 100 / price if price is not None and price > 0 and dividends is not None else None

    growth_values = [value for value in (revenue_growth, income_growth) if value is not None]
    growth_score = round(sum(
        _higher_is_better(value, ((Decimal("15"), 100), (Decimal("8"), 80), (Decimal("3"), 60), (Decimal("0"), 40)))
        for value in growth_values
    ) / len(growth_values)) if growth_values else 0
    profitability_score = _higher_is_better(
        margin,
        ((Decimal("20"), 100), (Decimal("10"), 80), (Decimal("5"), 60), (Decimal("0"), 40)),
    )
    cash_score = _higher_is_better(
        cash_margin,
        ((Decimal("15"), 100), (Decimal("8"), 80), (Decimal("0"), 60)),
    )
    balance_score = (
        100 if leverage is not None and leverage <= 40
        else 75 if leverage is not None and leverage <= 60
        else 40 if leverage is not None and leverage <= 75
        else 0
    )
    valuation_score = (
        100 if pe_ratio is not None and pe_ratio <= 15
        else 75 if pe_ratio is not None and pe_ratio <= 25
        else 45 if pe_ratio is not None and pe_ratio <= 40
        else 20 if pe_ratio is not None
        else 0
    )
    income_score = _higher_is_better(
        dividend_yield,
        ((Decimal("4"), 100), (Decimal("2.5"), 80), (Decimal("1"), 50), (Decimal("0.01"), 25)),
    )

    def percent_value(label: str, value: Decimal | None) -> str:
        return f"{label} {_percent(value)}%" if value is not None else f"{label} unavailable"

    return {
        "growth": {
            "key": "fundamental_growth", "label": "Fundamental growth", "score": growth_score,
            "max_score": 100, "available": bool(growth_values),
            "value": f"{percent_value('Revenue', revenue_growth)} · {percent_value('Net income', income_growth)}",
        },
        "profitability": {
            "key": "profitability", "label": "Profitability", "score": profitability_score,
            "max_score": 100, "available": margin is not None, "value": percent_value("Net margin", margin),
        },
        "cash_flow": {
            "key": "cash_flow", "label": "Free cash flow quality", "score": cash_score,
            "max_score": 100, "available": cash_margin is not None, "value": percent_value("FCF margin", cash_margin),
        },
        "balance_sheet": {
            "key": "balance_sheet", "label": "Balance-sheet resilience", "score": balance_score,
            "max_score": 100, "available": leverage is not None, "value": percent_value("Liabilities / assets", leverage),
        },
        "valuation": {
            "key": "valuation", "label": "Earnings valuation", "score": valuation_score,
            "max_score": 100, "available": pe_ratio is not None,
            "value": f"Price / diluted EPS {_percent(pe_ratio)}x" if pe_ratio is not None else "Positive diluted EPS unavailable",
        },
        "income": {
            "key": "dividend_income", "label": "Dividend income", "score": income_score,
            "max_score": 100, "available": dividend_yield is not None,
            "value": percent_value("Indicated annual yield", dividend_yield),
        },
    }


def _fundamental_metrics_as_of(
    history: list[dict[str, Any]], as_of: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    eligible = []
    for item in history:
        filed = str(item.get("filed") or "")
        try:
            if date.fromisoformat(filed) <= date.fromisoformat(as_of):
                eligible.append(item)
        except ValueError:
            continue
    if not eligible:
        return None, None
    eligible.sort(key=lambda item: (str(item.get("period_end") or ""), str(item.get("filed") or "")))
    return _fundamental_metrics_from_history(eligible), eligible[-1]


def _serialize_strategy_template(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "id": row["id"],
        "name": row["name"],
        "technical_weight": int(row["technical_weight"]),
        "fundamental_weight": int(row["fundamental_weight"]),
        "valuation_weight": int(row["valuation_weight"]),
        "portfolio_weight": int(row["portfolio_weight"]),
        "fee_slippage_bps": int(row["fee_slippage_bps"]),
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "version_id": row["version_id"] if "version_id" in keys else None,
        "version_number": int(row["version_number"]) if "version_number" in keys and row["version_number"] else 1,
        "config_hash": row["config_hash"] if "config_hash" in keys else None,
        "version_created_at": row["version_created_at"] if "version_created_at" in keys else None,
    }


def _strategy_template_query(where: str) -> str:
    return (
        "SELECT st.*, sv.id AS version_id, sv.version_number, sv.config_hash, "
        "sv.created_at AS version_created_at "
        "FROM strategy_templates st LEFT JOIN strategy_versions sv ON sv.id = ("
        "SELECT id FROM strategy_versions WHERE template_id = st.id "
        "ORDER BY version_number DESC LIMIT 1) WHERE " + where
    )


def _strategy_templates_from_db(db: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        _strategy_template_query("st.user_id = ?")
        + " ORDER BY st.is_active DESC, st.updated_at DESC, st.id",
        (user_id,),
    ).fetchall()
    return [_serialize_strategy_template(row) for row in rows]


def _active_strategy_template_from_db(
    db: sqlite3.Connection, user_id: str
) -> dict[str, Any] | None:
    row = db.execute(
        _strategy_template_query("st.user_id = ? AND st.is_active = 1"),
        (user_id,),
    ).fetchone()
    return _serialize_strategy_template(row) if row else None


def list_strategy_templates(path: Path, user_id: str) -> list[dict[str, Any]]:
    with open_db(path) as db:
        return _strategy_templates_from_db(db, user_id)


def _strategy_versions_from_db(
    db: sqlite3.Connection, user_id: str, template_id: str | None = None
) -> list[dict[str, Any]]:
    parameters: list[Any] = [user_id]
    where = "user_id = ?"
    if template_id:
        where += " AND template_id = ?"
        parameters.append(template_id)
    rows = db.execute(
        "SELECT id, template_id, name, version_number, config_hash, config_json, "
        f"created_at, activated_at FROM strategy_versions WHERE {where} "
        "ORDER BY created_at DESC, version_number DESC LIMIT 100",
        parameters,
    ).fetchall()
    return [
        {
            "id": row["id"], "template_id": row["template_id"], "name": row["name"],
            "version_number": int(row["version_number"]), "config_hash": row["config_hash"],
            "config": json.loads(row["config_json"]), "created_at": row["created_at"],
            "activated_at": row["activated_at"],
        }
        for row in rows
    ]


def list_strategy_versions(
    path: Path, user_id: str, template_id: str | None = None
) -> list[dict[str, Any]]:
    with open_db(path) as db:
        return _strategy_versions_from_db(db, user_id, template_id)


def save_strategy_template(
    path: Path, user_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not 1 <= len(name) <= 60:
        raise InputError("Strategy template name must be 1-60 characters.")
    weights = {}
    for key, label in (
        ("technical_weight", "Technical weight"),
        ("fundamental_weight", "Fundamental weight"),
        ("valuation_weight", "Valuation weight"),
        ("portfolio_weight", "Position-risk weight"),
    ):
        try:
            weights[key] = int(payload.get(key))
        except (TypeError, ValueError):
            raise InputError(f"{label} must be a whole percentage.") from None
        if not 0 <= weights[key] <= 100:
            raise InputError(f"{label} must be between 0 and 100.")
    if sum(weights.values()) != 100:
        raise InputError("Strategy weights must add up to exactly 100%.")
    try:
        cost_bps = int(payload.get("fee_slippage_bps", 10))
    except (TypeError, ValueError):
        raise InputError("Fee and slippage must be a whole number of basis points.") from None
    if not 0 <= cost_bps <= 500:
        raise InputError("Fee and slippage must be between 0 and 500 basis points per side.")
    activate = payload.get("activate", True)
    if not isinstance(activate, bool):
        raise InputError("Activate must be true or false.")
    config = {**weights, "fee_slippage_bps": cost_bps}
    config_json = json.dumps(config, sort_keys=True, separators=(",", ":"))
    config_hash = hashlib.sha256(config_json.encode("utf-8")).hexdigest()
    current = now_iso()
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT id FROM strategy_templates WHERE user_id = ? AND name = ?",
            (user_id, name),
        ).fetchone()
        template_id = str(existing["id"]) if existing else str(uuid4())
        if activate:
            db.execute(
                "UPDATE strategy_templates SET is_active = 0, updated_at = ? WHERE user_id = ?",
                (current, user_id),
            )
        if existing:
            db.execute(
                "UPDATE strategy_templates SET technical_weight = ?, fundamental_weight = ?, "
                "valuation_weight = ?, portfolio_weight = ?, fee_slippage_bps = ?, "
                "is_active = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (
                    weights["technical_weight"], weights["fundamental_weight"],
                    weights["valuation_weight"], weights["portfolio_weight"], cost_bps,
                    int(activate), current, template_id, user_id,
                ),
            )
        else:
            db.execute(
                "INSERT INTO strategy_templates(id, user_id, name, technical_weight, "
                "fundamental_weight, valuation_weight, portfolio_weight, fee_slippage_bps, "
                "is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    template_id, user_id, name, weights["technical_weight"],
                    weights["fundamental_weight"], weights["valuation_weight"],
                    weights["portfolio_weight"], cost_bps, int(activate), current, current,
                ),
            )
        previous = db.execute(
            "SELECT COALESCE(MAX(version_number), 0) FROM strategy_versions "
            "WHERE user_id = ? AND name = ?",
            (user_id, name),
        ).fetchone()[0]
        version_number = int(previous) + 1
        version_id = str(uuid4())
        db.execute(
            "INSERT INTO strategy_versions(id, user_id, template_id, name, version_number, "
            "config_hash, config_json, created_at, activated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_id, user_id, template_id, name, version_number, config_hash,
                config_json, current, current if activate else None,
            ),
        )
        row = db.execute(
            _strategy_template_query("st.id = ? AND st.user_id = ?"),
            (template_id, user_id),
        ).fetchone()
        assert row is not None
        result = _serialize_strategy_template(row)
        _append_sync_event(db, user_id, "strategy_template", template_id, "upsert", result)
    return result


def activate_strategy_template(path: Path, user_id: str, template_id: str) -> dict[str, Any]:
    current = now_iso()
    with open_db(path) as db:
        row = db.execute(
            "SELECT id FROM strategy_templates WHERE id = ? AND user_id = ?",
            (template_id, user_id),
        ).fetchone()
        if not row:
            raise ApiError(404, "Strategy template was not found.")
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE strategy_templates SET is_active = 0, updated_at = ? WHERE user_id = ?",
            (current, user_id),
        )
        db.execute(
            "UPDATE strategy_templates SET is_active = 1, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (current, template_id, user_id),
        )
        db.execute(
            "UPDATE strategy_versions SET activated_at = ? WHERE id = ("
            "SELECT id FROM strategy_versions WHERE template_id = ? "
            "ORDER BY version_number DESC LIMIT 1)",
            (current, template_id),
        )
        result = _active_strategy_template_from_db(db, user_id)
        assert result is not None
        _append_sync_event(db, user_id, "strategy_template", template_id, "upsert", result)
    return result


def delete_strategy_template(path: Path, user_id: str, template_id: str) -> bool:
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            "DELETE FROM strategy_templates WHERE id = ? AND user_id = ?",
            (template_id, user_id),
        )
        if cursor.rowcount:
            _append_sync_event(db, user_id, "strategy_template", template_id, "delete", None)
        return bool(cursor.rowcount)


def _strategy_scorecard(
    style: str,
    horizon: str,
    factor_set: dict[str, Any],
    position_fit: int,
    metrics: dict[str, Any] | None,
    latest_close_micros: int | None,
    fundamental_period: dict[str, Any] | None = None,
    template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    definition = STRATEGY_DEFINITIONS.get(style, STRATEGY_DEFINITIONS["balanced"])
    if template is not None:
        configured_technical = int(template["technical_weight"])
        configured_fundamental = int(template["fundamental_weight"])
        configured_valuation = int(template["valuation_weight"])
        configured_portfolio = int(template["portfolio_weight"])
        fundamentals_available = metrics is not None
        effective_technical = configured_technical
        effective_fundamental = configured_fundamental
        effective_valuation = configured_valuation
        redistributed = False
        if not fundamentals_available:
            effective_technical += effective_fundamental + effective_valuation
            effective_fundamental = 0
            effective_valuation = 0
            redistributed = configured_fundamental + configured_valuation > 0
        technical_weights = _allocate_weights(effective_technical, definition["technical_mix"])
        factors = [
            _weighted_factor(factor, technical_weights[factor["key"]], "technical")
            for factor in factor_set["factors"] if technical_weights[factor["key"]]
        ]
        raw_fundamentals = _fundamental_raw_factors(metrics, latest_close_micros)
        nonvaluation_mix = {
            key: weight for key, weight in definition["fundamental_mix"].items()
            if key != "valuation"
        } or {"growth": 25, "profitability": 25, "cash_flow": 25, "balance_sheet": 25}
        fundamental_weights = _allocate_weights(effective_fundamental, nonvaluation_mix)
        for key, weight in fundamental_weights.items():
            if weight:
                factors.append(_weighted_factor(raw_fundamentals[key], weight, "fundamental"))
        if effective_valuation:
            factors.append(
                _weighted_factor(raw_fundamentals["valuation"], effective_valuation, "valuation")
            )
        if configured_portfolio:
            factors.append(
                {
                    "key": "portfolio_fit", "label": "Position-size fit",
                    "score": round(position_fit * configured_portfolio / 15),
                    "max_score": configured_portfolio, "value": "", "category": "portfolio",
                }
            )
        relevant = [raw_fundamentals[key] for key, weight in fundamental_weights.items() if weight]
        if effective_valuation:
            relevant.append(raw_fundamentals["valuation"])
        coverage = (
            round(sum(bool(item["available"]) for item in relevant) * 100 / len(relevant))
            if relevant else 100
        )
        return {
            "score": sum(int(factor["score"]) for factor in factors),
            "factors": factors,
            "strategy": {
                "style": style, "label": template["name"], "horizon": horizon,
                "template_id": template["id"], "template_name": template["name"],
                "version_id": template.get("version_id"),
                "version_number": template.get("version_number", 1),
                "config_hash": template.get("config_hash"),
                "technical_weight": effective_technical,
                "fundamental_weight": effective_fundamental,
                "valuation_weight": effective_valuation,
                "portfolio_weight": configured_portfolio,
                "configured_fundamental_weight": configured_fundamental,
                "configured_valuation_weight": configured_valuation,
                "fee_slippage_bps": int(template["fee_slippage_bps"]),
                "fundamentals_available": fundamentals_available,
                "fundamental_coverage_percent": coverage,
                "fundamentals_period_end": fundamental_period.get("period_end") if fundamental_period else None,
                "fundamentals_filed_at": fundamental_period.get("filed") if fundamental_period else None,
                "missing_fundamentals_redistributed": redistributed,
                "horizon_note": "Custom saved weights are applied exactly; missing fundamental and valuation weight moves to technical evidence.",
            },
        }
    configured_technical = int(definition["technical"])
    configured_fundamental = int(definition["fundamental"])
    if horizon == "day":
        shift = min(10, configured_fundamental)
        configured_technical += shift
        configured_fundamental -= shift
    elif horizon == "long_term":
        shift = min(10, configured_technical)
        configured_technical -= shift
        configured_fundamental += shift

    fundamentals_available = metrics is not None
    effective_technical = configured_technical
    effective_fundamental = configured_fundamental
    redistributed = False
    if not fundamentals_available and style in {"balanced", "momentum"}:
        effective_technical += effective_fundamental
        effective_fundamental = 0
        redistributed = True

    technical_weights = _allocate_weights(effective_technical, definition["technical_mix"])
    factors = [
        _weighted_factor(factor, technical_weights[factor["key"]], "technical")
        for factor in factor_set["factors"]
    ]
    raw_fundamentals = _fundamental_raw_factors(metrics, latest_close_micros)
    fundamental_weights = _allocate_weights(effective_fundamental, definition["fundamental_mix"])
    for key, weight in fundamental_weights.items():
        if not weight:
            continue
        factors.append(_weighted_factor(raw_fundamentals[key], weight, "fundamental"))
    factors.append(
        {
            "key": "portfolio_fit",
            "label": "Position-size fit",
            "score": position_fit,
            "max_score": 15,
            "value": "",
            "category": "portfolio",
        }
    )
    relevant_fundamentals = [
        raw_fundamentals[key] for key, weight in fundamental_weights.items() if weight
    ]
    coverage = (
        round(sum(bool(item["available"]) for item in relevant_fundamentals) * 100 / len(relevant_fundamentals))
        if relevant_fundamentals else 100
    )
    horizon_note = {
        "day": "End-of-day proxy only; intraday entries still require the Day Trade worksheet.",
        "long_term": "Long-term lens shifts 10 points from technical evidence to fundamentals.",
    }.get(horizon, "Swing lens uses the strategy's base technical and fundamental allocation.")
    return {
        "score": sum(int(factor["score"]) for factor in factors),
        "factors": factors,
        "strategy": {
            "style": style,
            "label": definition["label"],
            "horizon": horizon,
            "technical_weight": effective_technical,
            "fundamental_weight": effective_fundamental,
            "portfolio_weight": 15,
            "configured_fundamental_weight": configured_fundamental,
            "fundamentals_available": fundamentals_available,
            "fundamental_coverage_percent": coverage,
            "fundamentals_period_end": fundamental_period.get("period_end") if fundamental_period else None,
            "fundamentals_filed_at": fundamental_period.get("filed") if fundamental_period else None,
            "missing_fundamentals_redistributed": redistributed,
            "horizon_note": horizon_note,
        },
    }


def _modeled_execution_cost_bps(row: sqlite3.Row, base_bps: int) -> int:
    close = max(1, int(row["close_micros"]))
    range_bps = Decimal(int(row["high_micros"]) - int(row["low_micros"])) * 10_000 / Decimal(close)
    volatility_surcharge = min(25, max(0, round(range_bps / 40)))
    dollar_volume = Decimal(close) * Decimal(int(row["volume"])) / SCALE
    liquidity_surcharge = 20 if dollar_volume < 1_000_000 else 10 if dollar_volume < 10_000_000 else 5 if dollar_volume < 50_000_000 else 0
    return min(500, base_bps + volatility_surcharge + liquidity_surcharge)


def _walk_forward_backtest(
    rows: list[sqlite3.Row],
    style: str = "balanced",
    horizon: str = "swing",
    fundamental_history: list[dict[str, Any]] | None = None,
    cost_bps: int = 10,
    template: dict[str, Any] | None = None,
    benchmark_rows: list[sqlite3.Row] | None = None,
    entry_threshold: int = 75,
    exit_threshold: int = 55,
    include_sensitivity: bool = True,
    strategy_frozen_at: str | None = None,
) -> dict[str, Any]:
    if len(rows) < 70:
        return {
            "available": False,
            "reason": "At least 70 daily bars are required for the walk-forward scenario.",
        }
    closes = [int(row["close_micros"]) for row in rows]
    equity = Decimal(1)
    peak = Decimal(1)
    maximum_drawdown = Decimal(0)
    position = False
    exposure_days = 0
    entries = 0
    completed = 0
    wins = 0
    entry_equity = Decimal(0)
    entry_date: str | None = None
    entry_price: int | None = None
    entry_index: int | None = None
    curve = []
    equity_history: list[Decimal] = []
    trades = []
    fundamental_days = 0
    modeled_costs: list[int] = []
    history = fundamental_history or []

    def charge(row: sqlite3.Row) -> None:
        nonlocal equity
        modeled_bps = _modeled_execution_cost_bps(row, cost_bps)
        modeled_costs.append(modeled_bps)
        equity *= 1 - Decimal(modeled_bps) / Decimal(10_000)

    for index in range(50, len(rows) - 1):
        factors = _decision_factor_set(rows[: index + 1])
        if not factors:
            continue
        metrics, period = _fundamental_metrics_as_of(history, str(rows[index]["trading_date"]))
        fundamental_days += int(metrics is not None)
        score = int(_strategy_scorecard(
            style, horizon, factors, 15, metrics, closes[index], period, template
        )["score"])
        desired = score >= entry_threshold if not position else score >= exit_threshold
        if desired and not position:
            charge(rows[index])
            position = True
            entries += 1
            entry_equity = equity
            entry_date = str(rows[index]["trading_date"])
            entry_price = closes[index]
            entry_index = index
        elif not desired and position:
            charge(rows[index])
            position = False
            completed += 1
            wins += int(equity > entry_equity)
            trade = {
                    "entry_date": entry_date,
                    "entry_price": decimal_string(entry_price or closes[index]),
                    "exit_date": rows[index]["trading_date"],
                    "exit_price": decimal_string(closes[index]),
                    "return_percent": _percent((equity / entry_equity - 1) * 100),
                    "outcome": "win" if equity > entry_equity else "loss",
                    "duration_sessions": index - entry_index + 1 if entry_index is not None else None,
                }
            if entry_index is not None and entry_price:
                trade_rows = rows[entry_index : index + 1]
                trade["maximum_adverse_excursion_percent"] = _percent(
                    (Decimal(min(int(row["low_micros"]) for row in trade_rows))
                    / Decimal(entry_price) - 1) * 100
                )
                trade["maximum_favorable_excursion_percent"] = _percent(
                    (Decimal(max(int(row["high_micros"]) for row in trade_rows))
                    / Decimal(entry_price) - 1) * 100
                )
            trades.append(trade)
            entry_date = None
            entry_price = None
            entry_index = None
        if position:
            equity *= Decimal(closes[index + 1]) / Decimal(closes[index])
            exposure_days += 1
        peak = max(peak, equity)
        maximum_drawdown = min(maximum_drawdown, equity / peak - 1)
        curve.append(
            {
                "trading_date": rows[index + 1]["trading_date"],
                "equity": format(equity.quantize(Decimal("0.0001")), "f"),
                "score": score,
                "invested": position,
            }
        )
        equity_history.append(equity)
    if position:
        charge(rows[-1])
        if equity_history:
            equity_history[-1] = equity
            curve[-1]["equity"] = format(equity.quantize(Decimal("0.0001")), "f")
        completed += 1
        wins += int(equity > entry_equity)
        trade = {
                "entry_date": entry_date,
                "entry_price": decimal_string(entry_price or closes[-1]),
                "exit_date": rows[-1]["trading_date"],
                "exit_price": decimal_string(closes[-1]),
                "return_percent": _percent((equity / entry_equity - 1) * 100),
                "outcome": "win" if equity > entry_equity else "loss",
                "duration_sessions": len(rows) - entry_index if entry_index is not None else None,
            }
        if entry_index is not None and entry_price:
            trade_rows = rows[entry_index:]
            trade["maximum_adverse_excursion_percent"] = _percent(
                (Decimal(min(int(row["low_micros"]) for row in trade_rows))
                / Decimal(entry_price) - 1) * 100
            )
            trade["maximum_favorable_excursion_percent"] = _percent(
                (Decimal(max(int(row["high_micros"]) for row in trade_rows))
                / Decimal(entry_price) - 1) * 100
            )
        trades.append(trade)
    benchmark = Decimal(closes[-1]) / Decimal(closes[50]) - 1
    spy_return: Decimal | None = None
    if benchmark_rows:
        sample_start = str(rows[50]["trading_date"])
        sample_end = str(rows[-1]["trading_date"])
        spy = [
            int(row["close_micros"]) for row in benchmark_rows
            if sample_start <= str(row["trading_date"]) <= sample_end
        ]
        if len(spy) >= 2:
            spy_return = Decimal(spy[-1]) / Decimal(spy[0]) - 1
    strategy_return = equity - 1
    sample_days = max(1, len(rows) - 51)
    result = {
        "available": True,
        "walk_forward": True,
        "sample_start": rows[50]["trading_date"],
        "sample_end": rows[-1]["trading_date"],
        "sample_days": sample_days,
        "strategy_return_percent": _percent(strategy_return * 100),
        "buy_hold_return_percent": _percent(benchmark * 100),
        "relative_return_percent": _percent((strategy_return - benchmark) * 100),
        "spy_return_percent": _percent(spy_return * 100) if spy_return is not None else None,
        "relative_to_spy_percent": (
            _percent((strategy_return - spy_return) * 100) if spy_return is not None else None
        ),
        "benchmark_symbol": "SPY",
        "benchmark_available": spy_return is not None,
        "max_drawdown_percent": _percent(maximum_drawdown * 100),
        "entries": entries,
        "completed_trades": completed,
        "win_rate_percent": _percent(Decimal(wins) * 100 / Decimal(completed)) if completed else None,
        "exposure_percent": _percent(Decimal(exposure_days) * 100 / Decimal(sample_days)),
        "fee_slippage_bps_per_side": cost_bps,
        "execution_cost_model": "base bps plus daily-range and dollar-volume surcharge",
        "average_modeled_cost_bps_per_side": (
            _percent(Decimal(sum(modeled_costs)) / Decimal(len(modeled_costs)))
            if modeled_costs else _percent(Decimal(cost_bps))
        ),
        "modeled_cost_events": len(modeled_costs),
        "entry_threshold": entry_threshold,
        "exit_threshold": exit_threshold,
        "strategy_style": style,
        "time_horizon": horizon,
        "point_in_time_fundamentals": True,
        "fundamental_observation_days": fundamental_days,
        "rules": f"{template['name'] if template else STRATEGY_DEFINITIONS.get(style, STRATEGY_DEFINITIONS['balanced'])['label']} {horizon.replace('_', ' ')}: use only prices and SEC filings known at each close; enter when score >= {entry_threshold} and exit when score < {exit_threshold}.",
        "assumption": f"Long or cash, fractional exposure, {cost_bps} base bps plus a daily-range and dollar-volume execution surcharge per side; excludes tax.",
        "equity_curve": curve,
        "trades": trades,
    }
    if len(equity_history) >= 30:
        split_index = max(1, len(equity_history) * 70 // 100)
        baseline_equity = equity_history[split_index - 1]
        holdout_return = equity_history[-1] / baseline_equity - 1
        baseline_row_index = 50 + split_index
        holdout_buy_hold = Decimal(closes[-1]) / Decimal(closes[baseline_row_index]) - 1
        holdout_spy_return: Decimal | None = None
        baseline_date = str(rows[baseline_row_index]["trading_date"])
        if benchmark_rows:
            holdout_spy = [
                int(row["close_micros"]) for row in benchmark_rows
                if baseline_date <= str(row["trading_date"]) <= str(rows[-1]["trading_date"])
            ]
            if len(holdout_spy) >= 2:
                holdout_spy_return = Decimal(holdout_spy[-1]) / Decimal(holdout_spy[0]) - 1
        try:
            frozen_date = datetime.fromisoformat(
                str(strategy_frozen_at).replace("Z", "+00:00")
            ).date()
            frozen_before_holdout = frozen_date <= date.fromisoformat(baseline_date)
        except (TypeError, ValueError):
            frozen_before_holdout = False
        holdout = {
            "available": frozen_before_holdout,
            "method": "chronological_70_30_holdout",
            "parameters_frozen": frozen_before_holdout,
            "strategy_frozen_at": strategy_frozen_at,
            "development_sessions": split_index,
            "sessions": len(equity_history) - split_index,
            "sample_start": rows[baseline_row_index]["trading_date"],
            "sample_end": curve[-1]["trading_date"],
            "selection_warning": "The final 30% is a chronological holdout for this frozen run. Reusing it to tune parameters turns it into development data.",
        }
        if frozen_before_holdout:
            holdout.update({
                "strategy_return_percent": _percent(holdout_return * 100),
                "buy_hold_return_percent": _percent(holdout_buy_hold * 100),
                "relative_to_buy_hold_percent": _percent((holdout_return - holdout_buy_hold) * 100),
                "spy_return_percent": _percent(holdout_spy_return * 100) if holdout_spy_return is not None else None,
                "relative_to_spy_percent": (
                    _percent((holdout_return - holdout_spy_return) * 100)
                    if holdout_spy_return is not None else None
                ),
            })
        else:
            holdout["reason"] = (
                "This strategy version was not frozen before the candidate holdout began. "
                "Keep it unchanged and collect future bars before treating results as out-of-sample."
            )
        result["out_of_sample"] = holdout
    else:
        result["out_of_sample"] = {
            "available": False,
            "method": "chronological_70_30_holdout",
            "parameters_frozen": False,
            "strategy_frozen_at": strategy_frozen_at,
            "reason": "At least 30 evaluated walk-forward sessions are required for a separate holdout window.",
        }
    if include_sensitivity:
        variants = []
        for label, entry_gate, exit_gate in (
            ("conservative", 80, 60),
            ("base", entry_threshold, exit_threshold),
            ("permissive", 70, 50),
        ):
            scenario = result if label == "base" else _walk_forward_backtest(
                rows, style, horizon, fundamental_history, cost_bps, template,
                benchmark_rows, entry_gate, exit_gate, False, strategy_frozen_at,
            )
            variants.append(
                {
                    "label": label,
                    "entry_threshold": entry_gate,
                    "exit_threshold": exit_gate,
                    "strategy_return_percent": scenario.get("strategy_return_percent"),
                    "max_drawdown_percent": scenario.get("max_drawdown_percent"),
                    "completed_trades": scenario.get("completed_trades"),
                    "win_rate_percent": scenario.get("win_rate_percent"),
                }
            )
        returns = [Decimal(str(item["strategy_return_percent"])) for item in variants]
        spread = max(returns) - min(returns)
        result["parameter_sensitivity"] = variants
        result["stability"] = {
            "label": "stable" if spread <= 10 else "mixed" if spread <= 25 else "unstable",
            "return_range_points": _percent(spread),
            "note": "Compares stricter and looser score gates on the same point-in-time sample.",
        }
    return result


def _decision_settings_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    row = db.execute("SELECT * FROM decision_settings WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        raise RuntimeError("Decision settings are missing for this account.")
    return {
        "auto_refresh_enabled": bool(row["auto_refresh_enabled"]),
        "refresh_interval_hours": int(row["refresh_interval_hours"]),
        "last_refresh_at": row["last_refresh_at"],
        "updated_at": row["updated_at"],
        "scope": "The local server refreshes cached daily bars only while it is running.",
    }


def decision_settings(path: Path, user_id: str) -> dict[str, Any]:
    with open_db(path) as db:
        return _decision_settings_from_db(db, user_id)


def update_decision_settings(path: Path, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    enabled = payload.get("auto_refresh_enabled")
    if not isinstance(enabled, bool):
        raise InputError("Automatic refresh must be true or false.")
    try:
        interval = int(payload.get("refresh_interval_hours"))
    except (TypeError, ValueError):
        raise InputError("Refresh interval must be a whole number of hours.") from None
    if not 12 <= interval <= 168:
        raise InputError("Refresh interval must be between 12 and 168 hours.")
    updated_at = now_iso()
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE decision_settings SET auto_refresh_enabled = ?, refresh_interval_hours = ?, "
            "updated_at = ? WHERE user_id = ?",
            (int(enabled), interval, updated_at, user_id),
        )
        result = _decision_settings_from_db(db, user_id)
        _append_sync_event(db, user_id, "decision_settings", user_id, "upsert", result)
    return result


def _decision_rows(
    db: sqlite3.Connection, user_id: str, symbol: str | None = None, limit: int = 30
) -> list[dict[str, Any]]:
    parameters: list[Any] = [user_id]
    where = "user_id = ?"
    if symbol:
        where += " AND symbol = ?"
        parameters.append(symbol)
    parameters.append(max(1, min(limit, 1_000_000)))
    rows = db.execute(
        f"SELECT result_json FROM decision_runs WHERE {where} ORDER BY rowid DESC LIMIT ?",
        parameters,
    ).fetchall()
    return [json.loads(row["result_json"]) for row in rows]


def _strategy_freeze_context(
    model_version: Any,
    style: Any,
    horizon: Any,
    strategy: dict[str, Any] | None,
    freeze_protocol: Any,
) -> str:
    strategy = strategy if isinstance(strategy, dict) else {}
    return json.dumps(
        {
            "model_version": model_version,
            "freeze_protocol": freeze_protocol,
            "style": style,
            "horizon": horizon,
            "config_hash": strategy.get("config_hash"),
            "version_id": strategy.get("version_id"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _strategy_context_frozen_at(
    runs: list[dict[str, Any]],
    model_version: str,
    style: str,
    horizon: str,
    strategy: dict[str, Any] | None,
) -> str | None:
    target = _strategy_freeze_context(
        model_version,
        style,
        horizon,
        strategy,
        STRATEGY_FREEZE_PROTOCOL,
    )
    frozen_at = None
    for run in runs:
        run_strategy = run.get("strategy") if isinstance(run.get("strategy"), dict) else {}
        context = _strategy_freeze_context(
            run.get("model_version"),
            run_strategy.get("style"),
            run_strategy.get("horizon"),
            run_strategy,
            run_strategy.get("freeze_protocol"),
        )
        if context != target:
            break
        if run.get("created_at"):
            frozen_at = str(run["created_at"])
    return frozen_at


def _strategy_validation_from_db(
    db: sqlite3.Connection,
    user_id: str,
    symbol: str,
    backtest: dict[str, Any],
) -> dict[str, Any]:
    runs = _decision_rows(db, user_id, symbol, 200)
    latest_by_date: dict[str, dict[str, Any]] = {}
    for run in runs:
        trading_date = str(run.get("trading_date") or "")
        if trading_date:
            latest_by_date.setdefault(trading_date, run)
    market_rows = db.execute(
        "SELECT trading_date, high_micros, low_micros, close_micros FROM market_daily "
        "WHERE symbol = ? AND source = 'alpha_vantage' ORDER BY trading_date",
        (symbol,),
    ).fetchall()
    outcomes = []
    forward_values: dict[int, list[Decimal]] = {5: [], 10: [], 20: [], 60: []}
    mae_values: list[Decimal] = []
    mfe_values: list[Decimal] = []
    for run_date, run in sorted(latest_by_date.items()):
        plan = run.get("price_plan") or {}
        if not plan.get("available") or not plan.get("targets_active"):
            continue
        try:
            reference = Decimal(str(plan["reference_price"]))
            target = Decimal(str(plan["target_1"]))
            stop = Decimal(str(plan["risk_stop"]))
        except (InvalidOperation, KeyError, TypeError):
            continue
        future = [row for row in market_rows if str(row["trading_date"]) > run_date][:60]
        if not future:
            continue
        resolution = "unresolved"
        resolved_date = None
        for row in future:
            high = Decimal(int(row["high_micros"])) / SCALE
            low = Decimal(int(row["low_micros"])) / SCALE
            target_hit = high >= target
            stop_hit = low <= stop
            if target_hit and stop_hit:
                resolution, resolved_date = "ambiguous", row["trading_date"]
                break
            if target_hit:
                resolution, resolved_date = "target_first", row["trading_date"]
                break
            if stop_hit:
                resolution, resolved_date = "stop_first", row["trading_date"]
                break
        lows = [Decimal(int(row["low_micros"])) / SCALE for row in future]
        highs = [Decimal(int(row["high_micros"])) / SCALE for row in future]
        mae = (min(lows) / reference - 1) * 100
        mfe = (max(highs) / reference - 1) * 100
        mae_values.append(mae)
        mfe_values.append(mfe)
        forward_returns = {}
        for horizon in forward_values:
            if len(future) >= horizon:
                future_close = Decimal(int(future[horizon - 1]["close_micros"])) / SCALE
                value = (future_close / reference - 1) * 100
                forward_values[horizon].append(value)
                forward_returns[str(horizon)] = _percent(value)
        outcomes.append(
            {
                "decision_date": run_date,
                "signal": run.get("signal"),
                "score": run.get("score"),
                "reference_price": format(reference.normalize(), "f"),
                "target_1": format(target.normalize(), "f"),
                "risk_stop": format(stop.normalize(), "f"),
                "resolution": resolution,
                "resolved_date": resolved_date,
                "observed_sessions": len(future),
                "maximum_adverse_excursion_percent": _percent(mae),
                "maximum_favorable_excursion_percent": _percent(mfe),
                "forward_returns_percent": forward_returns,
            }
        )
    counts = {
        state: sum(item["resolution"] == state for item in outcomes)
        for state in ("target_first", "stop_first", "ambiguous", "unresolved")
    }
    decisive = counts["target_first"] + counts["stop_first"]
    return {
        "available": bool(outcomes),
        "symbol": symbol,
        "eligible_decisions": len(outcomes),
        "target_first": counts["target_first"],
        "stop_first": counts["stop_first"],
        "ambiguous": counts["ambiguous"],
        "unresolved": counts["unresolved"],
        "target_first_rate_percent": (
            _percent(Decimal(counts["target_first"]) * 100 / Decimal(decisive))
            if decisive else None
        ),
        "average_forward_returns_percent": {
            str(horizon): _percent(sum(values) / Decimal(len(values))) if values else None
            for horizon, values in forward_values.items()
        },
        "average_maximum_adverse_excursion_percent": (
            _percent(sum(mae_values) / Decimal(len(mae_values))) if mae_values else None
        ),
        "average_maximum_favorable_excursion_percent": (
            _percent(sum(mfe_values) / Decimal(len(mfe_values))) if mfe_values else None
        ),
        "outcomes": list(reversed(outcomes[-20:])),
        "parameter_sensitivity": backtest.get("parameter_sensitivity", []),
        "stability": backtest.get("stability"),
        "reason": None if outcomes else "Generate decisions, then cache later daily bars to measure outcomes.",
        "scope": "Uses each stored decision's original target and stop against later cached daily highs/lows. Same-day target and stop hits are marked ambiguous.",
    }


def _validation_strategy_context(run: dict[str, Any]) -> str:
    strategy = run.get("strategy") if isinstance(run.get("strategy"), dict) else {}
    return json.dumps(
        {
            "model_version": run.get("model_version"),
            "freeze_protocol": strategy.get("freeze_protocol"),
            "style": strategy.get("style"),
            "horizon": strategy.get("horizon"),
            "config_hash": strategy.get("config_hash"),
            "version_id": strategy.get("version_id"),
            "technical_weight": strategy.get("technical_weight"),
            "fundamental_weight": strategy.get("fundamental_weight"),
            "valuation_weight": strategy.get("valuation_weight"),
            "portfolio_weight": strategy.get("portfolio_weight"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _validation_operations_snapshot(
    path: Path, user_id: str, validation: dict[str, Any]
) -> dict[str, Any]:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=48)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    with open_db(path) as db:
        settings = _decision_settings_from_db(db, user_id)
        symbols = [
            str(row["symbol"])
            for row in db.execute(
                "SELECT symbol FROM watchlist WHERE user_id = ? ORDER BY created_at, symbol",
                (user_id,),
            ).fetchall()
        ]
        plan_center = _plan_review_center_from_db(db, user_id)
        runs = _collection_runs_from_db(db, user_id, 30)
        reports = [
            dict(row)
            for row in db.execute(
                "SELECT period, report_date, created_at FROM research_reports "
                "WHERE user_id = ? ORDER BY report_date DESC, created_at DESC LIMIT 8",
                (user_id,),
            ).fetchall()
        ]
    alpha_key, alpha_source = _alpha_vantage_api_key()
    alpaca_key, alpaca_secret, alpaca_source = _alpaca_credentials()
    alpha_ready = bool(alpha_key)
    alpaca_ready = bool(alpaca_key and alpaca_secret)
    recent_failures = []
    for run in runs:
        if str(run.get("started_at") or "") < cutoff or run.get("status") not in {"failed", "partial"}:
            continue
        errors = run.get("result", {}).get("errors") if isinstance(run.get("result"), dict) else None
        recent_failures.append({
            "job_type": run["job_type"],
            "status": run["status"],
            "error": run.get("error_text") or (json.dumps(errors)[:500] if errors else "Some requested items did not complete."),
            "started_at": run["started_at"],
        })
    blockers = []
    warnings = []
    if not alpha_ready:
        blockers.append({
            "key": "alpha_vantage",
            "label": "Daily market provider required",
            "detail": "Save an Alpha Vantage key in Settings before automatic daily decisions can run.",
        })
    if len(symbols) < 5:
        blockers.append({
            "key": "symbol_pool",
            "label": "Validation pool needs five symbols",
            "detail": f"{len(symbols)} configured; add {5 - len(symbols)} more liquid symbols.",
        })
    if not settings["auto_refresh_enabled"]:
        blockers.append({
            "key": "daily_schedule",
            "label": "Daily decision schedule is off",
            "detail": "Enable the 24-hour decision refresh schedule to accumulate frozen evidence.",
        })
    if not alpaca_ready:
        warnings.append({
            "key": "alpaca",
            "label": "Intraday and option collection paused",
            "detail": "Save Alpaca Paper/IEX credentials to collect minute bars and indicative option snapshots.",
        })
    if plan_center["awaiting_review"]:
        warnings.append({
            "key": "plan_reviews",
            "label": "Paper plans await review",
            "detail": f"{plan_center['awaiting_review']} saved plan(s) have no followed/skipped decision.",
        })
    if recent_failures:
        warnings.append({
            "key": "collection_failures",
            "label": "Recent collection needs attention",
            "detail": f"{len(recent_failures)} failed or partial run(s) in the last 48 hours.",
        })
    latest_jobs: dict[str, dict[str, Any]] = {}
    for run in runs:
        latest_jobs.setdefault(str(run["job_type"]), run)
    return {
        "status": "blocked" if blockers else "attention" if warnings else "ready",
        "app_version": APP_VERSION,
        "pool": {"symbols": symbols, "count": len(symbols), "required": 5},
        "providers": {
            "daily": {"configured": alpha_ready, "source": alpha_source},
            "intraday_options": {"configured": alpaca_ready, "source": alpaca_source},
        },
        "automation": {
            "scheduler_running": bool(SCHEDULER_STATE["running"]),
            "last_cycle_at": SCHEDULER_STATE["last_cycle_at"],
            "daily_decisions": bool(settings["auto_refresh_enabled"] and alpha_ready),
            "refresh_interval_hours": settings["refresh_interval_hours"],
            "last_decision_refresh_at": settings["last_refresh_at"],
            "intraday_collection": bool(
                os.environ.get("INVESTORLAB_INTRADAY_COLLECTION") == "1" and alpaca_ready
            ),
            "option_collection": bool(
                os.environ.get("INVESTORLAB_OPTION_COLLECTION", "1") == "1" and alpaca_ready
            ),
            "verified_daily_backup": True,
            "daily_weekly_reports": True,
            "local_review_reminders": True,
        },
        "review_queue": {
            "awaiting": plan_center["awaiting_review"],
            "active_followed": plan_center["active_followed"],
        },
        "blockers": blockers,
        "warnings": warnings,
        "recent_failures": recent_failures[:10],
        "latest_jobs": latest_jobs,
        "reports": reports,
        "capital_review_ready": bool(validation.get("ready_for_capital_review")),
        "instruction": "Clear blocking setup items, keep one strategy context frozen, and let the local scheduler collect evidence. Missing provider data never counts as a completed sample.",
    }


def validation_dashboard(path: Path, user_id: str, window_days: int = 60) -> dict[str, Any]:
    window_days = max(30, min(window_days, 60))
    cutoff_date = (datetime.now(timezone.utc).date() - timedelta(days=window_days)).isoformat()
    cutoff_time = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat(timespec="seconds").replace("+00:00", "Z")
    with open_db(path) as db:
        decision_runs = _decision_rows(db, user_id, limit=1_000_000)
        current_runs = [
            run for run in decision_runs
            if run.get("model_version") == DECISION_MODEL_VERSION
        ]
        cohort_runs: list[dict[str, Any]] = []
        if current_runs:
            current_context = _validation_strategy_context(current_runs[0])
            for run in current_runs:
                if _validation_strategy_context(run) != current_context:
                    break
                cohort_runs.append(run)
        windowed_cohort_runs = [
            run for run in cohort_runs
            if run.get("created_at") and str(run["created_at"]) >= cutoff_time
        ]
        eligible_cohort_runs = [
            run for run in windowed_cohort_runs
            if isinstance(run.get("data_quality"), dict)
            and run["data_quality"].get("decision_eligible") is True
        ]
        first_evidence = min(
            (
                str(run.get("created_at")) for run in eligible_cohort_runs
                if run.get("created_at")
            ),
            default=None,
        )
        effective_cutoff_time = max(cutoff_time, first_evidence) if first_evidence else cutoff_time
        effective_cutoff_date = max(cutoff_date, first_evidence[:10]) if first_evidence else cutoff_date
        symbols = sorted({
            str(run.get("symbol")) for run in eligible_cohort_runs if run.get("symbol")
        })
        by_symbol = []
        all_outcomes = []
        for symbol in symbols:
            validation = _strategy_validation_from_db(db, user_id, symbol, {})
            recent_outcomes = [
                item for item in validation["outcomes"]
                if str(item.get("decision_date") or "") >= effective_cutoff_date
            ]
            if recent_outcomes:
                counts = {
                    state: sum(item["resolution"] == state for item in recent_outcomes)
                    for state in ("target_first", "stop_first", "ambiguous", "unresolved")
                }
                decisive = counts["target_first"] + counts["stop_first"]
                by_symbol.append({
                    "symbol": symbol,
                    "samples": len(recent_outcomes),
                    **counts,
                    "target_first_rate_percent": (
                        _percent(Decimal(counts["target_first"]) * 100 / Decimal(decisive))
                        if decisive else None
                    ),
                })
                all_outcomes.extend({"symbol": symbol, **item} for item in recent_outcomes)
        reviews = [
            item for item in _plan_review_rows(db, user_id, limit=None)
            if str(item.get("created_at") or "") >= effective_cutoff_time
        ]
        followed = [item for item in reviews if item["decision"] == "followed"]
        resolved_reviews = [item for item in followed if item["outcome"] in {"win", "loss", "scratch"}]
        r_values = [Decimal(str(item["realized_r_multiple"])) for item in followed if item.get("realized_r_multiple") is not None]
        pnl_values = [Decimal(str(item["realized_pnl"])) for item in followed if item.get("realized_pnl") is not None]
        discipline = [Decimal(int(item["discipline_score"])) for item in reviews if item.get("discipline_score") is not None]
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            intraday_sessions = int(db.execute(
                "SELECT COUNT(DISTINCT symbol || ':' || substr(bar_timestamp, 1, 10)) "
                "FROM intraday_bars WHERE timeframe = '1Min' AND bar_timestamp >= ? "
                f"AND symbol IN ({placeholders})",
                [effective_cutoff_time, *symbols],
            ).fetchone()[0])
        else:
            intraday_sessions = 0
        option_snapshots = int(db.execute(
            "SELECT COUNT(*) FROM option_chain_snapshots WHERE user_id = ? AND fetched_at >= ?",
            (user_id, effective_cutoff_time),
        ).fetchone()[0])
    decisive_outcomes = [item for item in all_outcomes if item["resolution"] in {"target_first", "stop_first"}]
    target_first = sum(item["resolution"] == "target_first" for item in decisive_outcomes)
    observation_days = 0
    if first_evidence:
        first = datetime.fromisoformat(str(first_evidence).replace("Z", "+00:00")).date()
        observation_days = max(0, (datetime.now(timezone.utc).date() - first).days)
    strategy_contexts = {
        _validation_strategy_context(run) for run in eligible_cohort_runs
    }
    parameters_frozen = bool(eligible_cohort_runs) and len(strategy_contexts) == 1
    restarted_after_context_change = len(current_runs) > len(cohort_runs)
    gates = [
        {"key": "observation_window", "label": "30 calendar days observed", "passed": observation_days >= 30, "value": observation_days, "required": 30},
        {"key": "symbol_coverage", "label": "5 decision symbols", "passed": len(symbols) >= 5, "value": len(symbols), "required": 5},
        {"key": "parameter_consistency", "label": "1 frozen strategy context", "passed": parameters_frozen, "value": len(strategy_contexts), "required": 1},
        {"key": "decision_samples", "label": "20 resolved decision samples", "passed": len(decisive_outcomes) >= 20, "value": len(decisive_outcomes), "required": 20},
        {"key": "review_samples", "label": "20 resolved paper-plan reviews", "passed": len(resolved_reviews) >= 20, "value": len(resolved_reviews), "required": 20},
        {"key": "intraday_samples", "label": "10 cached intraday sessions", "passed": intraday_sessions >= 10, "value": intraday_sessions, "required": 10},
    ]
    ready_for_capital_review = all(item["passed"] for item in gates)
    if not first_evidence:
        campaign_status = "not_started"
    elif ready_for_capital_review:
        campaign_status = "capital_review_ready"
    elif observation_days < 30:
        campaign_status = "collecting"
    elif observation_days <= 60:
        campaign_status = "review_window"
    else:
        campaign_status = "extended_collection"
    result = {
        "window_days": window_days,
        "cutoff_date": effective_cutoff_date,
        "generated_at": now_iso(),
        "observation_days": observation_days,
        "ready_for_capital_review": ready_for_capital_review,
        "readiness_gates": gates,
        "campaign": {
            "status": campaign_status,
            "started_at": first_evidence,
            "day_number": min(window_days, observation_days + 1) if first_evidence else 0,
            "minimum_days": 30,
            "maximum_days": 60,
            "parameters_frozen": parameters_frozen,
            "strategy_contexts": len(strategy_contexts),
            "model_version": DECISION_MODEL_VERSION,
            "eligible_decisions": len(eligible_cohort_runs),
            "restarted_after_context_change": restarted_after_context_change,
            "instruction": "Keep one strategy context frozen for 30-60 calendar days; changing the model, style, horizon, or weights starts a new cohort automatically.",
        },
        "decision_validation": {
            "eligible": len(all_outcomes),
            "decisive": len(decisive_outcomes),
            "target_first": target_first,
            "stop_first": len(decisive_outcomes) - target_first,
            "target_first_rate_percent": _percent(Decimal(target_first) * 100 / Decimal(len(decisive_outcomes))) if decisive_outcomes else None,
            "by_symbol": by_symbol,
        },
        "paper_reviews": {
            "total": len(reviews),
            "followed": len(followed),
            "resolved": len(resolved_reviews),
            "wins": sum(item["outcome"] == "win" for item in resolved_reviews),
            "losses": sum(item["outcome"] == "loss" for item in resolved_reviews),
            "scratches": sum(item["outcome"] == "scratch" for item in resolved_reviews),
            "average_r_multiple": _percent(sum(r_values) / Decimal(len(r_values))) if r_values else None,
            "realized_pnl": format(sum(pnl_values).quantize(Decimal("0.01")), "f") if pnl_values else None,
            "average_discipline_score": _percent(sum(discipline) / Decimal(len(discipline))) if discipline else None,
        },
        "coverage": {
            "intraday_sessions": intraday_sessions,
            "option_chain_snapshots": option_snapshots,
            "decision_symbols": len(symbols),
        },
        "scope": "A local paper-validation gate. It measures stored decisions, later bars, and self-recorded plan reviews; it does not predict future returns or route orders.",
    }
    result["operations"] = _validation_operations_snapshot(path, user_id, result)
    return result


def validation_report(path: Path, user_id: str) -> dict[str, Any]:
    dashboard = validation_dashboard(path, user_id, 60)
    campaign = dashboard["campaign"]
    operations = dashboard["operations"]
    lines = [
        "# Stock Thesis Ledger validation report",
        "",
        f"Generated: {dashboard['generated_at']}",
        f"Campaign: day {campaign['day_number']} of {campaign['maximum_days']}",
        f"Model: {campaign['model_version']} / {STRATEGY_FREEZE_PROTOCOL}",
        f"Capital review ready: {'yes' if dashboard['ready_for_capital_review'] else 'no'}",
        "",
        "## Readiness gates",
        "",
    ]
    for gate in dashboard["readiness_gates"]:
        marker = "x" if gate["passed"] else " "
        lines.append(
            f"- [{marker}] {gate['label']}: {gate['value']} / {gate['required']}"
        )
    lines.extend(["", "## Operations", ""])
    lines.append(f"- Pool: {', '.join(operations['pool']['symbols']) or 'none'}")
    lines.append(
        f"- Daily decisions: {'running' if operations['automation']['daily_decisions'] else 'blocked'}"
    )
    lines.append(
        f"- Intraday collection: {'running' if operations['automation']['intraday_collection'] else 'blocked'}"
    )
    lines.append(
        f"- Option collection: {'running' if operations['automation']['option_collection'] else 'blocked'}"
    )
    for item in operations["blockers"] + operations["warnings"]:
        lines.append(f"- {item['label']}: {item['detail']}")
    lines.extend([
        "",
        "## Scope",
        "",
        dashboard["scope"],
        "",
    ])
    return {
        "filename": f"stock-thesis-ledger-validation-{date.today().isoformat()}.md",
        "generated_at": dashboard["generated_at"],
        "markdown": "\n".join(lines),
        "dashboard": dashboard,
    }


def _decision_center_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    recent = _decision_rows(db, user_id, limit=50)
    latest_by_symbol: dict[str, dict[str, Any]] = {}
    for item in recent:
        latest_by_symbol.setdefault(item["symbol"], item)
    return {
        "model_version": DECISION_MODEL_VERSION,
        "latest": sorted(latest_by_symbol.values(), key=lambda item: item["symbol"]),
        "recent_changes": [item for item in recent if item["change"]["signal_changed"]][:10],
        "settings": _decision_settings_from_db(db, user_id),
    }


def _watchlist_screener_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    decisions = {
        item["symbol"]: item for item in _decision_center_from_db(db, user_id)["latest"]
    }
    research = {
        item["symbol"]: item for item in _watchlist_research_from_db(db, user_id)
    }
    signal_priority = {
        "sell_review": 0,
        "reduce": 1,
        "buy_candidate": 2,
        "watch": 3,
        "hold": 4,
        "avoid": 5,
        "refresh_required": 6,
        "data_required": 7,
    }
    items = []
    today = date.today()
    for watch in _watchlist_rows(db, user_id):
        symbol = watch["symbol"]
        market = research.get(symbol, {"available": False, "symbol": symbol})
        decision = decisions.get(symbol)
        trading_date = market.get("trading_date")
        age_days = (today - date.fromisoformat(trading_date)).days if trading_date else None
        freshness = "missing" if not market.get("available") else "stale" if age_days is not None and age_days > 7 else "current"
        signal = decision["signal"] if decision else "data_required"
        segment = (
            "risk"
            if signal in {"sell_review", "reduce"}
            else "opportunity"
            if signal == "buy_candidate"
            else "data"
            if freshness != "current" or signal in {"refresh_required", "data_required"}
            else "position"
            if signal == "hold"
            else "watch"
        )
        items.append(
            {
                "symbol": symbol,
                "segment": segment,
                "signal": signal,
                "signal_label": decision["signal_label"] if decision else "Data required",
                "score": decision["score"] if decision else None,
                "has_position": bool(decision and decision["has_position"]),
                "account_percent": decision["position"]["account_percent"] if decision else "0.00",
                "latest_close": market.get("latest_close"),
                "change_percent": market.get("change_percent"),
                "state_label": market.get("state_label") or "No cached evidence",
                "trading_date": trading_date,
                "data_age_days": age_days,
                "freshness": freshness,
                "rank": signal_priority.get(signal, 99),
            }
        )
    items.sort(key=lambda item: (item["rank"], -(item["score"] or -1), item["symbol"]))
    return {
        "items": items,
        "counts": {
            segment: sum(item["segment"] == segment for item in items)
            for segment in ("risk", "opportunity", "data", "position", "watch")
        },
        "sort": "Risk actions, opportunities, watch/hold, then missing data; score breaks ties.",
        "freshness": "end_of_day",
    }


def _sec_event_center_from_db(db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    events = []
    current = date.today()
    for watch in _watchlist_rows(db, user_id):
        symbol = watch["symbol"]
        cached = _sec_cached(db, f"fundamentals:{symbol}")
        if not cached or not cached.get("available"):
            continue
        for filing in cached.get("filings", []):
            if not isinstance(filing, dict):
                continue
            form = str(filing.get("form") or "")
            kind, title, priority = {
                "10-K": ("annual_results", "Annual report filed", "review"),
                "10-K/A": ("annual_results", "Annual report amendment filed", "review"),
                "10-Q": ("quarterly_results", "Quarterly report filed", "review"),
                "10-Q/A": ("quarterly_results", "Quarterly report amendment filed", "review"),
                "8-K": ("material_update", "Material current report filed", "attention"),
            }.get(form, ("other", "SEC filing filed", "review"))
            try:
                age_days = (current - date.fromisoformat(str(filing.get("filed") or ""))).days
            except ValueError:
                continue
            if age_days < 0 or age_days > 90:
                continue
            events.append(
                {
                    **filing,
                    "id": f"{symbol}-{filing.get('accession') or hashlib.sha256(str(filing.get('url')).encode()).hexdigest()[:12]}",
                    "symbol": symbol,
                    "company_name": cached.get("company_name") or symbol,
                    "form": form,
                    "kind": filing.get("kind") or kind,
                    "title": filing.get("title") or title,
                    "priority": filing.get("priority") or priority,
                    "age_days": age_days,
                    "is_recent": age_days <= 7,
                }
            )
    events.sort(key=lambda item: (item["filed"], item["symbol"], item["form"]), reverse=True)
    return {
        "events": events[:30],
        "recent_count": sum(item["is_recent"] for item in events),
        "attention_count": sum(
            item["is_recent"] and item.get("priority") == "attention" for item in events
        ),
        "annual_count": sum(item.get("kind") == "annual_results" for item in events),
        "quarterly_count": sum(item.get("kind") == "quarterly_results" for item in events),
        "scope": "Official SEC filings cached for watchlist companies from the last 90 calendar days; recent means filed within seven days.",
    }


def _daily_briefing_from_db(
    db: sqlite3.Connection,
    user_id: str,
    *,
    alerts: dict[str, Any] | None = None,
    plan_center: dict[str, Any] | None = None,
    sec_events: dict[str, Any] | None = None,
) -> dict[str, Any]:
    screener = _watchlist_screener_from_db(db, user_id)
    alerts = alerts or _alert_center_from_db(db, user_id)
    plan_center = plan_center or _plan_review_center_from_db(db, user_id)
    sec_events = sec_events or _sec_event_center_from_db(db, user_id)
    tasks = []

    for item in screener["items"]:
        if item["segment"] == "risk":
            tasks.append(
                {
                    "id": f"decision-{item['symbol']}",
                    "category": "risk",
                    "severity": "critical" if item["signal"] == "sell_review" else "warning",
                    "symbol": item["symbol"],
                    "title": item["signal_label"],
                    "detail": f"Score {item['score']}/100; position is {item['account_percent']}% of paper account.",
                    "destination": "overview",
                }
            )
        elif item["segment"] == "opportunity":
            tasks.append(
                {
                    "id": f"opportunity-{item['symbol']}",
                    "category": "opportunity",
                    "severity": "opportunity",
                    "symbol": item["symbol"],
                    "title": item["signal_label"],
                    "detail": f"Score {item['score']}/100 with {item['state_label'].lower()}.",
                    "destination": "overview",
                }
            )
        elif item["segment"] == "data":
            age = (
                f"Latest bar is {item['data_age_days']} calendar days old."
                if item["data_age_days"] is not None
                else "No daily bars are cached."
            )
            tasks.append(
                {
                    "id": f"data-{item['symbol']}",
                    "category": "data",
                    "severity": "warning",
                    "symbol": item["symbol"],
                    "title": "Refresh market evidence",
                    "detail": age,
                    "destination": "overview",
                }
            )

    for event in sec_events["events"]:
        if not event["is_recent"]:
            continue
        tasks.append(
            {
                "id": f"sec-{event['id']}",
                "category": "filing",
                "severity": "warning" if event.get("priority") == "attention" else "info",
                "symbol": event["symbol"],
                "title": event["title"],
                "detail": f"{event['form']} filed {event['filed']}; report period {event['report_date'] or 'not supplied'}.",
                "destination": "overview",
            }
        )

    for rule in alerts["rules"]:
        if not rule["is_triggered"]:
            continue
        condition = "at or above" if rule["direction"] == "above" else "at or below"
        tasks.append(
            {
                "id": f"alert-{rule['id']}",
                "category": "alert",
                "severity": "critical",
                "symbol": rule["symbol"],
                "title": "Price threshold met",
                "detail": f"Cached close {rule['latest_price']} is {condition} {rule['threshold']}.",
                "destination": "overview",
            }
        )

    for item in plan_center["option_attention"]:
        if item["days_remaining"] > 7:
            continue
        days = "expired" if item["days_remaining"] < 0 else "expires today" if item["days_remaining"] == 0 else f"expires in {item['days_remaining']} days"
        tasks.append(
            {
                "id": f"option-{item['plan_id']}",
                "category": "options",
                "severity": "critical" if item["days_remaining"] <= 0 else "warning",
                "symbol": item["symbol"],
                "title": "Options plan needs review",
                "detail": f"{item['strategy'].replace('_', ' ')} {days}.",
                "destination": "options",
            }
        )

    if plan_center["awaiting_review"]:
        tasks.append(
            {
                "id": "plans-awaiting-review",
                "category": "review",
                "severity": "info",
                "symbol": None,
                "title": "Complete plan reviews",
                "detail": f"{plan_center['awaiting_review']} saved plan(s) have no recorded decision.",
                "destination": "journal",
            }
        )

    severity_order = {"critical": 0, "warning": 1, "opportunity": 2, "info": 3}
    tasks.sort(key=lambda item: (severity_order[item["severity"]], item["symbol"] or "", item["id"]))
    risk_count = sum(item["category"] in {"risk", "alert", "options"} for item in tasks)
    opportunity_count = sum(item["category"] == "opportunity" for item in tasks)
    data_issue_count = sum(item["category"] == "data" for item in tasks)
    filing_count = sum(item["category"] == "filing" for item in tasks)
    attention_count = len(tasks) - opportunity_count
    return {
        "generated_at": now_iso(),
        "headline": (
            "Review risk items first"
            if risk_count
            else "Review new SEC filings"
            if filing_count
            else "New candidates are ready"
            if opportunity_count
            else "Market evidence needs refresh"
            if data_issue_count
            else "Workspace is up to date"
        ),
        "summary": f"{attention_count} attention item(s), {opportunity_count} opportunity candidate(s).",
        "attention_count": attention_count,
        "risk_count": risk_count,
        "opportunity_count": opportunity_count,
        "data_issue_count": data_issue_count,
        "filing_count": filing_count,
        "tasks": tasks[:20],
        "scope": "Derived from cached end-of-day data, official SEC filings, saved thresholds, paper positions, and latest decision runs.",
    }


def _position_decision_context(
    db: sqlite3.Connection, user_id: str, symbol: str, profile: dict[str, Any]
) -> dict[str, Any]:
    position = next(
        (
            item for item in _portfolio_from_db(db, user_id)["positions"]
            if item["symbol"] == symbol and item["asset_type"] == "equity"
            and Decimal(item["quantity"]) > 0
        ),
        None,
    )
    risk_position = next(
        (
            item for item in _portfolio_risk_from_db(db, user_id)["positions"]
            if item["symbol"] == symbol and item["asset_type"] == "equity"
        ),
        None,
    )
    account_size = Decimal(profile["paper_account_size"])
    exposure = Decimal(risk_position["exposure"]) if risk_position else Decimal(0)
    account_percent = exposure * 100 / account_size if account_size else Decimal(0)
    maximum_percent = Decimal(profile["max_position_percent"])
    if not position:
        fit_score = 15
    elif account_percent <= maximum_percent * Decimal("0.75"):
        fit_score = 15
    elif account_percent <= maximum_percent:
        fit_score = 10
    elif account_percent <= maximum_percent * Decimal("1.25"):
        fit_score = 5
    else:
        fit_score = 0
    return {
        "has_position": position is not None,
        "quantity": position["quantity"] if position else "0",
        "exposure": decimal_string(int(exposure * SCALE)),
        "account_percent": _percent(account_percent),
        "fit_score": fit_score,
        "maximum_percent": profile["max_position_percent"],
    }


def _decision_signal(
    score: int, has_position: bool, below_sma50: bool, position_fit_score: int
) -> str:
    if has_position:
        if below_sma50 or score < 45:
            return "sell_review"
        if score < 60 or position_fit_score < 10:
            return "reduce"
        return "hold"
    if score >= 75:
        return "buy_candidate"
    if score >= 55:
        return "watch"
    return "avoid"


def generate_decision(path: Path, user_id: str, raw_symbol: Any) -> dict[str, Any]:
    symbol = normalize_symbol(raw_symbol)
    with open_db(path) as db:
        rows = _decision_market_rows(db, symbol)
        benchmark_rows = _decision_market_rows(db, "SPY")
        profile = _investor_profile_from_db(db, user_id)
        active_template = _active_strategy_template_from_db(db, user_id)
        position = _position_decision_context(db, user_id, symbol, profile)
        fundamentals = _sec_cached(db, f"fundamentals:{symbol}")
        fundamental_history = (
            list(fundamentals.get("annual_history") or [])
            if fundamentals and fundamentals.get("available") else []
        )
        fundamental_metrics = (
            dict(fundamentals.get("metrics") or {})
            if fundamentals and fundamentals.get("available") else None
        )
        fundamental_period = fundamental_history[-1] if fundamental_history else None
        latest_date = rows[-1]["trading_date"] if rows else None
        latest_close = int(rows[-1]["close_micros"]) if rows else None
        data_quality = _market_data_quality(
            rows,
            historically_adjusted=_adjusted_history_available(db, symbol, rows),
        )
        factor_set = _decision_factor_set(rows)
        scorecard = None
        quality = "complete"
        signal = "data_required"
        score: int | None = None
        reason = None
        if not rows or len(rows) < 60 or not factor_set:
            quality = "insufficient"
            reason = f"60 daily bars are required; {len(rows)} are cached."
        else:
            age_days = (date.today() - date.fromisoformat(str(latest_date))).days
            scorecard = _strategy_scorecard(
                profile["strategy_style"],
                profile["time_horizon"],
                factor_set,
                int(position["fit_score"]),
                fundamental_metrics,
                latest_close,
                fundamental_period,
                active_template,
            )
            score = int(scorecard["score"])
            if data_quality["blockers"]:
                quality = "blocked"
                signal = "refresh_required"
                reason = data_quality["blockers"][0]
            elif age_days > 7:
                quality = "stale"
                signal = "refresh_required"
                reason = f"Latest trading date is {age_days} calendar days old."
            else:
                signal = _decision_signal(
                    score,
                    bool(position["has_position"]),
                    bool(latest_close and latest_close < int(factor_set["sma_50_micros"])),
                    int(position["fit_score"]),
                )
                if not scorecard["strategy"]["fundamentals_available"]:
                    quality = "partial"
                    reason = "SEC fundamentals are not cached; this strategy is using the available technical and portfolio evidence."
                elif scorecard["strategy"]["fundamental_coverage_percent"] < 100:
                    quality = "partial"
                    reason = f"SEC factor coverage is {scorecard['strategy']['fundamental_coverage_percent']}% for this strategy."

        context = {
            "symbol": symbol,
            "model_version": DECISION_MODEL_VERSION,
            "strategy_freeze_protocol": STRATEGY_FREEZE_PROTOCOL,
            "trading_date": latest_date,
            "latest_close": latest_close,
            "profile_updated_at": profile["updated_at"],
            "strategy_style": profile["strategy_style"],
            "time_horizon": profile["time_horizon"],
            "quantity": position["quantity"],
            "exposure": position["exposure"],
            "fundamentals": fundamental_metrics,
            "fundamentals_period_end": fundamental_period.get("period_end") if fundamental_period else None,
            "strategy_template": active_template,
            "data_quality": {
                "status": data_quality["status"],
                "score": data_quality["score"],
                "decision_eligible": data_quality["decision_eligible"],
                "blockers": data_quality["blockers"],
                "latest_age_days": data_quality.get("latest_age_days"),
            },
        }
        context_hash = hashlib.sha256(
            json.dumps(context, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        existing = db.execute(
            "SELECT result_json FROM decision_runs WHERE user_id = ? AND symbol = ? "
            "AND model_version = ? AND context_hash = ?",
            (user_id, symbol, DECISION_MODEL_VERSION, context_hash),
        ).fetchone()
        if existing:
            return {**json.loads(existing["result_json"]), "reused": True}

        previous_row = db.execute(
            "SELECT result_json FROM decision_runs WHERE user_id = ? AND symbol = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (user_id, symbol),
        ).fetchone()
        previous = json.loads(previous_row["result_json"]) if previous_row else None
        labels = {
            "buy_candidate": "Buy candidate",
            "watch": "Watch / wait",
            "avoid": "Avoid for now",
            "hold": "Hold",
            "reduce": "Reduce exposure",
            "sell_review": "Sell / exit review",
            "data_required": "More data required",
            "refresh_required": "Refresh data",
        }
        factors = list(scorecard["factors"]) if scorecard else []
        if not scorecard:
            factors.append(
                {
                    "key": "portfolio_fit", "label": "Position-size fit",
                    "score": position["fit_score"], "max_score": 15,
                    "category": "portfolio", "value": "",
                }
            )
        for factor in factors:
            if factor["key"] == "portfolio_fit":
                factor["value"] = f"{position['account_percent']}% of paper account vs {position['maximum_percent']}% cap"
        account_size = Decimal(profile["paper_account_size"])
        maximum_position_value = account_size * Decimal(profile["max_position_percent"]) / 100
        current_exposure = Decimal(position["exposure"])
        risk_budget = account_size * Decimal(profile["risk_per_trade_percent"]) / 100
        recent_closes = [int(row["close_micros"]) for row in rows[-20:]]
        observed_range = {
            "low": decimal_string(min(recent_closes)) if recent_closes else None,
            "high": decimal_string(max(recent_closes)) if recent_closes else None,
            "label": "Observed 20-day close range; not a price target.",
        }
        below_sma50 = bool(
            factor_set and latest_close and latest_close < int(factor_set["sma_50_micros"])
        )
        evidence = []
        counter_evidence = []
        if factor_set:
            evidence.extend(
                [
                    "Price is above the 20-day average." if latest_close and latest_close > factor_set["sma_20_micros"] else "Price is not above the 20-day average.",
                    f"20-day momentum is {_percent(factor_set['momentum_percent'])}%.",
                    f"Latest volume is {_percent(factor_set['volume_percent'])}% of its 20-day average.",
                ]
            )
            if below_sma50:
                counter_evidence.append("Close is below the 50-day average.")
            if factor_set["volatility_percent"] > 35:
                counter_evidence.append("Annualized historical volatility is above 35%.")
            if factor_set["drawdown_percent"] < -10:
                counter_evidence.append("The recent close path has drawn down more than 10%.")
        if scorecard:
            for factor in factors:
                if factor.get("category") not in {"fundamental", "valuation"}:
                    continue
                statement = f"{factor['label']}: {factor['value']}."
                if factor.get("available") and int(factor["score"]) * 2 >= int(factor["max_score"]):
                    evidence.append(statement)
                else:
                    counter_evidence.append(statement)
            if scorecard["strategy"]["missing_fundamentals_redistributed"]:
                counter_evidence.append("SEC fundamentals are missing; their configured weight was reassigned to technical evidence for this run.")
        if position["fit_score"] < 10:
            counter_evidence.append("Current exposure is above the supplied maximum-position setting.")
        if reason:
            counter_evidence.insert(0, reason)

        created_at = now_iso()
        run_id = str(uuid4())
        signal_changed = bool(previous and previous["signal"] != signal)
        score_delta = score - previous["score"] if previous and score is not None and previous.get("score") is not None else None
        if signal_changed:
            change_summary = f"Changed from {previous['signal_label']} to {labels[signal]}."
        elif previous and score_delta:
            change_summary = f"Signal unchanged; score moved {score_delta:+d} points."
        elif previous:
            change_summary = "Signal and score are unchanged for the new input context."
        else:
            change_summary = "Initial recorded decision for this symbol."
        previous_factors = {
            str(item.get("key")): item for item in (previous.get("factors", []) if previous else [])
        }
        factor_changes = []
        for factor in factors:
            prior = previous_factors.get(str(factor.get("key")))
            if prior is None:
                continue
            current_score = int(factor.get("score") or 0)
            prior_score = int(prior.get("score") or 0)
            delta = current_score - prior_score
            if delta:
                factor_changes.append(
                    {
                        "key": factor["key"], "label": factor["label"],
                        "previous_score": prior_score, "current_score": current_score,
                        "score_delta": delta,
                        "direction": "improved" if delta > 0 else "weakened",
                    }
                )
        factor_changes.sort(key=lambda item: abs(item["score_delta"]), reverse=True)
        changed_inputs = []
        if previous:
            if previous.get("trading_date") != latest_date:
                changed_inputs.append("New daily market bar")
            if previous.get("strategy", {}).get("template_id") != (
                active_template.get("id") if active_template else None
            ):
                changed_inputs.append("Strategy template")
            if previous.get("strategy", {}).get("fundamentals_period_end") != (
                fundamental_period.get("period_end") if fundamental_period else None
            ):
                changed_inputs.append("SEC filing period")
            if previous.get("position") != position:
                changed_inputs.append("Paper position")
        invalidation = (
            f"Review an exit if a daily close is below SMA50 ({decimal_string(factor_set['sma_50_micros'])}) or the score falls below 45."
            if position["has_position"] and factor_set else
            f"Invalidate the buy case if a daily close is below SMA50 ({decimal_string(factor_set['sma_50_micros'])}) or the score falls below 55."
            if factor_set else "Refresh enough current daily bars before acting on this result."
        )
        price_plan = _decision_price_plan(rows, factor_set, profile, signal)
        strategy_output = scorecard["strategy"] if scorecard else {
            "style": profile["strategy_style"],
            "label": STRATEGY_DEFINITIONS[profile["strategy_style"]]["label"],
            "horizon": profile["time_horizon"],
            "technical_weight": 0,
            "fundamental_weight": 0,
            "valuation_weight": 0,
            "portfolio_weight": 15,
            "fundamentals_available": fundamental_metrics is not None,
            "fundamental_coverage_percent": 0,
            "fundamentals_period_end": fundamental_period.get("period_end") if fundamental_period else None,
            "fundamentals_filed_at": fundamental_period.get("filed") if fundamental_period else None,
            "missing_fundamentals_redistributed": False,
            "horizon_note": "Refresh enough daily bars to calculate strategy weights.",
        }
        strategy_output = {
            **strategy_output,
            "freeze_protocol": STRATEGY_FREEZE_PROTOCOL,
            "origin": (
                "Investor Lab transparent multi-factor rules; not copied from a single advisory model "
                "and not generated by machine learning."
            ),
            "decision_rules": [
                "At least 60 current daily bars are required.",
                "No position: score 75-100 Buy candidate; 55-74 Watch; below 55 Avoid.",
                (
                    "Held position: below SMA50 or score below 45 Sell / exit review; score below 60 "
                    "or position-fit below 10 Reduce; otherwise Hold."
                ),
                "Price targets are separate ATR and reward/risk scenarios; they do not raise the decision score.",
            ],
            "data_sources": [
                "Alpha Vantage cached end-of-day OHLCV",
                "SEC EDGAR company-reported facts when cached",
                "Your local paper portfolio and planning limits",
            ],
            "price_plan_method": (
                "14-day ATR, SMA20/SMA50, prior 20-session high, and your saved minimum reward/risk."
            ),
        }
        strategy_frozen_at = _strategy_context_frozen_at(
            _decision_rows(db, user_id, limit=1_000_000),
            DECISION_MODEL_VERSION,
            profile["strategy_style"],
            profile["time_horizon"],
            strategy_output,
        ) or created_at
        strategy_output["frozen_at"] = strategy_frozen_at
        result = {
            "id": run_id,
            "symbol": symbol,
            "model_version": DECISION_MODEL_VERSION,
            "signal": signal,
            "signal_label": labels[signal],
            "score": score,
            "quality": quality,
            "quality_reason": reason,
            "data_quality": data_quality,
            "trading_date": latest_date,
            "latest_close": decimal_string(latest_close) if latest_close else None,
            "valid_through": (
                (date.fromisoformat(str(latest_date)) + timedelta(days=7)).isoformat()
                if latest_date else None
            ),
            "has_position": position["has_position"],
            "position": position,
            "factors": factors,
            "evidence": evidence,
            "counter_evidence": counter_evidence,
            "observed_range": observed_range,
            "risk_plan": {
                "paper_account_size": profile["paper_account_size"],
                "risk_budget": format(risk_budget.normalize(), "f"),
                "maximum_position_value": format(maximum_position_value.normalize(), "f"),
                "remaining_position_capacity": format(max(Decimal(0), maximum_position_value - current_exposure).normalize(), "f"),
                "note": "Share sizing can use the price plan's reference risk stop; review it before any paper trade.",
            },
            "price_plan": price_plan,
            "invalidation": invalidation,
            "backtest": _walk_forward_backtest(
                rows,
                profile["strategy_style"],
                profile["time_horizon"],
                fundamental_history,
                int(active_template["fee_slippage_bps"]) if active_template else 10,
                active_template,
                benchmark_rows,
                strategy_frozen_at=strategy_frozen_at,
            ),
            "strategy": strategy_output,
            "profile": {
                "strategy_style": profile["strategy_style"],
                "time_horizon": profile["time_horizon"],
            },
            "change": {
                "signal_changed": signal_changed,
                "previous_signal": previous["signal"] if previous else None,
                "previous_score": previous["score"] if previous else None,
                "score_delta": score_delta,
                "summary": change_summary,
                "factor_changes": factor_changes[:8],
                "changed_inputs": changed_inputs,
                "explanation": (
                    "; ".join(
                        f"{item['label']} {item['score_delta']:+d}" for item in factor_changes[:3]
                    ) or (", ".join(changed_inputs) if changed_inputs else "No measured factor changed.")
                ),
            },
            "created_at": created_at,
            "disclaimer": "Personal research output from transparent end-of-day rules; review the evidence and risk limits before any trade.",
        }
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO decision_runs(id, user_id, symbol, model_version, context_hash, "
            "signal, score, result_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                user_id,
                symbol,
                DECISION_MODEL_VERSION,
                context_hash,
                signal,
                score,
                json.dumps(result),
                created_at,
            ),
        )
        _append_sync_event(
            db,
            user_id,
            "decision_run",
            run_id,
            "upsert",
            {
                "symbol": symbol,
                "signal": signal,
                "score": score,
                "signal_changed": signal_changed,
                "model_version": DECISION_MODEL_VERSION,
                "created_at": created_at,
            },
        )
    return result


def decision_bundle(path: Path, user_id: str, raw_symbol: Any) -> dict[str, Any]:
    symbol = normalize_symbol(raw_symbol)
    with open_db(path) as db:
        rows = _decision_market_rows(db, symbol)
        benchmark_rows = _decision_market_rows(db, "SPY")
        history = _decision_rows(db, user_id, symbol, 30)
        profile = _investor_profile_from_db(db, user_id)
        active_template = _active_strategy_template_from_db(db, user_id)
        context_runs = _decision_rows(db, user_id, limit=1_000_000)
        fundamentals = _sec_cached(db, f"fundamentals:{symbol}")
        fundamental_history = (
            list(fundamentals.get("annual_history") or [])
            if fundamentals and fundamentals.get("available") else []
        )
        backtest = _walk_forward_backtest(
            rows,
            profile["strategy_style"],
            profile["time_horizon"],
            fundamental_history,
            int(active_template["fee_slippage_bps"]) if active_template else 10,
            active_template,
            benchmark_rows,
            strategy_frozen_at=_strategy_context_frozen_at(
                context_runs,
                DECISION_MODEL_VERSION,
                profile["strategy_style"],
                profile["time_horizon"],
                active_template,
            ),
        )
        return {
            "symbol": symbol,
            "latest": history[0] if history else None,
            "history": history,
            "backtest": backtest,
            "validation": _strategy_validation_from_db(db, user_id, symbol, backtest),
        }


def refresh_watchlist_decisions(path: Path, user_id: str) -> dict[str, Any]:
    api_key, _ = _alpha_vantage_api_key()
    if not api_key:
        raise ApiError(503, "Save an Alpha Vantage key before refreshing watchlist decisions.")
    with open_db(path) as db:
        symbols = [item["symbol"] for item in _watchlist_rows(db, user_id)]
        user = db.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise ApiError(404, "Account was not found.")
    results = []
    errors = []
    market_refreshed = 0
    fundamentals_refreshed = 0
    try:
        cache_minutes = int(os.environ.get("INVESTORLAB_MARKET_CACHE_MINUTES", "720"))
    except ValueError:
        raise ApiError(500, "INVESTORLAB_MARKET_CACHE_MINUTES must be an integer.") from None
    for symbol in symbols:
        symbol_errors = []
        try:
            refresh_market(path, symbol, api_key, cache_minutes)
            market_refreshed += 1
        except (ApiError, InputError) as error:
            symbol_errors.append({"component": "market", "error": str(error)})
        try:
            refresh_fundamentals(path, symbol, user["email"])
            fundamentals_refreshed += 1
        except (ApiError, InputError) as error:
            symbol_errors.append({"component": "fundamentals", "error": str(error)})
        try:
            results.append(generate_decision(path, user_id, symbol))
        except (ApiError, InputError) as error:
            symbol_errors.append({"component": "decision", "error": str(error)})
        if symbol_errors:
            errors.append({"symbol": symbol, "components": symbol_errors})
    attempted_at = now_iso()
    with open_db(path) as db:
        db.execute(
            "UPDATE decision_settings SET last_refresh_at = ? WHERE user_id = ?",
            (attempted_at, user_id),
        )
    calendar_error = None
    try:
        calendar = refresh_earnings_calendar(path, user_id, api_key)
    except ApiError as error:
        calendar = earnings_calendar(path, user_id)
        calendar_error = str(error)
    return {
        "attempted_at": attempted_at,
        "symbols": len(symbols),
        "completed": len(results),
        "failed": len(errors),
        "market_refreshed": market_refreshed,
        "fundamentals_refreshed": fundamentals_refreshed,
        "earnings_calendar": calendar,
        "calendar_error": calendar_error,
        "decisions": results,
        "errors": errors,
    }


def run_scheduled_decision_refreshes(path: Path) -> list[dict[str, Any]]:
    current = datetime.now(timezone.utc)
    with open_db(path) as db:
        rows = db.execute(
            "SELECT user_id, refresh_interval_hours, last_refresh_at, updated_at "
            "FROM decision_settings WHERE auto_refresh_enabled = 1"
        ).fetchall()
    results = []
    for row in rows:
        reference = row["last_refresh_at"] or row["updated_at"]
        last = datetime.fromisoformat(str(reference).replace("Z", "+00:00"))
        if current - last < timedelta(hours=int(row["refresh_interval_hours"])):
            continue
        with open_db(path) as db:
            requested = int(db.execute(
                "SELECT COUNT(*) FROM watchlist WHERE user_id = ?", (row["user_id"],)
            ).fetchone()[0])
        run_id = _start_collection_run(path, row["user_id"], "watchlist_refresh", requested)
        try:
            result = refresh_watchlist_decisions(path, row["user_id"])
            status = "completed" if not result.get("failed") else "partial"
            _finish_collection_run(path, run_id, status, int(result.get("completed") or 0), result)
            results.append(result)
        except (ApiError, InputError) as error:
            _finish_collection_run(path, run_id, "failed", 0, error=str(error))
            results.append({"user_id": row["user_id"], "error": str(error)})
    return results


def run_scheduled_intraday_collection(path: Path) -> list[dict[str, Any]]:
    if os.environ.get("INVESTORLAB_INTRADAY_COLLECTION") != "1":
        return []
    key_id, secret, _ = _alpaca_credentials()
    if not key_id or not secret:
        return []
    clock = market_clock(path)
    if clock.get("session_phase") not in {"premarket", "regular"}:
        return []
    try:
        maximum = int(os.environ.get("INVESTORLAB_INTRADAY_SYMBOL_LIMIT", "12"))
    except ValueError:
        raise ApiError(500, "INVESTORLAB_INTRADAY_SYMBOL_LIMIT must be an integer.") from None
    maximum = max(1, min(maximum, 30))
    with open_db(path) as db:
        users = db.execute("SELECT id FROM users ORDER BY created_at").fetchall()
    results = []
    for user in users:
        user_id = str(user["id"])
        with open_db(path) as db:
            symbols = [
                str(row["symbol"]) for row in db.execute(
                    "SELECT symbol FROM watchlist WHERE user_id = ? ORDER BY created_at LIMIT ?",
                    (user_id, maximum),
                ).fetchall()
            ]
        if not symbols:
            continue
        run_id = _start_collection_run(path, user_id, "intraday_scan", len(symbols))
        completed = []
        errors = []
        for symbol in symbols:
            try:
                plan = realtime_day_trade_plan(path, symbol, user_id, clock)
                if plan.get("available"):
                    completed.append({"symbol": symbol, "available": True})
                else:
                    errors.append({
                        "symbol": symbol,
                        "error": str(plan.get("reason") or "No intraday evidence was returned."),
                    })
            except (ApiError, InputError) as error:
                errors.append({"symbol": symbol, "error": str(error)})
        result = {"symbols": completed, "errors": errors}
        status = "completed" if not errors else "partial" if completed else "failed"
        _finish_collection_run(path, run_id, status, len(completed), result, errors[0]["error"] if errors and not completed else "")
        results.append({"user_id": user_id, **result})
    return results


def run_scheduled_option_collection(path: Path) -> list[dict[str, Any]]:
    if os.environ.get("INVESTORLAB_OPTION_COLLECTION", "1") != "1":
        return []
    key_id, secret, _ = _alpaca_credentials()
    if not key_id or not secret:
        return []
    now_ny = datetime.now(timezone.utc).astimezone(NEW_YORK)
    if now_ny.weekday() >= 5 or not (9, 35) <= (now_ny.hour, now_ny.minute) < (16, 5):
        return []
    local_midnight = now_ny.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = local_midnight.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with open_db(path) as db:
        users = db.execute("SELECT id FROM users ORDER BY created_at").fetchall()
    results = []
    for user in users:
        user_id = str(user["id"])
        with open_db(path) as db:
            symbols = [
                str(row["symbol"])
                for row in db.execute(
                    "SELECT symbol FROM watchlist WHERE user_id = ? "
                    "AND symbol NOT IN (SELECT symbol FROM option_chain_snapshots "
                    "WHERE user_id = ? AND fetched_at >= ?) ORDER BY created_at, symbol LIMIT 1",
                    (user_id, user_id, cutoff),
                ).fetchall()
            ]
        if not symbols:
            continue
        symbol = symbols[0]
        run_id = _start_collection_run(path, user_id, "option_chain_scan", 1)
        try:
            chain = option_chain(path, user_id, symbol, _option_filters({}))
            available = bool(chain.get("available"))
            result = {
                "symbol": symbol, "available": available,
                "contracts": int((chain.get("summary") or {}).get("contracts") or 0),
                "reason": chain.get("reason"),
            }
            _finish_collection_run(
                path, run_id, "completed" if available else "partial",
                1 if available else 0, result,
                "" if available else str(chain.get("reason") or "No option contracts were returned."),
            )
            results.append(result)
        except (ApiError, InputError) as error:
            _finish_collection_run(path, run_id, "failed", 0, error=str(error))
            results.append({"symbol": symbol, "available": False, "error": str(error)})
    return results


def run_scheduled_reports(path: Path) -> list[dict[str, Any]]:
    today = date.today()
    report_dates = {
        "daily": today.isoformat(),
        "weekly": (today - timedelta(days=today.weekday())).isoformat(),
    }
    with open_db(path) as db:
        users = db.execute("SELECT id FROM users ORDER BY created_at").fetchall()
    results = []
    for user in users:
        user_id = str(user["id"])
        with open_db(path) as db:
            existing = {
                (str(row["period"]), str(row["report_date"]))
                for row in db.execute(
                    "SELECT period, report_date FROM research_reports WHERE user_id = ?",
                    (user_id,),
                ).fetchall()
            }
        missing = [
            period for period, report_date in report_dates.items()
            if (period, report_date) not in existing
        ]
        if not missing:
            continue
        run_id = _start_collection_run(path, user_id, "research_report", len(missing))
        generated = []
        try:
            for period in missing:
                report = generate_research_report(path, user_id, period)
                generated.append({
                    "period": period, "report_date": report["report_date"], "id": report["id"]
                })
            _finish_collection_run(
                path, run_id, "completed", len(generated), {"reports": generated}
            )
            results.extend(generated)
        except Exception as error:
            _finish_collection_run(
                path, run_id, "partial" if generated else "failed", len(generated),
                {"reports": generated}, str(error),
            )
            raise
    return results


def run_validation_cycle(path: Path, user_id: str) -> dict[str, Any]:
    run_id = _start_collection_run(path, user_id, "validation_cycle", 6)
    completed = []
    blocked = []
    try:
        alpha_key, _ = _alpha_vantage_api_key()
        if alpha_key:
            result = refresh_watchlist_decisions(path, user_id)
            completed.append({
                "component": "daily_decisions", "completed": result["completed"],
                "failed": result["failed"],
            })
        else:
            blocked.append({
                "component": "daily_decisions",
                "error": "Save an Alpha Vantage key before running the daily decision cycle.",
            })
        alpaca_key, alpaca_secret, _ = _alpaca_credentials()
        if alpaca_key and alpaca_secret:
            intraday = run_scheduled_intraday_collection(path)
            options = run_scheduled_option_collection(path)
            completed.append({"component": "intraday", "runs": len(intraday)})
            completed.append({"component": "options", "runs": len(options)})
        else:
            blocked.append({
                "component": "intraday_options",
                "error": "Save Alpaca Paper/IEX credentials before collecting intraday and option evidence.",
            })
        backup = run_scheduled_backup(path)
        completed.append({
            "component": "backup",
            "result": backup["filename"] if backup else "already completed today",
        })
        daily_report = generate_research_report(path, user_id, "daily")
        weekly_report = generate_research_report(path, user_id, "weekly")
        completed.append({
            "component": "reports",
            "daily": daily_report["report_date"], "weekly": weekly_report["report_date"],
        })
        health = run_system_health_check(path, user_id)
        completed.append({"component": "health", "status": health["status"]})
        dashboard = validation_dashboard(path, user_id, 60)
        status = "completed" if not blocked else "partial"
        result = {
            "status": status, "completed": completed, "blocked": blocked,
            "dashboard": dashboard, "notifications": notification_center(path, user_id),
            "completed_at": now_iso(),
        }
        _finish_collection_run(path, run_id, status, len(completed), result)
        return result
    except Exception as error:
        _finish_collection_run(
            path, run_id, "partial" if completed else "failed", len(completed),
            {"completed": completed, "blocked": blocked}, str(error),
        )
        raise


def run_scheduled_health_checks(path: Path) -> list[dict[str, Any]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(timespec="seconds").replace("+00:00", "Z")
    with open_db(path) as db:
        users = db.execute("SELECT id FROM users ORDER BY created_at").fetchall()
    results = []
    for user in users:
        user_id = str(user["id"])
        with open_db(path) as db:
            recent = db.execute(
                "SELECT 1 FROM data_collection_runs WHERE user_id = ? AND job_type = 'health_check' "
                "AND started_at >= ? LIMIT 1",
                (user_id, cutoff),
            ).fetchone()
        if not recent:
            results.append(run_system_health_check(path, user_id))
    return results


def _decision_scheduler_loop(path: Path, stop: threading.Event) -> None:
    SCHEDULER_STATE.update({
        "running": True, "started_at": now_iso(), "last_cycle_at": None, "last_error": None,
    })
    tasks = (
        run_scheduled_decision_refreshes,
        run_scheduled_intraday_collection,
        run_scheduled_option_collection,
        run_scheduled_backup,
        run_scheduled_reports,
        run_scheduled_health_checks,
    )
    try:
        if stop.wait(5):
            return
        while not stop.is_set():
            errors = []
            for task in tasks:
                try:
                    task(path)
                except Exception as error:
                    message = f"{task.__name__}: {error}"
                    errors.append(message)
                    print(f"Investor Lab scheduler error: {message}", file=sys.stderr, flush=True)
            SCHEDULER_STATE["last_cycle_at"] = now_iso()
            SCHEDULER_STATE["last_error"] = "; ".join(errors) or None
            if stop.wait(900):
                return
    finally:
        SCHEDULER_STATE["running"] = False


def market_research(path: Path, raw_symbol: Any) -> dict[str, Any]:
    symbol = normalize_symbol(raw_symbol)
    with open_db(path) as db:
        research = _market_research_from_db(db, symbol)
    try:
        quote = realtime_quote(path, symbol)
    except ApiError as error:
        quote = {
            "available": False,
            "configured": True,
            "symbol": symbol,
            "provider": "Alpaca Market Data",
            "feed": "iex",
            "reason": str(error),
        }
    source_check = compare_prices(
        research.get("latest_close"), quote.get("latest_price")
    )
    if isinstance(research.get("data_quality"), dict):
        quality = {**research["data_quality"], "cross_source_price": source_check}
        if source_check["status"] == "warning":
            quality["warnings"] = [
                *quality.get("warnings", []),
                "The latest IEX observation differs by more than 3% from the cached end-of-day close; confirm session timing and source scope.",
            ]
        research = {**research, "data_quality": quality}
    return {**research, "realtime_quote": quote}


def refresh_market(path: Path, raw_symbol: Any, api_key: str, cache_minutes: int = 720) -> dict[str, Any]:
    symbol = normalize_symbol(raw_symbol)
    if not api_key:
        raise ApiError(503, "Save an Alpha Vantage key in Settings or configure it on the server before refreshing market data.")
    with open_db(path) as db:
        row = db.execute(
            "SELECT MAX(fetched_at) AS fetched_at FROM market_daily "
            "WHERE symbol = ? AND source = 'alpha_vantage'",
            (symbol,),
        ).fetchone()
    if row["fetched_at"]:
        fetched_at = datetime.fromisoformat(row["fetched_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - fetched_at < timedelta(minutes=cache_minutes):
            return {**market_research(path, symbol), "cache_hit": True}

    adjustment_rows: list[tuple[Any, ...]] = []
    if os.environ.get("INVESTORLAB_ADJUSTED_DAILY") == "1":
        rows, adjustment_rows = _alpha_vantage_adjustments(symbol, api_key)
    else:
        rows = _alpha_vantage_daily(symbol, api_key)
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "DELETE FROM market_daily WHERE symbol = ? AND source = 'alpha_vantage'",
            (symbol,),
        )
        db.execute(
            "DELETE FROM market_adjustments WHERE symbol = ? AND source = 'alpha_vantage'",
            (symbol,),
        )
        db.executemany(
            "INSERT INTO market_daily(symbol, trading_date, open_micros, high_micros, "
            "low_micros, close_micros, volume, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol, trading_date, source) DO UPDATE SET "
            "open_micros=excluded.open_micros, high_micros=excluded.high_micros, "
            "low_micros=excluded.low_micros, close_micros=excluded.close_micros, "
            "volume=excluded.volume, fetched_at=excluded.fetched_at",
            rows,
        )
        if adjustment_rows:
            db.executemany(
                "INSERT INTO market_adjustments(symbol, trading_date, adjusted_close_micros, "
                "dividend_micros, split_coefficient_micros, source, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(symbol, trading_date, source) DO UPDATE SET "
                "adjusted_close_micros=excluded.adjusted_close_micros, dividend_micros=excluded.dividend_micros, "
                "split_coefficient_micros=excluded.split_coefficient_micros, fetched_at=excluded.fetched_at",
                adjustment_rows,
            )
    return {
        **market_research(path, symbol),
        "cache_hit": False,
        "adjusted_rows": len(adjustment_rows),
        "history_mode": os.environ.get("INVESTORLAB_MARKET_HISTORY", "compact"),
    }


def make_handler(db_path: Path, web_root: Path) -> type[BaseHTTPRequestHandler]:
    request_rate_limiter = RequestRateLimiter()

    class InvestorLabHandler(BaseHTTPRequestHandler):
        server_version = "InvestorLab/1.0"

        def log_message(self, format_string: str, *args: Any) -> None:
            print(f"{self.address_string()} - {format_string % args}")

        def _security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header(
                "Content-Security-Policy",
                CONTENT_SECURITY_POLICY,
            )

        def _client_address(self) -> str:
            return client_address(
                self.client_address[0],
                self.headers,
                trust_proxy=os.environ.get("INVESTORLAB_TRUST_PROXY") == "1",
            )

        def _enforce_rate_limit(self, route: str, method: str) -> None:
            if route in {"/", "/api/health", "/api/contract"} or route in STATIC_ASSETS:
                limit, window = 240, 60
            elif route in {"/api/auth/register", "/api/auth/login"}:
                limit, window = 20, 15 * 60
            elif route.startswith("/api/alpaca/paper-orders") or route == "/api/system/restore":
                limit, window = 10, 60
            elif method in {"POST", "PATCH", "DELETE"}:
                limit, window = 60, 60
            else:
                limit, window = 300, 60
            address = self._client_address()
            key = identity_hash("request-rate", f"{address}:{method}:{route}")
            allowed, retry_after = request_rate_limiter.check(key, limit, window)
            if not allowed:
                append_security_event(
                    db_path,
                    "request_rate_limit",
                    "blocked",
                    address=address,
                    details={"method": method, "route": route, "retry_after": retry_after},
                )
                raise ApiError(
                    429,
                    "Too many requests for this endpoint. Try again later.",
                    {"Retry-After": str(retry_after)},
                )

        def _access_gateway_email(self) -> str | None:
            if os.environ.get("INVESTORLAB_ACCESS_GATEWAY", "").lower() != "cloudflare":
                return None
            if os.environ.get("INVESTORLAB_TRUST_PROXY") != "1":
                raise ApiError(500, "Cloudflare Access requires INVESTORLAB_TRUST_PROXY=1.")
            if self.headers.get("X-Forwarded-Proto", "").lower() != "https":
                raise ApiError(401, "Authenticated HTTPS gateway required.")
            if not self.headers.get("Cf-Access-Jwt-Assertion", "").strip():
                raise ApiError(401, "Cloudflare Access assertion required.")
            email = self.headers.get("Cf-Access-Authenticated-User-Email", "").strip().lower()
            if not email:
                raise ApiError(401, "Cloudflare Access identity required.")
            return email

        def _send_json(
            self, status: int, value: Any, extra_headers: dict[str, str] | None = None
        ) -> None:
            body = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            for name, header_value in (extra_headers or {}).items():
                self.send_header(name, header_value)
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise InputError("Invalid Content-Length.") from None
            if length <= 0 or length > 1_000_000:
                raise InputError("Request body must be between 1 byte and 1 MB.")
            try:
                payload = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise InputError("Request body must be valid JSON.") from None
            if not isinstance(payload, dict):
                raise InputError("Request body must be a JSON object.")
            return payload

        def _send_file(
            self,
            path: Path,
            content_type: str,
            missing_message: str,
            cache_control: str,
        ) -> None:
            if not path.is_file():
                self._send_json(404, {"error": missing_message})
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control)
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, path: Path, missing_message: str) -> None:
            self._send_file(
                path,
                "text/html; charset=utf-8",
                missing_message,
                "no-cache",
            )

        def _send_day_trade_stream(self, user_id: str, limit: int, max_events: int) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self._security_headers()
            self.end_headers()
            for _ in range(max_events):
                try:
                    body = json.dumps(
                        day_trade_scanner(db_path, user_id, limit), separators=(",", ":")
                    )
                    self.wfile.write(f"event: scanner\ndata: {body}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                except Exception as error:
                    try:
                        body = json.dumps({"error": str(error)}, separators=(",", ":"))
                        self.wfile.write(f"event: error\ndata: {body}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    break
                if max_events > 1:
                    threading.Event().wait(20)
            self.close_connection = True

        def _route(self) -> str:
            return urlparse(self.path).path.rstrip("/") or "/"

        def _session_cookie(self, token: str, max_age: int) -> str:
            cookie = SimpleCookie()
            cookie["investorlab_session"] = token
            morsel = cookie["investorlab_session"]
            morsel["path"] = "/"
            morsel["httponly"] = True
            morsel["samesite"] = "Strict"
            morsel["max-age"] = str(max_age)
            if os.environ.get("INVESTORLAB_SECURE_COOKIE") == "1":
                morsel["secure"] = True
            return morsel.OutputString()

        def _token_from_request(self) -> tuple[str, bool]:
            authorization = self.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                return authorization[7:].strip(), True
            raw_cookie = self.headers.get("Cookie", "")
            try:
                cookie = SimpleCookie()
                cookie.load(raw_cookie)
            except CookieError:
                raise ApiError(401, "Authentication required.") from None
            morsel = cookie.get("investorlab_session")
            return (morsel.value if morsel else ""), False

        def _require_auth(
            self, require_csrf: bool = False
        ) -> tuple[dict[str, str], str, str, bool]:
            token, is_bearer = self._token_from_request()
            user, csrf_token, token_hash, client_type = authenticate_session(db_path, token)
            if is_bearer != (client_type == "ios"):
                raise ApiError(401, "Session transport does not match the authenticated client.")
            gateway_email = self._access_gateway_email()
            if gateway_email and not hmac.compare_digest(gateway_email, user["email"].lower()):
                raise ApiError(403, "Gateway identity does not match the signed-in account.")
            if require_csrf and client_type == "web":
                supplied = self.headers.get("X-CSRF-Token", "")
                if not supplied or not hmac.compare_digest(supplied, csrf_token):
                    raise ApiError(403, "Invalid CSRF token.")
            return user, csrf_token, token_hash, is_bearer

        def _auth_response(
            self, status: int, user: dict[str, str], session: dict[str, str], client: str
        ) -> None:
            response: dict[str, Any] = {
                "user": user,
                "csrf_token": session["csrf_token"],
                "expires_at": session["expires_at"],
                "revision": snapshot(db_path, user["id"])["revision"],
            }
            if session.get("security_notice"):
                response["security_notice"] = session["security_notice"]
                response["unusual_login"] = bool(session.get("unusual_login"))
            headers = None
            if client == "ios":
                response["access_token"] = session["access_token"]
            else:
                headers = {
                    "Set-Cookie": self._session_cookie(
                        session["access_token"], SESSION_DAYS * 24 * 60 * 60
                    )
                }
            self._send_json(status, response, headers)

        def _handle_error(self, error: Exception) -> None:
            if isinstance(error, ApiError):
                self._send_json(error.status, {"error": str(error)}, error.headers)
            elif isinstance(error, InputError):
                self._send_json(400, {"error": str(error)})
            else:
                self.log_error("Unhandled request error: %s", error)
                self._send_json(500, {"error": "Internal server error."})

        def do_GET(self) -> None:
            try:
                route = self._route()
                self._enforce_rate_limit(route, "GET")
                if route == "/api/health":
                    self._send_json(
                        200,
                        {
                            "status": "ok",
                            "mode": "local",
                            "app_version": APP_VERSION,
                            "schema_version": SCHEMA_VERSION,
                            "api_contract_version": API_CONTRACT_VERSION,
                            "auth_required": True,
                        },
                    )
                elif route == "/api/contract":
                    self._send_json(200, contract_document())
                elif route == "/":
                    self._send_html(web_root / "index.html", "Web app is missing.")
                elif route in {"/design-system", "/design-system.html"}:
                    self._send_html(
                        web_root / "design-system.html",
                        "Design-system preview is missing.",
                    )
                elif route in STATIC_ASSETS:
                    filename, content_type = STATIC_ASSETS[route]
                    self._send_file(
                        web_root / filename,
                        content_type,
                        "Static asset is missing.",
                        "public, max-age=300",
                    )
                elif route == "/api/auth/session":
                    user, csrf_token, _, _ = self._require_auth()
                    self._send_json(
                        200,
                        {
                            "user": user,
                            "csrf_token": csrf_token,
                            "revision": snapshot(db_path, user["id"])["revision"],
                        },
                    )
                else:
                    user, _, _, _ = self._require_auth()
                    user_id = user["id"]
                    if route == "/api/watchlist":
                        self._send_json(200, list_watchlist(db_path, user_id))
                    elif route == "/api/trades":
                        self._send_json(200, list_trades(db_path, user_id))
                    elif route == "/api/portfolio":
                        self._send_json(200, portfolio(db_path, user_id))
                    elif route == "/api/portfolio/risk":
                        self._send_json(200, portfolio_risk(db_path, user_id))
                    elif route == "/api/portfolio/actions":
                        with open_db(db_path) as db:
                            self._send_json(200, _portfolio_actions_from_db(db, user_id))
                    elif route == "/api/strategy-templates":
                        self._send_json(200, list_strategy_templates(db_path, user_id))
                    elif route == "/api/strategy-versions":
                        query = parse_qs(urlparse(self.path).query)
                        self._send_json(
                            200,
                            list_strategy_versions(
                                db_path, user_id, query.get("template_id", [None])[0]
                            ),
                        )
                    elif route == "/api/day-trade/guardrails":
                        with open_db(db_path) as db:
                            self._send_json(200, _day_trade_guardrails_from_db(db, user_id))
                    elif route == "/api/day-trade/clock":
                        self._send_json(200, market_clock(db_path))
                    elif route == "/api/day-trade/scanner":
                        query = parse_qs(urlparse(self.path).query)
                        try:
                            scanner_limit = int(query.get("limit", ["12"])[0])
                        except ValueError:
                            raise InputError("Scanner limit must be an integer.") from None
                        self._send_json(200, day_trade_scanner(db_path, user_id, scanner_limit))
                    elif route == "/api/day-trade/stream":
                        query = parse_qs(urlparse(self.path).query)
                        try:
                            scanner_limit = max(1, min(int(query.get("limit", ["12"])[0]), 30))
                            max_events = max(1, min(int(query.get("max_events", ["30"])[0]), 120))
                        except ValueError:
                            raise InputError("Stream limit and max_events must be integers.") from None
                        self._send_day_trade_stream(user_id, scanner_limit, max_events)
                    elif route.startswith("/api/day-trade/replay/"):
                        symbol = unquote(route.removeprefix("/api/day-trade/replay/"))
                        query = parse_qs(urlparse(self.path).query)
                        self._send_json(
                            200,
                            day_trade_session_replay(
                                db_path, symbol, query.get("date", [None])[0]
                            ),
                        )
                    elif route.startswith("/api/day-trade/live/"):
                        symbol = unquote(route.removeprefix("/api/day-trade/live/"))
                        self._send_json(
                            200,
                            realtime_day_trade_plan(
                                db_path, symbol, user_id, market_clock(db_path)
                            ),
                        )
                    elif route.startswith("/api/options/chain/"):
                        symbol = unquote(route.removeprefix("/api/options/chain/"))
                        query = parse_qs(urlparse(self.path).query)
                        self._send_json(200, option_chain(db_path, user_id, symbol, _option_filters(query)))
                    elif route == "/api/validation/dashboard":
                        query = parse_qs(urlparse(self.path).query)
                        try:
                            window_days = int(query.get("window_days", ["60"])[0])
                        except ValueError:
                            raise InputError("Validation window must be an integer.") from None
                        self._send_json(200, validation_dashboard(db_path, user_id, window_days))
                    elif route == "/api/validation/report":
                        self._send_json(200, validation_report(db_path, user_id))
                    elif route == "/api/snapshot":
                        self._send_json(200, snapshot(db_path, user_id))
                    elif route == "/api/investor-profile":
                        self._send_json(200, investor_profile(db_path, user_id))
                    elif route == "/api/devices":
                        self._send_json(200, list_devices(db_path, user_id))
                    elif route == "/api/imports":
                        self._send_json(200, list_portfolio_imports(db_path, user_id))
                    elif route == "/api/system/health":
                        self._send_json(200, system_health(db_path, user_id))
                    elif route == "/api/system/backups":
                        require_owner(user)
                        self._send_json(200, list_database_backups(db_path))
                    elif route == "/api/alpaca/paper-account":
                        require_owner(user)
                        self._send_json(200, paper_account(db_path, user_id))
                    elif route == "/api/alpaca/paper-orders/control":
                        require_owner(user)
                        self._send_json(200, paper_order_control(db_path, user_id))
                    elif route == "/api/alpaca/paper-orders":
                        require_owner(user)
                        query = parse_qs(urlparse(self.path).query)
                        try:
                            order_limit = int(query.get("limit", ["100"])[0])
                        except ValueError:
                            raise InputError("Paper order limit must be an integer.") from None
                        self._send_json(200, list_paper_orders(db_path, user_id, order_limit))
                    elif route == "/api/scanner-presets":
                        self._send_json(200, list_scanner_presets(db_path, user_id))
                    elif route == "/api/notifications/rules":
                        self._send_json(200, notification_center(db_path, user_id))
                    elif route == "/api/research/command-center":
                        self._send_json(200, research_command_center(db_path, user_id))
                    elif route == "/api/strategies/compare":
                        symbol = parse_qs(urlparse(self.path).query).get("symbol", [None])[0]
                        self._send_json(200, strategy_comparison(db_path, user_id, symbol))
                    elif route == "/api/portfolio/intelligence":
                        self._send_json(200, portfolio_intelligence(db_path, user_id))
                    elif route == "/api/data-quality":
                        self._send_json(200, data_quality_center(db_path, user_id))
                    elif route == "/api/security/events":
                        self._send_json(
                            200,
                            {
                                **read_security_events(db_path, user_id=user_id, limit=100),
                                "scope": "Privacy-preserving local audit; network, email, device, and user identifiers are stored only as hashes.",
                            },
                        )
                    elif route == "/api/reports":
                        query = parse_qs(urlparse(self.path).query)
                        try:
                            report_limit = int(query.get("limit", ["20"])[0])
                        except ValueError:
                            raise InputError("Report limit must be an integer.") from None
                        self._send_json(200, list_research_reports(db_path, user_id, report_limit))
                    elif route == "/api/export":
                        self._send_json(
                            200,
                            export_account_data(db_path, user_id),
                            {"Content-Disposition": "attachment; filename=investor-lab-export.json"},
                        )
                    elif route == "/api/analytics/review":
                        self._send_json(200, review_stats(db_path, user_id))
                    elif route == "/api/plans/review-center":
                        self._send_json(200, plan_review_center(db_path, user_id))
                    elif route == "/api/alerts":
                        self._send_json(200, alert_center(db_path, user_id))
                    elif route == "/api/decision-settings":
                        self._send_json(200, decision_settings(db_path, user_id))
                    elif route == "/api/search":
                        query = parse_qs(urlparse(self.path).query).get("q", [""])[0]
                        self._send_json(200, search_securities(db_path, query, user["email"]))
                    elif route == "/api/earnings-calendar":
                        self._send_json(200, earnings_calendar(db_path, user_id))
                    elif route.startswith("/api/decisions/"):
                        symbol = unquote(route.removeprefix("/api/decisions/"))
                        self._send_json(200, decision_bundle(db_path, user_id, symbol))
                    elif route == "/api/journal":
                        query = parse_qs(urlparse(self.path).query)
                        try:
                            limit = int(query.get("limit", ["50"])[0])
                        except ValueError:
                            raise InputError("Journal limit must be an integer.") from None
                        self._send_json(200, list_journal_entries(db_path, user_id, limit))
                    elif route == "/api/market/status":
                        self._send_json(200, market_status(db_path, user_id))
                    elif route == "/api/data-sources/readiness":
                        self._send_json(200, data_source_readiness(db_path, user_id))
                    elif route.startswith("/api/market/research/"):
                        symbol = unquote(route.removeprefix("/api/market/research/"))
                        self._send_json(200, market_research(db_path, symbol))
                    elif route.startswith("/api/fundamentals/"):
                        symbol = unquote(route.removeprefix("/api/fundamentals/"))
                        self._send_json(200, fundamental_research(db_path, symbol))
                    elif route == "/api/plans":
                        query = parse_qs(urlparse(self.path).query)
                        kind = query.get("kind", [None])[0]
                        try:
                            limit = int(query.get("limit", ["30"])[0])
                        except ValueError:
                            raise InputError("Plan limit must be an integer.") from None
                        self._send_json(
                            200, list_research_plans(db_path, user_id, kind, limit)
                        )
                    elif route == "/api/sync":
                        query = parse_qs(urlparse(self.path).query)
                        try:
                            since = int(query.get("since", ["0"])[0])
                            limit = int(query.get("limit", ["200"])[0])
                        except ValueError:
                            raise InputError("Sync cursor and limit must be integers.") from None
                        self._send_json(200, sync_feed(db_path, user_id, since, limit))
                    else:
                        self._send_json(404, {"error": "Not found."})
            except Exception as error:
                self._handle_error(error)

        def do_POST(self) -> None:
            try:
                route = self._route()
                self._enforce_rate_limit(route, "POST")
                payload = self._read_json()
                if route in {"/api/auth/register", "/api/auth/login"}:
                    client = str(payload.get("client") or "web").lower()
                    if client not in {"web", "ios"}:
                        raise InputError("Client must be web or ios.")
                    gateway_email = self._access_gateway_email()
                    payload_email = normalize_email(payload.get("email"))
                    if gateway_email and not hmac.compare_digest(gateway_email, payload_email):
                        raise ApiError(403, "Gateway identity does not match the requested account.")
                    if route == "/api/auth/register":
                        allow_additional = os.environ.get("INVESTORLAB_ALLOW_REGISTRATION") == "1"
                        user, session = register_user(db_path, payload, allow_additional)
                        append_security_event(
                            db_path,
                            "registration",
                            "success",
                            user_id=user["id"],
                            email=user["email"],
                            address=self._client_address(),
                            device_id=str(payload.get("device_id") or ""),
                            client_type=client,
                        )
                        self._auth_response(201, user, session, client)
                    else:
                        user, session = login_user(db_path, payload, self._client_address())
                        self._auth_response(200, user, session, client)
                else:
                    user, _, token_hash, _ = self._require_auth(require_csrf=True)
                    user_id = user["id"]
                    if route == "/api/auth/logout":
                        delete_session(db_path, token_hash)
                        append_security_event(
                            db_path, "logout", "success", user_id=user_id,
                            address=self._client_address(),
                        )
                        self._send_json(
                            200,
                            {"logged_out": True},
                            {"Set-Cookie": self._session_cookie("", 0)},
                        )
                    elif route == "/api/auth/change-password":
                        result = change_password(db_path, user_id, payload)
                        append_security_event(
                            db_path, "password_change", "success", user_id=user_id,
                            address=self._client_address(),
                        )
                        self._send_json(
                            200, result,
                            {"Set-Cookie": self._session_cookie("", 0)},
                        )
                    elif route == "/api/auth/logout-all":
                        result = logout_all(db_path, user_id, payload)
                        append_security_event(
                            db_path, "logout_all", "success", user_id=user_id,
                            address=self._client_address(),
                        )
                        self._send_json(
                            200, result,
                            {"Set-Cookie": self._session_cookie("", 0)},
                        )
                    elif route == "/api/watchlist":
                        self._send_json(
                            201, add_watchlist(db_path, user_id, payload.get("symbol"))
                        )
                    elif route == "/api/trades":
                        self._send_json(201, record_trade(db_path, user_id, payload))
                    elif route == "/api/imports/portfolio/preview":
                        self._send_json(200, preview_portfolio_import(payload))
                    elif route == "/api/imports/portfolio":
                        self._send_json(201, import_portfolio_csv(db_path, user_id, payload))
                    elif route == "/api/journal":
                        self._send_json(201, record_journal_entry(db_path, user_id, payload))
                    elif route == "/api/alerts":
                        self._send_json(201, create_price_alert(db_path, user_id, payload))
                    elif route == "/api/market/configure":
                        require_owner(user)
                        self._send_json(200, configure_market_data(payload))
                    elif route == "/api/realtime/configure":
                        require_owner(user)
                        self._send_json(200, configure_realtime_data(payload))
                    elif route == "/api/data-sources/test":
                        require_owner(user)
                        self._send_json(
                            200,
                            test_data_source_connection(
                                db_path, user_id, user["email"], payload
                            ),
                        )
                    elif route == "/api/system/backup":
                        require_owner(user)
                        self._send_json(201, create_database_backup(db_path, user_id))
                    elif route == "/api/system/restore":
                        require_owner(user)
                        result = restore_database_backup(db_path, user_id, payload)
                        append_security_event(
                            db_path, "database_restore", "success", user_id=user_id,
                            address=self._client_address(),
                            details={"filename": result["filename"]},
                        )
                        self._send_json(200, result)
                    elif route == "/api/system/health-check":
                        self._send_json(200, run_system_health_check(db_path, user_id))
                    elif route == "/api/validation/run":
                        self._send_json(200, run_validation_cycle(db_path, user_id))
                    elif route == "/api/alpaca/paper-account/sync":
                        require_owner(user)
                        self._send_json(200, sync_paper_account(db_path, user_id))
                    elif route == "/api/alpaca/paper-orders":
                        require_owner(user)
                        result = submit_paper_order(db_path, user_id, payload)
                        append_security_event(
                            db_path, "paper_order_submit", "success", user_id=user_id,
                            address=self._client_address(),
                            details={"symbol": result.get("symbol"), "order_id": result.get("broker_order_id")},
                        )
                        self._send_json(201, result)
                    elif re.fullmatch(r"/api/alpaca/paper-orders/[^/]+/cancel", route):
                        require_owner(user)
                        order_id = unquote(route.removeprefix("/api/alpaca/paper-orders/").removesuffix("/cancel"))
                        result = cancel_paper_order(db_path, user_id, order_id, payload)
                        append_security_event(
                            db_path, "paper_order_cancel", "success", user_id=user_id,
                            address=self._client_address(), details={"order_id": order_id},
                        )
                        self._send_json(200, result)
                    elif re.fullmatch(r"/api/alpaca/paper-orders/[^/]+/replace", route):
                        require_owner(user)
                        order_id = unquote(route.removeprefix("/api/alpaca/paper-orders/").removesuffix("/replace"))
                        result = replace_paper_order(db_path, user_id, order_id, payload)
                        append_security_event(
                            db_path, "paper_order_replace", "success", user_id=user_id,
                            address=self._client_address(), details={"order_id": order_id},
                        )
                        self._send_json(200, result)
                    elif route == "/api/scanner-presets":
                        self._send_json(201, save_scanner_preset(db_path, user_id, payload))
                    elif route == "/api/scanner/run":
                        self._send_json(200, run_universe_scanner(db_path, user_id, payload))
                    elif route == "/api/notifications/rules":
                        self._send_json(201, create_notification_rule(db_path, user_id, payload))
                    elif route == "/api/options/scenario":
                        self._send_json(200, option_scenario(payload))
                    elif route == "/api/research/copilot":
                        self._send_json(200, research_copilot(db_path, user_id, payload))
                    elif route == "/api/reports":
                        self._send_json(201, generate_research_report(db_path, user_id, str(payload.get("period") or "daily")))
                    elif route == "/api/strategy-templates":
                        self._send_json(201, save_strategy_template(db_path, user_id, payload))
                    elif re.fullmatch(r"/api/strategy-templates/[^/]+/activate", route):
                        template_id = unquote(
                            route.removeprefix("/api/strategy-templates/").removesuffix("/activate")
                        )
                        self._send_json(200, activate_strategy_template(db_path, user_id, template_id))
                    elif route == "/api/portfolio/rebalance":
                        self._send_json(200, rebalance_portfolio(db_path, user_id, payload))
                    elif route == "/api/fundamentals/refresh":
                        self._send_json(
                            200,
                            refresh_fundamentals_and_decision(
                                db_path, user_id, payload.get("symbol"), user["email"]
                            ),
                        )
                    elif route == "/api/earnings-calendar/refresh":
                        api_key, _ = _alpha_vantage_api_key()
                        self._send_json(
                            200, refresh_earnings_calendar(db_path, user_id, api_key)
                        )
                    elif route == "/api/decisions":
                        self._send_json(
                            201, generate_decision(db_path, user_id, payload.get("symbol"))
                        )
                    elif route == "/api/decisions/refresh-watchlist":
                        self._send_json(200, refresh_watchlist_decisions(db_path, user_id))
                    elif route == "/api/plans/day-trade":
                        self._send_json(
                            201,
                            record_research_plan(db_path, user_id, "day_trade", payload),
                        )
                    elif route == "/api/plans/options":
                        self._send_json(
                            201,
                            record_research_plan(db_path, user_id, "options", payload),
                        )
                    elif re.fullmatch(r"/api/plans/[^/]+/reviews", route):
                        plan_id = unquote(route.removeprefix("/api/plans/").removesuffix("/reviews"))
                        self._send_json(
                            201, record_plan_review(db_path, user_id, plan_id, payload)
                        )
                    elif route == "/api/market/refresh":
                        try:
                            cache_minutes = int(
                                os.environ.get("INVESTORLAB_MARKET_CACHE_MINUTES", "720")
                            )
                        except ValueError:
                            raise ApiError(
                                500, "INVESTORLAB_MARKET_CACHE_MINUTES must be an integer."
                            ) from None
                        if not 1 <= cache_minutes <= 10_080:
                            raise ApiError(
                                500, "INVESTORLAB_MARKET_CACHE_MINUTES must be between 1 and 10080."
                            )
                        api_key, _ = _alpha_vantage_api_key()
                        research = refresh_market(
                            db_path,
                            payload.get("symbol"),
                            api_key,
                            cache_minutes,
                        )
                        research["decision"] = generate_decision(
                            db_path, user_id, payload.get("symbol")
                        )
                        alert_center(db_path, user_id)
                        self._send_json(200, research)
                    elif route == "/api/devices":
                        self._send_json(
                            201, register_device(db_path, user_id, token_hash, payload)
                        )
                    elif route == "/api/sync/ack":
                        self._send_json(200, acknowledge_sync(db_path, user_id, payload))
                    elif route == "/api/account/delete":
                        self._send_json(
                            200,
                            delete_account(db_path, user_id, payload),
                            {"Set-Cookie": self._session_cookie("", 0)},
                        )
                    else:
                        self._send_json(404, {"error": "Not found."})
            except Exception as error:
                self._handle_error(error)

        def do_PATCH(self) -> None:
            try:
                route = self._route()
                self._enforce_rate_limit(route, "PATCH")
                payload = self._read_json()
                user, _, _, _ = self._require_auth(require_csrf=True)
                if route == "/api/investor-profile":
                    self._send_json(
                        200, update_investor_profile(db_path, user["id"], payload)
                    )
                elif route == "/api/decision-settings":
                    self._send_json(
                        200, update_decision_settings(db_path, user["id"], payload)
                    )
                elif route == "/api/alpaca/paper-orders/control":
                    require_owner(user)
                    result = update_paper_order_control(db_path, user["id"], payload)
                    append_security_event(
                        db_path, "paper_order_control", "success", user_id=user["id"],
                        address=self._client_address(), details={"enabled": result.get("enabled")},
                    )
                    self._send_json(200, result)
                else:
                    self._send_json(404, {"error": "Not found."})
            except Exception as error:
                self._handle_error(error)

        def do_DELETE(self) -> None:
            try:
                route = self._route()
                self._enforce_rate_limit(route, "DELETE")
                user, _, token_hash, _ = self._require_auth(require_csrf=True)
                if route.startswith("/api/watchlist/"):
                    deleted = remove_watchlist(
                        db_path, user["id"], unquote(route.removeprefix("/api/watchlist/"))
                    )
                    if not deleted:
                        self._send_json(404, {"error": "Symbol is not in the watchlist."})
                        return
                    self._send_json(200, {"deleted": True})
                elif route.startswith("/api/alerts/"):
                    deleted = delete_price_alert(
                        db_path, user["id"], unquote(route.removeprefix("/api/alerts/"))
                    )
                    if not deleted:
                        self._send_json(404, {"error": "Price alert was not found."})
                        return
                    self._send_json(200, {"deleted": True})
                elif route.startswith("/api/devices/"):
                    deleted = delete_device(
                        db_path,
                        user["id"],
                        unquote(route.removeprefix("/api/devices/")),
                        token_hash,
                    )
                    if not deleted:
                        self._send_json(404, {"error": "Device was not found."})
                        return
                    self._send_json(200, {"deleted": True})
                elif route.startswith("/api/strategy-templates/"):
                    deleted = delete_strategy_template(
                        db_path, user["id"], unquote(route.removeprefix("/api/strategy-templates/"))
                    )
                    if not deleted:
                        self._send_json(404, {"error": "Strategy template was not found."})
                        return
                    self._send_json(200, {"deleted": True})
                elif route.startswith("/api/scanner-presets/"):
                    deleted = delete_scanner_preset(
                        db_path, user["id"], unquote(route.removeprefix("/api/scanner-presets/"))
                    )
                    if not deleted:
                        self._send_json(404, {"error": "Scanner preset was not found."})
                        return
                    self._send_json(200, {"deleted": True})
                elif route.startswith("/api/notifications/rules/"):
                    deleted = delete_notification_rule(
                        db_path, user["id"], unquote(route.removeprefix("/api/notifications/rules/"))
                    )
                    if not deleted:
                        self._send_json(404, {"error": "Notification rule was not found."})
                        return
                    self._send_json(200, {"deleted": True})
                else:
                    self._send_json(404, {"error": "Not found."})
            except Exception as error:
                self._handle_error(error)

    return InvestorLabHandler


def main() -> None:
    db_path = Path(os.environ.get("INVESTORLAB_DB", DEFAULT_DB))
    host = os.environ.get("INVESTORLAB_HOST", "127.0.0.1")
    port = int(os.environ.get("INVESTORLAB_PORT", "8000"))
    init_db(db_path)
    server = ThreadingHTTPServer((host, port), make_handler(db_path, DEFAULT_WEB_ROOT))
    scheduler_stop = threading.Event()
    scheduler = threading.Thread(
        target=_decision_scheduler_loop,
        args=(db_path, scheduler_stop),
        name="investor-lab-decision-scheduler",
        daemon=True,
    )
    scheduler.start()
    print(f"Investor Lab running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        scheduler_stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
