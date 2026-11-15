package com.newsswipe.app.data.remote

import com.newsswipe.app.data.model.CategoriesResponse
import com.newsswipe.app.data.model.NewsFeedResponse
import com.newsswipe.app.data.model.SwipeRecordRequest
import com.newsswipe.app.data.model.TopicSummaryResponse
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

/** The public endpoints the app uses. Admin endpoints are deliberately absent. */
interface NewsApiService {

    @GET("api/v1/feed")
    suspend fun getNewsFeed(): Response<NewsFeedResponse>

    /** The response body is only an echo of the request, so it is not parsed. */
    @POST("api/v1/swipe")
    suspend fun recordSwipe(@Body request: SwipeRecordRequest): Response<Unit>

    /**
     * The topic catalogue: label and brand colour per topic. Drives the topic
     * chooser on the summaries screen. Server-driven, so a new topic appears
     * without an app release (ADR-006).
     */
    @GET("api/v1/categories")
    suspend fun getCategories(): Response<CategoriesResponse>

    /**
     * One topic's weekly digests, newest week first.
     *
     * Per topic rather than the all-topics `/api/v1/summaries`. A topic is
     * skipped in any week where too few of its articles had a real publisher
     * description, so the newest available week differs between topics.
     */
    @GET("api/v1/summaries/{topic}")
    suspend fun getTopicSummaries(
        @Path("topic") topic: String,
        @Query("weeks") weeks: Int
    ): Response<TopicSummaryResponse>
}
