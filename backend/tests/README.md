# Backend test suites

Standalone scripts, not a pytest suite. Each prints `PASS` / `FAIL` per check and
exits non-zero on failure, so they work equally well by hand or in CI.

Run them from inside the backend container:

```bash
docker compose exec news-cards-backend python tests/test_pool.py
docker compose exec news-cards-backend python tests/test_live_pg.py
docker compose exec news-cards-backend python tests/test_telemetry_provenance.py
docker compose exec news-cards-backend python tests/test_admin_auth.py
docker compose exec news-cards-backend python tests/test_api_security.py
docker compose exec news-cards-backend python tests/test_swipe_logging.py
docker compose exec news-cards-backend python tests/test_dedup.py
docker compose exec news-cards-backend python tests/test_topic_summaries.py
```

Or all of them:

```bash
docker compose exec news-cards-backend sh -c 'for t in tests/test_*.py; do echo "== $t"; python "$t" || exit 1; done'
```

| File | Needs Postgres | What it guards |
|---|---|---|
| `test_pool.py` | no | Connection lifecycle: no leak on any error path, bounded acquisition, thread-safe init |
| `test_live_pg.py` | yes | The same guarantees against a real server, incl. the 500-bad-swipes scenario |
| `test_telemetry_provenance.py` | yes | No fabricated metrics; every field declares `measured` |
| `test_admin_auth.py` | yes | The /analytics gate, session cookies, CSRF posture, fail-closed behaviour |
| `test_api_security.py` | yes | Safe methods, admin auth, input validation, rate limits |
| `test_swipe_logging.py` | yes | Swipes written once, charts still complete, real status codes logged, request logs buffered off the request path and bounded by retention |
| `test_dedup.py` | yes | `save_article` reports whether it inserted; no read-then-write pre-check; concurrent inserts of one key produce exactly one winner |
| `test_topic_summaries.py` | yes | Week arithmetic and the half-open UTC window; boilerplate descriptions never reach the generator; `number_of_articles` describes what was read and `articles_in_window` what was seen; no placeholder row on failure; the summariser runs before the purge and its output outlives the articles |

## Notes

**They write to the database they connect to.** Each seeds a fixture article and
records swipes. Point `DATABASE_URL` at a scratch database if you do not want
production rows; the volumes are small (tens of rows) but they are real.

**`test_topic_summaries.py` seeds weeks in 2019 on purpose.** Its count checks
compare against fixture-only totals, so a live article inside a fixture window
makes them all wrong -- and wrong in a way that reads as a filtering bug. A week
older than `PURGE_OLDER_THAN_DAYS` cannot contain live rows, and
`require_empty_week()` asserts that rather than trusting it. It also calls
`purge_old_articles()` once, which is the same call the scheduler makes every
12 hours, and it never deletes a summary for a week it did not create.

**`test_api_security.py`, `test_admin_auth.py` and `test_swipe_logging.py` set
`ADMIN_API_KEY` themselves** and reload `main`, so they do not depend on your
`.env`.

**They bypass `lifespan()`** and open the pool directly. That is deliberate:
running `lifespan()` would trigger a real news fetch and spend NewsAPI quota on
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
