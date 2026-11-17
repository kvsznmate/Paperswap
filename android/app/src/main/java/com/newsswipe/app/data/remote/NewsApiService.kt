package com.newsswipe.app.data.remote

import com.newsswipe.app.data.model.CategoriesResponse
import com.newsswipe.app.data.model.NewsFeedResponse
import com.newsswipe.app.data.model.SwipeRecordRequest
import com.newsswipe.app.data.model.WeeklySummariesResponse
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Query

/** The public endpoints the app uses. Admin endpoints are deliberately absent. */
interface NewsApiService {

    /**
     * The deck. `categories` is a comma-separated topic filter ("SPORTS,SCIENCE");
     * null sends no parameter, which means every topic.
     */
    @GET("api/v1/feed")
    suspend fun getNewsFeed(@Query("categories") categories: String? = null): Response<NewsFeedResponse>

    /** The response body is only an echo of the request, so it is not parsed. */
    @POST("api/v1/swipe")
    suspend fun recordSwipe(@Body request: SwipeRecordRequest): Response<Unit>

    /**
     * The topic catalogue: label and brand colour per topic. Drives the topic
     * filter and the section headings on the week screen. Server-driven, so a new topic appears
     * without an app release (ADR-006).
     */
    @GET("api/v1/categories")
    suspend fun getCategories(): Response<CategoriesResponse>

    /**
     * Every topic's digest for one week. `weekStart` is any ISO date inside the
     * wanted week; null asks for the newest week that has summaries.
     */
    @GET("api/v1/summaries")
    suspend fun getWeeklySummaries(
        @Query("week_start") weekStart: String? = null
    ): Response<WeeklySummariesResponse>
}
