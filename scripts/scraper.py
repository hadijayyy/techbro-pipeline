#!/usr/bin/env python3
"""
scraper.py — International AI/Tech focused: TechCrunch, The Verge, Ars Technica, Wired, HN, Anthropic
Scrapes hot/viral articles from last 24h. English content for global Threads audience.
"""
import re
import json
import asyncio
import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlunparse

UTC = timezone.utc
MAX_AGE_HOURS = 12
FALLBACK_HOURS = 24  # fallback if 12h yields nothing
TOP_N = 1

# Source names used by scrape_all_async — single source of truth
SOURCE_NAMES = ["bbc", "bloomberg_technoz"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}

# ─── Scoring Keywords ────────────────────────────────────────────

# TIER1 = hot topics — scored 3x on title match
TIER1 = [
    # Entertainment & Celebrity (Taylor Swift, Netflix, music, film)
    "taylor swift", "travis kelce", "celebrity", "wedding", "marry", "married",
    "netflix", "disney", "spotify", "grammy", "oscar", "beyonce", "drake",
    "concert", "tour", "album", "movie", "film", "tv show", "reality tv",
    "backlash", "controversy", "viral", "trending",
    # AI & Tech
    "ai", "artificial intelligence", "chatgpt", "openai", "google", "meta",
    "apple", "microsoft", "nvidia", "amazon", "tesla", "robot", "deepfake",
    "playstation", "xbox", "nintendo", "gta", "gaming", "esports",
    "cybersecurity", "hack", "data breach", "privacy", "surveillance",
    # Climate & Environment
    "climate", "heatwave", "temperature", "record-breaking", "flood",
    "wildfire", "drought", "renewable", "solar", "wind", "carbon",
    "extinction", "endangered", "pollution",
    # Science & Space
    "nasa", "spacex", "mars", "moon", "space", "telescope", "satellite",
    "quantum", "discovery", "breakthrough", "research", "study",
    "brain", "dna", "vaccine", "cancer", "disease", "health",
    # Politics & Global (UK + US + world)
    "trump", "starmer", "election", "parliament", "government", "pm",
    "ukraine", "russia", "china", "iran", "war", "conflict", "sanctions",
    "referendum", "policy", "law", "ban", "regulation",
    # Crime & Safety
    "scam", "fraud", "smuggler", "investigation", "police", "court",
    "prison", "victims", "abuse", "safety", "warning",
]

# TIER2 = tech adjacent (masih relate)
TIER2 = [
    # Indonesian Economy & Tech
    "startup", "funding", "pendanaan", "valuasi", "ipo", "akuisisi",
    "umkm", "digital", "transformasi digital", "ecommerce", "e-commerce",
    # Global Tech (tetap menarik kalo ada angle Indonesia)
    "semiconductor", "chip", "gpu", "nvidia", "apple", "google", "meta",
    "microsoft", "amazon", "tesla", "spacex",
    # Security
    "cybersecurity", "hack", "breach", "malware", "scam", "penipuan",
    "phishing",
    # Social & Content
    "tiktok", "instagram", "threads", "twitter", "x", "youtube",
    "influencer", "content creator", "monetisasi",
    # Fintech & Finance — NEW
    "bank", "payment", "pembayaran", "transaksi", "saldo",
    "reward", "cashback", "promo", "voucher",
    # Karier & Produktivitas — NEW
    "productivity", "produktivitas", "skill", "upskilling",
    "resign", "interview", "wawancara", "cv", "resume",
    "gaji", "umr", "upah",
    # Geopolitics & Tech — NEW
    "china", "amerika", "serikat", "perang dagang", "sanksi",
    "impor", "ekspor", "tarif",
]

# TIER3 = generic (low weight)
TIER3 = [
    "technology", "innovation", "digital", "platform", "app",
    "teknologi", "inovasi", "aplikasi",
]

# PENALTY = product reviews/promos
PENALTY = [
    "unboxing", "hands-on", "review:", "buying guide",
    "best of 2026", "gift guide", "coupon", "discount",
    "earbuds", "earphone", "headphone", "smartphone review",
    "battery life test", "benchmark score",
]

# EXCLUDE = off-topic
EXCLUDE = [
    "zodiak", "horoscope", "astrology", "gossip", "celebrity",
    "sports score", "match schedule", "recipe", "cooking",
    "fashion week", "beauty tips", "weight loss",
    # Gaming (off-topic for our niche)
    "pokemon", "genshin", "wuthering waves", "volleyball legends",
    "mobile legends", "free fire", "pubg", "valorant", "fortnite",
    "roblox", "minecraft", "gacha", "gameplay", "let's play",
    "clash of clans", "clash royale", "coc tips", "fifa", "efootball",
    "dota", "league of legends", "wild rift", "among us", "stumble guys",
]

_TIER1_SET = {k.lower() for k in TIER1}
_TIER2_SET = {k.lower() for k in TIER2}
_TIER3_SET = {k.lower() for k in TIER3}
_PENALTY_RE = re.compile("|".join(re.escape(k.lower()) for k in PENALTY))
_EXCL_RE = re.compile("|".join(re.escape(k.lower()) for k in EXCLUDE))

# ─── Stemming (simple suffix stripper for Jaccard dedup) ───────

_STEM_SUFFIXES = ("ing", "tion", "sion", "ment", "ness", "able", "ible",
                   "ies", "ied", "ing", "ers", "est", "ful", "ous",
                   "ive", "ize", "ise", "ed", "er", "ly", "es", "s")


def _stem(word: str) -> str:
    """Minimal English stemmer for dedup matching. Strips common suffixes."""
    if len(word) <= 4:
        return word
    for suffix in _STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


def _unique_matches(text: str, keywords: set) -> int:
    """Count unique keyword matches. Uses \\b word boundaries for precision."""
    count = 0
    for kw in keywords:
        if " " in kw:  # bigram: word boundary at phrase edges
            if re.search(r'\b' + re.escape(kw) + r'\b', text):
                count += 1
        elif re.search(r'\b' + re.escape(kw) + r'\b', text):
            count += 1
    return count


def fast_content_filter(title: str, body: str) -> str | None:
    """Layer 2 fast drop. Returns rejection reason or None if OK."""
    text = (title + " " + body[:500]).lower()
    if _EXCL_RE.search(text):
        return "excluded keyword"
    if len(set(_PENALTY_RE.findall(text))) >= 3:
        return "penalty keyword"
    return None


def score_article(title: str, body: str, date=None) -> int:
    """Score article. Assumes fast_content_filter() already passed."""
    title_l = title.lower()
    body_l = body[:1500].lower()
    text = title_l + " " + body_l

    # Title gets 3x weight (readers see title first)
    t1 = _unique_matches(title_l, _TIER1_SET) * 30 + _unique_matches(body_l, _TIER1_SET) * 10
    t2 = _unique_matches(title_l, _TIER2_SET) * 15 + _unique_matches(body_l, _TIER2_SET) * 5
    t3 = _unique_matches(title_l, _TIER3_SET) * 5 + _unique_matches(body_l, _TIER3_SET) * 2

    s = t1 + t2 + t3

    # Density bonus: if title has 2+ TIER1 keywords, it's a core AI story
    if _unique_matches(title_l, _TIER1_SET) >= 2:
        s += 20

    # HN virality bonus: 500pts = +100, 1000pts = +100 (capped)

    # Recency: exponential decay. 0h = +30, 12h = +0
    if date:
        hours_old = (datetime.now(UTC) - date).total_seconds() / 3600
        recency_bonus = max(0, 30 - int(hours_old * 30 / MAX_AGE_HOURS))
        s += recency_bonus

    return max(0, min(s, 150))


def fix_image_url(url: str) -> str:
    """Upgrade OG image to highest resolution."""
    if not url:
        return url
    # Verge: strip quality/crop params for full res
    if "platform.theverge.com" in url or "vox-cdn.com" in url:
        # Keep as-is (already good quality from OG)
        return url.split("?")[0] if "quality=" in url else url
    # TechCrunch: ensure w=1200
    if "techcrunch.com" in url:
        if "?" in url:
            return url  # already has params
        return url + "?w=1200"
    # Ars Technica: already 1152x648
    if "arstechnica.net" in url:
        return url
    # Wired: strip crop params
    if "wired.com" in url:
        # Pattern: /w_1280,c_limit/ → keep
        return url
    return url


def parse_date_iso(date_str: str) -> datetime | None:
    """Parse ISO 8601 date string."""
    if not date_str:
        return None
    try:
        from dateutil.parser import parse as dtparse
        return dtparse(date_str).astimezone(UTC)
    except Exception:
        pass
    return None


def is_fresh(dt, hours=MAX_AGE_HOURS) -> bool:
    if dt is None:
        return False
    return (datetime.now(UTC) - dt).total_seconds() / 3600 <= hours


def get_og_image(soup) -> str:
    tag = soup.find("meta", property="og:image")
    if tag and tag.get("content"):
        return fix_image_url(str(tag["content"]).strip())
    return ""


def extract_body(soup, selectors: list[tuple]) -> str:
    for tag, cls in selectors:
        div = soup.find(tag, class_=cls) if cls else soup.find(tag)
        if not div:
            continue
        paras = [p.get_text(" ", strip=True) for p in div.find_all("p")
                 if len(p.get_text(strip=True)) > 30]
        if paras:
            return "\n\n".join(paras)
    return ""


# ─── Noise filters per source ────────────────────────────────────

_NOISE_PATTERNS = [
    # ── Email / subscribe ──
    r"subscribe to.*newsletter",
    r"sign up for.*newsletter",
    r"subscribe to our",
    r"sign up for",
    r"\bsubscribe\b",
    r"newsletter\s*[.!]",
    r"daily email digest",
    r"delivered to your inbox",
    # ── Click / navigation ──
    r"please click here",
    r"click here\b",
    r"scroll to continue with content",
    r"read more about.*on.*TechCrunch",
    # ── Comments ──
    r"moderates?\s+comments?\s+to",
    r"confirm your public display name",
    r"must confirm.*before commenting",
    r"join the conversation",
    # ── Ads / sponsors ──
    r"featured video.*from.*sponsor",
    r"\badvertisement\b",
    r"\bpromoted\b",
    r"\bsponsored content",
    # ── Copyright / legal ──
    r"©\s*\d{4}.*all rights reserved",
    r"any references to my blogs must be accompanied",
    r"all rights reserved\.?",
    # ── Related / more ──
    r"^related:.*$",
    r"^see also:.*$",
    r"you may also like",
    r"^more from\b",
    r"read next",
    r"trending now",
    r"watch more",
    r"breaking news",
    # ── Short promo / CTA lines (single-line match) ──
    r"^(?:get|follow|share|tweet|login|register)\b.{0,50}$",
    r"^(?:breaking|exclusive|developing)\b.{0,30}$",
    r"^(?:photo|image|credit|getty|shutterstock).{0,50}$",
    r"^want serverless.{0,80}$",
    r"^get .* delivered to your inbox",
    # ── Posts from TechCrunch ──
    r"posts from this topic will be added",
    r"posts from this author will be added",
]

_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS), re.I | re.M)


def clean_body(text: str) -> str:
    """Remove noise from extracted body text."""
    out = _NOISE_RE.sub("", text)
    # Collapse multiple newlines
    out = re.sub(r'\n{3,}', '\n\n', out)
    return out.strip()


# ─── Body selectors per source ───────────────────────────────────

_TC_SEL = [
    ("div", re.compile("article-content|entry-content|post-content")),
    ("article", None),
]
_VERGE_SEL = [
    ("div", re.compile("duet--layout--entry-body")),
    ("div", re.compile("article-body|entry-content")),
    ("article", None),
]
_ARS_SEL = [
    ("div", re.compile("post-content")),
    ("article", None),
]
_WIRED_SEL = [
    ("div", re.compile("body__inner|article-body|entry-content")),
    ("article", None),
]
_HN_SEL = [
    ("article", None),
    ("main", None),
    ("div", re.compile("post-content|article-content|entry-content|post-body")),
    ("div", None),  # generic fallback
]

async def scrape_article_async(url: str, client: httpx.AsyncClient, source: str,
                                rss_date: datetime | None = None,
                                ) -> dict | None:
    try:
        # YouTube: use oEmbed API (no auth needed)
        if source == "youtube":
            return await scrape_youtube_oembed(url, client)

        r = await client.get(url, timeout=15)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        title_tag = soup.find("h1")
        title = title_tag.get_text(strip=True) if title_tag else ""
        # Marginalian: first h1 is "Archives", real title is h1.entry-title
        if source == "marginalian":
            real_h1 = soup.find("h1", class_="entry-title")
            if real_h1:
                title = real_h1.get_text(strip=True)
        # Bloomberg Technoz: first h1 is empty logo, real title is h1.title
        if not title and source == "bloomberg_technoz":
            real_h1 = soup.find("h1", class_="title")
            if real_h1:
                title = real_h1.get_text(strip=True)
        if not title:
            og = soup.find("meta", property="og:title")
            title = og.get("content", "").split(" - ")[0].strip() if og else ""
        if not title:
            return None

        dt = rss_date or parse_date_iso(
            (soup.find("meta", property="article:published_time") or {}).get("content", "")
        )
        # BBC: parse datePublished from JSON-LD
        if not dt and source == "bbc":
            import json
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string)
                    if isinstance(data, list):
                        data = data[0]
                    if "datePublished" in data:
                        dt = parse_date_iso(data["datePublished"])
                        break
                except Exception:
                    pass

        if not is_fresh(dt, hours=FALLBACK_HOURS):
            # BBC feeds contain old articles — allow wider window
            if source == "bbc" and not is_fresh(dt, hours=168):  # 7 days
                return None
            elif source != "bbc":
                return None

        image = get_og_image(soup)

        if source == "techcrunch":
            body = extract_body(soup, _TC_SEL)
        elif source == "theverge":
            body = extract_body(soup, _VERGE_SEL)
        elif source == "arstechnica":
            body = extract_body(soup, _ARS_SEL)
        elif source == "wired":
            body = extract_body(soup, _WIRED_SEL)
        elif source in ("hn", "anthropic"):
            body = extract_body(soup, _HN_SEL)
        elif source in ("cnbc_id", "detik", "liputan6", "kumparan", "antara", "republika", "cnnindonesia", "merdeka", "kompas", "hipwee"):
            # Indonesian sources: try common selectors
            _ID_SEL = [
                ("div", "post-content"), ("div", "detail-text"),
                ("div", "article-content"), ("div", "content-text"),
                ("div", "read__content"), ("div", "article_body"),
                ("div", "read-content"), ("div", "inner-content"),  # merdeka/kompas
                ("div", "article"),  # merdeka (tailwind)
                ("article", None),
            ]
            body = extract_body(soup, _ID_SEL)
        elif source in ("psyblog", "nesslabs"):
            body = extract_body(soup, [("div", "entry-content"), ("article", None)])
        elif source == "farnam_street":
            body = extract_body(soup, [("div", "entry-content entry-content-single"), ("div", "entry-content"), ("article", None)])
        elif source == "mark_manson":
            body = extract_body(soup, [("div", "article-content"), ("div", "article-content-container"), ("article", None)])
        elif source == "marginalian":
            body = extract_body(soup, [("div", "entry_content"), ("div", "post_content"), ("article", None)])
        elif source == "mindful":
            body = extract_body(soup, [("div", "article__body"), ("div", "article__intro p-summary"), ("article", None)])
        elif source == "bbc":
            body = extract_body(soup, [("article", None), ("div", "ssrcss-11r1m41-RichTextComponentWrapper")])
        elif source == "bloomberg_technoz":
            body = extract_body(soup, [("article", None)])
        else:
            return None

        if not body or len(body) < 300:
            return None

        body = clean_body(body)

        return {
            "title": title, "date": dt, "image": image,
            "body": body, "url": url, "source": source,
        }
    except Exception:
        return None


# ─── RSS Parsers ─────────────────────────────────────────────────


async def _rss_links(client: httpx.AsyncClient, url: str, limit: int = 20) -> list[tuple[str, datetime | None]]:
    """Generic RSS 2.0 link extractor. Returns [(url, pub_date|None), ...]."""
    items = []
    try:
        r = await client.get(url, timeout=12, follow_redirects=True)
        for item_block in re.finditer(r"<item>(.*?)</item>", r.text, re.DOTALL):
            block = item_block.group(1)
            link_m = re.search(r"<link>([^<]+)</link>", block)
            date_m = re.search(r"<pubDate>(.*?)</pubDate>", block)
            if not link_m:
                continue
            item_url = link_m.group(1).strip().split("?")[0]
            dt = None
            if date_m:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(date_m.group(1).strip()).astimezone(UTC)
                except Exception:
                    pass
            items.append((item_url, dt))
    except Exception:
        pass
    return items[:limit]


# ─── BBC News ───────────────────────────────────────────────────

async def get_links_bbc(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """BBC News — top stories, tech, science."""
    items = []
    feeds = [
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://feeds.bbci.co.uk/news/technology/rss.xml",
        "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    ]
    from email.utils import parsedate_to_datetime
    for feed_url in feeds:
        try:
            r = await client.get(feed_url, timeout=12)
            # BBC uses CDATA: <title><![CDATA[text]]></title>
            for item_block in re.finditer(r"<item>(.*?)</item>", r.text, re.DOTALL):
                block = item_block.group(1)
                link_m = re.search(r"<link>([^<]+)</link>", block)
                date_m = re.search(r"<pubDate>([^<]+)</pubDate>", block)
                if not link_m:
                    continue
                url = link_m.group(1).strip().split("?")[0]
                dt = None
                if date_m:
                    try:
                        dt = parsedate_to_datetime(date_m.group(1).strip()).astimezone(UTC)
                    except Exception:
                        pass
                items.append((url, dt))
        except Exception:
            pass
    # Dedupe
    seen = set()
    deduped = []
    for url, dt in items:
        if url not in seen:
            seen.add(url)
            deduped.append((url, dt))
    return deduped[:30]


# ─── Bloomberg Technoz ──────────────────────────────────────────

async def get_links_bloomberg_technoz(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Bloomberg Technoz — Indonesian business, tech, economy news."""
    items = []
    try:
        r = await client.get("https://www.bloombergtechnoz.com/rss", timeout=12)
        from email.utils import parsedate_to_datetime
        for item_block in re.finditer(r"<item>(.*?)</item>", r.text, re.DOTALL):
            block = item_block.group(1)
            link_m = re.search(r"<link>([^<]+)</link>", block)
            date_m = re.search(r"<pubDate>([^<]+)</pubDate>", block)
            if not link_m:
                continue
            url = link_m.group(1).strip()
            dt = None
            if date_m:
                try:
                    dt = parsedate_to_datetime(date_m.group(1).strip()).astimezone(UTC)
                except Exception:
                    pass
            items.append((url, dt))
    except Exception:
        pass
    return items[:20]


# ─── News Sources (kept for mix) ───────────────────────────────

async def get_links_cnbc_indonesia(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """CNBC Indonesia Tech — startup, fintech, crypto, regulasi."""
    return await _rss_links(client, "https://www.cnbcindonesia.com/tech/rss")


async def get_links_detik_inet(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Detik Inet — gadget, internet, social media, viral tech."""
    items = []
    try:
        r = await client.get("https://inet.detik.com/rss", timeout=12)
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
                    dt = parsedate_to_datetime(date_m.group(1).strip()).astimezone(UTC)
                except Exception:
                    pass
            items.append((url, dt))
    except Exception:
        pass
    return items[:20]


async def get_links_liputan6_tekno(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Liputan6 Tekno — gadget, apps, viral tech stories."""
    items = []
    try:
        r = await client.get("https://www.liputan6.com/tekno/rss", timeout=12)
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
                    dt = parsedate_to_datetime(date_m.group(1).strip()).astimezone(UTC)
                except Exception:
                    pass
            items.append((url, dt))
    except Exception:
        pass
    return items[:20]


async def get_links_kumparan_tekno(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Kumparan Tekno — Indonesian tech news, startup ecosystem."""
    items = []
    try:
        r = await client.get("https://kumparan.com/feed/tekno", timeout=12)
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
                    dt = parsedate_to_datetime(date_m.group(1).strip()).astimezone(UTC)
                except Exception:
                    pass
            items.append((url, dt))
    except Exception:
        pass
    return items[:20]


async def get_links_antara_tekno(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Antara News Tekno — Indonesian tech/gadget news."""
    items = []
    try:
        r = await client.get("https://www.antaranews.com/rss/tekno", timeout=12)
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
                    dt = parsedate_to_datetime(date_m.group(1).strip())
                except Exception:
                    pass
            items.append((url, dt))
    except Exception:
        pass
    return items[:20]


async def get_links_republika_tekno(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Republika Tekno — Indonesian tech news."""
    items = []
    try:
        r = await client.get("https://www.republika.co.id/rss/tekno", timeout=12)
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
                    dt = parsedate_to_datetime(date_m.group(1).strip())
                except Exception:
                    pass
            items.append((url, dt))
    except Exception:
        pass
    return items[:20]


async def get_links_cnnindonesia_tekno(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """CNN Indonesia Tekno — fresh Indonesian tech news (100+ items)."""
    items = []
    try:
        r = await client.get("https://www.cnnindonesia.com/teknologi/rss", timeout=12)
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
                    dt = parsedate_to_datetime(date_m.group(1).strip()).astimezone(UTC)
                except Exception:
                    pass
            items.append((url, dt))
    except Exception:
        pass
    return items[:20]


async def get_links_merdeka_tekno(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Merdeka Tekno — Indonesian tech/gadget/digital news via RSS."""
    items = []
    try:
        r = await client.get("https://www.merdeka.com/rss/teknologi", timeout=12)
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
                    dt = parsedate_to_datetime(date_m.group(1).strip())
                except Exception:
                    pass
            items.append((url, dt))
    except Exception:
        pass
    return items[:20]


async def get_links_kompas_tekno(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Kompas Tekno — biggest Indonesian news portal, direct scrape (no RSS)."""
    items = []
    try:
        r = await client.get("https://tekno.kompas.com/", timeout=12)
        soup = BeautifulSoup(r.text, "html.parser")
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            title = a.get_text().strip()
            if "/read/" in href and title and len(title) > 20 and href not in seen:
                seen.add(href)
                items.append((href.split("?")[0], None))
    except Exception:
        pass
    return items[:20]


async def get_links_hipwee(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Hipwee — tips, life hacks, productivity, lifestyle for Gen Z/Millennials."""
    items = []
    try:
        r = await client.get("https://www.hipwee.com/feed/", timeout=12)
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
                    dt = parsedate_to_datetime(date_m.group(1).strip()).astimezone(UTC)
                except Exception:
                    pass
            items.append((url, dt))
    except Exception:
        pass
    return items[:20]


async def get_links_youtube_trending(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """YouTube trending — search for Indonesian tips/lifehack/book insight videos.
    Uses yt-dlp to search YouTube and returns video URLs as 'articles'.
    Title is used as body content for scoring; URL links to the video.
    """
    import subprocess
    items = []
    queries = [
        "ytsearch5:tips and tricks Indonesia",
        "ytsearch5:life hack Indonesia",
        "ytsearch5:insight buku Indonesia",
        "ytsearch5:tips produktivitas Indonesia",
        "ytsearch5:ringkasan buku populer Indonesia",
    ]
    try:
        for query in queries:
            result = subprocess.run(
                ["yt-dlp", "--flat-playlist", "--print", "%(title)s|||%(id)s|||%(upload_date)s", query],
                capture_output=True, text=True, timeout=30
            )
            for line in result.stdout.strip().split("\n"):
                if "|||" not in line:
                    continue
                parts = line.split("|||")
                if len(parts) < 2:
                    continue
                title, vid_id = parts[0].strip(), parts[1].strip()
                if not vid_id or len(title) < 10:
                    continue
                url = f"https://www.youtube.com/watch?v={vid_id}"
                items.append((url, None))
    except Exception:
        pass
    # Dedupe by URL
    seen = set()
    deduped = []
    for url, dt in items:
        if url not in seen:
            seen.add(url)
            deduped.append((url, dt))
    return deduped[:15]


async def scrape_youtube_oembed(url: str, client: httpx.AsyncClient) -> dict | None:
    """Scrape YouTube video info via oEmbed API (no auth needed)."""
    try:
        oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
        r = await client.get(oembed_url, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        title = data.get("title", "")
        if not title:
            return None
        # Use title as body for scoring (YouTube descriptions need auth)
        body = title
        return {
            "title": title, "date": None, "image": data.get("thumbnail_url", ""),
            "body": body, "url": url, "source": "youtube",
        }
    except Exception:
        return None


# ─── Main Scraper ────────────────────────────────────────────────

async def scrape_all_async(top_n: int = TOP_N) -> list[dict]:
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        # 1. Gather links from RSS feeds + HN
        # Priority: Indonesian sources first, global for breaking news
        link_tasks = await asyncio.gather(
            get_links_bbc(client),
            get_links_bloomberg_technoz(client),
        )

        # 2. Build scrape tasks
        all_tasks = []
        seen_urls = set()

        for src, links in zip(SOURCE_NAMES, link_tasks):
            if not isinstance(links, list) or not links:
                continue
            for item in links:
                if isinstance(item, tuple) and len(item) == 3:
                    url, rss_date = item
                elif isinstance(item, tuple) and len(item) == 2:
                    url, rss_date = item
                else:
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                all_tasks.append(
                    scrape_article_async(url, client, src, rss_date=rss_date)
                )

        # 3. Scrape all articles
        results = await asyncio.gather(*all_tasks, return_exceptions=True)

    # 4. Score and sort with source diversity
    articles = []
    seen = set()
    for art in results:
        if not isinstance(art, dict):
            continue
        if art["url"] in seen:
            continue
        seen.add(art["url"])
        art["score"] = score_article(art["title"], art["body"], art["date"])
        if art["score"] > 10:
            articles.append(art)

    # 5. Cross-source virality: if same topic in 2+ sources, boost
    topic_map: dict[str, list[dict]] = {}
    for art in articles:
        # Extract key topic words from title (skip common words)
        words = set(re.findall(r'\b[a-z]{4,}\b', art["title"].lower()))
        stop = {"this", "that", "with", "from", "have", "been", "will", "more", "than", "about", "just", "into", "your", "they", "their", "what", "when", "which", "were", "also", "could", "would", "should", "like", "very", "most", "some", "only"}
        keywords = {_stem(w) for w in words - stop}
        matched = False
        for topic, group in topic_map.items():
            topic_words = set(topic.split())
            # Jaccard similarity > 0.3 = same topic (words already stemmed)
            if keywords and topic_words and len(keywords & topic_words) / max(len(keywords | topic_words), 1) > 0.3:
                group.append(art)
                matched = True
                break
        if not matched:
            topic_map[" ".join(sorted(keywords)[:5])] = [art]

    # Boost articles that appear in 2+ sources
    for topic, group in topic_map.items():
        if len(group) >= 2:
            sources = set(art["source"] for art in group)
            if len(sources) >= 2:
                for art in group:
                    art["score"] = min(art["score"] + 30, 150)
                    art["virality"] = f"cross-source ({len(sources)} sources)"

    articles.sort(key=lambda x: x["score"], reverse=True)

    # Source diversity: max 2 per source
    diversified = []
    source_count: dict[str, int] = {}
    MAX_PER_SOURCE = 2
    for art in articles:
        src = art["source"]
        if source_count.get(src, 0) < MAX_PER_SOURCE:
            diversified.append(art)
            source_count[src] = source_count.get(src, 0) + 1
        if len(diversified) >= top_n:
            break
    return diversified


def scrape_all(top_n: int = TOP_N) -> list[dict]:
    return asyncio.run(scrape_all_async(top_n))


if __name__ == "__main__":
    import sys, time
    n = int(sys.argv[1]) if len(sys.argv) > 1 else TOP_N
    t0 = time.time()
    results = scrape_all(n)
    elapsed = time.time() - t0
    src_label = {
        "techcrunch": "TechCrunch", "theverge": "The Verge",
        "arstechnica": "Ars Technica", "wired": "Wired", "hn": "Hacker News",
        "anthropic": "Anthropic",
    }
    for i, art in enumerate(results, 1):
        dt_str = art["date"].strftime("%Y-%m-%d %H:%M UTC") if art["date"] else "unknown date"
        print(f"\n{'='*60}")
        print(f"#{i} [{src_label.get(art['source'], art['source'])}] score={art['score']}")
        print(f"Title : {art['title']}")
        print(f"Date  : {dt_str}")
        print(f"Image : {art['image'][:100]}")
        print(f"URL   : {art['url']}")
        print(f"Body  : {len(art['body'])} chars")
        print(f"Preview:\n{art['body'][:400]}...")
    if not results:
        print("No articles found.")
    print(f"\nDone in {elapsed:.1f}s")
