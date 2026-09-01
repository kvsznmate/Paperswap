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
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from apscheduler.schedulers.background import BackgroundScheduler
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from news_fetcher import fetch_and_sync_news_to_db
import summarizer
import database as db


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
# Set true once HTTPS is in front (see PROJECT_STATUS.md, Caddy). Left false by
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
# come from the proxy, collapsing all users into one bucket. Deploying Caddy in
# front of this (planned, see PROJECT_STATUS.md) requires X-Forwarded-For
# handling before these limits mean anything per-client.
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
    title="PaperSwap Multi-Topic News Feed API",
    description=(
        "Dockerized API serving 9:16 mobile swipe news cards backed by PostgreSQL. "
        "Topics: Tech, Finance, Sports, Politics, Programming, Science, Beauty. "
        "Cards are rendered on the phone.\n\n"
        "**Authentication.** Read endpoints are public. Endpoints that mutate state "
        "or expose operational data require an `X-API-Key` header and are marked "
        "with a padlock below."
    ),
    version="3.1.0",
    lifespan=lifespan
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


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
    global _LOG_DROPPED

    response = await call_next(request)

    path = request.url.path
    status_code = response.status_code

    if path.startswith(LOG_EXCLUDED_PREFIXES) or path.endswith(".ico"):
        # Excluded from the table, but a burst of 401s against the telemetry
        # routes is worth seeing live -- that is someone probing the admin API.
        if status_code >= 400:
            add_log_entry(f"{request.method} {path} - {status_code}", "ERROR")
        return response

    if len(_LOG_BUFFER) == _LOG_BUFFER.maxlen:
        _LOG_DROPPED += 1
    _LOG_BUFFER.append((
        path,
        request.method,
        status_code,
        datetime.datetime.now(datetime.timezone.utc),
    ))

    add_log_entry(f"{request.method} {path} - {status_code}",
                  "ERROR" if status_code >= 400 else "HTTP")

    return response


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


class HeartbeatRequest(BaseModel):
    session_id: str
    user_agent: Optional[str] = None


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


@app.get("/", response_class=HTMLResponse, tags=["pages"])
def read_root():
    """Serve the desktop/gallery dashboard."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard template missing</h1>", status_code=404)


@app.get("/mobile", response_class=HTMLResponse, tags=["pages"])
def read_mobile_app():
    """Serve the Tinder-Style Mobile Swipe App UI."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "mobile_preview.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Mobile template missing</h1>", status_code=404)


@app.get("/api/v1/categories", tags=["public"])
@limiter.limit("60/minute")
def get_categories(request: Request):
    """List every topic the feed can serve, with its brand colour and how many
    articles are currently stored. Drives the client topic filter bar."""
    categories = db.get_enabled_categories()
    return JSONResponse(content={
        "status": "ok",
        "count": len(categories),
        "categories": categories
    })


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

    if balanced:
        articles = db.get_balanced_feed(limit=deck_limit, categories=selected)
    else:
        articles = db.get_latest_articles(limit=deck_limit, categories=selected)

    # Cold start: nothing stored yet, so pull a batch synchronously.
    if not articles:
        fetch_and_sync_news_to_db(categories=selected or None)
        articles = db.get_balanced_feed(limit=deck_limit, categories=selected)

    return JSONResponse(content={
        "status": "ok",
        "count": len(articles),
        "categories": selected or list(db.CATEGORIES.keys()),
        "balanced": balanced,
        "articles": articles
    })


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
    against NewsAPI and Google News. Browsers, link prefetchers and crawlers
    treat GET as safe and will follow it, which previously made a single
    prefetch enough to start burning the NewsAPI daily quota.

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
    """
    try:
        db.record_user_swipe(req.article_id, req.action)
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Article {req.article_id} not found.",
        )

    return {
        "status": "recorded",
        "article_id": req.article_id,
        "action": req.action,
    }


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

    template_path = os.path.join(os.path.dirname(__file__), "templates", "analytics.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Analytics template missing</h1>", status_code=404)


@app.get("/summaries", response_class=HTMLResponse, tags=["pages"])
def read_summaries_page():
    """Serve the weekly topic summary page."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "summaries.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Summaries template missing</h1>", status_code=404)


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
    summaries = db.get_topic_summaries(requested)

    if not summaries:
        return JSONResponse(content={
            "status": "ok",
            "count": 0,
            "week_start": requested.isoformat() if requested else None,
            "summaries": [],
            "detail": "No summaries stored for this week yet.",
        })

    present = {s["topic"] for s in summaries}
    return JSONResponse(content={
        "status": "ok",
        "count": len(summaries),
        "week_start": summaries[0]["week_start"],
        "week_end": summaries[0]["week_end"],
        "topics_missing": [s for s in db.CATEGORIES if s not in present],
        "summaries": summaries,
    })


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
    history = db.get_summary_history(slug, weeks=weeks)
    return JSONResponse(content={
        "status": "ok",
        "topic": slug,
        "topic_label": db.CATEGORIES[slug]["label"],
        "accent_color": db.CATEGORIES[slug]["accent"],
        "count": len(history),
        "summaries": history,
    })


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
    background.add_task(
        summarizer.generate_weekly_summaries, week_start=requested, force=force)
    return {
        "status": "accepted",
        "week_start": requested.isoformat() if requested else "last completed week",
        "force": force,
        "detail": "Generation started in the background. Poll /api/v1/summaries for results.",
    }


@app.post("/api/v1/telemetry/heartbeat", tags=["public"])
@limiter.limit("30/minute")
def user_heartbeat(request: Request, req: HeartbeatRequest):
    """Client ping endpoint to keep active session alive and track duration."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = req.user_agent or request.headers.get("user-agent", "")
    db.record_session_heartbeat(req.session_id, user_agent=user_agent, ip_address=client_ip)
    return JSONResponse(content={"status": "ok", "session_id": req.session_id})


RECENT_LOGS = collections.deque(maxlen=100)

def add_log_entry(message: str, level: str = "INFO"):
    """Helper to append timestamped application logs for live dashboard streaming."""
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    entry = f"[{now_str}] [{level}] {message}"
    RECENT_LOGS.append(entry)
    print(entry)

# Log startup message
add_log_entry("Application process initialized. Telemetry system online.", "SYSTEM")


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
    parser = argparse.ArgumentParser(description="Tech & Finance News Feed API")
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode to sync news directly")
    parser.add_argument("--port", type=int, default=8000, help="Port for the FastAPI web server")
    args = parser.parse_args()

    if args.cli:
        run_cli_mode()
    else:
        import uvicorn
        print(f"Starting server on http://localhost:{args.port} ...")
        uvicorn.run("main:app", host="0.0.0.0", port=args.port, reload=False)
