import os
import re
import requests
import feedparser
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

NEWS_API_KEY = os.getenv("NEWS_API_KEY", "").strip()

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
    """
    Generate a distinct, concise short summary for the card.
    Prevents the description from duplicating the headline.
    """
    clean_desc = clean_html(raw_desc)
    clean_title = clean_html(title)
    
    # Strip source suffix if present in description
    if ' - ' in clean_desc:
        clean_desc = clean_desc.rsplit(' - ', 1)[0].strip()

    # Check if raw description is empty, or just duplicates the title
    title_words = set(clean_title.lower().split())
    desc_words = set(clean_desc.lower().split())
    
    overlap = len(title_words.intersection(desc_words)) / max(len(title_words), 1)

    if clean_desc and overlap < 0.70 and len(clean_desc) > 30:
        return clean_desc

    # Generate a smart, distinct short summary based on key headline concepts
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

def fetch_from_news_api(category: str, query: str, count: int = 10) -> list:
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
                    "id": f"{category.lower()}_{idx+1}",
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

def fetch_from_rss(category: str, rss_url: str, count: int = 10) -> list:
    """Fallback fetch from Google News / Public RSS feeds."""
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

            # Publication date formatting
            pub_str = "Recently"
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                dt = datetime(*entry.published_parsed[:6])
                pub_str = dt.strftime("%b %d, %Y - %H:%M")

            img = CATEGORY_IMAGES[category][idx % len(CATEGORY_IMAGES[category])]

            results.append({
                "id": f"{category.lower()}_{idx+1}",
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

def get_latest_20_news() -> list:
    """Fetch 10 Tech and 10 Finance news articles (total 20)."""
    tech_news = fetch_from_news_api("TECH", "technology OR tech OR AI", 10)
    finance_news = fetch_from_news_api("FINANCE", "finance OR stock OR market", 10)

    if len(tech_news) < 10:
        rss_tech_url = "https://news.google.com/rss/search?q=technology+industry+AI&hl=en-US&gl=US&ceid=US:en"
        tech_news = fetch_from_rss("TECH", rss_tech_url, 10)

    if len(finance_news) < 10:
        rss_fin_url = "https://news.google.com/rss/search?q=finance+stocks+markets&hl=en-US&gl=US&ceid=US:en"
        finance_news = fetch_from_rss("FINANCE", rss_fin_url, 10)

    total_news = tech_news[:10] + finance_news[:10]
    
    for idx, item in enumerate(total_news, start=1):
        item["index"] = idx

    return total_news

if __name__ == "__main__":
    news = get_latest_20_news()
    print(f"Successfully fetched {len(news)} news articles!")
    for item in news:
        print(f"[{item['index']}] [{item['category']}] Title: {item['title']}")
        print(f"    Summary: {item['description']}\n")
