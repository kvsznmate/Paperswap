package com.newsswipe.app.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.newsswipe.app.data.model.NewsArticle
import com.newsswipe.app.data.model.SwipeMetrics
import com.newsswipe.app.data.model.SwipeRecordRequest
import com.newsswipe.app.data.remote.RetrofitInstance
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

sealed class NewsUiState {
    object Loading : NewsUiState()
    data class Success(val articles: List<NewsArticle>, val currentIndex: Int = 0) : NewsUiState()
    data class Error(val message: String) : NewsUiState()
}

class NewsViewModel : ViewModel() {

    private val _uiState = MutableStateFlow<NewsUiState>(NewsUiState.Loading)
    val uiState: StateFlow<NewsUiState> = _uiState.asStateFlow()

    private val historyStack = mutableListOf<Int>() // Stack of swiped indices for Undo

    init {
        loadNewsFeed()
    }

    fun loadNewsFeed() {
        viewModelScope.launch {
            _uiState.value = NewsUiState.Loading
            try {
                val response = RetrofitInstance.api.getNewsFeed()
                if (response.isSuccessful && response.body() != null) {
                    val articles = response.body()!!.articles
                    _uiState.value = NewsUiState.Success(articles = articles, currentIndex = 0)
                    historyStack.clear()
                } else {
                    _uiState.value = NewsUiState.Error("Server response error: ${response.code()}")
                }
            } catch (e: Exception) {
                _uiState.value = NewsUiState.Error("Failed to connect to Oracle VM (${e.localizedMessage})")
            }
        }
    }

    /**
     * @param metrics dwell time and flip state, when the swipe came from the card
     *   itself. Swipes from the bottom action buttons pass the default, whose
     *   fields are null -- those never had a dwell timer running, and reporting
     *   0ms would look like an instant dismissal to anything reading the column
     *   later. Null means unmeasured; see SwipeMetrics.
     */
    fun onSwipe(direction: String, metrics: SwipeMetrics = SwipeMetrics()) {
        val currentState = _uiState.value
        if (currentState is NewsUiState.Success) {
            val articles = currentState.articles
            val currentIndex = currentState.currentIndex

            if (currentIndex < articles.size) {
                val article = articles[currentIndex]
                val action = if (direction == "right") "read" else "pass"

                // Log swipe to backend DB
                viewModelScope.launch {
                    try {
                        RetrofitInstance.api.recordSwipe(
                            SwipeRecordRequest(
                                articleId = article.id,
                                action = action,
                                dwellMs = metrics.dwellMs,
                                flipped = metrics.flipped
                            )
                        )
                    } catch (e: Exception) {
                        // Silent fallback if log fails
                    }
                }

                historyStack.add(currentIndex)
                _uiState.value = currentState.copy(currentIndex = currentIndex + 1)
            }
        }
    }

    fun undoSwipe() {
        val currentState = _uiState.value
        if (currentState is NewsUiState.Success && historyStack.isNotEmpty()) {
            val lastIndex = historyStack.removeAt(historyStack.size - 1)
            _uiState.value = currentState.copy(currentIndex = lastIndex)
        }
    }
}
