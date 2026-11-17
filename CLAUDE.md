# Paperswap — notes for coding agents

Swipe-based news reader. FastAPI + PostgreSQL 16 backend (`backend/`), Kotlin/Compose Android client (`android/`), and a web preview served by the backend (`backend/templates/mobile_preview.html`). Start with `README.md`, then `docs/ARCHITECTURE.md` (ADR-001..012). Each ADR explains *why*, and most of the non-obvious code below traces back to one.

## Layout that matters

- `backend/main.py`: routes, admin auth (header for POSTs, header-or-cookie for read-only admin GETs), the request-log buffer, `refresh_pipeline()`, lifespan, and the `--cli` runner.
- `backend/database.py`: connection pool, the whole schema and its migrations (`init_db()`: `CREATE ... IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`, no Alembic), queries, purges and telemetry. Loads `backend/.env`.
- `backend/news_fetcher.py`: `TOPIC_FEEDS`, the keyword classifier and card blurbs (`generate_short_summary_tiered` returns `(text, description_source)`).
- `backend/summarizer.py`: weekly digests. `backend/enrichment.py`: a one-shot ML job run from cron, never imported by the API.
- Topics: `database.CATEGORIES` is the single source of truth. A new topic needs an entry there plus one in `news_fetcher.TOPIC_FEEDS`, and no client change.

## Invariants — do not break

1. **Dedup is the `article_key` unique constraint + `ON CONFLICT DO NOTHING`.** Never add a read-then-insert pre-check (`tests/test_dedup.py` enforces this).
2. **`refresh_pipeline` order is fetch → summarise → purge.** The purge destroys the summariser's input (ADR-011). `PURGE_OLDER_THAN_DAYS` is clamped to ≥ 9.
3. **Retention windows stay equal.** `REQUEST_LOG_RETENTION_DAYS` and `SESSION_RETENTION_DAYS` default to the article window because the analytics panels UNION those tables with `user_swipes`.
4. **Telemetry provenance (ADR-010).** Every numeric telemetry field is either measured at request time (`measured: true`) or `null` with an `unavailable_reason`. Never estimate, scale or hardcode a reading.
5. **NULL means "not measured".** Never backfill `dwell_ms`, `flipped`, `request_logs.status_code` or `description_source` with a guessed value.
6. **`swipe_events` and `topic_summaries` are never purged.** `swipe_events` has no FK on purpose.
7. **Summaries: on failure, write no row.** A placeholder becomes indistinguishable from a real summary once the purge runs.
8. **Admin endpoints fail closed** (`503` when `ADMIN_API_KEY` is unset). State-changing admin routes accept the `X-API-Key` header only, never the cookie (CSRF).
9. **Name columns explicitly in feed queries.** `articles.embedding` is `BYTEA` and is not JSON-serialisable.
10. **No database I/O on the request path for logging.** The middleware appends to an in-memory deque, and the scheduler flushes it.
11. **Escape server data before it reaches `innerHTML`** in the templates. Request paths and RSS titles are attacker-influenced.
12. **Every public write path is bounded globally, not just per IP** (`public_limits.py`: swipe and new-session budgets, per-session swipe dedupe, cold-start gate). A new public endpoint that writes rows or calls out needs the same.

## Working conventions

- Every query borrows a pooled connection through `with db_cursor(commit=...)`. Never open raw connections.
- Rate-limited handlers need an explicit `request: Request` param, with `@app.<method>` above `@limiter.limit` (docs/RATE_LIMITS.md).
- New env vars must be added in three places: the code, `backend/.env.example`, and the `environment:` block of `backend/docker-compose.yml`. Compose does not pass `.env` through wholesale, so a variable missing from the block is silently ignored in the container.
- Secrets: generate with `openssl rand -hex 24` (hex, never base64, because the password is embedded in a URL).
- Templates in `backend/templates/` are plain HTML served verbatim (no templating engine); fonts come from Google Fonts.

## Tests

Standalone scripts, not pytest. They need a live Postgres and write to whatever `DATABASE_URL` points at. Run them inside the container:

```bash
cd backend
docker compose exec news-cards-backend sh -c 'for t in tests/test_*.py; do echo "== $t"; python "$t" || exit 1; done'
```

`tests/README.md` describes what each suite guards. The image does not ship the test-only dependencies, so run `pip install -r requirements-dev.txt` first, in the container or anywhere else (httpx for TestClient, ruff).

CI (`.github/workflows/ci.yml`) runs on every PR into `dev` or `main`: `ruff check` (pyflakes + syntax only, see `backend/ruff.toml`), every `tests/test_*.py` against Postgres 16, a Compose boot with dummy secrets plus a smoke test of the public and admin routes, and the Android debug + release builds. A new test file is picked up automatically if it matches `tests/test_*.py` and exits non-zero on failure. There is no Android test suite.

## Branches

`main` is production; the VM deploys from it. Feature branches start from `dev` and every PR targets `dev` (`gh pr create --base dev`, because GitHub's default base is `main`). `dev` is merged into `main` only as a deliberate release. Never open a PR from a feature branch straight to `main`.

Android has no tests. The check is that it compiles. Gradle 8.8 / AGP 8.5 needs JDK 17–22 (`JAVA_HOME`); Android Studio's bundled JBR 25 is too new for the command line:

```bash
cd android
./gradlew :app:assembleDebug :app:compileReleaseKotlin
```

Android conventions: every backend call goes through `apiCall {}` (`data/remote/ApiCall.kt`); topic colours come from the server via `parseAccent`, are made readable with `readableOn`, and are never hardcoded per topic; screens read the Paper palette through `Paper.colors` (ADR-013), never literal colours; the base URL is `BuildConfig.BASE_URL` (Gradle property `paperswap.baseUrl`).

Release builds run R8 (`isMinifyEnabled` + `isShrinkResources`). **Gson models must live in `data/model/`**, the package `proguard-rules.pro` keeps; anywhere else they are stripped and deserialise to nulls in release only. Do not re-add `material-icons-extended`; copy single icons as in `ui/components/UndoIcon.kt`. Offline, `assembleRelease` needs `-x lintVitalAnalyzeRelease -x lintVitalReportRelease -x lintVitalRelease`, because the lint artifacts are not in the Gradle cache.
