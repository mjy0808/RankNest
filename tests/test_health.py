from __future__ import annotations

import unittest

from app_radar.config import HealthConfig
from app_radar.health import assess_source_health
from app_radar.models import SourceStatus


class HealthTests(unittest.TestCase):
    def test_sudden_item_drop_is_degraded(self) -> None:
        statuses = [SourceStatus("Apple/US", 190)]
        assessed = assess_source_health(
            statuses,
            {"Apple/US": 360},
            HealthConfig(previous_count_ratio=0.65, apple_min_items=180),
        )
        self.assertEqual(assessed[0].state, "degraded")
        self.assertIn("previous healthy snapshot", assessed[0].detail)

    def test_normal_item_count_stays_healthy(self) -> None:
        statuses = [SourceStatus("Steam/US", 120)]
        assessed = assess_source_health(
            statuses,
            {"Steam/US": 125},
            HealthConfig(previous_count_ratio=0.65, steam_min_items=70),
        )
        self.assertEqual(assessed[0].state, "healthy")


if __name__ == "__main__":
    unittest.main()
