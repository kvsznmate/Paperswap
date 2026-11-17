package com.newsswipe.app.ui.components

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathBuilder
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.unit.dp

/**
 * Line icons for the Paper design, drawn here rather than taken from
 * material-icons-extended, which would add thousands of unused icon classes to
 * the APK (see UndoIcon.kt). 24-unit grid, 2-unit round stroke, to match the
 * web preview's inline SVGs. `Icon(tint = ...)` recolours them.
 */
private fun lineIcon(name: String, filled: Boolean = false, block: PathBuilder.() -> Unit): ImageVector =
    ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).apply {
        path(
            fill = if (filled) SolidColor(Color.Black) else null,
            stroke = SolidColor(Color.Black),
            strokeLineWidth = 2f,
            strokeLineCap = StrokeCap.Round,
            strokeLineJoin = StrokeJoin.Round,
            pathBuilder = block
        )
    }.build()

private val bookmarkPath: PathBuilder.() -> Unit = {
    moveTo(6f, 3f); lineTo(18f, 3f); lineTo(18f, 21f); lineTo(12f, 17f); lineTo(6f, 21f); close()
}

val BookmarkIcon: ImageVector by lazy { lineIcon("Bookmark", block = bookmarkPath) }
val BookmarkFilledIcon: ImageVector by lazy { lineIcon("BookmarkFilled", filled = true, block = bookmarkPath) }

/** A card with two text lines: the Deck tab. */
val DeckIcon: ImageVector by lazy {
    lineIcon("Deck") {
        moveTo(7f, 3f); lineTo(17f, 3f); arcToRelative(2f, 2f, 0f, false, true, 2f, 2f)
        lineTo(19f, 19f); arcToRelative(2f, 2f, 0f, false, true, -2f, 2f)
        lineTo(7f, 21f); arcToRelative(2f, 2f, 0f, false, true, -2f, -2f)
        lineTo(5f, 5f); arcToRelative(2f, 2f, 0f, false, true, 2f, -2f); close()
        moveTo(9f, 8f); lineTo(15f, 8f)
        moveTo(9f, 12f); lineTo(15f, 12f)
    }
}

/** A folded newspaper: the This week tab. */
val WeekIcon: ImageVector by lazy {
    lineIcon("Week") {
        moveTo(4f, 4f); lineTo(17f, 4f); lineTo(17f, 20f); lineTo(6f, 20f)
        arcToRelative(2f, 2f, 0f, false, true, -2f, -2f); close()
        moveTo(17f, 8f); lineTo(20f, 8f); lineTo(20f, 18f)
        arcToRelative(2f, 2f, 0f, false, true, -2f, 2f)
        moveTo(8f, 8f); lineTo(13f, 8f)
        moveTo(8f, 12f); lineTo(13f, 12f)
        moveTo(8f, 16f); lineTo(11f, 16f)
    }
}

/** Three narrowing lines: the topic filter. */
val FilterIcon: ImageVector by lazy {
    lineIcon("Filter") {
        moveTo(4f, 6f); lineTo(20f, 6f)
        moveTo(7f, 12f); lineTo(17f, 12f)
        moveTo(10f, 18f); lineTo(14f, 18f)
    }
}

val CloseIcon: ImageVector by lazy {
    lineIcon("Close") {
        moveTo(6f, 6f); lineTo(18f, 18f)
        moveTo(18f, 6f); lineTo(6f, 18f)
    }
}
