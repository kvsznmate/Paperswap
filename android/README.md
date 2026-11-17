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

## Releasing

Release APKs are signed with one key that is kept outside the repo (#65). Android installs an update only if it is signed with the same key as the installed app, so **this key is the app's identity**. If it is lost, no existing install can ever be updated again.

**One-time setup on the machine that signs.** Generate the key with any JDK 17 `keytool`. Put its values in `~/.gradle/gradle.properties`, never in the repo (`*.jks` is gitignored):

```bash
keytool -genkeypair -keystore ~/.paperswap-keys/paperswap-release.jks -storetype PKCS12 \
  -alias paperswap -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=Paperswap"
```
```properties
paperswap.signing.storeFile=C:/Users/<you>/.paperswap-keys/paperswap-release.jks
paperswap.signing.storePassword=...
paperswap.signing.keyAlias=paperswap
# PKCS12 uses the store password for the key too.
paperswap.signing.keyPassword=...
```

Back up the `.jks` file **and** the password together; a password manager can hold both. Each property can also come from an environment variable: `PAPERSWAP_SIGNING_STORE_FILE`, `..._STORE_PASSWORD`, `..._KEY_ALIAS` and `..._KEY_PASSWORD`. If none is set, `assembleRelease` builds an unsigned APK, as before. If only some are set, the build fails and names the missing ones.

**Build a signed APK locally.** `./gradlew :app:assembleRelease` writes `app/build/outputs/apk/release/app-release.apk`; the name loses `-unsigned` once the APK is signed. Check it with `apksigner verify --print-certs`.

**Publish a release** (`.github/workflows/release.yml`):

1. Bump `versionCode` (+1) and `versionName` in `app/build.gradle.kts`. Open a PR into `dev`, then release `dev` into `main`.
2. Tag the commit on `main` and push the tag: `git tag v1.1 && git push origin v1.1`. The tag must equal `v` + `versionName`.
3. The workflow builds, signs and verifies `paperswap-1.1.apk`, then attaches it to a GitHub Release with generated notes.

The workflow needs four repository secrets:

- `PAPERSWAP_KEYSTORE_BASE64`: the output of `base64 -w0 paperswap-release.jks`
- `PAPERSWAP_SIGNING_STORE_PASSWORD`
- `PAPERSWAP_SIGNING_KEY_ALIAS`
- `PAPERSWAP_SIGNING_KEY_PASSWORD`

The optional repository variable `PAPERSWAP_BASE_URL` sets the backend that the release APK talks to. If it is unset, the APK keeps the Tailscale default and only works on the tailnet until #32 lands.

**Installing over a debug build.** The debug APK is signed with the debug key of the machine that built it, so a signed release cannot install over it. Uninstall the debug build once (this clears Saved and the topic filter), then install the release.

---

## App size

The release APK is **~1.1 MB**, down from 11.0 MB.

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
- **No offline support.** A cold start without connectivity shows only an error.
- **No tests.** There is no unit or instrumented test source set.
