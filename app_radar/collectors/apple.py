from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from itertools import islice
from typing import Any, Iterable
from urllib.parse import urlencode

from ..config import AppleConfig
from ..http import HttpClient
from ..models import Candidate, SourceStatus


FEED_TEMPLATE = "https://rss.marketingtools.apple.com/api/v2/{market}/apps/{chart}/{limit}/apps.json"
GAME_FEED_TEMPLATE = (
    "https://itunes.apple.com/{market}/rss/{chart}/limit={limit}/genre=6014/json"
)
LOOKUP_URL = "https://itunes.apple.com/lookup"
GAME_CHART_PATHS = {
    "top-free": "topfreeapplications",
    "top-paid": "toppaidapplications",
    "top-grossing": "topgrossingapplications",
}


def _date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _chunks(values: Iterable[str], size: int) -> Iterable[list[str]]:
    iterator = iter(values)
    while chunk := list(islice(iterator, size)):
        yield chunk


def parse_feed(data: dict[str, Any], market: str, chart: str) -> dict[str, Candidate]:
    feed = data.get("feed", {})
    results = feed.get("results", []) if isinstance(feed, dict) else []
    parsed: dict[str, Candidate] = {}
    for rank, item in enumerate(results, start=1):
        if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
            continue
        external_id = str(item["id"])
        genres = item.get("genres", [])
        genre_names = [str(x.get("name", "")) for x in genres if isinstance(x, dict)]
        kind = "game" if any(name.lower() == "games" for name in genre_names) else "app"
        parsed[external_id] = Candidate(
            source="apple",
            external_id=external_id,
            name=str(item["name"]),
            kind=kind,
            developer=str(item.get("artistName", "")),
            url=str(item.get("url", "")),
            image_url=str(item.get("artworkUrl100", "")),
            release_date=_date(item.get("releaseDate")),
            ranks={f"{market}:{chart}": rank},
            metadata={"genres": genre_names},
        )
    return parsed


def _legacy_label(value: Any) -> str:
    return str(value.get("label", "")) if isinstance(value, dict) else ""


def parse_mobile_game_feed(
    data: dict[str, Any], market: str, chart: str
) -> dict[str, Candidate]:
    feed = data.get("feed", {})
    entries = feed.get("entry", []) if isinstance(feed, dict) else []
    if isinstance(entries, dict):
        entries = [entries]
    parsed: dict[str, Candidate] = {}
    for rank, item in enumerate(entries, start=1):
        if not isinstance(item, dict):
            continue
        id_attributes = item.get("id", {}).get("attributes", {})
        external_id = str(id_attributes.get("im:id", ""))
        name = _legacy_label(item.get("im:name"))
        if not external_id or not name:
            continue
        images = item.get("im:image", [])
        image_url = _legacy_label(images[-1]) if isinstance(images, list) and images else ""
        links = item.get("link", [])
        if isinstance(links, dict):
            links = [links]
        url = ""
        for link in links:
            attributes = link.get("attributes", {}) if isinstance(link, dict) else {}
            if attributes.get("rel") == "alternate" or not url:
                url = str(attributes.get("href", url))
        parsed[external_id] = Candidate(
            source="apple",
            external_id=external_id,
            name=name,
            kind="game",
            developer=_legacy_label(item.get("im:artist")),
            url=url,
            image_url=image_url,
            release_date=_date(_legacy_label(item.get("im:releaseDate"))),
            ranks={f"{market}:mobile-{chart}": rank},
            metadata={"genre": "Games"},
        )
    return parsed


def enrich_with_lookup(
    candidates: dict[str, Candidate], lookup_results: list[dict[str, Any]], market: str
) -> None:
    for item in lookup_results:
        if not isinstance(item, dict) or item.get("trackId") is None:
            continue
        candidate = candidates.get(str(item["trackId"]))
        if candidate is None:
            continue
        genre = str(item.get("primaryGenreName", ""))
        candidate.kind = "game" if genre.lower() == "games" else candidate.kind
        candidate.name = str(item.get("trackName") or candidate.name)
        candidate.developer = str(item.get("sellerName") or item.get("artistName") or candidate.developer)
        candidate.url = str(item.get("trackViewUrl") or candidate.url)
        candidate.image_url = str(item.get("artworkUrl100") or candidate.image_url)
        candidate.release_date = _date(item.get("releaseDate")) or candidate.release_date
        candidate.update_date = _date(item.get("currentVersionReleaseDate"))
        if item.get("averageUserRating") is not None:
            candidate.ratings[market] = float(item["averageUserRating"])
        if item.get("userRatingCount") is not None:
            candidate.review_counts[market] = int(item["userRatingCount"])
        candidate.price_text = str(item.get("formattedPrice") or "")
        candidate.metadata.update(
            {
                "genre": genre,
                "genres": [str(value) for value in item.get("genres", [])]
                if isinstance(item.get("genres"), list)
                else candidate.metadata.get("genres", []),
                "version": str(item.get("version", "")),
                "currency": str(item.get("currency", "")),
                "file_size_bytes": str(item.get("fileSizeBytes", "")),
            }
        )


def _collect_market(
    market: str, config: AppleConfig, client: HttpClient
) -> tuple[list[Candidate], SourceStatus]:
    combined: dict[str, Candidate] = {}
    errors: list[str] = []
    for chart in config.charts:
        url = FEED_TEMPLATE.format(market=market, chart=chart, limit=config.limit)
        try:
            chart_candidates = parse_feed(client.get_json(url), market, chart)
        except Exception as exc:  # Keep the other markets/charts alive.
            errors.append(f"{chart}: {exc}")
            continue
        if len(chart_candidates) < min(config.limit, 10):
            errors.append(f"{chart}: parsed only {len(chart_candidates)} items")
        for external_id, candidate in chart_candidates.items():
            if external_id in combined:
                combined[external_id].merge(candidate)
            else:
                combined[external_id] = candidate

    for chart in config.mobile_game_charts:
        path = GAME_CHART_PATHS.get(chart)
        if path is None:
            errors.append(f"mobile-{chart}: unsupported chart")
            continue
        url = GAME_FEED_TEMPLATE.format(market=market, chart=path, limit=config.limit)
        try:
            chart_candidates = parse_mobile_game_feed(
                client.get_json(url), market, chart
            )
        except Exception as exc:
            errors.append(f"mobile-{chart}: {exc}")
            continue
        if len(chart_candidates) < min(config.limit, 10):
            errors.append(f"mobile-{chart}: parsed only {len(chart_candidates)} items")
        for external_id, candidate in chart_candidates.items():
            if external_id in combined:
                combined[external_id].merge(candidate)
            else:
                combined[external_id] = candidate

    for ids in _chunks(combined.keys(), 100):
        query = urlencode({"id": ",".join(ids), "country": market})
        try:
            payload = client.get_json(f"{LOOKUP_URL}?{query}")
            results = payload.get("results", [])
            if isinstance(results, list):
                enrich_with_lookup(combined, results, market)
                if len(results) < max(1, round(len(ids) * 0.7)):
                    errors.append(f"lookup: returned {len(results)}/{len(ids)} items")
            else:
                errors.append("lookup: results field is not a list")
        except Exception as exc:
            errors.append(f"lookup: {exc}")

    state = "healthy" if combined and not errors else "degraded" if combined else "failed"
    detail = "; ".join(errors[:3])
    return list(combined.values()), SourceStatus(
        name=f"Apple/{market.upper()}", item_count=len(combined), state=state, detail=detail
    )


def collect_apple(
    config: AppleConfig, client: HttpClient
) -> tuple[list[Candidate], list[SourceStatus]]:
    if not config.enabled:
        return [], [SourceStatus("Apple", 0, "disabled", "disabled")]

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
                statuses.append(SourceStatus(f"Apple/{market.upper()}", 0, "failed", str(exc)))
                continue
            statuses.append(status)
            results_by_market[market] = candidates
    # Merge in configured order so the first market supplies canonical text.
    for market in config.markets:
        for candidate in results_by_market.get(market, []):
            if candidate.key in merged:
                merged[candidate.key].merge(candidate)
            else:
                merged[candidate.key] = candidate
    statuses.sort(key=lambda status: status.name)
    return list(merged.values()), statuses
