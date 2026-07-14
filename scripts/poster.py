#!/usr/bin/env python3
"""
poster.py — Post thread chains to Threads via Graph API.

Based on pressbox-pipeline/threads_poster.py pattern:
  1. POST /{user_id}/threads          -> creates container, returns creation_id
  2. POST /{user_id}/threads_publish   -> publishes container, returns post_id

To CHAIN posts: each subsequent container uses `reply_to_id` = previous post_id.
"""
import os
import sys
import re
import time
import logging
from pathlib import Path
from typing import Optional

import httpx

# Load token from threads-agent .env
_ENV_PATH = Path.home() / "threads-agent" / ".env"
def _load_env():
    env = {}
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    os.environ.update(env)

_load_env()

GRAPH = "https://graph.threads.net/v1.0"
TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
USER_ID = os.environ.get("THREADS_USER_ID", "")
DEFAULT_TIMEOUT = 30
INTER_POST_DELAY_SEC = 3

logger = logging.getLogger("poster")


def _normalize_text(text: str) -> str:
    """Normalize whitespace and strip markdown for Threads."""
    # Collapse 3+ newlines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Strip markdown bold/italic
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', text)
    # Insert \n\n between sentences (Threads renders \n as space, \n\n as break)
    text = re.sub(
        r'(?<!Mr)(?<!Mrs)(?<!Ms)(?<!Dr)(?<!St)(?<!vs)(?<!Jr)(?<!Sr)(?<!Prof)'
        r'([.?!])\s+(?=[A-Z])',
        r'\1\n\n',
        text
    )
    return text


def _create_container(
    text: str,
    reply_to_id: Optional[str] = None,
    image_url: Optional[str] = None,
) -> Optional[str]:
    """Step 1: create a media container. Returns creation_id."""
    text = _normalize_text(text)
    
    params = {
        "text": text,
        "access_token": TOKEN,
    }
    
    if image_url:
        params["media_type"] = "IMAGE"
        params["image_url"] = image_url
    else:
        params["media_type"] = "TEXT"
    
    if reply_to_id:
        params["reply_to_id"] = reply_to_id
    
    try:
        r = httpx.post(f"{GRAPH}/{USER_ID}/threads", data=params, timeout=DEFAULT_TIMEOUT)
        if r.status_code != 200:
            print(f"  [ERR] Container create failed: {r.status_code} {r.text[:200]}")
            return None
        data = r.json()
        creation_id = data.get("id")
        if not creation_id:
            print(f"  [ERR] No creation_id returned: {data}")
            return None
        logger.info("Created container %s (reply_to=%s)", creation_id, reply_to_id)
        return creation_id
    except Exception as e:
        print(f"  [ERR] Container create exception: {e}")
        return None


def _publish_container(creation_id: str) -> Optional[str]:
    """Step 2: publish the container. Returns the live post id."""
    try:
        r = httpx.post(f"{GRAPH}/{USER_ID}/threads_publish", data={
            "access_token": TOKEN,
            "creation_id": creation_id,
        }, timeout=DEFAULT_TIMEOUT)
        if r.status_code != 200:
            print(f"  [ERR] Publish failed: {r.status_code} {r.text[:200]}")
            return None
        post_id = r.json().get("id")
        if not post_id:
            print(f"  [ERR] No post_id returned on publish: {r.json()}")
            return None
        logger.info("Published post %s", post_id)
        return post_id
    except Exception as e:
        print(f"  [ERR] Publish exception: {e}")
        return None


def post_single(text: str, image_url: Optional[str] = None) -> Optional[str]:
    """Create + publish a single post. Returns post_id."""
    creation_id = _create_container(text, image_url=image_url)
    if not creation_id:
        return None
    time.sleep(2)  # buffer for container processing
    return _publish_container(creation_id)


def post_thread(slides: list[str], image_url: Optional[str] = None) -> list[dict]:
    """Post slides as a thread chain. Returns list of {text, post_id}."""
    if not TOKEN:
        print("ERROR: THREADS_ACCESS_TOKEN not set")
        return []
    if not USER_ID:
        print("ERROR: THREADS_USER_ID not set")
        return []
    
    results = []
    reply_to = None
    
    for i, text in enumerate(slides):
        print(f"  Slide {i+1}/{len(slides)}: {text[:60]}...")
        
        # First slide gets image if provided
        img = image_url if (i == 0 and image_url) else None
        
        # Step 1: create container
        creation_id = _create_container(text, reply_to_id=reply_to, image_url=img)
        if not creation_id:
            print(f"  [ERR] Failed at slide {i+1}, stopping chain")
            break
        
        # Wait for container to be ready
        time.sleep(2)
        
        # Step 2: publish container
        post_id = _publish_container(creation_id)
        if not post_id:
            print(f"  [ERR] Failed to publish slide {i+1}, stopping chain")
            break
        
        results.append({"text": text, "post_id": post_id})
        reply_to = post_id
        print(f"  ✓ Posted: {post_id}")
        
        # Rate limit: wait between posts
        if i < len(slides) - 1:
            time.sleep(INTER_POST_DELAY_SEC)
    
    return results


def get_metrics(post_id: str) -> Optional[dict]:
    """Pull engagement metrics for a Threads post. Returns dict or None."""
    if not TOKEN:
        return None
    try:
        r = httpx.get(
            f"{GRAPH}/{post_id}/insights",
            params={
                "metric": "views,likes,replies,reposts,quotes",
                "access_token": TOKEN,
            },
            timeout=15,
        )
        if r.status_code != 200:
            return None
        d = r.json()
        metrics = {}
        for item in d.get("data", []):
            name = item.get("name")
            value = item.get("values", [{}])[0].get("value", 0)
            metrics[name] = value
        if not metrics:
            return None
        return {
            "views": metrics.get("views", 0),
            "likes": metrics.get("likes", 0),
            "replies": metrics.get("replies", 0),
            "shares": metrics.get("reposts", 0) + metrics.get("quotes", 0),
            "permalink": "",
        }
    except Exception:
        return None


def pull_engagement(conn, max_per_run: int = 10) -> int:
    """Pull metrics for posts >12h old that haven't been tracked yet. Returns count updated."""
    import time as _time
    cutoff = _time.time() - 3600  # 1 hour — aggressive pull
    
    rows = conn.execute("""
        SELECT id, thread_post_id, posted_at 
        FROM posts 
        WHERE status='posted' 
          AND thread_post_id IS NOT NULL
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()
    
    updated = 0
    processed = 0
    for row in rows:
        if processed >= max_per_run:
            break
        if row['posted_at']:
            try:
                from datetime import datetime
                pt = datetime.fromisoformat(row['posted_at']).timestamp()
                if pt > cutoff:
                    continue  # too recent
            except:
                continue
        
        metrics = get_metrics(row['thread_post_id'])
        processed += 1
        if metrics:
            conn.execute("""
                INSERT OR REPLACE INTO performance (post_id, likes, replies, reposts, views)
                VALUES (?, ?, ?, ?, ?)
            """, (row['id'], metrics['likes'], metrics['replies'], metrics['shares'], metrics['views']))
            conn.execute("""
                UPDATE posts SET views=?, likes=?, replies=?, shares=?
                WHERE id=?
            """, (metrics['views'], metrics['likes'], metrics['replies'], metrics['shares'], row['id']))
            updated += 1
        time.sleep(0.3)  # rate limit
    
    if updated:
        conn.commit()
    return updated


def post_from_db(limit: int = 1, dry_run: bool = False):
    """Post staged posts from pipeline.db."""
    from db import get_db, get_staged_posts, mark_posted
    
    conn = get_db()
    staged = get_staged_posts(conn, limit)
    if not staged:
        print("No staged posts.")
        return
    
    for post in staged:
        print(f"\nPosting: {post['title'][:60]}...")
        
        # sqlite3.Row doesn't have .get(), convert to dict
        p = dict(post)
        
        # Build slides from DB columns
        slides = []
        for key in ['slide_hook', 'slide_setup', 'slide_twist', 'slide_deep', 'slide_sowhat', 'slide_cta']:
            val = p.get(key, '')
            if val:
                slides.append(val)
        
        caption = p.get('caption', '')
        
        # Fallback: if slide columns empty, check caption for thread_chain or single post
        if not slides and caption:
            if '\n---\n' in caption:
                # Thread chain: parse all slides from caption
                slides = [s.strip() for s in caption.split('\n---\n') if s.strip()]
                print(f"  Thread chain from caption: {len(slides)} slides")
            else:
                # Single post: use caption as the only slide
                slides = [caption]
                print(f"  Single post from caption")
        
        if not slides:
            print("  [SKIP] No slides")
            continue
        
        if dry_run:
            for i, s in enumerate(slides, 1):
                print(f"  [{i}] {s[:80]}")
            continue
        
        results = post_thread(slides, image_url=p.get('image'))
        if results:
            mark_posted(conn, post['id'], results[0]['post_id'])
            print(f"  ✓ Thread root: {results[0]['post_id']}")
        else:
            print("  ✗ Failed")
    
    conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    post_from_db(dry_run=dry)
