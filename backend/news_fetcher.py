import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import feedparser

import database as db  # also loads backend/.env

# How many articles to pull per topic on each refresh cycle.
# 7 topics x 12 = 84 candidate articles per batch (before dedup).
ARTICLES_PER_CATEGORY = int(os.getenv("ARTICLES_PER_CATEGORY", "12"))

# How the fetcher introduces itself to publishers. A named agent instead of
# feedparser's default lets a publisher see who is reading their feed and
# address robots.txt rules to it (ADR-015). Override to add a contact URL.
FETCH_USER_AGENT = os.getenv(
    "FETCH_USER_AGENT", "Paperswap/1.0 (non-commercial demo news reader)").strip()

# robots.txt answers are cached per host for this long, so a refresh of ~20
# feeds costs a handful of robots requests a day, not one per feed per refresh.
ROBOTS_CACHE_SECONDS = 24 * 3600


# ---------------------------------------------------------------------------
# TOPIC FEED CONFIGURATION
# One entry per slug in database.CATEGORIES. To add a topic: add it to
# database.CATEGORIES, then add a matching block here. Nothing else changes.
#
#   rss_urls        -> publisher feeds, round-robin. Verified with check_feeds.py
#   summary_fallback-> used when the feed gives no usable description
#
# These were Google News search feeds until every one of them was replaced.
# Google News does not publish article links, it publishes redirect wrappers
# (news.google.com/rss/articles/CBMi...) that bounce through consent.google.com
# and loop, and they credit Google rather than the publisher. Publisher feeds
# link straight to the story, which is what the feeds' terms ask for.
#
# Feeds rot. Re-run `python check_feeds.py` before trusting a change here, and
# read a new feed's terms of use first (#66): some forbid use in an app.
# ---------------------------------------------------------------------------
TOPIC_FEEDS = {
    "TECH": {
        "rss_urls": [
            "https://feeds.arstechnica.com/arstechnica/index",
            "https://www.theverge.com/rss/index.xml",
            "https://techcrunch.com/feed/",
            "https://www.engadget.com/rss.xml",
        ],
        "summary_fallback": "Breakthrough technology updates and strategic market shifts impacting digital infrastructure and software.",
    },
    "FINANCE": {
        "rss_urls": [
            "https://www.cnbc.com/id/100003114/device/rss/rss.html",
            "https://feeds.bbci.co.uk/news/business/rss.xml",
        ],
        "summary_fallback": "Financial sector analysis covering corporate earnings, market trends, and strategic investment movements.",
    },
    "SPORTS": {
        "rss_urls": [
            "https://feeds.bbci.co.uk/sport/rss.xml",
            "https://www.skysports.com/rss/12040",
        ],
        "summary_fallback": "Match results, transfer moves, and championship standings from across the world of professional sport.",
    },
    "POLITICS": {
        "rss_urls": [
            "https://feeds.bbci.co.uk/news/politics/rss.xml",
            "https://feeds.npr.org/1014/rss.xml",
        ],
        "summary_fallback": "Legislative developments, election coverage, and policy debates shaping domestic and international governance.",
    },
    "PROGRAMMING": {
        "rss_urls": [
            "https://dev.to/feed",
            "https://feed.infoq.com/",
            # Highest-variance feed here: HN links wherever the story lives, so
            # extraction success swings between a clean blog and a PDF.
            "https://hnrss.org/frontpage",
        ],
        "summary_fallback": "Language releases, framework updates, and open-source project news relevant to working software engineers.",
    },
    "SCIENCE": {
        "rss_urls": [
            "https://phys.org/rss-feed/",
            "https://www.sciencedaily.com/rss/all.xml",
            "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        ],
        "summary_fallback": "Peer-reviewed findings, space missions, and laboratory breakthroughs reported from the global research community.",
    },
    "BEAUTY": {
        "rss_urls": [
            "https://www.allure.com/feed/rss",
            "https://www.refinery29.com/en-us/beauty/rss.xml",
        ],
        "summary_fallback": "Product launches, skincare research, and brand movements across the global beauty and cosmetics industry.",
    },
}

def get_topic_config(category: str) -> dict:
    """Return the feed config for a topic, falling back to the default topic."""
    slug = db.normalize_category(category)
    return TOPIC_FEEDS.get(slug, TOPIC_FEEDS[db.DEFAULT_CATEGORY])




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
# Add a new topic by adding one entry here.
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


# ---------------------------------------------------------------------------
# PUBLISHERS (#71, #72, ADR-016)
#
# Who an article belongs to, keyed by its link: the host without "www.",
# optionally with a path prefix. The longest match wins, so "bbc.co.uk/sport"
# beats "bbc.co.uk", and a subdomain matches its parent ("feeds.npr.org" ->
# "npr.org").
#
#   name            the credit shown on cards and in digests, in the wording the
#                   publisher's terms ask for where they ask (#66 survey: NPR,
#                   Science Daily, BBC News). The RSS channel title is not used:
#                   CNBC's is "US Top News and Analysis".
#   hotlink_images  False where the terms forbid linking to their images
#                   (The Verge, Refinery29) or to their content "in-line"
#                   (TechCrunch, CNBC). Those cards are type-only (ADR-013).
#
# A link that matches nothing keeps the feed's own name. Items from an
# aggregator feed (AGGREGATORS, keyed by the feed's title) link to arbitrary
# sites, so they are credited "<site> via <aggregator>".
#
# Applied at fetch time, and again at serve time so rows stored before a change
# here pick it up. Both are idempotent.
# ---------------------------------------------------------------------------
PUBLISHERS = {
    "arstechnica.com":   {"name": "Ars Technica"},
    "theverge.com":      {"name": "The Verge", "hotlink_images": False},
    "techcrunch.com":    {"name": "TechCrunch", "hotlink_images": False},
    "engadget.com":      {"name": "Engadget"},
    "cnbc.com":          {"name": "CNBC", "hotlink_images": False},
    "bbc.co.uk":         {"name": "BBC News"},
    "bbc.com":           {"name": "BBC News"},
    "bbc.co.uk/sport":   {"name": "BBC Sport"},
    "bbc.com/sport":     {"name": "BBC Sport"},
    "skysports.com":     {"name": "Sky Sports"},
    "npr.org":           {"name": "NPR"},
    "dev.to":            {"name": "DEV Community"},
    "infoq.com":         {"name": "InfoQ"},
    "phys.org":          {"name": "Phys.org"},
    "sciencedaily.com":  {"name": "Science Daily"},
    "allure.com":        {"name": "Allure"},
    "refinery29.com":    {"name": "Refinery29", "hotlink_images": False},
}

AGGREGATORS = {
    "Hacker News: Front Page": "Hacker News",
}


def _host_and_path(url: str) -> tuple:
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return "", ""
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host, parts.path or "/"


def publisher_for(url: str):
    """The PUBLISHERS entry for an article link, or None."""
    host, path = _host_and_path(url)
    if not host:
        return None
    best, best_len = None, -1
    for key, entry in PUBLISHERS.items():
        k_host, _, k_path = key.partition("/")
        if not (host == k_host or host.endswith("." + k_host)):
            continue
        if k_path and not (path == "/" + k_path or path.startswith("/" + k_path + "/")):
            continue
        if len(key) > best_len:
            best, best_len = entry, len(key)
    return best


def publisher_credit(url: str, feed_source: str) -> str:
    """The name to credit for an article: the registry's, else '<site> via
    <aggregator>' for aggregator items, else the feed's own name."""
    entry = publisher_for(url)
    if entry:
        return entry["name"]
    feed_source = (feed_source or "").strip()
    if feed_source in AGGREGATORS:
        host, _ = _host_and_path(url)
        if host:
            return f"{host} via {AGGREGATORS[feed_source]}"
    return feed_source or "Unknown"


def image_allowed(url: str) -> bool:
    """False when the article's publisher forbids hot-linking its images."""
    entry = publisher_for(url)
    return entry is None or entry.get("hotlink_images", True)


# BBC feeds link a 240 px thumbnail; a card shows it 4-9x enlarged. The same
# image is served at larger widths by changing that path segment (976 checked
# 2026-11: 6 KB at 240 px, 72 KB at 976 px).
_BBC_THUMB = re.compile(r"(//ichef\.bbci\.co\.uk/ace/standard/)240/")


def upgrade_image_url(url: str) -> str:
    """Swap a known low-resolution thumbnail URL for a larger one. Anything else,
    including '' and None, comes back unchanged."""
    if not url:
        return url
    return _BBC_THUMB.sub(r"\g<1>976/", url)


def present_for_client(articles: list) -> list:
    """The publisher rules and the BBC image upgrade, applied to a deck about to
    be served. Mutates and returns.

    Done at serve time as well as at fetch time, so rows stored before a rule
    changed get it too (they live up to PURGE_OLDER_THAN_DAYS).
    """
    for item in articles:
        link = item.get('url') or ''
        item['source'] = publisher_credit(link, item.get('source'))
        image = item.get('image_url') or ''
        item['image_url'] = upgrade_image_url(image) if image_allowed(link) else ''
        for rel in item.get('related') or []:
            rel['source'] = publisher_credit(rel.get('url'), rel.get('source'))
    return articles


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


_robots_cache = {}  # "scheme://host" -> (fetched_at, RobotFileParser)


def _robots_for(url: str) -> RobotFileParser:
    """The host's robots.txt, fetched with our User-Agent, cached per host.

    Status handling follows RFC 9309, section 2.3.1:
      - 2xx: obey the file.
      - 4xx (including 401/403): "unavailable", the crawler MAY access
        anything. Python's RobotFileParser.read() would treat 401/403 as
        disallow-all instead, which is why the file is fetched here.
      - 5xx or no answer: "unreachable", the crawler MUST assume complete
        disallow. Cached for an hour rather than a day, so a brief outage
        does not cost the feed for a whole day.
    """
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    now = time.time()
    cached = _robots_cache.get(base)
    if cached and now < cached[0]:
        return cached[1]

    parser = RobotFileParser()
    ttl = ROBOTS_CACHE_SECONDS
    try:
        req = urllib.request.Request(base + "/robots.txt",
                                     headers={"User-Agent": FETCH_USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            parser.parse(resp.read().decode("utf-8", "replace").splitlines())
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500:
            parser.allow_all = True
        else:
            parser.disallow_all = True
            ttl = 3600
    except Exception:
        parser.disallow_all = True
        ttl = 3600

    _robots_cache[base] = (now + ttl, parser)
    return parser


def feed_allowed(url: str) -> bool:
    """True when the feed host's robots.txt lets FETCH_USER_AGENT fetch url."""
    return _robots_for(url).can_fetch(FETCH_USER_AGENT, url)


def fetch_from_rss(category: str, count: int = ARTICLES_PER_CATEGORY) -> list:
    """Fetch one topic, round-robin across its publisher feeds.

    Publisher RSS is the only news source (ADR-014 removed NewsAPI, which never
    worked from the server).

    Round-robin rather than concatenate. BBC Sport returns 76 entries and Sky
    returns 20, so reading them in order would make nearly every Sports card a
    BBC card. Interleaving keeps the mix even -- which matters beyond variety,
    because these articles get embedded, and one publisher's house style
    becoming a proxy for the topic is a real way to poison a classifier.

    (BBC still supplies a feed to four of the seven topics. Round-robin caps it
    at a third to a half within each one, but the cross-topic concentration is
    worth remembering when reading any per-category metric later.)

    A dead feed is skipped, not fatal. Feeds rot -- ESPN answered 202 and Byrdie
    403 during the last check -- and one bad response should cost a few
    articles, not the whole topic.
    """
    config = get_topic_config(category)
    urls = config.get("rss_urls") or []

    batches = []
    for url in urls:
        if not feed_allowed(url):
            print(f"[Skip] {category}: robots.txt disallows {url} for {FETCH_USER_AGENT!r}")
            continue
        try:
            feed = feedparser.parse(url, agent=FETCH_USER_AGENT)
            entries = list(getattr(feed, "entries", []))
            if not entries:
                # feedparser does not raise on a 404 or a bot challenge; it
                # returns an object with no entries. This is the only signal.
                print(f"[Warn] {category}: no entries from {url} "
                      f"(http {getattr(feed, 'status', '?')})")
                continue
            # Publisher name comes from the feed, not the entry. Google News set
            # entry.source on every item; direct publisher feeds rarely do.
            feed_title = getattr(getattr(feed, "feed", None), "title", "") or ""
            batches.append((feed_title, entries))
        except Exception as exc:
            print(f"[Error] {category}: RSS fetch failed for {url}: {exc}")

    results = []
    depth = max((len(entries) for _, entries in batches), default=0)

    for position in range(depth):
        for feed_title, entries in batches:
            if position >= len(entries):
                continue
            if len(results) >= count:
                return results

            entry = entries[position]

            link = entry.get("link", "#")
            source = publisher_credit(link, feed_title)

            # The old code stripped everything after the last ' - ' from both
            # title and source, because Google News appends ' - Publisher' to
            # every headline. Publisher feeds do not, so that rule now only
            # truncates real headlines that happen to contain a dash. Removed.
            title = entry.get("title", "")
            if not title:
                continue

            raw_desc = entry.get("summary", entry.get("description", ""))

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
                # No image is better than a stock one (ADR-013), and none at all
                # where the publisher forbids hot-linking (ADR-016).
                "image_url": upgrade_image_url(extract_rss_image(entry)) if image_allowed(link) else "",
                "url": link,
            })

    return results


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
        items = fetch_from_rss(category, ARTICLES_PER_CATEGORY)
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
