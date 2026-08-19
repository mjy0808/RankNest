from __future__ import annotations

import unittest
from pathlib import Path

from app_radar.config import load_config


class ConfigTests(unittest.TestCase):
    def test_default_runtime_disables_steam_segment(self) -> None:
        config = load_config(Path(__file__).resolve().parents[1] / "config.json")
        self.assertTrue(config.apple.enabled)
        self.assertFalse(config.steam.enabled)
        self.assertEqual(config.report.segment_counts, {"app": 8, "mobile_game": 6})
        self.assertEqual(
            config.report.segment_early_counts, {"app": 2, "mobile_game": 2}
        )


if __name__ == "__main__":
    unittest.main()
