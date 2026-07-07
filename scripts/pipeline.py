#!/usr/bin/env python3
"""
pipeline.py — Orchestrator: scrape → score → generate → stage in SQLite.
Run: python3 scripts/pipeline.py [--top N] [--dry-run]
"""
import sys
import re
import time
import argparse
from collections import Counter
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

from scraper import scrape_all, score_article, fast_content_filter, check_article_quality, SOURCE_NAMES
from generator import generate_carousel
from db import get_db, upsert_article, stage_post, get_stats, mark_failed, cleanup_old
from poster import post_from_db
from trending import score_article_drama, detect_dramas

TOP_N = 50  # articles per run (pick best unposted from larger pool)
DRAMA_BOOST = 30  # bonus points for drama articles

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
    stop = {"the", "a", "an", "is", "to", "for", "and", "of", "in", "its", "on", "at", "by",
            "yang", "di", "dan", "ini", "itu", "dengan", "untuk", "pada", "dari", "ke",
            "adalah", "juga", "sudah", "masih", "belum", "akan", "bisa", "tidak",
            "gak", "bukan", "lebih", "paling", "sangat", "atau", "tapi", "namun"}
    w1 = set(_normalize_title(title1).split()) - _ENTITIES - stop
    w2 = set(_normalize_title(title2).split()) - _ENTITIES - stop
    action_overlap = len(w1 & w2) / max(len(w1 | w2), 1)
    return action_overlap > 0.5  # raised from 0.3 — too many false positives

DAILY_POST_LIMIT = 20
POSTING_HOURS = (7, 23)  # WIB — only post between 07:00-23:00
ALLOWED_SOURCES = set(SOURCE_NAMES)

def _check_relatability(title: str, body_excerpt: str) -> int:
    """Rate how relatable article is for Indonesian audience (1-5).
    Uses cheap Mistral call. Returns score 1-5."""
    import os, httpx
    key = os.environ.get("MISTRAL_API_KEY", "")
    if not key:
        print("  [RELATE] No MISTRAL_API_KEY, skipping")
        return 3  # pass through if no key

    prompt = """Rate how relatable this tech/AI news is for young Indonesian professionals (age 22-35).
Score 1-5:
1 = Super niche (Linux kernel, dev tools, startup funding) — 99% don't care
2 = Somewhat niche (cloud infra, specific API, enterprise B2B) — 90% don't care
3 = Moderately relatable (new tech product, industry trend) — 50% might care
4 = Very relatable (scam alert, AI affecting jobs, money saving, productivity) — 70%+ care
5 = Extremely relatable (government policy affecting everyone, viral tech drama, free tools) — 90%+ care

Consider: Does this directly affect daily life, money, job, or safety of someone in Indonesia?

Respond with ONLY a single digit (1-5), nothing else."""

    try:
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "mistral-small-latest",
                  "messages": [
                      {"role": "system", "content": prompt},
                      {"role": "user", "content": f"Title: {title}\nExcerpt: {body_excerpt}"}
                  ],
                  "temperature": 0.1, "max_tokens": 5},
            timeout=15)
        if r.status_code == 200:
            raw = r.json()["choices"][0]["message"]["content"].strip()
            # Extract digit from response
            digit = re.search(r'[1-5]', raw)
            if digit:
                return int(digit.group())
    except Exception as e:
        print(f"  [RELATE] Error: {e}")
    return 3  # default pass on error


_TOPIC_CATEGORIES = {
    # High-engagement categories (from analytics)
    "phk_layoff": ["phk", "karyawan", "layoff", "pecat", "dirumahkan", "pemutusan"],
    "ojol_ridehail": ["gojek", "grab", "ojol", "driver", "mitra", "ridehail"],
    "ecommerce_reg": ["e-commerce", "tiktok shop", "tokopedia", "shopee", "marketplace", "pajak", "regulasi"],
    "emas_gold": ["emas", "gold", "batangan", "pegadaian", "antam"],
    "apple": ["apple", "iphone", "ipad", "macbook", "ios"],
    "bigtech_meta": ["meta", "facebook", "whatsapp", "instagram", "threads"],
    "bigtech_google": ["google", "android", "chrome", "youtube", "gemini"],
    "bigtech_microsoft": ["microsoft", "windows", "copilot", "bing"],
    "ai_tech": ["openai", "chatgpt", "ai ", "artificial", "machine learning", "deepfake"],
    "indo_gov": ["kominfo", "pemerintah", "kemenkominfo", "ri ", "indonesia", "presiden"],
    "indo_local": ["asn", "pns", "pegawai", "bpjs", "pajak", "npwp", "ktp", "nik", "ojk", "komdigi"],
    # Low-engagement categories (from analytics)
    "foreign_news": ["nhs", "amerika serikat", "us government", "uk government", "eu ", "eropa", "jepang", "korea selatan", "inggris"],
    "niche_ngo": ["umkm", "ngo", "kol", "influencer", "creator"],
    "apple_mac": ["macbook", "mac ", "macos", "safari", "apple silicon"],
    "niche_devtools": ["github", "copilot", "claude code", "vs code", "cursor"],
    "infra_telco": ["telkom", "telkomsel", "5g", "bts", "fiber", "infrastruktur"],
    "crypto_web3": ["crypto", "bitcoin", "blockchain", "web3", "token", "nft"],
}

_EDUCATION_KEYWORDS = [
    "tips", "cara", "panduan", "tutorial", "langkah", "belajar", "edukasi",
    "investasi", "saham", "emas", "nabung", "dana darurat",
    "scam", "penipuan", "keamanan", "privacy", "password",
    "produktivitas", "skill", "cv", "resume", "gaji", "negosiasi", "side hustle",
    "ai", "chatgpt", "prompt", "otomatisasi", "digital",
    "strategi", "rahasia", "kesalahan", "mistake", "peluang",
]

_STOPWORDS = {"yang", "dan", "ini", "itu", "dengan", "untuk", "dari", "pada", "adalah",
              "akan", "oleh", "juga", "telah", "sudah", "ada", "tidak", "bisa", "lebih",
              "baru", "lagi", "bukan", "dalam", "tersebut", "karena", "namun", "gak",
              "gue", "lo", "lu", "dong", "sih", "nih", "aja", "deh", "kok", "banget",
              "kayak", "kalau", "pas", "kan", "terus", "trus", "biar", "atau", "mau",
              "the", "and", "for", "that", "this", "with", "from", "have", "been", "just"}

def _classify_topic(title: str, body: str) -> list[str]:
    """Classify article into topic categories."""
    text = (title + " " + body[:500]).lower()
    cats = []
    for cat, keywords in _TOPIC_CATEGORIES.items():
        if any(kw in text for kw in keywords):
            cats.append(cat)
    return cats if cats else ["other"]


def _pull_analytics_feedback(conn) -> dict:
    """Pull engagement metrics and compute category-level boosts/penalties.
    Returns: {'hook_boosts': {keyword: +pts}, 'topic_penalties': {keyword: -pts},
              'cat_boosts': {category: +pts}, 'cat_penalties': {category: -pts},
              'median_views': int}
    Uses topic CATEGORIES instead of individual words to avoid noise.
    """
    from poster import fetch_engagement, track_engagement
    result = {"hook_boosts": {}, "topic_penalties": {}, "cat_boosts": {}, "cat_penalties": {}, "median_views": 0}

    # Auto-track engagement for posted posts missing metrics (>12h old)
    try:
        tracked = track_engagement(limit=20)
        if tracked.get("updated", 0) > 0:
            print(f"  [ANALYTICS] Updated {tracked['updated']} posts with engagement data")
    except Exception as e:
        print(f"  [ANALYTICS] Track error (non-blocking): {e}")

    # Get posts with performance data — only posts >24h old (new ones have low views)
    rows = conn.execute("""
        SELECT p.slide_hook, p.slide_cta, a.title, a.source, a.body,
               perf.views, perf.likes, perf.replies, perf.reposts
        FROM posts p
        JOIN articles a ON p.article_id = a.id
        JOIN performance perf ON perf.post_id = p.id
        WHERE p.status = 'posted'
          AND p.posted_at < datetime('now', '-24 hours')
        ORDER BY p.posted_at DESC
        LIMIT 30
    """).fetchall()

    if len(rows) < 5:
        print(f"  [ANALYTICS] Only {len(rows)} posts with metrics — need 5+ for feedback")
        return result

    # Compute median views
    views_list = sorted([r["views"] for r in rows if r["views"] and r["views"] > 0])
    if not views_list:
        return result

    mid = len(views_list) // 2
    median_views = views_list[mid] if len(views_list) % 2 else (views_list[mid-1] + views_list[mid]) // 2
    result["median_views"] = median_views
    print(f"  [ANALYTICS] Median views: {median_views} (from {len(views_list)} posts)")

    # Classify high/low performers
    high = [r for r in rows if r["views"] and r["views"] >= median_views * 1.3]
    low = [r for r in rows if r["views"] and r["views"] < median_views * 0.7 and r["views"] > 0]

    # Category-level analytics (main signal)
    cat_high_counts = Counter()
    cat_low_counts = Counter()
    cat_high_views = {}
    cat_low_views = {}

    for r in high:
        cats = _classify_topic(r["title"] or "", r["body"] or "")
        for c in cats:
            cat_high_counts[c] += 1
            cat_high_views.setdefault(c, []).append(r["views"])

    for r in low:
        cats = _classify_topic(r["title"] or "", r["body"] or "")
        for c in cats:
            cat_low_counts[c] += 1
            cat_low_views.setdefault(c, []).append(r["views"])

    # Categories that appear 2+ times in high performers → boost
    for cat, count in cat_high_counts.items():
        if count >= 2:
            avg_views = sum(cat_high_views[cat]) // len(cat_high_views[cat])
            result["cat_boosts"][cat] = min(20, max(5, avg_views // (median_views or 1) * 5))
            print(f"  [ANALYTICS] cat boost: +{result['cat_boosts'][cat]} ({cat}) — {count} posts, avg {avg_views} views")

    # Categories that appear 2+ times in low performers → penalty
    for cat, count in cat_low_counts.items():
        if count >= 2 and cat not in result["cat_boosts"]:
            result["cat_penalties"][cat] = -15
            print(f"  [ANALYTICS] cat penalty: -15 ({cat}) — {count} posts")

    # Word-level hook keywords from high performers (supplementary, only meaningful words)
    high_hook_counts = Counter()
    for r in high:
        words = set(re.findall(r'\b[a-z]{5,}\b', (r["slide_hook"] or "").lower()))
        meaningful = words - _STOPWORDS
        for w in meaningful:
            high_hook_counts[w] += 1
    for word, count in high_hook_counts.items():
        if count >= 2:
            result["hook_boosts"][word] = 10

    if result["hook_boosts"]:
        print(f"  [ANALYTICS] Hook boosts: {list(result['hook_boosts'].keys())[:8]}")

    return result


def run(top_n: int = TOP_N, dry_run: bool = False):
    t0 = time.time()
    conn = get_db()
    try:
        _run_inner(conn, top_n, dry_run, t0)
    finally:
        conn.close()


def _run_inner(conn, top_n: int, dry_run: bool, t0: float):
    staged_this_run = False

    # 0. Posting hours check (WIB = UTC+7)
    from datetime import datetime, timezone, timedelta
    now_wib = datetime.now(timezone(timedelta(hours=7)))
    current_hour = now_wib.hour
    if not (POSTING_HOURS[0] <= current_hour < POSTING_HOURS[1]) and not dry_run:
        print(f"Outside posting hours ({POSTING_HOURS[0]}:00-{POSTING_HOURS[1]}:00 WIB). Now: {current_hour}:00. Skipping.")
        return

    # 0b. Simple file lock to prevent overlapping runs
    import fcntl
    lock_path = Path(__file__).parent.parent / ".pipeline.lock"
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another pipeline run is already in progress. Skipping.")
        return
    print(f"[HOURS] {current_hour}:00 WIB — within posting window")

    # 1. Daily post limit check
    today_count = conn.execute(
        "SELECT COUNT(*) as c FROM posts WHERE date(posted_at)=date('now') AND status='posted'"
    ).fetchone()['c']
    if today_count >= DAILY_POST_LIMIT and not dry_run:
        print(f"Daily limit reached ({today_count}/{DAILY_POST_LIMIT}). Skipping.")
        return
    print(f"[LIMIT] {today_count}/{DAILY_POST_LIMIT} posted today")

    # 1. Auto-clean old articles (>7 days)
    cleaned = cleanup_old(conn, days=7)
    if cleaned["deleted_articles"] > 0:
        print(f"[0/4] Cleaned {cleaned['deleted_articles']} old articles")

    # 1b. Analytics feedback — pull engagement, compute boosts (pressbox pattern)
    analytics = _pull_analytics_feedback(conn)

    # 1. Scrape + score
    print(f"[1/4] Scraping top {top_n} articles...")

    articles = scrape_all(top_n)

    # Track topics from RECENT posts only (last 1 day) for dedup — exclude failed
    posted_titles = [row['title'] for row in conn.execute(
        "SELECT a.title FROM posts p JOIN articles a ON p.article_id=a.id WHERE p.status='posted' AND p.created_at > datetime('now', '-6 hours')"
    ).fetchall()]

    # Track topics staged THIS run (prevents duplicates within same run)
    staged_titles_this_run = []

    if articles:

        # ── LAYER 2: Fast Dedup & Fast Drop ──────────────────────
        fresh = []
        for art in articles:
            # Source whitelist — skip if somehow scraped from removed sources
            if art.get("source", "") not in ALLOWED_SOURCES:
                print(f"  [DROP] source not allowed: {art.get('source', '?')}: {art['title'][:50]}...")
                continue
            # 2a. Content filter (exclude/penalty keywords)
            reject = fast_content_filter(art["title"], art["body"])
            if reject:
                print(f"  [DROP] {reject}: {art['title'][:50]}...")
                continue
            # 2a2. Article quality filter (3-layer: char/words/sentences)
            qreject = check_article_quality(art["body"])
            if qreject:
                print(f"  [DROP] quality: {qreject}: {art['title'][:50]}...")
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
            # Personal branding: boost educational content
            title_l = art["title"].lower()
            body_l = art["body"][:800].lower()
            edu_matches = sum(1 for kw in _EDUCATION_KEYWORDS if kw in title_l)
            if edu_matches >= 2:
                art["score"] = min(art["score"] + 15, 150)
            elif edu_matches >= 1:
                art["score"] = min(art["score"] + 8, 150)
            # Boost Indonesian-local stories (high relatability)
            indo_local_kw = ["asn", "pns", "pegawai", "bpjs", "pajak", "npwp",
                              "ojk", "kominfo", "menteri", "presiden", "thr",
                              "gaji", "ktp", "nik", "komdigi"]
            if any(kw in title_l for kw in indo_local_kw):
                art["score"] = min(art["score"] + 20, 150)
                art.setdefault("analytics_tag", []).append("indo-local+20")
            # Penalize pure product news (low educational value)
            if any(kw in title_l for kw in ["review:", "hands-on", "launch", "peluncuran", "diluncurkan",
                                              "iphone", "galaxy", "smartphone", "ponsel", "hp baru", "fold"]):
                art["score"] = max(art["score"] - 20, 0)  # stronger penalty -20

        # ── LAYER 3a: Analytics Feedback Boosts ──────────────────
        if analytics.get("hook_boosts") or analytics.get("topic_penalties") or analytics.get("cat_boosts") or analytics.get("cat_penalties"):
            for art in fresh:
                title_words = set(re.findall(r'\b[a-z]{4,}\b', art["title"].lower()))
                body_words = set(re.findall(r'\b[a-z]{4,}\b', art["body"][:500].lower()))
                all_words = title_words | body_words
                # Hook boost: if title/body matches high-performing hook keywords
                for kw, pts in analytics.get("hook_boosts", {}).items():
                    if kw in all_words:
                        art["score"] = min(art["score"] + pts, 150)
                        art.setdefault("analytics_tag", []).append(f"hook+{pts}")
                # Topic penalty: if title matches low-performing topic keywords
                for kw, pts in analytics.get("topic_penalties", {}).items():
                    if kw in title_words:
                        art["score"] = max(art["score"] + pts, 0)
                        art.setdefault("analytics_tag", []).append(f"topic{pts}")
                # Category-level boosts/penalties (main signal from analytics)
                cats = _classify_topic(art["title"], art["body"])
                for cat in cats:
                    if cat in analytics.get("cat_boosts", {}):
                        pts = analytics["cat_boosts"][cat]
                        art["score"] = min(art["score"] + pts, 150)
                        art.setdefault("analytics_tag", []).append(f"cat+{pts}({cat})")
                    if cat in analytics.get("cat_penalties", {}):
                        pts = analytics["cat_penalties"][cat]
                        art["score"] = max(art["score"] + pts, 0)
                        art.setdefault("analytics_tag", []).append(f"cat{pts}({cat})")

        # ── LAYER 3b: Relatability Check (LLM) ──────────────────
        # Filter articles by how relatable they are to Indonesian audience.
        # Uses cheap/fast Mistral call. Score 1-5, reject < 3.
        relatable_fresh = []
        for art in fresh:
            rel = _check_relatability(art["title"], art["body"][:500])
            if rel >= 3:
                art["relatability"] = rel
                relatable_fresh.append(art)
                print(f"  [RELATE] {rel}/5 ✅ {art['title'][:60]}")
            else:
                print(f"  [RELATE] {rel}/5 ❌ {art['title'][:60]}")
        fresh = relatable_fresh

        if not fresh:
            print("  [RELATE] All articles rejected by relatability filter")

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

        # ── LAYER 5: Drama Boost ───────────────────────────────
        # Articles with drama signals in TITLE + niche relevance get priority
        DRAMA_TITLE_SIGNALS = [
            "viral", "heboh", "korban", "banting setir", "dipecat",
            "resign", "phk", "skandal", "terungkap", "ternyata",
            "didenda", "dituntut", "ditangkap", "gugat",
            "layoff", "scandal", "fired", "bankrupt", "crisis",
        ]
        for art in fresh:
            title_lower = art["title"].lower()
            has_drama_in_title = any(s in title_lower for s in DRAMA_TITLE_SIGNALS)
            if has_drama_in_title:
                _, dr = score_article_drama(art["title"], art["body"])
                has_niche = "niche:" in dr
                if has_niche:
                    art["score"] = min(art["score"] + DRAMA_BOOST, 180)
                    art["drama"] = dr
                    print(f"  [DRAMA +{DRAMA_BOOST}] {art['title'][:50]}... ({dr})")

        # Sort by score desc
        fresh.sort(key=lambda x: x.get("score", 0), reverse=True)

        for art in fresh:
            vir_tag = f" +{art.get('virality', '')}" if art.get("virality") else ""
            print(f"\n  [{art['source']}] score={art['score']}{vir_tag} | {art['title'][:60]}...")

            # 2. Upsert article to DB (skip on dry-run)
            if dry_run:
                print(f"  [DRY RUN] Skipping DB save + generation")
                continue
            article_id = upsert_article(conn, art)
            print(f"  Article #{article_id} saved to DB")

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
            WHERE a.id NOT IN (SELECT article_id FROM posts)
              AND a.source IN ({})
            ORDER BY a.score DESC
            LIMIT ?
        """.format(','.join('?' * len(ALLOWED_SOURCES))), (*ALLOWED_SOURCES, top_n)).fetchall()

        if unposted:
            for art in unposted:
                art = dict(art)
                # Layer 2: Content filter + DB dedup
                reject = fast_content_filter(art['title'], art['body'])
                if reject:
                    print(f"  [DROP] {reject}: {art['title'][:50]}...")
                    continue
                qreject = check_article_quality(art['body'])
                if qreject:
                    print(f"  [DROP] quality: {qreject}: {art['title'][:50]}...")
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

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=TOP_N)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args.top, args.dry_run)
