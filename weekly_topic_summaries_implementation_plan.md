# Weekly Topic Summaries — Implementation Plan

_Drafted: 2026-09-01 · Status: proposed, not started_

A per-topic weekly digest: for each topic in the catalogue, one short paragraph describing what that week's news was about, plus how many articles it was drawn from. Surfaced as a summary page in the client.

Target shape, in the user's words: a table with `topic`, `summary`, `number_of_articles`.

---

## 0. What already exists (read before planning changes)

| Thing | Where | Relevant fact |
|---|---|---|
| Article store | `backend/database.py` → `articles` | Postgres 16. Columns: `article_key`, `title`, `description`, `source`, `published_at`, `category`, `image_url`, `url`, `created_at`. |
| Topic catalogue | `database.CATEGORIES` → `categories` table | 7 slugs: TECH, FINANCE, SPORTS, POLITICS, PROGRAMMING, SCIENCE, BEAUTY. Code is source of truth, synced into the table on every boot (ADR-006). |
| Classification | `news_fetcher.py` | Weighted keyword scorer. **Accuracy has never been measured** (ADR-007). |
| Refresh cycle | `main.refresh_pipeline()` | `fetch_and_sync_news_to_db()` then `purge_old_data(...)`. Runs on startup and every `REFRESH_INTERVAL` hours (default 12). |
| Retention | `main.PURGE_OLDER_THAN_DAYS` | **Articles are deleted after 7 days.** |
| Migrations | `database.init_db()` | No Alembic. `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, run on every boot. |
| Provenance rules | ADR-010 | Every numeric field is either measured or explicitly null-with-a-reason. Nothing is estimated. This constrains the design below. |

---

## 1. The constraint that decides everything: the 7-day purge

`purge_old_articles(days=7)` runs inside `refresh_pipeline()`, every 12 hours, deleting anything with `created_at` older than 7 days.

Consequences:

1. **The summary must be generated before its source articles are deleted.** After the purge, the summary row is the only surviving record of that week. There is no re-run, no backfill, no recovery.
2. **A calendar week (Mon–Sun) is barely inside the window.** For the week Mon `W` → Sun `W+6`, the Monday articles hit 7 days old at 00:00 on Monday `W+7`. A refresh firing anywhere on that Monday deletes them. If the summariser runs after that refresh, it silently summarises 6 days and writes `number_of_articles` for 6 days under a label saying 7.
3. **`number_of_articles` becomes unverifiable once the purge runs.** Nothing downstream can ever recompute it. It has to be right when written.

Three ways to resolve this. Pick one before writing code.

**Option A — summarise inside the pipeline, before the purge, and widen retention. (Recommended.)**
Call `generate_weekly_summaries()` in `refresh_pipeline()` on the line above `db.purge_old_data(...)`. Ordering is then guaranteed by control flow rather than by cron timing. Raise `PURGE_OLDER_THAN_DAYS` from 7 to **9** so a full Mon–Sun week is still present whenever Monday's first refresh lands: 7 days of week + up to 24 h of intra-day offset + one 12 h refresh interval ≈ 8.5 days.

> ⚠️ Knock-on: `REQUEST_LOG_RETENTION_DAYS` and `SESSION_RETENTION_DAYS` both **default to** `PURGE_OLDER_THAN_DAYS`. Raising it moves all three together, which preserves the invariant those comments care about (the union'd analytics panels must cover the same period). Do not pin one of the three to 7 and leave the others at 9 — that reintroduces exactly the distortion `purge_old_request_logs`'s docstring warns about. Also note `avg_session_window_days` in the telemetry payload will start reporting 9.

**Option B — rolling 7-day window instead of a calendar week.**
Summarise "the trailing 7 days as of now" rather than "week 36". Needs no retention change at all, and no race. Costs legibility on the summary page ("the week of Aug 31" reads better than "the 7 days ending Sep 8") and makes week-over-week comparison fuzzier. Store `window_start`/`window_end` timestamps instead of a week number.

**Option C — make the purge summary-aware.**
`purge_old_articles` refuses to delete an article belonging to a completed week that has no summary row yet. Strongest guarantee, most code, and it introduces a way for the disk to fill if the generator stays broken. Only worth it if summaries become load-bearing.

The rest of this plan assumes **Option A**.

---

## 2. Schema

### 2.1 Why the proposed three columns aren't enough

`(topic, summary, number_of_articles)` has no time dimension, which means:

- one row per topic, forever — no history, no week-over-week page;
- a re-run can't distinguish "update this week's row" from "overwrite last week's"; and
- the number has no period attached, so it can't be interpreted.

Adding a week key fixes all three and costs one column.

### 2.2 Table

Goes in `init_db()`, **after** the `categories` sync loop so the FK target exists.

```sql
CREATE TABLE IF NOT EXISTS topic_summaries (
    id                  SERIAL PRIMARY KEY,
    topic               TEXT NOT NULL REFERENCES categories (slug),
    week_start          DATE NOT NULL,            -- Monday, UTC
    week_end            DATE NOT NULL,            -- Sunday, UTC, inclusive
    summary             TEXT NOT NULL,
    number_of_articles  INTEGER NOT NULL CHECK (number_of_articles > 0),
    top_sources         TEXT[],                   -- measured; nullable
    generator           TEXT NOT NULL,            -- 'extractive:v1' | 'anthropic:<model-id>'
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (topic, week_start)
);

CREATE INDEX IF NOT EXISTS idx_topic_summaries_week
    ON topic_summaries (week_start DESC, topic);
```

Column rationale:

- **`REFERENCES categories (slug)`** — the catalogue is server-driven (ADR-006). The FK stops a typo'd or retired slug entering, the same way `normalize_category()` protects `articles`.
- **`UNIQUE (topic, week_start)`** — makes the write idempotent via `ON CONFLICT DO UPDATE`. The scheduler runs every 12 h; without this, a Monday with two refreshes produces two rows.
- **`number_of_articles`** — the count of rows **actually passed to the generator**, not the count matching the window. If the generator skips articles (empty description, over a token budget), this must reflect the skip or the number describes a different set than the summary does. ADR-010 territory.
- **`generator` + `generated_at`** — ADR-010 has no "estimated" state, and a generated sentence is not a measurement. It can't be given `measured: true`, so instead it names what produced it and when. A summary from `extractive:v1` and one from an LLM are different kinds of object and the row should say which it is.
- **`top_sources`** — genuinely measured (a `GROUP BY source` on the same window). Cheap, and gives the summary page something factual to sit next to the prose.

### 2.3 Retention

**Summaries are not purged with articles.** Outliving their sources is the point — the table is the long-term memory the 7-day window doesn't provide.

Every other table here has a stated retention, so state one: `SUMMARY_RETENTION_WEEKS`, default **0 = keep forever**. At 7 topics × 52 weeks × ~1 KB, a decade is under 4 MB. If a cap is ever wanted, it belongs in `purge_old_data()` alongside the others.

---

## 3. Step-by-step build

Steps 1–5 are the minimum shippable feature. Steps 6–9 make it good.

### Step 1 — Fix the week definition (decision, not code)

- **Window column: `created_at`, not `published_at`.** `published_at` is `TEXT` and `save_article` defaults it to the literal string `'Recently'`. It cannot be compared or bucketed.
- **Therefore the honest label is "articles ingested during week N", not "news published during week N."** These differ, usually by hours but sometimes more. Say so in the API response and on the page. Do not quietly call it a publication week.
- **Boundary:** ISO week, Monday 00:00 UTC → Sunday 23:59:59.999 UTC. Postgres `date_trunc('week', ...)` already starts on Monday.
- **Always query half-open:** `created_at >= week_start AND created_at < week_start + INTERVAL '7 days'`. Never `BETWEEN` with a 23:59:59 upper bound — that drops the final second and the bug is invisible until it isn't.
- **Most recent completed week, in Python:**
  ```python
  def last_completed_week_start(today: date) -> date:
      """Monday of the most recently *finished* Mon–Sun week."""
      return today - timedelta(days=today.weekday() + 7)
  ```
  Correct on every weekday: Monday → previous Monday; Wednesday → the Monday 9 days back.

### Step 2 — The measured half: read the week

In `database.py`:

```python
def get_articles_for_week(topic: str, week_start: date) -> list
def count_articles_for_week(topic: str, week_start: date) -> int
def get_top_sources_for_week(topic: str, week_start: date, limit: int = 5) -> list
def weeks_missing_summaries(oldest_week: date) -> list[tuple[str, date]]
```

Pure SQL, no generation, no network. This half is fully testable without an API key and is where correctness actually lives.

`weeks_missing_summaries()` is the idempotency gate — it returns `(topic, week_start)` pairs that have articles in the window but no `topic_summaries` row. When it returns empty, the whole job is a no-op, which is what happens on 13 of every 14 refreshes.

### Step 3 — The derived half: `backend/summarizer.py`

One interface, two backends, selected by `SUMMARY_GENERATOR`:

```python
def summarise(topic: str, articles: list[dict]) -> tuple[str, str]:
    """Return (summary_text, generator_id). Raises on failure — never returns
    a placeholder, because a placeholder in this table is indistinguishable
    from a real summary once the source articles are purged."""
```

**`extractive:v1` — build this first.** Deterministic, no network, no key, no cost. Roughly: term-frequency over titles with a stopword list, pick the top 3–5 headlines weighted for source diversity, assemble a templated paragraph ("Coverage centred on X and Y, across N articles from A, B and C."). It is not eloquent. It is honest, free, and it makes the whole pipeline end-to-end testable on day one — which means Steps 1, 2, 4 and 5 can be verified before any LLM question is settled.

**`anthropic:<model-id>` — the quality path.** One call per topic per week.

- Prompt gets `title`, `description`, `source` for each article — never full text (not stored) and never the URL (invites hallucinated citation).
- Instruct: 3–5 sentences, only what appears in the supplied list, name recurring stories, no invented figures, no speculation about causes.
- Volume: `ARTICLES_PER_CATEGORY=12` × 2 refreshes/day × 7 days = 168 candidates per topic per week before dedup; realistically 30–80 unique. At ~60 tokens each that's ~5 k input tokens per topic, ~35 k for all seven, **once a week**. The cost is negligible; a per-run token budget guard is still worth having so a feed anomaly can't turn into a surprise bill.
- Pin the model ID in config and record it in `generator`. Check current IDs against the Anthropic docs rather than hardcoding from memory — they change.
- Add `anthropic` (or plain `requests`, which is already a dependency) to `requirements.txt`. Note the box is 956 MB with a 2 GB swap file; a local model is not an option.

**On failure: no row.** Log a warning, leave the gap, let the next refresh retry. The gap is recoverable while the articles survive, and it is visible. A fabricated placeholder is neither.

**Floor:** `MIN_ARTICLES_FOR_SUMMARY`, default 3. Below it, write nothing — a "weekly summary" of two articles is noise wearing a label that implies coverage. The `CHECK (number_of_articles > 0)` constraint backstops this.

### Step 4 — Persist

```python
def save_topic_summary(topic, week_start, week_end, summary,
                       number_of_articles, top_sources, generator) -> None
```

```sql
INSERT INTO topic_summaries (...)
VALUES (...)
ON CONFLICT (topic, week_start) DO UPDATE SET
    summary            = EXCLUDED.summary,
    number_of_articles = EXCLUDED.number_of_articles,
    top_sources        = EXCLUDED.top_sources,
    generator          = EXCLUDED.generator,
    generated_at       = NOW()
```

Use the existing `db_cursor(commit=True)` context manager. Do not open raw connections — the pool exists because that leaked connections until Postgres fell over.

### Step 5 — Wire into the pipeline

```python
def refresh_pipeline():
    fetch_and_sync_news_to_db()
    generate_weekly_summaries()      # BEFORE the purge. Ordering is the whole point.
    db.purge_old_data(
        articles_days=PURGE_OLDER_THAN_DAYS,
        request_logs_days=REQUEST_LOG_RETENTION_DAYS,
        sessions_days=SESSION_RETENTION_DAYS,
    )
```

- `generate_weekly_summaries()` calls `weeks_missing_summaries()` first and returns immediately when there's nothing to do. Cost on a normal tick: one indexed query.
- Wrap the whole thing in try/except and log. A summariser failure must not prevent the purge — the disk matters more than the digest.
- Set `PURGE_OLDER_THAN_DAYS=9` in `.env` (see §1) and update the comment in `.env.example` to explain that the extra two days exist to protect the weekly summary window. Somebody will otherwise "tidy" it back to 7.
- **Do not** add a separate APScheduler cron job for this. A cron job races the purge; a pipeline step cannot.

### Step 6 — API

Mirror the existing endpoint conventions exactly — `slowapi` limits, `JSONResponse`, `require_admin_key` on anything that mutates.

| Method | Path | Auth | Limit | Notes |
|---|---|---|---|---|
| GET | `/api/v1/summaries` | public | 60/min | Latest completed week, all topics. Optional `?week_start=YYYY-MM-DD`. |
| GET | `/api/v1/summaries/{topic}` | public | 60/min | History for one topic. `?weeks=N` (default 8, max 52). 404 on unknown slug via `clean_category_filter`. |
| POST | `/api/v1/summaries/generate` | `require_admin_key` | 2/hour | 202 + `BackgroundTasks`, exactly like `/api/v1/cards/refresh`. Optional `?week_start=` for backfill. POST because it mutates and costs money. |

Decorate responses the way `_decorate_articles` does — attach `category_label` and `accent_color` from `CATEGORIES` so the client never hardcodes a colour map (ADR-006). Include `week_start`, `week_end`, `number_of_articles`, `generator`, `generated_at`, and a `basis` field spelling out `"articles ingested in this window; ingest time, not publication time"`.

### Step 7 — Client

- **Android app** (`android/`, see `paperswap_android_implementation_plan.md`): a Summary tab. One card per topic in `sort_order`, accent-coloured from the API, showing the week range, the summary paragraph, and "from N articles". Tapping a topic filters the swipe deck to it.
- **`templates/mobile_preview.html`**: same content, reachable at `/summaries`, so the feature is demoable before the app ships.
- Show `generated_at` and the generator on the card, or in a detail view. It costs one line and it means nobody has to guess whether a paragraph was written by a model.

### Step 8 — Tests (`backend/tests/test_topic_summaries.py`)

Follow the pattern in `test_telemetry_provenance.py` — that suite exists because a number was once wrong in a way that mattered, and the same risk applies here.

1. **Week maths** — `last_completed_week_start()` for all 7 weekdays, plus a year boundary and an ISO week 53 year.
2. **Half-open window** — an article at `week_end 23:59:59.9` is included; one at `week_start - 1µs` is not.
3. **Count integrity** — `number_of_articles` equals `len(articles_passed_to_generator)`, not the raw window count, when the generator skips rows. The ADR-010 check.
4. **Idempotency** — running the job twice yields one row per `(topic, week)`, updated rather than duplicated.
5. **Ordering** — seed articles at day 8, run `refresh_pipeline()`, assert the summary row exists *and* the articles are gone. This is the regression test for §1 and the one most likely to save the feature.
6. **Failure writes nothing** — generator raises → zero rows, warning logged, no placeholder.
7. **Floor** — a topic with 2 articles produces no row.
8. **FK** — an unknown topic slug is rejected.

### Step 9 — Docs

- **`docs/ARCHITECTURE.md` → ADR-011**, "Weekly topic summaries outlive their sources". Record: the purge race and why the summariser sits inside the pipeline; why the retention window moved 7 → 9 and that all three retention values move together; why `created_at` and not `published_at`; why a generated summary carries `generator` instead of `measured`.
- **`docs/DATABASE_GUIDE.md`** — add `topic_summaries` to the table reference.
- **`.env.example`** — new variables, with the reasoning comments this file already uses.
- **`PROJECT_STATUS.md`** — move to milestones once live. Its "Current State" paragraph still says SQLite + Pillow and is out of date; worth fixing in the same pass.

---

## 4. New environment variables

```ini
# --- Weekly topic summaries ---

# Which generator produces the summary text.
#   extractive:v1  - deterministic, offline, free. No API key needed.
#   anthropic      - LLM-written. Requires ANTHROPIC_API_KEY.
SUMMARY_GENERATOR=extractive:v1

# Required only when SUMMARY_GENERATOR=anthropic.
ANTHROPIC_API_KEY=

# Model ID, recorded verbatim in topic_summaries.generator so a row always
# names what wrote it. Verify against current Anthropic docs before setting.
SUMMARY_MODEL=

# Topics with fewer than this many articles in the week get NO row. A weekly
# digest built from two articles is noise with an authoritative label on it.
MIN_ARTICLES_FOR_SUMMARY=3

# Weeks of summary history to keep. 0 = forever (the default and the intent:
# these rows are the only record of a week once its articles are purged).
SUMMARY_RETENTION_WEEKS=0

# NOTE: PURGE_OLDER_THAN_DAYS must be >= 9 for calendar-week summaries.
# At 7, the Monday of the summarised week is already purge-eligible when the
# Monday job runs, and the summary silently covers 6 days. See ADR-011.
```

---

## 5. Known limitations — write these down before shipping

This project's own standard (ADR-010) is that a number travels with its provenance. The same applies to prose.

1. **Topic assignment is unmeasured.** Every summary is "articles the keyword classifier assigned to TECH", not "the week's tech news". ADR-007 states the classifier's accuracy is unknown and that Tech↔Finance reassignment cannot currently fire. The summary inherits that error and, being prose, hides it better than a badge does.
2. **Ingest time ≠ publication time.** `created_at` is when Paperswap saw the article. A Sunday-night story fetched Monday 06:00 lands in the following week.
3. **Summary quality is unevaluated.** No rubric, no baseline, no human comparison. Same honest position as ADR-007: it may be good, nobody has checked. If it matters, the evaluation is a handful of weeks scored blind against the extractive baseline.
4. **`number_of_articles` is unverifiable after 7 (or 9) days.** No test and no audit can recompute it once the sources are purged. It is right at write time or never.
5. **Coverage is bounded by the fetcher, not by the world.** `ARTICLES_PER_CATEGORY=12` per refresh. The summary describes what Paperswap ingested, which is a sample of a sample.
6. **Single-user caveat.** As with personalisation (ADR "Open questions"), any claim about what these summaries reveal is, for now, a claim about one person's feed.

---

## 6. Open decisions

1. **Option A, B or C from §1?** A is recommended; B is the zero-retention-change escape hatch if moving `PURGE_OLDER_THAN_DAYS` feels risky.
2. **LLM or extractive at launch?** Suggestion: ship extractive, get the pipeline correct and tested, then flip `SUMMARY_GENERATOR` once. The row records which wrote it, so both can coexist in history.
3. **One summary per topic, or a cross-topic "week in review" too?** The latter is one more row with `topic = '__ALL__'`, but that breaks the FK to `categories`. If it's wanted, it needs a nullable `topic` and a partial unique index instead.
4. **Backfill?** The current DB holds ~7 days, so at most one completed week is recoverable — and only if the job runs before the next purge. Everything before that is already gone. Worth running `POST /api/v1/summaries/generate` manually the day this ships.
5. **Should the summary page be public or admin-gated?** The feed is public; the analytics dashboard is not. Summaries feel like product, not operations, so public — but it's a per-topic aggregate of what the service ingests, which is closer to operational data than a single card is.

---

## 7. Suggested order of work

| # | Work | Depends on | Ships something |
|---|---|---|---|
| 1 | §1 decision + §2 schema in `init_db()` | — | no |
| 2 | Step 2 read functions + their tests | 1 | no |
| 3 | `extractive:v1` generator | — | no |
| 4 | Step 4 persist + Step 5 pipeline wiring | 1–3 | **yes — rows appear** |
| 5 | Step 8 tests 1–6 | 4 | no |
| 6 | Step 6 API endpoints | 4 | **yes — data reachable** |
| 7 | Step 7 client page | 6 | **yes — feature visible** |
| 8 | Anthropic generator behind the env flag | 4 | **yes — quality jump** |
| 9 | Step 9 docs + ADR-011 | all | no |

Rows exist by item 4, the feature is visible by item 7, and the LLM is an independent upgrade that can land whenever.
