package com.newsswipe.app.ui.components

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import com.newsswipe.app.data.model.NewsArticle

/** Duration of the flip, in ms. Also the delay before a tap can be re-registered. */
const val FLIP_DURATION_MS = 450

/**
 * A news card that rotates about its vertical axis to reveal the summary on the
 * reverse.
 *
 * Owns the animation only. Whether the card is flipped is hoisted to
 * SwipeableCardStack, which needs the same state to report the `flipped` signal
 * with the swipe and to reset it when the deck advances.
 *
 * Three things make this look right rather than merely work:
 *
 * 1. `cameraDistance`. Compose defaults to 8f, which is a very near viewpoint --
 *    on a card this large the near edge balloons and the far edge collapses,
 *    reading as a fisheye rather than a turning card. Raising it flattens the
 *    perspective toward orthographic. 14f is a compromise: enough depth to see
 *    it as a rotation, not so much that it looks like a crossfade.
 *
 * 2. Faces swap at exactly 90 degrees. Past the perpendicular the layer is
 *    mirrored, so the back gets a compensating 180-degree rotation. Rendering
 *    both faces at once and relying on alpha instead produces visible ghosting
 *    through the semi-transparent gradient during the middle of the turn.
 *
 * 3. Only one face is composed at a time. The alternative -- keeping both in the
 *    tree -- means Coil holds the hero image for a card whose front is not
 *    visible, on a device where the deck already keeps a second card alive for
 *    the stack effect.
 */
@Composable
fun FlippableNewsCard(
    article: NewsArticle,
    flipped: Boolean,
    modifier: Modifier = Modifier
) {
    val rotation by animateFloatAsState(
        targetValue = if (flipped) 180f else 0f,
        animationSpec = tween(durationMillis = FLIP_DURATION_MS, easing = FastOutSlowInEasing),
        label = "cardFlipRotation"
    )

    Box(
        modifier = modifier
            .fillMaxSize()
            .graphicsLayer {
                rotationY = rotation
                cameraDistance = 14f * density
            }
    ) {
        if (rotation <= 90f) {
            NewsCard(article = article)
        } else {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .graphicsLayer { rotationY = 180f }
            ) {
                NewsCardBack(article = article)
            }
        }
    }
}
