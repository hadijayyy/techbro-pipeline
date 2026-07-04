#!/usr/bin/env python3
"""
poster.py — Post carousel slides to Threads as thread replies.
Token: reads from /home/ubuntu/threads-agent/.env
"""
import os
import sys
import re
import time
import httpx
from pathlib import Path

# Load token from threads-agent .env
_ENV_PATH = Path.home() / "threads-agent" / ".env"
def _load_env():
    env = {}
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                # Fix #9: handle values containing = (e.g. tokens, URLs with query params)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                env[k] = v
    os.environ.update(env)

_load_env()

GRAPH = "https://graph.threads.net/v1.0"
TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
USER_ID = os.environ.get("THREADS_USER_ID", "")


def _check_auth(r: httpx.Response) -> bool:
    """Check if response indicates token expiry. Returns False if auth failed."""
    if r.status_code in (401, 403):
        print(f"  [AUTH] Token expired or invalid ({r.status_code}). Refresh THREADS_ACCESS_TOKEN.")
        return False
    return True


def _fetch_og_image(url: str) -> str | None:
    """Re-fetch og:image from article URL as fallback."""
    try:
        r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=15)
        if r.status_code != 200:
            return None
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', r.text)
        if not m:
            m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', r.text)
        return m.group(1).strip() if m else None
    except Exception:
        return None


def _post_container(text: str, reply_to: str | None = None, image_url: str | None = None) -> str | None:
    """Create a single thread container. Returns container_id."""
    data = {"access_token": TOKEN}
    
    # Validate image URL before posting (stream to avoid full download)
    if image_url:
        try:
            with httpx.stream("GET", image_url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=10) as h:
                ct = h.headers.get("content-type", "")
                if h.status_code != 200 or not ct.startswith("image/"):
                    print(f"  [WARN] Image invalid (status={h.status_code}, type={ct})")
                    return None  # Fail — don't post slide 1 without image
        except Exception as e:
            print(f"  [WARN] Image check failed: {e}")
            return None  # Fail — don't post slide 1 without image
    
    if image_url:
        params = {
            "access_token": TOKEN,
            "media_type": "IMAGE",
            "image_url": image_url,
            "text": text,
        }
        if reply_to:
            params["reply_to_id"] = reply_to
        r = httpx.post(f"{GRAPH}/{USER_ID}/threads", data=params, timeout=30)
        if r.status_code != 200:
            print(f"  [ERR] Image post failed: {r.status_code} {r.text[:200]}")
            return None  # Abort — don't silently degrade to TEXT
    else:
        params = {
            "access_token": TOKEN,
            "media_type": "TEXT",
            "text": text,
        }
        if reply_to:
            params["reply_to_id"] = reply_to
        r = httpx.post(f"{GRAPH}/{USER_ID}/threads", data=params, timeout=30)
    
    if r.status_code != 200:
        _check_auth(r)
        print(f"  [ERR] Container create failed: {r.status_code} {r.text[:200]}")
        return None
    
    container_id = r.json().get("id")
    if not container_id:
        print(f"  [ERR] No container ID in response: {r.json()}")
        return None
    
    # Poll container status until ready (Meta recommends this over fixed sleep)
    for attempt in range(10):
        status_r = httpx.get(f"{GRAPH}/{container_id}", params={
            "fields": "status",
            "access_token": TOKEN,
        }, timeout=15)
        if status_r.status_code == 200:
            status = status_r.json().get("status", "")
            if status == "FINISHED":
                break
            if status == "ERROR":
                print(f"  [ERR] Container processing error")
                return None
        time.sleep(2)
    else:
        print(f"  [WARN] Container not ready after 20s, publishing anyway")
    
    # Publish the container
    r2 = httpx.post(f"{GRAPH}/{USER_ID}/threads_publish", data={
        "access_token": TOKEN,
        "creation_id": container_id,
    }, timeout=30)
    
    if r2.status_code != 200:
        _check_auth(r2)
        print(f"  [ERR] Publish failed: {r2.status_code} {r2.text[:200]}")
        return None
    
    post_id = r2.json().get("id")
    return post_id


def _normalize_for_threads(text: str) -> str:
    """Normalize text for Threads: strip markdown, fix spacing, format lists."""
    # Strip markdown bold/italic
    text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text)
    text = re.sub(r'\*+', '', text)

    # Fix spacing
    text = re.sub(r' ([.,!?;:])', r'\1', text)
    text = re.sub(r'\( +', '(', text)
    text = re.sub(r' +\)', ')', text)
    text = re.sub(r' {2,}', ' ', text)

    # Format A/B/C options: "A) text B) text C) text" → one per line
    text = re.sub(r'([A-Z])\)\s+', r'\n\1) ', text)
    text = re.sub(r'^\n', '', text)  # remove leading newline

    # Use shared list normalizer from generator
    try:
        from generator import _format_lists
        text = _format_lists(text)
    except ImportError:
        print("  [WARN] generator._format_lists not available, skipping list normalization")

    # Final cleanup
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def post_carousel(slides: list[str], image_url: str = None) -> list[str]:
    """Post a carousel as a thread chain. Returns list of post IDs."""
    if not TOKEN:
        print("ERROR: THREADS_ACCESS_TOKEN not set")
        return []
    if not USER_ID:
        print("ERROR: THREADS_USER_ID not set")
        return []
    
    # Normalize all slides for Threads
    slides = [_normalize_for_threads(s) for s in slides]
    
    post_ids = []
    reply_to = None
    
    for i, text in enumerate(slides):
        print(f"  Slide {i+1}/{len(slides)}: {text[:60]}...")
        
        # First slide gets image if provided
        img = image_url if (i == 0 and image_url) else None
        
        post_id = _post_container(text, reply_to=reply_to, image_url=img)
        if not post_id:
            print(f"  [ERR] Failed at slide {i+1}, stopping chain")
            break
        
        post_ids.append(post_id)
        reply_to = post_id
        print(f"  ✓ Posted: {post_id}")
        
        # Rate limit: wait between posts
        if i < len(slides) - 1:
            time.sleep(3)
    
    return post_ids


def post_from_db(limit: int = 1, dry_run: bool = False):
    """Post staged posts from pipeline.db."""
    from db import get_db, get_staged_posts, mark_posted
    
    conn = get_db()
    
    # Get staged posts with article image
    rows = conn.execute('''
        SELECT p.*, a.image as article_image, a.url as article_url
        FROM posts p
        JOIN articles a ON p.article_id = a.id
        WHERE p.status = 'staged'
        ORDER BY p.created_at DESC
        LIMIT ?
    ''', (limit,)).fetchall()
    
    if not rows:
        print("No staged posts.")
        return
    
    for post in rows:
        post = dict(post)
        print(f"\nPosting: {post.get('title', 'Untitled')[:60]}...")
        
        # Build slides from DB columns
        slides = []
        for key in ['slide_hook', 'slide_setup', 'slide_twist', 'slide_deep', 'slide_sowhat', 'slide_cta']:
            val = post.get(key, '')
            if val:
                slides.append(val)
        
        # Auto-append article URL to last slide (CTA) — always from DB, never hardcoded
        article_url = (post.get('article_url') or '').strip()
        if slides and article_url:
            # Strip any existing URLs in last slide to prevent mismatch
            import re as _re
            slides[-1] = _re.sub(r'https?://\S+', '', slides[-1]).rstrip()
            slides[-1] = slides[-1].rstrip() + '\n\n' + article_url
        
        if not slides:
            print("  [SKIP] No slides")
            continue
        
        image_url = post.get('article_image')
        
        # If no image stored, try re-fetching og:image from article URL
        if not image_url and post.get('article_url'):
            image_url = _fetch_og_image(post['article_url'])
            if image_url:
                # Update DB so we don't re-fetch next time
                conn.execute("UPDATE articles SET image=? WHERE id=(SELECT article_id FROM posts WHERE id=?)", (image_url, post['id']))
                conn.commit()
        
        # Wajib image untuk slide 1 — skip kalau gak ada
        if not image_url:
            print(f"  [SKIP] No image for slide 1: {post.get('title', 'Untitled')[:50]}")
            continue
        
        print(f"  {len(slides)} slides, image: {image_url[:60] if image_url else 'none'}")
        
        if dry_run:
            for i, s in enumerate(slides, 1):
                print(f"  [{i}] {s[:80]}")
            continue
        
        post_ids = post_carousel(slides, image_url=image_url)
        if post_ids and len(post_ids) == len(slides):
            mark_posted(conn, post['id'], post_ids[0])
            print(f"  ✓ Thread root: {post_ids[0]}")
        elif post_ids:
            # ROLLBACK: delete partial posts from Threads
            print(f"  ⚠ Partial post: {len(post_ids)}/{len(slides)} slides — rolling back")
            for pid in post_ids:
                try:
                    r = httpx.delete(f"{GRAPH}/{pid}", params={"access_token": TOKEN}, timeout=15)
                    print(f"    Rollback {pid}: {r.status_code}")
                except Exception as e:
                    print(f"    [ERR] Rollback {pid} failed: {e}")
            print(f"  ✗ Rolled back {len(post_ids)} partial slides")
        else:
            print("  ✗ Failed")
    
    conn.close()


def fetch_engagement(media_id: str) -> dict | None:
    """Fetch engagement metrics for a Threads post."""
    if not TOKEN:
        return None
    try:
        # Threads API: get insights for a media container
        r = httpx.get(
            f"{GRAPH}/{media_id}",
            params={
                "fields": "insights.metric(likes,replies,reposts,views)",
                "access_token": TOKEN,
            },
            timeout=15
        )
        if r.status_code != 200:
            _check_auth(r)
            print(f"  [ERR] Engagement fetch failed: {r.status_code}")
            return None
        
        data = r.json()
        insights = data.get("insights", {}).get("data", [])
        
        metrics = {}
        for item in insights:
            name = item.get("name", "")
            values = item.get("values", [])
            value = values[0].get("value") if values else None
            metrics[name] = value  # None if API didn't return it
        
        return {
            "likes": metrics.get("likes") or 0,
            "replies": metrics.get("replies") or 0,
            "reposts": metrics.get("reposts") or 0,
            "views": metrics.get("views"),  # None if not available — don't default to 0
        }
    except Exception as e:
        print(f"  [ERR] Engagement fetch error: {e}")
        return None


def track_engagement(limit: int = 10) -> dict:
    """Fetch engagement for recent posted posts and store in DB."""
    from db import get_db
    
    conn = get_db()
    
    # Get recent posted posts with thread_post_id
    rows = conn.execute('''
        SELECT p.id, p.thread_post_id, p.slide_hook
        FROM posts p
        WHERE p.status = 'posted' AND p.thread_post_id IS NOT NULL
        ORDER BY p.posted_at DESC
        LIMIT ?
    ''', (limit,)).fetchall()
    
    tracked = 0
    total_likes = 0
    total_views = 0
    
    for row in rows:
        post_id = row['id']
        media_id = row['thread_post_id']
        
        metrics = fetch_engagement(media_id)
        if not metrics:
            continue
        
        # Store in performance table
        conn.execute('''
            INSERT INTO performance (post_id, likes, replies, reposts, views)
            VALUES (?, ?, ?, ?, ?)
        ''', (post_id, metrics['likes'], metrics['replies'], 
              metrics['reposts'], metrics['views']))
        
        tracked += 1
        total_likes += metrics['likes']
        total_views += metrics['views']
        
        hook = row['slide_hook'][:40] if row['slide_hook'] else '...'
        print(f"  [{post_id}] {hook} | 👁 {metrics['views']} ❤️ {metrics['likes']} 🔄 {metrics['reposts']}")
    
    conn.commit()
    conn.close()
    
    return {"tracked": tracked, "total_likes": total_likes, "total_views": total_views}


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    track = "--track" in sys.argv
    
    if track:
        track_engagement()
    else:
        post_from_db(dry_run=dry)
