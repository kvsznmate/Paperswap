"""Deduplication is the headline feature; this asserts it works BECAUSE of the
code rather than despite it.

Claims under test:
  1. save_article returns (id, was_inserted); a repeat returns the same id and
     was_inserted False.
  2. The read-then-write pre-check is gone -- is_article_in_db() no longer
     exists and nothing calls it.
  3. Under a real race on the same article_key, exactly ONE caller is told it
     inserted. This is the bug the pre-check had: two threads could both read
     "not present" and both increment new_count, while ON CONFLICT quietly kept
     the table correct underneath them.
  4. A new article now costs ONE transaction instead of two.

On (4): this counts transactions opened by the application, not
pg_stat_database.numbackends. Backends are the wrong instrument -- the pool
keeps three connections warm and the sync loop is sequential on one thread, so
it borrows and returns the same connection either way and numbackends reads
identically before and after. What actually changed is the number of
transactions, since every db_cursor() block commits or rolls back.

Note the honest scope of the win: it applies to articles that turn out to be
NEW. A duplicate cost one transaction before (the pre-check) and costs one now
(the insert that no-ops), so a steady-state refresh -- mostly duplicates -- sees
little change. The reason to make this change is claim 3.

Run:  python tests/test_dedup.py
"""
import os
import sys
import ast
import uuid
import threading
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(name)


def fixture(tag: str) -> dict:
    """A unique article, so reruns never collide with a previous run's rows."""
    return {
        "title": f"dedup fixture {tag}",
        "url": f"https://example.com/dedup/{tag}",
        "description": "d", "source": "s", "published_at": "now", "category": "TECH",
    }


# --- transaction counter ----------------------------------------------------
# Wraps db_cursor so we count exactly what the code opens, deterministically,
# with no dependency on Postgres stats-collector flush timing.
TXN = {"n": 0}
_real_db_cursor = db.db_cursor


@contextlib.contextmanager
def counting_cursor(commit: bool = False):
    TXN["n"] += 1
    with _real_db_cursor(commit=commit) as cur:
        yield cur


db.init_pool()
db.init_db()


print("--- 1. save_article reports whether it actually inserted ---")
art = fixture(uuid.uuid4().hex[:12])

first = db.save_article(art)
check("returns a 2-tuple", isinstance(first, tuple) and len(first) == 2, f"{first!r}")
id1, inserted1 = first
check("first call reports was_inserted=True", inserted1 is True, f"({inserted1!r})")
check("first call returns a real id", isinstance(id1, int) and id1 > 0, f"({id1})")

id2, inserted2 = db.save_article(art)
check("second call reports was_inserted=False", inserted2 is False, f"({inserted2!r})")
check("second call returns the SAME id", id2 == id1, f"({id2} vs {id1})")

# Same title+url through a different dict must hash to the same key.
id3, inserted3 = db.save_article(dict(art, description="different blurb"))
check("dedup keys on title+url, not the whole payload",
      id3 == id1 and inserted3 is False, f"({id3}, {inserted3})")


print("\n--- 2. The pre-check is gone ---")
check("is_article_in_db no longer exists", not hasattr(db, "is_article_in_db"))
check("article_exists (dead helper) removed too", not hasattr(db, "article_exists"))

def module_calls(src):
    """Every `name.attr(...)` call in the source, via AST.

    Deliberately not a substring grep: the fetch loop keeps a comment explaining
    what the old is_article_in_db() gate did and why it went, and a grep for the
    name matches that comment -- failing the check precisely because the fix is
    documented. Only real Call nodes count.
    """
    return {
        f"{n.func.value.id}.{n.func.attr}"
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
    }


FETCHER_SRC = open(os.path.join(BACKEND, "news_fetcher.py"), encoding="utf-8").read()
check("news_fetcher has no pre-check call",
      "db.is_article_in_db" not in module_calls(FETCHER_SRC))
check("news_fetcher counts from the insert result", "_, inserted = db.save_article" in FETCHER_SRC)

DB_SRC = open(os.path.join(BACKEND, "database.py"), encoding="utf-8").read()
check("save_article no longer prints per article", 'print(f"[DB] Article ID' not in DB_SRC)
check("ON CONFLICT is still what does the dedup", "ON CONFLICT (article_key) DO NOTHING" in DB_SRC)


print("\n--- 3. Concurrent inserts of the same key: exactly one winner ---")
# The actual race the pre-check introduced. Three code paths reach save_article
# with no mutual exclusion: the scheduler's refresh job, the cold-start fetch in
# GET /api/v1/feed, and the background task behind POST /cards/refresh.
racer = fixture(uuid.uuid4().hex[:12])
THREADS = 12
results = [None] * THREADS
barrier = threading.Barrier(THREADS)


def race(slot):
    barrier.wait()          # release all threads at the same instant
    try:
        results[slot] = db.save_article(racer)
    except Exception as exc:
        results[slot] = exc


threads = [threading.Thread(target=race, args=(i,)) for i in range(THREADS)]
for t in threads:
    t.start()
for t in threads:
    t.join()

errors = [r for r in results if isinstance(r, Exception)]
check("no thread raised", not errors, f"{errors[:2]}")

if not errors:
    winners = [r for r in results if r[1] is True]
    ids = {r[0] for r in results}
    check(f"exactly 1 of {THREADS} threads reports was_inserted=True",
          len(winners) == 1, f"({len(winners)})")
    check("every thread got the same id", len(ids) == 1, f"{ids}")

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM articles WHERE article_key = %s",
                    (db.generate_article_key(racer["title"], racer["url"]),))
        check("exactly one row stored", cur.fetchone()["n"] == 1)


print("\n--- 4. A new article costs one transaction, not two ---")
db.db_cursor = counting_cursor
try:
    fresh = [fixture(uuid.uuid4().hex[:12]) for _ in range(20)]

    TXN["n"] = 0
    for a in fresh:
        db.save_article(a)
    new_txns = TXN["n"]

    TXN["n"] = 0
    for a in fresh:
        db.save_article(a)
    dup_txns = TXN["n"]
finally:
    db.db_cursor = _real_db_cursor

check("20 NEW articles open 20 transactions (was 40: pre-check + insert)",
      new_txns == 20, f"({new_txns})")
check("20 DUPLICATE articles open 20 transactions (unchanged, as expected)",
      dup_txns == 20, f"({dup_txns})")
print(f"      -> a 84-article cold refresh: 168 transactions before, {84 * new_txns // 20} now.")

db.close_pool()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILURE(S): {fails}")
sys.exit(1 if fails else 0)
