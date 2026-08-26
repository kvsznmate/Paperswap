"""Admin authentication: the /analytics gate, session cookies, and CSRF posture.

The dashboard cannot use header auth, because a browser cannot attach a header
to a top-level navigation. It is gated with a session cookie instead. These
tests pin down that the gate actually gates, that the cookie is safe, and that a
cookie alone can never authorise a state change.

Run:  python tests/test_admin_auth.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ADMIN_API_KEY"] = "correct-horse-battery"

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
KEY = {"X-API-Key": "correct-horse-battery"}
c = TestClient(m.app, raise_server_exceptions=False)


print("--- /analytics is gated ---")
r = c.get("/analytics")
check("anonymous /analytics -> 401", r.status_code == 401, f"({r.status_code})")
check("401 serves the sign-in form", "Analytics access" in r.text)
# The shell is not "empty": it renders the DB schema. Anonymous users see none of it.
check("dashboard shell NOT leaked to anonymous",
      "Relational Database Inspector" not in r.text and "public.user_swipes" not in r.text)


print("\n--- sign-in flow ---")
r = c.post("/api/v1/auth/session", json={"key": "wrong"})
check("wrong key -> 401", r.status_code == 401, f"({r.status_code})")
check("no cookie set on failure", m.SESSION_COOKIE not in c.cookies)
check("dashboard still gated after failed sign-in", c.get("/analytics").status_code == 401)

r = c.post("/api/v1/auth/session", json={"key": "correct-horse-battery"})
check("correct key -> 200", r.status_code == 200, f"({r.status_code})")
sc = r.headers.get("set-cookie", "")
check("cookie is HttpOnly (XSS cannot read it)", "httponly" in sc.lower(), sc[:70])
check("cookie is SameSite=strict", "samesite=strict" in sc.lower())
check("cookie value is NOT the api key", "correct-horse-battery" not in sc)
check("after sign-in /analytics -> 200", c.get("/analytics").status_code == 200)


print("\n--- telemetry accepts cookie OR header ---")
check("telemetry via session cookie -> 200",
      c.get("/api/v1/telemetry/stats").status_code == 200)
anon = TestClient(m.app, raise_server_exceptions=False)
check("telemetry anonymous -> 401", anon.get("/api/v1/telemetry/stats").status_code == 401)
check("telemetry via header, no cookie -> 200",
      anon.get("/api/v1/telemetry/stats", headers=KEY).status_code == 200)


print("\n--- refresh is header-only (CSRF posture) ---")
# Cookies ride along on cross-site requests by default; SameSite=strict blocks
# that, but requiring an explicit header means even a SameSite regression cannot
# turn the admin's logged-in browser into a refresh trigger.
check("refresh with session cookie but NO header -> 401",
      c.post("/api/v1/cards/refresh").status_code == 401)
check("refresh with header -> 202",
      c.post("/api/v1/cards/refresh", headers=KEY).status_code == 202)


print("\n--- logout ---")
check("logout -> 200", c.post("/api/v1/auth/logout").status_code == 200)
check("/analytics gated again after logout", c.get("/analytics").status_code == 401)


print("\n--- forged / expired sessions ---")
f = TestClient(m.app, raise_server_exceptions=False)
f.cookies.set(m.SESSION_COOKIE, "made-up-token")
check("forged cookie rejected on /analytics", f.get("/analytics").status_code == 401)
check("forged cookie rejected on telemetry",
      f.get("/api/v1/telemetry/stats").status_code == 401)

tok = m._new_session()
with m._sessions_lock:
    m._sessions[tok] = 0          # expire it
e = TestClient(m.app, raise_server_exceptions=False)
e.cookies.set(m.SESSION_COOKIE, tok)
check("expired session rejected", e.get("/analytics").status_code == 401)
check("expired token pruned from the store", tok not in m._sessions)


print("\n--- unset key fails closed ---")
_saved = m.ADMIN_API_KEY
m.ADMIN_API_KEY = ""
u = TestClient(m.app, raise_server_exceptions=False)
check("telemetry -> 503 when key unset", u.get("/api/v1/telemetry/stats").status_code == 503)
check("sign-in -> 503 when key unset",
      u.post("/api/v1/auth/session", json={"key": "x"}).status_code == 503)
check("/analytics shows the gate, never the dashboard",
      u.get("/analytics").status_code == 401)
m.ADMIN_API_KEY = _saved

db.close_pool()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILURE(S): {fails}")
sys.exit(1 if fails else 0)
