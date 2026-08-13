from __future__ import annotations

from .config import HealthConfig
from .models import SourceStatus


def assess_source_health(
    statuses: list[SourceStatus],
    previous_counts: dict[str, int],
    config: HealthConfig,
) -> list[SourceStatus]:
    """Downgrade suspiciously small Apple and Steam snapshots.

    Collectors already flag an empty or partially parsed chart. This second
    layer catches plausible-looking responses whose overall item count has
    collapsed compared with the previous healthy daily snapshot.
    """

    assessed: list[SourceStatus] = []
    minimums = {"Apple": config.apple_min_items, "Steam": config.steam_min_items}
    for status in statuses:
        source = status.name.split("/", 1)[0]
        if source not in minimums or status.state in {"failed", "disabled"}:
            assessed.append(status)
            continue

        issues: list[str] = []
        minimum = minimums[source]
        if status.item_count < minimum:
            issues.append(f"item count {status.item_count} below minimum {minimum}")

        previous_count = previous_counts.get(status.name)
        if previous_count:
            ratio = status.item_count / previous_count
            if ratio < config.previous_count_ratio:
                issues.append(
                    f"item count fell to {ratio:.0%} of previous healthy snapshot "
                    f"({status.item_count}/{previous_count})"
                )

        if not issues:
            assessed.append(status)
            continue
        detail = "; ".join(part for part in (status.detail, *issues) if part)
        assessed.append(
            SourceStatus(
                name=status.name,
                item_count=status.item_count,
                state="degraded",
                detail=detail,
            )
        )
    return assessed
