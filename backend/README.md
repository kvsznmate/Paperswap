# Paperswap Backend

FastAPI service that ingests news across seven topics, deduplicates it into PostgreSQL, and serves a topic-balanced swipe deck over REST.

Full project documentation — architecture, API reference, configuration — lives in the [root README](../README.md). This file covers backend development specifically.

---

## Run it

```bash
cp .env.example .env      # fill in the required values first
docker compose up --build
```

Compose fails to start if `NEWS_API_KEY`, `POSTGRES_USER`, or `POSTGRES_PASSWORD` are missing. That is deliberate — a missing secret should fail loudly rather than boot on a default.

Without Docker, you need a PostgreSQL instance to point at:

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:password@localhost:5432/newsdb
python main.py            # server on :8000
python main.py --cli      # sync news once, print a per-topic summary, exit
```

---

## Modules

| File | Responsibility |
| --- | --- |
| `main.py` | FastAPI app, routes, APScheduler lifespan, CLI runner |
| `database.py` | Schema, topic catalogue (`CATEGORIES`), queries, dedup, purge |
| `news_fetcher.py` | NewsAPI + RSS ingestion, keyword classification, summary generation |
| `backfill_categories.py` | One-off reclassification of stored rows. **Dry-run by default** — pass `--apply` to write. |

`database.CATEGORIES` is the single source of truth for topics. Add an entry there plus a feed in `news_fetcher.TOPIC_FEEDS`, and `init_db()` syncs it into the `categories` table on the next boot. The Android client reads labels and colours from the API, so new topics need no client release.

---

## Schema

| Table | Holds | Retention |
| --- | --- | --- |
| `articles` | Headline, summary, source, topic, image URL, link, `article_key` (unique) | `PURGE_OLDER_THAN_DAYS` (7) on `created_at` |
| `categories` | Topic catalogue synced from `database.CATEGORIES` on boot | permanent |
| `user_swipes` | One row per swipe — `read` or `pass` | cascades when its article is purged |
| `user_sessions` | Session heartbeats for engagement metrics | `SESSION_RETENTION_DAYS` (7) on `last_heartbeat` |
| `request_logs` | One row per HTTP request, with its real status code | `REQUEST_LOG_RETENTION_DAYS` (7) on `logged_at` |

Three details worth knowing before editing queries:

**Deduplication is atomic.** `article_key` is `md5(title + url)` with a unique constraint, and inserts use `ON CONFLICT DO NOTHING`. Don't add a check-then-insert around it — that reintroduces a race and doubles the connection count. `save_article` returns `(id, was_inserted)`; take the count from that flag, never from a prior read.

**The retention windows are deliberately equal.** The hourly-usage and top-endpoint panels are a UNION of `request_logs` and `user_swipes`, and `total_articles` / `total_swipes` / `total_sessions` render side by side. `user_swipes` is already capped at the article window by `ON DELETE CASCADE`, so the other two default to that same window. Raise one in isolation and a panel starts mixing time periods, which reads as a trend rather than a config choice.

**Sessions are purged on `last_heartbeat`, not `created_at`.** `record_session_heartbeat` recomputes `duration_seconds` as `NOW() - created_at` on every beat, so deleting a row that is still beating would not end the session — the next beat re-inserts it and its duration restarts at zero.

**The balanced feed uses a window function.** `ROW_NUMBER() OVER (PARTITION BY category ORDER BY id DESC)` ranks articles within each topic, then the outer query orders by rank to interleave topics. Any pagination must apply `OFFSET` to the outer query, not the CTE, or the balance breaks across page boundaries.

---

## Operational notes

Things that have caused real outages here:

**`POSTGRES_PASSWORD` only applies at initdb time.** Once the `pgdata` volume exists, changing that variable does nothing. To rotate, run `ALTER USER ... WITH PASSWORD` inside Postgres first, then update `.env`, then recreate.

**After changing `.env`, use `docker compose down && up`.** `--force-recreate` can carry stale environment forward.

**Generate passwords with `openssl rand -hex 24`, not base64.** The password is embedded in `DATABASE_URL`, and a `+` or `/` breaks URL parsing in a way that presents as an authentication failure.

**Startup is fragile.** `init_db()` connects eagerly in `lifespan` with no retry, so an unreachable database exits the process rather than degrading. If the container is in a restart loop, check `docker compose logs` before anything else.

**`templates/analytics.html` is a bundle, not editable HTML.** The served file is a self-extracting single-file capture: a bootstrap script plus the real dashboard stored JSON-escaped inside `<script type="__bundler/template">` on one 61 KB line. Editing a label means editing that escaped string, or re-exporting from whatever produced it. It also means every `/analytics` load ships ~234 KB, most of it inlined fonts, re-read from disk per request.

---

## Tests

Seven standalone suites under `tests/` — see [tests/README.md](tests/README.md). They need a live Postgres and write to whatever `DATABASE_URL` points at.

```bash
docker compose exec news-cards-backend sh -c 'for t in tests/test_*.py; do echo "== $t"; python "$t" || exit 1; done'
```

---

## Known issues

- No CI; the suites above are run by hand
- Rate limits are keyed on the client IP with in-memory storage. Behind a reverse proxy every request appears to come from the proxy, collapsing all clients into one bucket — putting Caddy in front requires `X-Forwarded-For` handling first
- `ADMIN_COOKIE_SECURE` is `false` by default and must be flipped once HTTPS terminates in front
- The `/api/v1/feed` cold-start path fetches synchronously inside the request, so the first call against an empty database blocks for the duration of ~84 outbound HTTP calls
- `duration_seconds` is recomputed from `created_at` on every heartbeat, so a client that reuses one `session_id` across days reports a session lasting days
