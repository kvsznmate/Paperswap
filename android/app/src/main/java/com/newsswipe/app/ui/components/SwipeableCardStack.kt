package com.newsswipe.app.ui.components

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.foundation.BorderStroke
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
import com.newsswipe.app.ui.theme.Paper
import com.newsswipe.app.ui.theme.PaperSans
import com.newsswipe.app.ui.theme.PaperSerif
import kotlinx.coroutines.launch

// A swipe commits once the card passes this fraction of the screen width.
private const val SWIPE_THRESHOLD_FRACTION = 0.30f

// Tilt at the moment the swipe commits, in degrees.
//
// Rotation used to be measured against the SCREEN WIDTH -- `(dragX /
// screenWidthPx) * 20f` -- which meant about 3 degrees during an ordinary drag
// and only 6 at the point the card actually commits. Present in the code,
// invisible on the glass. Adding the top nav bar shortened the card, which cut
// the apparent corner travel further and tipped it from "barely there" to "not
// there".
//
// Measuring against the THRESHOLD instead ties the tilt to the thing the user
// cares about: the card is unmistakably angled exactly when it is about to fly
// off, and holds that angle rather than winding up further on a long drag.
private const val MAX_CARD_ROTATION = 16f

// Card flip: tap turns the card over to show the description and related
// coverage (NewsCardBack). Both arrive with the feed, so flipping costs no request.
private const val FLIP_DURATION_MS = 420

// Perspective depth for the Y-axis flip. Without raising this from the default
// the card appears to balloon toward the viewer at 90 degrees instead of
// turning on the spot -- the higher the value, the flatter and more card-like
// the rotation reads.
private const val FLIP_CAMERA_DISTANCE = 14f

@Composable
fun SwipeableCardStack(
    articles: List<NewsArticle>,
    currentIndex: Int,
    /** Called after the fly-off animation; `read` is true for a right swipe. */
    onSwipe: (read: Boolean) -> Unit,
    // Fired the first time the user turns a card over. Feeds user_swipes.flipped,
    // which the backend documents as "a stronger interest signal than direction
    // alone" -- so the flip is not only a reading affordance, it is training data.
    onFlip: () -> Unit = {},
    /** "Start again" on the empty deck: reloads the feed. */
    onRestart: () -> Unit = {},
    modifier: Modifier = Modifier
) {
    val configuration = LocalConfiguration.current
    // Named localDensity, NOT density. GraphicsLayerScope also exposes a
    // `density` -- a Float -- and a local of the same name in the enclosing
    // scope beats the implicit receiver's property. With this called `density`,
    // `FLIP_CAMERA_DISTANCE * density` inside graphicsLayer{} resolved to
    // Float * Density and failed to compile.
    val localDensity = LocalDensity.current
    val screenWidthPx = with(localDensity) { configuration.screenWidthDp.dp.toPx() }
    val swipeThresholdPx = screenWidthPx * SWIPE_THRESHOLD_FRACTION

    val coroutineScope = rememberCoroutineScope()
    val offsetX = remember { Animatable(0f) }
    val offsetY = remember { Animatable(0f) }

    val activeArticle = articles.getOrNull(currentIndex)

    fun flyOff(read: Boolean) = coroutineScope.launch {
        val direction = if (read) 1f else -1f
        offsetX.animateTo(direction * screenWidthPx * 1.5f, animationSpec = tween(250))
        onSwipe(read)
        offsetX.snapTo(0f)
        offsetY.snapTo(0f)
    }

    Box(
        modifier = modifier
            .fillMaxSize(),
        contentAlignment = Alignment.Center
    ) {
        if (currentIndex >= articles.size) {
            val c = Paper.colors
            Column(
                modifier = Modifier.fillMaxSize().padding(horizontal = 12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically)
            ) {
                Text(
                    text = "That’s the whole deck.",
                    fontFamily = PaperSerif,
                    fontWeight = FontWeight.Bold,
                    fontSize = 26.sp,
                    lineHeight = 32.sp,
                    color = c.ink
                )
                Text(
                    text = "New stories arrive with the next refresh, every 12 hours.",
                    fontFamily = PaperSans,
                    fontSize = 14.sp,
                    lineHeight = 21.sp,
                    color = c.muted
                )
                OutlinedButton(
                    onClick = onRestart,
                    shape = CardShape,
                    border = BorderStroke(1.5.dp, c.ink),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = c.ink),
                    modifier = Modifier.height(48.dp)
                ) {
                    Text("Start again", fontFamily = PaperSans, fontWeight = FontWeight.Bold, fontSize = 15.sp)
                }
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
                // Capped at the threshold, so dragging far past the commit point
                // does not keep spinning the card.
                val rotationAngle =
                    (dragX / swipeThresholdPx).coerceIn(-1f, 1f) * MAX_CARD_ROTATION

                // Keyed on currentIndex, so the next card always arrives face up
                // and an undo does not restore a card mid-flip.
                var isFlipped by remember(currentIndex) { mutableStateOf(false) }
                val flipRotation by animateFloatAsState(
                    targetValue = if (isFlipped) 180f else 0f,
                    animationSpec = tween(FLIP_DURATION_MS, easing = FastOutSlowInEasing),
                    label = "cardFlip"
                )

                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .graphicsLayer {
                            translationX = dragX
                            translationY = offsetY.value
                            rotationZ = rotationAngle
                        }
                        // Tap and drag live in separate pointerInput blocks on
                        // purpose. They coexist because onDrag consumes the
                        // movement, which cancels the tap detector -- so a swipe
                        // never also registers as a tap, and a tap that never
                        // moves past touch slop never starts a drag.
                        .pointerInput(currentIndex) {
                            detectTapGestures(
                                onTap = {
                                    // Report only the turn TO the back. Flipping
                                    // it back is not a second act of reading.
                                    if (!isFlipped) onFlip()
                                    isFlipped = !isFlipped
                                }
                            )
                        }
                        .pointerInput(currentIndex) {
                            detectDragGestures(
                                onDragEnd = {
                                    when {
                                        offsetX.value > swipeThresholdPx -> flyOff(read = true)
                                        offsetX.value < -swipeThresholdPx -> flyOff(read = false)
                                        else -> coroutineScope.launch {
                                            // Spring back
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
                    // Only the two faces sit inside the flipping layer. The
                    // swipe badges below must stay outside it, or they would be
                    // rendered mirrored once the card passes 90 degrees.
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .graphicsLayer {
                                rotationY = flipRotation
                                cameraDistance = FLIP_CAMERA_DISTANCE * density
                            }
                    ) {
                        if (flipRotation <= 90f) {
                            NewsCard(
                                article = activeArticle,
                                position = "${currentIndex + 1} / ${articles.size}"
                            )
                        } else {
                            // Counter-rotated, otherwise the back face renders
                            // as a mirror image of itself.
                            NewsCardBack(
                                article = activeArticle,
                                modifier = Modifier.graphicsLayer { rotationY = 180f }
                            )
                        }
                    }

                    // Drag feedback, outside the flipping layer so it is never mirrored.
                    // Each stamp sits on the side the card moves AWAY from; on the
                    // leading side it would slide off-screen with the card.
                    if (dragX > 40f) {
                        SwipeBadge("READ →", Paper.colors.ink, Alignment.TopStart, -12f, dragX / 200f)
                    } else if (dragX < -40f) {
                        SwipeBadge("PASS", Paper.colors.muted, Alignment.TopEnd, 12f, -dragX / 200f)
                    }
                }
            }
        }
    }
}

/** The READ / PASS stamp that fades in while a card is dragged: an ink stamp on paper. */
@Composable
private fun BoxScope.SwipeBadge(
    text: String,
    color: Color,
    alignment: Alignment,
    angle: Float,
    strength: Float
) {
    Box(
        modifier = Modifier
            .align(alignment)
            .padding(top = 40.dp, start = 30.dp, end = 30.dp)
            .rotate(angle)
            .alpha(strength.coerceIn(0f, 1f))
            .border(2.5.dp, color, RoundedCornerShape(4.dp))
            .background(Paper.colors.card)
            .padding(horizontal = 16.dp, vertical = 4.dp)
    ) {
        Text(
            text = text,
            color = color,
            fontFamily = PaperSans,
            fontSize = 22.sp,
            letterSpacing = 1.5.sp,
            fontWeight = FontWeight.ExtraBold
        )
    }
}
