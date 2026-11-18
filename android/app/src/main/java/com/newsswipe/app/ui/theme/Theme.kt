package com.newsswipe.app.ui.theme

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontFamily
import androidx.core.view.WindowCompat

/**
 * Headlines and story text. FontFamily.Serif is Noto Serif on Android: a real
 * newspaper serif that costs the APK nothing, because it ships with the OS.
 * The web preview serves the same family itself (backend/static/fonts, ADR-017).
 */
val PaperSerif: FontFamily = FontFamily.Serif

/** Labels, buttons and meta. The platform sans (Roboto). */
val PaperSans: FontFamily = FontFamily.Default

/** Shorthand for the current palette: `Paper.colors.ink`. */
object Paper {
    val colors: PaperPalette
        @Composable @ReadOnlyComposable get() = LocalPaper.current
}

@Composable
fun PaperTheme(
    night: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit
) {
    val palette = if (night) NightPaper else DayPaper

    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            // The bars take the ground colour, so the screen reads as one sheet,
            // and their icons flip to stay visible on light paper.
            window.statusBarColor = palette.ground.toArgb()
            window.navigationBarColor = palette.ground.toArgb()
            WindowCompat.getInsetsController(window, view).apply {
                isAppearanceLightStatusBars = !night
                isAppearanceLightNavigationBars = !night
            }
        }
    }

    // Material components (sheets, progress, ripples) pick these up; the app's
    // own surfaces read LocalPaper directly.
    val scheme = if (night) {
        darkColorScheme(
            primary = palette.ink, onPrimary = palette.onInk,
            background = palette.ground, onBackground = palette.ink,
            surface = palette.card, onSurface = palette.ink,
            surfaceVariant = palette.card, onSurfaceVariant = palette.muted,
            outline = palette.rule
        )
    } else {
        lightColorScheme(
            primary = palette.ink, onPrimary = palette.onInk,
            background = palette.ground, onBackground = palette.ink,
            surface = palette.card, onSurface = palette.ink,
            surfaceVariant = palette.card, onSurfaceVariant = palette.muted,
            outline = palette.rule
        )
    }

    CompositionLocalProvider(LocalPaper provides palette) {
        MaterialTheme(colorScheme = scheme, content = content)
    }
}
