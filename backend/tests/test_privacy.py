"""Privacy: no IP addresses or User-Agents at rest, and a privacy page that
states the server's real retention windows (#67, ADR-017).

Claims under test:
  1. user_sessions has no ip_address or user_agent column, and init_db drops
     them, with their data, from a table created under the old schema.
  2. A heartbeat stores only the random session id and timestamps. An older
     client that still sends user_agent is accepted and the value is ignored.
  3. The rate limiter keeps the client IP out of the logs: a rejected request
     logs no address, and slowapi's logger is raised to ERROR. uvicorn's access
     log (which starts each line with the IP) is off in the Dockerfile and in
     `python main.py`.
  4. /privacy answers 200 and states the configured retention windows, with no
     placeholder left.
  5. No public page loads anything from Google Fonts. The self-hosted font CSS
     and every file it names are served.

Needs Postgres for 1-2. Bypasses lifespan(), so no real news fetch happens.

Run:  python tests/test_privacy.py
"""
import inspect
import logging
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ADMIN_API_KEY"] = "test-key"

import importlib
import database as db
import main as m

importlib.reload(m)
from fastapi.testclient import TestClient

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + str(detail) if detail else ''}")
    if not cond:
        fails.append(name)


def session_columns():
    with db.db_cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'user_sessions'")
        return {r["column_name"] for r in cur.fetchall()}


db.init_pool()
db.init_db()
c = TestClient(m.app, raise_server_exceptions=False)

print("--- 1. Schema and migration ---")
cols = session_columns()
check("user_sessions has no ip_address or user_agent",
      not cols & {"ip_address", "user_agent"}, sorted(cols))

old_id = f"privacy-old-{uuid.uuid4().hex}"
with db.db_cursor(commit=True) as cur:
    # The table as the old schema left it, with one stored IP.
    cur.execute("ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS user_agent TEXT")
    cur.execute("ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS ip_address TEXT")
    cur.execute("INSERT INTO user_sessions (session_id, user_agent, ip_address) "
                "VALUES (%s, 'Mozilla/5.0', '203.0.113.7')", (old_id,))
try:
    db.init_db()
    cols = session_columns()
    check("init_db drops the old columns", not cols & {"ip_address", "user_agent"}, sorted(cols))
    with db.db_cursor() as cur:
        cur.execute("SELECT * FROM user_sessions WHERE session_id = %s", (old_id,))
        row = cur.fetchone()
    check("the session itself survives the migration", row is not None)
    check("no stored value still holds the IP",
          row is not None and "203.0.113.7" not in map(str, row.values()))
finally:
    with db.db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM user_sessions WHERE session_id = %s", (old_id,))


print("\n--- 2. Heartbeats ---")
sid = f"privacy-{uuid.uuid4().hex}"
r = c.post("/api/v1/telemetry/heartbeat", headers={"User-Agent": "Secret/1.0"},
           json={"session_id": sid, "user_agent": "Secret/1.0 (old client)"})
check("an old client that sends user_agent gets 200", r.status_code == 200, r.status_code)
with db.db_cursor() as cur:
    cur.execute("SELECT * FROM user_sessions WHERE session_id = %s", (sid,))
    row = cur.fetchone()
check("the session is stored", row is not None)
check("the row holds only the id and timestamps",
      row is not None and set(row) == {"session_id", "created_at", "last_heartbeat", "duration_seconds"},
      sorted(row) if row else None)
check("the User-Agent is stored nowhere in the row",
      row is not None and not any("Secret" in str(v) for v in row.values()))
check("record_session_heartbeat takes no IP or User-Agent",
      list(inspect.signature(db.record_session_heartbeat).parameters) == ["session_id"])
with db.db_cursor(commit=True) as cur:
    cur.execute("DELETE FROM user_sessions WHERE session_id = %s", (sid,))


print("\n--- 3. Logs carry no IP ---")
check("slowapi logs at ERROR and above only",
      logging.getLogger("slowapi").getEffectiveLevel() >= logging.ERROR)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


cap, root = Capture(), logging.getLogger()
saved_level = root.level
root.addHandler(cap)
root.setLevel(logging.DEBUG)
try:
    # The heartbeat limit is 30/minute; the TestClient's address is "testclient".
    hb_id = f"privacy-limit-{uuid.uuid4().hex}"
    codes = [c.post("/api/v1/telemetry/heartbeat", json={"session_id": hb_id}).status_code
             for _ in range(32)]
finally:
    root.removeHandler(cap)
    root.setLevel(saved_level)
    with db.db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM user_sessions WHERE session_id = %s", (hb_id,))
check("the limit was reached", 429 in codes, codes[-3:])
leaked = [ln for ln in cap.lines if "testclient" in ln]
check("a rejected request logs no client address", not leaked, leaked[:2])

with open(os.path.join(BACKEND, "Dockerfile"), encoding="utf-8") as f:
    cmd = next(ln for ln in f if ln.startswith("CMD"))
check("the container runs uvicorn without its access log", "--no-access-log" in cmd, cmd.strip())
with open(os.path.join(BACKEND, "main.py"), encoding="utf-8") as f:
    check("`python main.py` runs uvicorn without its access log", "access_log=False" in f.read())


print("\n--- 4. The privacy page ---")
r = c.get("/privacy")
check("/privacy answers 200", r.status_code == 200, r.status_code)
page = r.text
check("no placeholder is left", "{{" not in page, re.findall(r"\{\{\w+\}\}", page))
for label, days in [("articles", m.PURGE_OLDER_THAN_DAYS), ("sessions", m.SESSION_RETENTION_DAYS),
                    ("request statistics", m.REQUEST_LOG_RETENTION_DAYS)]:
    check(f"states the {label} window ({days} days)", f"{days} days" in page)
saved_days = m.SESSION_RETENTION_DAYS
m.SESSION_RETENTION_DAYS = 31
try:
    check("the windows come from the running config, not the file",
          "31 days after the last ping" in c.get("/privacy").text)
finally:
    m.SESSION_RETENTION_DAYS = saved_days
check("the web preview links to it", 'href="/privacy"' in c.get("/mobile").text)


print("\n--- 5. Fonts are self-hosted ---")
for path in ["/mobile", "/", "/privacy"]:
    html = c.get(path).text
    check(f"{path} loads nothing from Google Fonts",
          "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html)
r = c.get("/static/fonts/fonts.css")
check("fonts.css is served", r.status_code == 200, r.status_code)
urls = re.findall(r"url\((/static/fonts/[^)]+)\)", r.text)
check("fonts.css names font files", len(urls) >= 3, len(urls))
bad = [u for u in urls if c.get(u).status_code != 200]
check("every font file it names is served", not bad, bad)


print("\n" + "=" * 62)
if fails:
    print(f"FAILED ({len(fails)}): " + ", ".join(fails))
    sys.exit(1)
print("All privacy checks passed.")
