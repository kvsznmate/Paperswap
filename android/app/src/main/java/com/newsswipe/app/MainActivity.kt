package com.newsswipe.app

import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.newsswipe.app.data.model.NewsArticle
import com.newsswipe.app.ui.components.ActionButtons
import com.newsswipe.app.ui.components.SwipeableCardStack
import com.newsswipe.app.ui.theme.DarkObsidian
import com.newsswipe.app.ui.theme.PureBlack
import com.newsswipe.app.ui.theme.SpaceCadet
import com.newsswipe.app.ui.theme.NewsSwipeAndroidTheme
import androidx.compose.ui.text.style.TextDecoration
import com.newsswipe.app.ui.theme.TextPrimary
import com.newsswipe.app.ui.theme.TextSecondary
import com.newsswipe.app.viewmodel.NewsUiState
import com.newsswipe.app.viewmodel.NewsViewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            NewsSwipeAndroidTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = PureBlack
                ) {
                    NewsSwipeAppScreen()
                }
            }
        }
    }
}

@Composable
fun NewsSwipeAppScreen(
    viewModel: NewsViewModel = viewModel()
) {
    val uiState by viewModel.uiState.collectAsState()
    val context = LocalContext.current

    // Helper to open news story URL via Android Custom Tabs
    fun openNewsUrl(url: String) {
        try {
            val builder = CustomTabsIntent.Builder()
            val customTabsIntent = builder.build()
            customTabsIntent.launchUrl(context, Uri.parse(url))
        } catch (e: Exception) {
            // Fallback to standard browser intent
            val intent = android.content.Intent(android.content.Intent.ACTION_VIEW, Uri.parse(url))
            context.startActivity(intent)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(PureBlack)
            .padding(horizontal = 16.dp, vertical = 12.dp)
    ) {
        // 1. Top Bar Header
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

        // 2. Main Content Area
        Box(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth(),
            contentAlignment = Alignment.Center
        ) {
            when (val state = uiState) {
                is NewsUiState.Loading -> {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        CircularProgressIndicator(color = Color(0xFF6366F1))
                        Spacer(modifier = Modifier.height(16.dp))
                        Text(
                            text = "Connecting to Oracle VM...",
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
                            text = "⚠️ Connection Issue",
                            color = Color.White,
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
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF6366F1))
                        ) {
                            Text("Retry Connection", color = Color.White)
                        }
                    }
                }

                is NewsUiState.Success -> {
                    SwipeableCardStack(
                        articles = state.articles,
                        currentIndex = state.currentIndex,
                        onSwipeRight = { article ->
                            viewModel.onSwipe("right")
                            openNewsUrl(article.url)
                        },
                        onSwipeLeft = {
                            viewModel.onSwipe("left")
                        }
                    )
                }
            }
        }

        // 3. Bottom Action Controls
        if (uiState is NewsUiState.Success) {
            val state = uiState as NewsUiState.Success
            ActionButtons(
                onPassClick = {
                    if (state.currentIndex < state.articles.size) {
                        viewModel.onSwipe("left")
                    }
                },
                onReadClick = {
                    if (state.currentIndex < state.articles.size) {
                        val article = state.articles[state.currentIndex]
                        viewModel.onSwipe("right")
                        openNewsUrl(article.url)
                    }
                },
                onUndoClick = {
                    viewModel.undoSwipe()
                }
            )
        }
    }
}
