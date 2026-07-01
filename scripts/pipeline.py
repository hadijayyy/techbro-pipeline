#!/usr/bin/env python3
"""
pipeline.py — Orchestrator: scrape → score → generate → stage in SQLite.
Run: python3 scripts/pipeline.py [--top N] [--dry-run]
"""
import sys
import time
import argparse
from pathlib import Path

# Load .env from project root
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            import os
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(Path(__file__).parent))

from scraper import scrape_all
from generator import generate_carousel
from db import get_db, upsert_article, stage_post, get_stats, mark_failed

TOP_N = 1  # articles per run

def _normalize_title(title: str) -> str:
    """Normalize title for dedup: lowercase, strip punctuation, collapse spaces."""
    import re
    t = re.sub(r'[^a-z0-9\s]', '', title.lower())
    return ' '.join(t.split())

def _title_overlap(t1: str, t2: str) -> float:
    """Jaccard similarity on word sets. >0.5 = likely same story."""
    w1 = set(_normalize_title(t1).split())
    w2 = set(_normalize_title(t2).split())
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / len(w1 | w2)

def run(top_n: int = TOP_N, dry_run: bool = False):
    t0 = time.time()
    conn = get_db()
    staged_this_run = False

    # 1. Scrape + score
    print(f"[1/3] Scraping top {top_n} articles...")
    articles = scrape_all(top_n)

    # Get already-posted/staged article titles for dedup
    posted_titles = [row['title'] for row in conn.execute(
        "SELECT a.title FROM posts p JOIN articles a ON p.article_id=a.id"
    ).fetchall()]

    if articles:
        print(f"  Found {len(articles)} articles")

        for art in articles:
            print(f"\n  [{art['source']}] score={art['score']} | {art['title'][:60]}...")

            # Dedup: skip if already posted/staged (title similarity)
            skip = False
            for pt in posted_titles:
                if _title_overlap(art['title'], pt) > 0.5:
                    print(f"  [DEDUP] Similar to already-posted: {pt[:60]}...")
                    skip = True
                    break
            if skip:
                continue

            # 2. Upsert article to DB
            article_id = upsert_article(conn, art)
            print(f"  Article #{article_id} saved to DB")

            if dry_run:
                print("  [DRY RUN] Skipping generation")
                continue

            # 3. Generate carousel
            print(f"  [2/3] Generating carousel via LM...")
            slides = generate_carousel(art["title"], art["body"], art["image"] or "", art["url"] or "", art["source"] if "source" in art.keys() else "")
            if not slides:
                print("  ERROR: LM generation failed")
                mark_failed(conn, article_id)
                continue

            provider = slides.pop("_provider", "unknown")
            print(f"  Generated via {provider}")

            # 4. Stage post
            post_id = stage_post(conn, article_id, slides, slides.get("caption", ""), slides.get("hashtags", ""))
            posted_titles.append(art['title'])
            staged_this_run = True
            print(f"  [3/3] Post #{post_id} staged in DB")
            print(f"  Hook: {slides.get('slide_1', slides.get('hook', '?'))[:80]}")
            print(f"  CTA:  {slides.get('slide_6', slides.get('cta', '?'))[:80]}")
            break  # one article per run
    else:
        print("  No fresh articles from scraper.")

    # Fallback: if nothing staged from fresh scrape, pick best unposted from DB
    if not staged_this_run:
        print("  Checking DB for unposted articles...")
        unposted = conn.execute("""
            SELECT a.id, a.title, a.body, a.url, a.image, a.score
            FROM articles a
            WHERE a.id NOT IN (SELECT article_id FROM posts WHERE status != 'failed')
            ORDER BY a.score DESC
            LIMIT ?
        """, (top_n,)).fetchall()

        if unposted:
            for art in unposted:
                print(f"  [DB] score={art['score']} | {art['title'][:60]}")

                if dry_run:
                    print("  [DRY RUN] Skipping generation")
                    continue

                print(f"  [2/3] Generating carousel via LM...")
                slides = generate_carousel(art["title"], art["body"], art["image"] or "", art["url"] or "", art["source"] if "source" in art.keys() else "")
                if not slides:
                    print("  ERROR: LM generation failed")
                    mark_failed(conn, art["id"])
                    continue

                provider = slides.pop("_provider", "unknown")
                print(f"  Generated via {provider}")

                post_id = stage_post(conn, art["id"], slides, slides.get("caption", ""), slides.get("hashtags", ""))
                staged_this_run = True
                print(f"  [3/3] Post #{post_id} staged in DB")
                print(f"  Hook: {slides.get('slide_1', slides.get('hook', '?'))[:80]}")
                print(f"  CTA:  {slides.get('slide_6', slides.get('cta', '?'))[:80]}")
                break  # one article per run
        else:
            print("  No unposted articles in DB.")

    stats = get_stats(conn)
    elapsed = time.time() - t0
    print(f"\n{'='*50}")
    print(f"Done in {elapsed:.1f}s")
    print(f"DB: {stats['articles']} articles | {stats['staged']} staged | {stats['posted']} posted")
    if not staged_this_run:
        print("No posts staged this run.")
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=TOP_N)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args.top, args.dry_run)
