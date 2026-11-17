package com.newsswipe.app.data.model

import com.google.gson.annotations.SerializedName

/**
 * A card the user saved for later, stored ON THE PHONE only (LocalStore).
 *
 * A copy of the fields the Saved screen shows, not a reference by id: the
 * server deletes articles after PURGE_OLDER_THAN_DAYS (9), and a saved story
 * must outlive that. Nothing here is ever sent to the server -- saving is not
 * a swipe and records nothing in swipe_events.
 *
 * Lives in data/model because Gson writes and reads it, and proguard-rules.pro
 * keeps only this package: anywhere else R8 renames the fields and every saved
 * article reads back as nulls in release builds.
 *
 * Fields are nullable because Gson can leave any of them null in a stored
 * entry from an older version; LocalStore drops entries without a url.
 */
data class SavedArticle(
    @SerializedName("id") val id: Int,
    @SerializedName("title") val title: String?,
    @SerializedName("url") val url: String?,
    @SerializedName("source") val source: String?,
    @SerializedName("category") val category: String?,
    @SerializedName("category_label") val categoryLabel: String?,
    @SerializedName("accent_color") val accentColor: String?,
    @SerializedName("saved_at_ms") val savedAtMs: Long
)
