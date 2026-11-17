package com.newsswipe.app.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/**
 * The Paper palette (#13, ADR-013): ink on warm paper by day, the same roles
 * reversed at night. The app follows the phone's dark mode.
 *
 * Roles, not colours, so every screen reads from one place:
 *   ground  the screen behind everything
 *   card    the paper a story is printed on (and sheets)
 *   rule    hairlines and outlines
 *   ink     headlines and primary text; also the filled button
 *   ink2    body text, one step quieter
 *   muted   labels, meta, hints. Passes 4.5:1 on both ground and card.
 *   photo   the block shown while a photo loads
 *   onInk   text on an ink-filled button
 *
 * Topic colours are NOT here: they come from the server (ADR-006) and are made
 * readable against `card` by readableOn() in AccentColor.kt.
 */
@Immutable
data class PaperPalette(
    val ground: Color,
    val card: Color,
    val rule: Color,
    val ink: Color,
    val ink2: Color,
    val muted: Color,
    val photo: Color,
    val onInk: Color,
    val isNight: Boolean
)

val DayPaper = PaperPalette(
    ground = Color(0xFFF4EFE4),
    card = Color(0xFFFFFDF8),
    rule = Color(0xFFD9D1C0),
    ink = Color(0xFF1C1A16),
    ink2 = Color(0xFF47433B),
    muted = Color(0xFF6B655A),
    photo = Color(0xFFE6DDCC),
    onInk = Color(0xFFFFFDF8),
    isNight = false
)

val NightPaper = PaperPalette(
    ground = Color(0xFF14120E),
    card = Color(0xFF1E1B16),
    rule = Color(0xFF3A352C),
    ink = Color(0xFFEDE6D6),
    ink2 = Color(0xFFC9C1AF),
    muted = Color(0xFFA39B89),
    photo = Color(0xFF2E2A22),
    onInk = Color(0xFF14120E),
    isNight = true
)

val LocalPaper = staticCompositionLocalOf { DayPaper }

/** Fallback accent when the server sends none or a malformed one. */
val FallbackAccent = Color(0xFF6366F1)
