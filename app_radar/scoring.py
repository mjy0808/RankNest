from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from statistics import mean

from .models import (
    Candidate,
    HistorySnapshot,
    PreviousObservation,
    ScoredCandidate,
    SourceStatus,
)


SEGMENT_LABELS = {
    "app": "App",
    "mobile_game": "手游",
    "steam_game": "Steam 游戏",
}

SEGMENT_WEIGHTS: dict[str, dict[str, float]] = {
    "app": {"排名动量": 35, "口碑动量": 25, "讨论热度": 15, "市场广度": 15, "产品新鲜度": 10},
    "mobile_game": {"排名动量": 40, "口碑动量": 25, "讨论热度": 5, "市场广度": 20, "产品新鲜度": 10},
    "steam_game": {"排名动量": 40, "口碑动量": 25, "讨论热度": 5, "市场广度": 15, "产品新鲜度": 15},
}

HORIZON_WEIGHTS = {1: 0.50, 3: 0.30, 7: 0.20}


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(value, maximum))


def _rank_strength(candidate: Candidate) -> float:
    return _clamp((101 - candidate.best_rank) / 100)


def _traction_quality(candidate: Candidate) -> float:
    rating_quality = _clamp((candidate.rating - 3.2) / 1.6) if candidate.rating else 0.25
    if candidate.review_count <= 0:
        traction_window = 0.15
    else:
        log_reviews = math.log10(candidate.review_count + 1)
        center = 3.4 if candidate.segment == "app" else 3.1
        traction_window = _clamp(1 - abs(log_reviews - center) / 4.2, 0.12, 1.0)
    return 0.65 * rating_quality + 0.35 * traction_window


def _freshness(candidate: Candidate, today: date) -> tuple[float, int | None]:
    if candidate.release_date is None:
        return 0.25, None
    age_days = (today - candidate.release_date).days
    if age_days <= 0:
        return 1.0, age_days
    return math.exp(-age_days / 240), age_days


def _status_markets(statuses: list[SourceStatus], healthy_only: bool = True) -> set[tuple[str, str]]:
    markets: set[tuple[str, str]] = set()
    for status in statuses:
        if healthy_only and not status.ok:
            continue
        if not healthy_only and not status.usable:
            continue
        if "/" not in status.name:
            continue
        source_name, market = status.name.split("/", 1)
        source = {"Apple": "apple", "Steam": "steam"}.get(source_name)
        if source:
            markets.add((source, market.lower()))
    return markets


def _market_from_rank_key(key: str) -> str:
    return key.split(":", 1)[0].lower()


def _paired_rank_velocity(
    candidate: Candidate,
    previous: PreviousObservation | None,
    snapshot: HistorySnapshot,
    current_healthy: set[tuple[str, str]],
) -> tuple[float | None, float]:
    valid_markets = {
        market
        for source, market in current_healthy.intersection(snapshot.healthy_markets)
        if source == candidate.source
    }
    if not valid_markets:
        return None, 0.0
    previous_ranks = previous.ranks if previous else {}
    keys = {
        key
        for key in set(candidate.ranks).union(previous_ranks)
        if _market_from_rank_key(key) in valid_markets
    }
    if not keys:
        return None, 0.0
    deltas = [previous_ranks.get(key, 101) - candidate.ranks.get(key, 101) for key in keys]
    total_delta = mean(deltas)
    return total_delta / max(snapshot.actual_days, 1), total_delta


def _paired_review_velocity(
    candidate: Candidate,
    previous: PreviousObservation | None,
    snapshot: HistorySnapshot,
    current_healthy: set[tuple[str, str]],
) -> tuple[float | None, int]:
    if previous is None or not previous.review_counts:
        return None, 0
    valid_markets = {
        market
        for source, market in current_healthy.intersection(snapshot.healthy_markets)
        if source == candidate.source
    }
    markets = sorted(set(candidate.review_counts).intersection(previous.review_counts, valid_markets))
    if not markets:
        return None, 0
    deltas = [max(candidate.review_counts[m] - previous.review_counts[m], 0) for m in markets]
    relatives = [
        delta / max(previous.review_counts[market], 25)
        for market, delta in zip(markets, deltas)
    ]
    if candidate.source == "steam":
        absolute_delta = max(deltas)
        relative_delta = max(relatives)
    else:
        absolute_delta = round(mean(deltas))
        relative_delta = mean(relatives)
    days = max(snapshot.actual_days, 1)
    daily_delta = absolute_delta / days
    daily_relative = relative_delta / days
    signal = 0.55 * _clamp(math.log1p(daily_delta) / math.log1p(1000))
    signal += 0.45 * _clamp(daily_relative / 0.20)
    return signal, int(absolute_delta)


def _weighted(values: list[tuple[float, float]]) -> float | None:
    if not values:
        return None
    total_weight = sum(weight for _, weight in values)
    return sum(value * weight for value, weight in values) / total_weight


@dataclass
class _RawScore:
    candidate: Candidate
    signals: dict[str, float]
    reasons: list[str]
    previous: PreviousObservation | None


def _raw_score(
    candidate: Candidate,
    history: dict[int, HistorySnapshot],
    statuses: list[SourceStatus],
    today: date,
) -> _RawScore:
    current_healthy = _status_markets(statuses)
    rank_values: list[tuple[float, float]] = []
    review_values: list[tuple[float, float]] = []
    reasons: list[str] = []
    best_rank_change: tuple[float, int] | None = None
    best_review_change: tuple[int, int] | None = None

    for horizon, snapshot in history.items():
        previous = snapshot.observations.get(candidate.key)
        rank_velocity, total_rank_delta = _paired_rank_velocity(
            candidate, previous, snapshot, current_healthy
        )
        weight = HORIZON_WEIGHTS.get(horizon, 0.1)
        if rank_velocity is not None:
            rank_values.append((_clamp(rank_velocity / 15), weight))
            if previous is not None and total_rank_delta > 0 and (
                best_rank_change is None or total_rank_delta > best_rank_change[0]
            ):
                best_rank_change = (total_rank_delta, snapshot.actual_days)
        review_signal, review_delta = _paired_review_velocity(
            candidate, previous, snapshot, current_healthy
        )
        if review_signal is not None:
            review_values.append((review_signal, weight))
            if review_delta > 0 and (
                best_review_change is None or review_delta > best_review_change[0]
            ):
                best_review_change = (review_delta, snapshot.actual_days)

    rank_signal = _weighted(rank_values)
    review_signal = _weighted(review_values)
    if rank_signal is None:
        rank_signal = 0.45 * _rank_strength(candidate)
    else:
        rank_signal = 0.80 * rank_signal + 0.20 * _rank_strength(candidate)
    if review_signal is None:
        review_signal = 0.45 * _traction_quality(candidate)
    else:
        review_signal = 0.80 * review_signal + 0.20 * _traction_quality(candidate)

    if best_rank_change:
        reasons.append(
            f"{best_rank_change[1]} 日配对榜位上升 {best_rank_change[0]:.1f} 位"
        )
    if best_review_change:
        reasons.append(
            f"{best_review_change[1]} 日新增 {best_review_change[0]:,} 条可比评价"
        )
    if not history:
        reasons.append("建立首日监控基线")
    elif all(snapshot.observations.get(candidate.key) is None for snapshot in history.values()):
        reasons.append("新进入监控榜单")

    discussion = _clamp(math.log1p(candidate.social_mentions) / math.log(6))
    if candidate.social_mentions:
        reasons.append(f"近 48 小时 HN 精确提及 {candidate.social_mentions} 次")

    source_markets = {market for source, market in current_healthy if source == candidate.source}
    ranked_healthy_markets = {
        _market_from_rank_key(key)
        for key in candidate.ranks
        if (candidate.source, _market_from_rank_key(key)) in current_healthy
    }
    breadth = len(ranked_healthy_markets) / max(len(source_markets), 1)
    if len(ranked_healthy_markets) >= 2:
        reasons.append(
            f"覆盖 {len(ranked_healthy_markets)}/{max(len(source_markets), 1)} 个健康市场"
        )

    fresh, age_days = _freshness(candidate, today)
    if age_days is not None and age_days <= 180:
        reasons.append("即将发布，已进入趋势榜" if age_days < 0 else f"上线仅 {age_days} 天")

    previous = None
    for horizon in (1, 3, 7):
        if horizon in history and candidate.key in history[horizon].observations:
            previous = history[horizon].observations[candidate.key]
            break
    return _RawScore(
        candidate=candidate,
        signals={
            "排名动量": _clamp(rank_signal),
            "口碑动量": _clamp(review_signal),
            "讨论热度": discussion,
            "市场广度": _clamp(breadth),
            "产品新鲜度": _clamp(fresh),
        },
        reasons=reasons,
        previous=previous,
    )


def _percentile_blend(values: list[float]) -> list[float]:
    if not values or max(values) <= 0:
        return [0.0 for _ in values]
    positives = sorted(value for value in values if value > 0)
    normalized: list[float] = []
    for value in values:
        if value <= 0:
            normalized.append(0.0)
            continue
        lower = sum(other < value for other in positives)
        equal = sum(other == value for other in positives)
        percentile = (lower + 0.5 * equal) / len(positives)
        normalized.append(_clamp(0.65 * percentile + 0.35 * value))
    return normalized


def _effective_weights(group: list[_RawScore], segment: str) -> dict[str, float]:
    base = dict(SEGMENT_WEIGHTS[segment])
    discussion_coverage = (
        sum(item.signals["讨论热度"] > 0 for item in group) / len(group) if group else 0.0
    )
    # A signal seen in only a tiny fraction of a segment is useful evidence,
    # but should not decide the entire ranking. Ramp to full weight at 10%
    # coverage and redistribute the rest across the other active signals.
    discussion_reliability = _clamp(discussion_coverage / 0.10)
    base["讨论热度"] *= discussion_reliability
    missing_weight = 100 - sum(base.values())
    recipients = [label for label in base if label != "讨论热度"]
    recipient_total = sum(base[label] for label in recipients)
    for label in recipients:
        base[label] += missing_weight * base[label] / recipient_total
    return {label: round(weight, 4) for label, weight in base.items()}


def score_all(
    candidates: list[Candidate],
    history: dict[int, HistorySnapshot],
    statuses: list[SourceStatus],
    today: date,
) -> list[ScoredCandidate]:
    raw_items = [_raw_score(candidate, history, statuses, today) for candidate in candidates]
    scored: list[ScoredCandidate] = []
    for segment in SEGMENT_LABELS:
        group = [item for item in raw_items if item.candidate.segment == segment]
        if not group:
            continue
        weights = _effective_weights(group, segment)
        normalized_by_signal = {
            signal: _percentile_blend([item.signals[signal] for item in group])
            for signal in weights
        }
        for index, item in enumerate(group):
            components = {
                signal: round(normalized_by_signal[signal][index] * weight, 2)
                for signal, weight in weights.items()
            }
            reasons = list(dict.fromkeys(item.reasons))
            if len(reasons) < 2:
                reasons.append(f"当前最佳榜位 #{item.candidate.best_rank}")
            if item.candidate.rating >= 4.5 and len(reasons) < 3:
                reasons.append(f"用户评分 {item.candidate.rating:.1f}/5")
            scored.append(
                ScoredCandidate(
                    candidate=item.candidate,
                    score=round(sum(components.values()), 2),
                    components=components,
                    component_weights=weights,
                    reasons=tuple(reasons[:3]),
                    previous=item.previous,
                )
            )
    return sorted(scored, key=lambda item: item.score, reverse=True)


def select_segment_items(
    scored: list[ScoredCandidate], segment_counts: dict[str, int]
) -> dict[str, list[ScoredCandidate]]:
    return {
        segment: [item for item in scored if item.candidate.segment == segment][:count]
        for segment, count in segment_counts.items()
    }
