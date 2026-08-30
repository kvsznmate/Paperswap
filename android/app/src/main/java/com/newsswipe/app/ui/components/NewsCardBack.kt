package com.newsswipe.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.newsswipe.app.data.model.NewsArticle
import com.newsswipe.app.ui.theme.*

/**
 * The reverse face of a news card: extractive summary bullets.
 *
 * Deliberately shares the front's shell -- same 28dp radius, border, gradient
 * and padding -- because during the flip both faces are briefly visible edge-on
 * and a mismatched outline reads as two different cards rather than one turning.
 *
 * Content is sized against the measured corpus rather than guessed: bullets
 * average ~150 characters and 2.99 per article, so a typical back holds ~450
 * characters. MAX_SENTENCE_CHARS caps a single bullet at 320, which would only
 * overflow if all three hit the ceiling at once -- not observed. maxLines is the
 * backstop. There is intentionally no scrolling: a scroll container inside a
 * horizontally-swipeable card fights the drag handler for every vertical gesture.
 */
@Composable
fun NewsCardBack(
    article: NewsArticle,
    modifier: Modifier = Modifier
) {
    val fallbackAccent = if (article.category.equals("TECH", ignoreCase = true)) {
        TechIndigo
    } else {
        FinanceEmerald
    }
    val accentColor = parseAccent(article.accentColor, fallbackAccent)
    val categoryLabel = (article.categoryLabel ?: article.category).uppercase()
    val bullets = article.summaryBullets.orEmpty()

    Card(
        modifier = modifier
            .fillMaxSize()
            .border(1.5.dp, CardBorder, RoundedCornerShape(28.dp)),
        shape = RoundedCornerShape(28.dp),
        colors = CardDefaults.cardColors(containerColor = CardBackground),
        elevation = CardDefaults.cardElevation(defaultElevation = 12.dp)
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    Brush.verticalGradient(
                        colors = listOf(Color(0xFF101522), Color(0xFF0D121B))
                    )
                )
                .padding(20.dp)
        ) {
            Column(modifier = Modifier.fillMaxSize()) {

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(20.dp))
                            .background(accentColor)
                            .padding(horizontal = 14.dp, vertical = 6.dp)
                    ) {
                        Text(
                            text = categoryLabel,
                            color = Color.White,
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

                Spacer(modifier = Modifier.height(18.dp))

                Text(
                    text = article.title,
                    color = TextPrimary,
                    fontSize = 17.sp,
                    fontWeight = FontWeight.Bold,
                    lineHeight = 23.sp,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis
                )

                Spacer(modifier = Modifier.height(16.dp))

                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(1.dp)
                        .background(CardBorder)
                )

                Spacer(modifier = Modifier.height(18.dp))

                Column(modifier = Modifier.weight(1f)) {
                    if (bullets.isEmpty()) {
                        // Reached whenever enrichment has not run for this row, or
                        // ran and could not read the page -- a paywall, a bot
                        // challenge, a JS shell. Roughly 1 article in 80 at the
                        // current extraction rate, so it is rare but not rare
                        // enough to leave as a blank panel.
                        Box(
                            modifier = Modifier.fillMaxSize(),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                text = "No summary available\nfor this article",
                                color = TextMuted,
                                fontSize = 13.sp,
                                lineHeight = 20.sp,
                                fontWeight = FontWeight.Medium
                            )
                        }
                    } else {
                        bullets.forEachIndexed { index, bullet ->
                            Row(modifier = Modifier.fillMaxWidth()) {
                                // Marker sits on its own baseline-ish offset rather
                                // than being prefixed into the string, so wrapped
                                // lines indent under the text instead of under the dot.
                                Box(
                                    modifier = Modifier
                                        .padding(top = 7.dp)
                                        .size(6.dp)
                                        .clip(RoundedCornerShape(3.dp))
                                        .background(accentColor)
                                )
                                Spacer(modifier = Modifier.width(12.dp))
                                Text(
                                    text = bullet,
                                    color = TextSecondary,
                                    fontSize = 13.sp,
                                    lineHeight = 19.sp,
                                    maxLines = 6,
                                    overflow = TextOverflow.Ellipsis
                                )
                            }
                            if (index != bullets.lastIndex) {
                                Spacer(modifier = Modifier.height(14.dp))
                            }
                        }
                    }
                }

                Spacer(modifier = Modifier.height(10.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "TAP TO FLIP BACK",
                        color = TextMuted,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Bold
                    )
                    Text(
                        text = "SWIPE TO DECIDE",
                        color = TextMuted,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Bold
                    )
                }
            }
        }
    }
}
