package com.newsswipe.app.data.model

import com.google.gson.annotations.SerializedName

// Only the fields the app reads are declared. Gson ignores the rest of the
// payload, so the backend can add fields without breaking the client.

data class NewsFeedResponse(
    @SerializedName("articles") val articles: List<NewsArticle>
)

data class NewsArticle(
    @SerializedName("id") val id: Int,
    @SerializedName("title") val title: String,
    // The publisher's blurb (or a generated fallback). Shown on the card front.
    @SerializedName("description") val description: String,
    @SerializedName("source") val source: String,
    @SerializedName("published_at") val publishedAt: String,
    @SerializedName("category") val category: String,
    // Human-readable topic name and brand colour, supplied per article by the
    // backend so new topics need no Android release. Nullable for older APIs.
    @SerializedName("category_label") val categoryLabel: String? = null,
    @SerializedName("accent_color") val accentColor: String? = null,
    // The publisher's own photo, or "" when it sent none or forbids
    // hot-linking (ADR-016). The backend no longer stores stock filler.
    @SerializedName("image_url") val imageUrl: String,
    @SerializedName("url") val url: String,

    // Coverage of the same story by other publishers, matched by the backend
    // from article embeddings (ADR-015) and shown on the card back. Empty is
    // normal: most stories are covered by one outlet, and new articles are
    // matched every 6 hours.
    //
    // Nullable rather than defaulted: Gson skips Kotlin default values, so an
    // older backend that omits the key would leave a non-null List null anyway.
    // Call sites use .orEmpty().
    @SerializedName("related") val related: List<RelatedArticle>? = null,

    // Where `description` came from: "publisher", or one of the backend's
    // canned tiers ("keyword_template", "topic_fallback"). The card hides a
    // canned blurb, because it reads as a summary of this story and is not.
    // Null = not recorded (older row or backend); the blurb is then shown.
    @SerializedName("description_source") val descriptionSource: String? = null
)

/** Another publisher's article on the same story: headline, publisher, link. */
data class RelatedArticle(
    @SerializedName("id") val id: Int,
    @SerializedName("title") val title: String,
    @SerializedName("source") val source: String,
    @SerializedName("url") val url: String
)

data class CategoriesResponse(
    @SerializedName("categories") val categories: List<NewsCategory>
)

data class NewsCategory(
    @SerializedName("slug") val slug: String,
    @SerializedName("label") val label: String,
    @SerializedName("accent_color") val accentColor: String
)

data class SwipeRecordRequest(
    @SerializedName("article_id") val articleId: Int,
    @SerializedName("action") val action: String, // "read" or "pass"

    // Recommender signals. Both are OMITTED when unmeasured, never sent as
    // 0/false: the backend stores NULL as "not measured", and a fabricated zero
    // would poison the training set.
    @SerializedName("dwell_ms") val dwellMs: Int? = null,
    @SerializedName("flipped") val flipped: Boolean? = null,

    // Random per app run. The server stores at most one swipe per session per
    // article, so a retried or replayed POST cannot add a second row.
    @SerializedName("session_id") val sessionId: String? = null
)
