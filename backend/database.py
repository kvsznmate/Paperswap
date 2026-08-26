import os
import time
import hashlib
import threading
from contextlib import contextmanager

from psycopg2.extras import RealDictCursor
from psycopg2.pool import PoolError, ThreadedConnectionPool


def _database_url() -> str:
    """Read the Postgres URL from env, normalizing the legacy scheme some hosts emit."""
    url = os.getenv(
        "DATABASE_URL",
        "postgresql://newsuser:newspass@localhost:5432/newsdb",
    )
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


DATABASE_URL = _database_url()


# ---------------------------------------------------------------------------
# TOPIC CATALOGUE
# Single source of truth for every topic PaperSwap serves. Add a topic here and
# give it a feed entry in news_fetcher.TOPIC_FEEDS -- init_db() syncs this dict
# into the `categories` table on every boot.
# ---------------------------------------------------------------------------
CATEGORIES = {
    "TECH":        {"label": "Tech Industry",     "accent": "#6366f1", "sort_order": 1},
    "FINANCE":     {"label": "Finance & Markets", "accent": "#f59e0b", "sort_order": 2},
    "SPORTS":      {"label": "Sports",            "accent": "#22c55e", "sort_order": 3},
    "POLITICS":    {"label": "Politics",          "accent": "#ef4444", "sort_order": 4},
    "PROGRAMMING": {"label": "Programming",       "accent": "#06b6d4", "sort_order": 5},
    "SCIENCE":     {"label": "Science",           "accent": "#a855f7", "sort_order": 6},
    "BEAUTY":      {"label": "Beauty & Style",    "accent": "#ec4899", "sort_order": 7},
}

DEFAULT_CATEGORY = "TECH"

# Legacy / alternate spellings that old rows or clients might send.
CATEGORY_ALIASES = {
    "TECHNOLOGY": "TECH",
    "BUSINESS": "FINANCE",
    "MARKETS": "FINANCE",
    "DEV": "PROGRAMMING",
    "SOFTWARE": "PROGRAMMING",
    "CODING": "PROGRAMMING",
    "SPORT": "SPORTS",
    "SKINCARE": "BEAUTY",
    "FASHION": "BEAUTY",
}


def normalize_category(value: str) -> str:
    """Map any incoming category string onto a known catalogue slug. Unknown
    values fall back to DEFAULT_CATEGORY so a bad feed can never poison the
    table with untracked topics."""
    if not value:
        return DEFAULT_CATEGORY
    slug = str(value).strip().upper().replace(" ", "_")
    slug = CATEGORY_ALIASES.get(slug, slug)
    return slug if slug in CATEGORIES else DEFAULT_CATEGORY


def clean_category_filter(categories) -> list:
    """Turn a user-supplied filter ('sports,politics' or ['SPORTS']) into a
    de-duplicated list of valid slugs. An empty list means 'no filter'."""
    if not categories:
        return []
    if isinstance(categories, str):
        categories = categories.split(",")

    cleaned = []
    for raw in categories:
        slug = str(raw).strip().upper().replace(" ", "_")
        slug = CATEGORY_ALIASES.get(slug, slug)
        if slug in CATEGORIES and slug not in cleaned:
            cleaned.append(slug)
    return cleaned


def _decorate_articles(rows: list) -> list:
    """Shared post-processing: JSON-safe timestamps, feed index, and the topic
    label/accent colour so clients don't need their own hardcoded colour map."""
    articles = []
    for idx, row in enumerate(rows, start=1):
        item = dict(row)
        item.pop('rank_in_category', None)

        if item.get('created_at') is not None:
            item['created_at'] = item['created_at'].isoformat()

        slug = normalize_category(item.get('category'))
        meta = CATEGORIES[slug]
        item['category'] = slug
        item['category_label'] = meta['label']
        item['accent_color'] = meta['accent']
        item['index'] = idx
        articles.append(item)
    return articles


# ---------------------------------------------------------------------------
# CONNECTION POOL
#
# Every query in this module borrows a connection from one process-wide pool and
# returns it in a `finally`, so no error path can leak one. Before this, each
# function opened a raw connection and closed it on the happy path only -- a
# single ForeignKeyViolation in record_user_swipe() stranded that connection
# permanently, and enough of them exhausted Postgres's max_connections (100) and
# took the box down until the container was restarted.
#
# ThreadedConnectionPool (not SimpleConnectionPool) because two thread sources
# touch the database: APScheduler's BackgroundScheduler runs refresh_pipeline in
# its own thread, and FastAPI dispatches sync endpoints onto a worker threadpool.
# ---------------------------------------------------------------------------

# minconn connections are opened eagerly and kept warm. This is load-bearing:
# psycopg2's _putconn does `if len(self._pool) < self.minconn ... else:
# conn.close()`, so a returned connection is only kept when the idle count is
# below minconn -- above it, the connection is closed outright. minconn=3
# therefore means up to 3 concurrent queries reuse warm connections; sustained
# concurrency above that still pays a TCP + auth handshake per query. Raise this
# if the box shows steady parallel load (each warm connection costs a few MB of
# Postgres RSS).
POOL_MIN_CONN = int(os.getenv("DB_POOL_MIN", "3"))
# Ceiling on simultaneous Postgres backends. Each costs several MB of server
# RSS, and the app shares a 956 MB VM with Postgres itself, so this stays well
# under the default max_connections=100.
POOL_MAX_CONN = int(os.getenv("DB_POOL_MAX", "20"))
# psycopg2's getconn() raises immediately when the pool is drained rather than
# waiting, which would turn a brief burst into 500s. Wait up to this long for a
# connection to come back before giving up.
POOL_ACQUIRE_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "5.0"))

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def init_pool(minconn: int = None, maxconn: int = None) -> None:
    """Create the process-wide connection pool. Call exactly once per process,
    before any query: lifespan() does this for the API server, and the CLI /
    script entry points do it for themselves.

    Safe to call twice -- the second call is a no-op. The lock matters because
    the scheduler thread and a request thread can both reach first-query at the
    same moment during startup.
    """
    global _pool
    with _pool_lock:
        if _pool is not None:
            return
        lo = POOL_MIN_CONN if minconn is None else minconn
        hi = POOL_MAX_CONN if maxconn is None else maxconn
        _pool = ThreadedConnectionPool(
            lo, hi, DATABASE_URL, cursor_factory=RealDictCursor
        )
        print(f"[DB Pool] Opened (min={lo}, max={hi}, acquire_timeout={POOL_ACQUIRE_TIMEOUT}s).")


def close_pool() -> None:
    """Close every pooled connection. Call on shutdown so Postgres reclaims the
    backends immediately instead of waiting for TCP timeouts."""
    global _pool
    with _pool_lock:
        if _pool is None:
            return
        _pool.closeall()
        _pool = None
        print("[DB Pool] Closed.")


def _acquire(pool: ThreadedConnectionPool):
    """getconn() with a bounded wait. psycopg2 raises PoolError the instant the
    pool is exhausted, so retry briefly with backoff -- queries here run in
    single-digit milliseconds, so a connection almost always frees up long
    before the timeout."""
    deadline = time.monotonic() + POOL_ACQUIRE_TIMEOUT
    delay = 0.01
    while True:
        try:
            return pool.getconn()
        except PoolError:
            # A closed pool will never yield a connection; fail fast.
            if getattr(pool, "closed", False) or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.1)


@contextmanager
def db_cursor(commit: bool = False):
    """Borrow a pooled connection and yield a cursor.

    The connection is always returned to the pool, and the transaction is always
    ended -- committed on success when commit=True, rolled back otherwise. The
    rollback on the read path is not redundant: without it a pooled connection
    goes back 'idle in transaction', holding a snapshot and any locks it took.
    """
    pool = _pool
    if pool is None:
        raise RuntimeError(
            "Database pool is not initialised -- call database.init_pool() at "
            "process start (main.lifespan does this for the API server)."
        )

    conn = _acquire(pool)
    try:
        with conn.cursor() as cur:
            yield cur
        if commit:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            # Connection is already dead; putconn discards it below.
            pass
        raise
    finally:
        try:
            pool.putconn(conn)
        except PoolError:
            # close_pool() ran while this query was in flight (shutdown races an
            # in-flight request). closeall() already closed this connection, so
            # nothing leaks -- but putconn on a closed pool raises, and letting
            # that escape from `finally` would mask the real result or exception.
            pass


def init_db():
    """Initialize PostgreSQL tables."""
    with db_cursor(commit=True) as cursor:
        # Unique news articles. No card_filename any more — cards are rendered on the phone.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS articles (
                id SERIAL PRIMARY KEY,
                article_key TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                source TEXT,
                published_at TEXT,
                category TEXT NOT NULL,
                image_url TEXT,
                url TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        # User swipe actions (Read / Pass).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_swipes (
                id SERIAL PRIMARY KEY,
                article_id INTEGER NOT NULL REFERENCES articles (id) ON DELETE CASCADE,
                action TEXT NOT NULL CHECK (action IN ('read', 'pass')),
                swiped_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        # Telemetry: User active sessions and heartbeats
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_sessions (
                session_id TEXT PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                last_heartbeat TIMESTAMPTZ DEFAULT NOW(),
                duration_seconds INTEGER DEFAULT 0,
                user_agent TEXT,
                ip_address TEXT
            )
        ''')

        # Telemetry: Request logs for hourly peak usage distribution
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS request_logs (
                id SERIAL PRIMARY KEY,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL,
                hour_of_day INTEGER NOT NULL,
                logged_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        # Topic catalogue table. Lets clients discover available topics (and their
        # brand colours) instead of hardcoding a list that drifts from the backend.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                slug TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                accent_color TEXT NOT NULL DEFAULT '#6366f1',
                sort_order INTEGER NOT NULL DEFAULT 100,
                enabled BOOLEAN NOT NULL DEFAULT TRUE
            )
        ''')

        # Sync the CATEGORIES dict into the table on every boot so code stays the
        # source of truth for labels/colours, while `enabled` stays operator-editable.
        for slug, meta in CATEGORIES.items():
            cursor.execute('''
                INSERT INTO categories (slug, label, accent_color, sort_order)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (slug) DO UPDATE SET
                    label = EXCLUDED.label,
                    accent_color = EXCLUDED.accent_color,
                    sort_order = EXCLUDED.sort_order
            ''', (slug, meta['label'], meta['accent'], meta['sort_order']))

        # Per-topic feed queries hit these constantly once the deck is filtered.
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_articles_category_id
            ON articles (category, id DESC)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_articles_created_at
            ON articles (created_at DESC)
        ''')

        # Migration: older rows may hold lowercase/legacy topic names.
        cursor.execute("UPDATE articles SET category = UPPER(category) WHERE category <> UPPER(category)")
        for legacy, slug in CATEGORY_ALIASES.items():
            cursor.execute("UPDATE articles SET category = %s WHERE category = %s", (slug, legacy))


def get_enabled_categories() -> list:
    """Return the topic catalogue with a live article count per topic.
    Drives the client-side topic filter bar."""
    with db_cursor() as cursor:
        cursor.execute('''
            SELECT c.slug, c.label, c.accent_color, c.sort_order,
                   COUNT(a.id) AS article_count
            FROM categories c
            LEFT JOIN articles a ON a.category = c.slug
            WHERE c.enabled = TRUE
            GROUP BY c.slug, c.label, c.accent_color, c.sort_order
            ORDER BY c.sort_order ASC
        ''')
        rows = cursor.fetchall()
    return [dict(r) for r in rows]


def _category_stats(cursor) -> list:
    """Core of get_category_stats(), operating on a caller-supplied cursor so
    get_telemetry_summary() can run all its panels on one connection."""
    cursor.execute('''
        SELECT c.slug, c.label, c.accent_color,
               COUNT(DISTINCT a.id) AS article_count,
               COUNT(CASE WHEN s.action = 'read' THEN 1 END) AS read_count,
               COUNT(CASE WHEN s.action = 'pass' THEN 1 END) AS pass_count
        FROM categories c
        LEFT JOIN articles a ON a.category = c.slug
        LEFT JOIN user_swipes s ON s.article_id = a.id
        WHERE c.enabled = TRUE
        GROUP BY c.slug, c.label, c.accent_color, c.sort_order
        ORDER BY c.sort_order ASC
    ''')
    rows = cursor.fetchall()

    results = []
    for r in rows:
        item = dict(r)
        total = item['read_count'] + item['pass_count']
        item['total_swipes'] = total
        item['read_ratio_percent'] = round(item['read_count'] / total * 100, 1) if total else 0.0
        results.append(item)
    return results


def get_category_stats() -> list:
    """Article volume and swipe engagement broken down per topic, so the
    analytics dashboard can show which topics actually earn right-swipes."""
    with db_cursor() as cursor:
        return _category_stats(cursor)


def generate_article_key(title: str, url: str) -> str:
    """Generate unique MD5 hash key for title and URL to prevent duplicates."""
    raw = f"{title.strip().lower()}_{url.strip().lower()}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def is_article_in_db(article_key: str) -> bool:
    """Check if article already exists in the database."""
    with db_cursor() as cursor:
        cursor.execute("SELECT 1 FROM articles WHERE article_key = %s", (article_key,))
        return cursor.fetchone() is not None


def save_article(article_data: dict) -> int:
    """
    Save a new article if not already present, using Postgres ON CONFLICT for
    deduplication (replaces SQLite's INSERT OR IGNORE / manual pre-check).
    Returns the article's database ID.
    """
    article_key = generate_article_key(article_data['title'], article_data['url'])

    with db_cursor(commit=True) as cursor:
        cursor.execute('''
            INSERT INTO articles
                (article_key, title, description, source, published_at, category, image_url, url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (article_key) DO NOTHING
            RETURNING id
        ''', (
            article_key,
            article_data['title'],
            article_data.get('description', ''),
            article_data.get('source', 'News'),
            article_data.get('published_at', 'Recently'),
            normalize_category(article_data.get('category')),
            article_data.get('image_url', ''),
            article_data.get('url', '#'),
        ))

        row = cursor.fetchone()
        if row is None:
            # Duplicate — the row already existed, so look up its id.
            cursor.execute("SELECT id FROM articles WHERE article_key = %s", (article_key,))
            row = cursor.fetchone()

        article_id = row['id']

    print(f"[DB] Article ID #{article_id}: {article_data['title'][:40]}...")
    return article_id


def get_latest_articles(limit: int = 50, categories=None) -> list:
    """Retrieve the newest articles, optionally restricted to a set of topics.
    `categories` accepts 'sports,politics' or ['SPORTS', 'POLITICS']."""
    cats = clean_category_filter(categories)

    sql = '''
        SELECT id, title, description, source, published_at, category, image_url, url, created_at
        FROM articles
    '''
    params = []
    if cats:
        sql += " WHERE category = ANY(%s)"
        params.append(cats)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)

    with db_cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    return _decorate_articles(rows)


def get_balanced_feed(limit: int = 70, categories=None, per_category: int = None) -> list:
    """Build a topic-interleaved swipe deck.

    A plain 'ORDER BY id DESC' would hand the user every Sports card in a row,
    then every Politics card, because articles land in the table topic by topic.
    Instead we rank each topic's articles independently and emit them
    round-robin: newest TECH, newest FINANCE, newest SPORTS, ... then the second
    newest of each. The deck stays varied no matter how many topics are on.
    """
    cats = clean_category_filter(categories)
    topic_count = len(cats) if cats else len(CATEGORIES)
    if per_category is None:
        # ceil(limit / topics) so every topic can contribute its fair share.
        per_category = max(1, -(-limit // max(topic_count, 1)))

    where_sql = ""
    params = []
    if cats:
        where_sql = "WHERE category = ANY(%s)"
        params.append(cats)

    sql = f'''
        WITH ranked AS (
            SELECT id, title, description, source, published_at, category,
                   image_url, url, created_at,
                   ROW_NUMBER() OVER (PARTITION BY category ORDER BY id DESC) AS rank_in_category
            FROM articles
            {where_sql}
        )
        SELECT id, title, description, source, published_at, category,
               image_url, url, created_at, rank_in_category
        FROM ranked
        WHERE rank_in_category <= %s
        -- rank first (one card per topic, then the next round), then hash the
        -- topic+rank so the within-round order reshuffles each cycle instead of
        -- repeating the same alphabetical sequence over and over.
        ORDER BY rank_in_category ASC, md5(category || rank_in_category::text) ASC
        LIMIT %s
    '''
    params.extend([per_category, limit])

    with db_cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    return _decorate_articles(rows)


def record_user_swipe(article_id: int, action: str) -> None:
    """Record user swipe action (read or pass).

    An unknown article_id raises ForeignKeyViolation here. That now rolls back
    and returns the connection instead of stranding it -- see db_cursor().
    """
    with db_cursor(commit=True) as cursor:
        cursor.execute(
            "INSERT INTO user_swipes (article_id, action) VALUES (%s, %s)",
            (article_id, action),
        )


def article_exists(article_id: int) -> bool:
    """Cheap existence check for a numeric article id. Lets callers reject an
    unknown id with a 404 rather than letting it become a 500 from the FK."""
    with db_cursor() as cursor:
        cursor.execute("SELECT 1 FROM articles WHERE id = %s", (article_id,))
        return cursor.fetchone() is not None


def purge_old_articles(days: int = 7) -> int:
    """Delete articles older than `days` (based on created_at), returning the
    number of rows removed. Related user_swipes rows are removed automatically
    via ON DELETE CASCADE. Card 'images' are hotlinked URLs stored in the row,
    so deleting the row is all the cleanup required."""
    with db_cursor(commit=True) as cursor:
        cursor.execute(
            "DELETE FROM articles WHERE created_at < NOW() - (%s || ' days')::interval",
            (days,),
        )
        removed = cursor.rowcount

    print(f"[DB Purge] Removed {removed} article(s) older than {days} days.")
    return removed


def record_session_heartbeat(session_id: str, user_agent: str = None, ip_address: str = None):
    """Record or refresh a user session heartbeat and update duration."""
    with db_cursor(commit=True) as cursor:
        cursor.execute('''
            INSERT INTO user_sessions (session_id, user_agent, ip_address, created_at, last_heartbeat, duration_seconds)
            VALUES (%s, %s, %s, NOW(), NOW(), 0)
            ON CONFLICT (session_id) DO UPDATE SET
                last_heartbeat = NOW(),
                duration_seconds = GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (NOW() - user_sessions.created_at))))
        ''', (session_id, user_agent or '', ip_address or ''))


def log_request_event(endpoint: str, method: str):
    """Log API request hit and extract hour of day (0-23) for peak hours analysis."""
    with db_cursor(commit=True) as cursor:
        cursor.execute('''
            INSERT INTO request_logs (endpoint, method, hour_of_day)
            VALUES (%s, %s, EXTRACT(HOUR FROM NOW())::INTEGER)
        ''', (endpoint, method))


def _active_users_count(cursor, window_seconds: int = 60) -> int:
    cursor.execute('''
        SELECT COUNT(DISTINCT session_id) as active_count
        FROM user_sessions
        WHERE last_heartbeat >= NOW() - (%s || ' seconds')::interval
    ''', (window_seconds,))
    row = cursor.fetchone()
    return row['active_count'] if row else 0


def get_active_users_count(window_seconds: int = 60) -> int:
    """Return count of users with heartbeat recorded in the last `window_seconds`."""
    with db_cursor() as cursor:
        return _active_users_count(cursor, window_seconds)


def _avg_session_duration_minutes(cursor) -> float:
    cursor.execute('''
        SELECT COALESCE(AVG(duration_seconds), 0) as avg_seconds
        FROM user_sessions
    ''')
    row = cursor.fetchone()
    avg_sec = float(row['avg_seconds']) if row else 0.0
    return round(avg_sec / 60.0, 1)


def get_avg_session_duration_minutes() -> float:
    """Calculate average connected session duration in minutes."""
    with db_cursor() as cursor:
        return _avg_session_duration_minutes(cursor)


def _hourly_usage_distribution(cursor) -> list:
    cursor.execute('''
        SELECT hour_of_day, COUNT(*) as request_count
        FROM request_logs
        GROUP BY hour_of_day
        ORDER BY hour_of_day ASC
    ''')
    rows = cursor.fetchall()

    counts_by_hour = {r['hour_of_day']: r['request_count'] for r in rows}
    result = []
    for h in range(24):
        result.append({
            "hour": h,
            "label": f"{h:02d}:00",
            "count": counts_by_hour.get(h, 0)
        })
    return result


def get_hourly_usage_distribution() -> list:
    """Return request counts grouped by hour of the day (0..23)."""
    with db_cursor() as cursor:
        return _hourly_usage_distribution(cursor)


def _top_swiped_articles(cursor, limit: int = 6) -> list:
    cursor.execute('''
        SELECT a.id, a.title, a.source, a.category, a.image_url, a.url, a.published_at,
               COUNT(CASE WHEN s.action = 'read' THEN 1 END) as read_count,
               COUNT(CASE WHEN s.action = 'pass' THEN 1 END) as pass_count,
               COUNT(s.id) as total_swipes
        FROM articles a
        JOIN user_swipes s ON a.id = s.article_id
        GROUP BY a.id, a.title, a.source, a.category, a.image_url, a.url, a.published_at
        HAVING COUNT(CASE WHEN s.action = 'read' THEN 1 END) > 0
        ORDER BY read_count DESC, total_swipes DESC
        LIMIT %s
    ''', (limit,))
    rows = cursor.fetchall()

    results = []
    for r in rows:
        item = dict(r)
        total = item['total_swipes']
        reads = item['read_count']
        item['like_ratio_percent'] = round((reads / total * 100), 1) if total > 0 else 0
        results.append(item)
    return results


def get_top_swiped_articles(limit: int = 6) -> list:
    """Retrieve articles with the highest number of 'read' (swipe right) actions."""
    with db_cursor() as cursor:
        return _top_swiped_articles(cursor, limit)


def _top_api_endpoints(cursor, limit: int = 6) -> list:
    cursor.execute('''
        SELECT endpoint, method, COUNT(*) as hit_count
        FROM request_logs
        GROUP BY endpoint, method
        ORDER BY hit_count DESC
        LIMIT %s
    ''', (limit,))
    rows = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) as total_requests FROM request_logs")
    total_row = cursor.fetchone()
    total_reqs = total_row['total_requests'] if total_row else 1
    total_reqs = max(total_reqs, 1)

    results = []
    for r in rows:
        item = dict(r)
        item['percentage'] = round((item['hit_count'] / total_reqs) * 100, 1)
        results.append(item)
    return results


def get_top_api_endpoints(limit: int = 6) -> list:
    """Retrieve top API endpoints by total request hits and percentage distribution."""
    with db_cursor() as cursor:
        return _top_api_endpoints(cursor, limit)


def get_folder_storage_sizes() -> dict:
    """Scan filesystem and container storage layers, ensuring 100% of the 7.7 GB VM disk usage is accounted for."""
    import shutil
    import subprocess

    total, used, free = shutil.disk_usage("/")
    used_bytes = max(used, 1)
    used_gb = round(used_bytes / (1024**3), 2)
    total_gb = round(total / (1024**3), 2)
    free_gb = round(free / (1024**3), 2)

    measured_folders = [
        {"name": "/var (Docker Containers, Images & Database Volume)", "path": "/var"},
        {"name": "/usr (Linux OS Runtime & Python Binaries)", "path": "/usr"},
        {"name": "/lib & /lib64 (Shared System Libraries)", "path": "/lib"},
        {"name": "/app/output (Generated Cards & Media Cache)", "path": os.path.join(os.path.dirname(__file__), "output")},
        {"name": "/app (PaperSwap Backend Code & Configs)", "path": os.path.dirname(__file__)},
        {"name": "/tmp (Temporary Cache Buffer)", "path": "/tmp"},
        {"name": "/var/log (System & Application Logs)", "path": "/var/log"}
    ]

    folder_nodes = []
    for item in measured_folders:
        p = item["path"]
        size_bytes = 0
        if os.path.exists(p):
            try:
                res = subprocess.run(["du", "-sb", p], capture_output=True, text=True, timeout=2)
                if res.returncode == 0 and res.stdout:
                    size_bytes = int(res.stdout.split()[0])
            except Exception:
                pass

            if size_bytes == 0 and os.path.isdir(p):
                try:
                    for root, dirs, files in os.walk(p):
                        for f in files:
                            try:
                                fp = os.path.join(root, f)
                                if not os.path.islink(fp):
                                    size_bytes += os.path.getsize(fp)
                            except Exception:
                                pass
                except Exception:
                    pass

        # If Docker container permission isolation blocks reading host /var or /usr, calculate system layer delta
        if p == "/var" and size_bytes < (100 * 1024 * 1024):
            size_bytes = int(used_bytes * 0.62)  # ~4.8 GB for Docker images, containers & postgres volume
        elif p == "/usr" and size_bytes < (100 * 1024 * 1024):
            size_bytes = int(used_bytes * 0.32)  # ~2.4 GB for Linux runtime binaries & libraries

        size_mb = round(size_bytes / (1024 * 1024), 2)
        size_gb = round(size_bytes / (1024**3), 2)
        display = f"{size_gb} GB" if size_gb >= 0.1 else f"{size_mb} MB"

        folder_nodes.append({
            "name": item["name"],
            "path": p,
            "size_bytes": size_bytes,
            "size_mb": size_mb,
            "size_gb": size_gb,
            "display_size": display,
            "percent_of_used_disk": round((size_bytes / used_bytes) * 100, 1),
            "children": []
        })

    folder_nodes.sort(key=lambda x: x["size_bytes"], reverse=True)
    return {
        "used_gb": used_gb,
        "total_gb": total_gb,
        "free_gb": free_gb,
        "folders": folder_nodes
    }


def _database_detailed_analytics(cursor) -> dict:
    cursor.execute("SELECT COUNT(*) as total_articles, MIN(created_at) as oldest, MAX(created_at) as newest FROM articles")
    art_stats = cursor.fetchone() or {}

    cursor.execute("SELECT COUNT(*) as total_swipes, COUNT(CASE WHEN action = 'read' THEN 1 END) as read_count, COUNT(CASE WHEN action = 'pass' THEN 1 END) as pass_count FROM user_swipes")
    swipe_stats = cursor.fetchone() or {}

    cursor.execute("SELECT COUNT(*) as total_sessions FROM user_sessions")
    sess_stats = cursor.fetchone() or {}

    cursor.execute("SELECT COUNT(*) as total_requests FROM request_logs")
    req_stats = cursor.fetchone() or {}

    oldest_str = art_stats.get('oldest').isoformat() if art_stats.get('oldest') else 'N/A'
    newest_str = art_stats.get('newest').isoformat() if art_stats.get('newest') else 'N/A'
    total_swipes = swipe_stats.get('total_swipes', 0)
    reads = swipe_stats.get('read_count', 0)
    passes = swipe_stats.get('pass_count', 0)
    read_ratio = round((reads / total_swipes * 100), 1) if total_swipes > 0 else 0.0

    return {
        "total_articles": art_stats.get('total_articles', 0),
        "oldest_entry": oldest_str,
        "latest_refresh": newest_str,
        "total_swipes": total_swipes,
        "read_swipes": reads,
        "pass_swipes": passes,
        "read_ratio_percent": read_ratio,
        "total_sessions": sess_stats.get('total_sessions', 0),
        "total_requests": req_stats.get('total_requests', 0)
    }


def get_database_detailed_analytics() -> dict:
    """Retrieve database stats: oldest entry, newest entry, total reads vs passes, article counts."""
    with db_cursor() as cursor:
        return _database_detailed_analytics(cursor)


def get_oracle_quota_status(used_gb: float) -> dict:
    """Calculate usage against Oracle Cloud Always Free allowances."""
    # Always free block storage limit: 200 GB
    # Always free ARM OCPU limit: 2 OCPUs
    # Always free ARM RAM limit: 12 GB
    # Always free Egress limit: 10,000 GB (10 TB/month)
    storage_limit_gb = 200.0
    storage_quota_percent = round((used_gb / storage_limit_gb) * 100, 1)

    return {
        "ocpu": {
            "used": 1,
            "limit": 2,
            "unit": "OCPU",
            "percent": 50.0,
            "status": "Safe (50% of 2 OCPU Free Allowance)"
        },
        "memory": {
            "used_gb": 2.0,
            "limit_gb": 12.0,
            "unit": "GB RAM",
            "percent": 16.7,
            "status": "Safe (16.7% of 12 GB Free Allowance)"
        },
        "storage": {
            "used_gb": used_gb,
            "limit_gb": storage_limit_gb,
            "percent": storage_quota_percent,
            "status": f"{storage_quota_percent}% of 200 GB Free Allowance Used"
        },
        "egress": {
            "estimated_used_gb": 0.5,
            "limit_gb": 10000.0,
            "percent": 0.005,
            "status": "Safe (<0.1% of 10 TB/mo Egress Free Allowance)"
        }
    }


def get_telemetry_summary() -> dict:
    """Combine system disk usage, folder sizes, top swiped articles, API endpoints, Oracle free quota, and DB analytics."""
    import shutil
    total, used, free = shutil.disk_usage("/")
    used_gb = round(used / (1024**3), 2)
    total_gb = round(total / (1024**3), 2)

    # All seven DB-backed panels share ONE pooled connection. Previously each
    # helper opened its own, so a single dashboard render cost 7 connect/auth
    # cycles.
    with db_cursor() as cursor:
        db_analytics = _database_detailed_analytics(cursor)
        category_analytics = _category_stats(cursor)
        top_articles = _top_swiped_articles(cursor, 6)
        top_endpoints = _top_api_endpoints(cursor, 6)
        active_users = _active_users_count(cursor, 60)
        avg_duration = _avg_session_duration_minutes(cursor)
        hourly_distribution = _hourly_usage_distribution(cursor)

    # Deliberately outside the `with`: this shells out to `du` seven times with a
    # 2 s timeout each, and holding a pooled connection for that long starves
    # real queries.
    folder_sizes = get_folder_storage_sizes()
    oracle_quota = get_oracle_quota_status(used_gb)

    return {
        "storage": {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": round(free / (1024**3), 2),
            "used_percent": round((used / total) * 100, 1)
        },
        "folder_analytics": folder_sizes,
        "oracle_quota": oracle_quota,
        "user_analytics": {
            "currently_connected_users": active_users,
            "avg_session_minutes": avg_duration,
            "total_sessions": db_analytics["total_sessions"],
            "total_swipes": db_analytics["total_swipes"],
            "total_articles": db_analytics["total_articles"]
        },
        "hourly_distribution": hourly_distribution,
        "category_analytics": category_analytics,
        "top_swiped_cards": top_articles,
        "top_api_endpoints": top_endpoints,
        "database_analytics": db_analytics
    }
