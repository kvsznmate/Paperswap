"""stories:v2: weekly digests built from story groups, not headline vocabulary.

Claims under test:
  1. Paraphrases group. Three write-ups of one event with almost no words in
     common land in one story when their embeddings agree -- the case
     extractive:v1's word-overlap check cannot catch.
  2. Distinct stories stay apart, even when they share a subject (cosine just
     under the threshold).
  3. No chaining. A~B and B~C above the threshold with A and C unrelated does
     not glue A and C together: average linkage, not single linkage.
  4. Ranking. Distinct outlets outrank raw size: one event covered by three
     outlets beats one outlet filing four updates.
  5. The lede. Each line is the first bullet of the member closest to the
     story centroid, among members with bullets. With no bullets anywhere in
     the story, the headline -- and from_bullet says which.
  6. Nothing is lost or invented. Every article is in exactly one story,
     articles without an embedding (or with a foreign dimension) become
     single-article stories, and the digest's stated counts are the input's.
  7. The database hands the generator bullets as a list and the embedding as
     bytes, and a week is written with generator = stories:v2 by default,
     with number_of_articles unchanged.
  8. Under SUMMARY_GENERATOR=anthropic, a failed or truncated call falls back
     to stories:v2 and the row says stories:v2.
  9. get_articles_for_week never reaches a route: its rows carry BYTEA.

Embeddings are synthetic, built from a chosen Gram matrix, so every similarity
in these fixtures is exact rather than whatever a model happens to produce.
What the threshold should be on real MiniLM vectors is a separate question,
answered in ADR-011 with `python summarizer.py --explain`.

Sections 1-6 and 8 need no database. Section 7 writes fixture articles to a
week twenty weeks back, the same isolation scheme as test_topic_summaries.py.

Run:  python tests/test_stories.py
"""
import os
import sys
import json
import math
import struct
import inspect
import logging
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
import news_fetcher as nf
import summarizer as sm

fails = []
DIM = 384
TOPIC = "TECH"


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(name)


def section(title):
    print(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
# Synthetic embeddings
# ---------------------------------------------------------------------------

def vectors_from_gram(gram):
    """Unit vectors whose pairwise dot products are exactly `gram` (Cholesky),
    padded to DIM. `gram` must be positive definite with a unit diagonal."""
    n = len(gram)
    low = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = gram[i][j] - sum(low[i][k] * low[j][k] for k in range(j))
            low[i][j] = math.sqrt(s) if i == j else s / low[j][j]
    return [row + [0.0] * (DIM - n) for row in low]


def block_gram(sizes, within, between=0.0, overrides=None):
    """Gram matrix for groups of the given sizes: `within` inside a group,
    `between` across groups, plus explicit (i, j): value overrides."""
    labels = [g for g, size in enumerate(sizes) for _ in range(size)]
    n = len(labels)
    gram = [[1.0 if i == j else (within if labels[i] == labels[j] else between)
             for j in range(n)] for i in range(n)]
    for (i, j), v in (overrides or {}).items():
        gram[i][j] = gram[j][i] = v
    return gram


def pack(vec):
    return struct.pack(f"<{len(vec)}f", *vec)


def article(title, vec=None, source="Wire", bullets=None):
    return {
        "title": title,
        "source": source,
        "description": f"Publisher blurb for {title}.",
        "summary_bullets": bullets or [],
        "embedding": pack(vec) if vec is not None else None,
    }


def membership(stories_or_groups, n):
    """Map article index -> group number, for either group_stories output or
    build_stories output."""
    out = {}
    for g, item in enumerate(stories_or_groups):
        members = item["members"] if isinstance(item, dict) else item
        for i in members:
            out[i] = g
    return [out.get(i) for i in range(n)]


# ---------------------------------------------------------------------------
# 1. Paraphrases group
# ---------------------------------------------------------------------------
section("1. paraphrased headlines are one story")

# Three write-ups of one chip-earnings story with almost no shared words, and
# two unrelated stories.
paraphrase_titles = [
    "Nvidia earnings beat expectations",
    "Chipmaker posts record quarter",
    "GPU giant's revenue surges past forecasts",
]
vecs = vectors_from_gram(block_gram([3, 1, 1], within=0.8, between=0.1))
arts = [article(t, vecs[i], source=f"Outlet {i}") for i, t in enumerate(paraphrase_titles)]
arts += [article("City council approves new cycle lanes", vecs[3], source="Outlet 3"),
         article("Museum reopens after renovation", vecs[4], source="Outlet 4")]

groups = sm.group_stories(arts, threshold=0.6)
m = membership(groups, len(arts))
check("the three paraphrases share one story", m[0] == m[1] == m[2], str(groups))
check("the unrelated articles are their own stories",
      len({m[0], m[3], m[4]}) == 3, str(groups))

# The point of v2: v1's word-overlap dedupe treats these as three stories.
terms = ["nvidia", "chipmaker", "gpu", "earnings", "record", "revenue"]
v1_picks = sm._representative_headlines(arts[:3], terms, n=3)
check("extractive:v1 word overlap does NOT merge them (the gap v2 closes)",
      len(v1_picks) == 3, str(v1_picks))


# ---------------------------------------------------------------------------
# 2. Distinct stories with a shared subject stay apart
# ---------------------------------------------------------------------------
section("2. near-miss stories stay apart")

vecs = vectors_from_gram(block_gram([2, 2], within=0.85, between=0.55))
arts = [article(f"story {i}", vecs[i], source=f"Outlet {i}") for i in range(4)]
groups = sm.group_stories(arts, threshold=0.6)
m = membership(groups, 4)
check("two stories at cosine 0.55 to each other are not merged at 0.6",
      m[0] == m[1] and m[2] == m[3] and m[0] != m[2], str(groups))

groups_low = sm.group_stories(arts, threshold=0.5)
check("...and are merged when the threshold drops below 0.55",
      len(groups_low) == 1, str(groups_low))


# ---------------------------------------------------------------------------
# 3. No chaining
# ---------------------------------------------------------------------------
section("3. average linkage does not chain")

# a~b 0.65, b~c 0.65, a~c 0.2. Single linkage would put all three together.
gram = [[1.0, 0.65, 0.2], [0.65, 1.0, 0.65], [0.2, 0.65, 1.0]]
vecs = vectors_from_gram(gram)
check("fixture similarities are exact",
      abs(sm._dot(vecs[0], vecs[1]) - 0.65) < 1e-9 and abs(sm._dot(vecs[0], vecs[2]) - 0.2) < 1e-9)
arts = [article(c, vecs[i], source=c) for i, c in enumerate("abc")]
groups = sm.group_stories(arts, threshold=0.6)
check("a and c are not in the same story", membership(groups, 3)[0] != membership(groups, 3)[2],
      str(groups))
check("exactly one pair merged", sorted(len(g) for g in groups) == [1, 2], str(groups))


# ---------------------------------------------------------------------------
# 4. Ranking
# ---------------------------------------------------------------------------
section("4. distinct outlets outrank size")

vecs = vectors_from_gram(block_gram([4, 3, 1], within=0.9, between=0.05))
arts = [article(f"one-outlet update {i}", vecs[i], source="Daily Bugle") for i in range(4)]
arts += [article(f"wire story {i}", vecs[4 + i], source=src)
         for i, src in enumerate(["Reuters", "BBC", "AP"])]
arts += [article("a lone story", vecs[7], source="Herald")]

stories = sm.build_stories(arts, threshold=0.6)
check("three stories found", len(stories) == 3, str([s["members"] for s in stories]))
check("3 outlets x 3 articles ranks above 1 outlet x 4 articles",
      stories[0]["members"] == [4, 5, 6] and stories[1]["members"] == [0, 1, 2, 3],
      str([s["members"] for s in stories]))
check("coverage names the count and the outlets",
      stories[0]["coverage"] == "3 articles · AP, BBC, Reuters", stories[0]["coverage"])
check("a single outlet is named once with its count",
      stories[1]["coverage"] == "4 articles · Daily Bugle", stories[1]["coverage"])


# ---------------------------------------------------------------------------
# 5. The lede
# ---------------------------------------------------------------------------
section("5. the lede: first bullet of the most central article")

# Article 2 is the most central but was never enriched; article 1 is next and
# has bullets; article 0 is the least central and also has bullets.
gram = block_gram([3], within=0.7, overrides={(0, 1): 0.62, (1, 2): 0.95, (0, 2): 0.8})
vecs = vectors_from_gram(gram)
arts = [
    article("peripheral write-up", vecs[0], "A", ["Peripheral first sentence about it.",
                                                   "Peripheral second sentence."]),
    article("central write-up", vecs[1], "B", ["The central article's lede sentence.",
                                                "Its second sentence."]),
    article("central but unenriched", vecs[2], "C", []),
]
story = sm.build_stories(arts, threshold=0.6)[0]
check("one story of three", story["size"] == 3, str(story["members"]))
check("fixture: the unenriched article really is the most central",
      sm._dot(vecs[2], [sum(c) for c in zip(*vecs[:3])])
      > sm._dot(vecs[1], [sum(c) for c in zip(*vecs[:3])]))
check("the representative is the most central article WITH bullets",
      story["representative"] == 1, f"got {story['representative']}")
check("the line is its FIRST bullet", story["text"] == "The central article's lede sentence.",
      story["text"])
check("from_bullet is recorded", story["from_bullet"] is True)

vecs = vectors_from_gram(block_gram([2], within=0.9))
arts = [article("Headline one of an unenriched story", vecs[0], "A"),
        article("Headline two of an unenriched story", vecs[1], "B")]
story = sm.build_stories(arts, threshold=0.6)[0]
check("no bullets anywhere -> the headline is used",
      story["text"] in {a["title"] for a in arts}, story["text"])
check("...and from_bullet says so", story["from_bullet"] is False)


# ---------------------------------------------------------------------------
# 6. Nothing lost, nothing invented
# ---------------------------------------------------------------------------
section("6. counts and fallbacks")

vecs = vectors_from_gram(block_gram([3, 2], within=0.85, between=0.1))
arts = [article(f"grouped {i}", vecs[i], source=f"Outlet {i}",
                bullets=[f"Extracted sentence number {i} about the event."]) for i in range(5)]
arts.append(article("never enriched", None, source="Outlet 5"))
arts.append(article("foreign dimension", None, source="Outlet 6"))
arts[-1]["embedding"] = pack([0.1] * 512)
arts.append(article("zero vector", None, source="Outlet 7"))
arts[-1]["embedding"] = pack([0.0] * DIM)
arts.append(article("truncated blob", None, source="Outlet 8"))
arts[-1]["embedding"] = b"\x00\x01\x02"

stories = sm.build_stories(arts, threshold=0.6)
all_members = sorted(i for s in stories for i in s["members"])
check("every article is in exactly one story", all_members == list(range(len(arts))),
      str(all_members))
check("story sizes sum to the input count", sum(s["size"] for s in stories) == len(arts))
for idx in (5, 6, 7, 8):
    story = next(s for s in stories if idx in s["members"])
    check(f"'{arts[idx]['title']}' is a single-article story", story["size"] == 1)

text, generator = sm._stories_v2(TOPIC, arts, threshold=0.6)
lines = text.split("\n")
check("generator id is stories:v2", generator == "stories:v2")
check("the opening states the input count", lines[0].startswith(f"{len(arts)} "), lines[0])
check("multi-article stories only when there are enough (2 here, topped up to 3)",
      len(lines) == 1 + sm.MIN_STORIES, text)
check("the biggest story leads, with its true count", "(3 articles" in lines[1], lines[1])
check("the second story carries its true count", "(2 articles" in lines[2], lines[2])
check("the top-up story says 1 article", "(1 article ·" in lines[3], lines[3])
check("lines quote extracted sentences verbatim",
      lines[1].startswith("• Extracted sentence number "), lines[1])

check("'and N others' appears only when there are others",
      sm._coverage(["A", "B", "C", "D", "E"], 6) == "6 articles · A, B, C and 2 others"
      and sm._coverage(["A", "B", "C", "D"], 4) == "4 articles · A, B, C and 1 other"
      and sm._coverage(["A", "B"], 2) == "2 articles · A, B",
      sm._coverage(["A", "B", "C", "D", "E"], 6))

vecs = vectors_from_gram(block_gram([1, 1, 1, 1], within=1.0, between=0.1))
arts = [article(f"scattered {i}", vecs[i], source=f"O{i}") for i in range(4)]
text, _ = sm._stories_v2(TOPIC, arts, threshold=0.6)
check("a week with no repeated story does not claim 'most covered'",
      "Most covered" not in text and "no story appeared in more than one" in text, text)
check("...and shows MIN_STORIES of them", len(text.split("\n")) == 1 + sm.MIN_STORIES, text)

check("deterministic: the same input gives the same text",
      sm._stories_v2(TOPIC, arts, 0.6) == sm._stories_v2(TOPIC, arts, 0.6))


# ---------------------------------------------------------------------------
# 8. Anthropic falls back to stories:v2   (no network: requests.post is faked)
# ---------------------------------------------------------------------------
section("8. anthropic fallback")


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


vecs = vectors_from_gram(block_gram([2, 1, 1], within=0.9, between=0.1))
arts = [article(f"fallback {i}", vecs[i], source=f"O{i}") for i in range(4)]
saved = (sm.SUMMARY_GENERATOR, sm.ANTHROPIC_API_KEY, sm.SUMMARY_MODEL, sm.requests.post)
sent = {}


def fake_post(url, headers=None, json=None, timeout=None):
    sent["body"] = json
    return sent["reply"]


try:
    logging.getLogger("paperswap.summarizer").disabled = True   # expected warnings
    sm.SUMMARY_GENERATOR, sm.ANTHROPIC_API_KEY, sm.SUMMARY_MODEL = "anthropic", "", "test-model"
    _, gen = sm.summarise(TOPIC, arts)
    check("missing API key -> stories:v2, labelled as such", gen == "stories:v2", gen)

    sm.ANTHROPIC_API_KEY = "test-key"
    sm.requests.post = fake_post

    sent["reply"] = FakeResponse(200, {"stop_reason": "max_tokens",
                                       "content": [{"type": "text", "text": "Cut off mid"}]})
    text, gen = sm.summarise(TOPIC, arts)
    check("a truncated reply is not stored as the summary",
          gen == "stories:v2" and "Cut off mid" not in text, gen)

    sent["reply"] = FakeResponse(500, {"error": "overloaded"})
    _, gen = sm.summarise(TOPIC, arts)
    check("an HTTP error -> stories:v2", gen == "stories:v2", gen)

    sent["reply"] = FakeResponse(200, {"stop_reason": "end_turn",
                                       "content": [{"type": "text", "text": "A real digest."}]})
    text, gen = sm.summarise(TOPIC, arts)
    check("a complete reply is used and names the model",
          text == "A real digest." and gen == "anthropic:test-model", f"{gen}: {text}")
    prompt = sent["body"]["messages"][0]["content"]
    check("the model is fed story lines with coverage, not raw article rows",
          "[2 articles · O0, O1]" in prompt, prompt[-300:])
    check("thinking is disabled (it is billed as output)",
          sent["body"].get("thinking") == {"type": "disabled"}, str(sent["body"].get("thinking")))
    check("max_tokens is the cost ceiling, not thinking headroom",
          sent["body"]["max_tokens"] <= 1000, str(sent["body"]["max_tokens"]))

    # A week of huge headlines and ledes must not grow the prompt without bound.
    # 20 unrelated stories, so the 15-story cap and the char cap both bind.
    big_vecs = vectors_from_gram(block_gram([1] * 20, within=1.0, between=0.0))
    big = [article(f"{i} " + "H" * 5000, big_vecs[i], source=f"{i} " + "S" * 500,
                   bullets=["L" * 5000]) for i in range(20)]
    sm.summarise(TOPIC, big)
    prompt = sent["body"]["messages"][0]["content"]
    n_lines = prompt.count("\n- [")
    check("the prompt has a hard length ceiling",
          len(prompt) <= len(sm._PROMPT) + sm._ANTHROPIC_MAX_LINES_CHARS + 50, f"{len(prompt)} chars")
    check("...reached by dropping the least-covered stories, not by cutting a line",
          0 < n_lines < sm._ANTHROPIC_MAX_STORIES and prompt.rstrip().endswith("…"),
          f"{n_lines} story lines")
finally:
    sm.SUMMARY_GENERATOR, sm.ANTHROPIC_API_KEY, sm.SUMMARY_MODEL, sm.requests.post = saved
    logging.getLogger("paperswap.summarizer").disabled = False


# ---------------------------------------------------------------------------
# 9. The week query never reaches a route
# ---------------------------------------------------------------------------
section("9. BYTEA stays off the request path")
import main  # noqa: E402

check("no route in main.py calls get_articles_for_week",
      "get_articles_for_week" not in inspect.getsource(main),
      "its rows carry the embedding BYTEA, which is not JSON-serialisable (invariant 9)")


# ---------------------------------------------------------------------------
# 7. Database round trip
# ---------------------------------------------------------------------------
FIXTURE_URL_PREFIX = "https://example.com/stories/"
WEEK = sm.last_completed_week_start() - datetime.timedelta(weeks=20)


def at(day, hour):
    return datetime.datetime.combine(WEEK + datetime.timedelta(days=day), datetime.time(hour),
                                     tzinfo=datetime.timezone.utc)


def drop_fixtures():
    with db.db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM articles WHERE url LIKE %s", (FIXTURE_URL_PREFIX + "%",))
        cur.execute("DELETE FROM topic_summaries WHERE week_start = %s", (WEEK,))


db.init_pool(minconn=1, maxconn=4)
try:
    db.init_db()
    drop_fixtures()
    section("7. database round trip and default generator")

    n_live = db.count_articles_for_week(TOPIC, WEEK)
    check(f"fixture week {WEEK} is clean", n_live == 0, f"found {n_live} live article(s)")

    vecs = vectors_from_gram(block_gram([3, 1], within=0.85, between=0.1))
    bullets = [["First extracted sentence of the shared story, long enough.", "Second."],
               ["Another outlet's opening sentence about the same story."],
               [], ["The lone story's own opening sentence, long enough."]]
    for i in range(4):
        aid, _ = db.save_article({
            "title": f"stories fixture {i}", "url": f"{FIXTURE_URL_PREFIX}{i}",
            "description": f"A genuine publisher description {i}, long enough to keep.",
            "description_source": nf.DESC_PUBLISHER, "source": f"Outlet {i}",
            "published_at": "Recently", "category": TOPIC, "image_url": "",
        })
        with db.db_cursor(commit=True) as cur:
            cur.execute("UPDATE articles SET created_at = %s, embedding = %s, "
                        "summary_bullets = %s WHERE id = %s",
                        (at(1, 8 + i), pack(vecs[i]),
                         json.dumps(bullets[i]) if bullets[i] else None, aid))
    # One with a corrupt bullets column: it must read as [], not crash the week.
    aid, _ = db.save_article({
        "title": "stories fixture corrupt", "url": f"{FIXTURE_URL_PREFIX}corrupt",
        "description": "A genuine publisher description for the corrupt row, long enough.",
        "description_source": nf.DESC_PUBLISHER, "source": "Outlet 9",
        "published_at": "Recently", "category": TOPIC, "image_url": "",
    })
    with db.db_cursor(commit=True) as cur:
        cur.execute("UPDATE articles SET created_at = %s, summary_bullets = 'not json' "
                    "WHERE id = %s", (at(2, 9), aid))

    # The corrupt row logs a WARNING by design; keep a passing run quiet.
    logging.getLogger("paperswap.database").disabled = True
    rows = db.get_articles_for_week(TOPIC, WEEK)
    check("all five fixtures returned", len(rows) == 5, str(len(rows)))
    check("summary_bullets comes back as a list",
          rows[0]["summary_bullets"] == bullets[0], str(rows[0]["summary_bullets"]))
    check("a NULL bullets column comes back as []", rows[2]["summary_bullets"] == [])
    check("an unparseable bullets column comes back as []", rows[4]["summary_bullets"] == [])
    check("embedding comes back as bytes", isinstance(rows[0]["embedding"], bytes),
          type(rows[0]["embedding"]).__name__)
    decoded = struct.unpack(f"<{DIM}f", rows[0]["embedding"])
    check("embedding round-trips exactly (float32)",
          all(abs(a - b) < 1e-6 for a, b in zip(decoded, vecs[0])))
    check("an un-embedded row comes back as None", rows[4]["embedding"] is None)

    saved_gen = sm.SUMMARY_GENERATOR
    sm.SUMMARY_GENERATOR = "stories:v2"
    try:
        sm.generate_weekly_summaries(week_start=WEEK)
    finally:
        sm.SUMMARY_GENERATOR = saved_gen
        logging.getLogger("paperswap.database").disabled = False
    row = next((r for r in db.get_topic_summaries(WEEK) if r["topic"] == TOPIC), None)
    check("a summary row was written", row is not None)
    if row:
        check("generator column records stories:v2", row["generator"] == "stories:v2",
              row["generator"])
        check("number_of_articles is what the generator read (unchanged by grouping)",
              row["number_of_articles"] == 5, str(row["number_of_articles"]))
        check("articles_in_window unchanged", row["articles_in_window"] == 5)
        check("the shared story leads with its first bullet",
              row["summary"].split("\n")[1].startswith("• First extracted sentence"),
              row["summary"])
        check("the basis sentence describes stories:v2", "grouped into stories" in row["basis"],
              row["basis"])
        check("the API payload is JSON-serialisable", bool(json.dumps(row)))

    check("the shipped default generator is stories:v2",
          'os.getenv("SUMMARY_GENERATOR", "stories:v2")' in inspect.getsource(sm))
finally:
    try:
        drop_fixtures()
    finally:
        db.close_pool()


print("\n" + "=" * 62)
if fails:
    print(f"FAILED ({len(fails)}): " + ", ".join(fails))
    sys.exit(1)
print("All stories:v2 checks passed.")
