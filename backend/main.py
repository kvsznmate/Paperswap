import os
import argparse
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler

from news_fetcher import fetch_and_sync_news_to_db
import database as db


# Hours between automatic refreshes. Overridable via env (docker-compose sets REFRESH_INTERVAL).
REFRESH_INTERVAL_HOURS = float(os.getenv("REFRESH_INTERVAL", "12"))
# Articles older than this are purged on each refresh.
PURGE_OLDER_THAN_DAYS = int(os.getenv("PURGE_OLDER_THAN_DAYS", "7"))

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
    title="Tinder-Style Tech & Finance News Feed API",
    description="Dockerized API serving 9:16 mobile swipe news cards backed by PostgreSQL. Cards are rendered on the phone.",
    version="2.0.0",
    lifespan=lifespan
)


class SwipeRequest(BaseModel):
    article_id: int
    action: str  # 'read' or 'pass'


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


@app.get("/api/news")
@app.get("/api/v1/feed")
def get_news_feed():
    """API endpoint serving latest 50 news card items from the database."""
    articles = db.get_latest_articles(50)
    if not articles:
        articles = fetch_and_sync_news_to_db()
    return JSONResponse(content={"status": "ok", "count": len(articles), "articles": articles})


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


def run_cli_mode():
    """CLI mode to fetch news and deduplicate in the database."""
    print("=" * 60)
    print(" TECH & FINANCE NEWS FEED SYNC (CLI + POSTGRESQL)")
    print("=" * 60)
    print("[1/2] Initializing PostgreSQL Database...")
    db.init_db()

    print("[2/2] Fetching, syncing, and purging old news in the database...")
    fetch_and_sync_news_to_db()
    db.purge_old_articles(days=PURGE_OLDER_THAN_DAYS)
    articles = db.get_latest_articles(50)
    print("\n" + "=" * 60)
    print(f"SUCCESS! Database updated with {len(articles)} active news cards.")
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
