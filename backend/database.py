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


def get_top_swiped_articles(limit: int = 6) -> list:
    """Retrieve articles with the highest number of 'read' (swipe right) actions."""
    conn = get_db_connection()
    cursor = conn.cursor()
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
    cursor.close()
    conn.close()

    results = []
    for r in rows:
        item = dict(r)
        total = item['total_swipes']
        reads = item['read_count']
        item['like_ratio_percent'] = round((reads / total * 100), 1) if total > 0 else 0
        results.append(item)
    return results


def get_top_api_endpoints(limit: int = 6) -> list:
    """Retrieve top API endpoints by total request hits and percentage distribution."""
    conn = get_db_connection()
    cursor = conn.cursor()
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

    cursor.close()
    conn.close()

    results = []
    for r in rows:
        item = dict(r)
        item['percentage'] = round((item['hit_count'] / total_reqs) * 100, 1)
        results.append(item)
    return results


def get_folder_storage_sizes() -> list:
    """Analyze folder disk usage across key system, docker container, and application directories."""
    target_paths = [
        {"name": "Docker Containers & Images", "path": "/var/lib/docker"},
        {"name": "Linux System Binaries & Libs (/usr)", "path": "/usr"},
        {"name": "OS System Cache & State (/var)", "path": "/var/cache"},
        {"name": "PostgreSQL Database Data", "path": "/var/lib/postgresql/data"},
        {"name": "Application Backend Workspace", "path": os.path.dirname(__file__)},
        {"name": "HTML Templates & Static Assets", "path": os.path.join(os.path.dirname(__file__), "templates")},
        {"name": "System Logs (/var/log)", "path": "/var/log"},
        {"name": "Temporary Files (/tmp)", "path": "/tmp"}
    ]
    results = []
    for item in target_paths:
        p = item["path"]
        size_bytes = 0
        exists = os.path.exists(p)
        if exists:
            if os.path.isfile(p):
                size_bytes = os.path.getsize(p)
            else:
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
        size_mb = round(size_bytes / (1024 * 1024), 2)
        results.append({
            "name": item["name"],
            "path": p,
            "exists": exists,
            "size_mb": size_mb,
            "display_size": f"{size_mb} MB" if size_mb < 1024 else f"{round(size_mb/1024, 2)} GB"
        })
    return results



def get_database_detailed_analytics() -> dict:
    """Retrieve database stats: oldest entry, newest entry, total reads vs passes, article counts."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as total_articles, MIN(created_at) as oldest, MAX(created_at) as newest FROM articles")
    art_stats = cursor.fetchone() or {}

    cursor.execute("SELECT COUNT(*) as total_swipes, COUNT(CASE WHEN action = 'read' THEN 1 END) as read_count, COUNT(CASE WHEN action = 'pass' THEN 1 END) as pass_count FROM user_swipes")
    swipe_stats = cursor.fetchone() or {}

    cursor.execute("SELECT COUNT(*) as total_sessions FROM user_sessions")
    sess_stats = cursor.fetchone() or {}

    cursor.execute("SELECT COUNT(*) as total_requests FROM request_logs")
    req_stats = cursor.fetchone() or {}

    cursor.close()
    conn.close()

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

    db_analytics = get_database_detailed_analytics()
    top_articles = get_top_swiped_articles(6)
    top_endpoints = get_top_api_endpoints(6)
    folder_sizes = get_folder_storage_sizes()
    oracle_quota = get_oracle_quota_status(used_gb)
    active_users = get_active_users_count(60)
    avg_duration = get_avg_session_duration_minutes()
    hourly_distribution = get_hourly_usage_distribution()

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
        "top_swiped_cards": top_articles,
        "top_api_endpoints": top_endpoints,
        "database_analytics": db_analytics
    }


