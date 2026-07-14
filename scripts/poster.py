#!/usr/bin/env python3
"""
poster.py — Threads posting for TechBro pipeline.

Thin wrapper around the shared threads_poster.py library.
All Threads API calls delegate to the shared lib.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Load shared library
# ---------------------------------------------------------------------------
_sys_path_save = sys.path.copy()
sys.path.insert(0, str(Path.home() / ".hermes" / "scripts"))
from threads_poster import ThreadsPoster  # noqa: E402

sys.path = _sys_path_save

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
_ENV_PATH = Path.home() / "threads-agent" / ".env"


def _load_env() -> None:
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
USER_ID = os.environ.get("THREADS_USER_ID", "")


def _get_poster() -> ThreadsPoster:
    return ThreadsPoster(access_token=TOKEN, user_id=USER_ID)


# ======================================================================
# Public API (matches old poster.py surface area)
# ======================================================================


def post_single(text: str, image_url: Optional[str] = None) -> Optional[str]:
    """Create + publish a single post.  Returns post_id or None."""
    if not TOKEN or not USER_ID:
        print("ERROR: THREADS credentials not set")
        return None
    try:
        return _get_poster().post_single(text, image_url=image_url, fetch_permalink=False)
    except Exception as e:
        print(f"  [ERR] post_single failed: {e}")
        return None


def post_thread(
    slides: list[str],
    image_url: Optional[str] = None,
    mode: str = "chain",
    image_fallback: bool = False,
) -> list[dict]:
    """Post slides as a thread chain or carousel.

    Returns list of {text, post_id, permalink}.
    """
    if not TOKEN or not USER_ID:
        print("ERROR: THREADS credentials not set")
        return []

    if not slides:
        return []

    images = [image_url] + [None] * (len(slides) - 1) if image_url else None

    try:
        results = _get_poster().post_thread(
            slides,
            image_urls=images,
            mode=mode,
            image_fallback=image_fallback,
            fetch_permalink=True,
            stop_on_error=False,
        )
        return [
            {"text": r.text, "post_id": r.post_id, "permalink": r.permalink}
            for r in results
        ]
    except Exception as e:
        print(f"  [ERR] post_thread failed: {e}")
        return []


def post_carousel(
    slides: list[str],
    image_url: Optional[str] = None,
    image_fallback: bool = True,
) -> list[dict]:
    """Post slides as carousel fan-out (all reply to root)."""
    return post_thread(
        slides, image_url=image_url, mode="carousel", image_fallback=image_fallback
    )


def _post_container(text: str, image_url: Optional[str] = None) -> Optional[str]:
    """Backward-compat alias for post_single.  Returns post_id (not creation_id).

    gen_mindset_post.py uses this.
    """
    return post_single(text, image_url=image_url)


def get_metrics(post_id: str) -> Optional[dict]:
    """Pull engagement metrics for a Threads post.

    Returns {views, likes, replies, shares} or None.
    """
    if not TOKEN:
        return None
    try:
        return _get_poster().get_metrics(post_id)
    except Exception:
        return None


def pull_engagement(conn, max_per_run: int = 10) -> int:
    """Pull metrics for posts >1h old that haven't been tracked. Returns count updated."""
    import time as _time

    cutoff = _time.time() - 3600  # 1 hour

    rows = conn.execute(
        """
        SELECT id, thread_post_id, posted_at
        FROM posts
        WHERE status='posted'
          AND thread_post_id IS NOT NULL
        ORDER BY id DESC
        LIMIT 50
    """
    ).fetchall()

    updated = 0
    processed = 0
    for row in rows:
        if processed >= max_per_run:
            break
        if row["posted_at"]:
            try:
                from datetime import datetime

                pt = datetime.fromisoformat(row["posted_at"]).timestamp()
                if pt > cutoff:
                    continue
            except Exception:
                continue

        metrics = get_metrics(row["thread_post_id"])
        processed += 1
        if metrics:
            conn.execute(
                """
                INSERT OR REPLACE INTO performance (post_id, likes, replies, reposts, views)
                VALUES (?, ?, ?, ?, ?)
            """,
                (row["id"], metrics["likes"], metrics["replies"], metrics["shares"], metrics["views"]),
            )
            conn.execute(
                """
                UPDATE posts SET views=?, likes=?, replies=?, shares=?
                WHERE id=?
            """,
                (metrics["views"], metrics["likes"], metrics["replies"], metrics["shares"], row["id"]),
            )
            updated += 1
        _time.sleep(0.3)

    if updated:
        conn.commit()
    return updated


def post_from_db(limit: int = 1, dry_run: bool = False) -> None:
    """Post staged posts from pipeline.db."""
    from db import get_db, get_staged_posts, mark_posted

    conn = get_db()
    staged = get_staged_posts(conn, limit)
    if not staged:
        print("No staged posts.")
        return

    for post in staged:
        print(f"\nPosting: {post['title'][:60]}...")

        p = dict(post)

        slides = []
        for key in [
            "slide_hook",
            "slide_setup",
            "slide_twist",
            "slide_deep",
            "slide_sowhat",
            "slide_cta",
        ]:
            val = p.get(key, "")
            if val:
                slides.append(val)

        caption = p.get("caption", "")

        if not slides and caption:
            if "\n---\n" in caption:
                slides = [s.strip() for s in caption.split("\n---\n") if s.strip()]
                print(f"  Thread chain from caption: {len(slides)} slides")
            else:
                slides = [caption]
                print("  Single post from caption")

        if not slides:
            print("  [SKIP] No slides")
            continue

        if dry_run:
            for i, s in enumerate(slides, 1):
                print(f"  [{i}] {s[:80]}")
            continue

        results = post_thread(slides, image_url=p.get("image"))
        if results:
            mark_posted(conn, post["id"], results[0]["post_id"])
            print(f"  ✓ Thread root: {results[0]['post_id']}")
        else:
            print("  ✗ Failed")

    conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    post_from_db(dry_run=dry)
