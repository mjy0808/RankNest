from __future__ import annotations

from collections import defaultdict
from typing import Any

from .models import Candidate, HistorySnapshot


VALIDATION_HORIZONS = (7, 14, 30)


def _confirmed_growth(candidate: Candidate, previous_rank: float, previous_reviews: int) -> bool:
    rank_improved = candidate.average_rank <= previous_rank - 3
    minimum_review_gain = max(5, round(previous_reviews * 0.01))
    reviews_improved = candidate.review_count >= previous_reviews + minimum_review_gain
    return rank_improved or reviews_improved


def build_backtest(
    candidates: list[Candidate], history: dict[int, HistorySnapshot]
) -> dict[str, Any]:
    current = {candidate.key: candidate for candidate in candidates}
    active_segments = {candidate.segment for candidate in candidates}
    horizons: dict[str, Any] = {}
    for horizon in VALIDATION_HORIZONS:
        snapshot = history.get(horizon)
        if snapshot is None:
            continue
        selected = [
            (key, observation)
            for key, observation in snapshot.observations.items()
            if observation.selected_rank is not None
            and observation.segment in active_segments
        ]
        if not selected:
            continue
        confirmed = 0
        retained = 0
        segment_totals: dict[str, int] = defaultdict(int)
        segment_confirmed: dict[str, int] = defaultdict(int)
        winners: list[str] = []
        for key, observation in selected:
            segment = observation.segment or "unknown"
            segment_totals[segment] += 1
            candidate = current.get(key)
            if candidate is None:
                continue
            retained += 1
            if _confirmed_growth(candidate, observation.average_rank, observation.review_count):
                confirmed += 1
                segment_confirmed[segment] += 1
                winners.append(candidate.name or observation.name)
        horizons[str(horizon)] = {
            "actual_days": snapshot.actual_days,
            "total": len(selected),
            "retained": retained,
            "confirmed": confirmed,
            "retention_rate": round(retained / len(selected) * 100, 1),
            "confirmation_rate": round(confirmed / len(selected) * 100, 1),
            "segments": {
                segment: {
                    "total": total,
                    "confirmed": segment_confirmed.get(segment, 0),
                    "confirmation_rate": round(
                        segment_confirmed.get(segment, 0) / total * 100, 1
                    ),
                }
                for segment, total in segment_totals.items()
            },
            "winners": winners[:5],
        }
    return {
        "available_horizons": [int(value) for value in horizons],
        "horizons": horizons,
        "definition": "历史入选产品当前仍在监控范围，且平均榜位改善至少 3 位或评价增长至少 1%/5 条",
    }
