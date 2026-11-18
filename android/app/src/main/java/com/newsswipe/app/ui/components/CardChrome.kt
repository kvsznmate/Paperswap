package com.newsswipe.app.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.newsswipe.app.data.model.NewsArticle
import com.newsswipe.app.ui.theme.Paper
import com.newsswipe.app.ui.theme.PaperSans
import com.newsswipe.app.ui.theme.parseAccent
import com.newsswipe.app.ui.theme.readableOn

// Shared by both faces of a card, so a flipped card visibly stays the same
// story: same paper, same kicker line.

internal val CardShape = RoundedCornerShape(6.dp)

/** Description tiers the backend writes from a template rather than the publisher. */
private val CANNED_DESCRIPTION_SOURCES = setOf("keyword_template", "topic_fallback")

/** The article's topic colour as the server sent it (ADR-006). */
val NewsArticle.accent: Color get() = parseAccent(accentColor)

/** The topic colour adjusted to 4.5:1 on the card, for text and marks. */
@Composable
fun NewsArticle.kickerColor(): Color {
    val card = Paper.colors.card
    return remember(accentColor, card) { readableOn(accent, card) }
}

/** "Sports", from the server's label, falling back to the slug. */
val NewsArticle.topicName: String
    get() = categoryLabel ?: category.lowercase().replaceFirstChar { it.uppercase() }

/**
 * Show a photo only when the publisher sent one; otherwise the card leads with
 * type (ADR-013). The backend sends "" for no photo, including publishers that
 * forbid hot-linking (ADR-016). `imageUrl` is declared non-null but Gson can
 * still leave it null.
 */
@Suppress("SENSELESS_COMPARISON")
val NewsArticle.showsPhoto: Boolean
    get() = imageUrl != null && imageUrl.isNotBlank()

/** Hide a canned blurb: it reads as a summary of this story and is not one. */
@Suppress("SENSELESS_COMPARISON")
val NewsArticle.showsBlurb: Boolean
    get() = description != null && description.isNotBlank() &&
        descriptionSource !in CANNED_DESCRIPTION_SOURCES

/** "Nov 17 - 12:36" from "Nov 17, 2026 - 12:36": the year is noise on a fresh card. */
internal val NewsArticle.shortTime: String
    get() = publishedAt.replace(Regex(""",\s*\d{4}"""), "")

/** A sheet of paper: rounded 6 dp, hairline edge, the faintest lift. */
@Composable
fun NewsCardFrame(
    modifier: Modifier = Modifier,
    contentPadding: Dp = 20.dp,
    content: @Composable ColumnScope.() -> Unit
) {
    val c = Paper.colors
    Surface(
        modifier = modifier.fillMaxSize(),
        shape = CardShape,
        color = c.card,
        contentColor = c.ink,
        border = BorderStroke(1.dp, c.rule),
        shadowElevation = 1.dp
    ) {
        Column(modifier = Modifier.padding(contentPadding), content = content)
    }
}

/** "SPORTS · SKY SPORTS · NOV 17 - 12:36": topic in its colour, the rest muted. */
@Composable
fun Kicker(article: NewsArticle, withTime: Boolean = true) {
    val topicColour = article.kickerColor()
    val muted = Paper.colors.muted
    val line = remember(article.id, topicColour, muted, withTime) {
        buildAnnotatedString {
            withStyle(SpanStyle(color = topicColour, fontWeight = FontWeight.Bold)) {
                append(article.topicName.uppercase())
            }
            withStyle(SpanStyle(color = muted)) {
                append("  ·  ")
                append(article.source.uppercase())
                if (withTime && article.publishedAt.isNotBlank()) {
                    append("  ·  ")
                    append(article.shortTime.uppercase())
                }
            }
        }
    }
    Text(
        text = line,
        fontFamily = PaperSans,
        fontSize = 11.sp,
        letterSpacing = 0.8.sp,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis
    )
}
