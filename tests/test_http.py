from __future__ import annotations

import unittest
from http.client import IncompleteRead
from unittest.mock import MagicMock, patch

from app_radar.http import HttpClient


class HttpClientTests(unittest.TestCase):
    @patch("app_radar.http.time.sleep")
    @patch("app_radar.http.urlopen")
    def test_retries_incomplete_response(self, mocked_urlopen, mocked_sleep) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        mocked_urlopen.side_effect = [IncompleteRead(b"partial", 10), response]
        payload = HttpClient(timeout=1, retries=1).get_json("https://example.test/data")
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(mocked_urlopen.call_count, 2)
        mocked_sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
