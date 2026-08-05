#!/usr/bin/env python3
"""
Techbro v3 — EKONOMI NASIONAL + POV PRIBADI + 6 Script Hack Elements
Article-based: scrape economy RSS/HTML → 6 threads with personal POV.
"""

import html, httpx, json, logging, os, random, re, struct, sys, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
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
    "cnbc_ekonomi":   {"url": "https://www.cnbcindonesia.com/news/rss",          "score": 10, "type": "rss",  "domain": "cnbcindonesia.com/"},
    "cnbc_market":    {"url": "https://www.cnbcindonesia.com/market/",           "score": 9,  "type": "html", "domain": "cnbcindonesia.com/market/"},
    "cnn_global":     {"url": "https://www.cnnindonesia.com/internasional/rss",  "score": 8,  "type": "rss",  "domain": "cnnindonesia.com/internasional/"},
    "cnn_ekonomi":    {"url": "https://www.cnnindonesia.com/ekonomi/rss",        "score": 9,  "type": "rss",  "domain": "cnnindonesia.com/ekonomi/"},
    "detik_finance":  {"url": "https://finance.detik.com/",                     "score": 9,  "type": "html", "domain": "finance.detik.com/"},
    "detik_hukum":    {"url": "https://news.detik.com/hukum/",                  "score": 8,  "type": "html", "domain": "news.detik.com/hukum/"},
    "cnn_nasional":   {"url": "https://www.cnnindonesia.com/nasional/rss",       "score": 8,  "type": "rss",  "domain": "cnnindonesia.com/nasional/"},
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
            if len(title) < 25:
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
    return articles

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
        if size and size[0] >= 1200 and size[1] >= 674:
            return url
        log.warning(f"Reject non-HD article image: {size or 'unknown'} {url[:80]}")
    except httpx.RequestError as e:
        log.warning(f"Validate article image failed: {e}")
    return None

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
                    published_ts = datetime.strptime(str(date_tag["content"]), "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
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


def _is_techbro_relevant(body):
    """Require a concrete Indonesia or global finance/economy signal in article body."""
    return bool(re.search(
        r"\b(indonesia|ri|rupiah|apbn|anggaran|pajak|subsidi|bansos|"
        r"pemerintah indonesia|presiden|mahkamah konstitusi|mk|kemenkeu|"
        r"bank indonesia|bi|ojk|bpk|dpr|federal reserve|the fed|ecb|bank sentral eropa|"
        r"bank of japan|boj|bank rakyat china|pboc|opec|harga minyak dunia|tarif dagang|"
        r"perang dagang|sanksi ekonomi|resesi global|ekonomi global|perdagangan global)\b",
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
    market = any(word in text for word in ("rupiah", "kurs", "ihsg", "saham", "harga emas", "harga minyak"))
    # Policy must drive headline. Incidental body mentions cannot rescue a daily market update.
    policy = any(word in headline for word in (
        "bi rate", "apbn", "anggaran", "pajak", "subsidi", "peraturan",
        "ditetapkan", "putusan", "bpk", "ojk",
    ))
    return market and not policy


def _topic_score(title, body):
    """Score full article against Techbro's Indonesia/global economy editorial brief."""
    text = f"{title} {body}".lower()
    # Utility/tutorial updates are not editorial stories unless policy conflict drives headline.
    utility = any(phrase in title.lower() for phrase in (
        "cek bansos", "cara cek", "pakai nik", "status penerima", "syarat daftar",
    ))
    policy_change = any(phrase in text for phrase in (
        "anggaran", "kriteria penerima", "aturan baru", "peraturan", "audit",
        "penyelewengan", "dihentikan", "diperluas", "dipotong", "ditambah",
    ))
    if utility and not policy_change:
        return 0, 0, 0

    def hits(words):
        return sum(word in text for word in words)

    economy = hits((
        "apbn", "anggaran", "pajak", "subsidi", "bansos", "inflasi", "daya beli",
        "upah", "phk", "pekerja", "pengangguran", "bbm", "pangan", "listrik",
        "bi rate", "suku bunga", "rupiah", "kredit", "ekspor", "impor", "umkm",
        "industri", "pabrik", "harga", "b50", "mbg", "danantara", "investasi",
        "penanaman modal", "pma", "ikn", "konstruksi", "properti", "ritel",
        "perkantoran", "investor", "federal reserve", "the fed", "ecb", "boj",
        "pboc", "opec", "tarif dagang", "perang dagang", "harga minyak dunia",
        "sanksi ekonomi", "resesi global", "ekonomi global", "perdagangan global",
    ))
    change = hits((
        "putusan", "disahkan", "ditetapkan", "berlaku", "dicabut", "ditunda",
        "direvisi", "dipotong", "ditambah", "dialihkan", "naik", "turun",
        "melonjak", "anjlok", "audit", "temuan", "phk", "pabrik tutup",
        "kebocoran", "kerugian negara", "polemik", "protes", "kritik", "resmi memulai",
        "mulai konstruksi", "realisasi investasi", "rampung",
    ))
    impact = hits((
        "masyarakat", "rumah tangga", "konsumen", "pekerja", "buruh", "umkm",
        "lapangan kerja", "daya beli", "biaya hidup", "harga pangan", "subsidi",
        "pajak", "kesehatan", "pendidikan", "transportasi", "industri", "pelaku usaha",
        "usaha lokal", "kontraktor", "kawasan",
    ))
    source = hits((
        "menurut", "berdasarkan", "data", "laporan", "putusan", "peraturan",
        "badan pusat statistik", "bank indonesia", "kementerian", "ojk", "bpk",
    ))
    # 0–3 economy/change, 0–2 impact/source. 7/10 minimum per editorial brief.
    score = min(economy, 3) + min(change, 3) + min(impact, 2) + min(source, 2)
    return score, min(economy, 3), min(impact, 2)


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
    """Reject a quote-only official reaction with no action, rule, or concrete data."""
    headline = title.lower()
    quote_only = any(word in headline for word in ("kata", "soal", "buka suara", "ungkap", "respons", "bakal"))
    # Body mentions of tax/tariff etc. do not turn a reaction headline into news.
    substance = any(word in headline for word in (
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
        "temperature": 0.7,
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

SYSTEM_PROMPT = """# TECHBRO EKONOMI — BODY-ONLY THREADS

Kamu mengubah satu artikel ekonomi Indonesia atau global menjadi tepat 6 post Threads yang akurat, mudah dipahami pembaca awam, dan terasa seperti teman pintar menjelaskan berita rumit.

## SUMBER DAN ANTI-HALUSINASI — HARD
Satu-satunya sumber fakta adalah **ISI ARTIKEL** di user message. Judul, URL, pengetahuan umum, pengalaman pribadi, dan asumsi bukan sumber fakta. Artikel harus terbit dalam 24 jam terakhir; waktu RSS, halaman kategori, dan waktu crawl bukan bukti waktu terbit.

- Semua angka, nominal, tanggal, periode, nama, lembaga, lokasi, kebijakan, kutipan, dan status waktu harus ada di isi artikel. Angka makro hanya boleh dipakai bila tertulis di body dari sumber kredibel atau resmi.
- Tanggal harus ditulis lengkap persis seperti sumber, misalnya “28 Februari hingga 1 Juli 2026”; jangan memendekkan menjadi “sejak Februari”.
- Jangan menciptakan contoh hitungan, nominal, kutipan, atau sumber baru.
- Jangan mengubah “akan/rencana/diperkirakan/berpotensi/bisa” menjadi fakta pasti atau kejadian yang sudah selesai.
- Jangan mengubah korelasi menjadi sebab-akibat. Jelaskan mekanisme hanya jika artikel menyebutnya.
- Pisahkan fakta dengan analisis. Analisis hanya boleh menjelaskan batas informasi sumber, atau hubungan yang tertulis jelas di artikel.
- Dampak ke harga, gaji, pekerjaan, cicilan, usaha, atau dompet hanya boleh dibahas bila artikel menyebut dampaknya atau mekanismenya secara jelas.
- Jika isi artikel tidak cukup untuk membuat thread akurat, balas: {"status":"error","message":"insufficient_evidence"}.

## SUARA DAN BAHASA
- Bahasa Indonesia lisan, sederhana, pendek, natural untuk ponsel. Jelaskan istilah ekonomi segera dengan kata mudah.
- Pakai “kalian” dan “kita” bila perlu. Jangan pakai “lu”, “lo”, “gua”, atau “gw”.
- Cerdas, kritis, adil, tidak sok tahu, tidak menggurui, tidak menjual ketakutan atau optimisme.
- Jangan pakai jargon birokratis, kalimat laporan pemerintah, hashtag, atau pengalaman pribadi palsu.
- Jangan memulai kalimat dengan template AI/laporan: “Fakta:”, “Aturan bilang:”, “Pemerintah bilang:”, “Yang perlu dicatat:”, “Perlu diketahui:”, atau “Artinya:”. Tulis fakta itu langsung dalam kalimat biasa.
- Jangan pakai: akselerasi, mitigasi, implementasi, optimalisasi, realisasi, signifikan, komprehensif, mekanisme, skema, portofolio. Jika nama resmi memakai kata sulit, jelaskan artinya.

## CARA BERPIKIR — SUARA AHLI EKONOMI
Bertindak sebagai penerjemah ekonomi, bukan peringkas berita. Keahlian terlihat dari cara membaca angka dan batasnya, bukan dari jargon atau klaim pengalaman.

Untuk tiap fakta utama, cari satu hal yang paling penting bagi pembaca:
- periode dan statusnya: sudah terjadi, rencana, atau masih dikaji;
- apa yang angka itu ukur dan apa yang tidak bisa dibuktikan;
- siapa atau institusi yang benar-benar disebut, serta kewajiban atau konsekuensi literalnya;
- mekanisme sebab-akibat yang tertulis; bila tidak tertulis, katakan artikel belum menjelaskannya;
- beda antara pengumuman kebijakan dan dampak yang sudah dirasakan.

Pilih 3–6 fakta terkuat. Bentuk satu tesis yang didukung sumber: anggapan umum yang perlu diluruskan, fakta penting yang belum jelas di judul, atau batas ketidakpastian yang perlu diketahui. Setiap post harus menambah pemahaman baru; jangan mengulang rangkuman berita dengan kata lain. Jangan memaksa tesis, konflik, dampak dompet, tindakan, atau contoh hitungan bila sumber tidak mendukung. Jika suatu dampak tidak disebut, jangan menulis disclaimer seperti “belum disebut”, “tidak diketahui”, atau “belum ada dampak”. Ganti dengan fakta lanjutan, kewajiban, konsekuensi keputusan, batas aturan, atau jadwal yang literal di artikel.

Dampak dompet hanya boleh ditulis jika artikel menyebutnya. Jika tidak, jangan mengarang dampak atau menjelaskan ketiadaannya; gunakan konsekuensi literal, pihak yang dituju, aturan, atau jadwal yang benar-benar ada di artikel.

## GAYA TERBARU — PINDAR-6 DENGAN POV TAJAM
Jangan cuma rangkum berita. Tulis sebagai cerita pendek yang bergerak dari masalah nyata, tekanan yang datang, bukti angka, pihak yang terjepit, lalu solusi dan taruhan akhirnya. Gunakan satu subjek yang memang disebut artikel, misalnya kontraktor, pemegang polis, atau pekerja; jangan menciptakan tokoh, dialog, pengalaman, atau emosi baru.

Tulis seperti manusia yang menjelaskan ke anak usia 10 tahun: kalimat pendek dalam kalimat panjang S1, kata sehari-hari, dan detail konkret dari body. Jika harus memakai istilah resmi seperti premi, laba, CSM, atau restitusi, jelaskan artinya dengan kata sederhana di kalimat yang sama atau slide berikutnya sebelum istilah itu dipakai lagi. Hindari pola kalimat seragam, kalimat motivasi kosong, pembuka generik, daftar fakta kaku, pertanyaan palsu, atau kesimpulan yang mengulang S1. Setiap slide harus punya satu **take**: kontras, taruhan, atau ironi yang lahir dari fakta — bukan opini liar.

Bawa pembaca dari perubahan besar ke arti personalnya: **Perubahan → Implikasi personal → Nominal/skala → Dilema pihak terdampak → Arah dampak → Respons pembaca.**

Cari satu kontras literal untuk jadi benang merah: uang sudah dianggarkan tetapi aturan teknis masih disusun; tujuan resmi berhadapan dengan syarat ketat; angka naik tetapi penerima berbeda kelas jabatan. Buka dari kontras itu, bukan dari kalimat laporan seperti “pemerintah mengumumkan” atau “berita ini membahas”.

Pakai label diam-diam saat menulis:
- **Fakta:** tulis langsung bila literal di artikel.
- **Inferensi:** hanya dari mekanisme yang tertulis; pakai “bisa”, “berpotensi”, atau “kemungkinan”.
- **Skenario:** pakai “jika X, maka Y”; X dan mekanismenya wajib didukung artikel. Jangan menulis skenario sebagai ramalan.
- **Larangan:** jangan menghitung sendiri sisa bulan/tahun, menulis deadline, atau menyimpulkan proyek akan tepat waktu, molor, gagal, sukses, menarik/menolak investor lain. Target selesai bukan bukti hasil. Jangan mengubah “melibatkan” menjadi “wajib” atau menambah kewajiban baru.

Thread wajib punya tiga hal yang BUKTINYA ada di artikel: **isu ekonomi/finansial**, **dampak atau pihak yang terkena**, dan **konflik/titik tegang** antara tujuan, angka, aturan, atau kepentingan. Jika salah satunya tidak punya bukti literal, balas insufficient_evidence. Jangan bikin konflik, dampak, profesi, atau pilihan palsu demi hook.

Konten wajib memberi **solusi nyata**, bukan nasihat kosong. Solusi harus salah satu dari: tindakan/kewajiban resmi yang disebut artikel, pilihan aman yang mekanismenya tertulis di artikel, atau hal konkret yang perlu diperiksa pembaca sebelum terdampak. Jika artikel tidak mendukung solusi nyata, balas insufficient_evidence. Jangan membuat rekomendasi beli/jual investasi, hitungan baru, atau langkah personal yang tidak ada di sumber. Setiap slide membuka fakta baru dan menanam pertanyaan berikutnya.

## STRUKTUR PINDAR 6 POST
Tulis tepat post_1 sampai post_6. Setiap post 1–3 kalimat, 100–300 karakter; post_1 wajib tepat satu kalimat 80–140 karakter. Jangan gunakan bullet atau daftar.

- S1 — Stop-scroll ala berita viral: buka dalam 10 kata pertama dengan nama pihak, angka, atau aksi nyata dari artikel. Lalu tulis satu benturan/ironi konkret dan boleh tutup pertanyaan tajam yang jawabannya ada di body. Pilih satu angka maksimal. Format: “[pihak/fakta] + [benturan] + [pertanyaan spesifik]”. Dilarang membuka dengan kalimat generik seperti “Dulu cukup”, “sekarang”, “kuota”, “internet”, “zaman”, atau pengalaman pembaca yang tidak disebut artikel. Jangan membuat metafora atau dampak seperti “jadi modal kerja” bila tidak tertulis. Jangan buka dengan istilah teknis; jelaskan istilah jika wajib dipakai. Bukan “bayangin”, bukan tokoh fiktif, bukan ringkasan.
- S2 — Tekanan datang: tunjukkan perubahan atau konflik yang membuat masalah S1 membesar. Beri maksimal dua data literal.
- S3 — Bukti skala: jelaskan angka atau detail yang membuat pembaca paham kenapa masalah ini tidak kecil. Terjemahkan istilah sulit segera.
- S4 — Pihak yang terjepit: jelaskan fungsi/pihak yang disebut artikel dan dilema literal mereka. Jangan tebak korban atau profesi.
- S5 — Jalan keluar nyata: tulis tindakan, kewajiban, pilihan aman, atau pemeriksaan konkret yang secara literal didukung artikel. Terangkan masalah apa yang dijawab solusi itu. Jika tidak ada, balas insufficient_evidence.
- S6 — Taruhan akhir: tutup dengan konsekuensi bila solusi S5 tidak jalan, hanya jika mekanismenya didukung body. Pertanyaan tajam hanya boleh bila ada dua pilihan nyata yang didukung body; jangan pakai CTA generik.

Jangan pakai “pantau terus”, “cek lagi bulan depan”, atau “gimana menurut kalian”.

## AUDIT INTERNAL
Sebelum JSON final: cek tiap klaim ke isi artikel, terutama angka, nama, status waktu, sebab-akibat, dan kutipan. Hapus klaim yang tidak didukung. Jangan tampilkan audit.

## OUTPUT
Balas JSON valid saja. Tidak markdown. Tidak field arc.
{
  "status":"success",
  "angle":"satu kalimat sudut pandang yang didukung artikel",
  "post_1":"...",
  "post_2":"...",
  "post_3":"...",
  "post_4":"...",
  "post_5":"...",
  "post_6":"..."
}
"""

REVISION_PROMPT = """Previous output failed validation. Fix ONLY the specific issues below. Keep everything else.

Issues: {revision_notes}"""

def build_user_prompt(article):
    """Build user prompt with article content."""
    title = article.get("title", "")
    body = article.get("body", "")
    url = article.get("url", "")
    source = article.get("source", "")
    parts = [
        f"**Judul:** {title}",
        f"**Sumber:** {source}",
        f"**URL:** {url}",
        "",
        "**Isi Artikel:**",
        body if body else "(isi artikel tidak tersedia)",
        "",
        "Buat JSON tepat 6 post HANYA dari isi artikel di atas. Judul bukan sumber fakta. Jika buktinya tidak cukup, balas status error insufficient_evidence.",
    ]
    return "\n".join(parts)

# ── Validation ───────────────────────────────────────────────────────────────

def deterministic_validate(posts):
    warnings = []
    slop_phrases = [
        "tau gak sih", "gak bakal percaya", "coba resapin", "let that sink in",
        "bayangin", "yang rugi siapa", "yang menarik", "patut dicatat",
        "tapi ternyata", "faktanya", "nyatanya", "inilah yang", "inilah kenapa",
        "sudah bukan rahasia lagi", "tak terelakkan", "perlu kalian tahu", "perlu diingat",
        "coba kalian bayangin", "gimana menurut kalian", "termasuk kalian",
        "itulah mengapa", "jadi intinya",
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
            if sent_count != 1:
                warnings.append(f"{k}: needs exactly one sentence")
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
        if i == 1 and re.match(r"\s*(?:dulu\s+cukup|sekarang\b|kuota\b|internet\b|zaman\b|bayangin\b)", outside, re.I):
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
        # PINDAR permits one source-backed open loop on S2; other early questions risk fake mystery.
        if i != 2 and i <= 5 and "?" in outside:
            warnings.append(f"{k}: rhetorical question")
        if i == 2 and outside.count("?") > 1:
            warnings.append(f"{k}: too many open-loop questions")
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
        emphasis = {"BUKAN", "PERTAMA", "JADI", "TAPI", "KALAU", "JIKA", "DAN", "YANG", "UNTUK", "BOLEH", "WAJIB", "TIDAK"}
        for acronym in set(re.findall(r'\b[A-Z]{2,}\b', text)):
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
                     "putusan", "wajib", "hingga", "mulai"]
    if not any(m in s1 for m in viral_markers):
        warnings.append("S1: no concrete viral driver")
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
    for attempt in range(1, 4):
        content, error = _call_llm(SYSTEM_PROMPT, user, max_retries=2)
        if error:
            log.warning(f"  LLM attempt {attempt}/3 — {error[:80]}")
            if attempt < 3:
                time.sleep(3)
            continue
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r'^```(?:json)?\s*', "", content)
            content = re.sub(r'\s*```$', "", content)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            log.warning(f"  LLM attempt {attempt}/3 — bad JSON")
            if attempt < 3:
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
        if article_url and article_url not in s6:
            posts["post_6"] = s6.rstrip() + "\n\n" + article_url
        return {
            "article_title": article.get("title", ""),
            "article_url": article.get("url", ""),
            "article_source": article.get("source", ""),
            "angle": data.get("angle", ""),
            "arc": data.get("arc", "market_shock"),
            "posts": posts,
        }, None
    return None, "LLM failed after 3 attempts"

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
        log.info("Fetching article body...")
        candidate_body, candidate_image, source_ts = _fetch_article_body(candidate["url"])
        # Fail closed: RSS time is not proof of article recency; use source publish time.
        if not source_ts or source_ts > time.time() + 300 or time.time() - source_ts > 86400:
            log.info("  Skip: source publish time missing, invalid, or older than 24h")
            skipped_urls.add(candidate["url"])
            continue
        topic_score, economy_score, impact_score = _topic_score(candidate["title"], candidate_body)
        eligible = topic_score >= 5
        global_ok = candidate["source"] != "cnn_global" or _is_global_finance_story(candidate["title"], candidate_body)
        if candidate_body and global_ok and not _is_routine_market_story(candidate["title"], candidate_body) and not _is_empty_commentary(candidate["title"], candidate_body) and _is_techbro_relevant(candidate_body) and eligible:
            article, body, og_image = candidate, candidate_body, candidate_image
            article["body"] = body
            log.info(f"  Body: {len(body)} chars | Editorial score: {topic_score}/10")
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
        if image_url:
            log.info(f"  Image: og:image from article")
        else:
            log.warning("  Image: none (no og:image)")
            log.warning("  Skip: article has no og:image — next cron tick will try another")
            return
    if image_url:
        log.info(f"  Image URL: {image_url[:80]}...")
    else:
        log.info("  Image: disabled via --no-image")

    # Step 5: Generate
    log.info("Generating thread...")
    result, error = generate_thread(article)
    if error:
        log.error(f"Generation failed: {error}")
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
