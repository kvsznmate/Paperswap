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
  6. Ordering. The summariser runs BEFORE the purge inside refresh_pipeline, so
     a week is summarised while its articles still exist.
  7. Failure writes nothing. A raising generator leaves no row -- never a
     placeholder, which after the purge is indistinguishable from a real summary.
  8. The floor. A topic below MIN_ARTICLES_FOR_SUMMARY gets no row at all.
  9. The FK rejects a topic that is not in the catalogue.

On (3) and (7): these are the ADR-010 checks in this feature's clothing. Once
purge_old_articles has run, the summary row is the only surviving record of the
week -- nothing downstream can recompute the count or re-derive the text. A
number that is wrong at write time is wrong forever, and a placeholder written
"temporarily" becomes permanent the moment the sources are deleted.

On (6): this is the test most likely to save the feature. The failure it guards
is silent by construction. If the purge runs first, the summary covers six days,
reports six days' worth of articles under a seven-day label, and the evidence
that would reveal it has been deleted.

Run:  python tests/test_topic_summaries.py
"""
import os
import sys
import uuid
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
import news_fetcher as nf
import summarizer as sm

fails = []
TOPIC = "TECH"


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(name)


def section(title):
    print(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
# Fixtures
#
# Articles are inserted with an explicit created_at so a week can be seeded
# without waiting one. save_article() always stamps NOW(), so the timestamp is
# rewritten afterwards -- going through save_article first keeps the dedup and
# normalisation paths in play rather than testing a hand-built row.
# ---------------------------------------------------------------------------

def seed(tag, when, description, description_source, source="Test Wire",
         title=None, topic=TOPIC):
    art = {
        "title": title or f"summary fixture {tag}",
        "url": f"https://example.com/summaries/{tag}",
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


def cleanup(week_start):
    with db.db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM articles WHERE url LIKE 'https://example.com/summaries/%%'")
        cur.execute("DELETE FROM topic_summaries WHERE week_start = %s", (week_start,))


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

    WEEK = datetime.date(2026, 8, 31)          # Monday
    WEEK_END = datetime.date(2026, 9, 6)       # Sunday
    cleanup(WEEK)

    # -----------------------------------------------------------------------
    # 2. Half-open window
    # -----------------------------------------------------------------------
    section("2. half-open window, anchored to UTC")

    seed("edge-first", utc(2026, 8, 31, 0, 0, 0), "Publisher blurb for the very first instant of the week.", nf.DESC_PUBLISHER)
    seed("edge-last", utc(2026, 9, 6, 23, 59, 59, 999999), "Publisher blurb for the very last instant of the week.", nf.DESC_PUBLISHER)
    seed("edge-before", utc(2026, 8, 30, 23, 59, 59, 999999), "Publisher blurb from the previous week entirely.", nf.DESC_PUBLISHER)
    seed("edge-after", utc(2026, 9, 7, 0, 0, 0), "Publisher blurb from the following week entirely.", nf.DESC_PUBLISHER)

    got = {a["title"] for a in db.get_articles_for_week(TOPIC, WEEK)}
    check("Monday 00:00:00.000000Z is INSIDE", "summary fixture edge-first" in got)
    check("Sunday 23:59:59.999999Z is INSIDE", "summary fixture edge-last" in got,
          "a BETWEEN with a 23:59:59 bound would drop this")
    check("the microsecond before Monday is OUTSIDE", "summary fixture edge-before" not in got)
    check("next Monday 00:00:00Z is OUTSIDE", "summary fixture edge-after" not in got)
    check("window count == 2", db.count_articles_for_week(TOPIC, WEEK) == 2,
          f"got {db.count_articles_for_week(TOPIC, WEEK)}")

    cleanup(WEEK)

    # -----------------------------------------------------------------------
    # 3 + 4. Boilerplate exclusion and count integrity
    # -----------------------------------------------------------------------
    section("3+4. boilerplate exclusion and the two counts")

    fallback = nf.TOPIC_FEEDS[TOPIC]["summary_fallback"]
    keyword_tpl = nf.KEYWORD_SUMMARY_RULES[0][1].format(source="Reuters")

    # 4 real, 3 tier-3 fallback, 2 tier-2 template, 2 legacy NULL boilerplate.
    for i in range(4):
        seed(f"real-{i}", utc(2026, 9, 1, 8 + i),
             f"A genuine publisher description number {i} with enough length to pass.",
             nf.DESC_PUBLISHER, source=f"Outlet {i}",
             title=f"Datacentre buildout accelerates in region {i}")
    for i in range(3):
        seed(f"fb-{i}", utc(2026, 9, 2, 8 + i), fallback, nf.DESC_TOPIC_FALLBACK)
    for i in range(2):
        seed(f"kw-{i}", utc(2026, 9, 3, 8 + i), keyword_tpl, nf.DESC_KEYWORD_TEMPLATE)
    # Legacy rows: written before the column existed, so the tier is NULL and the
    # LIKE arm is the only thing that can catch them.
    for i in range(2):
        aid = seed(f"legacy-{i}", utc(2026, 9, 4, 8 + i), fallback, None)
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
    rows = db.get_topic_summaries(WEEK)
    tech = next((r for r in rows if r["topic"] == TOPIC), None)

    check("a summary row was written", tech is not None, str(report))
    if tech:
        check("number_of_articles == what the generator read",
              tech["number_of_articles"] == 4, f"got {tech['number_of_articles']}")
        check("articles_in_window == the raw window count",
              tech["articles_in_window"] == 11, f"got {tech['articles_in_window']}")
        check("they are NOT the same number", tech["number_of_articles"] != tech["articles_in_window"])
        check("week_end is the Sunday", tech["week_end"] == WEEK_END.isoformat(), tech["week_end"])
        check("generator is recorded", bool(tech["generator"]), tech["generator"])
        check("summary text is non-empty", len(tech["summary"] or "") > 20)
        check("the canned fallback sentence is not echoed in the summary",
              fallback[:40] not in tech["summary"],
              "boilerplate reaching the prose is the failure this feature exists to avoid")

    sm.generate_weekly_summaries(week_start=WEEK, force=True)
    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM topic_summaries WHERE topic=%s AND week_start=%s",
                    (TOPIC, WEEK))
        n = cur.fetchone()["n"]
    check("a second run updates rather than duplicates", n == 1, f"{n} rows")

    # -----------------------------------------------------------------------
    # 6. The floor
    # -----------------------------------------------------------------------
    section("6. MIN_ARTICLES_FOR_SUMMARY floor")

    THIN = datetime.date(2026, 8, 17)
    cleanup(THIN)
    seed("thin-0", utc(2026, 8, 18, 9), "One solitary genuine publisher description, long enough to qualify.", nf.DESC_PUBLISHER)
    for i in range(9):
        seed(f"thin-fb-{i}", utc(2026, 8, 18, 10 + i), fallback, nf.DESC_TOPIC_FALLBACK)

    sm.generate_weekly_summaries(week_start=THIN)
    check("10 articles but only 1 usable -> no row at all",
          not any(r["topic"] == TOPIC for r in db.get_topic_summaries(THIN)),
          "a digest of one article is noise wearing an authoritative label")
    cleanup(THIN)

    # -----------------------------------------------------------------------
    # 7. Failure writes nothing
    # -----------------------------------------------------------------------
    section("7. a failing generator writes no placeholder")

    FAIL_WEEK = datetime.date(2026, 8, 10)
    cleanup(FAIL_WEEK)
    for i in range(5):
        seed(f"fail-{i}", utc(2026, 8, 11, 9 + i),
             f"Genuine publisher description {i}, long enough to be kept by the filter.",
             nf.DESC_PUBLISHER, source=f"Outlet {i}")

    original = sm.summarise
    sm.summarise = lambda topic, articles: (_ for _ in ()).throw(RuntimeError("simulated API outage"))
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
    cleanup(FAIL_WEEK)

    # -----------------------------------------------------------------------
    # 8. Ordering: summarise BEFORE purge
    # -----------------------------------------------------------------------
    section("8. refresh_pipeline summarises before it purges")

    import inspect
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

    # The behavioural half: an article at day 8 must be summarised and only then
    # deleted. A static read of the source cannot prove the purge really spares
    # it long enough.
    LIVE = sm.last_completed_week_start()
    cleanup(LIVE)
    for i in range(4):
        when = datetime.datetime.combine(
            LIVE + datetime.timedelta(days=i), datetime.time(9, 0),
            tzinfo=datetime.timezone.utc)
        seed(f"live-{i}", when,
             f"Genuine publisher description {i} for the live ordering check, long enough.",
             nf.DESC_PUBLISHER, source=f"Outlet {i}")

    sm.generate_weekly_summaries(week_start=LIVE)
    wrote = any(r["topic"] == TOPIC for r in db.get_topic_summaries(LIVE))
    db.purge_old_articles(days=main.PURGE_OLDER_THAN_DAYS)
    survived = any(r["topic"] == TOPIC for r in db.get_topic_summaries(LIVE))

    check("last completed week is summarised at PURGE_OLDER_THAN_DAYS=9", wrote)
    check("the summary survives the purge that deletes its sources", survived,
          "outliving the articles is the entire point of the table")
    cleanup(LIVE)

    # -----------------------------------------------------------------------
    # 9. Foreign key
    # -----------------------------------------------------------------------
    section("9. schema guards")

    import psycopg2
    rejected = False
    try:
        with db.db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO topic_summaries (topic, week_start, week_end, summary, "
                "number_of_articles, articles_in_window, generator) "
                "VALUES ('NOT_A_TOPIC', %s, %s, 'x', 1, 1, 'test')", (WEEK, WEEK_END))
    except psycopg2.errors.ForeignKeyViolation:
        rejected = True
    except Exception:
        pass
    check("an unknown topic slug is rejected by the FK", rejected)

    zero_rejected = False
    try:
        with db.db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO topic_summaries (topic, week_start, week_end, summary, "
                "number_of_articles, articles_in_window, generator) "
                "VALUES (%s, %s, %s, 'x', 0, 0, 'test')",
                (TOPIC, datetime.date(2020, 1, 6), datetime.date(2020, 1, 12)))
    except psycopg2.errors.CheckViolation:
        zero_rejected = True
    except Exception:
        pass
    check("number_of_articles = 0 is rejected by the CHECK", zero_rejected,
          "a summary of nothing should not be storable")

    cleanup(WEEK)
    with db.db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM topic_summaries WHERE week_start = %s",
                    (datetime.date(2020, 1, 6),))

finally:
    db.close_pool()


print("\n" + "=" * 62)
if fails:
    print(f"FAILED ({len(fails)}): " + ", ".join(fails))
    sys.exit(1)
print("All topic-summary checks passed.")
