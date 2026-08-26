#!/usr/bin/env python3
"""Freeze, run, inspect, and export a local Paper validation campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    APP_VERSION,
    DECISION_MODEL_VERSION,
    DEFAULT_DB,
    SCHEMA_VERSION,
    STRATEGY_FREEZE_PROTOCOL,
    _alpha_vantage_api_key,
    _alpaca_credentials,
    _decision_rows,
    _decision_settings_from_db,
    _investor_profile_from_db,
    _validation_strategy_context,
    _watchlist_rows,
    open_db,
    run_validation_cycle,
    validation_dashboard,
    validation_report,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def source_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def resolve_user_id(db_path: Path, requested: str | None) -> str:
    with open_db(db_path) as db:
        if requested:
            row = db.execute("SELECT id FROM users WHERE id = ?", (requested,)).fetchone()
        else:
            row = db.execute(
                "SELECT id FROM users ORDER BY CASE role WHEN 'owner' THEN 0 ELSE 1 END, created_at, id LIMIT 1"
            ).fetchone()
    if not row:
        detail = "The requested account does not exist." if requested else "Create the first local account before freezing a campaign."
        raise RuntimeError(detail)
    return str(row["id"])


def require_current_schema(db_path: Path) -> None:
    with open_db(db_path) as db:
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
    if version != SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema is {version}; start the current app once to migrate to {SCHEMA_VERSION}, then retry."
        )


def create_baseline(
    db_path: Path,
    user_id: str,
    *,
    started_at: datetime | None = None,
    provider_status: dict[str, bool] | None = None,
) -> dict[str, Any]:
    started = started_at or utc_now()
    with open_db(db_path) as db:
        profile = _investor_profile_from_db(db, user_id)
        settings = _decision_settings_from_db(db, user_id)
        symbols = [item["symbol"] for item in _watchlist_rows(db, user_id)]
        latest = _decision_rows(db, user_id, limit=1)

    if provider_status is None:
        alpha_key, _ = _alpha_vantage_api_key()
        alpaca_key, alpaca_secret, _ = _alpaca_credentials()
        provider_status = {
            "alpha_vantage_configured": bool(alpha_key),
            "alpaca_paper_iex_configured": bool(alpaca_key and alpaca_secret),
        }

    latest_context = None
    if latest:
        latest_context = {
            "context": list(_validation_strategy_context(latest[0])),
            "strategy": latest[0].get("strategy"),
            "decision_created_at": latest[0].get("created_at"),
        }

    return {
        "format": "stock-thesis-ledger-paper-validation-baseline",
        "format_version": 1,
        "created_at": iso(started),
        "campaign": {
            "started_at": iso(started),
            "day_30_review_at": iso(started + timedelta(days=30)),
            "day_60_close_at": iso(started + timedelta(days=60)),
            "minimum_calendar_days": 30,
            "maximum_calendar_days": 60,
        },
        "software": {
            "app_version": APP_VERSION,
            "source_commit": source_commit(),
            "decision_model_version": DECISION_MODEL_VERSION,
            "strategy_freeze_protocol": STRATEGY_FREEZE_PROTOCOL,
        },
        "account_fingerprint": hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16],
        "watchlist": sorted(symbols),
        "investor_profile": profile,
        "decision_schedule": settings,
        "latest_strategy_context": latest_context,
        "providers": provider_status,
        "rules": {
            "parameter_change_starts_new_cohort": True,
            "missing_provider_data_counts_as_sample": False,
            "paper_only": True,
            "live_broker_route_implemented": False,
        },
        "scope": "Local immutable baseline. It contains no email, password, session, API key, server URL, account number, position, or order data.",
    }


def write_new_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def status_summary(dashboard: dict[str, Any]) -> str:
    campaign = dashboard["campaign"]
    lines = [
        f"Campaign: {campaign['status']} · day {campaign['day_number']} / {campaign['maximum_days']}",
        f"Model: {campaign['model_version']} · frozen: {'yes' if campaign['parameters_frozen'] else 'no'}",
        f"Capital review gate: {'READY' if dashboard['ready_for_capital_review'] else 'NOT READY'}",
    ]
    lines.extend(
        f"[{'x' if gate['passed'] else ' '}] {gate['label']}: {gate['value']} / {gate['required']}"
        for gate in dashboard["readiness_gates"]
    )
    blockers = dashboard["operations"]["blockers"]
    if blockers:
        lines.append(f"Operational blockers: {len(blockers)}")
        lines.extend(f"- {item['label']}: {item['detail']}" for item in blockers)
    return "\n".join(lines)


def report_paths(output_dir: Path, generated_at: str) -> tuple[Path, Path]:
    stamp = generated_at.replace("-", "").replace(":", "").replace("T", "-").replace("Z", "")
    return output_dir / f"validation-{stamp}.md", output_dir / f"validation-{stamp}.json"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    root.add_argument("--user-id", help="Account ID; defaults to the local owner")
    commands = root.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze", help="Write an immutable campaign baseline")
    freeze.add_argument("--output", type=Path, default=ROOT / "data" / "validation" / "campaign-baseline.json")
    status = commands.add_parser("status", help="Read the current machine-checkable gates")
    status.add_argument("--json", action="store_true", help="Print the full dashboard JSON")
    commands.add_parser("run", help="Run one explicit provider-consuming validation cycle")
    report = commands.add_parser("report", help="Export timestamped Markdown and JSON reports")
    report.add_argument("--output-dir", type=Path, default=ROOT / "data" / "validation")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    db_path = args.db.expanduser().resolve()
    if not db_path.is_file():
        raise RuntimeError(f"Database does not exist: {db_path}")
    require_current_schema(db_path)
    user_id = resolve_user_id(db_path, args.user_id)

    if args.command == "freeze":
        output = args.output.expanduser().resolve()
        write_new_json(output, create_baseline(db_path, user_id))
        print(f"Immutable campaign baseline: {output}")
        return 0
    if args.command == "status":
        dashboard = validation_dashboard(db_path, user_id, 60)
        print(json.dumps(dashboard, indent=2, sort_keys=True) if args.json else status_summary(dashboard))
        return 0
    if args.command == "run":
        result = run_validation_cycle(db_path, user_id)
        print(f"Validation cycle: {result['status']} at {result['completed_at']}")
        print(status_summary(result["dashboard"]))
        return 0
    if args.command == "report":
        report = validation_report(db_path, user_id)
        markdown_path, json_path = report_paths(args.output_dir.expanduser().resolve(), report["generated_at"])
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        with markdown_path.open("x", encoding="utf-8") as handle:
            handle.write(report["markdown"])
            handle.write("\n")
        write_new_json(json_path, report["dashboard"])
        print(f"Markdown report: {markdown_path}")
        print(f"JSON evidence: {json_path}")
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"paper-validation: {error}", file=sys.stderr)
        raise SystemExit(2) from error
