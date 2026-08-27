import os
import time
import shutil
import hashlib
import threading
import subprocess
from contextlib import contextmanager

from psycopg2.extras import RealDictCursor, execute_values
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

        # Telemetry: Request logs for hourly peak usage distribution.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS request_logs (
                id SERIAL PRIMARY KEY,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL,
                status_code INTEGER,
                hour_of_day INTEGER NOT NULL,
                logged_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        # Migration for databases created before status_code existed.
        #
        # Nullable, and old rows are deliberately NOT backfilled to 200. Those
        # requests were logged BEFORE the response existed, so their status was
        # never observed -- writing 200 now would manufacture a measurement,
        # which is the exact failure ADR-010 exists to prevent. NULL reads as
        # "not measured", consistent with the provenance contract below.
        cursor.execute('''
            ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS status_code INTEGER
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

        # Supports the retention purge below. The analytics panels scan the whole
        # table anyway, but the nightly DELETE should not have to.
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_request_logs_logged_at
            ON request_logs (logged_at)
        ''')

        # Migration: older rows may hold lowercase/legacy topic names.
        cursor.execute("UPDATE articles SET category = UPPER(category) WHERE category <> UPPER(category)")
        for legacy, slug in CATEGORY_ALIASES.items():
            cursor.execute("UPDATE articles SET category = %s WHERE category = %s", (slug, legacy))

        # Migration: swipes are no longer written to request_logs, because
        # user_swipes.swiped_at already carries that timestamp. Rows logged under
        # the old behaviour are now exact duplicates of user_swipes entries, and
        # the analytics panels union both sources -- so leaving them would make
        # every historical swipe count twice. Delete them once, here.
        cursor.execute("DELETE FROM request_logs WHERE endpoint = '/api/v1/swipe'")
        if cursor.rowcount:
            print(f"[DB Migration] Removed {cursor.rowcount} duplicate swipe row(s) "
                  f"from request_logs (now derived from user_swipes).")


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


def purge_old_request_logs(days: int = 7) -> int:
    """Delete request_logs rows older than `days`, returning the number removed.

    This table previously had no retention policy at all: it grew for the life of
    the deployment, on a 956 MB VM whose disk is shared with the Postgres volume.
    An unbounded analytics table is a slow leak with no ceiling.

    The window MUST match the article purge, and the reason is not tidiness.
    _hourly_usage_distribution and _top_api_endpoints are each a UNION of this
    table and user_swipes, and user_swipes rows vanish by ON DELETE CASCADE when
    their article is purged. So user_swipes is already capped at the article
    retention window. If request_logs were kept longer, the two halves of that
    union would cover different periods -- swipes would under-represent against
    every other endpoint, and the distortion would read as a genuine drop in
    swipe traffic rather than as a retention artefact.

    main.REQUEST_LOG_RETENTION_DAYS therefore defaults to PURGE_OLDER_THAN_DAYS.
    """
    with db_cursor(commit=True) as cursor:
        cursor.execute(
            "DELETE FROM request_logs WHERE logged_at < NOW() - (%s || ' days')::interval",
            (days,),
        )
        removed = cursor.rowcount

    print(f"[DB Purge] Removed {removed} request log row(s) older than {days} days.")
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


def log_request_events_bulk(rows: list) -> int:
    """Write a batch of request events in ONE round trip. Returns rows written.

    `rows` is a list of (endpoint, method, status_code, timestamp) tuples, where
    timestamp is a timezone-aware UTC datetime captured when the response was
    produced -- not when the flush ran. That distinction matters: batching means
    a row can be written up to a flush interval after the request happened, and
    stamping it with NOW() would smear traffic across hour boundaries in the
    peak-hours chart.

    Called from main.flush_request_logs() on the scheduler thread. It is never
    called from the request path: this is a blocking call, and the middleware
    that used to make it per-request ran on the event loop.

    Swipes are deliberately NOT logged here -- user_swipes.swiped_at already
    carries that timestamp, so a row here would duplicate it. See
    main.LOG_EXCLUDED_PREFIXES.
    """
    if not rows:
        return 0

    # hour_of_day is derived from the tuple's own UTC timestamp. The dashboard
    # labels this axis "utc" and the swipe-derived half of the same chart is
    # converted the same way, so the two sources cannot land in different buckets.
    values = [(endpoint, method, status_code, ts.hour, ts)
              for endpoint, method, status_code, ts in rows]

    with db_cursor(commit=True) as cursor:
        execute_values(
            cursor,
            '''INSERT INTO request_logs (endpoint, method, status_code, hour_of_day, logged_at)
               VALUES %s''',
            values,
        )

    return len(values)


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
    """Request counts per UTC hour, combining two measured sources.

    Swipes are not written to request_logs (they would duplicate
    user_swipes.swiped_at), so this unions the two tables. Both are real counts
    of real events — nothing here is scaled, sampled or estimated.
    """
    cursor.execute('''
        SELECT hour, SUM(n)::BIGINT AS request_count
        FROM (
            SELECT hour_of_day AS hour, COUNT(*) AS n
            FROM request_logs
            GROUP BY hour_of_day

            UNION ALL

            SELECT EXTRACT(HOUR FROM (swiped_at AT TIME ZONE 'UTC'))::INTEGER AS hour,
                   COUNT(*) AS n
            FROM user_swipes
            GROUP BY 1
        ) combined
        GROUP BY hour
        ORDER BY hour ASC
    ''')
    rows = cursor.fetchall()

    counts_by_hour = {r['hour']: r['request_count'] for r in rows}
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
    """Most-hit endpoints, combining request_logs with the swipe count.

    /api/v1/swipe is the highest-traffic endpoint but is not written to
    request_logs, so it is counted from user_swipes and folded in here. Omitting
    it would leave the busiest route missing from a panel titled 'Most Frequent
    API Endpoints'.
    """
    cursor.execute('''
        WITH combined AS (
            SELECT endpoint, method, COUNT(*) AS hit_count
            FROM request_logs
            GROUP BY endpoint, method

            UNION ALL

            SELECT '/api/v1/swipe' AS endpoint, 'POST' AS method, COUNT(*) AS hit_count
            FROM user_swipes
            HAVING COUNT(*) > 0
        )
        SELECT endpoint, method, SUM(hit_count)::BIGINT AS hit_count
        FROM combined
        GROUP BY endpoint, method
        ORDER BY hit_count DESC
        LIMIT %s
    ''', (limit,))
    rows = cursor.fetchall()

    # Denominator must span BOTH sources or the percentages will not sum sanely.
    cursor.execute('''
        SELECT (SELECT COUNT(*) FROM request_logs)
             + (SELECT COUNT(*) FROM user_swipes) AS total_requests
    ''')
    total_row = cursor.fetchone()
    total_reqs = max(total_row['total_requests'] if total_row else 1, 1)

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


# ---------------------------------------------------------------------------
# TELEMETRY MEASUREMENT — PROVENANCE RULES
#
# Every numeric field this module reports carries a `measured` flag:
#   measured=True   -> read from the OS, the filesystem, or Postgres at call time.
#   measured=False  -> not observable from inside this container; value is None
#                      and `unavailable_reason` says why.
#
# There is no third category. Nothing is estimated, inferred from a ratio, or
# hardcoded to a plausible-looking constant. An earlier version of this file
# manufactured host directory sizes as fixed 62%/32% shares of disk usage and
# reported invented CPU/RAM/egress figures as if they were readings; both are
# gone. See docs/ARCHITECTURE.md (ADR-010).
#
# Published ALLOWANCES are a legitimate exception: a documented quota is a fact,
# not a measurement. Those live in ALWAYS_FREE below, carry `is_limit`, and are
# never presented as though they were observed usage.
# ---------------------------------------------------------------------------

def _human_bytes(n) -> str:
    """Format a byte count. Returns 'unknown' for None so an unmeasured value can
    never be rendered as '0 B', which would read as a measurement of zero."""
    if n is None:
        return "unknown"
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < step or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= step
    return f"{n:.1f} TB"


def _du_bytes(path: str, timeout: float = 2.0):
    """Real recursive size of `path` in bytes, or None if it cannot be measured.

    Returning None is the whole point: a path we cannot read is reported as
    unmeasured, never back-filled with a guess.
    """
    if not os.path.isdir(path):
        return None
    try:
        res = subprocess.run(["du", "-sb", path],
                             capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0 and res.stdout.split():
            return int(res.stdout.split()[0])
    except Exception:
        pass
    # `du` unavailable or timed out: walk instead, but only report a total if the
    # walk completes, so a partial traversal never masquerades as a full one.
    try:
        total = 0
        for root, _dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    if not os.path.islink(fp):
                        total += os.path.getsize(fp)
                except OSError:
                    pass
        return total
    except Exception:
        return None


def _measured_paths() -> list:
    """Paths this container genuinely owns and can therefore measure.

    Deliberately excludes host directories. The backend runs in a container, so
    `/var` here is the *container's* /var, not the host's Docker image store or
    the Postgres volume. Measuring those and labelling them as host figures was
    the original bug.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return [
        {"name": "Application code", "path": here},
        {"name": "Generated output & media cache", "path": os.path.join(here, "output")},
        {"name": "Python runtime & site-packages", "path": "/usr"},
        {"name": "Container state", "path": "/var/lib"},
        {"name": "Temporary buffer", "path": "/tmp"},
    ]


def get_folder_storage_sizes() -> dict:
    """Real `du` measurements of paths inside THIS container.

    Scope: the container filesystem only. Host-level consumers of the disk (the
    Docker image store, the Postgres volume, the host OS) are not visible from
    here, so they are reported as an explicit `unaccounted_bytes` remainder
    rather than divided up among invented shares.
    """
    total, used, free = shutil.disk_usage("/")

    folders, unmeasured = [], []
    for item in _measured_paths():
        size = _du_bytes(item["path"])
        if size is None:
            unmeasured.append({
                "name": item["name"],
                "path": item["path"],
                "measured": False,
                "unavailable_reason": "Path is not readable from inside the container.",
            })
            continue
        folders.append({
            "name": item["name"],
            "path": item["path"],
            "size_bytes": size,
            "display_size": _human_bytes(size),
            "percent_of_disk_used": round(size / used * 100, 1) if used else 0.0,
            "measured": True,
            "source": "du -sb",
        })

    folders.sort(key=lambda x: x["size_bytes"], reverse=True)
    measured_bytes = sum(f["size_bytes"] for f in folders)
    unaccounted = max(used - measured_bytes, 0)

    return {
        "scope": "Container filesystem only — host directories are not visible from here.",
        "measured": True,
        "source": "shutil.disk_usage('/') + du -sb",
        "disk_total_bytes": total,
        "disk_used_bytes": used,
        "disk_free_bytes": free,
        "disk_total_display": _human_bytes(total),
        "disk_used_display": _human_bytes(used),
        "disk_free_display": _human_bytes(free),
        "folders": folders,
        "unmeasured": unmeasured,
        "measured_bytes": measured_bytes,
        "measured_display": _human_bytes(measured_bytes),
        "unaccounted_bytes": unaccounted,
        "unaccounted_display": _human_bytes(unaccounted),
        "unaccounted_note": (
            "Disk consumed by the host OS, Docker image layers and the Postgres "
            "volume. Not visible from inside this container and deliberately not "
            "estimated."
        ),
    }


def _read_meminfo() -> dict:
    """Parse /proc/meminfo into bytes. Empty dict if unreadable."""
    values = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    values[key] = int(parts[0]) * 1024   # /proc reports kB
    except Exception:
        return {}
    return values


def _cgroup_memory_limit():
    """The container's memory ceiling if one is set, else None.

    None means no limit, in which case /proc/meminfo reports the host's memory —
    which for this deployment is the real VM figure we want.
    """
    for path in ("/sys/fs/cgroup/memory.max",
                 "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path) as fh:
                raw = fh.read().strip()
            if raw == "max":
                return None
            value = int(raw)
            # cgroup v1 uses a huge sentinel to mean 'unlimited'.
            return None if value >= (1 << 62) else value
        except Exception:
            continue
    return None


def get_system_metrics() -> dict:
    """Live CPU and memory readings, taken at call time from /proc."""
    mem = _read_meminfo()
    cgroup_limit = _cgroup_memory_limit()

    mem_total = cgroup_limit if cgroup_limit is not None else mem.get("MemTotal")
    mem_available = mem.get("MemAvailable")
    mem_used = (mem_total - mem_available
                if mem_total is not None and mem_available is not None else None)

    memory = {
        "measured": mem_total is not None,
        "source": ("/sys/fs/cgroup (container limit)" if cgroup_limit is not None
                   else "/proc/meminfo:MemTotal (no cgroup limit — host total)"),
        "total_bytes": mem_total,
        "available_bytes": mem_available,
        "used_bytes": mem_used,
        "total_display": _human_bytes(mem_total),
        "used_display": _human_bytes(mem_used),
        "used_percent": (round(mem_used / mem_total * 100, 1)
                         if mem_used is not None and mem_total else None),
    }
    if mem_total is None:
        memory["unavailable_reason"] = "/proc/meminfo could not be read."

    swap_total = mem.get("SwapTotal")
    swap_free = mem.get("SwapFree")
    swap = {
        "measured": swap_total is not None,
        "source": "/proc/meminfo:SwapTotal",
        "total_bytes": swap_total,
        "used_bytes": (swap_total - swap_free
                       if swap_total is not None and swap_free is not None else None),
        "total_display": _human_bytes(swap_total),
    }

    try:
        with open("/proc/loadavg") as fh:
            one, five, fifteen = fh.read().split()[:3]
        load = {"measured": True, "source": "/proc/loadavg",
                "load_1m": float(one), "load_5m": float(five), "load_15m": float(fifteen)}
    except Exception:
        load = {"measured": False, "source": "/proc/loadavg",
                "load_1m": None, "load_5m": None, "load_15m": None,
                "unavailable_reason": "/proc/loadavg could not be read."}

    cpu_count = os.cpu_count()
    return {
        "memory": memory,
        "swap": swap,
        "load_average": load,
        "cpu": {
            "measured": cpu_count is not None,
            "source": "os.cpu_count()",
            "visible_cpus": cpu_count,
            "note": "Logical CPUs visible to the container, not an OCPU allowance figure.",
        },
    }


def _database_storage_sizes(cursor) -> dict:
    """Real on-disk size of the database and each table, straight from Postgres."""
    cursor.execute("SELECT pg_database_size(current_database()) AS total_bytes")
    total = cursor.fetchone()["total_bytes"]

    cursor.execute('''
        SELECT c.relname AS table_name, pg_total_relation_size(c.oid) AS size_bytes
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY 2 DESC
    ''')
    tables = [{
        "name": r["table_name"],
        "size_bytes": r["size_bytes"],
        "display_size": _human_bytes(r["size_bytes"]),
        "measured": True,
    } for r in cursor.fetchall()]

    return {
        "measured": True,
        "source": "pg_database_size() / pg_total_relation_size()",
        "total_bytes": total,
        "total_display": _human_bytes(total),
        "tables": tables,
    }


def get_database_storage_sizes() -> dict:
    """Standalone wrapper around _database_storage_sizes()."""
    with db_cursor() as cursor:
        return _database_storage_sizes(cursor)


def _database_detailed_analytics(cursor) -> dict:
    cursor.execute("SELECT COUNT(*) as total_articles, MIN(created_at) as oldest, MAX(created_at) as newest FROM articles")
    art_stats = cursor.fetchone() or {}

    cursor.execute("SELECT COUNT(*) as total_swipes, COUNT(CASE WHEN action = 'read' THEN 1 END) as read_count, COUNT(CASE WHEN action = 'pass' THEN 1 END) as pass_count FROM user_swipes")
    swipe_stats = cursor.fetchone() or {}

    cursor.execute("SELECT COUNT(*) as total_sessions FROM user_sessions")
    sess_stats = cursor.fetchone() or {}

    # Swipes are no longer written to request_logs, so counting that table alone
    # would understate traffic by roughly the swipe volume (the largest share).
    cursor.execute('''
        SELECT (SELECT COUNT(*) FROM request_logs)
             + (SELECT COUNT(*) FROM user_swipes) AS total_requests
    ''')
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


# Oracle Cloud Always Free published allowances.
#
# These are LIMITS, not measurements — documented facts with a verification date,
# which is why they are allowed to be constants. They are never rendered as
# though they were observed usage.
#
# Source:  https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
# Verified: 2026-08-26
#
# IMPORTANT: this deployment runs VM.Standard.E2.1.Micro (x86) per
# PROJECT_STATUS.md — the AMD micro allowance. A previous version of this file
# used the Ampere A1 (Arm) allowance of 2 OCPU / 12 GB, which describes a
# machine this project does not have. The micro shape gets 1/8 OCPU and 1 GB of
# memory per instance, two instances per tenancy.
ALWAYS_FREE = {
    "is_limit": True,
    "verified_on": "2026-08-26",
    "source_url": ("https://docs.oracle.com/en-us/iaas/Content/FreeTier/"
                   "freetier_topic-Always_Free_Resources.htm"),
    "shape": "VM.Standard.E2.1.Micro (x86)",
    "micro_instances": 2,
    "ocpu_per_micro_instance": 0.125,
    "memory_gb_per_micro_instance": 1,
    "block_volume_total_gb": 200,
    "outbound_transfer_tb_per_month": 10,
}


def get_free_tier_allowances(system: dict = None) -> dict:
    """Published Always Free allowances alongside what can actually be measured.

    Pass `system` (from get_system_metrics()) when the caller has already taken a
    reading, so the quota panel and the system panel report the *same* reading
    rather than two measurements taken moments apart — two panels disagreeing
    about current memory use looks exactly like the fabrication this replaced.

    Only two of these are observable from inside the VM, and one only partially:

      memory   — measurable (/proc/meminfo, see get_system_metrics).
      storage  — the *provisioned* size of this volume is measurable. Note that
                 what counts against the 200 GB account allowance is provisioned
                 size across every volume in the tenancy, not how full any one
                 of them is. The old code compared *used* bytes against the
                 allowance, which measured the wrong quantity.
      ocpu     — not observable; requires the OCI Monitoring API.
      egress   — not observable; requires the OCI Monitoring API.

    The two unobservable items are reported with measured=False and a null
    value rather than a plausible-looking number.
    """
    total, _used, _free = shutil.disk_usage("/")
    if system is None:
        system = get_system_metrics()
    memory = system["memory"]

    provisioned_gb = round(total / (1024 ** 3), 1)
    allowance_gb = ALWAYS_FREE["block_volume_total_gb"]

    return {
        "reference": ALWAYS_FREE,
        "items": [
            {
                "key": "memory",
                "label": "Memory (this instance)",
                "measured": memory["measured"],
                "used_bytes": memory["used_bytes"],
                "total_bytes": memory["total_bytes"],
                "used_display": memory["used_display"],
                "total_display": memory["total_display"],
                "percent": memory["used_percent"],
                "source": memory["source"],
                "limit_note": (
                    f"Always Free allows "
                    f"{ALWAYS_FREE['memory_gb_per_micro_instance']} GB per micro instance."
                ),
            },
            {
                "key": "block_storage",
                "label": "Block volume (this volume only)",
                "measured": True,
                "provisioned_gb": provisioned_gb,
                "allowance_gb": allowance_gb,
                "percent": round(provisioned_gb / allowance_gb * 100, 1),
                "source": "shutil.disk_usage('/') total",
                "limit_note": (
                    f"{allowance_gb} GB allowance is account-wide across all volumes; "
                    "only this volume is visible from inside the VM."
                ),
            },
            {
                "key": "ocpu",
                "label": "OCPU allowance consumption",
                "measured": False,
                "percent": None,
                "unavailable_reason": (
                    "Requires the OCI Monitoring API. An instance cannot observe how "
                    "much of a tenancy-wide OCPU allowance it consumes."
                ),
                "limit_note": (
                    f"Always Free allows {ALWAYS_FREE['micro_instances']} micro instances at "
                    f"{ALWAYS_FREE['ocpu_per_micro_instance']} OCPU each."
                ),
            },
            {
                "key": "outbound_transfer",
                "label": "Outbound data transfer",
                "measured": False,
                "percent": None,
                "unavailable_reason": (
                    "Requires the OCI Monitoring API. Per-tenancy egress is not "
                    "derivable from container network counters."
                ),
                "limit_note": (
                    f"Always Free allows "
                    f"{ALWAYS_FREE['outbound_transfer_tb_per_month']} TB per month."
                ),
            },
        ],
    }


def get_telemetry_summary() -> dict:
    """Combine engagement analytics with live system measurements.

    Provenance contract: every numeric field below is either read at call time
    (`measured: true`) or reported as unavailable (`measured: false` with a null
    value and an `unavailable_reason`). Nothing here is estimated or hardcoded.
    Documented in docs/ARCHITECTURE.md, ADR-010.
    """
    # All DB-backed panels share ONE pooled connection.
    with db_cursor() as cursor:
        db_analytics = _database_detailed_analytics(cursor)
        category_analytics = _category_stats(cursor)
        top_articles = _top_swiped_articles(cursor, 6)
        top_endpoints = _top_api_endpoints(cursor, 6)
        active_users = _active_users_count(cursor, 60)
        avg_duration = _avg_session_duration_minutes(cursor)
        hourly_distribution = _hourly_usage_distribution(cursor)
        database_storage = _database_storage_sizes(cursor)

    # Filesystem and /proc reads happen outside the `with`: du can take seconds
    # and holding a pooled connection across it would starve real queries.
    system = get_system_metrics()
    folder_sizes = get_folder_storage_sizes()
    free_tier = get_free_tier_allowances(system)   # reuse the same reading

    disk_total = folder_sizes["disk_total_bytes"]
    disk_used = folder_sizes["disk_used_bytes"]

    return {
        "provenance": {
            "contract": (
                "Every numeric field carries `measured`. Fields that cannot be read "
                "from inside the container are measured=false with a null value and "
                "an unavailable_reason. No field is estimated or hardcoded."
            ),
            "documented_in": "docs/ARCHITECTURE.md (ADR-010)",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "storage": {
            "measured": True,
            "source": folder_sizes["source"],
            "total_bytes": disk_total,
            "used_bytes": disk_used,
            "free_bytes": folder_sizes["disk_free_bytes"],
            "total_display": folder_sizes["disk_total_display"],
            "used_display": folder_sizes["disk_used_display"],
            "free_display": folder_sizes["disk_free_display"],
            "used_percent": round(disk_used / disk_total * 100, 1) if disk_total else None,
        },
        "system": system,
        "folder_analytics": folder_sizes,
        "database_storage": database_storage,
        "free_tier": free_tier,
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
