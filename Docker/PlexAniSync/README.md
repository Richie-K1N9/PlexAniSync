# Docker-PlexAniSync

## Usage

### Docker Run

```
docker run -d \
  --name=plexanisync \
  --restart unless-stopped \
  -e PLEX_SECTION="Anime|Anime Movies" \
  -e PLEX_URL=http://127.0.0.1:32400 \
  -e PLEX_TOKEN=SomePlexToken \
  -e ANI_USERNAME=SomeUser \
  -e ANI_TOKEN=SomeToken \
  -e INTERVAL=3600 \
  -v /etc/localtime:/etc/localtime:ro \
  -v /path/to/your/custom_mappings.yaml:/plexanisync/custom_mappings.yaml \
  ghcr.io/richie-k1n9/plexanisync:latest
```

### Docker Compose

```yaml
version: '3.7'
services:
  plexanisync:
    container_name: plexanisync
    image: 'ghcr.io/richie-k1n9/plexanisync:latest'
    restart: unless-stopped
    environment:
      - PLEX_SECTION=Anime|Anime Movies
      - 'PLEX_URL=http://127.0.0.1:32400'
      - PLEX_TOKEN=SomePlexToken
      - ANI_USERNAME=SomeUser
      - ANI_TOKEN=SomeToken
      - INTERVAL=3600
    volumes:
      - '/etc/localtime:/etc/localtime:ro'
      - '/path/to/your/custom_mappings.yaml:/plexanisync/custom_mappings.yaml'
```

### Environment Variables

| ID                          | Default                | Required  | Note                                                                                                                                                     |
| --------------------------- | ---------------------- | :-------: | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| PLEX_SECTION                | Anime                  | &#10003;* | The library where your anime resides.<br /><br />You can specify multiple values by seperating the library names with &#124; .                           | 
| PLEX_URL                    | http://127.0.0.1:32400 | &#10003;* | The address to your Plex Media Server, for example: http://127.0.0.1:32400                                                                               |
| PLEX_TOKEN                  | -                      | &#10003;* | Follow [this guide](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)                                                                        |
| ANI_USERNAME                | -                      | &#10003;* | Your [AniList.co](http://www.anilist.co) username                                                                                                                                    |
| ANI_TOKEN                   | -                      | &#10003;* | Get it [here](https://anilist.co/api/v2/oauth/authorize?client_id=1549&response_type=token)                                                                                          |
| PLEX_EPISODE_COUNT_PRIORITY | -                      | &#10005;  | If set to True, Plex episode watched count will take priority over AniList (default = False)                                                                                         |
| SYNC_RATINGS                | -                      | &#10005;  | If set to True, Plex ratings will be used for Anilist scores. Make sure to read the [extended description](https://github.com/Richie-K1N9/PlexAniSync#use-plex-ratings-for-anilist-scores)|
| SKIP_LIST_UPDATE            | -                      | &#10005;  | If set to True, it will NOT update your AniList which is useful if you want to do a test run to check if everything lines up properly. (default = False)                             |
| LOG_FAILED_MATCHES          | -                      | &#10005;  | If set to True, failed matches will be written to /plexanisync/failed_matches.txt (default = False)                                                                                  |
| SETTINGS_FILE               | -                      | &#10005;  | Location of a custom settings.ini for more advanced configuration. Makes all settings above obsolete. See section below for usage.                                                   |
| INTERVAL                    | 3600                   | &#10005;  | The time in between syncs in seconds. If this value is set to <= 0, the container will stop after the first sync.                                                                    |

### New environment variables (cache, webhook, retry)

These were added alongside the local-cache and webhook features. All are **optional** — leaving them unset uses the defaults shown in the table and your existing container will keep working unchanged.

| ID | Default | Note |
| --- | --- | --- |
| `CACHE_DB_PATH` | `plexanisync_cache.db` | SQLite file. Set to empty string to disable persistence. |
| `ANILIST_CACHE_TTL_HOURS` | `24` | How often to do a full AniList list refresh. Between refreshes only active lists are fetched. |
| `CACHE_MAX_AGE_DAYS` | `14` | Hard expiry — the entire cache is wiped after this many days. |
| `CACHE_SESSION_MARKER_PATH` | `/tmp/plexanisync_session` | Ephemeral marker file used to detect container restarts. Must live in tmpfs. |
| `BATCH_EPISODE_UPDATES` | `False` | When `True`, collapse per-episode incremental updates into one mutation. Trades activity-feed granularity for fewer API calls. |
| `MAX_RATE_LIMIT_RETRIES` | `8` | Maximum 429 retries per single request before that show is skipped. |
| `MAX_RATE_LIMIT_WAIT_SECONDS` | `300` | Cap on AniList's `retry-after` — refuses to sleep longer than this. |
| `MAX_TRANSIENT_ERROR_RETRIES` | `10` | Retries for non-429 transient errors. Exponential backoff, capped at 60s. |
| `WEBHOOK_URL` | empty | Optional URL POSTed once per sync with year-mismatch, title-not-found, auth-failure, and per-show error events. Rate-limit messages are **not** sent. |
| `WEBHOOK_FORMAT` | `discord` | `discord` sends `{"content": ...}`, `slack` sends `{"text": ...}`. |
| `WEBHOOK_MIN_SEVERITY` | `error` | Set to `warning` to also include year-mismatch warnings. |

### Migrating an existing docker-compose.yml

If you have a working PlexAniSync compose file from before these changes, the only change you **need** is none — the container still runs unmodified. To opt into the new features:

```diff
 version: '3.7'
 services:
   plexanisync:
     container_name: plexanisync
     image: 'ghcr.io/richie-k1n9/plexanisync:latest'
     restart: unless-stopped
     environment:
       - PLEX_SECTION=Anime|Anime Movies
       - 'PLEX_URL=http://127.0.0.1:32400'
       - PLEX_TOKEN=SomePlexToken
       - ANI_USERNAME=SomeUser
       - ANI_TOKEN=SomeToken
       - INTERVAL=3600
+      # --- Optional: webhook for actionable errors ---
+      - 'WEBHOOK_URL=https://discord.com/api/webhooks/.../...'
+      # WEBHOOK_FORMAT=discord (default) or slack
+      # WEBHOOK_MIN_SEVERITY=error (default) or warning
+      # --- Optional: cache tuning ---
+      # CACHE_MAX_AGE_DAYS=14
+      # ANILIST_CACHE_TTL_HOURS=24
+      # BATCH_EPISODE_UPDATES=True  # cuts mutations dramatically during catch-up syncs
     volumes:
       - '/etc/localtime:/etc/localtime:ro'
       - '/path/to/your/custom_mappings.yaml:/plexanisync/custom_mappings.yaml'
+      # --- Optional: persist the cache file between cron iterations within a container ---
+      # The cache is wiped automatically on container restart (via /tmp marker) and after
+      # CACHE_MAX_AGE_DAYS regardless. Mounting it is only useful if your INTERVAL is short
+      # and you want the cache to also survive `docker compose pull && up -d` (it won't — the
+      # restart wipe still fires; mount mostly helps if you set the marker path elsewhere).
+      # - '/path/on/host/plexanisync_cache.db:/plexanisync/plexanisync_cache.db'
```

**Step-by-step:**

1. **Pull the new image**: `docker compose pull plexanisync`
2. **(Optional) Add the webhook URL** to the `environment:` list — create a Discord/Slack incoming webhook first if you don't have one.
3. **(Optional) Add cache tuning vars** if you want non-default behaviour. Defaults are sensible for most users.
4. **Recreate the container**: `docker compose up -d plexanisync`. The first sync after upgrade will be identical to the old behaviour (cache empty → full fetch). Subsequent syncs benefit from the cache.
5. **Verify**: tail the logs with `docker compose logs -f plexanisync`. You should see `[CACHE]` lines on startup and `Using cached AniList list; refreshing active entries only` on second-and-later syncs.

**Nothing to migrate:**
- Your `settings.ini` (if you use `SETTINGS_FILE`) will get default values for the new keys automatically. To customize, add them under `[ANILIST]` and a new `[NOTIFICATIONS]` section — see `settings.ini.example` in the repo root.
- Custom mappings, Plex/AniList credentials, the `INTERVAL` knob — all unchanged.

**Reset the cache manually:**
```
docker compose exec plexanisync rm /plexanisync/plexanisync_cache.db
docker compose restart plexanisync
```

### Custom mappings

In order to provide a [custom_mappings.yaml file](https://github.com/Richie-K1N9/PlexAniSync#custom-anime-mapping), mount the file on your host to `/plexanisync/custom_mappings.yaml` like this:

```
-v /path/to/your/custom_mappings.yaml:/plexanisync/custom_mappings.yaml
```

You can modify the file on the host system anytime and it will be used during the next run. Restarting the container is not necessary.

### Custom settings.ini

If you want to use other Plex login mechanisms, you can use your own settings.ini file by mapping it into the container and setting the environment variable `SETTINGS_FILE` with the path to the file inside the container.

If the settings file is located at `/docker/plexanisync/settings.ini` and you want to place it to `/config/settings.ini`, use the following volume mapping and environment variable:

```
-v '/docker/plexanisync/settings.ini:/config/settings.ini:ro'
-e 'SETTINGS_FILE=/config/settings.ini'
```
