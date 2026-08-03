# Paperswap — Project Status & Progress Log

_Last updated: 2026-08-03_

A running record of what's been built, the milestones crossed, and what remains. The headline achievement so far: **the backend is fully deployed and live on Oracle Cloud.**

---

## 🎯 Current State in One Sentence

The Paperswap backend (FastAPI + SQLite + Pillow card generator) is **built, containerized, and running on an Oracle Cloud VM**, serving news cards and a swipe UI — verified working from a phone. The next major phase is building the **native Android app** (Option C) that consumes this backend as an API.

---

## 🏆 Milestones Crossed — Cloud Deployment

This is the big one. We took the project from "code on a laptop" to "live service on the internet." Each of these was a distinct hurdle cleared:

| # | Milestone | Status |
|---|---|---|
| 1 | **Chose cloud provider** — Oracle Cloud Infrastructure (OCI), Always Free tier | ✅ |
| 2 | **OCI account created**, home region selected | ✅ |
| 3 | **Compute VM provisioned** — navigated `A1.Flex` capacity errors, landed on **E2.1.Micro** (x86, Always Free, 956 MB RAM) | ✅ |
| 4 | **Networking stood up** — VCN, public regional subnet, internet gateway, route table, security list | ✅ |
| 5 | **Public IP attached** — ephemeral, `141.148.226.251` | ✅ |
| 6 | **SSH access established** — key-based auth; sorted Windows key path + permissions | ✅ |
| 7 | **Docker + Compose installed** — v29.7.0, daemon enabled, non-root usage working | ✅ |
| 8 | **2 GB swap file added** — critical: the 956 MB box couldn't build Pillow without it; made persistent via `fstab` | ✅ |
| 9 | **Code uploaded to VM** — via `scp` from Windows | ✅ |
| 10 | **Docker image built** — Pillow via prebuilt wheels to survive low RAM; container `news_cards_backend` running | ✅ |
| 11 | **App verified serving** — `uvicorn` on `0.0.0.0:8000`, DB initialized, news synced, dedup engine confirmed (9 new / 41 existing) | ✅ |
| 12 | **Firewall configured** — OCI Security List ingress rules; port 8000 scoped toward own IP (`84.80.68.54/32`) | ✅ |
| 13 | **Tested end-to-end from phone** — swipe UI loads and works over the network | ✅ |
| 14 | **Cost guardrail** — OCI Budget alert set up for spend monitoring | ✅ (verify) |

**In plain terms:** a complete, working web service deployed to production cloud infrastructure, reachable from a real device. That's a full deployment pipeline exercised end to end — provisioning, networking, containerization, resource tuning, security, and validation.

---

## 🏆 Milestones Crossed — Backend Functionality

Built prior to / alongside deployment (per `walkthrough.md`):

- ✅ **SQLite database** — 50-card batch capacity (25 Tech + 25 Finance).
- ✅ **MD5 deduplication engine** — unique key from `title` + `url`; skips duplicate renders/inserts.
- ✅ **News fetcher** — batches of 50 with short-summary generation.
- ✅ **Pillow card generator** — 9:16 vertical PNGs (720×1280).
- ✅ **REST endpoints** — `GET /api/v1/feed`, `GET /api/v1/feed/next`, `POST /api/v1/swipe`, `POST /api/v1/cards/refresh`.
- ✅ **Web swipe preview** (`mobile_preview.html`) — served at `/mobile`; proved the concept (will be superseded by the native app).
- ✅ **Dockerfile + docker-compose.yml** — containerization complete.

---

## 🔑 Key Decisions Made

| Decision | Choice | Rationale |
|---|---|---|
| **Cloud host** | Oracle Cloud (OCI) | Generous Always Free tier |
| **VM shape** | E2.1.Micro (x86) | A1.Flex Arm was out of capacity; micro is Always Free |
| **RAM workaround** | 2 GB swap file | 956 MB insufficient for Pillow build |
| **Public IP type** | Ephemeral | Free; no need for a reserved IP |
| **Access model** | Personal use — lock endpoint to own IP | "Just me" security goal |
| **App distribution** | **Native Android app (Option C)** | Wants a real app, not a browser page |
| **Recommended app stack** | React Native + Expo | Approachable, instant device testing, cloud APK builds |

---

## ⚠️ Open Items / Known Issues

Things started but not finished, or flagged for attention:

1. **Orphaned boot volumes — PENDING.** A ~630 HUF/month charge appeared, most likely from **detached boot volumes left over from the failed A1 launch attempts** (OCI doesn't delete boot volumes when an instance fails/terminates). Investigation started (Cost Analysis → Boot Volumes) but not completed. **Action:** find detached/available volumes across all ADs and delete the orphans (NOT the one attached to `paperswap-backend`).

2. **HTTPS — NOT YET SET UP.** Currently serving plain HTTP. This is now a **hard prerequisite** for the native app, because **Android blocks cleartext HTTP by default**. Plan: **DuckDNS subdomain + Caddy reverse proxy** (auto Let's Encrypt cert), then close public port 8000.

3. **Security List cleanup — VERIFY.** A public `0.0.0.0/0` rule on port 8000 existed alongside the `/32` rule. Intended end state: **only the `/32` (own-IP) rule remains**. **Action:** confirm the `0.0.0.0/0` port-8000 rule was removed.

4. **SSH still open to `0.0.0.0/0` (port 22).** Functional but probed by bots. Mitigated by key-only auth. Optional hardening: restrict to own IP (risk: dynamic home IP could lock you out).

5. **Budget alert — VERIFY COMPLETION.** Walked through creation; confirm it was saved with an actual + forecast rule and your email.

---

## 🚀 Next Phase — Native Android App (Option C)

Full detail in `paperswap_android_implementation_plan.md`. Summary of remaining pillars:

1. **Phase 0 — Backend readiness** (~1–2 hrs, mandatory): HTTPS via DuckDNS + Caddy; verify `/api/v1/feed` returns clean JSON with absolute image + article URLs; add pagination.
2. **Phase 1–4 — Build the app** (~10–18 hrs): scaffold Expo project → API client → swipe deck (right = open article, left = skip) → states + polish.
3. **Phase 5 — Build APK** (~1–2 hrs): `eas build` → sideload onto phone.
4. **Phase 6 — Play Store** (optional): $25 dev account; only if distributing beyond yourself.

**Critical path:** HTTPS → confirm JSON API → scaffold Expo → wire swipe deck → build APK.

---

## 📌 Quick Reference

| Item | Value |
|---|---|
| VM public IP | `141.148.226.251` |
| SSH user | `ubuntu` |
| App port (internal) | `8000` |
| App entry (web preview) | `http://141.148.226.251:8000/mobile` |
| Gallery | `http://141.148.226.251:8000` |
| Container name | `news_cards_backend` |
| Compose location on VM | `~/paperswap` |
| Start / stop app | `docker compose up -d` / `docker compose down` |
| VM shape | E2.1.Micro (x86, Always Free, 956 MB + 2 GB swap) |
| OS | Ubuntu 22.04 |

---

## 🧠 Architecture (target state)

```
┌─────────────────┐      HTTPS/JSON       ┌──────────────────────────┐
│  Android App    │ ────────────────────► │  Oracle VM (FastAPI)     │
│  (Expo / RN)    │ ◄──────────────────── │  Caddy → :8000           │
│  swipe deck     │                       │  SQLite + Pillow cards   │
└─────────────────┘                       └──────────────────────────┘
        │
        │ Swipe right → open article URL
        ▼
   External news publisher
```

_Note: HTTPS/Caddy layer is planned (Phase 0), not yet deployed._
