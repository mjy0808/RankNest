from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import HistorySnapshot, PreviousObservation, ScoredCandidate, SourceStatus


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    source_status_json TEXT NOT NULL,
    candidate_count INTEGER NOT NULL,
    run_day TEXT,
    timezone TEXT,
    data_fingerprint TEXT,
    report_path TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    developer TEXT NOT NULL,
    url TEXT NOT NULL,
    image_url TEXT NOT NULL,
    release_date TEXT,
    update_date TEXT,
    rating REAL NOT NULL,
    review_count INTEGER NOT NULL,
    market_count INTEGER NOT NULL,
    best_rank INTEGER NOT NULL,
    average_rank REAL NOT NULL,
    social_mentions INTEGER NOT NULL,
    score REAL NOT NULL,
    ranks_json TEXT NOT NULL,
    components_json TEXT NOT NULL,
    segment TEXT,
    review_counts_json TEXT NOT NULL DEFAULT '{}',
    ratings_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    selected_rank INTEGER,
    PRIMARY KEY (run_id, source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_observations_identity
ON observations(source, external_id, run_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_day_timezone
ON runs(run_day, timezone) WHERE run_day IS NOT NULL;
"""


RUN_COLUMNS = {
    "run_day": "TEXT",
    "timezone": "TEXT",
    "data_fingerprint": "TEXT",
    "report_path": "TEXT",
}

OBSERVATION_COLUMNS = {
    "segment": "TEXT",
    "review_counts_json": "TEXT NOT NULL DEFAULT '{}'",
    "ratings_json": "TEXT NOT NULL DEFAULT '{}'",
    "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
    "selected_rank": "INTEGER",
}


class RadarStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _column_names(self, table: str) -> set[str]:
        return {str(row[1]) for row in self.connection.execute(f"PRAGMA table_info({table})")}

    def _add_missing_columns(self, table: str, columns: dict[str, str]) -> None:
        existing = self._column_names(table)
        for name, declaration in columns.items():
            if name not in existing:
                self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def _migrate(self) -> None:
        # Create legacy-compatible tables first, then add v2 columns for an
        # existing database before creating indexes that reference them.
        self.connection.executescript(
            SCHEMA.split("CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_day_timezone", 1)[0]
        )
        self._add_missing_columns("runs", RUN_COLUMNS)
        self._add_missing_columns("observations", OBSERVATION_COLUMNS)
        self.connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_day_timezone
            ON runs(run_day, timezone) WHERE run_day IS NOT NULL
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "RadarStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def backfill_legacy_run_days(self, timezone_name: str) -> None:
        zone = ZoneInfo(timezone_name)
        rows = self.connection.execute(
            "SELECT id, captured_at FROM runs WHERE run_day IS NULL ORDER BY captured_at DESC, id DESC"
        ).fetchall()
        with self.connection:
            for run_id, captured_at_text in rows:
                captured_at = datetime.fromisoformat(captured_at_text)
                if captured_at.tzinfo is None:
                    captured_at = captured_at.replace(tzinfo=timezone.utc)
                run_day = captured_at.astimezone(zone).date().isoformat()
                try:
                    self.connection.execute(
                        "UPDATE runs SET run_day = ?, timezone = ? WHERE id = ?",
                        (run_day, timezone_name, run_id),
                    )
                except sqlite3.IntegrityError:
                    # Preserve older same-day legacy snapshots without making
                    # them eligible as the canonical daily baseline.
                    self.connection.execute(
                        "UPDATE runs SET run_day = ?, timezone = ? WHERE id = ?",
                        (run_day, f"{timezone_name}#legacy-{run_id}", run_id),
                    )

    @staticmethod
    def _healthy_markets(status_payload: list[dict[str, object]]) -> frozenset[tuple[str, str]]:
        markets: set[tuple[str, str]] = set()
        for status in status_payload:
            if status.get("state", "healthy" if status.get("ok") else "failed") != "healthy":
                continue
            name = str(status.get("name", ""))
            if "/" not in name:
                continue
            source_name, market = name.split("/", 1)
            source = {"Apple": "apple", "Steam": "steam"}.get(source_name)
            if source:
                markets.add((source, market.lower()))
        return frozenset(markets)

    def _load_observations(
        self, run_id: int, run_day: date, captured_at: datetime
    ) -> dict[tuple[str, str], PreviousObservation]:
        observations: dict[tuple[str, str], PreviousObservation] = {}
        rows = self.connection.execute(
            """
            SELECT source, external_id, average_rank, best_rank, review_count,
                   market_count, social_mentions, ranks_json, review_counts_json,
                   selected_rank
            FROM observations WHERE run_id = ?
            """,
            (run_id,),
        )
        for (
            source, external_id, avg_rank, best_rank, reviews, markets, mentions,
            ranks, counts, selected_rank,
        ) in rows:
            try:
                rank_values = {str(k): int(v) for k, v in json.loads(ranks or "{}").items()}
            except (TypeError, ValueError, json.JSONDecodeError):
                rank_values = {}
            try:
                review_values = {str(k): int(v) for k, v in json.loads(counts or "{}").items()}
            except (TypeError, ValueError, json.JSONDecodeError):
                review_values = {}
            observations[(str(source), str(external_id))] = PreviousObservation(
                captured_at=captured_at,
                run_day=run_day,
                average_rank=float(avg_rank),
                best_rank=int(best_rank),
                review_count=int(reviews),
                market_count=int(markets),
                social_mentions=int(mentions),
                selected_rank=int(selected_rank) if selected_rank is not None else None,
                ranks=rank_values,
                review_counts=review_values,
            )
        return observations

    def load_history(
        self,
        current_day: date,
        timezone_name: str,
        horizons: tuple[int, ...] = (1, 3, 7),
    ) -> dict[int, HistorySnapshot]:
        self.backfill_legacy_run_days(timezone_name)
        snapshots: dict[int, HistorySnapshot] = {}
        for horizon in horizons:
            target_day = current_day - timedelta(days=horizon)
            row = self.connection.execute(
                """
                SELECT id, run_day, captured_at, source_status_json
                FROM runs
                WHERE timezone = ? AND run_day <= ? AND run_day < ?
                ORDER BY run_day DESC, id DESC LIMIT 1
                """,
                (timezone_name, target_day.isoformat(), current_day.isoformat()),
            ).fetchone()
            if row is None:
                continue
            run_id, run_day_text, captured_at_text, status_json = row
            history_day = date.fromisoformat(run_day_text)
            actual_days = (current_day - history_day).days
            # A 1-day window should not silently become a week-old baseline.
            if actual_days > horizon + 2:
                continue
            captured_at = datetime.fromisoformat(captured_at_text)
            try:
                status_payload = json.loads(status_json)
            except (TypeError, json.JSONDecodeError):
                status_payload = []
            snapshots[horizon] = HistorySnapshot(
                run_id=int(run_id),
                run_day=history_day,
                captured_at=captured_at,
                actual_days=actual_days,
                observations=self._load_observations(int(run_id), history_day, captured_at),
                healthy_markets=self._healthy_markets(status_payload),
            )
        return snapshots

    def load_previous_source_counts(
        self, current_day: date, timezone_name: str
    ) -> dict[str, int]:
        row = self.connection.execute(
            """
            SELECT source_status_json FROM runs
            WHERE timezone = ? AND run_day < ?
            ORDER BY run_day DESC, id DESC LIMIT 1
            """,
            (timezone_name, current_day.isoformat()),
        ).fetchone()
        if row is None:
            return {}
        try:
            payload = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return {}
        return {
            str(status.get("name")): int(status.get("item_count", 0))
            for status in payload
            if isinstance(status, dict)
            and status.get("state", "healthy" if status.get("ok") else "failed") == "healthy"
        }

    def load_selected_streaks(
        self,
        current_day: date,
        timezone_name: str,
        selected_keys: set[tuple[str, str]],
    ) -> dict[tuple[str, str], int]:
        if not selected_keys:
            return {}
        days_by_key: dict[tuple[str, str], set[date]] = {key: set() for key in selected_keys}
        rows = self.connection.execute(
            """
            SELECT r.run_day, o.source, o.external_id
            FROM runs r JOIN observations o ON o.run_id = r.id
            WHERE r.timezone = ? AND r.run_day < ? AND o.selected_rank IS NOT NULL
            ORDER BY r.run_day DESC
            """,
            (timezone_name, current_day.isoformat()),
        )
        for run_day_text, source, external_id in rows:
            key = (str(source), str(external_id))
            if key in days_by_key:
                days_by_key[key].add(date.fromisoformat(run_day_text))

        streaks: dict[tuple[str, str], int] = {}
        for key, days in days_by_key.items():
            streak = 1
            while current_day - timedelta(days=streak) in days:
                streak += 1
            streaks[key] = streak
        return streaks

    def save_run(
        self,
        captured_at: datetime,
        run_day: date,
        timezone_name: str,
        fingerprint: str,
        report_path: Path,
        statuses: list[SourceStatus],
        scored: list[ScoredCandidate],
        selected_ranks: dict[tuple[str, str], int] | None = None,
    ) -> tuple[int, bool]:
        status_json = json.dumps(
            [status.as_dict() for status in statuses], ensure_ascii=False, separators=(",", ":")
        )
        existing = self.connection.execute(
            "SELECT id FROM runs WHERE run_day = ? AND timezone = ?",
            (run_day.isoformat(), timezone_name),
        ).fetchone()
        with self.connection:
            if existing:
                run_id = int(existing[0])
                self.connection.execute("DELETE FROM observations WHERE run_id = ?", (run_id,))
                self.connection.execute(
                    """
                    UPDATE runs SET captured_at = ?, source_status_json = ?, candidate_count = ?,
                                    data_fingerprint = ?, report_path = ?
                    WHERE id = ?
                    """,
                    (
                        captured_at.isoformat(), status_json, len(scored), fingerprint,
                        str(report_path), run_id,
                    ),
                )
            else:
                cursor = self.connection.execute(
                    """
                    INSERT INTO runs(
                        captured_at, source_status_json, candidate_count, run_day,
                        timezone, data_fingerprint, report_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        captured_at.isoformat(), status_json, len(scored), run_day.isoformat(),
                        timezone_name, fingerprint, str(report_path),
                    ),
                )
                run_id = int(cursor.lastrowid)

            rows = []
            for item in scored:
                candidate = item.candidate
                rows.append(
                    (
                        run_id, candidate.source, candidate.external_id, candidate.name,
                        candidate.kind, candidate.developer, candidate.url, candidate.image_url,
                        candidate.release_date.isoformat() if candidate.release_date else None,
                        candidate.update_date.isoformat() if candidate.update_date else None,
                        candidate.rating, candidate.review_count, candidate.market_count,
                        candidate.best_rank, candidate.average_rank, candidate.social_mentions,
                        item.score,
                        json.dumps(candidate.ranks, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(item.components, ensure_ascii=False, separators=(",", ":")),
                        candidate.segment,
                        json.dumps(candidate.review_counts, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(candidate.ratings, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(candidate.metadata, ensure_ascii=False, separators=(",", ":")),
                        (selected_ranks or {}).get(candidate.key),
                    )
                )
            self.connection.executemany(
                """
                INSERT INTO observations(
                    run_id, source, external_id, name, kind, developer, url, image_url,
                    release_date, update_date, rating, review_count, market_count,
                    best_rank, average_rank, social_mentions, score, ranks_json, components_json,
                    segment, review_counts_json, ratings_json, metadata_json, selected_rank
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return run_id, bool(existing)

    def prune_history(self, current_day: date, timezone_name: str, retention_days: int) -> int:
        cutoff = (current_day - timedelta(days=retention_days)).isoformat()
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM runs WHERE timezone = ? AND run_day < ?",
                (timezone_name, cutoff),
            )
        return int(cursor.rowcount)
