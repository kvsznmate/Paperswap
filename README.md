# Paperswap

**Swipe your news.**

Paperswap borrows the Tinder interaction model for reading headlines. Instead of scrolling an endless list, you get one card at a time:

- 👉 **Swipe right** — Interested. Opens the article on the publisher's site.
- 👈 **Swipe left** — Pass. The card animates away and the next headline appears.

The repository contains a **Dockerized FastAPI + PostgreSQL backend** that ingests live news across seven topics, deduplicates it, serves a topic-balanced deck over REST, and records swipe feedback — plus a **native Android client** in Kotlin/Compose and a **touch-enabled web preview** you can open on a phone browser without installing anything.

> **TODO:** add a screenshot or GIF of the swipe UI here. This is the single highest-value addition to this file.

---

## What it does

| Capability | How it works |
| --- | --- |
| **Seven topics, balanced** | Tech, Finance, Sports, Politics, Programming, Science, Beauty. The deck interleaves topics round-robin so you never get twelve Sports cards before seeing anything else. |
| **Never shows the same story twice** | Each article gets an MD5 key from its title + URL. Inserts use `ON CONFLICT DO NOTHING`, so deduplication is atomic rather than check-then-write. |
| **Stays fresh on its own** | An APScheduler job refetches every 12 hours and purges articles older than 7 days, cascading their swipe records. |
| **Server-driven topics** | Each article carries its own `category_label` and `accent_color`. Adding a topic server-side changes the app with no release. |
| **Records behaviour** | Every swipe is written to `user_swipes`. Not yet used for ranking — see [Roadmap](#roadmap). |
| **One image runs anywhere** | The same container runs locally and on Oracle Cloud, Render, Railway, Fly.io, or App Runner. |

---

## Architecture

```mermaid
graph TD
    A["📱 Android client (Kotlin + Compose)"] -->|"GET /api/v1/feed"| B["🐳 FastAPI + Uvicorn"]
    A -->|"POST /api/v1/swipe"| B
    A -->|"Swipe right"| C["🌐 Publisher webpage"]
    B --> D["⚡ news_fetcher.py — NewsAPI + Google News RSS"]
    B --> E["🐘 PostgreSQL 16 — articles, categories, user_swipes, sessions, request_logs"]
    F["⏰ APScheduler — every 12h"] --> B
```

Cards are rendered **on the phone**, not on the server. An earlier version generated 720×1280 PNGs with Pillow; that was retired when the client went native, which removed the image pipeline, the disk volume, and the memory pressure that made Pillow painful on a 956 MB VM.

### How a card reaches the screen

1. **Fetch** — `news_fetcher.py` pulls `ARTICLES_PER_CATEGORY` (12) items per topic across 7 topics: 84 candidates per cycle. NewsAPI when a key is set, Google News RSS otherwise.
2. **Classify** — a weighted keyword scorer can reassign an article away from the topic whose feed it arrived on, when the signal is strong enough.
3. **Deduplicate** — `article_key = md5(title + url)` with a unique constraint. Repeat stories are dropped at insert time.
4. **Balance** — `get_balanced_feed()` uses `ROW_NUMBER() OVER (PARTITION BY category ORDER BY id DESC)` to interleave topics evenly.
5. **Serve** — `/api/v1/feed` returns metadata, an absolute image URL, and the source link.
6. **Record** — the client posts each swipe to `/api/v1/swipe`.

### Tech stack

**Backend** — Python 3.12 · FastAPI · Uvicorn · psycopg2 · APScheduler · feedparser · PostgreSQL 16
**Android** — Kotlin · Jetpack Compose · Retrofit · Coil · Custom Tabs
**Web preview** — HTML5 + vanilla JS touch gesture engine
**Infrastructure** — Docker · Docker Compose · Oracle Cloud (E2.1.Micro, Always Free)

---

## Folder structure

```
Paperswap/
├── README.md                      # This file
├── PROJECT_STATUS.md              # Progress log and open items
├── DEPLOYMENT.md                  # Hosting options and cost comparison
├── DATABASE_AND_CLOUD_GUIDE.md    # Cloud concepts walkthrough
│
├── backend/
│   ├── main.py                    # FastAPI app, routes, scheduler, CLI runner
│   ├── database.py                # PostgreSQL schema, topic catalogue, queries
│   ├── news_fetcher.py            # Ingestion, classification, summarisation
│   ├── backfill_categories.py     # One-off reclassification migration (dry-run by default)
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── .env.example               # Copy to .env and fill in
│   └── templates/
│       ├── mobile_preview.html    # Touch swipe UI      → /mobile
│       ├── dashboard.html         # Card gallery        → /
│       └── analytics.html         # Telemetry dashboard → /analytics
│
└── android/
    └── app/src/main/java/com/newsswipe/app/
        ├── MainActivity.kt
        ├── data/{model,remote}/
        ├── ui/{components,theme}/
        └── viewmodel/NewsViewModel.kt
```

---

## Getting started

### Docker Compose (recommended)

```bash
cd backend
cp .env.example .env      # then fill in the required values
docker compose up --build
```

Compose refuses to start if `NEWS_API_KEY`, `POSTGRES_USER`, or `POSTGRES_PASSWORD` are unset — deliberately, so a missing secret fails loudly instead of silently booting on a weak default.

### Local Python

Requires a PostgreSQL instance you can point at.

```bash
cd backend
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:password@localhost:5432/newsdb
python main.py
```

Add `--cli` to sync news once and exit without starting the server.

### Then open

| URL | What you get |
| --- | --- |
| `http://localhost:8000/mobile` | Swipe UI — open this on a phone |
| `http://localhost:8000` | Card gallery dashboard |
| `http://localhost:8000/analytics` | Telemetry and engagement dashboard |
| `http://localhost:8000/docs` | Interactive OpenAPI documentation |

---

## Configuration

Set in `backend/.env`. See `.env.example` for the template.

| Variable | Description | Default |
| --- | --- | --- |
| `NEWS_API_KEY` | [NewsAPI.org](https://newsapi.org/) key. Without it, Google News RSS is used. | **required** |
| `POSTGRES_USER` | Database user | **required** |
| `POSTGRES_PASSWORD` | Database password. Generate with `openssl rand -hex 24`. | **required** |
| `POSTGRES_DB` | Database name | `newsdb` |
| `DATABASE_URL` | Full connection string. Compose assembles this from the three variables above — set it only for non-Docker runs. | — |
| `REFRESH_INTERVAL` | **Hours** between background refreshes | `12` |
| `PURGE_OLDER_THAN_DAYS` | Articles older than this are deleted each cycle | `7` |
| `ARTICLES_PER_CATEGORY` | Fetched per topic per cycle (× 7 topics = 84 candidates) | `12` |
| `FEED_DEFAULT_LIMIT` | Default deck size from `/api/v1/feed` | `70` |

> `REFRESH_INTERVAL` is in **hours**, not seconds. An earlier version of this file said seconds, which meant following it produced a refresh cycle roughly every five years.

**Generate passwords as hex, not base64.** `DATABASE_URL` embeds the password in a URL, where `+` and `/` are structural characters. A base64 password containing either silently fails authentication in a way that looks like a wrong password.

---

## API reference

Interactive docs at `/docs`.

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/v1/feed` | The swipe deck. Query: `categories` (comma-separated), `limit` (1–300), `balanced` (default `true`). |
| `GET` | `/api/v1/categories` | Topic catalogue with label, accent colour, sort order, and live article count. |
| `GET` | `/api/v1/cards/refresh` | Trigger a fetch + dedup cycle. |
| `POST` | `/api/v1/swipe` | Record a swipe. Body: `{"article_id": int, "action": "read" \| "pass"}`. |
| `POST` | `/api/v1/telemetry/heartbeat` | Session keepalive. Body: `{"session_id": str, "user_agent": str?}`. |
| `GET` | `/api/v1/telemetry/stats` | System and engagement metrics. |
| `GET` | `/api/v1/telemetry/logs` | Recent application logs. |

`GET /api/news` and `GET /api/cards/generate` remain as deprecated aliases.

### Known API issues

These are real and tracked, not oversights:

- **`/api/v1/cards/refresh` is a `GET` that mutates state** and requires no authentication. A single request triggers 84 outbound calls. Should be an authenticated `POST`.
- **No authentication anywhere**, including the telemetry endpoints.
- **No pagination.** `/api/v1/feed` has `limit` but no `offset`, so the deck ends when the client exhausts it.
- **`/api/v1/feed/next` does not exist** despite appearing in older documentation.

---

## Deployment

Running on an Oracle Cloud E2.1.Micro (Always Free, 956 MB + 2 GB swap) under Docker Compose. See `DEPLOYMENT.md` for a cost comparison across providers.

Deployment is currently manual: `git pull` on the VM, then `docker compose down && docker compose up -d`. There is no CI, no health gate, and no rollback. Building that pipeline is on the roadmap.

**Currently serving plain HTTP.** HTTPS via DuckDNS + Caddy is the next infrastructure task, and a prerequisite for removing `usesCleartextTraffic` from the Android manifest.

---

## Roadmap

**Correctness and reliability**
- Connection pooling — every query currently opens a new connection, and error paths leak them
- Move request logging off the hot path; it currently blocks the event loop
- Retry-tolerant startup so an unreachable database yields a `503` rather than a crash loop
- Test suite and CI — there are currently no tests

**Security**
- HTTPS via Caddy; close port 8000
- API key on mutating and telemetry endpoints
- Restrict SSH ingress

**Data science** — the substantial gap
- The topic classifier is hand-tuned keyword scoring with **no labelled data and no measured accuracy**. Building an evaluation set and benchmarking it against TF-IDF and transformer baselines is the priority.
- `user_swipes` has been collecting implicit feedback that nothing consumes. No personalisation exists; the feed is identical for every user.
- Parts of the telemetry dashboard report estimated values as if measured. Those need to be measured or explicitly labelled.

---

## Project visibility

Development happens on `dev` branches merged to `main` via pull request. [Issues](../../issues) and [Pull Requests](../../pulls) are public.

---

## Contributing

To request access, email `mate.kovasznai@gmail.com`. Bug reports and feature suggestions are welcome via Issues.
