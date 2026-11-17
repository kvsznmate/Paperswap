"""Limits for a public API: bounded writes (#56) and a cheap feed (#57).

Claims under test:
  1. GlobalBudget, TTLCache and ColdStartGate behave as documented.
  2. A session can store at most one swipe per article; a repeat is
     acknowledged (200 "duplicate") and writes nothing to either swipe table.
     Clients that send no session_id are unaffected.
  3. A spent global swipe budget returns 503 with Retry-After.
  4. Heartbeat fields are length-limited, and NEW sessions draw on a global
     budget while existing sessions are always refreshed.
  5. Responses are gzip-compressed, and public GETs carry Cache-Control.
  6. Identical feed requests are served from memory; an empty deck is not cached.
  7. The cold-start fetch runs at most once per topic selection per cooldown.

Runs against a live Postgres with the pool open, bypassing lifespan() so no
real news fetch happens (the fetch function is replaced in section 7).

Run:  python tests/test_public_limits.py
"""
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ADMIN_API_KEY"] = "test-key"

import importlib
import database as db
import main as m
from public_limits import ColdStartGate, GlobalBudget, TTLCache

importlib.reload(m)
from fastapi.testclient import TestClient

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(name)


db.init_pool()
db.init_db()
c = TestClient(m.app, raise_server_exceptions=False)
AID, _ = db.save_article({
    "title": f"public limits fixture {uuid.uuid4().hex[:8]}",
    "url": f"https://example.com/limits/{uuid.uuid4().hex}",
    "description": "d", "source": "s", "published_at": "now", "category": "TECH",
})


print("--- 1. Building blocks ---")
b = GlobalBudget("t", 2, window_seconds=3600)
check("budget allows up to its limit", b.take() and b.take())
check("budget refuses past its limit", not b.take())
check("refusals are counted", b.snapshot()["rejected_since_start"] == 1)
check("retry_after is within the window", 1 <= b.retry_after() <= 3600, f"({b.retry_after()})")
short = GlobalBudget("t", 1, window_seconds=0.05)
short.take()
time.sleep(0.08)
check("budget resets after its window", short.take())

cache = TTLCache(0.05)
cache.set("k", {"v": 1})
check("cache returns a fresh value", cache.get("k") == {"v": 1})
time.sleep(0.08)
check("cache expires values", cache.get("k") is None)
small = TTLCache(60, max_entries=2)
for k in ("a", "b", "c"):
    small.set(k, k)
check("cache cannot grow past max_entries", small.get("a") is None and small.get("c") == "c")

gate = ColdStartGate(600)
calls = []
check("gate runs the first fetch", gate.run_once("A", lambda: calls.append(1)))
check("gate skips a repeat within the cooldown", not gate.run_once("A", lambda: calls.append(1)))
check("gate keys are independent", gate.run_once("B", lambda: calls.append(1)))


def boom():
    raise RuntimeError("upstream down")


try:
    gate.run_once("C", boom)
except RuntimeError:
    pass
check("a failed fetch still starts the cooldown", not gate.run_once("C", lambda: calls.append(1)))
gate._lock.acquire()
check("a fetch in progress makes others skip, not wait",
      not gate.run_once("D", lambda: calls.append(1)))
gate._lock.release()
check("fetch count is exact", len(calls) == 2, f"({len(calls)})")


def swipe_rows(session_id):
    with db.db_cursor() as cur:
        cur.execute("SELECT (SELECT COUNT(*) FROM user_swipes WHERE session_id = %s) us,"
                    "       (SELECT COUNT(*) FROM swipe_events WHERE session_id = %s) se",
                    (session_id, session_id))
        r = cur.fetchone()
        return r["us"], r["se"]


print("\n--- 2. One stored swipe per session per article ---")
sid = f"test-{uuid.uuid4().hex}"
r1 = c.post("/api/v1/swipe", json={"article_id": AID, "action": "read", "session_id": sid})
r2 = c.post("/api/v1/swipe", json={"article_id": AID, "action": "pass", "session_id": sid})
check("first swipe -> 201 recorded",
      r1.status_code == 201 and r1.json()["status"] == "recorded", f"({r1.status_code})")
check("repeat -> 200 duplicate",
      r2.status_code == 200 and r2.json()["status"] == "duplicate", f"({r2.status_code})")
check("exactly one row in each swipe table", swipe_rows(sid) == (1, 1), f"{swipe_rows(sid)}")
r3 = c.post("/api/v1/swipe", json={"article_id": AID, "action": "pass", "session_id": "x" * 65})
check("over-long session_id -> 422", r3.status_code == 422, f"({r3.status_code})")
with db.db_cursor() as cur:
    cur.execute("SELECT COUNT(*) n FROM user_swipes WHERE article_id = %s AND session_id IS NULL",
                (AID,))
    before = cur.fetchone()["n"]
for _ in range(2):
    c.post("/api/v1/swipe", json={"article_id": AID, "action": "pass"})
with db.db_cursor() as cur:
    cur.execute("SELECT COUNT(*) n FROM user_swipes WHERE article_id = %s AND session_id IS NULL",
                (AID,))
    after = cur.fetchone()["n"]
check("clients without session_id are not deduplicated", after - before == 2, f"(+{after-before})")


print("\n--- 3. Global swipe budget ---")
saved_swipes = m.SWIPE_BUDGET
m.SWIPE_BUDGET = GlobalBudget("swipes", 1)
try:
    ok = c.post("/api/v1/swipe", json={"article_id": AID, "action": "pass"})
    busy = c.post("/api/v1/swipe", json={"article_id": AID, "action": "pass"})
    check("within budget -> 201", ok.status_code == 201, f"({ok.status_code})")
    check("budget spent -> 503", busy.status_code == 503, f"({busy.status_code})")
    check("503 carries Retry-After", busy.headers.get("retry-after", "").isdigit(),
          f"({busy.headers.get('retry-after')})")
finally:
    m.SWIPE_BUDGET = saved_swipes


print("\n--- 4. Heartbeats: field limits and the new-session budget ---")
check("over-long session_id -> 422",
      c.post("/api/v1/telemetry/heartbeat", json={"session_id": "s" * 65}).status_code == 422)
check("over-long user_agent -> 422",
      c.post("/api/v1/telemetry/heartbeat",
             json={"session_id": "ok", "user_agent": "u" * 257}).status_code == 422)
saved_sessions = m.SESSION_BUDGET
m.SESSION_BUDGET = GlobalBudget("new_sessions", 1)
try:
    a, b2 = f"hb-{uuid.uuid4().hex}", f"hb-{uuid.uuid4().hex}"
    first = c.post("/api/v1/telemetry/heartbeat", json={"session_id": a})
    second = c.post("/api/v1/telemetry/heartbeat", json={"session_id": b2})
    again = c.post("/api/v1/telemetry/heartbeat", json={"session_id": a})
    check("new session within budget -> 200", first.status_code == 200, f"({first.status_code})")
    check("new session past budget -> 503", second.status_code == 503, f"({second.status_code})")
    check("existing session still refreshed -> 200", again.status_code == 200,
          f"({again.status_code})")
    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM user_sessions WHERE session_id = %s", (b2,))
        check("rejected session wrote no row", cur.fetchone()["n"] == 0)
finally:
    m.SESSION_BUDGET = saved_sessions


print("\n--- 5. Compression and Cache-Control ---")
page = c.get("/mobile", headers={"Accept-Encoding": "gzip"})
check("large responses are gzip-compressed",
      page.headers.get("content-encoding") == "gzip", f"({page.headers.get('content-encoding')})")
m.invalidate_public_caches()
for path, age in (("/api/v1/feed", m.PUBLIC_CACHE_SECONDS),
                  ("/api/v1/categories", m.PUBLIC_CACHE_SECONDS),
                  ("/api/v1/summaries", m.SUMMARY_CACHE_SECONDS)):
    cc = c.get(path).headers.get("cache-control", "")
    check(f"{path} Cache-Control public, max-age={age}", cc == f"public, max-age={age}", f"({cc})")


print("\n--- 6. Identical feed requests hit memory, not Postgres ---")
real_feed = m.db.get_balanced_feed
feed_calls = []


def counting_feed(*args, **kwargs):
    feed_calls.append(1)
    return real_feed(*args, **kwargs)


m.db.get_balanced_feed = counting_feed
try:
    m.invalidate_public_caches()
    first = c.get("/api/v1/feed")
    c.get("/api/v1/feed")
    check("deck is non-empty (fixture present)", first.json()["count"] > 0)
    check("two identical requests -> one query", len(feed_calls) == 1, f"({len(feed_calls)})")
    m.invalidate_public_caches()
    c.get("/api/v1/feed")
    check("invalidation forces a fresh query", len(feed_calls) == 2, f"({len(feed_calls)})")
finally:
    m.db.get_balanced_feed = real_feed


print("\n--- 7. Cold-start fetch: once per topic selection per cooldown ---")
real_fetch = m.fetch_and_sync_news_to_db
saved_gate = m._COLD_START
fetches, queries = [], []


def empty_feed(*args, **kwargs):
    queries.append(1)
    return []


m.fetch_and_sync_news_to_db = lambda categories=None: fetches.append(categories)
m.db.get_balanced_feed = empty_feed
m._COLD_START = ColdStartGate(600)
try:
    m.invalidate_public_caches()
    for _ in range(3):
        r = c.get("/api/v1/feed?categories=BEAUTY")
    check("empty topic still answers 200", r.status_code == 200 and r.json()["count"] == 0)
    check("three requests -> one fetch", len(fetches) == 1, f"({len(fetches)})")
    check("the fetch was scoped to the requested topic", fetches[:1] == [["BEAUTY"]],
          f"({fetches[:1]})")
    check("empty decks are not cached (each request queried)", len(queries) >= 3,
          f"({len(queries)})")
    c.get("/api/v1/feed?categories=SPORTS")
    check("a different topic selection gets its own fetch", len(fetches) == 2, f"({len(fetches)})")
finally:
    m.fetch_and_sync_news_to_db = real_fetch
    m.db.get_balanced_feed = real_feed
    m._COLD_START = saved_gate
    m.invalidate_public_caches()


db.close_pool()
print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}'}")
sys.exit(1 if fails else 0)
