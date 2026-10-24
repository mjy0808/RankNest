from __future__ import annotations

import unittest

from app_radar.models import Candidate, ScoredCandidate
from app_radar.opportunities import build_theme_summaries


def themed_item(segment: str, key: str, name: str) -> ScoredCandidate:
    candidate = Candidate(
        source="steam" if segment == "non_mobile_game" else "apple",
        external_id=name, name=name, kind="app" if segment == "app" else "game",
        metadata={"opportunity_theme_key": key, "opportunity_fit": 80},
    )
    return ScoredCandidate(candidate, 75, {}, {}, (), None)


class OpportunityThemeTests(unittest.TestCase):
    def test_shared_game_theme_is_counted_separately_per_category(self) -> None:
        sections = {
            segment: [themed_item(segment, "puzzle", f"{segment}-{index}") for index in range(2)]
            for segment in ("mobile_game", "non_mobile_game")
        }
        themes = build_theme_summaries(sections)
        self.assertEqual(len(themes), 2)
        for theme in themes:
            self.assertEqual(theme["count"], 2)
            self.assertEqual(theme["examples"], [item.candidate.name for item in sections[theme["segment"]]])

    def test_singletons_from_different_categories_do_not_form_a_shared_theme(self) -> None:
        sections = {
            segment: [themed_item(segment, "puzzle", segment)]
            for segment in ("mobile_game", "non_mobile_game")
        }
        self.assertEqual(build_theme_summaries(sections), [])

    def test_theme_limit_applies_independently_to_each_category(self) -> None:
        sections = {
            segment: [
                themed_item(segment, key, f"{segment}-{key}-{index}")
                for key in ("puzzle", "idle_tycoon") for index in range(2)
            ]
            for segment in ("mobile_game", "non_mobile_game")
        }
        themes = build_theme_summaries(sections, limit=1)
        self.assertEqual(len(themes), 2)
        self.assertEqual({theme["segment"] for theme in themes}, set(sections))
