# Paperswap — Android client

A native Android app in **Kotlin** with **Jetpack Compose**. It talks to the FastAPI backend in `../backend` and shows topic-balanced news cards across seven categories in a swipe interface, plus a weekly-summaries tab. The package name is still `com.newsswipe.app`, a leftover from the project's original name.

---

## Features

- **Swipe deck.** Swipe right 👉 to read (the article opens in Android Custom Tabs). Swipe left 👈 to skip.
- **Card flip.** Tap a card to see the summary bullets produced by the backend enrichment job. The back is empty until the article has been enriched.
- **Swipe logging.** Each swipe sends `POST /api/v1/swipe` with `dwell_ms` and `flipped`. `dwell_ms` counts only the time the deck was actually on screen: the timer pauses on the Summaries tab, in the Custom Tab and in the background. If a swipe can't be saved, a notice appears under the deck.
- **Undo.** Each swipe is held for 5 seconds before it is sent, so an undo within that window cancels it entirely. Older swipes have already been recorded, and undo only brings their card back.
- **Weekly summaries.** Pick a topic and see its digest history from `GET /api/v1/summaries/{topic}`.
- **Server-driven topics.** Badge labels and colours come from each article's `category_label` / `accent_color`.

---

## Project structure

```text
android/app/src/main/java/com/newsswipe/app/
├── MainActivity.kt                 # Compose host, tab switch, Custom Tabs launcher
├── data/
│   ├── model/
│   │   ├── NewsArticle.kt          # Feed / swipe / category models (match backend JSON)
│   │   └── TopicSummary.kt         # Weekly summary models
│   └── remote/
│       ├── NewsApiService.kt       # Retrofit interface: feed, swipe, categories, summaries
│       ├── ApiCall.kt              # apiCall {}: one place that turns HTTP/network errors into Result
│       └── RetrofitInstance.kt     # OkHttp client; base URL from BuildConfig
├── ui/
│   ├── theme/                      # Color.kt, AccentColor.kt (hex → Color, contrast), Theme.kt
│   ├── screens/SummariesScreen.kt  # Topic chooser + week history
│   └── components/
│       ├── CardChrome.kt           # Frame + topic badge/source header shared by both card faces
│       ├── NewsCard.kt             # Card front
│       ├── NewsCardBack.kt         # Card back (summary bullets)
│       ├── SwipeableCardStack.kt   # Drag/fling gestures, flip, swipe badges
│       ├── TopNavBar.kt            # Cards / Weekly Summaries switch
│       └── ActionButtons.kt        # Pass, Read, Undo
└── viewmodel/
    ├── NewsViewModel.kt            # Deck state, undo, dwell timing, swipe POSTs
    └── SummaryViewModel.kt         # Topic list + per-topic history cache
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
