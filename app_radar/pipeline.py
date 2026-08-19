from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .collectors.apple import collect_apple
from .collectors.social import apply_hacker_news_mentions
from .collectors.steam import collect_steam
from .config import Config
from .http import HttpClient
from .health import assess_source_health
from .models import Candidate, SourceStatus
from .opportunities import build_theme_summaries
from .report import ReportArtifacts, write_report
from .scoring import score_all, select_segment_items
from .storage import RadarStore
from .validation import build_backtest


@dataclass(frozen=True)
class PipelineResult:
    artifacts: ReportArtifacts
    statuses: list[SourceStatus]
    total_candidates: int
    selected_count: int
    run_id: int
    fingerprint: str
    history_days: tuple[int, ...]
    replaced_existing_run: bool


def _merge(target: dict[tuple[str, str], Candidate], candidates: list[Candidate]) -> None:
    for candidate in candidates:
        if candidate.key in target:
            target[candidate.key].merge(candidate)
        else:
            target[candidate.key] = candidate


def _fingerprint(candidates: list[Candidate], statuses: list[SourceStatus]) -> str:
    payload = {
        "candidates": [
            {
                "key": candidate.key,
                "segment": candidate.segment,
                "ranks": sorted(candidate.ranks.items()),
                "reviews": sorted(candidate.review_counts.items()),
                "rating": round(candidate.rating, 5),
                "mentions": candidate.social_mentions,
            }
            for candidate in sorted(candidates, key=lambda item: item.key)
        ],
        "statuses": [status.as_dict() for status in sorted(statuses, key=lambda item: item.name)],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def run_pipeline(config: Config, database_path: Path, output_dir: Path) -> PipelineResult:
    captured_at = datetime.now(timezone.utc)
    local_now = captured_at.astimezone(ZoneInfo(config.report.timezone))
    client = HttpClient(timeout=config.network.timeout_seconds, retries=config.network.retries)

    merged: dict[tuple[str, str], Candidate] = {}
    statuses: list[SourceStatus] = []
    apple_candidates, apple_statuses = collect_apple(config.apple, client)
    _merge(merged, apple_candidates)
    statuses.extend(apple_statuses)

    steam_candidates, steam_statuses = collect_steam(config.steam, client)
    _merge(merged, steam_candidates)
    statuses.extend(steam_statuses)

    candidates = list(merged.values())
    if config.social.hacker_news_enabled and candidates:
        statuses.append(
            apply_hacker_news_mentions(
                candidates, client, config.social.lookback_hours, config.social.max_stories
            )
        )
    if not candidates:
        details = "; ".join(
            f"{status.name}: {status.detail}" for status in statuses if not status.usable
        )
        raise RuntimeError(f"all collectors returned no candidates. {details}")

    report_path = output_dir / "archive" / f"{local_now:%Y-%m-%d}.html"
    with RadarStore(database_path) as store:
        previous_counts = store.load_previous_source_counts(
            local_now.date(), config.report.timezone
        )
        statuses = assess_source_health(statuses, previous_counts, config.health)
        fingerprint = _fingerprint(candidates, statuses)
        history = store.load_history(local_now.date(), config.report.timezone)
        first_seen_days = store.load_first_seen_days(
            local_now.date(), config.report.timezone, set(merged)
        )
        for candidate in candidates:
            candidate.metadata["first_seen_days"] = first_seen_days.get(candidate.key, 0)
        scored = score_all(candidates, history, statuses, local_now.date())
        sections = select_segment_items(
            scored,
            config.report.segment_counts,
            config.report.segment_early_counts,
        )
        backtest = build_backtest(candidates, history)
        selected_ranks = {
            item.candidate.key: rank
            for items in sections.values()
            for rank, item in enumerate(items, start=1)
        }
        streaks = store.load_selected_streaks(
            local_now.date(), config.report.timezone, set(selected_ranks)
        )
        for items in sections.values():
            for item in items:
                item.candidate.metadata["selected_streak"] = streaks.get(
                    item.candidate.key, 1
                )
        themes = build_theme_summaries(sections)
        run_id, replaced = store.save_run(
            captured_at=captured_at,
            run_day=local_now.date(),
            timezone_name=config.report.timezone,
            fingerprint=fingerprint,
            report_path=report_path,
            statuses=statuses,
            scored=scored,
            selected_ranks=selected_ranks,
        )
        store.prune_history(
            local_now.date(), config.report.timezone, config.storage.retention_days
        )

    artifacts = write_report(
        output_dir=output_dir,
        title=config.report.title,
        generated_at=local_now,
        sections=sections,
        statuses=statuses,
        total_candidates=len(candidates),
        history_days=sorted(history),
        run_id=run_id,
        fingerprint=fingerprint,
        themes=themes,
        backtest=backtest,
        retention_days=config.storage.retention_days,
    )
    return PipelineResult(
        artifacts=artifacts,
        statuses=statuses,
        total_candidates=len(candidates),
        selected_count=sum(len(items) for items in sections.values()),
        run_id=run_id,
        fingerprint=fingerprint,
        history_days=tuple(sorted(history)),
        replaced_existing_run=replaced,
    )
