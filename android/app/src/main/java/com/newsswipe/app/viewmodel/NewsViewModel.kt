package com.newsswipe.app.viewmodel

import android.app.Application
import android.os.SystemClock
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.newsswipe.app.data.local.LocalStore
import com.newsswipe.app.data.model.NewsArticle
import com.newsswipe.app.data.model.NewsCategory
import com.newsswipe.app.data.model.SwipeRecordRequest
import com.newsswipe.app.data.remote.RetrofitInstance
import com.newsswipe.app.data.remote.apiCall
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.UUID

sealed class NewsUiState {
    object Loading : NewsUiState()
    data class Success(
        val articles: List<NewsArticle>,
        val currentIndex: Int = 0,
        /** Set when a swipe could not be recorded; cleared by the next success. */
        val swipeNotice: String? = null
    ) : NewsUiState() {
        val currentArticle: NewsArticle? get() = articles.getOrNull(currentIndex)
        val canUndo: Boolean get() = currentIndex > 0
    }
    data class Error(val message: String) : NewsUiState()
}

class NewsViewModel(application: Application) : AndroidViewModel(application) {

    private companion object {
        /** Backend rejects dwell above an hour (the backgrounded-app case). */
        const val MAX_DWELL_MS = 3_600_000L

        /**
         * How long a swipe is held before it is sent. Undo inside this window
         * cancels the swipe outright, so the permanent swipe_events table never
         * records a swipe the user took back.
         */
        const val UNDO_WINDOW_MS = 5_000L
    }

    /** Identifies this app run to the server; see SwipeRecordRequest.sessionId. */
    private val sessionId = UUID.randomUUID().toString()

    private val _uiState = MutableStateFlow<NewsUiState>(NewsUiState.Loading)
    val uiState: StateFlow<NewsUiState> = _uiState.asStateFlow()

    private val store = LocalStore.get(application)

    /** Selected topic slugs, kept on the phone. Empty = every topic. */
    val selectedTopics: StateFlow<Set<String>> = store.topics

    private val _catalogue = MutableStateFlow<List<NewsCategory>>(emptyList())
    /** Topic catalogue for the filter sheet; loaded the first time it opens. */
    val catalogue: StateFlow<List<NewsCategory>> = _catalogue.asStateFlow()

    init {
        loadNewsFeed()
    }

    fun loadNewsFeed() {
        _uiState.value = NewsUiState.Loading
        // Sorted so the same choice always sends the same query string, which
        // the server and any edge cache key on.
        val categories = selectedTopics.value.sorted().joinToString(",").ifEmpty { null }
        viewModelScope.launch {
            apiCall { RetrofitInstance.api.getNewsFeed(categories) }
                .onSuccess {
                    _uiState.value = NewsUiState.Success(articles = it.articles)
                    resetCardSignals()
                }
                .onFailure { _uiState.value = NewsUiState.Error(it.message.orEmpty()) }
        }
    }

    // --- Topic filter (part of #19) --------------------------------------------

    fun loadCatalogueIfNeeded() {
        if (_catalogue.value.isNotEmpty()) return
        viewModelScope.launch {
            apiCall { RetrofitInstance.api.getCategories() }
                .onSuccess { _catalogue.value = it.categories }
        }
    }

    /**
     * Store the choice and reload the deck. Every topic selected is stored as
     * the empty set, so a topic added on the server later is included rather
     * than silently filtered out.
     */
    fun setTopics(slugs: Set<String>) {
        val all = _catalogue.value.map { it.slug }.toSet()
        val normalised = if (slugs.isEmpty() || (all.isNotEmpty() && slugs.containsAll(all))) emptySet() else slugs
        if (normalised == selectedTopics.value) return
        flushPendingSwipe()
        store.setTopics(normalised)
        loadNewsFeed()
    }

    // --- Swiping ---------------------------------------------------------------

    private var pendingSwipe: SwipeRecordRequest? = null
    private var pendingSwipeJob: Job? = null

    /** Swipe the current card: right ([read] = true) or left. */
    fun onSwipe(read: Boolean) {
        val state = _uiState.value as? NewsUiState.Success ?: return
        val article = state.currentArticle ?: return

        // Captured before the state update, so they describe the card being
        // swiped rather than the one that replaces it.
        val request = SwipeRecordRequest(
            articleId = article.id,
            action = if (read) "read" else "pass",
            dwellMs = currentDwellMs(),
            flipped = currentCardFlipped,
            sessionId = sessionId
        )

        // Only the latest swipe is undoable, so the previous one goes out now.
        flushPendingSwipe()
        pendingSwipe = request
        pendingSwipeJob = viewModelScope.launch {
            delay(UNDO_WINDOW_MS)
            pendingSwipeJob = null   // this job is finishing; do not cancel it
            flushPendingSwipe()
        }

        resetCardSignals()
        _uiState.value = state.copy(currentIndex = state.currentIndex + 1)
    }

    /**
     * Step back one card. If that card's swipe is still inside the undo window
     * it is cancelled; after that it has already been recorded and stays so.
     */
    fun undoSwipe() {
        val state = _uiState.value as? NewsUiState.Success ?: return
        if (!state.canUndo) return

        pendingSwipeJob?.cancel()
        pendingSwipeJob = null
        pendingSwipe = null

        resetCardSignals()
        _uiState.value = state.copy(currentIndex = state.currentIndex - 1)
    }

    /** Send the held swipe immediately, if there is one. */
    private fun flushPendingSwipe() {
        val request = pendingSwipe ?: return
        pendingSwipe = null
        pendingSwipeJob?.cancel()
        pendingSwipeJob = null
        viewModelScope.launch { sendSwipe(request) }
    }

    private suspend fun sendSwipe(request: SwipeRecordRequest) {
        val result = apiCall { RetrofitInstance.api.recordSwipe(request) }
        val notice = result.exceptionOrNull()?.let { "Swipe not saved: ${it.message}" }
        _uiState.update { if (it is NewsUiState.Success) it.copy(swipeNotice = notice) else it }
    }

    // --- Interaction signals ---------------------------------------------------
    //
    // dwell_ms counts only time the deck is actually on screen. The timer pauses
    // when the deck is hidden (Summaries tab, Custom Tab opened by a right swipe,
    // app in background), so that time is not charged to the next card.
    //
    // elapsedRealtime, not currentTimeMillis: monotonic, so a clock change
    // mid-session cannot produce a negative or absurd dwell.

    private var dwellAccumulatedMs = 0L
    private var visibleSinceMs: Long? = null
    private var currentCardFlipped = false

    /** The deck became visible (screen shown and app in the foreground). */
    fun onDeckShown() {
        if (visibleSinceMs == null) visibleSinceMs = SystemClock.elapsedRealtime()
    }

    /**
     * The deck stopped being visible. Also sends any held swipe now, because
     * once the app is backgrounded the ViewModel may be cleared before the undo
     * window ends, and viewModelScope dies with it.
     */
    fun onDeckHidden() {
        visibleSinceMs?.let { dwellAccumulatedMs += SystemClock.elapsedRealtime() - it }
        visibleSinceMs = null
        flushPendingSwipe()
    }

    /** Called by the card stack when the user turns the current card over. */
    fun onCardFlipped() {
        currentCardFlipped = true
    }

    private fun resetCardSignals() {
        dwellAccumulatedMs = 0L
        if (visibleSinceMs != null) visibleSinceMs = SystemClock.elapsedRealtime()
        currentCardFlipped = false
    }

    /**
     * Milliseconds the current card was on screen, or null if that is not a
     * usable measurement. Out-of-range values are dropped, not clamped --
     * clamping would invent a reading.
     */
    private fun currentDwellMs(): Int? {
        val running = visibleSinceMs?.let { SystemClock.elapsedRealtime() - it } ?: 0L
        val total = dwellAccumulatedMs + running
        return if (total in 1..MAX_DWELL_MS) total.toInt() else null
    }
}
