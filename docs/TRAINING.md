# Training

The modelling work that has not been built yet: a hand-labelled holdout, a distilled topic classifier, and a swipe-trained ranker.

Nothing here runs on the VM. That is the organising constraint — see "Where things run" below.

**Status:** none of this exists. `tools/label_articles.py` is written and tested; everything downstream is unbuilt.

---

## Why this is the important part

The deployed pipeline runs three pretrained-or-hand-written components and trains nothing. A reviewer will notice. Enrichment is engineering; this document is the data science.

The thing worth training is a model on **your own swipe data** — labels nobody else has, evaluated against a baseline. That is a stronger artifact than any off-the-shelf checkpoint.

It also closes two open questions carried in `ARCHITECTURE.md`: whether the keyword classifier is any good (ADR-007 records that its accuracy "has never been measured"), and whether the taxonomy is even well-posed.

---

## Where things run

The VM is an OCI E2.1.Micro: 956 MB RAM, ~550 MB free after Postgres and the API. `bart-large-mnli` is 1.6 GB and `import torch` alone costs ~350 MB before any weights load. Neither fits.

So: **train off-box, serve on-box.** The laptop produces weights; the VM applies them.

| | VM | Laptop |
| --- | --- | --- |
| Embeddings | ONNX int8 MiniLM, ~150 MB peak | — |
| Bullets | TextRank, pure numpy | distilbart, for the ROUGE comparison |
| Topics | keyword classifier → distilled weights | bart-large-mnli teacher, BERTopic |
| Ranker | dot product, ~20 floats | trained here |

A logistic regression over a 384-dim embedding is a 384×7 matrix plus 7 biases — about 11 KB as JSON, small enough to commit. Inference is one matrix multiply against a vector `enrichment.py` already computes, so it costs microseconds and no extra RAM.

This is also just how real ML systems are built, and a better thing to describe in an interview than "it all ran on one server."

---

## Phase 0 — Export

```bash
cd ~/paperswap/backend
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -A -c \
  "SELECT row_to_json(t) FROM (SELECT article_key,title,description,source,category,summary_bullets FROM articles) t"' \
  > ~/articles.jsonl
wc -l ~/articles.jsonl
```

`scp` it to the laptop. The automated export endpoint and puller (ADR-012) are still unbuilt; a manual pull is sufficient and blocks nothing.

Articles are purged at 7 days, so the corpus holds ~100 at any moment and grows only if you export repeatedly. `label_articles.py` dedupes on `article_key`, so re-running against a later export adds only what is new.

---

## Phase 1 — Hand labelling

**This is the bottleneck and it gates everything else.** It is also the only phase that costs attention rather than compute.

### Running it

```powershell
cd "C:\Users\matek_yulq090\Desktop\Paperswap"
python tools\label_articles.py --source articles.jsonl --limit 300
```

Per article you see source, headline, and description. Press `1`–`7` to toggle topics, `Enter` to commit, `s` to skip, `q` to quit. Type `12` then `Enter` for two topics.

Writes to `data/labels.csv`, flushed after every article. Quit any time; re-running resumes and skips what is done.

`--stats` prints the distribution and warns about classes under 20 examples, where per-class metrics get too noisy to report honestly.

### Three design decisions that matter more than the interface

**The existing `category` is hidden while you label.** It is written to the CSV so a confusion matrix can be built later, but never shown on screen. Seeing `FINANCE` beside a headline anchors you, and an anchored holdout measures agreement with the classifier you are trying to evaluate.

**You see only what the model sees** — title and description. The classifier embeds those two fields (ADR-011), so labelling from the full article would grade it against evidence it never received.

**Labels are multi-select.** An Apple earnings story is genuinely Tech and Finance. Forcing one label bakes an unresolved taxonomy question into the ground truth. Train seven independent binary classifiers, not a softmax over seven classes.

### Do not use Excel or a SQL client

A spreadsheet shows the existing `category` column, defeating the first point above. It shows URL and source, defeating the second. And Excel mangles CSVs — it reinterprets anything date-like, strips leading zeros, and re-encodes UTF-8 on save. Your headlines contain em-dashes and smart quotes.

### Self-agreement

After a few hundred labels:

```powershell
python tools\label_articles.py --recheck 40
```

Re-serves 40 articles you have already done, without showing your earlier answer, and reports how often you agree with yourself.

**That number is the ceiling on any model's measurable accuracy against this set.** If you disagree with yourself 12% of the time, a model scoring 90% is at the noise floor and tuning it further measures nothing.

Agreement is exact set match, so `[TECH, FINANCE]` vs `[TECH]` counts as full disagreement. Deliberately strict — it gives a conservative ceiling, which is the direction to err.

Reporting your own annotation consistency alongside model accuracy is something most student projects skip entirely.

---

## Phase 2 — Distilled topic classifier

Ground truth from Phase 1 is the *test* set. Training labels come from a teacher.

1. Run `bart-large-mnli` zero-shot over the exported corpus on the laptop. It scores each of the seven topics independently by treating the article as an NLI premise and `"This text is about {label}."` as the hypothesis — one forward pass per label, which is exactly why it cannot run on the VM.
2. Train seven independent binary logistic regressions on `[MiniLM embedding] → [teacher score]`.
3. Export weights as JSON (~11 KB), commit them.
4. Add a `topic_scores JSONB` column; load the weights in `enrichment.py` and apply them to the embedding already being computed.

### The evaluation trap

**Do not evaluate the student against the teacher's labels.** That measures agreement with the teacher, not accuracy. A student perfectly reproducing a mediocre teacher scores 100% — a number that is worthless and looks excellent.

Evaluate against the hand-labelled holdout only, and label it before looking at any model's output on it.

### Deploy beside the old one, not over it

Keep `category` exactly as the keyword classifier sets it. Run both, log where they disagree, and switch the feed only once the holdout says the student is better.

Claiming the new labels are better before measuring would be the ADR-010 failure in a new place — and undetectable, because both produce plausible-looking topics.

### What this produces

A three-way comparison on one test set: keyword classifier, distilled student, teacher. Any outcome is publishable. If the keyword scorer holds up, that is a real engineering result on a 956 MB box. If it does not, you have the confusion matrix showing where.

Two specific things to check, already visible by eye:

- ADR-007 suspects a Tech↔Finance bug: both weighted 1, so a single title match scores 2 against a `MIN_OVERRIDE_MARGIN` of 3, meaning reassignment can never fire between the two largest topics.
- Observed misfires include *"Sports Direct founder attacks Burnham's populist"* → SPORTS, and a lucerne seed irrigation study → FINANCE. Both look like surface-token matching.

---

## Phase 3 — Ranker

The part that uses data nobody else has.

`swipe_events` (ADR-012) stores every swipe permanently with `dwell_ms` and `flipped`. A flip is a stronger interest signal than a fast right-swipe; dwell time is stronger still.

Start with logistic regression over `[cosine similarity to profile, category one-hot, recency, source]` predicting swipe direction. Even that is a real supervised model on labels you generated.

**Filter `dwell_ms IS NOT NULL`.** NULL means the client did not report it, not zero engagement. Treating NULL as 0 manufactures a measurement.

Evaluate by chronological holdout — hold out the last N swipes, replay, report precision@5, MRR, NDCG against two baselines: random and reverse-chronological. **A recommender without a baseline comparison is not a result.**

### The honest framing

`ARCHITECTURE.md` already records that with 2–3 users, any personalisation claim is statistically unsupportable, and that framing this as a single-user cold-start study is more honest. That remains true. The extra signals raise the ceiling on what such a study can show; they do not turn it into a multi-user result.

Note also that `swipe_events` held only 7 rows when 148 swipes were destroyed by the ADR-013 corpus deletion. History accumulates from that point, not before.

---

## Also worth doing in the same session

`SUMMARIZATION.md` records that bullet quality is unmeasured. Two cheap checks, same export, same sitting:

- ROUGE-L between bullets and the RSS `description` field — a free reference summary already in the data.
- Hand-rate 30 articles good / acceptable / wrong.

Batching these with the labelling avoids pulling the data twice.

---

## Sequencing

| # | Step | Gates | Cost |
| --- | --- | --- | --- |
| 0 | Export corpus | everything | minutes |
| 1 | Hand-label 200–300 | 2, 3 | a few hours |
| 1b | `--recheck 40` | interpreting any accuracy | 20 min |
| 2 | Teacher pass | student training | overnight, unattended |
| 3 | Train student, export weights | shadow deploy | minutes |
| 4 | `topic_scores` column, shadow run | switching over | one deploy |
| 5 | Compare on holdout | the decision | an afternoon |
| 6 | Ranker + offline evaluation | — | a day |

Step 1 is unglamorous and it is what makes the rest count. Steps 2 onward are mostly waiting.
