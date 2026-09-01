"""Test candidate RSS feeds before wiring them into news_fetcher.CATEGORIES.

    docker compose run --rm enrichment python check_feeds.py
    docker compose run --rm enrichment python check_feeds.py --extract
    docker compose run --rm enrichment python check_feeds.py --category TECH

Why this exists
---------------
Every one of the seven feeds in news_fetcher.CATEGORIES is a Google News
search feed, and Google News does not publish article links -- it publishes
redirect wrappers (news.google.com/rss/articles/CBMi...). Those bounce through
consent.google.com and loop, so trafilatura extracts nothing, so TextRank has
no sentences to rank, so every card back is empty. Since 2024 the wrapper
cannot be decoded offline either; resolving it requires calling Google's
internal batchexecute endpoint, which is undocumented and breaks without
notice.

The fix is feeds that link straight to publishers. The candidates below are
UNVERIFIED -- RSS URLs rot constantly, publishers move them, and several of
these were probably already dead when this file was written. That is the
entire point of the script: run it, keep what passes, delete what does not.

`--extract` is the test that actually matters. A feed can return a perfectly
valid link that trafilatura still cannot read, because the page is a JS shell,
a hard paywall, or a consent interstitial. Only fetching a real article tells
you whether a feed will produce bullets.
"""

import sys
import argparse

# UNVERIFIED CANDIDATES. Do not trust this list -- run the script.
# Several per category on purpose: you want two or three live feeds each so a
# single publisher going down does not empty a whole topic.
CANDIDATES = {
    "TECH": [
        "https://feeds.arstechnica.com/arstechnica/index",
        "https://www.theverge.com/rss/index.xml",
        "https://techcrunch.com/feed/",
        "https://www.engadget.com/rss.xml",
    ],
    "FINANCE": [
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "http://feeds.marketwatch.com/marketwatch/topstories/",
        "https://finance.yahoo.com/news/rssindex",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
    ],
    "SPORTS": [
        "https://www.espn.com/espn/rss/news",
        "https://feeds.bbci.co.uk/sport/rss.xml",
        "https://www.skysports.com/rss/12040",
    ],
    "POLITICS": [
        "https://feeds.bbci.co.uk/news/politics/rss.xml",
        "https://feeds.npr.org/1014/rss.xml",
        "https://rss.politico.com/politics-news.xml",
    ],
    "PROGRAMMING": [
        "https://dev.to/feed",
        "https://feed.infoq.com/",
        "https://hnrss.org/frontpage",
        "https://stackoverflow.blog/feed/",
    ],
    "SCIENCE": [
        "https://phys.org/rss-feed/",
        "https://www.sciencedaily.com/rss/all.xml",
        "https://www.nature.com/nature.rss",
        "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    ],
    "BEAUTY": [
        "https://www.allure.com/feed/rss",
        "https://www.byrdie.com/rss",
        "https://www.refinery29.com/en-us/beauty/rss.xml",
    ],
}

BAD_HOSTS = ("news.google.com", "consent.google.com")


def check_feed(url: str, want_extract: bool) -> dict:
    import feedparser

    result = {
        "url": url, "ok": False, "entries": 0,
        "wrapped": 0, "sample_link": "", "sample_title": "",
        "extract_chars": None, "note": "",
    }

    try:
        feed = feedparser.parse(url)
    except Exception as exc:
        result["note"] = f"parse error: {exc}"
        return result

    # feedparser does not raise on a 404 -- it returns an object with no
    # entries and sets bozo. Checking entries is the only reliable signal.
    entries = getattr(feed, "entries", [])
    result["entries"] = len(entries)

    if not entries:
        status = getattr(feed, "status", "?")
        result["note"] = f"no entries (http {status})"
        return result

    for entry in entries:
        link = entry.get("link", "")
        if any(host in link for host in BAD_HOSTS):
            result["wrapped"] += 1

    first = entries[0]
    result["sample_link"] = first.get("link", "")
    result["sample_title"] = (first.get("title") or "")[:60]

    if result["wrapped"]:
        result["note"] = f"{result['wrapped']}/{len(entries)} links are wrapped"
        return result

    result["ok"] = True

    if want_extract:
        result["extract_chars"] = try_extract(result["sample_link"])
        if not result["extract_chars"]:
            result["ok"] = False
            result["note"] = "link is clean but extraction returned nothing"
        elif result["extract_chars"] < 400:
            result["ok"] = False
            result["note"] = f"only {result['extract_chars']} chars extracted"

    return result


def try_extract(url: str):
    """Fetch one article and see whether trafilatura gets usable text.

    Mirrors enrichment.extract_full_text, including its 400-character floor, so
    a feed that passes here will behave the same way in the nightly job.
    """
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return 0
        text = trafilatura.extract(downloaded, include_comments=False,
                                   include_tables=False)
        return len(text) if text else 0
    except Exception:
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="Test candidate RSS feeds for usable article links.")
    parser.add_argument("--category", help="Check one category only.")
    parser.add_argument("--extract", action="store_true",
                        help="Also fetch one article per feed and try to "
                             "extract it. Slower, and the only test that "
                             "proves a feed will produce bullets.")
    parser.add_argument("--url", help="Check a single ad-hoc feed URL.")
    args = parser.parse_args()

    if args.url:
        groups = {"AD-HOC": [args.url]}
    elif args.category:
        key = args.category.upper()
        if key not in CANDIDATES:
            print(f"Unknown category {key}. Known: {', '.join(CANDIDATES)}")
            sys.exit(1)
        groups = {key: CANDIDATES[key]}
    else:
        groups = CANDIDATES

    if args.extract:
        print("Extraction enabled -- one article fetched per feed. Be patient.\n")

    passing = {}

    for category, urls in groups.items():
        print(f"\n{'=' * 78}\n  {category}\n{'=' * 78}")
        passing[category] = []

        for url in urls:
            res = check_feed(url, args.extract)
            mark = "PASS" if res["ok"] else "FAIL"
            print(f"\n  [{mark}] {url}")
            print(f"         entries: {res['entries']}")

            if res["sample_link"]:
                print(f"         sample : {res['sample_title']}")
                print(f"                  {res['sample_link'][:100]}")
            if res["extract_chars"] is not None:
                print(f"         text   : {res['extract_chars']} chars")
            if res["note"]:
                print(f"         note   : {res['note']}")

            if res["ok"]:
                passing[category].append(url)

    print(f"\n\n{'=' * 78}\n  SUMMARY\n{'=' * 78}")
    for category, urls in passing.items():
        state = f"{len(urls)} usable" if urls else "NONE USABLE"
        print(f"\n  {category}: {state}")
        for url in urls:
            print(f"    {url}")

    empty = [c for c, u in passing.items() if not u]
    if empty:
        print(f"\n  No working feed for: {', '.join(empty)}")
        print("  Find replacements before switching news_fetcher over, or "
              "those topics go empty.")

    if not args.extract:
        print("\n  These passed the link check only. Re-run with --extract "
              "before trusting them -- a clean link can still be a JS shell "
              "or a paywall that yields no text.")


if __name__ == "__main__":
    main()
