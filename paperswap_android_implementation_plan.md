# Paperswap — Native Android App Implementation Plan

## Where we are today (what's already done ✅)

You've built and deployed the entire **backend half** of the project. This is real, working infrastructure — not throwaway:

- **FastAPI backend** running in Docker on an Oracle Cloud VM (Ubuntu, E2.1.Micro). Address not recorded here — see the OCI console.
- **SQLite database** with news articles, MD5 deduplication, and swipe logging.
- **Pillow card generator** producing 9:16 (720×1280) PNG cards.
- **News fetcher** pulling Tech + Finance stories in batches.
- **REST endpoints** (per your plan): `GET /api/v1/feed`, `GET /api/v1/feed/next`, `POST /api/v1/swipe`, `POST /api/v1/cards/refresh`.
- A **web swipe UI** (`mobile_preview.html`) — useful for testing, but *not* part of the native app. Think of it as the throwaway prototype that proved the backend works.

**The mental model going forward:** your Oracle VM does **not** become the app. It becomes the **API server** that the Android app talks to over the network. The app runs on the phone; the backend serves it data and images.

```
┌─────────────────┐        HTTPS/JSON        ┌──────────────────────────┐
│  Android App    │ ───────────────────────► │  Oracle VM (FastAPI)     │
│  (runs on phone)│ ◄─────────────────────── │  cards + images + swipes │
└─────────────────┘                          └──────────────────────────┘
        │
        │ Swipe right → opens article URL in browser
        ▼
   External news site
```

---

## What's left — the three remaining pillars

1. **Backend readiness** — make the API safe and correct for a native client (HTTPS + clean JSON contract). *Small but mandatory.*
2. **The Android app itself** — a new codebase that renders the swipe deck natively. *The bulk of the work.*
3. **Build & distribution** — turn it into an installable APK (and optionally publish).

Everything below is organized into phases you can tackle in order.

---

## Phase 0 — Backend Readiness (prerequisite, ~1–2 hrs)

A native Android app has stricter requirements than a browser. Two things must be true before the app can talk to your backend.

### 0.1 — Add HTTPS (now mandatory, not optional)

Android **blocks plaintext HTTP traffic by default** (cleartext is disabled unless you explicitly allow it, which you shouldn't for production). So the DuckDNS + Caddy setup we discussed becomes a hard prerequisite.

Steps:
1. **Get a free subdomain** at [duckdns.org](https://www.duckdns.org) → e.g. `paperswap.duckdns.org`, pointed at the VM's public IP.
2. **Install Caddy** on the VM as a reverse proxy. A minimal `Caddyfile`:
   ```
   paperswap.duckdns.org {
       reverse_proxy 127.0.0.1:8000
   }
   ```
   Caddy auto-provisions and renews a free Let's Encrypt certificate.
3. **Open ports 80 + 443** in the OCI Security List (needed for the cert challenge + HTTPS). **Close public 8000** — only Caddy talks to the app now.
4. Your API base URL becomes `https://paperswap.duckdns.org`.

> This also removes the browser HTTPS-forcing friction entirely, and closes the "anyone can hit port 8000" exposure in one move.

### 0.2 — Verify the JSON API contract

The web preview may return **HTML**; a native app needs clean **JSON**. Confirm each endpoint returns structured data the app can parse. Target shapes:

**`GET /api/v1/feed?limit=20&offset=0`** → list of cards:
```json
{
  "cards": [
    {
      "id": 168,
      "title": "The Tech Download: Anduril CEO...",
      "summary": "Short generated summary...",
      "category": "tech",
      "image_url": "https://paperswap.duckdns.org/output/cards/168.png",
      "article_url": "https://source-publisher.com/article",
      "published_at": "2026-08-01T10:00:00Z"
    }
  ],
  "next_offset": 20,
  "has_more": true
}
```

**`POST /api/v1/swipe`** → logs the action:
```json
// request body
{ "card_id": 168, "direction": "right" }
// response
{ "status": "ok" }
```

Action items:
- Confirm `image_url` is an **absolute HTTPS URL** the phone can fetch directly (not a local file path).
- Confirm `article_url` is present on every card (right-swipe needs it).
- Add **pagination** (`limit`/`offset` or a cursor) so the app can load cards in batches instead of all 50 at once.
- Make sure the card PNGs are served over HTTPS (static file route, or bake image bytes into the JSON as a fallback — but URLs are better).

### 0.3 — Keep it alive

- Ensure the container has `restart: unless-stopped` so it survives reboots.
- Confirm the news-refresh scheduler runs (fresh cards over time).
- Consider light **rate limiting** on `POST /api/v1/cards/refresh` so it can't be abused.

---

## Phase 1 — Choose Stack & Scaffold the App (~2–4 hrs)

### Recommendation: **React Native + Expo**

For your situation (learning as you go, want a real native app, backend already speaks REST), Expo is the smoothest path:

- **JavaScript/TypeScript** — likely more approachable than Dart or Kotlin.
- **Expo Go** — test instantly on your physical phone by scanning a QR code; no Android Studio needed to start.
- **EAS Build** — produces a real APK/AAB in the cloud, so you don't need a full local Android toolchain.
- **Mature swipe libraries** — `react-native-deck-swiper` or a Reanimated-based deck give you Tinder-style gestures out of the box.

**Strong alternative: Flutter** — excellent performance and built-in swipe widgets, but Dart is a new language to learn. Pick Flutter if you already lean that way; otherwise Expo.

**Kotlin (fully native)** — the most "native," but the steepest learning curve and most boilerplate. Only if you specifically want to learn Android's native stack.

The rest of this plan assumes **Expo**.

### 1.1 — Setup

```bash
# On your Windows machine (not the VM)
npm install -g expo-cli eas-cli
npx create-expo-app paperswap-app
cd paperswap-app
npx expo start   # scan QR with Expo Go on your phone
```

### 1.2 — Install core dependencies

```bash
npx expo install react-native-deck-swiper react-native-gesture-handler \
    react-native-reanimated expo-linking expo-image
```

- `react-native-deck-swiper` — the card stack + swipe gestures.
- `expo-image` — fast image loading/caching for the card PNGs.
- `expo-linking` — open article URLs in the system browser on right-swipe.

### 1.3 — Project structure

```
paperswap-app/
├── App.js                 # root, navigation
├── src/
│   ├── api/client.js      # talks to your backend
│   ├── components/Card.js # single 9:16 card
│   ├── screens/DeckScreen.js  # the swipe deck
│   └── config.js          # API_BASE_URL
```

---

## Phase 2 — API Integration Layer (~2–3 hrs)

### 2.1 — Config

```js
// src/config.js
export const API_BASE_URL = "https://paperswap.duckdns.org";
```

### 2.2 — API client

```js
// src/api/client.js
import { API_BASE_URL } from "../config";

export async function fetchFeed(offset = 0, limit = 20) {
  const res = await fetch(`${API_BASE_URL}/api/v1/feed?offset=${offset}&limit=${limit}`);
  if (!res.ok) throw new Error(`Feed failed: ${res.status}`);
  return res.json();
}

export async function logSwipe(cardId, direction) {
  await fetch(`${API_BASE_URL}/api/v1/swipe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ card_id: cardId, direction }),
  });
}
```

Deliverables: fetch a page of cards, log a swipe, handle network errors gracefully.

---

## Phase 3 — The Swipe Deck UI (~4–8 hrs, the heart of the app)

### 3.1 — Card component

Renders one 9:16 card: the PNG image filling the frame, with title/summary overlay if the image doesn't already contain them.

```jsx
// src/components/Card.js
import { View, Text, StyleSheet } from "react-native";
import { Image } from "expo-image";

export default function Card({ card }) {
  return (
    <View style={styles.card}>
      <Image source={card.image_url} style={styles.image} contentFit="cover" transition={200} />
    </View>
  );
}
```

### 3.2 — Deck screen with gestures

```jsx
// src/screens/DeckScreen.js — sketch
import Swiper from "react-native-deck-swiper";
import * as Linking from "expo-linking";
import { fetchFeed, logSwipe } from "../api/client";

// On mount: load first page into state.
// <Swiper
//   cards={cards}
//   renderCard={(card) => <Card card={card} />}
//   onSwipedRight={(i) => { logSwipe(cards[i].id, "right"); Linking.openURL(cards[i].article_url); }}
//   onSwipedLeft={(i) => { logSwipe(cards[i].id, "left"); }}
//   onSwipedAll={() => loadNextPage()}
//   overlayLabels={{ left: {title:"SKIP"}, right:{title:"READ"} }}
// />
```

Behaviors to implement:
- **Swipe right** → log swipe + open `article_url` in system browser.
- **Swipe left** → log swipe + advance to next card.
- **Overlay badges** — green "READ" on right drag, red "SKIP" on left (mirrors your web preview).
- **Load more** — when the deck runs low, fetch the next page (infinite feed).
- **Rotation/spring animation** — the library handles this; tune stiffness to taste.

---

## Phase 4 — States, Caching & Polish (~2–4 hrs)

- **Loading state** — spinner while the first feed loads.
- **Empty state** — "No more cards — pull to refresh" when the feed is exhausted.
- **Error state** — friendly message + retry button when the backend is unreachable.
- **Image prefetching** — preload the next few card images so swipes feel instant (`expo-image` caches automatically; you can also prefetch).
- **Pull-to-refresh** — trigger a fresh feed load.
- **App icon + splash screen** — Expo config in `app.json`.

---

## Phase 5 — Build & Test on Device (~1–2 hrs)

### 5.1 — Test continuously during dev
Use **Expo Go** (QR scan) throughout Phases 2–4 — instant reload on your real phone.

### 5.2 — Produce an installable APK

```bash
eas build:configure
eas build -p android --profile preview   # builds an APK in the cloud
```

EAS returns a download link → install the APK directly on your Android device (enable "install from unknown sources"). This is a **real, standalone app** — it no longer needs Expo Go and runs even when your laptop is off (it just needs the backend reachable).

---

## Phase 6 — (Optional) Google Play Store

Only if you want to distribute beyond yourself:

- **Google Play Developer account** — one-time **$25** fee.
- Build an **AAB** (`eas build -p android --profile production`).
- Prepare store listing (screenshots, description, privacy policy — required if you collect any data).
- Submit for review.

For personal use, **skip this** — just sideload the APK from Phase 5.

---

## Effort & Sequencing Summary

| Phase | What | Rough effort | Blocking? |
|---|---|---|---|
| 0 | Backend: HTTPS + JSON contract | 1–2 hrs | **Yes — do first** |
| 1 | Scaffold Expo app | 2–4 hrs | Yes |
| 2 | API client | 2–3 hrs | Yes |
| 3 | Swipe deck UI | 4–8 hrs | Yes (core) |
| 4 | States + polish | 2–4 hrs | No (quality) |
| 5 | Build APK | 1–2 hrs | Yes (to ship) |
| 6 | Play Store | +½ day | Optional |

**Realistic total for a working personal app:** roughly 12–25 hours of focused work, depending on how much React Native is new to you. Phase 3 is where most of the time and learning goes.

---

## Critical Path (shortest route to a swipeable APK)

1. **Phase 0.1** — HTTPS via DuckDNS + Caddy (Android won't connect without it).
2. **Phase 0.2** — confirm `/api/v1/feed` returns JSON with absolute image URLs + article URLs.
3. **Phase 1** — scaffold Expo, test with Expo Go.
4. **Phase 2 + 3** — API client + deck swiper wired to right→article / left→skip.
5. **Phase 5** — `eas build` → sideload APK.

Polish (Phase 4) and the store (Phase 6) come after you have something working in your hand.

---

## Open Decisions for You

1. **Stack confirmation** — go with **Expo/React Native** (recommended), or do you prefer **Flutter** / **Kotlin**?
2. **Domain** — free **DuckDNS subdomain** (fastest), or buy a real domain (~€1–10/yr)?
3. **Distribution** — just for you (sideload APK), or Play Store eventually?
4. **API contract** — can you confirm what `GET /api/v1/feed` currently returns (JSON vs HTML, and whether image/article URLs are absolute)? That determines how much Phase 0.2 work is needed.

Answer these and the next step is either **"set up DuckDNS + Caddy"** (Phase 0) or **"scaffold the Expo app"** (Phase 1) — whichever you'd rather start with.
