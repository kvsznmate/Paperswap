package com.newsswipe.app.ui.theme

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.graphics.luminance
import kotlin.math.max
import kotlin.math.min

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
fun parseAccent(hex: String?, fallback: Color = FallbackAccent): Color =
    runCatching { Color(android.graphics.Color.parseColor(hex)) }.getOrDefault(fallback)

/** WCAG contrast ratio between two colours, 1 to 21. */
fun contrastRatio(a: Color, b: Color): Float {
    val la = a.luminance()
    val lb = b.luminance()
    return (max(la, lb) + 0.05f) / (min(la, lb) + 0.05f)
}

/**
 * The accent, moved toward black (on a light background) or white (on a dark
 * one) just far enough to reach [minRatio] against [background].
 *
 * The server's accents were picked for the old dark UI: on paper, Finance's
 * amber or Sports' green as small text fail 4.5:1, and at night indigo does.
 * Computing the adjustment keeps topic colours server-driven (ADR-006) and
 * makes a topic added later legible on arrival. Hue is kept; only lightness
 * moves, in 5% steps, so the colour stays recognisably the topic's.
 */
fun readableOn(accent: Color, background: Color, minRatio: Float = 4.5f): Color {
    val target = if (background.luminance() > 0.5f) Color.Black else Color.White
    var colour = accent
    var steps = 0
    while (contrastRatio(colour, background) < minRatio && steps < 20) {
        colour = lerp(accent, target, 0.05f * (steps + 1))
        steps++
    }
    return colour
}
