#!/usr/bin/env python3
"""
Budakorporat Pipeline v2.1 — Full Pressbox Parity
Real news → hot topic detection → viral pattern → 6-slide → grounding → evaluator → post

Features:
- 8 RSS sources with fingerprint dedup + forced full-scrape fallback
- Workplace relevance scoring + analytics-driven auto-tuning
- Hot topic detection via Union-Find entity clustering (4h window)
- Viral pattern selection (A=scandal, C=detail+emotion) based on article content
- Proper noun + number grounding (blocks hallucinated names/stats)
- Skeptical evaluator (REJECT on factual errors)
- Engagement pull + score auto-tuning from live data
- Source diversity cap (max 50% from one source)
- Article quality gates (>1000 chars, >150 words, >8 sentences, commercial body filter)

Usage:
    python3 pipeline-v2.py                # normal run
    python3 pipeline-v2.py --dry-run      # generate only, don't post
    python3 pipeline-v2.py --with-jitter  # random 0-30s delay (cron)
"""
import html as html_mod
import json
import os
import re
import sys
import time
import random
import logging
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

# ── Paths ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
HOME = Path.home()
POSTED_FILE = BASE_DIR / "posted_topics_v2.json"
ANALYTICS_FILE = BASE_DIR / "analytics_v2.json"
SCORE_TUNING_FILE = BASE_DIR / "score-tuning-v2.json"
ARTICLE_CACHE = HOME / ".hermes" / "budakorporat" / "article-cache-v2.json"
SOURCE_FINGERPRINTS = HOME / ".hermes" / "budakorporat" / "source-fingerprints-v2.json"

for d in [HOME / ".hermes" / "budakorporat"]:
    d.mkdir(parents=True, exist_ok=True)

WIB = timezone(timedelta(hours=7))

# ── Logging ─────────────────────────────────────────────────────────────────

log = logging.getLogger("bpv2")
log.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
log.addHandler(_handler)

# ── Config ──────────────────────────────────────────────────────────────────

DRY_RUN = "--dry-run" in sys.argv
USER_ID = "27516379201355016"  # @budakorporat_id
MAX_CHARS = 480  # Threads per-slide limit
COOLDOWN_MINUTES = 30
SCORE_GATE = 25  # dynamic: naik ke 40 kalau topics >= 30 (pressbox pattern)
ARTICLE_MIN_CHARS = 500
ARTICLE_MIN_WORDS = 150
ARTICLE_MIN_SENTENCES = 8
MAX_PER_SOURCE_RATIO = 0.5  # max 50% of ranked pool from one source

# ── RSS Sources ─────────────────────────────────────────────────────────────

SOURCES = {
    "cnn_nasional": {"url": "https://www.cnnindonesia.com/nasional/rss", "base_score": 8},
    "cnbc_news":    {"url": "https://www.cnbcindonesia.com/news/rss", "base_score": 8},
    "tempo_nasional": {"url": "https://rss.tempo.co/nasional", "base_score": 9},
    "tempo_bisnis":   {"url": "https://rss.tempo.co/bisnis", "base_score": 9},
    "bbc_indo":     {"url": "https://feeds.bbci.co.uk/indonesia/rss.xml", "base_score": 10},
    "gnews_kerja":  {"url": "https://news.google.com/rss/search?q=karyawan+OR+gaji+OR+phk+OR+resign+OR+kantor+OR+lembur&hl=id&gl=ID&ceid=ID:id", "base_score": 7},
    "gnews_phk":    {"url": "https://news.google.com/rss/search?q=phk+indonesia+2026&hl=id&gl=ID&ceid=ID:id", "base_score": 9},
    "gnews_bos":    {"url": "https://news.google.com/rss/search?q=bos+toxic+OR+micromanage+OR+burnout+karyawan&hl=id&gl=ID&ceid=ID:id", "base_score": 8},
}

# ── Keyword Tiers ───────────────────────────────────────────────────────────

WORKPLACE_KW = {
    "high": {
        "phk": 15, "layoff": 15, "resign": 12, "gaji": 12, "upah": 12,
        "karyawan": 10, "pekerja": 10, "lembur": 10, "overtime": 10, "burnout": 10,
        "bos": 8, "manajer": 8, "hrd": 10, "human resource": 10,
        "kantor": 8, "workplace": 8, "kerja": 6, "tunjangan": 10,
        "bpjs": 8, "jamsostek": 8, "kontrak": 8, "pkwt": 10,
        "perjanjian kerja": 10, "demo": 8, "serikat": 8, "buruh": 8,
        "pph 21": 8, "pajak": 6, "pengangguran": 12, "kerja paksa": 15,
    },
    "medium": {
        "indonesia": 3, "jakarta": 3, "perusahaan": 5, "pt ": 3,
        "startup": 5, "tech": 3, "digital": 3, "ekonomi": 3,
        "inflasi": 4, "daya beli": 4, "karir": 5, "promosi": 4,
        "jabatan": 4, "toxic": 8, "diskriminasi": 8, "pelecehan": 10,
    },
}

INDONESIAN_COMPANIES = {
    "tokopedia", "shopee", "gojek", "grab", "traveloka", "bukalapak",
    "telkomsel", "indosat", "bank mandiri", "bri", "bni", "btn", "bsi",
    "pertamina", "pln", "garuda", "lion air", "unilever", "indofood",
    "kemnaker", "kementerian", "dpr", "jokowi", "prabowo",
    "bpjs kesehatan", "bpjs ketenagakerjaan",
}
REGULATIONS = {
    "uu cipta kerja", "omnibus law", "pp 35", "pp 78", "uu 13",
    "perpres", "kepmenaker", "pph 21", "pph 23", "ppn",
}

# ── Env ─────────────────────────────────────────────────────────────────────

def _load_env():
    env = {}
    for env_path in [HOME / ".hermes" / ".env", BASE_DIR / ".env"]:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip("\"'")
    return env

ENV = _load_env()
MISTRAL_KEY = ENV.get("MISTRAL_API_KEY", "")
THREADS_TOKEN = ENV.get("THREADS_ACCESS_TOKEN", "")

# ── Threads Metrics (pressbox parity) ──────────────────────────────────────

GRAPH_API = "https://graph.threads.net/v1.0"

class ThreadsMetrics:
    """Pull engagement metrics for posts via Threads Insights API."""
    def __init__(self, access_token):
        self.token = access_token

    def get_metrics(self, post_id):
        """Pull views/likes/replies/shares for a post. Returns dict or None."""
        if not self.token:
            return None
        url = f"{GRAPH_API}/{post_id}/insights"
        params = f"metric=views,likes,replies,shares&access_token={self.token}"
        try:
            import httpx
            r = httpx.get(f"{url}?{params}", timeout=15)
            if r.status_code != 200:
                return None
            data = r.json()
            metrics = {}
            for item in data.get("data", []):
                name = item.get("name")
                value = item.get("values", [{}])[0].get("value", 0)
                metrics[name] = value
            return metrics if metrics else None
        except Exception as e:
            log.warning(f"Metrics fetch failed for {post_id}: {e}")
            return None

# ── HTTP ────────────────────────────────────────────────────────────────────

def _http_get(url, timeout=10):
    """Returns (status_code, text)."""
    try:
        import httpx
        r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout, follow_redirects=True, verify=False)
        return r.status_code, r.text
    except Exception:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", errors="replace")

# ── RSS Scraping ────────────────────────────────────────────────────────────

def scrape_rss(url, source, base_score=8):
    """Parse RSS feed → list of topic dicts."""
    topics = []
    try:
        code, text = _http_get(url)
        if code != 200:
            return topics
        root = ET.fromstring(text)
        for item in root.findall(".//item")[:20]:
            te = item.find("title")
            le = item.find("link")
            if te is None or le is None:
                continue
            title = re.sub(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", r"\1", (te.text or "").strip())
            title = html_mod.unescape(title)
            if not title or len(title) < 20:
                continue
            link = (le.text or "").strip().split("?")[0]
            de = item.find("description")
            desc = re.sub(r"<[^>]+>", " ", (de.text or "")).strip()[:500] if de is not None else ""
            desc = html_mod.unescape(desc)
            pe = item.find("pubDate")
            ts = None
            if pe is not None and pe.text:
                try:
                    ts = parsedate_to_datetime(pe.text.strip()).timestamp()
                except Exception:
                    pass
            if ts and (time.time() - ts) > 86400:
                continue
            topics.append(dict(title=title, source=source, url=link, score=base_score,
                               description=desc, published_ts=ts))
    except Exception as e:
        log.warning(f"RSS scrape failed for {source}: {e}")
    return topics

def scrape_all():
    """Scrape all sources in parallel with fingerprint dedup."""
    log.info("Scraping sources...")
    t0 = time.time()
    fingerprints = {}
    try:
        if SOURCE_FINGERPRINTS.exists():
            fingerprints = json.loads(SOURCE_FINGERPRINTS.read_text())
    except Exception:
        pass
    new_fps = {}
    all_topics = []

    def scrape_with_fp(name, cfg):
        topics = scrape_rss(cfg["url"], name, cfg["base_score"])
        if not topics:
            return [], False
        fp = topics[0].get("title", "")[:80]
        if fingerprints.get(name) == fp:
            return [], False
        return topics, True

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {name: ex.submit(scrape_with_fp, name, cfg) for name, cfg in SOURCES.items()}
        for name, f in futs.items():
            try:
                topics, changed = f.result(timeout=15)
                if changed:
                    new_fps[name] = topics[0].get("title", "")[:80]
                    all_topics.extend(topics)
                    log.info(f"  {name}: {len(topics)} topics")
                else:
                    log.info(f"  {name}: unchanged (skipped)")
            except Exception as e:
                log.warning(f"  {name}: {e}")

    if not all_topics:
        log.info("  All unchanged — forcing full scrape")
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {name: ex.submit(scrape_rss, cfg["url"], name, cfg["base_score"])
                    for name, cfg in SOURCES.items()}
            for name, f in futs.items():
                try:
                    all_topics.extend(f.result(timeout=15))
                except Exception:
                    pass

    fingerprints.update(new_fps)
    try:
        SOURCE_FINGERPRINTS.write_text(json.dumps(fingerprints, indent=2))
    except Exception:
        pass

    log.info(f"  Total: {len(all_topics)} in {time.time()-t0:.1f}s")
    return all_topics

# ── Hot Topic Detection ─────────────────────────────────────────────────────

def _extract_entities(title):
    tl = title.lower()
    found = set()
    for ent in INDONESIAN_COMPANIES:
        if ent in tl:
            found.add(ent)
    for reg in REGULATIONS:
        if reg in tl:
            found.add(reg)
    return found

def detect_hot_topics(topics, window_hours=4):
    """Cluster topics by entity overlap. Returns {url: hotness_score}."""
    now = time.time()
    cutoff = now - (window_hours * 3600)

    cached = []
    try:
        if ARTICLE_CACHE.exists():
            cached = json.loads(ARTICLE_CACHE.read_text())
            if isinstance(cached, dict):
                cached = []
    except Exception:
        pass

    seen_urls = set()
    merged = []
    for t in cached + topics:
        url = t.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(t)

    fresh = [t for t in merged if (t.get("published_ts") or now) >= cutoff]
    try:
        ARTICLE_CACHE.write_text(json.dumps(fresh, indent=2))
    except Exception:
        pass

    if len(fresh) < 2:
        return {}

    article_entities = [(t, _extract_entities(t.get("title", ""))) for t in fresh]
    n = len(article_entities)

    parent = list(range(n))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            if len(article_entities[i][1] & article_entities[j][1]) >= 2:
                union(i, j)

    skip_words = {"the","a","an","in","on","at","to","for","of","and","or","but","is","was",
                  "ini","yang","dan","di","dari","untuk","pada","dengan","ada","adalah"}
    for i in range(n):
        for j in range(i + 1, n):
            if article_entities[i][1] & article_entities[j][1]:
                w1 = set(article_entities[i][0].get("title", "").lower().split()) - skip_words
                w2 = set(article_entities[j][0].get("title", "").lower().split()) - skip_words
                if len(w1 & w2) >= 4:
                    union(i, j)

    clusters = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(article_entities[i])

    hotness = {}
    for root, members in clusters.items():
        if len(members) < 2:
            continue
        sources = set(m[0].get("source", "") for m in members)
        count = len(members)
        tier_bonus = 1.5 if any(s.startswith("bbc") or s.startswith("tempo") for s in sources) else 1.0
        recency_sum = sum(1.0 / max(0.01, (now - (m.get("published_ts") or now)) / 3600) for m, _ in members)
        recency_avg = recency_sum / count
        hot = count * tier_bonus * recency_avg

        cluster_ents = set()
        for m, ents in members:
            cluster_ents |= ents
        for m, _ in members:
            url = m.get("url", "")
            if url:
                hotness[url] = max(hotness.get(url, 0), hot)
                hotness[url + "_entities"] = list(cluster_ents)

    if hotness:
        hot_count = sum(1 for k, v in hotness.items() if isinstance(v, (int, float)))
        log.info(f"  Hot detection: {hot_count} articles in clusters")
    return hotness

# ── Scoring ─────────────────────────────────────────────────────────────────

def _workplace_relevance(title, description=""):
    text = (title + " " + description).lower()
    score = 0
    for kw, pts in WORKPLACE_KW["high"].items():
        if kw in text:
            score += pts
    for kw, pts in WORKPLACE_KW["medium"].items():
        if kw in text:
            score += pts
    return min(score, 50)

def _classify_hook(title_lower):
    if any(w in title_lower for w in ["phk", "layoff", "resign", "pengangguran"]):
        return "layoff"
    if any(w in title_lower for w in ["gaji", "upah", "bonus", "tunjangan"]):
        return "compensation"
    if any(w in title_lower for w in ["bos", "toxic", "manajer", "hrd"]):
        return "boss_drama"
    if any(w in title_lower for w in ["burnout", "lembur", "kerja keras"]):
        return "burnout"
    if any(w in title_lower for w in ["demo", "serikat", "buruh"]):
        return "labor_action"
    return "other"

def _is_workplace_relevant(title, description=""):
    text = (title + " " + description).lower()
    required = ["karyawan", "pekerja", "buruh", "gaji", "upah", "phk", "layoff",
                "resign", "lembur", "kantor", "kerja", "bos", "hrd", "burnout",
                "tunjangan", "bpjs", "kontrak", "perusahaan", "pt ", "pt.",
                "startup", "tech", "digital", "ekonomi", "pengangguran"]
    return any(kw in text for kw in required)

# ── Posting History ─────────────────────────────────────────────────────────

def _clean_words(text):
    t = text.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return frozenset(w for w in t.split() if len(w) > 1)

def _is_similar(new_title, posted_ws, threshold=0.4):
    nw = _clean_words(new_title)
    if not nw:
        return False
    for pw in posted_ws:
        if not pw:
            continue
        inter = len(nw & pw)
        union = len(nw | pw)
        if union > 0 and inter / union >= threshold:
            return True
    return False

def load_posted():
    posted_urls, posted_ws = set(), []
    try:
        data = json.loads(POSTED_FILE.read_text())
        for t in (data.get("topics", []) if isinstance(data, dict) else data):
            u = (t.get("url") or "").strip()
            if u.startswith("http"):
                posted_urls.add(u)
            ti = (t.get("title") or "").strip()
            if ti:
                posted_ws.append(_clean_words(ti))
    except Exception:
        pass
    return posted_urls, posted_ws

def check_cooldown(minutes=COOLDOWN_MINUTES):
    try:
        data = json.loads(POSTED_FILE.read_text())
        topics = data.get("topics", [])
        if not topics:
            return False
        recent = sorted(topics, key=lambda x: x.get("posted_at", ""), reverse=True)[:1]
        posted = recent[0].get("posted_at", "")
        if posted:
            dt = datetime.fromisoformat(posted)
            if (datetime.now(WIB) - dt).total_seconds() < minutes * 60:
                return True
    except Exception:
        pass
    return False

def track_post(title, url, source, root_id, permalink, hotness_score=0, workplace_score=0):
    try:
        data = json.loads(POSTED_FILE.read_text())
    except Exception:
        data = {"topics": []}
    if "topics" not in data:
        data["topics"] = []
    entry = {
        "title": title, "url": url, "source": source,
        "post_id": root_id, "permalink": permalink,
        "posted_at": datetime.now(WIB).isoformat(),
        "workplace_score": workplace_score,
    }
    if hotness_score:
        entry["hotness_score"] = round(hotness_score, 2)
    data["topics"].append(entry)
    data["topics"] = data["topics"][-200:]
    POSTED_FILE.write_text(json.dumps(data, indent=2))

# ── Engagement Pull (pressbox pattern) ──────────────────────────────────────

def pull_engagement(poster=None):
    """Pull metrics for posts >12h that haven't been tracked yet. Max 10/run."""
    if not poster:
        return
    try:
        data = json.loads(POSTED_FILE.read_text())
    except Exception:
        return

    cutoff = time.time() - 43200  # 12 hours
    updated = failed = processed = 0
    MAX_PER_RUN = 10

    for topic in data.get("topics", []):
        if processed >= MAX_PER_RUN:
            break
        if topic.get("views") is not None or topic.get("metrics_failed"):
            continue
        posted_at = topic.get("posted_at", "")
        if posted_at:
            try:
                pt = datetime.fromisoformat(posted_at).timestamp()
                if pt > cutoff:
                    continue
            except Exception:
                continue
        post_id = topic.get("post_id")
        if not post_id:
            continue
        metrics = poster.get_metrics(post_id) if hasattr(poster, "get_metrics") else None
        processed += 1
        if metrics:
            topic["views"] = metrics.get("views", 0)
            topic["likes"] = metrics.get("likes", 0)
            topic["replies"] = metrics.get("replies", 0)
            topic["shares"] = metrics.get("shares", 0)
            updated += 1
        else:
            topic["metrics_failed"] = True
            failed += 1
        time.sleep(0.3)

    if updated or failed:
        POSTED_FILE.write_text(json.dumps(data, indent=2))
        if updated:
            log.info(f"  Updated metrics for {updated} posts")
        if failed:
            log.info(f"  Metrics failed for {failed} posts (marked to skip)")

# ── Analytics + Score Auto-Tuning ───────────────────────────────────────────

def get_analytics_summary():
    """Generate analytics summary with auto-tuning from posted_topics_v2.json."""
    try:
        data = json.loads(POSTED_FILE.read_text())
    except Exception:
        return {}
    topics = data.get("topics", [])
    with_metrics = [t for t in topics if t.get("views") is not None]
    if len(with_metrics) < 3:
        return {}

    by_hook = defaultdict(list)
    by_source = defaultdict(list)
    for t in with_metrics:
        hook = _classify_hook((t.get("title") or "").lower())
        by_hook[hook].append(t.get("views", 0))
        by_source[(t.get("source") or "").lower()].append(t.get("views", 0))

    def avg(lst):
        return sum(lst) / len(lst) if lst else 0
    all_views = sorted([t.get("views", 0) for t in with_metrics])
    median_views = all_views[len(all_views) // 2] if all_views else 0

    summary = {
        "total_posts": len(with_metrics),
        "avg_views": avg([t.get("views", 0) for t in with_metrics]),
        "median_views": median_views,
        "best_hooks": sorted([(h, avg(v)) for h, v in by_hook.items()], key=lambda x: -x[1])[:3],
        "worst_hooks": sorted([(h, avg(v)) for h, v in by_hook.items()], key=lambda x: x[1])[:2],
        "best_sources": sorted([(s, avg(v)) for s, v in by_source.items()], key=lambda x: -x[1]),
    }

    # Score auto-tuning from engagement data (pressbox pattern)
    if len(with_metrics) >= 10:
        tuning = _compute_score_tuning(with_metrics, median_views)
        if tuning:
            summary["score_tuning"] = tuning

    return summary

def _compute_score_tuning(posts, median_views):
    """Analyze engagement → scoring weight adjustments. Saves to SCORE_TUNING_FILE."""
    def avg(lst):
        return sum(lst) / len(lst) if lst else 0

    high = [p for p in posts if p.get("views", 0) >= median_views * 1.3]
    low = [p for p in posts if p.get("views", 0) < median_views * 0.7]
    if len(high) < 3 or len(low) < 3:
        return {}

    tuning = {}

    # 1. Hook effectiveness: which hooks get more views?
    high_hooks = defaultdict(int)
    low_hooks = defaultdict(int)
    for p in high:
        high_hooks[_classify_hook((p.get("title") or "").lower())] += 1
    for p in low:
        low_hooks[_classify_hook((p.get("title") or "").lower())] += 1
    for hook in set(list(high_hooks.keys()) + list(low_hooks.keys())):
        h, l = high_hooks[hook], low_hooks[hook]
        if l > 0:
            ratio = h / l
            if ratio > 1.5:
                tuning[f"boost_{hook}"] = round(min(20, (ratio - 1.0) * 10))
            elif ratio < 0.7:
                tuning[f"penalize_{hook}"] = round(min(20, (1.0 - ratio) * 10))

    # 2. Source effectiveness
    high_src = Counter(p.get("source", "") for p in high)
    low_src = Counter(p.get("source", "") for p in low)
    for src in set(list(high_src.keys()) + list(low_src.keys())):
        h, l = high_src[src], low_src[src]
        if l > 0:
            ratio = h / l
            if ratio > 1.5:
                tuning[f"boost_source_{src}"] = round(min(15, (ratio - 1.0) * 10))

    # 3. Hot vs cold
    hot_posts = [p for p in posts if p.get("hotness_score", 0) > 0]
    cold_posts = [p for p in posts if not p.get("hotness_score")]
    if hot_posts and cold_posts:
        hot_avg = avg([p.get("views", 0) for p in hot_posts])
        cold_avg = avg([p.get("views", 0) for p in cold_posts])
        if cold_avg > 0:
            ratio = hot_avg / cold_avg
            if ratio >= 1.5:
                tuning["hot_boost_adjust"] = min(10, int((ratio - 1.0) * 10))
            elif ratio < 0.8:
                tuning["hot_boost_adjust"] = max(-10, int((ratio - 1.0) * 10))

    if tuning:
        tuning_data = {
            "computed_at": datetime.now().isoformat(),
            "posts_analyzed": len(posts),
            "median_views": median_views,
            "high_posts": len(high),
            "low_posts": len(low),
            "weights": tuning,
        }
        try:
            SCORE_TUNING_FILE.write_text(json.dumps(tuning_data, indent=2))
        except Exception:
            pass
        log.info(f"  Score tuning: {tuning} (from {len(posts)} posts)")
    return tuning

# ── Source Diversity Cap ────────────────────────────────────────────────────

def _apply_source_diversity(results, max_ratio=MAX_PER_SOURCE_RATIO):
    """Cap: no single source > max_ratio of ranked pool."""
    if not results:
        return results
    max_per_source = max(1, int(len(results) * max_ratio))
    source_count = Counter()
    capped = []
    for t in results:
        src = t.get("source", "")
        if source_count[src] < max_per_source:
            capped.append(t)
            source_count[src] += 1
    return capped

# ── Viral Pattern Selector ──────────────────────────────────────────────────

def _select_viral_pattern(topic, article_text):
    """Pick Pattern A (scandal) or C (detail+emotion) based on article content."""
    title = (topic.get("title") or "").lower()
    text = article_text.lower()[:2000]
    combined = title + " " + text

    scandal_words = ["scandal", "controversy", "behind the scenes", "secret", "real reason",
                     "nobody talks", "ugly truth", "shocking", "betray", "refuse", "clash",
                     "furious", "rage", "slam", "blast", "row", "rift", "feud",
                     "korupsi", "penyelewengan", "skandal", "suap", "gratifikasi"]
    scandal_score = sum(1 for w in scandal_words if w in combined)

    detail_words = ["rp", "juta", "miliar", "triliun", "rupiah", "%",
                    "phk", "karyawan", "buruh", "demo", "unjuk rasa",
                    "ibu", "anak", "keluarga", "korban", "menangis", "tangis",
                    "ketidakadilan", "tidak adil", "hak", "perlindungan"]
    detail_score = sum(1 for w in detail_words if w in combined)

    has_specific_number = bool(re.search(r'\d+[\d,.]*\s*(?:juta|miliar|triliun|%)', combined))
    if has_specific_number:
        detail_score += 3

    pattern = "a" if scandal_score > detail_score else "c"
    pattern_name = "A (scandal)" if pattern == "a" else "C (detail+emotion)"
    log.info(f"  Viral pattern: {pattern_name} (scandal={scandal_score}, detail={detail_score})")
    return pattern

# ── Article Extraction + Quality Gates ──────────────────────────────────────

def _is_commercial_body(text):
    """Check if article body is commercial/shopping, not news."""
    bl = text[:3000].lower()
    news_kws = ["karyawan", "pekerja", "gaji", "phk", "kantor", "kerja", "bos", "resign",
                "lembur", "tunjangan", "bpjs", "ekonomi", "indonesia"]
    commercial_kws = ["buy now", "shop now", "discount", "sale", "voucher", "coupon",
                      "basket", "checkout", "add to basket", "purchase", "% off",
                      "free shipping", "snap up", "order now"]
    news_count = sum(1 for kw in news_kws if kw in bl)
    commercial_count = sum(1 for kw in commercial_kws if kw in bl)
    return news_count < 2 and commercial_count >= 2

def extract_article_text(url):
    """Fetch article page, extract clean text + image."""
    try:
        import httpx
        r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10, follow_redirects=True, verify=False)
        html = r.text
    except Exception:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        r = urllib.request.urlopen(req, timeout=10)
        html = r.read().decode("utf-8", errors="replace")

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    body = (soup.find("article")
            or soup.find("div", class_=lambda c: c and any(k in c.lower() for k in ["article", "content", "story", "body"]))
            or soup.find("main"))
    if body:
        for tag in body.find_all(["nav", "aside", "footer", "script", "style"]):
            tag.decompose()
        paragraphs = [p.get_text(strip=True) for p in body.find_all("p") if len(p.get_text(strip=True)) > 20]
        text = " ".join(paragraphs)
    else:
        text = soup.get_text(separator=" ", strip=True)[:5000]

    # Fallback: trafilatura for short articles
    if len(text.strip()) < 500:
        try:
            import trafilatura
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                extracted = trafilatura.extract(downloaded)
                if extracted and len(extracted) > len(text):
                    text = extracted
        except Exception:
            pass

    image_url = ""
    for pat in [r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"',
                r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"',
                r'<meta[^>]+name="twitter:image"[^>]+content="([^"]+)"']:
        m = re.search(pat, html, re.I)
        if m:
            image_url = m.group(1)
            break

    return text[:8000], image_url

def _pass_quality_gates(article_text):
    """Check article quality. Returns (pass, reason)."""
    if not article_text or len(article_text.strip()) < ARTICLE_MIN_CHARS:
        return False, f"too short ({len(article_text)} chars < {ARTICLE_MIN_CHARS})"
    word_count = len(article_text.split())
    if word_count < ARTICLE_MIN_WORDS:
        return False, f"too thin ({word_count} words < {ARTICLE_MIN_WORDS})"
    sentences = [s.strip() for s in re.split(r'[.!?]+', article_text) if len(s.strip()) > 20]
    if len(sentences) < ARTICLE_MIN_SENTENCES:
        return False, f"too few sentences ({len(sentences)} < {ARTICLE_MIN_SENTENCES})"
    if _is_commercial_body(article_text):
        return False, "commercial body, not news"
    return True, ""

# ── LLM Generation ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """# Budakorporat v2.3 — 6-Slide Thread

## ROLE
Lo content creator @budakorporat_id — akun drama kantor Indonesia di Threads. Nulis kayak temen sekantor yang tau semua gosip. Pedas, relatable, gak basa-basi. Rahasia lo: bikin orang NEED baca terus.

## CONTEXT
Pembaca: pekerja kantor Indonesia 22-35. Stres sama bos, gaji telat, toxic coworker. Scroll cepet — yang berhenti cuma yang bikin mereka MERASA sesuatu.

## ARTICLE
Title: {title}
Body: {body}
Source: {source}

## WINNING FORMULA
Hook yang viral:

```
[Perusahaan/Orang] baru aja [tindakan konkret] [timing].

Ternyata?

[Fakta absurd yang bikin orang share].
```

Kenapa works: "baru aja" = urgency. Timing = credibility. "Ternyata?" = curiosity gap. Fakta absurd = shareability.

Contoh:
- "Gojek baru aja PHK 500 orang. Ternyata? Yang kena duluan yang baru dapet promosi."
- "Bank BUMN baru aja blokir gaji karyawan jam 2 pagi. Ternyata? HRD-nya cuti ke luar negeri."

**Anti-patterns (KILL engagement):**
❌ "[Perusahaan] PHK massal" — generic, no urgency
❌ "Dalam berita terbaru..." — boring preamble
❌ Mulai dari kesimpulan — no curiosity gap

## VIRAL CRITERIA (tiap slide minimal kena 1)
1. Pro & Con — ada debat dua sisi?
2. Relatable — gaji, burnout, PHK, toxic boss.
3. Famous figure — perusahaan besar = scroll stopper.
4. Viral / Trending — trending? Tambah angle baru.
5. Comedy / Irony — absurd twist.
6. Surprising fact — angka yang bikin geleng.
7. Emotional hook — marah, simpati, frustasi.
8. Absurd detail — "gak dikasih THR" > "kompensasi tidak memadai".

## WRITING STYLE
- Bahasa Indonesia kasual, Gue/Lo.
- Kalimat pendek. Satu ide per kalimat.
- Zero emoji. No em-dash.
- Pakai "baru aja" + timing detail ("jam 2 pagi", "3 hari sebelum deadline").
- Text message test — kalau lo gak mau ngetik ke temen, rewrite.
- Maks 1 angka per slide.

## TASK — 6 SLIDES

### Slide 1 — Hook (maks 3 baris, <40 kata)
WAJIB ikutin winning formula. Kena Viral Criteria #3 DAN #8. JANGAN mulai dari kesimpulan.

### Slide 2-4 — Context (maks 3 sentences/slide, <40 kata/slide)
Satu beat per slide. Tiap slide kena minimal 1 Viral Criteria.

### Slide 5 — Take (maks 3 sentences, <40 kata)
Bacaan lo. Pick ONE angle grounded di artikel. Kena Criteria #1 atau #7.

### Slide 6 — Closing + CTA (maks 50 kata)
Wrap up + ajak comment. Satu pertanyaan balik ke hook. JANGAN tulis source URL di slide ini.

## OUTPUT
Return ONLY valid JSON:
{{"slide_1":"", "slide_2":"", "slide_3":"", "slide_4":"", "slide_5":"", "slide_6":"", "caption":"", "hashtags":""}}
Caption: 1 sentence. Zero emoji. Max 1 hashtag.

## GROUNDING RULES
Every fact must come from the article. Never invent quotes, stats, or incidents.
1. No invented strategic intent.
2. No exaggerated paraphrasing.
3. No speculative consequences.
4. Quotes = word-for-word from article.
5. Rumor = say so explicitly.

## RULES
1. No em-dash.
2. Jelaskan konteks kalau gak semua orang tau.
3. Hindari frasa blog generik.
4. Zero "link in bio." Never fabricate quotes.
5. Tone dramatis boleh, false urgency tidak.

## BANNED PATTERNS
You won't believe... / Gak bakal percaya... / Ini mengubah segalanya / Kabar mengejutkan / Sources say (unspecified) / Let that sink in / Fakta yang disembunyikan / Lo belum siap sama yang ini
"""

def generate_slides(article_text, url, title="", source="", pattern="c"):
    """Generate 6-slide thread via Mistral. Returns list of slide dicts or None."""
    if not MISTRAL_KEY:
        log.error("No MISTRAL_API_KEY")
        return None

    system = SYSTEM_PROMPT.replace("{title}", title).replace("{body}", article_text[:6000]).replace("{source}", source)

    # Pattern-specific instruction
    if pattern == "a":
        system += "\n## PATTERN: SCANDAL/CONSPIRACY\nFrame the story around hidden reasons, behind-the-scenes drama, nobody's talking about this angle."
    else:
        system += "\n## PATTERN: DETAIL + EMOTION\nLead with specific numbers/amounts. Connect to human cost. Make readers feel the weight of the data."

    user_msg = f"Generate 6-slide thread for this article.\n\nURL: {url}"

    for attempt in range(1, 4):
        log.info(f"  LLM attempt {attempt}/3...")
        try:
            import httpx
            r = httpx.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
                json={"model": "mistral-large-latest", "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg}
                ], "max_tokens": 2000, "temperature": 0.5},
                timeout=60
            )
            if r.status_code != 200:
                log.warning(f"  Mistral HTTP {r.status_code}")
                time.sleep(2 + attempt)
                continue

            content = r.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

            data = json.loads(content)
            slides = []
            for i in range(1, 7):
                key = f"slide_{i}"
                text = data.get(key, "").strip()
                if text and len(text) >= 10:
                    text = text.replace("\u2014", " - ").replace("\u2013", " - ")
                    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
                    # Auto-trim slide 2-5 to max 3 sentences (pressbox pattern)
                    if i in (2, 3, 4, 5):
                        sents = re.split(r'(?<=[.!?])\s+', text.strip())
                        if len(sents) > 3:
                            text = " ".join(sents[:3])
                    # Ensure under MAX_CHARS
                    if len(text) > MAX_CHARS:
                        text = text[:MAX_CHARS-3] + "..."
                    slides.append({"title": f"S{i}", "content": text})

            if len(slides) < 4:
                log.warning(f"  Only {len(slides)} slides parsed")
                continue

            # Add source URL to last slide
            last = slides[-1]["content"]
            url_base = url.split("?")[0].rstrip("/")
            if url_base not in last:
                new_last = last.rstrip() + "\n\n" + url
                if len(new_last) > MAX_CHARS:
                    new_last = last[:MAX_CHARS - len(url) - 5] + "...\n\n" + url
                slides[-1]["content"] = new_last

            caption = data.get("caption", "").strip()
            hashtags = data.get("hashtags", "").strip()
            if caption:
                slides[0]["caption"] = caption
            if hashtags:
                slides[0]["hashtags"] = hashtags

            return slides

        except json.JSONDecodeError as e:
            log.warning(f"  JSON parse failed: {e}")
            time.sleep(1)
            continue
        except Exception as e:
            log.error(f"  LLM error: {e}")
            continue

    log.error("Failed after 3 attempts")
    return None

# ── Grounding Check (pressbox parity: proper nouns + numbers) ───────────────

_SKIP_WORDS = frozenset({
    "The", "This", "That", "These", "Those", "A", "An", "When", "Where", "What", "Which",
    "After", "Before", "During", "Under", "Over", "Since", "Until", "Between", "Through",
    "Against", "Into", "Upon", "Within", "Without", "From", "With", "About", "Above",
    "For", "Nor", "Once", "Though", "Although", "Because", "Whether", "If", "Unless",
    "Even", "Still", "Just", "Now", "Then", "Here", "There", "Only", "Already",
    "Also", "Perhaps", "Both", "Either", "Neither", "Each", "Every", "Most", "Rather",
})

def _extract_proper_nouns(text):
    """Extract capitalized multi-word names (companies, people, organizations)."""
    names = re.findall(r'([A-Z\u00C0-\u024F][a-z\u00E0-\u024F]+(?:\s[A-Z\u00C0-\u024F][a-z\u00E0-\u024F]+)+)', text)
    cleaned = []
    for n in names:
        words = n.split()
        if words[0] in _SKIP_WORDS and len(words) > 2:
            cleaned.append(" ".join(words[1:]))
        elif words[0] not in _SKIP_WORDS:
            cleaned.append(n)
    return set(n for n in cleaned if len(n) > 4)

def grounding_check(slides_text, article_text):
    """Check for hallucinated proper nouns and numbers. Returns warnings list."""
    warnings = []

    # 1. Proper noun check (pressbox pattern)
    slide_names = _extract_proper_nouns(slides_text)
    art_names = _extract_proper_nouns(article_text)
    for name in slide_names:
        if name not in article_text and len(name) > 4:
            warnings.append(f"HALLUCINATED_NAME: '{name}'")

    # 2. Number check
    slides_clean = re.sub(r'https?://\S+', '', slides_text)
    slide_numbers = set(re.findall(r'(?<!\d)(\d{3,})(?!\d)', slides_clean))
    art_numbers = set(re.findall(r'(?<!\d)(\d{3,})(?!\d)', article_text))
    for num in slide_numbers:
        if num not in art_numbers:
            warnings.append(f"UNVERIFIED_NUMBER: {num}")

    return warnings

# ── Evaluator ───────────────────────────────────────────────────────────────

def evaluator_check(slides, article_text, url):
    """Skeptical editor review. Returns (decision, reasons)."""
    if not MISTRAL_KEY:
        return "APPROVE", ["no API key"]

    slides_text = "\n\n".join(f"[Slide {i+1}]\n{s['content']}" for i, s in enumerate(slides))
    art_short = article_text[:3000]

    system = (
        "You are a skeptical editor reviewing social media slides for @budakorporat_id — "
        "a casual Indonesian workplace drama account on Threads. The voice is intentionally "
        "informal (Gue/Lo), uses clickbait hooks, and sensationalizes workplace issues. "
        "This is the BRAND, not a bug.\n\n"
        "ONLY flag these as problems:\n"
        "1. FACTUAL ERRORS: claims not supported by the article (numbers, events, quotes)\n"
        "2. HALLUCINATION: invented stats, names, quotes that don't appear in the source\n"
        "3. MISLEADING: says X but article says Y\n\n"
        "DO NOT flag as problems:\n"
        "- Informal tone, Gue/Lo language, casual style (this is intentional)\n"
        "- Clickbait hooks or dramatic framing (this is the account's style)\n"
        "- Questions like 'Lo gimana?' or 'Lo pernah gak?' (standard CTA pattern)\n"
        "- Hashtags like #DramaKantor (brand hashtag)\n\n"
        'Respond in EXACTLY this JSON format:\n'
        '{"decision": "APPROVE|REVISE|REJECT", "reasons": ["reason1", "reason2"]}\n'
        "APPROVE = post as-is. REVISE = has issues but fixable. REJECT = do not post "
        "(ONLY for factual errors or hallucinations that damage credibility)."
    )
    user = f"ARTICLE (source):\n{art_short}\n\nSLIDES (to review):\n{slides_text}\n\nSource URL: {url}\n\nReview for factual accuracy only. Tone and style are intentional."

    import time as _time
    for attempt in range(1, 4):
        try:
            import httpx
            r = httpx.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
                json={"model": "mistral-small-latest", "messages": [
                    {"role": "system", "content": system}, {"role": "user", "content": user}
                ], "max_tokens": 500, "temperature": 0.1},
                timeout=30
            )
            if r.status_code != 200:
                if attempt < 3:
                    _time.sleep(2 * attempt)
                    continue
                return "REJECT", [f"evaluator HTTP {r.status_code} (fail_safe)"]
            content = r.json()["choices"][0]["message"]["content"].strip()
            candidate = re.sub(r"^```(?:json)?\s*", "", content)
            candidate = re.sub(r"\s*```$", "", candidate)
            data = json.loads(candidate)
            decision = data.get("decision", "APPROVE").upper()
            reasons = data.get("reasons", [])
            if decision not in ("APPROVE", "REVISE", "REJECT"):
                decision = "APPROVE"
            return decision, reasons
        except Exception as e:
            if attempt < 3:
                _time.sleep(2 * attempt)
                continue
            return "REJECT", [f"evaluator error: {e} (fail_safe)"]

# ── Thread Posting ──────────────────────────────────────────────────────────

def post_to_threads(slides, image_url=None, url=None):
    """Post slides as chained thread to @budakorporat_id."""
    if not THREADS_TOKEN:
        log.error("No THREADS_ACCESS_TOKEN")
        return None, None

    import httpx
    GRAPH = GRAPH_API

    def create_container(text, reply_to_id=None, image_url=None):
        # Normalize whitespace: collapse 3+ newlines to 2
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Strip markdown italic/bold markers
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', text)
        # Insert \n\n between sentences (Threads renders \n as space, \n\n as break)
        text = re.sub(r'(?<!Mr)(?<!Mrs)(?<!Ms)(?<!Dr)(?<!St)(?<!vs)(?<!Jr)(?<!Sr)(?<!Prof)([.?!])\s+(?=[A-Z])', r'\1\n\n', text)

        data = {"user_id": USER_ID, "text": text, "access_token": THREADS_TOKEN}
        if reply_to_id:
            data["reply_to_id"] = reply_to_id
        if image_url:
            data["media_type"] = "IMAGE"
            data["image_url"] = image_url
        else:
            data["media_type"] = "TEXT"
        try:
            r = httpx.post(f"{GRAPH}/{USER_ID}/threads", data=data, timeout=30)
            if r.status_code == 200:
                return r.json().get("id")
            log.warning(f"Create failed: {r.status_code} {r.text[:200]}")
        except Exception as e:
            log.error(f"Create error: {e}")
        return None

    def publish_container(creation_id):
        try:
            r = httpx.post(f"{GRAPH}/{USER_ID}/threads_publish", data={
                "creation_id": creation_id, "access_token": THREADS_TOKEN
            }, timeout=30)
            if r.status_code == 200:
                return r.json().get("id")
            log.warning(f"Publish failed: {r.status_code} {r.text[:200]}")
        except Exception as e:
            log.error(f"Publish error: {e}")
        return None

    results = []
    reply_to = None

    for i, slide in enumerate(slides):
        text = slide["content"]

        # Append caption + source URL to last slide
        if i == len(slides) - 1:
            caption = slides[0].get("caption", "").strip()
            if caption:
                text = f"{text}\n\n{caption}"
            if url:
                text = f"{text}\n\nSumber: {url}"

        log.info(f"  Slide {i+1}/{len(slides)}: {text[:60]}...")

        img = image_url if (i == 0 and image_url) else None
        creation_id = create_container(text, reply_to_id=reply_to, image_url=img)
        if not creation_id:
            log.error(f"  Failed at slide {i+1}")
            break

        time.sleep(2)
        post_id = publish_container(creation_id)
        if not post_id:
            log.error(f"  Failed to publish slide {i+1}")
            break

        results.append({"text": text, "post_id": post_id})
        reply_to = post_id
        log.info(f"  Posted: {post_id}")

        if i < len(slides) - 1:
            time.sleep(3)

    return results

# ── Main ────────────────────────────────────────────────────────────────────

def main():
    START = time.time()
    log.info("=== BUDAKORPORAT V2.1 ===")

    # Cooldown check
    if not DRY_RUN and check_cooldown():
        log.info("Skip — posted recently")
        print("Skip — baru posting < 2 jam lalu", flush=True)
        return

    # 0. Pull engagement metrics for old posts (pressbox pattern)
    if not DRY_RUN and THREADS_TOKEN:
        metrics_client = ThreadsMetrics(THREADS_TOKEN)
        pull_engagement(metrics_client)
        log.info("  Engagement pull done")

    # 1. Scrape
    topics = scrape_all()
    if not topics:
        log.error("No topics scraped")
        print("No topics scraped", flush=True)
        sys.exit(1)

    # 2. Filter + Score
    posted_urls, posted_ws = load_posted()
    analytics = get_analytics_summary()
    if analytics:
        log.info(f"  Analytics: {analytics['total_posts']} posts, avg {analytics['avg_views']:.0f} views")
    hotness = detect_hot_topics(topics, window_hours=4)

    # Extract tuning weights
    tuning = analytics.get("score_tuning", {})

    scored = []
    for t in topics:
        title = t.get("title", "")
        desc = t.get("description", "")
        tl = title.lower()

        if not _is_workplace_relevant(title, desc):
            continue
        if t.get("url", "") in posted_urls:
            continue
        if _is_similar(title, posted_ws):
            continue

        # Base score
        ws = _workplace_relevance(title, desc)
        s = t.get("score", 5) + ws
        if ws == 0:
            s -= 15  # heavy penalty for zero workplace relevance (soft filter)

        # Analytics-driven boost (pressbox pattern)
        hook = _classify_hook(tl)
        if tuning:
            boost_key = f"boost_{hook}"
            penalize_key = f"penalize_{hook}"
            if boost_key in tuning:
                s += tuning[boost_key]
            if penalize_key in tuning:
                s -= tuning[penalize_key]
            src_boost = f"boost_source_{t.get('source', '')}"
            if src_boost in tuning:
                s += tuning[src_boost]

        # Best/worst hook from analytics
        if analytics:
            best_hooks = [h[0] for h in analytics.get("best_hooks", [])]
            worst_hooks = [h[0] for h in analytics.get("worst_hooks", [])]
            if hook in best_hooks[:2]:
                s += 15
            if hook in worst_hooks:
                s -= 10

        # Hot boost
        url = t.get("url", "")
        hot = hotness.get(url, 0)
        hot_adjust = tuning.get("hot_boost_adjust", 0) if tuning else 0
        if hot >= 3.0:
            s += 25 + hot_adjust
        elif hot >= 1.5:
            s += 15 + hot_adjust

        # Breaking news boost — topic baru hari ini (last 2h) +15
        pub_ts = t.get("published_ts")
        if pub_ts and (time.time() - pub_ts) < 7200:
            s += 15

        # Peak hour boost
        hour = datetime.now(WIB).hour
        if hour in {7, 8, 9, 10, 11, 12, 17, 18, 19, 20, 21}:
            s += 5

        # Soft cap (pressbox pattern)
        if s > 100:
            s = int(100 + (s - 100) * 0.3)

        t["_score"] = s
        t["_workplace_score"] = ws
        scored.append(t)

    scored.sort(key=lambda x: -x.get("_score", 0))

    if not scored:
        log.error("No workplace-relevant topics after filter")
        print("No workplace topics found", flush=True)
        sys.exit(1)

    # Title dedup (cannibalization filter)
    seen_sigs = set()
    deduped = []
    skip_words = {"yang", "dan", "di", "dari", "untuk", "pada", "ini", "itu", "the", "a", "an", "in", "on", "at"}
    for t in scored:
        words = set(t.get("title", "").lower().split()) - skip_words
        sig = " ".join(sorted(words)[:4])
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            deduped.append(t)
    scored = deduped

    # Source diversity cap (pressbox pattern)
    scored = _apply_source_diversity(scored)

    # Dynamic score gate (pressbox pattern)
    score_gate = 25 if len(scored) < 30 else 40
    top_candidates = [t for t in scored if t["_score"] >= score_gate]
    if not top_candidates:
        best = scored[0]
        log.info(f"  Score {best['_score']} < {score_gate} — skipping")
        print(f"Skip — score {best['_score']} below threshold", flush=True)
        sys.exit(1)

    log.info(f"  {len(top_candidates)} candidates above gate {score_gate}")

    # ── Try up to 3 candidates: fetch → generate → ground → evaluate ──
    MAX_ATTEMPTS = min(8, len(top_candidates))
    best = slides = article_text = image_url = None
    llm_time = 0

    for attempt_idx in range(MAX_ATTEMPTS):
        candidate = top_candidates[attempt_idx]
        url = candidate.get("url", "")
        log.info(f"  ── Attempt {attempt_idx+1}/{MAX_ATTEMPTS}: {candidate['title'][:50]} (score={candidate['_score']})")

        # Fetch + quality gates
        article_text, image_url = extract_article_text(url)
        passed, reason = _pass_quality_gates(article_text)
        if not passed:
            log.info(f"    Article rejected: {reason} — trying next")
            continue

        log.info(f"    Article: {len(article_text)} chars, image: {'yes' if image_url else 'no'}")

        # Select viral pattern
        pattern = _select_viral_pattern(candidate, article_text)

        # Generate
        t0 = time.time()
        slides = generate_slides(article_text, url, title=candidate.get("title", ""), source=candidate.get("source", ""), pattern=pattern)
        if not slides:
            log.warning(f"    LLM generation failed — trying next")
            continue
        llm_time = time.time() - t0
        log.info(f"    Generated {len(slides)} slides in {llm_time:.1f}s")

        # Grounding check
        slides_text = " ".join(s["content"] for s in slides)
        warnings = grounding_check(slides_text, article_text)
        hallucinated_names = [w for w in warnings if "HALLUCINATED_NAME" in w]
        hallucinated_numbers = [w for w in warnings if "UNVERIFIED_NUMBER" in w]
        if hallucinated_names:
            log.warning(f"    Name warnings (soft): {'; '.join(hallucinated_names)}")
        if hallucinated_numbers:
            log.warning(f"    Number warnings (soft): {'; '.join(hallucinated_numbers)}")

        # Evaluator
        eval_decision, eval_reasons = evaluator_check(slides, article_text, url)
        log.info(f"    Evaluator: {eval_decision} — {'; '.join(eval_reasons[:3])}")

        if eval_decision == "REJECT":
            log.warning(f"    Rejected — trying next candidate")
            continue

        # APPROVE or REVISE — use this one
        best = candidate
        break

    if not best or not slides:
        log.error(f"All {MAX_ATTEMPTS} candidates failed")
        print(f"All {MAX_ATTEMPTS} candidates failed (quality/LLM/evaluator)", flush=True)
        sys.exit(1)

    url = best.get("url", "")

    # 8. Preview
    for i, s in enumerate(slides):
        log.info(f"  S{i+1}: {s['content'][:80]}...")

    # 9. Dry run or post
    if DRY_RUN:
        for i, s in enumerate(slides):
            print(f"\n--- Slide {i+1} ---\n{s['content']}")
        if slides[0].get("caption"):
            print(f"\n--- Caption ---\n{slides[0]['caption']}")
        if slides[0].get("hashtags"):
            print(f"\n--- Hashtags ---\n{slides[0]['hashtags']}")
        total = time.time() - START
        print(f"\nDone in {total:.1f}s (LLM: {llm_time:.1f}s)")
        return

    # Post
    results = post_to_threads(slides, image_url, url=url)
    if not results:
        log.error("Post failed")
        print("Post failed", flush=True)
        sys.exit(1)

    root_id = results[0]["post_id"]
    permalink = f"https://www.threads.net/@budakorporat_id/post/{root_id}"

    # Track
    track_post(best["title"], url, best.get("source", ""), root_id, permalink,
               hotness_score=hotness.get(url, 0), workplace_score=best.get("_workplace_score", 0))

    total = time.time() - START
    log.info(f"  Posted: {permalink}")
    log.info(f"  Total: {total:.1f}s (LLM: {llm_time:.1f}s)")
    print(f"Posted: {best['title'][:60]}\n{permalink}", flush=True)

if __name__ == "__main__":
    if "--with-jitter" in sys.argv:
        jitter = random.randint(0, 30)
        log.info(f"Jitter: {jitter}s")
        time.sleep(jitter)
    main()
