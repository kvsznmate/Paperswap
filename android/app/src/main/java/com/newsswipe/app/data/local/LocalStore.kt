package com.newsswipe.app.data.local

import android.content.Context
import com.google.gson.Gson
import com.newsswipe.app.data.model.NewsArticle
import com.newsswipe.app.data.model.SavedArticle
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * What the app keeps on the phone: saved articles and the topic filter.
 *
 * SharedPreferences + Gson rather than DataStore or Room: both are a few
 * hundred entries at most, Gson is already a dependency, and every added
 * library is APK size the release build fought to remove. Writes use apply(),
 * so they never block the UI thread.
 *
 * One instance per process (get), so the deck and the Saved screen observe the
 * same StateFlow and a save on one shows up on the other at once.
 */
class LocalStore private constructor(context: Context) {

    private val prefs = context.applicationContext
        .getSharedPreferences("paperswap", Context.MODE_PRIVATE)
    private val gson = Gson()

    private val _saved = MutableStateFlow(readSaved())
    /** Newest first. */
    val saved: StateFlow<List<SavedArticle>> = _saved.asStateFlow()

    private val _topics = MutableStateFlow(prefs.getStringSet(KEY_TOPICS, null)?.toSet().orEmpty())
    /** Selected topic slugs. Empty means every topic: no filter. */
    val topics: StateFlow<Set<String>> = _topics.asStateFlow()

    fun isSaved(articleId: Int): Boolean = _saved.value.any { it.id == articleId }

    /** Save the article, or un-save it if already saved. Returns the new state. */
    fun toggleSaved(article: NewsArticle): Boolean {
        val current = _saved.value
        val next = if (current.any { it.id == article.id }) {
            current.filterNot { it.id == article.id }
        } else {
            val entry = SavedArticle(
                id = article.id,
                title = article.title,
                url = article.url,
                source = article.source,
                category = article.category,
                categoryLabel = article.categoryLabel,
                accentColor = article.accentColor,
                savedAtMs = System.currentTimeMillis()
            )
            (listOf(entry) + current).take(MAX_SAVED)
        }
        writeSaved(next)
        return next.any { it.id == article.id }
    }

    fun removeSaved(articleId: Int) {
        writeSaved(_saved.value.filterNot { it.id == articleId })
    }

    fun setTopics(slugs: Set<String>) {
        _topics.value = slugs
        prefs.edit().putStringSet(KEY_TOPICS, slugs).apply()
    }

    private fun writeSaved(list: List<SavedArticle>) {
        _saved.value = list
        prefs.edit().putString(KEY_SAVED, gson.toJson(list.toTypedArray())).apply()
    }

    /**
     * An array class, not a TypeToken: R8 full mode (on by default with AGP 8)
     * strips the generic signature an anonymous TypeToken subclass relies on.
     * A corrupt or older value yields an empty list rather than a crash.
     */
    private fun readSaved(): List<SavedArticle> = runCatching {
        val json = prefs.getString(KEY_SAVED, null) ?: return emptyList()
        gson.fromJson(json, Array<SavedArticle>::class.java)
            ?.filter { !it.url.isNullOrBlank() }
            .orEmpty()
    }.getOrDefault(emptyList())

    companion object {
        private const val KEY_SAVED = "saved_articles"
        private const val KEY_TOPICS = "topic_filter"

        /** Oldest saves are dropped past this, so the file cannot grow forever. */
        private const val MAX_SAVED = 200

        @Volatile private var instance: LocalStore? = null

        fun get(context: Context): LocalStore =
            instance ?: synchronized(this) {
                instance ?: LocalStore(context).also { instance = it }
            }
    }
}
