# NewsSwipe Native Android App ⚡

A native Android application written in **Kotlin** using **Jetpack Compose** that connects directly to the FastAPI + SQLite backend running on your **Oracle Cloud VM** (`http://141.148.226.251:8000/api/v1/feed`).

It renders **50 Tech & Finance news cards** in a Tinder-style swipe interface.

---

## 📱 Features

- **Oracle VM Direct Connection**: Pre-configured to connect to `http://141.148.226.251:8000/`.
- **Jetpack Compose UI**: 100% native Android declarative UI with dark obsidian theme.
- **Tinder Swipe Engine**: Smooth touch gestures (`Swipe Right 👉 to Read`, `Swipe Left 👈 to Skip`).
- **In-App Browser Integration**: Swiping right automatically launches Android Custom Tabs with the full news publisher URL.
- **Swipe Action Logging**: Sends `POST /api/v1/swipe` feedback to record user preferences in the Oracle VM SQLite database.
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
│           │       └── RetrofitInstance.kt      # Oracle VM IP config (http://141.148.226.251:8000/)
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
2. Select **Open An Existing Project** and choose the `android/` directory inside `Antigrav_test`:
   `c:\Users\matek_yulq090\Desktop\Antigrav_test\android`
3. Allow Gradle to sync dependencies automatically.
4. Connect an Android phone (via USB with USB Debugging enabled) or start an Android Emulator.
5. Click **Run ▶** (or press `Shift + F10`) to launch **NewsSwipe** on your Android device!
