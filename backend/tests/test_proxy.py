"""Behind Cloudflare Tunnel: whose address the rate limiter keys on, and what
the deployment files promise (#32, ADR-018).

Claims under test:
  1. client_ip() returns the socket address when CLIENT_IP_HEADER is unset,
     whatever headers arrive (local runs, CI, Tailscale).
  2. With CLIENT_IP_HEADER set, the header is used only when the request comes
     from TRUSTED_PROXIES. From anywhere else it is ignored, so a client on the
     internet cannot pick its own bucket. A missing or malformed header falls
     back to the socket address. With a list, the last entry (the one the
     nearest proxy wrote) wins.
  3. Through the app: behind a trusted proxy, two visitors get separate rate
     limit buckets; an untrusted sender that varies the header still shares one.
  4. /privacy names Cloudflare as a processor exactly when the server runs
     behind it.
  5. Deployment files: cloudflared is pinned, behind the "tunnel" profile, and
     the API port's bind address is configurable. The Android release build
     allows no cleartext and refuses to package a signed APK with an http URL;
     the debug build keeps cleartext for the tailnet and emulator.

Needs Postgres for sections 3-4 (the routes read categories and articles). The
Android checks in section 5 are skipped where android/ is absent, as inside the
backend container.

Run:  python tests/test_proxy.py
"""
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ADMIN_API_KEY"] = "test-key"

import importlib
import main as m

importlib.reload(m)
from fastapi.testclient import TestClient
from starlette.requests import Request

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(BACKEND)
fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + str(detail) if detail else ''}")
    if not cond:
        fails.append(name)


def req(peer, **headers):
    raw = [(k.replace("_", "-").lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 40000) if peer else None})


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


CF = "CF-Connecting-IP"
saved = m.CLIENT_IP_HEADER

print("--- 1. Header unset: the socket address ---")
m.CLIENT_IP_HEADER = ""
check("a public peer is itself", m.client_ip(req("198.51.100.4")) == "198.51.100.4")
check("a header from a trusted peer is ignored",
      m.client_ip(req("172.18.0.5", cf_connecting_ip="203.0.113.9")) == "172.18.0.5")
check("no client at all", m.client_ip(req(None)) == "unknown")

print("\n--- 2. CLIENT_IP_HEADER = CF-Connecting-IP ---")
m.CLIENT_IP_HEADER = CF
for name, request, want in [
    ("trusted peer (compose network): the header", req("172.18.0.5", cf_connecting_ip="203.0.113.9"), "203.0.113.9"),
    ("trusted peer (loopback): the header", req("127.0.0.1", cf_connecting_ip="203.0.113.9"), "203.0.113.9"),
    ("IPv6 visitor", req("172.18.0.5", cf_connecting_ip="2001:db8::1"), "2001:db8::1"),
    ("untrusted peer: the header is ignored", req("198.51.100.4", cf_connecting_ip="203.0.113.9"), "198.51.100.4"),
    ("Tailscale peer is not a proxy", req("100.67.1.2", cf_connecting_ip="203.0.113.9"), "100.67.1.2"),
    ("trusted peer, no header: the peer", req("172.18.0.5"), "172.18.0.5"),
    ("trusted peer, malformed header: the peer", req("172.18.0.5", cf_connecting_ip="x' OR 1=1"), "172.18.0.5"),
    ("a list: the last entry", req("172.18.0.5", cf_connecting_ip="1.2.3.4, 203.0.113.9"), "203.0.113.9"),
    ("non-IP peer (TestClient)", req("testclient", cf_connecting_ip="203.0.113.9"), "testclient"),
]:
    got = m.client_ip(request)
    check(name, got == want, got)
check("the limiter keys on client_ip", m.limiter._key_func is m.client_ip)

print("\n--- 3. Rate-limit buckets through the app ---")
# /api/v1/categories is limited to 60/minute.
import database as db
db.init_pool()
db.init_db()
saved_proxies = m.TRUSTED_PROXIES
try:
    # TestClient's peer is the string "testclient", which is no address. A
    # tiny ASGI shim gives each client a real peer: one inside the default
    # TRUSTED_PROXIES (the compose network), one outside it.
    def with_peer(app, peer):
        async def shim(scope, receive, send):
            if scope["type"] == "http":
                scope = dict(scope, client=(peer, 40000))
            await app(scope, receive, send)
        return shim

    trusted = TestClient(with_peer(m.app, "172.18.0.5"), raise_server_exceptions=False)
    outside = TestClient(with_peer(m.app, "198.51.100.4"), raise_server_exceptions=False)
    tag = uuid.uuid4().int % 200
    a, b = f"203.0.113.{tag}", f"203.0.113.{tag + 1}"

    codes_a = [trusted.get("/api/v1/categories", headers={CF: a}).status_code for _ in range(61)]
    check("visitor A is limited after 60", codes_a[-1] == 429 and 429 not in codes_a[:60], codes_a[-2:])
    check("visitor B through the same proxy has its own bucket",
          trusted.get("/api/v1/categories", headers={CF: b}).status_code == 200)

    spoofed = [outside.get("/api/v1/categories", headers={CF: f"192.0.2.{i}"}).status_code
               for i in range(61)]
    check("an untrusted sender cannot escape its bucket by varying the header",
          spoofed[-1] == 429, spoofed[-2:])
finally:
    m.TRUSTED_PROXIES = saved_proxies

print("\n--- 4. The privacy page names the transport ---")
c = TestClient(m.app, raise_server_exceptions=False)
m.CLIENT_IP_HEADER = CF
page = c.get("/privacy").text
check("behind Cloudflare: Cloudflare is named", "Cloudflare</a>, which provides the HTTPS" in page)
check("behind Cloudflare: no 'plain HTTP' line", "plain HTTP" not in page)
m.CLIENT_IP_HEADER = ""
page = c.get("/privacy").text
check("direct: says plain HTTP", "plain HTTP for now" in page)
check("direct: Cloudflare is not claimed", "Cloudflare" not in page)
check("no placeholder is left", "{{" not in page)
m.CLIENT_IP_HEADER = saved

print("\n--- 5. Deployment files ---")
compose = read("backend", "docker-compose.yml")
block = compose.split("\n  cloudflared:", 1)[1].split("\nvolumes:", 1)[0] if "\n  cloudflared:" in compose else ""
check("compose has a cloudflared service", bool(block))
image = re.search(r"image:\s*(\S+)", block)
check("cloudflared is pinned to a version",
      bool(image) and re.fullmatch(r"cloudflare/cloudflared:\d{4}\.\d+\.\d+", image.group(1)),
      image.group(1) if image else None)
check("cloudflared is behind the tunnel profile", 'profiles: ["tunnel"]' in block)
check("cloudflared reads the token from the environment", "TUNNEL_TOKEN: ${CLOUDFLARE_TUNNEL_TOKEN" in block)
check("the API port's bind address is configurable", '"${API_BIND:-0.0.0.0}:8000:8000"' in compose)
for var in ["CLIENT_IP_HEADER", "TRUSTED_PROXIES"]:
    check(f"{var} reaches the container", f"{var}: ${{{var}:-" in compose)
    check(f"{var} is in .env.example", var in read("backend", ".env.example"))

if not os.path.isdir(os.path.join(REPO, "android")):
    print("SKIP  Android checks: no android/ next to backend/")
else:
    main_manifest = read("android", "app", "src", "main", "AndroidManifest.xml")
    debug_manifest = read("android", "app", "src", "debug", "AndroidManifest.xml")
    gradle = read("android", "app", "build.gradle.kts")
    check("release builds allow no cleartext", 'android:usesCleartextTraffic="false"' in main_manifest)
    check("debug builds keep cleartext", 'android:usesCleartextTraffic="true"' in debug_manifest
          and 'tools:replace="android:usesCleartextTraffic"' in debug_manifest)
    check("a signed release needs an https URL",
          'it.name == "packageRelease"' in gradle and 'url.startsWith("https://")' in gradle)

print("\n" + "=" * 62)
if fails:
      print(f"FAILED ({len(fails)}): " + ", ".join(fails))
      sys.exit(1)
print("All proxy checks passed.")
