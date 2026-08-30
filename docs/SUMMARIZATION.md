# Summarization

How the card-back bullets are produced, what they can and cannot be trusted to do, and the numbers that tell you when something has broken.

Design rationale lives in ADR-011 and ADR-013 (`ARCHITECTURE.md`). This document is the operational reference.

---

## What it produces

Three bullets per article, stored in `articles.summary_bullets` as a JSON array and served by `/api/v1/feed` as a real array (parsed in `database._decorate_articles`, which yields `[]` rather than null so the client can iterate unconditionally).

Every bullet is a **verbatim sentence from the article body**. Nothing is generated, rephrased, or compressed.

---

## Pipeline

Runs in `enrichment.py`, invoked by `run_enrichment.sh` every 6 hours.

```
articles.url
   ↓  trafilatura              extract_full_text()     → articles.full_text
   ↓  split on \n, then [.!?]  split_sentences()       → candidate sentences
   ↓  MiniLM int8 ONNX         Embedder.encode()       → one vector per sentence
   ↓  PageRank over cosine     textrank()              → top 3, in document order
   ↓                                                    → articles.summary_bullets
```

Two details that are easy to misread:

**The similarity graph uses sentence embeddings, not TF-IDF.** The original 2004 TextRank paper used word overlap. The MiniLM session is already loaded for the article vector, so the better measure is free — it catches sentences that restate an idea in different words.

**Bullets are returned in document order, not score order.** Three sentences printed by rank read as disconnected fragments; in source order they usually still read as a summary, because news writing is already ordered.

---

## Reliability

### Guaranteed

**No hallucination is structurally possible.** Every bullet can be found verbatim in `full_text`:

```sql
SELECT full_text LIKE '%' || (summary_bullets::json->>0) || '%' FROM articles WHERE id = ?;
```

This is the strongest reliability property available and the main reason extractive was right here beyond the RAM constraint. An abstractive model on a news card can fabricate a quote, a number, or an attribution. This cannot.

### Not guaranteed

**Centrality is not importance.** TextRank ranks sentences by similarity to the rest of the article. That usually correlates with summarising well, but a sentence restating the topic in generic terms scores higher than the one carrying the actual news. Observed example:

> "This gives designers something useful to think about when creating layouts."

Grammatical, no broken reference, and attachable to any design article ever written.

**Dangling references.** The classic extractive failure. Pull sentence 14 out of context and "he" has no antecedent. Measured at 5% (see below) — low enough not to warrant a rule, and note that a regex filter would flag informative bullets while missing empty ones like the example above.

**Boilerplate leakage.** If trafilatura captures a cookie notice or newsletter pitch, those become candidates. `MIN_SENTENCE_CHARS` filters short furniture but not a well-formed paragraph of it.

**Length bias.** Longer sentences share more content with everything else, so they score higher. Scoring does not normalise for length. `MAX_SENTENCE_CHARS` caps the damage but does not remove the bias.

---

## Measured baseline

Corpus of 114 articles, 341 bullets, taken 2026-08-30. **Re-measure after any feed change** — these are the reference values every threshold below is calibrated against.

| Metric | Value |
| --- | --- |
| Extraction success | 98.8% (82/83 on first full run) |
| Bullets per article | 2.99 |
| Mean bullet length | 150 chars |
| Typical card total | ~450 chars |
| Dangling-pronoun starts | 5% (18/341) |
| Throughput | ~3.4 s/article |

The single extraction failure was an FT paywall (403), which exhausted its 3-attempt budget and stopped being retried. That is the retry logic working, not a defect.

---

## Operational thresholds

What to do when a number moves. These are tripwires, not targets.

| Metric | Healthy | Investigate | Likely cause |
| --- | --- | --- | --- |
| Extraction success | ≥ 90% | < 80% | A feed rotted or added a paywall → `check_feeds.py --extract` |
| Bullets per article | 2.8 – 3.0 | < 2.5 | Extraction returning thin text, or `split_sentences` over-filtering |
| Mean bullet length | 120 – 200 | > 250 | Run-ons — check the splitter against list markup |
| | | < 80 | Fragments — `MIN_SENTENCE_CHARS` too low, or headline-only extraction |
| Dangling pronouns | < 10% | > 20% | Consider penalising pronoun openers for bullet 1 only |
| "no article text" per run | < 10% of batch | > 30% | Feed or network problem, not a summarizer problem |
| Rows exhausting retries | rare, stable | rising | A publisher started blocking — check which in the log |

`enrichment.py` already warns on the systemic case (`no_text >= 5 and not with_bullets`) with a pointer to `check_feeds.py`. The floor of 5 exists so a small tail-end batch containing one paywalled article does not cry wolf; a warning that fires on healthy runs stops being read.

---

## Auditing

Read a random sample with character counts:

```bash
cd ~/paperswap/backend
docker compose run --rm enrichment python -c "
import database as db, json, textwrap
db.init_pool()
with db.db_cursor() as c:
    c.execute('''SELECT source, title, summary_bullets FROM articles
                 WHERE summary_bullets IS NOT NULL ORDER BY random() LIMIT 5''')
    for r in c.fetchall():
        print('='*70)
        print(f\"{r['source']} | {r['title'][:58]}\")
        for b in json.loads(r['summary_bullets']):
            print()
            for line in textwrap.wrap(b, 66): print('  ' + line)
            print(f'  [{len(b)} chars]')
db.close_pool()
"
```

Corpus-wide stats including the pronoun rate:

```bash
docker compose run --rm enrichment python -c "
import database as db, json, re
P = re.compile(r'^(He|She|They|It|This|That|These|Those|His|Her|Their)\b')
db.init_pool()
with db.db_cursor() as c:
    c.execute('SELECT summary_bullets FROM articles WHERE summary_bullets IS NOT NULL')
    rows = [json.loads(r['summary_bullets']) for r in c.fetchall()]
b = [x for r in rows for x in r]
d = [x for x in b if P.match(x)]
print(f'{len(rows)} articles, {len(b)} bullets, {len(b)/len(rows):.2f} per article')
print(f'mean length {sum(map(len,b))//len(b)} chars')
print(f'dangling-pronoun starts: {len(d)} ({100*len(d)//len(b)}%)')
db.close_pool()
"
```

---

## Tuning

All read from the environment; set in `docker-compose.yml` under the `enrichment` service.

| Variable | Default | Effect |
| --- | --- | --- |
| `BULLET_COUNT` | 3 | Bullets per article. Raising it costs card space, not compute |
| `ENRICH_MAX_ATTEMPTS` | 3 | Retries before a row is abandoned |
| `ENRICH_MIN_AVAILABLE_MB` | 250 | Refuses to load the ONNX session below this |

Module constants in `enrichment.py`, not environment-driven:

| Constant | Default | Effect |
| --- | --- | --- |
| `MIN_SENTENCE_CHARS` | 40 | Filters bylines, credits, "Sign up for our newsletter" |
| `MAX_SENTENCE_CHARS` | 320 | Card display ceiling. Rarely binds at a 150-char mean |
| `MAX_SENTENCES` | 60 | Caps the similarity matrix. 60×60 is trivial; raising it is safe |

Two changes worth considering only if the numbers move:

**Total-length cap.** Currently only per-sentence. Worst case is 3 × 320 = 960 chars, which would overflow a 9:16 card. Not observed in practice (mean total ~450), so unimplemented — but it is a three-line change if the mean climbs.

**Length-normalised scoring.** Dividing the PageRank score by sentence length would push selection toward punchier picks. This changes every existing bullet, so do it before building a card design around current output, or not at all.

---

## What is not measured

Bullet **quality** has no number attached to it. There is no ROUGE score, no human rating, no comparison against an abstractive baseline. Everything above measures shape and failure rate, not whether the right sentences were chosen.

Two ways to close that, both laptop jobs against a `pg_dump` export:

**ROUGE-L against the RSS `description` field.** Every article already carries a publisher-written summary of itself. It is an imperfect reference — descriptions are short and sometimes promotional — but it costs nothing and produces a defensible baseline.

**Hand-rate 30 articles** as good / acceptable / wrong. Twenty minutes, and it is the number worth putting in a writeup.

The stronger comparison — extractive against `distilbart-cnn-12-6` — is the open question ADR-011 records. Extractive was chosen on a hardware constraint, not on quality, and that remains unverified. See `TRAINING.md`; batch it with the labelling session rather than pulling the data twice.
