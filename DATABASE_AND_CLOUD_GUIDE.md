# Paperswap — Database & Cloud Guide

_A plain-English explainer of what we deployed, why we made each choice, and how to poke at the database yourself._

---

## ⚠️ First, an important clarification: two different "Oracles"

This trips up almost everyone, so let's settle it up front:

- **Oracle Database** — a famous, heavyweight commercial *database product*. **We are NOT using this.**
- **Oracle Cloud Infrastructure (OCI)** — Oracle's *cloud hosting platform* (a competitor to Amazon AWS, Microsoft Azure, Google Cloud). **This is what we're using** — purely as a place to rent a virtual computer.

So when we say "the database in the cloud," we mean **our own SQLite database file, running on a rented Oracle Cloud server.** The database software is SQLite; Oracle just provides the machine it sits on. Oracle's own database product isn't involved at all.

---

## Part 1 — What is Oracle Cloud (OCI)?

### What "the cloud" actually means
Instead of buying a physical computer, plugging it in at home, and leaving it on 24/7, you **rent a computer that lives in a company's data center**. You control it over the internet. That rented computer is called a **virtual machine (VM)** or **instance** — it behaves exactly like a real computer, but it's a slice of a much bigger physical server.

### What OCI gives us
OCI is Oracle's version of this service. We used it to rent:
- **A VM** (our server, `141.148.226.251`) — a small Linux computer that's always on.
- **Networking** — a virtual network, a public IP address, and a firewall.
- **Storage** — the VM's disk.

### Why we chose Oracle Cloud
One reason above all: **the "Always Free" tier.** OCI gives away a genuinely usable amount of computing for free, forever — enough to run a small app like Paperswap at zero cost. Most competitors only offer free *trials* that expire. For a personal project, free-forever is ideal.

---

## Part 2 — The Design Choices, Explained

Every piece of the stack was a deliberate choice. Here's what each is and *why*.

### 🐧 Why Ubuntu (the operating system)
**What it is:** Ubuntu is a version ("distribution") of Linux — a free, open-source operating system, the counterpart to Windows or macOS but built for servers.

**Why we chose it:**
- **Most popular server OS** — the vast majority of tutorials, Docker guides, and Stack Overflow answers assume Ubuntu. When you hit a problem, the solution almost always exists for Ubuntu.
- **LTS = Long-Term Support** — we used Ubuntu **22.04 LTS**, which gets security updates for years. Stable and dependable.
- **Docker runs beautifully on it** — first-class support.
- **Free and lightweight** — no licensing cost, runs comfortably on a tiny VM.

**The alternative** would be something like Windows Server (heavier, costs money, overkill) or another Linux flavor (fine, but less universally documented). Ubuntu is the safe, well-trodden path.

### 🖥️ Why the E2.1.Micro VM shape
**What it is:** the "shape" is the size/power of the rented computer. E2.1.Micro is a small x86 machine: ~1/8 of a CPU and **956 MB of RAM**.

**Why we chose it:** honestly, **partly by necessity.** We originally wanted the more powerful **Arm-based A1.Flex** shape (also free, with 6+ GB RAM), but Oracle was **out of capacity** for it in our region — a very common frustration. The E2.1.Micro is the *other* Always Free shape, and it's easy to get. It's small, but enough for one lightweight app.

**The consequence:** its tiny 956 MB RAM led directly to the next choice…

### 💾 Why we added a "swap file"
**What it is:** swap is disk space the system uses as *overflow* when it runs out of RAM. It's slower than real RAM, but it prevents crashes when memory runs short.

**Why we needed it:** building the app's image required compiling/installing **Pillow** (the image-generation library), which briefly needs more than 956 MB of RAM. Without swap, the build would get **killed** partway through (an "out of memory" crash). Adding a **2 GB swap file** gave the system enough breathing room to finish. It's a classic small-server workaround.

### 🐳 Why Docker
**What it is:** Docker packages an app *together with everything it needs to run* (Python version, libraries, system dependencies, fonts) into a single self-contained unit called a **container**.

**Why we chose it:**
- **"Works on my machine" → works everywhere.** The container runs identically on your laptop and on the cloud VM. No "but it worked locally!" surprises.
- **Clean isolation** — the app's dependencies don't pollute the host system.
- **One-command deploy** — `docker compose up` builds and launches the whole thing.
- **Reproducible** — if the VM dies, you rebuild the exact same environment from the `Dockerfile`.

**The alternative** (installing Python, libraries, and dependencies directly on the VM by hand) is fragile, hard to reproduce, and easy to break. Docker makes deployment repeatable.

### 🗄️ Why SQLite (the database itself)
**What it is:** SQLite is a database that lives in **a single file** (`news_database.db`). Unlike big databases, it needs **no separate server process** — the app just opens the file and reads/writes to it.

**Why we chose it:**
- **Zero configuration** — no database server to install, secure, or manage. It just works.
- **Perfect for a small, single-server app** — Paperswap has one backend and modest data (50 cards). SQLite handles this effortlessly.
- **Tiny footprint** — critical on a 956 MB machine. A full database server (like PostgreSQL or MySQL) would eat precious RAM.
- **Portable** — the entire database is one file you can copy, back up, or move.

**The trade-offs (and when you'd outgrow it):**
- SQLite isn't built for **many servers writing at once** or **very high traffic**. If Paperswap grew to multiple backend instances or thousands of simultaneous users, you'd migrate to **PostgreSQL** or **MySQL**.
- For a personal app, none of that applies — SQLite is exactly right.

### 🌐 Why an Ephemeral public IP
**What it is:** the public IP (`141.148.226.251`) is the internet address that lets you reach the VM. "Ephemeral" means it's tied to the VM's lifetime (vs. "Reserved," which persists independently).

**Why we chose it:** ephemeral IPs are **free**, and we don't need the address to survive the VM being deleted. A Reserved IP would cost money when unattached, for no benefit here.

### ⚡ Why FastAPI (the web framework)
**What it is:** FastAPI is a modern Python framework for building web APIs — it handles incoming requests and sends back responses.

**Why it fits:** fast, lightweight, easy to write, and automatically generates API documentation. Great match for serving card data to a future mobile app.

---

## Part 3 — The Database: What We Actually Have

### The essentials
- **Type:** SQLite
- **File name:** `news_database.db` (a single file)
- **Location:** inside the running Docker container (the app reads/writes it there)
- **Purpose:** stores the news articles, prevents duplicates, and logs swipes

### What it stores (conceptually)
Based on the app's design, the database holds roughly:

1. **Articles / cards** — each fetched news story: an ID, title, URL, summary, category (tech/finance), and a link to its generated card image.
2. **A deduplication key** — an **MD5 hash** computed from each article's `title` + `url`. Before inserting a new article, the app checks whether this hash already exists; if it does, it **skips** the duplicate. (This is why your logs showed "9 NEW inserted, 41 ALREADY EXISTED.")
3. **Swipe log** — records of user actions from `POST /api/v1/swipe` (which card, which direction).

> **Note:** the exact table names and columns are defined in `database.py`. The steps in Part 4 show you how to inspect the *real* schema directly, rather than relying on this summary.

---

## Part 4 — How to Interact With the Database, Step by Step

Because the database lives **inside a Docker container**, there's a small extra step to reach it. Below is the **safe, recommended path**: copy the database file out and explore a *copy*, so you can't accidentally corrupt the live one. (Alternatives are at the end.)

### Step 1 — Connect to the VM
From your Windows machine (PowerShell):
```powershell
ssh -i C:\Users\matek_yulq090\Downloads\private_key_paperswap.key ubuntu@141.148.226.251
```
You should land at `ubuntu@paperswap-primary-vnic:~$`.

### Step 2 — Make sure the app container is running
```bash
cd ~/paperswap
docker compose ps
```
You want to see `news_cards_backend` with status **Up**. If not, start it: `docker compose up -d`.

### Step 3 — Find the database file inside the container
The file is somewhere inside the container; this locates it:
```bash
docker exec news_cards_backend find / -name "*.db" 2>/dev/null
```
This prints the full path — likely something like `/app/news_database.db`. **Note the path** it returns; you'll use it below. (If nothing prints, the DB may have a different extension — try `find / -name "news_database*" 2>/dev/null`.)

### Step 4 — Install the SQLite command-line tool on the VM
The VM likely doesn't have it yet:
```bash
sudo apt update && sudo apt install -y sqlite3
```

### Step 5 — Copy the database out of the container
Replace `/app/news_database.db` with the path from Step 3 if different:
```bash
docker cp news_cards_backend:/app/news_database.db ~/news_database_copy.db
```
Now you have a safe copy at `~/news_database_copy.db`.

### Step 6 — Open the copy in the SQLite shell
```bash
sqlite3 ~/news_database_copy.db
```
Your prompt changes to `sqlite>`. You're now inside the database.

### Step 7 — Explore the real structure
These "dot commands" reveal what's actually inside:

```sql
.tables
```
→ lists all table names (e.g. `articles`, `swipes` — whatever they're really called).

```sql
.schema
```
→ shows the full structure (columns and types) of every table. **This is the source of truth** for the next queries.

Make the output readable first:
```sql
.mode column
.headers on
```

### Step 8 — Run some queries
Adapt the table/column names to what `.schema` showed you. Examples (assuming a table named `articles`):

Count how many articles are stored:
```sql
SELECT COUNT(*) FROM articles;
```

See the 10 most recent articles (adjust column names as needed):
```sql
SELECT id, title, category FROM articles LIMIT 10;
```

Count articles by category:
```sql
SELECT category, COUNT(*) FROM articles GROUP BY category;
```

If there's a swipes table, see swipe activity:
```sql
SELECT direction, COUNT(*) FROM swipes GROUP BY direction;
```

> If a query errors with "no such table" or "no such column," it just means the real name differs — check `.tables` / `.schema` and adjust.

### Step 9 — Exit safely
```sql
.quit
```
This returns you to the normal shell. Since you were working on a *copy*, nothing you did affects the live app.

### Step 10 (optional) — Keep it as a backup
That `~/news_database_copy.db` file is a **full snapshot** of your data. To pull it all the way down to your Windows machine as a backup, run this **from Windows** (not the VM):
```powershell
scp -i C:\Users\matek_yulq090\Downloads\private_key_paperswap.key ubuntu@141.148.226.251:~/news_database_copy.db C:\Users\matek_yulq090\Desktop\news_database_backup.db
```

---

## Alternative interaction methods

**Query the LIVE database in place (without copying):**
Use Python inside the container (Python's SQLite support is always present):
```bash
docker exec -it news_cards_backend python -c "import sqlite3; c=sqlite3.connect('/app/news_database.db'); print(c.execute('SELECT COUNT(*) FROM articles').fetchone())"
```
⚠️ Be careful running writes against the live DB while the app is using it.

**Use a graphical tool (nicest for browsing):**
Download the copied `.db` file to your computer (Step 10) and open it in **[DB Browser for SQLite](https://sqlitebrowser.org/)** — a free visual app where you can click through tables and run queries without the command line.

---

## Quick Mental Model

```
Oracle Cloud (rented data-center computer)
   └── Ubuntu Linux (the operating system)
          └── Docker (packaging + isolation)
                 └── Container: news_cards_backend
                        ├── FastAPI app (serves the API)
                        └── SQLite file: news_database.db  ← the database
                               ├── articles (the news cards)
                               └── swipes (user actions)
```

- **Oracle** = the landlord renting us the computer.
- **Ubuntu** = the computer's operating system.
- **Docker** = the shipping container holding the app.
- **SQLite** = the actual database (just a file).

Everything was chosen to be **free, lightweight, and reproducible** — the right priorities for a small personal project on a tiny free server.
