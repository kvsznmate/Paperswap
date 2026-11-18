"""Health-check the RSS feeds in news_fetcher.TOPIC_FEEDS.

    docker compose run --rm enrichment python check_feeds.py
    docker compose run --rm enrichment python check_feeds.py --category TECH
    docker compose run --rm enrichment python check_feeds.py --url <candidate>

Feeds rot. A feed is usable when, fetched the way news_fetcher fetches it:
  - its host's robots.txt allows FETCH_USER_AGENT (news_fetcher skips it
    otherwise, see feed_allowed),
  - it returns entries, and
  - its links go straight to the publisher's article. Google News search feeds
    publish redirect wrappers (news.google.com/rss/...), which credit Google
    rather than the publisher; the feeds' terms ask for a working link to the
    original story.

Use `--url` to vet a candidate before adding it to TOPIC_FEEDS, and check its
terms of use too (#66): a feed that passes here can still forbid use in an app.
"""

import sys
import argparse

from news_fetcher import TOPIC_FEEDS, FETCH_USER_AGENT, feed_allowed

FEEDS = {slug: cfg["rss_urls"] for slug, cfg in TOPIC_FEEDS.items()}

BAD_HOSTS = ("news.google.com", "consent.google.com")


def check_feed(url: str) -> dict:
    import feedparser

    result = {
        "url": url, "ok": False, "entries": 0,
        "wrapped": 0, "sample_link": "", "sample_title": "", "note": "",
    }

    if not feed_allowed(url):
        result["note"] = f"robots.txt disallows it for {FETCH_USER_AGENT!r}"
        return result

    try:
        feed = feedparser.parse(url, agent=FETCH_USER_AGENT)
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
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Check the configured RSS feeds: robots.txt, entries, direct links.")
    parser.add_argument("--category", help="Check one category only.")
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

    passing = {}

    for category, urls in groups.items():
        print(f"\n{'=' * 78}\n  {category}\n{'=' * 78}")
        passing[category] = []

        for url in urls:
            res = check_feed(url)
            mark = "PASS" if res["ok"] else "FAIL"
            print(f"\n  [{mark}] {url}")
            print(f"         entries: {res['entries']}")

            if res["sample_link"]:
                print(f"         sample : {res['sample_title']}")
                print(f"                  {res['sample_link'][:100]}")
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
              "get no articles at all.")


if __name__ == "__main__":
    main()
