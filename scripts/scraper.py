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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}

# ─── Scoring Keywords ────────────────────────────────────────────

# TIER1 = hot topics yang bikin orang Indonesia PEDULI (title 3x weight)
TIER1 = [
    # AI Impact (emosional: takut kehilangan kerja)
    "phk", "layoff", "dirumahkan", "ai replace", "ai gantikan", "automasi",
    "kehilangan pekerjaan", "ai jobs", "ai agent", "vibe coding",
    # AI Models & Companies (masih menarik)
    "openai", "anthropic", "claude", "gpt", "gemini", "deepseek", "llama",
    "mistral", "chatgpt", "copilot", "cursor", "ai model", "llm", "agi",
    # Startup Indonesia (relatable: Gojek, Tokopedia, etc.)
    "gojek", "tokopedia", "traveloka", "bukalapak", "blibli", "shopee",
    "tiktok shop", "grab", "sea group", "goto", "startup indonesia",
    "unicorn", "decacorn",
    # Fintech & Money (emosional: duit)
    "pinjol", "fintech", "ovo", "gopay", "dana", "shopeepay",
    "kripto", "crypto", "investasi", "saham", "digital bank",
    # Regulation & Drama
    "kominfo", "pse", "blokir", "sensor", "uu ite", "peraturan",
    "pelanggaran data", "data bocor", "privasi",
]

# TIER2 = tech adjacent (masih relate)
TIER2 = [
    # Indonesian Economy & Tech
    "startup", "funding", "pendanaan", "valuasi", "ipo", "akuisisi",
    "umkm", "digital", "transformasi digital", "ecommerce", "e-commerce",
    "remote work", "wfh", "wfo", "hybrid", "freelance", "side hustle",
    # Global Tech (tetap menarik kalo ada angle Indonesia)
    "semiconductor", "chip", "gpu", "nvidia", "apple", "google", "meta",
    "microsoft", "amazon", "tesla", "spacex",
    # Security
    "cybersecurity", "hack", "breach", "malware", "scam", "penipuan",
    "phishing",
    # Social & Content
    "tiktok", "instagram", "threads", "twitter", "x", "youtube",
    "influencer", "content creator", "monetisasi",
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


def score_article(title: str, body: str, date=None, hn_score: int = 0) -> int:
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
    if hn_score > 0:
        s += min(hn_score // 5, 100)

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
    r"Posts from this topic will be added to your daily email digest",
    r"Posts from this author will be added to your daily email digest",
    r"Subscribe to.*newsletter",
    r"Sign up for.*newsletter",
    r"Read more about.*on.*TechCrunch",
    r"Featured Video.*From.*Sponsor",
    r"Advertisement\b",
    r"^Related:.*$",
    r"^See also:.*$",
    r"© \d{4}.*All rights reserved",
    r"Any references to my blogs must be accompanied",
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
                                hn_score: int = 0) -> dict | None:
    try:
        r = await client.get(url, timeout=15)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        title_tag = soup.find("h1")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            return None

        dt = rss_date or parse_date_iso(
            (soup.find("meta", property="article:published_time") or {}).get("content", "")
        )

        if not is_fresh(dt, hours=FALLBACK_HOURS):
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
        elif source in ("cnbc_id", "detik", "liputan6", "kumparan"):
            # Indonesian sources: try common selectors
            _ID_SEL = [
                ("div", "detail-text"), ("div", "article-content"),
                ("div", "content-text"), ("div", "read__content"),
                ("div", "article_body"), ("article", None),
            ]
            body = extract_body(soup, _ID_SEL)
        else:
            return None

        if not body or len(body) < 300:
            return None

        body = clean_body(body)

        return {
            "title": title, "date": dt, "image": image,
            "body": body, "url": url, "source": source,
            "_hn_score": hn_score,
        }
    except Exception:
        return None


# ─── RSS Parsers ─────────────────────────────────────────────────

async def get_links_techcrunch(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    items = []
    try:
        r = await client.get("https://techcrunch.com/category/artificial-intelligence/feed/", timeout=12)
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
    return items[:30]


async def get_links_theverge(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """The Verge Atom feed."""
    items = []
    try:
        r = await client.get("https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", timeout=12)
        for entry in re.finditer(r"<entry>(.*?)</entry>", r.text, re.DOTALL):
            block = entry.group(1)
            link_m = re.search(r'<link[^>]*href="([^"]+)"', block)
            date_m = re.search(r"<published>(.*?)</published>", block)
            if not link_m:
                continue
            url = link_m.group(1).strip()
            dt = parse_date_iso(date_m.group(1).strip()) if date_m else None
            items.append((url, dt))
    except Exception:
        pass
    return items[:20]


async def get_links_arstechnica(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    items = []
    try:
        r = await client.get("https://feeds.arstechnica.com/arstechnica/technology-lab", timeout=12)
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


async def get_links_wired(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    items = []
    try:
        r = await client.get("https://www.wired.com/feed/tag/ai/latest/rss", timeout=12)
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


async def get_links_hn(client: httpx.AsyncClient) -> list[tuple[str, datetime | None, int]]:
    """Hacker News top stories via Firebase API. Returns (url, date, hn_score)."""
    items = []
    try:
        r = await client.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10)
        ids = json.loads(r.text)[:30]

        async def fetch_hn(hn_id):
            try:
                r2 = await client.get(f"https://hacker-news.firebaseio.com/v0/item/{hn_id}.json", timeout=5)
                return json.loads(r2.text)
            except Exception:
                return None

        tasks = [fetch_hn(i) for i in ids]
        results = await asyncio.gather(*tasks)

        for item in results:
            if not item or item.get("type") != "story":
                continue
            url = item.get("url", "")
            if not url:
                continue
            # Skip HN self-posts and non-article links
            if "news.ycombinator.com" in url:
                continue
            score = item.get("score", 0)
            # Convert HN time to datetime
            dt = datetime.fromtimestamp(item.get("time", 0), tz=UTC)
            items.append((url, dt, score))
    except Exception:
        pass
    return items[:20]


# ─── Main Scraper ────────────────────────────────────────────────

async def get_links_cnbc_indonesia(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """CNBC Indonesia Tech — startup, fintech, crypto, regulasi."""
    items = []
    try:
        r = await client.get("https://www.cnbcindonesia.com/tech/rss", timeout=12)
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


# ─── Main Scraper ────────────────────────────────────────────────

async def scrape_all_async(top_n: int = TOP_N) -> list[dict]:
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        # 1. Gather links from RSS feeds + HN
        # Priority: Indonesian sources first, global for breaking news
        link_tasks = await asyncio.gather(
            get_links_cnbc_indonesia(client),
            get_links_detik_inet(client),
            get_links_liputan6_tekno(client),
            get_links_kumparan_tekno(client),
            get_links_hn(client),
            get_links_techcrunch(client),
            return_exceptions=True,
        )
        source_names = ["cnbc_id", "detik", "liputan6", "kumparan", "hn", "techcrunch"]

        # 2. Build scrape tasks
        all_tasks = []
        seen_urls = set()

        for src, links in zip(source_names, link_tasks):
            if not isinstance(links, list) or not links:
                continue
            for item in links:
                if isinstance(item, tuple) and len(item) == 3:
                    url, rss_date, hn_score = item
                elif isinstance(item, tuple) and len(item) == 2:
                    url, rss_date = item
                    hn_score = 0
                else:
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                all_tasks.append(
                    scrape_article_async(url, client, src, rss_date=rss_date, hn_score=hn_score)
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
        hn_score = art.pop("_hn_score", 0)
        art["score"] = score_article(art["title"], art["body"], art["date"], hn_score)
        if art["score"] > 20:
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
