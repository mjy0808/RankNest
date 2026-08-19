from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from app_radar.lark import _already_sent, _mark_sent, build_card, build_failure_card, send_card


class LarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = {
            "generated_at": "2026-08-13T08:30:00+08:00",
            "run_id": 7,
            "total_candidates": 2549,
            "source_status": [{"state": "healthy"}, {"state": "failed"}],
            "themes": [{"label": "高频轻工具", "count": 3}],
            "backtest": {
                "horizons": {
                    "7": {"confirmation_rate": 60, "confirmed": 12, "total": 20}
                }
            },
            "sections": {
                "app": [
                    {
                        "rank": 1,
                        "name": "Fresh Notes",
                        "score": 91.2,
                        "reasons": ["榜位上升"],
                        "early_candidate": True,
                        "early_signal_score": 78,
                    }
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
        self.assertIn("高频轻工具", body)
        self.assertIn("7 日历史命中率", body)
        self.assertIn("更早期苗头", body)
        self.assertIn("Fresh Notes(78)", body)
        self.assertNotIn("Steam 游戏", body)
        self.assertIn("https://mjy0808.github.io/RankNest/", body)

    def test_failure_card_links_to_workflow(self) -> None:
        card = build_failure_card("https://github.com/example/runs/1", "build=failure")
        self.assertIn("build=failure", str(card))
        self.assertIn("https://github.com/example/runs/1", str(card))

    def test_same_day_state_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "lark-state.json"
            _mark_sent(state, "2026-08-13", "abc")
            self.assertTrue(_already_sent(state, "2026-08-13"))
            self.assertFalse(_already_sent(state, "2026-08-14"))

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

    @patch("app_radar.lark.time.sleep")
    @patch("app_radar.lark.urlopen")
    def test_retries_rate_limit(self, mocked_urlopen, mocked_sleep) -> None:
        response = mocked_urlopen.return_value.__enter__.return_value
        response.read.return_value = b'{"code":0,"msg":"success"}'
        mocked_urlopen.side_effect = [
            HTTPError("https://example", 429, "rate limited", {}, None),
            mocked_urlopen.return_value,
        ]
        send_card(
            "https://open.larksuite.com/open-apis/bot/v2/hook/test",
            build_card(self.payload, "https://mjy0808.github.io/RankNest/"),
            backoff_seconds=0,
        )
        self.assertEqual(mocked_urlopen.call_count, 2)
        mocked_sleep.assert_called_once()
