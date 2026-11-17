package com.newsswipe.app.ui.components

import androidx.compose.material.icons.materialIcon
import androidx.compose.material.icons.materialPath
import androidx.compose.ui.graphics.vector.ImageVector

/**
 * Material "Undo" (filled), copied from androidx material-icons-extended 1.6.1
 * (Apache-2.0).
 *
 * Not in material-icons-core, and depending on the extended artifact for it pulled several thousand icon
 * classes into the APK. The other line icons are in PaperIcons.kt.
 */
val UndoIcon: ImageVector by lazy {
    materialIcon(name = "Filled.Undo") {
        materialPath {
            moveTo(12.5f, 8.0f)
            curveToRelative(-2.65f, 0.0f, -5.05f, 0.99f, -6.9f, 2.6f)
            lineTo(2.0f, 7.0f)
            verticalLineToRelative(9.0f)
            horizontalLineToRelative(9.0f)
            lineToRelative(-3.62f, -3.62f)
            curveToRelative(1.39f, -1.16f, 3.16f, -1.88f, 5.12f, -1.88f)
            curveToRelative(3.54f, 0.0f, 6.55f, 2.31f, 7.6f, 5.5f)
            lineToRelative(2.37f, -0.78f)
            curveTo(21.08f, 11.03f, 17.15f, 8.0f, 12.5f, 8.0f)
            close()
        }
    }
}
