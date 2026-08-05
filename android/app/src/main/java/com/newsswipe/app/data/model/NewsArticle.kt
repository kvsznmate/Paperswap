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
    @SerializedName("image_url") val imageUrl: String,
    @SerializedName("url") val url: String,
    @SerializedName("card_filename") val cardFilename: String? = null,
    @SerializedName("index") val index: Int? = 1
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
