from __future__ import annotations

import unittest
from pathlib import Path

from app_radar.config import load_config


class ConfigTests(unittest.TestCase):
    def test_runtime_includes_desktop_and_web_game_sources(self) -> None:
        config = load_config(Path(__file__).resolve().parents[1] / "config.json")
        self.assertTrue(config.apple.enabled)
        self.assertTrue(config.steam.enabled)
        self.assertTrue(config.itch.enabled)
        self.assertIn("web", config.itch.platforms)
        self.assertEqual(config.report.segment_counts, {"app": 8, "mobile_game": 6, "non_mobile_game": 6})
        self.assertEqual(
            config.report.segment_early_counts, {"app": 2, "mobile_game": 2, "non_mobile_game": 2}
        )


if __name__ == "__main__":
    unittest.main()
