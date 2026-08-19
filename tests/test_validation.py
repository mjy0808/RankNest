from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from app_radar.models import Candidate, HistorySnapshot, PreviousObservation
from app_radar.validation import build_backtest


class ValidationTests(unittest.TestCase):
    def test_backtest_counts_confirmed_and_missing_recommendations(self) -> None:
        growing = Candidate(
            source="apple",
            external_id="growing",
            name="Growing Tool",
            kind="app",
            ranks={"us:top": 10},
            review_counts={"us": 150},
        )
        captured = datetime(2026, 8, 12, tzinfo=timezone.utc)
        observations = {
            growing.key: PreviousObservation(
                captured_at=captured,
                run_day=date(2026, 8, 12),
                average_rank=30,
                best_rank=25,
                review_count=100,
                market_count=1,
                social_mentions=0,
                selected_rank=1,
                name="Growing Tool",
                segment="app",
            ),
            ("apple", "missing"): PreviousObservation(
                captured_at=captured,
                run_day=date(2026, 8, 12),
                average_rank=20,
                best_rank=15,
                review_count=100,
                market_count=1,
                social_mentions=0,
                selected_rank=2,
                name="Missing Tool",
                segment="app",
            ),
            ("steam", "old-game"): PreviousObservation(
                captured_at=captured,
                run_day=date(2026, 8, 12),
                average_rank=5,
                best_rank=3,
                review_count=500,
                market_count=2,
                social_mentions=0,
                selected_rank=1,
                name="Old Steam Recommendation",
                segment="steam_game",
            ),
        }
        snapshot = HistorySnapshot(
            run_id=1,
            run_day=date(2026, 8, 12),
            captured_at=captured,
            actual_days=7,
            observations=observations,
            healthy_markets=frozenset({("apple", "us")}),
        )
        result = build_backtest([growing], {7: snapshot})
        metric = result["horizons"]["7"]
        self.assertEqual(metric["total"], 2)
        self.assertEqual(metric["retained"], 1)
        self.assertEqual(metric["confirmed"], 1)
        self.assertEqual(metric["confirmation_rate"], 50.0)


if __name__ == "__main__":
    unittest.main()
