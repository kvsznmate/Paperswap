"""Connection-pool guarantees against a LIVE Postgres.

Mirrors PS-07's acceptance criteria, in particular: 500 requests to the swipe
path with an invalid article_id, then confirm pg_stat_activity has not grown and
the service still responds.

Run:  python tests/test_live_pg.py
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import database as db

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(name)


def _dbname():
    with db.db_cursor() as cur:
        cur.execute("SELECT current_database() AS d")
        return cur.fetchone()["d"]


def backends(dbname):
    """Count server-side connections, measured from OUTSIDE the pool."""
    c = psycopg2.connect(db.DATABASE_URL)
    try:
        with c.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = %s", (dbname,))
            return cur.fetchone()[0]
    finally:
        c.close()


def idle_in_txn(dbname):
    c = psycopg2.connect(db.DATABASE_URL)
    try:
        with c.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = %s AND state = 'idle in transaction'", (dbname,))
            return cur.fetchone()[0]
    finally:
        c.close()


db.init_pool()
db.init_db()
DBNAME = _dbname()

valid_id = db.save_article({
    "title": "PS-07 live fixture", "url": "https://example.com/ps07",
    "description": "d", "source": "s", "published_at": "now", "category": "TECH",
})

baseline = backends(DBNAME)
print(f"\nbaseline backends: {baseline}\n")

# --- the ticket's scenario: 500 swipes with an invalid article_id -------------
errors = 0
for _ in range(500):
    try:
        db.record_user_swipe(999_999_999, "read")
    except Exception:
        errors += 1

after = backends(DBNAME)
check("500 invalid swipes all raised", errors == 500, f"({errors}/500)")
check("backends did NOT grow", after <= baseline, f"(baseline {baseline} -> {after})")
check("service still responds after the burst", db.get_latest_articles(limit=1) is not None)
check("no 'idle in transaction' connections left", idle_in_txn(DBNAME) == 0)

# --- reads must not strand a transaction -------------------------------------
for _ in range(50):
    db.get_balanced_feed(limit=10)
check("after 50 reads: backends stable", backends(DBNAME) <= baseline, f"({backends(DBNAME)})")
check("after 50 reads: none idle in transaction", idle_in_txn(DBNAME) == 0)

# --- rollback correctness: a failed write must not persist --------------------
before_ct = len(db.get_latest_articles(limit=500))
try:
    with db.db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO articles (article_key,title,category,url) "
                    "VALUES ('rollback-probe','T','TECH','u')")
        raise RuntimeError("simulated crash mid-transaction")
except RuntimeError:
    pass
check("failed transaction rolled back (row not persisted)",
      len(db.get_latest_articles(limit=500)) == before_ct)

# --- concurrency -------------------------------------------------------------
results = []


def worker():
    try:
        db.get_balanced_feed(limit=5)
        results.append("ok")
    except Exception as e:
        results.append(type(e).__name__)


threads = [threading.Thread(target=worker) for _ in range(60)]
[t.start() for t in threads]
[t.join() for t in threads]
ok = results.count("ok")
check("60 concurrent queries all succeeded", ok == 60, f"({ok}/60)")
check("concurrency: backends stayed under max_connections", backends(DBNAME) < 100,
      f"({backends(DBNAME)})")
check("concurrency: none idle in transaction", idle_in_txn(DBNAME) == 0)

# NOTE: with sub-millisecond queries these threads rarely overlap, so this
# exercises correctness rather than genuine pool contention. The maxconn ceiling
# and the retry-on-PoolError path are covered in test_pool.py instead.

db.close_pool()
check("close_pool released backends", backends(DBNAME) <= baseline, f"({backends(DBNAME)})")

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILURE(S): {fails}")
sys.exit(1 if fails else 0)
