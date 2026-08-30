package com.newsswipe.app.data.model

import com.google.gson.annotations.SerializedName

data class NewsFeedResponse(
    @SerializedName("status") val status: String,
    @SerializedName("count") val count: Int,
    @SerializedName("articles") val articles: List<NewsArticle>
)

data class NewsArticle(
    @SerializedName("id") val id: Int,
    @SerializedName("title") val title: String,
    @SerializedName("description") val description: String,
    @SerializedName("source") val source: String,
    @SerializedName("published_at") val publishedAt: String,
    @SerializedName("category") val category: String,
    // Human-readable topic name and brand colour, both supplied by the backend
    // so new topics don't require an Android release. Nullable for older APIs.
    @SerializedName("category_label") val categoryLabel: String? = null,
    @SerializedName("accent_color") val accentColor: String? = null,
    @SerializedName("image_url") val imageUrl: String,
    @SerializedName("url") val url: String,
    // Extractive bullets from the nightly enrichment job (docs/SUMMARIZATION.md).
    // The backend always sends an array -- _decorate_articles yields [] rather
    // than null for an unenriched article -- but this stays nullable so an older
    // API build that omits the field entirely still deserialises. Either way the
    // card back has to render without it.
    @SerializedName("summary_bullets") val summaryBullets: List<String>? = null,
    @SerializedName("index") val index: Int? = 1
)

data class CategoriesResponse(
    @SerializedName("status") val status: String,
    @SerializedName("count") val count: Int,
    @SerializedName("categories") val categories: List<NewsCategory>
)

data class NewsCategory(
    @SerializedName("slug") val slug: String,
    @SerializedName("label") val label: String,
    @SerializedName("accent_color") val accentColor: String,
    @SerializedName("sort_order") val sortOrder: Int,
    @SerializedName("article_count") val articleCount: Int
)

/**
 * Interaction signals captured alongside a swipe.
 *
 * Both fields are nullable and default to null, deliberately. A swipe made from
 * the bottom action buttons never had a dwell timer or a flip observed, so it
 * reports null rather than 0/false. The backend treats null as "not measured"
 * and any model trained on the column must filter it out; sending 0 would
 * manufacture a measurement. See ADR-010 and ADR-012.
 */
data class SwipeMetrics(
    val dwellMs: Long? = null,
    val flipped: Boolean? = null
)

data class SwipeRecordRequest(
    @SerializedName("article_id") val articleId: Int,
    @SerializedName("action") val action: String, // 'read' or 'pass'
    // Gson omits nulls from the body by default, so an unmeasured swipe sends
    // neither key and the server's Optional fields stay None.
    @SerializedName("dwell_ms") val dwellMs: Long? = null,
    @SerializedName("flipped") val flipped: Boolean? = null
)

data class SwipeRecordResponse(
    @SerializedName("status") val status: String,
    @SerializedName("article_id") val articleId: Int,
    @SerializedName("action") val action: String
)
