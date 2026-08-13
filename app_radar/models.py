from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import mean
from typing import Any


@dataclass
class Candidate:
    source: str
    external_id: str
    name: str
    kind: str
    developer: str = ""
    url: str = ""
    image_url: str = ""
    release_date: date | None = None
    update_date: date | None = None
    price_text: str = ""
    ranks: dict[str, int] = field(default_factory=dict)
    ratings: dict[str, float] = field(default_factory=dict)
    review_counts: dict[str, int] = field(default_factory=dict)
    social_mentions: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return self.source, self.external_id

    @property
    def segment(self) -> str:
        if self.source == "steam":
            return "steam_game"
        if self.kind == "game":
            return "mobile_game"
        return "app"

    @property
    def market_count(self) -> int:
        return len({key.split(":", 1)[0] for key in self.ranks})

    @property
    def best_rank(self) -> int:
        return min(self.ranks.values()) if self.ranks else 999

    @property
    def average_rank(self) -> float:
        return mean(self.ranks.values()) if self.ranks else 999.0

    @property
    def rating(self) -> float:
        if not self.ratings:
            return 0.0
        weighted_total = 0.0
        weight = 0
        for market, rating in self.ratings.items():
            market_reviews = max(self.review_counts.get(market, 1), 1)
            weighted_total += rating * market_reviews
            weight += market_reviews
        return weighted_total / weight if weight else mean(self.ratings.values())

    @property
    def review_count(self) -> int:
        if not self.review_counts:
            return 0
        if self.source == "apple":
            return sum(self.review_counts.values())
        return max(self.review_counts.values())

    def merge(self, other: "Candidate") -> None:
        if self.key != other.key:
            raise ValueError("cannot merge different candidates")
        self.ranks.update(other.ranks)
        self.ratings.update(other.ratings)
        self.review_counts.update(other.review_counts)
        self.social_mentions = max(self.social_mentions, other.social_mentions)
        # The first market is the configured canonical market. Keep its
        # localized presentation stable while still merging all metrics.
        if other.developer and not self.developer:
            self.developer = other.developer
        if other.url and not self.url:
            self.url = other.url
        if other.image_url and not self.image_url:
            self.image_url = other.image_url
        if other.release_date and not self.release_date:
            self.release_date = other.release_date
        if other.update_date and (not self.update_date or other.update_date > self.update_date):
            self.update_date = other.update_date
        if other.price_text and not self.price_text:
            self.price_text = other.price_text
        if other.kind == "game":
            self.kind = "game"
        for key, value in other.metadata.items():
            self.metadata.setdefault(key, value)


@dataclass(frozen=True)
class PreviousObservation:
    captured_at: datetime
    run_day: date
    average_rank: float
    best_rank: int
    review_count: int
    market_count: int
    social_mentions: int
    selected_rank: int | None = None
    ranks: dict[str, int] = field(default_factory=dict)
    review_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Candidate
    score: float
    components: dict[str, float]
    component_weights: dict[str, float]
    reasons: tuple[str, ...]
    previous: PreviousObservation | None


@dataclass(frozen=True)
class SourceStatus:
    name: str
    item_count: int
    state: str = "healthy"
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "healthy"

    @property
    def usable(self) -> bool:
        return self.state in {"healthy", "degraded"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "state": self.state,
            "item_count": self.item_count,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class HistorySnapshot:
    run_id: int
    run_day: date
    captured_at: datetime
    actual_days: int
    observations: dict[tuple[str, str], PreviousObservation]
    healthy_markets: frozenset[tuple[str, str]]
