from __future__ import annotations

import unittest
from unittest.mock import patch

from app_radar.mailer import send_report


class MailerTests(unittest.TestCase):
    def test_missing_recipient_skips_without_connecting(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            sent, message = send_report("subject", "<p>html</p>", "text")
        self.assertFalse(sent)
        self.assertIn("EMAIL_TO", message)

    def test_invalid_security_mode_is_rejected(self) -> None:
        environment = {
            "EMAIL_TO": "reader@example.com",
            "EMAIL_FROM": "radar@example.com",
            "SMTP_HOST": "smtp.example.com",
            "SMTP_SECURITY": "invalid",
        }
        with patch.dict("os.environ", environment, clear=True):
            sent, message = send_report("subject", "<p>html</p>", "text")
        self.assertFalse(sent)
        self.assertIn("SMTP_SECURITY", message)


if __name__ == "__main__":
    unittest.main()
