"""Verifies the swipe-logging and request-log buffering rework.

Claims under test:
  1. A swipe writes ONE row (user_swipes), not two.
  2. The hourly chart and endpoint panel still see swipes, derived from user_swipes.
  3. The live console records real status codes, not a hardcoded 200 OK.
  4. Rejected requests are persisted with their REAL status -- not skipped, not
     faked as 200.
  5. The swipe limit clears realistic human swipe speed.
  6. The middleware performs NO database I/O; flush_request_logs() does it in
     batches off the request path.
  7. request_logs and user_sessions are bounded by retention purges, on the same
     window as the article purge that caps user_swipes.
  8. The average-session-length metric is windowed rather than lifetime, and its
     window can never exceed the retention window it reads from.

Run:  python tests/test_swipe_logging.py
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ADMIN_API_KEY"] = "test-key"

import importlib
import database as db
import main as m

importlib.reload(m)
from fastapi.testclient import TestClient

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(name)


db.init_pool()
db.init_db()


def counts():
    with db.db_cursor() as cur:
        cur.execute("SELECT (SELECT COUNT(*) FROM request_logs) rl,"
                    "       (SELECT COUNT(*) FROM user_swipes) us")
        r = cur.fetchone()
        return r["rl"], r["us"]


AID, _ = db.save_article({"title": "log rework fixture", "url": "https://example.com/lr",
                          "description": "d", "source": "s", "published_at": "n",
                          "category": "TECH"})
c = TestClient(m.app, raise_server_exceptions=False)

print("--- 1. One swipe writes one row, not two ---")
rl0, us0 = counts()
for _ in range(20):
    c.post("/api/v1/swipe", json={"article_id": AID, "action": "pass"})
m.flush_request_logs()      # so the absence below is real, not merely unflushed
rl1, us1 = counts()
check("20 swipes -> 20 user_swipes rows", us1 - us0 == 20, f"(+{us1-us0})")
check("20 swipes -> 0 request_logs rows", rl1 - rl0 == 0, f"(+{rl1-rl0})")

print("\n--- 2. Non-swipe endpoints are still logged (once flushed) ---")
m.flush_request_logs()
rl2, _ = counts()
c.get("/api/v1/feed")
c.get("/api/v1/categories")
m.flush_request_logs()
rl3, _ = counts()
check("feed + categories still logged", rl3 - rl2 == 2, f"(+{rl3-rl2})")

print("\n--- 3. Charts still see swipes despite not logging them ---")
with db.db_cursor() as cur:
    hourly = db._hourly_usage_distribution(cur)
    tops = db._top_api_endpoints(cur, 6)
    analytics = db._database_detailed_analytics(cur)

total_hourly = sum(h["count"] for h in hourly)
check("hourly chart total covers swipes", total_hourly >= us1,
      f"(chart {total_hourly} >= {us1} swipes)")
check("hourly buckets are all 0-23 and complete", len(hourly) == 24)

swipe_row = next((t for t in tops if t["endpoint"] == "/api/v1/swipe"), None)
check("/api/v1/swipe appears in top endpoints", swipe_row is not None)
if swipe_row:
    # Exact, not approximate. If this drifts, historical /api/v1/swipe rows are
    # being double-counted alongside user_swipes -- see the de-dup migration in
    # database.init_db().
    check("swipe hit_count matches user_swipes exactly",
          swipe_row["hit_count"] == us1, f"({swipe_row['hit_count']} vs {us1})")
check("percentages never exceed 100",
      all(0 <= t["percentage"] <= 100 for t in tops),
      f"{[t['percentage'] for t in tops]}")
check("total_requests includes swipes",
      analytics["total_requests"] >= us1, f"({analytics['total_requests']})")

print("\n--- 4. Real status codes, persisted, written off the request path ---")
m.RECENT_LOGS.clear()
m.flush_request_logs()
rl4, _ = counts()

c.get("/api/v1/telemetry/stats")                                   # 401, excluded path
c.post("/api/v1/swipe", json={"article_id": AID, "action": "x"})    # 422, excluded path
c.get("/api/cards/generate")                                        # 404, logged

# The acceptance criterion: the middleware itself INSERTs nothing.
rl_mid, _ = counts()
check("middleware wrote 0 rows itself (buffered, not INSERTed)",
      rl_mid - rl4 == 0, f"(+{rl_mid-rl4})")

flushed = m.flush_request_logs()
rl5, _ = counts()
check("flush persisted exactly the one non-excluded request",
      rl5 - rl4 == 1, f"(+{rl5-rl4}, flush returned {flushed})")

with db.db_cursor() as cur:
    cur.execute("SELECT status_code FROM request_logs "
                "WHERE endpoint = '/api/cards/generate' ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
check("404 stored with its REAL status_code, not 200",
      row is not None and row["status_code"] == 404,
      f"({row['status_code'] if row else 'no row'})")

joined = " | ".join(list(m.RECENT_LOGS))
check("401 recorded with its real status", "401" in joined, joined[:70])
check("422 recorded with its real status", "422" in joined)
check("404 recorded with its real status", "404" in joined)
check("no fabricated '200 OK' on failures", "200 OK" not in joined)

m.RECENT_LOGS.clear()
c.get("/api/v1/feed")
check("success logged with real 200", any("200" in l for l in m.RECENT_LOGS),
      " | ".join(m.RECENT_LOGS)[:70])
m.flush_request_logs()

print("\n--- 5. Limit clears realistic swipe speed ---")
codes = [c.post("/api/v1/swipe", json={"article_id": AID, "action": "read"}).status_code
         for _ in range(60)]
check("60 swipes in a session all accepted (was 10 under the old limit)",
      429 not in codes, f"({codes.count(201)} accepted, {codes.count(429)} rejected)")

print("\n--- 6. Buffer / flush contract ---")
m.flush_request_logs()
check("flushing an empty buffer writes nothing", m.flush_request_logs() == 0)

rl6, _ = counts()
c.get("/api/v1/feed")
c.get("/api/v1/categories")
check("events wait in memory", len(m._LOG_BUFFER) == 2, f"({len(m._LOG_BUFFER)})")
check("and nothing has reached Postgres yet", counts()[0] - rl6 == 0)

n = m.flush_request_logs()
check("flush drains the buffer completely", len(m._LOG_BUFFER) == 0)
check("flush reports the row count it wrote", n == 2, f"({n})")
check("rows landed in one batch", counts()[0] - rl6 == 2)

# Static guards. The runtime checks above pass just as well against a slow
# per-request INSERT; these are what stop it coming back.
MAIN_SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "main.py"), encoding="utf-8").read()


def module_calls(src):
    """Every `name.attr(...)` call in the source, via AST.

    Deliberately not a substring grep: main.py carries a comment explaining what
    db.log_request_event() used to do and why it went, and a grep for the name
    matches that comment -- failing the check precisely because the fix is
    documented. Only real Call nodes count.
    """
    return {
        f"{n.func.value.id}.{n.func.attr}"
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
    }


check("static: no per-request DB write left in main.py",
      "db.log_request_event" not in module_calls(MAIN_SRC))
check("static: flush job registered on the scheduler",
      'id="flush_request_logs"' in MAIN_SRC)
check("static: no `except Exception: pass` swallowing DB failures",
      "except Exception:\n            pass" not in MAIN_SRC)

print("\n--- 7. Retention ---")
# Every counter rendered in the same panel must cover the same period.
# user_swipes is capped by ON DELETE CASCADE at the article window; if
# request_logs or user_sessions outlive it, the panel mixes time windows.
for name, actual, expected, env in [
    ("request log", m.REQUEST_LOG_RETENTION_DAYS, m.PURGE_OLDER_THAN_DAYS, "REQUEST_LOG_RETENTION_DAYS"),
    ("session", m.SESSION_RETENTION_DAYS, m.PURGE_OLDER_THAN_DAYS, "SESSION_RETENTION_DAYS"),
]:
    check(f"{name} retention defaults to the article purge window",
          os.getenv(env) is not None or actual == expected,
          f"({actual} vs {expected})")

refresh_src = MAIN_SRC.split("def refresh_pipeline")[1][:900]
check("the refresh job runs the combined purge", "purge_old_data" in refresh_src)

DB_SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "database.py"), encoding="utf-8").read()
purge_src = DB_SRC.split("def purge_old_data")[1][:900]
check("purge_old_data covers all three tables",
      all(p in purge_src for p in
          ("purge_old_articles", "purge_old_request_logs", "purge_old_sessions")))

with db.db_cursor(commit=True) as cur:
    cur.execute("INSERT INTO request_logs "
                "(endpoint, method, status_code, hour_of_day, logged_at) "
                "VALUES ('/retention-probe', 'GET', 200, 3, "
                "        NOW() - INTERVAL '400 days')")

rl7, _ = counts()
removed = db.purge_old_request_logs(days=m.REQUEST_LOG_RETENTION_DAYS)
check("purge removed the stale probe row", removed >= 1, f"({removed})")
check("row count fell by exactly what purge reported",
      rl7 - counts()[0] == removed, f"({rl7 - counts()[0]} vs {removed})")

with db.db_cursor() as cur:
    cur.execute("SELECT COUNT(*) n FROM request_logs WHERE endpoint = '/retention-probe'")
    check("stale row is gone", cur.fetchone()["n"] == 0)
    cur.execute("SELECT COUNT(*) n FROM request_logs WHERE endpoint = '/api/v1/feed'")
    check("rows inside the window survive", cur.fetchone()["n"] > 0)

print("\n--- 7b. Session retention purges on last_heartbeat, not created_at ---")
# The distinction that matters: a session created long ago but STILL beating is
# live and must survive. Purging on created_at would delete it, and the next
# heartbeat would re-insert it with duration_seconds restarted at zero.
with db.db_cursor(commit=True) as cur:
    cur.execute(
        "INSERT INTO user_sessions "
        "(session_id, created_at, last_heartbeat, duration_seconds) VALUES "
        "('probe-idle',  NOW() - INTERVAL '400 days', NOW() - INTERVAL '400 days', 60),"
        "('probe-live',  NOW() - INTERVAL '400 days', NOW(),                       60),"
        "('probe-fresh', NOW(),                       NOW(),                       10) "
        "ON CONFLICT (session_id) DO UPDATE SET "
        "  created_at = EXCLUDED.created_at, last_heartbeat = EXCLUDED.last_heartbeat"
    )

removed_s = db.purge_old_sessions(days=m.SESSION_RETENTION_DAYS)
check("purge removed the idle session", removed_s >= 1, f"({removed_s})")

with db.db_cursor() as cur:
    cur.execute("SELECT session_id FROM user_sessions "
                "WHERE session_id LIKE 'probe-%' ORDER BY session_id")
    survivors = [r["session_id"] for r in cur.fetchall()]
check("long-lived but STILL BEATING session survives", "probe-live" in survivors, f"{survivors}")
check("fresh session survives", "probe-fresh" in survivors, f"{survivors}")
check("session idle past the window is gone", "probe-idle" not in survivors, f"{survivors}")

with db.db_cursor(commit=True) as cur:
    cur.execute("DELETE FROM user_sessions WHERE session_id LIKE 'probe-%'")

print("\n--- 7c. Session duration is windowed, not a lifetime average ---")
# Runs inside a db_cursor() WITHOUT commit, so the DELETE and INSERTs below are
# rolled back on exit. That makes the arithmetic deterministic without touching
# whatever real sessions the database already holds.
with db.db_cursor() as cur:
    cur.execute("DELETE FROM user_sessions")
    cur.execute(
        "INSERT INTO user_sessions "
        "(session_id, created_at, last_heartbeat, duration_seconds) VALUES "
        "('m-fresh', NOW(),                     NOW(),                      60),"
        "('m-old',   NOW() - INTERVAL '5 days', NOW() - INTERVAL '5 days',  600)"
    )
    w1 = db._avg_session_duration_minutes(cur, 1)
    w7 = db._avg_session_duration_minutes(cur, 7)

check("1-day window sees only the fresh session", w1 == 1.0, f"({w1} min)")
check("7-day window averages both", w7 == 5.5, f"({w7} min)")
check("the window changes the result -- a lifetime average could not", w1 != w7)

# The metric window must not outrun the data. Sessions are purged on
# last_heartbeat, so a wider window would average the surviving rows and label
# the answer with a period those rows do not cover.
check("metric window defaults to the session retention window",
      os.getenv("SESSION_METRIC_WINDOW_DAYS") is not None
      or m.SESSION_METRIC_WINDOW_DAYS == m.SESSION_RETENTION_DAYS,
      f"({m.SESSION_METRIC_WINDOW_DAYS} vs {m.SESSION_RETENTION_DAYS})")
check("metric window never exceeds retention",
      m.SESSION_METRIC_WINDOW_DAYS <= m.SESSION_RETENTION_DAYS,
      f"({m.SESSION_METRIC_WINDOW_DAYS} vs {m.SESSION_RETENTION_DAYS})")

stats = db.get_telemetry_summary(session_window_days=m.SESSION_METRIC_WINDOW_DAYS)
check("payload reports the window beside the figure",
      stats["user_analytics"]["avg_session_window_days"] == m.SESSION_METRIC_WINDOW_DAYS,
      f"({stats['user_analytics'].get('avg_session_window_days')})")

removed_all = db.purge_old_data(
    articles_days=m.PURGE_OLDER_THAN_DAYS,
    request_logs_days=m.REQUEST_LOG_RETENTION_DAYS,
    sessions_days=m.SESSION_RETENTION_DAYS,
)
check("purge_old_data returns per-table counts",
      set(removed_all) == {"articles", "request_logs", "user_sessions"}, f"{removed_all}")
check("the counts are integers", all(isinstance(v, int) for v in removed_all.values()))

m.flush_request_logs()
db.close_pool()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILURE(S): {fails}")
sys.exit(1 if fails else 0)
