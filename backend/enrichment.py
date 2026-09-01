"""Nightly enrichment: article body extraction, extractive bullets, embeddings.

Runs as a SEPARATE ONE-SHOT PROCESS, not as an APScheduler job inside the API.
That is the whole design constraint. This deployment is an OCI E2.1.Micro with
956 MB of RAM and 2 GB of swap, shared with Postgres and the API container. The
only way an ONNX session fits is if it is never resident at the same time as
anything else that can be avoided, and if the process exits when it is done.

    docker compose run --rm enrichment python enrichment.py --dry-run
    docker compose run --rm enrichment python enrichment.py --limit 5
    docker compose run --rm enrichment python enrichment.py

Measured on the deployed instance: ~6.2 s/article, so a full 84-article batch
takes roughly 9 minutes. That matches the estimate in ADR-011.

Cost of the models chosen, on this box:

    all-MiniLM-L6-v2, int8 ONNX   ~23 MB on disk, ~150 MB peak RSS
    TextRank                       pure numpy, no model
    trafilatura                    HTTP + lxml, no model

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

# Give up on a row after this many failed attempts. Three nights is enough for a
# transient outage to clear; beyond that the page is genuinely unreadable
# (paywall, JS shell, bot challenge) and refetching it nightly forever is waste.
MAX_ATTEMPTS = int(os.getenv("ENRICH_MAX_ATTEMPTS", "3"))

# Refuse to load the ONNX session below this much available memory. 250 MB is
# roughly the session plus activations plus headroom; starting under it means
# swapping, and swapping here means the OOM killer picks a victim -- usually
# Postgres, because it is the largest RSS on the box.
MIN_AVAILABLE_MB = int(os.getenv("ENRICH_MIN_AVAILABLE_MB", "250"))

# Domains whose links are redirect wrappers rather than articles. Google News
# bounces through consent.google.com and loops; trafilatura burns three retries
# and ~2 s per row discovering this. Skipping them up front costs nothing.
#
# The feeds were switched to direct publishers, so this should match nothing new
# -- it is here so a future feed change that reintroduces a wrapper fails
# cheaply and visibly instead of quietly producing empty card backs.
UNREADABLE_HOSTS = ("news.google.com", "consent.google.com")

# Sentences shorter than this are bylines, photo credits, or "Sign up for our
# newsletter". They score well on TextRank because they are lexically similar to
# everything, and they are useless as bullets.
MIN_SENTENCE_CHARS = 40
# And an upper bound, because a card has to display these. Anything longer is
# usually a list or a table that survived extraction as one block.
MAX_SENTENCE_CHARS = 320
MAX_SENTENCES = 60


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
# STAGE 1 -- ARTICLE BODY
# ---------------------------------------------------------------------------

def extract_full_text(url: str):
    """Pull the article body. Returns None on any failure.

    None is a real value here and is stored as such: it means extraction was
    attempted and did not produce usable text. Bullets are then skipped rather
    than being generated from the two-sentence RSS description, which would just
    hand those two sentences back as "bullets".
    """
    if not url or any(host in url for host in UNREADABLE_HOSTS):
        return None

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
_LIST_MARKER = re.compile(r'^\s*(?:[-*\u2022\u2013]|\d+[.)])\s+')


def split_sentences(text: str) -> list:
    """Split into candidate bullet sentences.

    Splits on LINE BREAKS FIRST, then on sentence punctuation within each line.
    The first version flattened newlines to spaces before splitting, which
    destroyed the only boundary between list items -- trafilatura returns bullet
    lists as separate lines whose items often have no terminal punctuation, so
    an entire list collapsed into one 400-character "sentence" and TextRank
    happily ranked it first. Real example from production:

        "Architecture Shape PES splits the agent into two components: The
        contract bridge between them enforces: - Approval matrix: ... - DLP
        grading: ... - Identity continuity: ..."

    Leading list markers are stripped so a bullet does not start with a dash,
    and MAX_SENTENCE_CHARS drops anything that is still a run-on, because these
    have to fit on a phone card.
    """
    out = []
    for line in text.split("\n"):
        line = _LIST_MARKER.sub("", line.strip())
        if not line:
            continue
        for sentence in _SENTENCE_BREAK.split(line):
            sentence = " ".join(sentence.split())
            if MIN_SENTENCE_CHARS <= len(sentence) <= MAX_SENTENCE_CHARS:
                out.append(sentence)
            if len(out) >= MAX_SENTENCES:
                return out
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
# PIPELINE
# ---------------------------------------------------------------------------

def pending_articles(limit=None) -> list:
    """Rows awaiting enrichment.

    enriched_at IS NULL is the resume point; enrich_attempts caps how many
    nights a permanently unreadable page can cost us.
    """
    sql = '''
        SELECT id, title, description, url, full_text, enrich_attempts
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


def enrich_article(row: dict, embedder: "Embedder") -> dict:
    """Produce full_text, bullets, and the article vector for one row."""
    full_text = row.get("full_text") or extract_full_text(row.get("url"))

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


def write_enrichment(article_id: int, result: dict) -> bool:
    """Commit one article. Returns True if the row is finished.

    enriched_at is set ONLY when bullets were produced. The previous version set
    it unconditionally, so a failed body fetch marked the row permanently done
    with an empty card back and no retry -- it silently poisoned eight rows in
    production before anyone read the summary_bullets column.

    The embedding is written either way, because it is computed from title and
    description and is real work regardless of whether the body was reachable.

    Per-row commit rather than per-batch: this runs beside Postgres on a box that
    can OOM, and a kill 40 articles in should keep those 40.
    """
    finished = bool(result["bullets"])

    with db.db_cursor(commit=True) as cursor:
        cursor.execute('''
            UPDATE articles
            SET full_text       = %s,
                summary_bullets = %s,
                embedding       = %s,
                enrich_attempts = COALESCE(enrich_attempts, 0) + 1,
                enriched_at     = CASE WHEN %s THEN NOW() ELSE NULL END
            WHERE id = %s
        ''', (
            result["full_text"],
            json.dumps(result["bullets"]) if result["bullets"] else None,
            result["embedding"],
            finished,
            article_id,
        ))

    return finished


def run(limit=None, dry_run: bool = False) -> None:
    started = time.time()

    db.init_pool()
    try:
        pending = pending_articles(limit)
        logger.info("%d article(s) awaiting enrichment", len(pending))

        if dry_run:
            for row in pending:
                logger.info("  [%s] attempt %s  %s", row["id"],
                            (row.get("enrich_attempts") or 0) + 1,
                            (row.get("title") or "")[:70])
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

        # Counted separately on purpose. The old single "enriched" counter
        # reported 5 successes for a batch that produced zero bullets, which is
        # the same class of error ADR-010 exists to prevent -- a number that
        # reads as a measurement but is not one.
        with_bullets = no_text = errored = exhausted = 0

        for row in pending:
            try:
                result = enrich_article(row, embedder)
                if write_enrichment(row["id"], result):
                    with_bullets += 1
                else:
                    no_text += 1
                    if (row.get("enrich_attempts") or 0) + 1 >= MAX_ATTEMPTS:
                        exhausted += 1
                        logger.info("  giving up on %s after %d attempts: %s",
                                    row["id"], MAX_ATTEMPTS,
                                    (row.get("url") or "")[:80])
            except Exception as exc:
                # One bad article must not end the batch. enriched_at stays NULL
                # so a later run retries it, within the attempt budget.
                logger.warning("Article %s raised: %s", row["id"], exc)
                errored += 1

            done = with_bullets + no_text + errored
            if done % 10 == 0:
                logger.info("  %d/%d processed", done, len(pending))

        logger.info(
            "%d with bullets, %d embedded but no article text, %d errored "
            "(%d gave up permanently) in %.1fs",
            with_bullets, no_text, errored, exhausted, time.time() - started,
        )
        if no_text and not with_bullets:
            logger.warning(
                "Every article embedded but none produced bullets. Check that "
                "articles.url points at publishers rather than a redirect "
                "wrapper -- run check_feeds.py --extract."
            )
    finally:
        db.close_pool()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # trafilatura logs every redirect hop at INFO through urllib3, which buried
    # the actual result lines behind hundreds of URLs during the Google News
    # failures. Warnings still surface.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description="Nightly Paperswap enrichment.")
    parser.add_argument("--limit", type=int, help="Cap the number of articles.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be processed; write nothing.")
    args = parser.parse_args()

    run(limit=args.limit, dry_run=args.dry_run)
