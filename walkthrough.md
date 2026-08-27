# Paperswap — Walkthrough

A swipe-based news reader. Cards arrive one at a time; swipe right to open the
article, left to skip. Seven topics, refreshed on a schedule, deduplicated into
PostgreSQL and served to an Android client over REST.

The backend renders nothing. It stores article *fields* — headline, summary,
source, image URL, link — and the client draws the card. There is no
server-side image pipeline.

---

## Layout

| Path | What it is |
| --- | --- |
| `backend/main.py` | FastAPI app: routes, auth, request-log middleware, APScheduler lifespan, CLI runner |
| `backend/database.py` | Connection pool, schema, topic catalogue, queries, dedup, retention purges, telemetry |
| `backend/news_fetcher.py` | NewsAPI + Google News RSS ingestion, keyword classification, summary generation |
| `backend/backfill_categories.py` | One-off reclassification of stored rows (dry-run unless `--apply`) |
| `backend/templates/` | `dashboard.html`, `mobile_preview.html`, `analytics.html` |
| `backend/tests/` | Seven standalone suites — see [tests/README.md](backend/tests/README.md) |
| `android/` | Android client |
| `backend/Dockerfile`, `backend/docker-compose.yml` | Container suite: app + `postgres:16-alpine` with a durable `pgdata` volume |

Backend development specifics — module responsibilities, schema notes,
operational gotchas — live in [backend/README.md](backend/README.md).

---

## Run it

```bash
cd backend
cp .env.example .env      # fill in the required values first
docker compose up --build
```

Compose refuses to start without `NEWS_API_KEY`, `POSTGRES_USER`,
`POSTGRES_PASSWORD` and `ADMIN_API_KEY`. That is deliberate: a missing secret
should fail loudly rather than boot on a default.

Without Docker you need a PostgreSQL instance to point at:

```bash
export DATABASE_URL=postgresql://user:password@localhost:5432/newsdb
python main.py            # server on :8000
python main.py --cli      # sync once, print a per-topic summary, exit
```

| URL | What |
| --- | --- |
| `/mobile` | Touch swipe preview |
| `/` | Gallery dashboard |
| `/analytics` | Telemetry dashboard — gated, sign in with `ADMIN_API_KEY` |
| `/docs` | OpenAPI, with a padlock on the protected routes |

---

## Topics

`database.CATEGORIES` is the single source of truth: **Tech, Finance, Sports,
Politics, Programming, Science, Beauty**. Add an entry there plus a feed in
`news_fetcher.TOPIC_FEEDS` and `init_db()` syncs it into the `categories` table
on the next boot. The client reads labels and accent colours from the API, so a
new topic needs no client release.

Each refresh pulls `ARTICLES_PER_CATEGORY` (12) per topic — 84 candidates.
`GET /api/v1/feed` returns 70 by default, interleaved round-robin so the deck
never opens with twelve Sports cards in a row.

Articles are **reclassified after fetch**, from their own title and summary
rather than from whichever query returned them. That is what stops a skincare
story that mentions "market" being filed under Finance. The classifier only
overrides the query's label when another topic wins by a clear margin.

---

## Deduplication

`article_key` is `md5(title + url)`, with a unique constraint on the column.

Inserts go through `ON CONFLICT (article_key) DO NOTHING ... RETURNING id`, and
`save_article` returns `(id, was_inserted)` taken from whether that `RETURNING`
produced a row. Postgres decides new-versus-duplicate inside the insert's own
transaction, so the count is correct even though three code paths reach it
concurrently: the scheduler's refresh job, the cold-start fetch inside
`GET /api/v1/feed`, and the background task behind `POST /api/v1/cards/refresh`.

There is no read-then-write pre-check, and adding one back would be a
regression — it reintroduces a race in which two callers both decide an article
is new. `tests/test_dedup.py` releases twelve threads onto one key and asserts
exactly one of them is told it inserted.

---

## Retention

Everything is bounded, and on the same window by default.

| Table | Purged on | Setting |
| --- | --- | --- |
| `articles` | `created_at` | `PURGE_OLDER_THAN_DAYS` (7) |
| `user_swipes` | — | cascades when its article is purged |
| `request_logs` | `logged_at` | `REQUEST_LOG_RETENTION_DAYS` (defaults to the above) |
| `user_sessions` | `last_heartbeat` | `SESSION_RETENTION_DAYS` (defaults to the above) |

The equality is the point, not tidiness. The hourly-usage and top-endpoint
panels are a UNION of `request_logs` and `user_swipes`, and the article, swipe
and session counters render side by side. `user_swipes` is already capped by
the article cascade, so if either of the others outlived it, one panel would be
mixing time periods — and that reads as a trend rather than a config choice.

Sessions purge on `last_heartbeat` so a live session is never removed
mid-flight.

---

## Telemetry

`/analytics` reports storage, memory, engagement and quota figures. Every
numeric field carries a `measured` flag: either it was read from `/proc`, the
filesystem or Postgres at call time, or it is `null` with an
`unavailable_reason`. Nothing is estimated, sampled or hardcoded to a
plausible-looking constant.

Published Oracle Always Free allowances are the one exception — a documented
quota is a fact, not a measurement — and they carry `is_limit` and a
verification date so they are never mistaken for observed usage.

Request logging never touches the database on the request path. The middleware
appends to an in-memory buffer after the response exists, so the recorded
status code is the real one, and a background job flushes the buffer to
Postgres in batches every ten seconds plus once on shutdown.

`tests/test_telemetry_provenance.py` guards this by changing the world and
asserting the number moves: write 5 MB into a measured directory, confirm the
reported size shifts by 5 MB, delete it, confirm it returns. A constant cannot
respond to that.

---

## API

| Method | Path | Auth |
| --- | --- | --- |
| `GET` | `/api/v1/feed` | public |
| `GET` | `/api/v1/categories` | public |
| `POST` | `/api/v1/swipe` | public |
| `POST` | `/api/v1/telemetry/heartbeat` | public |
| `POST` | `/api/v1/cards/refresh` | `X-API-Key` |
| `GET` | `/api/v1/telemetry/stats` | `X-API-Key` or session cookie |
| `GET` | `/api/v1/telemetry/logs` | `X-API-Key` or session cookie |
| `POST` | `/api/v1/auth/session` | exchanges the key for a browser session |

Refresh is a `POST`, not a `GET`: it mutates state and fans out to ~84 outbound
requests, and browsers and link prefetchers follow `GET` freely — a single
prefetch used to be enough to start burning the NewsAPI daily quota. It returns
`202` immediately and runs in the background.

Admin auth fails **closed**: with `ADMIN_API_KEY` unset those endpoints return
`503`, never open. State-changing routes accept the header only, not the
session cookie, so a cookie-authenticated POST cannot be forged cross-site.
