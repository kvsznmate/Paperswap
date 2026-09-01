"""Hand-label articles for the topic classifier holdout set.

Runs on a LAPTOP, not the VM. It lives in tools/ rather than backend/ because
backend/ is COPY'd into the Docker image, and nothing here should ship to a
956 MB instance.

    python tools/label_articles.py --source postgresql://... --limit 300
    python tools/label_articles.py --source articles.jsonl
    python tools/label_articles.py --recheck 40

Why this exists
---------------
ADR-007 records that the keyword classifier's accuracy "has never been
measured. There is no labelled dataset, no confusion matrix, and no baseline
comparison." This produces that dataset. Without it there is no way to say
whether the distilled classifier beats the keyword scorer, and any claim that
it does would be the ADR-010 failure in a new place.

Three design choices that matter more than the code
---------------------------------------------------
1. THE EXISTING CATEGORY IS HIDDEN WHILE YOU LABEL. It is recorded in the
   output so a confusion matrix can be built later, but it is never shown on
   screen. Seeing "FINANCE" before deciding anchors you to it, and an anchored
   holdout measures agreement with the keyword classifier rather than truth.

2. YOU SEE EXACTLY WHAT THE MODEL SEES -- title and description, nothing more.
   The classifier embeds title + description (ADR-011), so labelling from the
   full article would grade the model against evidence it never had.

3. LABELS ARE MULTI-SELECT. An Apple earnings story is genuinely Tech and
   Finance, which is an open question in ARCHITECTURE.md. Forcing one label
   bakes an unresolved taxonomy question into the ground truth. Train seven
   independent binary classifiers, not a softmax over seven classes.

Run --recheck after a few hundred labels. It re-serves articles you have
already done, without showing your earlier answer, and reports how often you
agree with yourself. That number is the CEILING on any model's measurable
accuracy against this set -- if you disagree with yourself 12% of the time, a
model scoring 90% is at the noise floor and "improving" it is not meaningful.
"""

import os
import csv
import sys
import json
import time
import random
import argparse
import textwrap
from datetime import datetime, timezone

# Mirrors database.CATEGORIES. Not imported from it -- that module imports
# psycopg2 at load, and this tool must run against a JSONL export on a machine
# with no database driver installed. If you add a topic there, add it here.
TOPICS = [
    ("TECH",        "Tech"),
    ("FINANCE",     "Finance"),
    ("SPORTS",      "Sports"),
    ("POLITICS",    "Politics"),
    ("PROGRAMMING", "Programming"),
    ("SCIENCE",     "Science"),
    ("BEAUTY",      "Beauty"),
]

FIELDS = [
    "article_key", "title", "description", "source",
    "labels", "keyword_category", "labeled_at", "mode",
]

# Fixed so a re-run serves the same articles in the same order. Labelling in id
# order would mean labelling in news-cycle order, which correlates topic with
# position in the queue.
SHUFFLE_SEED = 20260829

WRAP = 76


# ---------------------------------------------------------------------------
# TERMINAL
# ---------------------------------------------------------------------------

def safe_print(text: str = "") -> None:
    """Print, surviving a console that cannot encode the character.

    Windows consoles still default to cp1252 in several common terminals, and
    news headlines are full of en-dashes and smart quotes. An unhandled
    UnicodeEncodeError three hours into a labelling session loses the session.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        print(text.encode(encoding, errors="replace").decode(encoding))


def make_getch():
    """Single-keypress reader, or None if the terminal will not cooperate.

    Falls back to line input rather than failing. A labelling tool that breaks
    in one terminal emulator is worse than one that costs an extra Enter.
    """
    try:
        import msvcrt

        def getch_win():
            ch = msvcrt.getch()
            if ch in (b"\x00", b"\xe0"):   # arrow / function key prefix
                msvcrt.getch()
                return ""
            if ch == b"\x03":
                raise KeyboardInterrupt
            return ch.decode("utf-8", errors="ignore")

        return getch_win
    except ImportError:
        pass

    try:
        import tty
        import termios

        def getch_unix():
            fd = sys.stdin.fileno()
            saved = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            if ch == "\x03":
                raise KeyboardInterrupt
            return ch

        if sys.stdin.isatty():
            return getch_unix
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# SOURCES
# ---------------------------------------------------------------------------

def load_from_postgres(url: str, limit: int) -> list:
    import psycopg2

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT article_key, title, description, source, category
                FROM articles
                WHERE title IS NOT NULL
                ORDER BY id DESC
                LIMIT %s
            ''', (limit * 4,))   # over-fetch; resume filtering happens after
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "article_key": r[0], "title": r[1], "description": r[2] or "",
            "source": r[3] or "", "keyword_category": r[4] or "",
        }
        for r in rows
    ]


def load_from_file(path: str) -> list:
    """Accept JSONL or CSV. Both are things a pg_dump gets turned into."""
    out = []
    if path.lower().endswith((".jsonl", ".ndjson", ".json")):
        with open(path, encoding="utf-8") as fh:
            content = fh.read().strip()
            records = json.loads(content) if content.startswith("[") else [
                json.loads(line) for line in content.splitlines() if line.strip()
            ]
    else:
        with open(path, encoding="utf-8", newline="") as fh:
            records = list(csv.DictReader(fh))

    for rec in records:
        if not rec.get("title"):
            continue
        out.append({
            "article_key": rec.get("article_key") or rec.get("id") or rec["title"][:64],
            "title": rec["title"],
            "description": rec.get("description") or "",
            "source": rec.get("source") or "",
            "keyword_category": rec.get("category") or "",
        })
    return out


# ---------------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------------

def load_done(path: str) -> dict:
    """Existing labels, keyed by article_key. This is the resume point."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8", newline="") as fh:
        return {
            row["article_key"]: row
            for row in csv.DictReader(fh)
            if row.get("article_key")
        }


def append_row(path: str, row: dict) -> None:
    """Append and flush per article.

    Not buffered until exit: a labelling session is long and hand-labelling is
    the most expensive input in this whole project. A crash at article 250
    should cost one article, not 250.
    """
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)
        fh.flush()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def render(article: dict, done_count: int, target: int, session_count: int,
           elapsed: float, selected: set) -> None:
    safe_print("\n" + "=" * WRAP)

    rate = f"{elapsed / session_count:.1f}s/article" if session_count else "--"
    safe_print(f"  {done_count}/{target} labelled    "
               f"session: {session_count}    {rate}")
    safe_print("-" * WRAP)

    if article["source"]:
        safe_print(f"  {article['source']}")
    safe_print("")

    for line in textwrap.wrap(article["title"], WRAP - 4):
        safe_print(f"  {line}")

    if article["description"]:
        safe_print("")
        body = textwrap.wrap(article["description"], WRAP - 4)[:6]
        for line in body:
            safe_print(f"  {line}")

    safe_print("")
    safe_print("-" * WRAP)

    row1 = "   ".join(f"{i+1} {label}" for i, (_, label) in enumerate(TOPICS[:4]))
    row2 = "   ".join(f"{i+5} {label}" for i, (_, label) in enumerate(TOPICS[4:]))
    safe_print(f"  {row1}")
    safe_print(f"  {row2}")
    safe_print("  Enter=commit   0=none of these   s=skip   u=undo   q=quit")

    chosen = [label for slug, label in TOPICS if slug in selected]
    safe_print(f"  selected: [{', '.join(chosen) if chosen else '-'}]")


def collect(getch, article, done_count, target, session_count, elapsed):
    """Return a set of slugs, or a sentinel string: SKIP / UNDO / QUIT."""
    selected = set()

    while True:
        render(article, done_count, target, session_count, elapsed, selected)

        if getch:
            key = getch()
        else:
            raw = input("  > ").strip()
            if raw == "":
                key = "\r"
            elif raw.lower() in ("s", "u", "q"):
                key = raw.lower()
            else:
                for ch in raw:
                    if ch.isdigit():
                        _toggle(selected, ch)
                key = "\r"

        if key in ("\r", "\n"):
            if not selected:
                safe_print("\n  Nothing selected. Press 0 for 'none of these', "
                           "or s to skip.")
                continue
            return selected
        if key == "s":
            return "SKIP"
        if key == "u":
            return "UNDO"
        if key == "q":
            return "QUIT"
        if key.isdigit():
            _toggle(selected, key)


def _toggle(selected: set, digit: str) -> None:
    if digit == "0":
        selected.clear()
        selected.add("NONE")
        return
    idx = int(digit) - 1
    if 0 <= idx < len(TOPICS):
        selected.discard("NONE")
        slug = TOPICS[idx][0]
        selected.symmetric_difference_update({slug})


# ---------------------------------------------------------------------------
# MODES
# ---------------------------------------------------------------------------

def run_label(articles, done, out_path, target, getch):
    queue = [a for a in articles if a["article_key"] not in done]
    random.Random(SHUFFLE_SEED).shuffle(queue)
    queue = queue[:max(0, target - len(done))]

    if not queue:
        safe_print(f"\nNothing left to label -- {len(done)} already done.")
        return

    safe_print(f"\n{len(queue)} article(s) queued, {len(done)} already labelled.")
    safe_print("The existing keyword category is hidden on purpose. Do not go "
               "look it up.\n")

    started = time.time()
    session = 0
    history = []

    for article in queue:
        result = collect(getch, article, len(done) + session, target,
                         session, time.time() - started)

        if result == "QUIT":
            break
        if result == "SKIP":
            continue
        if result == "UNDO":
            if history:
                safe_print(f"\n  Last: {history[-1]}  (edit {out_path} by hand "
                           f"to change it -- rows are already flushed)")
            continue

        labels = sorted(result)
        append_row(out_path, {
            "article_key": article["article_key"],
            "title": article["title"],
            "description": article["description"],
            "source": article["source"],
            "labels": "|".join(labels),
            "keyword_category": article["keyword_category"],
            "labeled_at": datetime.now(timezone.utc).isoformat(),
            "mode": "label",
        })
        history.append(f"{article['title'][:50]} -> {'|'.join(labels)}")
        session += 1

    report(out_path, session, time.time() - started)


def run_recheck(done, out_path, sample_size, getch):
    """Re-serve already-labelled articles to measure self-agreement."""
    pool = [r for r in done.values() if r.get("mode") == "label"]
    if len(pool) < sample_size:
        safe_print(f"Only {len(pool)} labelled article(s); need {sample_size}.")
        return

    picks = random.sample(pool, sample_size)
    recheck_path = out_path.replace(".csv", "_recheck.csv")

    safe_print(f"\nRe-labelling {sample_size} article(s) you have already done.")
    safe_print("Your earlier answer is not shown.\n")

    started = time.time()
    agree = total = 0

    for i, row in enumerate(picks):
        article = {
            "article_key": row["article_key"], "title": row["title"],
            "description": row["description"], "source": row["source"],
            "keyword_category": "",
        }
        result = collect(getch, article, i, sample_size, i,
                         time.time() - started)
        if result in ("QUIT",):
            break
        if result in ("SKIP", "UNDO"):
            continue

        labels = sorted(result)
        original = sorted((row["labels"] or "").split("|")) if row["labels"] else []
        total += 1
        if labels == original:
            agree += 1

        append_row(recheck_path, {
            "article_key": row["article_key"], "title": row["title"],
            "description": row["description"], "source": row["source"],
            "labels": "|".join(labels),
            "keyword_category": row.get("keyword_category", ""),
            "labeled_at": datetime.now(timezone.utc).isoformat(),
            "mode": f"recheck:was={'|'.join(original)}",
        })

    if total:
        pct = 100.0 * agree / total
        safe_print("\n" + "=" * WRAP)
        safe_print(f"  Self-agreement: {agree}/{total} = {pct:.1f}%")
        safe_print("")
        safe_print(textwrap.fill(
            f"  This is the ceiling. A model scoring above {pct:.0f}% on this "
            f"holdout is fitting your inconsistency, not the task. If the "
            f"number is low, the taxonomy is ambiguous -- that is a finding "
            f"about the label set, not about you.", WRAP))
        safe_print("=" * WRAP)
        safe_print(f"  Disagreements written to {recheck_path}")


def report(out_path: str, session: int, elapsed: float) -> None:
    done = load_done(out_path)
    counts = {}
    multi = 0
    for row in done.values():
        labels = [l for l in (row.get("labels") or "").split("|") if l]
        if len(labels) > 1:
            multi += 1
        for label in labels:
            counts[label] = counts.get(label, 0) + 1

    safe_print("\n" + "=" * WRAP)
    safe_print(f"  {session} labelled this session ({elapsed/60:.1f} min)")
    safe_print(f"  {len(done)} total in {out_path}")
    if done:
        safe_print(f"  {multi} article(s) carry more than one topic "
                   f"({100.0*multi/len(done):.0f}%)")
    safe_print("")
    for slug, label in TOPICS + [("NONE", "None of these")]:
        n = counts.get(slug, 0)
        bar = "#" * min(40, n)
        safe_print(f"  {label:<14} {n:>4}  {bar}")
    safe_print("=" * WRAP)

    thin = [label for slug, label in TOPICS if counts.get(slug, 0) < 20]
    if thin:
        safe_print(textwrap.fill(
            f"  Under 20 examples for: {', '.join(thin)}. Per-class metrics "
            f"on those will be too noisy to report. Either label more of them "
            f"or say plainly that those classes are unmeasured.", WRAP))


def main():
    parser = argparse.ArgumentParser(
        description="Hand-label articles for the topic classifier holdout.")
    parser.add_argument("--source",
                        help="Postgres URL, or a path to a .jsonl/.csv export.")
    parser.add_argument("--out", default="data/labels.csv",
                        help="Output CSV (default: data/labels.csv).")
    parser.add_argument("--limit", type=int, default=300,
                        help="Target number of labelled articles (default: 300).")
    parser.add_argument("--recheck", type=int, metavar="N",
                        help="Re-label N done articles to measure self-agreement.")
    parser.add_argument("--stats", action="store_true",
                        help="Print the current distribution and exit.")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    done = load_done(args.out)
    getch = make_getch()

    if getch is None:
        safe_print("Single-key input unavailable; type digits then Enter "
                   "(e.g. '15' for Tech + Programming).")

    if args.stats:
        report(args.out, 0, 0.0)
        return

    try:
        if args.recheck:
            run_recheck(done, args.out, args.recheck, getch)
            return

        if not args.source:
            parser.error("--source is required unless using --stats or --recheck")

        if args.source.startswith(("postgresql://", "postgres://")):
            articles = load_from_postgres(args.source, args.limit)
        else:
            articles = load_from_file(args.source)

        run_label(articles, done, args.out, args.limit, getch)

    except KeyboardInterrupt:
        safe_print("\n\nStopped. Every labelled row is already on disk.")
        report(args.out, 0, 0.0)


if __name__ == "__main__":
    main()
