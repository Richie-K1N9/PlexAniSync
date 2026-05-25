# coding=utf-8
import json
import logging
import os
import sqlite3
import tempfile
import time
import uuid
from typing import Iterable, List, Optional, Tuple

from plexanisync.graphql import AnilistSeries
from plexanisync.logger_adapter import PrefixLoggerAdapter

logger = PrefixLoggerAdapter(logging.getLogger("PlexAniSync"), {"prefix": "CACHE"})

DEFAULT_MAX_AGE_DAYS = 14
DEFAULT_FULL_REFRESH_HOURS = 24
ACTIVE_STATUSES = ("CURRENT", "REPEATING", "PAUSED")


def _default_marker_path() -> str:
    return os.path.join(tempfile.gettempdir(), "plexanisync_session")


class SyncCache:
    def __init__(
        self,
        db_path: str,
        max_age_days: int = DEFAULT_MAX_AGE_DAYS,
        full_refresh_hours: int = DEFAULT_FULL_REFRESH_HOURS,
        session_marker_path: Optional[str] = None,
    ):
        self.db_path = db_path
        self.max_age_seconds = max_age_days * 86400
        self.full_refresh_seconds = full_refresh_hours * 3600
        self.session_marker_path = session_marker_path or _default_marker_path()
        self.enabled = db_path != ""

        if not self.enabled:
            logger.info("Cache disabled (empty cache_db_path)")
            self.conn: Optional[sqlite3.Connection] = None
            return

        is_memory = db_path == ":memory:"
        if not is_memory:
            parent = os.path.dirname(os.path.abspath(db_path))
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)

        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

        self._create_schema()
        self._check_lifecycle()

    def _create_schema(self):
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS anilist_entries (
                anilist_id    INTEGER PRIMARY KEY,
                progress      INTEGER NOT NULL,
                status        TEXT NOT NULL,
                score         INTEGER,
                episodes      INTEGER,
                media_status  TEXT,
                ended_year    INTEGER,
                title_romaji  TEXT,
                title_english TEXT,
                title_native  TEXT,
                synonyms_json TEXT,
                series_type   TEXT,
                series_format TEXT,
                source        TEXT,
                season        TEXT,
                started_year  INTEGER,
                refreshed_at  INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plex_match_cache (
                plex_guid     TEXT NOT NULL,
                season_number INTEGER NOT NULL,
                anilist_id    INTEGER NOT NULL,
                plex_title    TEXT,
                matched_at    INTEGER NOT NULL,
                PRIMARY KEY (plex_guid, season_number)
            );
            CREATE TABLE IF NOT EXISTS plex_state_cache (
                anilist_id    INTEGER NOT NULL,
                season_number INTEGER NOT NULL,
                plex_episodes INTEGER NOT NULL,
                plex_rating   INTEGER,
                synced_at     INTEGER NOT NULL,
                PRIMARY KEY (anilist_id, season_number)
            );
            CREATE TABLE IF NOT EXISTS cache_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        self.conn.commit()

    def _check_lifecycle(self):
        now = int(time.time())
        created_at = self._get_meta_int("created_at")
        if created_at is None:
            self._set_meta("created_at", str(now))
            created_at = now

        age_exceeded = (now - created_at) > self.max_age_seconds
        if age_exceeded:
            logger.info(f"Cache exceeded max age ({self.max_age_seconds // 86400}d), wiping")
            self._wipe(now)
            self._rotate_session_marker(now)
            return

        if self._restart_detected():
            logger.info("Container/host restart detected, wiping cache")
            self._wipe(now)
            self._rotate_session_marker(now)
            return

        if self._get_meta("session_id") is None:
            self._rotate_session_marker(now)

    def _restart_detected(self) -> bool:
        stored_session = self._get_meta("session_id")
        if stored_session is None:
            return False
        try:
            with open(self.session_marker_path, "r", encoding="utf-8") as f:
                marker_session = f.read().strip()
        except OSError:
            return True
        return marker_session != stored_session

    def _rotate_session_marker(self, now: int):
        new_session = str(uuid.uuid4())
        self._set_meta("session_id", new_session)
        try:
            parent = os.path.dirname(os.path.abspath(self.session_marker_path))
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            with open(self.session_marker_path, "w", encoding="utf-8") as f:
                f.write(new_session)
        except OSError as exc:
            logger.warning(f"Failed to write session marker at {self.session_marker_path}: {exc}")

    def _wipe(self, now: int):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM anilist_entries")
        cur.execute("DELETE FROM plex_match_cache")
        cur.execute("DELETE FROM plex_state_cache")
        cur.execute("DELETE FROM cache_meta")
        cur.execute(
            "INSERT INTO cache_meta(key, value) VALUES('created_at', ?)",
            (str(now),),
        )
        self.conn.commit()

    def _get_meta(self, key: str) -> Optional[str]:
        cur = self.conn.cursor()
        row = cur.execute("SELECT value FROM cache_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _get_meta_int(self, key: str) -> Optional[int]:
        value = self._get_meta(key)
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None

    def _set_meta(self, key: str, value: str):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO cache_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # --- AniList list cache ---

    def needs_full_refresh(self) -> bool:
        if not self.enabled:
            return True
        last = self._get_meta_int("full_refresh_at")
        if last is None:
            return True
        return (int(time.time()) - last) > self.full_refresh_seconds

    def mark_full_refresh(self):
        if not self.enabled:
            return
        self._set_meta("full_refresh_at", str(int(time.time())))

    def get_cached_entries(self, exclude_statuses: Iterable[str] = ACTIVE_STATUSES) -> List[AnilistSeries]:
        if not self.enabled:
            return []
        placeholders = ",".join("?" for _ in exclude_statuses)
        query = f"SELECT * FROM anilist_entries WHERE status NOT IN ({placeholders})"
        rows = self.conn.execute(query, tuple(exclude_statuses)).fetchall()
        return [self._row_to_series(r) for r in rows]

    def upsert_entries(self, entries: Iterable[AnilistSeries]):
        if not self.enabled:
            return
        now = int(time.time())
        cur = self.conn.cursor()
        cur.executemany(
            """
            INSERT INTO anilist_entries (
                anilist_id, progress, status, score, episodes, media_status, ended_year,
                title_romaji, title_english, title_native, synonyms_json,
                series_type, series_format, source, season, started_year, refreshed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(anilist_id) DO UPDATE SET
                progress = excluded.progress,
                status = excluded.status,
                score = excluded.score,
                episodes = excluded.episodes,
                media_status = excluded.media_status,
                ended_year = excluded.ended_year,
                title_romaji = excluded.title_romaji,
                title_english = excluded.title_english,
                title_native = excluded.title_native,
                synonyms_json = excluded.synonyms_json,
                series_type = excluded.series_type,
                series_format = excluded.series_format,
                source = excluded.source,
                season = excluded.season,
                started_year = excluded.started_year,
                refreshed_at = excluded.refreshed_at
            """,
            [self._series_to_row(e, now) for e in entries],
        )
        self.conn.commit()

    def replace_all_entries(self, entries: Iterable[AnilistSeries]):
        if not self.enabled:
            return
        cur = self.conn.cursor()
        cur.execute("DELETE FROM anilist_entries")
        self.conn.commit()
        self.upsert_entries(entries)
        self.mark_full_refresh()

    def remove_entries(self, anilist_ids: Iterable[int]):
        if not self.enabled:
            return
        ids = list(anilist_ids)
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        self.conn.execute(
            f"DELETE FROM anilist_entries WHERE anilist_id IN ({placeholders})",
            ids,
        )
        self.conn.commit()

    # --- Plex match cache ---

    def lookup_match(self, plex_guid: str, season_number: int) -> Optional[int]:
        if not self.enabled or not plex_guid:
            return None
        row = self.conn.execute(
            "SELECT anilist_id FROM plex_match_cache WHERE plex_guid = ? AND season_number = ?",
            (plex_guid, season_number),
        ).fetchone()
        return row["anilist_id"] if row else None

    def record_match(self, plex_guid: str, season_number: int, anilist_id: int, plex_title: str):
        if not self.enabled or not plex_guid:
            return
        self.conn.execute(
            "INSERT INTO plex_match_cache(plex_guid, season_number, anilist_id, plex_title, matched_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(plex_guid, season_number) DO UPDATE SET "
            "anilist_id = excluded.anilist_id, plex_title = excluded.plex_title, matched_at = excluded.matched_at",
            (plex_guid, season_number, anilist_id, plex_title, int(time.time())),
        )
        self.conn.commit()

    def vacuum_old_matches(self, max_age_days: int = 90):
        if not self.enabled:
            return
        cutoff = int(time.time()) - (max_age_days * 86400)
        self.conn.execute("DELETE FROM plex_match_cache WHERE matched_at < ?", (cutoff,))
        self.conn.commit()

    # --- Plex state cache ---

    def lookup_state(self, anilist_id: int, season_number: int) -> Optional[Tuple[int, Optional[int]]]:
        if not self.enabled:
            return None
        row = self.conn.execute(
            "SELECT plex_episodes, plex_rating FROM plex_state_cache "
            "WHERE anilist_id = ? AND season_number = ?",
            (anilist_id, season_number),
        ).fetchone()
        if not row:
            return None
        return (row["plex_episodes"], row["plex_rating"])

    def record_state(self, anilist_id: int, season_number: int, plex_episodes: int, plex_rating: Optional[int]):
        if not self.enabled:
            return
        self.conn.execute(
            "INSERT INTO plex_state_cache(anilist_id, season_number, plex_episodes, plex_rating, synced_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(anilist_id, season_number) DO UPDATE SET "
            "plex_episodes = excluded.plex_episodes, plex_rating = excluded.plex_rating, synced_at = excluded.synced_at",
            (anilist_id, season_number, plex_episodes, plex_rating, int(time.time())),
        )
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    # --- Row mapping ---

    @staticmethod
    def _row_to_series(row: sqlite3.Row) -> AnilistSeries:
        synonyms = []
        if row["synonyms_json"]:
            try:
                synonyms = json.loads(row["synonyms_json"]) or []
            except json.JSONDecodeError:
                synonyms = []
        return AnilistSeries(
            anilist_id=row["anilist_id"],
            series_type=row["series_type"] or "",
            series_format=row["series_format"] or "",
            source=row["source"] or "",
            status=row["status"] or "",
            media_status=row["media_status"] or "",
            progress=row["progress"] or 0,
            season=row["season"] or "",
            episodes=row["episodes"] or 0,
            title_english=row["title_english"] or "",
            title_romaji=row["title_romaji"] or "",
            title_native=row["title_native"] or "",
            synonyms=synonyms,
            started_year=row["started_year"] or 0,
            ended_year=row["ended_year"] or 0,
            score=row["score"] or 0,
        )

    @staticmethod
    def _series_to_row(series: AnilistSeries, refreshed_at: int) -> tuple:
        synonyms_json = json.dumps(series.synonyms) if series.synonyms else None
        return (
            series.anilist_id,
            series.progress or 0,
            series.status or "",
            series.score,
            series.episodes,
            series.media_status,
            series.ended_year,
            series.title_romaji,
            series.title_english,
            series.title_native,
            synonyms_json,
            series.series_type,
            series.series_format,
            series.source,
            series.season,
            series.started_year,
            refreshed_at,
        )
