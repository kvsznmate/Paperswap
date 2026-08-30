package com.newsswipe.app.ui.components

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.newsswipe.app.data.model.NewsArticle
import com.newsswipe.app.data.model.SwipeMetrics
import com.newsswipe.app.ui.theme.PassRed
import com.newsswipe.app.ui.theme.ReadGreen
import kotlinx.coroutines.launch

@Composable
fun SwipeableCardStack(
    articles: List<NewsArticle>,
    currentIndex: Int,
    onSwipeRight: (NewsArticle, SwipeMetrics) -> Unit,
    onSwipeLeft: (NewsArticle, SwipeMetrics) -> Unit,
    modifier: Modifier = Modifier
) {
    val configuration = LocalConfiguration.current
    val density = LocalDensity.current
    val screenWidthPx = with(density) { configuration.screenWidthDp.dp.toPx() }

    val coroutineScope = rememberCoroutineScope()
    val offsetX = remember { Animatable(0f) }
    val offsetY = remember { Animatable(0f) }

    // Which face is showing right now.
    var isFlipped by remember { mutableStateOf(false) }

    // Whether the card was EVER turned over, which is the signal worth logging.
    // A user who flips, reads the summary, flips back and then swipes has shown
    // real interest; isFlipped would be false at that moment and would lose it.
    var everFlipped by remember { mutableStateOf(false) }

    // Start of dwell for the card currently on top.
    var shownAtMs by remember { mutableStateOf(System.currentTimeMillis()) }

    // Reset per card. Without this the next card inherits the previous card's
    // flip state -- it would arrive already showing its back -- and its dwell
    // would be measured from whenever the deck was first built.
    LaunchedEffect(currentIndex) {
        isFlipped = false
        everFlipped = false
        shownAtMs = System.currentTimeMillis()
    }

    fun currentMetrics() = SwipeMetrics(
        dwellMs = System.currentTimeMillis() - shownAtMs,
        flipped = everFlipped
    )

    val activeArticle = articles.getOrNull(currentIndex)

    Box(
        modifier = modifier
            .fillMaxWidth()
            .fillMaxHeight(0.85f),
        contentAlignment = Alignment.Center
    ) {
        if (currentIndex >= articles.size) {
            // Empty Deck State
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    text = "🎉 All News Swiped!",
                    color = Color.White,
                    fontSize = 20.sp,
                    fontWeight = FontWeight.Bold
                )
            }
        } else {
            // Render background stacked depth card
            val nextArticle = articles.getOrNull(currentIndex + 1)
            if (nextArticle != null) {
                NewsCard(
                    article = nextArticle,
                    modifier = Modifier
                        .fillMaxSize()
                        .graphicsLayer {
                            scaleX = 0.95f
                            scaleY = 0.95f
                            translationY = 24.dp.toPx()
                        }
                )
            }

            // Top Interactive Active Card
            if (activeArticle != null) {
                val dragX = offsetX.value
                val rotationAngle = (dragX / screenWidthPx) * 20f

                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .graphicsLayer {
                            translationX = dragX
                            translationY = offsetY.value
                            rotationZ = rotationAngle
                        }
                        // Tap and drag live in separate pointerInput blocks and
                        // coexist without a manual slop check. detectDragGestures
                        // waits on awaitTouchSlopOrCancellation and consumes
                        // nothing until the finger has actually travelled, so a
                        // stationary press still reaches the tap detector; once
                        // slop is exceeded the tap detector cancels itself.
                        //
                        // Doing this by hand -- measuring displacement and
                        // duration in one handler -- was the alternative, and it
                        // means reimplementing platform touch slop, which varies
                        // by device.
                        .pointerInput(currentIndex) {
                            detectTapGestures(
                                onTap = {
                                    isFlipped = !isFlipped
                                    if (isFlipped) everFlipped = true
                                }
                            )
                        }
                        .pointerInput(currentIndex) {
                            detectDragGestures(
                                onDragEnd = {
                                    val threshold = screenWidthPx * 0.30f
                                    if (offsetX.value > threshold) {
                                        // Swipe Right (Read)
                                        val metrics = currentMetrics()
                                        coroutineScope.launch {
                                            offsetX.animateTo(screenWidthPx * 1.5f, animationSpec = tween(250))
                                            onSwipeRight(activeArticle, metrics)
                                            offsetX.snapTo(0f)
                                            offsetY.snapTo(0f)
                                        }
                                    } else if (offsetX.value < -threshold) {
                                        // Swipe Left (Pass)
                                        val metrics = currentMetrics()
                                        coroutineScope.launch {
                                            offsetX.animateTo(-screenWidthPx * 1.5f, animationSpec = tween(250))
                                            onSwipeLeft(activeArticle, metrics)
                                            offsetX.snapTo(0f)
                                            offsetY.snapTo(0f)
                                        }
                                    } else {
                                        // Spring back
                                        coroutineScope.launch {
                                            offsetX.animateTo(0f, animationSpec = tween(200))
                                            offsetY.animateTo(0f, animationSpec = tween(200))
                                        }
                                    }
                                },
                                onDrag = { change, dragAmount ->
                                    change.consume()
                                    coroutineScope.launch {
                                        offsetX.snapTo(offsetX.value + dragAmount.x)
                                        offsetY.snapTo(offsetY.value + dragAmount.y)
                                    }
                                }
                            )
                        }
                ) {
                    FlippableNewsCard(article = activeArticle, flipped = isFlipped)

                    // Overlay Swipe Badges.
                    //
                    // Drawn outside FlippableNewsCard on purpose: they belong to
                    // the drag, not to either face. Nested inside, they would
                    // rotate away with the card and vanish mid-flip.
                    if (dragX > 40f) {
                        val alphaVal = (dragX / 200f).coerceIn(0f, 1f)
                        Box(
                            modifier = Modifier
                                .align(Alignment.TopEnd)
                                .padding(top = 40.dp, end = 30.dp)
                                .rotate(15f)
                                .alpha(alphaVal)
                                .border(3.dp, ReadGreen, RoundedCornerShape(12.dp))
                                .background(Color(0x3310B981))
                                .padding(horizontal = 20.dp, vertical = 6.dp)
                        ) {
                            Text(
                                text = "READ →",
                                color = ReadGreen,
                                fontSize = 24.sp,
                                fontWeight = FontWeight.ExtraBold
                            )
                        }
                    } else if (dragX < -40f) {
                        val alphaVal = (-dragX / 200f).coerceIn(0f, 1f)
                        Box(
                            modifier = Modifier
                                .align(Alignment.TopStart)
                                .padding(top = 40.dp, start = 30.dp)
                                .rotate(-15f)
                                .alpha(alphaVal)
                                .border(3.dp, PassRed, RoundedCornerShape(12.dp))
                                .background(Color(0x33EF4444))
                                .padding(horizontal = 20.dp, vertical = 6.dp)
                        ) {
                            Text(
                                text = "SKIP",
                                color = PassRed,
                                fontSize = 24.sp,
                                fontWeight = FontWeight.ExtraBold
                            )
                        }
                    }
                }
            }
        }
    }
}
