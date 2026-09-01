"""Weekly per-topic summaries.

This is a summary OF summaries. Article-level summaries already exist -- every
row in `articles` carries one in `description`, written by
news_fetcher.generate_short_summary_tiered() at fetch time. Nothing here
re-summarises an article, and it could not: the raw article text was never
stored, so `description` is the only body text that exists.

Two things about that input shape the whole module.

1. Only tier-1 descriptions (the publisher's own blurb) carry real per-article
   information. Tiers 2 and 3 are canned sentences -- identical across every
   article that trips the same keyword rule, or across every unmatched article
   in a topic. Hand a summariser 25 copies of one sentence and it will report
   that sentence's subject as the theme of the week, which is a fact about
   news_fetcher's rule table rather than about the news. database.py filters
   them out; MIN_ARTICLES_FOR_SUMMARY catches what is left.

2. The window is `created_at`, i.e. when Paperswap ingested the article, not
   when it was published. `published_at` is TEXT and is frequently the literal
   string "Recently", so it cannot be bucketed. Every surface that shows a
   summary says "ingested", because those two are not the same thing.

Ordering note: this runs INSIDE refresh_pipeline, before purge_old_data. See
ADR-011. Articles live 9 days; a calendar week is only fully present for a short
window, and a cron job scheduled independently would race the purge.
"""

import os
import re
import json
import logging
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import requests

import database as db

logger = logging.getLogger("paperswap.summarizer")


# --- Configuration -----------------------------------------------------------

# 'extractive:v1' (offline, deterministic, free) or 'anthropic' (LLM-written).
SUMMARY_GENERATOR = os.getenv("SUMMARY_GENERATOR", "extractive:v1").strip()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "").strip()
SUMMARY_TIMEOUT = float(os.getenv("SUMMARY_TIMEOUT", "60"))

# Filter out canned descriptions. Leave this on -- see the module docstring.
PUBLISHER_ONLY = os.getenv(
    "SUMMARY_PUBLISHER_DESCRIPTIONS_ONLY", "true").lower() == "true"

# Counted AFTER the boilerplate filter, so it bites harder than it looks. A
# weekly digest built from two articles is noise wearing an authoritative label.
MIN_ARTICLES_FOR_SUMMARY = int(os.getenv("MIN_ARTICLES_FOR_SUMMARY", "3"))

# Hard ceiling on articles fed to one generator call. Exists so a feed anomaly
# cannot turn a routine weekly job into an unbounded request.
SUMMARY_MAX_ARTICLES = int(os.getenv("SUMMARY_MAX_ARTICLES", "80"))

# 0 = keep summaries forever, which is the intent: once the articles are purged
# these rows are the only surviving record of the week.
SUMMARY_RETENTION_WEEKS = int(os.getenv("SUMMARY_RETENTION_WEEKS", "0"))


# --- Week arithmetic ---------------------------------------------------------

def utc_today() -> date:
    """Today in UTC. Not date.today(), which follows the container's clock and
    would move week boundaries by the local offset without saying so."""
    return datetime.now(timezone.utc).date()


def last_completed_week_start(today: date = None) -> date:
    """Monday of the most recently FINISHED Mon-Sun week.

    Correct on every weekday: on a Monday it returns the previous Monday (the
    week that ended yesterday), and on a Wednesday it returns the Monday nine
    days back. The current, partial week is never summarised -- a digest of
    three days labelled as a week is the same defect as a six-day one.
    """
    today = today or utc_today()
    return today - timedelta(days=today.weekday() + 7)


def week_end_for(week_start: date) -> date:
    return week_start + timedelta(days=6)


# --- extractive:v1 -----------------------------------------------------------

_STOPWORDS = frozenset("""
a an the and or but of for to in on at by with from as is are was were be been
being has have had do does did will would can could should may might must this
that these those it its it's you your we our they their he she his her not no
new says say said after before over under more most than then there here about
what when where which who whom how why all any both each few other some such
only own same so too very just now also into out up down off again once
""".split())

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'&.-]{2,}")


def _salient_terms(articles: list, k: int = 6) -> list:
    """Most frequent non-stopword title terms.

    Titles rather than descriptions: even after the tier-1 filter, descriptions
    are publisher marketing copy and share a lot of generic vocabulary, while a
    headline is compressed and specific. Terms appearing in only one article are
    dropped -- one mention is not a theme.
    """
    counts = Counter()
    for art in articles:
        seen = set()
        for token in _TOKEN_RE.findall(art.get("title") or ""):
            low = token.lower().strip(".'-&")
            if len(low) < 3 or low in _STOPWORDS or low in seen:
                continue
            seen.add(low)
            counts[low] += 1
    return [term for term, n in counts.most_common(k * 3) if n > 1][:k]


# Two headlines counted as the same story when this share of the shorter one's
# terms also appear in the longer. Overlap coefficient, NOT Jaccard: Jaccard
# divides by the union, so a short headline and a long one describing the same
# event score low purely because their lengths differ. "Fed holds rates steady
# in September decision" vs "Federal Reserve holds rates steady, September"
# scores exactly 0.50 on Jaccard and 0.67 here -- the first passes a 0.5 Jaccard
# gate untouched, which is how three paraphrases of one story reach the output.
#
# 0.6 is a judgement call, not a measured optimum. Above it, genuinely distinct
# stories sharing a subject start collapsing; below it, paraphrases survive.
_DUPLICATE_OVERLAP = 0.6


def _representative_headlines(articles: list, terms: list, n: int = 3) -> list:
    """Headlines scoring highest on the salient terms, deduplicated two ways.

    The per-source cap handles wire copy: one story arrives from six outlets and
    without the cap all three slots go to the same event.

    The overlap check handles what the source cap misses -- the SAME story
    written up by three DIFFERENT outlets, which carry different source names and
    so pass the first filter untouched. Three paraphrases of one headline read as
    three stories, which overstates how much happened that week.

    Neither check is exhaustive. Two reports of one event that share few words
    ("Nvidia earnings beat" / "Chipmaker posts record quarter") still count
    twice. Catching those needs semantics, which this generator deliberately does
    not have.
    """
    term_set = set(terms)
    scored = []
    for art in articles:
        title = (art.get("title") or "").strip()
        if not title:
            continue
        tokens = {t.lower().strip(".'-&") for t in _TOKEN_RE.findall(title)}
        tokens -= _STOPWORDS
        scored.append((len(tokens & term_set), title, art.get("source") or "", tokens))

    scored.sort(key=lambda row: (-row[0], row[1]))

    picked, picked_tokens, used_sources = [], [], set()
    for score, title, source, tokens in scored:
        if score == 0 or source in used_sources or not tokens:
            continue
        if any(len(tokens & prev) / min(len(tokens), len(prev)) >= _DUPLICATE_OVERLAP
               for prev in picked_tokens):
            continue
        picked.append(title)
        picked_tokens.append(tokens)
        used_sources.add(source)
        if len(picked) == n:
            break
    return picked


def _extractive_v1(topic: str, articles: list) -> tuple:
    """Deterministic, offline, free. No API key, no network, no model.

    Not eloquent, and not meant to be. It exists so the whole pipeline -- window
    maths, filtering, counts, upsert, scheduling -- can be built and tested
    before the LLM question is settled, and so the feature keeps working if the
    API key is ever missing. Every clause below is derived from the input; it
    asserts nothing it cannot count.
    """
    label = db.CATEGORIES[db.normalize_category(topic)]["label"]
    terms = _salient_terms(articles)
    source_counts = Counter(a.get("source") for a in articles if a.get("source"))
    sources = [s for s, _ in source_counts.most_common(3)]
    headlines = _representative_headlines(articles, terms)

    parts = [f"{len(articles)} {label} articles were collected this week"]
    if sources:
        # "and others" ONLY when there genuinely are others. Appending it on a
        # fixed list length would assert the existence of sources that may not
        # exist -- a small claim, but an unmeasured one, and the whole point of
        # this generator is that every clause is derived from the input.
        tail = " and others" if len(source_counts) > len(sources) else ""
        parts.append("across " + ", ".join(sources) + tail)
    opening = " ".join(parts) + "."

    body = ""
    if terms:
        body = " Recurring terms in the headlines: " + ", ".join(terms) + "."
    if headlines:
        body += " Representative stories: " + "; ".join(headlines) + "."

    return (opening + body).strip(), "extractive:v1"


# --- anthropic ---------------------------------------------------------------

_PROMPT = """You are writing a weekly digest for the "{label}" topic of a news app.

The list below is NOT full articles. Each line is an article's headline followed
by the publisher's own one-line summary. You are compressing {n} short summaries
into one paragraph; write accordingly and do not imply you read the articles.

Rules:
- 3 to 5 sentences, plain prose, no headings, no bullet points, no preamble.
- Use only what appears in the list. Do not add background, figures, dates or
  causes that are not present.
- Name the stories that recur across multiple lines. If nothing recurs, say the
  week's coverage was scattered rather than inventing a theme.
- No speculation about what any of it means or what happens next.
- Do not begin with "This week" or "In summary".

Articles ({n}):
{lines}"""


def _anthropic(topic: str, articles: list) -> tuple:
    """One API call per topic per week. Roughly 2-5k input tokens per topic.

    Uses `requests` rather than the SDK: it is already a dependency, this is a
    single unauthenticated-shape POST, and the box has 956 MB of RAM and a
    2 GB swap file -- every avoided dependency is a build that does not thrash.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("SUMMARY_GENERATOR=anthropic but ANTHROPIC_API_KEY is unset.")
    if not SUMMARY_MODEL:
        raise RuntimeError(
            "SUMMARY_GENERATOR=anthropic but SUMMARY_MODEL is unset. Pin an explicit "
            "model id -- it is recorded in topic_summaries.generator, so a row always "
            "names what wrote it."
        )

    label = db.CATEGORIES[db.normalize_category(topic)]["label"]
    # Source and headline and blurb. Never the URL: handing a model a link is an
    # invitation to cite it as though it had been read.
    lines = "\n".join(
        f"- [{a.get('source') or 'Unknown'}] {a.get('title')} :: {a.get('description')}"
        for a in articles
    )
    prompt = _PROMPT.format(label=label, n=len(articles), lines=lines)

    resp = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        json={
            "model": SUMMARY_MODEL,
            "max_tokens": 600,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=SUMMARY_TIMEOUT,
    )

    if resp.status_code != 200:
        # Body, not just the code: a 400 here is nearly always a bad model id,
        # and the message says which.
        raise RuntimeError(
            f"Anthropic API returned {resp.status_code}: {resp.text[:300]}"
        )

    blocks = resp.json().get("content", [])
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    if not text:
        raise RuntimeError("Anthropic API returned no text content.")

    return text, f"anthropic:{SUMMARY_MODEL}"


# --- Dispatch ----------------------------------------------------------------

def summarise(topic: str, articles: list) -> tuple:
    """Return (summary_text, generator_id).

    RAISES on failure. It never returns a placeholder, and that is deliberate:
    once the source articles are purged, a placeholder row is indistinguishable
    from a real summary and there is nothing left to check it against. A missing
    row is visible, recoverable while the articles survive, and honest.
    """
    if SUMMARY_GENERATOR.startswith("anthropic"):
        return _anthropic(topic, articles)
    return _extractive_v1(topic, articles)


def generate_weekly_summaries(week_start: date = None, force: bool = False) -> dict:
    """Fill in missing summaries for one completed week. Returns a report dict.

    Idempotent and cheap. On a normal tick topics_missing_summary() returns
    nothing and this costs one indexed query, which is what happens on thirteen
    of every fourteen refreshes.

    `force` regenerates topics that already have a row -- for the admin endpoint
    and for backfills. It still will not invent a week whose articles are gone.
    """
    week_start = week_start or last_completed_week_start()
    week_end = week_end_for(week_start)

    if force:
        topics = [c["slug"] for c in db.get_enabled_categories()]
    else:
        topics = db.topics_missing_summary(week_start)
        if not topics:
            return {"week_start": week_start.isoformat(), "written": 0,
                    "skipped": [], "failed": [], "detail": "nothing to do"}

    written, skipped, failed = 0, [], []

    for topic in topics:
        try:
            in_window = db.count_articles_for_week(topic, week_start)
            articles = db.get_articles_for_week(
                topic, week_start,
                publisher_only=PUBLISHER_ONLY,
                limit=SUMMARY_MAX_ARTICLES,
            )

            if len(articles) < MIN_ARTICLES_FOR_SUMMARY:
                # No row rather than a thin one. Common for the narrow topics --
                # Beauty and Programming on Google News RSS often have almost no
                # publisher descriptions. This is the system working.
                skipped.append({
                    "topic": topic,
                    "usable": len(articles),
                    "in_window": in_window,
                    "reason": f"fewer than MIN_ARTICLES_FOR_SUMMARY ({MIN_ARTICLES_FOR_SUMMARY}) "
                              f"usable articles after the boilerplate filter",
                })
                continue

            text, generator = summarise(topic, articles)
            top_sources = db.get_top_sources_for_week(
                topic, week_start, publisher_only=PUBLISHER_ONLY)

            db.save_topic_summary(
                topic=topic,
                week_start=week_start,
                week_end=week_end,
                summary=text,
                # What the generator actually read, NOT the window count. These
                # differ by exactly the boilerplate that was filtered out, and
                # swapping them would attach a number to prose it does not
                # describe. Neither can be recomputed after the purge.
                number_of_articles=len(articles),
                articles_in_window=in_window,
                top_sources=top_sources,
                generator=generator,
            )
            written += 1
            logger.info("Summarised %s for week %s: %d of %d articles via %s.",
                        topic, week_start, len(articles), in_window, generator)

        except Exception as exc:
            # One topic failing must not cost the other six. The gap is retried
            # on the next refresh, and while the articles survive it is fully
            # recoverable.
            failed.append({"topic": topic, "error": str(exc)[:200]})
            logger.warning("Weekly summary failed for %s (week %s): %s",
                           topic, week_start, exc, exc_info=True)

    print(f"[Summaries] Week {week_start} -> {week_end}: {written} written, "
          f"{len(skipped)} skipped, {len(failed)} failed.")

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "generator": SUMMARY_GENERATOR,
        "written": written,
        "skipped": skipped,
        "failed": failed,
    }


if __name__ == "__main__":
    # Standalone run: no lifespan(), so open and close the pool here.
    logging.basicConfig(level=logging.INFO)
    db.init_pool(minconn=1, maxconn=4)
    try:
        db.init_db()
        print(json.dumps(generate_weekly_summaries(force=True), indent=2))
    finally:
        db.close_pool()
