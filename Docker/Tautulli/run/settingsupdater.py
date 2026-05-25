import configparser
import os

config = configparser.ConfigParser()

config['PLEX'] = {
    'anime_section': os.environ.get('PLEX_SECTION'),
    'authentication_method': 'direct',
    'base_url': os.environ.get('PLEX_URL'),
    'token': os.environ.get('PLEX_TOKEN'),
}

config['ANILIST'] = {
    'username': os.environ.get('ANI_USERNAME'),
    'access_token': os.environ.get('ANI_TOKEN'),
    'plex_episode_count_priority': os.environ.get('PLEX_EPISODE_COUNT_PRIORITY') or False,
    'skip_list_update': os.environ.get('SKIP_LIST_UPDATE') or False,
    'log_failed_matches': os.environ.get('LOG_FAILED_MATCHES') or False,
    'sync_ratings': os.environ.get('SYNC_RATINGS') or False,
    'cache_db_path': os.environ.get('CACHE_DB_PATH') or 'plexanisync_cache.db',
    'anilist_cache_full_refresh_hours': os.environ.get('ANILIST_CACHE_TTL_HOURS') or '24',
    'cache_max_age_days': os.environ.get('CACHE_MAX_AGE_DAYS') or '14',
    'cache_session_marker_path': os.environ.get('CACHE_SESSION_MARKER_PATH') or '/tmp/plexanisync_session',
    'batch_episode_updates': os.environ.get('BATCH_EPISODE_UPDATES') or False,
    'max_rate_limit_retries': os.environ.get('MAX_RATE_LIMIT_RETRIES') or '8',
    'max_rate_limit_wait_seconds': os.environ.get('MAX_RATE_LIMIT_WAIT_SECONDS') or '300',
    'max_transient_error_retries': os.environ.get('MAX_TRANSIENT_ERROR_RETRIES') or '10',
}

config['NOTIFICATIONS'] = {
    'webhook_url': os.environ.get('WEBHOOK_URL') or '',
    'webhook_format': os.environ.get('WEBHOOK_FORMAT') or 'discord',
    'webhook_min_severity': os.environ.get('WEBHOOK_MIN_SEVERITY') or 'error',
}

with open('/plexanisync/settings.ini', 'w', encoding="UTF-8") as configfile:
    config.write(configfile)
