package com.newsswipe.app.data.model

import com.google.gson.annotations.SerializedName

/**
 * Every topic's digest for one week, from GET /api/v1/summaries. Drives the
 * "This week" edition screen.
 *
 * `weekStart` is null only when the newest week was asked for and none exists
 * yet. `topicsMissing` names topics with no row that week -- too few articles
 * with a real publisher description -- so the screen can say so rather than
 * silently leaving them out.
 */
data class WeeklySummariesResponse(
    @SerializedName("week_start") val weekStart: String? = null,
    @SerializedName("week_end") val weekEnd: String? = null,
    @SerializedName("topics_missing") val topicsMissing: List<String>? = null,
    @SerializedName("summaries") val summaries: List<TopicSummary>? = null
)

/** One topic's history from GET /api/v1/summaries/{topic}. The app no longer calls it. */
data class TopicSummaryResponse(
    @SerializedName("summaries") val summaries: List<TopicSummary>
)

/**
 * One topic's digest for one completed Monday-Sunday week.
 *
 * The table outlives the articles it was built from -- those are purged after
 * nine days -- so these objects are the only record of a week that exists
 * anywhere.
 */
data class TopicSummary(
    @SerializedName("topic") val topic: String,
    @SerializedName("topic_label") val topicLabel: String,
    @SerializedName("accent_color") val accentColor: String?,
    @SerializedName("week_start") val weekStart: String,
    @SerializedName("week_end") val weekEnd: String,
    @SerializedName("summary") val summary: String,

    // TWO counts, and they are not interchangeable.
    //
    // numberOfArticles is what the generator actually read. articlesInWindow is
    // what the topic collected that week, before articles carrying a canned
    // description rather than the publisher's own were filtered out. Showing
    // only the first hides how thin a week was; showing only the second puts a
    // number next to prose that does not describe it. Neither can be recomputed
    // once the articles are purged, so the UI shows both.
    @SerializedName("number_of_articles") val numberOfArticles: Int,
    @SerializedName("articles_in_window") val articlesInWindow: Int,

    @SerializedName("top_sources") val topSources: List<String>? = null,

    // Which generator wrote the prose, e.g. "extractive:v1" or
    // "anthropic:<model>". Surfaced in the UI rather than hidden: a generated
    // paragraph should say so, and the two kinds coexist in the history.
    @SerializedName("generator") val generator: String,

    // Server-supplied provenance line. Nullable so an older backend simply
    // omits it instead of the client inventing a claim about how it was built.
    @SerializedName("basis") val basis: String? = null
) {
    /** True when boilerplate was filtered out, i.e. the two counts disagree. */
    val wasFiltered: Boolean get() = numberOfArticles != articlesInWindow
}
