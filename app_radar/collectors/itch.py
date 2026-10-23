from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree

from ..config import ItchConfig
from ..http import HttpClient
from ..models import Candidate, SourceStatus


PLATFORM_LABELS = {"web": "Web", "windows": "Windows", "osx": "macOS", "linux": "Linux"}


def feed_url(platform: str, chart: str) -> str:
    prefix = "" if chart == "popular" else f"{chart}/"
    return f"https://itch.io/games/{prefix}platform-{platform}.xml"


def _feed_date(value: str | None) -> date | None:
    try:
        return parsedate_to_datetime(value or "").date()
    except (TypeError, ValueError, OverflowError):
        return None


def parse_feed(text: str, platform: str, chart: str, limit: int) -> list[Candidate]:
    root = ElementTree.fromstring(text)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("itch.io returned an invalid RSS feed")
    candidates: list[Candidate] = []
    for rank, item in enumerate(root.findall("./channel/item")[:limit], start=1):
        full_title = (item.findtext("title") or "").strip()
        name = (item.findtext("plainTitle") or re.sub(r"(?:\s+\[[^\]]+\])+$", "", full_title)).strip()
        url = (item.findtext("link") or "").strip().split("?", 1)[0].rstrip("/")
        parsed = urlparse(url)
        if not name or parsed.scheme != "https" or not (parsed.hostname or "").endswith(".itch.io"):
            continue
        description_html = item.findtext("description") or ""
        image = re.search(r'<img[^>]+src=[\"\x27]([^\"\x27]+)', description_html, re.I)
        description = " ".join(html.unescape(re.sub(r"<[^>]*>", " ", description_html)).split())
        created = _feed_date(item.findtext("createDate"))
        updated = _feed_date(item.findtext("updateDate") or item.findtext("pubDate"))
        categories = [value.text for value in item.findall("category") if value.text]
        categories.extend(
            value for value in re.findall(r"\[([^\]]+)\]", full_title)
            if value.casefold() not in {"free", "paid", "windows", "linux", "macos", "osx", "html5", "web"}
            and not value.startswith("$")
        )
        developer = item.findtext("{http://purl.org/dc/elements/1.1/}creator") or parsed.hostname.split(".")[0]
        candidates.append(Candidate(
            source="itch", external_id=url, name=html.unescape(name), kind="game",
            developer=developer, url=url,
            image_url=html.unescape(item.findtext("imageurl") or (image.group(1) if image else "")),
            release_date=created, update_date=updated, price_text=item.findtext("price") or "",
            ranks={f"global:{platform}-{chart}": rank},
            metadata={
                "description": description[:1000], "genres": categories,
                "platforms": [PLATFORM_LABELS[platform]],
                "discovery_charts": [chart], "rank_limit": limit,
                "review_count_unavailable": True, "release_date_unavailable": created is None,
                "source_note": "itch.io 热榜；RSS 未提供评分和评价量，创建日期可用时作为上线参考",
            },
        ))
    return candidates


def collect_itch(config: ItchConfig, client: HttpClient) -> tuple[list[Candidate], list[SourceStatus]]:
    if not config.enabled:
        return [], [SourceStatus("Itch/GLOBAL", 0, "disabled", "disabled")]
    results: dict[tuple[str, str], list[Candidate]] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(config.workers, 1)) as executor:
        futures = {
            executor.submit(client.get_text, feed_url(platform, chart)): (platform, chart)
            for platform in config.platforms for chart in config.charts
        }
        for future in as_completed(futures):
            platform, chart = futures[future]
            try:
                items = parse_feed(future.result(), platform, chart, config.limit)
                results[(platform, chart)] = items
                if len(items) < min(5, config.limit):
                    errors.append(f"{platform}/{chart}: parsed only {len(items)} items")
            except Exception as exc:
                errors.append(f"{platform}/{chart}: {exc}")
    merged: dict[tuple[str, str], Candidate] = {}
    for platform in config.platforms:
        for chart in config.charts:
            for candidate in results.get((platform, chart), []):
                if candidate.key in merged:
                    merged[candidate.key].merge(candidate)
                else:
                    merged[candidate.key] = candidate
    state = "healthy" if merged and not errors else "degraded" if merged else "failed"
    return list(merged.values()), [SourceStatus(
        "Itch/GLOBAL", len(merged), state, "; ".join(sorted(errors)[:3])
    )]
