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

    @GET("api/v1/cards/refresh")
    suspend fun refreshCards(): Response<NewsFeedResponse>

    @POST("api/v1/swipe")
    suspend fun recordSwipe(@Body request: SwipeRecordRequest): Response<SwipeRecordResponse>
}
