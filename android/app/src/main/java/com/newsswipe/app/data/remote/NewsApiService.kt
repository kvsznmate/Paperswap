package com.newsswipe.app.data.remote

import com.newsswipe.app.data.model.NewsFeedResponse
import com.newsswipe.app.data.model.SwipeRecordRequest
import com.newsswipe.app.data.model.SwipeRecordResponse
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

interface NewsApiService {

    @GET("api/v1/feed")
    suspend fun getNewsFeed(): Response<NewsFeedResponse>

    // NOTE: refreshCards() was removed. The refresh endpoint is now
    // POST /api/v1/cards/refresh, requires an X-API-Key admin header, and
    // returns 202 with no article payload. It is an operator action, not
    // something the app should trigger -- the server refreshes on a schedule.
    // The method was dead code here (never called from NewsViewModel).

    @POST("api/v1/swipe")
    suspend fun recordSwipe(@Body request: SwipeRecordRequest): Response<SwipeRecordResponse>
}
