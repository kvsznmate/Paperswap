# Architecture Decision Records

Why Paperswap is built the way it is. Each record states the situation, the decision, and what it cost — including the decisions that turned out to be wrong.

Format: a lightweight [ADR](https://adr.github.io/). Superseded records stay in place rather than being deleted; the history is the point.

---

## ADR-001 — Retire server-side card rendering; render on the phone

**Status:** Accepted · **Supersedes:** the original Pillow pipeline

### Context

The first version generated a 720×1280 PNG per article with Pillow and served it from `output/cards/`. Three problems emerged once it was deployed:

- The 956 MB VM could not build Pillow without adding a 2 GB swap file, and image composition was the single heaviest operation the server performed.
- Every article carried an image file, so the disk footprint grew with the feed.
- Card layout changes required a server redeploy and regeneration of every stored card.

### Decision

The backend became a thin JSON API. Cards are composed on the device — a native Compose `NewsCard` built from JSON props, existing only in device memory.

### Consequences

Roughly 99% smaller storage and near-zero server compute. Layout changes ship with the app rather than requiring regeneration. Pillow and the image volume left the dependency tree entirely.

The cost: card appearance is now client-side, so the web preview and the Android app can drift apart visually. Accepted — they serve different purposes.

Retired: `card_generator.py`, `output/cards/`, the `card_filename` field, and `RetrofitInstance.getFullImageUrl()`.

---

## ADR-002 — SQLite → PostgreSQL 16

**Status:** Accepted

### Context

SQLite serialises writers. The APScheduler refresh thread and request handlers contended for the write lock, and a refresh could block reads. SQLite also lacks the window functions needed for topic interleaving (ADR-005).

### Decision

PostgreSQL 16 in a sidecar container with a named `pgdata` volume.

### Consequences

Gained concurrent writes, window functions, `ON CONFLICT` upserts, and `ON DELETE CASCADE` for swipe cleanup.

The cost is a second container on a small VM and connection management becoming a real concern — see [Failure modes](#failure-modes-weve-hit). `news_database.db` was orphaned by this migration and stayed committed to the repo for months afterward, which is how the documentation drifted.

**Postgres is not published to the host.** The `db` service has no `ports:` mapping, so it is reachable only on the compose network. This is deliberate and is the reason a weak database password was never externally exploitable.

---

## ADR-003 — Oracle Cloud Always Free (E2.1.Micro)

**Status:** Accepted

### Context

The project needed an always-on host: the refresh scheduler runs in-process, so scale-to-zero platforms would silently stop fetching. Budget was zero.

### Decision

Oracle Cloud Always Free. The intended A1.Flex ARM shape was persistently out of capacity, so E2.1.Micro (x86, 956 MB RAM) was used, with a 2 GB swap file made persistent via `fstab`.

### Consequences

€0/month, always-on, 10 TB egress. The 956 MB ceiling directly motivated ADR-001.

Risks accepted: Oracle reclaims idle instances (the refresh job provides enough activity), the free ARM allowance was halved in June 2026 with no notice, and the public IP is ephemeral — which is why hardcoding it in the Android client is a known defect.

---

## ADR-004 — Deduplication by MD5 key with `ON CONFLICT DO NOTHING`

**Status:** Accepted

### Context

The same story appears across multiple feeds, and topic feeds overlap heavily — an Apple earnings story arrives via both Tech and Finance.

### Decision

`article_key = md5(normalised title + normalised url)`, `UNIQUE`, with inserts using `ON CONFLICT (article_key) DO NOTHING`.

### Consequences

Deduplication is atomic and race-free. Two threads inserting the same story concurrently produce one row with no application-level locking.

**Do not add a check-then-insert around this.** The current code calls `is_article_in_db()` before `save_article()`, which is redundant, doubles connection count, and reintroduces the race the constraint exists to prevent. Removing it is a tracked task.

---

## ADR-005 — Topic-balanced feed via window function

**Status:** Accepted

### Context

Ordering strictly by recency produced runs of a dozen Sports cards before any other topic, because feed volume differs sharply per topic. Current distribution ranges from ~174 Sports articles to ~14 Programming.

### Decision

```sql
ROW_NUMBER() OVER (PARTITION BY category ORDER BY id DESC)
```

Rank within each topic, then order by rank in the outer query to interleave round-robin. An MD5 shuffle on `(category, rank)` varies which topic leads.

### Consequences

Every topic appears early regardless of volume. This is the single query that required Postgres over SQLite.

**Pagination must apply `OFFSET` to the outer query, not the CTE**, or balance breaks across page boundaries. The endpoint currently has no `offset` at all — tracked.

---

## ADR-006 — Server-driven topic catalogue

**Status:** Accepted

### Context

Topic display names and brand colours were hardcoded in the client. Adding a topic meant a release.

### Decision

`database.CATEGORIES` is the single source of truth. `init_db()` syncs it into the `categories` table on every boot, and every article carries `category_label` and `accent_color` in its payload. `/api/v1/categories` exposes the catalogue with live counts.

### Consequences

Adding a topic is a backend change with no client release.

This decision was correct and then **not honoured for months**. `NewsCard.kt` kept a hardcoded `isTech ? TECH : FINANCE` branch, so five of seven topics rendered a "FINANCE & MARKETS" badge in the wrong colour while the correct values sat unread in the payload. Fixed.

The lesson generalises: a capability that nothing consumes is indistinguishable from a capability that does not exist.

---

## ADR-007 — Keyword classifier, not a trained model

**Status:** Accepted, unmeasured

### Context

Articles arrive tagged by the feed they came from, which is often wrong — a Tech feed carries plenty of finance and general news.

### Decision

Weighted keyword scoring. Each topic has a keyword set and a weight; title matches score double description matches; an article is reassigned only if it beats its feed-assigned topic by `MIN_OVERRIDE_MARGIN`. Weights favour narrow topics (Beauty, Sports = 3) over broad ones (Tech, Finance = 1), so a specific signal can override a generic one.

Runs in well under a millisecond with no training data, no model artifact, and no memory cost — which matters on a 956 MB box.

### Consequences

**Its accuracy has never been measured.** There is no labelled dataset, no confusion matrix, and no baseline comparison. Every weight and the threshold were chosen by intuition. The classifier may be excellent or poor; the honest answer is that nobody knows.

One known consequence of the parameter choice: Tech and Finance share weight 1, so a single title match scores 2 — below the margin of 3. **Tech↔Finance reassignment therefore cannot fire**, which is likely the most common confusion in this feed. Whether that is correct conservatism or a bug is exactly the question a labelled dataset would answer.

Closing this gap is the top priority on the roadmap.

---

## ADR-008 — Secrets in `.env`, with fail-fast substitution

**Status:** Accepted

### Context

Database credentials were literals in `docker-compose.yml`, in a public repository. `NEWS_API_KEY` used `${NEWS_API_KEY:-your_news_api_key_here}` — a **silent** default.

That silent default nearly caused real data loss. The `.env` on the VM went missing at some point, and the container kept running on environment values Docker had frozen at creation time. Nothing reported a problem. Had the container been recreated, the only copy of the API key would have been gone. The placeholder default meant NewsAPI could have been failing indefinitely while the app served RSS content and looked healthy.

### Decision

All credentials come from a gitignored `.env`, referenced with the **required** form:

```yaml
POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in backend/.env}
```

`.env.example` documents the keys with empty values. `.gitignore` uses `.env*` with `!.env.example`, so backups like `.env.bak` cannot be committed by accident.

### Consequences

A missing secret stops the container with a named error instead of booting into a degraded state. Slightly less convenient for first-time setup; that trade is correct.

---

## ADR-009 — Generate passwords as hex, not base64

**Status:** Accepted

### Context

`DATABASE_URL` embeds the password in a URI: `postgresql://user:password@host:5432/db`. The userinfo component requires percent-encoding.

`openssl rand -base64` draws from an alphabet including `+`, `/`, and `=`. A `+` decodes as a space; a `/` terminates the authority component. Postgres stores the literal password correctly and `psql` authenticates fine over a socket, but libpq parses the URI before connecting and sends something different.

The failure presents as `password authentication failed` — indistinguishable from a genuinely wrong password. It cost several hours.

### Decision

Generate with `openssl rand -hex 24`. Hex is `[0-9a-f]` only: no character special to URIs, shells, `.env` parsing, or SQL. 48 hex characters carries more entropy than 32 base64 characters.

### Consequences

Removes an entire class of silent failure. **The better fix is structural** — pass discrete connection parameters (`host`, `dbname`, `user`, `password`) to `psycopg2.connect()` instead of composing a URI, which makes any character safe. That is planned alongside connection pooling.

---

## Failure modes we've hit

Hard-won operational facts. Each of these caused a real outage.

### `POSTGRES_PASSWORD` is an initdb-time variable

The Postgres entrypoint reads it **only when the data directory is empty**. Once `pgdata` is populated it is inert. Changing it in compose does nothing, and if `DATABASE_URL` is updated to match, the backend locks itself out of a database that still holds the old password.

Rotate in this order:

```bash
docker exec -i news_cards_db psql -U newsuser -d newsdb -c "ALTER USER newsuser WITH PASSWORD '$NEW';"
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$NEW|" .env
docker compose down && docker compose up -d
```

Read `$NEW` from one variable for both steps so the two cannot diverge.

### `.env` changes need `down`/`up`, not `--force-recreate`

`--force-recreate` can carry stale environment forward. A rotation that appears to have been applied may not have reached the container at all.

### Verify the deployed artifact before acting on it

An edit is not a deploy. There are four links — commit, push, merge, pull — and a change stalled at any one of them produces symptoms identical to a change that arrived and failed.

Before debugging anything that depends on a change, confirm it landed:

```bash
grep -n POSTGRES_PASSWORD backend/docker-compose.yml   # expect ${POSTGRES_PASSWORD:?...}
```

Skipping this check produced six consecutive failed fixes against a file that had never left the development machine. **This is the most expensive lesson in this document.**

The structural fix is a `/health` endpoint reporting the deployed commit SHA, injected at build time. One `curl` would have answered the question immediately.

### Startup is fragile by design flaw

`init_db()` connects eagerly inside `lifespan` with no retry and no backoff, so an unreachable database exits the process. `restart: unless-stopped` then produces a crash loop that reports nothing useful — the outage is only visible via `docker compose ps` or by noticing port 8000 is not listening.

A pool with startup retry would log the failure, keep the process alive, and let `/health` return `503`. Tracked.

### The database survives container removal

`docker compose down` removes containers and networks but **not** named volumes. Article data persisted through roughly a dozen recreates during one debugging session. Only `down -v` destroys it.

---

## Open questions

- **Is the topic taxonomy well-posed?** An Apple earnings story is genuinely both Tech and Finance. Single-label classification may be the wrong frame; multi-label with a primary topic for the badge is worth evaluating.
- **Can per-user personalisation be justified?** `user_swipes` collects implicit feedback, but with a single user any personalisation claim is statistically unsupportable. Framing it as a single-user cold-start study is more honest.
- **Should the classifier be replaced?** Only a labelled dataset can answer this. If a keyword scorer matches TF-IDF at a fraction of the latency on a 956 MB box, keeping it is a defensible engineering result — but it has to be measured to be claimed.
