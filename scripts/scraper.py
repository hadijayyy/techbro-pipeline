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
MAX_AGE_HOURS = 24
FALLBACK_HOURS = 48  # fallback if 24h yields nothing
TOP_N = 1

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}

# ─── Scoring Keywords ────────────────────────────────────────────

# TIER1 = hot AI/tech topics (title 3x weight)
TIER1 = [
    # AI Models & Companies
    "openai", "anthropic", "claude", "gpt", "gemini", "deepseek", "llama",
    "mistral", "cohere", "ai model", "language model", "llm", "agi",
    # AI Products & Tools
    "copilot", "cursor", "midjourney", "sora", "chatgpt", "ai agent",
    "ai assistant", "ai tool", "ai coding", "vibe coding",
    # AI Impact
    "ai replace", "ai jobs", "layoff", "automation", "phk",
    "deepfake", "ai safety", "ai regulation", "ai ethics",
    # Big Tech AI
    "nvidia", "apple ai", "google ai", "meta ai", "microsoft ai",
    "amazon ai", "samsung ai",
    # Hot Topics
    "ai chip", "gpu", "quantum", "robot", "humanoid",
    "ai startup", "ai funding", "ai valuation",
]

# TIER2 = tech adjacent
TIER2 = [
    "semiconductor", "chip", "data center", "cloud", "saas",
    "startup", "venture capital", "funding", "ipo", "valuation",
    "privacy", "cybersecurity", "hack", "breach", "encryption",
    "crypto", "blockchain", "web3",
    "remote work", "productivity", "workflow",
    "open source", "developer", "engineering",
]

# TIER3 = generic tech
TIER3 = [
    "technology", "innovation", "digital", "platform", "app",
    "software", "hardware", "tech", "silicon valley",
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


def _unique_matches(text: str, keywords: set) -> int:
    count = 0
    for kw in keywords:
        if len(kw) <= 4:
            if re.search(r'\b' + re.escape(kw) + r'\b', text):
                count += 1
        elif kw in text:
            count += 1
    return count


def score_article(title: str, body: str, date=None, hn_score: int = 0) -> int:
    title_l = title.lower()
    body_l = body[:1000].lower()
    text = title_l + " " + body_l

    if _EXCL_RE.search(text):
        return 0

    penalty_hits = len(set(_PENALTY_RE.findall(text)))
    if penalty_hits >= 3:
        return 0

    t1 = _unique_matches(title_l, _TIER1_SET) * 30 + _unique_matches(body_l, _TIER1_SET) * 15
    t2 = _unique_matches(title_l, _TIER2_SET) * 12 + _unique_matches(body_l, _TIER2_SET) * 5
    t3 = _unique_matches(title_l, _TIER3_SET) * 4 + _unique_matches(body_l, _TIER3_SET) * 2

    s = t1 + t2 + t3

    # HN virality bonus
    if hn_score > 0:
        s += min(hn_score // 10, 50)  # cap at +50

    # Recency: exponential decay. 0h = +30, 24h = +0
    if date:
        hours_old = (datetime.now(UTC) - date).total_seconds() / 3600
        recency_bonus = max(0, 30 - int(hours_old * 30 / MAX_AGE_HOURS))
        s += recency_bonus

    return max(0, min(s, 100))


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
    r"Subscribe to.*newsletter",
    r"Sign up for.*newsletter",
    r"Read more about.*on.*TechCrunch",
    r"Featured Video.*From.*Sponsor",
    r"Advertisement\b",
    r"^Related:.*$",
    r"^See also:.*$",
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
_ANTHROPIC_SEL = [
    ("article", None),
    ("main", None),
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
            body = extract_body(soup, _ANTHROPIC_SEL)
        else:
            return None

        if not body or len(body) < 200:
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

async def scrape_all_async(top_n: int = TOP_N) -> list[dict]:
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        # 1. Gather links from RSS feeds + HN
        link_tasks = await asyncio.gather(
            get_links_techcrunch(client),
            get_links_theverge(client),
            get_links_arstechnica(client),
            get_links_wired(client),
            get_links_hn(client),
            return_exceptions=True,
        )
        source_names = ["techcrunch", "theverge", "arstechnica", "wired", "hn"]

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

    # 4. Score and sort
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
