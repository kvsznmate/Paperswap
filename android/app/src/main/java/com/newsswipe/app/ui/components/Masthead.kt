package com.newsswipe.app.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.newsswipe.app.ui.theme.Paper
import com.newsswipe.app.ui.theme.PaperSans
import com.newsswipe.app.ui.theme.PaperSerif
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * A newspaper masthead: serif title, a small-caps line under it, an optional
 * control on the right, and the double rule (thick over thin) beneath.
 */
@Composable
fun Masthead(
    title: String,
    subtitle: String,
    modifier: Modifier = Modifier,
    trailing: @Composable RowScope.() -> Unit = {}
) {
    val c = Paper.colors
    Column(modifier = modifier.fillMaxWidth().padding(horizontal = 20.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 16.dp, bottom = 10.dp),
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = title,
                    fontFamily = PaperSerif,
                    fontWeight = FontWeight.Bold,
                    fontSize = 26.sp,
                    lineHeight = 30.sp,
                    color = c.ink,
                    modifier = Modifier.semantics { heading() }
                )
                Text(
                    text = subtitle.uppercase(),
                    fontFamily = PaperSans,
                    fontSize = 11.sp,
                    letterSpacing = 0.9.sp,
                    color = c.muted
                )
            }
            trailing()
        }
        HorizontalDivider(thickness = 2.dp, color = c.ink)
        Spacer(Modifier.height(2.dp))
        HorizontalDivider(thickness = 1.dp, color = c.ink)
    }
}

/** "Tuesday 17 November", in the phone's language. */
fun todayLine(): String = SimpleDateFormat("EEEE d MMMM", Locale.getDefault()).format(Date())
