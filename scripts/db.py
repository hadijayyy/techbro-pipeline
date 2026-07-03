#!/usr/bin/env python3
"""db.py — SQLite staging for Tech-AI Threads pipeline."""
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

WIB = timezone(timedelta(hours=7))
DB_PATH = Path(__file__).parent.parent / "pipeline.db"

def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            date TEXT,
            image TEXT,
            body TEXT,
            score INTEGER DEFAULT 0,
            scraped_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'staged',
            slide_hook TEXT,
            slide_setup TEXT,
            slide_twist TEXT,
            slide_deep TEXT,
            slide_sowhat TEXT,
            slide_cta TEXT,
            caption TEXT,
            hashtags TEXT,
            generated_at TEXT NOT NULL DEFAULT (datetime('now')),
            posted_at TEXT,
            thread_post_id TEXT,
            FOREIGN KEY (article_id) REFERENCES articles(id)
        );
        CREATE TABLE IF NOT EXISTS performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            likes INTEGER DEFAULT 0,
            replies INTEGER DEFAULT 0,
            reposts INTEGER DEFAULT 0,
            views INTEGER DEFAULT 0,
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (post_id) REFERENCES posts(id)
        );
        CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url);
        CREATE INDEX IF NOT EXISTS idx_articles_score ON articles(score DESC);
        CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
    """)
    conn.commit()
    return conn

def upsert_article(conn, art: dict) -> int:
    conn.execute("""INSERT OR IGNORE INTO articles (url, source, title, date, image, body, score)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (art["url"], art["source"], art["title"],
         art["date"].isoformat() if art.get("date") else None,
         art.get("image", ""), art.get("body", ""), art.get("score", 0)))
    conn.commit()
    return conn.execute("SELECT id FROM articles WHERE url = ?", (art["url"],)).fetchone()["id"]

def stage_post(conn, article_id: int, slides: dict, caption: str, hashtags: str) -> int:
    # Generator uses slide_1..slide_6, DB uses hook/setup/twist/deep/sowhat/cta
    key_map = {"slide_1": "hook", "slide_2": "setup", "slide_3": "twist",
               "slide_4": "deep", "slide_5": "sowhat", "slide_6": "cta"}
    mapped = {}
    for slide_key, db_key in key_map.items():
        mapped[db_key] = slides.get(slide_key, "")

    conn.execute("""INSERT INTO posts (article_id, status, slide_hook, slide_setup, slide_twist,
        slide_deep, slide_sowhat, slide_cta, caption, hashtags)
        VALUES (?, 'staged', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (article_id, mapped["hook"], mapped["setup"], mapped["twist"],
         mapped["deep"], mapped["sowhat"], mapped["cta"], caption, hashtags))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

def mark_posted(conn, post_id: int, thread_post_id: str):
    conn.execute("UPDATE posts SET status='posted', posted_at=datetime('now'), thread_post_id=? WHERE id=?",
        (thread_post_id, post_id))
    conn.commit()

def mark_failed(conn, article_id: int):
    """Mark article as failed generation — skip in future fallback."""
    conn.execute("INSERT INTO posts (article_id, status) VALUES (?, 'failed')", (article_id,))
    conn.commit()

def get_staged_posts(conn, limit: int = 5) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT p.*, a.title, a.url, a.image, a.source FROM posts p JOIN articles a ON p.article_id=a.id WHERE p.status='staged' ORDER BY p.id DESC LIMIT ?",
        (limit,)).fetchall()]

def get_stats(conn) -> dict:
    return {
        "articles": conn.execute("SELECT COUNT(*) c FROM articles").fetchone()["c"],
        "staged": conn.execute("SELECT COUNT(*) c FROM posts WHERE status='staged'").fetchone()["c"],
        "posted": conn.execute("SELECT COUNT(*) c FROM posts WHERE status='posted'").fetchone()["c"],
    }

def cleanup_old(conn, days: int = 7) -> dict:
    """Delete articles and posts older than N days. Returns counts."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    # Delete posts for old articles
    old_posts = conn.execute(
        "DELETE FROM posts WHERE article_id IN (SELECT id FROM articles WHERE scraped_at < ?)",
        (cutoff,)
    ).rowcount
    # Delete old articles
    old_articles = conn.execute(
        "DELETE FROM articles WHERE scraped_at < ?", (cutoff,)
    ).rowcount
    conn.commit()
    # Vacuum if we deleted anything (run outside transaction to avoid blocking)
    if old_articles > 0:
        try:
            conn.execute("VACUUM")
        except Exception:
            pass  # non-critical, DB still works without vacuum
    return {"deleted_articles": old_articles, "deleted_posts": old_posts}
