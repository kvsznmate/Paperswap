import os
import re
import requests
import feedparser
from datetime import datetime
from dotenv import load_dotenv

import database as db
from card_generator import create_visual_card

load_dotenv()

NEWS_API_KEY = os.getenv("NEWS_API_KEY", "").strip()
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output", "cards")
os.makedirs(OUTPUT_DIR, exist_ok=True)

CATEGORY_IMAGES = {
    "TECH": [
        "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1485827404703-89b55fcc595e?auto=format&fit=crop&w=800&q=80",
    ],
    "FINANCE": [
        "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1590283603385-17ffb3a7f29f?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1559526324-4b87b5e36e44?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1460925895917-afdab827c52f?auto=format&fit=crop&w=800&q=80",
    ]
}

def clean_html(text: str) -> str:
    """Strip HTML tags and clean whitespace from text."""
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', text)
    return ' '.join(clean.split())

def generate_short_summary(title: str, category: str, raw_desc: str, source: str) -> str:
    """Generate distinct short summary for news item."""
    clean_desc = clean_html(raw_desc)
    clean_title = clean_html(title)
    
    if ' - ' in clean_desc:
        clean_desc = clean_desc.rsplit(' - ', 1)[0].strip()

    title_words = set(clean_title.lower().split())
    desc_words = set(clean_desc.lower().split())
    overlap = len(title_words.intersection(desc_words)) / max(len(title_words), 1)

    if clean_desc and overlap < 0.70 and len(clean_desc) > 30:
        return clean_desc

    title_lower = title.lower()
    if "ai" in title_lower or "artificial intelligence" in title_lower:
        return f"Industry developments and strategic moves in AI model scaling, infrastructure, and enterprise adoption reported by {source}."
    elif "layoff" in title_lower or "job" in title_lower or "cut" in title_lower:
        return f"Workforce adjustments and operational efficiency shifts across key sector players as reported by {source}."
    elif "chip" in title_lower or "nvidia" in title_lower or "semiconductor" in title_lower or "amd" in title_lower:
        return f"Semiconductor supply dynamics, hardware innovation, and market demand driving global hardware valuations."
    elif "stock" in title_lower or "dow" in title_lower or "nasdaq" in title_lower or "s&p" in title_lower or "market" in title_lower:
        return f"Key market indices react to economic indicators, investor sentiment, and recent earnings reports according to {source}."
    elif "tariff" in title_lower or "trade" in title_lower or "china" in title_lower:
        return f"Macroeconomic policies, trade regulations, and international market impacts analyzed by {source}."
    elif category == "TECH":
        return f"Breakthrough technology updates and strategic market shifts impacting digital infrastructure and software."
    else:
        return f"Financial sector analysis covering corporate earnings, market trends, and strategic investment movements."

def fetch_from_news_api(category: str, query: str, count: int = 25) -> list:
    """Fetch news from NewsAPI.org."""
    if not NEWS_API_KEY or NEWS_API_KEY == "your_news_api_key_here":
        return []
    
    url = f"https://newsapi.org/v2/everything?q={query}&language=en&sortBy=publishedAt&pageSize={count}&apiKey={NEWS_API_KEY}"
    try:
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            articles = data.get("articles", [])
            results = []
            for idx, art in enumerate(articles[:count]):
                img = art.get("urlToImage") or CATEGORY_IMAGES[category][idx % len(CATEGORY_IMAGES[category])]
                pub_time = art.get("publishedAt", "")
                try:
                    dt = datetime.strptime(pub_time[:19], "%Y-%m-%dT%H:%M:%S")
                    pub_str = dt.strftime("%b %d, %Y - %H:%M")
                except Exception:
                    pub_str = pub_time or "Recently"

                src_name = art.get("source", {}).get("name", "News API")
                title = art.get("title", "Untitled News")
                raw_desc = art.get("description", "")
                short_summary = generate_short_summary(title, category, raw_desc, src_name)

                results.append({
                    "title": title,
                    "description": short_summary,
                    "source": src_name,
                    "published_at": pub_str,
                    "category": category,
                    "image_url": img,
                    "url": art.get("url", "#")
                })
            return results
    except Exception as e:
        print(f"[Warning] NewsAPI fetch failed for {category}: {e}")
    return []

def fetch_from_rss(category: str, rss_url: str, count: int = 25) -> list:
    """Fetch news from Google News / RSS fallback."""
    results = []
    try:
        feed = feedparser.parse(rss_url)
        entries = feed.entries[:count]
        for idx, entry in enumerate(entries):
            source = "Google News"
            if hasattr(entry, 'source') and hasattr(entry.source, 'title'):
                source = entry.source.title
            elif ' - ' in entry.title:
                parts = entry.title.rsplit(' - ', 1)
                source = parts[1]

            title = entry.title
            if ' - ' in title:
                title = title.rsplit(' - ', 1)[0]

            raw_desc = getattr(entry, 'summary', getattr(entry, 'description', ''))
            short_summary = generate_short_summary(title, category, raw_desc, source)

            pub_str = "Recently"
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                dt = datetime(*entry.published_parsed[:6])
                pub_str = dt.strftime("%b %d, %Y - %H:%M")

            img = CATEGORY_IMAGES[category][idx % len(CATEGORY_IMAGES[category])]

            results.append({
                "title": title,
                "description": short_summary,
                "source": source,
                "published_at": pub_str,
                "category": category,
                "image_url": img,
                "url": getattr(entry, 'link', '#')
            })
    except Exception as e:
        print(f"[Error] RSS feed fetch error for {category}: {e}")
    return results

def fetch_and_sync_news_to_db() -> list:
    """
    1. Fetches 25 Tech + 25 Finance = 50 news items.
    2. Checks SQLite database if news already exists.
    3. Saves ONLY new articles to DB and renders 9:16 PNG cards.
    4. Returns latest 50 articles from SQLite database.
    """
    tech_news = fetch_from_news_api("TECH", "technology OR tech OR AI", 25)
    finance_news = fetch_from_news_api("FINANCE", "finance OR stock OR market", 25)

    if len(tech_news) < 25:
        rss_tech_url = "https://news.google.com/rss/search?q=technology+industry+AI&hl=en-US&gl=US&ceid=US:en"
        tech_news = fetch_from_rss("TECH", rss_tech_url, 25)

    if len(finance_news) < 25:
        rss_fin_url = "https://news.google.com/rss/search?q=finance+stocks+markets&hl=en-US&gl=US&ceid=US:en"
        finance_news = fetch_from_rss("FINANCE", rss_fin_url, 25)

    raw_articles = tech_news[:25] + finance_news[:25]
    
    new_count = 0
    skipped_count = 0

    for idx, item in enumerate(raw_articles, start=1):
        item['index'] = idx
        article_key = db.generate_article_key(item['title'], item['url'])

        # Check if already in DB
        if db.is_article_in_db(article_key):
            skipped_count += 1
        else:
            filename = f"{article_key}_card.png"
            filepath = os.path.join(OUTPUT_DIR, filename)
            try:
                create_visual_card(item, filepath)
            except Exception as e:
                print(f"[Warning] Card rendering error: {e}")

            db.save_article(item, filename)
            new_count += 1

    print(f"[DB Sync Summary] Processed {len(raw_articles)} items: {new_count} NEW inserted, {skipped_count} ALREADY EXISTED.")

    # Retrieve latest 50 articles from SQLite database
    latest_articles = db.get_latest_articles(50)
    return latest_articles

if __name__ == "__main__":
    articles = fetch_and_sync_news_to_db()
    print(f"Total articles in DB query: {len(articles)}")
