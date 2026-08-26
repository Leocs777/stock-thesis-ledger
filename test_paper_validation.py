from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app import init_db, open_db, register_user
from scripts.paper_validation import (
    create_baseline,
    report_paths,
    require_current_schema,
    status_summary,
    write_new_json,
)


class PaperValidationOperatorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        init_db(self.db)
        user, _ = register_user(
            self.db,
            {
                "email": "owner@example.test",
                "password": "Correct-Horse-Battery-77",
                "display_name": "Owner",
                "device_id": "web-test-device",
                "device_name": "Test browser",
            },
        )
        self.user_id = user["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_baseline_is_bounded_and_contains_no_identity_or_credentials(self):
        with open_db(self.db) as db:
            db.execute(
                "INSERT INTO watchlist(id, user_id, symbol, created_at) VALUES (?, ?, ?, ?)",
                ("watch-1", self.user_id, "AAPL", "2026-08-26T12:00:00Z"),
            )
        with patch("scripts.paper_validation.source_commit", return_value="abc123"):
            payload = create_baseline(
                self.db,
                self.user_id,
                started_at=datetime(2026, 8, 26, 12, tzinfo=timezone.utc),
                provider_status={"alpha_vantage_configured": True, "alpaca_paper_iex_configured": False},
            )
        rendered = str(payload)
        self.assertEqual(payload["watchlist"], ["AAPL"])
        self.assertEqual(payload["software"]["source_commit"], "abc123")
        self.assertEqual(payload["campaign"]["day_30_review_at"], "2026-09-25T12:00:00Z")
        self.assertNotIn("owner@example.test", rendered)
        self.assertNotIn(self.user_id, rendered)
        self.assertNotIn("api_key", rendered.lower())

    def test_baseline_write_is_immutable_and_report_names_are_timestamped(self):
        output = Path(self.temp.name) / "baseline.json"
        write_new_json(output, {"ok": True})
        with self.assertRaises(FileExistsError):
            write_new_json(output, {"ok": False})
        markdown, evidence = report_paths(Path(self.temp.name), "2026-08-26T12:34:56Z")
        self.assertEqual(markdown.name, "validation-20260826-123456.md")
        self.assertEqual(evidence.name, "validation-20260826-123456.json")

    def test_status_summary_surfaces_gate_and_blocker(self):
        text = status_summary(
            {
                "campaign": {"status": "collecting", "day_number": 3, "maximum_days": 60, "model_version": "decision-v4.1", "parameters_frozen": True},
                "ready_for_capital_review": False,
                "readiness_gates": [{"passed": False, "label": "30 days", "value": 2, "required": 30}],
                "operations": {"blockers": [{"label": "Daily data", "detail": "Key required."}]},
            }
        )
        self.assertIn("NOT READY", text)
        self.assertIn("30 days: 2 / 30", text)
        self.assertIn("Daily data: Key required.", text)

    def test_schema_mismatch_is_reported_before_operator_queries(self):
        with open_db(self.db) as db:
            db.execute("PRAGMA user_version = 16")
        with self.assertRaisesRegex(RuntimeError, "start the current app once"):
            require_current_schema(self.db)


if __name__ == "__main__":
    unittest.main()
