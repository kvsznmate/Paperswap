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
    @SerializedName("image_url") val imageUrl: String,
    @SerializedName("url") val url: String,

    // Key sentences picked by the backend enrichment job (trafilatura body
    // extraction + TextRank), shown on the card back. Empty is normal: the job
    // runs every 6 hours, and extraction fails on paywalled or JS-only pages.
    //
    // Nullable rather than defaulted: Gson skips Kotlin default values, so an
    // older backend that omits the key would leave a non-null List null anyway.
    // Call sites use .orEmpty().
    @SerializedName("summary_bullets") val summaryBullets: List<String>? = null
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
    @SerializedName("flipped") val flipped: Boolean? = null
)
