"""Related coverage and the no-article-text rule (ADR-015, #66).

Claims under test:
  1. compute_related links an article to the closest articles from OTHER
     publishers, most similar first, capped at top_k, at or above the
     threshold, within the time window. An unknown time is not excluded, and
     an article with no neighbour maps to [] (computed), not to a missing key.
  2. refresh_related stores related_ids as a JSON array, leaves rows with no
     embedding (and vectors from another model) at NULL, and writes nothing on
     a second run with nothing new (it only writes changes).
  3. The feed serves `related` as headline + publisher + link only, drops an id
     whose article has since been purged, and carries no related_ids,
     summary_bullets or full_text.
  4. No article text: the articles table has no full_text or summary_bullets
     column, enrichment does not fetch pages, and trafilatura is not a
     dependency.
  5. Feed fetching names itself (FETCH_USER_AGENT) and obeys robots.txt with
     RFC 9309 status rules: 4xx = allowed, 5xx/unreachable = disallowed, and a
     Disallow rule for our agent is honoured.

Needs Postgres for 2-4. Bypasses lifespan(), so no real news fetch happens.

Run:  python tests/test_related.py
"""
import datetime
import email.message
import inspect
import io
import json
import os
import sys
import urllib.error
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ADMIN_API_KEY"] = "test-key"

import importlib
import numpy as np
import database as db
import enrichment as en
import news_fetcher as nf
import main as m

importlib.reload(m)
from fastapi.testclient import TestClient

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + str(detail) if detail else ''}")
    if not cond:
        fails.append(name)


def unit(*xs):
    v = np.zeros(en.EMBED_DIM, dtype=np.float32)
    v[:len(xs)] = xs
    return v / np.linalg.norm(v)


print("--- 1. compute_related ---")
T0 = datetime.datetime(2026, 11, 18, 12, tzinfo=datetime.timezone.utc)
hours = datetime.timedelta(hours=1)
#           id  source  time        vector
cases = [(1, "A", T0, unit(1, 0.00)),
         (2, "B", T0, unit(1, 0.10)),           # ~0.995 with 1
         (3, "C", T0, unit(1, 0.40)),           # ~0.93 with 1
         (4, "A", T0, unit(1, 0.05)),           # same publisher as 1
         (5, "D", T0, unit(0, 1.00)),           # unrelated
         (6, "E", T0 + 100 * hours, unit(1, 0.0)),  # same story, outside 72 h
         (7, "F", None, unit(1, 0.20))]         # unknown time
ids, srcs, times, vecs = zip(*cases)
rel = en.compute_related(list(ids), list(srcs), list(times), np.vstack(vecs),
                         threshold=0.55, top_k=3, window_hours=72)
check("every article gets an entry", set(rel) == set(ids), sorted(rel))
check("same publisher never counts", 4 not in rel[1] and 1 not in rel[4], rel[1])
check("most similar first, capped at top_k", rel[1] == [2, 7, 3], rel[1])
check("below the threshold -> []", rel[5] == [], rel[5])
check("outside the time window is excluded", 6 not in rel[1] and 1 not in rel[6], rel[6])
check("an unknown time is not excluded", 7 in rel[1], rel[1])
check("the empty deck is fine", en.compute_related([], [], [], np.zeros((0, en.EMBED_DIM))) == {})


print("\n--- 2-3. Database and feed ---")
db.init_pool()
db.init_db()
c = TestClient(m.app, raise_server_exceptions=False)
PREFIX = f"https://example.com/related/{uuid.uuid4().hex[:8]}/"
TAG = uuid.uuid4().hex[:6]

fixture_vecs = {
    "bbc": unit(1, 0.00), "npr": unit(1, 0.10), "cnbc": unit(1, 0.30),
    "lone": unit(0, 1.00), "foreign": None, "unembedded": None,
}
row_ids = {}
for key, vec in fixture_vecs.items():
    aid, _ = db.save_article({
        "title": f"related fixture {key} {TAG}", "url": PREFIX + key,
        "description": "d", "description_source": "publisher",
        "source": f"Outlet {key} {TAG}", "published_at": "now", "category": "BEAUTY",
    })
    row_ids[key] = aid
    blob = db.pack_embedding(vec) if vec is not None else None
    if key == "foreign":
        blob = db.pack_embedding(np.ones(512, dtype=np.float32) / np.sqrt(512))
    if blob is not None:
        with db.db_cursor(commit=True) as cur:
            cur.execute("UPDATE articles SET embedding = %s WHERE id = %s", (blob, aid))

try:
    first = en.refresh_related()
    with db.db_cursor() as cur:
        cur.execute("SELECT id, related_ids FROM articles WHERE url LIKE %s", (PREFIX + "%",))
        stored = {r["id"]: r["related_ids"] for r in cur.fetchall()}
    by_key = {k: stored[v] for k, v in row_ids.items()}
    check("related_ids is a JSON array, most similar first",
          json.loads(by_key["bbc"] or "null") == [row_ids["npr"], row_ids["cnbc"]], by_key["bbc"])
    check("no neighbour -> [] (computed, none found)", by_key["lone"] == "[]", by_key["lone"])
    check("no embedding -> NULL (not computed)", by_key["unembedded"] is None)
    check("a vector from another model is left out -> NULL", by_key["foreign"] is None)
    check("the run reports the foreign vector", first["skipped_foreign_dim"] >= 1, first)

    second = en.refresh_related()
    check("a second run with nothing new writes nothing", second["changed"] == 0, second)

    m.invalidate_public_caches()
    r = c.get("/api/v1/feed", params={"categories": "BEAUTY", "limit": 300, "balanced": "false"})
    arts = {a["url"]: a for a in r.json().get("articles", [])}
    bbc = arts.get(PREFIX + "bbc", {})
    check("feed answers 200", r.status_code == 200, r.status_code)
    check("the card carries `related`, resolved",
          [x.get("id") for x in bbc.get("related", [])] == [row_ids["npr"], row_ids["cnbc"]],
          bbc.get("related"))
    check("each related entry is headline + publisher + link only",
          all(set(x) == {"id", "title", "source", "url"} for x in bbc.get("related", [])),
          bbc.get("related"))
    check("every card has `related` and none has related_ids, bullets or full text",
          all("related" in a and not ({"related_ids", "summary_bullets", "full_text"} & set(a))
              for a in arts.values()))

    with db.db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM articles WHERE id = %s", (row_ids["npr"],))
    m.invalidate_public_caches()
    r = c.get("/api/v1/feed", params={"categories": "BEAUTY", "limit": 300, "balanced": "false"})
    bbc = next((a for a in r.json()["articles"] if a["url"] == PREFIX + "bbc"), {})
    check("a purged related article drops out instead of dangling",
          [x["id"] for x in bbc.get("related", [])] == [row_ids["cnbc"]], bbc.get("related"))

    print("\n--- 4. No article text ---")
    with db.db_cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'articles'")
        columns = {r["column_name"] for r in cur.fetchall()}
    check("articles has no full_text or summary_bullets column",
          not ({"full_text", "summary_bullets"} & columns), sorted(columns))
    check("articles has related_ids", "related_ids" in columns)
finally:
    with db.db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM articles WHERE url LIKE %s", (PREFIX + "%",))
    m.invalidate_public_caches()

src = inspect.getsource(en)
check("enrichment fetches no pages",
      not any(w in src for w in ("trafilatura", "urlopen", "requests.", "fetch_url")))
req = open(os.path.join(os.path.dirname(en.__file__), "requirements.txt")).read()
check("trafilatura is not a dependency", "trafilatura" not in req)


print("\n--- 5. Polite fetching ---")


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def robots_answer(answer):
    def fake_urlopen(req, timeout=None):
        fake_urlopen.agent = req.get_header("User-agent")
        if isinstance(answer, int):
            raise urllib.error.HTTPError(req.full_url, answer, "x", email.message.Message(), None)
        if answer is None:
            raise urllib.error.URLError("unreachable")
        return FakeResp(answer.encode())
    return fake_urlopen


saved_urlopen = nf.urllib.request.urlopen
try:
    for answer, want, label in [
        (403, True, "robots.txt 403 -> allowed (RFC 9309: 4xx is 'unavailable')"),
        (404, True, "robots.txt 404 -> allowed"),
        (503, False, "robots.txt 503 -> disallowed (RFC 9309: 'unreachable')"),
        (None, False, "robots.txt unreachable -> disallowed"),
        ("User-agent: *\nDisallow:\n", True, "empty Disallow -> allowed"),
        ("User-agent: Paperswap\nDisallow: /\n", False, "Disallow for our agent is honoured"),
        ("User-agent: GPTBot\nDisallow: /\n", True, "a rule for another bot does not apply"),
    ]:
        nf._robots_cache.clear()
        fake = robots_answer(answer)
        nf.urllib.request.urlopen = fake
        got = nf.feed_allowed("https://feeds.example.com/rss.xml")
        check(label, got is want, got)
    check("robots.txt is requested with FETCH_USER_AGENT", fake.agent == nf.FETCH_USER_AGENT,
          fake.agent)
finally:
    nf.urllib.request.urlopen = saved_urlopen
    nf._robots_cache.clear()

check("the agent names the app", nf.FETCH_USER_AGENT.startswith("Paperswap/"), nf.FETCH_USER_AGENT)
check("feeds are fetched with that agent",
      "agent=FETCH_USER_AGENT" in inspect.getsource(nf.fetch_from_rss))


print("\n" + "=" * 62)
if fails:
    print(f"FAILED ({len(fails)}): " + ", ".join(fails))
    sys.exit(1)
print("All related-coverage checks passed.")
