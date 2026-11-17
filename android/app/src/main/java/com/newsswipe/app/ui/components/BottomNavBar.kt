package com.newsswipe.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.newsswipe.app.ui.theme.Paper
import com.newsswipe.app.ui.theme.PaperSans

enum class AppTab(val label: String) {
    DECK("Deck"),
    WEEK("This week"),
    SAVED("Saved")
}

/**
 * Bottom navigation between the three destinations. It replaced the segmented
 * pill at the top, which took 118 dp above the card and was the loudest thing
 * on the screen. The selected tab is marked by weight and an underline, not
 * colour alone.
 */
@Composable
fun BottomNavBar(
    selected: AppTab,
    savedCount: Int,
    onSelect: (AppTab) -> Unit,
    modifier: Modifier = Modifier
) {
    val c = Paper.colors
    Column(modifier = modifier.fillMaxWidth().background(c.ground)) {
        HorizontalDivider(thickness = 1.dp, color = c.rule)
        Row(modifier = Modifier.fillMaxWidth().selectableGroup()) {
            AppTab.values().forEach { tab ->
                val active = tab == selected
                val icon: ImageVector = when (tab) {
                    AppTab.DECK -> DeckIcon
                    AppTab.WEEK -> WeekIcon
                    AppTab.SAVED -> if (active) BookmarkFilledIcon else BookmarkIcon
                }
                val label = if (tab == AppTab.SAVED && savedCount > 0) "Saved · $savedCount" else tab.label
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .heightIn(min = 60.dp)
                        .selectable(selected = active, role = Role.Tab, onClick = { onSelect(tab) })
                        .padding(top = 8.dp, bottom = 10.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(3.dp)
                ) {
                    Icon(
                        icon,
                        contentDescription = null,
                        tint = if (active) c.ink else c.muted,
                        modifier = Modifier.size(22.dp)
                    )
                    Text(
                        text = label,
                        fontFamily = PaperSans,
                        fontSize = 12.sp,
                        fontWeight = if (active) FontWeight.Bold else FontWeight.Normal,
                        textDecoration = if (active) TextDecoration.Underline else null,
                        color = if (active) c.ink else c.muted
                    )
                }
            }
        }
    }
}
