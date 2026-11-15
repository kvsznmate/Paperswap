package com.newsswipe.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.newsswipe.app.data.model.NewsArticle
import com.newsswipe.app.ui.theme.TextMuted
import com.newsswipe.app.ui.theme.TextPrimary
import com.newsswipe.app.ui.theme.TextSecondary

/**
 * Back face of a news card: the key sentences from `article.summaryBullets`,
 * picked by the backend enrichment job. They arrive with the feed, so flipping
 * costs no request.
 *
 * Shows ONLY those bullets. When there are none it says so, and does NOT fall
 * back to `article.description`: a two-sentence publisher teaser under a
 * "SUMMARY" heading would read as generated output. Empty is normal -- the job
 * runs every 6 hours and cannot read paywalled or JS-only pages.
 *
 * No image and no scrolling: the flip is about the words, and a vertical scroll
 * inside a card that is drag-tracked in both axes would fight the swipe.
 */
@Composable
fun NewsCardBack(
    article: NewsArticle,
    modifier: Modifier = Modifier
) {
    val accent = article.accent
    val bullets = article.summaryBullets.orEmpty().filter { it.isNotBlank() }

    NewsCardFrame(modifier = modifier, contentPadding = 22.dp) {
        NewsCardHeader(article)

        Spacer(modifier = Modifier.height(20.dp))

        Text(
            text = article.title,
            color = TextPrimary,
            fontSize = 17.sp,
            fontWeight = FontWeight.Bold,
            lineHeight = 23.sp,
            maxLines = 3,
            overflow = TextOverflow.Ellipsis
        )

        Spacer(modifier = Modifier.height(18.dp))

        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(width = 26.dp, height = 3.dp)
                    .clip(RoundedCornerShape(2.dp))
                    .background(accent)
            )
            Spacer(modifier = Modifier.width(9.dp))
            Text(
                text = "SUMMARY",
                color = TextMuted,
                fontSize = 10.sp,
                fontWeight = FontWeight.ExtraBold,
                letterSpacing = 1.5.sp
            )
        }

        Spacer(modifier = Modifier.height(14.dp))

        if (bullets.isNotEmpty()) {
            bullets.forEach { bullet ->
                Row(
                    modifier = Modifier.padding(bottom = 12.dp),
                    verticalAlignment = Alignment.Top
                ) {
                    // Nudged down to sit on the first line rather than its ascender.
                    Box(
                        modifier = Modifier
                            .padding(top = 8.dp)
                            .size(6.dp)
                            .clip(RoundedCornerShape(3.dp))
                            .background(accent)
                    )
                    Spacer(modifier = Modifier.width(12.dp))
                    Text(
                        text = bullet,
                        color = Color(0xFFDBE2EC),
                        fontSize = 15.sp,
                        lineHeight = 22.sp,
                        maxLines = 6,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
        } else {
            Text(
                text = "Summary not generated yet.",
                color = TextSecondary,
                fontSize = 15.sp,
                fontWeight = FontWeight.SemiBold
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = "The enrichment job extracts the article body and " +
                    "writes key points every 6 hours. Recent stories, and " +
                    "pages it cannot read, stay blank here.",
                color = TextMuted,
                fontSize = 12.sp,
                lineHeight = 18.sp
            )
        }

        Spacer(modifier = Modifier.weight(1f))

        Text(
            text = article.publishedAt,
            color = TextMuted,
            fontSize = 11.sp
        )

        Spacer(modifier = Modifier.height(12.dp))

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "↺ TAP TO FLIP BACK",
                color = TextMuted,
                fontSize = 10.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 0.5.sp
            )
            Text(
                text = "SWIPE RIGHT TO READ →",
                color = accent,
                fontSize = 10.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 0.5.sp
            )
        }
    }
}
