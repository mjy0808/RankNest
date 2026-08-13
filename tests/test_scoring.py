from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from app_radar.models import Candidate, HistorySnapshot, PreviousObservation, SourceStatus
from app_radar.scoring import score_all, select_segment_items


def make_candidate(
    external_id: str, segment: str = "app", rank: int = 20, reviews: int = 1200
) -> Candidate:
    source = "steam" if segment == "steam_game" else "apple"
    kind = "game" if segment != "app" else "app"
    return Candidate(
        source=source,
        external_id=external_id,
        name=f"Candidate {external_id}",
        kind=kind,
        release_date=date(2026, 7, 1),
        ranks={"us:top": rank, "jp:top": rank + 2, "de:top": rank + 4},
        ratings={"us": 4.7},
        review_counts={"us": reviews},
        metadata={"genre": "Productivity" if segment == "app" else "Puzzle"},
    )


def statuses() -> list[SourceStatus]:
    return [
        SourceStatus("Apple/US", 100),
        SourceStatus("Apple/JP", 100),
        SourceStatus("Apple/DE", 100),
        SourceStatus("Steam/US", 100),
        SourceStatus("Steam/JP", 100),
        SourceStatus("Steam/DE", 100),
    ]


class ScoringTests(unittest.TestCase):
    def test_paired_growth_beats_flat_history(self) -> None:
        growing_candidate = make_candidate("growing", rank=10, reviews=1200)
        flat_candidate = make_candidate("flat", rank=30, reviews=1200)
        previous_growing = PreviousObservation(
            captured_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
            run_day=date(2026, 8, 10),
            average_rank=42,
            best_rank=35,
            review_count=600,
            market_count=3,
            social_mentions=0,
            ranks={"us:top": 40, "jp:top": 42, "de:top": 44},
            review_counts={"us": 600},
        )
        previous_flat = PreviousObservation(
            captured_at=previous_growing.captured_at,
            run_day=previous_growing.run_day,
            average_rank=32,
            best_rank=30,
            review_count=1200,
            market_count=3,
            social_mentions=0,
            ranks=dict(flat_candidate.ranks),
            review_counts={"us": 1200},
        )
        snapshot = HistorySnapshot(
            run_id=1,
            run_day=date(2026, 8, 10),
            captured_at=previous_growing.captured_at,
            actual_days=1,
            observations={
                growing_candidate.key: previous_growing,
                flat_candidate.key: previous_flat,
            },
            healthy_markets=frozenset({("apple", "us"), ("apple", "jp"), ("apple", "de")}),
        )
        scored = score_all(
            [growing_candidate, flat_candidate], {1: snapshot}, statuses(), date(2026, 8, 11)
        )
        by_id = {item.candidate.external_id: item for item in scored}
        self.assertGreater(by_id["growing"].score, by_id["flat"].score)
        self.assertTrue(any("配对榜位上升" in reason for reason in by_id["growing"].reasons))

    def test_three_segments_are_selected_independently(self) -> None:
        candidates = []
        for segment in ("app", "mobile_game", "steam_game"):
            for index in range(5):
                candidates.append(make_candidate(f"{segment}-{index}", segment=segment, rank=index + 1))
        scored = score_all(candidates, {}, statuses(), date(2026, 8, 11))
        sections = select_segment_items(
            scored, {"app": 3, "mobile_game": 2, "steam_game": 4}
        )
        self.assertEqual({key: len(value) for key, value in sections.items()}, {
            "app": 3, "mobile_game": 2, "steam_game": 4
        })
        for segment, items in sections.items():
            self.assertTrue(all(item.candidate.segment == segment for item in items))
            self.assertAlmostEqual(sum(items[0].component_weights.values()), 100.0, places=3)
            self.assertEqual(items[0].component_weights["讨论热度"], 0.0)

    def test_copyable_niche_beats_mature_network_product(self) -> None:
        niche = make_candidate("niche", rank=8, reviews=1_200)
        mature = make_candidate("mature", rank=1, reviews=50_000_000)
        mature.name = "Mature Social Network"
        mature.release_date = date(2015, 1, 1)
        mature.metadata = {"genre": "Social Networking"}
        scored = score_all([niche, mature], {}, statuses(), date(2026, 8, 13))
        by_id = {item.candidate.external_id: item for item in scored}
        self.assertGreater(by_id["niche"].score, by_id["mature"].score)
        self.assertGreater(
            niche.metadata["opportunity_fit"], mature.metadata["opportunity_fit"]
        )
        self.assertIn("成熟头部产品", mature.metadata["opportunity_tags"])

    def test_ip_and_low_fit_products_are_not_selected(self) -> None:
        safe = make_candidate("safe", segment="steam_game", rank=5, reviews=500)
        safe.name = "Tiny Repair Simulator"
        risky = make_candidate("risky", segment="steam_game", rank=1, reviews=500)
        risky.name = "Famous Franchise™ Legacy Edition"
        scored = score_all([safe, risky], {}, statuses(), date(2026, 8, 13))
        sections = select_segment_items(
            scored, {"app": 1, "mobile_game": 1, "steam_game": 2}
        )
        ids = [item.candidate.external_id for item in sections["steam_game"]]
        self.assertIn("safe", ids)
        self.assertNotIn("risky", ids)


if __name__ == "__main__":
    unittest.main()
