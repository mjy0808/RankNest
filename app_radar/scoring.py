from __future__ import annotations

import math
import re
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
from .opportunities import build_opportunity_card


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

HORIZON_WEIGHTS = {1: 0.35, 3: 0.25, 7: 0.20, 14: 0.12, 30: 0.08}
MIN_OPPORTUNITY_FIT = {"app": 72, "mobile_game": 72, "steam_game": 70}
MIN_EARLY_OPPORTUNITY_FIT = {"app": 58, "mobile_game": 55, "steam_game": 58}
MIN_EARLY_SIGNAL = {"app": 62, "mobile_game": 58, "steam_game": 62}
EARLY_REVIEW_CAP = {"app": 12_000, "mobile_game": 20_000, "steam_game": 3_000}
MATURE_REVIEW_LEVEL = {"app": 40_000, "mobile_game": 100_000, "steam_game": 10_000}
EXCLUDED_OPPORTUNITY_TAGS = {
    "成熟头部产品",
    "平台或网络效应较强",
    "存在品牌/IP 风险",
    "政府或机构专属",
    "高合规风险",
    "博彩或真钱风险",
    "大型制作规模",
    "续作或成熟 IP",
}

PUBLIC_SECTOR_TERMS = {
    "government", "ministry", "federal agency", "public authority", "bundesagentur",
    "arbeitsagentur", "gov.uk", "gov.br", "gov.cn", "고용24", "한국고용정보원",
    "政务", "政府", "公共就业", "公共服務", "公共服务",
}
REGULATED_TERMS = {
    "banking", "bank account", "insurance", "broker", "trading", "crypto wallet",
    "medical diagnosis", "patient portal", "identity verification", "authentication",
    "vpn", "proxy", "secure access",
}
GAMBLING_TERMS = {
    "casino", "slots", "sportsbook", "betting", "real cash", "win cash", "poker money",
}
KNOWN_IP_REFERENCE_TERMS = {
    "sensi ff", "free fire", "pokemon", "marvel", "star wars", "harry potter",
    "lord of the rings", "game of thrones",
    "microsoft flight simulator",
}
MAJOR_GAME_PUBLISHER_TERMS = {
    "activision", "bandai namco", "blizzard", "capcom", "electronic arts",
    "gameloft", "king.com", "konami", "netease", "rockstar", "sega",
    "square enix", "supercell", "take-two", "tencent", "ubisoft",
}

# Stable tag ids emitted by Steam's own search result rows. Only use tags with
# clear production/distribution implications; unknown ids remain neutral.
STEAM_OPEN_WORLD_TAGS = {"1695", "128"}  # Open World, Massively Multiplayer
STEAM_LIVE_SERVICE_TAGS = {"113", "19", "21", "122", "4085", "1646"}
STEAM_NETWORK_TAGS = {"3859", "1685", "3843", "1775", "128"}
STEAM_LIGHTWEIGHT_TAGS = {"492", "597", "599", "1664"}  # Indie, Casual, Simulation, Puzzle
STEAM_HIGH_PRODUCTION_TAGS = {
    "19", "21", "122", "1695", "4106", "4231", "4608", "29482", "4777"
}


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


def _searchable_text(candidate: Candidate) -> str:
    values: list[object] = [
        candidate.name,
        candidate.developer,
        candidate.metadata.get("genre", ""),
        candidate.metadata.get("description", ""),
        candidate.metadata.get("publisher", ""),
    ]
    raw_genres = candidate.metadata.get("genres", [])
    if isinstance(raw_genres, list):
        values.extend(raw_genres)
    return " ".join(str(value).casefold() for value in values if value)


def _steam_tag_ids(candidate: Candidate) -> set[str]:
    raw = str(candidate.metadata.get("steam_tag_ids", ""))
    return set(re.findall(r"\d+", raw))


def _contains_term(text: str, term: str) -> bool:
    if re.fullmatch(r"[a-z0-9 -]+", term):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None
    return term in text


def _implementation_feasibility(
    candidate: Candidate,
) -> tuple[float, list[str], list[str]]:
    genres = _genres(candidate)
    steam_tags = _steam_tag_ids(candidate)
    name = candidate.name.casefold()
    text = _searchable_text(candidate)
    tags: list[str] = []
    risks: list[str] = []
    if candidate.segment == "app":
        primary = str(candidate.metadata.get("genre", "")).casefold()
        feasibility = APP_GENRE_FEASIBILITY.get(primary, 0.60)
        if feasibility >= 0.78:
            tags.append("功能边界清晰")
        elif feasibility <= 0.35:
            tags.append("平台或网络效应较强")
    else:
        feasibility = 0.58 if candidate.segment == "mobile_game" else 0.50
        if any(_contains_term(name, term) for term in SIMPLE_GAME_TERMS) or genres.intersection(
            SIMPLE_GAME_GENRES
        ):
            feasibility += 0.25
            tags.append("核心玩法易拆解")
        elif candidate.segment == "steam_game" and steam_tags.intersection(
            STEAM_LIGHTWEIGHT_TAGS
        ):
            feasibility += 0.12
            tags.append("独立或轻量类型")
        if any(_contains_term(name, term) for term in COMPLEX_GAME_TERMS) or genres.intersection(
            COMPLEX_GAME_GENRES
        ):
            feasibility -= 0.25
            tags.append("内容或系统量较大")
            if candidate.segment == "steam_game":
                risks.append("大型制作规模")
        if candidate.segment == "steam_game":
            if steam_tags.intersection(STEAM_OPEN_WORLD_TAGS):
                feasibility -= 0.35
                risks.append("大型制作规模")
            if len(steam_tags.intersection(STEAM_LIVE_SERVICE_TAGS)) >= 4:
                feasibility -= 0.28
                risks.append("大型制作规模")
            if len(steam_tags.intersection(STEAM_NETWORK_TAGS)) >= 2:
                feasibility -= 0.25
                risks.append("平台或网络效应较强")
            if "113" in steam_tags and steam_tags.intersection(STEAM_NETWORK_TAGS):
                feasibility -= 0.20
                risks.append("平台或网络效应较强")
            if len(steam_tags.intersection(STEAM_HIGH_PRODUCTION_TAGS)) >= 5:
                feasibility -= 0.30
                risks.append("大型制作规模")
        sequel = re.search(r"(?:\b(?:ii|iii|iv)\b|(?:\s|:|-)[2-9])$", name)
        is_simple_game = any(_contains_term(name, term) for term in SIMPLE_GAME_TERMS) or bool(
            genres.intersection(SIMPLE_GAME_GENRES)
        )
        if candidate.segment == "steam_game" and sequel and not is_simple_game:
            feasibility -= 0.18
            risks.append("续作或成熟 IP")
    if "™" in candidate.name or "®" in candidate.name or any(
        term in text for term in KNOWN_IP_REFERENCE_TERMS
    ):
        feasibility -= 0.30
        risks.append("存在品牌/IP 风险")
    if candidate.segment != "app" and any(
        term in text for term in MAJOR_GAME_PUBLISHER_TERMS
    ):
        feasibility -= 0.25
        risks.append("续作或成熟 IP")

    file_size = candidate.metadata.get("file_size_bytes")
    try:
        file_size_bytes = int(file_size) if file_size else 0
    except (TypeError, ValueError):
        file_size_bytes = 0
    if file_size_bytes and file_size_bytes <= 250_000_000:
        feasibility += 0.08
        tags.append("产品体量较轻")
    elif file_size_bytes >= (
        800_000_000 if candidate.segment == "mobile_game" else 1_500_000_000
    ):
        feasibility -= 0.12
        tags.append("资源体量较重")
        if candidate.segment != "app":
            risks.append("大型制作规模")
    return _clamp(feasibility), tags, risks


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


def _risk_dimensions(
    candidate: Candidate,
) -> tuple[float, float, float, list[str]]:
    text = _searchable_text(candidate)
    portability = _clamp(0.42 + min(candidate.market_count, 3) * 0.18)
    if candidate.segment == "app":
        primary = str(candidate.metadata.get("genre", "")).casefold()
        distribution = APP_GENRE_FEASIBILITY.get(primary, 0.60)
    else:
        distribution = 0.72 if candidate.segment == "mobile_game" else 0.62
    legal_safety = 1.0
    risks: list[str] = []
    if any(term in text for term in PUBLIC_SECTOR_TERMS):
        portability, distribution, legal_safety = 0.08, 0.12, 0.45
        risks.append("政府或机构专属")
    if any(term in text for term in GAMBLING_TERMS):
        distribution, legal_safety = min(distribution, 0.20), 0.05
        risks.append("博彩或真钱风险")
    if any(term in text for term in REGULATED_TERMS):
        portability = min(portability, 0.45)
        distribution, legal_safety = min(distribution, 0.38), min(legal_safety, 0.35)
        risks.append("高合规风险")
    if any(term in text for term in KNOWN_IP_REFERENCE_TERMS) or "™" in candidate.name or "®" in candidate.name:
        portability, legal_safety = min(portability, 0.35), 0.08
        risks.append("存在品牌/IP 风险")
    return portability, _clamp(distribution), legal_safety, risks


def _opportunity_fit(candidate: Candidate, today: date) -> tuple[float, list[str]]:
    feasibility, tags, implementation_risks = _implementation_feasibility(candidate)
    validation = _validation_window(candidate)
    portability, distribution, legal_safety, risks = _risk_dimensions(candidate)
    risks = list(dict.fromkeys(implementation_risks + risks))
    fit = (
        0.30 * feasibility
        + 0.25 * validation
        + 0.20 * portability
        + 0.15 * distribution
        + 0.10 * legal_safety
    )

    age_days = (today - candidate.release_date).days if candidate.release_date else None
    if (
        candidate.segment == "steam_game"
        and age_days is not None
        and age_days > 1_825
        and _steam_tag_ids(candidate).intersection(STEAM_NETWORK_TAGS)
    ):
        fit *= 0.55
        risks.append("平台或网络效应较强")
    if age_days is not None and age_days > 1_095 and candidate.review_count > 250_000:
        fit *= 0.12
        risks.append("成熟头部产品")
    elif candidate.review_count >= 500_000:
        fit *= 0.40
        tags.append("竞争门槛较高")
    elif 50 <= candidate.review_count <= 50_000:
        tags.append("需求已有初步验证")

    risks = list(dict.fromkeys(risks))
    if fit >= 0.72 and not set(risks).intersection(EXCLUDED_OPPORTUNITY_TAGS):
        tags.insert(0, "小团队可切入")
    dimensions = {
        "需求验证": round(validation * 100),
        "开发可行": round(feasibility * 100),
        "跨市场可迁移": round(portability * 100),
        "分发可达": round(distribution * 100),
        "法律与合规安全": round(legal_safety * 100),
    }
    candidate.metadata["opportunity_fit"] = round(_clamp(fit) * 100)
    candidate.metadata["opportunity_tags"] = list(dict.fromkeys(tags + risks))[:5]
    candidate.metadata["opportunity_risks"] = risks
    candidate.metadata["opportunity_dimensions"] = dimensions
    card = build_opportunity_card(candidate)
    candidate.metadata["opportunity_theme_key"] = card["theme_key"]
    candidate.metadata["opportunity_theme"] = card["theme"]
    candidate.metadata["opportunity_card"] = card
    candidate.metadata["build_angle"] = card["differentiation"]
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
    seen_horizons: set[int] = set()
    positive_horizons: set[int] = set()

    for horizon, snapshot in history.items():
        previous = snapshot.observations.get(candidate.key)
        if previous is not None:
            seen_horizons.add(horizon)
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
            if previous is not None and total_rank_delta >= 3:
                positive_horizons.add(horizon)
        review_signal, review_delta = _paired_review_velocity(
            candidate, previous, snapshot, current_healthy
        )
        if review_signal is not None:
            review_values.append((review_signal, weight))
            if review_delta > 0 and (
                best_review_change is None or review_delta > best_review_change[0]
            ):
                best_review_change = (review_delta, snapshot.actual_days)
            if previous is not None and review_delta >= max(
                5, round(previous.review_count * 0.01)
            ):
                positive_horizons.add(horizon)

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
    confidence = _clamp(
        0.40 * min(len(seen_horizons) / 2, 1)
        + 0.35 * min(len(positive_horizons) / 2, 1)
        + 0.25 * min(candidate.market_count / 3, 1)
    )
    confidence_score = round(confidence * 100)
    candidate.metadata["history_horizons"] = sorted(seen_horizons)
    candidate.metadata["growth_horizons"] = sorted(positive_horizons)
    candidate.metadata["confidence_score"] = confidence_score
    candidate.metadata["confidence_label"] = (
        "高置信" if confidence_score >= 75 else "已确认" if confidence_score >= 50 else "待观察"
    )
    candidate.metadata["opportunity_tier"] = (
        "validated" if seen_horizons and confidence_score >= 50 else "watch"
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

    exact_mentions = int(candidate.metadata.get("hn_exact_mentions", 0))
    topic_mentions = int(candidate.metadata.get("hn_topic_mentions", 0))
    exact_signal = _clamp(math.log1p(exact_mentions) / math.log(6))
    topic_signal = 0.70 * _clamp(math.log1p(topic_mentions) / math.log(31))
    discussion = max(exact_signal, topic_signal)
    if exact_mentions:
        reasons.append(f"近 48 小时 HN 精确提及 {exact_mentions} 次")
    elif topic_mentions:
        topic = str(candidate.metadata.get("hn_trend_topic", "相关主题"))
        reasons.append(f"HN 主题「{topic}」近 48 小时 {topic_mentions} 条讨论")

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


def _launch_signal(candidate: Candidate, today: date) -> tuple[float, int | None]:
    if candidate.release_date is None:
        return 0.20, None
    age_days = (today - candidate.release_date).days
    if -180 <= age_days < 0:
        return 1.0, age_days
    if age_days <= 30:
        return 1.0, age_days
    if age_days <= 90:
        return 0.80, age_days
    if age_days <= 180:
        return 0.55, age_days
    return 0.05, age_days


def _annotate_early_signals(raw_items: list[_RawScore], today: date) -> None:
    groups: dict[tuple[str, str], list[_RawScore]] = {}
    for item in raw_items:
        candidate = item.candidate
        theme = str(candidate.metadata.get("opportunity_theme_key", "other"))
        groups.setdefault((candidate.segment, theme), []).append(item)

    for (segment, _), group in groups.items():
        mature_count = 0
        for item in group:
            candidate = item.candidate
            age_days = (
                (today - candidate.release_date).days if candidate.release_date else None
            )
            if candidate.review_count >= MATURE_REVIEW_LEVEL[segment] or (
                age_days is not None and age_days > 540 and candidate.best_rank <= 20
            ):
                mature_count += 1
        theme_crowding = _clamp(mature_count / 4)

        for item in group:
            candidate = item.candidate
            review_cap = EARLY_REVIEW_CAP[segment]
            individual_saturation = _clamp(
                math.log1p(candidate.review_count) / math.log1p(review_cap * 4)
            )
            competition = _clamp(
                0.60 * individual_saturation + 0.40 * theme_crowding
            )
            competition_score = round(competition * 100)
            competition_level = (
                "低竞争" if competition_score < 35
                else "中等竞争" if competition_score < 65
                else "高竞争"
            )

            launch, age_days = _launch_signal(candidate, today)
            try:
                first_seen_days = max(int(candidate.metadata.get("first_seen_days", 0)), 0)
            except (TypeError, ValueError):
                first_seen_days = 0
            emergence = math.exp(-first_seen_days / 7) if first_seen_days <= 45 else 0.05
            low_saturation = 1 - individual_saturation
            momentum = max(item.signals["排名动量"], item.signals["口碑动量"])
            early_base = (
                0.30 * launch
                + 0.24 * low_saturation
                + 0.18 * emergence
                + 0.16 * momentum
                + 0.07 * item.signals["市场广度"]
                + 0.05 * item.signals["讨论热度"]
            )
            # Crowded themes can still surface, but need materially stronger leading evidence.
            early_signal = _clamp(early_base * (0.78 + 0.22 * (1 - theme_crowding)))
            early_score = round(early_signal * 100)
            recent_release = age_days is not None and -180 <= age_days <= 180
            unknown_date_lead = (
                age_days is None
                and first_seen_days <= 3
                and candidate.review_count <= review_cap // 4
                and (
                    candidate.market_count >= 2
                    or int(candidate.metadata.get("hn_exact_mentions", 0)) > 0
                )
            )
            early_candidate = bool(
                candidate.review_count <= review_cap
                and early_score >= MIN_EARLY_SIGNAL[segment]
                and (recent_release or unknown_date_lead)
            )

            early_reasons: list[str] = []
            if age_days is not None and age_days < 0:
                early_reasons.append(f"距上线 {abs(age_days)} 天，已进入即将推出榜")
            elif age_days is not None and age_days <= 180:
                early_reasons.append(f"上线仅 {age_days} 天，仍处早期验证窗口")
            if first_seen_days == 0:
                early_reasons.append("今天首次进入监控池")
            elif first_seen_days <= 7:
                early_reasons.append(f"进入监控池仅 {first_seen_days} 天")
            if candidate.review_count <= review_cap:
                early_reasons.append(
                    f"当前仅 {_compact_review_count(candidate.review_count)} 条评价，尚未高度饱和"
                )
            if theme_crowding <= 0.25:
                early_reasons.append("同主题成熟竞品密度较低")

            candidate.metadata["first_seen_days"] = first_seen_days
            candidate.metadata["early_candidate"] = early_candidate
            candidate.metadata["early_signal_score"] = early_score
            candidate.metadata["early_reasons"] = early_reasons[:3]
            candidate.metadata["competition_score"] = competition_score
            candidate.metadata["competition_level"] = competition_level
            candidate.metadata["theme_mature_competitors"] = mature_count


def _compact_review_count(value: int) -> str:
    if value >= 10_000:
        return f"{value / 10_000:.1f}万"
    return f"{value:,}"


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
    _annotate_early_signals(raw_items, today)
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
            base_score = round(sum(components.values()), 2)
            opportunity_fit = int(item.candidate.metadata.get("opportunity_fit", 0)) / 100
            actionability_multiplier = 0.45 + 0.55 * opportunity_fit
            competition_score = int(
                item.candidate.metadata.get("competition_score", 0)
            ) / 100
            competition_multiplier = 0.72 + 0.28 * (1 - competition_score)
            final_score = round(
                base_score * actionability_multiplier * competition_multiplier, 2
            )
            item.candidate.metadata["base_momentum_score"] = base_score
            item.candidate.metadata["actionability_multiplier"] = round(
                actionability_multiplier, 3
            )
            item.candidate.metadata["competition_multiplier"] = round(
                competition_multiplier, 3
            )
            leading_reasons = (
                item.candidate.metadata.get("early_reasons", [])
                if item.candidate.metadata.get("early_candidate")
                else []
            )
            reasons = list(dict.fromkeys([*leading_reasons[:1], *item.reasons]))
            if len(reasons) < 2:
                reasons.append(f"当前最佳榜位 #{item.candidate.best_rank}")
            if item.candidate.rating >= 4.5 and len(reasons) < 3:
                reasons.append(f"用户评分 {item.candidate.rating:.1f}/5")
            scored.append(
                ScoredCandidate(
                    candidate=item.candidate,
                    score=final_score,
                    components=components,
                    component_weights=weights,
                    reasons=tuple(reasons[:3]),
                    previous=item.previous,
                )
            )
    return sorted(scored, key=lambda item: item.score, reverse=True)


def select_segment_items(
    scored: list[ScoredCandidate],
    segment_counts: dict[str, int],
    early_counts: dict[str, int] | None = None,
) -> dict[str, list[ScoredCandidate]]:
    def actionable(item: ScoredCandidate, early: bool = False) -> bool:
        fit = int(item.candidate.metadata.get("opportunity_fit", 0))
        tags = set(item.candidate.metadata.get("opportunity_tags", []))
        risks = set(item.candidate.metadata.get("opportunity_risks", []))
        thresholds = MIN_EARLY_OPPORTUNITY_FIT if early else MIN_OPPORTUNITY_FIT
        threshold = thresholds[item.candidate.segment]
        return fit >= threshold and not (tags | risks).intersection(EXCLUDED_OPPORTUNITY_TAGS)

    early_counts = early_counts or {}
    selected: dict[str, list[ScoredCandidate]] = {}
    for segment, count in segment_counts.items():
        standard = [
            item for item in scored
            if item.candidate.segment == segment and actionable(item)
        ]
        standard.sort(
            key=lambda item: (
                item.candidate.metadata.get("opportunity_tier") == "validated",
                item.score,
                int(item.candidate.metadata.get("confidence_score", 0)),
            ),
            reverse=True,
        )
        early_pool = [
            item for item in scored
            if item.candidate.segment == segment
            and item.candidate.metadata.get("early_candidate")
            and actionable(item, early=True)
        ]
        early_pool.sort(
            key=lambda item: (
                int(item.candidate.metadata.get("early_signal_score", 0)),
                item.score,
            ),
            reverse=True,
        )
        reserve = min(max(int(early_counts.get(segment, 0)), 0), count)
        early_picks = early_pool[:reserve]
        picked_keys = {item.candidate.key for item in early_picks}
        remaining = [item for item in standard if item.candidate.key not in picked_keys]
        items = early_picks + remaining[: max(count - len(early_picks), 0)]
        picked_keys = {item.candidate.key for item in items}
        if len(items) < count:
            items.extend(
                item for item in early_pool
                if item.candidate.key not in picked_keys
            )
        items = items[:count]
        items.sort(
            key=lambda item: (
                item.candidate.metadata.get("opportunity_tier") == "validated",
                item.score,
            ),
            reverse=True,
        )
        selected[segment] = items
    return selected
