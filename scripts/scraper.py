#!/usr/bin/env python3
"""
scraper.py — 7 tips-focused sources: CNN Lifestyle, CNBC Lifestyle, CNBC MyMoney, Detik Health, IDN Times, Hipwee, Lifehacker, Lifehack.org, Psychology Today
"""
import re
import json
import asyncio
import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlunparse

WIB = timezone(timedelta(hours=7))
MAX_AGE_DAYS = 30
TOP_N = 1

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}

# Scoring: tips & tricks focused
# TIER1 = viral gold (AI stories that affect people directly)
TIER1 = [
    "kecerdasan buatan", "chatgpt", "openai", "gemini", "claude",
    "anthropic", "llm", "agi", "gpt", "deepfake", "model ai",
    "phk", "hack", "bobol", "disinformasi", "scam ai", "tipu ai",
    # Gaya Hidup Produktif
    "produktif", "self-improvement", "time management", "goal setting",
    "work-life balance", "burnout", "prokrastinasi",
    # Life Hacks
    "life hack", "tips dan trik", "rahasia sukses", "trik jitu",
    # Entrepreneurship & UMKM
    "umkm", "wirausaha", "entrepreneur", "bisnis online", "reseller",
    "dropship", "modal kecil", "passive income", "franchise",
    # Psikologi & Mindset
    "psikolog", "kesehatan mental", "mental health", "depresi",
    "trauma", "self-care", "mindset", "kecemasan",
    # English psychology/self-improvement
    "feeling lost", "personal growth", "self-improvement",
    "mental health", "burnout", "life advice",
]
# TIER2 = cool tech (interesting but less viral)
TIER2 = [
    "chip", "semikonduktor", "quantum", "neural", "robot",
    "otomasi", "ai", "machine learning", "gpu",
    # Gaya Hidup Produktif
    "kebiasaan", "rutinitas", "disiplin", "motivasi", "inspirasi",
    "manajemen waktu", "fokus", "konsentrasi", "stres",
    # Life Hacks
    "shortcut", "cara mudah", "cara cepat",
    "efisien", "praktis", "otomatis",
    # Entrepreneurship & UMKM
    "omzet", "profit", "modal", "peluang usaha", "strategi bisnis",
    "marketing", "branding", "customer", "konsumen", "supply chain",
    # Psikologi & Mindset
    "kepribadian", "emosi", "konflik",
    "resilien", "adaptasi", "wellbeing", "terapi",
    # English self-improvement
    "energy", "purpose", "clarity", "rebuild",
    "routine", "habits", "discipline", "motivation",
]
# TIER3 = generic tech (rarely scores high alone)
TIER3 = [
    "teknologi", "startup", "inovasi",
    # Gaya Hidup
    "karir", "sukses", "leadership", "pengembangan diri",
    # Entrepreneurship & UMKM
    "pengusaha", "peluang", "strategi", "investasi", "anggaran",
    # Psikologi & Mindset
    "anxiety", "overthinking", "healing", "toxic",
]
# PENALTY = product/promo patterns
PENALTY = [
    "spesifikasi", "harga promo", "diskon", "flash sale", "cashback",
    "unboxing", "hands-on", "pre-order", "giveaway",
    # Product launch/review signals
    "resmi:", "rilis:", "harga rp", "harga mulai",
    "earbuds", "earphone", "headphone", "headset",
    "anc ", "tws ", "audio lossless", "noise cancellation",
    "smartphone", "tablet flagship", "powerbank",
    "baterai mah", "fast charging", "nfc murah",
    "kamera mp", "refresh rate", "ip rating",
    "resmi rilis", "resmi diluncurkan", "bocoran spesifikasi",
    "review lengkap", "kelebihan dan kekurangan", "benchmark",
]
EXCLUDE = [
    "prediksi cuaca", "ramalan zodiak", "gosip", "skor akhir",
    "jadwal pertandingan", "transfer pemain",
    # Sensitif / kasus pribadi / tragedi
    "kasus kematian", "kematian dokter", "korban meninggal", "mayat",
    "pembunuhan", "bunuh diri", "kecelakaan maut", "tenggelam",
    "pemerkosaan", "pencabulan", "kdrt", "penganiayaan",
    "viral di", "heboh", "kontroversi", "sindir", "sindiran",
    # Gosip / selebriti / hiburan ringan
    "rumah tangga", "cerai", "perselingkuhan",
    "hamil", "menikah", "resepsi", "lamaran",
]
# TIPS_BONUS = boost tips & tricks content
TIPS_BONUS = [
    "tips", "cara", "trik", "rahasia", "hack", "panduan", "langkah",
    "strategi", "tutorial", "cara mudah", "cara cepat",
    "begini cara", "ini dia", "yang perlu", "harus tahu",
    "wajib tahu", "jangan sampai", "hindari", "perhatikan",
    # English tips keywords (for Lifehacker/international sources)
    "how to", "trick", "guide", "step by step", "best practices",
    "productivity", "workflow", "shortcut", "beginner",
    "hacks", "everyday", "simple ways", "easy ways",
    # Psychology/self-improvement English
    "relationship", "mental health", "habits", "routine",
    "self-improvement", "personal growth", "mindset",
    "productivity tips", "life advice", "practical tips",
    "improve your", "better life", "daily routine",
    "step by step", "what to do", "ways to",
    "morning routine", "evening routine", "daily habits",
]
# NEWS_PENALTY = pure news signals (not tips)
NEWS_PENALTY = [
    "rilis", "peluncuran", "pengumuman", "resmi", "tutup operasi",
    "tutup layanan", "bakal", "akan", "segera", "rencana",
    "rencanakan", "targetkan", "anggarkan",
]

# Pre-compiled regex — unique match per tier (not count)
_TIER1_SET = {k.lower() for k in TIER1}
_TIER2_SET = {k.lower() for k in TIER2}
_TIER3_SET = {k.lower() for k in TIER3}
_PENALTY_RE = re.compile("|".join(re.escape(k.lower()) for k in PENALTY))
_EXCL_RE    = re.compile("|".join(re.escape(k.lower()) for k in EXCLUDE))
_TIPS_RE    = re.compile("|".join(re.escape(k.lower()) for k in TIPS_BONUS))
_NEWS_RE    = re.compile("|".join(re.escape(k.lower()) for k in NEWS_PENALTY))


def _unique_matches(text: str, keywords: set) -> int:
    """Count unique keyword matches (not repeat count). Word-boundary for short keywords."""
    count = 0
    for kw in keywords:
        if len(kw) <= 4:
            # Word boundary to avoid: agi→andalkan, ai→karena, etc.
            if re.search(r'\b' + re.escape(kw) + r'\b', text):
                count += 1
        elif kw in text:
            count += 1
    return count


def score_article(title: str, body: str, date=None) -> int:
    title_l = title.lower()
    body_l = body[:1000].lower()
    text = title_l + " " + body_l

    # Auto-zero: excluded topics
    if _EXCL_RE.search(text):
        return 0

    # Auto-zero: product promo/launch — 3+ penalty keywords = product article
    penalty_hits = len(set(_PENALTY_RE.findall(text)))
    if penalty_hits >= 3:
        return 0

    # Score by tier — title counts 3x (that's the hook)
    t1 = _unique_matches(title_l, _TIER1_SET) * 30 + _unique_matches(body_l, _TIER1_SET) * 15
    t2 = _unique_matches(title_l, _TIER2_SET) * 12 + _unique_matches(body_l, _TIER2_SET) * 5
    t3 = _unique_matches(title_l, _TIER3_SET) * 4  + _unique_matches(body_l, _TIER3_SET) * 2

    s = t1 + t2 + t3

    # Tips & tricks bonus: boost articles with how-to/tips signals
    tips_hits = len(set(_TIPS_RE.findall(text)))
    s += tips_hits * 8

    # News penalty: reduce pure news (rilis, pengumuman, etc.)
    news_hits = len(set(_NEWS_RE.findall(text)))
    s -= news_hits * 6

    # Penalty: product review/promo patterns
    penalty_count = len(_PENALTY_RE.findall(text))
    s -= penalty_count * 12

    # Recency bonus: newer = higher. Today +20, 30 days ago +0
    if date:
        days_old = (datetime.now(WIB) - date).days
        recency_bonus = max(0, 20 - int(days_old * 20 / 30))
        s += recency_bonus

    return max(0, min(s, 100))

def fix_image_url(url: str) -> str:
    if not url:
        return url
    if url.endswith(".jpe"):
        url += "g"
    parsed = urlparse(url)
    if "akcdn.detik" in parsed.netloc:
        base = urlunparse(parsed._replace(query=""))
        return base + "?w=1200&type=jpeg"
    # Liputan6: CDN hash depends on full path including filters — keep as-is
    if "akamaized.net" in parsed.netloc:
        return url
    # Kompas: strip crops/filters/watermark, keep original /data/photo/ path
    if "asset.kompas.com" in parsed.netloc:
        m = re.search(r"(/data/photo/.+)$", parsed.path)
        if m:
            return f"https://asset.kompas.com{m.group(1)}"
        path = re.sub(r"/\d+x\d+/", "/1200x675/", parsed.path)
        return urlunparse(parsed._replace(path=path, query=""))
    return url

def parse_date_from_url(url: str):
    # Kompas: /read/2026/06/29/...
    m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=WIB)
        except ValueError:
            pass
    # CNBC: /tech/20260630095442-37-746746/...
    m = re.search(r"/(\d{4})(\d{2})(\d{2})\d{6}-\d+-\d+/", url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=WIB)
        except ValueError:
            pass
    # CNN: /teknologi/20260629092526-641-1374523/...
    m = re.search(r"/(\d{4})(\d{2})(\d{2})\d{6}-", url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=WIB)
        except ValueError:
            pass
    # Psychology Today: /blog/.../202606/... (year+month, no day)
    m = re.search(r"/(\d{4})(\d{2})/[^/]+$", url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=WIB)
        except ValueError:
            pass
    return None

def parse_date_from_html(soup):
    """Extract date from HTML meta tags or <time> tag."""
    # meta[name=publishdate] — Detik uses: "2026/06/29 18:17:26"
    tag = soup.find("meta", attrs={"name": "publishdate"})
    if tag and tag.get("content"):
        try:
            return datetime.strptime(tag["content"].strip(), "%Y/%m/%d %H:%M:%S").replace(tzinfo=WIB)
        except ValueError:
            pass
    # meta[property=article:published_time] — ISO format
    tag = soup.find("meta", property="article:published_time")
    if tag and tag.get("content"):
        try:
            from dateutil.parser import parse as dtparse
            return dtparse(tag["content"]).replace(tzinfo=WIB)
        except Exception:
            pass
    # <time datetime="">
    tag = soup.find("time")
    if tag and tag.get("datetime"):
        try:
            from dateutil.parser import parse as dtparse
            return dtparse(tag["datetime"]).replace(tzinfo=WIB)
        except Exception:
            pass
    return None

def is_fresh(dt) -> bool:
    """Fix 2+6: unknown date → reject. <= MAX_AGE_DAYS (include day 3)."""
    if dt is None:
        return False
    return (datetime.now(WIB) - dt).days <= MAX_AGE_DAYS

def get_og_image(soup) -> str:
    tag = soup.find("meta", property="og:image")
    if tag and tag.get("content"):
        return fix_image_url(str(tag["content"]).strip())
    return ""

def extract_body(soup, selectors: list[tuple]) -> str:
    """Try each (tag, pre-compiled cls) until one yields paragraphs."""
    for tag, cls in selectors:
        div = soup.find(tag, class_=cls) if cls else soup.find(tag)
        if not div:
            continue
        paras = [p.get_text(" ", strip=True) for p in div.find_all("p")
                 if len(p.get_text(strip=True)) > 40]
        if paras:
            return "\n\n".join(paras)
    return ""

# Body selectors per source
_CNN_SEL    = [("div", re.compile("detail-text")), ("div", re.compile("content-det"))]
_CNBC_SEL   = [("div", re.compile("detail-text")), ("div", re.compile("cnbc-body"))]
_IDN_SEL    = [("div", re.compile("article-content")), ("div", re.compile("content-body")), ("article", None)]
_HIPWEE_SEL = [("div", re.compile("article-content")), ("div", re.compile("post-content")), ("div", re.compile("entry-content"))]
_DETIX_HEALTH_SEL = [("div", re.compile("detail__body-text")), ("div", re.compile("itp_bodycontent"))]
_LIFEHACKER_SEL = [("div", re.compile("article-content")), ("div", re.compile("js_post-content")), ("article", None)]
_LIFEHACK_SEL = [("div", re.compile("article-content")), ("div", re.compile("post-content")), ("article", None)]
_PSYCHTODAY_SEL = [("div", re.compile("entry-content")), ("div", re.compile("article-body")), ("article", None)]

async def scrape_article_async(url: str, client: httpx.AsyncClient, source: str, rss_date: datetime | None = None) -> dict | None:
    try:
        r = await client.get(url, timeout=12)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        title_tag = soup.find("h1")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            return None
        # RSS date > HTML meta > URL pattern (URL often lacks day precision)
        dt = rss_date or parse_date_from_html(soup) or parse_date_from_url(url)
        if not is_fresh(dt):
            return None
        image = get_og_image(soup)
        if source == "cnn_lifestyle":
            body = extract_body(soup, _CNN_SEL)
        elif source == "cnbc_lifestyle":
            body = extract_body(soup, _CNBC_SEL)
        elif source == "cnbc_mymoney":
            body = extract_body(soup, _CNBC_SEL)
        elif source == "detik_health":
            body = extract_body(soup, _DETIX_HEALTH_SEL)
        elif source == "idntimes":
            body = extract_body(soup, _IDN_SEL)
        elif source == "hipwee":
            body = extract_body(soup, _HIPWEE_SEL)
        elif source == "lifehacker":
            body = extract_body(soup, _LIFEHACKER_SEL)
        elif source == "lifehack":
            body = extract_body(soup, _LIFEHACK_SEL)
        elif source == "psychtoday":
            body = extract_body(soup, _PSYCHTODAY_SEL)
        else:
            return None
        if not body:
            return None
        # All sources are tips-focused, give bonus to all
        # Higher bonus for international sources (English content scores lower on Indo keywords)
        source_bonus = 20 if source in ("lifehacker", "lifehack", "psychtoday") else 15
        return {"title": title, "date": dt, "image": image, "body": body, "url": url, "source": source, "_source_bonus": source_bonus}
    except Exception:
        return None

# Link getters — tips-focused sources only

async def get_links_cnn_lifestyle(client: httpx.AsyncClient) -> list[str]:
    """CNN Gaya Hidup — RSS feed (lifestyle, psikologi, kesehatan mental)."""
    links = set()
    try:
        r = await client.get("https://www.cnnindonesia.com/gaya-hidup/rss", timeout=12)
        for m in re.finditer(r"<link>([^<]+)</link>", r.text):
            url = m.group(1).strip()
            if re.match(r"https://www\.cnnindonesia\.com/gaya-hidup/\d+", url):
                links.add(url.split("?")[0])
    except Exception:
        pass
    return list(links)[:50]

async def get_links_cnbc_lifestyle(client: httpx.AsyncClient) -> list[str]:
    """CNBC Indonesia lifestyle — RSS <guid> tags (career, finance tips)."""
    links = set()
    try:
        r = await client.get("https://www.cnbcindonesia.com/lifestyle/rss", timeout=12)
        for m in re.finditer(r"<guid>([^<]+)</guid>", r.text):
            url = m.group(1).strip()
            if re.match(r"https://www\.cnbcindonesia\.com/lifestyle/\d+", url):
                links.add(url.split("?")[0])
    except Exception:
        pass
    return list(links)[:50]

async def get_links_cnbc_mymoney(client: httpx.AsyncClient) -> list[str]:
    """CNBC Indonesia MyMoney — RSS (entrepreneurship, UMKM, keuangan)."""
    links = set()
    try:
        r = await client.get("https://www.cnbcindonesia.com/mymoney/rss", timeout=12)
        for m in re.finditer(r"<guid>([^<]+)</guid>", r.text):
            url = m.group(1).strip()
            if re.match(r"https://www\.cnbcindonesia\.com/mymoney/\d+", url):
                links.add(url.split("?")[0])
    except Exception:
        pass
    return list(links)[:50]

async def get_links_detik_health(client: httpx.AsyncClient) -> list[str]:
    """Detik Health — tips sehat, mental health, wellness."""
    links = set()
    try:
        r = await client.get("https://health.detik.com/rss", timeout=12)
        for m in re.finditer(r"<link>([^<]+)</link>", r.text):
            url = m.group(1).strip()
            if "health.detik.com/" in url and "/d-" in url:
                links.add(url.split("?")[0])
    except Exception:
        pass
    return list(links)[:50]

async def get_links_idntimes(client: httpx.AsyncClient) -> list[str]:
    """IDN Times — lifestyle, Gen Z, career tips."""
    links = set()
    try:
        r = await client.get("https://www.idntimes.com/life", timeout=12)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = str(a["href"])
            # IDN Times article pattern: /life/.../slug or /business/.../slug
            if "idntimes.com/" in href and re.search(r"idntimes\.com/\w+/.+", href):
                links.add(href.split("?")[0])
    except Exception:
        pass
    return list(links)[:50]

async def get_links_hipwee(client: httpx.AsyncClient) -> list[str]:
    """Hipwee — tips produktif, self-improvement, motivation."""
    links = set()
    try:
        r = await client.get("https://www.hipwee.com/", timeout=12)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = str(a["href"])
            # Hipwee article: hipwee.com/category/slug or hipwee.com/n/slug
            if "hipwee.com/" in href and re.search(r"hipwee\.com/(?!top|editors|community|dashboard|user|category|promo)[\w-]+/[\w-]+", href):
                links.add(href.split("?")[0])
    except Exception:
        pass
    return list(links)[:50]

async def get_links_lifehacker(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Lifehacker — tips, hacks, how-to. Returns (url, pubdate) tuples."""
    items: list[tuple[str, datetime | None]] = []
    try:
        r = await client.get("https://lifehacker.com/rss", timeout=12)
        for item_block in re.finditer(r"<item>(.*?)</item>", r.text, re.DOTALL):
            block = item_block.group(1)
            link_m = re.search(r"<link>([^<]+)</link>", block)
            date_m = re.search(r"<pubDate>(.*?)</pubDate>", block)
            if not link_m:
                continue
            url = link_m.group(1).strip().split("?")[0]
            dt = None
            if date_m:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(date_m.group(1).strip()).replace(tzinfo=WIB)
                except Exception:
                    pass
            items.append((url, dt))
    except Exception:
        pass
    return items[:50]

async def get_links_lifehack(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Lifehack.org — self-improvement, productivity, life tips. Returns (url, pubdate) tuples."""
    items: list[tuple[str, datetime | None]] = []
    try:
        r = await client.get("https://www.lifehack.org/feed", timeout=12)
        for item_block in re.finditer(r"<item>(.*?)</item>", r.text, re.DOTALL):
            block = item_block.group(1)
            link_m = re.search(r"<link>([^<]+)</link>", block)
            date_m = re.search(r"<pubDate>(.*?)</pubDate>", block)
            if not link_m:
                continue
            url = link_m.group(1).strip().split("?")[0]
            dt = None
            if date_m:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(date_m.group(1).strip()).replace(tzinfo=WIB)
                except Exception:
                    pass
            items.append((url, dt))
    except Exception:
        pass
    return items[:50]

async def get_links_psychtoday(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Psychology Today — mental health, relationship, psychology tips. Returns (url, pubdate) tuples."""
    items: list[tuple[str, datetime | None]] = []
    try:
        r = await client.get("https://www.psychologytoday.com/us/front/feed", timeout=12)
        for item_block in re.finditer(r"<item>(.*?)</item>", r.text, re.DOTALL):
            block = item_block.group(1)
            link_m = re.search(r"<link>([^<]+)</link>", block)
            date_m = re.search(r"<pubDate>(.*?)</pubDate>", block)
            if not link_m:
                continue
            url = link_m.group(1).strip().split("?")[0]
            dt = None
            if date_m:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(date_m.group(1).strip()).replace(tzinfo=WIB)
                except Exception:
                    pass
            items.append((url, dt))
    except Exception:
        pass
    return items[:50]


async def scrape_all_async(top_n: int = TOP_N) -> list[dict]:
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        link_tasks = await asyncio.gather(
            get_links_cnn_lifestyle(client),
            get_links_cnbc_lifestyle(client),
            get_links_cnbc_mymoney(client),
            get_links_detik_health(client),
            get_links_idntimes(client),
            get_links_hipwee(client),
            get_links_lifehacker(client),
            get_links_lifehack(client),
            get_links_psychtoday(client),
            return_exceptions=True,
        )
        source_names = ["cnn_lifestyle", "cnbc_lifestyle", "cnbc_mymoney", "detik_health", "idntimes", "hipwee", "lifehacker", "lifehack", "psychtoday"]
        all_tasks = []
        for src, links in zip(source_names, link_tasks):
            if not isinstance(links, list) or not links:
                continue
            for item in links:
                # Lifehacker returns (url, pubdate) tuples
                if isinstance(item, tuple):
                    url, rss_date = item
                    all_tasks.append(scrape_article_async(url, client, src, rss_date=rss_date))
                else:
                    all_tasks.append(scrape_article_async(item, client, src))

        results = await asyncio.gather(*all_tasks, return_exceptions=True)

    articles: list[dict] = []
    seen: set[str] = set()
    for art in results:
        if not isinstance(art, dict):
            continue
        if art["url"] in seen:
            continue
        seen.add(art["url"])
        art["score"] = score_article(art["title"], art["body"], art["date"]) + art.pop("_source_bonus", 0)
        if art["score"] > 25:
            articles.append(art)

    articles.sort(key=lambda x: x["score"], reverse=True)
    return articles[:top_n]

def scrape_all(top_n: int = TOP_N) -> list[dict]:
    return asyncio.run(scrape_all_async(top_n))


if __name__ == "__main__":
    import sys, time
    n = int(sys.argv[1]) if len(sys.argv) > 1 else TOP_N
    t0 = time.time()
    results = scrape_all(n)
    elapsed = time.time() - t0
    src_label = {
        "cnn_lifestyle": "CNN Gaya Hidup", "cnbc_lifestyle": "CNBC Lifestyle",
        "cnbc_mymoney": "CNBC MyMoney", "detik_health": "Detik Health",
        "idntimes": "IDN Times", "hipwee": "Hipwee",
        "lifehacker": "Lifehacker", "lifehack": "Lifehack.org",
        "psychtoday": "Psychology Today",
    }
    for i, art in enumerate(results, 1):
        dt_str = art["date"].strftime("%Y-%m-%d %H:%M WIB") if art["date"] else "unknown date"
        print(f"\n{'='*60}")
        print(f"#{i} [{src_label.get(art['source'], art['source'])}] score={art['score']}")
        print(f"Title : {art['title']}")
        print(f"Date  : {dt_str}")
        print(f"Image : {art['image']}")
        print(f"URL   : {art['url']}")
        print(f"Body  : {len(art['body'])} chars")
        print(f"Preview:\n{art['body'][:400]}...")
    if not results:
        print("No articles found.")
    print(f"\nDone in {elapsed:.1f}s")
