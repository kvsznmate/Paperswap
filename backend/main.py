import sys
import os
import argparse
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from news_fetcher import get_latest_20_news
from card_generator import generate_all_cards

from contextlib import asynccontextmanager

# Cache current news articles in memory
cached_news = []

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Fetch initial news and generate cards on startup."""
    global cached_news
    print("[Server Startup] Fetching latest 20 Tech & Finance news articles...")
    cached_news = get_latest_20_news()
    print(f"[Server Startup] Generating visual PNG cards for {len(cached_news)} news items...")
    generate_all_cards(cached_news, OUTPUT_DIR)
    print("[Server Startup] Visual cards ready!")
    yield

app = FastAPI(title="Tech & Finance News Visual Card Engine", lifespan=lifespan)

# Output directory for cards
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output", "cards")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Mount output folder as static file route
app.mount("/output/cards", StaticFiles(directory=OUTPUT_DIR), name="cards")

@app.get("/", response_class=HTMLResponse)
def read_root():
    """Serve the web visual dashboard."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard template missing</h1>", status_code=404)

@app.get("/api/news")
def get_news():
    """API endpoint to get the latest 20 news articles."""
    global cached_news
    if not cached_news:
        cached_news = get_latest_20_news()
    return JSONResponse(content={"status": "ok", "count": len(cached_news), "articles": cached_news})

@app.get("/api/cards/generate")
def trigger_generate_cards():
    """API endpoint to re-fetch news and regenerate visual PNG cards."""
    global cached_news
    cached_news = get_latest_20_news()
    files = generate_all_cards(cached_news, OUTPUT_DIR)
    return JSONResponse(content={
        "status": "success",
        "count": len(files),
        "output_directory": OUTPUT_DIR,
        "files": [os.path.basename(f) for f in files]
    })

def run_cli_mode():
    """CLI mode to fetch 20 news items and build visual card images directly."""
    print("=" * 60)
    print(" TECH & FINANCE NEWS VISUAL CARD GENERATOR (CLI)")
    print("=" * 60)
    print("[1/2] Fetching 20 latest news articles (10 Tech + 10 Finance)...")
    articles = get_latest_20_news()
    print(f"[OK] Fetched {len(articles)} news items.\n")

    print("[2/2] Rendering visual PNG cards into backend/output/cards/...")
    cards = generate_all_cards(articles, OUTPUT_DIR)
    print("\n" + "=" * 60)
    print(f"SUCCESS! {len(cards)} visual cards created successfully:")
    for card in cards:
        print(f"   * {card}")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tech & Finance News Visual Card Engine")
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode to generate cards directly")
    parser.add_argument("--port", type=int, default=8000, help="Port for the FastAPI web server")
    args = parser.parse_args()

    if args.cli:
        run_cli_mode()
    else:
        import uvicorn
        print(f"Starting server on http://localhost:{args.port} ...")
        uvicorn.run("main:app", host="0.0.0.0", port=args.port, reload=False)
