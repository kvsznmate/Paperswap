package com.newsswipe.app.data.remote

import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object RetrofitInstance {

    // Tailscale address of the backend VM. Stable across Oracle's ephemeral
    // public-IP rotations, and reachable from any network the phone is on as
    // long as Tailscale is connected. Requires the device to be signed into
    // the same tailnet -- the app will not work for anyone outside it.
    private const val BASE_URL = "http://100.67.178.57:8000/"

    private val loggingInterceptor = HttpLoggingInterceptor().apply {
        level = HttpLoggingInterceptor.Level.BODY
    }

    private val client = OkHttpClient.Builder()
        .addInterceptor(loggingInterceptor)
        // Tailscale adds a little latency on first connect while the tunnel
        // is established, so allow more headroom than a plain LAN call.
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build()

    val api: NewsApiService by lazy {
        Retrofit.Builder()
            .baseUrl(BASE_URL)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(NewsApiService::class.java)
    }
}
