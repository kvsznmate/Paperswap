"""One-off backfill: relabel articles that are already stored in the database.

The content classifier in news_fetcher.py only runs on newly fetched articles.
Rows inserted before it existed keep whatever topic the query that found them
happened to assign, and deduplication means they are never re-examined -- which
is why a skincare story can still show a FINANCE chip on the card.

This re-runs the same classifier over the existing rows, and (only where the
stored blurb is one the pipeline generated itself) realigns that too.

Run it from inside the backend container:

    docker compose exec news-cards-backend python backfill_categories.py           # preview
    docker compose exec news-cards-backend python backfill_categories.py --apply   # commit
"""

import argparse

import database as db
from news_fetcher import (
    TOPIC_FEEDS,
    classify_category,
    fallback_image,
    _STOCK_IMAGES,
)


# Blurbs the pipeline writes itself. Anything in this set is filler we may safely
# rewrite for the new topic; anything else is the publisher's own words and is
# left untouched.
GENERATED_BLURBS = {cfg["summary_fallback"] for cfg in TOPIC_FEEDS.values()} | {
    "Semiconductor supply dynamics, hardware innovation, and market demand driving global hardware valuations.",
    "Mission milestones and observational findings advancing our understanding of the solar system and beyond.",
    "Formulation trends, ingredient science, and brand strategy shaping the consumer beauty market.",
}


def plan_changes(rows: list) -> list:
    """Work out which rows need relabelling, without touching the database."""
    changes = []
    for row in rows:
        old_cat = db.normalize_category(row["category"])

        # Classify on the TITLE ONLY. The stored description may itself be a
        # wrong generated blurb from the older build, so feeding it back in
        # would let one bug reinforce the other.
        new_cat = classify_category(row["title"], "", old_cat)
        if new_cat == old_cat:
            continue

        # Only swap generic filler art, never a real publisher photo.
        new_image = row["image_url"]
        if new_image in _STOCK_IMAGES:
            new_image = fallback_image(new_cat, row["id"])

        new_desc = row["description"]
        if new_desc in GENERATED_BLURBS:
            new_desc = TOPIC_FEEDS[new_cat]["summary_fallback"]

        changes.append({
            "id": row["id"],
            "old": old_cat,
            "new": new_cat,
            "description": new_desc,
            "image_url": new_image,
            "title": row["title"],
        })
    return changes


def backfill(apply_changes: bool = False) -> None:
    with db.db_cursor() as cursor:
        cursor.execute(
            "SELECT id, title, description, category, image_url FROM articles ORDER BY id"
        )
        rows = cursor.fetchall()

    changes = plan_changes(rows)

    print(f"Scanned {len(rows)} article(s); {len(changes)} need relabelling.\n")
    for c in changes:
        print(f"  #{c['id']:<5} {c['old']:<12} -> {c['new']:<12} {c['title'][:56]}")

    if not changes:
        print("Nothing to do.")
        return

    if not apply_changes:
        print("\nDRY RUN - nothing written. Re-run with --apply to commit.")
        return

    # One transaction for the whole relabel: either every row moves or none do.
    # Previously a crash mid-loop left the table half-migrated.
    with db.db_cursor(commit=True) as cursor:
        for c in changes:
            cursor.execute(
                "UPDATE articles SET category = %s, description = %s, image_url = %s WHERE id = %s",
                (c["new"], c["description"], c["image_url"], c["id"]),
            )

    print(f"\nUpdated {len(changes)} row(s).")

    # Show the resulting spread so the outcome is easy to eyeball.
    with db.db_cursor() as cursor:
        cursor.execute(
            "SELECT category, COUNT(*) AS n FROM articles GROUP BY category ORDER BY n DESC"
        )
        print("\nArticles per topic after backfill:")
        for row in cursor.fetchall():
            print(f"  {row['category']:<12} {row['n']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Relabel stored articles using the content classifier."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the changes (default is a dry run that only prints them).",
    )
    args = parser.parse_args()

    # Script entry point: owns the pool lifecycle, since lifespan() never runs here.
    db.init_pool(minconn=1, maxconn=4)
    try:
        backfill(apply_changes=args.apply)
    finally:
        db.close_pool()
