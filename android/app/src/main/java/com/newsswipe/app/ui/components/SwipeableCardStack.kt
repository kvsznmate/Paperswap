package com.newsswipe.app.ui.components

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectDragGestures
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
import com.newsswipe.app.ui.theme.PassRed
import com.newsswipe.app.ui.theme.ReadGreen
import kotlinx.coroutines.launch

@Composable
fun SwipeableCardStack(
    articles: List<NewsArticle>,
    currentIndex: Int,
    onSwipeRight: (NewsArticle) -> Unit,
    onSwipeLeft: (NewsArticle) -> Unit,
    modifier: Modifier = Modifier
) {
    val configuration = LocalConfiguration.current
    val density = LocalDensity.current
    val screenWidthPx = with(density) { configuration.screenWidthDp.dp.toPx() }

    val coroutineScope = rememberCoroutineScope()
    val offsetX = remember { Animatable(0f) }
    val offsetY = remember { Animatable(0f) }

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
                        .pointerInput(currentIndex) {
                            detectDragGestures(
                                onDragEnd = {
                                    val threshold = screenWidthPx * 0.30f
                                    if (offsetX.value > threshold) {
                                        // Swipe Right (Read)
                                        coroutineScope.launch {
                                            offsetX.animateTo(screenWidthPx * 1.5f, animationSpec = tween(250))
                                            onSwipeRight(activeArticle)
                                            offsetX.snapTo(0f)
                                            offsetY.snapTo(0f)
                                        }
                                    } else if (offsetX.value < -threshold) {
                                        // Swipe Left (Pass)
                                        coroutineScope.launch {
                                            offsetX.animateTo(-screenWidthPx * 1.5f, animationSpec = tween(250))
                                            onSwipeLeft(activeArticle)
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
                    NewsCard(article = activeArticle)

                    // Overlay Swipe Badges
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
