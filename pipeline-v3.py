#!/usr/bin/env python3
"""
Techbro v3 — EKONOMI NASIONAL + POV PRIBADI + 6 Script Hack Elements
Article-based: scrape economy RSS/HTML → 6 threads with personal POV.
"""

import html, httpx, json, logging, os, random, re, statistics, struct, sys, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
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
    # Reuse Hermes custom-provider credential for local 9router LLM calls.
    load_dotenv(Path.home() / ".hermes" / ".env", override=False)
    # Cron/Hermes may inherit stale secrets; project .env is source of truth.
    load_dotenv(BASE / ".env", override=True)
    THREADS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN")
except Exception:
    pass

SZEJAY_BOT_TOKEN = os.getenv("SZEJAY_BOT_TOKEN")
# Report destination: Hadijayyy Telegram DM, not bot's own ID.
SZEJAY_CHAT_ID = "1022032312"

log = logging.getLogger("techbro-v3")
THREADS_USER_ID = None
METRICS_TOKEN_OK = False
if THREADS_TOKEN and not DRY_RUN:
    try:
        r = httpx.get(f"{GRAPH}/me?access_token={THREADS_TOKEN}", timeout=10)
        if r.status_code == 200:
            THREADS_USER_ID = r.json().get("id")
            log.info(f"Token: POST OK | user_id={THREADS_USER_ID}")
            mr = httpx.get(f"{GRAPH}/{THREADS_USER_ID}/threads?access_token={THREADS_TOKEN}&limit=1", timeout=10)
            if mr.status_code == 200:
                METRICS_TOKEN_OK = True
                log.info("Token: METRICS OK")
            else:
                log.warning(f"Token: METRICS HTTP {mr.status_code} — engagement tracking may fail")
        else:
            log.warning(f"Token: POST HTTP {r.status_code} — check THREADS_ACCESS_TOKEN")
    except Exception as e:
        log.warning(f"Token check failed: {e}")

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S")
# httpx logs full request URLs at INFO, which leaks access_token in query strings.
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Keyword Loader ───────────────────────────────────────────────────────────

def load_keywords():
    """Load scoring keywords from keywords.json. Returns dict with all keyword lists."""
    defaults = {
        "score_categories": [],
        "entity_boost": {},
        "number_bonus": [],
        "editorial_selection": {},
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
SOURCE_ARTICLE_CAPS = {
    "cnn_ekonomi": 8,
    "detik_finance": 8,
    "cnbc_market": 8,
    "cnbc_entrepreneur": 6,
    "antara_ekonomi": 6,
    "bi_release": 6,
    "kemenkeu_release": 6,
    "esdm_news": 6,
    "dailysocial": 3,
    "cnbc_global": 6,
    "bbc_business": 6,
}
CURRENT_COHORT = "techbro_v3_current"
LEGACY_COHORT = "legacy"

# ── Scoring Configuration (loaded from keywords.json) ──────────────────────────

SCORE_CATEGORIES = KW["score_categories"]
ENTITY_BOOST = KW["entity_boost"]
NUMBER_BONUS = KW["number_bonus"]
EDITORIAL_SELECTION = KW["editorial_selection"]
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


def normalize_topic_cohorts(data):
    """Label pre-existing ledger rows legacy; new rows set current explicitly."""
    changed = False
    for topic in data.get("topics", []):
        if "cohort" not in topic:
            topic["cohort"] = topic_cohort(topic)
            changed = True
    return changed

def save_data(data):
    tmp = POSTED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(POSTED_FILE)


def topic_cohort(topic):
    """Keep current 7-slide data separate from legacy ledgers."""
    if topic.get("cohort"):
        return topic["cohort"]
    slides = topic.get("slides") or {}
    if (topic.get("article_source") and topic.get("pattern") and topic.get("hook_pattern")
            and all(slides.get(f"post_{i}") for i in range(1, 8))):
        return CURRENT_COHORT
    if topic.get("article_source") or topic.get("pattern") or topic.get("slides"):
        return CURRENT_COHORT
    return LEGACY_COHORT


def _is_current_topic(topic):
    """Analytics accepts explicit current rows and unlabelled test/runtime rows."""
    return topic.get("cohort") != LEGACY_COHORT

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
    """Only a complete seven-post chain may enter dedup/analytics state."""
    expected = 7
    complete_posts = all(posts.get(f"post_{i}", "").strip() for i in range(1, expected + 1))
    return bool(pub and not pub.get("error") and complete_posts
                and len(pub.get("post_ids", [])) == expected)


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
        if not isinstance(posts, dict):
            return None
        eligible, reason = _is_eligible_candidate(
            article.get("title", ""), article.get("body", ""), article.get("source", "prepared")
        )
        if not eligible:
            log.warning("Prepared article rejected by current eligibility gate: %s", reason)
            return None
        if thread_contract_issues(posts, article["url"]):
            return None
        # Prose style warnings are advisory. Contract and grounding remain hard gates.
        if deterministic_grounding_validate(article, posts):
            return None
        if _validate_source_evidence_map(posts, article.get("body", "")):
            return None
        return article
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def save_prepared_article(article, result, image_url):
    """Persist only a fully validated seven-post draft for one later publish."""
    payload = dict(article)
    # Always persist a source timestamp so the 24h freshness check works on reload.
    # Prefer published_ts (source page), fall back to ts (RSS), then prepared_at.
    if not payload.get("published_ts"):
        payload["published_ts"] = article.get("ts") or payload["prepared_at"] or time.time()
    pattern, arc, hook = _content_metadata(article.get("title", ""), article.get("body", ""))
    payload.update({"posts": result["posts"], "angle": result.get("angle", ""),
                    "story_functions": STORY_FUNCTIONS,
                    "pattern": article.get("pattern") or pattern,
                    "arc": result.get("arc") or article.get("arc") or arc,
                    "hook_pattern": article.get("hook_pattern") or hook, "og_image": image_url,
                    "cohort": CURRENT_COHORT,
                    "prepared_at": time.time(), "expires_at": time.time() + 86400})
    tmp = PREPARED_ARTICLE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    tmp.replace(PREPARED_ARTICLE_FILE)


def _topic_entities(title):
    """Extract named economy entities; generic market terms are not repeat keys."""
    text = title.lower()
    aliases = {
        "mbg": ("mbg", "makan bergizi gratis"), "mk": ("putusan mk", "mahkamah konstitusi"),
        "pertamina": ("pertamina",), "danantara": ("danantara", "bpi danantara"),
        "bumn": ("bumn", "badan usaha milik negara"), "pppk": ("pppk",),
        "purbaya": ("purbaya",), "prabowo": ("prabowo",), "airlangga": ("airlangga",),
        "ojk": ("ojk", "otoritas jasa keuangan"), "bei": ("bei", "bursa efek indonesia"),
        "apbn": ("apbn",), "spbu": ("spbu",), "bi": ("bi rate", "bank indonesia"),
        "anggaran-pendidikan": ("anggaran pendidikan", "dana pendidikan"),
    }
    return {name for name, words in aliases.items() if any(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) for word in words)}


def _title_words(title):
    stop = {"yang", "dan", "dari", "untuk", "dengan", "soal", "ini", "itu", "di", "ke", "pada", "buat", "akan", "sudah", "baru", "buka", "suara"}
    return {word for word in re.findall(r"[a-z0-9]{4,}", title.lower()) if word not in stop}


ISSUE_TERMS = (
    "subsidi", "mbg", "bansos", "hilirisasi", "apbn", "apbd", "bumn", "danantara",
    "pajak", "tarif", "utang", "anggaran", "dividen", "akuisisi", "merger", "ipo",
    "rights issue", "phk", "upah", "gaji", "pangan", "bbm", "rups", "kredit",
)


def _issue_terms(title):
    text = title.lower()
    return {term for term in ISSUE_TERMS if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text)}


def _is_repeat_issue(title, topics, hours=72):
    """Block same entity + same issue; allow materially different stories."""
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
        strong_entities = shared_entities - {"apbn", "bumn", "bi", "bei", "ojk", "spbu"}
        prior_words = _title_words(previous)
        similarity = len(shared_words) / max(1, min(len(words), len(prior_words)))
        same_issue = bool(_issue_terms(title) & _issue_terms(previous))
        if strong_entities and (same_issue or len(shared_words) >= 3 or similarity >= 0.6):
            return True, strong_entities, shared_words
    return False, set(), set()

# ── HTTP Helpers ─────────────────────────────────────────────────────────────

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

def _http_get(url, timeout=12):
    """GET with one immediate fallback retry for flaky official pages."""
    for attempt in range(2):
        try:
            r = httpx.get(url, headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)
            if r.status_code == 200:
                return r.status_code, r.text
        except Exception:
            pass
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            return r.status, r.read().decode("utf-8", errors="replace")
        except Exception:
            if attempt == 1:
                return 0, ""
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
    # Filter mixed/homepage and source-specific noise before the source cap.
    articles = [a for a in articles
                if (_has_economy_title_signal(a["title"])
                    or a["source"] in {"cnbc_global", "bbc_business"})
                and _has_source_title_signal(a["title"], a["source"])]
    from collections import defaultdict
    by_source = defaultdict(list)
    for a in articles:
        by_source[a["source"]].append(a)
    deduped = []
    for source, src_articles in by_source.items():
        src_articles.sort(key=lambda a: (a["ts"], len(a["title"])), reverse=True)
        cap = SOURCE_ARTICLE_CAPS.get(source, MAX_ARTICLES_PER_SOURCE)
        deduped.extend(src_articles[:cap])
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
    if (not _has_economy_title_signal(title)
            and article.get("source") not in {"cnbc_global", "bbc_business"}):
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
    public_angle = any(_matches_keyword(tl, kw) for kw in ("kebijakan", "bi", "bank indonesia", "apbn", "pajak", "subsidi", "anggaran", "berlaku", "ditetapkan", "kenapa", "penyebab", "alasannya"))
    if technical and not public_angle:
        score -= 30

    # Routine SPBU price lists are utility updates, not Techbro analysis topics.
    # Keep structural fuel-policy stories such as B50 or subsidy changes eligible.
    routine_bbm = "bbm" in tl and "spbu" in tl
    fuel_policy = any(_matches_keyword(tl, kw) for kw in ("b50", "subsidi", "kebijakan", "aturan", "kuota", "alokasi", "apbn"))
    if routine_bbm and not fuel_policy:
        score -= 100

    # Kontras harga vs daya beli: high-performer pattern (UMR vs Greenland = 18K views).
    # Boost stories that contrast a price/cost figure against purchasing power.
    price_signal = any(_matches_keyword(tl, kw) for kw in ("harga", "biaya", "tarif", "rupiah"))
    wallet_signal = any(_matches_keyword(tl, kw) for kw in ("gaji", "upah", "umr", "ump", "daya beli", "pendapatan"))
    if price_signal and wallet_signal:
        score += 15  # price-vs-income contrast = proven virality catalyst

    # Concrete decision-maker + number + public consequence = strongest local hook.
    actor = bool(re.search(r"\b(prabowo|jokowi|menteri|gubernur|kemenkeu|ojk|danantara|bumn|pemerintah)\b", tl))
    consequence = any(_matches_keyword(tl, kw) for kw in ("beban", "gaji", "upah", "subsidi", "pajak", "apbn", "daya beli", "phk", "investasi"))
    if actor and max_val > 0 and consequence:
        score += 10

    # ── Editorial mix: amplify concrete numbers/household impact, demote explainer ──
    # number_shock: explicit price figure carrying a number = shock catalyst.
    if max_val > 0 and price_signal:
        score += 8
    # household_impact: income-side pressure (gaji/upah/umr) without a price word.
    if wallet_signal and not price_signal:
        score += 6
    # finance_practical demotion: how-to/tips/procedural headlines (weak hook, med 361 views).
    explainer = any(_matches_keyword(tl, kw) for kw in (
        "cara mudah", "tips", "trik", "kenali", "simak", "panduan", "apa itu",
        "tutorial", "begini cara", "yuk", "strategi investasi"))
    if explainer and signals < 2:
        score -= 25

    # Soft reject penalty (cancelled by sufficient economy signals)
    if signals >= 2:
        pass  # strong signals override soft reject
    else:
        for kw in SOFT_REJECT:
            if _matches_keyword(tl, kw):
                score -= 60
                break

    return (score, f"cats={categories_hit} sig={signals} dyn={dynamic_hits}")

def _learning_bonus(data, source, pattern=None, hook=None):
    """Bounded feedback. Never changes article/body/grounding gates."""
    stats = _compute_performance_stats(data)
    values = list(stats["source_avg"].values())
    if not values or stats["source_count"].get(source, 0) < 3:
        return 0.0
    baseline = sum(values) / len(values)
    score = stats["source_avg"].get(source, baseline)
    bonus = (score - baseline) * 2
    if pattern and stats["pattern_count"].get(pattern, 0) >= 3:
        pvalues = list(stats["pattern_avg"].values())
        pbaseline = sum(pvalues) / len(pvalues) if pvalues else 0.0
        bonus += (stats["pattern_avg"].get(pattern, pbaseline) - pbaseline) * 2
    if hook and stats["hook_count"].get(hook, 0) >= 3:
        hvalues = list(stats["hook_avg"].values())
        hbaseline = sum(hvalues) / len(hvalues) if hvalues else 0.0
        bonus += (stats["hook_avg"].get(hook, hbaseline) - hbaseline) * 2
    return max(-0.06, min(0.06, bonus))


def _source_diversity_penalty(data, source):
    """Bound recent source repetition; never override body/editorial gates."""
    recent = [topic for topic in (data or {}).get("topics", [])[:10]
              if _is_current_topic(topic)]
    count = sum(topic.get("article_source") == source for topic in recent)
    return -min(count * 3, 12)


def _hot_topic_cluster(title, pattern):
    """Stable, explainable cluster key; never creates a claim from article text."""
    entities = sorted(_topic_entities(title))
    if entities:
        return "/".join(entities)
    words = sorted(_title_words(title))[:4]
    return "/".join(words) or (pattern or "other").lower()


def _indonesia_topic_relevance(title, body):
    """Classify domestic relevance; material global economy stories may stand alone."""
    text = f"{title} {body}".lower()
    global_story = _is_global_event(title, body)
    global_finance = _is_global_finance_story(title, body)
    impact_channel = _international_impact_channel(title, body)
    indonesia = bool(re.search(r"\b(indonesia|ri|rupiah|apbn|bank indonesia|bi|kemenkeu|ojk|ikn|ibu kota nusantara)\b", f"{title} {body}", re.I))
    national_actor = bool(re.search(
        r"\b(pemerintah|menteri|kementerian|dpr|bpk|bumn|apbd|gubernur|"
        r"presiden|mahkamah konstitusi|kpk|dprd|ikn|ibu kota nusantara)\b", f"{title} {body}", re.I,
    ))
    if global_story and global_finance:
        if indonesia and impact_channel:
            return "global_indonesia_impact"
        return "international"
    return "national" if indonesia or national_actor else None


GLOBAL_EVENT_RE = re.compile(
    r"\b(federal reserve|the fed|ecb|bank of japan|boj|pboc|opec|minyak dunia|"
    r"tarif dagang|perang dagang|sanksi ekonomi|resesi global|ekonomi global|"
    r"perdagangan global|selat hormuz|hormuz|iran|timur tengah|donald trump|trump|"
    r"amerika serikat|united states|u\.s\.|us|american|america|global|international|"
    r"foreign|overseas|china|tiongkok|jepang|eropa|wall street|"
    r"pasar global|global market|investor global|global stocks|us stocks|oil prices|"
    r"interest rates|trade war|earnings|revenue|profit|acquisition|merger|ipo|"
    r"investment|investor|holding|stake|stocks|stock market|bond yield)\b", re.I,
)
INTERNATIONAL_CHANNELS = {
    "energy": r"bbm|minyak|energi|fuel|oil|harga energi",
    "trade": r"ekspor|impor|tarif|bea masuk|perdagangan|industri|export|import|trade",
    "monetary": r"rupiah|inflasi|suku bunga|biaya pembiayaan|financing costs|daya beli",
    "fiscal": r"apbn|subsidi|penerimaan negara|belanja negara|defisit",
    "investment": r"investasi|arus modal|pasar keuangan|investment|capital flow",
}
IMPACT_RELATION_RE = re.compile(
    r"\b(dampak|berdampak|berisiko|risiko|menekan|tekanan|mendorong|memicu|"
    r"mengerek|menaikkan|menurunkan|akibat|karena|sehingga|pengaruh|terdampak|"
    r"impact|affecting|affected|risk|could affect|would affect)\b", re.I,
)


def _is_global_event(title, body=""):
    return bool(GLOBAL_EVENT_RE.search(f"{title} {body}"))


def _international_impact_channel(title, body):
    """Return source-backed Indonesia impact lane; no event/channel = no global story."""
    if not _is_global_event(title, body):
        return None
    id_re = re.compile(r"\b(indonesia|indonesian|ri|rupiah|apbn|bank indonesia|"
                       r"kemenkeu|pemerintah indonesia|industri dalam negeri)\b", re.I)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body or "") if s.strip()]
    for sentence in sentences:
        if not id_re.search(sentence) or not IMPACT_RELATION_RE.search(sentence):
            continue
        for channel, terms in INTERNATIONAL_CHANNELS.items():
            if re.search(rf"\b(?:{terms})\b", sentence, re.I):
                return channel
    for index, sentence in enumerate(sentences):
        window = " ".join(sentences[index:index + 2])
        if id_re.search(window) and IMPACT_RELATION_RE.search(window) and GLOBAL_EVENT_RE.search(window):
            for channel, terms in INTERNATIONAL_CHANNELS.items():
                if re.search(rf"\b(?:{terms})\b", window, re.I):
                    return channel
    return None


def _story_lane(title, body=""):
    if _international_impact_channel(title, body):
        return "international_indonesia"
    return "international" if _is_global_finance_story(title, body) else "national"


def _is_administrative_distribution_story(title, body):
    """Reject subsidy logistics unless article contains material economic change."""
    text = f"{title} {body}".lower()
    operational = bool(re.search(
        r"\b(penyaluran|pendataan|verifikasi|validasi|tepat sasaran|"
        r"data penerima|identitas kependudukan|nik|distribusi|penertiban)\b", text,
    ))
    subsidy_context = bool(re.search(r"\b(subsidi|elpiji|lpg|bansos|bantuan sosial)\b", text))
    material_change = bool(re.search(
        r"\b(naik|turun|ubah|diubah|menetapkan harga|harga eceran tertinggi|"
        r"anggaran|kuota|nilai bantuan|nominal bantuan|syarat penerima|"
        r"kelompok penerima|biaya rumah tangga|daya beli|tarif|aturan baru|"
        r"beban rumah tangga|pengeluaran rumah tangga)\b", text,
    ))
    return operational and subsidy_context and not material_change


def _verify_one(candidate, now):
    """Single-candidate body fetch + gate check. Returns dict or None. Used by scout_hot_topics."""
    title, url, source = candidate.get("title", ""), candidate.get("url", ""), candidate.get("source", "")
    if not title or not url or not source:
        return None
    body, image, article_ts = _fetch_article_body(url)
    published_ts, timestamp_source, _ = _resolve_published_timestamp(article_ts, candidate.get("ts", 0), now)
    if not published_ts:
        return None
    eligible, reason = _is_eligible_candidate(title, body, source)
    if not eligible:
        return None
    indonesia_relevance = _indonesia_topic_relevance(title, body)
    if not indonesia_relevance:
        return None
    pattern, confidence = _classify_pattern(title, body)
    topic_score, economy_score, impact_score = _topic_score(title, body)
    source_quality = SOURCES.get(source, {}).get("score", candidate.get("score", 0))
    freshness = max(0.0, 24 - ((now - published_ts) / 3600)) / 24
    hot_score = round(topic_score * 10 + confidence * 10 + freshness * 10 + source_quality
                      + _learning_bonus({}, source, pattern)
                      + _engagement_priority_bonus(title, body), 3)
    return {
        "cluster": _hot_topic_cluster(title, pattern), "title": title,
        "canonical_url": _canonical_url(url), "source": source,
        "published_ts": published_ts, "timestamp_source": timestamp_source,
        "pattern": pattern, "pattern_confidence": round(confidence, 3),
        "topic_score": topic_score, "economy_score": economy_score, "impact_score": impact_score,
        "hot_score": hot_score, "body_verified": True, "image_available": bool(image),
        "indonesia_relevance": indonesia_relevance, "reason": reason,
        "has_material_economic_signal": True,
        "lane": _story_lane(title, body),
        "impact_channel": _international_impact_channel(title, body),
        "global_event": _is_global_event(title, body),
        "_body": body, "_image": image,
    }

def scout_hot_topics(articles, now=None, limit=HOT_TOPIC_LIMIT, per_source_limit=2, data=None,
                     allow_cluster_repeats=False):
    """Read-only body-verified ranking; fallback may reuse a cluster after primary fails."""
    now = time.time() if now is None else now
    verified = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        fut_map = {}
        for candidate in articles:
            title, url, source = candidate.get("title", ""), candidate.get("url", ""), candidate.get("source", "")
            if not title or not url or not source:
                continue
            fut_map[ex.submit(_verify_one, candidate, now)] = candidate
        for f in as_completed(fut_map):
            try:
                item = f.result()
                if item:
                    verified.append(item)
            except Exception as e:
                log.warning(f"Body verify failed: {e}")
    # Futures finish nondeterministically; preserve feed order on equal scores.
    feed_order = {_canonical_url(a.get("url", "")): i for i, a in enumerate(articles)}
    verified.sort(key=lambda item: (-item["hot_score"], feed_order.get(item["canonical_url"], len(articles))))
    selected, sources, clusters = [], {}, set()
    for item in verified:
        if item["source"] in sources and sources[item["source"]] >= per_source_limit:
            continue
        if not allow_cluster_repeats and item["cluster"] in clusters:
            continue
        sources[item["source"]] = sources.get(item["source"], 0) + 1
        clusters.add(item["cluster"])
        item["rank"] = len(selected) + 1
        selected.append(item)
        if len(selected) == limit:
            break
    return selected


def international_dry_run_candidates(hot_topics, fallback_topics=()):
    """Return body-verified international topics available to dry-run output."""
    return [topic for topic in (*hot_topics, *fallback_topics)
            if topic.get("lane") == "international"]


def save_hot_topics(topics, generated_ts=None):
    payload = {"generated_ts": generated_ts or time.time(), "topics": topics}
    tmp = HOT_TOPICS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    tmp.replace(HOT_TOPICS_FILE)


def _publish_candidates_from_hot_topics(articles, topics, fallback_topics=()):
    """Return primary then fallback body-verified scout choices, in rank order."""
    by_url = {_canonical_url(article.get("url", "")): article for article in articles}
    seen = set()
    candidates = []
    for topic in (*topics, *fallback_topics):
        url = topic.get("canonical_url")
        if url in by_url and url not in seen:
            candidates.append(by_url[url])
            seen.add(url)
    return candidates


def _publisher_pool(articles, hot_topics, verified_topics):
    """Hot topics boost order; verified RSS pool remains available as fallback."""
    by_url = {_canonical_url(article.get("url", "")): article for article in articles}
    ordered_urls = []
    for topic in (*hot_topics, *verified_topics):
        url = topic.get("canonical_url")
        if url in by_url and url not in ordered_urls:
            # Reuse body verification during ranking; do not rank title-only again.
            if topic.get("_body"):
                by_url[url]["body"] = topic["_body"]
            ordered_urls.append(url)
    return [by_url[url] for url in ordered_urls] + [
        article for article in articles
        if _canonical_url(article.get("url", "")) not in ordered_urls
    ]


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
    # Score only editorially valid candidates; numeric keyword hits cannot rescue advice/promo.
    filtered = []
    for a in candidates:
        editorial_reason = _editorial_candidate_gate(a.get("title", ""), a.get("body", ""))
        if editorial_reason:
            a["eco_score"] = 0
            a["_reason"] = editorial_reason
            continue
        filtered.append(a)
    candidates = filtered
    if not candidates:
        return None
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
        _, _, hook = _content_metadata(a.get("title", ""), a.get("body", ""))
        learning = _learning_bonus(data or {}, a["source"], a.get("pattern"), hook)
        a["learning_bonus"] = learning
        a["lane"] = _story_lane(a.get("title", ""), a.get("body", ""))
        a["impact_channel"] = _international_impact_channel(a.get("title", ""), a.get("body", ""))
        a["_weight"] = (eco_score + freshness + relevance + source_quality + learning
                         + _engagement_priority_bonus(a.get("title", ""), a.get("body", ""))
                         + _source_diversity_penalty(data, a["source"]))
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
        response = httpx.get(url, headers={"User-Agent": UA}, timeout=15, follow_redirects=True)
        size = _image_size(response.content) if response.status_code == 200 else None
        if size and size[0] >= 800 and size[1] >= 450:
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
                     str(tag.get("name") or tag.get("property") or tag.get("itemprop") or ""), re.I)
    ]
    values += [tag.get("content") for tag in soup.find_all(attrs={"itemprop": re.compile(r"datePublished|dateCreated", re.I)})]
    values += [tag.get("datetime") for tag in soup.find_all("time")]
    for tag in soup.find_all(attrs={"data-published": True}):
        values.extend((tag.get("data-published"), tag.get("data-published-at")))
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text())
        except (TypeError, json.JSONDecodeError):
            continue

        def collect_dates(value):
            if isinstance(value, dict):
                if value.get("datePublished"):
                    values.append(value["datePublished"])
                for child in value.values():
                    collect_dates(child)
            elif isinstance(value, list):
                for child in value:
                    collect_dates(child)

        collect_dates(data)
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


def _resolve_published_timestamp(article_ts, rss_ts, now):
    """Use article time; bounded RSS fallback only when article time is absent."""
    if article_ts:
        if article_ts > now + 300:
            return 0, "article", "future"
        if now - article_ts > 86400:
            return 0, "article", "stale"
        return article_ts, "article", "ok"
    if rss_ts and rss_ts <= now + 300 and now - rss_ts <= 86400:
        return rss_ts, "rss_fallback", "ok"
    return 0, "missing", "missing"


# In-memory body cache — avoids double-fetch between scout_hot_topics and main()
_BODY_CACHE = {}

def _fetch_article_body(url):
    """Fetch article HTML, extract clean text + og:image + source publish time."""
    cache_key = _canonical_url(url)
    if cache_key in _BODY_CACHE:
        return _BODY_CACHE[cache_key]
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
        # Article body — CNBC uses generated class names not covered by local selectors.
        host = urllib.parse.urlsplit(url).netloc.lower()
        body_el = None
        if "cnbc.com" in host:
            body_el = soup.select_one(".ArticleBody-articleBody")
        # article body — try known ID selectors
        body_el = (
            body_el
            or soup.find("div", class_=lambda c: c and "detail__body-text" in c)
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
            # Remove publisher UI noise before paragraph enters source body/evidence.
            txt = re.sub(r"(?i)^\s*scroll\s+to\s+continue\s+with\s+content\s*", "", txt).strip()
            if len(txt) > 20:
                paras.append(txt)
        if not paras:
            raw = body_el.get_text(separator="\n", strip=True)
            paras = [l.strip() for l in raw.split("\n") if len(l.strip()) > 40]
        text = "\n".join(paras)
        # Detik ad-insertion marker can survive paragraph extraction and must not
        # become source evidence or generated slide text.
        text = re.sub(r"(?im)^\s*scroll\s+to\s+continue\s+with\s+content\s*$\n?", "", text)
        # Strip inline "Baca juga"/"Baca juga artikel" + trailing URL from body
        # CNBC/detik often embed cross-links mid-paragraph that leak extra URLs into LLM context
        text = re.sub(r'\(?\s*Baca\s+(?:juga|artikel|tautan|terkait)\s*(?::|.*?)\s*(https?://\S+)', '', text, flags=re.I)
        text = text if len(text) > 200 else ""
    except Exception as e:
        log.warning(f"Fetch body: {url[:60]} — {e}")
    result = (text, og_image, published_ts)
    _BODY_CACHE[cache_key] = result
    return result


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
    "asn", "pppk", "guru", "tenaga pendidik", "aparatur sipil negara",
    "kebijakan", "regulasi", "tarif", "insentif", "hilirisasi", "perdagangan", "keuangan",
    "penerimaan", "belanja", "pembiayaan", "perbankan", "asuransi", "koperasi",
    # International economy; body gate requires explicit finance/economy evidence.
    "federal reserve", "the fed", "opec", "selat hormuz", "hormuz", "global economy", "economic recession",
    "interest rate", "interest rates", "inflation", "gdp", "trade war", "oil price", "oil prices",
    "global market", "stocks", "stock market", "economy", "tarif trump", "kebijakan trump",
    "donald trump", "trump", "earnings", "revenue", "profit", "acquisition", "merger",
    "ipo", "holding", "stake", "stocks", "stock market", "bond yield",
    "earnings", "revenue", "profit", "acquisition", "merger", "investment", "investor",
    # Tech/digital economy
    "startup", "series a", "series b", "series c", "funding", "pendanaan",
    "fintech", "edutech", "healthtech", "e-commerce", "ai ", "artificial intelligence",
    "digital", "platform", "aplikasi", "data cent", "cloud", "indosat",
    "telkom", "gojek", "tokopedia", "bukalapak", "unicorn", "decacorn",
    "nvidia", "openai", "agentic", "ipo", "akuisisi", "merger",
    "revenue", "profit", "laba", "ventura", "venture",
    "data centre", "datacenter", "data center", "centres", "centers", "acquisition",
    "acquires", "buys", "expands", "expansion", "ekspansi",
    "raises", "raise", "ventures", "venture capital",
)


MATERIAL_DIGITAL_TITLE_SIGNALS = (
    "funding", "pendanaan", "series a", "series b", "series c", "seri a", "seri b", "seri c",
    "ipo", "akuisisi", "merger", "phk", "laba", "rugi", "pendapatan", "investasi",
    "ekspansi", "kontrak", "bangkrut", "pailit", "regulasi", "acquisition", "acquires",
    "raises", "raised", "expands", "profit", "revenue",
)

GLOBAL_ECONOMY_TITLE_SIGNALS = (
    "federal reserve", "the fed", "ecb", "bank of japan", "boj", "pboc", "opec",
    "interest rate", "interest rates", "inflation", "gdp", "recession", "resesi",
    "trade war", "tarif", "trade", "oil price", "oil prices", "commodity", "commodities",
    "currency", "dollar", "stocks rally", "stocks fall", "stock market", "bond yield",
    "investment", "invests", "investor", "exports", "imports", "global economy",
    "ekonomi global", "perdagangan global", "harga minyak dunia", "selat hormuz", "hormuz",
    "trump", "donald trump", "earnings", "revenue", "profit", "acquisition", "merger",
    "ipo", "holding", "stake", "stocks", "stock market",
)


def _has_economy_title_signal(title):
    """Keep mixed/general feeds from consuming economy-pipeline LLM attempts."""
    title_lower = title.lower()
    return any(re.search(rf"(?<!\w){re.escape(signal)}(?!\w)", title_lower)
               for signal in ECONOMY_SELECTION_SIGNALS)


def _has_source_title_signal(title, source):
    """Keep source-specific noise out before body fetch; body gate remains authoritative."""
    title_lower = title.lower()
    if source == "dailysocial":
        return any(signal in title_lower for signal in MATERIAL_DIGITAL_TITLE_SIGNALS)
    if source == "cnbc_global":
        if "revenue chief" in title_lower:
            return False
        return any(signal in title_lower for signal in GLOBAL_ECONOMY_TITLE_SIGNALS)
    if source == "bbc_business":
        return any(signal in title_lower for signal in (
            "business", "company", "companies", "sales", "revenue", "profit", "jobs",
            "economy", "inflation", "interest rates", "trade", "market", "markets",
            "investment", "investor", "bank", "energy", "oil", "electric vehicle",
        ))
    return True


def _editorial_candidate_gate(title, body):
    """Keep Techbro on material economy stories, not advice or promotion."""
    text = f"{title} {body}".lower()
    cfg = EDITORIAL_SELECTION
    personal = cfg.get("personal_finance_advice", ())
    retail = cfg.get("retail_markers", ())
    promotion = cfg.get("promotion_markers", ())
    event = cfg.get("event_markers", ())
    material = cfg.get("material_change_markers", ())
    topic = cfg.get("material_topic_markers", ())
    title_lower = title.lower()
    if any(term in title_lower for term in cfg.get("non_economic_title_markers", ())):
        return "non_economic_title"
    historical_hits = sum(term in title_lower for term in cfg.get("historical_title_markers", ()))
    if historical_hits >= 2 and not any(term in title_lower for term in cfg.get("current_title_markers", ())):
        return "historical_economy_story"
    if (any(term in title_lower for term in cfg.get("personal_title_markers", ()))
            and sum(term in text for term in personal) >= 2):
        return "personal_finance_advice"
    retail_hits = sum(term in text for term in retail)
    promo_hits = sum(term in text for term in promotion)
    event_hits = sum(term in text for term in event)
    material_hits = sum(term in text for term in material)
    if retail_hits and (promo_hits >= 2 or event_hits >= 1) and material_hits < 2:
        return "retail_event_promotion"
    if not any(term in text for term in topic) or not any(term in text for term in material):
        return "no_material_economic_topic"
    return None


def _is_eligible_candidate(title, body, source):
    """Full economy gate shared by main pick and retry path.
    Returns (eligible: bool, reason: str)."""
    title_lower = title.lower()
    # Viral routing: title angle is editorial input, not proof of falsehood.
    # Body grounding and publish validators remain authoritative.
    if not body or len(body) < 500:
        return False, "body_under_500_chars"
    # Stable reject order: source noise, specific advice/promo, routine copy, material gate.
    if not _has_source_title_signal(title, source):
        return False, "source_title_not_material"
    editorial_reason = _editorial_candidate_gate(title, body)
    if editorial_reason == "personal_finance_advice":
        return False, editorial_reason
    if _is_corporate_promo(title, body):
        return False, "corporate_promo"
    if _is_low_value_promo(title, body):
        return False, "LOW_VALUE_PROMO"
    historical_markers = (
        "autobiografi", "biografi", "surat-surat", "lahir", "tahun lalu",
        "pensiun", "pensiunan", "proklamator",
    )
    current_action_markers = (
        "2026", "2027", "saat ini", "tahun ini", "baru-baru ini",
        "ditetapkan", "disahkan", "berlaku", "mengusulkan", "mengubah",
        "menaikkan", "menurunkan", "menyalurkan", "mengakuisisi", "melantai",
        "membagikan dividen", "menerbitkan utang",
    )
    body_lower = body.lower()
    if (sum(marker in body_lower for marker in historical_markers) >= 2
            and not any(marker in body_lower for marker in current_action_markers)):
        return False, "historical_profile_without_current_economy_action"
    global_ok = source not in {"cnn_global", "cnbc_global"} or _is_global_finance_story(title, body)
    if not global_ok:
        return False, "non-finance global story"
    if _is_routine_market_story(title, body):
        return False, "routine market story"
    if _is_administrative_distribution_story(title, body):
        return False, "administrative_distribution_story"
    if _is_empty_commentary(title, body):
        return False, "empty commentary"

    if source == "dailysocial" and not _is_material_digital_story(title, body):
        return False, "non_material_digital_story"
    if _is_low_value_corporate_story(title, body):
        return False, "low_value_corporate_story"
    if not has_material_economic_signal(title, body):
        return False, "NO_MATERIAL_ECONOMIC_SIGNAL"
    if not _is_techbro_relevant(body):
        return False, "not techbro relevant"
    if _indonesia_topic_relevance(title, body) is None:
        return False, "no_indonesia_relevance"
    topic_score, economy_score, impact_score = _topic_score(title, body)
    pattern_name, pattern_confidence = _classify_pattern(title, body)
    # ponytail: patterns rank/hooks only; evidence gates above decide eligibility.
    pattern_reason = (f"pattern={pattern_name} conf={pattern_confidence:.2f}"
                      if pattern_name else "pattern=none")
    return True, f"has_material_economic_signal=True {pattern_reason} topic={topic_score} economy={economy_score} impact={impact_score}"


MATERIAL_ECONOMIC_SIGNALS = (
    # Public policy, regulation, and public money.
    "kebijakan", "regulasi", "peraturan baru", "aturan baru", "putusan", "ditetapkan", "disahkan",
    "apbn", "apbd", "anggaran negara", "anggaran daerah", "subsidi", "pajak",
    "defisit", "penerimaan negara", "belanja pemerintah", "tarif resmi",
    # Material business events.
    "investasi", "pendanaan", "pendapatan", "revenue", "laba", "profit", "rugi",
    "phk", "pemutusan hubungan kerja", "ekspansi", "akuisisi", "merger", "ipo",
    "bangkrut", "pailit", "kontrak", "tender", "ekspor", "impor", "dividen",
    "restrukturisasi", "utang baru", "sanksi", "denda", "audit", "harga saham",
    # Economy-wide household, labour, and market impact.
    "inflasi", "daya beli", "bi rate", "suku bunga", "ojk", "upah minimum",
    "lapangan kerja", "pengangguran", "biaya rumah tangga", "harga pangan",
    "harga bbm", "harga listrik", "kredit umkm", "pembiayaan umkm", "electric vehicle",
    "vehicle sales", "car makers", "jobs", "employment",
)


def has_material_economic_signal(title, body):
    """Return explicit eligibility boolean for material economic news.

    Generic economy vocabulary, a lone price, or a retail discount is not
    material. Keep this classifier event/impact based; scoring remains ranking.
    """
    text = f"{title} {body}".lower()
    return any(signal in text for signal in MATERIAL_ECONOMIC_SIGNALS)


def _is_low_value_promo(title, body):
    """Reject retail/service promotion unless material signal gate already passes."""
    text = f"{title} {body}".lower()
    promo_markers = (
        "promo", "diskon", "sale", "cashback", "voucher", "gratis", "hadiah",
        "kupon", "potongan harga", "minimum transaksi", "buy one get one", "belanja",
        "program hadiah", "aplikasi", "layanan digital", "solusi keuangan",
        "bantu nasabah", "membantu nasabah", "customer experience", "kemudahan",
    )
    retail_markers = (
        "transmart", "supermarket", "minimarket", "hypermart", "toko", "mal", "mall",
        "retail", "ritel", "produk", "nasabah", "konsumen", "pengguna",
    )
    promo_hits = sum(marker in text for marker in promo_markers)
    retail_hits = sum(marker in text for marker in retail_markers)
    return promo_hits >= 2 and retail_hits >= 1 and not has_material_economic_signal(title, body)


def _is_corporate_promo(title, body):
    """Backward-compatible alias for callers using old promo helper."""
    return _is_low_value_promo(title, body)


def _final_publish_veto(article, result):
    """Re-run source relevance gates after LLM output; publishing fails closed."""
    title = article.get("title", "")
    body = article.get("body", "")
    material = has_material_economic_signal(title, body)
    low_value_promo = _is_low_value_promo(title, body)
    angle_arc = f"{result.get('angle', '')} {result.get('arc', '')}".lower().replace("-", "_")
    if low_value_promo and not material:
        return "LOW_VALUE_PROMO: material_economic_signal=False"
    if "wallet_pressure" in angle_arc and not material:
        return "WALLET_PRESSURE_WITHOUT_MATERIAL_ECONOMIC_SIGNAL"
    return None


def _is_low_value_corporate_story(title, body):
    """Reject corporate profile/strategy copy without a public-economy event."""
    text = f"{title} {body}".lower()
    profile_markers = (
        "strategi", "era digital", "layanan digital", "layanan keuangan",
        "kinerja positif", "bukukan laba", "catat laba", "masa depan",
        "inovasi", "solusi", "nasabah", "pemegang polis", "tata kelola",
        "keamanan", "privasi", "prinsip syariah", "rbc", "psak 117",
    )
    public_event_markers = (
        "akuisisi", "merger", "ipo", "phk", "dividen", "kontrak", "tender",
        "ekspor", "impor", "investasi senilai", "regulasi baru", "peraturan baru",
        "sanksi", "audit", "denda", "pailit", "restrukturisasi", "utang baru",
        "harga saham", "putusan", "tarif", "izin usaha dicabut",
    )
    corporate_identity = bool(re.search(
        r"\b(bank|bumn|asuransi|perusahaan|emiten|startup|aplikasi|platform|life)\b",
        text,
    ))
    profile_hits = sum(marker in text for marker in profile_markers)
    has_public_event = any(marker in text for marker in public_event_markers)
    return corporate_identity and profile_hits >= 2 and not has_public_event


def _is_material_digital_story(title, body):
    """Allow digital-economy stories only for material business events."""
    text = f"{title} {body}".lower()
    event = (
        "pendanaan", "funding", "seri a", "seri b", "seri c", "akuisisi",
        "merger", "ipo", "phk", "kontrak", "investasi", "ekspansi",
        "laba", "rugi", "pendapatan", "bangkrut", "pailit",
    )
    return any(marker in text for marker in event)


def _is_techbro_relevant(body):
    """Require a concrete Indonesia or global finance/economy signal in article body."""
    return bool(re.search(
        r"\b(indonesia|ri|rupiah|apbn|anggaran|pajak|subsidi|bansos|"
        r"pemerintah indonesia|presiden|mahkamah konstitusi|mk|kemenkeu|"
        r"bank indonesia|bi|ojk|bpk|dpr|federal reserve|the fed|ecb|bank sentral eropa|"
        r"bank of japan|boj|bank rakyat china|pboc|opec|harga minyak dunia|selat hormuz|hormuz|"
        r"iran|timur tengah|trump|donald trump|tarif dagang|perang dagang|sanksi ekonomi|"
        r"resesi global|ekonomi global|perdagangan global|investasi|investor|"
        r"saham|pasar saham|obligasi|pendapatan|laba|rugi|akuisisi|merger|ipo|"
        r"earnings|revenue|profit|acquisition|merger|stocks|investment|trade|"
        r"electric vehicle|vehicle sales|car makers|jobs|employment|business|company|"
        r"jakarta|surabaya|bandung|medan|semarang|makassar|palembang|"
        r"kalimantan|sumatera|sulawesi|papua|maluku|bali|nusa tenggara|"
        r"menteri|kementerian|direktur jenderal|gubernur|bupati|walikota|"
        r"dpr ri|dprd|kpk|kejaksaan|mahkamah agung|bumn|bumd)\b",
        body, re.IGNORECASE,
    ))


def _is_global_finance_story(title, body):
    """Global desk is for an explicit economy/finance headline, never general geopolitics."""
    headline = title.lower()
    headline_finance = any(word in headline for word in (
        "fed", "federal reserve", "ecb", "bank sentral", "suku bunga", "inflasi",
        "resesi", "gdp", "ekonomi", "tarif", "dagang", "opec", "minyak",
        "pasar", "saham", "obligasi", "dolar", "mata uang", "utang", "investasi",
        "stocks", "stock market", "interest rates", "oil prices", "trade", "economy", "stake",
        "earnings", "revenue", "profit", "acquisition", "merger", "ipo", "holding",
        "sales", "electric vehicle", "vehicle sales", "car makers", "car maker",
        "energy", "markets", "stock", "stocks",
        "share", "shares", "stake",
        "pendapatan", "laba", "rugi", "akuisisi", "merger", "ipo", "investasi",
    ))
    trump_policy = bool(re.search(r"\b(trump|donald trump)\b", headline)) and bool(re.search(
        r"\b(tarif|dagang|impor|ekspor|sanksi|minyak|investasi|ekonomi|pajak|dolar|"
        r"trade|tariff|import|export|sanction|oil|investment|economy)\b",
        f"{headline} {body}".lower(),
    ))
    hormuz_energy = bool(re.search(r"\b(selat hormuz|hormuz)\b", headline)) and bool(re.search(
        r"\b(minyak|energi|bbm|oil|energy|fuel)\b", f"{headline} {body}".lower(),
    ))
    return (headline_finance or trump_policy or hormuz_energy) and _is_techbro_relevant(body)


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
    global_shock = any(word in text for word in (
        "selat hormuz", "hormuz", "opec", "perang dagang", "tarif trump", "sanksi ekonomi",
    ))
    return market and not policy and not global_shock


def _engagement_priority_bonus(title, body):
    """Prefer decision/public-money stories over routine market updates."""
    text = f"{title} {body}".lower()
    bonus = 0
    for terms, value in (
        (("kebijakan", "aturan", "peraturan", "putusan", "ditetapkan", "disahkan"), 12),
        (("anggaran", "apbn", "apbd", "subsidi", "pajak", "belanja pemerintah"), 10),
        (("korupsi", "kerugian negara", "audit bpk", "temuan bpk"), 10),
        (("harga", "biaya", "tarif", "daya beli", "rumah tangga", "konsumen"), 6),
        (("siapa membayar", "pihak yang membayar", "pihak yang menerima", "penerima manfaat", "untuk subsidi"), 8),
        (("konflik", "berhadapan", "dibandingkan", "sementara", "belum jelas", "masih menunggu"), 5),
        (("rupiah melemah", "rupiah menguat", "ihsg", "harga emas", "harga minyak"), -12),
    ):
        if any(term in text for term in terms):
            bonus += value
    return bonus


# ── Pressbox-style Pattern Classification ──────────────────────────────────────
# 5 PINDAR patterns with keyword triggers + priority ordering.
# Priority: KORUPSI > KEBIJAKAN > PROYEK > PERDAGANGAN > PASAR
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
    "PERDAGANGAN": {
        "priority": 4,
        "label": "Perdagangan & Komoditas",
        "desc": "Impor, ekspor, neraca dagang, komoditas, harga pangan — dampak konsumen",
        "keywords": [
            "impor", "ekspor", "eksportir", "importir",
            "neraca perdagangan", "neraca dagang", "surplus", "defisit",
            "perdagangan", "komoditas", "komoditi",
            "bea cukai", "bea masuk", "bea keluar", "tarif impor",
            "harga pangan", "harga beras", "harga bawang", "harga cabai",
            "harga minyak", "harga daging", "harga telur", "harga gula",
            "stok", "pasokan", "ketersediaan", "kelangkaan",
            "bapanas", "bulog", "cadangan pangan",
            "ton", "ribu ton", "juta ton",
            "panen", "gagal panen", "musim panen",
            "data ekspor", "data impor", "ekspor-impor", "impor barang",
            "laporan perdagangan", "indonesia-china", "indonesia-india",
            "diimpor dari", "diekspor ke", "dipasok dari",
        ],
    },
    "PASAR": {
        "priority": 5,
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
        thresholds = {"KORUPSI": 5, "KEBIJAKAN": 5, "PROYEK": 6, "PERDAGANGAN": 5, "PASAR": 5}
        divisor = thresholds.get(name, 4)
        confidence = min(hits / divisor, 1.0)

        # Higher-priority patterns need fewer hits to qualify
        min_hits = {1: 2, 2: 2, 3: 3, 4: 2, 5: 3}.get(cfg["priority"], 3)
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


def _content_metadata(title, body):
    """Derive auditable content labels; never silently default every post."""
    pattern, _ = _classify_pattern(title, body)
    text = f"{title} {body}".lower()
    if pattern is None:
        if re.search(r"\b(rupiah|saham|ihsg|ipo|bursa|emiten|pasar modal|obligasi|dividen|bunga kredit|cicilan|kpr|utang|investasi)\b", text):
            pattern = "PASAR"
        elif re.search(r"\b(impor|ekspor|harga pangan|pasokan|stok|komoditas)\b", text):
            pattern = "PERDAGANGAN"
        elif re.search(r"\b(kebijakan|aturan|peraturan|ditetapkan|berlaku|apbn|subsidi)\b", text):
            pattern = "KEBIJAKAN"
    amount = bool(re.search(r"(?:rp\s*)?\d[\d.,]*\s*(?:%|persen|triliun|miliar|juta|ribu)", text))
    actor = bool(re.search(r"\b(prabowo|jokowi|menteri|gubernur|kemenkeu|ojk|danantara|bumn|pemerintah)\b", text))
    wallet = bool(re.search(r"\b(harga|biaya|tarif|gaji|upah|umr|ump|subsidi|pajak|daya beli)\b", text))
    decision = bool(re.search(r"\b(tetapkan|ditetapkan|berlaku|disahkan|targetkan|usulkan|batasi|larang|ubah)\b", text))
    finance_practical = bool(re.search(r"\b(bunga|cicilan|kpr|utang|kredit|investasi|risiko|cash flow|arus kas)\b", text))
    if finance_practical:
        hook = "finance_practical"
    elif amount and wallet:
        hook = "wallet_impact"
    elif amount:
        hook = "number_shock"
    elif actor and decision:
        hook = "named_decision"
    elif decision:
        hook = "decision_impact"
    else:
        hook = "source_explainer"
    supply_story = bool(re.search(r"\b(pasokan|distribusi|peternak|produsen|konsumen|surplus|kelangkaan)\b", text))
    if finance_practical:
        arc = "personal_finance_explainer"
    elif pattern == "PERDAGANGAN" and supply_story:
        arc = "supply_shock"
    elif wallet and amount:
        arc = "household_impact"
    elif pattern == "KORUPSI":
        arc = "public_money_trail"
    elif pattern == "KEBIJAKAN":
        arc = "policy_decision_story"
    elif pattern == "PROYEK":
        arc = "public_money_trail"
    elif pattern == "PERDAGANGAN":
        arc = "supply_shock"
    elif pattern == "PASAR" and (actor or decision):
        arc = "market_decision"
    else:
        arc = "market_shock" if pattern == "PASAR" else "source_explainer"
    return pattern, arc, hook


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
    if not isinstance(text, str):
        return ""
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
    if len(body) < 500:
        return "body_under_500_chars"
    # Four distinct source claims gives writer room without rejecting compact news.
    if len(source_claim_plan(article).splitlines()) < 4:
        return "insufficient_source_claims_for_four_posts"
    return None


def source_claim_plan(article):
    """Give writer distinct substantive source sentences, never title-derived facts."""
    body = re.sub(r"\s+", " ", article.get("body") or "").strip()
    selected = []
    seen = set()
    for sentence in re.split(r"(?<=[.!?])\s+", body):
        sentence = sentence.strip()
        key = sentence.lower()
        if len(sentence) >= 25 and key not in seen:
            seen.add(key)
            selected.append(sentence)
    return "\n".join(f"- {s}" for s in selected[:12])


def source_claim_map(article):
    """Rank distinct source sentences and assign one evidence unit to each slide."""
    body = re.sub(r"\s+", " ", article.get("body") or "").strip()
    sentences = []
    seen = set()
    for sentence in re.split(r"(?<=[.!?])\s+", body):
        sentence = sentence.strip()
        key = sentence.lower()
        if len(sentence) >= 25 and key not in seen:
            seen.add(key)
            sentences.append(sentence)

    slide_signals = {
        "post_1": ("menetapkan", "ditetapkan", "resmi", "usulkan", "calon", "keputusan", "perubahan"),
        "post_2": ("menteri", "ketua", "gubernur", "menurut", "mengatakan", "ujar", "dpr", "ojk", "bi"),
        "post_3": ("melalui", "dilakukan", "penyaluran", "program", "kerja sama", "pembiayaan", "mengubah"),
        "post_4": ("tujuan", "agar", "supaya", "mulai", "berlaku", "target", "menjaga", "hingga"),
        "post_5": ("dampak", "memengaruhi", "mempengaruhi", "risiko", "biaya", "harga", "konsumen", "pelaku usaha"),
        "post_6": ("menunggu", "belum", "proses", "pembahasan", "persetujuan", "ditentukan", "perlu"),
    }

    def score(sentence, signals):
        text = sentence.lower()
        value = 1
        value += sum(2 for signal in signals if signal in text)
        if re.search(r"(?:rp\s*)?\d|\d+\s*(?:persen|%|miliar|juta|triliun)", text, re.I):
            value += 2
        if '"' in sentence or '“' in sentence:
            value += 1
        return value

    if article.get("pattern") == "PASAR":
        sentences = [s for s in sentences if "calon gubernur bi" not in s.lower()]
    remaining = list(enumerate(sentences))
    result = {}
    for slide, signals in slide_signals.items():
        if not remaining:
            break
        index, sentence = max(remaining, key=lambda item: (score(item[1], signals), -item[0]))
        remaining = [(i, s) for i, s in remaining if i != index]
        result[slide] = [{"sentence": sentence, "score": score(sentence, signals)}]
    return result


STORY_FUNCTIONS = {
    "post_1": "hook_tension",
    "post_2": "proof",
    "post_3": "context_cause",
    "post_4": "impact_stakes",
    "post_5": "tradeoff_unknown",
    "post_6": "judgment_cta",
}


POLICY_WINNING_ROLES = {
    "post_1": ("menetapkan", "ditetapkan", "resmi", "usulan", "mengusulkan", "opsi", "wacana", "bakal", "akan", "perubahan", "dialihkan", "dipindahkan"),
    "post_2": ("menteri", "pemerintah", "pemda", "provinsi", "kabupaten", "kewenangan", "aturan", "undang", "uu", "dpr"),
    "post_3": ("biaya", "anggaran", "jumlah", "kapasitas", "menghitung", "perhitungan", "pendanaan", "pembiayaan", "beban", "guru", "pegawai"),
    "post_4": ("tujuan", "agar", "supaya", "pemerataan", "distribusi", "pembahasan", "pekan", "minggu", "selanjutnya", "rencana", "mulai"),
    "post_5": ("beban", "biaya", "anggaran", "manfaat", "untung", "rugi", "daerah", "pusat", "belum", "masih", "status", "nasib"),
    "post_6": ("belum", "masih", "menunggu", "pembahasan", "persetujuan", "ditentukan", "opsi", "pilih", "perlu", "keputusan"),
}

POLICY_TRADEOFF_MARKERS = ("tetapi", "namun", "sedangkan", "sementara", "di sisi lain", "mengurangi", "menambah")
POLICY_TRADEOFF_TERMS = ("beban", "biaya", "anggaran", "manfaat", "untung", "rugi", "keuntungan")
POLICY_STATUS_GAP_MARKERS = ("menjadi", "berubah", "beralih", "dipindahkan", "dialihkan", "ditarik", "kini", "sebelumnya", "setelah", "dibanding", "opsi", "usul", "wacana", "bakal")


def _policy_winner_enabled(article):
    if article.get("pattern") != "KEBIJAKAN":
        return False
    body = article.get("body", "")
    text = f"{article.get('title', '')} {body}".lower()
    authority = re.search(r"\b(pemerintah|presiden|menteri|kementerian|dpr|gubernur|pemda|otoritas)\b", text)
    policy_action = re.search(r"\b(menetapkan|ditetapkan|mengusulkan|usulan|opsi|wacana|aturan|peraturan|kebijakan|berlaku|kewenangan|dipindahkan|dialihkan|dilarang|subsidi|tarif)\b", text)
    # Only decision/transition stories use status-gap + trade-off arc.
    decision_trigger = re.search(
        r"\b(?:sebelumnya.{0,100}(?:kini|sekarang|mengusulkan)|"
        r"(?:kini|sekarang).{0,100}(?:opsi|usul|wacana|mengusulkan|berubah|"
        r"beralih|dipindahkan|dialihkan)|(?:opsi|usul|wacana)\s+"
        r"(?:baru|resmi|pemerintah)|(?:berubah|beralih|dipindahkan|dialihkan|"
        r"ditarik)\b)", body.lower()
    )
    return bool(authority and policy_action and decision_trigger)


def _policy_tradeoff_sentence(body):
    return next((sentence for sentence in _source_sentences(body)
                 if any(marker in sentence.lower() for marker in POLICY_TRADEOFF_MARKERS)
                 and sum(term in sentence.lower() for term in POLICY_TRADEOFF_TERMS) >= 2), None)


def _policy_status_gap_sentence(body):
    return next((sentence for sentence in _source_sentences(body)
                 if any(marker in sentence.lower() for marker in POLICY_STATUS_GAP_MARKERS)
                 and re.search(r"\b(akan|bakal|opsi|usul|wacana|menjadi|berubah|beralih|dipindahkan|dialihkan|ditarik|kini|sebelumnya|dibanding)\b", sentence.lower())), None)


def policy_winner_evidence(article):
    """Select literal evidence for policy decision arc; empty role blocks arc."""
    if not _policy_winner_enabled(article):
        return {}
    result = {}
    for slide, signals in POLICY_WINNING_ROLES.items():
        ranked = []
        for index, sentence in enumerate(_source_sentences(article.get("body", ""))):
            text = sentence.lower()
            score = sum(3 for signal in signals if signal in text)
            if re.search(r"(?:rp\s*)?\d|\d+\s*(?:persen|%|miliar|juta|triliun)", text, re.I):
                score += 2
            if score:
                ranked.append((score, -index, sentence))
        result[slide] = [sentence for _, _, sentence in sorted(ranked, reverse=True)[:2]]
    return result


def _validate_policy_winner_arc(article, posts):
    """Require source-backed six-slide policy decision story."""
    evidence = policy_winner_evidence(article)
    if not evidence:
        return []
    issues = []
    status_gap_source = _policy_status_gap_sentence(article.get("body", ""))
    tradeoff_source = _policy_tradeoff_sentence(article.get("body", ""))
    if not status_gap_source:
        issues.append("post_1: missing literal status-gap source")
    if not tradeoff_source:
        issues.append("post_5: missing literal trade-off source")
    for slide, sentences in evidence.items():
        if not sentences:
            issues.append(f"{slide}: missing winning arc evidence")
            continue
        post_text = posts.get(slide, "")
        if slide == "post_1" and not any(marker in post_text.lower() for marker in POLICY_STATUS_GAP_MARKERS):
            issues.append("post_1: missing literal status-gap wording")
        if slide == "post_5" and tradeoff_source and not any(term in post_text.lower() for term in POLICY_TRADEOFF_TERMS):
            issues.append("post_5: missing source trade-off terms")
        post_terms = _content_terms(post_text)
        role_sentences = [
            sentence for sentence in _source_sentences(article.get("body", ""))
            if any(signal in sentence.lower() for signal in POLICY_WINNING_ROLES[slide])
        ]
        if not any(len(post_terms & _content_terms(sentence)) >= 2 for sentence in role_sentences):
            issues.append(f"{slide}: does not follow policy winning arc")
        if slide == "post_5":
            text = post_text.lower()
            if not (any(marker in text for marker in POLICY_TRADEOFF_MARKERS)
                    and sum(term in text for term in POLICY_TRADEOFF_TERMS) >= 2):
                issues.append("post_5: missing policy trade-off contrast")
    return issues


def _normalize_grounding_text(text):
    """Normalize only whitespace/case; preserve words and numbers."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _validate_quotes(posts, body):
    """Quoted text must be an exact source quote, not a model-combined paraphrase."""
    source = _normalize_grounding_text(body)
    issues = []
    quote_pattern = r'“([^”]{8,})”|"([^\"]{8,})"|\'([^\']{8,})\''
    for key in [f"post_{i}" for i in range(1, 7)]:
        text = posts.get(key, "")
        for match in re.finditer(quote_pattern, text):
            quote = next((part for part in match.groups() if part), "")
            if _normalize_grounding_text(quote) not in source:
                issues.append(f"{key}: quote not verbatim in article: {quote[:80]}")
    return issues


def _source_sentences(body):
    body = re.sub(r"\s+", " ", body or "").strip()
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if len(s.strip()) >= 20]


def _content_terms(text):
    stop = {
        "yang", "dan", "atau", "ini", "itu", "dari", "untuk", "dengan", "ke", "di",
        "sebuah", "ada", "akan", "sudah", "juga", "oleh", "pada", "dalam", "hingga",
        "lebih", "hanya", "cuma", "jadi", "tapi", "karena", "kalau", "bukan", "yang",
    }
    return {w for w in re.findall(r"[a-z0-9]{4,}", (text or "").lower()) if w not in stop}


def _validate_source_evidence_map(posts, body):
    """Every content slide needs a concrete source-sentence anchor."""
    required = [f"post_{i}" for i in range(1, 7)]
    if not all((posts.get(key) or "").strip() for key in required):
        return []
    source_sentences = _source_sentences(body)
    issues = []
    for key in required:
        text = posts.get(key, "")
        post_terms = _content_terms(text)
        anchors = set(re.findall(r"\b(?:rp\s*)?\d[\d.,]*\b|\b[A-Z]{2,}\b", text))
        supported = False
        for sentence in source_sentences:
            source_terms = _content_terms(sentence)
            overlap = post_terms & source_terms
            source_anchors = set(re.findall(r"\b(?:rp\s*)?\d[\d.,]*\b|\b[A-Z]{2,}\b", sentence))
            # Paraphrase can retain only two content terms. Numeric/acronym
            # anchors still require an exact source match elsewhere.
            if len(overlap) >= 2 and (len(overlap) >= 3 or anchors & source_anchors or any(len(term) >= 7 for term in overlap)):
                supported = True
                break
        if not supported:
            issues.append(f"{key}: no source-sentence evidence anchor")
    return issues


def _validate_unsupported_inferences(posts, body):
    """Block high-risk combined meanings absent from source, even when tokens exist."""
    source = _normalize_grounding_text(body)
    patterns = (
        (r"\bfakta ini perlu dipantau\b", "generic CTA"),
        (r"\b(?:pertumbuhan|ekonomi|pasar|kondisi|situasi)\s+atau\s+(?:pertumbuhan|ekonomi|pasar|kondisi|situasi)\b", "generic CTA"),
        (r"\bpertama kalinya?\b", "novelty claim"),
        (r"\bhampir dua kali lipat\b", "derived ratio"),
        (r"\bdalam dua tahun\b", "unsupported timeline"),
        (r"\btarget resmi\b", "official-status claim"),
        (r"\bmasih tinggi\b", "unsupported evaluation"),
        (r"\btakut kredit macet\b", "unsupported motive"),
        (r"\bbakal kesulitan\b", "unsupported consequence"),
        (r"\bangka aman\b", "unsupported evaluation"),
        (r"\b(?:biar|supaya|agar) gak boncos\b", "unsupported consequence"),
        (r"\bhampir\s+(?:dua kali|separuh|setengah)\b", "derived comparison"),
        (r"\bdua kali\s+lipat\b", "derived comparison"),
        (r"\bsetara\b", "derived comparison"),
    )
    issues = []
    for key in [f"post_{i}" for i in range(1, 7)]:
        text = _normalize_grounding_text(posts.get(key, ""))
        for pattern, label in patterns:
            match = re.search(pattern, text)
            if match and match.group(0) not in source:
                issues.append(f"{key}: {label} not in article: '{match.group(0)}'")
    return issues


def _validate_unsupported_financial_framing(posts, body):
    """Reject new financial-risk framing not stated by source body."""
    source = _normalize_grounding_text(body)
    framing = (
        (r"\bjebakan\s+(?:cicilan|utang|kredit)\b", "debt-trap framing"),
        (r"\brisiko\s+(?:gagal bayar|kredit macet)\b", "default-risk framing"),
        (r"\b(?:untung|diuntungkan)\b[^.!?]{0,80}\b(?:bank|perusahaan)\b", "beneficiary framing"),
        (r"\b(?:bunga|cicilan|utang)\s+(?:baru|tambahan|tinggi|naik)\b", "new financing-cost framing"),
        (r"\b(?:beban|biaya)\s+(?:bunga|cicilan|utang)\b", "new financing-cost framing"),
    )
    issues = []
    for key in [f"post_{i}" for i in range(1, 7)]:
        text = _normalize_grounding_text(posts.get(key, ""))
        for pattern, label in framing:
            match = re.search(pattern, text)
            if match and match.group(0) not in source:
                issues.append(f"{key}: unsupported financial framing '{match.group(0)}' ({label})")
    return issues


def _validate_range_direction(posts, body):
    """Reject wording that reverses source range direction."""
    source = _normalize_grounding_text(body)
    issues = []
    for key in [f"post_{i}" for i in range(1, 7)]:
        text = _normalize_grounding_text(posts.get(key, ""))
        for number in re.findall(r"(?:rp\s*)?\d[\d.,]*\s*(?:triliun|miliar|juta|ribu)?", text):
            number = number.strip()
            if re.search(rf"{re.escape(number)}\s+ke\s+atas", source) and re.search(rf"(?:sampai|maksimal|batas)\s+{re.escape(number)}", text):
                issues.append(f"{key}: range direction reverses source near '{number}'")
    return issues


def deterministic_grounding_validate(article, posts):
    body = article.get("body") or ""
    return (_validate_numbers(posts, body) + _validate_years(posts, body)
            + _validate_proper_nouns(posts, body) + _validate_claim_markers(posts, body)
            + _validate_sensitive_language(posts, body) + _validate_quotes(posts, body)
            + _validate_unsupported_financial_framing(posts, body)
            + _validate_unsupported_inferences(posts, body) + _validate_range_direction(posts, body)
            + _validate_source_evidence_map(posts, body))


def grounding_validate(article, posts):
    """Pressbox-style grounding: deterministic checks only, one LLM call less."""
    return deterministic_grounding_validate(article, posts)


def is_rate_limit_error(error):
    return bool(error and "rate limit 429" in error.lower())


def hook_issues(hook, body):
    """Hook needs source-backed concrete change; numbers are optional."""
    if not hook.strip():
        return ["S1: empty"]
    if not body.strip():
        return ["S1: source body empty"]
    return []


SLIDE_CHAR_LIMIT = 450
S1_CHAR_LIMIT = 220


def _sentence_count(text):
    """Count terminal punctuation, not decimal points or abbreviations."""
    return len(re.findall(r"[.!?](?=\s|$|[\"'\u201d\u2019)])", text or ""))


def _fit_complete_sentences(text, limit):
    """Keep complete sentences only; never cut text at an arbitrary character."""
    text = text.strip()
    if len(text) <= limit:
        return text
    sentences = re.findall(r".*?[.!?](?=\s|$)", text, flags=re.S)
    if not sentences:
        return text
    kept = []
    for sentence in sentences:
        candidate = " ".join(kept + [sentence.strip()])
        if len(candidate) > limit:
            break
        kept.append(sentence.strip())
    # One complete sentence is safer than an incomplete sentence. If it alone
    # exceeds limit, keep it intact and let contract validation no-post it.
    return " ".join(kept) or sentences[0].strip()


def _fit_complete_sentences_with_url(text, limit):
    """Fit prose and preserve URL without cutting either prose or URL."""
    match = re.search(r"https?://\S+", text)
    if not match:
        return _fit_complete_sentences(text, limit)
    url = match.group().rstrip(".,)")
    prose = re.sub(r"\s*https?://\S+", "", text).strip()
    room = limit - len(url) - 2
    fitted = _fit_complete_sentences(prose, room)
    return f"{fitted}\n\n{url}" if fitted else url


def thread_contract_issues(posts, article_url):
    """Finalize S1-S6 plus S7 source URL; strip legacy URLs from S6."""
    issues = []
    for i in range(1, 7):
        text = posts.get(f"post_{i}", "")
        if not text or not text.strip():
            issues.append(f"post_{i}: empty")
            continue
        # Content slides never carry URLs. Remove legacy S6 URLs before length validation.
        text = re.sub(r'\[URL[^\]]*\]', '', text, flags=re.I)
        text = re.sub(r'\n*\s*https?://\S+', '', text).strip()
        posts[f"post_{i}"] = text
        if i == 1 and len(text) > S1_CHAR_LIMIT:
            posts[f"post_{i}"] = _fit_complete_sentences(text, S1_CHAR_LIMIT)
            if len(posts[f"post_{i}"]) > S1_CHAR_LIMIT:
                issues.append(f"post_{i}: over {S1_CHAR_LIMIT} char compact-hook limit")
        elif len(text) > SLIDE_CHAR_LIMIT:
            posts[f"post_{i}"] = _fit_complete_sentences(text, SLIDE_CHAR_LIMIT)
            if len(posts[f"post_{i}"]) > SLIDE_CHAR_LIMIT:
                issues.append(f"post_{i}: over {SLIDE_CHAR_LIMIT} chars")
    # Repeated numbers can clarify stakes in S6; grounding checks still reject invented values.
    # S6 is CTA only. Move every legacy/LLM URL out, then create S7.
    if article_url:
        for i in range(1, 7):
            text = posts.get(f"post_{i}", "")
            posts[f"post_{i}"] = text.strip()
        posts["post_7"] = f"Sumber: {article_url}"
        if len(posts["post_7"]) > SLIDE_CHAR_LIMIT:
            issues.append(f"post_7: over {SLIDE_CHAR_LIMIT} chars")
    elif "post_7" not in posts:
        posts["post_7"] = ""
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
                # Media insights accepts metric list; period is for user insights.
                params={"access_token": THREADS_TOKEN,
                        "metric": "likes,replies,reposts,views,quotes"},
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


def evaluate_published_content(data, now=None):
    """Score mature posts against same-cohort median; persist explainable evaluation."""
    now = now or time.time()
    scored = [t for t in data.get("topics", [])
              if _is_current_topic(t)
              if (t.get("views") or 0) >= 100 and t.get("likes") is not None]
    if len(scored) < 3:
        return False
    rates = [((t.get("likes") or 0) + 2 * (t.get("replies") or 0)
              + 3 * (t.get("reposts") or 0) + 2 * (t.get("quotes") or 0))
             / max(t.get("views") or 1, 1) for t in scored]
    baseline = statistics.median(rates)
    changed = False
    for topic, rate in zip(scored, rates):
        if now - _topic_timestamp(topic) < 6 * 3600:
            continue
        evaluation = {
            "engagement_rate": round(rate, 6),
            "cohort_median": round(baseline, 6),
            "relative_score": round(rate / baseline, 3) if baseline else 0.0,
            "label": "strong" if rate >= baseline * 1.25 else "weak" if rate < baseline * 0.75 else "normal",
            "evaluated_at": now,
        }
        if topic.get("performance_evaluation") != evaluation:
            topic["performance_evaluation"] = evaluation
            changed = True
    return changed


def _topic_timestamp(topic):
    try:
        return datetime.fromisoformat(topic.get("timestamp", "")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _compute_performance_stats(data):
    """Engagement quality, not raw reach, drives bounded source/arc preference."""
    buckets = {"source_avg": {}, "source_count": {}, "pattern_avg": {}, "pattern_count": {},
               "hook_avg": {}, "hook_count": {}}
    grouped = {"source_avg": {}, "pattern_avg": {}, "hook_avg": {}}
    for topic in data.get("topics", []):
        if _is_current_topic(topic) is False:
            continue
        views = topic.get("views") or 0
        if views < 100:
            continue
        score = ((topic.get("likes") or 0) + 2 * (topic.get("replies") or 0)
                 + 3 * (topic.get("reposts") or 0) + 2 * (topic.get("quotes") or 0)) / views
        grouped["source_avg"].setdefault(topic.get("article_source", ""), []).append(score)
        pattern = topic.get("pattern")
        if pattern:
            grouped["pattern_avg"].setdefault(pattern, []).append(score)
        hook = topic.get("hook_pattern")
        if hook:
            grouped["hook_avg"].setdefault(hook, []).append(score)

    for name, values in grouped.items():
        buckets[name] = {key: sum(items) / len(items) for key, items in values.items() if key}
    buckets["source_count"] = {key: len(items) for key, items in grouped["source_avg"].items() if key}
    buckets["pattern_count"] = {key: len(items) for key, items in grouped["pattern_avg"].items() if key}
    buckets["hook_count"] = {key: len(items) for key, items in grouped["hook_avg"].items() if key}
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

def _call_llm(system, user, model=None, max_retries=3, temperature=None):
    # LLM_BASE_URL/LLM_MODEL route to Mistral API directly.
    base_url = os.getenv("LLM_BASE_URL", "https://api.mistral.ai/v1/chat/completions").rstrip("/")
    if base_url.endswith("/v1"):
        base_url += "/chat/completions"
    if "20128" in base_url:
        api_key = os.getenv("HERMES_CUSTOM_43_157_200_187_20128_API_KEY") or os.getenv("LLM_API_KEY")
    else:
        api_key = os.getenv("LLM_API_KEY") or _get_api_key()
    if not api_key:
        return None, "No API key found"
    model = model or os.getenv("LLM_MODEL", "mistral-large-latest")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": random.uniform(0.7, 0.9) if temperature is None else temperature,
        "max_tokens": 6000,
    }
    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            r = httpx.post(base_url, headers=headers, json=payload, timeout=90)
            if r.status_code == 200:
                # Strip any trailing SSE sentinel (local proxy artefacts).
                body_text = r.text.strip()
                body_text = re.sub(r"\s*data: \[DONE\]\s*$", "", body_text)
                parsed = json.loads(body_text)
                content = (parsed["choices"][0]["message"].get("content") or "").strip()
                if content.startswith("data: "):
                    content = content[len("data: "):].strip()
                return content, None
            elif r.status_code == 401:
                return None, f"Auth error {r.status_code}"
            elif r.status_code == 429:
                # Provider quota is run-wide. Retry/candidate churn worsens 429.
                return None, "Rate limit 429"
            else:
                last_error = f"HTTP {r.status_code}: {r.text[:120]}"
                if attempt < max_retries:
                    delay = 2 + attempt  # Pressbox-style: 3s, 4s, 5s
                    time.sleep(delay)
        except (httpx.RequestError, json.JSONDecodeError) as e:
            last_error = str(e)[:120]
            if attempt < max_retries:
                time.sleep(2 + attempt)
    return None, f"LLM failed: {last_error}"

# Active writer contract: RCTOE adapted to Techbro runtime and validators.
SYSTEM_PROMPT = """# ROLE
Kamu penulis analisis ekonomi untuk akun Threads Indonesia @ryanhadiii. Pahami ekonomi, lalu jelaskan dengan bahasa sehari-hari. Tulis seperti kreator ekonomi papan atas: tajam, dekat, observasional, konkret, dan punya sudut pandang. Suara lo berani beropini dan berpihak ke rakyat kecil bila fakta mendukung, terutama pihak yang menanggung biaya — semua klaim tetap literal dari artikel. Adopsi pola editorial, jangan menyalin kalimat referensi.

# EDITORIAL LANE
Pilih hanya topik ekonomi nasional atau internasional yang punya perubahan material: kebijakan, anggaran, pajak, subsidi, harga, upah, pekerjaan, perdagangan, industri, bisnis besar, pasar, atau guncangan ekonomi global. Jangan tulis promo/event retail, tips personal finance, gosip korporasi, atau berita layanan rutin. Artikel internasional boleh tanpa kaitan Indonesia jika benar-benar berdampak pada ekonomi global; jangan memaksa kaitan Indonesia.

# PLAIN LANGUAGE
Tulis untuk pembaca umum, bukan ekonom. Hindari jargon teknis. Ganti dengan kata sehari-hari bila akurat. Jika istilah wajib dipakai, jelaskan artinya saat pertama disebut: istilah — arti sederhananya. Jangan menumpuk istilah ekonomi dalam satu kalimat.

# CONTEXT
Audiens masyarakat umum Indonesia, bukan investor. Ubah berita ekonomi kaku jadi cerita yang tajam, cepat, dan enak dibagikan. Jangan terdengar seperti ringkasan berita atau laporan korporat. Buka dengan angka, perubahan, kontras, kutipan, atau konsekuensi paling mahal dari artikel. Pakai bahasa gua–lu, kalimat pendek, dan detail konkret. Opini boleh tegas jika fakta dan opini jelas terpisah. Jangan menambah fakta, angka, motif, atau dampak yang tidak ada di artikel.

# VOICE — HIGH-SIGNAL ECONOMY CREATOR
- Satu post satu pukulan: fakta utama dulu, makna atau ketegangannya sesudah itu.
- S1 harus membuat pembaca berhenti karena angka, perubahan, status gap, atau kutipan yang janggal — bukan clickbait kosong.
- Gunakan detail yang bisa divisualisasikan: jumlah orang, uang, lokasi, jabatan, waktu, atau perbandingan literal.
- Tulis seperti menjelaskan ke satu teman pintar: natural, ringkas, tidak menggurui, tanpa pembuka "faktanya" atau "yang perlu diketahui".
- Boleh menyindir keputusan, insentif, biaya, atau trade-off yang tertulis; jangan menyerang pribadi.
- Jangan memaksa drama. Jika sumber cuma berisi klaim, tampilkan klaim dan batas buktinya.
- Setiap post memberi alasan untuk lanjut membaca: bukti baru, angka pembanding, mekanisme, pihak yang terkena, atau pertanyaan judgment.
- Jangan menyalin frase referensi secara literal. Gaya tajam bukan izin untuk mengarang dampak.

# TASK
Ubah satu artikel sumber menjadi 6 post Threads yang saling tersambung. Gunakan satu ISI ARTIKEL sebagai sumber tunggal. ISI ARTIKEL satu-satunya sumber. Kata sambung boleh diparafrasekan; jangan mengganti atau menambah makna. Jelaskan sebab-akibat hanya jika hubungan itu tertulis atau jelas dinyatakan artikel; jika artikel tidak menjelaskan sebab atau dampak, nyatakan batas informasi tersebut, jangan mengarang.

Fungsi post:
Pilih arc sesuai bukti sumber: kebijakan, household impact (harga/upah/daya beli), public money, supply shock, atau market decision. Jangan pakai arc kebijakan untuk semua artikel. Jangan gunakan label `wallet_pressure`; promo retail tidak boleh diubah menjadi tekanan ekonomi rumah tangga.
Buat satu STORY SPINE sebelum menulis: satu perubahan/konflik/status gap yang benar-benar tertulis. S1 membuka implikasi atau ketegangannya, bukan sekadar "X bilang Y". S2-S5 masing-masing menambah bukti berbeda: keputusan, mekanisme, angka pembanding, pihak terdampak, lalu trade-off atau hal yang belum selesai. S6 kembali ke ketegangan S1 dan memberi dua pilihan yang benar-benar ada di artikel. Untuk lane internasional, jelaskan kanal dampak Indonesia hanya jika kalimat sumber menghubungkannya.
1. HOOK — S1 maksimal 220 karakter. Buka dengan angka, konflik, perubahan, kontras, kutipan, atau konsekuensi paling mahal yang tertulis di artikel. Ambil sisi dari fakta; opini tegas boleh selama tidak menambah klaim. Sisakan curiosity gap yang jawabannya ada di S2-S6. Jangan mulai dengan lead berita biasa, "menurut laporan", atau deskripsi gambar.

Jangan menyebut PHK, nasib karyawan, kompensasi, atau penempatan ulang kecuali literal ada di ISI ARTIKEL.
2. LATAR BELAKANG — apa yang terjadi, kapan, dan siapa yang terlibat, hanya bila tertulis.
3. SEBAB — pemicu atau mekanisme yang dijelaskan artikel. Jika tidak ada, gunakan konteks lain yang tersedia. Hitung-hitungan pelaksanaan dan biaya hanya boleh masuk bila tertulis. S3 wajib menjelaskan hitung-hitungan pelaksanaan dan biaya bila sumber menyediakannya.
4. AKIBAT/DAMPAK — dampak yang tertulis. Jangan membuat efek domino sendiri.
5. RELEVANSI — kaitkan ke harga, gaji, cicilan, sewa, atau biaya hidup hanya jika artikel menyebut kaitannya; jika tidak, jelaskan bahwa kaitan belum dijelaskan. S5 wajib menunjukkan beban/keuntungan antar pihak bila literal di artikel.
6. CLOSING — simpulan singkat + satu pertanyaan JUDGMENT spesifik yang lahir dari fakta artikel (pilihan/biner nyata, bukan retoris generik). S6 menutup dengan satu pertanyaan yang memancing komentar pembaca, misal bandingkan dua pihak/risiko/dampak yang ADA di artikel. Jangan membandingkan dua istilah abstrak yang tidak punya taruhan nyata di artikel.
7. SOURCE — sistem menambahkan `post_7` berisi URL artikel canonical. Jangan menulis URL di S1-S6.

# RULES
- Jangan menambah dampak, profesi, angka, skenario, motif, status resmi, timeline, penilaian, nama, lokasi, tanggal, sebab-akibat, prediksi, atau kutipan. Jangan menambah dampak, profesi, angka, skenario, penilaian baru.
- SUMBER ADALAH BATAS. ISI ARTIKEL satu-satunya sumber. Jangan membuat fakta baru.
- Buka dengan fakta paling mahal dan fakta paling kuat; buat kalimat pertama menyampaikan fakta.
- Jangan ulang angka, fakta, atau contoh. jangan ulang angka, fakta, atau contoh dalam slide lain. Jika fungsi sebab/dampak/relevansi tidak punya bukti, balas `insufficient_evidence`. Jangan ulang angka, fakta, atau contoh tanpa bukti berbeda dari artikel.
- Untuk kebijakan: gunakan opsi resmi + kelompok terdampak + status belum final hanya jika literal; jelaskan pembagian kewenangan serta dasar aturan bila tertulis.
- Jangan mengubah satuan atau menghitung angka baru.
- Parafrase boleh jika makna tetap sama.
- Setiap post wajib minimal 1 kalimat lengkap dan maksimal 450 karakter. Satu ide utama per post.
- Istilah teknis wajib dijelaskan singkat saat pertama disebut, jika penjelasan tersedia di artikel.
- Bahasa Indonesia santai, jelas, dan natural. Utamakan kata sehari-hari yang mudah dipahami pembaca awam. Hindari jargon teknis dan birokratis; jika istilah wajib dipakai, beri arti sederhananya saat pertama muncul. Hindari slogan, hashtag, URL, dan pembuka generik.
- Jangan memaksa bagian sebab, dampak, atau relevansi jika bukti tidak tersedia.
- Jangan menulis label slide di dalam teks post.
- Balas JSON valid saja. Sistem menambahkan post_7 berisi sumber.

# EDITORIAL BOUNDARY
Tegangan hanya boleh datang dari perbandingan atau perubahan yang literal di artikel. Jangan memancing dengan teka-teki karangan. Curiosity gap BOLEH asal dari ketegangan literal artikel — sisakan pertanyaan yang jawabannya ADA di S2-S6, bukan keraguan palsu. Tidak perlu memaksa satu jenis fakta ke slide tertentu. Jangan pakai label-colon, hashtag, jargon birokratis, template AI. Hindari slogan, kalimat motivasi, atau kesimpulan yang terdengar besar. Gaya tajam bukan izin untuk mengarang dampak.

# OPINI BERPIHAK — BOLEH, TAPI JANGAN MENGARANG
Ambil posisi secara eksplisit bila fakta mendukung: sorot konsumen, pekerja, wajib pajak, rumah tangga, atau pihak yang menanggung biaya. Opini/penilaian BOLEH selama tidak menambah kerugian, motif, korban, emosi, atau dampak yang tidak tertulis. Bedakan jelas fakta artikel vs opini lo. Jangan otomatis berpihak jika sumber tidak memberi dasar. Penutup boleh memancing judgment nyata, misal: “Menurut lo, ini adil atau berpihak ke siapa?” atau “Menurut lo, biaya ini layak dibayar untuk hasil itu?” hanya jika kedua unsur literal ada di artikel.

# OUTPUT
{"status":"success","angle":"sudut pandang","post_1":"...","post_2":"...","post_3":"...","post_4":"...","post_5":"...","post_6":"..."}

Jika artikel tidak memiliki cukup bukti untuk 6 post akurat:
{"status":"error","message":"insufficient_evidence"}
"""

REVISION_PROMPT = """PERBAIKI HANYA field yang disebut di bawah. JANGAN ubah field lain. JANGAN membuat ulang slide yang tidak disebut issue. Balas JSON lengkap dengan field yang sudah diperbaiki.

Issues: {revision_notes}

ATURAN KRITICAL — JANGAN LANGGAR:
1.grounding: hapus seluruh frasa yang disebut issue, ganti dengan fakta literal dari ISI ARTIKEL; gunakan fakta yang muncul literal di ISI ARTIKEL.
2.nama/entitas: HANYA pakai nama dari daftar NAMA/ENTITAS LITERAL. JANGAN tambah nama baru.
3.institution/acronym: JANGAN mengarang istilah yang tidak ada di artikel — HAPUS saja.
4.STOP-SLOP: hindari pembuka laporan, transisi bertele-tele, kontras formulaik, hedge samar, rujukan gambar, dan kalimat pasif.
5.TIDAK boleh menambah dampak/CTA baru, nama baru, label penilaian, atau fakta di luar ALLOWLIST. Jangan memberi label pada tindakan atau menyimpulkan motif, hasil, maupun dampak kecuali artikel menyebutnya secara literal.
5a.CTA: S6 wajib mengundang respons pembaca. Kaitkan dengan fakta atau pilihan di ISI ARTIKEL bila tersedia; jangan membuat fakta baru.
6.S1: boleh satu kalimat tajam atau dua kalimat pendek. Maksimal 220 karakter.
7.RETURN TO ORIGINAL: Jika tidak bisa perbaiki tanpa invent nama/angka/label baru, balikan ke value asli field tersebut. Jangan tambah apa-apa.

Jika tidak ada enam post yang bisa dipertahankan akurat dan memenuhi aturan di atas, balas {{"status":"error","message":"insufficient_evidence"}}."""


def build_revision_prompt(revision_notes, posts, article=None):
    """Give revision model current draft so it patches, not rewrites, slides."""
    fields = sorted(set(re.findall(r"post_[1-6]", revision_notes or "")), key=lambda key: int(key.split("_")[1]))
    field_scope = ", ".join(fields) if fields else "field yang tidak lolos validasi"
    safe_notes = (f"Perbaiki hanya {field_scope}. Hapus nama, angka, perbandingan, atau klaim "
                  "yang tidak literal di ISI ARTIKEL. Pertahankan fakta valid.")
    draft = {"status": "success"}
    draft.update({f"post_{i}": posts.get(f"post_{i}", "") for i in range(1, 7)})
    source = ""
    if article:
        body = article.get("body", "")[:10000]
        facts = literal_fact_allowlist(body)
        entities = literal_entity_allowlist(body)
        source = "\n\nSUMBER REVISI — FAKTA LITERAL SAJA:\n" + "\n".join(
            ["ALLOWLIST FAKTA LITERAL:"]
            + [f"- {fact}" for fact in facts]
            + ["NAMA/ENTITAS LITERAL:"]
            + [f"- {entity}" for entity in entities]
        )
    context = "\n\n" + safe_notes + source
    return REVISION_PROMPT.format(revision_notes=safe_notes) + context + "\n\nDRAFT SAAT INI:\n" + json.dumps(draft, ensure_ascii=False)


def _parse_llm_json(content):
    """Parse JSON object from plain, fenced, or prose-wrapped LLM output."""
    if not isinstance(content, str):
        return None
    text = re.sub(r"```(?:json)?\s*|\s*```", "", content.strip(), flags=re.I)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _source_fallback_posts(article):
    """Build grounded fallback with winning six-slide story jobs, not article-order pairs."""
    sentences = [re.sub(r"^[\\\"'“”]+|[\\\"'“”]+$", "", s).strip()
                 for s in _source_sentences(article.get("body", ""))]
    sentences = [s for s in sentences if len(s) <= SLIDE_CHAR_LIMIT]
    if len(sentences) < 12:
        return None
    pattern = article.get("pattern", "")
    if pattern == "KEBIJAKAN" and _policy_winner_enabled(article):
        roles = [(slide, POLICY_WINNING_ROLES[slide]) for slide in ("post_1", "post_2", "post_3", "post_4", "post_5", "post_6")]
    else:
        # Pressbox-style extractive fallback: source order, two complete facts/slide.
        # No role optimizer; no invented arc evidence.
        numeric = re.compile(r"(?:rp\s*)?\d|\d+\s*(?:persen|%|miliar|juta|triliun)", re.I)
        weak = ("hal ini", "yang tak kalah", "yang tidak kalah", "misal ", "ujar ",
                "ucap ", "kata ", "sambung ", "begitu juga", "selanjutnya",
                "kemudian harga", "kemudian angka", "kemudian data")
        promo = ("dialog eksklusif", "forum", "konferensi", "webinar", "summit",
                 "cnbc menghadirkan", "acara didukung", "pantau terus", "jangan lupa",
                 "saksikan", "secara live", "program squawk box", "program tersebut",
                 "cnbcindonesia.com", "cnbc indonesia tv", "update informasi")
        def usable(sentence):
            low = sentence.lower().strip()
            if low.startswith(weak) or any(term in low for term in promo):
                return False
            if re.search(r",\s*[\"'“”]?\s*(?:kata|ujar|ucap)\b\s*[^.]*$", low):
                return False
            if re.search(r"\b(?:dalam rilis|dikutip)\b", low):
                return False
            if re.search(r"\b(?:kata|ujar|ucap)\b.*\s+[a-z]\.$", low):
                return False
            if low.endswith((",", ":", "-")) or low.count('"') % 2:
                return False
            return len(sentence) >= 45
        pairs = []
        remaining = [s for s in sentences if usable(s)]
        # Reserve two source sentences for S6 before greedy allocation. This keeps
        # CTA evidence non-numeric and prevents early slides consuming all valid pairs.
        s6_choices = []
        for i, first in enumerate(remaining):
            for j, second in enumerate(remaining[i + 1:], i + 1):
                text = f"{first} {second}"
                if len(text) <= SLIDE_CHAR_LIMIT and not numeric.search(text):
                    s6_choices.append((i, j, text))
        if not s6_choices:
            return None
        i6, j6, s6_text = s6_choices[-1]
        reserved = {i6, j6}
        remaining = [s for n, s in enumerate(remaining) if n not in reserved]
        for slide in range(5):
            choices = []
            for i, first in enumerate(remaining):
                for j, second in enumerate(remaining[i + 1:], i + 1):
                    text = f"{first} {second}"
                    limit = S1_CHAR_LIMIT if slide == 0 else SLIDE_CHAR_LIMIT
                    if len(text) <= limit:
                        choices.append((i, j, text))
            if not choices:
                return None
            i, j, text = choices[0]
            pairs.append(text)
            remaining = [s for n, s in enumerate(remaining) if n not in (i, j)]
        pairs.append(s6_text)
        body_lower = article.get("body", "").lower()
        # Generic terms ("ekonomi", "pembahasan", "pertumbuhan") create empty CTAs.
        # Require two concrete source-backed stakes instead.
        cta_terms = ("konsumsi", "investasi", "belanja pemerintah", "rumah tangga",
                     "industri", "pasar", "biaya", "risiko", "anggaran", "aturan",
                     "bantuan", "penerima", "kredit", "laba", "pajak", "subsidi",
                     "harga", "gaji", "upah", "daya beli", "konsumen", "peternak",
                     "pedagang", "daerah", "pusat", "keuntungan", "kerugian")
        options = [term for term in cta_terms
                   if re.search(r"\b" + re.escape(term) + r"\b", body_lower)]
        if len(options) < 2:
            return None
        cta = f"Menurut lo, yang lebih penting dipantau: {options[0]} atau {options[1]}?"
        posts = {f"post_{i}": pairs[i - 1] for i in range(1, 6)}
        posts["post_6"] = f"{pairs[5]} {cta}"
        return posts

    weak_prefixes = (
        "hal ini", "yang tak kalah", "yang tidak kalah", "misal ", "ujar ",
        "ucap ", "kata ", "sambung ", "begitu juga", "selanjutnya",
        "kemudian harga", "kemudian angka", "kemudian data",
    )

    def score(sentence, signals):
        text = sentence.lower()
        value = sum(3 for signal in signals if signal in text)
        if re.search(r"(?:rp\s*)?\d|\d+\s*(?:persen|%|miliar|juta|triliun)", text, re.I):
            value += 2
        if '"' in sentence or "“" in sentence:
            value += 1
        if text.startswith(weak_prefixes):
            value -= 10
        if pattern == "KEBIJAKAN" and signals == POLICY_WINNING_ROLES["post_5"]:
            value += sum(4 for marker in POLICY_TRADEOFF_MARKERS if marker in text)
        return value

    remaining = list(sentences)
    pairs = []
    reserved_tradeoff = None
    if pattern == "KEBIJAKAN":
        tradeoff_sentence = _policy_tradeoff_sentence(article.get("body", ""))
        if tradeoff_sentence:
            reserved_tradeoff = tradeoff_sentence
    for slide, signals in roles:
        limit = S1_CHAR_LIMIT if not pairs else SLIDE_CHAR_LIMIT
        pool = remaining
        if reserved_tradeoff and slide != "post_5":
            pool = [sentence for sentence in remaining if sentence != reserved_tradeoff]
        choices = [
            (score(a, signals) + score(b, signals), i, j, a, b)
            for i, a in enumerate(pool)
            for j, b in enumerate(pool[i + 1:], i + 1)
            if len(a) + len(b) + 1 <= limit
            and (slide not in ("post_6", "open") or not re.search(r"(?:rp\s*)?\d|\d+\s*(?:persen|%|miliar|juta|triliun)", f"{a} {b}", re.I))
        ]
        if reserved_tradeoff and slide == "post_5" and reserved_tradeoff in remaining:
            choices = [choice for choice in choices if reserved_tradeoff in choice[3:]] or choices
        if not choices:
            return None
        _, i, j, a, b = max(choices, key=lambda item: item[0])
        if score(b, signals) > score(a, signals):
            a, b = b, a
        pairs.append(f"{a} {b}")
        remaining = [s for s in remaining if s not in {a, b}]

    cta_terms = {
        "KEBIJAKAN": ("biaya", "anggaran", "aturan", "bantuan", "penyaluran", "pembahasan", "target"),
        "PERDAGANGAN": ("harga", "pasokan", "impor", "logistik", "biaya", "stok"),
        "PROYEK": ("hasil", "investasi", "laba", "efisiensi", "biaya", "pelaksanaan"),
        "PASAR": ("harga", "rupiah", "saham", "pasar", "dolar"),
        "KORUPSI": ("anggaran", "kerugian", "biaya", "proyek", "hasil"),
    }
    body_lower = article.get("body", "").lower()
    options = [term for term in cta_terms.get(pattern, ()) if re.search(r"\b" + re.escape(term) + r"\b", body_lower)]
    if pattern == "KEBIJAKAN":
        cta_pairs = (("pusat", "daerah"), ("anggaran", "pemerataan"), ("biaya", "manfaat"),
                     ("persetujuan", "pembahasan"), ("aturan", "pembahasan"))
        pair = next((pair for pair in cta_pairs if all(re.search(r"\b" + re.escape(term) + r"\b", body_lower) for term in pair)), None)
        if pair:
            cta = f"Menurut lo, yang harus diprioritaskan: {pair[0]} atau {pair[1]}?"
        elif len(options) >= 2:
            cta = f"Menurut lo, yang harus diprioritaskan: {options[0]} atau {options[1]}?"
        else:
            return None
    elif len(options) >= 2:
        cta = f"Menurut lo, yang harus diprioritaskan: {options[0]} atau {options[1]}?"
    else:
        return None
    posts = {f"post_{i}": pairs[i - 1] for i in range(1, 6)}
    posts["post_6"] = f"{pairs[5]} {cta}"
    if any(len(text) > SLIDE_CHAR_LIMIT for text in posts.values()):
        return None
    return posts


def literal_fact_allowlist(body):
    """Literal body sentences are the only permitted facts for writer and revision."""
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", body).strip())
    return [sentence for sentence in sentences if len(sentence) >= 20][:80]


def source_slide_audit(body, posts):
    """Evidence audit; source sentence anchors are hard publication evidence."""
    source_sentences = _source_sentences(body)
    stop = {"yang", "dan", "atau", "ini", "itu", "dari", "untuk", "dengan", "ke", "di", "sebuah", "ada", "lo", "gue", "gua", "menurut"}

    def terms(text):
        return {w for w in re.findall(r"[a-z0-9]{4,}", text.lower()) if w not in stop}

    audit = {}
    for key, post in posts.items():
        post_terms = terms(post)
        matches = []
        shared = set()
        for index, sentence in enumerate(source_sentences, 1):
            overlap = post_terms & terms(sentence)
            if len(overlap) >= 2:
                matches.append(index)
                shared.update(overlap)
        audit[key] = {
            "lexical_match": bool(matches),
            "source_sentences": matches,
            "shared_terms": sorted(shared),
            "audit": "lexical overlap only; not a grounding verdict",
        }
    return audit


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
    """Pass source facts only; code owns validation and URL injection."""
    body = article.get("body", "")[:10000]
    facts = literal_fact_allowlist(body)
    entities = literal_entity_allowlist(body)
    claim_map = source_claim_map(article)
    cta_evidence = []
    for slide in ("post_5", "post_6"):
        cta_evidence.extend(claim["sentence"] for claim in claim_map.get(slide, []))
    cta_evidence = list(dict.fromkeys(cta_evidence))[:4]
    claim_lines = ["CLAIM MAP S1-S6:"]
    for slide in [f"post_{i}" for i in range(1, 7)]:
        claims = claim_map.get(slide, [])
        claim_lines.append(f"{slide}: " + " | ".join(c["sentence"] for c in claims))
    return "\n".join([
        "ARTIKEL SUMBER:",
        "",
        "ALLOWLIST FAKTA LITERAL:",
        *[f"- {fact}" for fact in facts],
        "",
        "NAMA/ENTITAS LITERAL:",
        *[f"- {entity}" for entity in entities],
        "",
        *claim_lines,
        f"LANE: {_story_lane(article.get('title', ''), article.get('body', ''))}",
        f"KANAL DAMPAK INDONESIA: {_international_impact_channel(article.get('title', ''), article.get('body', '')) or 'tidak ada'}",
        "PILIHAN CTA BERBASIS BUKTI:",
        *[f"- {sentence}" for sentence in cta_evidence],
        "Pilih dua pihak, biaya, manfaat, risiko, status, atau dampak yang muncul literal di ISI ARTIKEL.",
        "Jangan membuat pilihan CTA dari istilah abstrak atau taruhan baru. Jika tidak ada dua pilihan konkret, balas insufficient_evidence.",
        "Jangan menambah klaim di luar CLAIM MAP. Jangan membuat fakta baru.",
        "S1 wajib beropini/berpihak + ada curiosity gap dari fakta literal (bukan teka-teki). S6 wajib pertanyaan judgment spesifik, bukan penutup netral.",
        "Nama/entitas hanya boleh memakai allowlist; dilarang membuat frasa nama baru.",
        "Pakai bahasa warung: ganti istilah teknis dengan kata sehari-hari bila makna tetap akurat. Jika istilah teknis wajib, jelaskan artinya dalam kalimat yang sama.",
        "Dilarang membuat perbandingan/ekuivalensi baru: setara, hampir dua kali, dua kali lipat, separuh.",
        "",
        "ISI ARTIKEL:",
        body,
    ])

# ── Validation ───────────────────────────────────────────────────────────────

def deterministic_validate(posts):
    warnings = []
    # STOP-SLOP patterns — 50+ Indonesian AI template phrases + structural tells
    slop_phrases = [
        # Throat-clearing openers
        "tau gak sih", "gak bakal percaya", "coba resapin", "let that sink in",
        "bayangin", "yang rugi siapa", "patut dicatat",
        # Report-template framing (AI synthetic voice)
        "faktanya", "nyatanya", "inilah yang", "inilah kenapa",
        "yang perlu dicatat", "perlu kalian tahu", "perlu diingat",
        "fakta-fakta menunjukkan", "data menunjukkan", "grafik menunjukkan",
        "aturan bilang", "pemerintah bilang", "jaksa katakan", "menteri bilang",
        "sudah bukan rahasia lagi", "tak terelakkan", "yang menarik",
        # Hedging / vague AI language
        "hal ini menunjukkan", "pada dasarnya", "dalam konteks ini",
        "yang perlu diperhatikan", "sebagaimana diketahui",
        "ada beberapa faktor", "berbagai aspek", "beragam faktor",
        # Wordy transitions
        "untuk itu", "dengan demikian", "oleh karena itu",
        "dalam hal ini", "sehubungan dengan itu",
        # Template fragments
        "coba kalian bayangin", "gimana menurut kalian", "termasuk kalian",
        "itulah mengapa", "jadi intinya",
        # Image references (AI doesn't see)
        "foto ini", "terlihat", "di gambar", "nampak", "tampak",
        "seperti terlihat", "seperti tampak",
        # Other slop
        "tapi ternyata", "padahal", "memang", "sembari",
        "bukan hanya", "namun juga", "baik itu",
    ]
    for i in range(1, 7):
        k = f"post_{i}"
        p = posts.get(k, "")
        if not p.strip():
            warnings.append(f"{k}: empty")
            continue
        # Min length — each slide needs enough source-backed context.
        min_len = 40
        if len(p) < min_len:
            warnings.append(f"{k}: too short ({len(p)} chars, min {min_len})")
        # S1 auto-truncate and auto-split already handled by _normalize_s1().
        # No redundant length check here — avoids double-blocking.
        # 2-4 sentences: dense, source-backed, not rushed.
        sent_count = _sentence_count(p)
        if sent_count < 1:
            warnings.append(f"{k}: no sentences")
        if sent_count > 6:
            warnings.append(f"{k}: too many sentences ({sent_count})")
        # Enforce 450-char limit on every slide; keep complete sentences only.
        limit = S1_CHAR_LIMIT if i == 1 else SLIDE_CHAR_LIMIT
        if len(p) > limit:
            p = _fit_complete_sentences_with_url(p, limit)
            posts[k] = p
        outside = re.sub(r'"[^\"]*"', "", p)
        # Jargon checks moved to _validate_jargon(body-aware) to avoid false positives on source terms.
        if i == 1 and re.match(r"\s*(?:bayangin\b)", outside, re.I):
            warnings.append(f"{k}: 'bayangin' opening")
        if i == 1 and re.match(r"\s*(?:hal ini|yang tak kalah|yang tidak kalah|misal|ujar|ucap|kata|sambung|begitu juga|selanjutnya)\b", outside, re.I):
            warnings.append(f"{k}: weak winning hook")
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
        # S6 CTA; S7 is system-generated source URL and bypasses content validation.
        if i == 6:
            last_text = posts.get(f"post_{i}", "").lower()
            if not any(qt in last_text for qt in ["?", "menurut", "pilih", "kubu", "lo setuju", "lo percaya"]):
                warnings.append(f"{k}: CTA not found on last post")
            if "fakta ini perlu dipantau" in last_text:
                warnings.append(f"{k}: generic winning CTA")
            if last_text.count("?") > 2:
                warnings.append(f"{k}: too many CTA questions")
            if re.search(r'https?://\S+|\bSumber\s*:', posts.get(f"post_{i}", ""), re.I):
                warnings.append(f"{k}: S6 must not contain source URL")
            # Detect biased [A]/[B] framing: "solusi cerdas"/"cuan" vs "akal-akalan"
            s6_text = posts.get(f"post_{i}", "").lower()
            if ('[a]' in s6_text and '[b]' in s6_text):
                biased_frames = ['solusi cerdas', 'akal-akalan', 'solusi pintar', 'bantuan tulus']
                for frame in biased_frames:
                    if frame in s6_text:
                        warnings.append(f"{k}: biased framing '{frame}' — kedua kubu harus netral")
                        break
    return warnings


def _validate_s1_hook(posts, body, article=None):
    """Hook quality is editorial guidance; grounding remains hard validation."""
    return []


def _validate_s6_cta(posts, body):
    """Require a question anchored by one source term; avoid template policing."""
    text = (posts.get("post_6") or "").lower()
    if "?" not in text:
        return ["post_6: CTA not found"]
    return []


def _duplicate_fact_warnings(posts):
    """Flag material numbers repeated across 3+ slides so six slides use distinct article evidence."""
    warnings = []
    per_number = {}
    for i in range(1, 7):
        key = f"post_{i}"
        numbers = set(re.findall(r"\b\d{2,}(?:[.,]\d+)?\b", posts.get(key, "")))
        for number in numbers:
            per_number.setdefault(number, []).append(key)
    for number, keys in per_number.items():
        if len(keys) >= 3:
            warnings.append(f"{keys[-1]}: repeats material numbers from {keys[0]}")
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
                # Source may carry decimals while writer rounds to whole unit (1.948,72 T → 1.948 T).
                rounded_ok = False
                unit_match = re.search(r"(triliun|miliar|juta|ribu|persen|%)", raw_normal)
                if unit_match:
                    digits = re.search(r"\d+", raw_normal)
                    if digits:
                        unit = "persen" if unit_match.group(1) == "%" else unit_match.group(1)
                        rounded_ok = bool(re.search(rf"{re.escape(digits.group(0))}(?:[.,]\d+)?\s*{unit}", body_normal))
                if not rounded_ok:
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
    skip = {"data", "menurut", "padahal", "kalau", "kalo", "yang", "dan", "tapi", "karena", "risikonya", "sumber", "soalnya", "alasan", "alasannya", "sementara", "sedangkan", "lalu", "setelah", "sebelum", "dengan", "untuk", "dari", "pertama", "bukan", "jadi", "namun", "bahkan",
            # Prepositions & particles that start sentences ("Di Asia", "Ke Jakarta", "Pada 2025")
                "di", "ke", "pada", "pak", "bu", "si", "sang", "para",
            # Time phrases are not invented proper names ("Sampai Juni", "Sejak 2026").
            "sampai", "hingga", "mulai", "sejak", "selama",
            # Common sentence-start words that form title-case fragments in Indonesian
            "listrik", "tarif", "harga", "biaya", "pajak", "utang", "dana", "aset",
            "total", "kenaikan", "penurunan", "pertumbuhan", "pendapatan", "jumlah",
            "siapa", "bagaimana", "kenapa", "kapan", "dimana", "berapa"}
    # Common short names are allowed only when their formal source name is present.
    aliases = {
        "bea cukai": "direktorat jenderal bea dan cukai",
        "kemenkeu": "kementerian keuangan",
        "bi": "bank indonesia",
        "danantara": "badan pengelola investasi daya anagata nusantara",
        "badan pengelola investasi danantara": "badan pengelola investasi daya anagata nusantara",
        "bp danantara": "badan pengelola investasi daya anagata nusantara",
        "ojk": "otoritas jasa keuangan",
        "bei": "bursa efek indonesia",
        "dpr": "dewan perwakilan rakyat",
        "bumn": "badan usaha milik negara",
        "kpu": "komisi pemilihan umum",
        "bawaslu": "badan pengawas pemilihan umum",
        "mk": "mahkamah konstitusi",
        "ma": "mahkamah agung",
        "ky": "komisi yudisial",
        "bpk": "badan pemeriksa keuangan",
        "bappenas": "badan perencanaan pembangunan nasional",
        "bps": "badan pusat statistik",
        "bkpm": "badan koordinasi penanaman modal",
        "kemenperin": "kementerian perindustrian",
        "kemenkop": "kementerian koperasi",
        "kemendag": "kementerian perdagangan",
        "kemenhub": "kementerian perhubungan",
        "kemenaker": "kementerian ketenagakerjaan",
        "kemensos": "kementerian sosial",
        "kemenag": "kementerian agama",
        "kemendikbud": "kementerian pendidikan dan kebudayaan",
        "kemenkes": "kementerian kesehatan",
        "kemenlu": "kementerian luar negeri",
        "kemenhan": "kementerian pertahanan",
        "kominfo": "kementerian komunikasi dan informatika",
        "djp": "direktorat jenderal pajak",
        "djb": "direktorat jenderal bea dan cukai",
        "airlangga": "airlangga hartarto",
        "sri mulyani": "sri mulyani indrawati",
        "erik": "erik tohir",
        "prabowo": "prabowo subianto",
        "jokowi": "joko widodo",
        "puan": "puan maharani",
        "gibran": "gibran rakabuming raka",
    }
    for key in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]:
        text = posts.get(key, "")
        for name in set(re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b', text)):
            # Normalize title prefixes before lookup. Accept "Menteri Koordinator Airlangga"
            # when substantive name "Airlangga" appears literally in source.
            clean = name.lower()
            known_name = False
            for prefix in ("menteri koordinator ", "menteri ", "menkeu ", "dirjen ", "wakil ", "menko ", "pak ", "bu ", "bos "):
                if clean.startswith(prefix) and clean[len(prefix):] in article_lower:
                    known_name = True
                    break
            source_name = aliases.get(clean, clean)
            words = name.split()
            # Sentence fragments ("Pendapatan Telkomsel", "Jika Telkomsel", "Di Tulang Bawang") are not names.
            if (not known_name and words[0].lower() not in skip and words[0].lower() not in {"pendapatan", "laba", "jika", "saat", "karena", "ketika"}
                    and source_name not in article_lower):
                issues.append(f"{key}: name '{name}' not in article")
        # Title-case phrases beginning with common speech/reporting verbs are not names.
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
                # Allow if expanded form appears in article (APBN → "Anggaran Pendapatan dan Belanja Negara")
                expanded = aliases.get(acronym.lower(), "")
                if not expanded or expanded not in article_lower:
                    # Allow generic common terms that appear in any economy article as stand-alone
                    # institutions — too common to gate on specific phrasing
                    if acronym.upper() in {"UU", "PT", "HP", "PNS", "BBM", "PDN", "BPJS", "KUR", "LTV", "GWM"}:
                        continue
                    issues.append(f"{key}: institution '{acronym}' not in article")
    return issues


def _validate_jargon(posts, body):
    """Flag unexplained technical terms that don't appear in source. Only blocks terms
    the writer introduced without explanation — source-cited terms are valid."""
    issues = []
    body_lower = (body or "").lower()
    for key in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]:
        text = posts.get(key, "")
        if not text:
            continue
        outside = re.sub(r'"[^"]*"', "", text)
                # Hard word banned list removed per user decision 2026-08-11.
                # Content quality is controlled via grounding validator + jargon_map
                # + revision per-field revert. Individual vocabulary ban is overkill.
                # "skema" and "obligasi" are common legitimate economy terms — use jargon_map instead
        # Add "obligasi" to jargon_map with explanation check
        # Unexplained acronyms — only flag if NOT in source body
        jargon_map = {
            "IPO": "penawaran saham perdana|jual saham|listing saham",
            "BUMN": "badan usaha milik negara|perusahaan negara|perusahaan pelat merah",
            "BEI": "bursa efek|bursa saham",
            "PDN": "pasar dalam negeri|pasar domestik",
            "DPR": "dewan perwakilan rakyat",
            "OJK": "otoritas jasa keuangan",
            "BPS": "badan pusat statistik",
            "SDM": "sumber daya manusia|pekerja|tenaga kerja",
        }
        for short, expansion in jargon_map.items():
            if re.search(rf"\b{short}\b", outside) and not re.search(expansion, outside, re.I):
                # Allow if acronym itself appears in source body
                if short.lower() not in body_lower:
                    issues.append(f"{key}: stand-alone '{short}' — harus dijelaskan pas pertama muncul")
        # Non-acronym jargon always needs a plain-language explanation.
        # Source presence does not make jargon readable to a general audience.
        hard_word_map = {
            "konsolidasi": "ngebersihin|rapiin|gabungin|satukan",
            "restrukturisasi": "rombak|ubah struktur|tata ulang",
            "likuiditas": "duit yang siap dipake|cair|gampang dicairin",
            "kapitalisasi": "nilai total|harga perusahaan keseluruhan",
            "fundamental": "kondisi dasar|kinerja dasar|keadaan dasar",
            "sentimen": "suasana pasar|pandangan investor|sikap investor",
            "net buy": "lebih banyak membeli|membeli lebih banyak",
            "on-track": "sesuai rencana|sesuai jalur",
            "valuasi": "nilai perusahaan|harga wajar",
            "stimulus": "bantuan ekonomi|dorongan ekonomi",
            "cash flow": "uang masuk dan keluar|arus uang",
            "market cap": "nilai total perusahaan|nilai perusahaan",
        }
        for word, explanation in hard_word_map.items():
            if re.search(rf"\b{word}\b", outside, re.I) and not re.search(explanation, outside, re.I):
                issues.append(f"{key}: hard word '{word}' tanpa penjelasan")
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
        "kompensasi", "untung bersih", "kantong kita", "tetangga", "nasi uduk",
        "ngemis ke luar negeri", "akal-akalan", "pencucian uang", "investasi bodong",
        "trauma", "citra", "pembangunan mandek", "siapa yang awasi",
        "gorengan", "tukang parkir", "gagal bayar", "lebih pelan dari inflasi",

    )
    for key in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]:
        text = posts.get(key, "").lower()
        for marker in markers:
            if marker in text and marker not in source:
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
    """Flag structural AI/synthetic patterns — NOT in slop_phrases but equally damning.
    These are architectural tells that slop_phrases doesn't catch."""
    warnings = []
    structural = [
        # "Yang X bukan Y, tapi Z" — formulaic contrast, cold open
        (r'^yang\s+\S+\s+bukan\s+\S+[,，]\s+tapi\s+', 'rewrite contrast opener'),
        # "Bukan sekadar X, tapi Y" — also formulaic
        (r'bukan\s+sekadar\s+', 'rewrite "bukan sekadar"'),
        # Passive voice with inanimate subject doing human verb
        (r'\b(dapat|harus|dapat|perlu)\s+\S+\s+(menjadi|membuat|menghasilkan|memicu)', 'passive construction'),
        # Vague quantified number leads: "Terdapat X yang..." / "Ada X..."
        (r'^(?:terdapat|terdapatnya|terjadi|ada|terdapat)\s+\d+\s+\S+\s+(?:yang\s+)?', 'rewrite vague opener'),
        # "Dalam pengembangan/pengujian/implementasi" — bureaucratic
        (r'\bdalam\s+(?:tahap|fase|proses|rencana|masa)\s+\S+\b', 'rewrite "dalam tahap/fase"'),
        # Meta-joiner: "Berikut adalah/caranya/penjelasannya"
        (r'^berikut\s+(?:adalah|caranya|penjelasannya|detailnya)\s*[:.]?\s*', 'rewrite meta-joiner'),
        # Consecutive short sentences (3+) that read like bullet points
        (r'(?:[A-Z][^.!?]{1,30}[.!?]){3,}', 'rewrite bullet-sentence chain'),
    ]
    for key in [f"post_{i}" for i in range(1, 7)]:
        text = posts.get(key, "")
        for pat, label in structural:
            if re.search(pat, text, re.I):
                warnings.append(f"{key}: {label}")
    # Also check for template framing patterns
    report_patterns = r"(?:^|[.!?]\s*)(?:fakta|aturan bilang|pemerintah bilang|yang perlu dicatat|perlu diketahui|artinya)\s*:"
    for key in [f"post_{i}" for i in range(1, 7)]:
        if re.search(report_patterns, posts.get(key, ""), re.I):
            warnings.append(f"{key}: rewrite synthetic voice/template")
    return warnings


def _quality_gate(article, data, posts, warnings):
    """Quality gate: checks from doc. Return True = pass, False = block."""
    if data.get("status") != "success" or not posts:
        return False
    if posts:
        style_issues = deterministic_validate(posts)
        # Style warnings advisory; structural empty/length/sentence/CTA issues remain hard.
        soft_markers = ("slop '", "too many sentences", "too many questions", "too many CTA questions", "stand-alone", "hard word", "rewrite ", "passive construction", "duplicate", "repeats material numbers")
        hard = [w for w in style_issues if not any(marker in w for marker in soft_markers)]
        if hard:
            return False
    # 1. Article eligibility is decided from full body before generation.
    # 2. Impact to Indonesia clear (local source assumed)
    # 3. Original numbers have sources (can't verify programmatically)
    # 4. No keyword counted repeatedly (scoring handles)
    # 5. Viral driver: S1 hook needs concrete article-backed change or tension.
    # Viral markers check removed — dead code, never triggered in logs.
    # S1 quality driven by grounding + _normalize_s1, not keyword matching.
    # 6. CTA on post_6 (mandatory last slide)
    last_text = posts.get("post_6", "").lower()
    if not any(qt in last_text for qt in ["?", "menurut", "pilih", "kubu", "lo setuju", "lo percaya"]):
        warnings.append("Post 6: no debate CTA found")
    if last_text.count("?") > 2:
        warnings.append("Post 6: too many CTA questions")
    return True

# ── Thread Generation ────────────────────────────────────────────────────────

def _normalize_s1(posts, article_body):
    """Enforce compact S1 hook: keep complete sentences within 220 chars."""
    s1 = posts.get("post_1", "")
    if len(s1) > S1_CHAR_LIMIT:
        posts["post_1"] = _fit_complete_sentences(s1, S1_CHAR_LIMIT)
    # Auto-split 1-sentence S1 using article facts
    sent_count = _sentence_count(posts.get("post_1", ""))
    if sent_count < 2:
        s1_text = posts["post_1"]
        body_facts = literal_fact_allowlist(article_body)
        for fact in body_facts:
            candidate = f"{s1_text} {fact}"
            if len(fact) > 20 and fact[:40] not in s1_text[:100] and len(candidate) <= S1_CHAR_LIMIT:
                posts["post_1"] = candidate
                break
        sent_count = _sentence_count(posts["post_1"])
        if sent_count < 2:
            log.warning("  S1: still 1 sentence after auto-split — caller skips article")
    return posts


def generate_thread(article):
    """Generate six source-grounded posts. Returns (data, error)."""
    evidence_error = article_evidence_gate(article)
    if evidence_error:
        return None, evidence_error
    user = build_user_prompt(article)
    # One writer plus one revision. If revision fails with hard issues, skip immediately —
    # second writer call with same prompt will generate same slop. Only retry (attempt 2)
    # when revision fails due to JSON/syntax issues, not hard validation.
    for attempt in range(1, 2):
        content, error = _call_llm(SYSTEM_PROMPT, user, max_retries=1, temperature=0.2)
        if error:
            log.warning(f"  Writer request failed — {error[:80]}")
            if is_rate_limit_error(error):
                return None, error
            continue
        content = content.strip()
        data = _parse_llm_json(content)
        if data is None:
            log.warning(f"  LLM attempt {attempt}/1 — bad JSON: {content[:80]}")
            continue
        if data.get("status") == "error":
            return None, data.get("message", "LLM error")
        posts = {k: _convert_pov(data.get(k) or "") for k in ["post_1","post_2","post_3","post_4","post_5","post_6"]}
        posts = _normalize_s1(posts, article["body"])

        # All 6 posts required.
        missing = [f"{k}: empty" for k in ["post_1","post_2","post_3","post_4","post_5","post_6"] if not posts.get(k, "").strip()]
        # Style warnings are advisory. Grounding, names, claims, empty/structure remain hard.
        style_warnings = deterministic_validate(posts) + _duplicate_fact_warnings(posts)
        noun_warnings = _validate_proper_nouns(posts, article["body"])
        claim_warnings = _validate_claim_markers(posts, article["body"])
        voice_warnings = _voice_warnings(posts)
        jargon_warnings = _validate_jargon(posts, article["body"])
        grounding_warnings = grounding_validate(article, posts)
        hard_style_warnings = [w for w in style_warnings + voice_warnings if any(x in w for x in ("empty", "too short", "no sentences", "minimum 2 sentences", "only 0 sentence", "S1 WAJIB", "weak winning hook", "generic winning CTA", "CTA not found", "S6 must not", "does not follow policy winning arc", "missing winning arc evidence"))]
        engagement_warnings = _validate_s1_hook(posts, article["body"], article) + _validate_s6_cta(posts, article["body"])
        warnings = missing + grounding_warnings + noun_warnings + claim_warnings + jargon_warnings + hard_style_warnings + engagement_warnings
        soft_warnings = style_warnings + voice_warnings
        if soft_warnings:
            log.info(f"  Soft style warnings (advisory): {soft_warnings}")
        if warnings:
            log.warning(f"  Hard validation: {warnings}")
            # Do not feed validator marker vocabulary back to model; models mirror it.
            revision_notes = re.sub(r"'[^']*'", "'unsupported wording'", '; '.join(warnings))
            rev_user = user + "\n\n" + build_revision_prompt(revision_notes, posts, article)
            # One bounded revision; no rapid provider churn.
            c2, e2 = _call_llm(SYSTEM_PROMPT, rev_user, max_retries=1, temperature=0.2)
            if c2:
                d2 = _parse_llm_json(c2)
                if d2 is not None:
                    p2 = {k: _convert_pov(d2.get(k) or "") for k in ["post_1","post_2","post_3","post_4","post_5","post_6"]}
                    p2 = _normalize_s1(p2, article["body"])
                    style_w2 = deterministic_validate(p2) + _duplicate_fact_warnings(p2)
                    noun_w2 = _validate_proper_nouns(p2, article["body"])
                    w2 = [f"{k}: empty" for k in ["post_1","post_2","post_3","post_4"] if not p2.get(k, "").strip()]
                    claim_w2 = _validate_claim_markers(p2, article["body"])
                    w2.extend(grounding_validate(article, p2))
                    w2.extend(noun_w2)
                    w2.extend(claim_w2)
                    w2.extend(_validate_jargon(p2, article["body"]))
                    w2.extend(_validate_s1_hook(p2, article["body"], article))
                    w2.extend(_validate_s6_cta(p2, article["body"]))
                    voice_w2 = _voice_warnings(p2)
                    if style_w2 or voice_w2:
                        log.info(f"  Soft style warnings after revision: {style_w2 + voice_w2}")
                    if d2.get("status") == "success" and not w2:
                        data, posts = d2, p2
                        warnings = []
                        log.info("  Revision fixed validation")
                    else:
                        log.warning(f"  Revision blocked: {w2 + style_w2 + voice_w2}")
                        # HARD VALIDATION FAILURE — per-field revert to pre-revision originals.
                        # The LLM tends to "solve" one hard issue while introducing a new one
                        # (e.g. patching post_2's 'padahal' → creates 'padahal' in post_1).
                        # Instead of returning "revision_failed", revert each post_ that has
                        # hard validation warnings back to its original value and re-validate.
                        # If original still fails → article skipped naturally via hard validation.
                        log.info("  Revision introduced new hard issues — per-field revert to originals")
                        p_orig = {k: _convert_pov(data.get(k) or "") for k in ["post_1","post_2","post_3","post_4","post_5","post_6"]}
                        p_orig = _normalize_s1(p_orig, article["body"])
                        # Re-check original against hard validation
                        w_orig = grounding_validate(article, p_orig)
                        w_orig.extend(_validate_jargon(p_orig, article["body"]))
                        w_orig.extend([f"{k}: empty" for k in ["post_1","post_2","post_3","post_4"] if not p_orig.get(k,"").strip()])
                        w_orig.extend(_validate_claim_markers(p_orig, article["body"]))
                        noun_orig = _validate_proper_nouns(p_orig, article["body"])
                        w_orig.extend(noun_orig)
                        if not w_orig:
                            # Original was actually valid — revision just made noise
                            posts = p_orig
                            warnings = []
                            log.info("  Per-field revert: original was valid, revision was noise")
                        else:
                            # Model can repeatedly invent names/quotes while repairing. Use
                            # deterministic source-only fallback before rejecting candidate.
                            log.warning(f"  Original posts also hard-fail: {w_orig}")
                            fallback_posts = _source_fallback_posts(article)
                            if fallback_posts:
                                fallback_posts = _normalize_s1(fallback_posts, article["body"])
                                fallback_issues = deterministic_grounding_validate(article, fallback_posts)
                                fallback_issues += _validate_s1_hook(fallback_posts, article["body"], article)
                                fallback_issues += _validate_s6_cta(fallback_posts, article["body"])
                                fallback_issues += thread_contract_issues(fallback_posts, article.get("url", ""))
                                fallback_issues += _source_fallback_dangling_refs(fallback_posts)
                                if not fallback_issues:
                                    posts = fallback_posts
                                    data = {"status": "success", "angle": "source-only fallback"}
                                    warnings = []
                                    log.info("  Source-only fallback passed deterministic validation")
                                else:
                                    log.warning(f"  Source-only fallback blocked: {fallback_issues[:3]}")
                                    return None, "revision_failed"
                            else:
                                return None, "revision_failed"
                else:
                    log.warning("  Revision blocked: bad JSON")
                    return None, "generation_failed"
            if warnings:
                continue
        # Quality gate: check article supports the thread
        if not _quality_gate(article, data, posts, warnings):
            log.warning("Quality gate blocked — skip generation")
            return None, "quality_gate"
        for k in posts:
            posts[k] = _format_sentence_blanks(posts[k])
        slide_audit = source_slide_audit(article.get("body", ""), posts)
        log.info("  Source-slide audit: %s", {k: v["source_sentences"] for k, v in slide_audit.items()})
        evidence_issues = _validate_source_evidence_map(posts, article.get("body", ""))
        if evidence_issues:
            log.warning("  Source evidence map blocked: %s", evidence_issues)
            return None, "source_evidence_map_failed"
        contract_issues = thread_contract_issues(posts, article.get("url", ""))
        if contract_issues:
            log.warning(f"  Thread contract blocked: {contract_issues}")
            continue
        return {
            "article_title": article.get("title", ""),
            "article_url": article.get("url", ""),
            "article_source": article.get("source", ""),
            "angle": data.get("angle", ""),
            "story_functions": STORY_FUNCTIONS,
            "arc": data.get("arc") or _content_metadata(article.get("title", ""), article.get("body", ""))[1],
            "hook_pattern": _content_metadata(article.get("title", ""), article.get("body", ""))[2],
            "posts": posts,
        }, None
    return None, "generation_failed"

# ══════════════════════════════════════════════
#   THREADS PUBLISHER
# ══════════════════════════════════════════════

def post_to_threads(article_title, posts, image_url=None, inflight=None):
    """Post a seven-post chain to Threads; S1 uses article image, S7 carries source URL."""
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


def _source_fallback_dangling_refs(posts):
    """Editorial gate for source-only fallback.

    Each fallback post is a standalone Threads post, so an opening
    demonstrative ('ini'/'tersebut'/'demikian'/'begini'/'begitu') with no
    antecedent is a dangling reference. Reject it so the caller falls back to
    the next-candidate skip instead of publishing the fragment.
    """
    issues = []
    opener = re.compile(r"^\W*[\w\s'\"“”.()]{1,60}\s+(ini|tersebut|demikian|begini|begitu)\b", re.I)
    for k in (f"post_{i}" for i in range(1, 7)):
        text = (posts.get(k) or "").strip()
        if not text:
            continue
        first = text.split(". ")[0].split(".")[0]
        if opener.match(first):
            issues.append(f"{k}: dangling demonstrative opener")
    return issues

def main():
    started_at = time.monotonic()
    data = load_data()
    cohorts_changed = normalize_topic_cohorts(data)
    # Dry-run must not write analytics or alter future selection.
    if not DRY_RUN:
        metrics_changed = refresh_performance_metrics(data)
        evaluation_changed = evaluate_published_content(data)
        if metrics_changed or evaluation_changed or cohorts_changed:
            save_data(data)
    inflight = load_inflight()
    if inflight:
        posts = inflight["posts"]
        article = inflight["article"]
        veto = _final_publish_veto(article, {
            "angle": inflight.get("topic", {}).get("angle", ""),
            "arc": inflight.get("topic", {}).get("arc", ""),
        })
        if veto:
            log.error(f"Final publish veto: {veto}")
            return
        log.warning(f"Resuming partial chain from S{len(inflight.get('post_ids', [])) + 1}")
        pub = post_to_threads(article["title"], posts, inflight.get("image_url"), inflight)
        if _publish_complete(pub, posts):
            topic = inflight["topic"]
            topic.setdefault("lane", _story_lane(article["title"], article.get("body", "")))
            topic.setdefault("impact_channel", _international_impact_channel(article["title"], article.get("body", "")))
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

    # Step 1: Pressbox-style single-run flow. Scrape, generate, validate, then
    # publish (or render on --dry-run). No prepared draft staging.
    article = body = og_image = None
    prepared_result = None
    articles = []
    if PREPARE_NEXT:
        log.warning("--prepare-next deprecated; running single-pass flow")
    log.info("Scraping economy sources...")
    articles = scrape_all()
    log.info(f"  Got {len(articles)} raw articles")
    # Scout is the publisher's only candidate pool: five body-verified daily topics.
    hot_topics = scout_hot_topics(articles, data=data)
    fallback_topics = scout_hot_topics(
        articles, data=data, per_source_limit=6, allow_cluster_repeats=True,
    )
    for topic in hot_topics:
        log.info(f"  Hot #{topic['rank']}: [{topic['lane']}] {topic['title'][:70]} (score={topic['hot_score']})")
    international_topics = international_dry_run_candidates(hot_topics, fallback_topics)
    if DRY_RUN and not international_topics:
        log.error("International dry-run gate failed: no body-verified lane=international candidate")
        print("NO_INTERNATIONAL_CANDIDATE", flush=True)
        return
    if international_topics:
        log.info(f"  International candidates: {len(international_topics)}")
    if not DRY_RUN:
        save_hot_topics(hot_topics)
    articles = _publisher_pool(articles, hot_topics, fallback_topics)
    log.info(f"  Publisher pool: {len(articles)} body-verified scout candidates")

    # Step 2: Search ranked pool. Like Pressbox, title ranks; body decides eligibility.
    from collections import Counter
    skipped_urls = set()
    reject_reasons = Counter()
    candidate_limit = len(articles) if not article else 0
    for _ in range(candidate_limit):
        candidate = _pick_article(articles, posted_urls | skipped_urls, data)
        if not candidate:
            break
        is_repeat, shared_entities, shared_words = _is_repeat_issue(candidate["title"], recent_topics)
        if is_repeat:
            reject_reasons["repeat_issue"] += 1
            skipped_urls.add(candidate["url"])
            log.info(f"  Skip: repeat issue within 72h (entities={sorted(shared_entities)}, title_words={len(shared_words)})")
            continue
        log.info(f"Picked: {candidate['title']}")
        log.info(f"  Source: {candidate['source']} | Score: {candidate.get('eco_score', 0)} | Reason: {candidate.get('_reason', '')} | Weight: {candidate.get('_weight', 0)}")
        log.info("Fetching article body...")
        candidate_body, candidate_image, article_ts = _fetch_article_body(candidate["url"])
        source_ts, timestamp_source, timestamp_reason = _resolve_published_timestamp(article_ts, candidate.get("ts", 0), time.time())
        if not source_ts:
            reject_reasons[f"timestamp_{timestamp_reason}"] += 1
            log.info(f"  Skip: timestamp {timestamp_reason} (article metadata; RSS fallback unavailable)")
            skipped_urls.add(candidate["url"])
            continue
        if timestamp_source == "rss_fallback":
            log.info("  Timestamp: rss_fallback (fresh RSS only)")
        topic_score, economy_score, impact_score = _topic_score(candidate["title"], candidate_body)
        pattern_name, pattern_confidence = _classify_pattern(candidate["title"], candidate_body)
        eligible_ok, eligible_reason = _is_eligible_candidate(candidate["title"], candidate_body, candidate["source"])
        if eligible_ok:
            if candidate_image is None and not IMAGE_DISABLED:
                reject_reasons["image_invalid"] += 1
                log.warning("  Skip: no valid HD image — trying next candidate")
                skipped_urls.add(candidate["url"])
                continue
            article, body, og_image = candidate, candidate_body, candidate_image
            article["body"] = body
            article["published_ts"] = source_ts
            article["image_hint"] = _image_hint(og_image)
            article["pattern"] = pattern_name
            article["pattern_label"] = _pattern_label(pattern_name)
            article["pattern"] = article.get("pattern") or _content_metadata(article["title"], body)[0]
            article["arc"] = _content_metadata(article["title"], body)[1]
            article["hook_pattern"] = _content_metadata(article["title"], body)[2]
            article["lane"] = _story_lane(article["title"], body)
            article["impact_channel"] = _international_impact_channel(article["title"], body)
            log.info(f"  Lane: {article['lane']} | Impact channel: {article['impact_channel'] or 'n/a'}")
            log.info(f"  Body: {len(body)} chars | Pattern: {pattern_name} ({article['pattern_label']}, confidence={pattern_confidence:.2f})")
            break
        reject_reasons[eligible_reason] += 1
        skipped_urls.add(candidate["url"])
        log.warning(f"  Skip: {eligible_reason} ({topic_score}/10, economy={economy_score}, impact={impact_score})")
    if not article:
        summary = ", ".join(f"{reason}={count}" for reason, count in reject_reasons.most_common()) or "none"
        log.error(f"Candidate rejection summary: {summary}")
        log.error(f"No eligible article among {candidate_limit} ranked candidates")
        print("NO_SAFE_CANDIDATE", flush=True)
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
    # Failure fingerprint — track systemic writer failures to circuit-break candidate churn
    failure_counts = {}  # {fingerprint: count}
    if result:
        log.info("Using immutable prepared draft...")
    else:
        log.info("Generating thread...")
        if recent_openings:
            article["recent_openings"] = recent_openings[:5]
        result, error = generate_thread(article)
    # Soft writer failure may use source-only fallback; hard gates stay mandatory.
    if error in {"revision_failed", "quality_gate", "revision_json_error", "generation_failed"}:
        fallback_posts = _source_fallback_posts(article)
        if fallback_posts:
            fallback_posts = _normalize_s1(fallback_posts, article["body"])
            fallback_issues = deterministic_grounding_validate(article, fallback_posts)
            fallback_issues += _validate_s1_hook(fallback_posts, article["body"], article)
            fallback_issues += _validate_s6_cta(fallback_posts, article["body"])
            fallback_issues += thread_contract_issues(fallback_posts, article.get("url", ""))
            fallback_issues += _source_fallback_dangling_refs(fallback_posts)
            if not fallback_issues:
                result = {"posts": fallback_posts, "angle": "source-only fallback",
                          "arc": article.get("arc", "")}
                error = None
                log.warning("Using source-only fallback after writer failure")
            else:
                log.warning(f"Source-only fallback blocked: {fallback_issues[:3]}")
    if error:
        # Track failure fingerprint for circuit-breaker
        fprint = f"{error}"
        failure_counts[fprint] = failure_counts.get(fprint, 0) + 1
        log.error(f"Generation failed: {error} (fingerprint count: {failure_counts[fprint]})")
        if is_rate_limit_error(error):
            log.error("Generation stopped: Mistral rate limit; cooling down 90s before retry candidate")
            time.sleep(90)
        else:
            skipped_urls.add(article["url"])

        # Generation may have succeeded via the 429 retry path — go straight to save/post.
        if result and not error:
            article["body"] = body
            article["image_hint"] = _image_hint(og_image)
            if IMAGE_URL:
                image_url = IMAGE_URL
            elif not IMAGE_DISABLED:
                image_url = og_image
            # Restore original article object for downstream use.
            article["pattern"] = article.get("pattern") or _classify_pattern(article["title"], article["body"])[0]
            article["pattern_label"] = _pattern_label(article["pattern"])
            goto_step5 = True
        else:
            goto_step5 = False

        # Try next-best candidate from remaining pool (fast retry)
        retry_article = None
        for _ in range(min(2, candidate_limit)):
            if goto_step5:
                break
            retry_article = _pick_article(articles, posted_urls | skipped_urls, data)
            if retry_article is None:
                break
            log.info(f"  Retry candidate: {retry_article['title'][:80]}")
            # Quick gate on retry candidate
            retry_body, retry_img, retry_article_ts = _fetch_article_body(retry_article["url"])
            retry_ts, retry_ts_source, retry_ts_reason = _resolve_published_timestamp(
                retry_article_ts, retry_article.get("ts", 0), time.time()
            )
            if not retry_ts:
                reject_reasons[f"retry_timestamp_{retry_ts_reason}"] += 1
                log.info(f"  Retry skip: timestamp {retry_ts_reason}")
                skipped_urls.add(retry_article["url"])
                continue
            if retry_ts_source == "rss_fallback":
                log.info("  Retry timestamp: rss_fallback (fresh RSS only)")
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
            retry_article["published_ts"] = retry_ts
            retry_article["image_hint"] = _image_hint(retry_img)
            retry_article["pattern"], retry_confidence = _classify_pattern(retry_article["title"], retry_body)
            retry_article["pattern_label"] = _pattern_label(retry_article["pattern"])
            retry_article["pattern"] = retry_article.get("pattern") or _content_metadata(retry_article["title"], retry_body)[0]
            retry_article["arc"] = _content_metadata(retry_article["title"], retry_body)[1]
            retry_article["hook_pattern"] = _content_metadata(retry_article["title"], retry_body)[2]
            retry_article["lane"] = _story_lane(retry_article["title"], retry_body)
            retry_article["impact_channel"] = _international_impact_channel(retry_article["title"], retry_body)
            og_image = retry_img
            if IMAGE_URL:
                image_url = IMAGE_URL
            elif not IMAGE_DISABLED:
                image_url = retry_img  # keep slide-1 image in sync with retried article
            if recent_openings:
                retry_article["recent_openings"] = recent_openings[:5]
            # Cooldown between candidates — reduced from 60-75s to 10-20s (was major bottleneck)
            cooldown = 10 + random.randint(0, 10)
            log.info(f"  Cooldown {cooldown}s before retry generation...")
            time.sleep(cooldown)
            result, error = generate_thread(retry_article)
            if error:
                # Track failure fingerprint — if same issue repeats 3+ times, circuit-break
                fprint = f"{error}"
                failure_counts[fprint] = failure_counts.get(fprint, 0) + 1
                log.error(f"Retry generation also failed: {error} (fingerprint count: {failure_counts[fprint]})")
                if failure_counts[fprint] >= 3:
                    log.error(f"CIRCUIT BREAK — same failure '{error}' seen {failure_counts[fprint]} times — stopping candidate churn")
                    return
                if is_rate_limit_error(error):
                    log.error("Generation stopped: Mistral rate limit; skip candidate churn")
                    return
                skipped_urls.add(retry_article["url"])
                continue
            article = retry_article  # update article ref for downstream use
            break  # generation succeeded — go straight to save/post

        if not result:
            log.error("Generation failed: no verified LLM draft after retry")
            print("NO_SAFE_CANDIDATE", flush=True)
            return

    if not result:
        log.error("Generation failed: no valid result")
        print("NO_SAFE_CANDIDATE", flush=True)
        return

    veto = _final_publish_veto(article, result)
    if veto:
        log.error(f"Final publish veto: {veto}")
        print("NO_SAFE_CANDIDATE", flush=True)
        return

    posts = result["posts"]
    if DRY_RUN:
        print("\n=== TECHBRO DRY RUN ===")
        print(f"TITLE: {article.get('title', '')}")
        for i in range(1, 7):
            print(f"S{i}: {posts.get(f'post_{i}', '')}")
        print(f"S7: Sumber: {article.get('url', '')}")
        print("=== END TECHBRO DRY RUN ===\n", flush=True)
    for i in range(1, 8):
        first_line = posts.get(f"post_{i}", "").split("\n")[0][:80] or "(empty)"
        log.info(f"  S{i}: {first_line}")

    # Step 6: Post
    if not DRY_RUN:
        inflight = {
            "article": article, "posts": posts, "post_ids": [], "image_url": image_url,
            "topic": {
                "title": article["title"], "article_url": article["url"], "article_source": article["source"],
                "lane": article.get("lane") or _story_lane(article["title"], article.get("body", "")),
                "impact_channel": article.get("impact_channel") or _international_impact_channel(article["title"], article.get("body", "")),
                "angle": result.get("angle", ""),
                "story_functions": STORY_FUNCTIONS,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+07:00"),
                "eco_score": article.get("eco_score"), "selection_weight": article.get("_weight"),
                "pattern": article.get("pattern"), "arc": result.get("arc", ""), "slides": posts,
                "likes": None, "replies": None, "reposts": None, "views": None, "quotes": None,
                "cohort": CURRENT_COHORT,
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
                "lane": article.get("lane") or _story_lane(article["title"], article.get("body", "")),
                "impact_channel": article.get("impact_channel") or _international_impact_channel(article["title"], article.get("body", "")),
                "angle": result.get("angle", ""),
                "post_id": pub["post_ids"][0],
                "media_id": pub["media_ids"][0] if pub.get("media_ids") else None,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+07:00"),
                "eco_score": article.get("eco_score"),
                "selection_weight": article.get("_weight"),
                "pattern": article.get("pattern") or _content_metadata(article["title"], article.get("body", ""))[0],
                "arc": result.get("arc") or article.get("arc") or _content_metadata(article["title"], article.get("body", ""))[1],
                "hook_pattern": article.get("hook_pattern") or _content_metadata(article["title"], article.get("body", ""))[2],
                "slides": posts,
                "likes": None,
                "replies": None,
                "reposts": None,
                "views": None,
                "quotes": None,
                "cohort": CURRENT_COHORT,
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
        for i in range(1, 8):
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
