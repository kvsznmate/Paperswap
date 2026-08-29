"""Nightly enrichment: article body extraction, extractive bullets, embeddings.

Runs as a SEPARATE ONE-SHOT PROCESS, not as an APScheduler job inside the API.
That is the whole design constraint. This deployment is an OCI E2.1.Micro with
956 MB of RAM and 2 GB of swap, shared with Postgres and the API container. The
only way an ONNX session fits is if it is never resident at the same time as
anything else that can be avoided, and if the process exits when it is done.

    python enrichment.py            # process everything unenriched
    python enrichment.py --limit 20 # cap the batch, useful for a first run
    python enrichment.py --dry-run  # report what would be processed, write nothing

Cost of the models chosen, on this box:

    all-MiniLM-L6-v2, int8 ONNX   ~23 MB on disk, ~150 MB peak RSS
    TextRank                       pure numpy, no model
    trafilatura                    HTTP + lxml, no model

The models NOT chosen, and why: distilbart-cnn-12-6 (1.2 GB) and
bart-large-mnli (1.6 GB) do not fit in the ~550 MB left after Postgres and the
API. They would not run slowly -- they would page against a network-attached
boot volume until the OOM killer took Postgres down. Comparing them against the
extractive summarizer here is a laptop job, run offline against a pg_dump. See
docs/ for the evaluation notebook.

--------------------------------------------------------------------------
MODEL FILES

Not vendored and not auto-downloaded -- an unattended download on a 956 MB box
is a bad failure mode. Fetch once, by hand:

    mkdir -p backend/models
    cd backend/models
    # Check the repo's Files tab for the exact quantized filename; it differs
    # between exports and some are AVX-512-VNNI-specific, which this 2018-era
    # Xeon does not have. Prefer a generic int8 export.
    curl -L -o minilm-int8.onnx  <onnx model url>
    curl -L -o tokenizer.json    <tokenizer.json url>

Then point EMBED_MODEL_PATH / EMBED_TOKENIZER_PATH at them, or accept the
defaults below.
--------------------------------------------------------------------------
"""

import os
import re
import sys
import json
import time
import logging
import argparse

import database as db

logger = logging.getLogger("paperswap.enrichment")

MODEL_PATH = os.getenv("EMBED_MODEL_PATH", "models/minilm-int8.onnx")
TOKENIZER_PATH = os.getenv("EMBED_TOKENIZER_PATH", "models/tokenizer.json")

BULLET_COUNT = int(os.getenv("BULLET_COUNT", "3"))
MAX_FULL_TEXT_CHARS = int(os.getenv("MAX_FULL_TEXT_CHARS", "20000"))
FETCH_TIMEOUT = int(os.getenv("EXTRACT_TIMEOUT", "10"))

# Refuse to load the ONNX session below this much available memory. 250 MB is
# roughly the session plus activations plus headroom; starting under it means
# swapping, and swapping here means the OOM killer picks a victim -- usually
# Postgres, because it is the largest RSS on the box.
MIN_AVAILABLE_MB = int(os.getenv("ENRICH_MIN_AVAILABLE_MB", "250"))

# Sentences shorter than this are almost always bylines, photo credits, or
# "Sign up for our newsletter". They score well on TextRank because they are
# lexically similar to everything, and they are useless as bullets.
MIN_SENTENCE_CHARS = 40
MAX_SENTENCES = 60


# ---------------------------------------------------------------------------
# MEMORY GUARD
# ---------------------------------------------------------------------------

def available_mb() -> int:
    """MemAvailable from /proc/meminfo, in MB.

    MemAvailable, not MemFree: MemFree excludes reclaimable page cache and reads
    alarmingly low on a healthy box. Returns -1 where /proc is absent (macOS,
    Windows dev machines) so the guard degrades to a warning rather than
    blocking local work.
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
# STAGE 1 -- ARTICLE BODY
# ---------------------------------------------------------------------------

def extract_full_text(url: str):
    """Pull the article body. Returns None on any failure.

    None is a real value here and is stored as such: it means extraction was
    attempted and did not produce usable text. Bullets are then skipped for that
    row rather than being generated from the two-sentence RSS description, which
    would return those two sentences back as "bullets".
    """
    try:
        import trafilatura
    except ImportError:
        logger.error("trafilatura not installed -- pip install trafilatura")
        return None

    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
        )
    except Exception as exc:
        logger.debug("Extraction failed for %s: %s", url, exc)
        return None

    if not text or len(text) < 400:
        return None
    return text[:MAX_FULL_TEXT_CHARS]


# ---------------------------------------------------------------------------
# SENTENCE SPLITTING + TEXTRANK
#
# TextRank is implemented here rather than imported from sumy, for two reasons.
# sumy's tokenizer wants the nltk punkt corpus, a ~35 MB download onto a box
# that is already tight. And the algorithm is forty lines: build a sentence
# similarity graph, run PageRank on it, take the top-k. Writing it out means it
# can be explained rather than cited.
#
# The similarity edges use MiniLM sentence embeddings rather than the TF-IDF
# overlap the original 2004 paper used. The model is already loaded for the
# article vector, so the better similarity measure is free -- it catches
# sentences that restate an idea in different words, which TF-IDF misses.
# ---------------------------------------------------------------------------

_SENTENCE_BREAK = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\'\u201c])')


def split_sentences(text: str) -> list:
    """Regex sentence split, then drop the boilerplate that pollutes bullets."""
    raw = _SENTENCE_BREAK.split(text.replace("\n", " "))
    out = []
    for s in raw:
        s = " ".join(s.split())
        if len(s) >= MIN_SENTENCE_CHARS:
            out.append(s)
        if len(out) >= MAX_SENTENCES:
            break
    return out


def textrank(sentences: list, embeddings, top_k: int) -> list:
    """Return the top_k sentences by PageRank over the similarity graph,
    restored to their original document order.

    Document order matters: three sentences pulled out by score and printed in
    score order read as disconnected fragments. In source order they usually
    still read as a summary, because news writing is already ordered.
    """
    import numpy as np

    n = len(sentences)
    if n <= top_k:
        return sentences

    # Embeddings are L2-normalized, so the Gram matrix IS the cosine similarity.
    sim = embeddings @ embeddings.T
    np.fill_diagonal(sim, 0.0)
    sim = np.clip(sim, 0.0, None)

    row_sums = sim.sum(axis=1, keepdims=True)
    # A sentence similar to nothing else gets a uniform row rather than a
    # divide-by-zero. Without this one orphan sentence turns the whole vector
    # into NaN and the summary comes back empty.
    row_sums[row_sums == 0] = 1.0
    transition = sim / row_sums

    damping = 0.85
    scores = np.full(n, 1.0 / n)
    for _ in range(50):
        updated = (1 - damping) / n + damping * (transition.T @ scores)
        if np.abs(updated - scores).sum() < 1e-6:
            scores = updated
            break
        scores = updated

    top_idx = sorted(np.argsort(scores)[-top_k:])
    return [sentences[i] for i in top_idx]


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

        for path, label in ((model_path, "model"), (tokenizer_path, "tokenizer")):
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"ONNX {label} not found at {path}. See the module docstring "
                    f"for the one-time download, or set "
                    f"EMBED_{'MODEL' if label == 'model' else 'TOKENIZER'}_PATH."
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
            # Some MiniLM exports drop token_type_ids. Feeding an input the
            # graph does not declare is a hard error in onnxruntime, so this is
            # gated on what the session actually reports.
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
# PIPELINE
# ---------------------------------------------------------------------------

def pending_articles(limit=None) -> list:
    """Rows awaiting enrichment. enriched_at IS NULL is the resume point, so an
    interrupted run picks up exactly where it stopped."""
    sql = '''
        SELECT id, title, description, url, full_text
        FROM articles
        WHERE enriched_at IS NULL
        ORDER BY id DESC
    '''
    params = []
    if limit:
        sql += " LIMIT %s"
        params.append(limit)

    with db.db_cursor() as cursor:
        cursor.execute(sql, params)
        return [
            {
                "id": r[0], "title": r[1], "description": r[2],
                "url": r[3], "full_text": r[4],
            }
            for r in cursor.fetchall()
        ]


def enrich_article(row: dict, embedder: "Embedder") -> dict:
    """Produce full_text, bullets, and the article vector for one row."""
    full_text = row.get("full_text") or extract_full_text(row["url"])

    bullets = []
    if full_text:
        sentences = split_sentences(full_text)
        if len(sentences) >= 2:
            sentence_vectors = embedder.encode(sentences)
            bullets = textrank(sentences, sentence_vectors, BULLET_COUNT)

    # The article vector embeds title + description, NOT the body. The user
    # swipes on the card, and the card shows the headline and the short summary.
    # Embedding 20,000 characters the user never saw would train the ranker on a
    # different stimulus than the one that produced the label.
    stimulus = f"{row['title']}. {row.get('description') or ''}".strip()
    article_vector = embedder.encode([stimulus])[0]

    return {
        "full_text": full_text,
        "bullets": bullets,
        "embedding": db.pack_embedding(article_vector),
    }


def write_enrichment(article_id: int, result: dict) -> None:
    """Commit one article. Per-row rather than per-batch: this job runs beside
    Postgres on a box that can OOM, and a kill 40 articles in should keep those
    40 rather than roll back the lot."""
    with db.db_cursor(commit=True) as cursor:
        cursor.execute('''
            UPDATE articles
            SET full_text = %s,
                summary_bullets = %s,
                embedding = %s,
                enriched_at = NOW()
            WHERE id = %s
        ''', (
            result["full_text"],
            json.dumps(result["bullets"]) if result["bullets"] else None,
            result["embedding"],
            article_id,
        ))


def run(limit=None, dry_run: bool = False) -> None:
    started = time.time()

    db.init_pool()
    try:
        pending = pending_articles(limit)
        logger.info("%d article(s) awaiting enrichment", len(pending))

        if dry_run:
            for row in pending:
                logger.info("  [%s] %s", row["id"], row["title"][:70])
            return
        if not pending:
            return

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

        ok = failed = 0
        for row in pending:
            try:
                write_enrichment(row["id"], enrich_article(row, embedder))
                ok += 1
            except Exception as exc:
                # One bad article must not end the batch. enriched_at stays NULL
                # so tomorrow's run retries it.
                logger.warning("Article %s failed: %s", row["id"], exc)
                failed += 1

            if (ok + failed) % 10 == 0:
                logger.info("  %d/%d done", ok + failed, len(pending))

        logger.info(
            "Enriched %d, failed %d in %.1fs",
            ok, failed, time.time() - started,
        )
    finally:
        db.close_pool()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Nightly Paperswap enrichment.")
    parser.add_argument("--limit", type=int, help="Cap the number of articles.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be processed; write nothing.")
    args = parser.parse_args()

    run(limit=args.limit, dry_run=args.dry_run)
