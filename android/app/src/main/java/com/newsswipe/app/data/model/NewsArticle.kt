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
    @SerializedName("card_filename") val cardFilename: String? = null,
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

data class SwipeRecordRequest(
    @SerializedName("article_id") val articleId: Int,
    @SerializedName("action") val action: String // 'read' or 'pass'
)

data class SwipeRecordResponse(
    @SerializedName("status") val status: String,
    @SerializedName("article_id") val articleId: Int,
    @SerializedName("action") val action: String
)
