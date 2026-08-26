"""Connection-pool lifecycle guarantees, using a fake pool (no Postgres needed).

Guards PS-07. The regression this exists to catch: an exception raised inside a
db_cursor() body must still return the connection to the pool. Before the fix,
every query opened a raw connection and closed it on the happy path only.

Run:  python tests/test_pool.py
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from psycopg2.pool import PoolError


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.closed = True

    def execute(self, *a, **k):
        if self.conn.fail_on_execute:
            raise RuntimeError("simulated ForeignKeyViolation")


class FakeConn:
    def __init__(self):
        self.committed = 0
        self.rolled_back = 0
        self.fail_on_execute = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


class FakePool:
    def __init__(self, size):
        self.free = [FakeConn() for _ in range(size)]
        self.out = []
        self.closed = False

    def getconn(self):
        if not self.free:
            raise PoolError("connection pool exhausted")
        c = self.free.pop()
        self.out.append(c)
        return c

    def putconn(self, c):
        self.out.remove(c)
        self.free.append(c)


def fresh(size=2):
    p = FakePool(size)
    db._pool = p
    return p


fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(name)


# 1. happy path with commit
p = fresh()
with db.db_cursor(commit=True) as cur:
    cur.execute("INSERT ...")
check("commit path: connection returned", len(p.out) == 0 and len(p.free) == 2)
check("commit path: committed once", p.free[-1].committed == 1)

# 2. read path must roll back, not leave the connection 'idle in transaction'
p = fresh()
with db.db_cursor() as cur:
    cur.execute("SELECT ...")
c = p.free[-1]
check("read path: rolled back, not committed", c.rolled_back == 1 and c.committed == 0)

# 3. THE REGRESSION: an exception inside the body must still return the connection
p = fresh()
p.free[-1].fail_on_execute = True
try:
    with db.db_cursor(commit=True) as cur:
        cur.execute("INSERT bad fk")
except RuntimeError:
    pass
check("error path: connection returned to pool", len(p.out) == 0 and len(p.free) == 2)
check("error path: rolled back", any(c.rolled_back >= 1 for c in p.free))
check("error path: NOT committed", all(c.committed == 0 for c in p.free))

# 4. 500 consecutive failures must not drain the pool (the curl-loop scenario)
p = fresh(size=5)
for c in p.free:
    c.fail_on_execute = True
for _ in range(500):
    try:
        with db.db_cursor(commit=True) as cur:
            cur.execute("INSERT bad fk")
    except RuntimeError:
        pass
check("500 failed swipes: pool intact (5 free, 0 leaked)",
      len(p.free) == 5 and len(p.out) == 0)

# 5. the exception must propagate, never be swallowed
p = fresh()
p.free[-1].fail_on_execute = True
raised = False
try:
    with db.db_cursor(commit=True) as cur:
        cur.execute("boom")
except RuntimeError:
    raised = True
check("error path: exception still propagates", raised)

# 6. strict init: no pool -> a clear RuntimeError, never a silent connect
db._pool = None
try:
    with db.db_cursor() as cur:
        pass
    check("strict init: raises without init_pool()", False)
except RuntimeError as e:
    check("strict init: raises without init_pool()", "init_pool" in str(e))

# 7. exhaustion is bounded, then raises (must not hang forever)
import time
db.POOL_ACQUIRE_TIMEOUT = 0.3
p = fresh(size=1)
held = p.getconn()          # drain it
t0 = time.monotonic()
try:
    with db.db_cursor() as cur:
        pass
    check("exhaustion: raises PoolError after timeout", False)
except PoolError:
    dt = time.monotonic() - t0
    check(f"exhaustion: bounded wait then PoolError ({dt:.2f}s)", 0.25 < dt < 1.5)

# 8. init_pool is idempotent and thread-safe
db._pool = None
calls = []


class CountingPool(FakePool):
    def __init__(self, lo, hi, dsn, **kw):
        super().__init__(hi)
        calls.append(1)


_real_pool_cls = db.ThreadedConnectionPool
db.ThreadedConnectionPool = CountingPool
threads = [threading.Thread(target=db.init_pool) for _ in range(20)]
[t.start() for t in threads]
[t.join() for t in threads]
db.ThreadedConnectionPool = _real_pool_cls
check("init_pool: created exactly once under 20 threads", len(calls) == 1)

db._pool = None
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILURE(S): {fails}")
sys.exit(1 if fails else 0)
