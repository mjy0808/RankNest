from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from ..http import HttpClient
from ..models import Candidate, SourceStatus


HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"

HN_TOPICS: dict[str, tuple[str, ...]] = {
    "AI 与自动化": (" ai ", " llm ", "agent", "automation", "chatbot"),
    "效率与工作流": ("productivity", "workflow", "task manager", "calendar", "notes app"),
    "隐私与安全": ("privacy", "security", "encryption", "authentication", "password"),
    "图片与视频": ("camera", "photo", "image", "video", "creator tool"),
    "学习与教育": ("education", "learning", "study", "language learning", "course"),
    "开发者工具": ("developer tool", "devtool", "api client", "terminal", "code editor"),
    "个人财务": ("personal finance", "budgeting", "expense", "invoice", "accounting"),
}


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


def _candidate_topic(candidate: Candidate) -> str | None:
    values: list[object] = [
        candidate.name,
        candidate.metadata.get("genre", ""),
        candidate.metadata.get("description", ""),
    ]
    genres = candidate.metadata.get("genres", [])
    if isinstance(genres, list):
        values.extend(genres)
    text = f" {_normalize(' '.join(str(value) for value in values if value))} "
    topic_signals = {
        "AI 与自动化": (" ai ", " llm ", "agent", "automation", "chatbot"),
        "效率与工作流": ("productivity", "business", "workflow", "task", "calendar", "notes"),
        "隐私与安全": ("privacy", "security", "encrypt", "password", "authenticator"),
        "图片与视频": ("photo", "video", "camera", "image", "graphics design"),
        "学习与教育": ("education", "reference", "learn", "study", "exam", "language"),
        "开发者工具": ("developer", "terminal", "api", "code", "debug"),
        "个人财务": ("finance", "budget", "expense", "invoice", "accounting"),
    }
    for topic, terms in topic_signals.items():
        if any(term in text for term in terms):
            return topic
    return None


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
    topic_counts = {
        topic: sum(
            1 for title in normalized_titles if any(term in f" {title} " for term in terms)
        )
        for topic, terms in HN_TOPICS.items()
    }
    exact_matched = 0
    topic_matched = 0
    for candidate in candidates:
        if not _eligible_for_discussion_signal(candidate, now):
            candidate.social_mentions = 0
            continue
        phrases = _candidate_phrases(candidate.name)
        exact_mentions = sum(
            1
            for title in normalized_titles
            if any(f" {phrase} " in f" {title} " for phrase in phrases)
        ) if phrases else 0
        topic = _candidate_topic(candidate) if candidate.segment == "app" else None
        topic_mentions = topic_counts.get(topic, 0) if topic else 0
        candidate.metadata["hn_exact_mentions"] = exact_mentions
        candidate.metadata["hn_trend_topic"] = topic or ""
        candidate.metadata["hn_topic_mentions"] = topic_mentions
        candidate.social_mentions = exact_mentions * 5 + min(topic_mentions, 20)
        exact_matched += exact_mentions > 0
        topic_matched += topic_mentions > 0
    state = "degraded" if error else "healthy"
    detail = (
        f"matched {exact_matched} exact products; "
        f"applied category trends to {topic_matched} candidates"
    )
    if error:
        detail += f"; pagination stopped: {error}"
    return SourceStatus("Hacker News", len(hits), state, detail)
