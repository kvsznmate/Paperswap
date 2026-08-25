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
| `backfill_categories.py` | One-off reclassification of stored rows. **Dry-run by default** — pass `--commit` to write. |

`database.CATEGORIES` is the single source of truth for topics. Add an entry there plus a feed in `news_fetcher.TOPIC_FEEDS`, and `init_db()` syncs it into the `categories` table on the next boot. The Android client reads labels and colours from the API, so new topics need no client release.

---

## Schema

| Table | Holds |
| --- | --- |
| `articles` | Headline, summary, source, topic, image URL, link, `article_key` (unique) |
| `categories` | Topic catalogue synced from `database.CATEGORIES` on boot |
| `user_swipes` | One row per swipe — `read` or `pass`, cascades on article delete |
| `user_sessions` | Session heartbeats for engagement metrics |
| `request_logs` | One row per HTTP request, for peak-hour analysis |

Two details worth knowing before editing queries:

**Deduplication is atomic.** `article_key` is `md5(title + url)` with a unique constraint, and inserts use `ON CONFLICT DO NOTHING`. Don't add a check-then-insert around it — that reintroduces a race and doubles the connection count.

**The balanced feed uses a window function.** `ROW_NUMBER() OVER (PARTITION BY category ORDER BY id DESC)` ranks articles within each topic, then the outer query orders by rank to interleave topics. Any pagination must apply `OFFSET` to the outer query, not the CTE, or the balance breaks across page boundaries.

---

## Operational notes

Things that have caused real outages here:

**`POSTGRES_PASSWORD` only applies at initdb time.** Once the `pgdata` volume exists, changing that variable does nothing. To rotate, run `ALTER USER ... WITH PASSWORD` inside Postgres first, then update `.env`, then recreate.

**After changing `.env`, use `docker compose down && up`.** `--force-recreate` can carry stale environment forward.

**Generate passwords with `openssl rand -hex 24`, not base64.** The password is embedded in `DATABASE_URL`, and a `+` or `/` breaks URL parsing in a way that presents as an authentication failure.

**Startup is fragile.** `init_db()` connects eagerly in `lifespan` with no retry, so an unreachable database exits the process rather than degrading. If the container is in a restart loop, check `docker compose logs` before anything else.

---

## Known issues

- No tests, no CI
- No connection pooling; error paths leak connections
- Request logging performs a blocking database write inside async middleware, and records a hardcoded `200` before the response exists
- `request_logs` and `user_sessions` grow without a retention policy
- `/api/v1/cards/refresh` is an unauthenticated `GET` that mutates state
