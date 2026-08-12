import os
import re
import requests
import feedparser
from datetime import datetime
from dotenv import load_dotenv

import database as db

load_dotenv()

NEWS_API_KEY = os.getenv("NEWS_API_KEY", "").strip()

# How many articles to pull per topic on each refresh cycle.
# 7 topics x 12 = 84 candidate articles per batch (before dedup).
ARTICLES_PER_CATEGORY = int(os.getenv("ARTICLES_PER_CATEGORY", "12"))


# ---------------------------------------------------------------------------
# TOPIC FEED CONFIGURATION
# One entry per slug in database.CATEGORIES. To add a topic: add it to
# database.CATEGORIES, then add a matching block here. Nothing else changes.
#
#   newsapi_query   -> passed to NewsAPI /v2/everything (primary source)
#   rss_url         -> Google News RSS (fallback when NewsAPI is unset/short)
#   summary_fallback-> used when the feed gives no usable description
#   images          -> hero image used only when the article has none of its own
# ---------------------------------------------------------------------------
TOPIC_FEEDS = {
    "TECH": {
        "newsapi_query": "technology OR tech OR AI",
        "rss_url": "https://news.google.com/rss/search?q=technology+industry+AI&hl=en-US&gl=US&ceid=US:en",
        "summary_fallback": "Breakthrough technology updates and strategic market shifts impacting digital infrastructure and software.",
        "images": [
            "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=800&q=80",
            "https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=800&q=80",
            "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=800&q=80",
            "https://images.unsplash.com/photo-1485827404703-89b55fcc595e?auto=format&fit=crop&w=800&q=80",
        ],
    },
    "FINANCE": {
        "newsapi_query": "finance OR stocks OR markets OR earnings",
        "rss_url": "https://news.google.com/rss/search?q=finance+stocks+markets&hl=en-US&gl=US&ceid=US:en",
        "summary_fallback": "Financial sector analysis covering corporate earnings, market trends, and strategic investment movements.",
        "images": [
            "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?auto=format&fit=crop&w=800&q=80",
            "https://images.unsplash.com/photo-1590283603385-17ffb3a7f29f?auto=format&fit=crop&w=800&q=80",
            "https://images.unsplash.com/photo-1559526324-4b87b5e36e44?auto=format&fit=crop&w=800&q=80",
            "https://images.unsplash.com/photo-1460925895917-afdab827c52f?auto=format&fit=crop&w=800&q=80",
        ],
    },
    "SPORTS": {
        "newsapi_query": "sports OR football OR basketball OR soccer OR tennis",
        "rss_url": "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-US&gl=US&ceid=US:en",
        "summary_fallback": "Match results, transfer moves, and championship standings from across the world of professional sport.",
        "images": [
            "https://loremflickr.com/800/1200/stadium,sport?lock=11",
            "https://loremflickr.com/800/1200/football?lock=12",
            "https://loremflickr.com/800/1200/basketball?lock=13",
            "https://loremflickr.com/800/1200/athlete,running?lock=14",
        ],
    },
    "POLITICS": {
        "newsapi_query": "politics OR election OR parliament OR government policy",
        "rss_url": "https://news.google.com/rss/search?q=politics+election+government+policy&hl=en-US&gl=US&ceid=US:en",
        "summary_fallback": "Legislative developments, election coverage, and policy debates shaping domestic and international governance.",
        "images": [
            "https://loremflickr.com/800/1200/parliament,building?lock=21",
            "https://loremflickr.com/800/1200/capitol?lock=22",
            "https://loremflickr.com/800/1200/voting,election?lock=23",
            "https://loremflickr.com/800/1200/flag,government?lock=24",
        ],
    },
    "PROGRAMMING": {
        "newsapi_query": "programming OR \"software development\" OR \"open source\" OR developer tools",
        "rss_url": "https://news.google.com/rss/search?q=programming+software+development+open+source+developer&hl=en-US&gl=US&ceid=US:en",
        "summary_fallback": "Language releases, framework updates, and open-source project news relevant to working software engineers.",
        "images": [
            "https://loremflickr.com/800/1200/code,screen?lock=31",
            "https://loremflickr.com/800/1200/keyboard,programming?lock=32",
            "https://loremflickr.com/800/1200/terminal,computer?lock=33",
            "https://loremflickr.com/800/1200/developer,laptop?lock=34",
        ],
    },
    "SCIENCE": {
        "newsapi_query": "science OR research OR space OR physics OR biology",
        "rss_url": "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-US&gl=US&ceid=US:en",
        "summary_fallback": "Peer-reviewed findings, space missions, and laboratory breakthroughs reported from the global research community.",
        "images": [
            "https://loremflickr.com/800/1200/laboratory,science?lock=41",
            "https://loremflickr.com/800/1200/space,galaxy?lock=42",
            "https://loremflickr.com/800/1200/microscope?lock=43",
            "https://loremflickr.com/800/1200/telescope,observatory?lock=44",
        ],
    },
    "BEAUTY": {
        "newsapi_query": "beauty OR skincare OR cosmetics OR makeup",
        "rss_url": "https://news.google.com/rss/search?q=beauty+skincare+cosmetics+makeup&hl=en-US&gl=US&ceid=US:en",
        "summary_fallback": "Product launches, skincare research, and brand movements across the global beauty and cosmetics industry.",
        "images": [
            "https://loremflickr.com/800/1200/cosmetics?lock=51",
            "https://loremflickr.com/800/1200/skincare?lock=52",
            "https://loremflickr.com/800/1200/makeup?lock=53",
            "https://loremflickr.com/800/1200/perfume,beauty?lock=54",
        ],
    },
}

# Backwards-compatible alias: older code imported CATEGORY_IMAGES directly.
CATEGORY_IMAGES = {slug: cfg["images"] for slug, cfg in TOPIC_FEEDS.items()}


def get_topic_config(category: str) -> dict:
    """Return the feed config for a topic, falling back to the default topic."""
    slug = db.normalize_category(category)
    return TOPIC_FEEDS.get(slug, TOPIC_FEEDS[db.DEFAULT_CATEGORY])


def fallback_image(category: str, idx: int) -> str:
    """Deterministic hero image for a topic when the article ships without one."""
    images = get_topic_config(category)["images"]
    return images[idx % len(images)]


def clean_html(text: str) -> str:
    """Strip HTML tags and clean whitespace from text."""
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', text)
    return ' '.join(clean.split())


def extract_rss_image(entry) -> str:
    """Pull a real article image out of an RSS entry when the publisher provides
    one (media:content, media:thumbnail, an image enclosure, or an <img> inside
    the summary HTML). Returns '' when nothing usable is present."""
    try:
        for media in getattr(entry, 'media_content', []) or []:
            if media.get('url'):
                return media['url']

        for thumb in getattr(entry, 'media_thumbnail', []) or []:
            if thumb.get('url'):
                return thumb['url']

        for link in getattr(entry, 'links', []) or []:
            if link.get('rel') == 'enclosure' and str(link.get('type', '')).startswith('image'):
                return link.get('href', '')

        raw = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw)
        if match:
            return match.group(1)
    except Exception:
        pass
    return ""


def generate_short_summary(title: str, category: str, raw_desc: str, source: str) -> str:
    """Generate a distinct short summary for a news item.

    Prefers the publisher's own description; only falls back to a generated line
    when the description is missing or just echoes the headline.
    """
    clean_desc = clean_html(raw_desc)
    clean_title = clean_html(title)

    if ' - ' in clean_desc:
        clean_desc = clean_desc.rsplit(' - ', 1)[0].strip()

    title_words = set(clean_title.lower().split())
    desc_words = set(clean_desc.lower().split())
    overlap = len(title_words.intersection(desc_words)) / max(len(title_words), 1)

    if clean_desc and overlap < 0.70 and len(clean_desc) > 30:
        return clean_desc

    # Cross-topic keyword heuristics, checked before the per-topic fallback.
    title_lower = title.lower()
    keyword_rules = [
        (("ai", "artificial intelligence", "machine learning"),
         f"Industry developments in AI model scaling, infrastructure, and enterprise adoption reported by {source}."),
        (("layoff", "job cuts", "hiring"),
         f"Workforce adjustments and operational efficiency shifts across key sector players as reported by {source}."),
        (("chip", "nvidia", "semiconductor", "amd"),
         "Semiconductor supply dynamics, hardware innovation, and market demand driving global hardware valuations."),
        (("stock", "dow", "nasdaq", "s&p", "inflation"),
         f"Key market indices react to economic indicators, investor sentiment, and recent earnings reports according to {source}."),
        (("tariff", "trade deal", "sanction"),
         f"Macroeconomic policies, trade regulations, and international market impacts analyzed by {source}."),
        (("election", "vote", "ballot", "campaign"),
         f"Campaign developments, polling shifts, and electoral outcomes covered by {source}."),
        (("nasa", "spacex", "orbit", "telescope"),
         "Mission milestones and observational findings advancing our understanding of the solar system and beyond."),
        (("transfer", "signing", "playoff", "championship", "final"),
         f"Squad changes, fixture outcomes, and title-race implications reported by {source}."),
        (("open source", "release", "framework", "runtime", "sdk"),
         f"Tooling and release notes for developers, covering API changes and ecosystem impact, via {source}."),
        (("skincare", "serum", "cosmetic", "fragrance"),
         "Formulation trends, ingredient science, and brand strategy shaping the consumer beauty market."),
    ]

    for keywords, summary in keyword_rules:
        if any(kw in title_lower for kw in keywords):
            return summary

    return get_topic_config(category)["summary_fallback"]


def fetch_from_news_api(category: str, count: int = ARTICLES_PER_CATEGORY) -> list:
    """Fetch news for one topic from NewsAPI.org."""
    if not NEWS_API_KEY or NEWS_API_KEY == "your_news_api_key_here":
        return []

    config = get_topic_config(category)
    params = {
        "q": config["newsapi_query"],
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": count,
        "apiKey": NEWS_API_KEY,
    }

    try:
        resp = requests.get("https://newsapi.org/v2/everything", params=params, timeout=8)
        if resp.status_code != 200:
            print(f"[Warning] NewsAPI returned {resp.status_code} for {category}")
            return []

        articles = resp.json().get("articles", [])
        results = []
        for idx, art in enumerate(articles[:count]):
            img = art.get("urlToImage") or fallback_image(category, idx)

            pub_time = art.get("publishedAt", "")
            try:
                dt = datetime.strptime(pub_time[:19], "%Y-%m-%dT%H:%M:%S")
                pub_str = dt.strftime("%b %d, %Y - %H:%M")
            except Exception:
                pub_str = pub_time or "Recently"

            src_name = art.get("source", {}).get("name", "News API")
            title = art.get("title", "Untitled News")

            results.append({
                "title": title,
                "description": generate_short_summary(title, category, art.get("description", ""), src_name),
                "source": src_name,
                "published_at": pub_str,
                "category": category,
                "image_url": img,
                "url": art.get("url", "#"),
            })
        return results
    except Exception as e:
        print(f"[Warning] NewsAPI fetch failed for {category}: {e}")
    return []


def fetch_from_rss(category: str, count: int = ARTICLES_PER_CATEGORY) -> list:
    """Fetch news for one topic from the Google News RSS fallback."""
    config = get_topic_config(category)
    results = []

    try:
        feed = feedparser.parse(config["rss_url"])
        for idx, entry in enumerate(feed.entries[:count]):
            source = "Google News"
            if hasattr(entry, 'source') and hasattr(entry.source, 'title'):
                source = entry.source.title
            elif ' - ' in entry.title:
                source = entry.title.rsplit(' - ', 1)[1]

            title = entry.title
            if ' - ' in title:
                title = title.rsplit(' - ', 1)[0]

            raw_desc = getattr(entry, 'summary', getattr(entry, 'description', ''))

            pub_str = "Recently"
            if getattr(entry, 'published_parsed', None):
                pub_str = datetime(*entry.published_parsed[:6]).strftime("%b %d, %Y - %H:%M")

            results.append({
                "title": title,
                "description": generate_short_summary(title, category, raw_desc, source),
                "source": source,
                "published_at": pub_str,
                "category": category,
                "image_url": extract_rss_image(entry) or fallback_image(category, idx),
                "url": getattr(entry, 'link', '#'),
            })
    except Exception as e:
        print(f"[Error] RSS feed fetch error for {category}: {e}")
    return results


def fetch_topic(category: str, count: int = ARTICLES_PER_CATEGORY) -> list:
    """Fetch one topic, preferring NewsAPI and falling back to RSS when the
    key is missing or the API returns a short batch."""
    items = fetch_from_news_api(category, count)
    if len(items) < count:
        rss_items = fetch_from_rss(category, count)
        if len(rss_items) > len(items):
            items = rss_items
    return items[:count]


def fetch_and_sync_news_to_db(categories=None) -> list:
    """
    1. Fetches ARTICLES_PER_CATEGORY items for every enabled topic
       (Tech, Finance, Sports, Politics, Programming, Science, Beauty).
    2. Checks the database to see if each article already exists.
    3. Saves ONLY new articles (deduplicated by MD5 article_key).
    4. Returns a fresh topic-balanced deck from the database.

    Cards are rendered on the phone from the article fields, not server-side.
    """
    target_categories = db.clean_category_filter(categories) or list(db.CATEGORIES.keys())

    raw_articles = []
    per_topic_counts = {}

    for category in target_categories:
        items = fetch_topic(category, ARTICLES_PER_CATEGORY)
        per_topic_counts[category] = len(items)
        raw_articles.extend(items)
        print(f"[Fetch] {category}: {len(items)} item(s) retrieved.")

    new_count = 0
    skipped_count = 0

    for idx, item in enumerate(raw_articles, start=1):
        item['index'] = idx
        article_key = db.generate_article_key(item['title'], item['url'])

        if db.is_article_in_db(article_key):
            skipped_count += 1
        else:
            db.save_article(item)
            new_count += 1

    breakdown = ", ".join(f"{cat} {count}" for cat, count in per_topic_counts.items())
    print(f"[DB Sync Summary] {len(raw_articles)} items across {len(target_categories)} topics "
          f"({breakdown}): {new_count} NEW inserted, {skipped_count} ALREADY EXISTED.")

    feed_limit = ARTICLES_PER_CATEGORY * len(target_categories)
    return db.get_balanced_feed(limit=feed_limit, categories=target_categories)


if __name__ == "__main__":
    db.init_db()
    articles = fetch_and_sync_news_to_db()
    print(f"Total articles in balanced deck: {len(articles)}")
