#!/usr/bin/env python3
"""
pipeline.py — Orchestrator: scrape → score → generate → stage in SQLite.
Run: python3 scripts/pipeline.py [--top N] [--dry-run]
"""
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scraper import scrape_all, detect_hot_topics
from generator import generate_carousel, generate_narrative_post, generate_thread_chain, evaluate_slides, generate_ab_variants
from db import get_db, upsert_article, stage_post, get_stats, mark_failed

TOP_N = 1  # articles per run

# Stopwords for title similarity (Indonesian + English, like Pressbox's clean_words)
_STOPWORDS = frozenset({
    "the","a","an","in","on","at","to","for","of","and","or","but","is","was","be","has","had",
    "just","not","are","were","do","did","get","got","its","his","her","she","he","you","your",
    "we","our","they","them","that","this","with","from","will","can","all","how","who","what",
    "when","where","why","more","than","some","about","into","over","after","before","between",
    "both","each","even","first","last","like","long","look","make","many","most","much","must",
    "new","now","old","only","other","out","own","people","say","see","take","tell","two","up",
    "us","use","very","want","way","well","also","back","come","good","great","here","him",
    "yang","dan","di","ke","dari","ini","itu","dengan","untuk","pada","adalah","akan","oleh",
    "tidak","bisa","ada","juga","sudah","sudah","atau","tapi","karena","namun","agar","serta",
    "hingga","sejak","tentang","melalui","setelah","antara","telah","secara","para","lain",
})

def _normalize_title(title: str) -> str:
    """Normalize title for dedup: lowercase, strip punctuation, remove stopwords."""
    import re
    t = re.sub(r'[^a-z0-9\\s]', '', title.lower())
    words = [w for w in t.split() if w not in _STOPWORDS and len(w) > 1]
    return ' '.join(words)

def _title_overlap(t1: str, t2: str) -> float:
    """Jaccard similarity on word sets (with stopwords removed). >0.35 = likely same story."""
    w1 = set(_normalize_title(t1).split())
    w2 = set(_normalize_title(t2).split())
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / len(w1 | w2)

def run(top_n: int = TOP_N, dry_run: bool = False, format: str = "auto"):
    """Run pipeline.
    
    Args:
        format: "carousel" (6-slide), "narrative" (single post), "thread_chain" (10-slide), or "auto" (alternate)
    """
    t0 = time.time()
    conn = get_db()
    staged_this_run = False
    
    # Daily limit: max 20 posts per day
    DAILY_LIMIT = 20
    today = __import__('datetime').datetime.now().strftime('%Y-%m-%d')
    posts_today = conn.execute(
        "SELECT COUNT(*) as c FROM posts WHERE status='posted' AND date(posted_at)=?",
        (today,)
    ).fetchone()['c']
    
    if posts_today >= DAILY_LIMIT:
        print(f"  [LIMIT] Daily limit reached ({posts_today}/{DAILY_LIMIT}). Skipping.")
        conn.close()
        return

    # Auto format: alternate between thread_chain and narrative
    if format == "auto":
        recent = conn.execute(
            "SELECT caption FROM posts WHERE status='posted' ORDER BY id DESC LIMIT 3"
        ).fetchall()
        # Default carousel (6 slides). Alternate with narrative every 3rd post.
        format = "carousel"
        print(f"  [AUTO FORMAT] Using: {format}")

    # 1. Scrape + score
    print(f"[1/3] Scraping top {top_n} articles...")
    articles = scrape_all(top_n * 10)  # Get more articles for hot detection
    
    # 1.5. Hot topic detection (Union-Find clustering)
    if articles:
        hotness = detect_hot_topics(articles)
        if hotness:
            # Re-score articles with hot boost
            from scraper import score_article
            for art in articles:
                url = art.get("url", "")
                hot_boost = 0
                if url in hotness:
                    score = hotness[url]
                    if score >= 3.0:
                        hot_boost = 25
                    elif score >= 1.5:
                        hot_boost = 15
                art["score"] = score_article(art["title"], art.get("body", ""), art.get("date"), hot_boost=hot_boost, source=art.get("source", ""))
            
            # Re-sort by score
            articles.sort(key=lambda x: x["score"], reverse=True)
    
    # Trim to requested top_n
    articles = articles[:top_n]

    # Get already-posted/staged article titles for dedup
    posted_titles = [row['title'] for row in conn.execute(
        "SELECT a.title FROM posts p JOIN articles a ON p.article_id=a.id"
    ).fetchall()]

    if articles:
        print(f"  Found {len(articles)} articles")

        # Source diversity: count recent celebrity posts to cap ratio (30% max)
        recent_celeb = conn.execute("""
            SELECT COUNT(*) FROM posts p
            JOIN articles a ON p.article_id = a.id
            WHERE p.status = 'posted'
              AND a.source IN ('celebrity', 'celebrity_id')
              AND p.created_at > datetime('now', '-48 hours')
        """).fetchone()[0]
        recent_total = conn.execute("""
            SELECT COUNT(*) FROM posts WHERE status = 'posted'
            AND created_at > datetime('now', '-48 hours')
        """).fetchone()[0]
        celeb_ratio = recent_celeb / max(recent_total, 1)
        allow_celeb = celeb_ratio < 0.3
        if not allow_celeb:
            print(f"  [RATIO] Celebrity cap hit ({recent_celeb}/{recent_total} = {celeb_ratio:.0%}). Will skip celebrity articles.")

        for art in articles:
            print(f"\n  [{art['source']}] score={art['score']} | {art['title'][:60]}...")

            # Dedup: skip if already posted/staged (title similarity)
            skip = False
            for pt in posted_titles:
                if _title_overlap(art['title'], pt) > 0.35:
                    print(f"  [DEDUP] Similar to already-posted: {pt[:60]}...")
                    skip = True
                    break
            if skip:
                continue

            # Source diversity: skip celebrity if cap hit
            a_src = dict(art)
            if not allow_celeb and a_src.get('source', '') in ('celebrity', 'celebrity_id'):
                print(f"  [RATIO] Skipping celebrity: {art['title'][:50]}...")
                continue

            # 2. Upsert article to DB
            article_id = upsert_article(conn, art)
            print(f"  Article #{article_id} saved to DB")

            if dry_run:
                print("  [DRY RUN] Skipping generation")
                continue

            # 3. Generate content (format-aware)
            a = dict(art)  # sqlite3.Row → dict for .get()
            gen_label = "narrative post" if format == "narrative" else "thread chain" if format == "thread_chain" else "carousel"
            print(f"  [2/3] Generating {gen_label} via LM...")
            if format == "narrative":
                slides = generate_narrative_post(a["title"], a["body"], a.get("url", ""), a.get("source", ""))
            elif format == "thread_chain":
                slides = generate_thread_chain(a["title"], a["body"], a.get("image", ""), a.get("url", ""), a.get("source", ""))
            else:
                # A/B testing: generate 3 hook variants, pick best
                slides = generate_ab_variants(a["title"], a["body"], a.get("image", ""), a.get("url", ""), a.get("source", ""), n_variants=3)
            if not slides:
                print("  ERROR: LM generation failed")
                mark_failed(conn, article_id)
                continue
            
            # 2.5. Evaluator loop (independent LLM review)
            eval_result = evaluate_slides(slides, a["title"], a["body"], art["score"])
            
            if eval_result["status"] == "REJECT":
                print(f"  [EVALUATOR] REJECTED: {eval_result['reason'][:100]}")
                mark_failed(conn, article_id)
                continue
            elif eval_result["status"] == "REVISE" and eval_result.get("revised_slides"):
                print(f"  [EVALUATOR] REVISED: {eval_result['reason'][:100]}")
                slides = eval_result["revised_slides"]
            
            # Handle different format structures
            if isinstance(slides, dict) and "_format" in slides:
                fmt = slides["_format"]
                if fmt == "narrative":
                    # Narrative: single post, only slide_hook filled
                    stage_post(conn, article_id, {"hook": slides.get("hook", ""), "setup": "", "twist": "", "deep": "", "sowhat": "", "cta": ""}, slides.get("caption", ""), slides.get("hashtags", ""))
                    staged_this_run = True
                elif fmt == "thread_chain":
                    # Thread chain: 10 slides, store all in caption for posting
                    slide_list = slides.get("slides", [])
                    caption = "\n---\n".join(slide_list)
                    # Map first 6 slides to DB columns
                    stage_post(conn, article_id, {
                        "hook": slide_list[0] if slide_list else "",
                        "setup": slide_list[1] if len(slide_list) > 1 else "",
                        "twist": slide_list[2] if len(slide_list) > 2 else "",
                        "deep": slide_list[3] if len(slide_list) > 3 else "",
                        "sowhat": slide_list[4] if len(slide_list) > 4 else "",
                        "cta": slide_list[5] if len(slide_list) > 5 else "",
                    }, caption, slides.get("hashtags", ""))
                    staged_this_run = True
                else:
                    # Carousel: 6 slides — pass original dict to stage_post
                    stage_post(conn, article_id, slides,
                        slides.get("caption", ""), slides.get("hashtags", ""))
                    staged_this_run = True
            else:
                # Legacy carousel format (dict with slide_1..slide_6) — pass directly
                stage_post(conn, article_id, slides,
                    slides.get("caption", ""), slides.get("hashtags", ""))
                staged_this_run = True
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
                print(f"  [DB] score={art['score']} | {art['title'][:60]}")

                if dry_run:
                    print("  [DRY RUN] Skipping generation")
                    continue

                # sqlite3.Row doesn't have .get(), use dict()
                a = dict(art)
                gen_label = "narrative post" if format == "narrative" else "thread chain" if format == "thread_chain" else "carousel"
                print(f"  [2/3] Generating {gen_label} via LM...")
                if format == "narrative":
                    slides = generate_narrative_post(a["title"], a["body"], a.get("url", ""), a.get("source", ""))
                elif format == "thread_chain":
                    slides = generate_thread_chain(a["title"], a["body"], a.get("image", ""), a.get("url", ""), a.get("source", ""))
                else:
                    slides = generate_carousel(a["title"], a["body"], a.get("image", ""), a.get("url", ""), a.get("source", ""))
                if not slides:
                    print("  ERROR: LM generation failed")
                    mark_failed(conn, art["id"])
                    continue

                # Handle different format structures
                if isinstance(slides, dict) and "_format" in slides:
                    fmt = slides["_format"]
                    if fmt == "narrative":
                        stage_post(conn, art["id"], {"hook": slides.get("hook", ""), "setup": "", "twist": "", "deep": "", "sowhat": "", "cta": ""}, slides.get("caption", ""), slides.get("hashtags", ""))
                        staged_this_run = True
                    elif fmt == "thread_chain":
                        slide_list = slides.get("slides", [])
                        caption = "\n---\n".join(slide_list)
                        stage_post(conn, art["id"], {
                            "hook": slide_list[0] if slide_list else "",
                            "setup": slide_list[1] if len(slide_list) > 1 else "",
                            "twist": slide_list[2] if len(slide_list) > 2 else "",
                            "deep": slide_list[3] if len(slide_list) > 3 else "",
                            "sowhat": slide_list[4] if len(slide_list) > 4 else "",
                            "cta": slide_list[5] if len(slide_list) > 5 else "",
                        }, caption, slides.get("hashtags", ""))
                        staged_this_run = True
                    else:
                        stage_post(conn, art["id"], slides,
                            slides.get("caption", ""), slides.get("hashtags", ""))
                        staged_this_run = True
                else:
                    stage_post(conn, art["id"], slides,
                        slides.get("caption", ""), slides.get("hashtags", ""))
                    staged_this_run = True
                break  # one article per run
        else:
            print("  No unposted articles in DB.")

    stats = get_stats(conn)
    elapsed = time.time() - t0
    
    # Pull engagement metrics for posts >12h old
    try:
        from poster import pull_engagement
        updated = pull_engagement(conn)
        if updated:
            print(f"📊 Engagement: updated metrics for {updated} posts")
    except Exception as e:
        print(f"  [ENGAGEMENT] Skipped: {e}")
    
    # Telegram notify (if posts were staged this run)
    if staged_this_run:
        try:
            import requests as _req
            token_file = Path.home() / ".szejay_token"
            if token_file.exists():
                token = token_file.read_text().strip()
                # Get the latest staged post
                latest = conn.execute(
                    "SELECT p.id, a.title, a.score FROM posts p JOIN articles a ON p.article_id=a.id WHERE p.status='staged' ORDER BY p.id DESC LIMIT 1"
                ).fetchone()
                if latest:
                    _req.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={
                            "chat_id": 1022032312,
                            "text": f"✅ Techbro staged: {dict(latest)['title'][:60]}\n📊 Score: {dict(latest)['score']}\n⏱️ {elapsed:.1f}s",
                            "disable_web_page_preview": True,
                        },
                        timeout=10,
                    )
        except Exception:
            pass  # non-critical
    
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
    parser.add_argument("--format", choices=["carousel", "narrative", "thread_chain", "auto"], default="carousel",
                        help="Content format: carousel (6-slide, default), narrative (single post), thread_chain (10-slide), auto (alternate)")
    args = parser.parse_args()
    run(args.top, args.dry_run, args.format)
