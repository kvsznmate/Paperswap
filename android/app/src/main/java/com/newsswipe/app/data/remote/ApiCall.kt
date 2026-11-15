package com.newsswipe.app.data.remote

import kotlinx.coroutines.CancellationException
import retrofit2.Response

/** A failed backend call, with a message fit to show the user. */
class ApiException(message: String, cause: Throwable? = null) : Exception(message, cause)

/**
 * Run one Retrofit call and fold every way it can fail into a [Result]:
 * a non-2xx status, an empty body, or a network exception.
 *
 * Coroutine cancellation is rethrown rather than reported, so leaving a screen
 * mid-request never surfaces as an error message.
 */
suspend fun <T> apiCall(block: suspend () -> Response<T>): Result<T> =
    try {
        val response = block()
        val body = response.body()
        if (response.isSuccessful && body != null) {
            Result.success(body)
        } else {
            Result.failure(ApiException("Server responded with HTTP ${response.code()}"))
        }
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        Result.failure(ApiException(e.localizedMessage ?: "Could not reach the server.", e))
    }
