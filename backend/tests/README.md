# Backend test suites

Standalone scripts, not a pytest suite. Each prints `PASS` / `FAIL` per check and
exits non-zero on failure, so they work equally well by hand or in CI.

**CI runs all of them** on every PR into `dev` or `main` (`.github/workflows/ci.yml`),
against a fresh Postgres 16. Any new file named `tests/test_*.py` is picked up
without editing the workflow, as long as it exits non-zero when a check fails.

The image does not include the test-only dependencies (`requirements-dev.txt`:
httpx, which FastAPI's TestClient needs, and ruff). Install them once per
container before running the suites:

```bash
docker compose exec news-cards-backend pip install -r requirements-dev.txt
```

Run them from inside the backend container:

```bash
docker compose exec news-cards-backend python tests/test_classifier.py
docker compose exec news-cards-backend python tests/test_pool.py
docker compose exec news-cards-backend python tests/test_live_pg.py
docker compose exec news-cards-backend python tests/test_telemetry_provenance.py
docker compose exec news-cards-backend python tests/test_admin_auth.py
docker compose exec news-cards-backend python tests/test_api_security.py
docker compose exec news-cards-backend python tests/test_swipe_logging.py
docker compose exec news-cards-backend python tests/test_dedup.py
docker compose exec news-cards-backend python tests/test_topic_summaries.py
docker compose exec news-cards-backend python tests/test_stories.py
docker compose exec news-cards-backend python tests/test_public_limits.py
docker compose exec news-cards-backend python tests/test_load_shedding.py
docker compose exec news-cards-backend python tests/test_paper_feed.py
```

Or all of them:

```bash
docker compose exec news-cards-backend sh -c 'for t in tests/test_*.py; do echo "== $t"; python "$t" || exit 1; done'
```

| File | Needs Postgres | What it guards |
|---|---|---|
| `test_classifier.py` | no | Topic classification relabels only on a clear keyword lead (title hits count double, ties and narrow leads keep the query's label), every keyword can actually match, `normalize_category` / `clean_category_filter` reduce untrusted input to catalogue slugs, and the `article_key` recipe stays frozen |
| `test_pool.py` | no | Connection lifecycle: no leak on any error path, bounded acquisition, thread-safe init |
| `test_live_pg.py` | yes | The same guarantees against a real server, incl. the 500-bad-swipes scenario |
| `test_telemetry_provenance.py` | yes | No fabricated metrics; every field declares `measured` |
| `test_admin_auth.py` | yes | The /analytics gate, session cookies, CSRF posture, fail-closed behaviour |
| `test_api_security.py` | yes | Safe methods, admin auth, input validation, rate limits |
| `test_swipe_logging.py` | yes | Swipes written once, charts still complete, real status codes logged, request logs buffered off the request path and bounded by retention |
| `test_dedup.py` | yes | `save_article` reports whether it inserted; no read-then-write pre-check; concurrent inserts of one key produce exactly one winner |
| `test_public_limits.py` | yes | Global swipe/new-session budgets return 503 + Retry-After; one stored swipe per session per article; heartbeat field limits; gzip and Cache-Control; feed served from memory; one cold-start fetch per topic selection per cooldown |
| `test_load_shedding.py` | yes | The global request ceiling returns 503 + Retry-After past its limit, sheds before routing and logging, and lets admin requests through without spending it; `API_DOCS_ENABLED=false` removes the docs; every `UVICORN_*` variable in the Dockerfile and compose file is a real uvicorn option |
| `test_topic_summaries.py` | yes | Week arithmetic and the half-open UTC window; boilerplate descriptions never reach the generator; `number_of_articles` describes what was read and `articles_in_window` what was seen; no placeholder row on failure; the summariser runs before the purge and its output outlives the articles |
| `test_stories.py` | yes (section 7 only) | `stories:v3`: paraphrases with no shared words group by embedding, near-miss stories stay apart, average linkage does not chain, distinct outlets outrank size, each line is the first sentence of the most central article's publisher-written description (headline otherwise; a canned or NULL-sourced blurb never), every article lands in exactly one story, a failed or truncated Anthropic call falls back to `stories:v3`, and the week query (which carries BYTEA) never reaches a route |
| `test_related.py` | yes (sections 2-4) | Related coverage (ADR-015): other publishers only, most similar first, capped, thresholded, within the time window; NULL vs `[]`; writes only changes; the feed serves headline + publisher + link and drops purged ids; no `full_text`/`summary_bullets` column and no page fetching; robots.txt obeyed with RFC 9309 status rules |
| `test_paper_feed.py` | yes | The feed fields behind the Paper cards (ADR-013): BBC 240 px thumbnails served at 976 px, stock filler flagged `image_is_stock`, `description_source` passed through as stored and a NULL never backfilled, and no embedding in the payload |

## Notes

**They write to the database they connect to.** Each seeds a fixture article and
records swipes. Point `DATABASE_URL` at a scratch database if you do not want
production rows; the volumes are small (tens of rows) but they are real.

**`test_topic_summaries.py` computes its fixture weeks 12 weeks back.** Its
count checks compare against fixture-only totals, so a live article inside a
fixture window makes them all wrong -- and wrong in a way that reads as a
filtering bug rather than as contamination. The only requirement is a week older
than `PURGE_OLDER_THAN_DAYS`; 12 weeks is just comfortable headroom, and
`require_empty_week()` asserts each window really is empty rather than trusting
it. The suite also calls `purge_old_articles()` once, which is the same call the
scheduler makes every 12 hours, and it never deletes a summary for a week it did
not create.

**`test_api_security.py`, `test_admin_auth.py` and `test_swipe_logging.py` set
`ADMIN_API_KEY` themselves** and reload `main`, so they do not depend on your
`.env`.

**They bypass `lifespan()`** and open the pool directly. That is deliberate:
running `lifespan()` would trigger a real fetch of every publisher RSS feed on
every test run.

**Rate limits are per-process and in-memory**, so `test_api_security.py` and
`test_swipe_logging.py` both consume the swipe budget. Running them back to back
within the same minute can surface a spurious 429 in the second. Leave a minute
between them, or run them as separate processes (the commands above already do).

## The one worth understanding

`test_telemetry_provenance.py` holds the check that actually catches fabricated
metrics. Static greps and schema assertions can be satisfied by a sufficiently
well-dressed constant. The responsiveness check cannot:

```
write 5 MB into the measured directory -> assert the reported size moves by 5 MB
delete it                              -> assert the figure returns
```

A constant cannot respond to a change in the world. If someone reintroduces an
estimated share of disk usage, that check fails.
