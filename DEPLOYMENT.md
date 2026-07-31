# Paperswap — Deployment Options (Budget Comparison)

_Last verified: July 2026. Cloud free tiers and prices change constantly — reconfirm on each provider's pricing page before committing. Provider list prices are in USD; €≈USD is close enough for ranking here._

---

## What we're actually deploying

One Dockerized **FastAPI container** + one **Postgres** database. The workload is tiny: ~50 rows, a news fetch every 3 hours, and occasional swipe writes. Images are hot-linked from the source (NewsAPI / Unsplash), so we store and serve almost no bytes ourselves. Requirements: **data persistence** + a **public HTTPS endpoint** the phone can reach.

Two things decide the cheapest viable choice:

1. **Always-on vs scale-to-zero.** The 3-hour fetch runs *inside* the app (APScheduler), so it only fires while the container is running. On always-on hosts it just works. On "sleep"/scale-to-zero free tiers it won't fire while asleep — you'd trigger `/api/v1/refresh` from a free external cron (e.g. cron-job.org) or lean on the app's existing "fetch when the feed is empty" fallback.
2. **Public HTTPS.** A PaaS gives you a free `*.provider.dev` HTTPS URL out of the box. Self-hosting (Pi / VM) needs either a tunnel or your own domain for clean HTTPS.

---

## TL;DR — cheapest first

1. **Raspberry Pi 400 (home) + Cloudflare Tunnel** — **~€1–3/mo** (electricity + optional domain). Cheapest, full control; your uptime = your home's uptime.
2. **Oracle Cloud Always Free (ARM VM)** — **€0/mo**, genuinely free-for-life, always-on, 10 TB egress. Sign-up friction + idle-reclamation risk; you run the VM.
3. **Neon (free Postgres) + a free container host** — **€0/mo**, hands-off managed DB that survives idle; the container either cold-starts (Render free) or costs a couple € to stay warm (Fly).
4. **Fly.io (app + self-managed Postgres)** — **~€4–8/mo**, always-on, no sleep.
5. **Railway** — **~€5/mo** floor, simplest developer experience.
6. **Render (fully managed)** — **~€6–13/mo**; its free Postgres is deleted after 30 days, so a real DB here is paid.

---

## At-a-glance comparison

| Option | Monthly cost | DB persistence | Always-on (3h job) | Public HTTPS | Effort | Watch out for |
|---|---|---|---|---|---|---|
| **Pi 400 + CF Tunnel** | ~€1–3 (power) | Yes (local disk) | Yes | CF Tunnel (free) | Medium | Home uptime, backups, SD-card wear |
| **Oracle Always Free ARM** | €0 | Yes (block vol) | Yes | IP/domain or CF | Med–High | Capacity "out of stock", idle reclaim, June 2026 cut to 2 OCPU/12 GB |
| **Neon + Render free web** | €0 | Yes (Neon, permanent) | No (web sleeps) | Free subdomain | Low | 15-min sleep → 30–60s cold start; needs external cron for the fetch |
| **Neon + small Fly machine** | ~€2–4 | Yes (Neon, permanent) | Yes | Free subdomain | Low–Med | pay-as-you-go metering |
| **Fly.io (app + PG machine)** | ~€4–8 | Yes (volume) | Yes | Free subdomain | Medium | volumes bill even when stopped; managed PG is €38 — use a plain PG machine |
| **Railway** | ~€5+ | Yes | Yes | Free subdomain | Low | no perpetual free tier; meter creeps with RAM held 24/7 |
| **Render (managed PG)** | ~€6–13 | Paid (free PG 30-day expiry) | Yes (Starter) | Free subdomain | Low | free web sleeps; free PG deleted after 30 days |

---

## Option details

### 1) Raspberry Pi 400 at home — cheapest overall
- **Specs:** quad-core Cortex-A72, 4 GB RAM, ARM64. Plenty for this — FastAPI ~150 MB + Postgres ~100 MB idle, DB < 1 MB.
- **Your `docker-compose.yml` runs unchanged.** `python:3.12-slim`, `postgres:16-alpine`, and `psycopg2-binary` all ship ARM64 builds, so nothing needs rebuilding for ARM.
- **Cost:** electricity only. ~5–7 W average ⇒ ~4–5 kWh/month ⇒ **~€1.50/mo** even at NL's ~€0.30/kWh. No infra bill.
- **Reaching it from a phone → Cloudflare Tunnel (free):** an outbound-only `cloudflared` container — no port-forwarding, no public IP, free HTTPS + DDoS protection on a domain you point at Cloudflare. A domain is the only extra cost (~€8–12/yr). It also sidesteps CGNAT/ISP issues that break plain port-forwarding.
  - _Alternative:_ **Tailscale** if the app is only for *your* devices (private WireGuard mesh). Public exposure via Tailscale **Funnel** is HTTPS-only with no custom domain on the free plan — fine for personal use, less so for a public app.
- **Pros:** near-zero cost, full control, no vendor limits, the 3h scheduler just works (always-on).
- **Cons:** uptime = your home power/internet; you own backups (a cron `pg_dump`), SD-card wear (boot Postgres off a USB SSD, not the SD card), and OS/security patching.

### 2) Oracle Cloud Always Free — best €0 cloud
- One **Ampere A1 (ARM) VM**. As of **15 June 2026 the always-free ARM allowance was halved to 2 OCPU / 12 GB** (was 4 / 24) — still far more than this app needs. Plus **200 GB block storage** and **10 TB/month egress**, free for life (unlike AWS/GCP's 12-month trials). Two small AMD micro VMs (1 GB) are also always-free.
- Run the same `docker-compose` (ARM-friendly). Always-on ⇒ scheduler works. Free reserved public IP.
- **Pros:** truly €0, always-on, huge egress allowance, a real VM you control.
- **Cons:** sign-up needs a card; ARM capacity is often "out of capacity" in popular regions (retry, or use a creation script); **idle instances can be reclaimed** — keep light periodic activity (your 3h job helps). HTTPS needs a domain (+ Caddy/Let's Encrypt) or Cloudflare in front. You patch/secure the VM yourself.

### 3) Managed split — Neon (DB) + a free/cheap container host
- **Neon** is the standout free managed Postgres: **0.5 GB storage**, scale-to-zero after 5 min idle but **resumes instantly**, and **permanent** (not time-limited). 50 rows is nothing; backups are included; you never babysit it.
  - _DB alternatives:_ **Supabase** — 500 MB free but the project **pauses after 1 week idle** (annoying for low traffic). **Aiven** — free with larger storage. **Render's own free Postgres is deleted after 30 days** — avoid for anything you keep.
- Pair Neon with a container host:
  - **Render free web service** — €0, but sleeps after 15 min (30–60 s cold start), and the in-app 3h fetch won't run while asleep → drive `/api/v1/refresh` from a free external cron (cron-job.org).
  - **Small Fly.io machine** (~€2–4/mo) — stays warm, scheduler runs, no cold starts.
  - **Koyeb / Google Cloud Run** free tiers also work (Cloud Run scales to zero → same external-cron note).
- **Pros:** least ops, a DB you never think about. **Cons:** cold starts on free compute, or a couple € to avoid them.

### 4) Fly.io (all-in, self-managed) — ~€4–8/mo
- No free tier for new accounts (removed 2024; only a 2-hr / 7-day trial). Pay-as-you-go, billed per second.
- App on a `shared-cpu-1x` machine: 256 MB ≈ **~€2/mo**, 1 GB ≈ **~€5.7/mo**.
- **Don't use Fly Managed Postgres (~€38/mo).** Run Postgres as a **plain Fly machine + small volume** (~€2–7/mo). Whole stack lands ~€4–8/mo, always-on, with 100 GB/month free egress (NA/EU).
- **Watch:** volumes bill even when the machine is stopped.

### 5) Railway — ~€5/mo, simplest DX
- No perpetual free tier (one-time $5 trial, card required). **Hobby $5/mo includes $5 of usage.** A tiny idle app can land near €5 all-in, but Postgres holds RAM 24/7 so the meter runs continuously — realistically **~€5–12/mo** with a small DB. Excellent one-click experience (auto-detects the stack, provisions Postgres in a click).

### 6) Render — ~€6–13/mo for a real setup
- Cleanest managed DX and a free web tier, **but**: the free web service sleeps after 15 min, and the **free Postgres is deleted after 30 days with no grace period**. A persistent DB therefore means the Basic Postgres (~€6/mo) + an always-on Starter service (~€7/mo). Fine if you value simplicity over squeezing every euro.

---

## Recommendation for Paperswap

- **Absolute cheapest, and you already own the hardware → Raspberry Pi 400 + Cloudflare Tunnel.** ~€1.50/mo power (+ ~€10/yr domain). Your compose file runs as-is, the 3h scheduler works, and a nightly `pg_dump` to another disk/cloud covers backups.
- **Want €0 but off your home network / more reliable → Oracle Always Free ARM VM.** Same compose, always-on, 10 TB egress — just budget an afternoon for sign-up + capacity hunting, and set a keep-alive so it isn't reclaimed.
- **Want zero ops and don't mind a cold start → Neon (free) + Render free web + a free external cron** to hit `/api/v1/refresh`. Upgrade the web side to a small Fly machine (~€2–4/mo) if cold starts annoy you.

For a personal project where the Pi is sitting there anyway, the **Pi wins on cost**; **Oracle** is the best hands-off €0 fallback if you'd rather not depend on home uptime.

---

## Cross-cutting gotchas

- **Backups.** Managed DBs (Neon / Supabase / Railway / Render) back up for you. On the Pi or Oracle you own it — schedule `pg_dump` (a cron writing to object storage or a second disk).
- **The 3-hour fetch.** Only runs while the container is up. Always-on hosts (Pi / Oracle / Fly / Railway) = fine. Sleep / scale-to-zero free tiers = add an external cron pinging `/api/v1/refresh`.
- **HTTPS.** PaaS gives a free HTTPS subdomain; self-hosting needs a tunnel or a domain.
- **"Free forever" fine print.** Oracle just halved its ARM tier with no notice and reclaims idle boxes; Render deletes free DBs at 30 days; Supabase pauses at 1 week. Read the current terms before relying on any of them.

---

## Sources
Provider pricing pages and independent pricing trackers, checked **July 2026** (Render, Railway, Fly.io, Neon, Supabase, Oracle OCI, Cloudflare Tunnel / Tailscale docs). Prices move — reconfirm before you deploy.
