"""Load shedding (#58): refuse work with a 503 instead of running out of memory.

Claims under test:
  1. Past GLOBAL_REQUESTS_PER_MINUTE, every request (any route, any client,
     matched or not) gets 503 with a Retry-After inside the window.
  2. A shed request never reaches the app: nothing enters the request-log
     buffer for it.
  3. Admin requests (valid X-API-Key or session cookie) bypass the ceiling and
     do not spend it; a wrong key is shed like anyone else.
  4. Rejections are counted and reported as a measured reading in
     /api/v1/telemetry/stats under global_limits.requests (ADR-010).
  5. API_DOCS_ENABLED=false removes /docs, /redoc and /openapi.json.
  6. Every UVICORN_* variable set in the Dockerfile and docker-compose.yml is
     an option the uvicorn CLI actually reads. A misspelt one is ignored
     without a word, and the server then runs with no concurrency limit.

uvicorn's --limit-concurrency itself is uvicorn's code, not ours, so it is not
re-tested here; claim 6 checks that it is configured under a name uvicorn reads.

Runs against a live Postgres with the pool open, bypassing lifespan() so no
real news fetch happens.

Run:  python tests/test_load_shedding.py
"""
import os
import re
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
os.environ["ADMIN_API_KEY"] = "test-key"
os.environ["GLOBAL_REQUESTS_PER_MINUTE"] = "40"
os.environ.pop("API_DOCS_ENABLED", None)

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
c = TestClient(m.app, raise_server_exceptions=False)
ADMIN = {"X-API-Key": "test-key"}


print("--- 1. The ceiling sheds load past its limit ---")
check("ceiling configured from the environment", m.REQUEST_BUDGET.limit == 40,
      f"({m.REQUEST_BUDGET.limit})")
# Mixed routes, including an unmatched one: the ceiling counts everything.
paths = ["/nope-not-a-route", "/mobile", "/api/v1/categories", "/openapi.json"]
codes = [c.get(paths[i % len(paths)]).status_code for i in range(40)]
check("requests up to the limit are served", 503 not in codes, f"({sorted(set(codes))})")

r = c.get("/api/v1/categories")
check("request past the limit -> 503", r.status_code == 503, f"({r.status_code})")
retry = r.headers.get("Retry-After", "")
check("503 carries Retry-After within the minute",
      retry.isdigit() and 1 <= int(retry) <= 60, f"({retry!r})")
check("503 body is JSON with a detail", "detail" in r.json(), f"({r.text[:80]})")
check("unmatched paths are shed too", c.get("/wp-login.php").status_code == 503)
check("POSTs are shed too",
      c.post("/api/v1/swipe", json={"article_id": 1, "action": "read"}).status_code == 503)


print("\n--- 2. A shed request never reaches the app ---")
before = len(m._LOG_BUFFER)
unmatched_before = m._UNMATCHED_REQUESTS
c.get("/mobile")
c.get("/another-junk-path")
check("nothing buffered for shed requests", len(m._LOG_BUFFER) == before,
      f"({before} -> {len(m._LOG_BUFFER)})")
check("shed junk paths not counted as unmatched", m._UNMATCHED_REQUESTS == unmatched_before)


print("\n--- 3. Admin traffic bypasses the ceiling ---")
used = m.REQUEST_BUDGET.snapshot()["used_this_window"]
r = c.get("/api/v1/telemetry/logs", headers=ADMIN)
check("valid X-API-Key served while the ceiling is spent", r.status_code == 200, f"({r.status_code})")
token = m._new_session()
r = c.get("/api/v1/telemetry/logs", cookies={m.SESSION_COOKIE: token})
check("valid session cookie served while the ceiling is spent", r.status_code == 200,
      f"({r.status_code})")
check("admin requests do not spend the budget",
      m.REQUEST_BUDGET.snapshot()["used_this_window"] == used)
r = c.get("/api/v1/telemetry/logs", headers={"X-API-Key": "wrong"})
check("a wrong key is shed, not authenticated", r.status_code == 503, f"({r.status_code})")
r = c.get("/api/v1/telemetry/logs", cookies={m.SESSION_COOKIE: "forged"})
check("a forged cookie is shed", r.status_code == 503, f"({r.status_code})")


print("\n--- 4. Rejections are reported, and measured ---")
r = c.get("/api/v1/telemetry/stats", headers=ADMIN)
check("stats reachable during the incident", r.status_code == 200, f"({r.status_code})")
reading = r.json().get("global_limits", {}).get("requests", {}) if r.status_code == 200 else {}
check("global_limits.requests reported", bool(reading), f"({reading})")
check("rejections counted", reading.get("rejected_since_start", 0) >= 7,
      f"({reading.get('rejected_since_start')})")
check("window is one minute", reading.get("window_seconds") == 60.0)
check("reading declares measured: true", reading.get("measured") is True)


print("\n--- 5. API docs can be switched off ---")
m.REQUEST_BUDGET._count = 0          # fresh window, so these are not shed
check("docs on by default", c.get("/openapi.json").status_code == 200
      and c.get("/docs").status_code == 200)
os.environ["API_DOCS_ENABLED"] = "false"
os.environ["GLOBAL_REQUESTS_PER_MINUTE"] = "1000"
try:
    importlib.reload(m)
    c_off = TestClient(m.app, raise_server_exceptions=False)
    for path in ("/docs", "/redoc", "/openapi.json"):
        code = c_off.get(path).status_code
        check(f"{path} -> 404 when disabled", code == 404, f"({code})")
    check("the API itself still serves", c_off.get("/api/v1/categories").status_code == 200)
finally:
    os.environ.pop("API_DOCS_ENABLED", None)


print("\n--- 6. UVICORN_* settings use names uvicorn reads ---")
# Not `import uvicorn.main as uvm`: the package re-exports the main() command
# under the same name, which shadows the module on attribute lookup.
uvm = importlib.import_module("uvicorn.main")

check("uvicorn CLI reads UVICORN_* variables",
      uvm.main.context_settings.get("auto_envvar_prefix") == "UVICORN")
options = {p.name.upper() for p in uvm.main.params}
env_pattern = re.compile(r"\bUVICORN_([A-Z_]+)\b")
for fname in ("Dockerfile", "docker-compose.yml"):
    with open(os.path.join(BACKEND, fname), encoding="utf-8") as fh:
        # Comments may mention option names loosely; only check assignments.
        text = "\n".join(line.split("#", 1)[0] for line in fh)
    names = set(env_pattern.findall(text))
    check(f"{fname} sets UVICORN_LIMIT_CONCURRENCY", "LIMIT_CONCURRENCY" in names,
          f"({sorted(names)})")
    for name in sorted(names):
        check(f"{fname}: UVICORN_{name} is a uvicorn option", name in options)


db.close_pool()
print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}'}")
sys.exit(1 if fails else 0)
