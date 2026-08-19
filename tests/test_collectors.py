from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from app_radar.collectors.apple import enrich_with_lookup, parse_feed, parse_mobile_game_feed
from app_radar.collectors.social import (
    _eligible_for_discussion_signal,
    apply_hacker_news_mentions,
)
from app_radar.collectors.steam import parse_search_payload
from app_radar.models import Candidate


FIXTURES = Path(__file__).parent / "fixtures"


class AppleCollectorTests(unittest.TestCase):
    def test_feed_and_lookup_are_merged(self) -> None:
        payload = json.loads((FIXTURES / "apple_feed.json").read_text(encoding="utf-8"))
        candidates = parse_feed(payload, "us", "top-free")
        enrich_with_lookup(
            candidates,
            [
                {
                    "trackId": 1001,
                    "trackName": "Fresh Notes",
                    "primaryGenreName": "Productivity",
                    "sellerName": "Tiny Studio LLC",
                    "averageUserRating": 4.8,
                    "userRatingCount": 321,
                    "releaseDate": "2026-08-01T00:00:00Z",
                    "currentVersionReleaseDate": "2026-08-10T00:00:00Z",
                    "formattedPrice": "Free",
                    "description": "A focused workflow tool for small teams.",
                }
            ],
            "us",
        )
        candidate = candidates["1001"]
        self.assertEqual(candidate.best_rank, 1)
        self.assertEqual(candidate.developer, "Tiny Studio LLC")
        self.assertEqual(candidate.review_count, 321)
        self.assertAlmostEqual(candidate.rating, 4.8)
        self.assertIn("workflow", candidate.metadata["description"])

    def test_legacy_game_feed_creates_mobile_game_candidate(self) -> None:
        payload = json.loads(
            (FIXTURES / "apple_mobile_games.json").read_text(encoding="utf-8")
        )
        candidates = parse_mobile_game_feed(payload, "us", "top-free")
        candidate = candidates["3003"]
        self.assertEqual(candidate.segment, "mobile_game")
        self.assertEqual(candidate.best_rank, 1)
        self.assertIn("us:mobile-top-free", candidate.ranks)


class SteamCollectorTests(unittest.TestCase):
    def test_search_html_is_parsed(self) -> None:
        payload = json.loads((FIXTURES / "steam_search.json").read_text(encoding="utf-8"))
        candidates = parse_search_payload(payload, "us", "topsellers")
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.external_id, "2002")
        self.assertEqual(candidate.name, "Fresh Game")
        self.assertEqual(candidate.review_count, 1234)
        self.assertAlmostEqual(candidate.rating, 4.6)
        self.assertEqual(candidate.price_text, "$9.99")


class SocialCollectorTests(unittest.TestCase):
    def test_mature_high_volume_product_is_excluded_from_name_matching(self) -> None:
        candidate = Candidate(
            source="apple",
            external_id="old",
            name="Ubiquitous Product",
            kind="app",
            release_date=date(2018, 1, 1),
            review_counts={"us": 2_000_000},
        )
        now = datetime(2026, 8, 11, tzinfo=timezone.utc)
        self.assertFalse(_eligible_for_discussion_signal(candidate, now))

    def test_recent_product_keeps_discussion_signal(self) -> None:
        candidate = Candidate(
            source="apple",
            external_id="new",
            name="New Product",
            kind="app",
            release_date=date(2026, 7, 1),
            review_counts={"us": 900_000},
        )
        now = datetime(2026, 8, 11, tzinfo=timezone.utc)
        self.assertTrue(_eligible_for_discussion_signal(candidate, now))

    def test_category_trend_is_applied_when_exact_name_is_absent(self) -> None:
        class FakeClient:
            def get_json(self, _url: str) -> dict[str, object]:
                return {
                    "hits": [
                        {"title": "AI agents are changing productivity workflows"},
                        {"title": "A practical guide to workflow automation"},
                    ],
                    "nbPages": 1,
                }

        candidate = Candidate(
            source="apple",
            external_id="workflow",
            name="Quiet Desk",
            kind="app",
            release_date=date(2026, 8, 1),
            metadata={"genre": "Productivity"},
        )
        status = apply_hacker_news_mentions([candidate], FakeClient(), 48, 10)  # type: ignore[arg-type]
        self.assertGreater(candidate.metadata["hn_topic_mentions"], 0)
        self.assertGreater(candidate.social_mentions, 0)
        self.assertIn("category trends", status.detail)


if __name__ == "__main__":
    unittest.main()
