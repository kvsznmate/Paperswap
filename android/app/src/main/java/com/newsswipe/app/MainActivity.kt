package com.newsswipe.app

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.viewmodel.compose.viewModel
import com.newsswipe.app.ui.components.ActionButtons
import com.newsswipe.app.ui.components.AppTab
import com.newsswipe.app.ui.components.BottomNavBar
import com.newsswipe.app.ui.components.CardShape
import com.newsswipe.app.ui.components.FilterIcon
import com.newsswipe.app.ui.components.Masthead
import com.newsswipe.app.ui.components.SwipeableCardStack
import com.newsswipe.app.ui.components.TopicSheet
import com.newsswipe.app.ui.components.todayLine
import com.newsswipe.app.ui.screens.Message
import com.newsswipe.app.ui.screens.SavedScreen
import com.newsswipe.app.ui.screens.WeekScreen
import com.newsswipe.app.ui.theme.Paper
import com.newsswipe.app.ui.theme.PaperSans
import com.newsswipe.app.ui.theme.PaperTheme
import com.newsswipe.app.viewmodel.NewsUiState
import com.newsswipe.app.viewmodel.NewsViewModel
import com.newsswipe.app.viewmodel.SavedViewModel
import com.newsswipe.app.viewmodel.SummaryViewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            PaperTheme {
                PaperSwapApp()
            }
        }
    }
}

/**
 * Shell: the selected destination above the bottom navigation.
 *
 * All three ViewModels are created here and outlive tab switches, so leaving
 * the deck to read the week and coming back keeps your place in it.
 */
@Composable
fun PaperSwapApp(
    newsViewModel: NewsViewModel = viewModel(),
    summaryViewModel: SummaryViewModel = viewModel(),
    savedViewModel: SavedViewModel = viewModel()
) {
    // Saved across rotation as the enum name; enums are not Bundle-saveable
    // without a custom Saver.
    var tabName by rememberSaveable { mutableStateOf(AppTab.DECK.name) }
    val tab = runCatching { AppTab.valueOf(tabName) }.getOrDefault(AppTab.DECK)
    val saved by savedViewModel.saved.collectAsState()
    val context = LocalContext.current

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Paper.colors.ground)
    ) {
        Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
            when (tab) {
                AppTab.DECK -> DeckScreen(newsViewModel, savedViewModel)

                AppTab.WEEK -> {
                    val weekState by summaryViewModel.uiState.collectAsState()
                    // Fetched on first visit rather than at start-up: someone who
                    // only ever swipes should not cost the box a request.
                    LaunchedEffect(Unit) { summaryViewModel.loadIfNeeded() }
                    WeekScreen(
                        state = weekState,
                        onStepWeek = summaryViewModel::stepWeek,
                        onRetry = summaryViewModel::retry
                    )
                }

                AppTab.SAVED -> SavedScreen(
                    saved = saved,
                    onOpen = { item -> item.url?.let { openArticle(context, it) } },
                    onRemove = { item -> savedViewModel.remove(item.id) }
                )
            }
        }
        BottomNavBar(
            selected = tab,
            savedCount = saved.size,
            onSelect = { tabName = it.name }
        )
    }
}

/** Masthead with the topic filter, the deck, and the action row. */
@Composable
private fun DeckScreen(viewModel: NewsViewModel, savedViewModel: SavedViewModel) {
    val c = Paper.colors
    val uiState by viewModel.uiState.collectAsState()
    val topics by viewModel.selectedTopics.collectAsState()
    val catalogue by viewModel.catalogue.collectAsState()
    val saved by savedViewModel.saved.collectAsState()
    val context = LocalContext.current
    var sheetOpen by remember { mutableStateOf(false) }

    // The dwell timer runs only while this screen is composed AND the app is in
    // the foreground. Leaving for another tab disposes this effect, and Read's
    // Custom Tab stops the activity -- both pause the timer, so that time is
    // not charged to the next card. An observer added to a started lifecycle is
    // replayed ON_START, so arriving here starts the timer.
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_START -> viewModel.onDeckShown()
                Lifecycle.Event.ON_STOP -> viewModel.onDeckHidden()
                else -> Unit
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
            viewModel.onDeckHidden()
        }
    }

    val success = uiState as? NewsUiState.Success
    val current = success?.currentArticle

    fun swipe(read: Boolean) {
        val article = current ?: return
        viewModel.onSwipe(read)
        if (read) openArticle(context, article.url)
    }

    Column(modifier = Modifier.fillMaxSize()) {
        Masthead(title = "PaperSwap", subtitle = todayLine()) {
            OutlinedButton(
                onClick = { viewModel.loadCatalogueIfNeeded(); sheetOpen = true },
                shape = CardShape,
                border = BorderStroke(1.dp, c.rule),
                colors = ButtonDefaults.outlinedButtonColors(containerColor = c.card, contentColor = c.ink),
                modifier = Modifier.height(44.dp).semantics { contentDescription = "Choose topics" }
            ) {
                Icon(FilterIcon, contentDescription = null, modifier = Modifier.size(16.dp))
                Spacer(Modifier.size(8.dp))
                Text(
                    if (topics.isEmpty()) "All topics" else "${topics.size} topics",
                    fontFamily = PaperSans, fontWeight = FontWeight.Medium, fontSize = 13.sp
                )
            }
        }

        Box(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .padding(start = 16.dp, end = 16.dp, top = 18.dp, bottom = 10.dp),
            contentAlignment = Alignment.Center
        ) {
            when (val state = uiState) {
                is NewsUiState.Loading -> Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(14.dp)
                ) {
                    CircularProgressIndicator(color = c.ink)
                    Text("Loading your deck…", fontFamily = PaperSans, fontSize = 14.sp, color = c.muted)
                }

                is NewsUiState.Error -> Message(
                    title = "Could not reach the server",
                    body = state.message.ifBlank { "Check your connection and try again." },
                    action = "Retry",
                    onAction = viewModel::loadNewsFeed
                )

                is NewsUiState.Success -> SwipeableCardStack(
                    articles = state.articles,
                    currentIndex = state.currentIndex,
                    onSwipe = ::swipe,
                    onFlip = viewModel::onCardFlipped,
                    onRestart = viewModel::loadNewsFeed
                )
            }
        }

        if (success != null) {
            success.swipeNotice?.let { notice ->
                Text(
                    text = notice,
                    fontFamily = PaperSans,
                    fontSize = 12.sp,
                    color = c.muted,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp)
                )
            }
            val isSaved = current != null && saved.any { it.id == current.id }
            ActionButtons(
                saved = isSaved,
                canUndo = success.canUndo,
                onUndoClick = viewModel::undoSwipe,
                onPassClick = { swipe(read = false) },
                onReadClick = { swipe(read = true) },
                onSaveClick = {
                    val article = current ?: return@ActionButtons
                    val nowSaved = savedViewModel.toggle(article)
                    Toast.makeText(
                        context,
                        if (nowSaved) "Saved on this phone" else "Removed from Saved",
                        Toast.LENGTH_SHORT
                    ).show()
                }
            )
        }
    }

    if (sheetOpen) {
        TopicSheet(
            catalogue = catalogue,
            selected = topics,
            onApply = { chosen -> viewModel.setTopics(chosen); sheetOpen = false },
            onDismiss = { sheetOpen = false }
        )
    }
}

/** Open an article in a Custom Tab, falling back to any browser. */
fun openArticle(context: Context, url: String) {
    val uri = Uri.parse(url)
    runCatching { CustomTabsIntent.Builder().build().launchUrl(context, uri) }
        .recoverCatching { context.startActivity(Intent(Intent.ACTION_VIEW, uri)) }
        .onFailure { Toast.makeText(context, "No app can open this link.", Toast.LENGTH_SHORT).show() }
}
