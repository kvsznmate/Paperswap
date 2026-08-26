# NewsSwipe Native Android App ⚡

A native Android application written in **Kotlin** using **Jetpack Compose** that connects to the FastAPI + PostgreSQL backend running on an **Oracle Cloud VM** (`http://<VM_IP>:8000/api/v1/feed`).

It renders topic-balanced news cards across seven categories in a Tinder-style swipe interface.

---

## 📱 Features

- **Oracle VM Direct Connection**: Server address configured in `RetrofitInstance.kt` (not recorded in this repo).
- **Jetpack Compose UI**: 100% native Android declarative UI with dark obsidian theme.
- **Tinder Swipe Engine**: Smooth touch gestures (`Swipe Right 👉 to Read`, `Swipe Left 👈 to Skip`).
- **In-App Browser Integration**: Swiping right automatically launches Android Custom Tabs with the full news publisher URL.
- **Swipe Action Logging**: Sends `POST /api/v1/swipe` feedback to record user preferences in the backend PostgreSQL database.
- **Undo Capability**: Restore previously swiped cards.

---

## 📁 Android Project Structure

```text
android/
├── app/
│   ├── build.gradle.kts
│   └── src/main/
│       ├── AndroidManifest.xml
│       └── java/com/newsswipe/app/
│           ├── MainActivity.kt                  # Compose UI Host & Custom Tabs launcher
│           ├── data/
│           │   ├── model/NewsArticle.kt         # Data models matching FastAPI JSON
│           │   └── remote/
│           │       ├── NewsApiService.kt        # Retrofit API interface
│           │       └── RetrofitInstance.kt      # Server base URL config
│           ├── ui/
│           │   ├── theme/                       # Colors, Type, Dark Obsidian Theme
│           │   └── components/
│           │       ├── NewsCard.kt              # 9:16 Portrait Card Component
│           │       ├── SwipeableCardStack.kt    # Tinder Gesture Engine
│           │       └── ActionButtons.kt         # Pass, Read, Undo Buttons
│           └── viewmodel/
│               └── NewsViewModel.kt             # State Manager & Retrofit Coroutines
├── build.gradle.kts
└── settings.gradle.kts
```

---

## 🛠 How to Open and Build in Android Studio

1. **Open Android Studio**.
2. Select **Open an Existing Project** and choose the `android/` directory in this repository.
3. Allow Gradle to sync dependencies automatically.
4. Set the server address in `RetrofitInstance.kt` before building — it is not committed.
5. Connect an Android phone (USB debugging enabled) or start an emulator.
6. Click **Run ▶** (or `Shift + F10`).

---

## ⚠ Known issues

- **Server URL is a compile-time constant** pointing at an ephemeral IP. When the address changes, installed builds break permanently. Should move to a `BuildConfig` field per build type.
- **`usesCleartextTraffic="true"`** in the manifest — a workaround for the backend serving plain HTTP, not a decision. Removed once HTTPS lands.
- **No repository layer.** `NewsViewModel` calls Retrofit directly, so there is no seam for a cache or a test fake.
- **No offline support.** A cold start without connectivity shows only an error.
- **Swipes are lost when offline.** The `POST` failure is swallowed silently, discarding the interaction data the project is meant to learn from.
- **Topic filtering is unused.** The backend serves `/api/v1/categories` with labels, colours, and counts; the client never calls it.
- **OkHttp body logging is unconditional**, so release builds log every payload to logcat.
