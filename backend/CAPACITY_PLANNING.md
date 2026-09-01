# Paperswap — Database Capacity Planning

_How many more articles can the VM hold before it hurts?_

**Written:** 2026-09-01
**Method:** configuration read directly from the repo; sizing modelled from that configuration. **Nothing here is a live reading of the VM.** Every modelled figure is labelled as such, and [Part 7](#part-7--replace-my-estimates-with-measurements) gives the commands to replace it with a real one.

---

## ⚠️ Read this first: two docs are lying to you

`AGENT_SYSTEM_PROMPT.md` and `DATABASE_AND_CLOUD_GUIDE.md` both describe an architecture the code no longer has:

| Those docs say | The code actually does | Source |
|---|---|---|
| SQLite, single `news_database.db` file | **PostgreSQL 16-alpine**, `pgdata` named volume | `docker-compose.yml` |
| Pillow renders 720×1280 PNGs server-side | **No Pillow at all** — "cards are rendered on the phone" | `Dockerfile`, `requirements.txt` |
| `articles.card_filename` column | Column doesn't exist | `database.py:246` |
| 2 topics (Tech, Finance), 50-card batches | **7 topics**, 84-candidate batches | `database.py:36`, `.env.example` |

This matters enormously for capacity. Under the documented architecture, one article costs ~250 KB (a PNG). Under the **actual** architecture, one article costs **~1 KB** (text only). Anyone sizing this system off those docs — a human or a coding agent — would over-provision by roughly **250×** and conclude you were nearly full when you have used a rounding error.

**Action:** update or delete those two docs. They will keep producing wrong answers.

---

## Part 1 — Where you are now

### Configuration (measured from the repo)

| Setting | Value | Source |
|---|---|---|
| Topics | 7 (Tech, Finance, Sports, Politics, Programming, Science, Beauty) | `database.py` `CATEGORIES` |
| `ARTICLES_PER_CATEGORY` | 12 | `.env.example` |
| Candidates per refresh | **84** (7 × 12) | derived |
| `REFRESH_INTERVAL` | 12 h → **2 cycles/day** | `.env.example` |
| Candidate fetches/day | **168** | derived |
| `PURGE_OLDER_THAN_DAYS` | 7 | `.env.example` |
| `FEED_DEFAULT_LIMIT` | 70 | `.env.example` |
| Feed hard cap | **300** (`le=300`) | `main.py:555` |
| Database | PostgreSQL 16-alpine | `docker-compose.yml` |
| Server-side images | **none** | `Dockerfile` |
| VM shape | E2.1.Micro — 1/8 OCPU, 956 MB RAM | `PROJECT_STATUS.md` |
| Swap | 2 GB | `PROJECT_STATUS.md` |

### How many articles are in there right now (modelled)

Steady state = `new articles per day × retention days`.

| Novelty rate | New rows/day | Steady state at 7-day retention |
|---|---|---|
| 18% (your observed 9-new-of-50 log) | 30 | **~210** |
| 30% (plausible over a 12 h gap) | 50 | **~350** |
| 100% (theoretical, zero duplicates) | 168 | **1,176** ← hard ceiling of current config |

So the `articles` table today holds somewhere in the region of **200–1,200 rows**, most likely **300–400**.

### What one article costs on disk (modelled)

Postgres row, from the actual schema at `database.py:246`:

| Component | Bytes |
|---|---|
| Tuple header + null bitmap + alignment | 28 |
| `id` (int4) | 4 |
| `article_key` (MD5 hex, 32 ch) | 33 |
| `title` (~75 ch) | 76 |
| `description` (~300 ch — **unbounded**, see note) | 301 |
| `source` | 16 |
| `published_at` (TEXT) | 21 |
| `category` | 9 |
| `image_url` (~110 ch) | 111 |
| `url` (~110 ch) | 111 |
| `created_at` (timestamptz) | 8 |
| Line pointer + 90% page fill | ~90 |
| **Heap subtotal** | **~800 B** |
| 4 indexes (pkey, `article_key` unique, `category+id`, `created_at`) | **~170 B** |
| **Total per article** | **≈ 1.0 KB** |

> **Note on `description`:** `generate_short_summary()` returns the publisher's description with **no length cap**. Most RSS descriptions are 200–350 characters, but some feeds emit multi-kilobyte blobs. A single 8 KB description gets TOASTed out-of-line. This is the one column that could make the 1 KB figure wrong — worth measuring (Part 7) and worth capping at ~400 characters regardless.

### Current footprint (modelled)

| Table | Rows | Size |
|---|---|---|
| `articles` | ~350 | **~0.35 MB** |
| `request_logs` | see Part 5 | ~9 MB |
| `user_swipes` | few hundred | <0.1 MB |
| `user_sessions` | tens | <0.1 MB |
| `categories` | 7 | trivial |
| Postgres catalogs + WAL | — | ~100–250 MB |
| **`pgdata` volume total** | | **~150–300 MB** |

**Your article data is under half a megabyte.** The database volume is almost entirely Postgres's own fixed overhead — write-ahead log and system catalogs — not your content.

---

## Part 2 — The four ceilings, ranked

There isn't one limit. There are four, and they bind in a surprising order.

| # | Ceiling | Binds at | Comment |
|---|---|---|---|
| 1 | **Article supply** | **~2,000–3,500** | Your news sources physically cannot produce more |
| 2 | **Feed query CPU** | ~20,000–50,000 | O(N) window function on 1/8 OCPU |
| 3 | **RAM (956 MB)** | doesn't scale with N | Bounded by `limit≤300`, not by table size |
| 4 | **Disk** | ~38,000,000 | Not a real constraint at any conceivable scale |

Read that top to bottom: **the thing that stops you is not the VM.** It's that there isn't enough news.

---

## Part 3 — Ceiling by ceiling

### 🥇 Ceiling 1 — Article supply (binds first)

`NEWS_API_KEY` points at NewsAPI, but its free Developer plan is capped at 100 requests/day, delays articles by roughly 24 hours, limits search history to about a month, restricts CORS to localhost only, and is licensed for development and testing rather than a live app. (Verified against NewsAPI's published pricing, September 2026; the first paid tier is $449/month.) A deployed VM serving a phone is exactly the case that tier excludes — so in practice your real source is the **Google News RSS fallback** in `TOPIC_FEEDS`.

Google News RSS search feeds return roughly **100 entries per query**, hard stop.

| | Value |
|---|---|
| Feeds configured | 7 (one per topic) |
| Max candidates per refresh | **700** (7 × 100) |
| At 2 refreshes/day | 1,400 gross/day |
| Genuinely new after dedup (~30% over a 12 h gap) | **~400/day** |
| × 7-day retention | **~2,800 steady state** |
| × 30-day retention | **~12,000 steady state** |

**This is your real ceiling.** Turning `ARTICLES_PER_CATEGORY` up to 500 changes nothing — the feed only has 100 items in it.

### 🥈 Ceiling 2 — Feed query CPU (the one to actually watch)

`get_balanced_feed()` (`database.py:509`) does this:

```sql
WITH ranked AS (
    SELECT ..., ROW_NUMBER() OVER (PARTITION BY category ORDER BY id DESC) AS rank_in_category
    FROM articles                    -- ← no recency filter
)
SELECT ... FROM ranked WHERE rank_in_category <= %s
```

The `WHERE rank_in_category <= N` **cannot be pushed inside the window function**. Postgres must rank *every row in the table* before discarding all but ~70 of them. The cost of serving one swipe deck is **O(total articles)**, forever.

`idx_articles_category_id (category, id DESC)` matches the PARTITION/ORDER exactly, so this is an ordered index scan feeding a streaming `WindowAgg` — no sort, no unbounded memory. Good. But it still walks every index entry, on a shape with **1/8 of an OCPU baseline**.

Modelled latency (verify with `EXPLAIN ANALYZE`, Part 7):

| Articles | Est. feed query time | Verdict |
|---|---|---|
| 350 (today) | 2–5 ms | invisible |
| 5,000 | 10–25 ms | invisible |
| 20,000 | 40–100 ms | fine |
| 50,000 | 100–250 ms | noticeable on deck reload |
| 200,000 | 0.4–1 s | swipe app feels broken |
| 1,000,000 | 2–5 s | unusable |

For a swipe app the deck load should be under ~150 ms. That puts the comfortable ceiling at **20,000–50,000 articles** — *unless you fix the query*, which is cheap to do (Part 6).

### 🥉 Ceiling 3 — RAM (956 MB)

The good news: **RAM does not scale with table size.** The feed endpoint is capped at `le=300` and the window function streams, so a bigger table costs CPU, not memory.

Peak memory of one maximum-size feed request (modelled):

| Stage | Cost |
|---|---|
| psycopg2 `RealDictCursor.fetchall()`, 300 rows × ~1.5 KB | 0.45 MB |
| `_decorate_articles()` builds a second dict per row (both alive at once) | +0.45 MB |
| FastAPI JSON body | +0.3 MB |
| **Peak per request** | **~1.2 MB** |

Ten concurrent max-size requests = ~12 MB. Not a problem.

What *does* threaten 956 MB is **connection concurrency**, which is unrelated to article count:

| Consumer | Idle | At pool saturation |
|---|---|---|
| Ubuntu 22.04 + systemd | 180 MB | 180 MB |
| Docker daemon + containerd | 110 MB | 110 MB |
| Postgres `shared_buffers` (default 128 MB) | 128 MB | 128 MB |
| Postgres backends (~10 MB each) | 3 × 10 = 30 MB | **20 × 10 = 200 MB** |
| FastAPI / uvicorn | 130 MB | 130 MB |
| **Total** | **~580 MB (61%)** | **~750 MB (78%)** |

`DB_POOL_MAX=20` on a 956 MB box is optimistic. The code comment at `database.py:130` already flags this. Swap absorbs the overshoot, but swapping on 1/8 OCPU is painful. **Consider `DB_POOL_MAX=10`** — with `minconn=3` and single-digit-millisecond queries you will not saturate 10.

### 4️⃣ Ceiling 4 — Disk (irrelevant)

Modelled, for a ~46.6 GB OCI boot volume:

| Consumer | Size |
|---|---|
| Ubuntu 22.04 base | 2.5 GB |
| Docker engine + containerd | 0.4 GB |
| Swap file | 2.0 GB |
| Docker images (app ~280 MB + postgres:16-alpine ~250 MB + dangling layers) | 0.9 GB |
| `pgdata` volume | 0.3 GB |
| Logs, APT cache, misc | 0.4 GB |
| **Fixed overhead** | **~6.5 GB** |
| 15% safety reserve | 7.0 GB |
| **Free for articles** | **~33 GB** |

At 1 KB/article: **33 GB ÷ 1 KB ≈ 34 million articles.**

Put another way:

| Articles | Disk used |
|---|---|
| 350 (today) | 0.34 MB |
| 4,000 | 3.9 MB |
| 12,000 | 12 MB |
| 50,000 | 49 MB |
| 100,000 | 98 MB |
| **1,000,000** | **977 MB** |
| 34,000,000 | 33 GB |

**A million articles fits in under one gigabyte.** Disk will never be the reason you stop.

---

## Part 4 — So how much can you increase it?

Three independent levers:

| Lever | Now | Recommended | Stretch | Multiplier |
|---|---|---|---|---|
| `ARTICLES_PER_CATEGORY` | 12 | **40** | 80 | ×3.3 → ×6.7 |
| `REFRESH_INTERVAL` (hours) | 12 | **6** | 4 | ×2 → ×3 |
| `PURGE_OLDER_THAN_DAYS` | 7 | **21** | 30 | ×3 → ×4.3 |

Naive combined multiplier is ~20×, but novelty decays as you fetch deeper into the same feed and refresh more often — you re-see the same items. Realistic gain is **8–12×**.

### Recommended target: ~350 → ~4,000 articles

```env
ARTICLES_PER_CATEGORY=40
REFRESH_INTERVAL=6
PURGE_OLDER_THAN_DAYS=21
REQUEST_LOG_RETENTION_DAYS=7     # pin explicitly — see Part 5
SESSION_RETENTION_DAYS=7         # pin explicitly — see Part 5
DB_POOL_MAX=10
```

| Metric | Before | After | Headroom used |
|---|---|---|---|
| Articles | ~350 | ~4,000 | — |
| `articles` table | 0.34 MB | **3.9 MB** | 0.012% of free disk |
| Feed query | ~3 ms | **~10 ms** | invisible |
| Peak RAM | unchanged | unchanged | — |
| Refresh cycles/day | 2 | 4 | — |

This is an **11× increase that costs 3.6 MB and 7 ms.** There is no risk here worth discussing.

### Stretch target: ~12,000 articles

`ARTICLES_PER_CATEGORY=80`, `REFRESH_INTERVAL=4`, `PURGE_OLDER_THAN_DAYS=30` → ~12 MB, feed query ~30–60 ms. Still comfortably safe, and roughly the point where **article supply runs out** — beyond this you'd need more topics or more feeds per topic, not more VM.

### Hard stop on this VM shape: 50,000 articles

Beyond that, fix the feed query (Part 6) before going further. With the query fixed, 500,000+ is fine on the same box.

---

## Part 5 — Two things that will fill the disk before articles do

### 1. `request_logs` already dwarfs `articles`

`_active_users_count` is polled by the dashboard **every 8 seconds** (per the comment at `database.py:350`). One analytics tab left open:

```
10,800 requests/day × ~125 B/row = 1.35 MB/day
× 7-day retention = ~9.5 MB
```

That table is **~27× larger than `articles`** right now. It's still small in absolute terms, but note the trap:

> `REQUEST_LOG_RETENTION_DAYS` and `SESSION_RETENTION_DAYS` both **default to `PURGE_OLDER_THAN_DAYS`**.

So raising the article window to 21 days silently triples your log retention too — to ~40 MB of logs you don't need, *and* it changes what the analytics panels measure. Your own `.env.example` warns that the hourly-usage panel UNIONs `request_logs` with `user_swipes`, and the two halves must cover the same period or swipes under-represent.

**Pin both explicitly before raising the article window.** They should track the *analytics* window, not the *content* window — these were coupled by accident, not by design.

### 2. Docker logs are uncapped

`docker-compose.yml` sets no `logging:` block, so both containers use the default `json-file` driver with **no `max-size`**. On a long-running container this is the single most likely thing to actually fill your boot volume, and it has nothing to do with how many articles you store.

```yaml
# add to BOTH services in docker-compose.yml
logging:
  driver: "json-file"
  options:
    max-size: "10m"
    max-file: "3"
```

---

## Part 6 — Unlock 10× more: fix the O(N) feed query

One line turns the feed query from "rank the whole table" into "rank a bounded slice":

```sql
WITH ranked AS (
    SELECT id, title, description, source, published_at, category,
           image_url, url, created_at,
           ROW_NUMBER() OVER (PARTITION BY category ORDER BY id DESC) AS rank_in_category
    FROM articles
    WHERE created_at > NOW() - INTERVAL '3 days'    -- ← add this
      {existing category filter}
)
```

`idx_articles_created_at (created_at DESC)` already exists to serve it. Cost becomes O(rows in window) instead of O(all rows), which means **retention length stops affecting feed latency entirely** — you could keep 90 days of archive and the deck would still load in single-digit milliseconds.

Trade-off to be explicit about: a topic with nothing published in 3 days returns an empty partition instead of falling back to older cards. Either widen the interval, or make it a config value, or add a fallback pass when a topic comes back short. Worth deciding deliberately rather than inheriting.

An alternative shape — `LATERAL` join over the `categories` table taking top-N per category — is O(topics × per_category) regardless of window, but it's a bigger rewrite.

---

## Part 7 — Replace my estimates with measurements

Everything above the line is modelled from config. Run these on the VM to get real numbers.

```bash
# --- articles: count and topic split ---
docker exec news_cards_db psql -U "$POSTGRES_USER" -d newsdb -c \
  "SELECT category, COUNT(*) FROM articles GROUP BY category ORDER BY 2 DESC;"

# --- real bytes per table, including indexes and TOAST ---
docker exec news_cards_db psql -U "$POSTGRES_USER" -d newsdb -c \
  "SELECT c.relname,
          pg_size_pretty(pg_total_relation_size(c.oid)) AS total,
          pg_size_pretty(pg_relation_size(c.oid))       AS heap,
          s.n_live_tup                                  AS rows
   FROM pg_class c
   JOIN pg_namespace n ON n.oid = c.relnamespace
   LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
   WHERE n.nspname = 'public' AND c.relkind = 'r'
   ORDER BY pg_total_relation_size(c.oid) DESC;"

# --- the key number: actual bytes per article (my model says ~1024) ---
docker exec news_cards_db psql -U "$POSTGRES_USER" -d newsdb -c \
  "SELECT pg_total_relation_size('articles') / GREATEST(COUNT(*),1) AS bytes_per_article,
          ROUND(AVG(LENGTH(description))) AS avg_desc_chars,
          MAX(LENGTH(description))        AS max_desc_chars
   FROM articles;"

# --- whole database ---
docker exec news_cards_db psql -U "$POSTGRES_USER" -d newsdb -c \
  "SELECT pg_size_pretty(pg_database_size('newsdb'));"

# --- feed query cost at today's row count ---
docker exec news_cards_db psql -U "$POSTGRES_USER" -d newsdb -c \
  "EXPLAIN (ANALYZE, BUFFERS)
   WITH ranked AS (
     SELECT id, category,
            ROW_NUMBER() OVER (PARTITION BY category ORDER BY id DESC) AS r
     FROM articles)
   SELECT * FROM ranked WHERE r <= 10 ORDER BY r LIMIT 70;"

# --- host: disk, docker, memory ---
df -h /
docker system df
free -m
```

### Plug your numbers into these

**Disk ceiling:**
```
max_articles = (free_bytes − 0.15 × volume_bytes) ÷ bytes_per_article
```

**CPU ceiling** (the one that actually matters):
```
max_articles ≈ (150 ms ÷ measured_execution_time_ms) × current_row_count
```
Take `measured_execution_time_ms` from the `EXPLAIN ANALYZE` above. 150 ms is a deck-load budget that still feels instant — lower it to 100 ms if you want margin.

---

## Summary

| Question | Answer |
|---|---|
| Current article count | ~350 (modelled; verify with Part 7) |
| Current article storage | **~0.35 MB** |
| Disk ceiling | ~34 million articles |
| Query-latency ceiling | ~20,000–50,000 articles |
| **Supply ceiling (binds first)** | **~2,800 at 7-day retention** |
| **Recommended increase** | **~350 → ~4,000 (11×), costs 3.6 MB** |
| Stretch | ~12,000, costs 12 MB |
| Do first | Pin log retention; cap Docker logs; `DB_POOL_MAX=10` |
| Do before exceeding 50k | Add the recency filter to `get_balanced_feed` |

**The headline:** you are using roughly **one thousandth of one percent** of your disk on article data. Increase freely. The constraint was never the VM — it's that seven Google News RSS feeds can only produce a few hundred genuinely new stories a day.
