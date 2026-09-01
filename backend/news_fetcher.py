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


# ---------------------------------------------------------------------------
# Post-fetch topic classification
#
# The category used to be decided by WHICH QUERY returned the article, so a
# beauty story that happened to match "market" was stored as FINANCE. These
# keywords let us read the article text and label it properly instead.
# Add a new topic by adding one entry here (plus CATEGORY_IMAGES below).
# ---------------------------------------------------------------------------
CATEGORY_KEYWORDS = {
    "BEAUTY": [
        "beauty", "cosmetic", "cosmetics", "skincare", "skin care", "makeup",
        "make-up", "fragrance", "perfume", "haircare", "hair care", "shampoo",
        "salon", "sephora", "ulta", "l'oreal", "loreal", "estee lauder",
        "shiseido", "glossier", "mascara", "lipstick", "moisturizer", "serum",
        "sunscreen", "retinol", "hyaluronic", "botox", "manicure",
        "nail polish", "anti-aging", "dermatology", "dermatologist", "grooming",
        "fashion", "runway", "couture", "beauty brand",
    ],
    "SPORTS": [
        "sports", "football", "soccer", "basketball", "baseball", "tennis",
        "golf", "cricket", "rugby", "hockey", "olympics", "olympic", "nba",
        "nfl", "nhl", "mlb", "fifa", "uefa", "premier league", "championship",
        "playoff", "playoffs", "tournament", "striker", "quarterback",
        "midfielder", "athlete", "world cup", "formula 1", "grand prix",
        "transfer window", "league title",
    ],
    "SCIENCE": [
        "science", "scientific", "scientists", "researchers", "nasa", "spacex",
        "orbit", "satellite", "telescope", "astronomy", "physics", "biology",
        "chemistry", "genome", "dna", "species", "fossil", "vaccine",
        "clinical trial", "peer-reviewed", "laboratory", "neuroscience",
        "particle", "asteroid", "climate change",
    ],
    "PROGRAMMING": [
        "programming", "python", "javascript", "typescript", "rust", "golang",
        "kotlin", "open source", "open-source", "github", "gitlab",
        "framework", "sdk", "compiler", "runtime", "devops", "kubernetes",
        "docker", "linux", "codebase", "refactor", "npm", "react",
        "node.js", "pull request", "software development", "developer tools",
    ],
    "POLITICS": [
        "politics", "political", "election", "elections", "senate",
        "congress", "parliament", "president", "prime minister", "governor",
        "campaign", "ballot", "voter", "voters", "legislation", "lawmakers",
        "democrat", "democrats", "republican", "republicans", "white house",
        "diplomacy", "sanctions", "treaty", "referendum", "impeachment",
    ],
    "TECH": [
        "technology", "tech", "ai", "artificial intelligence", "software",
        "hardware", "chip", "chips", "semiconductor", "nvidia", "openai",
        "anthropic", "microsoft", "google", "apple", "meta", "startup",
        "cloud", "cyber", "cybersecurity", "data center", "datacenter",
        "robot", "robotics", "quantum", "algorithm", "saas",
        "smartphone", "iphone", "android", "gpu", "machine learning", "llm",
        "neural network", "gadget", "silicon",
    ],
    "FINANCE": [
        "stock", "stocks", "market", "markets", "earnings", "revenue",
        "profit", "investor", "investors", "investment", "shares", "nasdaq",
        "dow jones", "s&p", "bond", "bonds", "yield", "interest rate",
        "federal reserve", "inflation", "ipo", "valuation", "dividend",
        "hedge fund", "portfolio", "currency", "crypto", "bitcoin",
        "trading", "trader", "bank", "banking", "economy", "economic",
        "gdp", "tariff", "merger", "acquisition", "wall street", "recession",
    ],
}

# Narrow, unambiguous topics outrank broad ones: "skincare" or "quarterback" is
# a far stronger signal than "market" or "tech", which bleed into every business
# story. Keep a weight for every key in CATEGORY_KEYWORDS.
CATEGORY_WEIGHT = {
    "BEAUTY": 3,
    "SPORTS": 3,
    "SCIENCE": 2,
    "PROGRAMMING": 2,
    "POLITICS": 2,
    "TECH": 1,
    "FINANCE": 1,
}

# How far the winning topic must beat the query's own label before we override
# it. Each topic already has a dedicated query, so the query's guess is usually
# right -- we only relabel on clear evidence, never on a narrow lead.
MIN_OVERRIDE_MARGIN = 3

# Word-boundary matching matters: bare "ai" would otherwise hit "said", "Dubai".
_KEYWORD_PATTERNS = {
    cat: [re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE) for kw in kws]
    for cat, kws in CATEGORY_KEYWORDS.items()
}


def score_categories(title: str, description: str) -> dict:
    """Weighted keyword score per topic. Title matches count double, since a
    headline is a much better signal of subject than a trailing summary."""
    title_text = clean_html(title or "")
    desc_text = clean_html(description or "")

    scores = {}
    for cat, patterns in _KEYWORD_PATTERNS.items():
        title_hits = sum(1 for p in patterns if p.search(title_text))
        desc_hits = sum(1 for p in patterns if p.search(desc_text))
        if title_hits or desc_hits:
            scores[cat] = CATEGORY_WEIGHT[cat] * (2 * title_hits + desc_hits)
    return scores


def classify_category(title: str, description: str, fallback: str) -> str:
    """Infer an article's topic from its own text instead of trusting whichever
    query returned it -- this is what stops a skincare story that mentions
    "market" from being filed under FINANCE.

    Deliberately conservative: every topic already has its own dedicated query,
    so `fallback` (the query's label) is right most of the time. We only
    override when another topic beats it by MIN_OVERRIDE_MARGIN, which keeps a
    passing mention of "Apple" from dragging a sports story into TECH.
    """
    fallback = db.normalize_category(fallback)
    scores = score_categories(title, description)
    if not scores:
        return fallback

    winner = max(scores, key=lambda cat: (scores[cat], CATEGORY_WEIGHT[cat]))
    if winner == fallback:
        return fallback

    if scores[winner] - scores.get(fallback, 0) >= MIN_OVERRIDE_MARGIN:
        return winner
    return fallback


# Lets us tell "generic topic filler" apart from a real article photo, so that
# relabelling an article swaps its filler image but never clobbers a real one.
_STOCK_IMAGES = {url for urls in CATEGORY_IMAGES.values() for url in urls}


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


# ---------------------------------------------------------------------------
# ARTICLE-LEVEL SUMMARIES AND THEIR PROVENANCE
#
# generate_short_summary_tiered() has three tiers and they are NOT equivalent:
#
#   publisher         the publisher's own blurb. Real, per-article information.
#   keyword_template  one of KEYWORD_SUMMARY_RULES below -- a CANNED sentence,
#                     byte-identical for every article that trips the same rule.
#   topic_fallback    TOPIC_FEEDS[cat]["summary_fallback"] -- one fixed string
#                     per topic, identical for every unmatched article in it.
#
# On a swipe card the distinction barely matters: the reader sees one blurb at a
# time and a generic line is merely dull. It matters enormously to the weekly
# topic summariser, which reads a whole week at once. Hand it 25 copies of the
# semiconductor template and it will report chip supply chains as the theme of
# the week -- a fact about this rule table, not about the news. Prose hides that
# kind of artefact far better than a fabricated number does. See ADR-010 for the
# principle and ADR-011 for this application of it.
#
# So the tier is recorded at write time in articles.description_source, and the
# summariser filters on it. Deriving it after the fact by matching strings works
# but rots the moment somebody rewords a fallback.
#
# The rules live at module scope with {source} as a PLACEHOLDER rather than an
# f-string, so boilerplate_like_patterns() can be generated from them. One source
# of truth: edit a rule and the exclusion patterns follow automatically.
# ---------------------------------------------------------------------------

DESC_PUBLISHER = "publisher"
DESC_KEYWORD_TEMPLATE = "keyword_template"
DESC_TOPIC_FALLBACK = "topic_fallback"

DESCRIPTION_SOURCES = (DESC_PUBLISHER, DESC_KEYWORD_TEMPLATE, DESC_TOPIC_FALLBACK)

KEYWORD_SUMMARY_RULES = [
    (("ai", "artificial intelligence", "machine learning"),
     "Industry developments in AI model scaling, infrastructure, and enterprise adoption reported by {source}."),
    (("layoff", "job cuts", "hiring"),
     "Workforce adjustments and operational efficiency shifts across key sector players as reported by {source}."),
    (("chip", "nvidia", "semiconductor", "amd"),
     "Semiconductor supply dynamics, hardware innovation, and market demand driving global hardware valuations."),
    (("stock", "dow", "nasdaq", "s&p", "inflation"),
     "Key market indices react to economic indicators, investor sentiment, and recent earnings reports according to {source}."),
    (("tariff", "trade deal", "sanction"),
     "Macroeconomic policies, trade regulations, and international market impacts analyzed by {source}."),
    (("election", "vote", "ballot", "campaign"),
     "Campaign developments, polling shifts, and electoral outcomes covered by {source}."),
    (("nasa", "spacex", "orbit", "telescope"),
     "Mission milestones and observational findings advancing our understanding of the solar system and beyond."),
    (("transfer", "signing", "playoff", "championship", "final"),
     "Squad changes, fixture outcomes, and title-race implications reported by {source}."),
    (("open source", "release", "framework", "runtime", "sdk"),
     "Tooling and release notes for developers, covering API changes and ecosystem impact, via {source}."),
    (("skincare", "serum", "cosmetic", "fragrance"),
     "Formulation trends, ingredient science, and brand strategy shaping the consumer beauty market."),
]


def _like_pattern(template: str) -> str:
    """Turn a summary template into a SQL LIKE pattern.

    Escapes LIKE's own wildcards first, THEN substitutes {source} -> %, so a
    template that ever gains a literal % or _ cannot silently widen into a
    pattern that excludes real publisher text.
    """
    escaped = (template.replace("\\", "\\\\")
                       .replace("%", "\\%")
                       .replace("_", "\\_"))
    return escaped.replace("{source}", "%")


def boilerplate_like_patterns() -> list:
    """SQL LIKE patterns matching every canned summary this module can emit.

    Needed only for rows written BEFORE articles.description_source existed.
    Those carry NULL and cannot be filtered on the column, so the summariser
    falls back to matching them here.

    This is a read-time filter, not a backfill. Nothing rewrites the old rows to
    a guessed tier -- the tier that produced them was never observed, and writing
    one now is the same class of error as backfilling the old request_logs rows
    with a status code nobody measured. Inside one purge window every NULL row is
    gone anyway.
    """
    patterns = [_like_pattern(tpl) for _kw, tpl in KEYWORD_SUMMARY_RULES]
    patterns += [_like_pattern(cfg["summary_fallback"]) for cfg in TOPIC_FEEDS.values()]
    return patterns


def _title_mentions(title_lower: str, keywords) -> bool:
    """Word-boundary keyword test.

    This used to be a plain `kw in title_lower` substring check, which meant the
    "ai" rule fired on "campaign", "Ukraine", "said", "remain" and "air" -- so
    politics and sports stories were handed the AI model-scaling blurb.
    """
    return any(re.search(r"\b" + re.escape(kw) + r"\b", title_lower) for kw in keywords)


def generate_short_summary(title: str, category: str, raw_desc: str, source: str) -> str:
    """Text-only wrapper around generate_short_summary_tiered().

    Kept because callers and tests that only want the blurb should not have to
    care about the tier. Everything that WRITES an article should use the tiered
    form so the provenance is stored rather than thrown away.
    """
    return generate_short_summary_tiered(title, category, raw_desc, source)[0]


def generate_short_summary_tiered(title: str, category: str, raw_desc: str,
                                  source: str) -> tuple:
    """Generate a short summary for a news item, and say where it came from.

    Returns (summary_text, description_source) where description_source is one
    of DESC_PUBLISHER / DESC_KEYWORD_TEMPLATE / DESC_TOPIC_FALLBACK.

    Prefers the publisher's own description; only falls back to a generated line
    when the description is missing or just echoes the headline. The second
    element is the whole point of this function existing -- see the block comment
    above KEYWORD_SUMMARY_RULES.
    """
    clean_desc = clean_html(raw_desc)
    clean_title = clean_html(title)

    if ' - ' in clean_desc:
        clean_desc = clean_desc.rsplit(' - ', 1)[0].strip()

    title_words = set(clean_title.lower().split())
    desc_words = set(clean_desc.lower().split())
    overlap = len(title_words.intersection(desc_words)) / max(len(title_words), 1)

    if clean_desc and overlap < 0.70 and len(clean_desc) > 30:
        return clean_desc, DESC_PUBLISHER

    # Cross-topic keyword heuristics, checked before the per-topic fallback.
    title_lower = title.lower()
    for keywords, template in KEYWORD_SUMMARY_RULES:
        if _title_mentions(title_lower, keywords):
            # .format on a template with no placeholder is a no-op, so the three
            # source-free rules pass through untouched.
            return template.format(source=source), DESC_KEYWORD_TEMPLATE

    return get_topic_config(category)["summary_fallback"], DESC_TOPIC_FALLBACK


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
            desc, desc_source = generate_short_summary_tiered(
                title, category, art.get("description", ""), src_name)

            results.append({
                "title": title,
                "description": desc,
                "description_source": desc_source,
                "raw_description": art.get("description", ""),
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

            desc, desc_source = generate_short_summary_tiered(title, category, raw_desc, source)

            results.append({
                "title": title,
                "description": desc,
                "description_source": desc_source,
                "raw_description": raw_desc,
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
    2. Inserts each one with ON CONFLICT DO NOTHING, so deduplication by MD5
       article_key is resolved atomically inside the write itself.
    3. Returns a fresh topic-balanced deck from the database.

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
    reclassified_count = 0

    for idx, item in enumerate(raw_articles, start=1):
        item['index'] = idx

        # Relabel by reading the article itself, so a beauty story that turned up
        # in the finance query is stored as BEAUTY rather than FINANCE.
        query_category = item['category']
        true_category = classify_category(item['title'], item.get('description', ''), query_category)

        if true_category != query_category:
            item['category'] = true_category
            reclassified_count += 1
            print(f"[Reclassified] {query_category} -> {true_category}: {item['title'][:50]}...")
            # Its filler hero image belonged to the old topic; only swap generic
            # filler, never a real photo the publisher supplied.
            if item.get('image_url') in _STOCK_IMAGES:
                item['image_url'] = fallback_image(true_category, idx)

        # Rebuild the blurb against the FINAL topic so a politics story can never
        # keep a tech-flavoured fallback line. The tier is recaptured with it --
        # reclassification can move an article from a publisher description to a
        # different topic's fallback, and description_source has to follow or the
        # weekly summariser will treat boilerplate as real content.
        item['description'], item['description_source'] = generate_short_summary_tiered(
            item['title'],
            true_category,
            item.get('raw_description', ''),
            item.get('source', ''),
        )

        # No read-then-write pre-check. ON CONFLICT settles new-vs-duplicate
        # inside the INSERT's own transaction, so these counts stay correct even
        # when the scheduler job, a cold-start /api/v1/feed fetch and a
        # POST /cards/refresh background task all run at the same moment. The
        # old is_article_in_db() gate left a window in which two of them could
        # both decide an article was new and both increment new_count.
        _, inserted = db.save_article(item)
        new_count += inserted
        skipped_count += not inserted

    breakdown = ", ".join(f"{cat} {count}" for cat, count in per_topic_counts.items())
    print(f"[DB Sync Summary] {len(raw_articles)} items across {len(target_categories)} topics "
          f"({breakdown}): {new_count} NEW inserted, {skipped_count} ALREADY EXISTED, "
          f"{reclassified_count} RELABELLED by content.")

    feed_limit = ARTICLES_PER_CATEGORY * len(target_categories)
    return db.get_balanced_feed(limit=feed_limit, categories=target_categories)


if __name__ == "__main__":
    # Standalone run: no lifespan(), so open and close the pool here.
    db.init_pool(minconn=1, maxconn=4)
    try:
        db.init_db()
        articles = fetch_and_sync_news_to_db()
        print(f"Total articles in balanced deck: {len(articles)}")
    finally:
        db.close_pool()
