import os
import secrets
import argparse
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
import database as db


# Hours between automatic refreshes. Overridable via env (docker-compose sets REFRESH_INTERVAL).
REFRESH_INTERVAL_HOURS = float(os.getenv("REFRESH_INTERVAL", "12"))
# Articles older than this are purged on each refresh.
PURGE_OLDER_THAN_DAYS = int(os.getenv("PURGE_OLDER_THAN_DAYS", "7"))
# Default deck size served by /api/v1/feed (7 topics x ~10 cards).
FEED_DEFAULT_LIMIT = int(os.getenv("FEED_DEFAULT_LIMIT", "70"))

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


def require_admin_key(provided: Optional[str] = Depends(_api_key_header)) -> None:
    """Reject the request unless it carries the configured admin key.

    Fails CLOSED: if ADMIN_API_KEY is unset the endpoint is unavailable rather
    than open, so a missing env var can never silently publish these routes.
    """
    if not ADMIN_API_KEY:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "ADMIN_API_KEY is not configured on the server.",
        )
    # compare_digest is constant-time, so response latency does not leak how many
    # leading characters of a guess were correct. It needs bytes to be safe
    # against non-ASCII input, which would raise TypeError on the str form.
    if not provided or not secrets.compare_digest(
        provided.encode("utf-8"), ADMIN_API_KEY.encode("utf-8")
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or missing X-API-Key.",
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
    """One full refresh cycle: fetch + dedup new news, then purge week-old rows
    (and their cascaded swipes). Run on startup and on the scheduled interval."""
    fetch_and_sync_news_to_db()
    db.purge_old_articles(days=PURGE_OLDER_THAN_DAYS)


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
    scheduler.start()
    print(f"[Scheduler] Auto-refresh every {REFRESH_INTERVAL_HOURS} h; "
          f"purging articles older than {PURGE_OLDER_THAN_DAYS} days.")

    yield

    scheduler.shutdown(wait=False)
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
    """Record request frequency for the peak-hours analytics.

    Logging happens AFTER call_next so the recorded status is the real one. The
    previous version wrote a hardcoded "200 OK" before the response existed,
    which meant every 401, 404 and 422 was reported to the dashboard as a
    success -- a fabricated measurement of exactly the kind ADR-010 removed.

    Rejected requests are surfaced in the live console but NOT written to
    request_logs: a flood of 401s should not become a flood of INSERTs.
    """
    response = await call_next(request)

    path = request.url.path
    status_code = response.status_code
    skip = path.startswith(LOG_EXCLUDED_PREFIXES) or path.endswith(".ico")

    if status_code >= 400:
        # In-memory only (bounded deque), so an error burst costs no disk.
        add_log_entry(f"{request.method} {path} - {status_code}", "ERROR")
    elif not skip:
        try:
            db.log_request_event(path, request.method)
            add_log_entry(f"{request.method} {path} - {status_code}", "HTTP")
        except Exception:
            pass  # DB might still be initializing

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


@app.get("/analytics", response_class=HTMLResponse, tags=["pages"])
def read_analytics_dashboard():
    """Serve the VM Telemetry & Engagement Analytics Dashboard.

    Intentionally NOT behind require_admin_key: a browser cannot attach an
    X-API-Key header to a top-level navigation, so gating this route would make
    the dashboard unreachable. The page itself is an empty shell — every figure
    it displays comes from /api/v1/telemetry/*, which IS protected. The page
    prompts for the key and sends it as a header on those fetches.
    """
    template_path = os.path.join(os.path.dirname(__file__), "templates", "analytics.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Analytics template missing</h1>", status_code=404)


@app.post("/api/v1/telemetry/heartbeat", tags=["public"])
@limiter.limit("30/minute")
def user_heartbeat(request: Request, req: HeartbeatRequest):
    """Client ping endpoint to keep active session alive and track duration."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = req.user_agent or request.headers.get("user-agent", "")
    db.record_session_heartbeat(req.session_id, user_agent=user_agent, ip_address=client_ip)
    return JSONResponse(content={"status": "ok", "session_id": req.session_id})


import collections
import datetime

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
    dependencies=[Depends(require_admin_key)],
    tags=["admin"],
)
@limiter.limit("30/minute")
def get_telemetry_stats(request: Request):
    """System telemetry: storage layout, active sessions, engagement metrics.

    Protected because it exposes operational detail about the host and
    aggregate user behaviour.
    """
    stats = db.get_telemetry_summary()
    return JSONResponse(content=stats)


@app.get(
    "/api/v1/telemetry/logs",
    dependencies=[Depends(require_admin_key)],
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

        print("[2/2] Fetching, syncing, and purging old news in the database...")
        fetch_and_sync_news_to_db()
        db.purge_old_articles(days=PURGE_OLDER_THAN_DAYS)
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
