from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReportConfig:
    title: str = "全球潜力 App / 游戏日报"
    app_count: int = 8
    mobile_game_count: int = 6
    steam_game_count: int = 6
    timezone: str = "Asia/Shanghai"

    @property
    def top_count(self) -> int:
        return self.app_count + self.mobile_game_count + self.steam_game_count

    @property
    def segment_counts(self) -> dict[str, int]:
        return {
            "app": self.app_count,
            "mobile_game": self.mobile_game_count,
            "steam_game": self.steam_game_count,
        }


@dataclass(frozen=True)
class AppleConfig:
    enabled: bool = True
    markets: tuple[str, ...] = (
        "us", "gb", "de", "fr", "jp", "kr", "cn", "in", "br", "mx", "id", "au"
    )
    charts: tuple[str, ...] = ("top-free", "top-paid")
    mobile_game_charts: tuple[str, ...] = ("top-free", "top-grossing")
    limit: int = 100
    workers: int = 6


@dataclass(frozen=True)
class SteamConfig:
    enabled: bool = True
    markets: tuple[str, ...] = ("us", "gb", "de", "jp", "kr", "br")
    charts: tuple[str, ...] = ("topsellers", "popularnew")
    limit: int = 100
    workers: int = 4


@dataclass(frozen=True)
class SocialConfig:
    hacker_news_enabled: bool = True
    lookback_hours: int = 48
    max_stories: int = 3000


@dataclass(frozen=True)
class NetworkConfig:
    timeout_seconds: int = 25
    retries: int = 2


@dataclass(frozen=True)
class StorageConfig:
    retention_days: int = 45


@dataclass(frozen=True)
class Config:
    report: ReportConfig = field(default_factory=ReportConfig)
    apple: AppleConfig = field(default_factory=AppleConfig)
    steam: SteamConfig = field(default_factory=SteamConfig)
    social: SocialConfig = field(default_factory=SocialConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"config section {name!r} must be an object")
    return value


def load_config(path: Path) -> Config:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config root must be an object")

    report = _section(data, "report")
    apple = _section(data, "apple")
    steam = _section(data, "steam")
    social = _section(data, "social")
    network = _section(data, "network")
    storage = _section(data, "storage")

    config = Config(
        report=ReportConfig(
            title=str(report.get("title", ReportConfig.title)),
            app_count=int(report.get("app_count", 8)),
            mobile_game_count=int(report.get("mobile_game_count", 6)),
            steam_game_count=int(report.get("steam_game_count", 6)),
            timezone=str(report.get("timezone", ReportConfig.timezone)),
        ),
        apple=AppleConfig(
            enabled=bool(apple.get("enabled", True)),
            markets=tuple(str(x).lower() for x in apple.get("markets", AppleConfig.markets)),
            charts=tuple(str(x) for x in apple.get("charts", AppleConfig.charts)),
            mobile_game_charts=tuple(
                str(x) for x in apple.get("mobile_game_charts", AppleConfig.mobile_game_charts)
            ),
            limit=int(apple.get("limit", AppleConfig.limit)),
            workers=int(apple.get("workers", AppleConfig.workers)),
        ),
        steam=SteamConfig(
            enabled=bool(steam.get("enabled", True)),
            markets=tuple(str(x).lower() for x in steam.get("markets", SteamConfig.markets)),
            charts=tuple(str(x) for x in steam.get("charts", SteamConfig.charts)),
            limit=int(steam.get("limit", SteamConfig.limit)),
            workers=int(steam.get("workers", SteamConfig.workers)),
        ),
        social=SocialConfig(
            hacker_news_enabled=bool(social.get("hacker_news_enabled", True)),
            lookback_hours=int(social.get("lookback_hours", SocialConfig.lookback_hours)),
            max_stories=int(social.get("max_stories", SocialConfig.max_stories)),
        ),
        network=NetworkConfig(
            timeout_seconds=int(network.get("timeout_seconds", NetworkConfig.timeout_seconds)),
            retries=int(network.get("retries", NetworkConfig.retries)),
        ),
        storage=StorageConfig(
            retention_days=int(storage.get("retention_days", StorageConfig.retention_days)),
        ),
    )
    _validate(config)
    return config


def _validate(config: Config) -> None:
    if any(value < 1 for value in config.report.segment_counts.values()):
        raise ValueError("all report segment counts must be positive")
    if not 1 <= config.apple.limit <= 100:
        raise ValueError("apple.limit must be between 1 and 100")
    if not 1 <= config.steam.limit <= 100:
        raise ValueError("steam.limit must be between 1 and 100")
    if config.network.timeout_seconds < 1 or config.network.retries < 0:
        raise ValueError("invalid network timeout or retry count")
    if config.storage.retention_days < 8:
        raise ValueError("storage.retention_days must be at least 8")
