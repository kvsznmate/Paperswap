package com.newsswipe.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.newsswipe.app.data.model.NewsArticle
import com.newsswipe.app.ui.theme.TextMuted
import com.newsswipe.app.ui.theme.TextPrimary
import com.newsswipe.app.ui.theme.TextSecondary

/** Front face of a news card: image, headline, publisher blurb. */
@Composable
fun NewsCard(
    article: NewsArticle,
    modifier: Modifier = Modifier
) {
    val accent = article.accent

    NewsCardFrame(modifier = modifier) {
        NewsCardHeader(article)

        Spacer(modifier = Modifier.height(14.dp))

        // Hero image. weight(1f) so the image, not the text, gives up space when
        // the card is short -- with a fixed image height the description below
        // was squeezed to nothing.
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f)
                .clip(RoundedCornerShape(20.dp))
                .border(1.dp, Color(0xFF2C364A), RoundedCornerShape(20.dp))
        ) {
            AsyncImage(
                model = article.imageUrl,
                contentDescription = article.title,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize()
            )

            Box(
                modifier = Modifier
                    .align(Alignment.BottomStart)
                    .padding(10.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(Color(0xCC090C15))
                    .padding(horizontal = 10.dp, vertical = 4.dp)
            ) {
                Text(
                    text = article.publishedAt,
                    color = TextSecondary,
                    fontSize = 11.sp
                )
            }
        }

        Spacer(modifier = Modifier.height(16.dp))

        Text(
            text = article.title,
            color = TextPrimary,
            fontSize = 20.sp,
            fontWeight = FontWeight.Bold,
            lineHeight = 26.sp,
            maxLines = 3,
            overflow = TextOverflow.Ellipsis
        )

        Spacer(modifier = Modifier.height(10.dp))

        // Fixed line budget and no weight, so the blurb always gets its space.
        // The enrichment bullets live on the back of the card.
        Text(
            text = article.description,
            color = TextSecondary,
            fontSize = 14.sp,
            lineHeight = 20.sp,
            maxLines = 4,
            overflow = TextOverflow.Ellipsis
        )

        Spacer(modifier = Modifier.height(10.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(width = 40.dp, height = 3.dp)
                        .clip(RoundedCornerShape(2.dp))
                        .background(accent)
                )
                Spacer(modifier = Modifier.width(8.dp))
                // Without a hint, tap-to-flip is invisible.
                Text(
                    text = "TAP FOR SUMMARY",
                    color = TextMuted,
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold
                )
            }

            Text(
                text = "READ FULL STORY →",
                color = accent,
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold
            )
        }
    }
}
