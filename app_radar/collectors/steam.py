from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from ..config import SteamConfig
from ..http import HttpClient
from ..models import Candidate, SourceStatus


SEARCH_URL = "https://store.steampowered.com/search/results/"


def _strip_tags(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _release_date(value: str) -> date | None:
    value = " ".join(value.split())
    for pattern in ("%b %d, %Y", "%d %b, %Y", "%b %Y", "%Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    return None


def parse_results_html(results_html: str, market: str, chart: str) -> list[Candidate]:
    rows = re.findall(
        r"<a\s+([^>]*?class=\"[^\"]*search_result_row[^\"]*\"[^>]*)>(.*?)</a>",
        results_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    parsed: list[Candidate] = []
    for rank, (attributes, body) in enumerate(rows, start=1):
        app_id_match = re.search(r'data-ds-appid="(\d+)"', attributes)
        title_match = re.search(
            r'<span[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</span>',
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not app_id_match or not title_match:
            continue
        app_id = app_id_match.group(1)
        name = _strip_tags(title_match.group(1))
        href_match = re.search(r'href="([^"]+)"', attributes)
        image_match = re.search(r'<img[^>]+src="([^"]+)"', body, flags=re.IGNORECASE)
        release_match = re.search(
            r'<div[^>]*class="[^"]*search_released[^"]*"[^>]*>(.*?)</div>',
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        tooltip_match = re.search(r'data-tooltip-html="([^"]+)"', body, flags=re.IGNORECASE)
        price_match = re.search(
            r'<div[^>]*class="[^"]*discount_final_price[^"]*"[^>]*>(.*?)</div>',
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        tags_match = re.search(
            r'<div[^>]*class="[^"]*search_tags[^"]*"[^>]*>(.*?)</div>',
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        tag_ids_match = re.search(r'data-ds-tagids="([^"]+)"', attributes)

        rating = 0.0
        review_count = 0
        if tooltip_match:
            tooltip = html.unescape(tooltip_match.group(1))
            review_match = re.search(
                r"(\d+)%\s+of\s+the\s+([\d,]+)\s+user reviews", tooltip, flags=re.IGNORECASE
            )
            if review_match:
                rating = int(review_match.group(1)) / 20.0
                review_count = int(review_match.group(2).replace(",", ""))

        candidate = Candidate(
            source="steam",
            external_id=app_id,
            name=name,
            kind="game",
            developer="",
            url=html.unescape(href_match.group(1)).split("?", 1)[0] if href_match else "",
            image_url=html.unescape(image_match.group(1)) if image_match else "",
            release_date=_release_date(_strip_tags(release_match.group(1))) if release_match else None,
            price_text=_strip_tags(price_match.group(1)) if price_match else "",
            ranks={f"{market}:{chart}": rank},
            ratings={market: rating} if rating else {},
            review_counts={market: review_count} if review_count else {},
            metadata={
                "genres": [
                    value.strip()
                    for value in _strip_tags(tags_match.group(1)).split(",")
                    if value.strip()
                ]
                if tags_match
                else [],
                "steam_tag_ids": tag_ids_match.group(1) if tag_ids_match else "",
            },
        )
        parsed.append(candidate)
    return parsed


def parse_search_payload(data: dict[str, Any], market: str, chart: str) -> list[Candidate]:
    if int(data.get("success", 0)) != 1:
        raise RuntimeError("Steam search response reported failure")
    results_html = data.get("results_html", "")
    if not isinstance(results_html, str):
        raise RuntimeError("Steam results_html is missing")
    return parse_results_html(results_html, market, chart)


def _collect_market(
    market: str, config: SteamConfig, client: HttpClient
) -> tuple[list[Candidate], SourceStatus]:
    combined: dict[str, Candidate] = {}
    errors: list[str] = []
    for chart in config.charts:
        params = {
            "query": "",
            "start": 0,
            "count": config.limit,
            "dynamic_data": "",
            "sort_by": "_ASC",
            "filter": chart,
            "infinite": 1,
            "category1": 998,
            "cc": market,
            "l": "english",
        }
        try:
            candidates = parse_search_payload(
                client.get_json(f"{SEARCH_URL}?{urlencode(params)}"), market, chart
            )
        except Exception as exc:
            errors.append(f"{chart}: {exc}")
            continue
        if len(candidates) < min(config.limit, 10):
            errors.append(f"{chart}: parsed only {len(candidates)} items")
        for candidate in candidates:
            if candidate.external_id in combined:
                combined[candidate.external_id].merge(candidate)
            else:
                combined[candidate.external_id] = candidate
    state = "healthy" if combined and not errors else "degraded" if combined else "failed"
    return list(combined.values()), SourceStatus(
        name=f"Steam/{market.upper()}",
        item_count=len(combined),
        state=state,
        detail="; ".join(errors[:3]),
    )


def collect_steam(
    config: SteamConfig, client: HttpClient
) -> tuple[list[Candidate], list[SourceStatus]]:
    if not config.enabled:
        return [], [SourceStatus("Steam", 0, "disabled", "disabled")]

    merged: dict[tuple[str, str], Candidate] = {}
    statuses: list[SourceStatus] = []
    results_by_market: dict[str, list[Candidate]] = {}
    with ThreadPoolExecutor(max_workers=max(config.workers, 1)) as executor:
        futures = {
            executor.submit(_collect_market, market, config, client): market
            for market in config.markets
        }
        for future in as_completed(futures):
            market = futures[future]
            try:
                candidates, status = future.result()
            except Exception as exc:
                statuses.append(SourceStatus(f"Steam/{market.upper()}", 0, "failed", str(exc)))
                continue
            statuses.append(status)
            results_by_market[market] = candidates
    for market in config.markets:
        for candidate in results_by_market.get(market, []):
            if candidate.key in merged:
                merged[candidate.key].merge(candidate)
            else:
                merged[candidate.key] = candidate
    statuses.sort(key=lambda status: status.name)
    return list(merged.values()), statuses
