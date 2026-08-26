"""Endpoint tests: mutating GET, admin auth, and input validation.

Runs against a live Postgres with the pool open, but bypasses lifespan() so the
suite never triggers an outbound news fetch.

Run:  python tests/test_api_security.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ADMIN_API_KEY"] = "test-key-do-not-use-in-prod"

import importlib
import database as db
import main as m

importlib.reload(m)          # pick up ADMIN_API_KEY set above
from fastapi.testclient import TestClient

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(name)


db.init_pool()
db.init_db()
VALID_ID = db.save_article({
    "title": "API security fixture", "url": "https://example.com/apisec",
    "description": "d", "source": "s", "published_at": "now", "category": "TECH",
})

KEY = {"X-API-Key": "test-key-do-not-use-in-prod"}
# raise_server_exceptions=False so a 500 is returned rather than re-raised,
# which is what a real client would see.
c = TestClient(m.app, raise_server_exceptions=False)


print("--- 1. Mutating GET is gone; refresh is an authenticated POST ---")
check("GET /api/v1/cards/refresh -> 405 Method Not Allowed",
      c.get("/api/v1/cards/refresh").status_code == 405,
      f"({c.get('/api/v1/cards/refresh').status_code})")
check("GET /api/cards/generate alias deleted -> 404",
      c.get("/api/cards/generate").status_code == 404)
check("GET /api/news alias deleted -> 404",
      c.get("/api/news").status_code == 404)
check("POST refresh without key -> 401",
      c.post("/api/v1/cards/refresh").status_code == 401)
check("POST refresh with WRONG key -> 401",
      c.post("/api/v1/cards/refresh", headers={"X-API-Key": "wrong"}).status_code == 401)

r = c.post("/api/v1/cards/refresh", headers=KEY)
check("POST refresh with key -> 202 Accepted", r.status_code == 202, f"({r.status_code})")
# FastAPI serialises `return body, 202` as a two-element ARRAY with HTTP 200.
# This asserts we used status_code=202 rather than that Flask idiom.
check("202 body is an object, not a [body, code] array",
      isinstance(r.json(), dict) and r.json().get("status") == "accepted",
      f"{r.json()}")


print("\n--- 2. Telemetry requires the key ---")
for path in ("/api/v1/telemetry/stats", "/api/v1/telemetry/logs"):
    check(f"GET {path} anonymous -> 401", c.get(path).status_code == 401)
    check(f"GET {path} wrong key -> 401",
          c.get(path, headers={"X-API-Key": "nope"}).status_code == 401)
    check(f"GET {path} with key -> 200",
          c.get(path, headers=KEY).status_code == 200)

# A browser cannot attach a header to a top-level navigation, so gating this
# route would make the dashboard unreachable. The shell holds no data.
_an = c.get("/analytics").status_code
check("/analytics shell is NOT key-gated", _an not in (401, 403), f"({_an})")
check("public feed still anonymous", c.get("/api/v1/feed").status_code == 200)
check("public categories still anonymous", c.get("/api/v1/categories").status_code == 200)


print("\n--- 3. Swipe validation ---")
r = c.post("/api/v1/swipe", json={"article_id": VALID_ID, "action": "read"})
check("valid swipe -> 201 Created", r.status_code == 201, f"({r.status_code})")

r = c.post("/api/v1/swipe", json={"article_id": 999999999, "action": "read"})
check("unknown article_id -> 404 (not 500)", r.status_code == 404, f"({r.status_code})")
check("404 names the article", "999999999" in r.text)

for bad, label in [
    ({"article_id": 0, "action": "read"},        "article_id=0"),
    ({"article_id": -5, "action": "read"},       "article_id negative"),
    ({"article_id": VALID_ID, "action": "like"}, "action='like'"),
    ({"article_id": VALID_ID},                   "action missing"),
    ({"action": "read"},                         "article_id missing"),
    ({"article_id": "abc", "action": "read"},    "article_id not an int"),
]:
    code = c.post("/api/v1/swipe", json=bad).status_code
    check(f"{label} -> 422", code == 422, f"({code})")

r = c.post("/api/v1/swipe", json={"article_id": VALID_ID, "action": "like"})
check("422 body documents allowed values", "read" in r.text and "pass" in r.text)


print("\n--- OpenAPI advertises the auth requirement ---")
spec = c.get("/openapi.json").json()
schemes = spec.get("components", {}).get("securitySchemes", {})
check("security scheme registered", "APIKeyHeader" in schemes, f"{list(schemes)}")
check("scheme is an X-API-Key header",
      schemes.get("APIKeyHeader", {}).get("name") == "X-API-Key")
for path, method in [("/api/v1/cards/refresh", "post"),
                     ("/api/v1/telemetry/stats", "get"),
                     ("/api/v1/telemetry/logs", "get")]:
    check(f"{method.upper()} {path} marked secured in /docs",
          bool(spec["paths"][path][method].get("security")))
check("public feed NOT marked secured",
      not spec["paths"]["/api/v1/feed"]["get"].get("security"))


print("\n--- Rate limits are wired ---")
# 130 calls: above the 120/min swipe limit, well above a real session (~60/min).
codes = [c.post("/api/v1/swipe", json={"article_id": VALID_ID, "action": "pass"}).status_code
         for _ in range(130)]
check("swipe limiter trips above 120/min", 429 in codes,
      f"(first 429 at request #{codes.index(429) + 1})" if 429 in codes else "never tripped")
check("a realistic 60-swipe session is never rejected", 429 not in codes[:60])

db.close_pool()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILURE(S): {fails}")
sys.exit(1 if fails else 0)
