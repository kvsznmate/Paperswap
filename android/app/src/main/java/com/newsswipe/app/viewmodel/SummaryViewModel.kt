package com.newsswipe.app.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.newsswipe.app.data.model.NewsCategory
import com.newsswipe.app.data.model.TopicSummary
import com.newsswipe.app.data.model.WeeklySummariesResponse
import com.newsswipe.app.data.remote.RetrofitInstance
import com.newsswipe.app.data.remote.apiCall
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import java.util.TimeZone

/**
 * State for the "This week" edition: every topic's digest for one week.
 *
 * `weekStart` is the Monday on screen. It is null only before the first load
 * or when no week has a summary yet. `newestWeekStart` is the newest week the
 * server has, so the screen knows when "next week" has nothing after it.
 */
data class WeekUiState(
    val loading: Boolean = false,
    val error: String? = null,
    val loaded: Boolean = false,
    val weekStart: String? = null,
    val weekEnd: String? = null,
    val summaries: List<TopicSummary> = emptyList(),
    val topicsMissing: List<String> = emptyList(),
    val newestWeekStart: String? = null,
    /** Topic catalogue, in the server's order. Labels the missing topics. */
    val catalogue: List<NewsCategory> = emptyList()
) {
    /** ISO dates compare correctly as strings. */
    val hasLater: Boolean
        get() = weekStart != null && newestWeekStart != null && weekStart < newestWeekStart

    /** A week with no summaries at all is treated as the start of history. */
    val hasEarlier: Boolean get() = weekStart != null && summaries.isNotEmpty()
}

class SummaryViewModel : ViewModel() {

    private val _uiState = MutableStateFlow(WeekUiState())
    val uiState: StateFlow<WeekUiState> = _uiState.asStateFlow()

    // Weeks already fetched, keyed by Monday. A past week's summaries never
    // change, so stepping back and forth costs no further requests. Only
    // successful responses are cached. Cleared with the process.
    private val cache = mutableMapOf<String, WeeklySummariesResponse>()

    /**
     * First visit only. Called from the screen rather than from init: someone
     * who never opens this tab should not cost the server a request.
     */
    fun loadIfNeeded() {
        val s = _uiState.value
        if (s.loaded || s.loading) return
        load(null)
        loadCatalogue()
    }

    fun retry() = load(_uiState.value.weekStart)

    /** -1 = the week before, +1 = the week after. */
    fun stepWeek(delta: Int) {
        val s = _uiState.value
        val current = s.weekStart ?: return
        if (delta < 0 && !s.hasEarlier) return
        if (delta > 0 && !s.hasLater) return
        load(shiftWeek(current, delta) ?: return)
    }

    private fun load(weekStart: String?) {
        weekStart?.let { cache[it] }?.let { show(it, weekStart); return }

        _uiState.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            val result = apiCall { RetrofitInstance.api.getWeeklySummaries(weekStart) }
            result.onSuccess { response ->
                (response.weekStart ?: weekStart)?.let { cache[it] = response }
                show(response, weekStart)
            }.onFailure { error ->
                _uiState.update { it.copy(loading = false, error = error.message) }
            }
        }
    }

    private fun show(response: WeeklySummariesResponse, requested: String?) {
        _uiState.update { s ->
            val shown = response.weekStart ?: requested
            s.copy(
                loading = false,
                error = null,
                loaded = true,
                weekStart = shown,
                weekEnd = response.weekEnd,
                summaries = response.summaries.orEmpty(),
                topicsMissing = response.topicsMissing.orEmpty(),
                // The newest week is whatever the no-argument request returned.
                newestWeekStart = if (requested == null) shown else s.newestWeekStart
            )
        }
    }

    private fun loadCatalogue() {
        viewModelScope.launch {
            apiCall { RetrofitInstance.api.getCategories() }
                .onSuccess { cats -> _uiState.update { it.copy(catalogue = cats.categories) } }
        }
    }

    /**
     * Calendar rather than java.time: minSdk is 24 without core library
     * desugaring, so LocalDate would crash on API 24-25. UTC throughout, because
     * the backend anchors weeks to UTC Mondays.
     */
    private fun shiftWeek(iso: String, weeks: Int): String? = runCatching {
        val fmt = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }
        val cal = Calendar.getInstance(TimeZone.getTimeZone("UTC"))
        cal.time = fmt.parse(iso) ?: return null
        cal.add(Calendar.DAY_OF_MONTH, 7 * weeks)
        fmt.format(cal.time)
    }.getOrNull()
}
