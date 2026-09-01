"""Weekly topic summaries: the numbers must describe the prose sitting next to
them, and the prose must not be an artefact of the fetcher's fallback strings.

Claims under test:
  1. Week arithmetic. last_completed_week_start() returns a Monday on every
     weekday, never the current partial week, and survives year boundaries.
  2. The window is half-open [Mon 00:00Z, next Mon 00:00Z). Sunday 23:59:59 is
     in; the microsecond before Monday is out. Anchored to UTC explicitly, not
     to the container's clock.
  3. Count integrity. number_of_articles is what the generator READ;
     articles_in_window is what the topic saw. They differ by exactly the
     boilerplate that was filtered out.
  4. Boilerplate exclusion. Canned descriptions from generate_short_summary's
     tier 2 and tier 3 never reach the generator -- including on legacy rows
     with a NULL description_source, which are caught by LIKE matching.
  5. Idempotency. Running the job twice yields one row per (topic, week),
     updated rather than duplicated.
  6. The floor. A topic below MIN_ARTICLES_FOR_SUMMARY gets no row at all.
  7. Failure writes nothing. A raising generator leaves no row -- never a
     placeholder, which after the purge is indistinguishable from a real summary.
  8. Ordering and retention. The summariser runs BEFORE the purge, the retention
     window leaves margin over the summariser's first opportunity, and a summary
     outlives the articles it was built from.
  9. The FK and CHECK reject rows the schema should not accept.

On (3) and (7): these are the ADR-010 checks in this feature's clothing. Once
purge_old_articles has run, the summary row is the only surviving record of the
week -- nothing downstream can recompute the count or re-derive the text. A
number that is wrong at write time is wrong forever, and a placeholder written
"temporarily" becomes permanent the moment the sources are deleted.

FIXTURE ISOLATION -- read this before changing a date below.

Every count assertion here compares against a number derived from the fixtures
alone. This suite runs against whatever DATABASE_URL points at, which in
practice is production (tests/README.md), so a single live article inside a
fixture window makes those assertions wrong -- and wrong in a way that reads as
a filtering bug rather than as contamination.

An earlier version of this file used a fixture week close to the present. On a
live box that week held 43 real articles, and six count checks failed while the
filtering logic they were meant to guard was working perfectly.

Fixture weeks are therefore YEARS in the past, where PURGE_OLDER_THAN_DAYS
guarantees nothing real can survive. require_empty_week() asserts that rather
than assuming it, so a future change to retention produces a clear message
instead of six confusing ones.

Section 7 prints a RuntimeError traceback. That is the deliberately simulated
generator failure being logged with exc_info, which is what production does; it
is not a test error.

Run:  python tests/test_topic_summaries.py
"""
import os
import sys
import inspect
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
import news_fetcher as nf
import summarizer as sm

fails = []
TOPIC = "TECH"

# All Mondays, all far outside any plausible retention window.
WEEK = datetime.date(2019, 1, 7)          # main fixture week
WEEK_END = datetime.date(2019, 1, 13)
THIN_WEEK = datetime.date(2019, 2, 4)     # the MIN_ARTICLES floor
FAIL_WEEK = datetime.date(2019, 3, 4)     # the raising generator
ORDER_WEEK = datetime.date(2019, 4, 1)    # ordering / outlives-its-sources
SCHEMA_WEEK = datetime.date(2019, 4, 29)  # FK and CHECK violations

ALL_FIXTURE_WEEKS = [WEEK, THIN_WEEK, FAIL_WEEK, ORDER_WEEK, SCHEMA_WEEK]
FIXTURE_URL_PREFIX = "https://example.com/summaries/"


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(name)


def section(title):
    print(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
# Fixtures
#
# Articles go in through save_article() so the dedup and normalisation paths
# stay in play, then created_at is rewritten -- save_article always stamps NOW()
# and a week cannot be seeded by waiting one.
# ---------------------------------------------------------------------------

def seed(tag, when, description, description_source, source="Test Wire",
         title=None, topic=TOPIC):
    art = {
        "title": title or f"summary fixture {tag}",
        "url": f"{FIXTURE_URL_PREFIX}{tag}",
        "description": description,
        "description_source": description_source,
        "source": source,
        "published_at": "Recently",
        "category": topic,
        "image_url": "",
    }
    article_id, _ = db.save_article(art)
    with db.db_cursor(commit=True) as cur:
        cur.execute("UPDATE articles SET created_at = %s WHERE id = %s", (when, article_id))
    return article_id


def drop_fixture_articles():
    with db.db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM articles WHERE url LIKE %s", (FIXTURE_URL_PREFIX + "%",))
        return cur.rowcount


def drop_fixture_summaries():
    """Only the fixture weeks. Never a live week -- a summary is the sole
    surviving record of its week once the articles are purged, so a test that
    deletes real ones destroys data it cannot regenerate."""
    with db.db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM topic_summaries WHERE week_start = ANY(%s)",
                    (ALL_FIXTURE_WEEKS,))
        return cur.rowcount


def require_empty_week(week_start, label):
    """A fixture week must contain nothing but fixtures."""
    n = db.count_articles_for_week(TOPIC, week_start)
    check(f"{label}: fixture week {week_start} is clean", n == 0,
          f"found {n} non-fixture article(s) -- counts below would be meaningless"
          if n else "")
    return n == 0


def utc(y, m, d, hh=0, mm=0, ss=0, us=0):
    return datetime.datetime(y, m, d, hh, mm, ss, us, tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# 1. Week arithmetic  (no database required)
# ---------------------------------------------------------------------------
section("1. week arithmetic")

for offset in range(7):
    today = datetime.date(2026, 9, 7) + datetime.timedelta(days=offset)
    ws = sm.last_completed_week_start(today)
    check(f"{today.strftime('%a')} {today} -> week 2026-08-31",
          ws == datetime.date(2026, 8, 31), f"got {ws}")

check("week_start is always a Monday",
      all(sm.last_completed_week_start(datetime.date(2026, 1, 1)
          + datetime.timedelta(days=n)).weekday() == 0 for n in range(400)))

check("never returns the current partial week",
      all((lambda t: sm.week_end_for(sm.last_completed_week_start(t)) < t)(
          datetime.date(2026, 1, 1) + datetime.timedelta(days=n))
          for n in range(400)))

for today in (datetime.date(2027, 1, 1), datetime.date(2026, 1, 1),
              datetime.date(2025, 1, 2)):
    ws = sm.last_completed_week_start(today)
    check(f"year boundary {today}", ws.weekday() == 0 and sm.week_end_for(ws) < today,
          f"-> {ws} .. {sm.week_end_for(ws)}")


# ---------------------------------------------------------------------------
# Everything below needs Postgres.
# ---------------------------------------------------------------------------
db.init_pool(minconn=1, maxconn=4)
try:
    db.init_db()
    drop_fixture_articles()
    drop_fixture_summaries()

    # -----------------------------------------------------------------------
    # 2. Half-open window
    # -----------------------------------------------------------------------
    section("2. half-open window, anchored to UTC")
    require_empty_week(WEEK, "window")

    seed("edge-first", utc(2019, 1, 7, 0, 0, 0),
         "Publisher blurb for the very first instant of the week.", nf.DESC_PUBLISHER)
    seed("edge-last", utc(2019, 1, 13, 23, 59, 59, 999999),
         "Publisher blurb for the very last instant of the week.", nf.DESC_PUBLISHER)
    seed("edge-before", utc(2019, 1, 6, 23, 59, 59, 999999),
         "Publisher blurb from the previous week entirely.", nf.DESC_PUBLISHER)
    seed("edge-after", utc(2019, 1, 14, 0, 0, 0),
         "Publisher blurb from the following week entirely.", nf.DESC_PUBLISHER)

    got = {a["title"] for a in db.get_articles_for_week(TOPIC, WEEK)}
    n_window = db.count_articles_for_week(TOPIC, WEEK)
    check("Monday 00:00:00.000000Z is INSIDE", "summary fixture edge-first" in got)
    check("Sunday 23:59:59.999999Z is INSIDE", "summary fixture edge-last" in got,
          "a BETWEEN with a 23:59:59 bound would drop this")
    check("the microsecond before Monday is OUTSIDE", "summary fixture edge-before" not in got)
    check("next Monday 00:00:00Z is OUTSIDE", "summary fixture edge-after" not in got)
    check("window count == 2", n_window == 2, f"got {n_window}")

    drop_fixture_articles()

    # -----------------------------------------------------------------------
    # 3 + 4. Boilerplate exclusion and count integrity
    # -----------------------------------------------------------------------
    section("3+4. boilerplate exclusion and the two counts")
    require_empty_week(WEEK, "counts")

    fallback = nf.TOPIC_FEEDS[TOPIC]["summary_fallback"]
    keyword_tpl = nf.KEYWORD_SUMMARY_RULES[0][1].format(source="Reuters")

    # 4 real, 3 tier-3 fallback, 2 tier-2 template, 2 legacy NULL boilerplate.
    for i in range(4):
        seed(f"real-{i}", utc(2019, 1, 8, 8 + i),
             f"A genuine publisher description number {i} with enough length to pass.",
             nf.DESC_PUBLISHER, source=f"Outlet {i}",
             title=f"Datacentre buildout accelerates in region {i}")
    for i in range(3):
        seed(f"fb-{i}", utc(2019, 1, 9, 8 + i), fallback, nf.DESC_TOPIC_FALLBACK)
    for i in range(2):
        seed(f"kw-{i}", utc(2019, 1, 10, 8 + i), keyword_tpl, nf.DESC_KEYWORD_TEMPLATE)
    # Legacy rows: written before the column existed, so the tier is NULL and the
    # LIKE arm is the only thing that can catch them.
    for i in range(2):
        aid = seed(f"legacy-{i}", utc(2019, 1, 11, 8 + i), fallback, None)
        with db.db_cursor(commit=True) as cur:
            cur.execute("UPDATE articles SET description_source = NULL WHERE id = %s", (aid,))

    in_window = db.count_articles_for_week(TOPIC, WEEK)
    usable = db.get_articles_for_week(TOPIC, WEEK, publisher_only=True)
    unfiltered = db.get_articles_for_week(TOPIC, WEEK, publisher_only=False)

    check("articles_in_window counts everything", in_window == 11, f"got {in_window}")
    check("publisher_only=False returns everything", len(unfiltered) == 11, f"got {len(unfiltered)}")
    check("publisher_only=True returns only the 4 real ones", len(usable) == 4, f"got {len(usable)}")
    check("tier-3 fallback text never reaches the generator",
          all(a["description"] != fallback for a in usable))
    check("tier-2 template text never reaches the generator",
          all(a["description"] != keyword_tpl for a in usable))
    check("legacy NULL rows are caught by LIKE matching",
          not any("legacy" in a["title"] for a in usable),
          "these predate description_source and have no column to filter on")
    check("the two counts differ by exactly the boilerplate",
          in_window - len(usable) == 7, f"{in_window} - {len(usable)}")
    check("publisher_only defaults to True",
          len(db.get_articles_for_week(TOPIC, WEEK)) == 4,
          "the contaminated path must be opt-in, not the default")

    # -----------------------------------------------------------------------
    # 5. Generation writes both counts, and is idempotent
    # -----------------------------------------------------------------------
    section("5. generation and idempotency")

    report = sm.generate_weekly_summaries(week_start=WEEK)
    tech = next((r for r in db.get_topic_summaries(WEEK) if r["topic"] == TOPIC), None)

    check("a summary row was written", tech is not None, str(report))
    if tech:
        check("number_of_articles == what the generator read",
              tech["number_of_articles"] == 4, f"got {tech['number_of_articles']}")
        check("articles_in_window == the raw window count",
              tech["articles_in_window"] == 11, f"got {tech['articles_in_window']}")
        check("they are NOT the same number",
              tech["number_of_articles"] != tech["articles_in_window"])
        check("week_end is the Sunday", tech["week_end"] == WEEK_END.isoformat(), tech["week_end"])
        check("generator is recorded", bool(tech["generator"]), tech["generator"])
        check("summary text is non-empty", len(tech["summary"] or "") > 20)
        check("the stated count matches the stated count",
              str(tech["number_of_articles"]) in tech["summary"],
              "extractive:v1 names its own count; it must be the stored one")
        check("the canned fallback sentence is not echoed in the summary",
              fallback[:40] not in tech["summary"],
              "boilerplate reaching the prose is the failure this feature exists to avoid")

    sm.generate_weekly_summaries(week_start=WEEK, force=True)
    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM topic_summaries WHERE topic=%s AND week_start=%s",
                    (TOPIC, WEEK))
        n = cur.fetchone()["n"]
    check("a second run updates rather than duplicates", n == 1, f"{n} rows")

    drop_fixture_articles()

    # -----------------------------------------------------------------------
    # 6. The floor
    # -----------------------------------------------------------------------
    section("6. MIN_ARTICLES_FOR_SUMMARY floor")
    require_empty_week(THIN_WEEK, "floor")

    seed("thin-0", utc(2019, 2, 5, 9),
         "One solitary genuine publisher description, long enough to qualify.",
         nf.DESC_PUBLISHER)
    for i in range(9):
        seed(f"thin-fb-{i}", utc(2019, 2, 5, 10 + i), fallback, nf.DESC_TOPIC_FALLBACK)

    sm.generate_weekly_summaries(week_start=THIN_WEEK)
    check("10 articles but only 1 usable -> no row at all",
          not any(r["topic"] == TOPIC for r in db.get_topic_summaries(THIN_WEEK)),
          "a digest of one article is noise wearing an authoritative label")
    drop_fixture_articles()

    # -----------------------------------------------------------------------
    # 7. Failure writes nothing
    # -----------------------------------------------------------------------
    section("7. a failing generator writes no placeholder")
    require_empty_week(FAIL_WEEK, "failure")
    print("      (the RuntimeError traceback below is the simulated failure, not a test error)")

    for i in range(5):
        seed(f"fail-{i}", utc(2019, 3, 5, 9 + i),
             f"Genuine publisher description {i}, long enough to be kept by the filter.",
             nf.DESC_PUBLISHER, source=f"Outlet {i}")

    original = sm.summarise
    sm.summarise = lambda topic, articles: (_ for _ in ()).throw(
        RuntimeError("simulated API outage"))
    try:
        rep = sm.generate_weekly_summaries(week_start=FAIL_WEEK)
    finally:
        sm.summarise = original

    check("no row written when the generator raises",
          not any(r["topic"] == TOPIC for r in db.get_topic_summaries(FAIL_WEEK)),
          "after the purge a placeholder is indistinguishable from a real summary")
    check("the failure is reported, not swallowed",
          any(f["topic"] == TOPIC for f in rep.get("failed", [])), str(rep.get("failed")))

    sm.generate_weekly_summaries(week_start=FAIL_WEEK)
    check("the gap is recoverable while the articles survive",
          any(r["topic"] == TOPIC for r in db.get_topic_summaries(FAIL_WEEK)))
    drop_fixture_articles()

    # -----------------------------------------------------------------------
    # 8. Ordering and retention
    # -----------------------------------------------------------------------
    section("8. ordering, retention margin, and outliving the sources")
    import main

    src = inspect.getsource(main.refresh_pipeline)
    i_sum = src.find("generate_weekly_summaries")
    i_purge = src.find("purge_old_data")
    check("both calls are present in refresh_pipeline", i_sum > 0 and i_purge > 0)
    check("generate_weekly_summaries precedes purge_old_data", 0 < i_sum < i_purge,
          "reversed, a week is summarised after its Monday has been deleted")
    check("PURGE_OLDER_THAN_DAYS is at least 9",
          main.PURGE_OLDER_THAN_DAYS >= 9, f"got {main.PURGE_OLDER_THAN_DAYS}")
    check("a lower value is clamped, not accepted silently",
          main.MIN_PURGE_DAYS_FOR_WEEKLY_SUMMARIES == 9)

    # Retention margin, as arithmetic rather than a live purge. The oldest
    # article of week W is Mon W 00:00Z. The summariser first reaches it on
    # Mon W+7; at the last refresh of that day its age is just under 8 days.
    # A live purge would assert something different depending on which weekday
    # the test happens to run, which is not a property worth encoding.
    OLDEST_AGE_AT_FIRST_OPPORTUNITY = 8.0
    margin = main.PURGE_OLDER_THAN_DAYS - OLDEST_AGE_AT_FIRST_OPPORTUNITY
    check("retention leaves margin over the summariser's first opportunity",
          margin > 0, f"{margin:.1f} days of slack for downtime or a missed tick")

    # Behavioural: a summary outlives the articles it was built from, and the
    # purge boundary really is where the arithmetic says it is.
    require_empty_week(ORDER_WEEK, "ordering")
    for i in range(4):
        seed(f"order-{i}", utc(2019, 4, 2, 9 + i),
             f"Genuine publisher description {i} for the ordering check, long enough.",
             nf.DESC_PUBLISHER, source=f"Outlet {i}")

    sm.generate_weekly_summaries(week_start=ORDER_WEEK)
    wrote = any(r["topic"] == TOPIC for r in db.get_topic_summaries(ORDER_WEEK))
    check("the fixture week is summarised while its articles exist", wrote)

    now = datetime.datetime.now(datetime.timezone.utc)
    seed("purge-survivor", now - datetime.timedelta(days=8),
         "Publisher description just inside the retention window, long enough to keep.",
         nf.DESC_PUBLISHER)
    seed("purge-doomed", now - datetime.timedelta(days=10),
         "Publisher description just outside the retention window, long enough to keep.",
         nf.DESC_PUBLISHER)

    # The same purge the scheduler runs every 12 hours, so it removes nothing
    # from the live table that would not have gone shortly anyway.
    db.purge_old_articles(days=main.PURGE_OLDER_THAN_DAYS)

    with db.db_cursor() as cur:
        cur.execute("SELECT url FROM articles WHERE url LIKE %s", (FIXTURE_URL_PREFIX + "%",))
        surviving = {r["url"].rsplit("/", 1)[1] for r in cur.fetchall()}

    check("an article 8 days old survives a 9-day purge", "purge-survivor" in surviving)
    check("an article 10 days old does not", "purge-doomed" not in surviving)
    check("the fixture week's articles are gone",
          not any(u.startswith("order-") for u in surviving))
    check("the summary outlives the articles it was built from",
          any(r["topic"] == TOPIC for r in db.get_topic_summaries(ORDER_WEEK)),
          "this is the entire reason topic_summaries is exempt from the purge")

    drop_fixture_articles()

    # -----------------------------------------------------------------------
    # 9. Schema guards
    # -----------------------------------------------------------------------
    section("9. schema guards")
    import psycopg2

    def rejected_by(sql, params, exc_type):
        try:
            with db.db_cursor(commit=True) as cur:
                cur.execute(sql, params)
        except exc_type:
            return True
        except Exception:
            return False
        return False

    check("an unknown topic slug is rejected by the FK",
          rejected_by(
              "INSERT INTO topic_summaries (topic, week_start, week_end, summary, "
              "number_of_articles, articles_in_window, generator) "
              "VALUES ('NOT_A_TOPIC', %s, %s, 'x', 1, 1, 'test')",
              (SCHEMA_WEEK, SCHEMA_WEEK + datetime.timedelta(days=6)),
              psycopg2.errors.ForeignKeyViolation))

    check("number_of_articles = 0 is rejected by the CHECK",
          rejected_by(
              "INSERT INTO topic_summaries (topic, week_start, week_end, summary, "
              "number_of_articles, articles_in_window, generator) "
              "VALUES (%s, %s, %s, 'x', 0, 0, 'test')",
              (TOPIC, SCHEMA_WEEK, SCHEMA_WEEK + datetime.timedelta(days=6)),
              psycopg2.errors.CheckViolation),
          "a summary of nothing should not be storable")

finally:
    try:
        a = drop_fixture_articles()
        s = drop_fixture_summaries()
        print(f"\n[cleanup] removed {a} fixture article(s), {s} fixture summary row(s).")
    finally:
        db.close_pool()


print("\n" + "=" * 62)
if fails:
    print(f"FAILED ({len(fails)}): " + ", ".join(fails))
    sys.exit(1)
print("All topic-summary checks passed.")
