"""Verifies the swipe-logging rework.

Claims under test:
  1. A swipe writes ONE row (user_swipes), not two.
  2. The hourly chart and endpoint panel still see swipes, derived from user_swipes.
  3. The live console records real status codes, not a hardcoded 200 OK.
  4. Rejected requests do not write to request_logs.
  5. The swipe limit clears realistic human swipe speed.

Run:  python tests/test_swipe_logging.py
"""
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


AID = db.save_article({"title": "log rework fixture", "url": "https://example.com/lr",
                       "description": "d", "source": "s", "published_at": "n",
                       "category": "TECH"})
c = TestClient(m.app, raise_server_exceptions=False)

print("--- 1. One swipe writes one row, not two ---")
rl0, us0 = counts()
for _ in range(20):
    c.post("/api/v1/swipe", json={"article_id": AID, "action": "pass"})
rl1, us1 = counts()
check("20 swipes -> 20 user_swipes rows", us1 - us0 == 20, f"(+{us1-us0})")
check("20 swipes -> 0 request_logs rows", rl1 - rl0 == 0, f"(+{rl1-rl0})")

print("\n--- 2. Non-swipe endpoints are still logged ---")
rl2, _ = counts()
c.get("/api/v1/feed")
c.get("/api/v1/categories")
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

print("\n--- 4. Real status codes; rejected requests not persisted ---")
m.RECENT_LOGS.clear()
rl4, _ = counts()
c.get("/api/v1/telemetry/stats")                                   # 401
c.post("/api/v1/swipe", json={"article_id": AID, "action": "x"})    # 422
c.get("/api/cards/generate")                                        # 404
rl5, _ = counts()
joined = " | ".join(list(m.RECENT_LOGS))
check("rejected requests wrote 0 request_logs rows", rl5 - rl4 == 0, f"(+{rl5-rl4})")
check("401 recorded with its real status", "401" in joined, joined[:70])
check("422 recorded with its real status", "422" in joined)
check("404 recorded with its real status", "404" in joined)
check("no fabricated '200 OK' on failures", "200 OK" not in joined)

m.RECENT_LOGS.clear()
c.get("/api/v1/feed")
check("success logged with real 200", any("200" in l for l in m.RECENT_LOGS),
      " | ".join(m.RECENT_LOGS)[:70])

print("\n--- 5. Limit clears realistic swipe speed ---")
codes = [c.post("/api/v1/swipe", json={"article_id": AID, "action": "read"}).status_code
         for _ in range(60)]
check("60 swipes in a session all accepted (was 10 under the old limit)",
      429 not in codes, f"({codes.count(201)} accepted, {codes.count(429)} rejected)")

db.close_pool()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILURE(S): {fails}")
sys.exit(1 if fails else 0)
