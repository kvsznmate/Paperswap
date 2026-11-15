package com.newsswipe.app.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * Colour helpers for values that arrive from the backend rather than the theme.
 *
 * Topic accents live in the server-side catalogue (`categories.accent_color`) so
 * adding a topic never needs an Android release -- see ADR-006. That means the
 * app receives colours as strings it did not choose, and has to cope with a
 * malformed or missing one without taking the screen down with it.
 */

/**
 * Parse a backend hex colour such as "#6366f1".
 *
 * Never throws: a malformed or absent value falls back rather than crashing.
 * `Color.parseColor` throws IllegalArgumentException on anything it dislikes,
 * and one bad row in the catalogue should not blank the whole feed.
 */
fun parseAccent(hex: String?, fallback: Color = TechIndigo): Color =
    runCatching { Color(android.graphics.Color.parseColor(hex)) }.getOrDefault(fallback)

/**
 * Readable foreground for an arbitrary background, by BT.601 perceived
 * luminance.
 *
 * A fixed white would fail on light accents such as Finance's amber, which
 * needs dark text; the darker accents (indigo, red, pink, purple) take white.
 * Computing it means a topic added server-side is legible on arrival instead of
 * waiting for someone to extend a hand-tuned map.
 */
fun contrastOn(background: Color): Color {
    val luminance =
        (background.red * 299f + background.green * 587f + background.blue * 114f) / 1000f
    return if (luminance > 0.588f) Color(0xFF0B0F18) else Color.White
}
