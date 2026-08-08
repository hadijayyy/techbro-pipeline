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

# ── Paths ────────────────────────────────────────────────────────────────────

BASE = Path(__file__).parent
POSTED_FILE = BASE / "posted_topics_v2.json"
KEYWORDS_FILE = BASE / "keywords.json"
DYNAMIC_KEYWORDS_FILE = BASE / "dynamic_keywords.json"
PREPARED_ARTICLE_FILE = BASE / "prepared_article.json"

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
log = logging.getLogger("techbro-v3")

# ── Dynamic Keywords Loader ────────────────────────────────────────────────────

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


def load_dynamic_keywords():
    """Load vetted, expiring topic terms; stale terms never affect ranking."""
    try:
        data = json.loads(DYNAMIC_KEYWORDS_FILE.read_text())
        if time.time() - data.get("updated_at", 0) > 86400:
            return []
        return [str(x).lower() for x in data.get("keywords", []) if len(str(x)) >= 3]
    except (OSError, json.JSONDecodeError, TypeError):
        return []

KW = load_keywords()
DYNAMIC_KEYWORDS = load_dynamic_keywords()

# ── Economy Sources ──────────────────────────────────────────────────────────

SOURCES = {
    # PURE ekonomi — RSS feeds
    "cnn_ekonomi":      {"url": "https://www.cnnindonesia.com/ekonomi/rss",        "score": 10, "type": "rss", "domain": "cnnindonesia.com/ekonomi/"},
    "detik_finance":    {"url": "https://finance.detik.com/rss",                  "score": 10, "type": "rss", "domain": "finance.detik.com/"},
    "cnbc_market":      {"url": "https://www.cnbcindonesia.com/market/rss",       "score": 9,  "type": "rss", "domain": "cnbcindonesia.com/market/"},
    # Mixed (ekonomi + umum) — prioritised below pure ekonomi via lower score
    "cnbc_news":        {"url": "https://www.cnbcindonesia.com/news/rss",          "score": 8,  "type": "rss", "domain": "cnbcindonesia.com/"},
    # HTML fallback — only used when RSS is empty, slower but deeper
    "detik_finance_html": {"url": "https://finance.detik.com/",                   "score": 7,  "type": "html", "domain": "finance.detik.com/"},
}

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
    big_matches = re.findall(r'Rp\s*(\d[\d,.]*)\s*(triliun|miliar|juta)', title.lower())
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
    POSTED_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_prepared_article(posted_urls):
    """One-shot editor-selected article for the next scheduled run."""
    try:
        article = json.loads(PREPARED_ARTICLE_FILE.read_text())
        if article.get("url") in posted_urls or time.time() > article.get("expires_at", 0):
            PREPARED_ARTICLE_FILE.unlink(missing_ok=True)
            return None
        if not all(article.get(k) for k in ("title", "url", "body", "og_image")):
            return None
        return article
    except (OSError, json.JSONDecodeError, TypeError):
        return None


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
            href = _canonical_url(str(a_tag["href"]).strip())
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
    # Same-source cluster dedup: keep max 2 per source, prefer diverse entities
    from collections import defaultdict
    by_source = defaultdict(list)
    for a in articles:
        by_source[a["source"]].append(a)
    deduped = []
    for source, src_articles in by_source.items():
        # Sort by title quality (longer = more specific = better)
        src_articles.sort(key=lambda a: len(a["title"]), reverse=True)
        # Keep max 2 per source to prevent OJK/OJK/OJK/OJK cluster
        deduped.extend(src_articles[:2])
    log.info(f"  Articles: {len(articles)} raw → {len(deduped)} deduped")
    return deduped

# ── Economy Relevance Scoring ────────────────────────────────────────────────

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
        if kw in tl:
            return (0, "hard_reject:" + kw)
    for name in NAMED_BLACKLIST:
        if name in tl:
            return (0, "blacklist:" + name)
    # Video reject — skip before body fetch/LLM
    if tl.startswith("video:") or "/video-" in article.get("url", ""):
        return (0, "video_article")
    # Category scoring — one keyword counted once per category
    for cat_entry in SCORE_CATEGORIES:
        cat_name = cat_entry.get("name", "") if isinstance(cat_entry, dict) else cat_entry[0]
        cat_max = cat_entry.get("max_score", 0) if isinstance(cat_entry, dict) else cat_entry[1]
        cat_kws = cat_entry.get("keywords", []) if isinstance(cat_entry, dict) else cat_entry[2]
        cat_score = 0
        for kw in cat_kws:
            if kw in tl:
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
            if ent in tl_full:
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

    # Dynamic terms are ranking hints only; body gates remain mandatory.
    dynamic_hits = sum(kw in tl for kw in DYNAMIC_KEYWORDS)
    score += min(dynamic_hits * 5, 15)

    # Hot-topic briefing: public money, mass impact, and final decisions.
    # Title only ranks candidates; article body remains the fact source.
    hot_signals = (
        (25, ("putusan mk", "putusan ma", "mahkamah konstitusi", "mahkamah agung", "audit bpk", "dpr setujui", "disahkan")),
        (20, ("apbn", "anggaran", "pajak", "subsidi", "bansos")),
        (15, ("dialihkan", "dipisah", "dipotong", "ditambah", "alokasi dana")),
        (20, ("bbm", "listrik", "sekolah", "kesehatan", "pangan", "transportasi", "upah", "pekerja")),
        (15, ("resmi", "ditetapkan", "berlaku", "putusan", "disahkan")),
    )
    for bonus, keywords in hot_signals:
        if any(kw in tl for kw in keywords):
            score += bonus
    if re.search(r"\bberlaku\b.*\b\d{1,2}\b|\b\d{1,2}\s+(januari|februari|maret|april|mei|juni|juli|agustus|september|oktober|november|desember)\b", tl):
        score += 10

    # Daily market moves are low-value unless title also signals policy or public impact.
    technical = any(kw in tl for kw in ("rupiah", "ihsg", "saham", "harga emas", "harga minyak"))
    public_angle = any(kw in tl for kw in ("kebijakan", "bi", "bank indonesia", "apbn", "pajak", "subsidi", "anggaran", "berlaku", "ditetapkan"))
    if technical and not public_angle:
        score -= 30

    # Routine SPBU price lists are utility updates, not Techbro analysis topics.
    # Keep structural fuel-policy stories such as B50 or subsidy changes eligible.
    routine_bbm = "bbm" in tl and "spbu" in tl
    fuel_policy = any(kw in tl for kw in ("b50", "subsidi", "kebijakan", "aturan", "kuota", "alokasi", "apbn"))
    if routine_bbm and not fuel_policy:
        score -= 100

    # Soft reject penalty (cancelled by sufficient economy signals)
    if signals >= 2:
        pass  # strong signals override soft reject
    else:
        for kw in SOFT_REJECT:
            if kw in tl:
                score -= 60
                break

    return (score, f"cats={categories_hit} sig={signals} dyn={dynamic_hits}")

def _pick_article(articles, posted_urls):
    """Pick best unscraped economy article. Uses category-based scoring + freshness + source quality."""
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
        # Final score
        a["_weight"] = eco_score + freshness + relevance + source_quality
    # Sort by weight descending
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
        if size and size[0] >= 1200 and size[1] >= 670:
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
        date_tag = soup.find("meta", attrs={"name": re.compile(r"(?:publishdate|datePublished|pubdate)", re.I)})
        if date_tag and date_tag.get("content"):
            try:
                published_ts = parsedate_to_datetime(date_tag["content"]).timestamp()
            except (TypeError, ValueError):
                try:
                    published_ts = datetime.strptime(str(date_tag["content"]), "%Y/%m/%d %H:%M:%S").replace(tzinfo=WIB).timestamp()
                except ValueError:
                    pass
        # og:image
        og_tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
        if og_tag and og_tag.get("content"):
            og_image = validate_article_image(_hd_image_url(og_tag["content"]))
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


def _is_eligible_candidate(title, body, source):
    """Full economy gate shared by main pick and retry path.
    Returns (eligible: bool, reason: str)."""
    if not body or len(body) < 500:
        return False, "body too short"
    title_lower = title.lower()
    if not any(sig in title_lower for sig in ECONOMY_TITLE_SIGNALS):
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
    eligible = (pattern_name is not None and pattern_confidence >= 0.33) or topic_score >= 7
    if not eligible:
        return False, f"editorial score failed ({topic_score}/10, economy={economy_score}, impact={impact_score})"
    return True, f"pattern={pattern_name} conf={pattern_confidence:.2f} topic={topic_score}"


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
    policy = any(word in headline for word in (
        "bi rate", "apbn", "anggaran", "pajak", "subsidi", "peraturan",
        "ditetapkan", "putusan", "bpk", "ojk",
    ))
    return market and not policy


# ── Pressbox-style Pattern Classification ──────────────────────────────────────
# 5 economy patterns with keyword triggers + priority ordering.
# Priority: DOMPET > KORUPSI > KEBIJAKAN > PROYEK > PASAR
# Pattern determines candidate selection priority AND S1 hook style in LLM prompt.

ECONOMY_PATTERNS = {
    "DOMPET": {
        "priority": 1,
        "label": "Dompet Kejepit",
        "desc": "Harga naik, tarif, pajak, subsidi, BBM — dampak langsung ke kantong rakyat",
        "keywords": [
            "harga naik", "tarif naik", "bbm naik", "harga bbm", "bbm turun", "pajak naik", "subsidi dipotong", "harga turun", "harga anjlok", "harga melonjak",
            "inflasi", "daya beli", "biaya hidup", "harga pangan", "sembako",
            "tarif listrik", "tarif air", "iuran bpjs", "tarif tol", "tarif parkir",
            "upah minimum", "umr", "umk", "gaji", "tunjangan",
            "bansos", "blt", "pkh", "bpn", "kartu prakerja",
            "ppn", "pph", "bea", "cukai", "pungutan",
            "kpr", "cicilan", "kredit rumah", "pinjaman",
            "biaya sekolah", "spp", "uang kuliah",
        ],
    },
    "KORUPSI": {
        "priority": 2,
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
        "priority": 3,
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
        "priority": 4,
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
        # DOMPET: hits/4, KORUPSI: hits/3, KEBIJAKAN: hits/3, PROYEK: hits/4, PASAR: hits/3
        thresholds = {"DOMPET": 6, "KORUPSI": 5, "KEBIJAKAN": 5, "PROYEK": 6, "PASAR": 5}
        divisor = thresholds.get(name, 4)
        confidence = min(hits / divisor, 1.0)

        # Higher-priority patterns need fewer hits to qualify
        min_hits = {1: 2, 2: 2, 3: 2, 4: 3, 5: 3}.get(cfg["priority"], 3)
        if hits >= min_hits and confidence > best_confidence:
            # Priority-weighted: higher priority gets bonus
            priority_bonus = (6 - cfg["priority"]) * 0.06
            adjusted_confidence = confidence + priority_bonus

            if adjusted_confidence > best_confidence:
                best_confidence = min(adjusted_confidence, 1.0)
                best_pattern = name

    return best_pattern, best_confidence


def _topic_score(title, body):
    """Score article relevance: 0-10. Primary: pattern classification. Fallback: editorial LLM."""
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
    """Normalize second person to kalian without adding a first-person narrator."""
    parts = re.split(r'("[^"]*")', text)
    for i, p in enumerate(parts):
        if i % 2 == 0:
            p = re.sub(r'\b(?:lo|lu|kamu|anda)\b', 'kalian', p, flags=re.IGNORECASE)

            parts[i] = p
    text = ''.join(parts)
    return re.sub(r'(?<!\w)[*_]+([^*_\n]+)[*_]+', r'\1', text)

def _format_sentence_blanks(text):
    """Collapse whitespace to one flowing paragraph per post.
    No forced blank line per sentence; URL/footer paragraphs appended after."""
    s = text.replace('\u2014 ', ' ').replace('\u2014', ' ')
    # Natural writing: drop label-colon style ("2020: RI digugat" -> "2020 RI digugat").
    # URL-safe: colon before a URL (https://... or www.) survives.
    s = re.sub(r":(?=\s+https?://|\s+www\.)", "\u0001", s)
    s = re.sub(r":\s+", " ", s)
    s = s.replace("\u0001", ":")
    s = re.sub(r'\s+', ' ', s)
    return s.strip()

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

def _call_llm(system, user, model="mistral-large-latest", max_retries=3):
    api_key = _get_api_key()
    if not api_key:
        return None, "No API key found"
    base_url = "https://api.mistral.ai/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": random.uniform(0.7, 0.9),
        "max_tokens": 1500,
    }
    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            r = httpx.post(base_url, headers=headers, json=payload, timeout=60)
            if r.status_code == 200:
                content = (r.json()["choices"][0]["message"].get("content") or "").strip()
                return content, None
            elif r.status_code == 401:
                return None, f"Auth error {r.status_code}"
            elif r.status_code == 429:
                last_error = f"Rate limit {r.status_code}"
                if attempt < max_retries:
                    # Rate windows need real cooldown; fast retries only burn quota.
                    time.sleep(30 * attempt)
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

SYSTEM_PROMPT = """# TECHBRO EKONOMI — THREADS WRITER V5

RETURN ONLY VALID JSON. No markdown. No explanation. No code fences. Your entire response must be parseable by json.loads().

⚠️ HALUSINASI = GAGAL. Setiap angka, nama lembaga, nama kota, nama proyek, nama orang, jumlah — HARUS ADA PERSIS di body artikel. Kalau gak yakin, balas insufficient_evidence. Lebih baik gak posting daripada posting fakta palsu.

Kamu mengubah satu artikel ekonomi Indonesia jadi tepat 6 post Threads. Akurat, viral, pedas, dan kayak temen lo yang pinter ekonomi ngomong langsung ke lo di DM.

## ATURAN MUTLAK
1. Sumber fakta SATU-SATUNYA adalah ISI ARTIKEL. Judul, pengetahuan umum, asumsi = bukan sumber.
2. Angka, tanggal, nama, kutipan, lembaga, kebijakan HARUS PERSIS dari isi artikel. KALAU GAK TERTULIS DI BODY → JANGAN DIPAKE. Nominal Rp, jumlah orang, nama kota/proyek — semua harus literal.
3. Jangan ubah "akan/rencana/diperkirakan/berpotensi" jadi fakta pasti.
4. Dampak harga/gaji/pekerjaan/dompet HANYA bila artikel menyebut mekanismenya.
5. Jika bukti tidak cukup → balas: {"status":"error","message":"insufficient_evidence"}

## SUARA — V5 "LO" STYLE
- "Lo" bukan "kalian", "gue" jarang. Nada: temen ngobrol di DM. Bahasa sehari-hari, pendek, kayak chat.
- Satu slide = 1-2 kalimat pendek. Gak usah panjang. Kayak lo kirim voice note 15 detik.
- BEROPINI — ambil sisi rakyat kecil. Jangan netral. Jangan format "di satu sisi... di sisi lain...".
- Terjemahkan istilah ekonomi langsung: "holding company → artinya perusahaan induk yang ngatur anak-anak perusahaan".
- JANGAN: kalimat laporan ("Fakta:", "Perlu diketahui:"), kata birokratis, hashtag, template AI.
- JANGAN pakai ":" di dalam kalimat. "Alasannya X" bukan "Alasannya: X". "2020 RI digugat" bukan "2020: RI digugat". "Yang paling kena buruh" bukan "Yang paling kena: buruh". Tulis natural kayak chat.
- JANGAN kata: akselerasi, mitigasi, implementasi, optimalisasi, signifikan, komprehensif.

## FORMULA VIRAL — RYAN HADI STYLE
Ini struktur post viral techbro ekonomi. Ikutin polanya:

**S1 — "BARU AJA" HOOK → 80-140 char**
"Baru aja [KEJADIAN]. [DAMPAK LANGSUNG KE LO — sebut angka dari artikel: berapa orang kena, harga apa naik/turun]."
Contoh dari post viral @ryanhadiii: "Baru aja Pelita Air dipindahin dari Pertamina ke Garuda. Bukan cuma ganti logo, tapi bisa ubah nasib 10.000 karyawan dan harga tiket lo."
KENAPA WORKS: fresh news + personal stakes dalam 2 kalimat. Gak pake basa-basi. Gak deskripsi gambar. SEMUA ANGKA HARUS DARI BODY ARTIKEL.

**S2 — "DEG-DEGAN" ANGKA → 80-200 char**
"[ANGKA SPESIFIK A] vs [ANGKA SPESIFIK B]. [IMPLIKASI: artinya...]. [PERTANYAAN: siapa paling kena?]"
Contoh: "Pelita Air punya 3.200 karyawan. Garuda punya 11.000. Gabung jadi satu, artinya ada yang harus efisiensi. Siapa yang paling deg-degan?"
KENAPA WORKS: angka bikin konkret, pertanyaan bikin lo mikir + pengen swipe.

**S3 — "TAPI" KONTRADIKSI → 80-200 char**
"[ALASAN RESMI / KATA PIHAK A]. Tapi [REALITAS BURUK ke orang kecil]. [BUKTI HISTORIS/ANGKA]."
Contoh: "Alasannya biar penerbangan nasional lebih kuat. Tapi efisiensi artinya PHK atau gaji dipotong. Garuda aja tahun lalu tutup rute dan potong bonus."
KENAPA WORKS: official story vs realita = ketegangan. Orang pengen komen belain salah satu sisi.

**S4 — "SIAPA KENA" SPESIFIK → 80-200 char**
"Sebut profesi/jabatan KONKRET dari artikel. [PIHAK A: sebut 2-3 profesi] paling kena. [PIHAK B: profesi yang aman]."
Contoh: "Yang paling kena petugas check-in, mekanik, dan pilot. Yang relatif aman teknisi IT dan manajemen."
KENAPA WORKS: super konkret, bikin pembaca langsung ngecek "gue termasuk yang mana?"

**S5 — "BISA NAIK, BISA TURUN" → 80-200 char**
"[VARIABEL YANG BERUBAH: harga/biaya/gaji]. Bisa [skenario buruk] kalau [kondisi]. Tapi bisa juga [skenario baik] kalau [kondisi lain]."
Contoh: "Harga tiket? Bisa naik kalau Garuda jadi satu-satunya pemain besar. Tapi bisa juga turun kalau efisiensi beneran berhasil."
KENAPA WORKS: gak sok tahu, kasih dua kemungkinan → orang komen pilih sisi + debat.

**S6 — "LO SIAPA?" CTA → 80-250 char**
"[PERTANYAAN SPESIFIK ke niche audience]. Atau [PERTANYAAN ALTERNATIF ke audience lebih luas]. [URL SUMBER]"
Contoh: "Lo kerja di maskapai BUMN? Cerita dong gimana suasana kantor sekarang. Atau lo sering terbang, udah ngerasain perubahan harga? [URL]"
KENAPA WORKS: CTA spesifik + low barrier. Bikin yang ngerasa "ini gue banget" langsung komen. URL di S6, bukan di S1.

## SETIAP SLIDE — ATURAN TEKNIS
- 1-2 kalimat per slide. Pendek = gampang dibaca di HP.
- S1: 80-140 char. S2-S6: 80-250 char.
- SETIAP slide harus ada KONTRAS: official vs realita, kelompok A vs B, dulu vs sekarang, angka vs dampak.
- Buka fakta BARU tiap slide. JANGAN ulang isi slide sebelumnya.

## EMPATI — LENSA RAKYAT KECIL
Identifikasi siapa paling dirugikan: buruh, petani, nelayan, pedagang, ibu RT, pekerja informal, konsumen akhir.
Tulis dampak dalam bahasa dompet: "berarti lo keluar RpX lebih tiap bulan", "pekerja kehilangan RpY per tahun".
JANGAN dari sudut pemerintah/korporasi. Pembaca harus ngerasa: "ini gue banget", "ini duit gue".

## IMAGE CONTEXT
Gambar S1 adalah backdrop — JANGAN deskripsi gambar. Gambar = pemicu opini. Langsung ke kontradiksi artikel.

## OUTPUT
JSON valid:
{"status":"success","angle":"satu kalimat sudut pandang","post_1":"...","post_2":"...","post_3":"...","post_4":"...","post_5":"...","post_6":"..."}
"""

REVISION_PROMPT = """PERBAIKI HANYA field yang disebut di bawah. JANGAN ubah field lain. Balas JSON lengkap dengan field yang sudah diperbaiki.

Issues: {revision_notes}"""

def build_user_prompt(article):
    """Build user prompt with article content, image context, and summarized body."""
    title = article.get("title", "")
    body = article.get("body", "")
    url = article.get("url", "")
    source = article.get("source", "")
    image_hint = article.get("image_hint", "")

    # Summarize body: keep lead + key paragraphs with numbers/entities
    short_body = body
    if len(body) > 1500:
        paras = [p.strip() for p in body.split("\n") if len(p.strip()) > 40]
        key_paras = [paras[0]] if paras else []  # lead
        for p in paras[1:]:
            if len(" ".join(key_paras)) > 1500:
                break
            # Keep paragraphs with numbers, entities, or action words
            if any(c.isdigit() for c in p) or any(w in p.lower() for w in (
                "rp", "us$", "juta", "miliar", "triliun", "persen", "%",
                "menteri", "presiden", "gubernur", "direktur", "bank", "bi",
                "pemerintah", "kebijakan", "anggaran", "subsidi", "pajak",
                "buruh", "pekerja", "harga", "naik", "turun")):
                key_paras.append(p)
        short_body = "\n".join(key_paras) if key_paras else body[:1500]

    parts = [
        f"**Judul:** {title}",
        f"**Sumber:** {source}",
        f"**URL:** {url}",
    ]
    # Pattern-specific hook instruction
    pattern = article.get("pattern", "")
    pattern_label = article.get("pattern_label", "")
    if pattern and pattern_label:
        pattern_hooks = {
            "DOMPET": "Fokus: dampak langsung ke kantong rakyat. S1 hook: harga/tarif/biaya yang naik/turun.",
            "KORUPSI": "Fokus: siapa tersangka, berapa kerugian negara, irony pejabat. S1 hook: angka kerugian + ironic twist.",
            "KEBIJAKAN": "Fokus: aturan baru — siapa diuntungkan, siapa dirugikan. S1 hook: kontradiksi kebijakan vs realita.",
            "PROYEK": "Fokus: nilai proyek, siapa dapat kontrak, dampak ke daerah. S1 hook: angka investasi + pertanyaan keberpihakan.",
            "PASAR": "Fokus: pergerakan pasar, saham, rupiah. S1 hook: angka shock + siapa paling kena.",
        }
        hook_hint = pattern_hooks.get(pattern, "")
        if hook_hint:
            parts.append(f"**Pattern:** {pattern_label} — {hook_hint}")
    if image_hint:
        parts.append(f"**Backdrop visual S1:** {image_hint} — jangan deskripsi, langsung ke OPINI")
    recent = article.get("recent_openings", [])
    if recent:
        parts.append("")
        parts.append("**5 post terakhir (HINDARI kemiripan angle/bahasa/pola):**")
        for i, opening in enumerate(recent, 1):
            parts.append(f"  {i}. {opening}")
    parts.extend([
        "",
        "**Isi Artikel:**",
        short_body,
        "",
        "⚠️ INTERNAL: Ekstrak fakta (angka, nama, lembaga, tanggal) dari body. Lalu tulis 6 post HANYA dari fakta yang ada. Kalau gak cukup -> insufficient_evidence. Output HANYA JSON — gak ada teks lain.",
    ])
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
        # Min length — S1 is compact; body slides need enough context.
        min_len = 50 if i == 6 else 80
        if len(p) < min_len:
            warnings.append(f"{k}: too short ({len(p)} chars, min {min_len})")
        if i == 1 and len(p) > 140:
            warnings.append(f"{k}: too long ({len(p)} chars, max 140)")
        # S1 and S6 are intentionally one sentence; S2-S5 need at least two.
        if i not in (1, 6):
            sent_count = len([c for c in p if c in ".!?"])
            if sent_count < 2:
                warnings.append(f"{k}: only {sent_count} sentences")
        if i == 1:
            sent_count = len([c for c in p if c in ".!?"])
            if sent_count > 2:
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
            r'(?:Rp\s*)?\d+(?:[.,]\d+)?\s*(?:triliun|miliar|juta|ribu|%|persen)',
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
        "akan", "bakal", "berpotensi", "diperkirakan", "diprediksi",
        "menyebabkan", "menyebab", "memicu", "membuat", "bikin",
        "berdampak", "imbas", "mengakibatkan",
    )
    for key in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]:
        text = posts.get(key, "").lower()
        for marker in markers:
            if re.search(rf"\b{re.escape(marker)}\b", text) and not re.search(rf"\b{re.escape(marker)}\b", source):
                issues.append(f"{key}: unsupported claim marker '{marker}'")
                break
    return issues


def _voice_warnings(posts):
    """Flag synthetic/report-template phrasing for prompt revision, not rejection."""
    warnings = []
    patterns = r"\b(?:gua|gw|lu|lo)\b|(?:^|[.!?]\s*)(?:fakta|aturan bilang|pemerintah bilang|yang perlu dicatat|perlu diketahui|artinya)\s*:"
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
    """Generate 6 posts from article. Returns (data, error)."""
    user = build_user_prompt(article)
    for attempt in range(1, 3):
        content, error = _call_llm(SYSTEM_PROMPT, user, max_retries=2)
        if error:
            log.warning(f"  LLM attempt {attempt}/2 — {error[:80]}")
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
        style_warnings = deterministic_validate(posts)
        noun_warnings = _validate_proper_nouns(posts, article["body"])
        missing = [f"{k}: empty" for k, v in posts.items() if not v.strip()]
        claim_warnings = _validate_claim_markers(posts, article["body"])
        voice_warnings = _voice_warnings(posts)
        warnings = missing + _validate_numbers(posts, article["body"]) + _validate_years(posts, article["body"]) + noun_warnings
        # Viral driver: S1 must have concrete tension marker or trigger revision.
        s1 = posts.get("post_1", "").lower()
        viral_markers = ["tapi", "padahal", "sementara", "malah", "naik", "turun",
                         "dipotong", "ditambah", "dialihkan", "ditetapkan", "berlaku",
                         "putusan", "wajib", "hingga", "mulai"]
        if not any(m in s1 for m in viral_markers):
            warnings.append("S1: no concrete viral driver — add contrast/tension marker")
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
                    style_w2 = deterministic_validate(p2)
                    noun_w2 = _validate_proper_nouns(p2, article["body"])
                    w2 = [f"{k}: empty" for k, v in p2.items() if not v.strip()]
                    w2.extend(_validate_numbers(p2, article["body"]))
                    w2.extend(_validate_years(p2, article["body"]))
                    w2.extend(noun_w2)
                    claim_w2 = _validate_claim_markers(p2, article["body"])
                    voice_w2 = _voice_warnings(p2)
                    if style_w2 or claim_w2 or voice_w2:
                        log.info(f"  Soft style/claim warnings after revision: {style_w2 + claim_w2 + voice_w2}")
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
        # Ensure S6 ends with article URL
        s6 = posts.get("post_6", "")
        article_url = article.get("url", "")
        if article_url:
            # Replace literal [URL] placeholder with the real URL.
            s6 = re.sub(r"\[URL\]", article_url, s6, flags=re.IGNORECASE)
            if article_url not in s6:
                s6 = s6.rstrip() + "\n\n" + article_url
            posts["post_6"] = s6
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

def post_to_threads(article_title, posts, image_url=None, pov_image_url=None):
    """Post chain to Threads via v1.0 Graph API. Slide 1 = article image, slide 7 = POV image."""
    if not THREADS_TOKEN or not THREADS_USER_ID:
        log.error("No THREADS_ACCESS_TOKEN or THREADS_USER_ID")
        return None
    if DRY_RUN:
        log.info("DRY RUN — skipping post")
        return None
    uid = THREADS_USER_ID
    published_ids = []
    last_post_id = None
    image_used = False
    slide_keys = sorted([k for k in posts if k.startswith("post_")], key=lambda x: int(x.split("_")[1]))
    for key in slide_keys:
        text = posts.get(key, "")
        if not text:
            continue
        i = int(key.split("_")[1])
        is_first = (i == 1)
        is_last = (i == 7)
        use_image = (is_first and image_url and not image_used) or (is_last and pov_image_url)
        data = {"user_id": uid, "media_type": "IMAGE" if use_image else "TEXT",
                "text": text, "access_token": THREADS_TOKEN}
        if use_image:
            data["image_url"] = pov_image_url if is_last else image_url
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
        log.info(f"  {key} {'IMAGE' if use_image else 'TEXT'} → {post_id}")
        time.sleep(2)
    return {"post_ids": published_ids, "media_ids": published_ids}

# ══════════════════════════════════════════════
#   MAIN
# ══════════════════════════════════════════════

def main():
    data = load_data()
    posted_urls = {t.get("article_url", t.get("title", "")) for t in data.get("topics", [])}
    recent_topics = data.get("topics", [])

    # Step 1: Editor may lock one vetted article for the next scheduled run.
    article = body = og_image = None
    article = load_prepared_article(posted_urls)
    if article:
        body, og_image = article["body"], article["og_image"]
        article["image_hint"] = _image_hint(og_image) if og_image else ""
        log.info(f"Prepared article: {article['title']}")
        articles = []
    else:
        log.info("Scraping economy sources...")
        articles = scrape_all()
        log.info(f"  Got {len(articles)} raw articles")

    # Step 2: Search ranked pool. Like Pressbox, title ranks; body decides eligibility.
    skipped_urls = set()
    candidate_limit = len(articles) if not article else 0
    for _ in range(candidate_limit):
        candidate = _pick_article(articles, posted_urls | skipped_urls)
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
        title_lower = candidate["title"].lower()
        if not any(sig in title_lower for sig in ECONOMY_TITLE_SIGNALS):
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
            article["pattern_label"] = ECONOMY_PATTERNS[pattern_name]["label"]
            log.info(f"  Body: {len(body)} chars | Pattern: {pattern_name} ({ECONOMY_PATTERNS[pattern_name]['label']}, confidence={pattern_confidence:.2f})")
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

    # Step 5: Generate (with retry on next candidate if hallucination fails)
    log.info("Generating thread...")
    recent_openings = data.get("recent_content", {}).get("openings", [])
    if recent_openings:
        article["recent_openings"] = recent_openings[:5]
    result, error = generate_thread(article)
    if error:
        log.error(f"Generation failed: {error}")
        skipped_urls.add(article["url"])
        # Try next-best candidate from remaining pool (fast retry)
        retry_article = None
        for _ in range(candidate_limit):
            retry_article = _pick_article(articles, posted_urls | skipped_urls)
            if retry_article is None:
                break
            log.info(f"  Retry candidate: {retry_article['title'][:80]}")
            # Quick gate on retry candidate
            retry_body, retry_img, retry_ts = _fetch_article_body(retry_article["url"])
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
            if recent_openings:
                retry_article["recent_openings"] = recent_openings[:5]
            result, error = generate_thread(retry_article)
            if error:
                log.error(f"Retry generation also failed: {error}")
                skipped_urls.add(retry_article["url"])
                continue
            article = retry_article  # update article ref for downstream use
            break
        if not result:
            log.error("No valid generation after retry")
            return

    if not result:
        log.error("Generation failed: no valid result")
        return

    posts = result["posts"]
    for i in range(1, 7):
        first_line = posts.get(f"post_{i}", "").split("\n")[0][:80] or "(empty)"
        log.info(f"  S{i}: {first_line}")

    # ── Slide 7: POV affiliate (rotate per post) ──
    _pov_path = BASE / "pov_affiliate.json"
    _pov_image_url = None
    try:
        _pov_data = json.loads(_pov_path.read_text())
        _povs = _pov_data.get("povs", [])
        _idx = _pov_data.get("current_index", 0)
        if _povs:
            _pov_text = _povs[_idx % len(_povs)]
            _pov_link = _pov_data.get("link", "")
            posts["post_7"] = _pov_text + ("\n\n" + _pov_link if _pov_link else "")
            if not DRY_RUN:
                _pov_data["current_index"] = (_idx + 1) % len(_povs)
                _pov_path.write_text(json.dumps(_pov_data, indent=2))
            log.info(f"  S7: {_pov_text.split(chr(10))[0][:60]}...")
        _pov_image_url = _pov_data.get("image_url", "") or None
    except Exception as _e:
        log.warning(f"POV affiliate slide failed: {_e}")

    # Step 6: Post
    if not DRY_RUN:
        pub = post_to_threads(article["title"], posts, image_url=image_url, pov_image_url=_pov_image_url)
        if pub and pub.get("post_ids"):
            log.info(f"Posted: {pub['post_ids'][0]}")
            topic = {
                "title": article["title"],
                "article_url": article["url"],
                "article_source": article["source"],
                "angle": result.get("angle", ""),
                "post_id": pub["post_ids"][0],
                "media_id": pub["media_ids"][0] if pub.get("media_ids") else None,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+07:00"),
                "likes": None,
                "replies": None,
            }
            data.setdefault("topics", []).insert(0, topic)
            rc = data.setdefault("recent_content", {})
            rc.setdefault("openings", []).insert(0, posts.get("post_1", "")[:100])
            rc.setdefault("ctas", []).insert(0, posts.get("post_6", "")[:100])
            for k in ["openings", "ctas"]:
                rc[k] = rc[k][:10]
            save_data(data)
            PREPARED_ARTICLE_FILE.unlink(missing_ok=True)
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
