import os
import hashlib

import psycopg2
from psycopg2.extras import RealDictCursor


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


def get_db_connection():
    """Establish connection to PostgreSQL. RealDictCursor gives dict-like rows,
    matching the old sqlite3.Row behaviour the rest of the code relies on."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    """Initialize PostgreSQL tables."""
    conn = get_db_connection()
    cursor = conn.cursor()

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

    conn.commit()
    cursor.close()
    conn.close()


def generate_article_key(title: str, url: str) -> str:
    """Generate unique MD5 hash key for title and URL to prevent duplicates."""
    raw = f"{title.strip().lower()}_{url.strip().lower()}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def is_article_in_db(article_key: str) -> bool:
    """Check if article already exists in the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM articles WHERE article_key = %s", (article_key,))
    exists = cursor.fetchone() is not None
    cursor.close()
    conn.close()
    return exists


def save_article(article_data: dict) -> int:
    """
    Save a new article if not already present, using Postgres ON CONFLICT for
    deduplication (replaces SQLite's INSERT OR IGNORE / manual pre-check).
    Returns the article's database ID.
    """
    article_key = generate_article_key(article_data['title'], article_data['url'])

    conn = get_db_connection()
    cursor = conn.cursor()

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
        article_data.get('category', 'TECH'),
        article_data.get('image_url', ''),
        article_data.get('url', '#'),
    ))

    row = cursor.fetchone()
    if row is None:
        # Duplicate — the row already existed, so look up its id.
        cursor.execute("SELECT id FROM articles WHERE article_key = %s", (article_key,))
        row = cursor.fetchone()

    article_id = row['id']
    conn.commit()
    cursor.close()
    conn.close()
    print(f"[DB] Article ID #{article_id}: {article_data['title'][:40]}...")
    return article_id


def get_latest_articles(limit: int = 50) -> list:
    """Retrieve latest articles from the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, title, description, source, published_at, category, image_url, url, created_at
        FROM articles
        ORDER BY id DESC
        LIMIT %s
    ''', (limit,))

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    articles = []
    for idx, row in enumerate(rows, start=1):
        item = dict(row)
        # created_at is a datetime in Postgres — make it JSON-serializable.
        if item.get('created_at') is not None:
            item['created_at'] = item['created_at'].isoformat()
        item['index'] = idx
        articles.append(item)
    return articles


def record_user_swipe(article_id: int, action: str):
    """Record user swipe action (read or pass)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_swipes (article_id, action) VALUES (%s, %s)",
        (article_id, action),
    )
    conn.commit()
    cursor.close()
    conn.close()


def purge_old_articles(days: int = 7) -> int:
    """Delete articles older than `days` (based on created_at), returning the
    number of rows removed. Related user_swipes rows are removed automatically
    via ON DELETE CASCADE. Card 'images' are hotlinked URLs stored in the row,
    so deleting the row is all the cleanup required."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM articles WHERE created_at < NOW() - (%s || ' days')::interval",
        (days,),
    )
    removed = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    print(f"[DB Purge] Removed {removed} article(s) older than {days} days.")
    return removed


def record_session_heartbeat(session_id: str, user_agent: str = None, ip_address: str = None):
    """Record or refresh a user session heartbeat and update duration."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO user_sessions (session_id, user_agent, ip_address, created_at, last_heartbeat, duration_seconds)
        VALUES (%s, %s, %s, NOW(), NOW(), 0)
        ON CONFLICT (session_id) DO UPDATE SET
            last_heartbeat = NOW(),
            duration_seconds = GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (NOW() - user_sessions.created_at))))
    ''', (session_id, user_agent or '', ip_address or ''))
    conn.commit()
    cursor.close()
    conn.close()


def log_request_event(endpoint: str, method: str):
    """Log API request hit and extract hour of day (0-23) for peak hours analysis."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO request_logs (endpoint, method, hour_of_day)
        VALUES (%s, %s, EXTRACT(HOUR FROM NOW())::INTEGER)
    ''', (endpoint, method))
    conn.commit()
    cursor.close()
    conn.close()


def get_active_users_count(window_seconds: int = 60) -> int:
    """Return count of users with heartbeat recorded in the last `window_seconds`."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT COUNT(DISTINCT session_id) as active_count
        FROM user_sessions
        WHERE last_heartbeat >= NOW() - (%s || ' seconds')::interval
    ''', (window_seconds,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row['active_count'] if row else 0


def get_avg_session_duration_minutes() -> float:
    """Calculate average connected session duration in minutes."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT COALESCE(AVG(duration_seconds), 0) as avg_seconds
        FROM user_sessions
    ''')
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    avg_sec = float(row['avg_seconds']) if row else 0.0
    return round(avg_sec / 60.0, 1)


def get_hourly_usage_distribution() -> list:
    """Return request counts grouped by hour of the day (0..23)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT hour_of_day, COUNT(*) as request_count
        FROM request_logs
        GROUP BY hour_of_day
        ORDER BY hour_of_day ASC
    ''')
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    counts_by_hour = {r['hour_of_day']: r['request_count'] for r in rows}
    result = []
    for h in range(24):
        result.append({
            "hour": h,
            "label": f"{h:02d}:00",
            "count": counts_by_hour.get(h, 0)
        })
    return result


def get_telemetry_summary() -> dict:
    """Combine system disk usage and user analytics."""
    import shutil
    total, used, free = shutil.disk_usage("/")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as total_articles FROM articles")
    articles_count = cursor.fetchone()['total_articles']

    cursor.execute("SELECT COUNT(*) as total_swipes FROM user_swipes")
    swipes_count = cursor.fetchone()['total_swipes']

    cursor.execute("SELECT COUNT(*) as total_sessions FROM user_sessions")
    sessions_count = cursor.fetchone()['total_sessions']
    cursor.close()
    conn.close()

    active_users = get_active_users_count(60)
    avg_duration = get_avg_session_duration_minutes()
    hourly_distribution = get_hourly_usage_distribution()

    return {
        "storage": {
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "used_percent": round((used / total) * 100, 1)
        },
        "user_analytics": {
            "currently_connected_users": active_users,
            "avg_session_minutes": avg_duration,
            "total_sessions": sessions_count,
            "total_swipes": swipes_count,
            "total_articles": articles_count
        },
        "hourly_distribution": hourly_distribution
    }

