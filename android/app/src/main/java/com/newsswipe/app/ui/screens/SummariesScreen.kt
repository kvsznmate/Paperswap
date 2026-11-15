package com.newsswipe.app.ui.screens

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.newsswipe.app.data.model.NewsCategory
import com.newsswipe.app.data.model.TopicSummary
import com.newsswipe.app.ui.theme.TechIndigo
import com.newsswipe.app.ui.theme.TextMuted
import com.newsswipe.app.ui.theme.TextPrimary
import com.newsswipe.app.ui.theme.TextSecondary
import com.newsswipe.app.ui.theme.contrastOn
import com.newsswipe.app.ui.theme.parseAccent
import com.newsswipe.app.viewmodel.SummaryUiState
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/**
 * Weekly topic summaries.
 *
 * Flow: pick a topic from the circles, then read that topic's most recent week.
 * The circles stay on screen so switching topics is one tap rather than a tap
 * and a back press.
 *
 * Stateless by design -- everything comes in as parameters. That keeps the
 * ViewModel out of the composables and makes each state (loading, error, empty,
 * loaded) previewable on its own.
 */
@Composable
fun SummariesScreen(
    state: SummaryUiState,
    onSelectTopic: (String) -> Unit,
    onStepWeek: (Int) -> Unit,
    onRetryTopics: () -> Unit,
    onRetryDetail: () -> Unit,
    modifier: Modifier = Modifier
) {
    // Bound to locals up front rather than read through `state` inside the when.
    // currentWeek is a computed getter and topicsError/detailError/selectedTopic
    // are read across branches -- Kotlin will not smart-cast either case, so
    // relying on it here would be a compile error waiting on a refactor.
    val topicsError = state.topicsError
    val detailError = state.detailError
    val selectedTopic = state.selectedTopic
    val week = state.currentWeek

    Column(modifier = modifier.fillMaxSize()) {

        when {
            state.topicsLoading && state.topics.isEmpty() -> {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = TechIndigo)
                }
            }

            topicsError != null && state.topics.isEmpty() -> {
                ErrorBlock(
                    title = "Could not load topics",
                    message = topicsError,
                    onRetry = onRetryTopics
                )
            }

            else -> {
                TopicCircleRow(
                    topics = state.topics,
                    selected = selectedTopic,
                    onSelect = onSelectTopic
                )

                HorizontalDivider(color = Color(0x14FFFFFF))

                Box(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth()
                ) {
                    when {
                        selectedTopic == null ->
                            Hint("Pick a topic to read its week.")

                        state.detailLoading -> Box(
                            Modifier.fillMaxSize(),
                            contentAlignment = Alignment.Center
                        ) { CircularProgressIndicator(color = TechIndigo) }

                        detailError != null -> ErrorBlock(
                            title = "Could not load this summary",
                            message = detailError,
                            onRetry = onRetryDetail
                        )

                        week == null -> NoSummaryYet(
                            label = state.topics
                                .firstOrNull { it.slug == selectedTopic }
                                ?.label ?: selectedTopic
                        )

                        else -> SummaryDetail(
                            summary = week,
                            hasEarlier = state.hasEarlier,
                            hasLater = state.hasLater,
                            onStepWeek = onStepWeek
                        )
                    }
                }
            }
        }
    }
}

/* ------------------------------------------------------------------ */
/* Topic chooser                                                       */
/* ------------------------------------------------------------------ */

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TopicCircleRow(
    topics: List<NewsCategory>,
    selected: String?,
    onSelect: (String) -> Unit
) {
    // FlowRow rather than a horizontally scrolling row: choosing means seeing
    // all the options, and seven circles wrap into two tidy rows on a phone.
    // A scroller would hide the last few behind a gesture.
    FlowRow(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 8.dp, vertical = 14.dp),
        horizontalArrangement = Arrangement.Center,
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        topics.forEach { topic ->
            TopicCircle(
                topic = topic,
                selected = selected == topic.slug,
                // Nothing is dimmed on arrival, so the circles read as an even
                // choice rather than as six disabled buttons and one live one.
                dimmed = selected != null && selected != topic.slug,
                onClick = { onSelect(topic.slug) }
            )
        }
    }
}

@Composable
private fun TopicCircle(
    topic: NewsCategory,
    selected: Boolean,
    dimmed: Boolean,
    onClick: () -> Unit
) {
    val accent = parseAccent(topic.accentColor)
    val scale by animateFloatAsState(if (selected) 1.07f else 1f, label = "circleScale")
    val interaction = remember { MutableInteractionSource() }

    Column(
        modifier = Modifier
            .width(74.dp)
            .clickable(
                interactionSource = interaction,
                indication = null,
                onClick = onClick
            ),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Box(
            modifier = Modifier
                .scale(scale)
                .size(54.dp)
                .alpha(if (dimmed) 0.34f else 1f)
                .clip(CircleShape)
                .background(accent)
                .then(
                    // A ring in the topic's own colour, so the selection marker
                    // never fights the palette it sits in.
                    if (selected) {
                        Modifier.border(3.dp, accent.copy(alpha = 0.45f), CircleShape)
                    } else {
                        Modifier
                    }
                ),
            contentAlignment = Alignment.Center
        ) {
            Text(
                text = topicInitials(topic.slug),
                color = contrastOn(accent),
                fontSize = 15.sp,
                fontWeight = FontWeight.ExtraBold,
                letterSpacing = 0.5.sp
            )
        }

        Spacer(Modifier.height(6.dp))

        Text(
            text = topic.label,
            color = if (selected) TextPrimary else TextSecondary,
            fontSize = 10.sp,
            lineHeight = 13.sp,
            fontWeight = FontWeight.SemiBold,
            textAlign = TextAlign.Center,
            maxLines = 2
        )
    }
}

/**
 * Two letters, not one.
 *
 * TECH, FINANCE, SPORTS and SCIENCE are all distinct on their initial, but
 * POLITICS and PROGRAMMING are not. Deriving from the slug also means a topic
 * added to the server catalogue gets a usable badge with no app release, which
 * a hardcoded icon map would not (ADR-006).
 */
private fun topicInitials(slug: String): String = slug.take(2).uppercase(Locale.US)

/* ------------------------------------------------------------------ */
/* Detail                                                              */
/* ------------------------------------------------------------------ */

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SummaryDetail(
    summary: TopicSummary,
    hasEarlier: Boolean,
    hasLater: Boolean,
    onStepWeek: (Int) -> Unit
) {
    val accent = parseAccent(summary.accentColor)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 18.dp)
    ) {
        // 1. Topic name
        Text(
            text = summary.topicLabel,
            color = accent,
            fontSize = 25.sp,
            fontWeight = FontWeight.ExtraBold,
            lineHeight = 29.sp
        )

        Spacer(Modifier.height(5.dp))

        // 2. The week it covers, smaller, straight underneath. The arrows step
        //    through history -- the only place older weeks are reachable at all,
        //    since the articles behind them were purged days ago.
        Row(verticalAlignment = Alignment.CenterVertically) {
            StepButton("\u2039", enabled = hasEarlier) { onStepWeek(1) }
            Spacer(Modifier.width(8.dp))
            Text(
                text = formatWeekRange(summary.weekStart, summary.weekEnd),
                color = TextSecondary,
                fontSize = 12.sp,
                fontWeight = FontWeight.SemiBold
            )
            Spacer(Modifier.width(8.dp))
            StepButton("\u203a", enabled = hasLater) { onStepWeek(-1) }
        }

        Spacer(Modifier.height(18.dp))

        // 3. The summary
        Text(
            text = summary.summary,
            color = Color(0xFFDBE2EC),
            fontSize = 15.sp,
            lineHeight = 24.sp
        )

        // 4. Total number of articles
        SectionDivider()
        StatLabel("Articles")
        Spacer(Modifier.height(6.dp))
        Text(
            text = summary.numberOfArticles.toString(),
            color = TextPrimary,
            fontSize = 22.sp,
            fontWeight = FontWeight.Bold
        )
        Spacer(Modifier.height(4.dp))
        Text(
            text = if (summary.wasFiltered) {
                "Summarised from ${summary.numberOfArticles} of the " +
                    "${summary.articlesInWindow} collected this week. The rest carried " +
                    "a generic description rather than the publisher\u2019s own."
            } else {
                "Every article collected this week was used."
            },
            color = TextSecondary,
            fontSize = 12.sp,
            lineHeight = 17.sp
        )

        // 5. Top sources
        SectionDivider()
        StatLabel("Top sources")
        Spacer(Modifier.height(9.dp))
        val sources = summary.topSources.orEmpty()
        if (sources.isEmpty()) {
            Text(
                text = "No source breakdown stored for this week.",
                color = TextSecondary,
                fontSize = 12.sp
            )
        } else {
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(7.dp),
                verticalArrangement = Arrangement.spacedBy(7.dp)
            ) {
                sources.forEach { SourceChip(it) }
            }
        }

        Spacer(Modifier.height(22.dp))

        // Provenance. The text is generated, and the window is ingest time
        // rather than publication time; both are stated rather than implied.
        Text(
            text = listOfNotNull(summary.basis, "Written by ${summary.generator}.")
                .joinToString(" "),
            color = TextMuted,
            fontSize = 10.sp,
            lineHeight = 15.sp
        )

        Spacer(Modifier.height(16.dp))
    }
}

@Composable
private fun StepButton(glyph: String, enabled: Boolean, onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    Box(
        modifier = Modifier
            .size(24.dp)
            .alpha(if (enabled) 1f else 0.22f)
            .clip(CircleShape)
            .background(Color(0xA61D2537))
            .border(1.dp, Color(0x1AFFFFFF), CircleShape)
            .clickable(
                enabled = enabled,
                interactionSource = interaction,
                indication = null,
                onClick = onClick
            ),
        contentAlignment = Alignment.Center
    ) {
        Text(text = glyph, color = TextSecondary, fontSize = 13.sp)
    }
}

@Composable
private fun SourceChip(name: String) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(Color(0xB31D2537))
            .border(1.dp, Color(0x14FFFFFF), RoundedCornerShape(999.dp))
            .padding(horizontal = 11.dp, vertical = 5.dp)
    ) {
        Text(
            text = name,
            color = Color(0xFFAEB7C7),
            fontSize = 11.sp,
            fontWeight = FontWeight.SemiBold
        )
    }
}

@Composable
private fun SectionDivider() {
    Spacer(Modifier.height(18.dp))
    HorizontalDivider(color = Color(0x12FFFFFF))
    Spacer(Modifier.height(14.dp))
}

@Composable
private fun StatLabel(text: String) {
    Text(
        text = text.uppercase(Locale.getDefault()),
        color = TextMuted,
        fontSize = 10.sp,
        fontWeight = FontWeight.ExtraBold,
        letterSpacing = 1.5.sp
    )
}

/* ------------------------------------------------------------------ */
/* Empty / error states                                                */
/* ------------------------------------------------------------------ */

@Composable
private fun Hint(text: String) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text(
            text = text,
            color = TextSecondary,
            fontSize = 13.sp,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(32.dp)
        )
    }
}

/**
 * A topic with no summary is not an error, and saying so matters: it happens
 * when too few of that topic's articles that week carried a real publisher
 * description. Beauty and Programming hit this most often. A blank pane would
 * look like a bug and send someone debugging the backend.
 */
@Composable
private fun NoSummaryYet(label: String) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text(
            text = "No summary yet for $label",
            color = TextPrimary,
            fontSize = 15.sp,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center
        )
        Spacer(Modifier.height(10.dp))
        Text(
            text = "Summaries are written once a week, covering the last completed " +
                "Monday\u2013Sunday. A topic is skipped when too few of its articles " +
                "that week came with a real publisher description.",
            color = TextSecondary,
            fontSize = 13.sp,
            lineHeight = 19.sp,
            textAlign = TextAlign.Center
        )
    }
}

@Composable
private fun ErrorBlock(title: String, message: String, onRetry: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(28.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text(text = title, color = TextPrimary, fontSize = 16.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(8.dp))
        Text(
            text = message,
            color = TextSecondary,
            fontSize = 13.sp,
            textAlign = TextAlign.Center
        )
        Spacer(Modifier.height(18.dp))
        Button(
            onClick = onRetry,
            colors = ButtonDefaults.buttonColors(containerColor = TechIndigo)
        ) {
            Text("Retry", color = Color.White)
        }
    }
}

/* ------------------------------------------------------------------ */
/* Dates                                                               */
/* ------------------------------------------------------------------ */

/**
 * "Aug 24 - Aug 30, 2026" from two ISO dates.
 *
 * SimpleDateFormat rather than java.time: minSdk is 24 and core library
 * desugaring is not enabled in app/build.gradle.kts, so LocalDate would compile
 * fine and then crash on API 24-25 devices at runtime.
 *
 * Both formatters are pinned to UTC. The backend anchors week boundaries to UTC,
 * and parsing "2026-08-24" as UTC midnight then formatting it in a
 * negative-offset zone would render the 23rd -- a date quietly disagreeing with
 * the week the summary actually covers.
 *
 * Falls back to the raw ISO strings rather than throwing: an unexpected date
 * format from the server should cost legibility, not the screen.
 */
internal fun formatWeekRange(startIso: String, endIso: String): String = runCatching {
    val utc = TimeZone.getTimeZone("UTC")
    val parser = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { timeZone = utc }
    val dayMonth = SimpleDateFormat("MMM d", Locale.getDefault()).apply { timeZone = utc }
    val dayMonthYear = SimpleDateFormat("MMM d, yyyy", Locale.getDefault()).apply { timeZone = utc }

    val start = parser.parse(startIso)
    val end = parser.parse(endIso)
    if (start == null || end == null) {
        "$startIso \u2013 $endIso"
    } else {
        "${dayMonth.format(start)} \u2013 ${dayMonthYear.format(end)}"
    }
}.getOrDefault("$startIso \u2013 $endIso")
