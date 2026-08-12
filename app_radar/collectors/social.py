from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from ..http import HttpClient
from ..models import Candidate, SourceStatus


HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).split())


def _candidate_phrases(name: str) -> tuple[str, ...]:
    names = [name]
    phrases: list[str] = []
    for value in names:
        phrase = _normalize(value)
        compact_length = len(phrase.replace(" ", ""))
        # Single generic words create too many false matches (for example
        # "Edits"). Keep exact multi-word names, or distinctive long tokens.
        if compact_length >= 8 or (" " in phrase and compact_length >= 6):
            phrases.append(phrase)
    return tuple(phrases)


def _eligible_for_discussion_signal(candidate: Candidate, today: datetime) -> bool:
    """Avoid letting ubiquitous incumbents dominate cold-start social matching."""
    if candidate.release_date is None:
        return candidate.review_count < 250_000
    age_days = (today.date() - candidate.release_date).days
    return age_days <= 730 or candidate.review_count < 250_000


def apply_hacker_news_mentions(
    candidates: list[Candidate], client: HttpClient, lookback_hours: int, max_stories: int
) -> SourceStatus:
    after = int((datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp())
    hits: list[dict[str, object]] = []
    page = 0
    page_size = min(max(max_stories, 1), 1000)
    error = ""
    try:
        while len(hits) < max_stories:
            params = urlencode(
                {
                    "tags": "story",
                    "numericFilters": f"created_at_i>{after}",
                    "hitsPerPage": min(page_size, max_stories - len(hits)),
                    "page": page,
                }
            )
            payload = client.get_json(f"{HN_SEARCH_URL}?{params}")
            page_hits = payload.get("hits", [])
            if not isinstance(page_hits, list):
                raise RuntimeError("Hacker News hits field is missing")
            hits.extend(hit for hit in page_hits if isinstance(hit, dict))
            page += 1
            if not page_hits or page >= int(payload.get("nbPages", page)):
                break
    except Exception as exc:
        error = str(exc)
        if not hits:
            return SourceStatus("Hacker News", 0, "failed", error)

    normalized_titles = [
        _normalize(str(hit.get("title") or hit.get("story_title") or ""))
        for hit in hits
    ]
    now = datetime.now(timezone.utc)
    for candidate in candidates:
        if not _eligible_for_discussion_signal(candidate, now):
            candidate.social_mentions = 0
            continue
        phrases = _candidate_phrases(candidate.name)
        if not phrases:
            continue
        candidate.social_mentions = sum(
            1
            for title in normalized_titles
            if any(f" {phrase} " in f" {title} " for phrase in phrases)
        )
    matched = sum(1 for candidate in candidates if candidate.social_mentions)
    state = "degraded" if error else "healthy"
    detail = f"matched {matched} monitored products"
    if error:
        detail += f"; pagination stopped: {error}"
    return SourceStatus("Hacker News", len(hits), state, detail)
