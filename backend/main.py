import os
import argparse
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler

from news_fetcher import fetch_and_sync_news_to_db
import database as db


# Hours between automatic refreshes. Overridable via env (docker-compose sets REFRESH_INTERVAL).
REFRESH_INTERVAL_HOURS = float(os.getenv("REFRESH_INTERVAL", "12"))
# Articles older than this are purged on each refresh.
PURGE_OLDER_THAN_DAYS = int(os.getenv("PURGE_OLDER_THAN_DAYS", "7"))
# Default deck size served by /api/v1/feed (7 topics x ~10 cards).
FEED_DEFAULT_LIMIT = int(os.getenv("FEED_DEFAULT_LIMIT", "70"))

scheduler = BackgroundScheduler()


def refresh_pipeline():
    """One full refresh cycle: fetch + dedup new news, then purge week-old rows
    (and their cascaded swipes). Run on startup and on the scheduled interval."""
    fetch_and_sync_news_to_db()
    db.purge_old_articles(days=PURGE_OLDER_THAN_DAYS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the database, run an initial refresh, and start the 12-hour scheduler."""
    print("[Server Startup] Initializing PostgreSQL database...")
    db.init_db()
    print("[Server Startup] Running initial fetch + purge...")
    refresh_pipeline()
    print("[Server Startup] Database ready!")

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


app = FastAPI(
    title="PaperSwap Multi-Topic News Feed API",
    description=(
        "Dockerized API serving 9:16 mobile swipe news cards backed by PostgreSQL. "
        "Topics: Tech, Finance, Sports, Politics, Programming, Science, Beauty. "
        "Cards are rendered on the phone."
    ),
    version="3.0.0",
    lifespan=lifespan
)


@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    """Middleware to track request frequency for usage peak hours analysis."""
    path = request.url.path
    if not path.startswith("/static") and not path.endswith(".ico") and not path.startswith("/api/v1/telemetry"):
        try:
            db.log_request_event(path, request.method)
            add_log_entry(f"{request.method} {path} - 200 OK", "HTTP")
        except Exception:
            pass  # DB might still be initializing
    response = await call_next(request)
    return response


class SwipeRequest(BaseModel):
    article_id: int
    action: str  # 'read' or 'pass'


class HeartbeatRequest(BaseModel):
    session_id: str
    user_agent: Optional[str] = None



@app.get("/", response_class=HTMLResponse)
def read_root():
    """Serve the desktop/gallery dashboard."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard template missing</h1>", status_code=404)


@app.get("/mobile", response_class=HTMLResponse)
def read_mobile_app():
    """Serve the Tinder-Style Mobile Swipe App UI."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "mobile_preview.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Mobile template missing</h1>", status_code=404)


@app.get("/api/v1/categories")
def get_categories():
    """List every topic the feed can serve, with its brand colour and how many
    articles are currently stored. Drives the client topic filter bar."""
    categories = db.get_enabled_categories()
    return JSONResponse(content={
        "status": "ok",
        "count": len(categories),
        "categories": categories
    })


@app.get("/api/news")
@app.get("/api/v1/feed")
def get_news_feed(
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


@app.get("/api/cards/generate")
@app.get("/api/v1/cards/refresh")
def trigger_refresh():
    """Re-fetch news and deduplicate via the database. (No PNG cards — the phone renders them.)"""
    articles = fetch_and_sync_news_to_db()
    return JSONResponse(content={
        "status": "success",
        "count": len(articles),
        "database": "PostgreSQL",
        "articles": articles
    })


@app.post("/api/v1/swipe")
def record_swipe(req: SwipeRequest):
    """Record mobile user swipe actions (Read / Pass) into the database."""
    if req.action not in ("read", "pass"):
        return JSONResponse(content={"error": "Invalid action. Must be 'read' or 'pass'"}, status_code=400)

    db.record_user_swipe(req.article_id, req.action)
    return JSONResponse(content={
        "status": "recorded",
        "article_id": req.article_id,
        "action": req.action
    })


@app.get("/analytics", response_class=HTMLResponse)
def read_analytics_dashboard():
    """Serve the VM Telemetry & Engagement Analytics Dashboard."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "analytics.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Analytics template missing</h1>", status_code=404)


@app.post("/api/v1/telemetry/heartbeat")
def user_heartbeat(req: HeartbeatRequest, request: Request):
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


@app.get("/api/v1/telemetry/stats")
def get_telemetry_stats():
    """Get system telemetry (VM storage, active users, avg time connected, peak hours, top swipes, etc.)."""
    stats = db.get_telemetry_summary()
    return JSONResponse(content=stats)


@app.get("/api/v1/telemetry/logs")
def get_telemetry_logs():
    """Stream recent 50 application & request logs to the analytics dashboard terminal."""
    return JSONResponse(content={"status": "ok", "logs": list(RECENT_LOGS)})




def run_cli_mode():
    """CLI mode to fetch news and deduplicate in the database."""
    topics = ", ".join(db.CATEGORIES.keys())
    print("=" * 60)
    print(" PAPERSWAP MULTI-TOPIC NEWS SYNC (CLI + POSTGRESQL)")
    print(f" Topics: {topics}")
    print("=" * 60)
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
