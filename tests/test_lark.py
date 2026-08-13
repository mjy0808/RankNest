from __future__ import annotations

import unittest
from unittest.mock import patch

from app_radar.lark import build_card, send_card


class LarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = {
            "generated_at": "2026-08-13T08:30:00+08:00",
            "run_id": 7,
            "total_candidates": 2549,
            "source_status": [{"state": "healthy"}, {"state": "failed"}],
            "sections": {
                "app": [
                    {"rank": 1, "name": "Fresh Notes", "score": 91.2, "reasons": ["榜位上升"]}
                ],
                "mobile_game": [],
                "steam_game": [],
            },
        }

    def test_card_contains_summary_and_pages_link(self) -> None:
        card = build_card(self.payload, "https://mjy0808.github.io/RankNest/")
        self.assertEqual(card["msg_type"], "interactive")
        body = str(card)
        self.assertIn("Fresh Notes", body)
        self.assertIn("2,549", body)
        self.assertIn("https://mjy0808.github.io/RankNest/", body)

    def test_rejects_non_lark_webhook_host(self) -> None:
        with self.assertRaises(ValueError):
            send_card("https://example.com/hook/secret", build_card(self.payload, "https://example.com"))

    @patch("app_radar.lark.urlopen")
    def test_accepts_successful_lark_response(self, mocked_urlopen) -> None:
        response = mocked_urlopen.return_value.__enter__.return_value
        response.read.return_value = b'{"code":0,"msg":"success"}'
        send_card(
            "https://open.larksuite.com/open-apis/bot/v2/hook/test",
            build_card(self.payload, "https://mjy0808.github.io/RankNest/"),
        )
        mocked_urlopen.assert_called_once()
