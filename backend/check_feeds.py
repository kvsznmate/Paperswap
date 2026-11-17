"""Health-check the RSS feeds in news_fetcher.TOPIC_FEEDS.

    docker compose run --rm enrichment python check_feeds.py
    docker compose run --rm enrichment python check_feeds.py --extract
    docker compose run --rm enrichment python check_feeds.py --category TECH
    docker compose run --rm enrichment python check_feeds.py --url <candidate>

Feeds rot, and a feed is only useful if it links straight to the publisher.
Google News search feeds publish redirect wrappers (news.google.com/rss/...)
that server-side extraction cannot follow, which is why they were replaced.

`--extract` is the test that matters: a feed can return clean links that
trafilatura still cannot read (JS shell, paywall, consent wall). Use `--url`
to vet a candidate before adding it to TOPIC_FEEDS.
"""

import sys
import argparse

from news_fetcher import TOPIC_FEEDS

FEEDS = {slug: cfg["rss_urls"] for slug, cfg in TOPIC_FEEDS.items()}

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
        description="Check the configured RSS feeds for usable article links.")
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
        if key not in FEEDS:
            print(f"Unknown category {key}. Known: {', '.join(FEEDS)}")
            sys.exit(1)
        groups = {key: FEEDS[key]}
    else:
        groups = FEEDS

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
        print("  Find replacements in news_fetcher.TOPIC_FEEDS, or those topics "
              "fall back to NewsAPI only.")

    if not args.extract:
        print("\n  These passed the link check only. Re-run with --extract "
              "before trusting them -- a clean link can still be a JS shell "
              "or a paywall that yields no text.")


if __name__ == "__main__":
    main()
