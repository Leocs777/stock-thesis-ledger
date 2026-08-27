import shutil
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from investor_lab.encrypted_backup import create_encrypted_backup, drill_encrypted_backup
from investor_lab.market_quality import assess_daily_bars, compare_prices, intraday_coverage, option_snapshot_quality
from investor_lab.security import RequestRateLimiter, read_security_events, record_login_event


class PhaseTwoQualityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_rate_limit_and_privacy_preserving_unusual_login_audit(self):
        limiter = RequestRateLimiter()
        self.assertEqual(limiter.check("login", 2, 60, current=100), (True, 0))
        self.assertEqual(limiter.check("login", 2, 60, current=101), (True, 0))
        self.assertEqual(limiter.check("login", 2, 60, current=102)[0], False)
        database = self.root / "ledger.sqlite3"
        first = record_login_event(
            database, successful=True, user_id="user-1", email="owner@example.com",
            address="127.0.0.1", device_id="iphone-a", client_type="ios",
        )
        second = record_login_event(
            database, successful=True, user_id="user-1", email="owner@example.com",
            address="192.0.2.10", device_id="iphone-b", client_type="ios",
        )
        self.assertFalse(first["unusual_login"])
        self.assertTrue(second["unusual_login"])
        raw = (self.root / "security-audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("owner@example.com", raw)
        self.assertNotIn("192.0.2.10", raw)
        self.assertEqual(len(read_security_events(database, user_id="user-1")["events"]), 2)

    def test_market_quality_covers_daily_intraday_cross_source_and_options(self):
        start = date(2026, 1, 1)
        bars = [
            {
                "trading_date": (start + timedelta(days=index)).isoformat(),
                "open_micros": 100_000_000, "high_micros": 102_000_000,
                "low_micros": 99_000_000, "close_micros": 101_000_000,
                "volume": 1_000_000,
            }
            for index in range(60)
        ]
        quality = assess_daily_bars(
            bars, historically_adjusted=True, current_date=start + timedelta(days=59)
        )
        self.assertTrue(quality["decision_eligible"])
        self.assertEqual(compare_prices("100", "104")["status"], "warning")
        session = date(2026, 8, 26)
        stamps = [datetime(2026, 8, 26, 9, 30) + timedelta(minutes=index) for index in range(30)]
        coverage = intraday_coverage(stamps, session_date=session, as_of=stamps[-1])
        self.assertEqual((coverage["status"], coverage["missing_minutes"]), ("ready", 0))
        options = option_snapshot_quality([
            {"bid": "2", "ask": "1", "spread_percent": "25", "liquid": False},
            {"bid": "1", "ask": "1.05", "spread_percent": "4.88", "liquid": True},
        ])
        self.assertEqual((options["crossed_markets"], options["wide_spreads"]), (1, 1))
        self.assertEqual(options["status"], "blocked")

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
    def test_encrypted_backup_restore_drill_never_changes_active_database(self):
        database = self.root / "active.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA user_version = 17")
            connection.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
            connection.execute("INSERT INTO evidence VALUES ('original')")
        before = database.read_bytes()
        created = create_encrypted_backup(database, self.root / "offsite", "phase-two-test-passphrase")
        encrypted = Path(created["path"])
        self.assertNotIn(b"original", encrypted.read_bytes())
        drill = drill_encrypted_backup(encrypted, database, "phase-two-test-passphrase")
        self.assertTrue(drill["drill_passed"])
        self.assertTrue(drill["active_database_unchanged"])
        self.assertEqual(database.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
