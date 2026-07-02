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

from scraper import scrape_all, score_article, fast_content_filter
from generator import generate_carousel
from db import get_db, upsert_article, stage_post, get_stats, mark_failed, cleanup_old
from poster import post_from_db

TOP_N = 5  # articles per run (pick best unposted)

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

# Known entities that indicate same topic when combined
_ENTITIES = {
    "openai", "anthropic", "google", "meta", "microsoft", "apple", "nvidia",
    "amazon", "tesla", "deepseek", "mistral", "cohere", "claude", "gpt",
    "gemini", "chatgpt", "copilot", "cursor", "midjourney", "sora",
    "sonnet", "opus", "mythos", "fable", "lama", "llama",
    # Product names
    "godot", "github", "apple", "iphone", "ipad", "macbook", "vision",
    "spacex", "starlink", "openai", "claude", "science", "agent",
}

def _extract_topic(title: str) -> set[str]:
    """Extract entity/topic keywords from title."""
    words = set(_normalize_title(title).split())
    return words & _ENTITIES

def _is_same_topic(title1: str, title2: str) -> bool:
    """Check if two titles are about the same topic (entity + action)."""
    topic1 = _extract_topic(title1)
    topic2 = _extract_topic(title2)
    if not topic1 or not topic2:
        return False
    overlap = topic1 & topic2
    if not overlap:
        return False
    # If 2+ entities match (e.g. "claude" + "science"), same topic
    if len(overlap) >= 2:
        return True
    # Single entity: check action similarity
    stop = {"the", "a", "an", "is", "to", "for", "and", "of", "in", "its", "on", "at", "by"}
    w1 = set(_normalize_title(title1).split()) - _ENTITIES - stop
    w2 = set(_normalize_title(title2).split()) - _ENTITIES - stop
    action_overlap = len(w1 & w2) / max(len(w1 | w2), 1)
    return action_overlap > 0.3

DAILY_POST_LIMIT = 20
POSTING_HOURS = (7, 23)  # WIB — only post between 07:00-22:00

def run(top_n: int = TOP_N, dry_run: bool = False):
    t0 = time.time()
    conn = get_db()
    staged_this_run = False

    # 0. Posting hours check (WIB = UTC+7)
    from datetime import datetime, timezone, timedelta
    now_wib = datetime.now(timezone(timedelta(hours=7)))
    current_hour = now_wib.hour
    if not (POSTING_HOURS[0] <= current_hour < POSTING_HOURS[1]) and not dry_run:
        print(f"Outside posting hours ({POSTING_HOURS[0]}:00-{POSTING_HOURS[1]}:00 WIB). Now: {current_hour}:00. Skipping.")
        conn.close()
        return
    print(f"[HOURS] {current_hour}:00 WIB — within posting window")

    # 1. Daily post limit check
    today_count = conn.execute(
        "SELECT COUNT(*) as c FROM posts WHERE date(posted_at)=date('now') AND status='posted'"
    ).fetchone()['c']
    if today_count >= DAILY_POST_LIMIT and not dry_run:
        print(f"Daily limit reached ({today_count}/{DAILY_POST_LIMIT}). Skipping.")
        conn.close()
        return
    print(f"[LIMIT] {today_count}/{DAILY_POST_LIMIT} posted today")

    # 1. Auto-clean old articles (>7 days)
    cleaned = cleanup_old(conn, days=7)
    if cleaned["deleted_articles"] > 0:
        print(f"[0/4] Cleaned {cleaned['deleted_articles']} old articles")

    # 1. Scrape + score
    print(f"[1/4] Scraping top {top_n} articles...")

    articles = scrape_all(top_n)

    # Track topics from ALL posts (posted + staged) for dedup
    posted_titles = [row['title'] for row in conn.execute(
        "SELECT a.title FROM posts p JOIN articles a ON p.article_id=a.id"
    ).fetchall()]

    # Track topics staged THIS run (prevents duplicates within same run)
    staged_titles_this_run = []

    if articles:

        # ── LAYER 2: Fast Dedup & Fast Drop ──────────────────────
        fresh = []
        for art in articles:
            # 2a. Content filter (exclude/penalty keywords)
            reject = fast_content_filter(art["title"], art["body"])
            if reject:
                print(f"  [DROP] {reject}: {art['title'][:50]}...")
                continue
            # 2b. DB dedup (title similarity + entity topic)
            skip = False
            for pt in posted_titles + staged_titles_this_run:
                if _title_overlap(art["title"], pt) > 0.5:
                    print(f"  [DEDUP] Similar title: {pt[:60]}...")
                    skip = True
                    break
                if _is_same_topic(art["title"], pt):
                    print(f"  [DEDUP] Same topic as: {pt[:60]}...")
                    skip = True
                    break
            if skip:
                continue
            fresh.append(art)

        # ── LAYER 3: Scoring Engine (keyword + decay) ────────────
        for art in fresh:
            art["score"] = score_article(art["title"], art["body"], art.get("date"))

        # ── LAYER 4: Cross-Source Virality ───────────────────────
        import re as _re
        topic_map: dict[str, list[dict]] = {}
        _stop = {"this", "that", "with", "from", "have", "been", "will", "more",
                 "than", "about", "just", "into", "your", "they", "their", "what",
                 "when", "which", "were", "also", "could", "would", "should",
                 "like", "very", "most", "some", "only"}
        for art in fresh:
            words = set(_re.findall(r'\b[a-z]{4,}\b', art["title"].lower())) - _stop
            matched = False
            for topic, group in topic_map.items():
                topic_words = set(topic.split())
                if words and topic_words and len(words & topic_words) / max(len(words | topic_words), 1) > 0.3:
                    group.append(art)
                    matched = True
                    break
            if not matched:
                topic_map[" ".join(sorted(words)[:5])] = [art]
        for topic, group in topic_map.items():
            if len(group) >= 2:
                sources = set(a["source"] for a in group)
                if len(sources) >= 2:
                    for a in group:
                        a["score"] = min(a["score"] + 30, 150)
                        a["virality"] = f"cross-source ({len(sources)} sources)"

        # Sort by score desc
        fresh.sort(key=lambda x: x.get("score", 0), reverse=True)

        for art in fresh:
            vir_tag = f" +{art.get('virality', '')}" if art.get("virality") else ""
            print(f"\n  [{art['source']}] score={art['score']}{vir_tag} | {art['title'][:60]}...")

            # 2. Upsert article to DB
            article_id = upsert_article(conn, art)
            print(f"  Article #{article_id} saved to DB")

            if dry_run:
                print("  [DRY RUN] Skipping generation")
                continue

            # 3. Generate carousel
            print(f"  [2/4] Generating carousel via LM...")
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
            staged_titles_this_run.append(art['title'])
            staged_this_run = True
            print(f"  [3/4] Post #{post_id} staged in DB")
            print(f"  Hook: {slides.get('slide_1', slides.get('hook', '?'))[:80]}")
            print(f"  CTA:  {slides.get('slide_6', slides.get('cta', '?'))[:80]}")

            # 4. Post to Threads immediately
            if not dry_run:
                print(f"\n[4/4] Posting to Threads...")
                post_from_db(limit=1)
            break  # one article per run
    else:
        print("  No fresh articles from scraper.")

    # Fallback: if nothing staged from fresh scrape, pick best unposted from DB
    if not staged_this_run:
        print("  Checking DB for unposted articles...")
        unposted = conn.execute("""
            SELECT a.id, a.title, a.body, a.url, a.image, a.score, a.source
            FROM articles a
            WHERE a.id NOT IN (SELECT article_id FROM posts WHERE status != 'failed')
            ORDER BY a.score DESC
            LIMIT ?
        """, (top_n,)).fetchall()

        if unposted:
            for art in unposted:
                art = dict(art)
                # Layer 2: Content filter + DB dedup
                reject = fast_content_filter(art['title'], art['body'])
                if reject:
                    print(f"  [DROP] {reject}: {art['title'][:50]}...")
                    continue

                skip = False
                for pt in posted_titles + staged_titles_this_run:
                    if _title_overlap(art['title'], pt) > 0.5:
                        print(f"  [DEDUP] Similar title: {pt[:60]}...")
                        skip = True
                        break
                    if _is_same_topic(art['title'], pt):
                        print(f"  [DEDUP] Same topic as: {pt[:60]}...")
                        skip = True
                        break
                if skip:
                    continue

                print(f"  [DB] score={art['score']} | {art['title'][:60]}")

                if dry_run:
                    print("  [DRY RUN] Skipping generation")
                    continue

                print(f"  [2/4] Generating carousel via LM...")
                slides = generate_carousel(art["title"], art["body"], art["image"] or "", art["url"] or "", art["source"] if "source" in art.keys() else "")
                if not slides:
                    print("  ERROR: LM generation failed")
                    mark_failed(conn, art["id"])
                    continue

                provider = slides.pop("_provider", "unknown")
                print(f"  Generated via {provider}")

                post_id = stage_post(conn, art["id"], slides, slides.get("caption", ""), slides.get("hashtags", ""))
                staged_titles_this_run.append(art["title"])
                staged_this_run = True
                print(f"  [3/4] Post #{post_id} staged in DB")
                print(f"  Hook: {slides.get('slide_1', slides.get('hook', '?'))[:80]}")
                print(f"  CTA:  {slides.get('slide_6', slides.get('cta', '?'))[:80]}")

                # 4. Post to Threads immediately
                if not dry_run:
                    print(f"\n[4/4] Posting to Threads...")
                    post_from_db(limit=1)
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
