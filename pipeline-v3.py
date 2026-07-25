#!/usr/bin/env python3
"""
Techbro v3 — EKONOMI NASIONAL + POV PRIBADI + 6 Script Hack Elements
Article-based: scrape economy RSS/HTML → 6 threads with personal POV.
"""

import html, httpx, json, logging, os, random, re, sys, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
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

# ── Env ───────────────────────────────────────────────────────────────────────

GRAPH = "https://graph.threads.net/v1.0"
THREADS_TOKEN = None
try:
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
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

KW = load_keywords()

# ── Economy Sources ──────────────────────────────────────────────────────────

SOURCES = {
    "cnbc_ekonomi":   {"url": "https://www.cnbcindonesia.com/news/rss",          "score": 10, "type": "rss",  "domain": "cnbcindonesia.com/"},
    "cnn_ekonomi":    {"url": "https://www.cnnindonesia.com/ekonomi/rss",        "score": 9,  "type": "rss",  "domain": "cnnindonesia.com/ekonomi/"},
    "money_kompas":   {"url": "https://money.kompas.com/",                      "score": 7,  "type": "html", "domain": "money.kompas.com/"},
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
            link = (item.findtext("link") or "").strip()
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
            if not title or not link:
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
            href = a_tag["href"].strip()
            if domain not in href:
                continue
            title = a_tag.get_text(strip=True)
            title = re.sub(r"\s+", " ", title).strip()
            if len(title) < 25:
                continue
            if href in seen:
                continue
            seen.add(href)
            articles.append({
                "title": title, "url": href.split("?")[0], "source": source,
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

    # Soft reject penalty (cancelled by sufficient economy signals)
    if signals >= 2:
        pass  # strong signals override soft reject
    else:
        for kw in SOFT_REJECT:
            if kw in tl:
                score -= 60
                break

    return (score, f"cats={categories_hit} sig={signals}")

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
    # Threshold: check against full weight (includes freshness, relevance, source quality)
    best = candidates[0]
    if best["_weight"] < SCORE_THRESHOLDS["process"]:
        log.warning(f"  Best article _weight {best['_weight']} below threshold {SCORE_THRESHOLDS['process']} — skipping")
        return None
    return best

# ── Article Body + Image ─────────────────────────────────────────────────────

def _fetch_article_body(url):
    """Fetch article HTML, extract clean text + og:image."""
    og_image = None
    text = ""
    try:
        code, raw = _http_get(url, timeout=15)
        if code != 200:
            return "", None
        soup = BeautifulSoup(raw, "html.parser")
        # og:image
        og_tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
        if og_tag and og_tag.get("content"):
            og_image = og_tag["content"]
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
            return "", og_image
        for tag in body_el.find_all(["script", "style", "nav", "aside", "footer", "form"]):
            tag.decompose()
        paras = []
        for p in body_el.find_all("p"):
            txt = p.get_text(separator=" ", strip=True)
            if len(txt) > 20:
                paras.append(txt)
        text = "\n".join(paras)
        text = text[:5000] if len(text) > 200 else ""
    except Exception as e:
        log.warning(f"Fetch body: {url[:60]} — {e}")
    return text, og_image

# ── POV Helpers ──────────────────────────────────────────────────────────────

def _convert_pov(text):
    """Normalize pronouns to gw/lu."""
    parts = re.split(r'("[^"]*")', text)
    for i, p in enumerate(parts):
        if i % 2 == 0:
            p = re.sub(r'\blo\b', 'lu', p)
            p = re.sub(r'\bkalian\b', 'lu', p)
            p = re.sub(r'\bkamu\b', 'lu', p)
            p = re.sub(r'\banda\b', 'lu', p)
            p = re.sub(r'\bgue\b', 'gw', p)
            p = re.sub(r'\bgua\b', 'gw', p)
            p = re.sub(r'\baku\b', 'gw', p)
            parts[i] = p
    return ''.join(parts)

def _format_sentence_blanks(text):
    """Clean text: strip em dash, then add blank lines between sentences."""
    # Strip em dash first — LLM ignores prompt ban
    s = text.replace('— ', ' ').replace('—', ' ')
    s = re.sub(r'[ \t]+', ' ', s)  # collapse horizontal whitespace only
    # Then insert blank lines between sentences
    s = re.sub(r'(?<=[.!?]) +', r'\n\n', s)
    s = re.sub(r'\n{3,}', '\n\n', s)
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
                    time.sleep(5)
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

SYSTEM_PROMPT = """# 6-SCRIPT HACK ELEMENTS — @ryanhadiii Ekonomi Engine

Kamu adalah content engine @ryanhadiii. Baca artikel ekonomi di bawah, lalu tulis 6 post Threads. Setiap thread HARUS nyambung dari S1 ke S6 kayak cerita ke temen di warung.

## ATURAN BAHASA — BIAR GAK KELIHATAN AI-GENERATED

**POV:** gw / lu. Gak pake: anda, kami, kita, masyarakat, kalian.
**Gaya:** Kalimat pendek. Fragment boleh. Nada cerita ke temen.
**Formal:** Gak pake: merupakan, terdapat, yakni, sehingga, maka, melaksanakan, dalam rangka.
**Data:** Angka, nama, persen — cuma kalo ADA di artikel. JANGAN ngarang.
**Panjang:** Maks 300 karakter per post.
**"Baru aja"** — cuma kalo kejadian maks 48 jam lalu. Kalo lebih: "Minggu ini...", "Belakangan ini...", "Ada perubahan baru soal...".
**Angka gede wajib dikonversi** di S2. Gak cuma "RpX triliun". "Setara 1.500 Avanza cuma kalo relevan", "gaji lo 500 tahun", "THR 40 kali". Pake asumsi jelas, dibulatin, jangan presisi palsu.

**LARANGAN — Gak boleh pake kata/frasa ini:**
emoji, hashtag, "tau gak sih", "gak bakal percaya", "coba resapin", "let that sink in", "bayangin", "coba lo bayangin", "yang rugi siapa", "yang menarik", "patut dicatat", "tapi ternyata", "faktanya", "nyatanya", "inilah yang", "inilah kenapa", "sudah bukan rahasia lagi", "tak terelakkan", "perlu lo tau", "perlu diingat", "gimana menurut lo", "termasuk lo", "itulah mengapa", "jadi intinya", "yang bikin... adalah...", "mulai dari... sampai..."
S1-S5: Gak boleh pertanyaan retoris. S6: maksi 1 CTA spesifik.

## KETEGANGAN CERITA — WAJIB minimal satu

Setiap thread butuh minimal satu dari:
- Kontradiksi / trade-off
- Pihak untung vs terdampak
- Janji vs pelaksanaan
- Angka besar vs hasil kecil
- Kebijakan vs dampak nyata

Kalo gak ada ketegangan yang kuat, artikel itu gak layak jadi thread.

## 7 ARC FORMAT — Pilih 1 berdasarkan isi artikel

**Arc 1 — MARKET SHOCK** (IHSG, rupiah, emas, minyak, suku bunga, kripto)
- S1: Pergerakan angka + penyebab/kontradiksi
- S2: Arti angka dalam kehidupan sehari-hari
- S3: Penyebab utama
- S4: Siapa terdampak / diuntungkan
- S5: Risiko / perkembangan berikutnya
- S6: "Lu udah siap kalo [skenario]?"

**Arc 2 — POLICY BOMB** (pajak, subsidi, tarif, aturan, APBN, BPJS)
- S1: "Baru aja" (kalo valid) + kebijakan + dampak dompet
- S2: Simulasi rupiah per orang/rumah tangga
- S3: Alasan pemerintah bikin kebijakan
- S4: Pihak paling terdampak
- S5: Celah, trade-off, konsekuensi
- S6: "Kalo pilihannya [A] atau [B], lu pilih mana?"

**Arc 3 — GLOBAL DOMINO** (AS, China, perang dagang, minyak dunia, The Fed)
- S1: Kejadian global + jalur dampak ke Indonesia
- S2: Dampak lokal dalam angka
- S3: Kenapa Indonesia ikut kena
- S4: Sektor/kelompok terdampak
- S5: Skenario berikutnya
- S6: "Lu paling khawatir dampaknya ke [objek spesifik] gak?"

**Arc 4 — DOMPET KEJEPIT** (inflasi, sembako, BBM, listrik, biaya hidup)
- S1: Harga kebutuhan + perubahan angka
- S2: Tambahan pengeluaran bulanan
- S3: Penyebab kenaikan
- S4: Perbandingan sama pertumbuhan gaji
- S5: Dampak ke daya beli
- S6: "Pengeluaran mana yang paling kerasa naik buat lu?"

**Arc 5 — JOBS UNDER PRESSURE** (PHK, upah, pabrik tutup, pengangguran)
- S1: Jumlah pekerja terdampak + kontradiksi
- S2: Konversi ke jumlah keluarga/kehilangan pendapatan
- S3: Penyebab bisnis/industri
- S4: Pekerjaan/daerah paling terdampak
- S5: Risiko lanjutan
- S6: "Kalo industri ini makin tertekan, lu paling takut efek yang mana?"

**Arc 6 — PUBLIC MONEY TRAIL** (korupsi, anggaran, pajak, BUMN, proyek pemerintah)
- S1: Nilai uang publik + tujuan awalnya
- S2: Konversi jadi layanan/bantuan publik
- S3: Alur kasus berdasarkan sumber
- S4: Pihak dan institusi terkait
- S5: Dampak ke negara/publik
- S6: "Menurut lu, hukuman paling masuk akal buat kasus sebesar ini apa?"

**Arc 7 — DEBT TRAP** (KPR, pinjol, paylater, cicilan, bunga kredit)
- S1: Perubahan bunga/jumlah utang
- S2: Simulasi cicilan bulanan
- S3: Penyebab perubahan
- S4: Kelompok paling rentan
- S5: Risiko gagal bayar/tekanan keuangan
- S6: "Kalo cicilan naik Rp[X], keuangan lu masih aman gak?"

## OUTPUT FORMAT

{
  "status": "success",
  "arc": "market_shock",
  "angle": "Satu kalimat angle thread ini",
  "post_1": "S1...",
  "post_2": "S2...",
  "post_3": "S3...",
  "post_4": "S4...",
  "post_5": "S5...",
  "post_6": "S6..."
}

Error:
{"status": "error", "message": "..."}
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
        body if body else "(gagal ambil teks — tulis berdasarkan judul doang)",
        "",
        "Buat 6 post Threads dengan struktur wajib di atas. POV pribadi (gw/lu). Data angka dari artikel.",
    ]
    return "\n".join(parts)

# ── Validation ───────────────────────────────────────────────────────────────

def deterministic_validate(posts):
    warnings = []
    slop_phrases = [
        "tau gak sih", "gak bakal percaya", "coba resapin", "let that sink in",
        "bayangin", "yang rugi siapa", "yang menarik", "patut dicatat",
        "tapi ternyata", "faktanya", "nyatanya", "inilah yang", "inilah kenapa",
        "sudah bukan rahasia lagi", "tak terelakkan", "perlu lo tau", "perlu diingat",
        "coba lo bayangin", "gimana menurut lo", "termasuk lo",
        "itulah mengapa", "jadi intinya",
    ]
    for i in range(1, 7):
        k = f"post_{i}"
        p = posts.get(k, "")
        if not p.strip():
            warnings.append(f"{k}: empty")
            continue
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
        # Check banned pronouns
        outside = re.sub(r'"[^"]*"', "", p)
        words = set(re.findall(r'\b[a-z]+\b', outside.lower()))
        for w in ["anda", "kalian", "kami", "kita", "aku"]:
            if w in words:
                warnings.append(f"{k}: banned '{w}'")
        # Check slop phrases
        pl = outside.lower()
        for phrase in slop_phrases:
            if phrase in pl:
                warnings.append(f"{k}: slop '{phrase}'")
                break  # one warning per slide
        # S1-5: no rhetorical questions
        if i <= 5 and "?" in outside and len(outside) < 100:
            warnings.append(f"{k}: rhetorical question")
    return warnings


def _quality_gate(article, data, posts, warnings):
    """Quality gate: 12 checks from doc. Return True = pass, False = block."""
    if data.get("status") != "success" or not posts:
        return False
    # 1. Article has real economy impact
    if not article.get("eco_score", 0) >= 1:
        return False
    # 2. Impact to Indonesia clear (local source assumed)
    # 3. Original numbers have sources (can't verify programmatically)
    # 4. Number conversion uses reasonable assumptions (LLM handles)
    # 5. No keyword counted repeatedly (scoring handles)
    # 6. Title supported by article body
    if article.get("body"):
        title_words = set(article["title"].lower().split())
        body_lower = article["body"].lower()
        ttl_hits = sum(1 for w in title_words if len(w) > 4 and w in body_lower)
        if ttl_hits < 1:
            return False  # title keywords not in body at all
    # 7. Tension present (S1 should contain contradiction/irony)
    s1 = posts.get("post_1", "").lower()
    tension_markers = ["tapi", "padahal", "tapi gak", "ironi", "sementara",
                       "kontradiksi", "jangankan", "malah", "berbanding"]
    if not any(m in s1 for m in tension_markers):
        warnings.append("S1: no tension marker")
    # 8. "baru aja" freshness check (soft warn if stale)
    # 9. S1-S5 no rhetorical questions
    s1_5_questions = any(
        "?" in posts.get(f"post_{i}", "") and "< 100 chars heuristic"
        for i in range(1, 6)
    )
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
        warnings = deterministic_validate(posts)
        if warnings:
            log.warning(f"  Validation: {warnings}")
            if attempt < 3 and len(warnings) <= 8:
                rev_user = user + f"\n\n{REVISION_PROMPT.format(revision_notes='; '.join(warnings))}"
                c2, e2 = _call_llm(SYSTEM_PROMPT, rev_user, max_retries=1)
                if c2:
                    c2 = c2.strip()
                    if c2.startswith("```"):
                        c2 = re.sub(r'^```(?:json)?\s*', "", c2)
                        c2 = re.sub(r'\s*```$', "", c2)
                    try:
                        d2 = json.loads(c2)
                        if d2.get("status") == "success":
                            p2 = {k: d2[k] for k in ["post_1","post_2","post_3","post_4","post_5","post_6"] if k in d2}
                            for k in p2:
                                p2[k] = _convert_pov(p2[k])
                            w2 = deterministic_validate(p2)
                            # Reject revision if any post still empty
                            has_empty = any(not p2.get(k, "").strip() for k in ["post_1","post_2","post_3","post_4","post_5","post_6"])
                            if not has_empty and (not w2 or len(w2) < len(warnings)):
                                data, posts = d2, p2
                                log.info("  Revision fixed validation")
                    except json.JSONDecodeError:
                        pass
            if not posts:
                continue
            # Quality gate: check article supports the thread
            qg_pass = _quality_gate(article, data, posts, warnings)
            if not qg_pass:
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

def post_to_threads(article_title, posts, image_url=None):
    """Post 6-slide chain to Threads via v1.0 Graph API. Slide 1 can have image."""
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
    for i in range(1, 7):
        key = f"post_{i}"
        text = posts.get(key, "")
        if not text:
            continue
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
        log.info(f"  {key} {'IMAGE' if use_image else 'TEXT'} → {post_id}")
        time.sleep(2)
    return {"post_ids": published_ids, "media_ids": published_ids}

# ══════════════════════════════════════════════
#   MAIN
# ══════════════════════════════════════════════

def main():
    data = load_data()
    posted_urls = {t.get("article_url", t.get("title", "")) for t in data.get("topics", [])}

    # Step 1: Scrape
    log.info("Scraping economy sources...")
    articles = scrape_all()
    log.info(f"  Got {len(articles)} raw articles")

    # Step 2: Pick best article
    article = _pick_article(articles, posted_urls)
    if not article:
        log.error("No fresh unscraped articles found")
        return
    log.info(f"Picked: {article['title']}")
    log.info(f"  Source: {article['source']} | Score: {article.get('eco_score', 0)} | Reason: {article.get('_reason', '')} | Weight: {article.get('_weight', 0)}")

    # Step 3: Fetch full body + image
    log.info("Fetching article body...")
    body, og_image = _fetch_article_body(article["url"])
    if body:
        log.info(f"  Body: {len(body)} chars")
    else:
        log.warning("  No body extracted — generating from title only")
    article["body"] = body

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

    # Step 6: Post
    if not DRY_RUN:
        pub = post_to_threads(article["title"], posts, image_url=image_url)
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
        elif pub and pub.get("error"):
            log.error(f"Post error: {pub['error']}")
    else:
        print()
        for i in range(1, 7):
            print(f"--- S{i} ---")
            print(posts.get(f"post_{i}", ""))
            print()
        print(f"Arc: {result.get('arc', 'market_shock')}")
        print(f"Article: {article['title']}")
        print(f"Source: {article['source']}")
        print(f"Angle: {result.get('angle', '')}")

if __name__ == "__main__":
    main()
