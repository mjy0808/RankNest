from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from app_radar.models import Candidate, SourceStatus
from app_radar.report import write_report
from app_radar.scoring import score_all, select_segment_items
from app_radar.storage import RadarStore


class StorageAndReportTests(unittest.TestCase):
    def test_snapshot_is_idempotent_and_report_is_traceable(self) -> None:
        candidate = Candidate(
            source="apple",
            external_id="42",
            name="Small Wonder",
            kind="app",
            developer="Wonder Lab",
            url="https://example.com/small-wonder",
            release_date=date(2026, 8, 1),
            ranks={"us:top-free": 5, "jp:top-free": 12},
            ratings={"us": 4.9},
            review_counts={"us": 777},
        )
        statuses = [SourceStatus("Apple/US", 100), SourceStatus("Apple/JP", 100)]
        scored = score_all([candidate], {}, statuses, date(2026, 8, 11))
        captured = datetime(2026, 8, 11, 0, 30, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "radar.db"
            with RadarStore(database) as store:
                run_id, replaced = store.save_run(
                    captured, date(2026, 8, 11), "Asia/Shanghai", "abc123",
                    root / "reports/2026-08-11.html", statuses, scored,
                )
                self.assertFalse(replaced)
                same_run_id, replaced = store.save_run(
                    captured, date(2026, 8, 11), "Asia/Shanghai", "abc123",
                    root / "reports/2026-08-11.html", statuses, scored,
                )
                self.assertTrue(replaced)
                self.assertEqual(run_id, same_run_id)
                count = store.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
                self.assertEqual(count, 1)

            sections = select_segment_items(scored, {"app": 1, "mobile_game": 1, "steam_game": 1})
            artifacts = write_report(
                root / "reports", "测试日报", captured, sections, statuses, 1,
                [], run_id, "abc123",
            )
            self.assertTrue(artifacts.latest_html.exists())
            self.assertIn("Small Wonder", artifacts.html_body)
            self.assertNotIn("<img", artifacts.html_body)
            payload = json.loads(artifacts.latest_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["run_id"], run_id)
            self.assertEqual(payload["data_fingerprint"], "abc123")
            self.assertEqual(payload["sections"]["app"][0]["external_id"], "42")


if __name__ == "__main__":
    unittest.main()
