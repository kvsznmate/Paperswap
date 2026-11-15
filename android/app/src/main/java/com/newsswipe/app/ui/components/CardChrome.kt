package com.newsswipe.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.newsswipe.app.data.model.NewsArticle
import com.newsswipe.app.ui.theme.CardBackground
import com.newsswipe.app.ui.theme.CardBorder
import com.newsswipe.app.ui.theme.TextSecondary
import com.newsswipe.app.ui.theme.contrastOn
import com.newsswipe.app.ui.theme.parseAccent

// Shared by both faces of a news card, so a flipped card visibly stays the same
// article: same frame, same topic badge, same source line.

private val CardShape = RoundedCornerShape(28.dp)

/** The article's topic colour, as sent by the backend (ADR-006). */
val NewsArticle.accent: Color get() = parseAccent(accentColor)

/** The article's topic name for the badge. */
val NewsArticle.badgeLabel: String get() = (categoryLabel ?: category).uppercase()

/** Rounded, bordered card with the dark gradient fill. */
@Composable
fun NewsCardFrame(
    modifier: Modifier = Modifier,
    contentPadding: Dp = 20.dp,
    content: @Composable ColumnScope.() -> Unit
) {
    Card(
        modifier = modifier
            .fillMaxSize()
            .border(1.5.dp, CardBorder, CardShape),
        shape = CardShape,
        colors = CardDefaults.cardColors(containerColor = CardBackground),
        elevation = CardDefaults.cardElevation(defaultElevation = 12.dp)
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(Brush.verticalGradient(listOf(Color(0xFF101522), Color(0xFF0D121B))))
                .padding(contentPadding)
        ) {
            Column(modifier = Modifier.fillMaxSize(), content = content)
        }
    }
}

/** Topic badge on the left, source name on the right. */
@Composable
fun NewsCardHeader(article: NewsArticle) {
    val accent = article.accent
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(20.dp))
                .background(accent)
                .padding(horizontal = 14.dp, vertical = 6.dp)
        ) {
            Text(
                text = article.badgeLabel,
                // Computed, not fixed white: light accents such as amber need dark text.
                color = contrastOn(accent),
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 0.5.sp
            )
        }

        Text(
            text = article.source,
            color = TextSecondary,
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium
        )
    }
}
