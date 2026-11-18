"""Weekly per-topic summaries.

This is a summary OF summaries. Nothing here reads article bodies, and nothing
in Paperswap stores them (ADR-015). It works from what the feeds published and
what earlier stages stored per article:

  description      the card blurb, from news_fetcher at fetch time
  embedding        a MiniLM vector of "title. description", from enrichment.py

The default generator, stories:v3, groups the week's articles into stories by
embedding similarity and reports each story's headline with the first sentence
of the publisher's own description, i.e. what happened, rather than the
headline vocabulary extractive:v1 counts. (stories:v2 used a sentence copied
from the article body instead; rows it wrote keep that generator id.)

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
import sys
import json
import math
import struct
import logging
import argparse
import operator
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import requests

import database as db

logger = logging.getLogger("paperswap.summarizer")


# --- Configuration -----------------------------------------------------------

# 'stories:v3' (default: offline, deterministic, free), 'extractive:v1' (the
# previous offline generator) or 'anthropic' (LLM-written from the story groups).
SUMMARY_GENERATOR = os.getenv("SUMMARY_GENERATOR", "stories:v3").strip()

# stories:v3 -- two articles belong to the same story when the average cosine
# similarity between their groups is at least this. See ADR-011 for how the
# value was chosen and `python summarizer.py --explain` for tuning it.
STORY_SIMILARITY = float(os.getenv("SUMMARY_STORY_SIMILARITY", "0.45"))

# Lines per digest. Up to MAX_STORIES stories covered by 2+ articles; when a week
# has fewer, single-article stories fill the digest up to MIN_STORIES.
MAX_STORIES = 5
MIN_STORIES = 3

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


# --- stories:v3 --------------------------------------------------------------
#
# Pure Python on purpose. The API process never imports numpy (only the
# enrichment job does), and this runs inside the API's scheduler, so numpy here
# would stay resident for the life of the process on a 956 MB box. At
# SUMMARY_MAX_ARTICLES = 80 the similarity matrix is 3,160 dot products of 384
# floats, which is tens of milliseconds in plain Python once a week.

def _unit_vectors(articles: list) -> list:
    """Each article's embedding as an L2-normalised list, or None.

    None for: no embedding yet (enrichment has not reached the row), a blob that
    is not whole float32s, a zero or non-finite vector, or a dimension that
    differs from the week's usual one (a model change mid-week). Those articles
    become single-article stories rather than being compared with vectors from
    a different space.
    """
    decoded = []
    for art in articles:
        blob = art.get("embedding")
        if not blob or len(blob) % 4:
            decoded.append(None)
            continue
        decoded.append(struct.unpack(f"<{len(blob) // 4}f", blob))

    dims = Counter(len(v) for v in decoded if v is not None)
    if not dims:
        return [None] * len(articles)
    dim = dims.most_common(1)[0][0]

    out = []
    for vec in decoded:
        if vec is None or len(vec) != dim:
            out.append(None)
            continue
        norm = math.sqrt(sum(x * x for x in vec))
        out.append([x / norm for x in vec] if norm > 0 and math.isfinite(norm) else None)
    return out


def _dot(a, b) -> float:
    return sum(map(operator.mul, a, b))


def _pair(i: int, j: int) -> tuple:
    return (i, j) if i < j else (j, i)


def group_stories(articles: list, threshold: float = None, vectors: list = None) -> list:
    """Partition article indices into stories. Returns a list of sorted index lists.

    Average-linkage agglomerative clustering: repeatedly merge the two groups
    whose MEAN pairwise cosine similarity is highest, until no pair reaches
    `threshold`. Average rather than single linkage because single linkage
    chains: A resembles B and B resembles C, so A and C land together even when
    they share nothing, and on a news week that glues one topic's distinct
    stories into a blob. Complete linkage goes the other way and splits a story
    the moment one outlet writes it up from an odd angle.

    Deterministic: ties go to the first pair in index order, and indices follow
    the query's created_at order.
    """
    threshold = STORY_SIMILARITY if threshold is None else threshold
    vecs = vectors if vectors is not None else _unit_vectors(articles)
    embedded = [i for i, v in enumerate(vecs) if v is not None]

    groups = {i: [i] for i in embedded}
    # Sum of pairwise similarities between every two live groups. Merging adds
    # rows, so average linkage never has to revisit individual articles.
    total = {}
    for pos, i in enumerate(embedded):
        for j in embedded[pos + 1:]:
            total[(i, j)] = _dot(vecs[i], vecs[j])

    while total:
        best, pair = None, None
        for (i, j), sim_sum in total.items():
            avg = sim_sum / (len(groups[i]) * len(groups[j]))
            if best is None or avg > best:
                best, pair = avg, (i, j)
        if best < threshold:
            break

        keep, gone = pair
        for k in groups:
            if k in (keep, gone):
                continue
            total[_pair(keep, k)] += total.pop(_pair(gone, k))
        del total[pair]
        groups[keep].extend(groups.pop(gone))

    stories = [sorted(g) for g in groups.values()]
    stories += [[i] for i, v in enumerate(vecs) if v is None]
    return sorted(stories, key=lambda g: g[0])


def _coverage(sources: list, n_articles: int) -> str:
    """"5 articles · Reuters, BBC and 2 others". Every part is a count."""
    noun = "article" if n_articles == 1 else "articles"
    if not sources:
        return f"{n_articles} {noun}"
    named = sources[:3]
    rest = len(sources) - len(named)
    tail = f" and {rest} other{'s' if rest != 1 else ''}" if rest else ""
    return f"{n_articles} {noun} · {', '.join(named)}{tail}"


_FIRST_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'“])")

# A digest line has to stay one line on a phone. Most publisher descriptions
# open with a sentence well under this; the few that do not are run-ons.
LEDE_MAX_CHARS = 240


def _publisher_lede(article: dict):
    """The first sentence of the article's publisher-written description, or
    None.

    Only description_source == 'publisher'. A canned blurb would put a fact
    about news_fetcher's rule table into the digest, and NULL (a row older than
    the column) is unknown provenance, which is not guessed (invariant 5).
    """
    if article.get("description_source") != "publisher":
        return None
    text = " ".join((article.get("description") or "").split())
    if not text:
        return None
    sentence = _FIRST_SENTENCE.split(text, maxsplit=1)[0]
    if len(sentence) > LEDE_MAX_CHARS:
        sentence = sentence[:LEDE_MAX_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return sentence


def build_stories(articles: list, threshold: float = None) -> list:
    """Every story in the week, most covered first.

    Ranked by distinct sources, then size. Distinct sources first because six
    outlets reporting one event says more about the week than one outlet
    filing six updates on it.

    The line for each story is the first sentence of the publisher-written
    description of the article closest to the story's centroid, among the
    members that have one. Publishers write the feed description lede first,
    so that sentence is normally what happened. A story where no member has a
    publisher description falls back to the representative headline, and
    `from_description` records which one was used.
    """
    vecs = _unit_vectors(articles)
    stories = []

    for members in group_stories(articles, threshold, vectors=vecs):
        embedded = [i for i in members if vecs[i] is not None]
        if len(embedded) > 1:
            centroid = [sum(col) for col in zip(*(vecs[i] for i in embedded))]
            closeness = {i: _dot(vecs[i], centroid) for i in embedded}
        else:
            closeness = {}
        by_centrality = sorted(members, key=lambda i: (-closeness.get(i, float("-inf")), i))

        described = [i for i in by_centrality if _publisher_lede(articles[i])]
        rep = described[0] if described else by_centrality[0]
        headline = (articles[rep].get("title") or "").strip()
        lede = _publisher_lede(articles[rep]) if described else None

        source_counts = Counter(articles[i].get("source") for i in members
                                if articles[i].get("source"))
        sources = [s for s, _ in sorted(source_counts.items(), key=lambda kv: (-kv[1], kv[0]))]

        stories.append({
            "members": members,
            "size": len(members),
            "sources": sources,
            "representative": rep,
            "headline": headline,
            "lede": lede,
            "from_description": lede is not None,
            "text": lede or headline,
            "coverage": _coverage(sources, len(members)),
        })

    stories.sort(key=lambda s: (-len(s["sources"]), -s["size"], s["members"][0]))
    return stories


def _pick_stories(stories: list) -> list:
    """Up to MAX_STORIES multi-article stories, topped up to MIN_STORIES with
    single-article ones when the week did not have enough."""
    multi = [s for s in stories if s["size"] > 1][:MAX_STORIES]
    if len(multi) >= MIN_STORIES:
        return multi
    singles = [s for s in stories if s["size"] == 1]
    return multi + singles[:MIN_STORIES - len(multi)]


def _stories_v3(topic: str, articles: list, threshold: float = None) -> tuple:
    """Deterministic, offline, free. One line per story: what happened, then
    how many articles covered it and which outlets.

    The text is the publishers' own headlines and feed descriptions plus
    counts. It does not paraphrase, so it cannot state anything the feeds did
    not.
    """
    label = db.CATEGORIES[db.normalize_category(topic)]["label"]
    picked = _pick_stories(build_stories(articles, threshold))

    if any(s["size"] > 1 for s in picked):
        opening = f"{len(articles)} {label} articles were collected this week. Most covered:"
    else:
        # Saying "most covered" over a set of one-article stories would rank
        # things that were never ranked.
        opening = (f"{len(articles)} {label} articles were collected this week, "
                   f"and no story appeared in more than one. A sample:")

    lines = [opening] + [f"• {s['text']} ({s['coverage']})" for s in picked]
    return "\n".join(lines), "stories:v3"


# --- anthropic ---------------------------------------------------------------

_PROMPT = """You are writing a weekly digest for the "{label}" topic of a news app.

The list below is NOT full articles. {n} articles were grouped into stories by
similarity. Each line is one story: how many articles covered it and from which
outlets, a representative headline, and, where available, the first sentence
of the publisher's own description of it. Lines are ordered by how widely the
story was covered. Write accordingly and do not imply you read the articles.

Rules:
- 3 to 5 sentences, plain prose, no headings, no bullet points, no preamble.
- Use only what appears in the list. Do not add background, figures, dates or
  causes that are not present.
- Lead with the most widely covered stories. If no story was covered by more
  than one article, say the week's coverage was scattered rather than
  inventing a theme.
- No speculation about what any of it means or what happens next.
- Do not begin with "This week" or "In summary".

Stories:
{lines}"""

# Stories handed to the model. The tail beyond this is single-article stories
# that the digest would not mention anyway.
_ANTHROPIC_MAX_STORIES = 15

# Cost ceiling. These three caps bound every call, so the yearly bill has a
# worst case that does not depend on what the feeds send. Haiku 5.5 is
# $0.10 / $0.50 per million input / output tokens (verified 2026-11):
#   input  <= ~9.5k chars of prompt; even at 1 char per token  -> $0.00095
#   output <= 800 tokens (thinking disabled, so all of it is text) -> $0.00040
#   7 topics x 52 weeks = 364 calls x $0.00135                     -> ~$0.49/year
# Realistic prompts are ~3k tokens, so the expected bill is nearer $0.15/year.
# The bound holds for Haiku only: SUMMARY_MODEL set to a larger model multiplies
# it by that model's price. See ADR-011, "Cost".
_ANTHROPIC_MAX_TOKENS = 800
_ANTHROPIC_MAX_LINES_CHARS = 8000
_ANTHROPIC_HEADLINE_CHARS = 200
_ANTHROPIC_LEDE_CHARS = 300


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _story_lines(stories: list) -> str:
    """The prompt's story list, clipped so its length has a hard ceiling.

    Stories arrive ranked, so the ones dropped for length are the least covered.
    """
    lines, used = [], 0
    for s in stories:
        line = f"- [{_clip(s['coverage'], 120)}] {_clip(s['headline'], _ANTHROPIC_HEADLINE_CHARS)}"
        if s["lede"]:
            line += f" :: {_clip(s['lede'], _ANTHROPIC_LEDE_CHARS)}"
        if lines and used + len(line) + 1 > _ANTHROPIC_MAX_LINES_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


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
    # Coverage, headline and the publisher's description sentence. Never the URL: handing a model
    # a link is an invitation to cite it as though it had been read.
    lines = _story_lines(build_stories(articles)[:_ANTHROPIC_MAX_STORIES])
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
            # Thinking off: rewording a ranked list needs none, and thinking
            # tokens are billed as output -- with it on (the Haiku 5.5 default)
            # the cap above would have to be ~4000 to avoid cut-off digests.
            # A 3-5 sentence digest is ~150-250 tokens; 800 is headroom.
            "max_tokens": _ANTHROPIC_MAX_TOKENS,
            "thinking": {"type": "disabled"},
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

    body = resp.json()
    # A truncated or refused reply is a failure, not a short summary. Stored, it
    # would read as complete once the articles are purged.
    stop_reason = body.get("stop_reason")
    if stop_reason not in ("end_turn", "stop_sequence"):
        raise RuntimeError(f"Anthropic API stopped with stop_reason={stop_reason!r}.")

    blocks = body.get("content", [])
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    if not text:
        raise RuntimeError("Anthropic API returned no text content.")

    # Measured, so the bill can be checked against the ceiling from the logs.
    usage = body.get("usage") or {}
    logger.info("Anthropic summary for %s: %s input / %s output tokens.",
                topic, usage.get("input_tokens"), usage.get("output_tokens"))
    return text, f"anthropic:{SUMMARY_MODEL}"


# --- Dispatch ----------------------------------------------------------------

def summarise(topic: str, articles: list) -> tuple:
    """Return (summary_text, generator_id).

    RAISES on failure. It never returns a placeholder, and that is deliberate:
    once the source articles are purged, a placeholder row is indistinguishable
    from a real summary and there is nothing left to check it against. A missing
    row is visible, recoverable while the articles survive, and honest.

    Under SUMMARY_GENERATOR=anthropic a failed call falls back to stories:v3.
    That is not a placeholder: it is a complete digest, and the row's generator
    column says stories:v3, so nothing claims the model wrote it.
    """
    if SUMMARY_GENERATOR.startswith("anthropic"):
        try:
            return _anthropic(topic, articles)
        except Exception as exc:
            logger.warning("Anthropic summary failed for %s, falling back to stories:v3: %s",
                           topic, exc, exc_info=True)
            return _stories_v3(topic, articles)
    if SUMMARY_GENERATOR == "extractive:v1":
        return _extractive_v1(topic, articles)
    return _stories_v3(topic, articles)


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
                # Beauty and Programming often have few publisher descriptions. This is the system working.
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


# --- Tuning ------------------------------------------------------------------

def explain_week(week_start: date, topics: list, thresholds: list, out=sys.stdout) -> None:
    """Print how stories:v3 groups one week at several thresholds. Read-only.

    This is how SUMMARY_STORY_SIMILARITY gets tuned: run it on a real week, read
    the multi-article groups, and look for the two failure modes. Too low merges
    distinct stories that share a subject (two different Fed stories). Too high
    leaves one event split across groups, which shows up as near-identical
    headlines in different groups.
    """
    for topic in topics:
        articles = db.get_articles_for_week(topic, week_start, publisher_only=PUBLISHER_ONLY,
                                            limit=SUMMARY_MAX_ARTICLES)
        embedded = sum(1 for v in _unit_vectors(articles) if v is not None)
        described = sum(1 for a in articles if _publisher_lede(a))
        print(f"\n=== {topic}  week {week_start}: {len(articles)} usable articles, "
              f"{embedded} embedded, {described} with a publisher description", file=out)
        if not articles:
            continue

        for threshold in thresholds:
            stories = build_stories(articles, threshold)
            multi = [s for s in stories if s["size"] > 1]
            print(f"\n--- threshold {threshold:.2f}: {len(stories)} stories, "
                  f"{len(multi)} with 2+ articles", file=out)
            for s in multi:
                print(f"  [{s['coverage']}]", file=out)
                for i in s["members"]:
                    mark = "*" if i == s["representative"] else " "
                    print(f"   {mark} {articles[i].get('title')}", file=out)

        text, _ = _stories_v3(topic, articles)
        print(f"\n--- digest at the configured SUMMARY_STORY_SIMILARITY "
              f"({STORY_SIMILARITY:.2f}):\n{text}", file=out)


def _main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate weekly summaries, or --explain how a week would be grouped.")
    parser.add_argument("--week", type=date.fromisoformat,
                        help="Monday of the week (default: the last completed week)")
    parser.add_argument("--explain", action="store_true",
                        help="print the story groups instead of writing summaries")
    parser.add_argument("--topic", action="append",
                        help="topic slug for --explain; repeatable (default: all enabled)")
    parser.add_argument("--thresholds", default="0.35,0.4,0.45,0.5,0.55,0.6",
                        help="comma-separated similarity thresholds for --explain")
    args = parser.parse_args(argv)

    week = args.week or last_completed_week_start()
    if week.weekday() != 0:
        parser.error(f"--week must be a Monday; {week} is a {week.strftime('%A')}")

    # Standalone run: no lifespan(), so open and close the pool here.
    logging.basicConfig(level=logging.INFO)
    db.init_pool(minconn=1, maxconn=4)
    try:
        db.init_db()
        if args.explain:
            topics = ([db.normalize_category(t) for t in args.topic] if args.topic
                      else [c["slug"] for c in db.get_enabled_categories()])
            thresholds = [float(t) for t in args.thresholds.split(",") if t.strip()]
            explain_week(week, topics, thresholds)
        else:
            print(json.dumps(generate_weekly_summaries(week_start=week, force=True), indent=2))
    finally:
        db.close_pool()


if __name__ == "__main__":
    _main()
