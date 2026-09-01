# Paperswap — Database & Cloud Guide

_A plain-English explainer of what's deployed, why each choice was made, and how to inspect the database yourself._

---

## ⚠️ First: two different "Oracles"

This trips up almost everyone:

- **Oracle Database** — a heavyweight commercial *database product*. **Not used here.**
- **Oracle Cloud Infrastructure (OCI)** — Oracle's *cloud hosting platform*, a competitor to AWS and Azure. **This is what we use**, purely as a place to rent a virtual computer.

So "the database in the cloud" means **our own PostgreSQL container, running on a rented Oracle Cloud server**. Oracle's database product isn't involved at all.

---

## Part 1 — What is Oracle Cloud?

### What "the cloud" actually means

Instead of buying a physical computer and leaving it on 24/7 at home, you **rent one in a data centre** and control it over the internet. That rented computer is a **virtual machine (VM)** — it behaves like a real computer but is a slice of a much bigger physical server.

### What we rent

- **A VM** — a small Linux computer that's always on
- **Networking** — a virtual network, a public IP, a firewall
- **Storage** — the VM's disk

### Why Oracle Cloud

One reason above all: the **Always Free** tier is genuinely free forever, not a 12-month trial like AWS or GCP. Enough to run a small app at zero cost.

---

## Part 2 — The design choices

### 🐧 Ubuntu 22.04 LTS

Linux distribution built for servers. Chosen because it's the most documented server OS — when you hit a problem, the answer almost certainly exists for Ubuntu. LTS means years of security updates. Docker has first-class support.

### 🖥️ E2.1.Micro VM

The "shape" is the size of the rented computer. E2.1.Micro is ~1/8 of a CPU and **956 MB of RAM**.

Chosen **partly by necessity.** The more powerful Arm-based A1.Flex (also free, 6+ GB RAM) was persistently out of capacity in our region — a very common OCI frustration. E2.1.Micro is the other Always Free shape and is easy to get.

That 956 MB ceiling shaped several later decisions.

### 💾 The 2 GB swap file

Swap is disk space used as overflow when RAM runs out. Slower than real RAM, but it prevents crashes.

It was needed because the original build compiled **Pillow** for server-side image generation, which briefly needs more than 956 MB. Without swap, the build was killed partway through.

Pillow has since been removed — cards are rendered on the phone now, see [ADR-001](ARCHITECTURE.md). The swap file stays as headroom.

### 🐳 Docker

Packages the app with everything it needs — Python version, libraries, system dependencies — into a self-contained container.

Chosen for reproducibility: the container runs identically on a laptop and on the VM, dependencies don't pollute the host, and if the VM dies you rebuild the exact environment from the `Dockerfile`.

### 🐘 PostgreSQL 16

**We started on SQLite and migrated.** That history is worth understanding.

**SQLite** is a database in a single file, with no separate server process. It was the right first choice: zero configuration, tiny memory footprint on a small VM, and the whole database is one portable file.

**Why we outgrew it:** SQLite serialises writers — only one thing can write at a time. Paperswap has a background scheduler fetching news every 12 hours *while* the API serves requests, so the refresh job and request handlers contended for the write lock. SQLite also lacks **window functions**, which the topic-balanced feed needs to interleave categories evenly.

**PostgreSQL 16** runs as a second container alongside the app, with data in a Docker **named volume** (`pgdata`) that survives container rebuilds. It brought concurrent writes, window functions, `ON CONFLICT` upserts for atomic deduplication, and `ON DELETE CASCADE` so deleting an article cleans up its swipe records automatically.

**The cost:** a second container on a 956 MB box, and connection management became something we have to think about. See [ADR-002](ARCHITECTURE.md).

**Security note:** the `db` service has **no port mapping**. It is reachable only from the app container on Docker's internal network, never from the internet.

### 🌐 Ephemeral public IP

"Ephemeral" means tied to the VM's lifetime, versus "Reserved" which persists independently. Ephemeral IPs are free and we don't need the address to outlive the VM.

The address isn't recorded in this repo — look it up in the OCI console.

### ⚡ FastAPI

Modern Python web framework. Fast, lightweight, and generates interactive API documentation automatically at `/docs`.

---

## Part 3 — What's actually in the database

- **Engine:** PostgreSQL 16 (`postgres:16-alpine`)
- **Container:** `news_cards_db`
- **Database:** `newsdb`
- **Storage:** Docker named volume `pgdata`, mounted at `/var/lib/postgresql/data`

### Tables

| Table | Holds |
| --- | --- |
| `articles` | Headline, summary, source, topic, image URL, link, and `article_key`. Plus the enrichment columns written nightly: `full_text`, `summary_bullets`, `embedding`, `enriched_at` |
| `categories` | The topic catalogue, synced from `database.CATEGORIES` on every boot |
| `user_swipes` | One row per swipe — `read` or `pass` — cascading on article delete |
| `swipe_events` | The same swipe, denormalised and **never purged**. The training set |
| `user_sessions` | Session heartbeats, used for engagement metrics |
| `request_logs` | One row per HTTP request, for peak-hour analysis |

### Why swipes are stored twice

This looks like a mistake and is not. A swipe is written to `user_swipes` **and** `swipe_events` in one transaction, because the two have incompatible retention needs.

`user_swipes` is cascade-deleted when its article is purged at seven days. The analytics dashboard depends on that: its panels UNION `user_swipes` with `request_logs`, so both windows must stay equal or a chart silently mixes two periods.

`swipe_events` has no foreign key and is never deleted. It snapshots the title, description, category, and source at swipe time, so a row stays usable long after its article is gone. That duplication is deliberate — it is the only reason old swipes remain trainable.

It stores no embedding on purpose. Vectors are recomputed off-box from the stored text, which means the whole history can be re-embedded when the model changes. See ADR-012.

`dwell_ms` and `flipped` are **nullable and never backfilled**. NULL means the client did not report it, not zero engagement — filter on `dwell_ms IS NOT NULL` before training on it.

### The enrichment columns

Written by `backend/enrichment.py`, a one-shot nightly job that runs outside the API process (ADR-011). It is safe to run by hand at any time:

```bash
docker compose exec news-cards-backend python enrichment.py --dry-run
docker compose exec news-cards-backend python enrichment.py --limit 5
```

| Column | Type | Notes |
| --- | --- | --- |
| `full_text` | `TEXT` | Article body via trafilatura, capped at 20k chars. NULL means extraction was attempted and failed |
| `summary_bullets` | `TEXT` | JSON array of strings. `_decorate_articles` parses it to a real array before it reaches the client, and yields `[]` rather than null so the Android card can iterate unconditionally |
| `embedding` | `BYTEA` | 384 little-endian float32 values. Use `database.pack_embedding` / `unpack_embedding`, never raw `np.tobytes()` — the explicit `<f4` byte order is what lets a VM-written dump load correctly on a laptop |
| `enriched_at` | `TIMESTAMPTZ` | Completion marker **and** resume point. The job selects `WHERE enriched_at IS NULL`, so never set it before every stage for that row has finished |

Neither feed query selects `embedding`, so the bytes never reach JSON serialisation. If you add a query, name your columns explicitly rather than using `SELECT *` — `bytea` is not JSON-serialisable and the failure surfaces as a 500 at response time, not at query time.

### How deduplication works

Every article gets `article_key = md5(title + url)` with a `UNIQUE` constraint. Inserts use `ON CONFLICT (article_key) DO NOTHING`, so a repeat story is dropped **by the database itself** rather than by an application check. Two threads inserting the same article concurrently produce exactly one row.

This is why logs report lines like "9 new / 41 existing."

> Table and column definitions live in `database.py`. Part 4 shows how to inspect the real schema rather than trusting this summary.

---

## Part 4 — Inspecting the database

PostgreSQL runs as a server, so you connect to it with a client — no copying files around.

### Step 1 — Connect to the VM

```powershell
ssh -i <PATH_TO_PRIVATE_KEY> ubuntu@<VM_IP>
```

### Step 2 — Check both containers are up

```bash
cd ~/paperswap/backend
docker compose ps
```

You want `news_cards_backend` **Up** and `news_cards_db` **Up (healthy)**. If the backend is `Restarting`, check `docker compose logs news-cards-backend` before going further.

### Step 3 — Open a psql shell

`psql` ships inside the Postgres image, so nothing needs installing:

```bash
docker exec -it news_cards_db psql -U newsuser -d newsdb
```

Your prompt becomes `newsdb=#`. No password is needed — connecting from inside the container uses Unix-socket peer authentication.

### Step 4 — Explore the real structure

```
\dt              -- list tables
\d articles      -- full column definitions for one table
\d+ articles     -- same, plus indexes and constraints
```

`\d` is the source of truth. Adapt the queries below to whatever it shows.

### Step 5 — Useful queries

```sql
-- How many articles are stored
SELECT COUNT(*) FROM articles;

-- Distribution across topics
SELECT category, COUNT(*) FROM articles GROUP BY category ORDER BY 2 DESC;

-- Ten most recent
SELECT id, category, LEFT(title, 60) AS title FROM articles ORDER BY id DESC LIMIT 10;

-- Swipe behaviour, and read-rate per topic
SELECT a.category,
       COUNT(*) FILTER (WHERE s.action = 'read') AS reads,
       COUNT(*) FILTER (WHERE s.action = 'pass') AS passes,
       ROUND(100.0 * COUNT(*) FILTER (WHERE s.action = 'read') / NULLIF(COUNT(*), 0), 1) AS read_pct
FROM user_swipes s
JOIN articles a ON a.id = s.article_id
GROUP BY a.category
ORDER BY read_pct DESC NULLS LAST;

-- Busiest hours
SELECT hour_of_day, COUNT(*) FROM request_logs GROUP BY hour_of_day ORDER BY 2 DESC LIMIT 5;
```

That read-rate query is the most interesting one in this file — it's the raw material for personalisation, which nothing currently uses.

### Step 6 — Exit

```
\q
```

### Step 7 — Take a backup

Unlike SQLite there's no single file to copy. Use `pg_dump`:

```bash
docker exec news_cards_db pg_dump -U newsuser newsdb > ~/newsdb_backup_$(date +%F).sql
```

Pull it to your machine:

```powershell
scp -i <PATH_TO_PRIVATE_KEY> ubuntu@<VM_IP>:~/newsdb_backup_*.sql C:\path\to\backups\
```

Restore into an empty database with:

```bash
cat backup.sql | docker exec -i news_cards_db psql -U newsuser -d newsdb
```

**There is currently no scheduled backup.** Worth adding a cron `pg_dump`.

---

## Alternative access

**One-off query without an interactive shell:**

```bash
docker exec news_cards_db psql -U newsuser -d newsdb -c "SELECT COUNT(*) FROM articles;"
```

**Graphical client:** [pgAdmin](https://www.pgadmin.org/) or [DBeaver](https://dbeaver.io/). Postgres isn't exposed to the internet, so tunnel over SSH first:

```powershell
ssh -i <KEY> -L 5432:localhost:5432 ubuntu@<VM_IP>
```

Then point the client at `localhost:5432`. This requires temporarily publishing the container port, since `db` has no host mapping by default.

---

## Quick mental model

```
Oracle Cloud (rented data-centre computer)
   └── Ubuntu 22.04 (operating system)
          └── Docker Compose (packaging + orchestration)
                 ├── Container: news_cards_backend
                 │      └── FastAPI app — serves the JSON API on :8000
                 │
                 └── Container: news_cards_db
                        └── PostgreSQL 16
                               └── Volume: pgdata  ← the actual data
                                      ├── articles
                                      ├── categories
                                      ├── user_swipes      (purged at 7d)
                                      ├── swipe_events     (never purged)
                                      ├── user_sessions
                                      └── request_logs
```

- **Oracle** — the landlord renting us the computer
- **Ubuntu** — the computer's operating system
- **Docker** — the shipping containers holding the app and the database
- **PostgreSQL** — the database server
- **`pgdata`** — where the data actually lives, surviving container rebuilds

Only the backend publishes a port. The database is reachable solely from the app container.

> **`swipe_events` has no backup.** Articles re-fetch, embeddings recompute, model weights re-download. A deleted swipe is gone. That table is the one thing in `pgdata` that cannot be reconstructed, and it currently exists in exactly one place — a free-tier volume with an ephemeral IP and Oracle's idle-reclamation policy over it. Take a backup (Part 4, Step 7) before any operation that touches the volume, and see ADR-012 for the planned local archive.

---

## Two things worth knowing before you touch credentials

**`POSTGRES_PASSWORD` only applies when the database is first created.** Once `pgdata` exists, changing that variable does nothing. Rotating requires `ALTER USER ... WITH PASSWORD` inside Postgres first, then updating `.env`.

**`docker compose down` does not delete your data.** It removes containers and networks but leaves named volumes intact. Only `down -v` destroys `pgdata`.

Both are covered in more detail under [Failure modes](ARCHITECTURE.md#failure-modes-weve-hit).
