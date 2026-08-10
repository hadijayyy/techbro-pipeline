#!/usr/bin/env python3
"""
Techbro v3 — EKONOMI NASIONAL + POV PRIBADI + 6 Script Hack Elements
Article-based: scrape economy RSS/HTML → 6 threads with personal POV.
"""

import html, httpx, json, logging, os, random, re, struct, sys, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))
from email.utils import parsedate_to_datetime
from pathlib import Path
from bs4 import BeautifulSoup

# ── CLI Flags ────────────────────────────────────────────────────────────────

IMAGE_URL = None
for i, a in enumerate(sys.argv):
    if a == "--image-url" and i + 1 < len(sys.argv):
        IMAGE_URL = sys.argv[i + 1]
        break
IMAGE_DISABLED = "--no-image" in sys.argv
DRY_RUN = "--dry-run" in sys.argv
PREPARE_NEXT = "--prepare-next" in sys.argv
HOT_TOPIC_LIMIT = 15
LLM_REQUEST_BUDGET = 4  # writer/verifier plus one revision/verifier; transport retries disabled.

# ── Paths ────────────────────────────────────────────────────────────────────

BASE = Path(__file__).parent
POSTED_FILE = BASE / "posted_topics_v2.json"
HOT_TOPICS_FILE = BASE / "hot_today.json"
KEYWORDS_FILE = BASE / "keywords.json"
SOURCES_FILE = BASE / "sources.json"
PREPARED_ARTICLE_FILE = BASE / "prepared_article.json"
INFLIGHT_FILE = BASE / "inflight_chain.json"

# ── Env ───────────────────────────────────────────────────────────────────────

GRAPH = "https://graph.threads.net/v1.0"
THREADS_TOKEN = None
try:
    from dotenv import load_dotenv
    # Cron/Hermes may inherit stale secrets; project .env is source of truth.
    load_dotenv(BASE / ".env", override=True)
    THREADS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN")
except Exception:
    pass

SZEJAY_BOT_TOKEN = os.getenv("SZEJAY_BOT_TOKEN")
SZEJAY_CHAT_ID = "8771306538"

THREADS_USER_ID = None
if THREADS_TOKEN and not DRY_RUN:
    try:
        r = httpx.get(f"{GRAPH}/me?access_token={THREADS_TOKEN}", timeout=10)
        if r.status_code == 200:
            THREADS_USER_ID = r.json().get("id")
    except Exception:
        pass

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S")
# httpx logs full request URLs at INFO, which leaks access_token in query strings.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("techbro-v3")

# ── Keyword Loader ───────────────────────────────────────────────────────────

def load_keywords():
    """Load scoring keywords from keywords.json. Returns dict with all keyword lists."""
    defaults = {
        "score_categories": [],
        "entity_boost": {},
        "number_bonus": [],
        "hard_reject": [],
        "soft_reject": [],
        "named_blacklist": [],
        "score_thresholds": {"reject": 45, "backup": 60, "process": 60, "priority": 75},
    }
    try:
        with open(KEYWORDS_FILE) as f:
            kw = json.load(f)
            for k in defaults:
                defaults[k] = kw.get(k, defaults[k])
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log.warning(f"keywords.json not found ({e}) — using built-in defaults")
    return defaults


KW = load_keywords()

# ── Economy Sources ──────────────────────────────────────────────────────────

def load_sources():
    """Load only maintained sources; malformed config fails closed."""
    try:
        data = json.loads(SOURCES_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    required = {"url", "score", "type", "domain"}
    return {
        name: cfg for name, cfg in data.items()
        if isinstance(name, str) and isinstance(cfg, dict)
        and required <= set(cfg) and cfg["type"] in {"rss", "html"}
    }


SOURCES = load_sources()
MAX_ARTICLES_PER_SOURCE = 6

# ── Scoring Configuration (loaded from keywords.json) ──────────────────────────

SCORE_CATEGORIES = KW["score_categories"]
ENTITY_BOOST = KW["entity_boost"]
NUMBER_BONUS = KW["number_bonus"]
HARD_REJECT = KW["hard_reject"]
SOFT_REJECT = KW["soft_reject"]
NAMED_BLACKLIST = KW["named_blacklist"]
SCORE_THRESHOLDS = KW["score_thresholds"]

# ── Number Parsing ──────────────────────────────────────────────────────────────

def _parse_number_in_title(title):
    """Extract largest Rp amount from title for bonus calculation."""
    big_matches = re.findall(r'rp\s*(\d[\d,.]*)\s*(triliun|miliar|juta)', title.lower())
    if not big_matches:
        big_matches = re.findall(r'(\d[\d,.]*)\s*(triliun|miliar|juta)\s*rupiah', title.lower())
    multipliers = {"triliun": 1e12, "miliar": 1e9, "juta": 1e6}
    max_val = 0
    for val_str, unit in big_matches:
        val = float(val_str.replace('.', '').replace(',', '.'))
        max_val = max(max_val, val * multipliers.get(unit, 1))
    return max_val

# ── Data Persistence ─────────────────────────────────────────────────────────

def load_data():
    try:
        return json.loads(POSTED_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"topics": [], "recent_content": {"urls": [], "openings": [], "ctas": []}}

def save_data(data):
    tmp = POSTED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(POSTED_FILE)

def load_inflight():
    try:
        data = json.loads(INFLIGHT_FILE.read_text())
        return data if isinstance(data, dict) and data.get("posts") else None
    except (OSError, json.JSONDecodeError):
        return None

def save_inflight(data):
    tmp = INFLIGHT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(INFLIGHT_FILE)


def _publish_complete(pub, posts):
    """Only a complete chain may enter dedup/analytics state."""
    expected = sum(1 for text in posts.values() if text)
    return bool(pub and not pub.get("error") and len(pub.get("post_ids", [])) == expected)


def send_success_report(title, pattern, elapsed, permalink):
    """Best-effort Telegram report after a complete live Threads chain."""
    if not SZEJAY_BOT_TOKEN:
        log.warning("Success report skipped: SZEJAY_BOT_TOKEN missing")
        return False
    text = (f"✅ v3 Posted @ {datetime.now(WIB):%H:%M} WIB\n{title}\n"
            f"Pattern: {pattern} | {elapsed:.1f}s\n{permalink}")
    payload = json.dumps({"chat_id": SZEJAY_CHAT_ID, "text": text}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{SZEJAY_BOT_TOKEN}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read())
        if result.get("ok"):
            log.info("Success report sent to @szejay_bot")
            return True
        log.warning("Success report rejected by Telegram: %s", result.get("description"))
    except Exception as e:
        log.warning(f"Success report failed: {e}")
    return False


def threads_permalink(post_id):
    """Resolve canonical Threads URL without blocking successful state persistence."""
    try:
        r = httpx.get(f"{GRAPH}/{post_id}", params={"fields": "permalink", "access_token": THREADS_TOKEN}, timeout=10)
        if r.status_code == 200 and r.json().get("permalink"):
            return r.json()["permalink"]
    except Exception as e:
        log.warning(f"Permalink lookup failed: {e}")
    return f"https://www.threads.com/@ryanhadiii/post/{post_id}"


def load_prepared_article(posted_urls):
    """Load one immutable, validated draft; stale data never reaches publishing."""
    try:
        article = json.loads(PREPARED_ARTICLE_FILE.read_text())
        stale = article.get("url") in posted_urls or time.time() > article.get("expires_at", 0)
        if stale:
            if not DRY_RUN:
                PREPARED_ARTICLE_FILE.unlink(missing_ok=True)
            return None
        required = ("title", "url", "body", "og_image", "posts", "prepared_at", "expires_at")
        if not all(article.get(k) for k in required):
            return None
        posts = article["posts"]
        if not isinstance(posts, dict) or deterministic_validate(posts) or deterministic_grounding_validate(article, posts):
            return None
        return article
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def save_prepared_article(article, result, image_url):
    """Persist only a fully validated six-slide draft for one later publish."""
    payload = dict(article)
    payload.update({"posts": result["posts"], "angle": result.get("angle", ""),
                    "arc": result.get("arc", "market_shock"), "og_image": image_url,
                    "prepared_at": time.time(), "expires_at": time.time() + 86400})
    tmp = PREPARED_ARTICLE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    tmp.replace(PREPARED_ARTICLE_FILE)


def _topic_entities(title):
    """Editorial entities. Two shared entities identify a repeated story."""
    text = title.lower()
    aliases = (
        ("mbg", ("mbg", "makan bergizi gratis")),
        ("mk", ("putusan mk", "mahkamah konstitusi", " mk ")),
        ("anggaran-pendidikan", ("anggaran pendidikan", "dana pendidikan")),
        ("apbn", ("apbn",)),
        ("spbu", ("spbu",)),
        ("pertamina", ("pertamina",)),
        ("bi", ("bi rate", "bank indonesia")),
        ("rupiah", ("rupiah",)),
    )
    return {name for name, words in aliases if any(word in text for word in words)}


def _title_words(title):
    stop = {"yang", "dan", "dari", "untuk", "dengan", "soal", "ini", "itu", "di", "ke", "pada", "buat", "akan", "sudah", "baru", "buka", "suara"}
    return {word for word in re.findall(r"[a-z0-9]{4,}", title.lower()) if word not in stop}


def _is_repeat_issue(title, topics, hours=72):
    """Pressbox-style dedup: 2 entities, or 1 entity plus 4 matching title words."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    entities, words = _topic_entities(title), _title_words(title)
    for topic in topics:
        try:
            published = datetime.fromisoformat(topic.get("timestamp", "")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if published < cutoff:
            continue
        previous = topic.get("title", "")
        shared_entities = entities & _topic_entities(previous)
        shared_words = words & _title_words(previous)
        # Diversity cap: one named policy/program/entity per 72h. Pressbox-style
        # similarity still catches repeats when an article has multiple entities.
        if shared_entities:
            return True, shared_entities, shared_words
    return False, set(), set()

# ── HTTP Helpers ─────────────────────────────────────────────────────────────

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

def _http_get(url, timeout=12):
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)
        return r.status_code, r.text
    except Exception:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            return r.status, r.read().decode("utf-8", errors="replace")
        except Exception:
            return 0, ""


def _canonical_url(url):
    """Drop tracking parameters so one article has one posted-state key."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))

# ── RSS Scraping ─────────────────────────────────────────────────────────────

def _title_is_junk(title):
    """Reject non-article titles: tickers, video prefixes, short codes, navigation."""
    t = title.strip()
    if len(t) < 20:
        return True
    # Market widgets / tickers
    if re.match(r'^[A-Z]{2,5}\d[\d,.]+\s*[-+]\d', t):
        return True
    if any(kw in t.lower() for kw in ("video:", "foto:", "live:", "infografis:", "live report")):
        return True
    # CNBC junk patterns
    if re.search(r'Market\d+\s+(jam|menit|detik)\s+yang\s+lalu', t, re.I):
        return True
    # Pure number-only titles
    if re.match(r'^[\d,.\s%+\-]+$', t):
        return True
    return False


def _scrape_rss(url, source, base_score):
    """Parse RSS feed → article dicts."""
    articles = []
    try:
        code, text = _http_get(url)
        if code != 200:
            return articles
        root = ET.fromstring(text)
        ns = {"media": "http://search.yahoo.com/mrss/",
              "content": "http://purl.org/rss/1.0/modules/content/"}
        for item in root.findall(".//item")[:15]:
            title = (item.findtext("title") or "").strip()
            link = _canonical_url((item.findtext("link") or "").strip())
            pub_str = item.findtext("pubDate") or ""
            pub_ts = 0
            if pub_str:
                try:
                    pub_ts = parsedate_to_datetime(pub_str).timestamp()
                except Exception:
                    pub_ts = time.time()
            # og:image from RSS media:content or media:thumbnail
            og_image = None
            mc = item.find("media:content", ns) or item.find("media:thumbnail", ns)
            if mc is not None:
                og_image = mc.get("url")
            if not title or not link or "/live/" in link or "/liveblog/" in link:
                continue
            if _title_is_junk(title):
                continue
            articles.append({
                "title": title, "url": link, "source": source,
                "score": base_score, "ts": pub_ts, "og_image": og_image,
            })
    except Exception as e:
        log.warning(f"RSS {source}: {e}")
    return articles

# ── HTML Scraping ────────────────────────────────────────────────────────────

def _scrape_html(url, source, base_score, domain):
    """Scrape front page HTML for article links."""
    articles = []
    try:
        code, text = _http_get(url)
        if code != 200:
            return articles
        soup = BeautifulSoup(text, "html.parser")
        seen = set()
        for a_tag in soup.find_all("a", href=True):
            href = _canonical_url(urllib.parse.urljoin(url, str(a_tag["href"]).strip()))
            if domain not in href:
                continue
            if "/live/" in href or "/liveblog/" in href:
                continue
            title = a_tag.get_text(strip=True)
            title = re.sub(r"\s+", " ", title).strip()
            if len(title) < 25 or _title_is_junk(title):
                continue
            if href in seen:
                continue
            seen.add(href)
            articles.append({
                "title": title, "url": href, "source": source,
                "score": base_score, "ts": time.time(), "og_image": None,
            })
    except Exception as e:
        log.warning(f"HTML {source}: {e}")
    # Dedup by title
    seen_titles = set()
    deduped = []
    for a in articles:
        sig = a["title"][:40].lower().strip()
        if sig not in seen_titles:
            seen_titles.add(sig)
            deduped.append(a)
    return deduped[:15]

# ── Scrape All Sources ───────────────────────────────────────────────────────

def scrape_all():
    """Scrape all sources in parallel. Returns sorted article list."""
    articles = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        fut_map = {}
        for name, cfg in SOURCES.items():
            if cfg["type"] == "rss":
                f = ex.submit(_scrape_rss, cfg["url"], name, cfg["score"])
            else:
                f = ex.submit(_scrape_html, cfg["url"], name, cfg["score"], cfg["domain"])
            fut_map[f] = name
        for f in as_completed(fut_map):
            try:
                articles.extend(f.result())
            except Exception as e:
                log.warning(f"Scrape {fut_map[f]}: {e}")
    # Filter mixed/homepage noise before the source cap; rank later still decides.
    articles = [a for a in articles if _has_economy_title_signal(a["title"])]
    from collections import defaultdict
    by_source = defaultdict(list)
    for a in articles:
        by_source[a["source"]].append(a)
    deduped = []
    for source, src_articles in by_source.items():
        src_articles.sort(key=lambda a: (a["ts"], len(a["title"])), reverse=True)
        deduped.extend(src_articles[:MAX_ARTICLES_PER_SOURCE])
    log.info(f"  Articles: {len(articles)} economy-title candidates after per-source cap")
    return deduped

# ── Economy Relevance Scoring ────────────────────────────────────────────────

def _matches_keyword(text, keyword):
    """Short terms require word boundaries; phrases keep natural substring matching."""
    return bool(re.search(rf"\b{re.escape(keyword)}\b", text)) if len(keyword) <= 4 else keyword in text


def _score_article(article):
    """Score article using new category-based system.
    Returns (score, reason) tuple where reason is short description.
    """
    title = article.get("title", "")
    tl = title.lower()
    score = 0
    signals = 0
    categories_hit = 0

    # Hard reject (instant fail)
    for kw in HARD_REJECT:
        if _matches_keyword(tl, kw):
            return (0, "hard_reject:" + kw)
    for name in NAMED_BLACKLIST:
        if _matches_keyword(tl, name):
            return (0, "blacklist:" + name)
    # Video reject — skip before body fetch/LLM
    if tl.startswith("video:") or "/video-" in article.get("url", ""):
        return (0, "video_article")
    if not _has_economy_title_signal(title):
        return (0, "out_of_scope")
    # Category scoring — one keyword counted once per category
    for cat_entry in SCORE_CATEGORIES:
        cat_name = cat_entry.get("name", "") if isinstance(cat_entry, dict) else cat_entry[0]
        cat_max = cat_entry.get("max_score", 0) if isinstance(cat_entry, dict) else cat_entry[1]
        cat_kws = cat_entry.get("keywords", []) if isinstance(cat_entry, dict) else cat_entry[2]
        cat_score = 0
        for kw in cat_kws:
            if _matches_keyword(tl, kw):
                cat_score += cat_max  # flat score per category, not per keyword
                signals += 1
                categories_hit += 1
                break
        score += min(cat_score, cat_max)

    # Entity boost
    tl_full = tl
    for tier_name, tier_data in ENTITY_BOOST.items():
        boost_val = tier_data.get("score", 0) if isinstance(tier_data, dict) else tier_data[0]
        entities = tier_data.get("names", []) if isinstance(tier_data, dict) else tier_data[1]
        for ent in entities:
            if _matches_keyword(tl_full, ent):
                score += boost_val
                signals += 1
                break

    # Bonus angka besar
    max_val = _parse_number_in_title(title)
    if max_val > 0:
        for bonus_entry in NUMBER_BONUS:
            lo = bonus_entry.get("min", 0) if isinstance(bonus_entry, dict) else bonus_entry[0]
            hi = bonus_entry.get("max", float('inf')) if isinstance(bonus_entry, dict) else bonus_entry[1]
            bonus = bonus_entry.get("score", 0) if isinstance(bonus_entry, dict) else bonus_entry[2]
            if lo <= max_val <= (hi or float('inf')):
                score += bonus
                break

    # Percentage bonus
    if '%' in title:
        score += 3

    # ponytail: dynamic keyword overlay retired; add back only with a maintained source and regression coverage.
    dynamic_hits = 0


    # Daily market moves are low-value unless title also signals policy or public impact.
    technical = any(_matches_keyword(tl, kw) for kw in ("rupiah", "ihsg", "saham", "harga emas", "harga minyak"))
    public_angle = any(_matches_keyword(tl, kw) for kw in ("kebijakan", "bi", "bank indonesia", "apbn", "pajak", "subsidi", "anggaran", "berlaku", "ditetapkan"))
    if technical and not public_angle:
        score -= 30

    # Routine SPBU price lists are utility updates, not Techbro analysis topics.
    # Keep structural fuel-policy stories such as B50 or subsidy changes eligible.
    routine_bbm = "bbm" in tl and "spbu" in tl
    fuel_policy = any(_matches_keyword(tl, kw) for kw in ("b50", "subsidi", "kebijakan", "aturan", "kuota", "alokasi", "apbn"))
    if routine_bbm and not fuel_policy:
        score -= 100

    # Soft reject penalty (cancelled by sufficient economy signals)
    if signals >= 2:
        pass  # strong signals override soft reject
    else:
        for kw in SOFT_REJECT:
            if _matches_keyword(tl, kw):
                score -= 60
                break

    return (score, f"cats={categories_hit} sig={signals} dyn={dynamic_hits}")

def _learning_bonus(data, source, pattern=None):
    """Bounded feedback. Never changes article/body/grounding gates."""
    stats = _compute_performance_stats(data)
    values = list(stats["source_avg"].values())
    if not values or stats["source_count"].get(source, 0) < 3:
        return 0.0
    baseline = sum(values) / len(values)
    score = stats["source_avg"].get(source, baseline)
    return max(-0.06, min(0.06, (score - baseline) * 2))


def _hot_topic_cluster(title, pattern):
    """Stable, explainable cluster key; never creates a claim from article text."""
    entities = sorted(_topic_entities(title))
    if entities:
        return "/".join(entities)
    words = sorted(_title_words(title))[:4]
    return "/".join(words) or (pattern or "other").lower()


def _indonesia_topic_relevance(title, body):
    """Classify body-backed national relevance; global stories need explicit Indonesia impact."""
    text = f"{title} {body}".lower()
    global_story = bool(re.search(r"\b(federal reserve|the fed|ecb|bank of japan|boj|pboc|opec|"
                                  r"minyak dunia|tarif dagang|perang dagang|sanksi ekonomi|"
                                  r"resesi global|ekonomi global|perdagangan global)\b", text))
    indonesia = bool(re.search(r"\b(indonesia|ri|rupiah|apbn|bank indonesia|bi|kemenkeu|ojk)\b", body, re.I))
    impact = bool(re.search(r"\b(dampak|berdampak|risiko|harga|inflasi|daya beli|ekspor|impor|"
                            r"investasi|konsumen|masyarakat|industri|bbm)\b", body, re.I))
    if global_story:
        return "global_indonesia_impact" if indonesia and impact else None
    return "national" if indonesia else None


def scout_hot_topics(articles, now=None, limit=HOT_TOPIC_LIMIT, per_source_limit=2, data=None):
    """Read-only body-verified top-15 ranking, one item per editorial cluster."""
    now = time.time() if now is None else now
    verified = []
    for candidate in articles:
        title, url, source = candidate.get("title", ""), candidate.get("url", ""), candidate.get("source", "")
        if not title or not url or not source:
            continue
        body, image, published_ts = _fetch_article_body(url)
        if not published_ts or published_ts > now + 300 or now - published_ts > 86400:
            continue
        eligible, reason = _is_eligible_candidate(title, body, source)
        if not eligible:
            continue
        indonesia_relevance = _indonesia_topic_relevance(title, body)
        if not indonesia_relevance:
            continue
        pattern, confidence = _classify_pattern(title, body)
        topic_score, economy_score, impact_score = _topic_score(title, body)
        source_quality = SOURCES.get(source, {}).get("score", candidate.get("score", 0))
        freshness = max(0.0, 24 - ((now - published_ts) / 3600)) / 24
        # Body evidence drives ranking. Source/learning cannot rescue weak evidence.
        hot_score = round(topic_score * 10 + confidence * 10 + freshness * 10 + source_quality + _learning_bonus(data or {}, source, pattern), 3)
        verified.append({
            "cluster": _hot_topic_cluster(title, pattern), "title": title,
            "canonical_url": _canonical_url(url), "source": source,
            "published_ts": published_ts, "pattern": pattern, "pattern_confidence": round(confidence, 3),
            "topic_score": topic_score, "economy_score": economy_score, "impact_score": impact_score,
            "hot_score": hot_score, "body_verified": True, "image_available": bool(image),
            "indonesia_relevance": indonesia_relevance, "reason": reason,
        })
    verified.sort(key=lambda item: item["hot_score"], reverse=True)
    selected, sources, clusters = [], {}, set()
    for item in verified:
        if item["source"] in sources and sources[item["source"]] >= per_source_limit:
            continue
        if item["cluster"] in clusters:
            continue
        sources[item["source"]] = sources.get(item["source"], 0) + 1
        clusters.add(item["cluster"])
        item["rank"] = len(selected) + 1
        selected.append(item)
        if len(selected) == limit:
            break
    return selected


def save_hot_topics(topics, generated_ts=None):
    payload = {"generated_ts": generated_ts or time.time(), "topics": topics}
    tmp = HOT_TOPICS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    tmp.replace(HOT_TOPICS_FILE)


def _publish_candidates_from_hot_topics(articles, topics):
    """Return only body-verified scout choices, in editorial rank order."""
    by_url = {_canonical_url(article.get("url", "")): article for article in articles}
    return [by_url[topic["canonical_url"]] for topic in topics
            if topic.get("canonical_url") in by_url]


def _pick_article(articles, posted_urls, data=None):
    """Pick best unscraped economy article. Learning only makes a bounded ranking adjustment."""
    now = time.time()
    candidates = [a for a in articles if a["url"] not in posted_urls]
    if not candidates:
        return None

    # Clean title
    for a in candidates:
        a["title"] = re.sub(r'^\d+', '', a["title"]).strip()
        a["title"] = re.sub(r'(Energi|Ekbis|Bisnis|Keuangan|Finance|Ekonomi|Nasional|Market)\d{2}/\d{2}/\d{4}$', '', a["title"]).strip()
        a["title"] = re.sub(r'(Energi|Ekbis|Bisnis|Keuangan|Finance|Ekonomi|Nasional)$', '', a["title"]).strip()
    # Score each candidate
    for a in candidates:
        eco_score, reason = _score_article(a)
        a["eco_score"] = eco_score
        a["_reason"] = reason
        # Freshness: 24h = +15, 25-48h = +10, 3-7d = +5, >7d = 0, republish = -30
        age_hours = (now - a["ts"]) / 3600
        if age_hours <= 24:
            freshness = 15
        elif age_hours <= 48:
            freshness = 10
        elif age_hours <= 168:  # 7 days
            freshness = 5
        else:
            freshness = 0
        # Indonesia relevance: default +10 for local sources
        relevance = 10
        # Source quality: base from SOURCES config
        src_cfg = SOURCES.get(a["source"], {})
        source_quality = src_cfg.get("score", 5)
        # Learning is capped. It cannot rescue an editorially weak candidate.
        learning = _learning_bonus(data or {}, a["source"], a.get("pattern"))
        a["learning_bonus"] = learning
        a["_weight"] = eco_score + freshness + relevance + source_quality + learning
    candidates.sort(key=lambda a: a["_weight"], reverse=True)
    log.debug("Top 5:")
    for i, a in enumerate(candidates[:5]):
        log.debug(f"  {i+1}. [s={a['eco_score']}] {a['title'][:60]} (w={a['_weight']})")
    # ponytail: title ranks only. Body/editorial/image gates decide publishability.
    return candidates[0]

# ── Article Body + Image ─────────────────────────────────────────────────────

def _hd_image_url(url):
    """Request a 1200px rendition from known CDN URLs."""
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    if any(host in parts.netloc for host in ("akcdn.detik.net.id", "awsimages.detik.net.id", "cdn.detik.net.id")):
        query["w"] = "1200"
        query["q"] = "90"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                    urllib.parse.urlencode(query), parts.fragment))


def _image_size(data):
    """Return JPEG/PNG dimensions without adding an image dependency."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if not data.startswith(b"\xff\xd8"):
        return None
    pos = 2
    while pos + 9 < len(data):
        if data[pos] != 0xff:
            pos += 1
            continue
        marker = data[pos + 1]
        pos += 2
        if marker in (0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
            continue
        size = struct.unpack(">H", data[pos:pos + 2])[0]
        if 0xc0 <= marker <= 0xc3 or 0xc5 <= marker <= 0xc7 or 0xc9 <= marker <= 0xcb or 0xcd <= marker <= 0xcf:
            return struct.unpack(">HH", data[pos + 3:pos + 7])[::-1]
        pos += size
    return None


def validate_article_image(url):
    """Require a real HD article lead image; tolerate 1px CDN rounding."""
    try:
        response = httpx.get(url, timeout=15, follow_redirects=True)
        size = _image_size(response.content) if response.status_code == 200 else None
        if size and size[0] >= 1200 and size[1] >= 669:
            return url
        log.warning(f"Reject non-HD article image: {size or 'unknown'} {url[:80]}")
    except httpx.RequestError as e:
        log.warning(f"Validate article image failed: {e}")
    return None

def _image_hint(url):
    """Extract a short visual hint from image URL path for S1 hook context."""
    if not url:
        return ""
    try:
        path = urllib.parse.urlsplit(url).path.lower()
        hints = []
        for kw, label in [
            ("ilustrasi", "ilustrasi"), ("pasar", "pasar"), ("pabrik", "pabrik"),
            ("buruh", "buruh/pekerja"), ("petani", "petani"), ("nelayan", "nelayan"),
            ("jalan", "jalan raya"), ("pelabuhan", "pelabuhan"), ("tambang", "tambang"),
            ("perkantoran", "kantor"), ("uang", "uang/transaksi"), ("bank", "bank"),
            ("menteri", "pejabat"), ("presiden", "presiden"), ("rapat", "rapat"),
            ("beras", "beras/pangan"), ("bbm", "BBM/SPBU"),
            ("listrik", "listrik/PLN"), ("sekolah", "sekolah"), ("rumah", "perumahan"),
        ]:
            if kw in path:
                hints.append(label)
        return ", ".join(hints[:3]) if hints else "foto berita"
    except Exception:
        return ""


def _published_timestamp(soup):
    """Read standard article publication fields; unknown formats remain untrusted."""
    values = [
        tag.get("content") for tag in soup.find_all("meta")
        if re.search(r"(?:publishdate|datepublished|pubdate|published_time)",
                     str(tag.get("name") or tag.get("property") or ""), re.I)
    ]
    values += [tag.get("datetime") for tag in soup.find_all("time")]
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("datePublished"):
                values.append(item["datePublished"])
    for value in values:
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=WIB)).timestamp()
        except ValueError:
            try:
                return parsedate_to_datetime(str(value)).timestamp()
            except (TypeError, ValueError):
                try:
                    return datetime.strptime(str(value), "%Y/%m/%d %H:%M:%S").replace(tzinfo=WIB).timestamp()
                except ValueError:
                    pass
    return 0


def _fetch_article_body(url):
    """Fetch article HTML, extract clean text + og:image + source publish time."""
    og_image = None
    published_ts = 0
    text = ""
    try:
        code, raw = _http_get(url, timeout=15)
        if code != 200:
            return "", None, 0
        soup = BeautifulSoup(raw, "html.parser")
        published_ts = _published_timestamp(soup)
        # og:image — logos are not lead images, fall through to body images.
        og_tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
        if og_tag and og_tag.get("content"):
            og_candidate = _hd_image_url(og_tag["content"])
            og_alt = og_tag.get("alt") or ""
            if not re.search(r"logo|favicon|icon|placeholder", og_candidate, re.I) and not re.search(r"logo|favicon|icon", og_alt, re.I):
                og_image = validate_article_image(og_candidate)
                # Some CDNs mirror og:image under a different path with a real alt on the <img>.
                # Reject if any <img> with the same filename is marked as a logo.
                if og_image:
                    og_stem = urllib.parse.urlsplit(og_image).path.rsplit("/", 1)[-1].split("?", 1)[0]
                    for img_tag in soup.find_all("img"):
                        img_alt = str(img_tag.get("alt") or "")
                        img_src = str(img_tag.get("src") or img_tag.get("data-src") or "")
                        if og_stem and og_stem in img_src and re.search(r"logo|favicon|icon|placeholder", img_alt, re.I):
                            log.warning(f"Reject logo-as-og:image: {og_stem} alt='{img_alt[:40]}'")
                            og_image = None
                            break
        # article body — try known ID selectors
        body_el = (
            soup.find("div", class_=lambda c: c and "detail__body-text" in c)
            or soup.find("div", class_=lambda c: c and "detail__body" in c)
            or soup.find("div", class_=lambda c: c and ("article-body" in c or "article_content" in c))
            or soup.find("div", class_=lambda c: c and "read__content" in c)
            or soup.find("div", class_=lambda c: c and "content-detail" in c)
            or soup.find("article")
            or soup.find("main")
        )
        if not body_el:
            return "", og_image, published_ts
        # Fallback: og:image was missing/logo — find the first real photo in the article body.
        if not og_image:
            for img_tag in body_el.find_all("img"):
                src = str(img_tag.get("src") or img_tag.get("data-src") or "")
                alt = str(img_tag.get("alt") or "")
                if not src or re.search(r"logo|favicon|icon|placeholder|avatar", src + " " + alt, re.I):
                    continue
                candidate = validate_article_image(_hd_image_url(src))
                if candidate:
                    og_image = candidate
                    break
        for tag in body_el.find_all(["script", "style", "nav", "aside", "footer", "form"]):
            tag.decompose()
        for tag in body_el.find_all(class_=lambda c: c and any(x in " ".join(c if isinstance(c, list) else [c]).lower() for x in ("related", "recommend", "read-more", "advert", "author", "share"))):
            tag.decompose()
        paras = []
        for p in body_el.find_all("p"):
            txt = p.get_text(separator=" ", strip=True)
            if len(txt) > 20:
                paras.append(txt)
        if not paras:
            raw = body_el.get_text(separator="\n", strip=True)
            paras = [l.strip() for l in raw.split("\n") if len(l.strip()) > 40]
        text = "\n".join(paras)
        text = text if len(text) > 200 else ""
    except Exception as e:
        log.warning(f"Fetch body: {url[:60]} — {e}")
    return text, og_image, published_ts


ECONOMY_TITLE_SIGNALS = (
    "ekonomi", "anggaran", "pajak", "subsidi", "bansos", "inflasi", "defisit",
    "utang", "rupiah", "dolar", "saham", "ihsg", "bi rate", "suku bunga",
    "dividen", "setor", "pnbp", "penerimaan negara", "apbn", "apbd", "bumn", "investasi", "ekspor", "impor", "dagang",
    "phk", "pekerja", "buruh", "upah", "gaji", "pabrik", "industri",
    "harga", "bbm", "listrik", "pangan", "minyak", "emas", "kredit",
    "bank", "ojk", "bi ", "kemenkeu", "sri mulyani", "danantara",
    "prabowo", "menteri", "presiden", "kebijakan", "regulasi", "tarif",
    "insentif", "cadangan devisa", "neraca", "resesi", "the fed",
    "hilirisasi", "perikanan", "perkebunan", "pertanian", "petani",
    "nelayan", "tambang", "mineral", "batu bara", "nikel", "gas",
    "konstruksi", "infrastruktur", "jalan tol", "kereta", "pelabuhan",
    "bandara", "proyek", "realisasi", "lelang", "tender",
    "pdb", "pertumbuhan", "konglomerat", "taipan", "kongsi",
    "bursa", "bei", "dividen", "laba", "rugi", "bangkrut",
    "pasar modal", "reksadana", "obligasi", "sbn", "sukuk",
    "perbankan", "fintech", "kripto", "startup", "unicorn",
    "koperasi", "asuransi", "dapen", "pensiun",
    "sawit", "beras", "jagung", "kedelai", "gula", "daging",
    "tekstil", "otomotif", "elektronik", "semen", "baja",
    "kemenhub", "audit", "keselamatan", "transportasi",
    "china", "rusia", "jepang", "asing", "penjajakan",
    "emiten", "digugat", "direktur", "miliar", "triliun",
    "garap", "railway", "trans", "gugat", "sengketa",
    "keuangan", "bpk", "fiskal", "penerimaan", "belanja",
    "pembiayaan", "neraca", "cadangan", "laporan",
)


ECONOMY_SELECTION_SIGNALS = (
    "ekonomi", "anggaran", "pajak", "subsidi", "bansos", "inflasi", "defisit", "utang",
    "rupiah", "dolar", "saham", "ihsg", "bi rate", "suku bunga", "apbn", "apbd", "bumn",
    "investasi", "ekspor", "impor", "phk", "pekerja", "buruh", "upah", "gaji", "pabrik",
    "industri", "harga", "bbm", "listrik", "pangan", "kredit", "bank", "ojk", "kemenkeu",
    "kebijakan", "regulasi", "tarif", "insentif", "hilirisasi", "perdagangan", "keuangan",
    "penerimaan", "belanja", "pembiayaan", "perbankan", "asuransi", "koperasi",
)


def _has_economy_title_signal(title):
    """Keep mixed/general feeds from consuming economy-pipeline LLM attempts."""
    title_lower = title.lower()
    return any(re.search(rf"(?<!\w){re.escape(signal)}(?!\w)", title_lower)
               for signal in ECONOMY_SELECTION_SIGNALS)


def _is_eligible_candidate(title, body, source):
    """Full economy gate shared by main pick and retry path.
    Returns (eligible: bool, reason: str)."""
    title_lower = title.lower()
    if any(phrase in title_lower for phrase in (
        "cara ", "syarat ", "saldo minimal", "daftar harga", "festival",
        "program sosial", "seremoni",
    )):
        return False, "utility_or_ceremony"
    if not body or len(body) < 500:
        return False, "body too short"
    if not _has_economy_title_signal(title):
        return False, "title has no economy signal"
    global_ok = source != "cnn_global" or _is_global_finance_story(title, body)
    if not global_ok:
        return False, "non-finance global story"
    if _is_routine_market_story(title, body):
        return False, "routine market story"
    if _is_empty_commentary(title, body):
        return False, "empty commentary"
    if not _is_techbro_relevant(body):
        return False, "not techbro relevant"
    topic_score, economy_score, impact_score = _topic_score(title, body)
    pattern_name, pattern_confidence = _classify_pattern(title, body)
    # ponytail: patterns rank/hooks only; evidence gates above decide eligibility.
    pattern_reason = (f"pattern={pattern_name} conf={pattern_confidence:.2f}"
                      if pattern_name else "pattern=none")
    return True, f"{pattern_reason} topic={topic_score} economy={economy_score} impact={impact_score}"


def _is_techbro_relevant(body):
    """Require a concrete Indonesia or global finance/economy signal in article body."""
    return bool(re.search(
        r"\b(indonesia|ri|rupiah|apbn|anggaran|pajak|subsidi|bansos|"
        r"pemerintah indonesia|presiden|mahkamah konstitusi|mk|kemenkeu|"
        r"bank indonesia|bi|ojk|bpk|dpr|federal reserve|the fed|ecb|bank sentral eropa|"
        r"bank of japan|boj|bank rakyat china|pboc|opec|harga minyak dunia|tarif dagang|"
        r"perang dagang|sanksi ekonomi|resesi global|ekonomi global|perdagangan global|"
        r"jakarta|surabaya|bandung|medan|semarang|makassar|palembang|"
        r"kalimantan|sumatera|sulawesi|papua|maluku|bali|nusa tenggara|"
        r"menteri|kementerian|direktur jenderal|gubernur|bupati|walikota|"
        r"dpr ri|dprd|kpk|kejaksaan|mahkamah agung|bumn|bumd)\b",
        body, re.IGNORECASE,
    ))


def _is_global_finance_story(title, body):
    """Global desk is for an explicit economy/finance headline, never general geopolitics."""
    headline = title.lower()
    return any(word in headline for word in (
        "fed", "federal reserve", "ecb", "bank sentral", "suku bunga", "inflasi",
        "resesi", "gdp", "ekonomi", "tarif", "dagang", "opec", "minyak",
        "pasar", "saham", "obligasi", "dolar", "mata uang", "utang", "investasi",
    )) and _is_techbro_relevant(body)


def _is_routine_market_story(title, body):
    """Daily market moves need an Indonesia policy/public-money angle."""
    text = f"{title} {body}".lower()
    headline = title.lower()
    # Market MOVEMENT keywords only — skip "pemegang saham", "mata uang" etc.
    market_movement = any(word in text for word in (
        "rupiah melemah", "rupiah menguat", "kurs rupiah", "ihsg", "harga emas", "harga minyak",
    ))
    market_headline = any(word in headline for word in (
        "saham naik", "saham turun", "saham anjlok", "saham melonjak",
        "indeks saham", "bursa saham", "harga saham", "saham hari ini",
    ))
    market = market_movement or market_headline
    # Policy evidence often sits in article body, not headline.
    policy = any(word in text for word in (
        "bi rate", "bank indonesia", "makroprudensial", "apbn", "anggaran", "pajak",
        "subsidi", "peraturan", "kebijakan", "ditetapkan", "putusan", "bpk", "ojk",
    ))
    return market and not policy


# ── Pressbox-style Pattern Classification ──────────────────────────────────────
# 4 PINDAR patterns with keyword triggers + priority ordering.
# Priority: KORUPSI > KEBIJAKAN > PROYEK > PASAR
# Pattern determines candidate selection priority AND S1 hook style in LLM prompt.

ECONOMY_PATTERNS = {

    "KORUPSI": {
        "priority": 1,
        "label": "Korupsi & Skandal",
        "desc": "Korupsi, suap, gratifikasi, temuan BPK, rugikan negara — viral, high engagement",
        "keywords": [
            "korupsi", "suap", "gratifikasi", "pencucian uang", "tppu",
            "kpk", "kejagung", "kejaksaan agung", "bareskrim",
            "rugikan negara", "kerugian negara", "merugikan negara",
            "temuan bpk", "audit bpk", "opini wtp", "catatan bpk", "wtp", "catatan", "bpk",
            "penyelewengan", "penyimpangan", "mark-up", "mark up",
            "proyek fiktif", "fiktif", "gelapkan", "penggelapan",
            "tersangka", "ditahan", "divonis", "dakwaan",
            "sita", "sitaan", "aset sitaan", "pemblokiran",
            "buron", "cekal", "red notice", "interpol",
            "saksi", "alat bukti", "sadap", "obar",
        ],
    },
    "KEBIJAKAN": {
        "priority": 2,
        "label": "Kebijakan & Aturan Baru",
        "desc": "Peraturan, putusan, kebijakan pemerintah — siapa kena dampak",
        "keywords": [
            "kebijakan", "regulasi", "peraturan", "putusan", "aturan",
            "disahkan", "ditetapkan", "berlaku", "dicabut", "direvisi",
            "dividen", "setor", "pnbp", "penerimaan negara", "apbn", "apbd", "anggaran negara", "anggaran daerah",
            "insentif", "keringanan", "pembebasan", "penghapusan",
            "larangan", "pembatasan", "moratorium",
            "impor", "ekspor", "bea masuk", "larangan ekspor",
            "hilirisasi", "larangan ekspor bahan mentah",
            "deregulasi", "omnibus law", "uu cipta kerja",
            "perppu", "perpres", "permen", "kepmen",
        ],
    },
    "PROYEK": {
        "priority": 3,
        "label": "Proyek & Infrastruktur",
        "desc": "Proyek besar, infrastruktur, investasi asing — lapangan kerja & kontrak",
        "keywords": [
            "infrastruktur", "proyek", "konstruksi", "pembangunan",
            "investasi", "penanaman modal", "pma", "pmdn",
            "kereta", "railway", "trans", "jalan tol", "pelabuhan", "bandara",
            "bendungan", "waduk", "irigasi", "plta", "plt",
            "realisasi", "groundbreaking", "peresmian", "rampung",
            "tender", "lelang", "kontrak", "konsorsium",
            "china", "rusia", "jepang", "korea", "asing",
            "danantara", "ina", "swf", "sovereign wealth fund",
            "ikn", "ibu kota nusantara", "ibu kota baru",
            "pabrik", "smelter", "kilang", "kawasan industri",
            "tambang", "mineral", "nikel", "batu bara", "emas", "tembaga", "esdm", "kontraksi", "bumn", "kementerian pu", "kemenhub", "basuki",
        ],
    },
    "PASAR": {
        "priority": 4,
        "label": "Pasar & Keuangan",
        "desc": "Saham, IHSG, rupiah, bursa, obligasi — investor & pelaku pasar",
        "keywords": [
            "saham", "ihsg", "bursa", "bei", "bursa efek", "indeks harga saham", "penguatan ihsg", "tren penguatan", "level",
            "rupiah", "dolar", "nilai tukar", "kurs",
            "laba", "rugi", "dividen", "rights issue",
            "obligasi", "sukuk", "sbn", "surat utang",
            "reksadana", "rdpt", "rds", "rdpu",
            "emiten", "ipo", "listing", "delisting",
            "the fed", "federal reserve", "suku bunga",
            "kripto", "bitcoin", "ethereum", "aset kripto",
            "capital outflow", "capital inflow", "hot money",
            "bank sentral", "bi rate", "bank indonesia",
        ],
    },
}


def _classify_pattern(title, body):
    """Classify article into economy pattern with confidence score.
    Returns (pattern_name, confidence) or (None, 0) if no pattern matches.
    Like Pressbox's _select_viral_pattern but for economy content.
    """
    text = f"{title} {body}".lower()

    # Utility/tutorial check — never generate from how-to articles
    utility = any(phrase in title.lower() for phrase in (
        "cek bansos", "cara cek", "pakai nik", "status penerima",
        "syarat daftar", "simak", "berikut", "ini dia",
    ))
    if utility:
        return None, 0

    best_pattern = None
    best_confidence = 0

    for name, cfg in sorted(ECONOMY_PATTERNS.items(), key=lambda x: x[1]["priority"]):
        hits = sum(1 for kw in cfg["keywords"] if re.search(rf"\b{re.escape(kw)}\b", text))
        # Confidence = hits weighted by priority (higher priority = more generous)
        thresholds = {"KORUPSI": 5, "KEBIJAKAN": 5, "PROYEK": 6, "PASAR": 5}
        divisor = thresholds.get(name, 4)
        confidence = min(hits / divisor, 1.0)

        # Higher-priority patterns need fewer hits to qualify
        min_hits = {1: 2, 2: 2, 3: 3, 4: 3}.get(cfg["priority"], 3)
        if hits >= min_hits and confidence > best_confidence:
            # Priority-weighted: higher priority gets bonus
            priority_bonus = (6 - cfg["priority"]) * 0.06
            adjusted_confidence = confidence + priority_bonus

            if adjusted_confidence > best_confidence:
                best_confidence = min(adjusted_confidence, 1.0)
                best_pattern = name

    return best_pattern, best_confidence


def _pattern_label(pattern_name):
    """Safe display label; eligibility still requires a real PINDAR pattern."""
    return ECONOMY_PATTERNS.get(pattern_name, {}).get("label", "Tidak terklasifikasi")


def _topic_score(title, body):
    """Score article relevance: 0-10. Primary: pattern classification. Fallback: editorial LLM."""
    text = f"{title} {body}".lower()
    routine_credit = any(term in text for term in ("bunga kredit", "cicilan", "kpr", "kredit bank"))
    official_trigger = any(term in text for term in ("bank indonesia", "bi rate", "ojk", "aturan", "kebijakan", "ditetapkan", "berlaku"))
    if routine_credit and not official_trigger:
        return 0, 0, 0
    pattern, confidence = _classify_pattern(title, body)
    if pattern:
        score = min(int(confidence * 10), 10)
        return score, score, score
    # Fallback: LLM-based editorial scoring for articles without clear pattern
    try:
        # Simple keyword-based fallback scoring
        text = f"{title} {body}".lower()
        economy_keywords = [
            "ekonomi", "anggaran", "apbn", "apbd", "pajak", "subsidi", "bansos",
            "inflasi", "defisit", "utang", "rupiah", "dolar", "saham", "ihsg",
            "bi rate", "suku bunga", "bumn", "investasi", "ekspor", "impor",
            "phk", "pekerja", "buruh", "upah", "gaji", "industri", "pabrik",
            "harga", "bbm", "listrik", "pangan", "minyak", "kredit", "dividen",
            "bank", "ojk", "kemenkeu", "menteri", "presiden", "dpr",
            "transformasi", "perubahan", "keadilan", "sosial", "csr",
        ]
        economy_hits = sum(1 for kw in economy_keywords if kw in text)
        impact_keywords = [
            "masyarakat", "rakyat", "konsumen", "pekerja", "petani", "nelayan",
            "pedagang", "pengusaha", "umkm", "buruh", "miskin", "harga naik",
            "harga turun", "daya beli", "lapangan kerja", "pengangguran",
        ]
        impact_hits = sum(1 for kw in impact_keywords if kw in text)
        # Scale to 0-10
        eco_score = min(economy_hits * 2, 10)
        impact = min(impact_hits * 2, 10)
        combined = max(eco_score, impact)
        return combined, eco_score, impact
    except Exception:
        return 0, 0, 0


def _is_official_mass_change(title, body):
    """Allow one-economy-signal stories only for explicit mass public changes."""
    text = f"{title} {body}".lower()
    official = any(word in text for word in (
        "ditetapkan", "berlaku", "disahkan", "putusan", "peraturan", "resmi naik",
        "resmi turun", "tarif naik", "tarif turun",
    ))
    mass = any(word in text for word in (
        "tarif transportasi", "transportasi", "listrik", "bbm", "pajak", "upah",
        "bansos", "pangan", "konsumen", "masyarakat", "penumpang",
    ))
    return official and mass


def _is_empty_commentary(title, body):
    """Reject a quote-only official reaction with no action, rule, or concrete data.
    Headline reaction words alone are not enough — check body for concrete substance too."""
    headline = title.lower()
    quote_only = any(word in headline for word in ("kata", "soal", "buka suara", "ungkap", "respons", "bakal"))
    if not quote_only:
        return False
    # Body with real data (Rp amounts, %, triliun/miliar) or policy/action words = substantive news
    body_lower = (body or "").lower()
    has_data = bool(re.search(r"(rp\s?\d|triliun|miliar|juta|\d+%|persen)", body_lower))
    has_action = any(re.search(rf"\b{re.escape(word)}\b", body_lower) for word in (
        "resmi", "berlaku", "ditetapkan",
        "disahkan", "putusan", "audit", "temuan", "phk", "naik", "turun",
        "dipotong", "ditambah", "dialihkan", "investasi", "ekspor", "impor",
        "defisit", "anggaran", "subsidi", "utang",
    ))
    if has_data or has_action:
        return False
    # Still no substance — headline reaction words only
    substance = any(re.search(rf"\b{re.escape(word)}\b", headline) for word in (
        "ditetapkan", "berlaku", "disahkan", "putusan", "peraturan", "audit",
        "temuan", "phk", "naik", "turun", "dipotong", "ditambah", "dialihkan",
    ))
    return quote_only and not substance

# ── POV Helpers ──────────────────────────────────────────────────────────────

def _convert_pov(text):
    """Keep conversational voice; only remove markup."""
    return re.sub(r'(?<!\w)[*_]+([^*_\n]+)[*_]+', r'\1', text)

def _format_sentence_blanks(text):
    """Collapse whitespace to one flowing paragraph per post."""
    s = text.replace('\u2014 ', ' ').replace('\u2014', ' ')
    s = re.sub(r":(?=\s+https?://|\s+www\.)", "\u0001", s)
    s = re.sub(r":\s+", " ", s)
    s = re.sub(r'\s+', ' ', s.replace("\u0001", ":"))
    return s.strip()


def article_evidence_gate(article):
    """Fail closed before LLM spend: body must support six non-repeated factual posts."""
    body = (article.get("body") or "").strip()
    if len(body) < 1000:
        return "body_under_1000_chars"
    has_number = bool(re.search(r"(?:rp\s*)?\d|\d+\s*(?:persen|%|miliar|juta|triliun)", body, re.I))
    has_quote = '"' in body or '“' in body
    if not (has_number or has_quote):
        return "no_numeric_or_quote_evidence"
    # Six slides require six article-backed factual units; reject thin sources before LLM.
    if len(source_claim_plan(article).splitlines()) < 6:
        return "insufficient_source_claims_for_six_posts"
    return None


def source_claim_plan(article):
    """Give writer only substantive source sentences, never title-derived facts."""
    body = re.sub(r"\s+", " ", article.get("body") or "").strip()
    sentences = re.split(r"(?<=[.!?])\s+", body)
    selected = [s for s in sentences if len(s) >= 25 and (
        re.search(r"(?:rp\s*)?\d|\d+\s*(?:persen|%|miliar|juta|triliun)", s, re.I)
        or '"' in s or '“' in s
        or re.search(r"\b(?:menurut|mengatakan|ditetapkan|berlaku|mengumumkan)\b", s, re.I)
    )]
    return "\n".join(f"- {s}" for s in selected[:12])


def deterministic_grounding_validate(article, posts):
    body = article.get("body") or ""
    return (_validate_numbers(posts, body) + _validate_years(posts, body)
            + _validate_proper_nouns(posts, body) + _validate_sensitive_language(posts, body))


def grounding_validate(article, posts):
    """Independent factual verifier; outage or unsupported fact blocks publish."""
    deterministic = deterministic_grounding_validate(article, posts)
    verifier_prompt = """Audit fakta dengan standar fail-closed. Jawab PASS hanya bila setiap pernyataan deklaratif dalam DRAFT didukung literal oleh SUMBER: angka, tanggal, nama, lembaga, status, pihak terdampak, sebab-akibat, konsekuensi, prediksi, perbandingan, penilaian ekonomi, dan kesimpulan. Jawab FAIL untuk inferensi atau tafsir baru, termasuk mengubah surplus menjadi klaim untung bersih, dampak ke kantong/pekerja, atau manfaat/rugi yang tidak dikatakan SUMBER. Gaya bahasa, hook, dan pertanyaan CTA boleh hanya bila tidak menyatakan premis fakta baru. Jika ragu, FAIL. Jawab satu kata saja: PASS atau FAIL."""
    draft = "\n".join(posts.values())
    verdict, error = _call_llm(
        verifier_prompt,
        f"SUMBER:\n{article.get('body', '')[:6000]}\nDRAFT:\n{draft}",
        max_retries=1,
        temperature=0,
    )
    if error or not verdict:
        return deterministic + ["grounding: verifier unavailable"]
    if verdict.strip().upper() != "PASS":
        deterministic.append("grounding: verifier rejected draft")
    return deterministic


def is_rate_limit_error(error):
    return bool(error and "rate limit 429" in error.lower())


def hook_issues(hook, body):
    """Hook needs source-backed concrete change; numbers are optional."""
    if not hook.strip():
        return ["S1: empty"]
    if not body.strip():
        return ["S1: source body empty"]
    return []


def thread_contract_issues(posts, article_url):
    """Finalize six posts. Source URL stays intact and every post stays within Threads limit."""
    issues = []
    if article_url:
        s6 = re.sub(r"\[URL(?:\s+[^\]]*)?\]", article_url, posts.get("post_6", ""), flags=re.I)
        if article_url not in s6:
            separator = "\n\n"
            room = 500 - len(separator) - len(article_url)
            if room < 1:
                return ["post_6: source URL exceeds 500 chars"]
            s6 = s6[:room].rstrip() + separator + article_url
        posts["post_6"] = s6
    for i in range(1, 7):
        text = posts.get(f"post_{i}", "")
        if not text.strip():
            issues.append(f"post_{i}: empty")
        elif len(text) > 500:
            issues.append(f"post_{i}: over 500 chars")
    return issues


def refresh_performance_metrics(data, now=None):
    """Refresh published S1 metrics. API failure preserves prior data and never blocks publish."""
    if not THREADS_TOKEN:
        return False
    now = now or time.time()
    changed = False
    for topic in data.get("topics", [])[:50]:
        post_id = topic.get("post_id")
        checked_at = topic.get("metrics_checked_at", 0)
        if not post_id or now - checked_at < 6 * 3600:
            continue
        try:
            response = httpx.get(
                f"{GRAPH}/{post_id}/insights",
                params={"access_token": THREADS_TOKEN,
                        "metric": "likes,replies,reposts,views,quotes", "period": "lifetime"},
                timeout=15,
            )
            if response.status_code != 200:
                log.warning("Metrics %s: HTTP %s", post_id, response.status_code)
                continue
            metrics = {item.get("name"): item.get("values", [{}])[0].get("value", 0)
                       for item in response.json().get("data", [])}
            for name in ("likes", "replies", "reposts", "views", "quotes"):
                topic[name] = metrics.get(name, topic.get(name) or 0)
            topic["metrics_checked_at"] = now
            changed = True
        except (httpx.RequestError, ValueError, IndexError, TypeError) as exc:
            log.warning("Metrics %s: %s", post_id, exc)
    return changed


def _compute_performance_stats(data):
    """Engagement quality, not raw reach, drives bounded source/arc preference."""
    buckets = {"source_avg": {}, "arc_avg": {}, "source_count": {}}
    grouped = {"source_avg": {}, "arc_avg": {}}
    for topic in data.get("topics", []):
        views = topic.get("views") or 0
        if views < 100:
            continue
        score = ((topic.get("likes") or 0) + 2 * (topic.get("replies") or 0)
                 + 3 * (topic.get("reposts") or 0) + 2 * (topic.get("quotes") or 0)) / views
        grouped["source_avg"].setdefault(topic.get("article_source", ""), []).append(score)
        grouped["arc_avg"].setdefault(topic.get("arc", ""), []).append(score)
    for name, values in grouped.items():
        buckets[name] = {key: sum(items) / len(items) for key, items in values.items() if key}
    buckets["source_count"] = {key: len(items) for key, items in grouped["source_avg"].items() if key}
    return buckets


# ── LLM Call ─────────────────────────────────────────────────────────────────

# ── LLM Call ─────────────────────────────────────────────────────────────────

def _get_api_key():
    for var in ["MISTRAL_API_KEY", "MISTRAL_KEY"]:
        key = os.getenv(var)
        if key:
            return key
    for var in ["OPENROUTER_API_KEY", "OPENROUTER_KEY"]:
        key = os.getenv(var)
        if key:
            return key
    return None

def _call_llm(system, user, model="mistral-large-latest", max_retries=3, temperature=None):
    api_key = _get_api_key()
    if not api_key:
        return None, "No API key found"
    base_url = "https://api.mistral.ai/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": random.uniform(0.7, 0.9) if temperature is None else temperature,
        "max_tokens": 4000,
    }
    last_error = ""
    rate_retries = 0
    for attempt in range(1, max_retries + 1):
        try:
            r = httpx.post(base_url, headers=headers, json=payload, timeout=60)
            if r.status_code == 200:
                content = (r.json()["choices"][0]["message"].get("content") or "").strip()
                return content, None
            elif r.status_code == 401:
                return None, f"Auth error {r.status_code}"
            elif r.status_code == 429:
                # Absorb transient rate limits with a bounded cooldown
                # (Retry-After header or 15s). Persistent 429 still returns an
                # error; the wrapper sleeps 120s before retrying the slot.
                if rate_retries < 2:
                    rate_retries += 1
                    headers = getattr(r, "headers", None) or {}
                    try:
                        cooldown = min(int(headers.get("Retry-After", "15")), 30)
                    except (TypeError, ValueError):
                        cooldown = 15
                    time.sleep(cooldown)
                    continue
                return None, f"Rate limit {r.status_code}"
            else:
                last_error = f"HTTP {r.status_code}: {r.text[:120]}"
                if attempt < max_retries:
                    time.sleep(2)
        except (httpx.RequestError, json.JSONDecodeError) as e:
            last_error = str(e)[:120]
            if attempt < max_retries:
                time.sleep(2)
    return None, f"LLM failed: {last_error}"

# ══════════════════════════════════════════════
#   SYSTEM PROMPT — 7 Arc + Aturan Bahasa + Quality Gate
# ══════════════════════════════════════════════

SYSTEM_PROMPT = """# RYANHADIII EKONOMI — WRITER

Balas JSON valid saja. Tidak ada markdown, penjelasan, atau code fence.

Ubah satu ISI ARTIKEL menjadi tepat 6 post Threads. Pakai gua–lu, kalimat pendek, bahasa awam. S1 80–140 karakter. S2–S6 maksimal 300 karakter. S1–S6 masing-masing minimal dua kalimat: kalimat kedua menerangkan atau mempersempit fakta di kalimat pertama, bukan mengulangnya. S1–S5 tanpa pertanyaan. S6 wajib punya satu pertanyaan spesifik, utuh, dan mudah dijawab dari perkembangan fakta artikel. URL sumber ditambahkan sistem.

## SUMBER ADALAH BATAS
- ISI ARTIKEL satu-satunya sumber. Judul, URL, pengetahuan umum, asumsi, contoh imajiner, dan pengalaman pribadi dilarang.
- Ambil semua kata isi dari ISI ARTIKEL: angka, nama, lembaga, lokasi, kebijakan, status, waktu, kutipan, pihak, sebab-akibat, konsekuensi, dan prediksi. Kata sambung boleh diparafrasekan; jangan mengganti atau menambah makna.
- Nama/lembaga wajib salin persis sebagai rangkaian kata utuh dari isi artikel. Jangan singkat, perluas, terjemahkan, atau gabungkan jabatan dengan nama.
- Jangan menambah dampak, profesi, angka, skenario, penilaian, atau pertanyaan yang premisnya tidak literal di artikel. Jangan menyebut PHK, nasib karyawan, kompensasi, atau penempatan ulang kecuali istilah dan faktanya literal di artikel.
- Jangan mengubah rencana, kemungkinan, atau proyeksi menjadi kepastian.
- Bila sumber tidak cukup untuk enam post akurat, balas {"status":"error","message":"insufficient_evidence"}.

## ALUR YANG BIKIN ORANG LANJUT BACA
Buka dengan fakta paling mahal: keputusan, perubahan, angka, atau kutipan paling konkret dari artikel. Jangan memancing dengan teka-teki, pertanyaan, skenario pembaca, atau opini. Tegangan hanya boleh datang dari perbandingan atau perubahan yang literal di artikel.

Setelah pembuka, susun bukti agar pembaca makin paham: apa yang berubah, ukuran atau pihak yang terkait, alasan atau mekanisme yang tertulis, lalu status/kutipan/contoh paling konkret. Tidak perlu memaksa satu jenis fakta ke slide tertentu. Pilih urutan yang paling jelas dari bukti yang tersedia. S6 menutup dengan satu pertanyaan spesifik dari fakta yang belum dipakai; jangan bikin janji waktu, hasil, dampak, atau premis baru.

Setiap slide wajib membawa bukti baru; jangan ulang angka, fakta, atau contoh. Buat kalimat pertama menyampaikan fakta, kalimat kedua menambah konteks yang belum ada. Jangan pakai label-colon, hashtag, jargon birokratis, template AI, deskripsi gambar, slogan, kalimat motivasi, atau kesimpulan yang terdengar besar.

## OUTPUT
{"status":"success","angle":"sudut pandang yang didukung artikel","post_1":"...","post_2":"...","post_3":"...","post_4":"...","post_5":"...","post_6":"..."}
"""

REVISION_PROMPT = """PERBAIKI HANYA field yang disebut di bawah. JANGAN ubah field lain. Balas JSON lengkap dengan field yang sudah diperbaiki.

Issues: {revision_notes}

Untuk tiap issue grounding: hapus seluruh frasa yang disebut issue, lalu hapus atau ganti dengan fakta yang muncul literal di ISI ARTIKEL. Untuk issue nama/entitas: hapus nama inventif dan ganti dengan nama yang persis ada di daftar NAMA/ENTITAS LITERAL. Jangan menambah dampak/CTA baru. Jika tidak ada enam post yang bisa dipertahankan akurat, balas {{\"status\":\"error\",\"message\":\"insufficient_evidence\"}}."""

def literal_fact_allowlist(body):
    """Literal body sentences are the only permitted facts for writer and revision."""
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", body).strip())
    return [sentence for sentence in sentences if len(sentence) >= 20][:80]


def literal_entity_allowlist(body):
    """Proper nouns and institutions literally present in the article body.
    Writer must reuse these verbatim; invented names fail noun validation."""
    if not body:
        return []
    text = re.sub(r"https?://\S+|www\.\S+", " ", body)
    entities = set(re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", text))
    entities |= set(re.findall(r"\b[A-Z]{2,}\b", text))
    drop = {"Data", "Menurut", "Padahal", "Kalau", "Kalo", "Yang", "Dan", "Tapi",
            "Karena", "Sumber", "Jadi", "Namun", "Bahkan", "Pertama", "Bukan",
            "Setelah", "Sebelum", "Dengan", "Untuk", "Dari", "Lalu", "Sementara",
            "Sedangkan", "Risikonya", "Soalnya", "Alasan", "Alasannya",
            "URL", "HTTP", "HTTPS", "WWW", "COM", "CO", "ID", "ORG", "NET"}
    return sorted(e for e in entities if e not in drop)[:40]


def build_user_prompt(article):
    """Build source-only prompt with a literal fact allowlist."""
    body = article.get("body", "")
    facts = literal_fact_allowlist(body)
    entities = literal_entity_allowlist(body)
    parts = [
        "**ISI ARTIKEL:**", body, "", "**ALLOWLIST FAKTA LITERAL:**",
        *[f"- {fact}" for fact in facts], "",
        "**NAMA/ENTITAS LITERAL — HANYA INI YANG BOLEH DIPAKAI:**",
        *[f"- {entity}" for entity in entities], "",
        "⚠️ INTERNAL: Setiap nama, angka, lembaga, tanggal, status, dan sebab-akibat harus diambil persis dari ALLOWLIST FAKTA LITERAL. Nama lembaga/entitas/istilah WAJIB verbatim dari daftar NAMA/ENTITAS LITERAL; dilarang membuat frasa nama baru (contoh: 'The Fed September', 'Survei Konsumen Juli', 'Peluang The Fed') atau singkatan yang tidak muncul di artikel. Jangan membuat fakta baru atau menggabungkan fakta menjadi klaim baru. Kalau tidak cukup untuk enam post, balas insufficient_evidence. Output HANYA JSON.",
    ]
    return "\n".join(parts)

# ── Validation ───────────────────────────────────────────────────────────────

def deterministic_validate(posts):
    warnings = []
    slop_phrases = [
        "tau gak sih", "gak bakal percaya", "coba resapin", "let that sink in",
        "bayangin", "yang rugi siapa", "patut dicatat",
        "tapi ternyata", "faktanya", "nyatanya", "inilah yang", "inilah kenapa",
        "sudah bukan rahasia lagi", "tak terelakkan", "perlu kalian tahu", "perlu diingat",
        "coba kalian bayangin", "gimana menurut kalian", "termasuk kalian",
        "itulah mengapa", "jadi intinya", "yang menarik",
        "foto ini", "terlihat", "di gambar", "nampak", "tampak",
        "perlu diketahui", "sebagaimana", "perlu dicatat",
    ]
    for i in range(1, 7):
        k = f"post_{i}"
        p = posts.get(k, "")
        if not p.strip():
            warnings.append(f"{k}: empty")
            continue
        # Min length — each slide needs enough source-backed context.
        min_len = 50 if i == 6 else 80
        if len(p) < min_len:
            warnings.append(f"{k}: too short ({len(p)} chars, min {min_len})")
        if i == 1 and len(p) > 140:
            warnings.append(f"{k}: too long ({len(p)} chars, max 140)")
        # Every slide needs a fact plus source-backed context.
        sent_count = len([c for c in p if c in ".!?"])
        if sent_count < 2:
            warnings.append(f"{k}: only {sent_count} sentences")
        elif sent_count > 2:
            warnings.append(f"{k}: too many sentences ({sent_count})")
        # Enforce 300 char limit
        if len(p) > 300:
            # Truncate at last period within limit
            truncated = p[:300]
            last_dot = truncated.rfind(".")
            if last_dot > 50:
                p = truncated[:last_dot+1]
            else:
                p = truncated
            posts[k] = p
        outside = re.sub(r'"[^\"]*"', "", p)
        # Child-readable language: reject bureaucratic words that prompt forbids.
        for word in ["akselerasi", "mitigasi", "implementasi", "optimalisasi", "realisasi", "signifikan", "komprehensif", "mekanisme", "skema", "portofolio"]:
            if re.search(rf"\b{word}\b", outside.lower()):
                warnings.append(f"{k}: hard word '{word}'")
                break
        # S1 must start from a source-backed fact, never a generic reader scenario.
        if i == 1 and re.match(r"\s*(?:bayangin\b)", outside, re.I):
            warnings.append(f"{k}: 'bayangin' opening")
        if i == 1 and re.match(r"\s*zaman sekarang harga barang naik semua\b", outside, re.I):
            warnings.append(f"{k}: generic/non-source opening")
        # Never fill a slide by describing what the source omits.
        pl = outside.lower()
        for phrase in ("artikel tidak menyebut", "artikel belum menyebut", "tidak disebut dalam artikel", "tidak diketahui", "belum ada dampak", "belum terasa"):
            if phrase in pl:
                warnings.append(f"{k}: absence disclaimer '{phrase}'")
                break
        # Check slop phrases
        for phrase in slop_phrases:
            if phrase in pl:
                warnings.append(f"{k}: slop '{phrase}'")
                break  # one warning per slide
        # Allow rhetorical questions in S2-S5 (provocation style)
        if i == 2 and outside.count("?") > 2:
            warnings.append(f"{k}: too many questions")
        if i == 6 and outside.count("?") > 1:
            warnings.append(f"{k}: too many CTA questions")
    return warnings


def _duplicate_fact_warnings(posts):
    """Flag repeated material numbers so six slides use distinct article evidence."""
    warnings = []
    seen = {}
    for i in range(1, 7):
        key = f"post_{i}"
        numbers = set(re.findall(r"\b\d{2,}(?:[.,]\d+)?\b", posts.get(key, "")))
        repeated = sorted(number for number in numbers if number in seen)
        if repeated:
            warnings.append(f"{key}: repeats material numbers from {seen[repeated[0]]}")
        for number in numbers:
            seen.setdefault(number, key)
    return warnings


def _validate_numbers(posts, body):
    """Light grounding: check significant numbers in thread appear in article body.
    Catches hallucinated RpXX.XXX, XX%, XX triliun/miliar/juta not in source.
    Normalizes Rp spacing to avoid false positives (Rp18.073 vs Rp 18.073)."""
    import re
    issues = []
    if not body:
        return issues
    body_normal = body.lower()
    # Normalize number formats: Rp spacing, persen↔%, thousand separators
    body_normal = re.sub(r'(\brp)\s+(\d)', r'\1\2', body_normal)
    body_normal = body_normal.replace('%', ' persen ').replace('  ', ' ')
    # Normalize thousand separators: remove dots in numbers (1.000→1000) but keep decimal commas
    body_normal = re.sub(r'(\d)\.(\d{3})', r'\1\2', body_normal)
    for key in ["post_1","post_2","post_3","post_4","post_5","post_6"]:
        text = posts.get(key, "")
        if not text:
            continue
        # Unit is mandatory: catches Rp100 juta and 10%, but ignores ordinary
        # sentence numbers such as slide labels or dates without a unit.
        patterns = re.finditer(
            r'(?:Rp\s*\d+(?:[.,]\d+)?(?:\s*(?:triliun|miliar|juta|ribu))?|\d+(?:[.,]\d+)?\s*(?:triliun|miliar|juta|ribu|%|persen))',
            text, re.IGNORECASE
        )
        for m in patterns:
            raw = m.group(0).strip()
            num_only = re.sub(r'[^0-9]', '', raw.split()[0])
            if len(num_only) < 4 and not any(u in raw.lower() for u in ['triliun','miliar','juta']):
                continue
            raw_normal = re.sub(r'(\brp)\s+(\d)', r'\1\2', raw.lower())
            raw_normal = raw_normal.replace('%', ' persen ').replace('  ', ' ').strip()
            # Normalize thousand separator dots in raw number too
            raw_normal = re.sub(r'(\d)\.(\d{3})', r'\1\2', raw_normal)
            if raw_normal not in body_normal:
                issues.append(f"{key}: '{raw}' not in article")
    return issues


def _validate_years(posts, body):
    """Years are factual claims too; reject any year absent from article body."""
    source_years = set(re.findall(r"\b(?:19|20)\d{2}\b", body))
    issues = []
    for key in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]:
        for year in set(re.findall(r"\b(?:19|20)\d{2}\b", posts.get(key, ""))):
            if year not in source_years:
                issues.append(f"{key}: year '{year}' not in article")
    return issues


def _validate_proper_nouns(posts, body):
    """Fail closed on invented multi-word names and all-caps institutions.
    Single title-cased words are excluded: Indonesian sentence starts cause too many false positives.
    """
    issues = []
    article_lower = body.lower()
    # Sentence connectors are not names when followed by a capitalized source term.
    skip = {"data", "menurut", "padahal", "kalau", "kalo", "yang", "dan", "tapi", "karena", "risikonya", "sumber", "soalnya", "alasan", "alasannya", "sementara", "sedangkan", "lalu", "setelah", "sebelum", "dengan", "untuk", "dari", "pertama", "bukan", "jadi", "namun", "bahkan"}
    # Common short names are allowed only when their formal source name is present.
    aliases = {"bea cukai": "direktorat jenderal bea dan cukai", "kemenkeu": "kementerian keuangan", "bi": "bank indonesia"}
    for key in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]:
        text = posts.get(key, "")
        for name in set(re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b', text)):
            source_name = aliases.get(name.lower(), name.lower())
            words = name.split()
            # Sentence fragments ("Pendapatan Telkomsel", "Jika Telkomsel") are not names.
            if (words[0].lower() not in skip and words[0].lower() not in {"pendapatan", "laba", "jika", "saat", "karena", "ketika"}
                    and source_name not in article_lower):
                issues.append(f"{key}: name '{name}' not in article")
        # Title-case phrases beginning with common speech/reporting verbs are not names.
        # Strip the verb before checking the remaining literal source name.
        for prefix in ("kata", "ujar", "tutur", "menurut", "sebut"):
            for name in set(re.findall(rf'\b{prefix.title()}\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text)):
                if name.lower() not in article_lower:
                    issues.append(f"{key}: name '{prefix.title()} {name}' not in article")
        # All-caps emphasis is common in generated Indonesian; only validate likely institutions.
        emphasis = {"BUKAN", "PERTAMA", "JADI", "TAPI", "KALAU", "JIKA", "DAN", "YANG", "UNTUK", "BOLEH", "WAJIB", "TIDAK",
                    "URL", "HTTP", "HTTPS", "WWW", "COM", "CO", "ID", "ORG", "NET", "INSTAGRAM", "THREADS"}
        # URLs always contain all-caps segments (CNBC, DETIK, WWW) — never flag them as institutions.
        text_no_urls = re.sub(r"https?://\S+|www\.\S+", " ", text)
        for acronym in set(re.findall(r'\b[A-Z]{2,}\b', text_no_urls)):
            if acronym not in emphasis and acronym.lower() not in article_lower:
                issues.append(f"{key}: institution '{acronym}' not in article")
    return issues


def _validate_claim_markers(posts, body):
    """Block high-risk status, prediction, and causal claims absent from source."""
    issues = []
    source = body.lower()
    markers = (
        "berpotensi", "diperkirakan", "diprediksi", "menyebabkan",
        "menyebab", "memicu", "berdampak", "imbas", "mengakibatkan", "berarti",
        "kebablasan", "coo bp bumn", "sudah kena", "tinggal tunggu giliran",
        "lapangan kerja", "layanan publik", "nasib karyawan", "skema penempatan ulang",
        "kompensasi", "untung bersih", "kantong kita",
    )
    for key in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]:
        text = posts.get(key, "").lower()
        for marker in markers:
            if re.search(rf"\b{re.escape(marker)}\b", text) and not re.search(rf"\b{re.escape(marker)}\b", source):
                issues.append(f"{key}: unsupported claim marker '{marker}'")
                break
    return issues


def _validate_sensitive_language(posts, body):
    """Sensitive reporting must preserve source attribution and legal status."""
    issues = []
    source = body.lower()
    verdicts = ("jelas korup", "pasti korup", "terbukti korup", "penjahat", "harus dihukum",
                "layak dihukum", "wajib dihukum", "pantas dihukum")
    for key in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]:
        text = posts.get(key, "").lower()
        if any(phrase in text for phrase in verdicts):
            issues.append(f"{key}: sensitive categorical verdict")
        if "tersangka" in text and "tersangka" not in source:
            issues.append(f"{key}: unsupported legal status 'tersangka'")
    return issues


def _voice_warnings(posts):
    """Flag synthetic/report-template phrasing for prompt revision, not rejection."""
    warnings = []
    patterns = r"(?:^|[.!?]\s*)(?:fakta|aturan bilang|pemerintah bilang|yang perlu dicatat|perlu diketahui|artinya)\s*:"
    for key in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]:
        if re.search(patterns, posts.get(key, ""), re.I):
            warnings.append(f"{key}: rewrite synthetic voice/template")
    return warnings


def _quality_gate(article, data, posts, warnings):
    """Quality gate: 12 checks from doc. Return True = pass, False = block."""
    if data.get("status") != "success" or not posts:
        return False
    # 1. Article eligibility is decided from full body before generation.
    # RSS/title eco_score is only a ranking hint and may be zero for valid articles.
    # 2. Impact to Indonesia clear (local source assumed)
    # 3. Original numbers have sources (can't verify programmatically)
    # 4. Number conversion uses reasonable assumptions (LLM handles)
    # 5. No keyword counted repeatedly (scoring handles)
    # 6. Title is ranking-only; full body already passed eligibility/grounding gates.
    # 7. Viral driver: hook needs a concrete article-backed change or tension.
    s1 = posts.get("post_1", "").lower()
    viral_markers = ["tapi", "padahal", "sementara", "malah", "naik", "turun",
                     "dipotong", "ditambah", "dialihkan", "ditetapkan", "berlaku",
                     "putusan", "wajib", "hingga", "mulai",
                     "lo", "gue", "gak adil", "enak", "masa", "tebak",
                     "ngomong", "siapa", "kok", "uangnya", "duitnya",
                     "baru aja", "deg-degan", "bisa naik", "bisa turun",
                     "kena", "ubah", "pindah", "ganti", "naik", "turun"]
    if not any(m in s1 for m in viral_markers):
        warnings.append("S1: no concrete viral driver — add contrast/action word")
    # 8. "baru aja" freshness check (soft warn if stale)
    # 9. S1-S5 no rhetorical questions
    # 10. S6 has specific CTA
    s6 = posts.get("post_6", "").lower()
    if not any(qt in s6 for qt in ["?", "menurut", "pilih"]):
        warnings.append("S6: no CTA found")
    # 11. No banned words (deterministic_validate handles)
    # 12. No fabricated facts (can't verify programmatically)
    return True

# ── Thread Generation ────────────────────────────────────────────────────────

def generate_thread(article):
    """Generate six source-grounded posts. Returns (data, error)."""
    evidence_error = article_evidence_gate(article)
    if evidence_error:
        return None, evidence_error
    user = build_user_prompt(article)
    # One writer plus one revision caps each candidate at two provider requests.
    for attempt in range(1, 2):
        content, error = _call_llm(SYSTEM_PROMPT, user, max_retries=1)
        if error:
            log.warning(f"  Writer request failed — {error[:80]}")
            if is_rate_limit_error(error):
                return None, error
            if attempt < 2:
                time.sleep(3)
            continue
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r'^```(?:json)?\s*', "", content)
            content = re.sub(r'\s*```$', "", content)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            log.warning(f"  LLM attempt {attempt}/2 — bad JSON")
            if attempt < 2:
                time.sleep(3)
            continue
        if data.get("status") == "error":
            return None, data.get("message", "LLM error")
        posts = {k: data.get(k, "") for k in ["post_1","post_2","post_3","post_4","post_5","post_6"]}
        for k in posts:
            posts[k] = _convert_pov(posts[k])
        # Enforce compact S1 hook length.
        s1 = posts.get("post_1", "")
        if len(s1) > 140:
            # Truncate to last sentence boundary within 140 chars
            trunc = s1[:140]
            last_period = max(trunc.rfind("."), trunc.rfind("!"), trunc.rfind("?"))
            if last_period > 40:
                posts["post_1"] = trunc[:last_period + 1]
            else:
                posts["post_1"] = trunc.rsplit(".", 1)[0] + "." if "." in trunc else trunc
        # Style issues are revision cues. Fact checks remain publish blockers.
        style_warnings = deterministic_validate(posts) + _duplicate_fact_warnings(posts)
        noun_warnings = _validate_proper_nouns(posts, article["body"])
        missing = [f"{k}: empty" for k, v in posts.items() if not v.strip()]
        claim_warnings = _validate_claim_markers(posts, article["body"])
        voice_warnings = _voice_warnings(posts)
        warnings = missing + grounding_validate(article, posts) + noun_warnings + claim_warnings
        if style_warnings or claim_warnings or voice_warnings:
            log.info(f"  Soft style/claim warnings: {style_warnings + claim_warnings + voice_warnings}")
        if warnings:
            log.warning(f"  Hard validation: {warnings}")
            revision_notes = '; '.join(warnings + style_warnings + claim_warnings + voice_warnings)
            rev_user = user + f"\n\n{REVISION_PROMPT.format(revision_notes=revision_notes)}"
            c2, e2 = _call_llm(SYSTEM_PROMPT, rev_user, max_retries=1)
            if c2:
                c2 = re.sub(r'^```(?:json)?\s*|\s*```$', "", c2.strip())
                try:
                    d2 = json.loads(c2)
                    p2 = {k: _convert_pov(d2.get(k, "")) for k in ["post_1","post_2","post_3","post_4","post_5","post_6"]}
                    style_w2 = deterministic_validate(p2) + _duplicate_fact_warnings(p2)
                    noun_w2 = _validate_proper_nouns(p2, article["body"])
                    w2 = [f"{k}: empty" for k, v in p2.items() if not v.strip()]
                    claim_w2 = _validate_claim_markers(p2, article["body"])
                    w2.extend(grounding_validate(article, p2))
                    w2.extend(noun_w2)
                    w2.extend(claim_w2)
                    voice_w2 = _voice_warnings(p2)
                    if style_w2 or voice_w2:
                        log.info(f"  Soft style warnings after revision: {style_w2 + voice_w2}")
                    if d2.get("status") == "success" and not w2:
                        data, posts = d2, p2
                        warnings = []
                        log.info("  Revision fixed validation")
                    else:
                        log.warning(f"  Revision blocked: {w2}")
                except json.JSONDecodeError:
                    log.warning("  Revision blocked: bad JSON")
            if warnings:
                continue
        # Quality gate: check article supports the thread
        if not _quality_gate(article, data, posts, warnings):
            log.warning("Quality gate blocked — skip generation")
            return None, "quality_gate"
        for k in posts:
            posts[k] = _format_sentence_blanks(posts[k])
        contract_issues = thread_contract_issues(posts, article.get("url", ""))
        if contract_issues:
            log.warning(f"  Thread contract blocked: {contract_issues}")
            continue
        return {
            "article_title": article.get("title", ""),
            "article_url": article.get("url", ""),
            "article_source": article.get("source", ""),
            "angle": data.get("angle", ""),
            "arc": data.get("arc", "market_shock"),
            "posts": posts,
        }, None
    return None, "LLM failed after 2 attempts"

# ══════════════════════════════════════════════
#   THREADS PUBLISHER
# ══════════════════════════════════════════════

def post_to_threads(article_title, posts, image_url=None, inflight=None):
    """Post a six-slide chain to Threads via v1.0 Graph API. Slide 1 uses article image."""
    if not THREADS_TOKEN or not THREADS_USER_ID:
        log.error("No THREADS_ACCESS_TOKEN or THREADS_USER_ID")
        return None
    if DRY_RUN:
        log.info("DRY RUN — skipping post")
        return None
    uid = THREADS_USER_ID
    published_ids = list((inflight or {}).get("post_ids", []))
    last_post_id = published_ids[-1] if published_ids else None
    image_used = False
    slide_keys = sorted([k for k in posts if k.startswith("post_")], key=lambda x: int(x.split("_")[1]))
    for key in slide_keys[len(published_ids):]:
        text = posts.get(key, "")
        if not text:
            continue
        i = int(key.split("_")[1])
        is_first = (i == 1)
        use_image = is_first and image_url and not image_used
        data = {"user_id": uid, "media_type": "IMAGE" if use_image else "TEXT",
                "text": text, "access_token": THREADS_TOKEN}
        if use_image:
            data["image_url"] = image_url
        if last_post_id:
            data["reply_to_id"] = last_post_id
        container_id = None
        for retry in range(2):
            try:
                r = httpx.post(f"{GRAPH}/{uid}/threads", data=data, timeout=15)
                if r.status_code == 200:
                    container_id = r.json().get("id")
                    break
                log.warning(f"  {key} create attempt {retry+1}: HTTP {r.status_code}")
            except (httpx.RequestError, json.JSONDecodeError) as e:
                log.warning(f"  {key} create attempt {retry+1}: {e}")
            time.sleep(2)
        if not container_id:
            if use_image:
                log.warning(f"  IMAGE container failed for {key}, falling back to TEXT")
                use_image = False
                image_used = True
                data["media_type"] = "TEXT"
                data.pop("image_url", None)
                for retry in range(2):
                    try:
                        r = httpx.post(f"{GRAPH}/{uid}/threads", data=data, timeout=15)
                        if r.status_code == 200:
                            container_id = r.json().get("id")
                            break
                        log.warning(f"  {key} TEXT fallback attempt {retry+1}: HTTP {r.status_code}")
                    except (httpx.RequestError, json.JSONDecodeError) as e:
                        log.warning(f"  {key} TEXT fallback attempt {retry+1}: {e}")
                    time.sleep(2)
            if not container_id:
                log.error(f"  {key} create failed")
                return {"error": f"{key} create failed", "post_ids": published_ids}
        if use_image:
            for poll in range(15):
                try:
                    sr = httpx.get(f"{GRAPH}/{container_id}",
                                   params={"fields": "status,error_message",
                                           "access_token": THREADS_TOKEN}, timeout=10)
                    if sr.status_code == 200:
                        status = sr.json().get("status", "")
                        if status == "FINISHED":
                            break
                        if status == "ERROR":
                            log.warning(f"  {key} image error: {sr.json().get('error_message', '')}")
                            break
                except Exception:
                    pass
                time.sleep(2)
            image_used = True
        time.sleep(1)
        post_id = None
        for retry in range(2):
            try:
                r = httpx.post(f"{GRAPH}/{uid}/threads_publish",
                              data={"access_token": THREADS_TOKEN, "creation_id": container_id}, timeout=15)
                if r.status_code == 200:
                    post_id = r.json().get("id")
                    break
                log.warning(f"  {key} publish attempt {retry+1}: HTTP {r.status_code}")
            except (httpx.RequestError, json.JSONDecodeError) as e:
                log.warning(f"  {key} publish attempt {retry+1}: {e}")
            time.sleep(2)
        if not post_id:
            log.error(f"  {key} publish failed")
            return {"error": f"{key} publish failed", "post_ids": published_ids}
        published_ids.append(post_id)
        last_post_id = post_id
        if inflight is not None:
            inflight["post_ids"] = published_ids
            save_inflight(inflight)
        log.info(f"  {key} {'IMAGE' if use_image else 'TEXT'} → {post_id}")
        time.sleep(2)
    return {"post_ids": published_ids, "media_ids": published_ids}

# ══════════════════════════════════════════════
#   MAIN
# ══════════════════════════════════════════════

def main():
    started_at = time.monotonic()
    data = load_data()
    # Dry-run must not write analytics or alter future selection.
    if not DRY_RUN and refresh_performance_metrics(data):
        save_data(data)
    inflight = load_inflight()
    if inflight:
        posts = inflight["posts"]
        article = inflight["article"]
        log.warning(f"Resuming partial chain from S{len(inflight.get('post_ids', [])) + 1}")
        pub = post_to_threads(article["title"], posts, inflight.get("image_url"), inflight)
        if _publish_complete(pub, posts):
            topic = inflight["topic"]
            topic["post_id"] = pub["post_ids"][0]
            topic["media_id"] = pub["media_ids"][0] if pub.get("media_ids") else None
            data.setdefault("topics", []).insert(0, topic)
            rc = data.setdefault("recent_content", {})
            rc.setdefault("openings", []).insert(0, posts.get("post_1", "")[:100])
            rc.setdefault("ctas", []).insert(0, posts.get("post_6", "")[:100])
            for k in ["openings", "ctas"]:
                rc[k] = rc[k][:10]
            save_data(data)
            INFLIGHT_FILE.unlink(missing_ok=True)
            PREPARED_ARTICLE_FILE.unlink(missing_ok=True)
            log.info(f"Posted: {pub['post_ids'][0]}")
            send_success_report(article["title"], article.get("pattern", "UNKNOWN"),
                                time.monotonic() - started_at, threads_permalink(pub["post_ids"][0]))
        elif pub and pub.get("error"):
            log.error(f"Post error: {pub['error']}")
        return
    posted_urls = {t.get("article_url", t.get("title", "")) for t in data.get("topics", [])}
    recent_topics = data.get("topics", [])

    # Step 1: Normal runs publish only a prepared immutable draft.
    article = body = og_image = None
    prepared_result = None
    articles = []
    article = load_prepared_article(posted_urls)
    if article:
        body, og_image = article["body"], article["og_image"]
        prepared_result = {"posts": article["posts"], "angle": article.get("angle", ""),
                           "arc": article.get("arc", "market_shock")}
        prepared_ok, prepared_reason = _is_eligible_candidate(article["title"], body, article.get("source", "prepared"))
        if article.get("published_ts", 0) <= 0 or time.time() - article["published_ts"] > 86400:
            prepared_ok, prepared_reason = False, "prepared article missing/failing 24h published_ts"
        elif article_evidence_gate(article):
            prepared_ok, prepared_reason = False, article_evidence_gate(article)
        elif not validate_article_image(og_image):
            prepared_ok, prepared_reason = False, "prepared article no valid HD image"
        if not prepared_ok:
            log.warning(f"Prepared article rejected: {prepared_reason}")
            article = None
        else:
            pattern_name, pattern_confidence = _classify_pattern(article["title"], body)
            article["pattern"] = pattern_name
            article["pattern_label"] = _pattern_label(pattern_name)
            article["image_hint"] = _image_hint(og_image)
            log.info(f"Prepared article: {article['title']}")
        articles = []
    if article and PREPARE_NEXT:
        log.info("Prepared draft already valid; leave immutable draft unchanged")
        return
    if not article and not PREPARE_NEXT:
        log.info("No valid prepared draft; no-post. Run --prepare-next to create one.")
        return
    if not article:
        log.info("Scraping economy sources...")
        articles = scrape_all()
        log.info(f"  Got {len(articles)} raw articles")
        # Scout is the publisher's only candidate pool: five body-verified daily topics.
        hot_topics = scout_hot_topics(articles, data=data)
        for topic in hot_topics:
            log.info(f"  Hot #{topic['rank']}: {topic['title'][:70]} (score={topic['hot_score']})")
        if not DRY_RUN:
            save_hot_topics(hot_topics)
        articles = _publish_candidates_from_hot_topics(articles, hot_topics)
        log.info(f"  Publisher pool: {len(articles)} body-verified hot topics")

    # Step 2: Search ranked pool. Like Pressbox, title ranks; body decides eligibility.
    skipped_urls = set()
    candidate_limit = len(articles) if not article else 0
    for _ in range(candidate_limit):
        candidate = _pick_article(articles, posted_urls | skipped_urls, data)
        if not candidate:
            break
        is_repeat, shared_entities, shared_words = _is_repeat_issue(candidate["title"], recent_topics)
        if is_repeat:
            skipped_urls.add(candidate["url"])
            log.info(f"  Skip: repeat issue within 72h (entities={sorted(shared_entities)}, title_words={len(shared_words)})")
            continue
        log.info(f"Picked: {candidate['title']}")
        log.info(f"  Source: {candidate['source']} | Score: {candidate.get('eco_score', 0)} | Reason: {candidate.get('_reason', '')} | Weight: {candidate.get('_weight', 0)}")
        # Quick title-level economy filter: skip obviously non-ekonomi before costly body fetch
        if not _has_economy_title_signal(candidate["title"]):
            log.info("  Skip: title has no economy signal")
            skipped_urls.add(candidate["url"])
            continue
        log.info("Fetching article body...")
        candidate_body, candidate_image, source_ts = _fetch_article_body(candidate["url"])
        # Fail closed: RSS time is not proof of article recency; use source publish time.
        if not source_ts or source_ts > time.time() + 300 or time.time() - source_ts > 86400:
            log.info("  Skip: source publish time missing, invalid, or older than 24h")
            skipped_urls.add(candidate["url"])
            continue
        topic_score, economy_score, impact_score = _topic_score(candidate["title"], candidate_body)
        pattern_name, pattern_confidence = _classify_pattern(candidate["title"], candidate_body)
        eligible_ok, eligible_reason = _is_eligible_candidate(candidate["title"], candidate_body, candidate["source"])
        if eligible_ok:
            if candidate_image is None and not IMAGE_DISABLED:
                log.warning("  Skip: no valid HD image — trying next candidate")
                skipped_urls.add(candidate["url"])
                continue
            article, body, og_image = candidate, candidate_body, candidate_image
            article["body"] = body
            article["image_hint"] = _image_hint(og_image)
            article["pattern"] = pattern_name
            article["pattern_label"] = _pattern_label(pattern_name)
            log.info(f"  Body: {len(body)} chars | Pattern: {pattern_name} ({article['pattern_label']}, confidence={pattern_confidence:.2f})")
            break
        skipped_urls.add(candidate["url"])
        log.warning(f"  Skip: body/relevance/editorial score failed ({topic_score}/10, economy={economy_score}, impact={impact_score})")
    if not article:
        log.error(f"No eligible article among {candidate_limit} ranked candidates")
        return

    # Step 4: Resolve image for slide 1
    image_url = None
    if IMAGE_URL:
        image_url = IMAGE_URL
        log.info("  Image: manual --image-url")
    elif not IMAGE_DISABLED:
        image_url = og_image
        log.info(f"  Image: {image_url[:80] if image_url else 'disabled'}")
    if image_url:
        log.info(f"  Image URL: {image_url[:80]}...")
    else:
        log.info("  Image: disabled via --no-image")

    # Step 5: Prepared drafts are never regenerated; new drafts use bounded requests.
    result = prepared_result
    error = None
    recent_openings = data.get("recent_content", {}).get("openings", [])
    if result:
        log.info("Using immutable prepared draft...")
    else:
        log.info("Generating thread...")
        if recent_openings:
            article["recent_openings"] = recent_openings[:5]
        result, error = generate_thread(article)
    if error:
        log.error(f"Generation failed: {error}")
        if is_rate_limit_error(error):
            log.error("Generation stopped: provider rate limit; skip candidate churn")
            return
        skipped_urls.add(article["url"])
        # Try next-best candidate from remaining pool (fast retry)
        retry_article = None
        for _ in range(candidate_limit):
            retry_article = _pick_article(articles, posted_urls | skipped_urls, data)
            if retry_article is None:
                break
            log.info(f"  Retry candidate: {retry_article['title'][:80]}")
            # Quick gate on retry candidate
            retry_body, retry_img, retry_ts = _fetch_article_body(retry_article["url"])
            if (not retry_ts or retry_ts > time.time() + 300
                    or time.time() - retry_ts > 86400):
                skipped_urls.add(retry_article["url"])
                continue
            if not retry_body or len(retry_body) < 500:
                skipped_urls.add(retry_article["url"])
                continue
            if retry_img is None and not IMAGE_DISABLED:
                skipped_urls.add(retry_article["url"])
                continue
            # Same full economy gate as main path — retry must not bypass relevance checks
            retry_ok, retry_reason = _is_eligible_candidate(retry_article["title"], retry_body, retry_article["source"])
            if not retry_ok:
                log.info(f"  Retry skip: {retry_reason}")
                skipped_urls.add(retry_article["url"])
                continue
            retry_article["body"] = retry_body
            retry_article["image_hint"] = _image_hint(retry_img)
            og_image = retry_img
            if IMAGE_URL:
                image_url = IMAGE_URL
            elif not IMAGE_DISABLED:
                image_url = retry_img  # keep slide-1 image in sync with retried article
            if recent_openings:
                retry_article["recent_openings"] = recent_openings[:5]
            result, error = generate_thread(retry_article)
            if error:
                log.error(f"Retry generation also failed: {error}")
                if is_rate_limit_error(error):
                    log.error("Generation stopped: provider rate limit; skip candidate churn")
                    return
                skipped_urls.add(retry_article["url"])
                continue
            article = retry_article  # update article ref for downstream use
            break
        if not result:
            log.error("Generation failed: no verified LLM draft after retry")
            return

    if not result:
        log.error("Generation failed: no valid result")
        return

    posts = result["posts"]
    if PREPARE_NEXT:
        if DRY_RUN:
            log.info("DRY RUN — validated draft not persisted")
        else:
            save_prepared_article(article, result, image_url)
            log.info(f"Prepared: {article['title']}")
        return
    for i in range(1, 7):
        first_line = posts.get(f"post_{i}", "").split("\n")[0][:80] or "(empty)"
        log.info(f"  S{i}: {first_line}")

    # Step 6: Post
    if not DRY_RUN:
        inflight = {
            "article": article, "posts": posts, "post_ids": [], "image_url": image_url,
            "topic": {
                "title": article["title"], "article_url": article["url"], "article_source": article["source"],
                "angle": result.get("angle", ""), "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+07:00"),
                "eco_score": article.get("eco_score"), "selection_weight": article.get("_weight"),
                "pattern": article.get("pattern"), "arc": result.get("arc", ""), "slides": posts,
 "likes": None, "replies": None, "reposts": None, "views": None, "quotes": None,
            },
        }
        save_inflight(inflight)
        pub = post_to_threads(article["title"], posts, image_url=image_url, inflight=inflight)
        if _publish_complete(pub, posts):
            log.info(f"Posted: {pub['post_ids'][0]}")
            topic = {
                "title": article["title"],
                "article_url": article["url"],
                "article_source": article["source"],
                "angle": result.get("angle", ""),
                "post_id": pub["post_ids"][0],
                "media_id": pub["media_ids"][0] if pub.get("media_ids") else None,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+07:00"),
                "eco_score": article.get("eco_score"),
                "selection_weight": article.get("_weight"),
                "pattern": article.get("pattern"),
                "arc": result.get("arc", ""),
                "slides": posts,
                "likes": None,
                "replies": None,
                "reposts": None,
                "views": None,
                "quotes": None,
            }
            data.setdefault("topics", []).insert(0, topic)
            rc = data.setdefault("recent_content", {})
            rc.setdefault("openings", []).insert(0, posts.get("post_1", "")[:100])
            rc.setdefault("ctas", []).insert(0, posts.get("post_6", "")[:100])
            for k in ["openings", "ctas"]:
                rc[k] = rc[k][:10]
            save_data(data)
            PREPARED_ARTICLE_FILE.unlink(missing_ok=True)
            INFLIGHT_FILE.unlink(missing_ok=True)
            send_success_report(article["title"], article.get("pattern", "UNKNOWN"),
                                time.monotonic() - started_at, threads_permalink(pub["post_ids"][0]))
        elif pub and pub.get("error"):
            log.error(f"Post error: {pub['error']}")
    else:
        print()
        for i in range(1, 7):
            if f"post_{i}" not in posts:
                continue
            print(f"--- S{i} ---")
            print(posts.get(f"post_{i}", ""))
            print()
        print(f"Arc: {result.get('arc', 'market_shock')}")
        print(f"Article: {article['title']}")
        print(f"Source: {article['source']}")
        print(f"Angle: {result.get('angle', '')}")

if __name__ == "__main__":
    main()
