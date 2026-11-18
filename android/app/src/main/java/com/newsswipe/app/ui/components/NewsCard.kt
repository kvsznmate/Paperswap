package com.newsswipe.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.newsswipe.app.data.model.NewsArticle
import com.newsswipe.app.ui.theme.Paper
import com.newsswipe.app.ui.theme.PaperSans
import com.newsswipe.app.ui.theme.PaperSerif

/**
 * Front of a card: the headline leads, a photo follows only if it is real, the
 * publisher's blurb only if it is the publisher's (ADR-013).
 *
 * Without a photo the headline grows and the blurb gets more lines, so a
 * type-only card is a designed state, not a broken one.
 */
@Composable
fun NewsCard(
    article: NewsArticle,
    modifier: Modifier = Modifier,
    /** "12 / 69", or null to leave the position out (the card behind). */
    position: String? = null
) {
    val c = Paper.colors
    val photo = article.showsPhoto

    NewsCardFrame(modifier = modifier) {
        // Everything but the footer. Measured first, so the photo -- the only
        // flexible child -- gives up height on a short screen, never the words.
        Column(
            modifier = Modifier.weight(1f).fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Kicker(article)

            Text(
                text = article.title,
                fontFamily = PaperSerif,
                fontWeight = FontWeight.Bold,
                fontSize = if (photo) 25.sp else 30.sp,
                lineHeight = if (photo) 30.sp else 36.sp,
                letterSpacing = (-0.2).sp,
                color = c.ink,
                maxLines = if (photo) 4 else 7,
                overflow = TextOverflow.Ellipsis
            )

            if (photo) {
                AsyncImage(
                    model = article.imageUrl,
                    contentDescription = null,   // decorative: the headline says it
                    contentScale = ContentScale.Crop,
                    modifier = Modifier
                        .weight(1f, fill = false)
                        .aspectRatio(3f / 2f, matchHeightConstraintsFirst = true)
                        .clip(RoundedCornerShape(3.dp))
                        .background(c.photo)
                )
            }

            if (article.showsBlurb) {
                Text(
                    text = article.description,
                    fontFamily = PaperSerif,
                    fontSize = 15.sp,
                    lineHeight = 22.sp,
                    color = c.ink2,
                    maxLines = if (photo) 3 else 8,
                    overflow = TextOverflow.Ellipsis
                )
            }
        }

        Spacer(Modifier.height(12.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            // Without a hint, tap-to-flip is invisible.
            Text("Tap for more coverage", fontFamily = PaperSans, fontSize = 12.sp, color = c.muted)
            if (position != null) {
                Text(position, fontFamily = PaperSans, fontSize = 12.sp, color = c.muted)
            }
        }
    }
}
