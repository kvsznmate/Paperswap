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

Risks accepted: Oracle reclaims idle instances (the refresh job provides enough activity), the free ARM allowance was halved in June 2026 with no notice, and the public IP is ephemeral. The Android client now reaches the server over a Tailscale address instead, so the IP no longer matters to it — but that address is still hardcoded in `RetrofitInstance.kt`, and the phone must be on the same tailnet.

---

## ADR-004 — Deduplication by MD5 key with `ON CONFLICT DO NOTHING`

**Status:** Accepted

### Context

The same story appears across multiple feeds, and topic feeds overlap heavily — an Apple earnings story arrives via both Tech and Finance.

### Decision

`article_key = md5(normalised title + normalised url)`, `UNIQUE`, with inserts using `ON CONFLICT (article_key) DO NOTHING`.

### Consequences

Deduplication is atomic and race-free. Two threads inserting the same story concurrently produce one row with no application-level locking.

**Do not add a check-then-insert around this.** An earlier `is_article_in_db()` pre-check doubled the connection count and reintroduced the race the constraint exists to prevent; it has been removed. `save_article()` returns `(id, was_inserted)` from the INSERT's own `RETURNING`, and `tests/test_dedup.py` guards against the pre-check coming back.

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

Removes an entire class of silent failure. **The better fix is structural** — pass discrete connection parameters (`host`, `dbname`, `user`, `password`) instead of composing a URI, which makes any character safe. Connection pooling has since shipped, but the pool is still built from `DATABASE_URL`, so this remains open.

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

## ADR-011 — Weekly topic summaries outlive their sources

**Status:** Accepted

### Context

The swipe deck answers "what is there to read now". It cannot answer "what happened in Finance last week", because `purge_old_articles` deletes the evidence on a rolling window. A weekly per-topic digest fills that gap, and it is the only place any history exists.

That framing creates three problems the obvious implementation gets wrong.

**The purge destroys the input.** `refresh_pipeline` runs `fetch` then `purge_old_data` every 12 hours. For the week Mon `W` → Sun `W+6`, the Monday articles reach 7 days old at 00:00 on Monday `W+7`. Any refresh on that Monday deletes them. A summariser scheduled independently — an APScheduler cron job, say — races that purge, and losing the race is silent: the digest covers six days, writes `number_of_articles` for six days, and labels it a week. Nothing downstream can detect the discrepancy because the rows that would reveal it are gone.

**Most article descriptions are boilerplate.** `generate_short_summary` has three tiers. Tier 1 is the publisher's own blurb. Tier 2 is one of ten canned sentences keyed on a title keyword — every article mentioning `chip`/`nvidia`/`semiconductor`/`amd` receives byte-identical text about "semiconductor supply dynamics". Tier 3 is a single fixed string per topic. On a swipe card that is merely dull. Fed to a summariser reading a whole week at once, 25 copies of one sentence become the dominant theme, and the digest reports chip supply chains as the story of the week. That is a claim about the rule table in `news_fetcher.py`, not about the news. It is ADR-010's fabrication problem in prose form, and prose conceals it far better than `int(used_bytes * 0.62)` did.

**`published_at` cannot be bucketed.** It is `TEXT`, and `save_article` defaults it to the literal string `'Recently'`.

### Decision

**The summariser is a step inside `refresh_pipeline`, immediately before the purge.** Ordering is guaranteed by control flow rather than by two schedulers agreeing. It is idempotent and gated on `topics_missing_summary()`, so thirteen of every fourteen refreshes cost one indexed query.

**`PURGE_OLDER_THAN_DAYS` moves 7 → 9,** and `main.py` clamps anything lower back up with a warning. Nine days covers 7 days of week, up to 24 h of intra-day offset, and one 12 h refresh interval. `REQUEST_LOG_RETENTION_DAYS` and `SESSION_RETENTION_DAYS` default to the same variable, so all three move together and the invariant in `purge_old_request_logs` — that the UNIONed analytics panels cover one period — is preserved. `avg_session_window_days` now reports 9.

**Provenance is recorded at write time, not reconstructed.** `articles.description_source` stores `publisher` / `keyword_template` / `topic_fallback`, and the summariser reads only the first. `KEYWORD_SUMMARY_RULES` moved to module scope with `{source}` as a placeholder so `boilerplate_like_patterns()` derives from the rules themselves; retyping the strings would rot the first time one was reworded. Pre-migration rows carry `NULL` and are matched by `LIKE` instead — they are **not** backfilled to a guessed tier, for the same reason `request_logs.status_code` left its old rows null.

**Two counts, both measured.** `number_of_articles` is what the generator read. `articles_in_window` is what the topic saw before filtering. Storing only the first hides how thin a week was; storing only the second attaches a number to prose that does not describe it. The page renders "summarised from 34 of 61 articles", which doubles as a health signal on the fetcher.

**A generated summary carries `generator`, not `measured`.** ADR-010's contract is two-valued and neither value fits a sentence written by a model. So the row names what produced it — `stories:v2` (the default since the revision below), `extractive:v1` or `anthropic:<model-id>` — and when. All of them can coexist in the history.

**On failure, no row.** The generator raises rather than returning a placeholder. A gap is visible and, while the articles survive, recoverable. A placeholder becomes permanent and indistinguishable from a real summary the moment the purge runs.

**`MIN_ARTICLES_FOR_SUMMARY` (default 3) applies after filtering**, and `topic_summaries` is exempt from the article retention window (`SUMMARY_RETENTION_WEEKS=0`, keep forever).

### Consequences

Some topics have no row some weeks. Beauty and Programming are the likely absentees — narrow topics on Google News RSS, where publisher descriptions are often missing. The API returns them in `topics_missing` so a client can say "not enough coverage" rather than render a blank card. This is the system working, and it will look like a bug to anyone who has not read this record.

The extractive generator shipped first and was the default until `stories:v2` replaced it (see the revision below). It is not eloquent; every clause is derived from the input and it asserts nothing it cannot count. It exists so the window arithmetic, filtering, counts, upsert and scheduling could all be built and tested before the LLM question was settled, and so the feature degrades to something honest rather than to nothing if a key goes missing.

Retention rose by two days across three tables to protect a feature that touches one. That is the cost of a calendar week; a rolling 7-day window would have avoided it at the price of a digest nobody can date.

What this feature does **not** fix: topic assignment is still the unmeasured keyword classifier of ADR-007, so every digest is "articles the classifier called TECH". The window is ingest time, not publication time, and the API says so on every row. And summary quality itself is unevaluated — no rubric, no baseline, no blind comparison against the extractive output. The same honest position as ADR-007: it may be good, nobody has checked.

### Enforcement

`backend/tests/test_topic_summaries.py`. The count-integrity and no-placeholder checks are the ADR-010 checks in this feature's clothing. The one that matters most is the ordering check: it seeds a real week, runs the summariser, then runs the purge, and asserts the summary exists and survives. A static read of `refresh_pipeline` can be satisfied by two calls in the right order that do the wrong thing; only the behavioural check proves the week was still there to be read.

### Revision (2026-11, #53) — `stories:v2` becomes the default

**Problem.** `extractive:v1` saw only headlines and blurbs, so its digests read "N articles were collected… Recurring terms: … Representative stories: headline; headline". They said what words were frequent, not what happened. Meanwhile ADR-012's enrichment job was already storing two better inputs per article that the summariser never selected: TextRank `summary_bullets` from the body, and a MiniLM `embedding` of `title. description`.

**Decision.** `stories:v2`, offline, deterministic, no new dependency:

1. `get_articles_for_week` also returns `summary_bullets` (parsed list) and `embedding` (raw `bytes`). Those rows reach only the summariser; a test asserts `main.py` never calls it, because `BYTEA` is not JSON-serialisable.
2. Articles are grouped into stories by **average-linkage** agglomerative clustering on cosine similarity, stopping when no pair of groups averages `SUMMARY_STORY_SIMILARITY` (default **0.45**). Average linkage rather than single linkage, because single linkage chains (A~B, B~C ⇒ A, C together) and glues one topic's distinct stories into a blob.
3. Stories are ranked by distinct outlets, then size.
4. Each line is the **first bullet** of the member closest to the story centroid, among the members that have bullets. Bullets are kept in document order and news is written lede first, so this is normally what happened. A story with no enriched member uses its headline. Articles without a usable embedding become single-article stories.
5. Output: an opening line with the exact count, then up to 5 stories covered by 2+ articles (topped up to 3 with single-article stories), each as `• <sentence> (5 articles · Reuters, BBC and 2 others)`. The text quotes extracted sentences verbatim and adds only counts. A week with no repeated story says so rather than calling anything "most covered".

`number_of_articles` / `articles_in_window` keep their meaning. The boilerplate filter stays on, because the embedding includes the description, and twenty copies of one canned description would pull unrelated articles into one "story" for the same reason they used to dominate v1's term counts.

It is pure Python (`struct` + `operator.mul`), not numpy. The API process otherwise never imports numpy, and this runs in its scheduler, so numpy would stay resident on a 956 MB box for a computation that takes tens of milliseconds once a week.

`SUMMARY_GENERATOR=anthropic` now feeds the model the story lines (coverage, headline, extracted sentence) instead of raw rows. Thinking is disabled: current models think by default, thinking is billed as output and counts against `max_tokens`, and rewording a ranked list needs none. Any `stop_reason` other than `end_turn` is treated as a failure. A failed call falls back to `stories:v2`. That is not a placeholder: it is a complete digest whose `generator` column says `stories:v2`. The API's per-row `basis` sentence is now chosen per generator, so v1 rows in the history still describe how v1 worked.

**Cost.** The LLM path has a fixed worst case of under $1 a year, set by caps in code, not by how much news arrives. With Haiku 5.5 at $0.10 / $0.50 per million input / output tokens (verified 2026-11):

| per call | cap | worst-case cost |
|---|---|---|
| input | story list clipped to 8,000 chars (headline 200, lede 300); ~9.5k chars of prompt. Even at 1 char per token: | $0.00095 |
| output | `max_tokens` 800, thinking disabled | $0.00040 |

That is 7 topics × 52 weeks = 364 calls, about **$0.49 a year** in the worst case. A typical prompt is about 3k tokens, which puts the expected cost nearer $0.15. A topic-week is generated at most once, because `topics_missing_summary()` skips rows that exist and a failed call falls back to a stored `stories:v2` row instead of retrying. The only extra calls are manual `python summarizer.py` regenerations, at about $0.001 each. Each call logs its measured `usage` token counts. The bound assumes Haiku: a larger `SUMMARY_MODEL` multiplies it by that model's price. A monthly spend limit on the Anthropic key is the backstop if code or prices change.

**Threshold.** The ticket guessed 0.6. Measured with the production model (Xenova `all-MiniLM-L6-v2` int8, the same `title. description` input enrichment embeds) on 21 hand-written articles: 6 multi-article stories written as deliberate paraphrases, 5 same-subject-different-event near misses (e.g. *Fed holds rates* vs *Fed governor resigns*; *Nvidia beats earnings* vs *Nvidia faces export curbs*), and unrelated singletons:

| pairs | n | min | mean | max |
|---|---|---|---|---|
| same story | 10 | 0.508 | 0.589 | 0.788 |
| same subject, different event | 12 | 0.139 | 0.287 | 0.407 |
| unrelated | 188 | −0.108 | 0.066 | 0.334 |

| threshold | 0.35 | 0.40 | 0.45 | 0.50 | 0.55 | 0.60 | 0.65 |
|---|---|---|---|---|---|---|---|
| stories fully grouped (of 6) | 5 | 6 | 6 | 6 | 3 | 2 | 1 |
| groups mixing two events | 1 | 0 | 0 | 0 | 0 | 0 | 0 |

At 0.6, four of six stories would have been reported as separate stories. 0.45 is near the middle of the clean 0.41–0.51 gap. The weakest true pairs are short, vocabulary-disjoint rewrites (*Apple unveils iPhone 18…* ~ *New iPhones arrive…* = 0.508; *Federal Reserve leaves rates unchanged…* ~ *US central bank pauses again* = 0.514). Real wire copy usually shares more named entities than these, so real paraphrases should score higher, not lower.

**Not yet done:** the same check on a real exported week. Hand-written fixtures can't show how dense a real topic-week's same-subject coverage is. Run `docker compose exec news-cards-backend python summarizer.py --explain --week <Monday>` on the VM. It prints every multi-article group at thresholds 0.35–0.60 and the digest at the configured value, without writing anything. Then record the verdict here.

**Enforcement.** `backend/tests/test_stories.py` uses synthetic embeddings built from an exact Gram matrix, so every similarity is chosen rather than whatever a model produces. It covers paraphrases grouping where v1's word overlap does not, near misses staying apart, no chaining, ranking, lede selection and fallback, exact counts, the Anthropic fallback, and the database round trip.

---

## ADR-012 — Enrichment runs off-process; swipes are kept forever in `swipe_events`

**Status:** Accepted

### Context

Card backs need more than the one- or two-sentence feed description: an extractive summary needs the article body, and a future ranker needs an embedding per article. Both need models, and the box has 956 MB of RAM shared with Postgres and the API.

Separately, `user_swipes` is purged with its article (`ON DELETE CASCADE`, 9-day window) so the analytics panels stay aligned (ADR-010, ADR-011). That is correct for analytics and fatal for a recommender: a swipe is the one thing in the database that cannot be regenerated.

### Decision

**Enrichment is a one-shot process, not an APScheduler job.** `backend/enrichment.py` extracts the body with trafilatura, ranks sentences with TextRank into `summary_bullets`, and embeds with an int8 ONNX export of all-MiniLM-L6-v2 (~150 MB peak RSS; torch alone would cost ~350 MB on import). `run_enrichment.sh` runs it from cron every 6 hours through the `enrichment` compose service; it exits when done, so the model is never resident alongside the API. A `MemAvailable` guard refuses to start on a starved box.

**Resumable by construction.** `enriched_at` is set only after every stage succeeds and is the resume point (`WHERE enriched_at IS NULL`); `enrich_attempts` caps retries so a permanently unreadable page stops after `ENRICH_MAX_ATTEMPTS`.

**Feeds must link to publishers.** Google News RSS returns redirect wrappers that server-side extraction cannot follow, so every topic moved to publisher feeds (`news_fetcher.TOPIC_FEEDS`). `check_feeds.py --extract` verifies a feed actually yields text.

**Swipes are written twice, in one transaction.** `user_swipes` keeps feeding analytics. `swipe_events` has no foreign key, is never purged, and snapshots title, description, category and source at swipe time, so a row stays trainable after its article is gone. It stores text, not vectors, so the whole history can be re-embedded when the model changes. `dwell_ms` and `flipped` are nullable and never backfilled: NULL means "not measured", not zero.

### Consequences

The API process carries no ML dependencies at runtime (they share the image, but are imported lazily by the job only). Card backs can be empty for up to a refresh-plus-cron interval after an article arrives.

`swipe_events` is the only irreplaceable table and currently has **no backup** — it lives on one free-tier volume. A scheduled `pg_dump` / local archive is the open follow-up (see [DATABASE_GUIDE.md](DATABASE_GUIDE.md)).

Bullet quality and the ranker are both unevaluated; see Open questions.

---

## ADR-013 — Paper design: the headline leads, a photo only when it is real

**Status:** Accepted (2026-11, #13)

### Context

The first UI was a dark, photo-led card: the hero image took all spare height (`NewsCard.kt`, `weight(1f)`), and the headline sat below it. A 69-card deck from a local refresh (17 Nov 2026, RSS only) showed that the photo is the weakest part of most cards:

| hero image | cards |
|---|---|
| real publisher photo | 26 |
| BBC 240 px thumbnail, shown 4–9× enlarged | 21 |
| stock filler from `TOPIC_FEEDS[...]["images"]` | 22 |

The stock filler is worse than no picture: the FAA ground-stop story showed a laptop reading "REVENUE". At least 12 of the 69 cards also showed a canned topic blurb ("Match results, transfer moves…") as if it summarised the story. The weekly-summaries tab opened on seven two-letter circles and nothing to read.

### Decision

A light "paper" design that follows the phone's dark mode, on Android and the web preview alike:

- **Cards lead with a serif headline.** A photo appears only when the publisher sent one; without one, the headline grows and the blurb gets more lines, so a type-only card is a designed state. A canned blurb is hidden.
- **The feed says which is which.** `/api/v1/feed` gains `image_is_stock` (the URL is in `_STOCK_IMAGES`) and returns `description_source`, which `articles` already stored. A NULL source (rows older than the column) stays null, and the client shows that blurb (invariant 5).
- **BBC thumbnails are upgraded** from `/ace/standard/240/` to `/976/` (`upgrade_image_url`), at fetch time and again at serve time for rows stored before the change.
- **Topic colours stay server-driven** (ADR-006). The server's accents were tuned for the dark UI, so the clients adjust each one toward black on paper (or white at night) until it reaches 4.5:1 against the card: `readableOn()` in Android, `readable()` on the web.
- **Bottom navigation** (Deck · This week · Saved) replaces the segmented pill that took 118 dp above the card.
- **The week is one edition**: every topic's digest for one week from `GET /api/v1/summaries`, one line per stories:v2 story with its coverage under it, both article counts per topic, and a line for topics with no digest.
- **Save for later is local only.** A copy of headline, link and source on the phone (`LocalStore`, SharedPreferences) or in the browser (`localStorage`). It is not a swipe: no row in `swipe_events`, and the deck does not advance. A copy, not an id, because the server deletes the article after 9 days.
- **The topic filter is local too** (part of #19). It sends the existing `?categories=` parameter; every topic selected is stored as "no filter", so a topic added later is not silently excluded.

Headlines use Noto Serif: it is Android's `FontFamily.Serif`, so the APK gains no font file (release APK 1.16 MB), and the web preview loads the same face from Google Fonts.

### Consequences

- About a third of cards (the stock ones) now carry no image at all. That is the intent, not a regression.
- The web preview now sends `flipped` with each swipe, because a card can be turned over there too. It still sends no `dwell_ms`: it does not measure one.
- `GET /api/v1/summaries/{topic}` has no client left. It stays, as the only per-topic history endpoint.

### Enforcement

`backend/tests/test_paper_feed.py`: the URL upgrade, `image_is_stock`, `description_source` passed through and never backfilled, and no embedding in the payload.

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

The connection pool now exists, but `init_pool()`/`init_db()` still have no retry. Adding one would log the failure, keep the process alive, and let a `/health` endpoint return `503`. Open.

### The database survives container removal

`docker compose down` removes containers and networks but **not** named volumes. Article data persisted through roughly a dozen recreates during one debugging session. Only `down -v` destroys it.

---

## Open questions

- **Is the topic taxonomy well-posed?** An Apple earnings story is genuinely both Tech and Finance. Single-label classification may be the wrong frame; multi-label with a primary topic for the badge is worth evaluating.
- **Are the weekly summaries any good?** ADR-011 ships them unevaluated. The cheapest answer is a handful of weeks scored blind against the `extractive:v1` baseline — which, being free and deterministic, is a fair control.
- **Can per-user personalisation be justified?** `user_swipes` collects implicit feedback, but with a single user any personalisation claim is statistically unsupportable. Framing it as a single-user cold-start study is more honest.
- **Should the classifier be replaced?** Only a labelled dataset can answer this. If a keyword scorer matches TF-IDF at a fraction of the latency on a 956 MB box, keeping it is a defensible engineering result — but it has to be measured to be claimed.
- **Are extractive bullets worse than abstractive ones here?** ADR-011 chose extractive on a hardware constraint, not on quality. Unmeasured. The comparison is a laptop job against an export, and it is the one part of the enrichment work that would produce a defensible number.
- **Does the ranker beat chronological order?** Not yet built, and the answer is only meaningful against a baseline. Random and reverse-chronological are the two that matter; precision@5 and NDCG over a chronological holdout are the metrics. Claiming a recommender works without that comparison would be the ADR-010 failure in a new place.
