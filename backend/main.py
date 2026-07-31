import os
import argparse
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager

from news_fetcher import fetch_and_sync_news_to_db
import database as db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the PostgreSQL database and sync news on startup."""
    print("[Server Startup] Initializing PostgreSQL database...")
    db.init_db()
    print("[Server Startup] Syncing Tech & Finance news with the database...")
    fetch_and_sync_news_to_db()
    print("[Server Startup] Database ready!")
    yield


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

    print("[2/2] Fetching and syncing news to the database...")
    articles = fetch_and_sync_news_to_db()
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
