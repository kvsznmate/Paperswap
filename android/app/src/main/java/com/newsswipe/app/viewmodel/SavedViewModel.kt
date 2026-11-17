package com.newsswipe.app.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import com.newsswipe.app.data.local.LocalStore
import com.newsswipe.app.data.model.NewsArticle
import com.newsswipe.app.data.model.SavedArticle
import kotlinx.coroutines.flow.StateFlow

/**
 * Saved for later. Purely local: nothing here talks to the server, and saving
 * is deliberately not a swipe, so it adds no row to swipe_events and does not
 * move the deck on.
 */
class SavedViewModel(application: Application) : AndroidViewModel(application) {

    private val store = LocalStore.get(application)

    val saved: StateFlow<List<SavedArticle>> = store.saved

    /** Returns true when the article is now saved. */
    fun toggle(article: NewsArticle): Boolean = store.toggleSaved(article)

    fun remove(articleId: Int) = store.removeSaved(articleId)
}
