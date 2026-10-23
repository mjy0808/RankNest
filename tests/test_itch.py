from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock

from app_radar.collectors.itch import collect_itch, parse_feed
from app_radar.config import ItchConfig
from app_radar.models import Candidate, ScoredCandidate, SourceStatus
from app_radar.report import _item_dict
from app_radar.scoring import score_all, select_segment_items
from datetime import date


FEED = (Path(__file__).parent / "fixtures/itch_feed.xml").read_text(encoding="utf-8")


class ItchTests(unittest.TestCase):
    def test_rss_game_has_unknown_metrics_and_uses_non_mobile_segment(self) -> None:
        items = parse_feed(FEED, "web", "new-and-popular", 30)
        self.assertEqual(len(items), 1)
        candidate = items[0]
        self.assertEqual(candidate.segment, "non_mobile_game")
        self.assertEqual(candidate.name, "Tiny Repair Workshop")
        self.assertEqual(candidate.metadata["platforms"], ["Web"])
        self.assertEqual(candidate.developer, "Tiny Studio")
        self.assertIsNone(candidate.release_date)
        self.assertEqual(candidate.update_date, date(2026, 10, 22))
        self.assertEqual(candidate.image_url, "https://img.itch.zone/repair.png")
        scored = score_all(items, {}, [SourceStatus("Itch/GLOBAL", 30)], date(2026, 10, 23))
        payload = _item_dict(1, scored[0])
        self.assertIsNone(payload["review_count"])
        self.assertIsNone(payload["rating"])
        self.assertIsNone(payload["competition_score"])
        self.assertEqual(payload["competition_level"], "竞争待核实")

    def test_original_creation_date_is_distinct_from_recent_update(self) -> None:
        feed = FEED.replace("<pubDate>", "<createDate>Sun, 01 Feb 2026 10:00:00 GMT</createDate><pubDate>")
        candidate = parse_feed(feed, "web", "new-and-popular", 30)[0]
        self.assertEqual(candidate.release_date, date(2026, 2, 1))
        self.assertEqual(candidate.update_date, date(2026, 10, 22))

    def test_known_franchise_cannot_enter_non_mobile_rankings(self) -> None:
        games = [Candidate("steam", name, name, "game") for name in (
            "FINAL FANTASY RESONANCE", "ONE PIECE: Grand Gourmet",
            "NBA 2K27", "Onimusha: Way of the Sword",
        )]
        scored = score_all(games, {}, [SourceStatus("Steam/US", 100)], date(2026, 10, 23))
        self.assertEqual(select_segment_items(scored, {"non_mobile_game": 6})["non_mobile_game"], [])

    def test_same_game_merges_platforms_and_one_feed_failure_is_degraded(self) -> None:
        client = Mock()
        client.get_text.return_value = FEED
        config = ItchConfig(platforms=("web", "windows"), charts=("popular",), limit=1)
        candidates, statuses = collect_itch(config, client)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].metadata["platforms"], ["Web", "Windows"])
        self.assertEqual(statuses[0].state, "healthy")

        def partial(url: str) -> str:
            if "platform-windows" in url:
                raise RuntimeError("temporary timeout")
            return FEED

        client.get_text.side_effect = partial
        candidates, statuses = collect_itch(config, client)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(statuses[0].state, "degraded")
        self.assertIn("temporary timeout", statuses[0].detail)

    def test_non_mobile_ranking_keeps_sources_web_and_unique_titles(self) -> None:
        def item(source: str, name: str, score: float) -> ScoredCandidate:
            candidate = Candidate(
                source, name, name, "game", metadata={
                    "opportunity_fit": 90, "opportunity_tier": "validated",
                    "platforms": ["Web"] if name == "Browser Puzzle" else ["Windows"],
                }
            )
            return ScoredCandidate(candidate, score, {}, {}, (), None)

        sections = select_segment_items([
            item("steam", "Shared Game", 99),
            item("itch", "Shared Game", 98),
            item("steam", "Steam Game", 97),
            item("steam", "Another Steam Game", 96),
            item("itch", "Desktop Indie", 80),
            item("itch", "Browser Puzzle", 65),
        ], {"non_mobile_game": 4})
        selected = sections["non_mobile_game"]
        self.assertEqual(len({value.candidate.name for value in selected}), 4)
        self.assertEqual({value.candidate.source for value in selected}, {"steam", "itch"})
        self.assertIn("Browser Puzzle", {value.candidate.name for value in selected})
