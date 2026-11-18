"""Feed fields behind the Paper cards: images and publisher credits (#13, #71,
#72; ADR-013, ADR-016).

Claims under test:
  1. upgrade_image_url swaps BBC's 240 px thumbnail for the 976 px image and
     leaves every other URL, '' and None alone.
  2. The publisher registry credits the real publisher in the wording its terms
     ask for, prefers the longest match (BBC Sport over BBC News), matches
     subdomains, credits aggregator items "<site> via <aggregator>", keeps the
     feed's name for unknown sites, and is idempotent.
  3. Publishers that forbid hot-linking (The Verge, Refinery29, TechCrunch,
     CNBC) never get an image, at fetch time or at serve time.
  4. The feed serves rows stored under the old rules correctly: the RSS channel
     title becomes the publisher's name (CNBC, not "US Top News and Analysis"),
     a forbidden image is dropped, a BBC thumbnail is upgraded, related entries
     are credited too, and description_source comes back as stored with NULL
     kept null (invariant 5). No image_is_stock field and no embedding.
  5. init_db clears the stock filler photos the fetcher used to store, and the
     fetcher no longer stores any.

Needs Postgres for 4-5. Bypasses lifespan(), so no real news fetch happens.

Run:  python tests/test_paper_feed.py
"""
import inspect
import os
import sys
import types
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ADMIN_API_KEY"] = "test-key"

import importlib
import database as db
import news_fetcher as nf
import main as m

importlib.reload(m)
from fastapi.testclient import TestClient

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + str(detail) if detail else ''}")
    if not cond:
        fails.append(name)


print("--- 1. upgrade_image_url ---")
BBC_240 = "https://ichef.bbci.co.uk/ace/standard/240/cpsprodpb/5c24/live/x.jpg"
BBC_976 = "https://ichef.bbci.co.uk/ace/standard/976/cpsprodpb/5c24/live/x.jpg"
for raw, want in [
    (BBC_240, BBC_976),
    (BBC_976, BBC_976),
    ("https://e0.365dm.com/26/11/1920x1080/a.jpg", "https://e0.365dm.com/26/11/1920x1080/a.jpg"),
    ("https://example.com/ace/standard/240/x.jpg", "https://example.com/ace/standard/240/x.jpg"),
    ("", ""),
    (None, None),
]:
    got = nf.upgrade_image_url(raw)
    check(f"upgrade_image_url({raw!r})", got == want, got)


print("\n--- 2. Publisher credits ---")
for url, feed, want in [
    ("https://www.cnbc.com/2026/11/18/x.html", "US Top News and Analysis", "CNBC"),
    ("https://www.npr.org/2026/11/18/x", "NPR Topics: Politics", "NPR"),
    ("https://www.sciencedaily.com/releases/x.htm", "Latest Science News -- ScienceDaily", "Science Daily"),
    ("https://www.bbc.co.uk/news/articles/x", "BBC News", "BBC News"),
    ("https://www.bbc.co.uk/sport/football/articles/x", "BBC Sport", "BBC Sport"),
    ("https://www.bbc.co.uk/sporting-chance", "BBC News", "BBC News"),
    ("https://feeds.npr.org/x", "anything", "NPR"),
    ("https://github.com/org/repo", "Hacker News: Front Page", "github.com via Hacker News"),
    ("https://unknown.example/x", "Some Feed", "Some Feed"),
    ("", "", "Unknown"),
]:
    got = nf.publisher_credit(url, feed)
    check(f"credit for {url or '(no url)'}", got == want, got)
check("idempotent: a credited aggregator item stays as it is",
      nf.publisher_credit("https://github.com/org/repo", "github.com via Hacker News")
      == "github.com via Hacker News")


print("\n--- 3. Image rules ---")
for url, want in [
    ("https://www.theverge.com/x", False), ("https://www.refinery29.com/x", False),
    ("https://techcrunch.com/x", False), ("https://www.cnbc.com/x", False),
    ("https://www.bbc.co.uk/news/x", True), ("https://arstechnica.com/x", True),
    ("https://unknown.example/x", True),
]:
    check(f"image_allowed({url})", nf.image_allowed(url) is want)

# The fetch path, with feedparser and robots.txt faked: a Verge entry with an
# image is stored without it, a BBC entry keeps its upgraded image, an entry
# with no image gets none (no stock filler), and every source is credited.
entry = lambda link, img=None: types.SimpleNamespace(  # noqa: E731
    get=lambda k, d=None, _l=link: {"title": f"t {_l}", "link": _l, "summary": "A real blurb."}.get(k, d),
    media_content=[{"url": img}] if img else [], media_thumbnail=[], links=[],
    summary="A real blurb.", published_parsed=None)
fake_feeds = {
    "https://feed.test/verge": ("The Verge", [entry("https://www.theverge.com/a", "https://img.verge/a.jpg")]),
    "https://feed.test/bbc": ("BBC News", [entry("https://www.bbc.co.uk/news/b", BBC_240),
                                          entry("https://www.bbc.co.uk/news/c")]),
}
saved = (nf.feedparser.parse, nf.feed_allowed, nf.TOPIC_FEEDS["TECH"]["rss_urls"])
try:
    nf.feedparser.parse = lambda url, agent=None: types.SimpleNamespace(
        entries=fake_feeds[url][1], feed=types.SimpleNamespace(title=fake_feeds[url][0]), status=200)
    nf.feed_allowed = lambda url: True
    nf.TOPIC_FEEDS["TECH"]["rss_urls"] = list(fake_feeds)
    items = {i["url"]: i for i in nf.fetch_from_rss("TECH", 10)}
finally:
    nf.feedparser.parse, nf.feed_allowed, nf.TOPIC_FEEDS["TECH"]["rss_urls"] = saved
check("fetch: a forbidden image is not stored", items["https://www.theverge.com/a"]["image_url"] == "")
check("fetch: an allowed image is stored, upgraded", items["https://www.bbc.co.uk/news/b"]["image_url"] == BBC_976)
check("fetch: no image means none, not stock filler", items["https://www.bbc.co.uk/news/c"]["image_url"] == "")
check("fetch: sources are credited", items["https://www.theverge.com/a"]["source"] == "The Verge")
check("no stock filler left in the code",
      not hasattr(nf, "fallback_image") and not hasattr(nf, "_STOCK_IMAGES")
      and all("images" not in cfg for cfg in nf.TOPIC_FEEDS.values()))


print("\n--- 4-5. The feed and the migration ---")
db.init_pool()
db.init_db()
c = TestClient(m.app, raise_server_exceptions=False)

PREFIX_TAG = uuid.uuid4().hex[:8]
STOCK = "https://loremflickr.com/800/1200/stadium,sport?lock=11"
# Stored the way the old fetcher stored them: channel titles, every image.
fixtures = {
    "cnbc": {"url": f"https://www.cnbc.com/{PREFIX_TAG}/a", "source": "US Top News and Analysis",
             "image_url": "https://image.cnbcfm.com/a.jpg", "description_source": "publisher"},
    "bbc": {"url": f"https://www.bbc.co.uk/sport/{PREFIX_TAG}/b", "source": "BBC Sport",
            "image_url": BBC_240, "description_source": "publisher"},
    "verge": {"url": f"https://www.theverge.com/{PREFIX_TAG}/c", "source": "The Verge",
              "image_url": "https://platform.theverge.com/c.jpg", "description_source": "topic_fallback"},
    "legacy": {"url": f"https://arstechnica.com/{PREFIX_TAG}/d", "source": "Ars Technica",
               "image_url": STOCK, "description_source": None},
}
ids = {}
for key, f in fixtures.items():
    ids[key], _ = db.save_article({"title": f"paper feed fixture {key} {PREFIX_TAG}", "description": "d",
                                   "published_at": "now", "category": "BEAUTY", **f})
with db.db_cursor(commit=True) as cur:
    cur.execute("UPDATE articles SET related_ids = %s WHERE id = %s",
                (f"[{ids['cnbc']}]", ids["bbc"]))
    # init_db already ran once above; the stock row was inserted after it.
    cur.execute("SELECT image_url FROM articles WHERE id = %s", (ids["legacy"],))
    stock_before = cur.fetchone()["image_url"]

try:
    db.init_db()
    with db.db_cursor() as cur:
        cur.execute("SELECT image_url FROM articles WHERE id = %s", (ids["legacy"],))
        stock_after = cur.fetchone()["image_url"]
    check("fixture: the stock URL was stored", stock_before == STOCK)
    check("init_db clears a stored stock filler photo", stock_after == "", stock_after)

    m.invalidate_public_caches()
    r = c.get("/api/v1/feed", params={"categories": "BEAUTY", "limit": 300, "balanced": "false"})
    check("feed answers 200", r.status_code == 200, r.status_code)
    arts = r.json().get("articles", [])
    by = {k: next((a for a in arts if a["url"] == f["url"]), None) for k, f in fixtures.items()}
    check("all four fixtures are in the feed", all(by.values()), [k for k, v in by.items() if not v])

    if all(by.values()):
        check("a channel title is served as the publisher's name",
              by["cnbc"]["source"] == "CNBC", by["cnbc"]["source"])
        check("a forbidden image stored earlier is dropped at serve time",
              by["cnbc"]["image_url"] == "" and by["verge"]["image_url"] == "")
        check("an allowed BBC thumbnail is served at 976 px", by["bbc"]["image_url"] == BBC_976,
              by["bbc"]["image_url"])
        check("related entries are credited too",
              [x["source"] for x in by["bbc"]["related"]] == ["CNBC"], by["bbc"]["related"])
        check("description_source comes back as stored",
              by["cnbc"]["description_source"] == "publisher"
              and by["verge"]["description_source"] == "topic_fallback")
        check("a NULL description_source stays null, not guessed",
              by["legacy"]["description_source"] is None)

    check("no card carries image_is_stock", all("image_is_stock" not in a for a in arts))
    check("no card carries the embedding", all("embedding" not in a for a in arts))

    m.invalidate_public_caches()
    r2 = c.get("/api/v1/feed", params={"categories": "BEAUTY", "limit": 300})
    cnbc2 = next((a for a in r2.json().get("articles", []) if a["url"] == fixtures["cnbc"]["url"]), {})
    check("the balanced deck applies the same rules",
          cnbc2.get("source") == "CNBC" and cnbc2.get("image_url") == "", cnbc2.get("source"))
finally:
    with db.db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM articles WHERE id = ANY(%s)", (list(ids.values()),))
    m.invalidate_public_caches()

check("the digest credits publishers the way cards do",
      "publisher_credit" in inspect.getsource(__import__("summarizer").build_stories))


print("\n" + "=" * 62)
if fails:
    print(f"FAILED ({len(fails)}): " + ", ".join(fails))
    sys.exit(1)
print("All Paper feed checks passed.")
