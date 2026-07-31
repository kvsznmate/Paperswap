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
