plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.newsswipe.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.newsswipe.app"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"

        // Backend address. Defaults to the server Tailscale address (the phone
        // must be on the same tailnet). Override without editing this file:
        //   ./gradlew assembleDebug -Ppaperswap.baseUrl=http://10.0.2.2:8000/
        // or put paperswap.baseUrl=... in ~/.gradle/gradle.properties.
        val baseUrl = (project.findProperty("paperswap.baseUrl") as String?)
            ?: "http://100.67.178.57:8000/"
        buildConfigField("String", "BASE_URL", "\"$baseUrl\"")

        // The UI is English-only, so drop the translated strings that AndroidX
        // libraries bundle for ~80 locales.
        resourceConfigurations += listOf("en")
    }

    buildTypes {
        release {
            // R8 removes unused code (most of Compose, OkHttp, Coil and Kotlin
            // stdlib that the app never touches) and then unused resources.
            // Reflection-dependent classes are kept in proguard-rules.pro.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.14"
    }
    packaging {
        resources {
            excludes += listOf(
                "/META-INF/{AL2.0,LGPL2.1}",
                // Only read by kotlin-reflect, which the app does not use.
                "/kotlin/**",
                "/kotlin-tooling-metadata.json",
                // Coroutines debug-agent probes, for debuggers attaching to
                // the process; not needed at runtime.
                "/DebugProbesKt.bin",
                // Library version markers for tooling. NOT META-INF/services:
                // coroutines uses ServiceLoader there to find Dispatchers.Main.
                "/META-INF/*.version"
            )
        }
    }
}

dependencies {
    // Android Core & Lifecycle
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")
    implementation("androidx.activity:activity-compose:1.8.2")

    // Jetpack Compose UI
    implementation(platform("androidx.compose:compose-bom:2024.02.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    // Core set only (Close, Favorite). The one extended icon, Undo, is copied
    // into ui/components/UndoIcon.kt rather than pulling ~thousands of icons in.
    implementation("androidx.compose.material:material-icons-core")

    // ViewModel Compose
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.7.0")

    // Networking - Retrofit 2 & Gson
    implementation("com.squareup.retrofit2:retrofit:2.9.0")
    implementation("com.squareup.retrofit2:converter-gson:2.9.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")

    // Image Loading - Coil Compose
    implementation("io.coil-kt:coil-compose:2.5.0")

    // Android Custom Tabs for seamless news browser opening
    implementation("androidx.browser:browser:1.7.0")

    // Tooling
    debugImplementation("androidx.compose.ui:ui-tooling")
}
