import os
import time
import logging
import secrets
import argparse
import datetime
import threading
import collections
from typing import Literal, Optional
from contextlib import asynccontextmanager

import psycopg2
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from apscheduler.schedulers.background import BackgroundScheduler
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.routing import Match

from news_fetcher import fetch_and_sync_news_to_db, present_for_client
import summarizer
import database as db
from public_limits import ColdStartGate, GlobalBudget, TTLCache


# Hours between automatic refreshes. Overridable via env (docker-compose sets REFRESH_INTERVAL).
REFRESH_INTERVAL_HOURS = float(os.getenv("REFRESH_INTERVAL", "12"))
# Articles older than this are purged on each refresh.
PURGE_OLDER_THAN_DAYS = int(os.getenv("PURGE_OLDER_THAN_DAYS", "7"))

# Weekly summaries need the whole of the last completed Mon-Sun week to still be
# in the table when the Monday job runs. That is 7 days of week, plus up to 24 h
# of intra-day offset, plus one refresh interval before the job is reached --
# about 8.5 days. At the old default of 7, the Monday of the summarised week is
# already purge-eligible, so the digest quietly covers six days and reports
# number_of_articles for six days under a label saying seven. Nothing downstream
# can detect it: the evidence has been deleted. See ADR-011.
MIN_PURGE_DAYS_FOR_WEEKLY_SUMMARIES = 9
if PURGE_OLDER_THAN_DAYS < MIN_PURGE_DAYS_FOR_WEEKLY_SUMMARIES:
    logging.getLogger("paperswap.config").warning(
        "PURGE_OLDER_THAN_DAYS=%d is below %d, so the last completed week may be "
        "partly purged before the weekly summariser reads it. Summaries would "
        "silently cover a short week. Raising to %d.",
        PURGE_OLDER_THAN_DAYS, MIN_PURGE_DAYS_FOR_WEEKLY_SUMMARIES,
        MIN_PURGE_DAYS_FOR_WEEKLY_SUMMARIES,
    )
    PURGE_OLDER_THAN_DAYS = MIN_PURGE_DAYS_FOR_WEEKLY_SUMMARIES
# Retention for the request_logs analytics table. Defaults to the article window
# deliberately: user_swipes is already capped at that window by ON DELETE CASCADE,
# and both tables are UNIONed in the hourly-usage and top-endpoint panels. Keeping
# request_logs longer would make swipes under-represent against every other
# endpoint -- see database.purge_old_request_logs.
REQUEST_LOG_RETENTION_DAYS = int(
    os.getenv("REQUEST_LOG_RETENTION_DAYS", str(PURGE_OLDER_THAN_DAYS))
)
# Retention for user_sessions, measured from last_heartbeat. Same default and
# the same reason: total_sessions, total_swipes and total_articles are rendered
# side by side, and total_swipes is already capped at the article window by
# ON DELETE CASCADE.
SESSION_RETENTION_DAYS = int(
    os.getenv("SESSION_RETENTION_DAYS", str(PURGE_OLDER_THAN_DAYS))
)
# Period the reported average session length covers. Clamped to the retention
# window below: user_sessions is purged on last_heartbeat, so a metric window
# wider than retention would compute over the rows that survived and report the
# result under a label claiming a longer period.
SESSION_METRIC_WINDOW_DAYS = int(
    os.getenv("SESSION_METRIC_WINDOW_DAYS", str(SESSION_RETENTION_DAYS))
)
if SESSION_METRIC_WINDOW_DAYS > SESSION_RETENTION_DAYS:
    logging.getLogger("paperswap.config").warning(
        "SESSION_METRIC_WINDOW_DAYS=%d exceeds SESSION_RETENTION_DAYS=%d; sessions "
        "older than the retention window no longer exist, so the average would be "
        "computed over %d days while claiming %d. Clamping to %d.",
        SESSION_METRIC_WINDOW_DAYS, SESSION_RETENTION_DAYS,
        SESSION_RETENTION_DAYS, SESSION_METRIC_WINDOW_DAYS, SESSION_RETENTION_DAYS,
    )
    SESSION_METRIC_WINDOW_DAYS = SESSION_RETENTION_DAYS
# Default deck size served by /api/v1/feed (7 topics x ~10 cards).
FEED_DEFAULT_LIMIT = int(os.getenv("FEED_DEFAULT_LIMIT", "70"))
# How often buffered request events are drained into Postgres.
LOG_FLUSH_INTERVAL_SECONDS = int(os.getenv("LOG_FLUSH_INTERVAL", "10"))

# --- Global limits (see public_limits.py and docs/RATE_LIMITS.md) ------------
# Per-IP limits do not bound totals: many clients multiply them. These do.
# Swipes are the writes that matter most -- swipe_events is never purged -- so
# the swipe budget is what bounds that table's growth under abuse.
GLOBAL_SWIPES_PER_HOUR = int(os.getenv("GLOBAL_SWIPES_PER_HOUR", "3000"))
GLOBAL_NEW_SESSIONS_PER_HOUR = int(os.getenv("GLOBAL_NEW_SESSIONS_PER_HOUR", "500"))
SWIPE_BUDGET = GlobalBudget("swipes", GLOBAL_SWIPES_PER_HOUR)
SESSION_BUDGET = GlobalBudget("new_sessions", GLOBAL_NEW_SESSIONS_PER_HOUR)

# Every request, every route, every client, per minute. The last line of the
# load-shedding stack (#58): uvicorn's --limit-concurrency caps work in flight,
# this caps throughput, and both answer 503 rather than letting memory grow.
# A circuit breaker, not a fairness mechanism -- one client can spend it for
# everyone. See docs/RATE_LIMITS.md, "Load shedding".
GLOBAL_REQUESTS_PER_MINUTE = int(os.getenv("GLOBAL_REQUESTS_PER_MINUTE", "1800"))
REQUEST_BUDGET = GlobalBudget("requests", GLOBAL_REQUESTS_PER_MINUTE, window_seconds=60.0)

# /docs, /redoc and /openapi.json. The schema maps every route, parameter and
# admin endpoint for whoever asks; turn it off once the API is public (#32).
API_DOCS_ENABLED = os.getenv("API_DOCS_ENABLED", "true").lower() == "true"

# Public GET payloads are identical for every client. Served from memory for
# this long, and sent with a matching Cache-Control so an edge cache can too.
PUBLIC_CACHE_SECONDS = int(os.getenv("PUBLIC_CACHE_SECONDS", "60"))
SUMMARY_CACHE_SECONDS = int(os.getenv("SUMMARY_CACHE_SECONDS", "600"))
_FEED_CACHE = TTLCache(PUBLIC_CACHE_SECONDS)
_SUMMARY_CACHE = TTLCache(SUMMARY_CACHE_SECONDS)

# At most one synchronous news fetch per topic selection per cooldown.
FEED_COLD_FETCH_COOLDOWN = int(os.getenv("FEED_COLD_FETCH_COOLDOWN", "600"))
_COLD_START = ColdStartGate(FEED_COLD_FETCH_COOLDOWN)


def invalidate_public_caches() -> None:
    """Call whenever articles, categories or summaries change."""
    _FEED_CACHE.clear()
    _SUMMARY_CACHE.clear()


def _public_json(content: dict, max_age: int) -> JSONResponse:
    """JSONResponse with a Cache-Control that lets browsers and an edge cache
    (Cloudflare, #32) serve it without reaching this process."""
    return JSONResponse(
        content=content,
        headers={"Cache-Control": f"public, max-age={max_age}"},
    )


def _busy(budget: GlobalBudget, what: str) -> HTTPException:
    """503 for an exhausted global budget. Logged, because a budget tripping is
    either real growth worth knowing about or someone hammering the API."""
    logging.getLogger("paperswap.limits").warning(
        "Global %s budget exhausted (%d/h); rejecting.", budget.name, budget.limit)
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        f"The server is not accepting more {what} right now. Try again later.",
        headers={"Retry-After": str(budget.retry_after())},
    )

scheduler = BackgroundScheduler()


# ---------------------------------------------------------------------------
# ADMIN AUTHENTICATION
#
# The public surface is the read-only feed the phone needs: /api/v1/feed,
# /api/v1/categories, /api/v1/swipe, /api/v1/telemetry/heartbeat. Everything
# that mutates server state or exposes operational data sits behind this key.
#
# APIKeyHeader (rather than a bare Header parameter) is deliberate: it registers
# a real OpenAPI security scheme, so /docs renders a padlock on protected routes
# and offers an Authorize button. A plain Header would document the parameter
# but not the requirement.
# ---------------------------------------------------------------------------
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

_api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,   # we raise our own errors so 401 vs 503 stay distinguishable
    description="Admin key. Set ADMIN_API_KEY in backend/.env and send it as X-API-Key.",
)

# --- Browser sessions -------------------------------------------------------
# /analytics is a page, not an API call. A browser cannot attach X-API-Key to a
# top-level navigation, so header auth alone would make the dashboard
# unreachable. Instead the page is gated: an unauthenticated GET returns a small
# key-entry form, which POSTs to /api/v1/auth/session and receives an opaque
# session cookie.
#
# The cookie is a random token, never the key itself, and is held in memory only
# -- a restart invalidates every session, which is the right default for an
# admin surface on a single-container deployment.
SESSION_COOKIE = "paperswap_admin_session"
SESSION_TTL_SECONDS = int(os.getenv("ADMIN_SESSION_TTL", "43200"))   # 12 h
# Set true once HTTPS (Caddy) is in front. Left false by
# default because a Secure cookie is silently dropped over plain HTTP, which
# would present as "the login form just reloads forever".
COOKIE_SECURE = os.getenv("ADMIN_COOKIE_SECURE", "false").lower() == "true"

_sessions: dict = {}          # token -> expiry epoch seconds
_sessions_lock = threading.Lock()


def _new_session() -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _sessions_lock:
        # Opportunistic prune; the dict is tiny and only admins create entries.
        for t in [t for t, exp in _sessions.items() if exp < now]:
            _sessions.pop(t, None)
        _sessions[token] = now + SESSION_TTL_SECONDS
    return token


def _valid_session(token: Optional[str]) -> bool:
    if not token:
        return False
    with _sessions_lock:
        expiry = _sessions.get(token)
        if expiry is None:
            return False
        if expiry < time.time():
            _sessions.pop(token, None)
            return False
    return True


def _require_key_configured() -> None:
    """Fail CLOSED: an unset key makes admin routes unavailable, never open."""
    if not ADMIN_API_KEY:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "ADMIN_API_KEY is not configured on the server.",
        )


def _key_matches(provided: Optional[str]) -> bool:
    """Constant-time comparison, so response latency does not leak how many
    leading characters of a guess were correct. Encoded to bytes because the str
    form of compare_digest raises TypeError on non-ASCII input -- which would
    turn a malformed key into a 500."""
    if not provided:
        return False
    return secrets.compare_digest(
        provided.encode("utf-8"), ADMIN_API_KEY.encode("utf-8")
    )


def require_admin_key(provided: Optional[str] = Depends(_api_key_header)) -> None:
    """Header-only auth. Used for state-changing admin endpoints.

    Deliberately does NOT accept the session cookie: cookies are attached by the
    browser automatically, so a cookie-authenticated POST is CSRF-reachable from
    another site. Requiring an explicit header means an attacker's page cannot
    forge the request even while the admin is logged in.
    """
    _require_key_configured()
    if not _key_matches(provided):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing X-API-Key.")


def require_admin_session(
    request: Request,
    provided: Optional[str] = Depends(_api_key_header),
) -> None:
    """Header OR session cookie. Used for read-only admin endpoints, so the
    browser dashboard works after logging in while curl can still pass a key."""
    _require_key_configured()
    if _key_matches(provided):
        return
    if _valid_session(request.cookies.get(SESSION_COOKIE)):
        return
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Invalid or missing credentials. Send X-API-Key, or sign in at /analytics.",
    )


# ---------------------------------------------------------------------------
# RATE LIMITING
#
# In-memory storage: per-process counters that reset on restart. Correct for a
# single container, and avoids running Redis on a 956 MB VM.
#
# NOTE: keyed on the client IP. Behind a reverse proxy every request appears to
# come from the proxy, collapsing all users into one bucket. Putting Caddy in
# front requires X-Forwarded-For handling first -- see docs/RATE_LIMITS.md.
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)


def refresh_pipeline():
    """One full refresh cycle: fetch + dedup new news, summarise the last
    completed week, then purge week-old rows.

    ORDER IS LOAD-BEARING. The summariser runs before the purge because the
    purge is what destroys its input -- once those rows are gone the summary row
    is the only surviving record of the week and there is no way to reconstruct
    it. Putting this on its own APScheduler cron job instead would race the
    purge; as a pipeline step the ordering is guaranteed by control flow. See
    ADR-011.

    The three purges are one unit on purpose. purge_old_articles cascades into
    user_swipes; the other two trim the tables that are reported alongside it.
    Running one without the others leaves the analytics panels reading different
    time windows in the same view. Run on startup and on the scheduled interval.
    """
    fetch_and_sync_news_to_db()

    try:
        summarizer.generate_weekly_summaries()
    except Exception:
        # A summariser failure must never stop the purge. Disk pressure on a
        # 956 MB box with a shared Postgres volume is the more urgent problem,
        # and the missing week is retried on the next tick anyway.
        logging.getLogger("paperswap.summarizer").warning(
            "Weekly summary generation failed; continuing to purge.", exc_info=True)

    db.purge_old_data(
        articles_days=PURGE_OLDER_THAN_DAYS,
        request_logs_days=REQUEST_LOG_RETENTION_DAYS,
        sessions_days=SESSION_RETENTION_DAYS,
    )
    # Separate call, not folded into purge_old_data: that function's three
    # tables share a window because they are reported side by side, whereas this
    # table exists precisely to outlive it. Defaults to 0, meaning keep forever.
    db.purge_old_summaries(summarizer.SUMMARY_RETENTION_WEEKS)
    invalidate_public_caches()


# ---------------------------------------------------------------------------
# REQUEST LOG BUFFERING
#
# log_requests_middleware() appends to _LOG_BUFFER -- an in-memory deque, no
# I/O -- and flush_request_logs() drains it into Postgres from the scheduler
# thread every LOG_FLUSH_INTERVAL_SECONDS.
#
# Before this, the middleware called db.log_request_event() directly. That is a
# synchronous connection checkout + INSERT + COMMIT, and the middleware is an
# `async def`, so it ran ON the event loop: every other in-flight request in the
# process stalled behind that round trip. Excluding /api/v1/swipe cut the volume
# but not the mechanism -- one blocking write on the loop is still one too many.
#
# Net effect: ~1 INSERT per request becomes 1 INSERT per flush interval, and the
# request path does no database work at all.
# ---------------------------------------------------------------------------
logger = logging.getLogger("paperswap.request_log")

# Last 100 log lines, served to the analytics dashboard by /api/v1/telemetry/logs.
RECENT_LOGS = collections.deque(maxlen=100)


def add_log_entry(message: str, level: str = "INFO"):
    """Append a timestamped line to RECENT_LOGS and echo it to stdout."""
    entry = f"[{datetime.datetime.now():%H:%M:%S}] [{level}] {message}"
    RECENT_LOGS.append(entry)
    print(entry)


add_log_entry("Application process initialized. Telemetry system online.", "SYSTEM")

# Bounded, so a traffic spike costs a fixed slice of memory instead of growing
# until this 956 MB VM starts swapping. At a 10 s interval, 10k slots absorbs a
# sustained ~1000 req/s. Overflow evicts the OLDEST entry -- which is counted and
# reported, never swallowed, because silently losing rows would under-report the
# very panels this table feeds.
_LOG_BUFFER = collections.deque(maxlen=10_000)
_LOG_DROPPED = 0


def flush_request_logs() -> int:
    """Drain the buffer into Postgres in one round trip. Returns rows written.

    Runs on the APScheduler thread, where a blocking database call is the right
    thing rather than the bug.

    Drains at most the length observed on entry, so events arriving mid-drain
    are left for the next tick instead of letting this loop chase a buffer that
    is being refilled under it.
    """
    global _LOG_DROPPED

    batch = []
    for _ in range(len(_LOG_BUFFER)):
        try:
            batch.append(_LOG_BUFFER.popleft())
        except IndexError:      # drained by a concurrent flush; nothing left
            break

    if not batch:
        return 0

    try:
        written = db.log_request_events_bulk(batch)
    except Exception:
        # Deliberately NOT `except Exception: pass`. The old middleware swallowed
        # database failures on every single request, so a Postgres outage looked
        # exactly like an idle server. Surface it.
        #
        # The batch is dropped rather than re-queued: telemetry must never be the
        # reason the process runs out of memory during an outage.
        logger.warning("Failed to flush %d request log row(s); batch discarded.",
                       len(batch), exc_info=True)
        return 0

    if _LOG_DROPPED:
        logger.warning(
            "Request log buffer overflowed: %d event(s) dropped before this flush. "
            "Raise LOG_FLUSH_INTERVAL frequency or the deque maxlen if this recurs.",
            _LOG_DROPPED,
        )
        _LOG_DROPPED = 0

    return written


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the connection pool, initialize the database, run an initial refresh,
    and start the 12-hour scheduler. The pool is closed on shutdown so Postgres
    reclaims the backends immediately."""
    print("[Server Startup] Opening PostgreSQL connection pool...")
    db.init_pool()
    print("[Server Startup] Initializing PostgreSQL database...")
    db.init_db()
    print("[Server Startup] Running initial fetch + purge...")
    refresh_pipeline()
    print("[Server Startup] Database ready!")

    if not ADMIN_API_KEY:
        print("[Server Startup] WARNING: ADMIN_API_KEY is unset. Admin endpoints "
              "(refresh, telemetry) will return 503 until it is configured.")

    scheduler.add_job(
        refresh_pipeline,
        "interval",
        hours=REFRESH_INTERVAL_HOURS,
        id="refresh_pipeline",
        replace_existing=True,
        max_instances=1,   # never let a slow run overlap the next tick
        coalesce=True,     # collapse missed runs into a single execution
    )
    scheduler.add_job(
        flush_request_logs,
        "interval",
        seconds=LOG_FLUSH_INTERVAL_SECONDS,
        id="flush_request_logs",
        replace_existing=True,
        max_instances=1,   # one drainer at a time
        coalesce=True,
    )
    scheduler.start()
    print(f"[Scheduler] Auto-refresh every {REFRESH_INTERVAL_HOURS} h; purging "
          f"articles older than {PURGE_OLDER_THAN_DAYS} d, request logs older "
          f"than {REQUEST_LOG_RETENTION_DAYS} d, sessions idle over "
          f"{SESSION_RETENTION_DAYS} d.")
    print(f"[Scheduler] Flushing buffered request logs every "
          f"{LOG_FLUSH_INTERVAL_SECONDS} s.")

    yield

    # Stop the scheduler first so no new flush starts, then take the final drain
    # here. Without it, every deploy silently loses up to one interval's worth of
    # request events -- and a gap that only ever appears at shutdown is the kind
    # of artefact that later gets misread as a traffic dip.
    scheduler.shutdown(wait=False)
    flushed = flush_request_logs()
    if flushed:
        print(f"[Server Shutdown] Flushed {flushed} buffered request log row(s).")
    db.close_pool()


app = FastAPI(
    title="Paperswap News Feed API",
    description=(
        "JSON API behind the Paperswap swipe deck: a topic-balanced news feed, "
        "swipe recording, and weekly per-topic summaries. Cards are rendered on "
        "the client.\n\n"
        "**Authentication.** Read endpoints are public. Endpoints that mutate state "
        "or expose operational data require an `X-API-Key` header and are marked "
        "with a padlock below."
    ),
    version="3.1.0",
    lifespan=lifespan,
    docs_url="/docs" if API_DOCS_ENABLED else None,
    redoc_url="/redoc" if API_DOCS_ENABLED else None,
    openapi_url="/openapi.json" if API_DOCS_ENABLED else None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# The deck is ~80 KB of JSON; gzip cuts it to roughly a third. Egress is the
# one Oracle Always Free meter a client can drive (10 TB/month on a 50 Mbps link).
app.add_middleware(GZipMiddleware, minimum_size=1024)


# Paths excluded from the request_logs analytics table.
#   /static, *.ico        - noise, no analytical value
#   /api/v1/telemetry/*   - the dashboard polls these every 8s; logging them would
#                           swamp the very chart they render
#   /api/v1/swipe         - ALREADY recorded in user_swipes with a swiped_at
#                           timestamp. A request_logs row would be a second copy of
#                           a timestamp we just stored, and swipes are the dominant
#                           traffic source (~70 per session), so this halves the
#                           write load of a session. The hourly-usage and
#                           top-endpoint panels derive swipe counts from
#                           user_swipes instead -- see database._hourly_usage_distribution.
LOG_EXCLUDED_PREFIXES = ("/static", "/api/v1/telemetry", "/api/v1/swipe")

# Requests that matched no route (scanners probing /wp-login.php, /.env, ...).
# Counted here instead of stored: every distinct junk path would otherwise be a
# request_logs row, and an internet-facing box sees thousands a day.
_UNMATCHED_REQUESTS = 0


def _route_template(scope) -> Optional[str]:
    """The matched route's path template ('/api/v1/summaries/{topic}'), or None
    when nothing matched.

    A FULL match (path and method) wins over a PARTIAL one (path only), the
    same precedence the router uses: POST /api/v1/summaries/generate must not
    be attributed to the GET /api/v1/summaries/{topic} route declared before
    it. A path-only match still names its route, so a 405 is logged under it.
    """
    partial = None
    for route in app.router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return getattr(route, "path", None)
        if match == Match.PARTIAL and partial is None:
            partial = getattr(route, "path", None)
    return partial


@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    """Record request frequency and outcome for the peak-hours analytics.

    Two rules here, both learned the hard way:

    1. Logging happens AFTER call_next, so the recorded status is the real one.
       The original wrote a hardcoded "200 OK" BEFORE the response existed,
       which reported every 401, 404 and 422 to the dashboard as a success --
       a fabricated measurement of exactly the kind ADR-010 removed.

    2. Nothing here touches the database. This coroutine runs on the event loop,
       so the synchronous INSERT it used to make blocked every other in-flight
       request for the duration of a connection checkout and a round trip.
       Events go into _LOG_BUFFER; flush_request_logs() drains them on the
       scheduler thread. See the REQUEST LOG BUFFERING block above.

    Errors ARE persisted now, with their real status. Under the old per-request
    INSERT they were skipped to stop an error burst becoming a write storm;
    batching removes that concern, and a status_code column whose rows are all
    <400 by construction would be decorative.
    """
    global _LOG_DROPPED, _UNMATCHED_REQUESTS

    response = await call_next(request)

    path = request.url.path
    status_code = response.status_code

    if path.startswith(LOG_EXCLUDED_PREFIXES) or path.endswith(".ico"):
        # Excluded from the table, but a burst of 401s against the telemetry
        # routes is worth seeing live -- that is someone probing the admin API.
        if status_code >= 400:
            add_log_entry(f"{request.method} {path} - {status_code}", "ERROR")
        return response

    template = _route_template(request.scope)
    if template is None:
        _UNMATCHED_REQUESTS += 1
        add_log_entry(f"{request.method} {path} - {status_code} (no route)", "ERROR")
        return response

    if len(_LOG_BUFFER) == _LOG_BUFFER.maxlen:
        _LOG_DROPPED += 1
    _LOG_BUFFER.append((
        # The template, not the raw path: /api/v1/summaries/TECH and /SPORTS
        # are one endpoint in the analytics panels, and a client cannot mint
        # new rows' worth of distinct endpoints by varying the path.
        template,
        request.method,
        status_code,
        datetime.datetime.now(datetime.timezone.utc),
    ))

    add_log_entry(f"{request.method} {path} - {status_code}",
                  "ERROR" if status_code >= 400 else "HTTP")

    return response


# Monotonic time of the last "ceiling reached" warning; one per window, so a
# flood produces one log line a minute rather than one per rejected request.
_CEILING_LAST_WARNED = float("-inf")


class GlobalRequestCeiling:
    """Answer 503 + Retry-After once GLOBAL_REQUESTS_PER_MINUTE requests have
    arrived in the current minute, counting every client and every route.

    Pure ASGI and registered last, so it is the OUTERMOST layer: a shed request
    costs one counter increment and never reaches routing, the request log or
    gzip. Shed requests are therefore not in request_logs; they are counted in
    REQUEST_BUDGET.rejected and reported under global_limits.requests in
    /api/v1/telemetry/stats.

    Why not slowapi's application_limits, which the issue suggested: its
    middleware skips any route that already has a @limiter.limit decorator --
    every API route here -- and it keys on client IP, so it is neither global
    nor applied where the load is.

    Admin requests (a valid X-API-Key or admin session cookie) bypass the
    ceiling and do not spend it, so the operator can still open /analytics
    and trigger a refresh during the incident the ceiling is reacting to.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        request = Request(scope)
        if (_key_matches(request.headers.get("x-api-key"))
                or _valid_session(request.cookies.get(SESSION_COOKIE))):
            return await self.app(scope, receive, send)

        if REQUEST_BUDGET.take():
            return await self.app(scope, receive, send)

        global _CEILING_LAST_WARNED
        now = time.monotonic()
        if now - _CEILING_LAST_WARNED >= REQUEST_BUDGET.window:
            _CEILING_LAST_WARNED = now
            logging.getLogger("paperswap.limits").warning(
                "Global request ceiling reached (%d/min); shedding load.",
                REQUEST_BUDGET.limit)
            add_log_entry(
                f"Request ceiling reached ({REQUEST_BUDGET.limit}/min); "
                f"answering 503 until the window resets.", "ERROR")
        response = JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "The server is busy. Try again shortly."},
            headers={"Retry-After": str(REQUEST_BUDGET.retry_after())},
        )
        await response(scope, receive, send)


# Registered after the logging middleware above, so it wraps it (Starlette runs
# the most recently added middleware first).
app.add_middleware(GlobalRequestCeiling)


class SwipeRequest(BaseModel):
    """Validation lives in the schema, so FastAPI returns a descriptive 422 and
    OpenAPI documents the allowed values automatically."""
    article_id: int = Field(
        gt=0,
        description="Database id of the article being swiped. Must be positive.",
        examples=[42],
    )
    action: Literal["read", "pass"] = Field(
        description="'read' for a right swipe (interested), 'pass' for a left swipe.",
        examples=["read"],
    )
    dwell_ms: Optional[int] = Field(
        default=None,
        ge=0,
        le=3_600_000,
        description=(
            "Milliseconds the card was frontmost before the swipe. Omit if the "
            "client cannot measure it -- do not send 0, which reads as a "
            "measured instant dismissal. Capped at 1 hour to reject the "
            "backgrounded-app case where a card sat on screen overnight."
        ),
        examples=[4200],
    )
    flipped: Optional[bool] = Field(
        default=None,
        description=(
            "Whether the user turned the card over to read the summary before "
            "swiping. Omit if unmeasured; a stronger interest signal than "
            "direction alone."
        ),
        examples=[True],
    )
    session_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=64,
        description=(
            "Client-generated id for this app run or browser. With it, a "
            "repeated swipe on the same article is acknowledged but not stored "
            "again. Optional for older clients."
        ),
    )


class HeartbeatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    user_agent: Optional[str] = Field(default=None, max_length=256)


class AdminLogin(BaseModel):
    key: str = Field(min_length=1, max_length=512,
                     description="The value of ADMIN_API_KEY.")


# Minimal, self-contained sign-in page. Kept inline rather than as a template so
# an unauthenticated visitor never touches the dashboard file at all.
_GATE_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PaperSwap Analytics - Sign in</title>
<style>
  body{background:#000;color:#e8e9ea;font-family:system-ui,sans-serif;display:flex;
       align-items:center;justify-content:center;min-height:100vh;margin:0}
  .box{background:#0a0a0a;border:1px solid #1c1c1c;border-radius:12px;padding:28px;
       width:min(380px,90vw)}
  h1{font-size:17px;margin:0 0 6px}
  p{font-size:12px;color:#6b6f78;margin:0 0 18px;line-height:1.5}
  input{width:100%;padding:11px 12px;border-radius:8px;border:1px solid #262626;
        background:#050505;color:#e8e9ea;font-family:ui-monospace,monospace;font-size:13px}
  button{width:100%;margin-top:12px;padding:11px;border:0;border-radius:8px;
         background:#c4f542;color:#000;font-weight:700;font-size:13px;cursor:pointer}
  .err{color:#f87171;font-size:12px;margin-top:12px;min-height:16px}
</style></head><body>
<div class="box">
  <h1>Analytics access</h1>
  <p>This dashboard exposes operational data. Enter the admin API key
     (<code>ADMIN_API_KEY</code>) to continue.</p>
  <input id="k" type="password" placeholder="admin api key" autocomplete="off" autofocus>
  <button id="go">Sign in</button>
  <div class="err" id="e"></div>
</div>
<script>
  const e = document.getElementById('e');
  async function submit() {
    const key = document.getElementById('k').value.trim();
    if (!key) { e.textContent = 'Enter a key.'; return; }
    e.textContent = 'Checking...';
    try {
      const r = await fetch('/api/v1/auth/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key })
      });
      if (r.ok) { location.reload(); return; }
      if (r.status === 401) e.textContent = 'Incorrect key.';
      else if (r.status === 429) e.textContent = 'Too many attempts. Wait a minute.';
      else if (r.status === 503) e.textContent = 'ADMIN_API_KEY is not configured on the server.';
      else e.textContent = 'Sign-in failed (HTTP ' + r.status + ').';
    } catch (err) { e.textContent = 'Network error.'; }
  }
  document.getElementById('go').addEventListener('click', submit);
  document.getElementById('k').addEventListener('keydown', ev => {
    if (ev.key === 'Enter') submit();
  });
</script>
</body></html>"""


TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


def _serve_template(name: str) -> HTMLResponse:
    """Return a file from templates/ verbatim (no templating engine involved)."""
    path = os.path.join(TEMPLATE_DIR, name)
    if not os.path.exists(path):
        return HTMLResponse(content=f"<h1>{name} missing</h1>", status_code=404)
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/", response_class=HTMLResponse, tags=["pages"])
def read_root():
    """Serve the desktop/gallery dashboard."""
    return _serve_template("dashboard.html")


@app.get("/mobile", response_class=HTMLResponse, tags=["pages"])
def read_mobile_app():
    """Serve the touch swipe UI (deck + weekly summaries tabs)."""
    return _serve_template("mobile_preview.html")


@app.get("/api/v1/categories", tags=["public"])
@limiter.limit("60/minute")
def get_categories(request: Request):
    """List every topic the feed can serve, with its brand colour and how many
    articles are currently stored. Drives the client topic filter bar."""
    payload = _FEED_CACHE.get(("categories",))
    if payload is None:
        categories = db.get_enabled_categories()
        payload = {"status": "ok", "count": len(categories), "categories": categories}
        _FEED_CACHE.set(("categories",), payload)
    return _public_json(payload, PUBLIC_CACHE_SECONDS)


@app.get("/api/v1/feed", tags=["public"])
@limiter.limit("60/minute")
def get_news_feed(
    request: Request,
    categories: str = Query(
        None,
        description="Comma-separated topic filter, e.g. 'SPORTS,SCIENCE'. Omit for all topics."
    ),
    limit: int = Query(None, ge=1, le=300, description="Maximum cards to return."),
    balanced: bool = Query(
        True,
        description="True interleaves topics round-robin; False returns strict newest-first."
    ),
):
    """Serve the swipe deck. Defaults to a topic-balanced mix across all topics,
    so the user never gets 12 Sports cards in a row before seeing anything else."""
    selected = db.clean_category_filter(categories)
    deck_limit = limit or FEED_DEFAULT_LIMIT

    cache_key = ("feed", tuple(selected), deck_limit, balanced)
    payload = _FEED_CACHE.get(cache_key)
    if payload is not None:
        return _public_json(payload, PUBLIC_CACHE_SECONDS)

    def query():
        if balanced:
            rows = db.get_balanced_feed(limit=deck_limit, categories=selected)
        else:
            rows = db.get_latest_articles(limit=deck_limit, categories=selected)
        # image_is_stock + the BBC thumbnail upgrade (ADR-013).
        return present_for_client(rows)

    articles = query()

    # Cold start: nothing stored for these topics, so pull a batch now -- but at
    # most once per topic selection per FEED_COLD_FETCH_COOLDOWN, and never two
    # at once. Everyone else gets the deck as it is (possibly empty).
    if not articles:
        def fetch():
            fetch_and_sync_news_to_db(categories=selected or None)
            invalidate_public_caches()
        if _COLD_START.run_once(tuple(selected), fetch):
            articles = query()

    payload = {
        "status": "ok",
        "count": len(articles),
        "categories": selected or list(db.CATEGORIES.keys()),
        "balanced": balanced,
        "articles": articles
    }
    # An empty deck is not cached: the next request should see the fetch.
    if articles:
        _FEED_CACHE.set(cache_key, payload)
    return _public_json(payload, PUBLIC_CACHE_SECONDS)


@app.post(
    "/api/v1/cards/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_admin_key)],
    tags=["admin"],
)
@limiter.limit("2/hour")
def trigger_refresh(request: Request, background: BackgroundTasks):
    """Trigger a news refresh (fetch + dedup + purge).

    POST, not GET: this mutates state and fans out to ~84 outbound requests
    against the publisher RSS feeds. Browsers, link prefetchers and crawlers
    treat GET as safe and will follow it, so a single prefetch would start a
    full fetch, summarise and purge.

    Returns 202 immediately and runs the pipeline in the background rather than
    holding the client open for the duration of 84 HTTP calls.
    """
    background.add_task(refresh_pipeline)
    return {
        "status": "accepted",
        "detail": "Refresh started in the background. Poll /api/v1/feed for results.",
    }


@app.post("/api/v1/swipe", status_code=status.HTTP_201_CREATED, tags=["public"])
@limiter.limit("120/minute")
def record_swipe(request: Request, req: SwipeRequest):
    """Record a mobile user's swipe (read / pass).

    Rate limit sizing: swiping is client-side, so a real session produces roughly
    one call per second (~60/min). 120/min sits clear of that while still capping
    a script. The limit exists to stop unbounded rows being written to a small
    volume, NOT to protect compute -- this insert costs well under a millisecond.

    `action` and `article_id` are validated by the schema, so a malformed body
    is rejected with a 422 before reaching the database. An id that parses but
    does not exist trips the foreign key, which is translated to a 404 rather
    than surfacing as an unhandled 500.

    Two bounds apply on top of the per-IP limit: a global swipes-per-hour
    budget (503 when spent), and at most one stored swipe per session per
    article -- a repeat returns 200 "duplicate" and writes nothing.
    """
    if not SWIPE_BUDGET.take():
        raise _busy(SWIPE_BUDGET, "swipes")
    try:
        stored = db.record_user_swipe(
            req.article_id, req.action, req.dwell_ms, req.flipped, req.session_id)
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Article {req.article_id} not found.",
        )

    body = {
        "status": "recorded" if stored else "duplicate",
        "article_id": req.article_id,
        "action": req.action,
    }
    if not stored:
        return JSONResponse(status_code=status.HTTP_200_OK, content=body)
    return body


@app.post("/api/v1/auth/session", tags=["admin"])
@limiter.limit("10/minute")
def create_admin_session(request: Request, body: AdminLogin, response: Response):
    """Exchange the admin key for a browser session cookie.

    Exists so /analytics can be gated at all: a top-level navigation cannot
    carry a header. Rate limited tightly because this is the one endpoint where
    guessing the key is the whole point.
    """
    _require_key_configured()
    if not _key_matches(body.key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect key.")

    response.set_cookie(
        SESSION_COOKIE,
        _new_session(),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,       # not readable from JS, so XSS cannot exfiltrate it
        samesite="strict",   # not sent on cross-site requests
        secure=COOKIE_SECURE,
        path="/",
    )
    return {"status": "ok", "expires_in_seconds": SESSION_TTL_SECONDS}


@app.post("/api/v1/auth/logout", tags=["admin"])
def end_admin_session(request: Request, response: Response):
    """Invalidate the current session server-side and clear the cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with _sessions_lock:
            _sessions.pop(token, None)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@app.get("/analytics", response_class=HTMLResponse, tags=["pages"])
def read_analytics_dashboard(request: Request):
    """Serve the VM Telemetry & Engagement Analytics Dashboard, behind a gate.

    A browser cannot attach X-API-Key to a top-level navigation, so this route
    cannot use header auth. Instead an unauthenticated GET returns the sign-in
    form with 401, and the dashboard file is only read once a valid session
    cookie is present -- so the shell, including the schema tables it renders,
    is never served to an anonymous visitor.
    """
    if not (ADMIN_API_KEY and _valid_session(request.cookies.get(SESSION_COOKIE))):
        return HTMLResponse(content=_GATE_PAGE, status_code=status.HTTP_401_UNAUTHORIZED)

    return _serve_template("analytics.html")


@app.get("/summaries", response_class=HTMLResponse, tags=["pages"])
def read_summaries_page():
    """Deep link straight to the weekly summaries tab.

    Serves the SAME template as /mobile rather than a second page. The two views
    live behind a nav bar in one document, so a separate summaries.html would be
    a second copy of the rendering logic, and the copies would drift the first
    time the payload changed. The client reads window.location.pathname and opens
    the right tab.
    """
    return read_mobile_app()


def _parse_week_start(raw: Optional[str]) -> Optional[datetime.date]:
    """Parse ?week_start=YYYY-MM-DD and snap it to that week's Monday.

    Snapping rather than rejecting a mid-week date: a caller who asks for
    Wednesday means the week containing it, and 404-ing them would be pedantry.
    A malformed string is still a 400 -- silently falling back to the latest week
    would answer a different question than the one asked.
    """
    if not raw:
        return None
    try:
        parsed = datetime.date.fromisoformat(raw.strip())
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "week_start must be an ISO date, e.g. 2026-08-31.",
        )
    return parsed - datetime.timedelta(days=parsed.weekday())


@app.get("/api/v1/summaries", tags=["public"])
@limiter.limit("60/minute")
def get_weekly_summaries(
    request: Request,
    week_start: str = Query(
        None,
        description="ISO date inside the wanted week. Omit for the most recent week available."
    ),
):
    """Weekly per-topic digests for one completed week.

    A topic can legitimately be absent: if fewer than MIN_ARTICLES_FOR_SUMMARY
    of its articles had a real publisher description that week, no row was
    written rather than a thin one. `topics_missing` names them so a client can
    say "not enough coverage" instead of rendering a blank card.
    """
    requested = _parse_week_start(week_start)
    cache_key = ("week", requested)
    payload = _SUMMARY_CACHE.get(cache_key)
    if payload is not None:
        return _public_json(payload, SUMMARY_CACHE_SECONDS)

    summaries = db.get_topic_summaries(requested)
    if not summaries:
        payload = {
            "status": "ok",
            "count": 0,
            "week_start": requested.isoformat() if requested else None,
            "summaries": [],
            "detail": "No summaries stored for this week yet.",
        }
    else:
        present = {s["topic"] for s in summaries}
        payload = {
            "status": "ok",
            "count": len(summaries),
            "week_start": summaries[0]["week_start"],
            "week_end": summaries[0]["week_end"],
            "topics_missing": [s for s in db.CATEGORIES if s not in present],
            "summaries": summaries,
        }
    _SUMMARY_CACHE.set(cache_key, payload)
    return _public_json(payload, SUMMARY_CACHE_SECONDS)


@app.get("/api/v1/summaries/{topic}", tags=["public"])
@limiter.limit("60/minute")
def get_topic_summary_history(
    request: Request,
    topic: str,
    weeks: int = Query(8, ge=1, le=52, description="How many weeks back to return."),
):
    """One topic's summary history, newest week first.

    This is the only place week-over-week history exists. The articles behind
    every week but the most recent have already been purged.
    """
    cleaned = db.clean_category_filter(topic)
    if not cleaned:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Unknown topic '{topic}'. See GET /api/v1/categories.",
        )

    slug = cleaned[0]
    cache_key = ("history", slug, weeks)
    payload = _SUMMARY_CACHE.get(cache_key)
    if payload is None:
        history = db.get_summary_history(slug, weeks=weeks)
        payload = {
            "status": "ok",
            "topic": slug,
            "topic_label": db.CATEGORIES[slug]["label"],
            "accent_color": db.CATEGORIES[slug]["accent"],
            "count": len(history),
            "summaries": history,
        }
        _SUMMARY_CACHE.set(cache_key, payload)
    return _public_json(payload, SUMMARY_CACHE_SECONDS)


@app.post(
    "/api/v1/summaries/generate",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_admin_key)],
    tags=["admin"],
)
@limiter.limit("4/hour")
def trigger_summary_generation(
    request: Request,
    background: BackgroundTasks,
    week_start: str = Query(None, description="ISO date inside the week to (re)generate."),
    force: bool = Query(False, description="Regenerate topics that already have a row."),
):
    """Generate weekly summaries on demand.

    Admin-only and POST for the same reasons as /api/v1/cards/refresh: it mutates
    state, and under SUMMARY_GENERATOR=anthropic it spends money. A prefetcher
    following a GET would do both.

    It cannot resurrect a week whose articles have been purged. If the window is
    empty it writes nothing rather than summarising the void.
    """
    requested = _parse_week_start(week_start)

    def generate():
        summarizer.generate_weekly_summaries(week_start=requested, force=force)
        invalidate_public_caches()

    background.add_task(generate)
    return {
        "status": "accepted",
        "week_start": requested.isoformat() if requested else "last completed week",
        "force": force,
        "detail": "Generation started in the background. Poll /api/v1/summaries for results.",
    }


@app.post("/api/v1/telemetry/heartbeat", tags=["public"])
@limiter.limit("30/minute")
def user_heartbeat(request: Request, req: HeartbeatRequest):
    """Client ping endpoint to keep active session alive and track duration.

    Existing sessions are always refreshed. A NEW session_id costs one unit of
    the global new-sessions budget (503 when spent), because each one is a row
    and the id is client-chosen.
    """
    if not db.refresh_session(req.session_id):
        if not SESSION_BUDGET.take():
            raise _busy(SESSION_BUDGET, "new sessions")
        client_ip = request.client.host if request.client else "unknown"
        user_agent = (req.user_agent or request.headers.get("user-agent", ""))[:256]
        db.record_session_heartbeat(req.session_id, user_agent=user_agent, ip_address=client_ip)
    return JSONResponse(content={"status": "ok", "session_id": req.session_id})


@app.get(
    "/api/v1/telemetry/stats",
    dependencies=[Depends(require_admin_session)],
    tags=["admin"],
)
@limiter.limit("30/minute")
def get_telemetry_stats(request: Request):
    """System telemetry: storage layout, active sessions, engagement metrics.

    Protected because it exposes operational detail about the host and
    aggregate user behaviour.
    """
    stats = db.get_telemetry_summary(session_window_days=SESSION_METRIC_WINDOW_DAYS)
    stats["global_limits"] = {
        "requests": REQUEST_BUDGET.snapshot(),
        "swipes": SWIPE_BUDGET.snapshot(),
        "new_sessions": SESSION_BUDGET.snapshot(),
        "unmatched_requests_not_stored": {
            "count": _UNMATCHED_REQUESTS,
            "measured": True,
            "source": "in-process counter since start",
        },
    }
    return JSONResponse(content=stats)


@app.get(
    "/api/v1/telemetry/logs",
    dependencies=[Depends(require_admin_session)],
    tags=["admin"],
)
@limiter.limit("30/minute")
def get_telemetry_logs(request: Request):
    """Recent application & request logs. Protected: request paths and timings
    are operational detail."""
    return JSONResponse(content={"status": "ok", "logs": list(RECENT_LOGS)})


def run_cli_mode():
    """CLI mode to fetch news and deduplicate in the database.

    This path never runs lifespan(), so it owns the pool lifecycle itself.
    A small pool is plenty -- the CLI is single-threaded.
    """
    topics = ", ".join(db.CATEGORIES.keys())
    print("=" * 60)
    print(" PAPERSWAP MULTI-TOPIC NEWS SYNC (CLI + POSTGRESQL)")
    print(f" Topics: {topics}")
    print("=" * 60)

    db.init_pool(minconn=1, maxconn=4)
    try:
        print("[1/2] Initializing PostgreSQL Database...")
        db.init_db()

        print("[2/2] Fetching, syncing, and purging expired rows in the database...")
        # Call the pipeline rather than repeating its steps, so the CLI can never
        # drift from what the scheduler actually runs.
        refresh_pipeline()
        articles = db.get_balanced_feed(limit=FEED_DEFAULT_LIMIT)

        print("\n" + "=" * 60)
        print(f"SUCCESS! Database updated with {len(articles)} active news cards.")
        for row in db.get_enabled_categories():
            print(f"   - {row['label']:<20} {row['article_count']} article(s)")
        print("=" * 60)
    finally:
        db.close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Paperswap news feed API")
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode to sync news directly")
    parser.add_argument("--port", type=int, default=8000, help="Port for the FastAPI web server")
    args = parser.parse_args()

    if args.cli:
        run_cli_mode()
    else:
        import uvicorn
        print(f"Starting server on http://localhost:{args.port} ...")
        # The uvicorn CLI (the Dockerfile CMD) reads UVICORN_* from the
        # environment by itself; uvicorn.run() does not, so pass them on here
        # to keep `python main.py` shedding load the same way the container does.
        uvicorn.run(
            "main:app", host="0.0.0.0", port=args.port, reload=False,
            limit_concurrency=int(os.getenv("UVICORN_LIMIT_CONCURRENCY", "64")),
            timeout_keep_alive=int(os.getenv("UVICORN_TIMEOUT_KEEP_ALIVE", "3")),
        )
