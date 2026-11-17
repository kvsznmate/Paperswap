"""Feed fields behind the Paper card design (#13, ADR-013).

Claims under test:
  1. upgrade_image_url swaps BBC's 240 px thumbnail for the 976 px image and
     leaves every other URL, '' and None alone.
  2. The feed marks stock filler photos (image_is_stock), so a client can drop
     them, and serves BBC images at 976 px even for rows stored at 240 px.
  3. The feed returns description_source as stored, so a client can hide a
     canned blurb. A NULL (row older than the column) stays null: it is never
     guessed (invariant 5).
  4. The new fields do not drag the embedding BYTEA into the payload.

Needs Postgres. Bypasses lifespan(), so no real news fetch happens.

Run:  python tests/test_paper_feed.py
"""
import os
import sys
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


print("\n--- 2-4. The feed ---")
db.init_pool()
db.init_db()
c = TestClient(m.app, raise_server_exceptions=False)

PREFIX = f"https://example.com/paper/{uuid.uuid4().hex[:8]}/"
STOCK = sorted(nf._STOCK_IMAGES)[0]
fixtures = {
    "stock": {"image_url": STOCK, "description_source": "topic_fallback"},
    "bbc": {"image_url": BBC_240, "description_source": "publisher"},
    "legacy": {"image_url": "https://e0.365dm.com/a.jpg", "description_source": None},
}
for key, extra in fixtures.items():
    db.save_article({
        "title": f"paper feed fixture {key} {uuid.uuid4().hex[:6]}",
        "url": PREFIX + key, "description": "d", "source": "s",
        "published_at": "now", "category": "BEAUTY", **extra,
    })

try:
    m.invalidate_public_caches()
    r = c.get("/api/v1/feed", params={"categories": "BEAUTY", "limit": 300, "balanced": "false"})
    check("feed answers 200", r.status_code == 200, r.status_code)
    arts = r.json().get("articles", [])
    by_key = {a["url"][len(PREFIX):]: a for a in arts if a.get("url", "").startswith(PREFIX)}
    check("all three fixtures are in the feed", set(by_key) == set(fixtures), sorted(by_key))

    if set(by_key) == set(fixtures):
        check("stock filler is flagged", by_key["stock"]["image_is_stock"] is True)
        check("a real photo is not flagged", by_key["bbc"]["image_is_stock"] is False
              and by_key["legacy"]["image_is_stock"] is False)
        check("a BBC row stored at 240 px is served at 976 px",
              by_key["bbc"]["image_url"] == BBC_976, by_key["bbc"]["image_url"])
        check("description_source comes back as stored",
              by_key["stock"]["description_source"] == "topic_fallback"
              and by_key["bbc"]["description_source"] == "publisher")
        check("a NULL description_source stays null, not guessed",
              by_key["legacy"]["description_source"] is None,
              by_key["legacy"]["description_source"])

    check("every card carries both new fields",
          all("image_is_stock" in a and "description_source" in a for a in arts))
    check("no card carries the embedding", all("embedding" not in a for a in arts))

    m.invalidate_public_caches()
    r2 = c.get("/api/v1/feed", params={"categories": "BEAUTY", "limit": 300})
    arts2 = [a for a in r2.json().get("articles", []) if a.get("url", "").startswith(PREFIX)]
    check("the balanced deck carries the fields too",
          len(arts2) == 3 and all("image_is_stock" in a and "description_source" in a for a in arts2),
          len(arts2))
finally:
    with db.db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM articles WHERE url LIKE %s", (PREFIX + "%",))
    m.invalidate_public_caches()


print("\n" + "=" * 62)
if fails:
    print(f"FAILED ({len(fails)}): " + ", ".join(fails))
    sys.exit(1)
print("All Paper feed checks passed.")
