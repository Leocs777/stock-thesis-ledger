import app
import json
import html
import os
import re
import sqlite3
import tempfile
import threading
import unittest
from datetime import date, datetime, time, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import (
    InputError,
    _configure_tls_ca_environment,
    _sec_filing_comparison,
    ThreadingHTTPServer,
    init_db,
    make_handler,
    now_iso,
    open_db,
    portfolio,
    refresh_market,
    register_user,
    to_micros,
)


VALID_ACCOUNT = {
    "client": "web",
    "display_name": "Local Investor",
    "email": "investor@example.com",
    "password": "PaperTrades2026",
}


class FakeResponse:
    def __init__(self, payload):
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit):
        return self.body


class InvestorLabAPITest(unittest.TestCase):
    def setUp(self):
        self.alpaca_credentials_patcher = patch(
            "app._alpaca_credentials", return_value=("", "", "unconfigured")
        )
        self.alpaca_credentials_patcher.start()
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = root / "test.sqlite3"
        init_db(self.db)
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(self.db, Path(__file__).parent / "web")
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.csrf_token = ""

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()
        self.alpaca_credentials_patcher.stop()

    def test_macos_tls_uses_system_ca_without_overriding_an_explicit_bundle(self):
        ca_bundle = Path(self.temp.name) / "cert.pem"
        ca_bundle.write_text("test bundle")
        with patch.dict(os.environ, {"SSL_CERT_FILE": ""}):
            _configure_tls_ca_environment("darwin", ca_bundle)
            self.assertEqual(os.environ["SSL_CERT_FILE"], str(ca_bundle))
        with patch.dict(os.environ, {"SSL_CERT_FILE": "/custom/cert.pem"}):
            _configure_tls_ca_environment("darwin", ca_bundle)
            self.assertEqual(os.environ["SSL_CERT_FILE"], "/custom/cert.pem")

    def request(self, method, path, payload=None, *, csrf=True, bearer=None):
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if csrf and method not in {"GET", "HEAD"} and self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        request = Request(self.base + path, data=body, method=method, headers=headers)
        with self.opener.open(request) as response:
            return response.status, json.load(response)

    def request_text(self, path):
        with self.opener.open(self.base + path) as response:
            return response.status, response.headers.get_content_type(), response.read().decode()

    def register(self, client="web", device_id=None, device_name=None):
        payload = {
            **VALID_ACCOUNT,
            "client": client,
            "device_id": device_id or f"{client}-test-device",
            "device_name": device_name or f"Test {client}",
        }
        status, data = self.request("POST", "/api/auth/register", payload, csrf=False)
        self.csrf_token = data["csrf_token"]
        return status, data

    def test_design_system_preview_and_health_are_public(self):
        status, content_type, app_shell = self.request_text("/")
        _, css_type, app_css = self.request_text("/assets/app.css")
        _, js_type, app_js = self.request_text("/assets/app.js")
        self.assertEqual((status, content_type), (200, "text/html"))
        self.assertEqual((css_type, js_type), ("text/css", "text/javascript"))
        self.assertNotIn("<style>", app_shell)
        self.assertNotIn("<script>", app_shell)
        self.assertIn("const zhCN", app_js)
        self.assertIn("--signal:", app_css)
        self.assertIn("Trading strategy", app_shell)
        self.assertIn('id="appLanguage"', app_shell)
        self.assertIn("简体中文", app_shell)
        self.assertIn('"Trading strategy": "交易策略"', app_js)
        self.assertIn("Maximum position · % per symbol", app_shell)
        self.assertIn(
            "60% technical · 25% fundamentals · 15% position fit",
            app_shell,
        )
        self.assertIn('id="paperOrderControlAcknowledged"', app_shell)
        self.assertIn('id="paperOrderAcknowledged"', app_shell)
        self.assertNotIn('id="paperOrderConfirmation"', app_shell)
        self.assertIn('/assets/investor-lab-logo.png', app_shell)
        self.assertIn('id="watchRefreshAllButton"', app_shell)
        self.assertIn('id="marketLive"', app_shell)
        self.assertIn('class="section-jump"', app_shell)

        with self.opener.open(self.base + "/assets/investor-lab-logo.png") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertEqual(response.read(8), b"\x89PNG\r\n\x1a\n")

        ios_app = (Path(__file__).parent / "ios" / "InvestorLab" / "InvestorLabApp.swift").read_text()
        self.assertIn("controlAcknowledged", ios_app)
        self.assertIn("orderAcknowledged", ios_app)
        self.assertNotIn("orderConfirmation", ios_app)

        status, content_type, body = self.request_text("/design-system")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/html")
        self.assertIn("Investor Lab — Phase 3 Design System", body)

        status, content_type, css = self.request_text("/assets/investor-lab-ui.css")
        self.assertEqual((status, content_type), (200, "text/css"))
        self.assertIn(".lab-button", css)

        status, health = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["auth_required"])
        self.assertEqual(health["schema_version"], 17)
        self.assertEqual(health["api_contract_version"], "2026-08-26.phase2")

        status, contract = self.request("GET", "/api/contract")
        self.assertEqual(status, 200)
        self.assertTrue(contract["paper_only"])

    def test_positive_micros_rejects_nonfinite_values(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value), self.assertRaises(InputError):
                to_micros(value, "Price")

    def test_chinese_catalog_covers_static_and_dynamic_product_copy(self):
        root = Path(__file__).parent
        web_shell = (root / "web" / "index.html").read_text()
        web_js = (root / "web" / "app.js").read_text()
        web = web_shell + "\n" + web_js
        app_source = (root / "app.py").read_text()
        catalog_source = web[web.index("const zhCN = {") : web.index("function t(value)")]
        web_keys = {
            json.loads(f'"{match}"')
            for match in re.findall(r'^\s+"((?:[^"\\]|\\.)+)":', catalog_source, re.MULTILINE)
        }
        body = web_shell[web_shell.index("<body") : web_shell.index("</body>")]
        visible_copy = {
            re.sub(r"\s+", " ", html.unescape(match)).strip()
            for match in re.findall(r">([^<>]+)<", body)
            if re.search(r"[A-Za-z]{2}", match)
        }
        intentional_product_tokens = {
            "IL",
            "Investor Lab",
            "Alpha Vantage",
            "DECISION-V4.1",
            "INDICATIVE",
            "English",
        }
        self.assertEqual(visible_copy - web_keys - intentional_product_tokens, set())
        for dynamic_key in (
            "equity",
            "option",
            "followed",
            "skipped",
            "current",
            "stale",
            "missing",
            "No current bar",
            "No decision",
        ):
            self.assertIn(dynamic_key, web_keys)
        self.assertIn('window.confirm(t("Permanently delete this account', web)

        backend_errors = {
            json.loads(f'"{match}"')
            for match in re.findall(
                r'(?:InputError|AuthError|ConflictError)\(\s*"((?:[^"\\]|\\.)+)"',
                app_source,
            )
        }
        self.assertEqual(backend_errors - web_keys, set())

        runtime_literals = {
            json.loads(f'"{match}"')
            for pattern in (
                r'notify\(\s*"((?:[^"\\]|\\.)+)"',
                r'\.textContent\s*=\s*"((?:[^"\\]|\\.)+)"',
            )
            for match in re.findall(pattern, web)
            if re.search(r"[A-Za-z]{2}", match)
        }
        self.assertEqual(runtime_literals - web_keys, set())

        strings = (root / "ios" / "InvestorLab" / "zh-Hans.lproj" / "Localizable.strings").read_text()
        ios_keys = set(re.findall(r'^"((?:[^"\\]|\\.)+)"\s*=', strings, re.MULTILINE))
        self.assertEqual(backend_errors - ios_keys, set())
        for dynamic_key in (
            "Equity",
            "Option",
            "Current",
            "Stale",
            "Missing",
            "No cached evidence",
            "No current bar",
            "No decision",
            "DATA GATE",
            "CACHED CLOSE",
            "COST BASIS",
        ):
            self.assertIn(dynamic_key, ios_keys)
        ios_app = (root / "ios" / "InvestorLab" / "InvestorLabApp.swift").read_text()
        self.assertIn('Text(labLocalized(decision.tradingDate ?? "No current bar"))', ios_app)
        self.assertIn("labLocalized(item.assetType.capitalized)", ios_app)
        self.assertIn('private static let defaultServerURL = ""', ios_app)
        self.assertNotIn("serverURLMigration", ios_app)
        self.assertNotIn(".ngrok-free.", ios_app)
        self.assertIn("components.host != nil", ios_app)
        self.assertIn("refreshFilingNotificationsIfAuthorized", ios_app)
        self.assertIn("notifySecFilingChanges(data.sec_events)", web)
        self.assertIn('id="workflowPanel"', web)
        self.assertIn('id="performancePanel"', web)
        self.assertIn('id="marketEvidencePanel"', web)
        self.assertIn('data-refresh-symbol', web)
        self.assertIn('id="commandAdvancedToggle"', web)
        self.assertIn('data-command-advanced hidden', web)
        self.assertIn('@AppStorage("workflowSymbol")', ios_app)
        self.assertIn("if showAdvancedTools", ios_app)
        self.assertNotIn('"Decision gate": "决策门槛', web)
        self.assertNotIn('"Decision gate" = "决策门槛', strings)
        self.assertNotIn("已阻塞", web)
        self.assertNotIn("已阻塞", strings)

    def test_protected_data_requires_authentication(self):
        with self.assertRaises(HTTPError) as error:
            self.request("GET", "/api/snapshot")
        self.assertEqual(error.exception.code, 401)

    def test_registration_cookie_csrf_and_hashed_session(self):
        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST",
                "/api/auth/register",
                {**VALID_ACCOUNT, "password": "short"},
                csrf=False,
            )
        self.assertEqual(error.exception.code, 400)

        status, auth = self.register()
        self.assertEqual(status, 201)
        self.assertNotIn("access_token", auth)
        self.assertEqual(auth["user"]["email"], VALID_ACCOUNT["email"])

        with self.assertRaises(HTTPError) as error:
            self.request("POST", "/api/watchlist", {"symbol": "AAPL"}, csrf=False)
        self.assertEqual(error.exception.code, 403)

        self.assertEqual(self.request("POST", "/api/watchlist", {"symbol": "AAPL"})[0], 201)
        with open_db(self.db) as db:
            stored = db.execute("SELECT token_hash FROM sessions").fetchone()["token_hash"]
            self.assertEqual(len(stored), 64)
            self.assertNotEqual(stored, auth.get("access_token"))

        with self.assertRaises(HTTPError) as error:
            self.request("POST", "/api/auth/register", VALID_ACCOUNT, csrf=False)
        self.assertEqual(error.exception.code, 403)

    def test_session_transport_device_binding_password_change_and_logout_all(self):
        _, auth = self.register(device_id="web-security-device", device_name="Security browser")
        with open_db(self.db) as db:
            web_session = db.execute(
                "SELECT client_type, device_id FROM sessions WHERE user_id = ?",
                (auth["user"]["id"],),
            ).fetchone()
        self.assertEqual(dict(web_session), {
            "client_type": "web", "device_id": "web-security-device",
        })
        web_bearer_session = app.create_session(
            self.db, auth["user"]["id"], "web", "web-security-device"
        )

        _, ios_auth = self.request(
            "POST", "/api/auth/login",
            {
                "client": "ios", "device_id": "ios-security-device",
                "device_name": "Security iPhone", "email": VALID_ACCOUNT["email"],
                "password": VALID_ACCOUNT["password"],
            },
            csrf=False,
        )
        ios_token = ios_auth["access_token"]
        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST", "/api/watchlist", {"symbol": "MSFT"},
                csrf=False, bearer=web_bearer_session["access_token"],
            )
        self.assertEqual(error.exception.code, 401)

        status, changed = self.request(
            "POST", "/api/auth/change-password",
            {
                "current_password": VALID_ACCOUNT["password"],
                "new_password": "NewPaperTrades2027",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(changed["reauth_required"])
        with self.assertRaises(HTTPError) as error:
            self.request("GET", "/api/auth/session")
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError) as error:
            self.request("GET", "/api/snapshot", csrf=False, bearer=ios_token)
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST", "/api/auth/login",
                {
                    "client": "web", "device_id": "web-security-device",
                    "device_name": "Security browser", "email": VALID_ACCOUNT["email"],
                    "password": VALID_ACCOUNT["password"],
                },
                csrf=False,
            )
        self.assertEqual(error.exception.code, 401)

        _, fresh = self.request(
            "POST", "/api/auth/login",
            {
                "client": "web", "device_id": "web-security-device",
                "device_name": "Security browser", "email": VALID_ACCOUNT["email"],
                "password": "NewPaperTrades2027",
            },
            csrf=False,
        )
        self.csrf_token = fresh["csrf_token"]
        _, second_ios = self.request(
            "POST", "/api/auth/login",
            {
                "client": "ios", "device_id": "ios-security-device-2",
                "device_name": "Second iPhone", "email": VALID_ACCOUNT["email"],
                "password": "NewPaperTrades2027",
            },
            csrf=False,
        )
        status, logged_out = self.request(
            "POST", "/api/auth/logout-all", {"current_password": "NewPaperTrades2027"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(logged_out["logged_out_all"])
        with self.assertRaises(HTTPError) as error:
            self.request("GET", "/api/snapshot", csrf=False, bearer=second_ios["access_token"])
        self.assertEqual(error.exception.code, 401)

    def test_login_rate_limit_cannot_be_bypassed_by_changing_email(self):
        self.register()
        for index in range(app.LOGIN_ATTEMPT_LIMIT):
            with self.assertRaises(HTTPError) as error:
                self.request(
                    "POST", "/api/auth/login",
                    {
                        "client": "web", "device_id": f"web-rate-{index:03d}",
                        "device_name": "Rate test", "email": f"unknown-{index}@example.com",
                        "password": "WrongPassword2026",
                    },
                    csrf=False,
                )
            self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST", "/api/auth/login",
                {
                    "client": "web", "device_id": "web-rate-blocked",
                    "device_name": "Rate test", "email": "another@example.com",
                    "password": "WrongPassword2026",
                },
                csrf=False,
            )
        self.assertEqual(error.exception.code, 429)

    def test_multi_account_reports_are_isolated_and_global_operations_require_owner(self):
        _, owner = self.register(device_id="web-owner-device", device_name="Owner browser")
        self.assertEqual(owner["user"]["role"], "owner")
        self.request("POST", "/api/watchlist", {"symbol": "PLTR"})

        bob_opener = build_opener(HTTPCookieProcessor(CookieJar()))

        def bob_request(method, path, payload=None, csrf_token=""):
            body = json.dumps(payload).encode() if payload is not None else None
            headers = {"Content-Type": "application/json"} if body is not None else {}
            if method not in {"GET", "HEAD"} and csrf_token:
                headers["X-CSRF-Token"] = csrf_token
            request = Request(self.base + path, data=body, method=method, headers=headers)
            with bob_opener.open(request) as response:
                return response.status, json.load(response)

        bob_payload = {
            "client": "web", "device_id": "web-bob-device", "device_name": "Bob browser",
            "display_name": "Bob", "email": "bob@example.com", "password": "BobPaperTrades2026",
        }
        with patch.dict(os.environ, {"INVESTORLAB_ALLOW_REGISTRATION": "1"}):
            _, bob = bob_request("POST", "/api/auth/register", bob_payload)
        self.assertEqual(bob["user"]["role"], "member")
        bob_csrf = bob["csrf_token"]
        bob_request("POST", "/api/watchlist", {"symbol": "SOFI"}, bob_csrf)

        with open_db(self.db) as db:
            for symbol, base in (("PLTR", 20), ("SOFI", 10)):
                for offset in range(60):
                    trading_day = date.today() - timedelta(days=59 - offset)
                    price = (base + offset) * 1_000_000
                    db.execute(
                        "INSERT INTO market_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            symbol, trading_day.isoformat(), price, price, price, price,
                            1_000_000, "alpha_vantage", now_iso(),
                        ),
                    )

        _, health = bob_request("GET", "/api/system/health")
        self.assertEqual([item["symbol"] for item in health["market_cache"]["symbols"]], ["SOFI"])
        _, status = bob_request("GET", "/api/market/status")
        self.assertEqual(status["cached_symbols"], 1)
        _, readiness = bob_request("GET", "/api/data-sources/readiness")
        self.assertNotIn("PLTR", json.dumps(readiness))
        _, quality = bob_request("GET", "/api/data-quality")
        self.assertEqual([item["symbol"] for item in quality["symbols"]], ["SOFI"])
        _, scan = bob_request("POST", "/api/scanner/run", {}, bob_csrf)
        self.assertNotIn("PLTR", json.dumps(scan))

        for method, path, payload in (
            ("GET", "/api/system/backups", None),
            ("POST", "/api/market/configure", {"api_key": "ABCDEFGH1234"}),
            ("POST", "/api/alpaca/paper-account/sync", {}),
        ):
            with self.assertRaises(HTTPError) as error:
                bob_request(method, path, payload, bob_csrf)
            self.assertEqual(error.exception.code, 403)

    def test_keychain_secrets_are_sent_over_stdin_not_process_arguments(self):
        result = type("SecurityResult", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with patch.object(app.sys, "platform", "darwin"), patch(
            "app.subprocess.run", return_value=result
        ) as run:
            app.configure_market_data({"api_key": "ALPHATEST123"})
            app.configure_realtime_data({
                "api_key_id": "ALPACAKEY123", "api_secret_key": "ALPACASECRETKEY123456",
            })
        calls = run.call_args_list
        self.assertEqual(len(calls), 3)
        for call in calls:
            command = call.args[0]
            self.assertNotIn("ALPHATEST123", command)
            self.assertNotIn("ALPACAKEY123", command)
            self.assertNotIn("ALPACASECRETKEY123456", command)
            self.assertTrue(call.kwargs["input"].endswith("\n"))

    def test_watchlist_append_only_ledger_and_incremental_sync(self):
        self.register()
        self.request(
            "POST",
            "/api/devices",
            {"device_id": "web-test-device", "name": "Test browser", "platform": "web"},
        )
        self.request("POST", "/api/watchlist", {"symbol": "aapl"})
        self.request(
            "POST",
            "/api/trades",
            {"symbol": "AAPL", "side": "buy", "quantity": "2", "price": "100"},
        )
        self.request(
            "POST",
            "/api/trades",
            {"symbol": "AAPL", "side": "sell", "quantity": "1", "price": "110"},
        )
        _, sync = self.request("GET", "/api/sync?since=0")
        self.assertGreaterEqual(len(sync["events"]), 4)
        self.assertEqual(sync["snapshot"]["watchlist"][0]["symbol"], "AAPL")
        self.assertEqual(sync["snapshot"]["portfolio"]["positions"][0]["quantity"], "1")
        self.assertEqual(sync["snapshot"]["portfolio"]["realized_pnl"], "10")
        self.assertEqual(sync["cursor"], sync["latest_revision"])

        status, ack = self.request(
            "POST",
            "/api/sync/ack",
            {"device_id": "web-test-device", "revision": sync["cursor"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(ack["revision"], sync["cursor"])

        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST",
                "/api/trades",
                {"symbol": "AAPL", "side": "sell", "quantity": "2", "price": "110"},
            )
        self.assertEqual(error.exception.code, 400)

        with self.assertRaises(HTTPError) as error:
            self.request("GET", "/api/sync?since=9223372036854775808")
        self.assertEqual(error.exception.code, 400)

    def test_ios_bearer_session_does_not_require_csrf(self):
        self.register()
        _, auth = self.request(
            "POST",
            "/api/auth/login",
            {
                "client": "ios",
                "device_id": "ios-bearer-device",
                "device_name": "Bearer iPhone",
                "email": VALID_ACCOUNT["email"],
                "password": VALID_ACCOUNT["password"],
            },
            csrf=False,
        )
        token = auth["access_token"]
        status, item = self.request(
            "POST", "/api/watchlist", {"symbol": "MSFT"}, csrf=False, bearer=token
        )
        self.assertEqual(status, 201)
        self.assertEqual(item["symbol"], "MSFT")

    def test_investor_profile_devices_and_account_export(self):
        self.register()
        self.request(
            "POST",
            "/api/devices",
            {"device_id": "web-test-device", "name": "Profile browser", "platform": "web"},
        )
        _, default_profile = self.request("GET", "/api/investor-profile")
        self.assertEqual(default_profile["paper_account_size"], "25000")
        self.assertTrue(default_profile["options_defined_risk_only"])

        status, updated = self.request(
            "PATCH",
            "/api/investor-profile",
            {
                "strategy_style": "momentum",
                "time_horizon": "day",
                "paper_account_size": "40000",
                "max_position_percent": "8",
                "risk_per_trade_percent": "0.35",
                "minimum_reward_risk": "2.5",
                "daily_loss_limit": "250",
                "options_defined_risk_only": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["minimum_reward_risk"], "2.5")

        _, current = self.request("GET", "/api/snapshot")
        self.assertEqual(current["investor_profile"]["strategy_style"], "momentum")
        self.assertEqual(current["devices"][0]["name"], "Profile browser")
        _, exported = self.request("GET", "/api/export")
        self.assertEqual(exported["format"], "investor-lab-account-export")
        self.assertEqual(exported["schema_version"], 17)
        self.assertIn("plan_reviews", exported)
        self.assertIn("portfolio_imports", exported)
        self.assertEqual(exported["investor_profile"]["daily_loss_limit"], "250")
        self.assertNotIn("password_hash", json.dumps(exported))
        self.assertNotIn("access_token", json.dumps(exported))
        self.assertIn("investor_profile", {event["entity_type"] for event in exported["sync_events"]})

        with self.assertRaises(HTTPError) as error:
            self.request(
                "PATCH",
                "/api/investor-profile",
                {**updated, "risk_per_trade_percent": "11"},
            )
        self.assertEqual(error.exception.code, 400)

    def test_logout_revokes_browser_session(self):
        self.register()
        status, response = self.request("POST", "/api/auth/logout", {})
        self.assertEqual(status, 200)
        self.assertTrue(response["logged_out"])
        with self.assertRaises(HTTPError) as error:
            self.request("GET", "/api/auth/session")
        self.assertEqual(error.exception.code, 401)

    def test_portfolio_csv_preview_import_deduplication_and_health(self):
        self.register()
        csv_text = (
            "symbol,quantity,average_cost,asset_type\n"
            "AAPL,2.5,190.25,equity\n"
            "SPY260918C00600000,1,12.5,option\n"
        )
        payload = {"filename": "starting-positions.csv", "csv_text": csv_text}
        status, preview = self.request(
            "POST", "/api/imports/portfolio/preview", payload
        )
        self.assertEqual(status, 200)
        self.assertEqual(preview["row_count"], 2)
        self.assertEqual(preview["total_cost"], "1725.625")
        self.assertEqual(len(preview["fingerprint"]), 64)

        status, imported = self.request("POST", "/api/imports/portfolio", payload)
        self.assertEqual(status, 201)
        self.assertEqual(imported["row_count"], 2)
        _, current = self.request("GET", "/api/snapshot")
        self.assertEqual(len(current["portfolio"]["positions"]), 2)
        self.assertEqual(current["recent_imports"][0]["filename"], "starting-positions.csv")
        _, exported = self.request("GET", "/api/export")
        self.assertEqual(exported["portfolio_imports"][0]["row_count"], 2)

        with self.assertRaises(HTTPError) as error:
            self.request("POST", "/api/imports/portfolio", payload)
        self.assertEqual(error.exception.code, 409)
        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST",
                "/api/imports/portfolio",
                {
                    "filename": "same-positions-new-order.csv",
                    "csv_text": (
                        "symbol,quantity,average_cost,asset_type\n"
                        "SPY260918C00600000,1,12.5,option\n"
                        "AAPL,2.5,190.25,equity\n"
                    ),
                },
            )
        self.assertEqual(error.exception.code, 409)
        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST",
                "/api/imports/portfolio",
                {"filename": "invalid.csv", "csv_text": "symbol,quantity,average_cost\nMSFT,-1,20\n"},
            )
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(len(self.request("GET", "/api/trades")[1]), 2)

        status, health = self.request("GET", "/api/system/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "attention")
        self.assertEqual(health["database"]["integrity"], "ok")
        self.assertEqual(health["account_counts"]["imports"], 1)
        self.assertEqual(health["schema_version"], 17)

    def test_removing_device_revokes_only_its_bound_sessions(self):
        self.register(device_id="web-primary-device", device_name="Primary browser")
        self.request(
            "POST",
            "/api/devices",
            {"device_id": "web-primary-device", "name": "Primary browser", "platform": "web"},
        )
        _, ios_auth = self.request(
            "POST",
            "/api/auth/login",
            {
                "client": "ios",
                "device_id": "ios-secondary-device",
                "device_name": "Test iPhone",
                "email": VALID_ACCOUNT["email"],
                "password": VALID_ACCOUNT["password"],
            },
            csrf=False,
        )
        ios_token = ios_auth["access_token"]
        self.request(
            "POST",
            "/api/devices",
            {"device_id": "ios-secondary-device", "name": "Test iPhone", "platform": "ios"},
            csrf=False,
            bearer=ios_token,
        )
        status, _ = self.request("DELETE", "/api/devices/ios-secondary-device")
        self.assertEqual(status, 200)
        with self.assertRaises(HTTPError) as error:
            self.request("GET", "/api/snapshot", csrf=False, bearer=ios_token)
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError) as error:
            self.request("DELETE", "/api/devices/web-primary-device")
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.request("GET", "/api/snapshot")[0], 200)

    def test_account_deletion_requires_password_and_confirmation(self):
        self.register()
        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST",
                "/api/account/delete",
                {"password": "WrongPassword2026", "confirmation": "DELETE"},
            )
        self.assertEqual(error.exception.code, 403)
        status, result = self.request(
            "POST",
            "/api/account/delete",
            {"password": VALID_ACCOUNT["password"], "confirmation": "DELETE"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["deleted"])
        with open_db(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
        with self.assertRaises(HTTPError) as error:
            self.request("GET", "/api/snapshot")
        self.assertEqual(error.exception.code, 401)

    def test_daily_market_refresh_analysis_and_cache(self):
        self.register()
        self.request("POST", "/api/watchlist", {"symbol": "AAPL"})
        start = date(2026, 1, 1)
        series = {}
        for index in range(60):
            close = 100 + index
            series[(start + timedelta(days=index)).isoformat()] = {
                "1. open": str(close - 1),
                "2. high": str(close + 1),
                "3. low": str(close - 2),
                "4. close": str(close),
                "5. volume": str(1_000_000 + index),
            }
        provider_payload = {"Time Series (Daily)": series}
        live_payload = {
            "latestTrade": {"p": 159.25, "t": "2026-03-01T15:30:00Z"},
            "latestQuote": {"bp": 159.20, "ap": 159.30},
            "dailyBar": {"c": 159.25},
        }
        with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test-key"}), patch(
            "app.urlopen", return_value=FakeResponse(provider_payload)
        ) as provider, patch(
            "app._alpaca_credentials", return_value=("paper-key", "paper-secret", "test")
        ), patch("app._alpaca_json", return_value=live_payload) as live_provider:
            status, research = self.request(
                "POST", "/api/market/refresh", {"symbol": "AAPL"}
            )
            self.assertEqual(status, 200)
            self.assertTrue(research["available"])
            self.assertFalse(research["cache_hit"])
            self.assertEqual(research["state"], "bullish_alignment")
            self.assertEqual(research["observations"], 60)
            self.assertIsNotNone(research["historical_scenario"])
            self.assertEqual(len(research["bars"]), 60)
            self.assertEqual(research["bars"][0]["trading_date"], "2026-01-01")
            self.assertEqual(research["bars"][-1]["close"], "159")
            self.assertEqual(research["range_stats"]["high_close"], "159")
            self.assertEqual(research["range_stats"]["low_close"], "100")
            self.assertEqual(research["range_stats"]["period_return_percent"], "59.00")
            self.assertEqual(research["range_stats"]["max_drawdown_percent"], "0.00")
            self.assertTrue(research["realtime_quote"]["available"])
            self.assertEqual(research["realtime_quote"]["latest_price"], "159.25")
            self.assertEqual(research["realtime_quote"]["bid"], "159.2")
            self.assertEqual(research["realtime_quote"]["ask"], "159.3")
            self.assertEqual(research["realtime_quote"]["feed"], "iex")
            self.assertEqual(research["decision"]["signal"], "refresh_required")
            self.assertEqual(research["decision"]["quality"], "stale")
            self.assertGreater(
                float(research["range_stats"]["annualized_volatility_percent"]), 0
            )

            _, cached = self.request("POST", "/api/market/refresh", {"symbol": "AAPL"})
            self.assertTrue(cached["cache_hit"])
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(live_provider.call_count, 1)

            _, status_data = self.request("GET", "/api/market/status")
            self.assertTrue(status_data["configured"])
            self.assertEqual(status_data["cached_symbols"], 1)
            _, stored = self.request("GET", "/api/market/research/AAPL")
            self.assertEqual(stored["latest_close"], "159")
            self.assertEqual(len(stored["bars"]), 60)
            _, current = self.request("GET", "/api/snapshot")
            self.assertEqual(current["watchlist_research"][0]["symbol"], "AAPL")
            self.assertNotIn("bars", current["watchlist_research"][0])
            self.assertEqual(
                current["watchlist_research"][0]["state"], "bullish_alignment"
            )

    def test_daily_briefing_explains_when_watchlist_market_data_needs_refresh(self):
        self.register()
        self.request("POST", "/api/watchlist", {"symbol": "AAPL"})
        _, snapshot = self.request("GET", "/api/snapshot")
        self.assertEqual(snapshot["daily_briefing"]["headline"], "Market evidence needs refresh")

    def test_adjusted_daily_refresh_uses_adjusted_ohlc_for_research(self):
        self.register()
        start = date.today() - timedelta(days=59)
        series = {}
        for index in range(60):
            adjusted_close = 100 + index
            raw_close = adjusted_close * 2 if index < 30 else adjusted_close
            series[(start + timedelta(days=index)).isoformat()] = {
                "1. open": str(raw_close - 2),
                "2. high": str(raw_close + 2),
                "3. low": str(raw_close - 4),
                "4. close": str(raw_close),
                "5. adjusted close": str(adjusted_close),
                "6. volume": str(1_000_000 + index),
                "7. dividend amount": "0",
                "8. split coefficient": "1",
            }
        provider_payload = {"Time Series (Daily)": series}
        with patch.dict(
            os.environ,
            {
                "ALPHAVANTAGE_API_KEY": "test-key",
                "INVESTORLAB_ADJUSTED_DAILY": "1",
            },
        ), patch("app.urlopen", return_value=FakeResponse(provider_payload)):
            _, research = self.request("POST", "/api/market/refresh", {"symbol": "AAPL"})

        self.assertEqual(research["adjusted_rows"], 60)
        self.assertEqual(research["latest_close"], "159")
        self.assertEqual(research["bars"][0]["close"], "100")
        self.assertEqual(research["bars"][0]["open"], "99")
        self.assertEqual(research["bars"][-1]["open"], "157")
        self.assertEqual(research["bars"][-1]["high"], "161")
        self.assertEqual(research["bars"][-1]["low"], "155")
        self.assertEqual(research["data_quality"]["price_adjustment"], "historically_adjusted")
        self.assertNotIn(
            "Daily OHLCV is raw and not adjusted for historical splits or cash dividends.",
            research["data_quality"]["warnings"],
        )

        raw_series = {
            trading_date: {
                "1. open": values["1. open"],
                "2. high": values["2. high"],
                "3. low": values["3. low"],
                "4. close": values["4. close"],
                "5. volume": values["6. volume"],
            }
            for trading_date, values in series.items()
        }
        with patch.dict(os.environ, {"INVESTORLAB_ADJUSTED_DAILY": "0"}), patch(
            "app.urlopen", return_value=FakeResponse({"Time Series (Daily)": raw_series})
        ):
            raw = refresh_market(self.db, "AAPL", "test-key", cache_minutes=0)
        self.assertEqual(raw["adjusted_rows"], 0)
        self.assertEqual(raw["data_quality"]["price_adjustment"], "raw")
        self.assertIn(
            "Daily OHLCV is raw and not adjusted for historical splits or cash dividends.",
            raw["data_quality"]["warnings"],
        )
        with open_db(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM market_adjustments").fetchone()[0], 0)

    def test_sec_fundamentals_refresh_parses_caches_and_declares_contact(self):
        self.register()
        self.request("POST", "/api/watchlist", {"symbol": "AAPL"})
        with open_db(self.db) as db:
            db.execute(
                "INSERT INTO market_daily(symbol,trading_date,open_micros,high_micros,low_micros,close_micros,volume,source,fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("AAPL", "2025-02-01", 199_000_000, 201_000_000, 198_000_000, 200_000_000, 1_000_000, "alpha_vantage", now_iso()),
            )

        def duration(year, value):
            return {
                "start": f"{year}-01-01",
                "end": f"{year}-12-31",
                "val": value,
                "form": "10-K",
                "fp": "FY",
                "fy": year,
                "filed": f"{year + 1}-02-01",
                "accn": f"0000320193-{str(year + 1)[-2:]}-000001",
            }

        def instant(year, value):
            item = duration(year, value)
            item.pop("start")
            return item

        def quarter(year, number, value):
            starts = {1: "01-01", 2: "04-01", 3: "07-01", 4: "10-01"}
            ends = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
            return {
                "start": f"{year}-{starts[number]}",
                "end": f"{year}-{ends[number]}",
                "val": value,
                "form": "10-Q" if number < 4 else "10-K",
                "fp": f"Q{number}",
                "fy": year,
                "filed": f"{year}-{min(number * 3 + 1, 12):02d}-30",
                "accn": f"quarter-{year}-{number}",
            }

        def concept(unit, entries):
            return {"units": {unit: entries}}

        tickers = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
        company_facts = {
            "entityName": "Apple Inc.",
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": concept(
                        "USD", [duration(2023, 1000), duration(2024, 1200)]
                        + [quarter(2024, number, 250 + number * 10) for number in range(1, 5)]
                    ),
                    "NetIncomeLoss": concept(
                        "USD", [duration(2023, 100), duration(2024, 150)]
                        + [quarter(2024, number, 25 + number) for number in range(1, 5)]
                    ),
                    "NetCashProvidedByUsedInOperatingActivities": concept(
                        "USD", [duration(2023, 200), duration(2024, 240)]
                        + [quarter(2024, number, 50 + number) for number in range(1, 5)]
                    ),
                    "PaymentsToAcquirePropertyPlantAndEquipment": concept(
                        "USD", [duration(2023, 50), duration(2024, 60)]
                        + [quarter(2024, number, 10 + number) for number in range(1, 5)]
                    ),
                    "Assets": concept("USD", [instant(2023, 2000), instant(2024, 2400)]),
                    "Liabilities": concept("USD", [instant(2023, 1000), instant(2024, 1100)]),
                    "StockholdersEquity": concept("USD", [instant(2023, 1000), instant(2024, 1300)]),
                    "EarningsPerShareDiluted": concept(
                        "USD/shares", [duration(2023, 1), duration(2024, 1.5)]
                        + [quarter(2024, number, 0.30 + number / 100) for number in range(1, 5)]
                    ),
                    "CommonStockDividendsPerShareDeclared": concept(
                        "USD/shares", [duration(2023, 0.20), duration(2024, 0.25)]
                    ),
                    "WeightedAverageNumberOfDilutedSharesOutstanding": concept(
                        "shares", [duration(2023, 100), duration(2024, 100)]
                        + [quarter(2024, number, 100) for number in range(1, 5)]
                    ),
                }
            },
        }
        submissions = {
            "filings": {
                "recent": {
                    "form": ["10-K", "10-Q"],
                    "accessionNumber": ["0000320193-25-000001", "0000320193-25-000002"],
                    "filingDate": ["2025-02-01", "2025-05-01"],
                    "reportDate": ["2024-12-31", "2025-03-31"],
                    "primaryDocument": ["aapl-20241231.htm", "aapl-20250331.htm"],
                }
            }
        }
        with patch(
            "app.urlopen",
            side_effect=[FakeResponse(tickers), FakeResponse(company_facts), FakeResponse(submissions)],
        ) as provider:
            status, fundamentals = self.request(
                "POST", "/api/fundamentals/refresh", {"symbol": "AAPL"}
            )
            self.assertEqual(status, 200)
            self.assertTrue(fundamentals["available"])
            self.assertFalse(fundamentals["cache_hit"])
            self.assertEqual(fundamentals["company_name"], "Apple Inc.")
            self.assertEqual(fundamentals["metrics"]["revenue_growth_percent"], "20.00")
            self.assertEqual(fundamentals["metrics"]["net_margin_percent"], "12.50")
            self.assertEqual(fundamentals["metrics"]["free_cash_flow"], 180)
            self.assertEqual(fundamentals["metrics"]["dividends_per_share"], 0.25)
            self.assertEqual(fundamentals["annual_history"][-1]["filed"], "2025-02-01")
            self.assertEqual(len(fundamentals["annual_history"]), 2)
            self.assertIn("aapl-20241231.htm", fundamentals["filings"][0]["url"])
            self.assertEqual(fundamentals["data_version"], 4)
            self.assertIn("quarterly_history", fundamentals)
            self.assertEqual(len(fundamentals["quarterly_history"]), 4)
            self.assertIn("valuation", fundamentals)
            self.assertTrue(fundamentals["valuation"]["available"])
            self.assertEqual(fundamentals["valuation"]["pe"], "153.85")
            self.assertEqual(fundamentals["filing_comparison"]["available"], False)
            self.assertEqual(fundamentals["filings"][0]["kind"], "annual_results")
            self.assertFalse(fundamentals["changes"]["detected"])
            self.assertEqual(fundamentals["decision"]["model_version"], "decision-v4.1")
            self.assertEqual(provider.call_count, 3)
            declared_request = provider.call_args_list[0].args[0]
            self.assertIn(VALID_ACCOUNT["email"], declared_request.get_header("User-agent"))

            _, cached = self.request(
                "POST", "/api/fundamentals/refresh", {"symbol": "AAPL"}
            )
            self.assertTrue(cached["cache_hit"])
            self.assertEqual(provider.call_count, 3)

        updated_company_facts = json.loads(json.dumps(company_facts))
        for fact in updated_company_facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]:
            if fact.get("fp") == "FY" and fact.get("fy") == 2024:
                fact["val"] = 175
        updated_submissions = json.loads(json.dumps(submissions))
        recent = updated_submissions["filings"]["recent"]
        for key, value in {
            "form": "8-K",
            "accessionNumber": "0000320193-26-000003",
            "filingDate": date.today().isoformat(),
            "reportDate": date.today().isoformat(),
            "primaryDocument": "aapl-current-report.htm",
        }.items():
            recent[key].insert(0, value)
        with open_db(self.db) as db:
            db.execute(
                "UPDATE sec_cache SET fetched_at = ? WHERE cache_key = ?",
                ("2000-01-01T00:00:00Z", "fundamentals:AAPL"),
            )
        with patch(
            "app.urlopen",
            side_effect=[FakeResponse(updated_company_facts), FakeResponse(updated_submissions)],
        ):
            _, changed = self.request(
                "POST", "/api/fundamentals/refresh", {"symbol": "AAPL"}
            )
        self.assertTrue(changed["changes"]["detected"])
        self.assertEqual(changed["changes"]["new_filings"][0]["form"], "8-K")
        self.assertIn(
            "net_income", {item["key"] for item in changed["changes"]["metric_changes"]}
        )

        _, snapshot = self.request("GET", "/api/snapshot")
        self.assertEqual(snapshot["sec_events"]["recent_count"], 1)
        self.assertEqual(snapshot["sec_events"]["attention_count"], 1)
        self.assertEqual(snapshot["daily_briefing"]["filing_count"], 1)
        self.assertIn("filing", {item["category"] for item in snapshot["daily_briefing"]["tasks"]})

        status, cached = self.request("GET", "/api/fundamentals/AAPL")
        self.assertEqual(status, 200)
        self.assertTrue(cached["available"])

    def test_company_search_and_watchlist_earnings_calendar(self):
        self.register()
        tickers = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp."},
        }
        with patch("app.urlopen", return_value=FakeResponse(tickers)) as provider:
            status, search = self.request("GET", "/api/search?q=apple")
        self.assertEqual(status, 200)
        self.assertEqual(search["results"][0]["symbol"], "AAPL")
        self.assertEqual(search["results"][0]["provider"], "SEC EDGAR")
        self.assertEqual(provider.call_count, 1)

        self.request("POST", "/api/watchlist", {"symbol": "AAPL"})
        future = (date.today() + timedelta(days=14)).isoformat()
        calendar_csv = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency\n"
            f"AAPL,Apple Inc.,{future},2026-06-30,1.45,USD\n"
            f"MSFT,Microsoft Corp.,{future},2026-06-30,3.10,USD\n"
        ).encode()
        with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test-key"}), patch(
            "app.urlopen", return_value=FakeResponse(calendar_csv)
        ) as provider:
            status, calendar = self.request("POST", "/api/earnings-calendar/refresh", {})
            self.assertEqual(status, 200)
            self.assertEqual([item["symbol"] for item in calendar["events"]], ["AAPL"])
            self.assertEqual(calendar["events"][0]["days_until"], 14)
            _, cached = self.request("POST", "/api/earnings-calendar/refresh", {})
            self.assertTrue(cached["cache_hit"])
            self.assertEqual(provider.call_count, 1)
        _, current = self.request("GET", "/api/snapshot")
        self.assertEqual(current["earnings_calendar"]["events"][0]["symbol"], "AAPL")

    def test_sec_filing_comparison_extracts_risk_and_management_wording(self):
        common = " ".join(["The company monitors operational and financial conditions carefully."] * 90)
        previous = (
            f"ITEM 1A. RISK FACTORS {common} Legacy supplier concentration may affect results. "
            f"ITEM 1B. UNRESOLVED STAFF COMMENTS ITEM 7. MANAGEMENT'S DISCUSSION {common} "
            "Demand was stable in the prior period. ITEM 7A. QUANTITATIVE DISCLOSURES"
        )
        current = (
            f"ITEM 1A. RISK FACTORS {common} Artificial intelligence regulation may increase compliance cost. "
            f"ITEM 1B. UNRESOLVED STAFF COMMENTS ITEM 7. MANAGEMENT'S DISCUSSION {common} "
            "Management expects higher infrastructure investment. ITEM 7A. QUANTITATIVE DISCLOSURES"
        )
        filings = [
            {"form": "10-K", "url": "https://sec/current", "filed": "2026-02-01"},
            {"form": "10-K", "url": "https://sec/previous", "filed": "2025-02-01"},
        ]
        with patch("app._sec_document_text", side_effect=[current, previous]):
            comparison = _sec_filing_comparison(filings, VALID_ACCOUNT["email"])
        self.assertTrue(comparison["available"])
        self.assertTrue(comparison["sections"]["risk_factors"]["available"])
        self.assertIn(
            "Artificial intelligence",
            comparison["sections"]["risk_factors"]["added"][0],
        )
        self.assertTrue(comparison["sections"]["management_discussion"]["available"])

    def test_market_key_is_saved_without_being_returned(self):
        self.register()
        api_key = "PersonalMarketKey2026"
        with patch("app.sys.platform", "darwin"), patch("app.subprocess.run") as security:
            security.return_value.returncode = 0
            security.return_value.stdout = ""
            status, response = self.request(
                "POST", "/api/market/configure", {"api_key": api_key}
            )
        self.assertEqual(status, 200)
        self.assertTrue(response["configured"])
        self.assertEqual(response["configuration_source"], "keychain")
        self.assertNotIn(api_key, json.dumps(response))
        self.assertNotIn(api_key, security.call_args.args[0])
        self.assertEqual(security.call_args.kwargs["input"], api_key + "\n")

        with self.assertRaises(HTTPError) as error:
            self.request("POST", "/api/market/configure", {"api_key": "bad key"})
        self.assertEqual(error.exception.code, 400)

    def test_data_source_readiness_and_read_only_connection_tests(self):
        self.register()
        with patch("app._alpha_vantage_api_key", return_value=("", "unconfigured")), patch(
            "app._alpaca_credentials", return_value=("", "", "unconfigured")
        ):
            _, readiness = self.request("GET", "/api/data-sources/readiness")
        self.assertIn(readiness["overall"], {"setup_required", "research_ready"})
        self.assertEqual(len(readiness["providers"]), 3)
        self.assertFalse(next(item for item in readiness["providers"] if item["key"] == "alpha_vantage")["configured"])
        self.assertTrue(next(item for item in readiness["providers"] if item["key"] == "sec_edgar")["configured"])
        self.assertFalse(readiness["paper_orders_enabled"])

        with patch("app._alpha_vantage_api_key", return_value=("alpha-key", "test")), patch(
            "app._alpha_vantage_daily", return_value=[("SPY", "2026-08-13")]
        ) as alpha:
            _, result = self.request(
                "POST", "/api/data-sources/test", {"source": "alpha_vantage", "symbol": "SPY"}
            )
        self.assertTrue(result["connected"])
        self.assertEqual(result["latest_data_date"], "2026-08-13")
        alpha.assert_called_once_with("SPY", "alpha-key")

        account = {"status": "ACTIVE", "trading_blocked": False, "account_blocked": False}
        with patch("app._alpaca_credentials", return_value=("paper-key", "paper-secret", "test")), patch(
            "app._alpaca_trading_json", return_value=account
        ) as alpaca:
            _, result = self.request(
                "POST", "/api/data-sources/test", {"source": "alpaca_paper"}
            )
        self.assertTrue(result["connected"])
        self.assertEqual(result["account_status"], "ACTIVE")
        alpaca.assert_called_once_with("/v2/account", {}, "paper-key", "paper-secret")

        with self.assertRaises(HTTPError) as error:
            self.request("POST", "/api/data-sources/test", {"source": "unknown"})
        self.assertEqual(error.exception.code, 400)

    def test_decision_engine_history_backtest_and_settings(self):
        self.register()
        self.request("POST", "/api/watchlist", {"symbol": "AAPL"})
        start = date.today() - timedelta(days=99)
        with open_db(self.db) as db:
            db.execute(
                "UPDATE investor_profiles SET updated_at = ?",
                ((start - timedelta(days=1)).isoformat() + "T00:00:00Z",),
            )
            for index in range(100):
                close = (100 + index) * 1_000_000
                db.execute(
                    "INSERT INTO market_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "AAPL",
                        (start + timedelta(days=index)).isoformat(),
                        close - 1_000_000,
                        close + 1_000_000,
                        close - 2_000_000,
                        close,
                        1_000_000 + index * 1_000,
                        "alpha_vantage",
                        "2026-08-13T00:00:00Z",
                    ),
                )

        status, first = self.request("POST", "/api/decisions", {"symbol": "AAPL"})
        self.assertEqual(status, 201)
        self.assertEqual(first["signal"], "buy_candidate")
        self.assertGreaterEqual(first["score"], 75)
        self.assertEqual(first["quality"], "partial")
        self.assertEqual(first["model_version"], "decision-v4.1")
        self.assertTrue(first["strategy"]["missing_fundamentals_redistributed"])
        self.assertIn("transparent multi-factor rules", first["strategy"]["origin"])
        self.assertEqual(len(first["strategy"]["decision_rules"]), 4)
        self.assertEqual(len(first["strategy"]["data_sources"]), 3)
        price_plan = first["price_plan"]
        self.assertTrue(price_plan["available"])
        self.assertEqual(price_plan["atr_14"], "3")
        self.assertEqual(price_plan["buy_zone_low"], "196")
        self.assertEqual(price_plan["buy_zone_high"], "199")
        self.assertEqual(price_plan["breakout_trigger"], "199.3")
        self.assertEqual(price_plan["risk_stop"], "193")
        self.assertEqual(price_plan["target_1"], "211")
        self.assertEqual(price_plan["target_2"], "217")
        self.assertTrue(price_plan["targets_active"])
        self.assertEqual({item["key"] for item in first["factors"]}, {
            "trend", "momentum", "drawdown", "volatility", "volume", "portfolio_fit"
        })
        self.assertTrue(first["backtest"]["walk_forward"])
        self.assertEqual(first["backtest"]["fee_slippage_bps_per_side"], 10)
        self.assertTrue(first["data_quality"]["decision_eligible"])
        self.assertEqual(len(first["backtest"]["parameter_sensitivity"]), 3)
        self.assertIn(first["backtest"]["stability"]["label"], {"stable", "mixed", "unstable"})
        holdout = first["backtest"]["out_of_sample"]
        self.assertFalse(holdout["available"])
        self.assertFalse(holdout["parameters_frozen"])
        self.assertEqual(holdout["method"], "chronological_70_30_holdout")
        self.assertEqual(holdout["sessions"], 15)
        self.assertEqual(holdout["sample_start"], (start + timedelta(days=84)).isoformat())
        self.assertEqual(holdout["sample_end"], (start + timedelta(days=99)).isoformat())
        self.assertIn("not frozen", holdout["reason"])
        self.assertFalse(first["change"]["signal_changed"])

        frozen_at = (start - timedelta(days=1)).isoformat() + "T00:00:00Z"
        with open_db(self.db) as db:
            frozen_result = json.loads(
                db.execute(
                    "SELECT result_json FROM decision_runs WHERE id = ?", (first["id"],)
                ).fetchone()[0]
            )
            frozen_result["created_at"] = frozen_at
            frozen_result["strategy"]["frozen_at"] = frozen_at
            db.execute(
                "UPDATE decision_runs SET result_json = ?, created_at = ? WHERE id = ?",
                (json.dumps(frozen_result), frozen_at, first["id"]),
            )
        _, default_comparison = self.request("GET", "/api/strategies/compare?symbol=AAPL")
        default_row = next(
            item for item in default_comparison["comparisons"]
            if item["version_id"] == "profile-default"
        )
        self.assertTrue(default_row["out_of_sample_available"])
        self.assertEqual(default_row["out_of_sample_sessions"], 15)

        _, copilot = self.request(
            "POST", "/api/research/copilot",
            {"symbol": "AAPL", "question": "Summarize the current thesis."},
        )
        self.assertEqual(copilot["thesis"], first["evidence"][:3])
        self.assertEqual(
            copilot["counter_thesis"][: len(first["counter_evidence"][:4])],
            first["counter_evidence"][:4],
        )
        self.assertNotIn(
            "Run or refresh a decision to create a rules-based thesis.",
            copilot["thesis"],
        )

        with open_db(self.db) as db:
            legacy_result = json.loads(json.dumps(first))
            legacy_result["evidence"] = ["", None]
            legacy_result["counter_evidence"] = []
            legacy_result["reasons"] = ["Legacy thesis evidence."]
            legacy_result["risks"] = ["Legacy counter-evidence."]
            db.execute(
                "UPDATE decision_runs SET result_json = ? WHERE id = ?",
                (json.dumps(legacy_result), first["id"]),
            )
        _, legacy_copilot = self.request(
            "POST", "/api/research/copilot",
            {"symbol": "AAPL", "question": "Summarize the stored legacy thesis."},
        )
        self.assertEqual(legacy_copilot["thesis"], ["Legacy thesis evidence."])
        self.assertEqual(legacy_copilot["counter_thesis"][0], "Legacy counter-evidence.")
        with open_db(self.db) as db:
            db.execute(
                "UPDATE decision_runs SET result_json = ? WHERE id = ?",
                (json.dumps(first), first["id"]),
            )

        _, reused = self.request("POST", "/api/decisions", {"symbol": "AAPL"})
        self.assertEqual(reused["id"], first["id"])
        self.assertTrue(reused["reused"])
        with open_db(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM decision_runs").fetchone()[0], 1)

        self.request(
            "POST", "/api/trades",
            {"symbol": "AAPL", "side": "buy", "quantity": "20", "price": "199"},
        )
        _, concentrated = self.request("POST", "/api/decisions", {"symbol": "AAPL"})
        self.assertEqual(concentrated["signal"], "reduce")
        self.assertTrue(concentrated["change"]["signal_changed"])
        self.assertEqual(concentrated["position"]["fit_score"], 0)

        with open_db(self.db) as db:
            db.execute(
                "UPDATE market_daily SET open_micros = 141000000, high_micros = 142000000, "
                "low_micros = 139000000, close_micros = 140000000 "
                "WHERE symbol = 'AAPL' AND trading_date = ?",
                (date.today().isoformat(),),
            )
        _, exit_review = self.request("POST", "/api/decisions", {"symbol": "AAPL"})
        self.assertEqual(exit_review["signal"], "sell_review")
        self.assertTrue(exit_review["change"]["signal_changed"])
        self.assertFalse(exit_review["price_plan"]["targets_active"])

        _, bundle = self.request("GET", "/api/decisions/AAPL")
        self.assertEqual(bundle["latest"]["id"], exit_review["id"])
        self.assertEqual(len(bundle["history"]), 3)
        self.assertTrue(bundle["backtest"]["available"])
        self.assertFalse(bundle["validation"]["available"])
        self.assertEqual(len(bundle["validation"]["parameter_sensitivity"]), 3)

        _, settings = self.request(
            "PATCH", "/api/decision-settings",
            {"auto_refresh_enabled": True, "refresh_interval_hours": 24},
        )
        self.assertTrue(settings["auto_refresh_enabled"])
        with self.assertRaises(HTTPError) as error:
            self.request(
                "PATCH", "/api/decision-settings",
                {"auto_refresh_enabled": True, "refresh_interval_hours": 8},
            )
        self.assertEqual(error.exception.code, 400)
        _, current = self.request("GET", "/api/snapshot")
        self.assertEqual(current["decision_center"]["latest"][0]["signal"], "sell_review")
        self.assertTrue(current["decision_center"]["settings"]["auto_refresh_enabled"])
        _, health = self.request("GET", "/api/system/health")
        self.assertEqual(health["account_counts"]["decisions"], 3)
        _, exported = self.request("GET", "/api/export")
        self.assertEqual(len(exported["decision_runs"]), 3)

    def test_strategy_lab_v2_weights_fundamentals_and_point_in_time_backtest(self):
        self.register()
        start = date.today() - timedelta(days=99)
        with open_db(self.db) as db:
            for index in range(100):
                close = (100 + index) * 1_000_000
                db.execute(
                    "INSERT INTO market_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "AAPL", (start + timedelta(days=index)).isoformat(),
                        close - 1_000_000, close + 1_000_000, close - 2_000_000,
                        close, 1_000_000 + index * 1_000, "alpha_vantage", now_iso(),
                    ),
                )
            annual_history = [
                {
                    "fiscal_year": 2023, "period_end": "2023-12-31",
                    "filed": (start - timedelta(days=40)).isoformat(),
                    "revenue": 1_000, "net_income": 120, "operating_cash_flow": 220,
                    "capital_expenditure": 40, "assets": 2_000, "liabilities": 1_000,
                    "equity": 1_000, "diluted_eps": 8, "dividends_per_share": 0.8,
                    "free_cash_flow": 180, "net_margin_percent": "12.00",
                    "liabilities_to_assets_percent": "50.00",
                },
                {
                    "fiscal_year": 2024, "period_end": "2024-12-31",
                    "filed": (start - timedelta(days=20)).isoformat(),
                    "revenue": 1_250, "net_income": 250, "operating_cash_flow": 310,
                    "capital_expenditure": 60, "assets": 2_400, "liabilities": 960,
                    "equity": 1_440, "diluted_eps": 10, "dividends_per_share": 1,
                    "free_cash_flow": 250, "net_margin_percent": "20.00",
                    "liabilities_to_assets_percent": "40.00",
                },
            ]
            payload = {
                "available": True, "symbol": "AAPL", "provider": "SEC EDGAR",
                "period_end": "2024-12-31", "annual_history": annual_history,
                "metrics": {
                    **annual_history[-1], "revenue_growth_percent": "25.00",
                    "net_income_growth_percent": "108.33",
                },
                "filings": [], "fetched_at": now_iso(),
            }
            db.execute(
                "INSERT INTO sec_cache(cache_key, payload_json, fetched_at) VALUES (?, ?, ?)",
                ("fundamentals:AAPL", json.dumps(payload), now_iso()),
            )

        _, balanced = self.request("POST", "/api/decisions", {"symbol": "AAPL"})
        self.assertEqual(balanced["quality"], "complete")
        self.assertEqual(balanced["strategy"]["technical_weight"], 60)
        self.assertEqual(balanced["strategy"]["fundamental_weight"], 25)
        self.assertEqual(sum(item["max_score"] for item in balanced["factors"]), 100)
        self.assertIn("fundamental_growth", {item["key"] for item in balanced["factors"]})
        self.assertTrue(balanced["backtest"]["point_in_time_fundamentals"])
        self.assertGreater(balanced["backtest"]["fundamental_observation_days"], 0)

        _, profile = self.request("GET", "/api/investor-profile")
        _, value = self.request(
            "PATCH", "/api/investor-profile",
            {**profile, "strategy_style": "value", "time_horizon": "long_term"},
        )
        self.assertEqual(value["strategy_style"], "value")
        _, value_run = self.request("POST", "/api/decisions", {"symbol": "AAPL"})
        self.assertEqual(value_run["strategy"]["technical_weight"], 25)
        self.assertEqual(value_run["strategy"]["fundamental_weight"], 60)
        self.assertIn("valuation", {item["key"] for item in value_run["factors"]})
        self.assertNotEqual(
            {item["key"]: item["max_score"] for item in balanced["factors"]},
            {item["key"]: item["max_score"] for item in value_run["factors"]},
        )

    def test_daily_briefing_screener_and_paper_performance(self):
        self.register()
        for symbol in ("AAPL", "MSFT", "TSLA"):
            self.request("POST", "/api/watchlist", {"symbol": symbol})
        start = date.today() - timedelta(days=99)
        with open_db(self.db) as db:
            for symbol, offset in (("AAPL", 0), ("MSFT", 100)):
                for index in range(100):
                    close = (100 + offset + index) * 1_000_000
                    db.execute(
                        "INSERT INTO market_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            symbol,
                            (start + timedelta(days=index)).isoformat(),
                            close - 1_000_000,
                            close + 1_000_000,
                            close - 2_000_000,
                            close,
                            1_000_000 + index * 1_000,
                            "alpha_vantage",
                            "2026-08-13T00:00:00Z",
                        ),
                    )

        self.request(
            "POST", "/api/trades",
            {"symbol": "AAPL", "side": "buy", "quantity": "5", "price": "150"},
        )
        _, held = self.request("POST", "/api/decisions", {"symbol": "AAPL"})
        self.assertEqual(held["signal"], "hold")
        _, candidate = self.request("POST", "/api/decisions", {"symbol": "MSFT"})
        self.assertEqual(candidate["signal"], "buy_candidate")
        self.request(
            "POST", "/api/alerts",
            {"symbol": "AAPL", "direction": "above", "threshold": "150"},
        )
        self.request(
            "POST", "/api/plans/day-trade",
            {
                "symbol": "AAPL",
                "direction": "long",
                "hypothesis": "Review the recorded setup after the session.",
                "account_size": "25000",
                "entry": "190",
                "stop": "188",
                "target": "196",
                "risk_percent": "0.5",
                "max_position_percent": "10",
                "daily_loss_limit": "300",
                "current_daily_loss": "0",
                "minimum_reward_risk": "2",
            },
        )

        _, current = self.request("GET", "/api/snapshot")
        performance = current["portfolio_performance"]
        self.assertEqual(performance["open_cost_basis"], "750")
        self.assertEqual(performance["market_value"], "995")
        self.assertEqual(performance["unrealized_pnl"], "245")
        self.assertEqual(performance["estimated_cash"], "24250")
        self.assertEqual(performance["estimated_account_value"], "25245")
        self.assertEqual(performance["total_return_percent"], "0.98")
        self.assertEqual(performance["positions"][0]["decision_signal"], "hold")

        screener = current["watchlist_screener"]
        self.assertEqual([item["symbol"] for item in screener["items"]], ["MSFT", "AAPL", "TSLA"])
        self.assertEqual(screener["counts"]["opportunity"], 1)
        self.assertEqual(screener["counts"]["position"], 1)
        self.assertEqual(screener["counts"]["data"], 1)

        briefing = current["daily_briefing"]
        self.assertEqual(briefing["headline"], "Review risk items first")
        self.assertEqual(briefing["risk_count"], 1)
        self.assertEqual(briefing["opportunity_count"], 1)
        self.assertEqual(briefing["data_issue_count"], 1)
        self.assertEqual(briefing["attention_count"], 3)
        self.assertEqual(
            {item["category"] for item in briefing["tasks"]},
            {"alert", "opportunity", "data", "review"},
        )

    def test_day_trade_and_options_plans_are_calculated_and_synced(self):
        self.register()
        status, day_plan = self.request(
            "POST",
            "/api/plans/day-trade",
            {
                "symbol": "AAPL",
                "direction": "long",
                "hypothesis": "Review a breakout retest that remains above supplied support.",
                "account_size": "25000",
                "entry": "100",
                "stop": "98",
                "target": "106",
                "risk_percent": "0.5",
                "max_position_percent": "10",
                "daily_loss_limit": "300",
                "current_daily_loss": "50",
                "minimum_reward_risk": "2",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(day_plan["analysis"]["risk_based_share_ceiling"], 62)
        self.assertEqual(day_plan["analysis"]["allocation_share_ceiling"], 25)
        self.assertEqual(day_plan["analysis"]["maximum_whole_shares"], 25)
        self.assertEqual(day_plan["analysis"]["binding_constraint"], "max_position")
        self.assertEqual(day_plan["analysis"]["reward_risk"], "3.00")
        self.assertEqual(day_plan["analysis"]["plan_status"], "ready_for_manual_review")

        expiration = (date.today() + timedelta(days=30)).isoformat()
        status, option_plan = self.request(
            "POST",
            "/api/plans/options",
            {
                "symbol": "AAPL",
                "strategy": "bull_call_spread",
                "hypothesis": "Compare a defined-risk call spread at expiration.",
                "expiration": expiration,
                "quantity": 1,
                "primary_strike": "100",
                "primary_premium": "6",
                "secondary_strike": "110",
                "secondary_premium": "2",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(option_plan["analysis"]["max_loss"], "400")
        self.assertEqual(option_plan["analysis"]["max_profit"], "600")
        self.assertEqual(option_plan["analysis"]["breakeven"], "104")

        status, day_review = self.request(
            "POST",
            f"/api/plans/{day_plan['id']}/reviews",
            {
                "decision": "followed",
                "outcome": "open",
                "discipline_score": 4,
                "note": "The paper entry followed the recorded trigger and stop.",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(day_review["symbol"], "AAPL")
        self.assertEqual(day_review["decision"], "followed")
        self.request(
            "POST",
            f"/api/plans/{option_plan['id']}/reviews",
            {
                "decision": "skipped",
                "outcome": "na",
                "note": "Premium no longer matched the saved comparison.",
            },
        )

        _, review_center = self.request("GET", "/api/plans/review-center")
        self.assertEqual(review_center["reviewed_plans"], 2)
        self.assertEqual(review_center["active_followed"], 1)
        self.assertEqual(review_center["decision_counts"]["skipped"], 1)
        self.assertEqual(review_center["follow_through_percent"], "50.0")
        self.assertEqual(review_center["option_attention"], [])

        _, plans = self.request("GET", "/api/plans?kind=options")
        self.assertEqual([plan["kind"] for plan in plans], ["options"])
        _, sync = self.request("GET", "/api/sync?since=0")
        self.assertEqual(len(sync["snapshot"]["recent_plans"]), 2)
        self.assertIn("research_plan", {event["entity_type"] for event in sync["events"]})
        self.assertIn("plan_review", {event["entity_type"] for event in sync["events"]})
        self.assertEqual(sync["snapshot"]["plan_review_center"]["reviewed_plans"], 2)

        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST",
                f"/api/plans/{day_plan['id']}/reviews",
                {"decision": "followed", "outcome": "na", "note": "Invalid state."},
            )
        self.assertEqual(error.exception.code, 400)

        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST",
                "/api/plans/day-trade",
                {
                    "symbol": "AAPL",
                    "direction": "long",
                    "hypothesis": "Invalid geometry is rejected.",
                    "account_size": "25000",
                    "entry": "100",
                    "stop": "101",
                    "target": "106",
                    "risk_percent": "0.5",
                    "daily_loss_limit": "300",
                    "current_daily_loss": "0",
                    "minimum_reward_risk": "2",
                },
            )
        self.assertEqual(error.exception.code, 400)

    def test_priority_two_and_three_strategy_backtest_risk_actions_and_rebalance(self):
        self.register()
        status, template = self.request(
            "POST",
            "/api/strategy-templates",
            {
                "name": "Technical with valuation",
                "technical_weight": 50,
                "fundamental_weight": 20,
                "valuation_weight": 20,
                "portfolio_weight": 10,
                "fee_slippage_bps": 8,
                "activate": True,
            },
        )
        self.assertEqual(status, 201)
        self.assertTrue(template["is_active"])
        self.assertEqual(template["fee_slippage_bps"], 8)
        _, templates = self.request("GET", "/api/strategy-templates")
        self.assertEqual([item["name"] for item in templates], ["Technical with valuation"])

        self.request("POST", "/api/watchlist", {"symbol": "AAPL"})
        start = date.today() - timedelta(days=99)
        with open_db(self.db) as db:
            frozen_at = (start - timedelta(days=1)).isoformat() + "T00:00:00Z"
            db.execute(
                "UPDATE strategy_versions SET created_at = ?, activated_at = ?",
                (frozen_at, frozen_at),
            )
            for index in range(100):
                trading_date = (start + timedelta(days=index)).isoformat()
                for symbol, close in (("AAPL", 100 + index), ("SPY", 400 + index // 2)):
                    close_micros = close * 1_000_000
                    db.execute(
                        "INSERT INTO market_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            symbol, trading_date, close_micros - 500_000,
                            close_micros + 1_000_000, close_micros - 1_000_000,
                            close_micros, 1_000_000 + index * 1_000,
                            "alpha_vantage", "2026-08-13T00:00:00Z",
                        ),
                    )

        _, decision = self.request("POST", "/api/decisions", {"symbol": "AAPL"})
        self.assertIn("template_id", decision["strategy"], decision["strategy"])
        self.assertEqual(decision["strategy"]["template_id"], template["id"])
        self.assertEqual(decision["backtest"]["fee_slippage_bps_per_side"], 8)
        self.assertTrue(decision["backtest"]["benchmark_available"])
        self.assertGreater(len(decision["backtest"]["equity_curve"]), 1)
        self.assertIn("factor_changes", decision["change"])

        with open_db(self.db) as db:
            frozen_result = json.loads(
                db.execute(
                    "SELECT result_json FROM decision_runs WHERE id = ?", (decision["id"],)
                ).fetchone()[0]
            )
            frozen_result["created_at"] = frozen_at
            frozen_result["strategy"]["frozen_at"] = frozen_at
            db.execute(
                "UPDATE decision_runs SET result_json = ?, created_at = ? WHERE id = ?",
                (json.dumps(frozen_result), frozen_at, decision["id"]),
            )

        status, comparison = self.request("GET", "/api/strategies/compare?symbol=AAPL")
        self.assertEqual(status, 200)
        self.assertIsNone(comparison["leader_version_id"])
        self.assertIn("No automatic leader", comparison["selection_rule"])
        compared = next(
            item for item in comparison["comparisons"]
            if item["version_id"] == template["version_id"]
        )
        self.assertTrue(compared["out_of_sample_available"])
        self.assertEqual(compared["out_of_sample_sessions"], 15)
        self.assertIsNotNone(compared["out_of_sample_return_percent"])

        _, late_template = self.request(
            "POST",
            "/api/strategy-templates",
            {
                "name": "Late-created version",
                "technical_weight": 60,
                "fundamental_weight": 25,
                "valuation_weight": 0,
                "portfolio_weight": 15,
                "fee_slippage_bps": 10,
                "activate": False,
            },
        )
        _, late_comparison = self.request("GET", "/api/strategies/compare?symbol=AAPL")
        late = next(
            item for item in late_comparison["comparisons"]
            if item["version_id"] == late_template["version_id"]
        )
        self.assertFalse(late["out_of_sample_available"])
        self.assertIn("not frozen", late["out_of_sample_reason"])
        self.assertIsNone(late_comparison["leader_version_id"])

        with open_db(self.db) as db:
            db.execute(
                "UPDATE investor_profiles SET strategy_style = 'growth', "
                "time_horizon = 'long_term', updated_at = ?",
                (now_iso(),),
            )
        _, changed_context = self.request("GET", "/api/strategies/compare?symbol=AAPL")
        changed_old_version = next(
            item for item in changed_context["comparisons"]
            if item["version_id"] == template["version_id"]
        )
        self.assertFalse(changed_old_version["out_of_sample_available"])
        self.assertIn("not frozen", changed_old_version["out_of_sample_reason"])

        self.request(
            "POST", "/api/trades",
            {"symbol": "AAPL", "side": "buy", "quantity": "20", "price": "100"},
        )
        with open_db(self.db) as db:
            db.execute(
                "UPDATE trades SET executed_at = ? WHERE symbol = 'AAPL'",
                ((date.today() - timedelta(days=1)).isoformat() + "T14:00:00Z",),
            )
        _, snapshot_data = self.request("GET", "/api/snapshot")
        self.assertGreater(len(snapshot_data["portfolio_performance"]["history"]), 1)
        self.assertEqual(
            {item["key"] for item in snapshot_data["portfolio_risk"]["stress_scenarios"]},
            {"market_down_5", "market_down_10", "technology_drawdown", "volatility_spike"},
        )
        self.assertTrue(snapshot_data["portfolio_actions"]["actions"])

        status, rebalance = self.request(
            "POST", "/api/portfolio/rebalance",
            {"targets": [{"symbol": "AAPL", "target_percent": "5"}, {"symbol": "SPY", "target_percent": "25"}]},
        )
        self.assertEqual(status, 200)
        self.assertEqual({row["symbol"] for row in rebalance["rows"]}, {"AAPL", "SPY"})
        self.assertEqual(rebalance["cash_target_percent"], "70")
        self.assertIn("no orders", rebalance["disclaimer"])

        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST", "/api/strategy-templates",
                {
                    "name": "Invalid weights", "technical_weight": 50,
                    "fundamental_weight": 20, "valuation_weight": 20,
                    "portfolio_weight": 5,
                },
            )
        self.assertEqual(error.exception.code, 400)

    def test_day_trade_live_levels_pdt_guardrails_and_execution_review(self):
        self.register()
        session_day = date.today().isoformat()
        snapshot_payload = {
            "latestTrade": {"p": 103.25, "t": f"{session_day}T14:34:00Z"},
            "latestQuote": {"bp": 103.20, "ap": 103.30},
            "dailyBar": {"h": 104, "l": 99, "c": 103.25},
            "prevDailyBar": {"h": 102, "l": 97, "c": 100},
        }
        bars_payload = {
            "bars": [
                {"t": f"{session_day}T13:00:00Z", "o": 100, "h": 101, "l": 99, "c": 100, "v": 100},
                {"t": f"{session_day}T13:30:00Z", "o": 101, "h": 103, "l": 100, "c": 102, "v": 200},
                {"t": f"{session_day}T13:34:00Z", "o": 102, "h": 104, "l": 101, "c": 103, "v": 300},
            ]
        }
        with patch("app._alpaca_credentials", return_value=("key", "secret", "keychain")), patch(
            "app._alpaca_json", side_effect=[snapshot_payload, bars_payload]
        ), patch("app._nasdaq_halts", return_value={}), patch(
            "app.market_clock",
            return_value={"session_phase": "regular", "is_open": True, "calendar": []},
        ):
            status, live = self.request("GET", "/api/day-trade/live/AAPL")
        self.assertEqual(status, 200)
        self.assertTrue(live["available"])
        self.assertEqual(live["latest_price"], "103.25")
        self.assertEqual(live["premarket_high"], "101.0000")
        self.assertEqual(live["opening_range_high"], "104.0000")
        self.assertFalse(live["halt"]["halted"])
        self.assertEqual(len(live["setups"]), 3)
        self.assertEqual(live["stored_bars"]["1Min"], 3)
        self.assertEqual(live["stored_bars"]["5Min"], 2)
        self.assertIn("replay", live)

        status, plan = self.request(
            "POST",
            "/api/plans/day-trade",
            {
                "symbol": "AAPL", "direction": "long",
                "hypothesis": "Test the opening range while price holds above VWAP.",
                "account_size": "20000", "entry": "100", "stop": "98", "target": "106",
                "risk_percent": "0.5", "max_position_percent": "10",
                "daily_loss_limit": "300", "current_daily_loss": "0",
                "minimum_reward_risk": "2", "premarket_high": "101",
                "premarket_low": "99", "vwap": "100.50",
                "opening_range_high": "104", "opening_range_low": "100",
                "support": "97", "resistance": "104", "halt_status": "clear",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(plan["analysis"]["key_levels"]["vwap"], "100.5")
        self.assertEqual(plan["analysis"]["halt_status"], "clear")
        status, review = self.request(
            "POST",
            f"/api/plans/{plan['id']}/reviews",
            {
                "decision": "followed", "outcome": "loss", "discipline_score": 3,
                "note": "Paper trade closed below the saved invalidation.",
                "actual_entry": "101", "actual_exit": "99",
                "execution_note": "Entered one point above plan.",
                "screenshot_data_url": "data:image/png;base64,iVBORw0KGgo=",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(review["execution_deviation_percent"], "1.00")
        self.assertEqual(review["realized_pnl"], "-40.00")
        self.assertTrue(review["has_screenshot"])

        for symbol in ("MSFT", "NVDA", "AMD", "SPY"):
            self.request(
                "POST", "/api/trades",
                {"symbol": symbol, "side": "buy", "quantity": "1", "price": "100"},
            )
            self.request(
                "POST", "/api/trades",
                {"symbol": symbol, "side": "sell", "quantity": "1", "price": "101"},
            )
        _, guardrails = self.request("GET", "/api/day-trade/guardrails")
        self.assertEqual(guardrails["estimated_day_trades"], 4)
        self.assertTrue(guardrails["pdt_threshold_reached"])
        self.assertEqual(guardrails["intraday_margin_status"], "broker_data_required")
        self.assertEqual(guardrails["regulatory_transition"]["broker_transition_deadline"], "2027-10-20")
        self.assertFalse(guardrails["stop_triggered"])
        self.assertEqual(guardrails["consecutive_losses"], 1)

        with patch("app._alpaca_credentials", return_value=(None, None, "unconfigured")):
            _, unavailable = self.request("GET", "/api/day-trade/live/TSLA")
        self.assertFalse(unavailable["available"])
        self.assertFalse(unavailable["halt"]["halted"])
        self.assertIn("not configured", unavailable["data_scope"])

    def test_live_day_trade_uses_regular_session_and_time_matched_relative_volume(self):
        self.register()
        ny = ZoneInfo("America/New_York")
        session_day = datetime.now(timezone.utc).astimezone(ny).date()
        prior_day = session_day - timedelta(days=1)
        while prior_day.weekday() >= 5:
            prior_day -= timedelta(days=1)

        def stamp(day_value, hour, minute):
            value = datetime.combine(day_value, time(hour, minute), tzinfo=ny)
            return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

        def bar(day_value, hour, minute, price, volume):
            return {
                "t": stamp(day_value, hour, minute), "o": price, "h": price + 1,
                "l": price - 1, "c": price, "v": volume,
            }

        bars_payload = {
            "bars": [
                bar(prior_day, 8, 0, 99, 10_000),
                bar(prior_day, 9, 30, 100, 100),
                bar(prior_day, 9, 34, 101, 100),
                bar(prior_day, 15, 0, 102, 20_000),
                bar(session_day, 8, 0, 100, 5_000),
                bar(session_day, 9, 30, 101, 100),
                bar(session_day, 9, 34, 102, 100),
            ]
        }
        snapshot_payload = {
            "latestTrade": {"p": 102}, "latestQuote": {"bp": 101.9, "ap": 102.1},
            "dailyBar": {"h": 103, "l": 99, "c": 102},
            "prevDailyBar": {"h": 102, "l": 98, "c": 100},
        }
        authoritative_clock = {
            "available": True, "is_open": True, "session_phase": "regular",
            "source": "test exchange clock", "calendar": [],
        }
        with patch("app._alpaca_credentials", return_value=("key", "secret", "test")), patch(
            "app._alpaca_json", side_effect=[snapshot_payload, bars_payload]
        ), patch("app._nasdaq_halts", return_value={}), patch(
            "app.market_clock", return_value=authoritative_clock
        ) as clock:
            _, live = self.request("GET", "/api/day-trade/live/AAPL")
        self.assertEqual(live["session_volume"], 200)
        self.assertEqual(live["relative_volume"], "1.00")
        self.assertEqual(live["session_phase"], "regular")
        self.assertEqual(live["session_volume_scope"], "regular_session")
        self.assertEqual(clock.call_count, 1)

    def test_sparse_replay_uses_opening_time_window_instead_of_first_five_bars(self):
        self.register()
        ny = ZoneInfo("America/New_York")
        session_day = datetime.now(timezone.utc).astimezone(ny).date()
        values = [
            (9, 30, 100, 101, 99, 100),
            (9, 32, 100, 102, 100, 101),
            (9, 34, 101, 102, 100, 101),
            (9, 35, 102, 104, 102, 103),
        ]
        with open_db(self.db) as db:
            for hour, minute, opening, high, low, close in values:
                timestamp = datetime.combine(
                    session_day, time(hour, minute), tzinfo=ny
                ).isoformat(timespec="seconds")
                db.execute(
                    "INSERT INTO intraday_bars VALUES (?, ?, '1Min', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "AAPL", timestamp, opening * 1_000_000, high * 1_000_000,
                        low * 1_000_000, close * 1_000_000, 1_000,
                        "alpaca_iex", now_iso(),
                    ),
                )
        _, replay = self.request(
            "GET", f"/api/day-trade/replay/AAPL?date={session_day.isoformat()}"
        )
        self.assertTrue(replay["available"])
        self.assertEqual(replay["opening_range_high"], "102")
        self.assertEqual(replay["opening_range_low"], "99")
        self.assertEqual(replay["trigger_index"], 3)
        self.assertEqual(replay["direction"], "long")

    def test_day_trade_round_trip_uses_new_york_trading_date_after_utc_midnight(self):
        self.register()
        ny = ZoneInfo("America/New_York")
        trading_day = datetime.now(timezone.utc).astimezone(ny).date()
        executed = datetime.combine(trading_day, time(20, 30), tzinfo=ny).astimezone(timezone.utc)
        self.request(
            "POST", "/api/trades",
            {"symbol": "AAPL", "side": "buy", "quantity": "1", "price": "100"},
        )
        self.request(
            "POST", "/api/trades",
            {"symbol": "AAPL", "side": "sell", "quantity": "1", "price": "101"},
        )
        with open_db(self.db) as db:
            db.execute(
                "UPDATE trades SET executed_at = ? WHERE symbol = 'AAPL'",
                (executed.isoformat(timespec="seconds").replace("+00:00", "Z"),),
            )
        _, guardrails = self.request("GET", "/api/day-trade/guardrails")
        self.assertEqual(guardrails["details"][0]["trading_date"], trading_day.isoformat())

    def test_option_chain_snapshots_candidates_and_cache(self):
        self.register()
        expiration = date.today() + timedelta(days=30)
        occ_date = expiration.strftime("%y%m%d")
        with open_db(self.db) as db:
            value = 102_000_000
            db.execute(
                "INSERT INTO market_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("AAPL", date.today().isoformat(), value, value, value, value, 1_000_000,
                 "alpha_vantage", now_iso()),
            )

        def snapshot(bid, ask, volume, iv, delta):
            return {
                "latestQuote": {"bp": bid, "ap": ask},
                "latestTrade": {"p": (bid + ask) / 2},
                "dailyBar": {"v": volume},
                "impliedVolatility": iv,
                "openInterest": 250,
                "greeks": {"delta": delta, "gamma": 0.03, "theta": -0.05, "vega": 0.12},
            }

        payload = {
            "snapshots": {
                f"AAPL{occ_date}C00100000": snapshot(4.8, 5.0, 120, 0.25, 0.58),
                f"AAPL{occ_date}C00105000": snapshot(2.3, 2.5, 90, 0.26, 0.42),
                f"AAPL{occ_date}P00100000": snapshot(2.7, 2.9, 110, 0.27, -0.42),
                f"AAPL{occ_date}P00095000": snapshot(1.2, 1.4, 80, 0.28, -0.25),
            }
        }
        with patch("app._alpaca_credentials", return_value=("key", "secret", "keychain")), patch(
            "app._alpaca_json", return_value=payload
        ) as alpaca:
            status, chain = self.request("GET", "/api/options/chain/AAPL")
            _, cached = self.request("GET", "/api/options/chain/AAPL")
            _, filtered = self.request(
                "GET", "/api/options/chain/AAPL?right=call&min_dte=7&max_dte=60&min_volume=100&max_spread_percent=20&liquid_only=true"
            )
        self.assertEqual(status, 200)
        self.assertTrue(chain["available"])
        self.assertEqual(chain["summary"]["contracts"], 4)
        self.assertEqual(chain["summary"]["liquid_contracts"], 4)
        self.assertTrue({
            "long_call", "long_put", "bull_call_spread", "bear_put_spread"
        }.issubset({item["strategy"] for item in chain["candidates"]}))
        self.assertTrue(cached["cache_hit"])
        self.assertEqual(alpaca.call_count, 1)
        self.assertEqual(filtered["filtered_contract_count"], 1)
        self.assertEqual(filtered["contracts"][0]["right"], "call")
        self.assertEqual(len(chain["analytics"]["term_structure"]), 1)
        self.assertEqual(chain["analytics"]["portfolio_greeks"]["matched_positions"], 0)
        with open_db(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM option_chain_snapshots").fetchone()[0], 1)

    def test_validation_dashboard_market_clock_scanner_and_verified_backup(self):
        self.register()
        self.request("POST", "/api/watchlist", {"symbol": "AAPL"})
        status, validation = self.request("GET", "/api/validation/dashboard?window_days=60")
        self.assertEqual(status, 200)
        self.assertEqual(validation["window_days"], 60)
        self.assertFalse(validation["ready_for_capital_review"])
        self.assertEqual(len(validation["readiness_gates"]), 6)
        self.assertEqual(validation["campaign"]["status"], "not_started")
        self.assertEqual(validation["campaign"]["minimum_days"], 30)
        self.assertEqual(validation["campaign"]["maximum_days"], 60)

        with patch("app._alpaca_credentials", return_value=("", "", "unconfigured")):
            _, clock = self.request("GET", "/api/day-trade/clock")
            _, scanner = self.request("GET", "/api/day-trade/scanner?limit=5")
        self.assertIn(clock["session_phase"], {"premarket", "regular", "closed"})
        self.assertEqual(scanner["symbols_requested"], 1)
        self.assertEqual(scanner["rows"], [])
        self.assertEqual(scanner["errors"][0]["symbol"], "AAPL")

        status, backup = self.request("POST", "/api/system/backup", {})
        self.assertEqual(status, 201)
        self.assertEqual(backup["integrity"], "ok")
        self.assertTrue(self.db.parent.joinpath("backups", backup["filename"]).exists())
        _, health = self.request("GET", "/api/system/health")
        self.assertEqual(health["database"]["latest_backup"]["filename"], backup["filename"])
        self.assertEqual(health["automation"]["recent_runs"][0]["job_type"], "manual_backup")

    def test_validation_campaign_uses_latest_frozen_context_cohort(self):
        self.register()
        with open_db(self.db) as db:
            user_id = db.execute("SELECT id FROM users").fetchone()[0]

            def insert_run(run_id, symbol, model, created_at, style, horizon):
                strategy = {
                    "style": style,
                    "horizon": horizon,
                    "config_hash": "same-weights",
                    "technical_weight": 60,
                    "fundamental_weight": 25,
                    "valuation_weight": 0,
                    "portfolio_weight": 15,
                }
                result = {
                    "id": run_id,
                    "symbol": symbol,
                    "model_version": model,
                    "created_at": created_at,
                    "trading_date": created_at[:10],
                    "signal": "watch",
                    "score": 60,
                    "strategy": strategy,
                    "data_quality": {"decision_eligible": True},
                    "price_plan": {"available": False, "targets_active": False},
                }
                db.execute(
                    "INSERT INTO decision_runs(id, user_id, symbol, model_version, context_hash, signal, score, result_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, user_id, symbol, model, run_id, "watch", 60, json.dumps(result), created_at),
                )

            insert_run("legacy", "AAPL", "decision-v3.0", (date.today() - timedelta(days=40)).isoformat() + "T12:00:00Z", "balanced", "swing")
            insert_run("old-context", "TSLA", "decision-v4.1", (date.today() - timedelta(days=10)).isoformat() + "T12:00:00Z", "balanced", "swing")
            insert_run("outside-window", "AMZN", "decision-v4.1", (date.today() - timedelta(days=90)).isoformat() + "T12:00:00Z", "growth", "long_term")
            insert_run("new-context-1", "MSFT", "decision-v4.1", (date.today() - timedelta(days=5)).isoformat() + "T12:00:00Z", "growth", "long_term")
            insert_run("new-context-2", "NVDA", "decision-v4.1", (date.today() - timedelta(days=4)).isoformat() + "T12:00:00Z", "growth", "long_term")

        status, validation = self.request("GET", "/api/validation/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(validation["window_days"], 60)
        self.assertEqual(validation["campaign"]["status"], "collecting")
        self.assertTrue(validation["campaign"]["parameters_frozen"])
        self.assertTrue(validation["campaign"]["restarted_after_context_change"])
        self.assertEqual(validation["campaign"]["strategy_contexts"], 1)
        self.assertEqual(validation["campaign"]["eligible_decisions"], 2)
        self.assertEqual(
            validation["campaign"]["started_at"],
            (date.today() - timedelta(days=5)).isoformat() + "T12:00:00Z",
        )
        self.assertLessEqual(validation["campaign"]["day_number"], 60)
        self.assertEqual(validation["coverage"]["decision_symbols"], 2)
        symbol_gate = next(
            gate for gate in validation["readiness_gates"]
            if gate["key"] == "symbol_coverage"
        )
        self.assertEqual(symbol_gate["value"], 2)

    def test_validation_operations_reports_and_missing_providers_are_explicit(self):
        self.register()
        for symbol in ("AAPL", "SOXX", "SPY", "MSFT", "JPM"):
            self.request("POST", "/api/watchlist", {"symbol": symbol})
        self.request(
            "PATCH", "/api/decision-settings",
            {"auto_refresh_enabled": True, "refresh_interval_hours": 24},
        )
        with patch("app._alpha_vantage_api_key", return_value=("", "unconfigured")), \
             patch("app._alpaca_credentials", return_value=("", "", "unconfigured")), \
             patch.dict(os.environ, {
                 "INVESTORLAB_INTRADAY_COLLECTION": "1",
                 "INVESTORLAB_OPTION_COLLECTION": "1",
             }):
            _, dashboard = self.request("GET", "/api/validation/dashboard?window_days=60")
            self.assertEqual(dashboard["operations"]["pool"]["count"], 5)
            self.assertFalse(dashboard["operations"]["automation"]["daily_decisions"])
            self.assertEqual(
                [item["key"] for item in dashboard["operations"]["blockers"]],
                ["alpha_vantage"],
            )

            _, notifications = self.request("GET", "/api/notifications/rules")
            operation_ids = {item["id"] for item in notifications["operational_alerts"]}
            self.assertIn("operation:alpha-vantage", operation_ids)
            self.assertIn("operation:alpaca", operation_ids)

            _, exported = self.request("GET", "/api/validation/report")
            self.assertTrue(exported["filename"].endswith(".md"))
            self.assertIn("## Readiness gates", exported["markdown"])

            _, cycle = self.request("POST", "/api/validation/run", {})
            self.assertEqual(cycle["status"], "partial")
            self.assertEqual(
                {item["component"] for item in cycle["blocked"]},
                {"daily_decisions", "intraday_options"},
            )
            _, reports = self.request("GET", "/api/reports")
            self.assertEqual({item["period"] for item in reports}, {"daily", "weekly"})
            with open_db(self.db) as db:
                validation_run = db.execute(
                    "SELECT status FROM data_collection_runs "
                    "WHERE job_type = 'validation_cycle' ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
                misleading_intraday = db.execute(
                    "SELECT COUNT(*) FROM data_collection_runs WHERE job_type = 'intraday_scan'"
                ).fetchone()[0]
            self.assertEqual(validation_run["status"], "partial")
            self.assertEqual(misleading_intraday, 0)

    def test_stored_decision_outcome_validation_uses_later_bars(self):
        self.register()
        with open_db(self.db) as db:
            user_id = db.execute("SELECT id FROM users").fetchone()[0]
            start = date.today() - timedelta(days=20)
            for index in range(21):
                close = 100_000_000 + index * 100_000
                high = 106_000_000 if index == 1 else close + 500_000
                low = close - 500_000
                db.execute(
                    "INSERT INTO market_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("AAPL", (start + timedelta(days=index)).isoformat(), close, high, low,
                     close, 1_000_000, "alpha_vantage", now_iso()),
                )
            result = {
                "id": "validation-run", "symbol": "AAPL", "trading_date": start.isoformat(),
                "signal": "buy_candidate", "score": 82,
                "price_plan": {
                    "available": True, "targets_active": True, "reference_price": "100",
                    "target_1": "105", "risk_stop": "95",
                },
            }
            db.execute(
                "INSERT INTO decision_runs(id, user_id, symbol, model_version, context_hash, signal, score, result_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("validation-run", user_id, "AAPL", "decision-v4.0", "validation-context",
                 "buy_candidate", 82, json.dumps(result), now_iso()),
            )
        status, bundle = self.request("GET", "/api/decisions/AAPL")
        self.assertEqual(status, 200)
        validation = bundle["validation"]
        self.assertTrue(validation["available"])
        self.assertEqual(validation["target_first"], 1)
        self.assertEqual(validation["target_first_rate_percent"], "100.00")
        self.assertEqual(validation["outcomes"][0]["resolution"], "target_first")

    def test_price_alerts_trigger_once_rearm_and_keep_history(self):
        self.register()
        status, rule = self.request(
            "POST",
            "/api/alerts",
            {"symbol": "AAPL", "direction": "above", "threshold": "150"},
        )
        self.assertEqual(status, 201)
        self.assertFalse(rule["is_triggered"])
        self.assertIsNone(rule["latest_price"])

        def add_close(trading_date, close):
            with open_db(self.db) as db:
                value = close * 1_000_000
                db.execute(
                    "INSERT INTO market_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("AAPL", trading_date, value, value, value, value, 1_000_000, "test", f"{trading_date}T22:00:00Z"),
                )

        add_close("2026-08-10", 160)
        _, alerts = self.request("GET", "/api/alerts")
        self.assertTrue(alerts["rules"][0]["is_triggered"])
        self.assertEqual(alerts["rules"][0]["latest_price"], "160")
        self.assertEqual(len(alerts["recent_triggers"]), 1)
        self.assertEqual(alerts["recent_triggers"][0]["observed_price"], "160")
        self.assertEqual(len(self.request("GET", "/api/alerts")[1]["recent_triggers"]), 1)

        add_close("2026-08-11", 140)
        self.assertFalse(self.request("GET", "/api/alerts")[1]["rules"][0]["is_triggered"])
        add_close("2026-08-12", 170)
        _, alerts = self.request("GET", "/api/alerts")
        self.assertTrue(alerts["rules"][0]["is_triggered"])
        self.assertEqual(len(alerts["recent_triggers"]), 2)

        _, sync = self.request("GET", "/api/sync?since=0")
        self.assertEqual(len(sync["snapshot"]["alerts"]["recent_triggers"]), 2)
        self.assertIn("price_alert_trigger", {event["entity_type"] for event in sync["events"]})

        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST",
                "/api/alerts",
                {"symbol": "AAPL", "direction": "above", "threshold": "150"},
            )
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.request("DELETE", f"/api/alerts/{rule['id']}")[0], 200)
        _, remaining = self.request("GET", "/api/alerts")
        self.assertEqual(remaining["rules"], [])
        self.assertEqual(len(remaining["recent_triggers"]), 2)

        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST",
                "/api/alerts",
                {"symbol": "AAPL", "direction": "crosses", "threshold": "150"},
            )
        self.assertEqual(error.exception.code, 400)

    def test_journal_review_stats_and_portfolio_risk_are_derived(self):
        self.register()
        self.request(
            "POST", "/api/trades", {"symbol": "AAPL", "side": "buy", "quantity": "3", "price": "100"}
        )
        self.request(
            "POST", "/api/trades", {"symbol": "MSFT", "side": "buy", "quantity": "1", "price": "100"}
        )
        with open_db(self.db) as db:
            db.execute(
                "INSERT INTO market_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("AAPL", "2026-08-11", 199_000_000, 202_000_000, 198_000_000, 200_000_000, 1_000_000, "test", "2026-08-12T00:00:00Z"),
            )

        status, first = self.request(
            "POST",
            "/api/journal",
            {
                "symbol": "AAPL",
                "kind": "review",
                "setup_tag": "breakout",
                "title": "Followed the invalidation",
                "body": "The exit matched the rule recorded before entry.",
                "outcome": "win",
                "discipline_score": 5,
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(first["setup_tag"], "breakout")
        self.request(
            "POST",
            "/api/journal",
            {
                "symbol": "MSFT",
                "kind": "review",
                "setup_tag": "breakout",
                "title": "Late invalidation",
                "body": "The supplied stop was not followed on the first test.",
                "outcome": "loss",
                "discipline_score": 3,
            },
        )
        self.request(
            "POST",
            "/api/journal",
            {
                "symbol": "AAPL",
                "kind": "lesson",
                "setup_tag": "process",
                "title": "Wait for confirmation",
                "body": "Record the observable trigger before the session opens.",
                "outcome": "na",
            },
        )

        _, risk = self.request("GET", "/api/portfolio/risk")
        self.assertEqual(risk["gross_exposure"], "700")
        self.assertEqual(risk["positions"][0]["symbol"], "AAPL")
        self.assertEqual(risk["positions"][0]["weight_percent"], "85.71")
        self.assertEqual(risk["positions"][0]["account_weight_percent"], "2.40")
        self.assertEqual(risk["sectors"][0]["weight_percent"], "100.00")
        self.assertEqual(risk["live_price_count"], 1)
        self.assertEqual(risk["fallback_price_count"], 1)

        _, stats = self.request("GET", "/api/analytics/review")
        self.assertEqual(stats["entries"], 3)
        self.assertEqual(stats["reviews"], 2)
        self.assertEqual(stats["win_rate_percent"], "50.0")
        self.assertEqual(stats["average_discipline_score"], "4.0")
        self.assertEqual(stats["setup_counts"][0], {"tag": "breakout", "count": 2})
        _, sync = self.request("GET", "/api/sync?since=0")
        self.assertEqual(len(sync["snapshot"]["journal_entries"]), 3)
        self.assertEqual(sync["snapshot"]["review_stats"]["resolved_reviews"], 2)
        self.assertIn("journal_entry", {event["entity_type"] for event in sync["events"]})

        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST",
                "/api/journal",
                {
                    "symbol": "AAPL",
                    "kind": "lesson",
                    "setup_tag": "process",
                    "title": "Invalid outcome",
                    "body": "Lessons cannot fabricate a trade outcome.",
                    "outcome": "win",
                },
            )
        self.assertEqual(error.exception.code, 400)

    def test_equity_and_option_positions_are_separate_and_options_use_contract_multiplier(self):
        self.register()
        self.request(
            "POST", "/api/trades",
            {"symbol": "AAPL", "asset_type": "equity", "side": "buy", "quantity": "10", "price": "200"},
        )
        self.request(
            "POST", "/api/trades",
            {"symbol": "AAPL", "asset_type": "option", "side": "buy", "quantity": "2", "price": "5.5"},
        )
        with self.assertRaises(HTTPError) as error:
            self.request(
                "POST", "/api/trades",
                {"symbol": "AAPL", "asset_type": "option", "side": "sell", "quantity": "3", "price": "6.5"},
            )
        self.assertEqual(error.exception.code, 400)

        self.request(
            "POST", "/api/trades",
            {"symbol": "AAPL", "asset_type": "option", "side": "sell", "quantity": "1", "price": "6.5"},
        )
        _, snapshot = self.request("GET", "/api/snapshot")
        positions = {
            (item["symbol"], item["asset_type"]): item
            for item in snapshot["portfolio"]["positions"]
        }
        self.assertEqual(positions[("AAPL", "equity")]["quantity"], "10")
        self.assertEqual(positions[("AAPL", "option")]["quantity"], "1")
        self.assertEqual(positions[("AAPL", "option")]["realized_pnl"], "100")
        self.assertEqual(snapshot["portfolio"]["realized_pnl"], "100")
        self.assertEqual(snapshot["portfolio_performance"]["open_cost_basis"], "2550")
        self.assertEqual(snapshot["portfolio_performance"]["estimated_cash"], "22550")
        self.assertEqual(snapshot["portfolio_performance"]["estimated_account_value"], "25100")
        risk_positions = {
            (item["symbol"], item["asset_type"]): item
            for item in snapshot["portfolio_risk"]["positions"]
        }
        self.assertEqual(risk_positions[("AAPL", "equity")]["account_weight_percent"], "8.00")
        self.assertEqual(risk_positions[("AAPL", "option")]["exposure"], "550")
        self.assertEqual(risk_positions[("AAPL", "option")]["account_weight_percent"], "2.20")
        self.assertEqual(
            app._position_value_micros(2_000_000, 5_500_000, "option"),
            1_100_000_000,
        )

    def test_option_scenario_is_zero_at_the_quoted_spot_and_uses_extrinsic_decay(self):
        self.register()
        _, scenario = self.request(
            "POST", "/api/options/scenario",
            {
                "spot": "100", "days_to_expiration": 30,
                "quoted_days_to_expiration": 30,
                "legs": [
                    {"right": "call", "side": "buy", "strike": "80", "premium": "20.5", "quantity": 1},
                ],
            },
        )
        unchanged = next(
            point for point in scenario["payoff_points"]
            if point["underlying"] == "100.00"
        )
        self.assertEqual(unchanged["modeled_pnl"], "0.00")
        self.assertEqual(scenario["modeled_theta_per_day"], "-0.83")

    def test_same_timestamp_uses_insertion_order_per_user(self):
        user, _ = register_user(self.db, VALID_ACCOUNT)
        with open_db(self.db) as db:
            db.execute(
                "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "z-buy",
                    user["id"],
                    "MSFT",
                    "equity",
                    "buy",
                    2_000_000,
                    100_000_000,
                    "2026-01-01T00:00:00Z",
                ),
            )
            db.execute(
                "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "a-sell",
                    user["id"],
                    "MSFT",
                    "equity",
                    "sell",
                    1_000_000,
                    110_000_000,
                    "2026-01-01T00:00:00Z",
                ),
            )

        result = portfolio(self.db, user["id"])
        self.assertEqual(result["positions"][0]["quantity"], "1")
        self.assertEqual(result["realized_pnl"], "10")

    def test_schema_17_strategy_versions_options_replay_backup_restore_and_health(self):
        self.register()
        base_template = {
            "name": "Versioned momentum",
            "technical_weight": 60,
            "fundamental_weight": 15,
            "valuation_weight": 10,
            "portfolio_weight": 15,
            "fee_slippage_bps": 8,
            "activate": True,
        }
        _, first = self.request("POST", "/api/strategy-templates", base_template)
        _, second = self.request(
            "POST", "/api/strategy-templates",
            {**base_template, "technical_weight": 55, "fundamental_weight": 20, "fee_slippage_bps": 12},
        )
        self.assertEqual(first["version_number"], 1)
        self.assertEqual(second["version_number"], 2)
        _, versions = self.request("GET", "/api/strategy-versions")
        self.assertEqual([item["version_number"] for item in versions], [2, 1])
        self.assertNotEqual(versions[0]["config_hash"], versions[1]["config_hash"])

        expiration = (date.today() + timedelta(days=30)).isoformat()
        _, straddle = self.request(
            "POST", "/api/plans/options",
            {
                "symbol": "AAPL", "strategy": "long_straddle",
                "hypothesis": "Test a defined-debit volatility structure around the same strike.",
                "expiration": expiration, "quantity": "1",
                "primary_strike": "100", "primary_premium": "5",
                "secondary_strike": "100", "secondary_premium": "4",
            },
        )
        self.assertEqual(straddle["analysis"]["max_loss"], "900")
        self.assertEqual(straddle["analysis"]["breakevens"], ["91", "109"])
        _, condor = self.request(
            "POST", "/api/plans/options",
            {
                "symbol": "AAPL", "strategy": "iron_condor",
                "hypothesis": "Test a four-leg range structure with bounded expiration risk.",
                "expiration": expiration, "quantity": "1",
                "primary_strike": "95", "primary_premium": "1",
                "secondary_strike": "100", "secondary_premium": "2",
                "tertiary_strike": "105", "tertiary_premium": "2",
                "quaternary_strike": "110", "quaternary_premium": "1",
            },
        )
        self.assertEqual(condor["analysis"]["max_loss"], "300")
        self.assertEqual(condor["analysis"]["max_profit"], "200")
        self.assertEqual(condor["analysis"]["net_premium_label"], "net credit")

        session_day = date.today().isoformat()
        with open_db(self.db) as db:
            minute_values = [
                ("09:30", 100, 101, 99, 100),
                ("09:31", 100, 101, 99, 100),
                ("09:32", 100, 101, 99, 100),
                ("09:33", 100, 101, 99, 100),
                ("09:34", 100, 101, 99, 100),
                ("09:35", 101, 102, 101, 102),
                ("09:36", 102, 108, 102, 107),
            ]
            for minute, opening, high, low, close in minute_values:
                db.execute(
                    "INSERT INTO intraday_bars VALUES (?, ?, '1Min', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "AAPL", f"{session_day}T{minute}:00-04:00",
                        opening * 1_000_000, high * 1_000_000, low * 1_000_000,
                        close * 1_000_000, 1000, "alpaca_iex", now_iso(),
                    ),
                )
        _, replay = self.request("GET", f"/api/day-trade/replay/AAPL?date={session_day}")
        self.assertTrue(replay["available"])
        self.assertEqual(len(replay["bars"]), 7)
        self.assertEqual(replay["direction"], "long")
        self.assertEqual(replay["outcome"], "target")

        self.request("POST", "/api/watchlist", {"symbol": "AAPL"})
        _, backup = self.request("POST", "/api/system/backup", {})
        self.request("POST", "/api/watchlist", {"symbol": "MSFT"})
        _, backups = self.request("GET", "/api/system/backups")
        self.assertTrue(backups[0]["restorable"])
        _, restored = self.request(
            "POST", "/api/system/restore",
            {"filename": backup["filename"], "confirmation": f"RESTORE {backup['filename']}"},
        )
        self.assertTrue(restored["restored"])
        _, restored_snapshot = self.request("GET", "/api/snapshot")
        self.assertEqual([item["symbol"] for item in restored_snapshot["watchlist"]], ["AAPL"])
        _, health = self.request("POST", "/api/system/health-check", {})
        self.assertEqual(health["schema_version"], 17)
        self.assertEqual(health["database"]["integrity"], "ok")
        self.assertTrue(health["checks"])

    def test_alpaca_paper_account_is_read_only_and_synchronized(self):
        self.register()
        paper_payloads = [
            {
                "id": "paper-account", "account_number": "PA123", "status": "ACTIVE",
                "currency": "USD", "cash": "25000", "equity": "26000",
                "last_equity": "25500", "portfolio_value": "26000", "buying_power": "50000",
                "regt_buying_power": "50000", "daytrading_buying_power": "0",
                "multiplier": "2", "daytrade_count": "1", "pattern_day_trader": False,
                "trading_blocked": False, "transfers_blocked": False, "account_blocked": False,
            },
            [{"asset_id": "asset", "symbol": "AAPL", "exchange": "NASDAQ", "asset_class": "us_equity", "side": "long", "qty": "5", "avg_entry_price": "190", "market_value": "1000", "cost_basis": "950", "unrealized_pl": "50", "unrealized_plpc": "0.0526", "current_price": "200", "lastday_price": "198", "change_today": "0.0101"}],
            [{"id": "order-1", "client_order_id": "client-1", "symbol": "AAPL", "asset_class": "us_equity", "side": "buy", "type": "limit", "time_in_force": "day", "status": "filled", "qty": "5", "filled_qty": "5", "filled_avg_price": "190"}],
            [{"id": "fill-1", "activity_type": "FILL", "transaction_time": now_iso(), "symbol": "AAPL", "qty": "5", "price": "190", "side": "buy", "order_id": "order-1"}],
        ]
        with patch("app._alpaca_credentials", return_value=("key", "secret", "keychain")), patch(
            "app._alpaca_trading_json", side_effect=paper_payloads
        ) as trading_api:
            status, paper = self.request("POST", "/api/alpaca/paper-account/sync", {})
        self.assertEqual(status, 200)
        self.assertEqual(trading_api.call_count, 4)
        self.assertTrue(paper["read_only"])
        self.assertEqual(paper["positions"][0]["symbol"], "AAPL")
        self.assertTrue(paper["paper_order_routing_available"])
        self.assertIn("locked by default", paper["scope"])
        _, cached = self.request("GET", "/api/alpaca/paper-account")
        self.assertEqual(cached["account"]["equity"], "26000")
        _, synced = self.request("GET", "/api/snapshot")
        self.assertEqual(synced["paper_account"]["provider"], "Alpaca Paper Trading API")

    def test_schema_17_gated_paper_order_lifecycle_and_idempotency(self):
        self.register()
        paper_payloads = [
            {
                "id": "paper-account", "status": "ACTIVE", "cash": "25000",
                "equity": "25000", "buying_power": "50000", "trading_blocked": False,
                "account_blocked": False,
            },
            [{"symbol": "AAPL", "side": "long", "qty": "5", "current_price": "200"}],
            [],
            [],
        ]
        with patch("app._alpaca_credentials", return_value=("key", "secret", "test")), patch(
            "app._alpaca_trading_json", side_effect=paper_payloads
        ):
            self.request("POST", "/api/alpaca/paper-account/sync", {})
        with open_db(self.db) as db:
            db.execute(
                "INSERT INTO market_daily VALUES ('AAPL', ?, 200000000, 201000000, 199000000, 200000000, 1000000, 'test', ?)",
                (date.today().isoformat(), now_iso()),
            )
        with self.assertRaises(HTTPError) as gate_error:
            self.request(
                "PATCH", "/api/alpaca/paper-orders/control",
                {"enabled": True, "max_order_notional": "1000", "daily_loss_limit": "300"},
            )
        self.assertEqual(gate_error.exception.code, 400)
        _, control = self.request(
            "PATCH", "/api/alpaca/paper-orders/control",
            {"enabled": True, "max_order_notional": "1000", "daily_loss_limit": "300", "acknowledged": True},
        )
        self.assertTrue(control["enabled"])
        order_payload = {
            "symbol": "AAPL", "side": "buy", "order_type": "limit", "time_in_force": "day",
            "quantity": "2", "limit_price": "199", "client_order_id": "schema16-order-1",
        }
        with self.assertRaises(HTTPError) as order_error:
            self.request("POST", "/api/alpaca/paper-orders", order_payload)
        self.assertEqual(order_error.exception.code, 400)
        order_payload["acknowledged"] = True
        broker_order = {"id": "paper-order-1", "status": "accepted", "filled_qty": "0"}
        with patch("app._alpaca_credentials", return_value=("key", "secret", "test")), patch(
            "app._alpaca_trading_mutation", return_value=broker_order
        ) as mutation:
            status, order = self.request("POST", "/api/alpaca/paper-orders", order_payload)
            self.assertEqual(status, 201)
            self.assertEqual(order["estimated_notional"], "398")
            _, replay = self.request("POST", "/api/alpaca/paper-orders", order_payload)
            self.assertTrue(replay["idempotent_replay"])
            self.request(
                "POST", f"/api/alpaca/paper-orders/{order['id']}/cancel",
                {"confirmation": "CANCEL PAPER AAPL"},
            )
        self.assertEqual(mutation.call_count, 2)
        _, legacy_locked = self.request(
            "PATCH", "/api/alpaca/paper-orders/control",
            {"enabled": False, "max_order_notional": "1000", "daily_loss_limit": "300"},
        )
        self.assertFalse(legacy_locked["enabled"])
        _, legacy_enabled = self.request(
            "PATCH", "/api/alpaca/paper-orders/control",
            {"enabled": True, "max_order_notional": "1000", "daily_loss_limit": "300", "confirmation": "enable paper orders"},
        )
        self.assertTrue(legacy_enabled["enabled"])
        _, ledger = self.request("GET", "/api/alpaca/paper-orders")
        self.assertEqual(ledger["orders"][0]["status"], "canceled")

    def test_schema_17_scanner_notifications_options_and_command_center(self):
        self.register()
        self.request("POST", "/api/watchlist", {"symbol": "AAPL"})
        with open_db(self.db) as db:
            for index in range(21):
                trading_day = date.today() - timedelta(days=20 - index)
                close = (180 + index) * 1_000_000
                db.execute(
                    "INSERT INTO market_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("AAPL", trading_day.isoformat(), close, close + 1_000_000, close - 1_000_000, close, 2_000_000 + index, "test", now_iso()),
                )
        _, preset = self.request(
            "POST", "/api/scanner-presets",
            {"name": "Liquid cached", "symbols": ["AAPL"], "filters": {"minimum_price": 10, "minimum_average_volume": 1000000}},
        )
        _, scan = self.request("POST", "/api/scanner/run", {"preset_id": preset["id"]})
        self.assertEqual(scan["matched"], 1)
        self.assertIn("no paid market-data", scan["cost_model"])
        _, rule = self.request(
            "POST", "/api/notifications/rules",
            {"kind": "data_stale", "symbol": "AAPL", "config": {"threshold": 30}},
        )
        self.assertEqual(rule["kind"], "data_stale")
        _, notifications = self.request("GET", "/api/notifications/rules")
        self.assertEqual(len(notifications["rules"]), 1)
        _, scenario = self.request(
            "POST", "/api/options/scenario",
            {
                "spot": "200", "days_to_expiration": 30, "iv_shift_percent": 10,
                "legs": [
                    {"right": "call", "side": "buy", "strike": "195", "premium": "8", "quantity": 1},
                    {"right": "call", "side": "sell", "strike": "210", "premium": "3", "quantity": 1},
                ],
            },
        )
        self.assertEqual(len(scenario["payoff_points"]), 25)
        self.assertEqual(len(scenario["legs"]), 2)
        _, report = self.request("POST", "/api/reports", {"period": "daily"})
        self.assertEqual(report["period"], "daily")
        self.assertEqual(report["calculation_version"], app.PORTFOLIO_CALCULATION_VERSION)
        _, command = self.request("GET", "/api/research/command-center")
        self.assertEqual(command["counts"]["scanner_presets"], 1)
        self.assertFalse(command["paper_execution"]["real_account_supported"])
        _, copilot = self.request(
            "POST", "/api/research/copilot",
            {"symbol": "AAPL", "question": "What evidence is missing?"},
        )
        self.assertIn("no external LLM", copilot["engine"])
        self.request("DELETE", f"/api/scanner-presets/{preset['id']}")
        self.request("DELETE", f"/api/notifications/rules/{rule['id']}")

    def test_every_historical_schema_version_upgrades_to_current(self):
        seed = Path(self.temp.name) / "migration-seed-v1.sqlite3"
        with sqlite3.connect(seed) as db:
            db.executescript(
                """
                CREATE TABLE watchlist(symbol TEXT PRIMARY KEY, created_at TEXT NOT NULL);
                CREATE TABLE trades(
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity_micros INTEGER NOT NULL,
                    price_micros INTEGER NOT NULL,
                    executed_at TEXT NOT NULL
                );
                PRAGMA user_version = 1;
                """
            )

        snapshots = {1: seed}
        with sqlite3.connect(seed) as source:
            for version in range(1, app.SCHEMA_VERSION):
                migration = getattr(app, f"_migrate_v{version}_to_v{version + 1}")
                migration(source)
                snapshot = Path(self.temp.name) / f"migration-seed-v{version + 1}.sqlite3"
                with sqlite3.connect(snapshot) as destination:
                    source.backup(destination)
                snapshots[version + 1] = snapshot

        for source_version, snapshot in snapshots.items():
            upgraded = Path(self.temp.name) / f"upgraded-from-v{source_version}.sqlite3"
            with sqlite3.connect(snapshot) as source, sqlite3.connect(upgraded) as destination:
                source.backup(destination)
            init_db(upgraded)
            with self.subTest(source_version=source_version), open_db(upgraded) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], app.SCHEMA_VERSION)
                self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertIn(
                    "role", {row["name"] for row in db.execute("PRAGMA table_info(users)")}
                )
                session_columns = {
                    row["name"] for row in db.execute("PRAGMA table_info(sessions)")
                }
                self.assertIn("device_id", session_columns)
                self.assertIn("client_type", session_columns)

    def test_v1_data_is_archived_then_claimed_by_first_account(self):
        legacy = Path(self.temp.name) / "legacy.sqlite3"
        with sqlite3.connect(legacy) as db:
            db.executescript(
                """
                CREATE TABLE watchlist(symbol TEXT PRIMARY KEY, created_at TEXT NOT NULL);
                CREATE TABLE trades(
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity_micros INTEGER NOT NULL,
                    price_micros INTEGER NOT NULL,
                    executed_at TEXT NOT NULL
                );
                INSERT INTO watchlist VALUES ('NVDA', '2026-01-01T00:00:00Z');
                INSERT INTO trades VALUES (
                    'legacy-trade', 'NVDA', 'equity', 'buy', 1000000, 100000000,
                    '2026-01-01T00:00:00Z'
                );
                PRAGMA user_version = 1;
                """
            )
        init_db(legacy)
        user, _ = register_user(legacy, VALID_ACCOUNT)
        with open_db(legacy) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 17)
            self.assertIsNotNone(
                db.execute("SELECT name FROM sqlite_master WHERE name = 'market_daily'").fetchone()
            )
            self.assertIsNotNone(
                db.execute("SELECT name FROM sqlite_master WHERE name = 'research_plans'").fetchone()
            )
            self.assertIsNotNone(
                db.execute("SELECT name FROM sqlite_master WHERE name = 'journal_entries'").fetchone()
            )
            self.assertIsNotNone(
                db.execute("SELECT name FROM sqlite_master WHERE name = 'price_alerts'").fetchone()
            )
            self.assertIsNotNone(
                db.execute("SELECT name FROM sqlite_master WHERE name = 'investor_profiles'").fetchone()
            )
            self.assertIsNotNone(
                db.execute("SELECT name FROM sqlite_master WHERE name = 'plan_reviews'").fetchone()
            )
            self.assertIsNotNone(
                db.execute("SELECT name FROM sqlite_master WHERE name = 'portfolio_imports'").fetchone()
            )
            self.assertIsNotNone(
                db.execute("SELECT name FROM sqlite_master WHERE name = 'decision_runs'").fetchone()
            )
            self.assertIsNotNone(
                db.execute("SELECT name FROM sqlite_master WHERE name = 'decision_settings'").fetchone()
            )
            self.assertIn(
                "device_id",
                {row["name"] for row in db.execute("PRAGMA table_info(sessions)").fetchall()},
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM watchlist_v1_archive").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM trades_v1_archive").fetchone()[0], 1)
            self.assertEqual(
                db.execute("SELECT user_id FROM watchlist").fetchone()["user_id"], user["id"]
            )
            self.assertEqual(
                db.execute("SELECT user_id FROM trades").fetchone()["user_id"], user["id"]
            )


if __name__ == "__main__":
    unittest.main()
