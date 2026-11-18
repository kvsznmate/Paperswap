package com.newsswipe.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.newsswipe.app.data.model.NewsArticle
import com.newsswipe.app.data.model.RelatedArticle
import com.newsswipe.app.openArticle
import com.newsswipe.app.ui.theme.Paper
import com.newsswipe.app.ui.theme.PaperSans
import com.newsswipe.app.ui.theme.PaperSerif

/**
 * Back of a card: the publisher's own description, then the same story as
 * other publishers covered it (`article.related`). Both arrive with the feed,
 * so flipping costs no request.
 *
 * Nothing here is copied from the article page. Paperswap does not read
 * article pages at all (ADR-015): the description is what the publisher put in
 * its feed for apps to show, and related coverage is the backend matching its
 * own article embeddings, shown as headline + publisher + link.
 *
 * A related row opens that article. Its click consumes the tap, so it does not
 * also turn the card back over; a drag still swipes.
 *
 * No scrolling: a vertical scroll inside a card that is drag-tracked in both
 * axes would fight the swipe. Text is capped by lines instead.
 */
@Composable
fun NewsCardBack(
    article: NewsArticle,
    modifier: Modifier = Modifier
) {
    val c = Paper.colors
    val related = article.related.orEmpty().filter { it.title.isNotBlank() && it.url.isNotBlank() }

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

        if (article.showsBlurb) {
            Spacer(Modifier.height(10.dp))
            Text(
                text = article.description,
                fontFamily = PaperSerif,
                fontSize = 15.sp,
                lineHeight = 22.sp,
                color = c.ink2,
                maxLines = 5,
                overflow = TextOverflow.Ellipsis
            )
        }

        Spacer(Modifier.height(14.dp))
        HorizontalDivider(thickness = 1.dp, color = c.rule)
        Spacer(Modifier.height(12.dp))
        SectionLabel("ALSO COVERED BY")
        Spacer(Modifier.height(10.dp))

        if (related.isNotEmpty()) {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                related.forEach { RelatedRow(it, article) }
            }
        } else {
            Text("No other publisher yet.", fontFamily = PaperSerif, fontSize = 16.sp, color = c.ink)
            Spacer(Modifier.height(6.dp))
            Text(
                "Stories are matched across publishers every 6 hours. Most are " +
                    "covered by one outlet only.",
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

@Composable
private fun SectionLabel(text: String) {
    Text(
        text = text,
        fontFamily = PaperSans,
        fontWeight = FontWeight.Bold,
        fontSize = 11.sp,
        letterSpacing = 1.1.sp,
        color = Paper.colors.muted
    )
}

@Composable
private fun RelatedRow(item: RelatedArticle, parent: NewsArticle) {
    val c = Paper.colors
    val context = LocalContext.current
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(role = Role.Button, onClickLabel = "Open at ${item.source}") {
                openArticle(context, item.url)
            }
            .padding(vertical = 6.dp)
    ) {
        // A square, not a dot: the print bullet, in the topic colour.
        Spacer(
            Modifier
                .padding(top = 7.dp)
                .size(6.dp)
                .background(parent.kickerColor())
        )
        Spacer(Modifier.width(12.dp))
        Column {
            Text(
                text = item.source.uppercase(),
                fontFamily = PaperSans,
                fontWeight = FontWeight.Bold,
                fontSize = 11.sp,
                letterSpacing = 0.8.sp,
                color = c.muted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
            Text(
                text = item.title,
                fontFamily = PaperSerif,
                fontSize = 15.sp,
                lineHeight = 21.sp,
                color = c.ink,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis
            )
        }
    }
}
