# Paperswap — Android client

A native Android app in **Kotlin** with **Jetpack Compose**. It talks to the FastAPI backend in `../backend` and shows topic-balanced news cards across seven categories in a swipe interface, a weekly edition, and a Saved list. The look is the Paper design (ADR-013): ink on paper by day, following the phone's dark mode at night. The package name is still `com.newsswipe.app`, a leftover from the project's original name.

---

## Features

- **Swipe deck.** Swipe right, or tap **Read →**, to open the article in Android Custom Tabs. Swipe left, or tap **Pass**, to skip. Cards lead with the headline; a photo shows only when the publisher sent one, and a canned blurb is hidden (`image_is_stock`, `description_source`).
- **Card flip.** Tap a card to see its key points, the sentences the backend enrichment job picked. The back says so when the article has not been enriched yet.
- **Swipe logging.** Each swipe sends `POST /api/v1/swipe` with `dwell_ms` and `flipped`. `dwell_ms` counts only the time the deck was actually on screen: the timer pauses on the other tabs, in the Custom Tab and in the background. If a swipe can't be saved, a notice appears under the deck.
- **Undo.** Each swipe is held for 5 seconds before it is sent, so an undo within that window cancels it entirely. Older swipes have already been recorded, and undo only brings their card back.
- **This week.** Every topic's digest for one week on one scroll, from `GET /api/v1/summaries`, with ‹ › to step through past weeks.
- **Save for later.** The bookmark keeps a copy of the card on the phone (`LocalStore`). It is not a swipe and sends nothing to the server.
- **Topic filter.** "All topics" opens a checklist; the choice is kept on the phone and sent as `?categories=`.
- **Server-driven topics.** Topic labels and colours come from the server (`category_label` / `accent_color`); `readableOn()` adjusts each colour to 4.5:1 on the card.

---

## Project structure

```text
android/app/src/main/java/com/newsswipe/app/
├── MainActivity.kt                 # Compose host, bottom-nav shell, deck screen, Custom Tabs launcher
├── data/
│   ├── local/LocalStore.kt         # Saved articles + topic filter, on the phone
│   ├── model/
│   │   ├── NewsArticle.kt          # Feed / swipe / category models (match backend JSON)
│   │   ├── SavedArticle.kt         # A saved card (Gson, so it lives in model/)
│   │   └── TopicSummary.kt         # Weekly summary models
│   └── remote/
│       ├── NewsApiService.kt       # Retrofit interface: feed, swipe, categories, summaries
│       ├── ApiCall.kt              # apiCall {}: one place that turns HTTP/network errors into Result
│       └── RetrofitInstance.kt     # OkHttp client; base URL from BuildConfig
├── ui/
│   ├── theme/                      # Paper palettes (day/night), readableOn(), PaperTheme
│   ├── screens/
│   │   ├── WeekScreen.kt           # The week as one edition
│   │   └── SavedScreen.kt          # Saved for later
│   └── components/
│       ├── CardChrome.kt           # Paper frame + kicker shared by both faces; photo/blurb rules
│       ├── NewsCard.kt             # Card front: headline first
│       ├── NewsCardBack.kt         # Card back (key points)
│       ├── SwipeableCardStack.kt   # Drag/fling gestures, flip, swipe stamps
│       ├── Masthead.kt             # Serif title + double rule
│       ├── BottomNavBar.kt         # Deck / This week / Saved
│       ├── TopicSheet.kt           # Topic filter checklist
│       ├── PaperIcons.kt, UndoIcon.kt
│       └── ActionButtons.kt        # Undo, Pass, Read →, Save
└── viewmodel/
    ├── NewsViewModel.kt            # Deck state, topic filter, undo, dwell timing, swipe POSTs
    ├── SummaryViewModel.kt         # The week shown, week stepping, per-week cache
    └── SavedViewModel.kt           # Saved list
```

---

## Build and run

1. Open the `android/` directory in Android Studio and let Gradle sync. The build needs JDK 17–22 (AGP 8.5 / Gradle 8.8); Android Studio's bundled JDK 25 is too new to run Gradle 8.8 from the command line.
2. The backend address defaults to the server's **Tailscale** address, so the phone must be on the same tailnet. To point it elsewhere, set the `paperswap.baseUrl` Gradle property. The URL is compiled into `BuildConfig.BASE_URL`:
   ```bash
   ./gradlew assembleDebug -Ppaperswap.baseUrl=http://10.0.2.2:8000/   # emulator → local backend
   ```
   Or set `paperswap.baseUrl=...` once in `~/.gradle/gradle.properties`.
3. Connect a phone with USB debugging, or start an emulator, then **Run ▶**.

---

## App size

The release APK is **~1.07 MB**, down from 11.0 MB.

| Change | Release APK |
| --- | --- |
| Before | 11.04 MB |
| Copied the one extended icon (`UndoIcon.kt`) and depend on `material-icons-core` instead of `material-icons-extended` | 6.86 MB |
| R8 code shrinking + resource shrinking, English-only resources | 1.09 MB |
| Excluded Kotlin builtins metadata, coroutines debug probes and `*.version` files | 1.07 MB |

**R8 keep rules live in `app/proguard-rules.pro`.** Gson fills the models by reflection, so every request/response class must live in `data/model/`, which is kept whole. A model placed anywhere else is shrunk by R8, and its fields arrive as `null` only in release builds. To add an icon, use `material-icons-core` if it has it; otherwise copy the single vector the way `UndoIcon.kt` does, rather than adding `material-icons-extended` back.

About 56% of the remaining code is Compose itself (ui, runtime, foundation), the fixed cost of a Compose app.

---

## Known issues

- **`usesCleartextTraffic="true"`** in the manifest is a workaround for the backend serving plain HTTP. Remove it once HTTPS lands.
- **Failed swipes are not retried.** A notice appears under the deck, but the swipe itself is dropped. Any swipe still in its 5-second undo window when the process is killed outright is also lost. Backgrounding the app sends it immediately.
- **No repository layer.** The ViewModels call Retrofit through `apiCall {}` directly, so there is no seam for a cache or a test fake.
- **The deck ignores topic filtering.** It never passes `?categories=` to `/api/v1/feed`.
- **No offline support.** A cold start without connectivity shows only an error.
- **No tests.** There is no unit or instrumented test source set.
- **Release builds are unsigned.** There is no `signingConfig` for `release`, so the ~1 MB release APK has to be signed (Android Studio → *Build → Generate Signed App Bundle / APK*) before it can be installed. The debug build (~16 MB, unshrunk) is what *Run ▶* installs.
