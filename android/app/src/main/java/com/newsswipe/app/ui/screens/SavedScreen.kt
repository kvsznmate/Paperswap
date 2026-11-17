package com.newsswipe.app.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.newsswipe.app.data.model.SavedArticle
import com.newsswipe.app.ui.components.CardShape
import com.newsswipe.app.ui.components.CloseIcon
import com.newsswipe.app.ui.components.Masthead
import com.newsswipe.app.ui.theme.Paper
import com.newsswipe.app.ui.theme.PaperSans
import com.newsswipe.app.ui.theme.PaperSerif
import com.newsswipe.app.ui.theme.parseAccent
import com.newsswipe.app.ui.theme.readableOn
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Saved for later: kept on this phone only, newest first. Each entry is a copy
 * (headline, link, source), so it stays after the server deletes the article.
 */
@Composable
fun SavedScreen(
    saved: List<SavedArticle>,
    onOpen: (SavedArticle) -> Unit,
    onRemove: (SavedArticle) -> Unit,
    modifier: Modifier = Modifier
) {
    val c = Paper.colors
    val count = saved.size

    Column(modifier = modifier.fillMaxSize()) {
        Masthead(
            title = "Saved",
            subtitle = if (count == 1) "1 article · kept on this phone" else "$count articles · kept on this phone"
        )

        if (saved.isEmpty()) {
            Box(Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
                Message(
                    title = "Nothing saved yet",
                    body = "Tap the bookmark under a card to keep it here. Saved stories " +
                        "stay on this phone, even after the server deletes them."
                )
            }
            return@Column
        }

        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, bottom = 20.dp)
        ) {
            items(saved, key = { it.id }) { item ->
                SavedRow(item, onOpen = { onOpen(item) }, onRemove = { onRemove(item) })
            }
            item {
                Text(
                    "The server deletes articles after 9 days. Saved keeps its own copy " +
                        "of each headline and link, so nothing here disappears with them.",
                    fontFamily = PaperSans,
                    fontSize = 13.sp,
                    lineHeight = 19.sp,
                    color = c.muted,
                    modifier = Modifier.padding(top = 16.dp)
                )
            }
        }
    }
}

@Composable
private fun SavedRow(item: SavedArticle, onOpen: () -> Unit, onRemove: () -> Unit) {
    val c = Paper.colors
    val topicColour = remember(item.accentColor, c.ground) { readableOn(parseAccent(item.accentColor), c.ground) }
    val kicker = remember(item.id, topicColour) {
        buildAnnotatedString {
            withStyle(SpanStyle(color = topicColour, fontWeight = FontWeight.Bold)) {
                append((item.categoryLabel ?: item.category.orEmpty()).uppercase())
            }
            withStyle(SpanStyle(color = c.muted)) {
                append("  ·  ")
                append(item.source.orEmpty().uppercase())
            }
        }
    }

    Column {
        Row(
            modifier = Modifier.fillMaxWidth().padding(vertical = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.Top
        ) {
            Column(
                modifier = Modifier.weight(1f).clickable(onClick = onOpen),
                verticalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                Text(kicker, fontFamily = PaperSans, fontSize = 11.sp, letterSpacing = 0.8.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(
                    item.title.orEmpty(),
                    fontFamily = PaperSerif,
                    fontWeight = FontWeight.Bold,
                    fontSize = 18.sp,
                    lineHeight = 24.sp,
                    color = c.ink
                )
                Text(savedLine(item.savedAtMs), fontFamily = PaperSans, fontSize = 12.sp, color = c.muted)
            }
            OutlinedButton(
                onClick = onRemove,
                shape = CardShape,
                border = BorderStroke(1.dp, c.rule),
                contentPadding = PaddingValues(0.dp),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = c.muted),
                modifier = Modifier.size(44.dp).semantics { contentDescription = "Remove from Saved" }
            ) {
                Icon(CloseIcon, contentDescription = null, modifier = Modifier.size(18.dp))
            }
        }
        HorizontalDivider(thickness = 1.dp, color = c.rule)
    }
}

private fun savedLine(ms: Long): String =
    "Saved " + SimpleDateFormat("EEE d MMM", Locale.getDefault()).format(Date(ms))
