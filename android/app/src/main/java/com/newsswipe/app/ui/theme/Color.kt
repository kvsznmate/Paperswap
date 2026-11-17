package com.newsswipe.app.ui.theme

import androidx.compose.ui.graphics.Color

val CardBackground = Color(0xFF121824)
val CardBorder = Color(0xFF222B3C)

// App canvas. Lifted well off pure black so the screen has a colour rather than
// a void, but every value stays BELOW CardBackground (#121824) in all three
// channels. That ordering is the elevation cue: the card is the lightest thing
// on screen, so it reads as raised. A canvas lighter than the card inverts that
// and makes the deck look sunken, which is what a first attempt at these values
// did at the top of the gradient.
//
// Top is 4-5 steps under the card, bottom is 9-15, giving a gentle wash without
// either end colliding with the card fill.
val AppBackground = Color(0xFF090C15)
val AppBackgroundTop = Color(0xFF0E131F)

// Brand colour, and the fallback accent when the server sends none. Topic
// colours themselves come from the backend (ADR-006), never from here.
val TechIndigo = Color(0xFF6366F1)

val TextPrimary = Color(0xFFF8FAFC)
val TextSecondary = Color(0xFF94A3B8)
val TextMuted = Color(0xFF64748B)

val PassRed = Color(0xFFEF4444)
val ReadGreen = Color(0xFF10B981)
val UndoAmber = Color(0xFFF59E0B)
