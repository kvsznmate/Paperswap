package com.newsswipe.app.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.newsswipe.app.data.model.TopicSummary
import com.newsswipe.app.ui.components.CardShape
import com.newsswipe.app.ui.components.Masthead
import com.newsswipe.app.ui.theme.Paper
import com.newsswipe.app.ui.theme.PaperSans
import com.newsswipe.app.ui.theme.PaperSerif
import com.newsswipe.app.ui.theme.parseAccent
import com.newsswipe.app.ui.theme.readableOn
import com.newsswipe.app.viewmodel.WeekUiState
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/**
 * "The week": every topic's digest for one week on one scroll, like an edition.
 *
 * It replaced a row of two-letter topic circles that showed nothing until one
 * was tapped. stories:v2 writes one line per story -- "• sentence (coverage)"
 * -- so each line is laid out as a story with its coverage under it. Prose
 * from another generator (anthropic, extractive:v1) is shown as a paragraph.
 *
 * Both counts stay visible per topic (see TopicSummary): what the generator
 * read, and what the topic collected.
 */
@Composable
fun WeekScreen(
    state: WeekUiState,
    onStepWeek: (Int) -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier
) {
    val c = Paper.colors

    Column(modifier = modifier.fillMaxSize()) {
        Masthead(
            title = "The week",
            subtitle = "Most covered first"
        ) {
            WeekStepper(state, onStepWeek)
        }

        when {
            state.loading && !state.loaded -> Centered { CircularProgressIndicator(color = c.ink) }

            state.error != null && !state.loaded -> Centered {
                Message(
                    title = "Could not load the week",
                    body = state.error,
                    action = "Retry",
                    onAction = onRetry
                )
            }

            state.loaded && state.summaries.isEmpty() -> Centered {
                Message(
                    title = if (state.weekStart == null) "No summaries yet" else "Nothing for this week",
                    body = "Summaries are written once a week, on Monday, for the week " +
                        "before. Each topic needs at least a few articles with a real " +
                        "publisher description."
                )
            }

            else -> Edition(state)
        }
    }
}

@Composable
private fun WeekStepper(state: WeekUiState, onStepWeek: (Int) -> Unit) {
    val c = Paper.colors
    Row(verticalAlignment = Alignment.CenterVertically) {
        StepButton("‹", "Previous week", state.hasEarlier && !state.loading) { onStepWeek(-1) }
        Text(
            text = state.weekStart?.let { formatWeekRange(it, state.weekEnd) } ?: "",
            fontFamily = PaperSans,
            fontWeight = FontWeight.Medium,
            fontSize = 13.sp,
            color = c.ink,
            textAlign = TextAlign.Center,
            modifier = Modifier.widthIn(min = 78.dp)
        )
        StepButton("›", "Next week", state.hasLater && !state.loading) { onStepWeek(1) }
    }
}

@Composable
private fun StepButton(glyph: String, label: String, enabled: Boolean, onClick: () -> Unit) {
    val c = Paper.colors
    OutlinedButton(
        onClick = onClick,
        enabled = enabled,
        shape = CardShape,
        border = BorderStroke(1.dp, c.rule),
        contentPadding = PaddingValues(0.dp),
        colors = ButtonDefaults.outlinedButtonColors(
            containerColor = c.card, contentColor = c.ink, disabledContentColor = c.rule
        ),
        modifier = Modifier.size(44.dp).semantics { contentDescription = label }
    ) {
        Text(glyph, fontSize = 20.sp)
    }
}

/** One section per catalogue topic, in the server's order; then any extra rows. */
@Composable
private fun Edition(state: WeekUiState) {
    val sections = remember(state.summaries, state.topicsMissing, state.catalogue) {
        val bySlug = state.summaries.associateBy { it.topic }
        val ordered = state.catalogue.map { cat ->
            Section(cat.label, cat.accentColor, bySlug[cat.slug])
        }
        val known = state.catalogue.map { it.slug }.toSet()
        val extra = state.summaries.filter { it.topic !in known }
            .map { Section(it.topicLabel, it.accentColor, it) }
        // Before the catalogue arrives, show what we have rather than nothing.
        if (ordered.isEmpty()) extra else ordered + extra
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 4.dp, bottom = 20.dp)
    ) {
        items(sections, key = { it.label }) { section -> TopicSection(section) }
        item {
            Text(
                "Grouped into stories from the week’s articles. Each line is a " +
                    "sentence from the most central article of its story. Past weeks " +
                    "stay here after the articles themselves are deleted.",
                fontFamily = PaperSans,
                fontSize = 12.sp,
                lineHeight = 18.sp,
                color = Paper.colors.muted,
                modifier = Modifier.padding(top = 14.dp)
            )
        }
    }
}

private data class Section(val label: String, val accentColor: String?, val summary: TopicSummary?)

@Composable
private fun TopicSection(section: Section) {
    val c = Paper.colors
    val colour = remember(section.accentColor, c.card) { readableOn(parseAccent(section.accentColor), c.ground) }
    val summary = section.summary

    Column(modifier = Modifier.fillMaxWidth().padding(top = 16.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.Bottom
        ) {
            Text(
                section.label.uppercase(),
                fontFamily = PaperSans,
                fontWeight = FontWeight.Bold,
                fontSize = 12.sp,
                letterSpacing = 1.2.sp,
                color = colour,
                modifier = Modifier.semantics { heading() }
            )
            if (summary != null) {
                Text(
                    "${summary.numberOfArticles} articles",
                    fontFamily = PaperSans, fontSize = 12.sp, color = c.muted
                )
            }
        }
        Spacer(Modifier.height(8.dp))

        if (summary == null) {
            Text(
                "No digest this week: too few articles with a real publisher description.",
                fontFamily = PaperSans, fontSize = 13.sp, lineHeight = 19.sp, color = c.muted
            )
        } else {
            val parsed = remember(summary.summary) { parseDigest(summary.summary) }
            if (parsed.stories.isEmpty()) {
                Text(
                    summary.summary,
                    fontFamily = PaperSerif, fontSize = 16.sp, lineHeight = 24.sp, color = c.ink
                )
            } else {
                parsed.stories.forEach { story ->
                    Row(modifier = Modifier.padding(bottom = 10.dp)) {
                        Box(Modifier.padding(top = 9.dp).size(5.dp).background(colour))
                        Spacer(Modifier.width(10.dp))
                        Column {
                            Text(story.text, fontFamily = PaperSerif, fontSize = 16.sp, lineHeight = 23.sp, color = c.ink)
                            if (story.coverage != null) {
                                Text(story.coverage, fontFamily = PaperSans, fontSize = 12.sp, color = c.muted)
                            }
                        }
                    }
                }
            }
            Spacer(Modifier.height(2.dp))
            Text(
                provenance(summary),
                fontFamily = PaperSans, fontSize = 11.sp, lineHeight = 16.sp, color = c.muted
            )
        }
        Spacer(Modifier.height(14.dp))
        HorizontalDivider(thickness = 1.dp, color = c.rule)
    }
}

/** "Read 18 of 25 collected articles · written by stories:v2". Both counts, always. */
private fun provenance(s: TopicSummary): String {
    val counts = if (s.wasFiltered) {
        "Read ${s.numberOfArticles} of ${s.articlesInWindow} collected articles; the rest had no real description"
    } else {
        "Read all ${s.numberOfArticles} collected articles"
    }
    return "$counts · written by ${s.generator}"
}

internal data class Story(val text: String, val coverage: String?)
internal data class Digest(val intro: String?, val stories: List<Story>)

/**
 * Split a stories:v2 digest into its lines. Each story line is
 * "• sentence (coverage)"; the coverage is the last parenthesised group.
 * Anything without "• " lines comes back with no stories and is shown whole.
 */
internal fun parseDigest(text: String): Digest {
    val lines = text.lines().map { it.trim() }.filter { it.isNotEmpty() }
    val storyLines = lines.filter { it.startsWith("• ") }
    if (storyLines.isEmpty()) return Digest(null, emptyList())
    val stories = storyLines.map { line ->
        val body = line.removePrefix("• ").trim()
        val open = body.lastIndexOf(" (")
        if (body.endsWith(")") && open > 0) {
            Story(body.substring(0, open), body.substring(open + 2, body.length - 1))
        } else {
            Story(body, null)
        }
    }
    return Digest(lines.firstOrNull { !it.startsWith("• ") }, stories)
}

/**
 * "10–16 Nov" from ISO dates; with the year when the week spans two.
 *
 * SimpleDateFormat rather than java.time: minSdk is 24 and core library
 * desugaring is not enabled, so LocalDate would compile and then crash on API
 * 24-25. Pinned to UTC, because the backend anchors week boundaries to UTC and
 * a negative-offset zone would otherwise render the day before.
 *
 * Falls back to the raw ISO string rather than throwing.
 */
internal fun formatWeekRange(startIso: String, endIso: String?): String = runCatching {
    val utc = TimeZone.getTimeZone("UTC")
    val parser = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { timeZone = utc }
    val start = parser.parse(startIso) ?: return startIso
    val end = endIso?.let { parser.parse(it) } ?: return startIso
    val day = SimpleDateFormat("d", Locale.getDefault()).apply { timeZone = utc }
    val dayMonth = SimpleDateFormat("d MMM", Locale.getDefault()).apply { timeZone = utc }
    val sameMonth = SimpleDateFormat("yyyyMM", Locale.US).apply { timeZone = utc }
    if (sameMonth.format(start) == sameMonth.format(end)) {
        "${day.format(start)}–${dayMonth.format(end)}"
    } else {
        "${dayMonth.format(start)} – ${dayMonth.format(end)}"
    }
}.getOrDefault(startIso)

@Composable
private fun Centered(content: @Composable () -> Unit) {
    Box(Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) { content() }
}

@Composable
internal fun Message(title: String, body: String, action: String? = null, onAction: () -> Unit = {}) {
    val c = Paper.colors
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(title, fontFamily = PaperSerif, fontWeight = FontWeight.Bold, fontSize = 22.sp, lineHeight = 28.sp, color = c.ink)
        Text(body, fontFamily = PaperSans, fontSize = 14.sp, lineHeight = 21.sp, color = c.muted)
        if (action != null) {
            Button(
                onClick = onAction,
                shape = CardShape,
                colors = ButtonDefaults.buttonColors(containerColor = c.ink, contentColor = c.onInk),
                modifier = Modifier.height(48.dp)
            ) {
                Text(action, fontFamily = PaperSans, fontWeight = FontWeight.Bold)
            }
        }
    }
}
