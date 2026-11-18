"""Scheduled enrichment: article embeddings and related coverage.

Two stages, both computed from what the feed itself published (title and
description). The job never downloads article pages: the publishers' terms and
the EU text-and-data-mining opt-outs do not allow copying their article text,
and nothing the analysis needs comes from it (ADR-015).

  1. Embed each new article's "title. description" with MiniLM.
  2. Related coverage: for every article, the nearest articles from OTHER
     publishers by cosine similarity, stored as `related_ids` and served on
     the card back as headline + publisher + link.

Runs as a SEPARATE ONE-SHOT PROCESS, not as an APScheduler job inside the API.
That is the whole design constraint. This deployment is an OCI E2.1.Micro with
956 MB of RAM and 2 GB of swap, shared with Postgres and the API container. The
only way an ONNX session fits is if it is never resident at the same time as
anything else that can be avoided, and if the process exits when it is done.

    docker compose run --rm enrichment python enrichment.py --dry-run
    docker compose run --rm enrichment python enrichment.py --limit 5
    docker compose run --rm enrichment python enrichment.py

Run every 6 hours by run_enrichment.sh (cron on the VM). Embedding loads the
ONNX session only when articles are pending; the related-coverage pass is
numpy over the stored vectors and runs every time. Design rationale: ADR-012
and ADR-015 in docs/ARCHITECTURE.md.

Cost of the model chosen, on this box:

    all-MiniLM-L6-v2, int8 ONNX   ~23 MB on disk, ~150 MB peak RSS

The models NOT chosen, and why: distilbart-cnn-12-6 (1.2 GB) and
bart-large-mnli (1.6 GB) do not fit in the ~550 MB left after Postgres and the
API. They would not run slowly -- they would page against a network-attached
boot volume until the OOM killer took Postgres down. Comparing them against the
extractive summarizer here is a laptop job, run offline against a pg_dump.

--------------------------------------------------------------------------
MODEL FILES

Not vendored and not auto-downloaded -- an unattended download on a 956 MB box
is a bad failure mode. Fetch once, by hand, into backend/models/:

    curl -s https://huggingface.co/api/models/Xenova/all-MiniLM-L6-v2 \
      | python3 -c "import json,sys; [print(s['rfilename']) for s in json.load(sys.stdin)['siblings']]"

    curl -fL -o minilm-int8.onnx \
      https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/onnx/model_quantized.onnx
    curl -fL -o tokenizer.json \
      https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/tokenizer.json

Use the Xenova repo, not sentence-transformers/all-MiniLM-L6-v2. The latter's
only quantized ONNX export is model_qint8_avx512_vnni.onnx, and this 2018-era
Xeon has no AVX-512 VNNI. Xenova's export targets Transformers.js, which runs in
browsers, so it cannot be architecture-specific.

The -f on curl matters. Without it a 404 body is written INTO the output file
and you get an HTML error page named minilm-int8.onnx that fails hours later as
a confusing ONNX parse error.
--------------------------------------------------------------------------
"""

import os
import sys
import json
import time
import logging
import argparse

import database as db

logger = logging.getLogger("paperswap.enrichment")

MODEL_PATH = os.getenv("EMBED_MODEL_PATH", "models/minilm-int8.onnx")
TOKENIZER_PATH = os.getenv("EMBED_TOKENIZER_PATH", "models/tokenizer.json")

# Give up on a row after this many failed attempts. Embedding a headline does
# not touch the network, so a row that raises three runs in a row is a poison
# row (bad encoding, a tokenizer edge case), and retrying it every 6 h forever
# would hide the problem in the logs.
MAX_ATTEMPTS = int(os.getenv("ENRICH_MAX_ATTEMPTS", "3"))

# Related coverage. Two articles are related when their title+description
# vectors have cosine similarity >= RELATED_SIMILARITY, they come from different
# publishers, and they were fetched within RELATED_WINDOW_HOURS of each other.
# 0.55 was measured on the live deck (300 cards, 2026-11-18): from 0.55 up the
# pairs are nearly all the same story from another outlet; 0.45-0.55 is mostly
# the same topic, not the same story (a football and a rugby result for one
# country, a deals list and a scam warning). 57 of 300 cards (19%) got at
# least one related article at 0.55. ADR-015.
RELATED_SIMILARITY = float(os.getenv("RELATED_SIMILARITY", "0.55"))
RELATED_MAX = int(os.getenv("RELATED_MAX", "3"))
RELATED_WINDOW_HOURS = float(os.getenv("RELATED_WINDOW_HOURS", "72"))

# all-MiniLM-L6-v2. A stored vector of any other length came from a different
# model and cannot be compared with these, so it is left out of the pass.
EMBED_DIM = 384

# Refuse to load the ONNX session below this much available memory. 250 MB is
# roughly the session plus activations plus headroom; starting under it means
# swapping, and swapping here means the OOM killer picks a victim -- usually
# Postgres, because it is the largest RSS on the box.
MIN_AVAILABLE_MB = int(os.getenv("ENRICH_MIN_AVAILABLE_MB", "250"))

# ---------------------------------------------------------------------------
# MEMORY GUARD
# ---------------------------------------------------------------------------

def available_mb() -> int:
    """MemAvailable from /proc/meminfo, in MB.

    MemAvailable, not MemFree: MemFree excludes reclaimable page cache and reads
    alarmingly low on a healthy box. Inside a container this reports the HOST's
    memory, which is what we want -- the question is whether the machine has
    room, not whether the cgroup does. Returns -1 where /proc is absent so the
    guard degrades to a warning on a dev laptop.
    """
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return -1


# ---------------------------------------------------------------------------
# EMBEDDING SESSION
# ---------------------------------------------------------------------------

class Embedder:
    """Minimal ONNX sentence embedder: tokenizers + onnxruntime + numpy.

    Deliberately not sentence-transformers, which pulls torch (~350 MB resident
    on import, before any model loads). Mean pooling and L2 normalization are
    ten lines and are done here explicitly.
    """

    def __init__(self, model_path: str, tokenizer_path: str):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        for path, label in ((model_path, "MODEL"), (tokenizer_path, "TOKENIZER")):
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"ONNX {label.lower()} not found at {path}. See the module "
                    f"docstring for the one-time download, or set EMBED_{label}_PATH."
                )

        self.np = np
        self.tokenizer = Tokenizer.from_file(tokenizer_path)
        self.tokenizer.enable_truncation(max_length=256)
        self.tokenizer.enable_padding()

        opts = ort.SessionOptions()
        # One thread. The instance has a single shared OCPU, so extra threads
        # contend with Postgres and the API for it and make the batch slower,
        # not faster, while adding per-thread arena allocations.
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.session = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.input_names = {i.name for i in self.session.get_inputs()}

    def encode(self, texts: list, batch_size: int = 8):
        """Embed a list of strings. Returns an (n, dim) L2-normalized array."""
        np = self.np
        vectors = []

        for start in range(0, len(texts), batch_size):
            chunk = texts[start:start + batch_size]
            encoded = self.tokenizer.encode_batch(chunk)

            ids = np.array([e.ids for e in encoded], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)

            feed = {"input_ids": ids, "attention_mask": mask}
            # The deployed export declares token_type_ids; some others omit it.
            # Feeding an input the graph does not declare is a hard error in
            # onnxruntime, so this is gated on what the session actually reports.
            if "token_type_ids" in self.input_names:
                feed["token_type_ids"] = np.zeros_like(ids)

            hidden = self.session.run(None, feed)[0]

            expanded = mask[..., None].astype(np.float32)
            pooled = (hidden * expanded).sum(axis=1) / np.clip(
                expanded.sum(axis=1), 1e-9, None
            )
            norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
            vectors.append(pooled / norms)

        return np.vstack(vectors)


# ---------------------------------------------------------------------------
# STAGE 1 -- EMBEDDINGS
# ---------------------------------------------------------------------------

def pending_articles(limit=None) -> list:
    """Rows awaiting an embedding.

    enriched_at IS NULL is the resume point; enrich_attempts caps how many runs
    a row that keeps raising can cost.
    """
    sql = '''
        SELECT id, title, description, enrich_attempts
        FROM articles
        WHERE enriched_at IS NULL
          AND COALESCE(enrich_attempts, 0) < %s
        ORDER BY id DESC
    '''
    params = [MAX_ATTEMPTS]
    if limit:
        sql += " LIMIT %s"
        params.append(limit)

    with db.db_cursor() as cursor:
        cursor.execute(sql, params)
        # The pool uses cursor_factory=RealDictCursor (database.init_pool), so
        # rows are dict-like and keyed by column name, not positional tuples.
        return [dict(r) for r in cursor.fetchall()]


def embed_article(row: dict, embedder: "Embedder") -> bytes:
    """The article vector, packed for the BYTEA column.

    It embeds title + description, the text the user sees on the card and
    swipes on. The ranker learns from swipes, so its input has to be the same
    stimulus that produced the label.
    """
    stimulus = f"{row['title']}. {row.get('description') or ''}".strip()
    return db.pack_embedding(embedder.encode([stimulus])[0])


def write_embedding(article_id: int, embedding: bytes) -> None:
    """Commit one article. Per-row commit rather than per-batch: this runs
    beside Postgres on a box that can OOM, and a kill 40 articles in should
    keep those 40."""
    with db.db_cursor(commit=True) as cursor:
        cursor.execute('''
            UPDATE articles
            SET embedding       = %s,
                enrich_attempts = COALESCE(enrich_attempts, 0) + 1,
                enriched_at     = NOW()
            WHERE id = %s
        ''', (embedding, article_id))


def record_failed_attempt(article_id: int) -> None:
    """Count a run that raised on this row, so MAX_ATTEMPTS can stop it."""
    with db.db_cursor(commit=True) as cursor:
        cursor.execute(
            "UPDATE articles SET enrich_attempts = COALESCE(enrich_attempts, 0) + 1 "
            "WHERE id = %s", (article_id,))


# ---------------------------------------------------------------------------
# STAGE 2 -- RELATED COVERAGE
#
# For each article, the closest articles from OTHER publishers in the same few
# days. This is what the card back shows instead of text copied from the
# article (ADR-015): coverage of the same story elsewhere, which the database
# can work out from its own vectors and which the publishers' terms allow
# (headline, publisher, link).
#
# Recomputed for every embedded row on every run, not just the new ones: a new
# article can be the best neighbour of an old one. ~1,000 rows x 384 floats is a
# 4 MB similarity matrix, milliseconds of numpy, and no ONNX session.
# ---------------------------------------------------------------------------

def compute_related(ids: list, sources: list, times: list, vectors,
                    threshold: float = None, top_k: int = None,
                    window_hours: float = None) -> dict:
    """Return {article_id: [related ids, most similar first]}.

    Pure function, so it is tested without a database. `times` are datetimes
    (None means unknown, which no window excludes); `vectors` is (n, dim).
    An article with no neighbour maps to [], which is a computed answer, not a
    missing one.
    """
    import numpy as np

    threshold = RELATED_SIMILARITY if threshold is None else threshold
    top_k = RELATED_MAX if top_k is None else top_k
    window_hours = RELATED_WINDOW_HOURS if window_hours is None else window_hours

    n = len(ids)
    if n == 0:
        return {}

    V = np.asarray(vectors, dtype=np.float32).reshape(n, -1)
    V = V / np.clip(np.linalg.norm(V, axis=1, keepdims=True), 1e-9, None)
    sim = V @ V.T

    # Same publisher (which includes the diagonal) never counts: "related
    # coverage" means another outlet, not the same outlet's follow-up.
    src = np.array([s or "" for s in sources], dtype=object)
    sim[src[:, None] == src[None, :]] = -np.inf

    if window_hours and window_hours > 0:
        t = np.array([ts.timestamp() if ts is not None else np.nan for ts in times])
        # NaN compares False, so an unknown time is never excluded.
        sim[np.abs(t[:, None] - t[None, :]) > window_hours * 3600] = -np.inf

    related = {}
    for i in range(n):
        best = np.argsort(-sim[i], kind="stable")[:top_k]
        related[ids[i]] = [int(ids[j]) for j in best if sim[i, j] >= threshold]
    return related


def refresh_related() -> dict:
    """Recompute related_ids for every embedded article; write only changes.

    Returns counts for the run log. related_ids stays NULL on rows with no
    embedding yet: NULL means "not computed", [] means "computed, none found".
    """
    import numpy as np

    with db.db_cursor() as cursor:
        cursor.execute('''
            SELECT id, source, created_at, embedding, related_ids
            FROM articles
            WHERE embedding IS NOT NULL
        ''')
        rows = [dict(r) for r in cursor.fetchall()]

    usable = []
    for r in rows:
        vec = np.frombuffer(bytes(r["embedding"]), dtype="<f4")
        if vec.shape[0] == EMBED_DIM:
            usable.append((r, vec))

    related = compute_related(
        [r["id"] for r, _ in usable],
        [r["source"] for r, _ in usable],
        [r["created_at"] for r, _ in usable],
        np.vstack([v for _, v in usable]) if usable else np.zeros((0, EMBED_DIM)),
    )

    changed = []
    for r, _ in usable:
        value = json.dumps(related[r["id"]])
        if r["related_ids"] != value:
            changed.append((value, r["id"]))

    if changed:
        with db.db_cursor(commit=True) as cursor:
            for value, article_id in changed:
                cursor.execute("UPDATE articles SET related_ids = %s WHERE id = %s",
                               (value, article_id))

    return {
        "articles": len(usable),
        "skipped_foreign_dim": len(rows) - len(usable),
        "with_related": sum(1 for ids in related.values() if ids),
        "changed": len(changed),
    }


# ---------------------------------------------------------------------------
# RUN
# ---------------------------------------------------------------------------

def run(limit=None, dry_run: bool = False) -> None:
    started = time.time()

    db.init_pool()
    try:
        pending = pending_articles(limit)
        logger.info("%d article(s) awaiting an embedding", len(pending))

        if dry_run:
            for row in pending:
                logger.info("  [%s] attempt %s  %s", row["id"],
                            (row.get("enrich_attempts") or 0) + 1,
                            (row.get("title") or "")[:70])
            return

        embedded = errored = exhausted = 0
        if pending:
            mem = available_mb()
            if mem == -1:
                logger.warning("Cannot read /proc/meminfo -- skipping the memory guard")
            elif mem < MIN_AVAILABLE_MB:
                logger.error(
                    "Only %d MB available, need %d. Refusing to load the ONNX session: "
                    "starting here means swapping, and swapping here means the OOM "
                    "killer takes Postgres. Retry when the box is quieter.",
                    mem, MIN_AVAILABLE_MB,
                )
                sys.exit(1)

            embedder = Embedder(MODEL_PATH, TOKENIZER_PATH)
            logger.info("ONNX session up (%d MB available)", available_mb())

            for row in pending:
                try:
                    write_embedding(row["id"], embed_article(row, embedder))
                    embedded += 1
                except Exception as exc:
                    # One bad article must not end the batch. enriched_at stays
                    # NULL, so a later run retries it within the attempt budget.
                    logger.warning("Article %s raised: %s", row["id"], exc)
                    errored += 1
                    record_failed_attempt(row["id"])
                    if (row.get("enrich_attempts") or 0) + 1 >= MAX_ATTEMPTS:
                        exhausted += 1
                        logger.info("  giving up on %s after %d attempts",
                                    row["id"], MAX_ATTEMPTS)

                done = embedded + errored
                if done % 10 == 0:
                    logger.info("  %d/%d processed", done, len(pending))

        rel = refresh_related()
        logger.info(
            "%d embedded, %d errored (%d gave up permanently); related coverage: "
            "%d of %d articles have at least one, %d rows changed%s, in %.1fs",
            embedded, errored, exhausted,
            rel["with_related"], rel["articles"], rel["changed"],
            f", {rel['skipped_foreign_dim']} skipped (vector from another model)"
            if rel["skipped_foreign_dim"] else "",
            time.time() - started,
        )
    finally:
        db.close_pool()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Paperswap enrichment: embeddings and related coverage.")
    parser.add_argument("--limit", type=int, help="Cap the number of articles.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be processed; write nothing.")
    args = parser.parse_args()

    run(limit=args.limit, dry_run=args.dry_run)
