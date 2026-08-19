from __future__ import annotations

import json
import time
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HttpClient:
    def __init__(self, timeout: int = 25, retries: int = 2) -> None:
        self.timeout = timeout
        self.retries = retries
        self.user_agent = "AppGamePotentialRadar/0.1 (+daily public-data research)"

    def get_text(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                request = Request(
                    url,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
                    },
                )
                with urlopen(request, timeout=self.timeout) as response:
                    return response.read().decode("utf-8", errors="replace")
            except (HTTPError, URLError, TimeoutError, OSError, HTTPException) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(0.5 * (2**attempt))
        raise RuntimeError(f"GET failed after {self.retries + 1} attempts: {url}: {last_error}")

    def get_json(self, url: str) -> dict[str, Any]:
        text = self.get_text(url)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            preview = " ".join(text[:120].split())
            raise RuntimeError(f"expected JSON from {url}, got {preview!r}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"expected a JSON object from {url}")
        return data
