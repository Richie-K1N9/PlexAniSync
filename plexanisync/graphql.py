# coding=utf-8
from configparser import SectionProxy
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
import base64
import json
import logging
import requests
import time

from sgqlc.endpoint.requests import RequestsEndpoint
from sgqlc.operation import Operation

from plexanisync.anilist_schema import anilist_schema as schema
from plexanisync.logger_adapter import PrefixLoggerAdapter
from plexanisync.webhook import NOTIFY_CATEGORY, NOTIFY_FLAG, CATEGORY_AUTH

logger = PrefixLoggerAdapter(logging.getLogger("PlexAniSync"), {"prefix": "GRAPHQL"})


class RateLimitExhaustedError(Exception):
    """Raised when 429 retries are exhausted for a single request."""


class TerminalGraphQLError(Exception):
    """Raised when the AniList API returns an error that retrying won't fix
    (bad query, auth failure, etc.)."""


_TERMINAL_STATUSES = {400, 401, 403, 404}


@dataclass
class AnilistSeries:
    anilist_id: int
    series_type: str
    series_format: str
    source: str
    status: str
    media_status: str
    progress: int
    season: str
    episodes: int
    title_english: str
    title_romaji: str
    title_native: str
    synonyms: List[str]
    started_year: int
    ended_year: int
    score: int

    def titles(self) -> List[str]:
        titles = [self.title_english, self.title_romaji, self.title_native]
        if self.synonyms:
            titles += self.synonyms
        # filter out empty values
        return [title for title in titles if title]


class GraphQL:
    def __init__(self, anilist_settings: SectionProxy):
        self.anilist_settings = anilist_settings
        anilist_token = anilist_settings["access_token"].strip()

        self.check_token_expiry(anilist_token)

        self.endpoint = RequestsEndpoint(
            url="https://graphql.anilist.co",
            base_headers={
                "Authorization": f"Bearer {anilist_token}",
                "Accept": "application/json",
                "Content-Type": "application/json"
            },
            timeout=10,  # seconds,
            session=requests.Session()
        )
        # Silence sgqlc's built-in endpoint logger — it dumps every response
        # header on errors (very noisy on 429s). Our __send_graphql_request
        # already produces the only log lines that matter.
        _silent = logging.getLogger("sgqlc.endpoint.requests")
        _silent.addHandler(logging.NullHandler())
        _silent.propagate = False
        self.endpoint.logger = _silent
        self.skip_list_update = self.anilist_settings.getboolean("skip_list_update", False)
        self.sync_ratings = self.anilist_settings.getboolean("sync_ratings", False)
        self.max_rate_limit_retries = self.anilist_settings.getint("max_rate_limit_retries", 8)
        self.max_rate_limit_wait_seconds = self.anilist_settings.getint("max_rate_limit_wait_seconds", 300)
        self.max_transient_error_retries = self.anilist_settings.getint("max_transient_error_retries", 10)

    def check_token_expiry(self, anilist_token):
        # pad the body with == so the base64 pad validation always passes
        base64_body = anilist_token.split(".")[1] + "=="
        anilist_token_body = json.loads(base64.urlsafe_b64decode(base64_body))
        token_expiry = int(anilist_token_body["exp"])
        if datetime.fromtimestamp(token_expiry) < datetime.now():
            # token expired in the past
            logger.error(
                "Anilist token is expired",
                extra={NOTIFY_FLAG: True, NOTIFY_CATEGORY: CATEGORY_AUTH},
            )
            raise RuntimeError("Anilist token is expired")

    def search_by_id(self, anilist_id: int):
        operation = Operation(schema.Query)
        media = operation.media(id=anilist_id, type="ANIME")
        media.__fields__(
            'id',
            'type',
            'format',
            'status',
            'source',
            'season',
            'episodes',
            'synonyms'
        )
        media.title.__fields__('romaji', 'english', 'native')
        media.start_date.year()
        media.end_date.year()

        data = self.__send_graphql_request(operation)

        media = (operation + data).media
        return self.__mediaitem_to_object(media) if media else None

    def search_by_name(self, anilist_show_name: str) -> List[AnilistSeries]:
        operation = Operation(schema.Query)
        media = operation.page(page=1, per_page=50).media(search=anilist_show_name, type="ANIME")
        media.__fields__(
            'id',
            'type',
            'format',
            'status',
            'source',
            'season',
            'episodes',
            'synonyms'
        )
        media.title.__fields__('romaji', 'english', 'native')
        media.start_date.year()
        media.end_date.year()

        data = self.__send_graphql_request(operation)

        media = (operation + data).page.media
        return list(map(self.__mediaitem_to_object, media))

    def fetch_user_list(self) -> List[AnilistSeries]:
        return self.fetch_user_list_filtered(None)

    def fetch_user_list_filtered(self, statuses: Optional[List[str]]) -> List[AnilistSeries]:
        """Fetch the user's AniList collection, optionally filtered to specific list statuses.

        statuses=None fetches every list (legacy behaviour). Passing e.g.
        ["CURRENT", "REPEATING", "PAUSED"] restricts the query to the active lists,
        which the cache layer uses to refresh only entries that can change.
        """
        operation = Operation(schema.Query)
        user_name = self.anilist_settings.get("username")
        kwargs = {"user_name": user_name, "type": "ANIME"}
        if statuses:
            kwargs["status_in"] = list(statuses)
        lists = operation.media_list_collection(**kwargs).lists
        lists.__fields__('name', 'status', 'is_custom_list')
        lists.entries.__fields__('id', 'progress', 'status', 'repeat')
        lists.entries.score(format="POINT_100")
        lists.entries.media.__fields__(
            'id',
            'type',
            'format',
            'status',
            'source',
            'season',
            'episodes',
            'synonyms'
        )
        lists.entries.media.start_date.year()
        lists.entries.media.end_date.year()
        lists.entries.media.title.__fields__('romaji', 'english', 'native')

        data = self.__send_graphql_request(operation)
        list_items = (operation + data).media_list_collection

        anilist_series = []
        for media_collection in list_items.lists:
            if hasattr(media_collection, "entries"):
                for list_entry in media_collection.entries:
                    if (hasattr(list_entry, "status") and list_entry.media):
                        series_obj = self.__mediaitem_to_object(list_entry.media)
                        series_obj.status = list_entry.status
                        series_obj.progress = list_entry.progress
                        series_obj.score = list_entry.score
                        anilist_series.append(series_obj)
        return anilist_series

    def update_series(self, media_id: int, progress: int, status: str, score_raw: int):
        if self.skip_list_update:
            logger.warning("Skip update is enabled in settings so not updating this item")
            return

        op = Operation(schema.Mutation)
        if score_raw and self.sync_ratings:
            op.save_media_list_entry(
                media_id=media_id,
                status=status,
                progress=progress,
                score_raw=score_raw
            )
        else:
            op.save_media_list_entry(
                media_id=media_id,
                status=status,
                progress=progress
            )
        self.__send_graphql_request(op)

    def update_score(self, media_id, score_raw: int):
        if self.skip_list_update:
            logger.warning("Skip update is enabled in settings so not updating this item")
            return

        op = Operation(schema.Mutation)
        op.save_media_list_entry(
            media_id=media_id,
            score_raw=score_raw
        )
        self.__send_graphql_request(op)

    def __send_graphql_request(self, operation):
        rate_limit_attempts = 0
        transient_attempts = 0

        while True:
            data = self.endpoint(operation)
            if "errors" not in data:
                # wait a bit to not overload AniList API
                time.sleep(0.2)
                return data

            error = data["errors"][0]
            status = error.get("status")

            if status == 429:
                requested_wait = int(data.get("headers", {}).get('retry-after', 0) or 0)
                wait_time = min(requested_wait, self.max_rate_limit_wait_seconds)
                # Keep the historical wording so existing log monitors / tests still match.
                logger.warning(f"Rate limit hit, waiting for {wait_time}s")
                time.sleep(wait_time + 1)
                rate_limit_attempts += 1
                if rate_limit_attempts >= self.max_rate_limit_retries:
                    raise RateLimitExhaustedError(
                        f"AniList rate limit retries exhausted after {rate_limit_attempts} attempts"
                    )
                continue

            if status in _TERMINAL_STATUSES:
                message = error.get("message") or str(error)
                category = CATEGORY_AUTH if status in (401, 403) else None
                extra = {NOTIFY_FLAG: True, NOTIFY_CATEGORY: category} if category else None
                logger.error(
                    f"AniList returned terminal error HTTP {status}: {message}",
                    extra=extra or {},
                )
                raise TerminalGraphQLError(f"HTTP {status}: {message}")

            transient_attempts += 1
            if transient_attempts >= self.max_transient_error_retries:
                raise data["exception"]
            backoff = min(60, 2 ** transient_attempts)
            logger.warning(
                f"Transient AniList error (HTTP {status}), retry "
                f"{transient_attempts}/{self.max_transient_error_retries} in {backoff}s"
            )
            time.sleep(backoff)

    def __mediaitem_to_object(self, media_item) -> AnilistSeries:
        anilist_id = media_item.id
        series_type = ""
        series_format = ""
        source = ""
        media_status = ""
        season = ""
        episodes = 0
        title_english = ""
        title_romaji = ""
        title_native = ""
        synonyms = []
        started_year = 0
        ended_year = 0

        if hasattr(media_item, "status"):
            media_status = media_item.status
        if hasattr(media_item, "type"):
            series_type = media_item.type
        if hasattr(media_item, "format"):
            series_format = media_item.format
        if hasattr(media_item, "source"):
            source = media_item.source
        if hasattr(media_item, "season"):
            season = media_item.season
        if hasattr(media_item, "episodes"):
            episodes = media_item.episodes
        if hasattr(media_item.title, "english"):
            title_english = media_item.title.english
        if hasattr(media_item.title, "romaji"):
            title_romaji = media_item.title.romaji
        if hasattr(media_item.title, "native"):
            title_native = media_item.title.native
        if hasattr(media_item, "synonyms"):
            synonyms = media_item.synonyms
        if hasattr(media_item.start_date, "year"):
            started_year = media_item.start_date.year
        if hasattr(media_item.end_date, "year"):
            ended_year = media_item.end_date.year

        series = AnilistSeries(
            anilist_id,
            series_type,
            series_format,
            source,
            "",
            media_status,
            0,
            season,
            episodes,
            title_english,
            title_romaji,
            title_native,
            synonyms,
            started_year,
            ended_year,
            0
        )
        return series
