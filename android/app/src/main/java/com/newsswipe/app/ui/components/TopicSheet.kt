package com.newsswipe.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.newsswipe.app.BuildConfig
import com.newsswipe.app.openArticle
import com.newsswipe.app.data.model.NewsCategory
import com.newsswipe.app.ui.theme.Paper
import com.newsswipe.app.ui.theme.PaperSans
import com.newsswipe.app.ui.theme.PaperSerif
import com.newsswipe.app.ui.theme.parseAccent
import com.newsswipe.app.ui.theme.readableOn

/**
 * "Show these topics": a checklist over the server's catalogue (ADR-006), so a
 * topic added on the server appears here without an app release.
 *
 * Works on a copy and applies on the button, so ticking three boxes reloads
 * the deck once, not three times. Dismissing the sheet discards the copy.
 * `selected` empty means every topic.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TopicSheet(
    catalogue: List<NewsCategory>,
    selected: Set<String>,
    onApply: (Set<String>) -> Unit,
    onDismiss: () -> Unit
) {
    val c = Paper.colors
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var draft by remember(catalogue, selected) {
        mutableStateOf(if (selected.isEmpty()) catalogue.map { it.slug }.toSet() else selected)
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = c.card,
        contentColor = c.ink,
        shape = RoundedCornerShape(topStart = 12.dp, topEnd = 12.dp)
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp)
                .navigationBarsPadding()
                .padding(bottom = 16.dp)
        ) {
            Text("Show these topics", fontFamily = PaperSerif, fontWeight = FontWeight.Bold, fontSize = 22.sp, color = c.ink)
            Spacer(Modifier.height(4.dp))
            Text(
                "Kept on this phone. The deck reloads with your choice.",
                fontFamily = PaperSans, fontSize = 13.sp, color = c.muted
            )
            Spacer(Modifier.height(10.dp))

            if (catalogue.isEmpty()) {
                Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = c.ink)
                }
            }

            catalogue.forEach { topic ->
                val on = topic.slug in draft
                val dot = remember(topic.accentColor, c.card) { readableOn(parseAccent(topic.accentColor), c.card) }
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 48.dp)
                        .toggleable(value = on, role = Role.Checkbox) {
                            draft = if (on) draft - topic.slug else draft + topic.slug
                        },
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Box(Modifier.size(10.dp).background(dot, CircleShape))
                    Text(
                        topic.label,
                        fontFamily = PaperSans,
                        fontSize = 15.sp,
                        color = if (on) c.ink else c.muted,
                        modifier = Modifier.weight(1f)
                    )
                    Checkbox(
                        checked = on,
                        onCheckedChange = null,   // the whole row toggles
                        colors = CheckboxDefaults.colors(
                            checkedColor = c.ink, uncheckedColor = c.muted, checkmarkColor = c.onInk
                        )
                    )
                }
                HorizontalDivider(thickness = 1.dp, color = c.rule)
            }

            Spacer(Modifier.height(14.dp))
            Button(
                onClick = { onApply(draft) },
                enabled = draft.isNotEmpty(),
                shape = CardShape,
                colors = ButtonDefaults.buttonColors(containerColor = c.ink, contentColor = c.onInk),
                modifier = Modifier.fillMaxWidth().height(50.dp)
            ) {
                Text(
                    if (draft.isEmpty()) "Pick at least one topic"
                    else if (draft.size == catalogue.size) "Show all topics"
                    else "Show ${draft.size} topics",
                    fontFamily = PaperSans, fontWeight = FontWeight.Bold, fontSize = 16.sp
                )
            }

            // The project's standing with the publishers (#72), stated once
            // where a reader looks for settings, not on every card.
            Spacer(Modifier.height(16.dp))
            Text(
                "PaperSwap is a non-commercial demo project, not affiliated with any publisher. Stories, headlines and descriptions belong to their publishers, and every card links to the original.",
                fontFamily = PaperSans,
                fontSize = 12.sp,
                lineHeight = 17.sp,
                color = c.muted
            )
            // The privacy policy (#67) is served by the backend, so it always
            // states the retention windows the server actually runs with.
            val context = LocalContext.current
            Text(
                "Privacy",
                fontFamily = PaperSans,
                fontSize = 12.sp,
                color = c.muted,
                textDecoration = TextDecoration.Underline,
                modifier = Modifier
                    .clickable(role = Role.Button) {
                        openArticle(context, BuildConfig.BASE_URL.trimEnd('/') + "/privacy")
                    }
                    .padding(vertical = 12.dp)
            )
        }
    }
}
