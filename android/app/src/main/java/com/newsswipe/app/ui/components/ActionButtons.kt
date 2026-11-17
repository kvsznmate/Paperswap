package com.newsswipe.app.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.newsswipe.app.ui.theme.Paper
import com.newsswipe.app.ui.theme.PaperSans

/**
 * Undo · Pass · Read → · Save.
 *
 * Words, not a cross and a heart: Read opens the article, which a heart does
 * not say. Pass and Read do exactly what the left and right swipes do. Save
 * keeps the card on the phone and does NOT move the deck on -- it is not a
 * swipe and records nothing on the server.
 */
@Composable
fun ActionButtons(
    saved: Boolean,
    canUndo: Boolean,
    onUndoClick: () -> Unit,
    onPassClick: () -> Unit,
    onReadClick: () -> Unit,
    onSaveClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    val c = Paper.colors
    val square = PaddingValues(0.dp)

    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        OutlinedButton(
            onClick = onUndoClick,
            enabled = canUndo,
            shape = CardShape,
            border = BorderStroke(1.dp, c.rule),
            contentPadding = square,
            colors = ButtonDefaults.outlinedButtonColors(contentColor = c.muted),
            modifier = Modifier.width(48.dp).height(52.dp)
                .semantics { contentDescription = "Undo the last card" }
        ) {
            Icon(UndoIcon, contentDescription = null, modifier = Modifier.size(20.dp))
        }

        OutlinedButton(
            onClick = onPassClick,
            shape = CardShape,
            border = BorderStroke(1.5.dp, c.ink),
            colors = ButtonDefaults.outlinedButtonColors(contentColor = c.ink),
            modifier = Modifier.weight(1f).height(52.dp)
        ) {
            Text("Pass", fontFamily = PaperSans, fontWeight = FontWeight.Bold, fontSize = 16.sp)
        }

        Button(
            onClick = onReadClick,
            shape = CardShape,
            colors = ButtonDefaults.buttonColors(containerColor = c.ink, contentColor = c.onInk),
            modifier = Modifier.weight(1f).height(52.dp)
        ) {
            Text("Read →", fontFamily = PaperSans, fontWeight = FontWeight.Bold, fontSize = 16.sp)
        }

        OutlinedButton(
            onClick = onSaveClick,
            shape = CardShape,
            border = BorderStroke(1.dp, c.rule),
            contentPadding = square,
            colors = ButtonDefaults.outlinedButtonColors(contentColor = c.ink),
            modifier = Modifier.width(48.dp).height(52.dp).semantics {
                contentDescription = if (saved) "Remove from Saved" else "Save for later"
                stateDescription = if (saved) "Saved" else "Not saved"
            }
        ) {
            Icon(
                if (saved) BookmarkFilledIcon else BookmarkIcon,
                contentDescription = null,
                modifier = Modifier.size(20.dp)
            )
        }
    }
}
