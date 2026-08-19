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
                stored_metadata = json.loads(
                    store.connection.execute("SELECT metadata_json FROM observations").fetchone()[0]
                )
                self.assertNotIn("opportunity_card", stored_metadata)
                first_seen = store.load_first_seen_days(
                    date(2026, 8, 12),
                    "Asia/Shanghai",
                    {candidate.key, ("apple", "unseen")},
                )
                self.assertEqual(first_seen[candidate.key], 1)
                self.assertEqual(first_seen[("apple", "unseen")], 0)

            sections = select_segment_items(scored, {"app": 1, "mobile_game": 1, "steam_game": 1})
            candidate.metadata["selected_streak"] = 2
            artifacts = write_report(
                root / "reports", "测试日报", captured, sections, statuses, 1,
                [], run_id, "abc123",
                themes=[{
                    "key": "focused_utility", "label": "高频轻工具", "count": 2,
                    "validated_count": 1, "average_score": 80.0, "average_fit": 90.0,
                    "examples": ["Small Wonder", "Another Tool"], "signal": "测试主题信号",
                }],
                backtest={"available_horizons": [], "horizons": {}, "definition": "测试"},
            )
            self.assertTrue(artifacts.latest_html.exists())
            self.assertIn("Small Wonder", artifacts.html_body)
            self.assertIn("连续 2 天", artifacts.html_body)
            self.assertIn("首次上榜", artifacts.html_body)
            self.assertTrue((root / "reports/archive/index.html").exists())
            self.assertEqual(artifacts.dated_html.parent.name, "archive")
            self.assertTrue(artifacts.dated_json.exists())
            self.assertIn("JSON", (root / "reports/archive/index.html").read_text())
            payload = json.loads(artifacts.latest_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 5)
            self.assertEqual(payload["run_id"], run_id)
            self.assertEqual(payload["data_fingerprint"], "abc123")
            self.assertEqual(payload["sections"]["app"][0]["external_id"], "42")
            self.assertEqual(payload["sections"]["app"][0]["selected_streak"], 2)
            self.assertIn("opportunity_fit", payload["sections"]["app"][0])
            self.assertIn("build_angle", payload["sections"]["app"][0])
            self.assertIn("opportunity_card", payload["sections"]["app"][0])
            self.assertEqual(payload["sections"]["app"][0]["opportunity_tier"], "watch")
            self.assertIn("early_signal_score", payload["sections"]["app"][0])
            self.assertIn("competition_score", payload["sections"]["app"][0])
            self.assertEqual(payload["themes"][0]["label"], "高频轻工具")


if __name__ == "__main__":
    unittest.main()
