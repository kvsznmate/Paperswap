import sqlite3
import os
import hashlib
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "news_database.db")

def get_db_connection():
    """Establish connection to SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize SQLite database tables."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Table for storing unique news articles & card paths
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_key TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            source TEXT,
            published_at TEXT,
            category TEXT NOT NULL,
            image_url TEXT,
            url TEXT NOT NULL,
            card_filename TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Table for tracking user swipe actions (Read / Pass)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_swipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL,
            action TEXT CHECK(action IN ('read', 'pass')) NOT NULL,
            swiped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (article_id) REFERENCES articles (id)
        )
    ''')

    conn.commit()
    conn.close()

def generate_article_key(title: str, url: str) -> str:
    """Generate unique MD5 hash key for title and URL to prevent duplicates."""
    raw = f"{title.strip().lower()}_{url.strip().lower()}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()

def is_article_in_db(article_key: str) -> bool:
    """Check if article already exists in SQLite database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM articles WHERE article_key = ?", (article_key,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists

def save_article(article_data: dict, card_filename: str) -> int:
    """
    Saves new article to database if not already present.
    Returns article database ID.
    """
    article_key = generate_article_key(article_data['title'], article_data['url'])
    
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, card_filename FROM articles WHERE article_key = ?", (article_key,))
    existing = cursor.fetchone()
    
    if existing:
        conn.close()
        return existing['id']

    cursor.execute('''
        INSERT INTO articles (article_key, title, description, source, published_at, category, image_url, url, card_filename)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        article_key,
        article_data['title'],
        article_data.get('description', ''),
        article_data.get('source', 'News'),
        article_data.get('published_at', 'Recently'),
        article_data.get('category', 'TECH'),
        article_data.get('image_url', ''),
        article_data.get('url', '#'),
        card_filename
    ))

    article_id = cursor.lastrowid
    conn.commit()
    conn.close()
    print(f"[DB] Inserted new article ID #{article_id}: {article_data['title'][:40]}...")
    return article_id

def get_latest_articles(limit: int = 50) -> list:
    """Retrieve latest articles from SQLite database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, title, description, source, published_at, category, image_url, url, card_filename, created_at
        FROM articles
        ORDER BY id DESC
        LIMIT ?
    ''', (limit,))
    
    rows = cursor.fetchall()
    conn.close()

    articles = []
    for idx, row in enumerate(rows, start=1):
        item = dict(row)
        item['index'] = idx
        articles.append(item)
    return articles

def record_user_swipe(article_id: int, action: str):
    """Record user swipe action (read or pass) in database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO user_swipes (article_id, action) VALUES (?, ?)", (article_id, action))
    conn.commit()
    conn.close()

# Initialize DB when module loaded
init_db()
