package com.newsswipe.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Undo
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.newsswipe.app.ui.theme.PassRed
import com.newsswipe.app.ui.theme.ReadGreen
import com.newsswipe.app.ui.theme.UndoAmber

@Composable
fun ActionButtons(
    onPassClick: () -> Unit,
    onReadClick: () -> Unit,
    onUndoClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = 12.dp),
        horizontalArrangement = Arrangement.SpaceEvenly,
        verticalAlignment = Alignment.CenterVertically
    ) {
        // Undo Button
        Box(
            modifier = Modifier
                .size(52.dp)
                .clip(CircleShape)
                .background(Color(0xFF161E2E))
                .border(1.dp, UndoAmber.copy(alpha = 0.3f), CircleShape)
                .clickable { onUndoClick() },
            contentAlignment = Alignment.Center
        ) {
            Icon(
                imageVector = Icons.Default.Undo,
                contentDescription = "Undo",
                tint = UndoAmber,
                modifier = Modifier.size(24.dp)
            )
        }

        // Pass Button (✖)
        Box(
            modifier = Modifier
                .size(64.dp)
                .clip(CircleShape)
                .background(Color(0xFF161E2E))
                .border(1.dp, PassRed.copy(alpha = 0.4f), CircleShape)
                .clickable { onPassClick() },
            contentAlignment = Alignment.Center
        ) {
            Icon(
                imageVector = Icons.Default.Close,
                contentDescription = "Pass",
                tint = PassRed,
                modifier = Modifier.size(32.dp)
            )
        }

        // Read Button (💚)
        Box(
            modifier = Modifier
                .size(72.dp)
                .clip(CircleShape)
                .background(ReadGreen.copy(alpha = 0.15f))
                .border(1.5.dp, ReadGreen.copy(alpha = 0.6f), CircleShape)
                .clickable { onReadClick() },
            contentAlignment = Alignment.Center
        ) {
            Icon(
                imageVector = Icons.Default.Favorite,
                contentDescription = "Read",
                tint = ReadGreen,
                modifier = Modifier.size(36.dp)
            )
        }
    }
}
