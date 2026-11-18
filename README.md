# Paperswap

[![CI](https://github.com/kvsznmate/Paperswap/actions/workflows/ci.yml/badge.svg?branch=dev)](https://github.com/kvsznmate/Paperswap/actions/workflows/ci.yml)

**Swipe your news.**

Paperswap borrows the Tinder interaction model for reading headlines. Instead of scrolling an endless list, you get one card at a time:

- 👉 **Swipe right** — Interested. Opens the article on the publisher's site.
- 👈 **Swipe left** — Pass. The next headline appears.
- **Tap** — Flip the card to read extracted summary bullets.

The repository contains a **Dockerized FastAPI + PostgreSQL backend** that ingests news across seven topics, deduplicates it, serves a topic-balanced deck over REST, records swipe feedback and writes a weekly per-topic digest. It also contains a **native Android client** (Kotlin/Compose) and a **touch-enabled web preview** served by the backend.

> **TODO:** add a screenshot or GIF of the swipe UI here.

---

## Architecture

```mermaid
graph TD
    A["📱 Android client (Kotlin + Compose)"] -->|"GET /api/v1/feed, /summaries"| B["🐳 FastAPI + Uvicorn"]
    A -->|"POST /api/v1/swipe"| B
    A -->|"Swipe right"| C["🌐 Publisher webpage"]
    S["⏰ APScheduler — every 12 h"] -->|"refresh_pipeline: fetch → summarise → purge"| B
    B --> D["⚡ news_fetcher.py — NewsAPI + publisher RSS"]
    B --> E["🐘 PostgreSQL 16"]
    F["🧠 enrichment.py — cron, every 6 h, separate container"] -->|"full_text, bullets, embeddings"| E
```

The backend renders nothing. It stores article *fields* (headline, summary, source, image URL, link) and the client draws the card. See [ADR-001](docs/ARCHITECTURE.md).

### How a card reaches the screen

1. **Fetch.** `news_fetcher.py` pulls `ARTICLES_PER_CATEGORY` (12) items per topic, giving 84 candidates per refresh. It tries NewsAPI first and falls back to the publisher RSS feeds in `TOPIC_FEEDS`. **In production RSS is the only source that actually returns anything:** the free NewsAPI plan rejects requests from servers.
2. **Classify.** A weighted keyword scorer reassigns an article away from its feed's topic only when another topic wins by a clear margin. Its accuracy is unmeasured ([ADR-007](docs/ARCHITECTURE.md)).
3. **Deduplicate.** `article_key = md5(title + url)` is a unique column, and inserts use `ON CONFLICT DO NOTHING`, so dedup is atomic ([ADR-004](docs/ARCHITECTURE.md)).
4. **Summarise and purge.** The weekly summariser runs inside the refresh, *before* the purge deletes its input. Articles are then purged after 9 days ([ADR-011](docs/ARCHITECTURE.md)).
5. **Enrich.** A separate one-shot job extracts article bodies, ranks summary bullets and computes embeddings ([ADR-012](docs/ARCHITECTURE.md)).
6. **Serve.** `/api/v1/feed` interleaves topics round-robin with a window function ([ADR-005](docs/ARCHITECTURE.md)). Each article carries its own `category_label` and `accent_color`, so adding a topic needs no client release ([ADR-006](docs/ARCHITECTURE.md)).
7. **Record.** Swipes go to `user_swipes` (purged with the article, for analytics) and `swipe_events` (kept forever, the training set).

### Tech stack

- **Backend:** Python 3.12 · FastAPI · psycopg2 (pooled) · APScheduler · slowapi · feedparser · PostgreSQL 16
- **Enrichment:** trafilatura · onnxruntime (int8 all-MiniLM-L6-v2) · numpy TextRank
- **Android:** Kotlin · Jetpack Compose · Retrofit · Coil · Custom Tabs
- **Infrastructure:** Docker Compose on Oracle Cloud Always Free (E2.1.Micro, 956 MB RAM + 2 GB swap)

---

## Repository layout

```
Paperswap/
├── README.md                 # This file
├── CLAUDE.md                 # Working notes for coding agents: invariants and conventions
├── docs/
│   ├── ARCHITECTURE.md       # Decision records (ADR-001..012) and failure modes
│   ├── DATABASE_GUIDE.md     # Tables, retention, how to inspect and back up Postgres
│   ├── RATE_LIMITS.md        # Every rate limit and why it is set there
│   └── DEPLOYMENT.md         # Hosting option comparison
├── backend/                  # FastAPI service — see backend/README.md
│   ├── main.py               # App, routes, auth, request-log buffer, scheduler, CLI
│   ├── database.py           # Pool, schema/migrations, queries, purges, telemetry
│   ├── news_fetcher.py       # Ingestion, classification, card blurbs
│   ├── summarizer.py         # Weekly per-topic digests
│   ├── enrichment.py         # One-shot body extraction / bullets / embeddings
│   ├── check_feeds.py        # RSS feed health check
│   ├── run_enrichment.sh     # Cron wrapper for enrichment.py
│   ├── templates/            # mobile_preview.html, dashboard.html, analytics.html
│   ├── tests/                # Standalone suites — see tests/README.md
│   └── Dockerfile, docker-compose.yml, .env.example
├── android/                  # Native client — see android/README.md
└── tools/label_articles.py   # Hand-labelling tool for a classifier holdout set
```

---

## Getting started

### Docker Compose (recommended)

```bash
cd backend
cp .env.example .env      # fill in the required values first
docker compose up --build
```

Compose refuses to start without `NEWS_API_KEY`, `ADMIN_API_KEY`, `POSTGRES_USER` and `POSTGRES_PASSWORD`. This is deliberate: a missing secret should fail loudly rather than boot on a default ([ADR-008](docs/ARCHITECTURE.md)). Generate secrets with `openssl rand -hex 24` and never base64 ([ADR-009](docs/ARCHITECTURE.md)).

### Local Python

You need a PostgreSQL instance to point at. `backend/.env` is loaded automatically.

```bash
cd backend
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:password@localhost:5432/newsdb
python main.py            # server on :8000
python main.py --cli      # sync once, print a per-topic summary, exit
```

### Then open

| URL | What you get |
| --- | --- |
| `/mobile` | Touch swipe UI in the Paper design: the deck, the week and Saved. Open it on a phone. `/summaries` deep-links to the week |
| `/` | Card gallery dashboard |
| `/analytics` | Telemetry dashboard. It is gated: sign in with `ADMIN_API_KEY` |
| `/docs` | OpenAPI. Protected routes show a padlock. Off when `API_DOCS_ENABLED=false` |

Configuration is documented in [`backend/.env.example`](backend/.env.example) and [`backend/README.md`](backend/README.md#environment).

---

## API

| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| `GET` | `/api/v1/feed` | public | Query params: `categories` (comma-separated), `limit` (1–300, default 70), `balanced` (default `true`) |
| `GET` | `/api/v1/categories` | public | Topic catalogue with label, accent colour and live article count |
| `GET` | `/api/v1/summaries` | public | One week's digests. Pass `week_start` to pick a week; topics without enough coverage are listed in `topics_missing` |
| `GET` | `/api/v1/summaries/{topic}` | public | One topic's history, newest week first (`weeks`, default 8) |
| `POST` | `/api/v1/swipe` | public | Body: `{"article_id": int, "action": "read"\|"pass", "dwell_ms"?: int, "flipped"?: bool}` |
| `POST` | `/api/v1/telemetry/heartbeat` | public | Session keepalive |
| `POST` | `/api/v1/cards/refresh` | `X-API-Key` | Runs the refresh pipeline in the background and returns `202` |
| `POST` | `/api/v1/summaries/generate` | `X-API-Key` | Regenerates a week's summaries (`week_start`, `force`) and returns `202` |
| `GET` | `/api/v1/telemetry/stats`, `/logs` | `X-API-Key` or session cookie | Every numeric field declares whether it was `measured` ([ADR-010](docs/ARCHITECTURE.md)) |
| `POST` | `/api/v1/auth/session`, `/logout` | — | Swaps the admin key for a browser session cookie, or ends that session |

Admin auth fails **closed**: with `ADMIN_API_KEY` unset, the admin endpoints return `503`. State-changing routes accept only the header, never the cookie, so they cannot be forged cross-site. Rate limits are listed in [docs/RATE_LIMITS.md](docs/RATE_LIMITS.md).

---

## Deployment

The app runs on an Oracle Cloud E2.1.Micro under Docker Compose ([ADR-003](docs/ARCHITECTURE.md); for other hosts see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)). Deploying is manual: `git pull` on the VM, then `docker compose down && docker compose up -d`. Use `down`/`up` and not `--force-recreate`, because the latter can carry stale environment forward.

---

## Known gaps

**Operations**
- CI (`.github/workflows/ci.yml`) lints, runs every backend suite against Postgres 16, boots the Compose stack and builds both APKs on each PR. It does not deploy, and there are no Android tests, only the compile. Signed APKs are built and published from the signing machine with `android/release.sh` (`android/README.md`, Releasing).
- The server is plain HTTP only. HTTPS through Caddy is planned, and it needs `X-Forwarded-For` handling for the rate limiter plus `ADMIN_COOKIE_SECURE=true`.
- Startup has no database retry, so an unreachable Postgres crash-loops the container. There is no `/health` endpoint.
- `swipe_events`, the one irreplaceable table, has no scheduled backup.

**API**
- `/api/v1/feed` has no `offset`, so the deck ends when the client exhausts it.
- On a cold start, `/api/v1/feed` fetches news synchronously inside the request (see RATE_LIMITS.md).

**Android**
- No offline support, no topic filter on the deck, and no automated tests. See [android/README.md](android/README.md#known-issues).

**Data science**
- The topic classifier, the summary bullets and the weekly digests have never been evaluated against labelled data or a baseline.
- `swipe_events` collects implicit feedback that nothing consumes yet. The feed is identical for every user.

---

## Contributing

Development happens on feature branches merged to `main` via pull request. To request access, email `mate.kovasznai@gmail.com`. Bug reports and feature suggestions are welcome via Issues.
