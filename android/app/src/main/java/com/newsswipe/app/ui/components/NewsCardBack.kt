package com.newsswipe.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.HorizontalDivider
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.newsswipe.app.data.model.NewsArticle
import com.newsswipe.app.ui.theme.Paper
import com.newsswipe.app.ui.theme.PaperSans
import com.newsswipe.app.ui.theme.PaperSerif

/**
 * Back of a card: the key sentences from `article.summaryBullets`, picked by the
 * backend enrichment job. They arrive with the feed, so flipping costs no
 * request.
 *
 * Shows ONLY those sentences. When there are none it says so, and does NOT
 * fall back to `article.description`: a publisher teaser under "Key points"
 * would read as extracted content. Empty is normal -- the job runs every 6
 * hours and cannot read paywalled or JS-only pages.
 *
 * No scrolling: a vertical scroll inside a card that is drag-tracked in both
 * axes would fight the swipe. Each point is capped at 5 lines instead.
 */
@Composable
fun NewsCardBack(
    article: NewsArticle,
    modifier: Modifier = Modifier
) {
    val c = Paper.colors
    val mark = article.kickerColor()
    val points = article.summaryBullets.orEmpty().filter { it.isNotBlank() }

    NewsCardFrame(modifier = modifier) {
        Kicker(article, withTime = false)
        Spacer(Modifier.height(12.dp))
        Text(
            text = article.title,
            fontFamily = PaperSerif,
            fontWeight = FontWeight.Bold,
            fontSize = 20.sp,
            lineHeight = 26.sp,
            color = c.ink,
            maxLines = 3,
            overflow = TextOverflow.Ellipsis
        )
        Spacer(Modifier.height(14.dp))
        HorizontalDivider(thickness = 1.dp, color = c.rule)
        Spacer(Modifier.height(12.dp))
        Text(
            text = "KEY POINTS",
            fontFamily = PaperSans,
            fontWeight = FontWeight.Bold,
            fontSize = 11.sp,
            letterSpacing = 1.1.sp,
            color = c.muted
        )
        Spacer(Modifier.height(12.dp))

        if (points.isNotEmpty()) {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                points.forEach { point ->
                    Row {
                        // A square, not a dot: the print bullet. Nudged onto the first line.
                        Spacer(
                            Modifier
                                .padding(top = 9.dp)
                                .size(6.dp)
                                .background(mark)
                        )
                        Spacer(Modifier.width(12.dp))
                        Text(
                            text = point,
                            fontFamily = PaperSerif,
                            fontSize = 16.sp,
                            lineHeight = 24.sp,
                            color = c.ink,
                            maxLines = 5,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }
            }
            Spacer(Modifier.height(10.dp))
            Text(
                "Sentences taken from the article itself.",
                fontFamily = PaperSans, fontSize = 12.sp, color = c.muted
            )
        } else {
            Text("No key points yet.", fontFamily = PaperSerif, fontSize = 16.sp, color = c.ink)
            Spacer(Modifier.height(6.dp))
            Text(
                "PaperSwap reads the article every 6 hours and picks its key " +
                    "sentences. New stories and paywalled pages stay empty.",
                fontFamily = PaperSans,
                fontSize = 13.sp,
                lineHeight = 19.sp,
                color = c.muted
            )
        }

        Spacer(Modifier.weight(1f))
        Text("Tap to turn back", fontFamily = PaperSans, fontSize = 12.sp, color = c.muted)
    }
}
