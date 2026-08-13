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
    "app": {
        "排名动量": 30,
        "口碑动量": 20,
        "讨论热度": 5,
        "市场广度": 10,
        "产品新鲜度": 10,
        "可借鉴性": 25,
    },
    "mobile_game": {
        "排名动量": 35,
        "口碑动量": 20,
        "讨论热度": 0,
        "市场广度": 10,
        "产品新鲜度": 10,
        "可借鉴性": 25,
    },
    "steam_game": {
        "排名动量": 35,
        "口碑动量": 20,
        "讨论热度": 0,
        "市场广度": 10,
        "产品新鲜度": 15,
        "可借鉴性": 20,
    },
}

HORIZON_WEIGHTS = {1: 0.50, 3: 0.30, 7: 0.20}
MIN_OPPORTUNITY_FIT = 60
EXCLUDED_OPPORTUNITY_TAGS = {"成熟头部产品", "平台或网络效应较强", "存在品牌/IP 风险"}


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


APP_GENRE_FEASIBILITY = {
    "utilities": 0.92,
    "productivity": 0.90,
    "photo & video": 0.82,
    "graphics & design": 0.82,
    "business": 0.82,
    "weather": 0.82,
    "reference": 0.80,
    "education": 0.78,
    "lifestyle": 0.76,
    "food & drink": 0.75,
    "travel": 0.70,
    "health & fitness": 0.66,
    "navigation": 0.62,
    "music": 0.58,
    "finance": 0.52,
    "news": 0.48,
    "entertainment": 0.42,
    "shopping": 0.35,
    "social networking": 0.18,
}
SIMPLE_GAME_TERMS = {
    "puzzle", "idle", "merge", "sort", "color", "colour", "block", "word",
    "clicker", "tycoon", "simulator", "simulation", "casual", "quiz", "trivia",
    "card", "board", "traffic", "chill", "repair", "deckbuilder", "roguelike",
}
COMPLEX_GAME_TERMS = {
    "mmo", "mmorpg", "open world", "multiplayer", "battle royale", "commander",
    "awakening", "warfare", "4x", "real-time strategy",
}
SIMPLE_GAME_GENRES = {"puzzle", "casual", "word", "board", "card", "trivia", "family"}
COMPLEX_GAME_GENRES = {"role playing", "strategy", "action", "adventure", "racing"}


def _genres(candidate: Candidate) -> set[str]:
    values: list[object] = []
    raw_genres = candidate.metadata.get("genres", [])
    if isinstance(raw_genres, list):
        values.extend(raw_genres)
    if candidate.metadata.get("genre"):
        values.append(candidate.metadata["genre"])
    return {str(value).strip().casefold() for value in values if str(value).strip()}


def _validation_window(candidate: Candidate) -> float:
    reviews = candidate.review_count
    if reviews <= 0:
        return 0.25
    log_reviews = math.log10(reviews + 1)
    if candidate.segment == "app":
        center, radius = 3.1, 3.2
    elif candidate.segment == "mobile_game":
        center, radius = 3.5, 3.4
    else:
        center, radius = 3.0, 3.0
    return _clamp(1 - abs(log_reviews - center) / radius, 0.03, 1.0)


def _implementation_feasibility(candidate: Candidate) -> tuple[float, list[str]]:
    genres = _genres(candidate)
    name = candidate.name.casefold()
    tags: list[str] = []
    if candidate.segment == "app":
        primary = str(candidate.metadata.get("genre", "")).casefold()
        feasibility = APP_GENRE_FEASIBILITY.get(primary, 0.60)
        if feasibility >= 0.78:
            tags.append("功能边界清晰")
        elif feasibility <= 0.35:
            tags.append("平台或网络效应较强")
    else:
        feasibility = 0.58 if candidate.segment == "mobile_game" else 0.50
        if any(term in name for term in SIMPLE_GAME_TERMS) or genres.intersection(
            SIMPLE_GAME_GENRES
        ):
            feasibility += 0.25
            tags.append("核心玩法易拆解")
        if any(term in name for term in COMPLEX_GAME_TERMS) or genres.intersection(
            COMPLEX_GAME_GENRES
        ):
            feasibility -= 0.20
            tags.append("内容或系统量较大")
        if "™" in candidate.name or "®" in candidate.name:
            feasibility -= 0.30
            tags.append("存在品牌/IP 风险")

    file_size = candidate.metadata.get("file_size_bytes")
    try:
        file_size_bytes = int(file_size) if file_size else 0
    except (TypeError, ValueError):
        file_size_bytes = 0
    if file_size_bytes and file_size_bytes <= 250_000_000:
        feasibility += 0.08
        tags.append("产品体量较轻")
    elif file_size_bytes >= 1_500_000_000:
        feasibility -= 0.12
        tags.append("资源体量较重")
    return _clamp(feasibility), tags


def _build_angle(candidate: Candidate) -> str:
    primary = str(candidate.metadata.get("genre", "")).casefold()
    name = candidate.name.casefold()
    if candidate.segment == "app":
        if primary in {"utilities", "weather", "navigation"}:
            return "聚焦一个高频工具任务，用更少步骤和更清晰反馈切入"
        if primary in {"productivity", "business"}:
            return "选择垂直职业或流程，用模板、自动化和协作细节差异化"
        if primary in {"photo & video", "graphics & design"}:
            return "围绕一种风格或拍摄场景，重做模板、批处理与分享体验"
        if primary in {"education", "reference"}:
            return "选择细分学习主题，用练习、测验和进度反馈形成闭环"
        return "缩小到单一高频场景，面向明确人群重新设计完整体验"
    if any(term in name for term in {"puzzle", "sort", "block", "word", "quiz"}):
        return "提炼短回合规则，重做主题、关卡曲线、反馈与商业化"
    if any(term in name for term in {"idle", "clicker", "tycoon"}):
        return "围绕单一成长循环换题材，并缩短首局正反馈时间"
    if any(term in name for term in {"simulator", "simulation", "repair"}):
        return "选择更窄的职业或物件场景，突出操作反馈和可扩展内容"
    if any(term in name for term in {"card", "deck", "rogue"}):
        return "保留可重复构筑循环，用新规则组合、题材和美术差异化"
    return "先验证 5–10 分钟核心循环，再从题材、关卡和变现方式差异化"


def _opportunity_fit(candidate: Candidate, today: date) -> tuple[float, list[str]]:
    feasibility, tags = _implementation_feasibility(candidate)
    validation = _validation_window(candidate)
    fit = 0.65 * feasibility + 0.35 * validation

    age_days = (today - candidate.release_date).days if candidate.release_date else None
    if age_days is not None and age_days > 1_095 and candidate.review_count > 250_000:
        fit *= 0.12
        tags.append("成熟头部产品")
    elif candidate.review_count >= 500_000:
        fit *= 0.40
        tags.append("竞争门槛较高")
    elif 50 <= candidate.review_count <= 50_000:
        tags.append("需求已有初步验证")

    if fit >= 0.72:
        tags.insert(0, "小团队可切入")
    candidate.metadata["opportunity_fit"] = round(_clamp(fit) * 100)
    candidate.metadata["opportunity_tags"] = list(dict.fromkeys(tags))[:3]
    candidate.metadata["build_angle"] = _build_angle(candidate)
    return _clamp(fit), candidate.metadata["opportunity_tags"]


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
    opportunity, opportunity_tags = _opportunity_fit(candidate, today)
    if opportunity_tags:
        reasons.append(
            f"可借鉴度 {round(opportunity * 100)}：{'、'.join(opportunity_tags[:2])}"
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
            "可借鉴性": opportunity,
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
    recipients = [label for label in base if label != "讨论热度" and base[label] > 0]
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
    def actionable(item: ScoredCandidate) -> bool:
        fit = int(item.candidate.metadata.get("opportunity_fit", 0))
        tags = set(item.candidate.metadata.get("opportunity_tags", []))
        return fit >= MIN_OPPORTUNITY_FIT and not tags.intersection(EXCLUDED_OPPORTUNITY_TAGS)

    return {
        segment: [
            item for item in scored
            if item.candidate.segment == segment and actionable(item)
        ][:count]
        for segment, count in segment_counts.items()
    }
