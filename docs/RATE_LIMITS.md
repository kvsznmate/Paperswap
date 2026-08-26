# Rate limits

Every limit configured in `backend/main.py`, what it protects, and why it is set
where it is.

## Current limits

| Endpoint | Method | Limit | Auth | What the limit protects |
|---|---|---|---|---|
| `/api/v1/feed` | GET | 60/min | public | Deck query, and a cold-start news fetch |
| `/api/v1/categories` | GET | 60/min | public | Small aggregate query |
| `/api/v1/swipe` | POST | 120/min | public | Unbounded rows in `user_swipes` |
| `/api/v1/telemetry/heartbeat` | POST | 30/min | public | Unbounded rows in `user_sessions` |
| `/api/v1/telemetry/stats` | GET | 30/min | admin | ~10 queries + `du` per call |
| `/api/v1/telemetry/logs` | GET | 30/min | admin | In-memory read |
| `/api/v1/auth/session` | POST | 10/min | none | Brute-forcing the admin key |
| `/api/v1/cards/refresh` | POST | 2/hour | admin | ~84 outbound calls to NewsAPI / Google News |

Limits are keyed on client IP and enforced by
[slowapi](https://github.com/laurentS/slowapi). Exceeding one returns **429** with
`{"error": "Rate limit exceeded: N per M"}`.

## How the numbers were chosen

**Swipe — 120/min.** Swiping is client-side; the deck advances an in-memory index
and never refetches. So a session produces roughly one call per second, ~60/min.
120 is double the realistic ceiling, leaving room for double-taps and bursts.

This started at 10/min, which was wrong and worth recording. At one swipe per
second a user exhausts it in ten seconds, and because the client only logged the
failure to the console, the deck kept working while the swipes silently stopped
being recorded. A rate limit that produces invisible data loss is worse than no
rate limit. The client now shows a notice on 429.

The limit is not protecting compute — a swipe insert measures ~0.6 ms. It bounds
how fast a script can write rows to a 47 GB volume.

**Feed — 60/min.** The client calls this once per session, so 60 is generous. It
matters more than the query cost suggests: on a cold start the endpoint fetches
news synchronously, ~84 outbound requests against a NewsAPI free tier of
1,000/day. See "Known gap" below.

**Refresh — 2/hour.** The most expensive endpoint in the system. Also admin-only,
so the limit is a second line behind authentication rather than the primary
control. The scheduler already refreshes every 12 h; manual refreshes are for
operators, not traffic.

**Sign-in — 10/min.** The one endpoint where guessing is the whole attack. Tight
enough to make online brute force impractical, loose enough to survive typos.
Note this also caps *legitimate* sign-ins from one IP.

**Telemetry — 30/min.** The dashboard polls every 8 s (7.5/min), so 30 allows a
few tabs. `stats` is the heaviest read in the system: ~10 queries plus `du` over
several directories.

**Heartbeat — 30/min.** The client sends one every 15 s (4/min).

## Configuration

Limits are literals in the route decorators in `backend/main.py`:

```python
@app.post("/api/v1/swipe", status_code=201)
@limiter.limit("120/minute")
def record_swipe(request: Request, req: SwipeRequest):
```

Two things to know if you change them:

- **slowapi requires an explicit `request: Request` parameter** on every limited
  handler. Omit it and the route raises at startup, not at call time.
- **Decorator order matters.** `@app.<method>` goes above `@limiter.limit`.

## Storage: in-memory

Counters live in the process. Consequences:

- **A restart clears every counter.** A deploy resets all limits to zero.
- **Per-process, not per-deployment.** One uvicorn worker today, so this is
  exact. Running multiple workers would multiply every effective limit by the
  worker count.
- **No Redis.** Deliberate: a second container is not worth 40–60 MB on a 956 MB
  VM for a personal-scale service.

## Window behaviour

Fixed window, not rolling — the underlying `limits` library default. The counter
resets on the wall-clock boundary, so 120 requests at 12:00:59 and another 120 at
12:01:01 both succeed. In practice the limits behave as burst-then-stall rather
than a smooth cap. Adequate here; worth knowing before treating a limit as a
hard rate.

## Known gap: reverse proxy

Limits key on `request.client.host`. Today the app is exposed directly, so that
is the real client IP.

**Once Caddy is in front (planned — see `PROJECT_STATUS.md`), every request will
appear to come from the proxy.** All users collapse into one bucket and each
limit becomes a global cap across the whole userbase rather than per-client.

The fix is to trust `X-Forwarded-For`, which is only safe when the proxy is
trusted and overwrites the header — otherwise any client can spoof its way past
the limiter by sending its own. Wire it behind an explicit `TRUST_PROXY` flag,
defaulting off, at the same time HTTPS lands. Tracked as a follow-up; the limits
above are correct only until that day.

## Known gap: cold-start fetch on the feed

`GET /api/v1/feed` still triggers a synchronous news fetch when the query returns
nothing, so 60/min against an empty topic permits up to 60 × 84 = 5,040 outbound
requests per minute — enough to exhaust the NewsAPI daily quota in seconds.

Rate limiting alone does not close this; the endpoint needs a cooldown so at most
one synchronous fetch happens per interval regardless of request volume. Tracked
as a follow-up (F1).

## Verifying

`backend/tests/test_api_security.py` asserts the swipe limiter trips above
120/min and that a realistic 60-swipe session is never rejected:

```bash
docker compose exec news-cards-backend python tests/test_api_security.py
```

Because counters are in-memory and per-process, running two suites that both
exercise the swipe endpoint inside the same minute can produce a spurious 429 in
the second. Leave a minute between them.
