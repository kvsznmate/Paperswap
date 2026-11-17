"""Pure functions behind the feed: topic classification, category parsing and
the dedup key. No Postgres needed -- nothing here opens a connection.

Guards #33. These functions decide which topic an article is filed under and
which rows count as duplicates, and until this file none of them had a test.

Run:  python tests/test_classifier.py
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
import news_fetcher as nf

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + str(detail) if detail else ''}")
    if not cond:
        fails.append(name)


def section(title):
    print(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
section("1. the keyword tables are complete and every keyword can match")
# ---------------------------------------------------------------------------
check("every keyword topic is a real topic",
      set(nf.CATEGORY_KEYWORDS) <= set(db.CATEGORIES),
      sorted(set(nf.CATEGORY_KEYWORDS) - set(db.CATEGORIES)))
check("every keyword topic has a weight (score_categories would KeyError)",
      set(nf.CATEGORY_KEYWORDS) == set(nf.CATEGORY_WEIGHT),
      sorted(set(nf.CATEGORY_KEYWORDS) ^ set(nf.CATEGORY_WEIGHT)))

# Patterns are \b<keyword>\b. A keyword that starts or ends with a non-word
# character ("c++", ".net") can never match, and nothing would say so: the
# keyword just silently scores zero forever.
dead = [kw for cat, kws in nf.CATEGORY_KEYWORDS.items() for kw in kws
        if not nf.score_categories(f"news about {kw} today", "").get(cat)]
check("every keyword matches itself inside a headline", not dead, dead)


# ---------------------------------------------------------------------------
section("2. score_categories")
# ---------------------------------------------------------------------------
s = nf.score_categories("Campaign in Ukraine, official said", "")
check("bare 'ai' does not fire inside 'Ukraine', 'said' or 'campaign'",
      "TECH" not in s, s)
check("...while the real keyword 'campaign' still scores", "POLITICS" in s, s)

title_hit = nf.score_categories("NBA finals tonight", "")
desc_hit = nf.score_categories("Tonight", "NBA finals")
check("a title hit counts double a description hit",
      title_hit.get("SPORTS") == 2 * desc_hit.get("SPORTS", 0),
      f"title {title_hit} desc {desc_hit}")
check("score is weight x (2 x title hits + description hits)",
      nf.score_categories("NBA playoffs", "tennis")["SPORTS"]
      == nf.CATEGORY_WEIGHT["SPORTS"] * (2 * 2 + 1))

check("matching is case-insensitive",
      nf.score_categories("nasa", "") == nf.score_categories("NASA", ""))
check("markup is stripped before matching",
      "SPORTS" in nf.score_categories("<b>NBA</b> recap", ""))
check("a keyword inside a tag attribute does not count",
      "SCIENCE" not in nf.score_categories('<a href="https://nasa.gov">link</a>', ""))
check("no keywords -> empty scores", nf.score_categories("Untitled", "") == {})
check("None title and description are tolerated",
      nf.score_categories(None, None) == {})


# ---------------------------------------------------------------------------
section("3. classify_category relabels only on clear evidence")
# ---------------------------------------------------------------------------
cases = [
    # (title, description, query's label, expected, why)
    ("L'Oreal launches new retinol serum line", "", "FINANCE", "BEAUTY",
     "three beauty keywords beat a finance query"),
    ("Quarterback signs record contract", "", "FINANCE", "SPORTS",
     "one heavily weighted sports keyword beats no finance keyword"),
    ("NASA telescope spots distant asteroid", "", "TECH", "SCIENCE",
     "science keywords beat a tech query"),
    ("Fed holds rates steady", "", "FINANCE", "FINANCE",
     "no keywords -> the query's label stands"),
    ("Apple stock slides", "", "FINANCE", "FINANCE",
     "a tie never relabels (TECH 2 vs FINANCE 2)"),
    ("Weekly update", "researchers say", "FINANCE", "FINANCE",
     "a lead of 2 is below MIN_OVERRIDE_MARGIN (SCIENCE 2 vs FINANCE 0)"),
    ("Weekly roundup", "football", "TECH", "SPORTS",
     "a lead of exactly MIN_OVERRIDE_MARGIN relabels"),
    ("Tech roundup", "football", "TECH", "TECH",
     "the label's own score is subtracted from the lead (3 - 2 = 1)"),
]
for title, desc, label, expected, why in cases:
    got = nf.classify_category(title, desc, label)
    check(f"{why}", got == expected,
          f"{title!r} [{label}] -> {got}, scores {nf.score_categories(title, desc)}")

check("MIN_OVERRIDE_MARGIN is what the margin cases above assume",
      nf.MIN_OVERRIDE_MARGIN == 3, nf.MIN_OVERRIDE_MARGIN)
check("an unknown query label falls back to the default topic, not an exception",
      nf.classify_category("Untitled", "", "NOT_A_TOPIC") == db.DEFAULT_CATEGORY)
check("a legacy label is normalised before comparing",
      nf.classify_category("Untitled", "", "business") == "FINANCE")
check("the result is always a real topic",
      all(nf.classify_category(t, d, lbl) in db.CATEGORIES for t, d, lbl, _, _ in cases))


# ---------------------------------------------------------------------------
section("4. normalize_category")
# ---------------------------------------------------------------------------
for raw, expected in [
    ("SPORTS", "SPORTS"), (" sports ", "SPORTS"), ("beauty", "BEAUTY"),
    ("business", "FINANCE"), ("Technology", "TECH"), ("coding", "PROGRAMMING"),
    ("", db.DEFAULT_CATEGORY), (None, db.DEFAULT_CATEGORY),
    ("not a topic", db.DEFAULT_CATEGORY), ("'; DROP TABLE articles; --", db.DEFAULT_CATEGORY),
]:
    got = db.normalize_category(raw)
    check(f"normalize_category({raw!r}) == {expected}", got == expected, got)

check("every alias points at a real topic",
      all(v in db.CATEGORIES for v in db.CATEGORY_ALIASES.values()),
      {k: v for k, v in db.CATEGORY_ALIASES.items() if v not in db.CATEGORIES})


# ---------------------------------------------------------------------------
section("5. clean_category_filter (untrusted query-string input)")
# ---------------------------------------------------------------------------
for raw, expected in [
    ("sports,politics", ["SPORTS", "POLITICS"]),
    (["SPORTS"], ["SPORTS"]),
    (" sports , politics ", ["SPORTS", "POLITICS"]),
    ("politics,sports", ["POLITICS", "SPORTS"]),                 # order kept
    ("sport,SPORTS,sports", ["SPORTS"]),                         # alias + dedupe
    ("sports,not_a_topic", ["SPORTS"]),                          # invalid dropped
    ("'; DROP TABLE articles; --", []),
    ("", []), (None, []), ([], []), (",,,", []),
    ([1, None], []),                                             # non-strings
]:
    got = db.clean_category_filter(raw)
    check(f"clean_category_filter({raw!r}) == {expected}", got == expected, got)

check("output is only catalogue slugs, so it is safe as SQL parameters",
      all(c in db.CATEGORIES for c in db.clean_category_filter(",".join(
          list(db.CATEGORIES) + list(db.CATEGORY_ALIASES) + ["x", "1=1"]))))


# ---------------------------------------------------------------------------
section("6. generate_article_key")
# ---------------------------------------------------------------------------
k = db.generate_article_key("  Apple Ships M5  ", "HTTPS://Ex.com/A ")
check("case and surrounding whitespace do not change the key",
      k == db.generate_article_key("apple ships m5", "https://ex.com/a"))
check("a different URL is a different article",
      k != db.generate_article_key("apple ships m5", "https://ex.com/b"))
check("a different title is a different article",
      k != db.generate_article_key("apple ships m6", "https://ex.com/a"))
check("the key is a 32-char hex MD5", len(k) == 32 and int(k, 16) >= 0, k)

# The format is frozen. Every stored row was keyed this way and the unique
# constraint is the dedup (invariant 1): change the recipe and the next refresh
# re-inserts every article still in the feeds as a "new" one.
check("key recipe is md5(lower(strip(title)) + '_' + lower(strip(url)))",
      k == hashlib.md5(b"apple ships m5_https://ex.com/a").hexdigest(), k)


print("\n" + "=" * 62)
if fails:
    print(f"FAILED ({len(fails)}): " + ", ".join(fails))
    sys.exit(1)
print("All classifier checks passed.")
