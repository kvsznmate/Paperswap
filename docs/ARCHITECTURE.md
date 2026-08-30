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

## ADR-010 — Telemetry reports provenance, and reports nothing it cannot measure

**Status:** Accepted · **Supersedes:** the estimated storage tree and the hardcoded quota panel

### Context

The analytics dashboard presented figures that were not measurements.

`get_folder_storage_sizes()` ran `du` against `/var` and `/usr`. Inside a container those paths are the *container's* — not the host's Docker image store or the Postgres volume — so the numbers came back small. The code treated "small" as "unreadable" and substituted fixed shares of total disk usage: `int(used_bytes * 0.62)` for `/var`, `int(used_bytes * 0.32)` for `/usr`. The docstring claimed this "ensures 100% of the 7.7 GB VM disk usage is accounted for." It did — by construction, not by measurement. The dashboard then rendered `percent_of_used_disk` to one decimal place, projecting a precision that did not exist.

`get_oracle_quota_status()` returned constants shaped like readings: 1 OCPU of 2, 2.0 GB of 12 GB RAM, 0.5 GB of egress, each tagged `"Safe"`. Three problems compounded. The values were never measured. The *limits* described an Ampere A1 (Arm) instance, while this project runs `VM.Standard.E2.1.Micro` (x86) — see ADR-003. And the memory figure claimed 2.0 GB in use on a machine with 956 MB of RAM, a number that could not be true.

The frontend carried its own untruths: a subtitle reading `oracle cloud arm vm`, and a quota row labelled `Block Storage (44 GB Max)` contradicting the backend's 200 GB.

A reviewer who opens the dashboard, is impressed, and then reads `int(used_bytes * 0.62)` has cause to discount every other number in the project. For work aimed at data roles this is not a coding defect; it is a data-integrity defect, and it is the more serious of the two.

### Decision

**Every numeric telemetry field declares its provenance.** The contract is two-valued:

| `measured` | Meaning |
|---|---|
| `true` | Read from the OS, the filesystem, or Postgres at request time. `source` names the mechanism. |
| `false` | Not observable from inside the container. Value is `null` and `unavailable_reason` says why. |

There is no third state. Nothing is estimated, interpolated, or inferred from a ratio.

**Published allowances are the one permitted constant.** A documented quota is a fact about Oracle's pricing, not a claim about this machine. Those live in `ALWAYS_FREE`, carry `is_limit`, a `source_url`, and a `verified_on` date, and are never rendered as though they were observed usage.

What is now measured: container directory sizes via `du -sb`; disk totals via `shutil.disk_usage`; memory and swap from `/proc/meminfo` (falling back to the cgroup limit when one is set); load average from `/proc/loadavg`; and database and per-table sizes from `pg_database_size()` and `pg_total_relation_size()`.

What is now declared unmeasurable: OCPU allowance consumption and outbound data transfer. Both require the OCI Monitoring API — an instance cannot observe its own tenancy-wide quota use.

The portion of the disk the container cannot see is reported as a single explicit `unaccounted_bytes` remainder with a note naming its likely occupants, rather than being divided among invented shares.

### Consequences

The storage panel now measures the container and the database instead of pretending to see the host, and it is honest about the remainder. The free-tier panel shows two measured rows and two greyed rows marked NOT MEASURABLE with the reason and the published allowance. The dashboard is smaller and less impressive at a glance. Every number on it is defensible, which is the trade this ADR makes deliberately.

One subtlety worth recording: the storage allowance compares **provisioned** volume size against the 200 GB limit, not bytes used. What consumes an Always Free block-volume allowance is the size of the volumes you have created, not how full they are. The previous code compared used bytes, which measured the wrong quantity even before the fabrication is considered.

A second subtlety: the memory reading is taken **once** per request and shared between the system panel and the quota panel. Two panels disagreeing about current memory use — because each took its own reading moments apart — looks indistinguishable from the fabrication this ADR removes.

### The measurement boundary, stated plainly

The backend runs in a container. It can see its own filesystem, its own `/proc`, and the database over the network. It cannot see the host filesystem, the Docker image store, the Postgres volume, or the OCI control plane. Granting that visibility is possible — mounting `/:/host:ro` plus the Docker socket — and was rejected: a read-only host-root mount hands any RCE in an internet-facing container the contents of `/etc/shadow`, the SSH keys, and `backend/.env`, and a read-only `docker.sock` mount is effectively host root, since it still permits creating privileged containers. ADR-002 records that Postgres is deliberately unpublished; undercutting that posture to fill in a bar chart is a poor trade. The honest dashboard is the cheaper and safer one.

### Enforcement

`backend/tests/test_telemetry_provenance.py` guards the boundary from three directions. Static checks reject the specific fabricating patterns and any float multiplier applied to a measured byte count. Schema checks assert every field carries `measured`, that unmeasured fields are null with a stated reason, and that the two memory panels report one shared reading. The responsiveness check is the one that actually catches fabrication: it writes 5 MB into the measured directory and asserts the reported size moves by 5 MB, then deletes it and asserts the figure returns. A constant cannot respond to a change in the world.

---

## ADR-011 — Enrichment runs as a one-shot ONNX job, not an in-process transformer

**Status:** Accepted · **Constrained by:** ADR-003 (E2.1.Micro, 956 MB)

### Context

The card back needs summary bullets, and the ranker needs article embeddings. The obvious stack is HuggingFace `transformers` — `distilbart-cnn-12-6` for abstractive summaries, `bart-large-mnli` for zero-shot topic labels, `sentence-transformers` for vectors.

None of it fits. The budget on this box:

| | RAM |
| --- | --- |
| Total | 956 MB |
| Postgres 16 resident | ~250 MB |
| FastAPI + uvicorn | ~130 MB |
| **Available** | **~550 MB** |
| `import torch`, before any weights load | ~350 MB |
| `distilbart-cnn-12-6` weights | 1.2 GB |
| `bart-large-mnli` weights | 1.6 GB |

The 2 GB swap file does not rescue this. Swap sits on a network-attached boot volume, so paging a 1.6 GB model per batch trades a 100 ns memory access for a ~1 ms network round trip. The realistic outcome is not a slow job — it is the OOM killer selecting Postgres, because Postgres is the largest RSS on the box.

A second constraint made the first one cheaper to accept: this deployment serves 2–3 users. There is no latency requirement on enrichment at all.

### Decision

Split the work by where it runs, not by what it is.

**On the VM**, `enrichment.py` runs as a separate one-shot process, invoked on a schedule by `run_enrichment.sh`. It never runs inside the API container's process, and it exits when finished so nothing stays resident.

(Originally specified as nightly. Measured throughput came in at ~3.4 s/article, so a full 84-article batch takes ~5 minutes and the job no-ops when nothing is pending. The installed cron runs every 6 hours instead, which keeps a card back from being empty for more than a few hours at a cost of ~20 min CPU/day.)

- Embeddings: `all-MiniLM-L6-v2`, int8 ONNX export, via `onnxruntime` + `tokenizers`. ~23 MB on disk, ~150 MB peak RSS. No torch anywhere in the tree — `transformers` and `sentence-transformers` were both rejected because they pull it transitively.
- Mean pooling and L2 normalisation are ten lines of numpy in `enrichment.Embedder`, rather than a library that would have brought the runtime with it.
- Bullets: TextRank implemented directly in `enrichment.textrank`. `sumy` was rejected because its tokenizer requires the nltk punkt corpus, a ~35 MB download onto a box with no room for it. The similarity graph uses MiniLM sentence vectors instead of the original paper's TF-IDF overlap, since the session is already loaded — this catches restatements that word overlap misses.
- `onnxruntime` is pinned to one intra-op and one inter-op thread. The instance has a single shared OCPU, so additional threads contend with Postgres for it and add per-thread arena allocations while making the batch slower.

**On a laptop**, against a database export: the model comparisons that do not fit. ROUGE scoring of extractive against abstractive summaries, BERTopic, `bart-large-mnli`, and ranker training. Unlimited RAM, no schedule.

Two guards make the split safe. Before loading the session, the job reads `MemAvailable` from `/proc/meminfo` and exits non-zero below `ENRICH_MIN_AVAILABLE_MB` (default 250) rather than starting a run that will swap. And `articles.enriched_at` is both the completion marker and the resume point — the job selects `WHERE enriched_at IS NULL` and commits per article, so a kill 40 rows in keeps those 40.

### Consequences

Inference runs on hardware that cannot host a transformer, at a total dependency cost of numpy, onnxruntime, tokenizers, and trafilatura. Nothing in the enrichment path is imported by `main.py`; the API container installs these only because it shares an image with the nightly job.

The cost is that production summaries are extractive. They select real sentences from the article and cannot paraphrase or compress across sentences, which an abstractive model does better. They also cannot hallucinate, which for a news card is arguably the right trade — but it is a trade, and it was forced by hardware rather than chosen on merit.

Whether extractive is actually worse here is unmeasured, and measuring it is a laptop task. That comparison is more valuable as a portfolio artifact than either model alone, because it is the only part of this that produces a number.

Model files are deliberately **not** vendored and **not** auto-downloaded. An unattended fetch on a 956 MB box is a poor failure mode; `enrichment.py`'s module docstring carries the one-time manual steps. Note that some published int8 exports are AVX-512-VNNI-specific, and this 2018-era Xeon does not have those instructions.

One parameter choice worth recording: the article vector embeds **title + description, not the body**. The user swipes on what the card shows. Embedding 20,000 characters they never saw would train the ranker on a different stimulus than the one that produced the label.

---

## ADR-012 — Split the training set from the analytics table

**Status:** Accepted · **Relates to:** ADR-010 (provenance)

### Context

`user_swipes.article_id` is `ON DELETE CASCADE`, and articles are purged at `PURGE_OLDER_THAN_DAYS` (7). Every swipe is therefore destroyed a week after its article was ingested.

That is correct for the dashboard. The hourly-usage and top-endpoint panels are a UNION of `request_logs` and `user_swipes`, and all three retention windows are held equal on purpose so a panel never renders two periods side by side as if they were one.

It is fatal for the recommender. Articles re-fetch, weights re-download, embeddings recompute — a swipe that happened and was deleted is gone. Capping the training set at seven days caps the model at seven days of signal permanently, no matter how long the deployment runs.

The two requirements are in direct conflict: analytics wants a bounded window, the model wants everything.

### Decision

Stop making one table serve both.

`user_swipes` is unchanged — same columns, same cascade, same window. Every analytics query reads it exactly as before.

`swipe_events` is new, has **no foreign key to `articles`**, and is never purged. `record_user_swipe` writes both tables in one transaction, using `INSERT..SELECT` so there is no window in which the article could be purged between a read and a write. It is ordered after the `user_swipes` insert deliberately: that insert is what raises `ForeignKeyViolation` on an unknown `article_id`, which `main.record_swipe` translates to a 404.

`swipe_events` denormalises title, description, category, and source at write time. That duplication is the point — it is what keeps a row trainable after its article is purged.

It stores **no vector**. The ranker trains off-box against a local archive, so the VM never reads a historical embedding; it only serves trained weights back. Text instead of vectors costs ~460 B/row rather than ~2 KB, and it means the entire history can be re-embedded when the model changes. A frozen vector cannot be — swap the embedding model and old rows silently occupy a different vector space than new ones, with no error to notice.

Two interaction signals were added to both tables: `dwell_ms` (time the card was frontmost) and `flipped` (whether the user turned it over). A flip is a stronger interest signal than a fast right-swipe, and neither is recoverable retroactively.

Per ADR-010, both are **nullable and old rows are not backfilled**. A swipe recorded before the client sent these fields had no dwell or flip observed; writing 0 would manufacture a measurement. NULL reads as "not measured", matching `request_logs.status_code`. Any model trained on this table must filter `dwell_ms IS NOT NULL` rather than treating NULL as zero engagement.

### Consequences

The model's history is no longer bounded by the dashboard's retention policy, and the dashboard's windows stayed aligned without compromise. Both requirements are met because neither had to bend.

The cost is unbounded growth in one table and duplicated text between two. At ~460 B/row, three users at 100 swipes/day is roughly 50 MB/year — small enough that no VM-side retention is needed yet, which is why none was added.

That table is currently the only copy of data that cannot be regenerated, and it lives on a free-tier volume with no backup, an ephemeral IP, and Oracle's idle-reclamation policy over it. Pulling it to a local archive is planned and not built: `GET /api/v1/export/swipes?since_id=N` behind `ADMIN_API_KEY`, a scheduled puller on a separate machine, and an export watermark so the VM never deletes above what has been confirmed archived. That watermark only becomes load-bearing once VM-side retention exists — today a missed sync loses nothing.

Exporting from another machine means the endpoint faces the internet, so HTTPS (Caddy + DuckDNS) is a prerequisite for the export route, not a follow-up to it. `ADMIN_COOKIE_SECURE` is still `false`.

---

## ADR-013 — Direct publisher feeds, not Google News

**Status:** Accepted · **Supersedes:** the original `rss_url` config

### Context

All seven topics pulled from Google News search feeds, and NewsAPI was nominally the primary source with RSS as fallback. In practice **100% of 532 stored articles came from RSS.** NewsAPI's free Developer plan is licensed for development use and rejects requests from server environments, so the key worked on a laptop and returned nothing from Oracle Cloud. The fetcher fell through silently every time, for the entire life of the deployment, and nothing surfaced it.

That mattered once enrichment arrived. Google News does not publish article links — it publishes redirect wrappers (`news.google.com/rss/articles/CBMi...`) that bounce through `consent.google.com` and loop until the client gives up. A phone browser follows them fine, so swipe-to-read always worked and the problem was invisible. Server-side extraction got nothing: `full_text` was NULL on every row and every card back was empty.

Since 2024 the wrapper cannot be decoded offline either. Resolving it requires Google's undocumented `batchexecute` endpoint, which would add a fragile dependency that breaks without notice.

### Decision

Replace all seven Google News feeds with direct publisher RSS, verified rather than assumed. `check_feeds.py` tests candidates and is the tool of record when a feed rots.

Verification has to include extraction, not just link cleanliness. Of 26 candidates, 19 passed — and three of the seven failures (MarketWatch, Politico, Nature) returned perfectly valid publisher links that yielded **zero** extractable text. A link-only check would have shipped all three. ESPN answered 202 and Byrdie 403; both are bot challenges that `feedparser` reports as an empty entry list rather than an error.

`fetch_from_rss` now round-robins across each topic's feeds rather than concatenating. BBC Sport returns 76 entries and Sky returns 20, so reading in order would make nearly every Sports card a BBC card. This matters beyond variety: these articles get embedded, and one publisher's house style becoming a proxy for the topic is a real way to poison a classifier.

NewsAPI is left in place. It costs nothing and starts working the day the plan is upgraded — but `newsapi_query` should not be read as live.

### Consequences

Extraction went from 0% to **98.8%** (82 of 83 on the first full run). The single failure was an FT paywall returning 403, which correctly exhausted its retry budget after three attempts rather than being refetched nightly forever.

Two Google-specific behaviours had to be removed from `fetch_from_rss`, and both would have silently corrupted data on direct feeds. Titles were stripped of everything after the last ` - `, because Google appends ` - Publisher` to every headline; on a publisher feed that rule truncates any real headline containing a dash. `source` was derived from that same suffix and defaulted to `"Google News"`; it now comes from the feed's own title.

The cost is source concentration. BBC supplies a feed to four of the seven topics and accounts for ~24% of the corpus. Round-robin caps it within each topic, but the cross-topic concentration is worth remembering when reading any per-category metric.

All 537 legacy articles were deleted rather than left to age out, since none could ever extract. That cascade destroyed 148 swipes; `swipe_events` (ADR-012) held only 7 at the time, having been added days earlier. The mechanism was correct and the timing was not — a reminder that a retention fix protects nothing retroactively.

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

### A profiled compose service will not be rebuilt by `up --build`

`docker compose up --build` skips services behind a `profiles:` key. When `enrichment` carried its own `build:` section it produced a second image that `up` never refreshed, so a freshly deployed API ran beside a week-old enrichment image. The symptom was a run printing the *previous version's log format* — code that no longer existed anywhere in the repo.

The fix is to share one image tag (`paperswap-backend:latest`) rather than build twice. The general rule: two build definitions in one compose file means two things that can drift.

The original block also carried a comment claiming it "reuses the backend image rather than defining its own" directly above a `build:` section. A comment describing intent rather than code is worse than no comment — it stops the reader from checking.

### Confirm which branch the deploy target is on

A long stretch of edits appeared to vanish: files were changed on the development machine, `git pull` on the VM reported success, and the changes were absent. The development machine was on `feature/news-labels` while the VM tracked `main`. Nothing was lost — it was in the other branch the whole time.

`git status` was clean on both, and both were legitimately up to date with their own remote. "Already up to date" answers a narrower question than it appears to.

```bash
cd ~/paperswap && git branch --show-current    # before any deploy
```

This is the same lesson as *Verify the deployed artifact* above, arriving through a different door. Worth noting it recurred **after** that section was written.

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
- **Can per-user personalisation be justified?** `user_swipes` collects implicit feedback, but with a single user any personalisation claim is statistically unsupportable. Framing it as a single-user cold-start study is more honest. ADR-012 does not change this — a longer history from 2–3 users is still not a population, and the extra signals (`dwell_ms`, `flipped`) raise the ceiling on what a single-user study can show without turning it into a multi-user result.
- **Should the classifier be replaced?** Only a labelled dataset can answer this. If a keyword scorer matches TF-IDF at a fraction of the latency on a 956 MB box, keeping it is a defensible engineering result — but it has to be measured to be claimed.
- **Are extractive bullets worse than abstractive ones here?** ADR-011 chose extractive on a hardware constraint, not on quality. Unmeasured. The comparison is a laptop job against an export, and it is the one part of the enrichment work that would produce a defensible number.
- **Does the ranker beat chronological order?** Not yet built, and the answer is only meaningful against a baseline. Random and reverse-chronological are the two that matter; precision@5 and NDCG over a chronological holdout are the metrics. Claiming a recommender works without that comparison would be the ADR-010 failure in a new place.
