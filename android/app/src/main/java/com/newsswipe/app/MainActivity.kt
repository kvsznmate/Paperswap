package com.newsswipe.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.viewmodel.compose.viewModel
import com.newsswipe.app.ui.components.ActionButtons
import com.newsswipe.app.ui.components.AppTab
import com.newsswipe.app.ui.components.SwipeableCardStack
import com.newsswipe.app.ui.components.TopNavBar
import com.newsswipe.app.ui.screens.SummariesScreen
import com.newsswipe.app.ui.theme.AppBackground
import com.newsswipe.app.ui.theme.AppBackgroundTop
import com.newsswipe.app.ui.theme.NewsSwipeAndroidTheme
import com.newsswipe.app.ui.theme.PassRed
import com.newsswipe.app.ui.theme.TechIndigo
import com.newsswipe.app.ui.theme.TextPrimary
import com.newsswipe.app.ui.theme.TextSecondary
import com.newsswipe.app.viewmodel.NewsUiState
import com.newsswipe.app.viewmodel.NewsViewModel
import com.newsswipe.app.viewmodel.SummaryViewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            NewsSwipeAndroidTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    // Bottom colour of the gradient below, so no seam shows
                    // wherever the Column does not paint (insets, overscroll).
                    color = AppBackground
                ) {
                    PaperSwapApp()
                }
            }
        }
    }
}

/**
 * Shell: brand header, the Cards / Weekly Summaries switch, then whichever
 * destination is selected.
 *
 * Both ViewModels are created here and outlive the tab switch, so leaving the
 * deck to glance at a summary and coming back does not lose your place in it.
 */
@Composable
fun PaperSwapApp(
    newsViewModel: NewsViewModel = viewModel(),
    summaryViewModel: SummaryViewModel = viewModel()
) {
    // Saved across rotation. Stored as the enum name rather than the enum
    // itself, since enums are not Bundle-saveable without a custom Saver.
    var tabName by rememberSaveable { mutableStateOf(AppTab.CARDS.name) }
    val tab = runCatching { AppTab.valueOf(tabName) }.getOrDefault(AppTab.CARDS)

    Column(
        modifier = Modifier
            .fillMaxSize()
            // A gentle top-down wash rather than a flat fill: it gives the
            // screen a light source and keeps the card from looking pasted onto
            // a void, which is what pure black was doing.
            .background(
                Brush.verticalGradient(listOf(AppBackgroundTop, AppBackground))
            )
            .padding(horizontal = 16.dp, vertical = 12.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 8.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "PaperSwap",
                color = TextPrimary,
                fontSize = 22.sp,
                fontWeight = FontWeight.ExtraBold,
                textDecoration = TextDecoration.Underline
            )
        }

        Spacer(Modifier.height(6.dp))

        TopNavBar(
            selected = tab,
            onSelect = { tabName = it.name }
        )

        Spacer(Modifier.height(10.dp))

        when (tab) {
            AppTab.CARDS -> CardsScreen(
                viewModel = newsViewModel,
                modifier = Modifier.weight(1f)
            )

            AppTab.SUMMARIES -> {
                val summaryState by summaryViewModel.uiState.collectAsState()

                // Fetched on first visit rather than in the ViewModel's init:
                // someone who only ever swipes should not cost the box a request.
                LaunchedEffect(Unit) { summaryViewModel.loadTopicsIfNeeded() }

                SummariesScreen(
                    state = summaryState,
                    onSelectTopic = summaryViewModel::selectTopic,
                    onStepWeek = summaryViewModel::stepWeek,
                    onRetryTopics = summaryViewModel::loadTopics,
                    onRetryDetail = summaryViewModel::retryDetail,
                    modifier = Modifier.weight(1f)
                )
            }
        }
    }
}

/** The swipe deck plus its action buttons. */
@Composable
private fun CardsScreen(
    viewModel: NewsViewModel,
    modifier: Modifier = Modifier
) {
    val uiState by viewModel.uiState.collectAsState()
    val context = LocalContext.current

    // The dwell timer runs only while this screen is composed AND the app is in
    // the foreground. Leaving for the Summaries tab disposes this effect, and
    // a right swipe's Custom Tab stops the activity -- both pause the timer, so
    // that time is not charged to the next card. An observer added to a started
    // lifecycle is replayed ON_START, so arriving here starts the timer.
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

    fun openNewsUrl(url: String) {
        val uri = Uri.parse(url)
        runCatching { CustomTabsIntent.Builder().build().launchUrl(context, uri) }
            .recoverCatching { context.startActivity(Intent(Intent.ACTION_VIEW, uri)) }
            .onFailure { Toast.makeText(context, "No app can open this link.", Toast.LENGTH_SHORT).show() }
    }

    fun swipe(read: Boolean) {
        val article = (uiState as? NewsUiState.Success)?.currentArticle ?: return
        viewModel.onSwipe(read)
        if (read) openNewsUrl(article.url)
    }

    Column(modifier = modifier.fillMaxWidth()) {
        Box(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth(),
            contentAlignment = Alignment.Center
        ) {
            when (val state = uiState) {
                is NewsUiState.Loading -> {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        CircularProgressIndicator(color = TechIndigo)
                        Spacer(modifier = Modifier.height(16.dp))
                        Text(
                            text = "Loading your deck…",
                            color = TextSecondary,
                            fontSize = 14.sp
                        )
                    }
                }

                is NewsUiState.Error -> {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        modifier = Modifier.padding(24.dp)
                    ) {
                        Text(
                            text = "⚠️ Could not reach the server",
                            color = TextPrimary,
                            fontSize = 18.sp,
                            fontWeight = FontWeight.Bold
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(
                            text = state.message,
                            color = TextSecondary,
                            fontSize = 13.sp,
                            modifier = Modifier.padding(horizontal = 16.dp)
                        )
                        Spacer(modifier = Modifier.height(20.dp))
                        Button(
                            onClick = { viewModel.loadNewsFeed() },
                            colors = ButtonDefaults.buttonColors(containerColor = TechIndigo)
                        ) {
                            Text("Retry", color = TextPrimary)
                        }
                    }
                }

                is NewsUiState.Success -> {
                    SwipeableCardStack(
                        articles = state.articles,
                        currentIndex = state.currentIndex,
                        onSwipe = ::swipe,
                        onFlip = viewModel::onCardFlipped
                    )
                }
            }
        }

        val success = uiState as? NewsUiState.Success
        if (success != null) {
            success.swipeNotice?.let { notice ->
                Text(
                    text = notice,
                    color = PassRed,
                    fontSize = 12.sp,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 4.dp)
                )
            }
            ActionButtons(
                onPassClick = { swipe(read = false) },
                onReadClick = { swipe(read = true) },
                onUndoClick = viewModel::undoSwipe
            )
        }
    }
}
