package com.newsswipe.app.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.newsswipe.app.data.model.NewsCategory
import com.newsswipe.app.data.model.TopicSummary
import com.newsswipe.app.data.remote.RetrofitInstance
import com.newsswipe.app.data.remote.apiCall
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * State for the weekly summaries screen.
 *
 * One flat object rather than a sealed hierarchy: the topic chooser and the
 * detail pane load independently, and the chooser must stay on screen while the
 * detail is loading or has failed. A sealed state would force the whole screen
 * into one of those conditions.
 */
data class SummaryUiState(
    val topicsLoading: Boolean = false,
    val topicsError: String? = null,
    val topics: List<NewsCategory> = emptyList(),

    val selectedTopic: String? = null,
    val detailLoading: Boolean = false,
    val detailError: String? = null,

    /** The selected topic's weeks, newest first. Empty means none exist yet. */
    val weeks: List<TopicSummary> = emptyList(),
    val weekIndex: Int = 0
) {
    val currentWeek: TopicSummary? get() = weeks.getOrNull(weekIndex)

    /** Older weeks sit further down the list, so "earlier" means a higher index. */
    val hasEarlier: Boolean get() = weekIndex < weeks.size - 1
    val hasLater: Boolean get() = weekIndex > 0
}

class SummaryViewModel : ViewModel() {

    private companion object {
        /** Weeks of history fetched per topic. The backend caps this at 52. */
        const val WEEKS_OF_HISTORY = 8
    }

    private val _uiState = MutableStateFlow(SummaryUiState())
    val uiState: StateFlow<SummaryUiState> = _uiState.asStateFlow()

    // Weeks already fetched, keyed by topic slug. Summaries change once a week,
    // so refetching on every tap would only redisplay text that cannot have
    // changed. Only successful responses are cached. Cleared with the process.
    private val cache = mutableMapOf<String, List<TopicSummary>>()

    /**
     * Load the catalogue, unless it is already loaded or in flight.
     *
     * Called from the screen rather than from init: the ViewModel is created
     * when the app starts, and someone who never opens this tab should not cost
     * the server a request.
     */
    fun loadTopicsIfNeeded() {
        val state = _uiState.value
        if (state.topics.isNotEmpty() || state.topicsLoading) return
        loadTopics()
    }

    fun loadTopics() {
        _uiState.update { it.copy(topicsLoading = true, topicsError = null) }
        viewModelScope.launch {
            val result = apiCall { RetrofitInstance.api.getCategories() }
            _uiState.update { state ->
                result.fold(
                    onSuccess = { state.copy(topics = it.categories, topicsLoading = false) },
                    onFailure = { state.copy(topicsLoading = false, topicsError = it.message) }
                )
            }
        }
    }

    fun selectTopic(slug: String) {
        val cached = cache[slug]
        _uiState.update {
            it.copy(
                selectedTopic = slug,
                weeks = cached.orEmpty(),
                weekIndex = 0,
                detailLoading = cached == null,
                detailError = null
            )
        }
        if (cached != null) return

        viewModelScope.launch {
            val result = apiCall { RetrofitInstance.api.getTopicSummaries(slug, WEEKS_OF_HISTORY) }

            // Tapping two topics quickly can land the responses out of order.
            // Without this guard the slower one would show one topic's text
            // under another topic's heading.
            if (_uiState.value.selectedTopic != slug) return@launch

            result.onSuccess { cache[slug] = it.summaries }
            _uiState.update { state ->
                result.fold(
                    onSuccess = { state.copy(weeks = it.summaries, weekIndex = 0, detailLoading = false) },
                    onFailure = { state.copy(detailLoading = false, detailError = it.message) }
                )
            }
        }
    }

    /** Step through the topic's history. +1 goes earlier, -1 goes later. */
    fun stepWeek(delta: Int) {
        _uiState.update { state ->
            val next = state.weekIndex + delta
            if (next in state.weeks.indices) state.copy(weekIndex = next) else state
        }
    }

    /** Failures are never cached, so selecting the topic again refetches it. */
    fun retryDetail() {
        val slug = _uiState.value.selectedTopic ?: return
        selectTopic(slug)
    }
}
