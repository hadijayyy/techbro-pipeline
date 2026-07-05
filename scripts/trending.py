"""
trending.py — Drama-driven content finder.
Two strategies:
1. GENERAL DRAMA: scan trending topics, find niche angle
2. ARTICLE DRAMA: scan our articles, flag ones with drama signals
"""

import httpx, re, xml.etree.ElementTree as ET

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

# Drama signals (Indonesian + English)
DRAMA_SIGNALS = [
    "viral", "heboh", "kontroversial", "korban", "banting setir",
    "nganggur", "dipecat", "resign", "PHK", "gugat", "skandal",
    "bongkar", "terungkap", "ternyata", "buktikan", "kejutan",
    "marah", "protes", "boikot", "gagal", "kolaps", "anjlok",
    "rusak", "hancur", "didenda", "dituntut", "ditangkap",
    "layoff", "scandal", "controversy", "exposed", "shutdown",
    "ban", "fired", "bankrupt", "crisis", "whistleblower",
]

# Broad niche patterns (word-boundary to avoid false positives)
NICHE_PATTERNS = [
    r'\bAI\b', r'\bChatGPT\b', r'\bOpenAI\b', r'\bMistral\b',
    r'\bGoogle\b', r'\bMeta\b', r'\bMicrosoft\b', r'\bApple\b',
    r'\bdeepfake\b', r'\brobot\b', r'\botomasi\b', r'\bteknologi\b',
    r'\bstartup\b', r'\binvestasi\b', r'\bsaham\b', r'\bkripto\b',
    r'\binflasi\b', r'\bCEO\b', r'\bgaji\b', r'\bpajak\b',
    r'\bDDR\d?\b', r'\bchip\b', r'\bsemikonduktor\b',
    r'\bThreads\b', r'\bTikTok\b', r'\bInstagram\b',
    r'\bdata center\b', r'\bcloud\b', r'\balgoritma\b',
    r'\bNFT\b', r'\bblockchain\b', r'\be-commerce\b',
    r'\bTokopedia\b', r'\bShopee\b', r'\bGrab\b', r'\bGojek\b',
    r'\bbank\b', r'\bkredit\b', r'\bpinjol\b', r'\bcrypto\b',
    r'\binternet\b', r'\bWiFi\b', r'\bHP\b', r'\blaptop\b',
    r'\baplikasi\b', r'\bplatform\b', r'\bdigital\b',
    r'\bkomputer\b', r'\bprogram\b', r'\bcoding\b',
]


def _has_drama(text: str) -> list[str]:
    """Find drama signal matches (word boundary)."""
    return [kw for kw in DRAMA_SIGNALS if re.search(r'\b' + re.escape(kw.lower()) + r'\b', text.lower())]


def _find_niche(text: str) -> list[str]:
    return [pat.replace(r'\b', '') for pat in NICHE_PATTERNS
            if re.search(pat, text, re.IGNORECASE)]


def score_topic(title: str, body: str = "") -> tuple[int, str]:
    """
    Score a topic for drama + niche relevance.
    High score = strong drama + strong niche.
    """
    text = f"{title} {body}"
    drama = _has_drama(text)
    niche = _find_niche(text)

    score = len(drama) * 10 + len(niche) * 15
    if drama and niche:
        score += 30  # COMBO bonus
    
    parts = []
    if drama:
        parts.append(f"drama:{','.join(drama[:2])}")
    if niche:
        parts.append(f"niche:{','.join(niche[:2])}")
    if drama and niche:
        parts.append("COMBO")
    
    return score, " | ".join(parts) if parts else "none"


# ─── Source 1: Google Trends Indonesia ───
def fetch_google_trends(limit: int = 30) -> list[dict]:
    try:
        r = httpx.get("https://trends.google.com/trending/rss?geo=ID", timeout=15)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
        results = []
        for item in root.findall('.//item')[:limit]:
            title = item.find('title')
            traffic = item.find('{http://schemas.google.com/trends/2022}approx_traffic')
            news = item.findall('{http://schemas.google.com/trends/2022}news_item')
            news_links = []
            for n in news:
                n_title = n.find('{http://schemas.google.com/trends/2022}news_item_title')
                n_url = n.find('{http://schemas.google.com/trends/2022}news_item_url')
                if n_title is not None and n_url is not None:
                    news_links.append({"title": n_title.text, "url": n_url.text})
            results.append({
                "topic": title.text if title is not None else "",
                "traffic": traffic.text if traffic is not None else "N/A",
                "news": news_links,
                "source": "google_trends",
            })
        return results
    except Exception:
        return []


# ─── Source 2: Detik Trending ───
def fetch_detik_trending(limit: int = 20) -> list[dict]:
    try:
        r = httpx.get("https://www.detik.com/terpopuler", timeout=15, headers=HEADERS)
        if r.status_code != 200:
            return []
        results = []
        seen = set()
        for url, title in re.findall(r'<a[^>]+href="(https://[^"]*detik\.com/[^"]*)"[^>]*>([^<]+)</a>', r.text):
            title = title.strip()
            if len(title) < 20 or title in seen:
                continue
            seen.add(title)
            results.append({"topic": title, "url": url, "source": "detik_trending"})
            if len(results) >= limit:
                break
        return results
    except Exception:
        return []


# ─── Source 3: CNBC Indonesia ───
def fetch_cnbc_trending(limit: int = 15) -> list[dict]:
    try:
        r = httpx.get("https://www.cnbcindonesia.com/most-popular", timeout=15, headers=HEADERS)
        if r.status_code != 200:
            return []
        results = []
        seen = set()
        for url, title in re.findall(r'<a[^>]+href="(https://[^"]*cnbcindonesia\.com/[^"]*)"[^>]*>([^<]{15,})</a>', r.text):
            title = title.strip()
            if title in seen:
                continue
            seen.add(title)
            results.append({"topic": title, "url": url, "source": "cnbc_trending"})
            if len(results) >= limit:
                break
        return results
    except Exception:
        return []


# ─── Source 4: Google News Indonesia ───
def fetch_google_news(limit: int = 20) -> list[dict]:
    """Fetch from Google News Indonesia general feed, filter for tech/drama."""
    try:
        r = httpx.get(
            "https://news.google.com/rss?hl=id&gl=ID&ceid=ID:id",
            timeout=15, headers=HEADERS
        )
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
        results = []
        for item in root.findall('.//item')[:limit]:
            title = item.find('title')
            link = item.find('link')
            if title is not None:
                results.append({
                    "topic": title.text or "",
                    "url": link.text if link is not None else "",
                    "source": "google_news",
                })
        return results
    except Exception:
        return []


def detect_dramas(min_score: int = 15) -> list[dict]:
    """
    Detect trending dramas relevant to niche.
    min_score=15 catches topics with either strong drama OR strong niche.
    """
    print("[trending] Scanning 4 sources...")
    
    all_topics = []
    all_topics.extend(fetch_google_trends())
    all_topics.extend(fetch_detik_trending())
    all_topics.extend(fetch_cnbc_trending())
    all_topics.extend(fetch_google_news())
    
    print(f"[trending] {len(all_topics)} raw topics")
    
    scored = []
    seen = set()
    for t in all_topics:
        title = t["topic"]
        if title in seen or not title:
            continue
        seen.add(title)
        
        score, reason = score_topic(title)
        if score >= min_score:
            t["drama_score"] = score
            t["drama_reason"] = reason
            scored.append(t)
    
    scored.sort(key=lambda x: x["drama_score"], reverse=True)
    
    print(f"[trending] {len(scored)} candidates (threshold={min_score})")
    for s in scored[:5]:
        print(f"  [{s['drama_score']}] {s['topic'][:60]} — {s['drama_reason']}")
    
    return scored


# ─── Article Drama Scoring ───
def score_article_drama(title: str, body: str) -> tuple[int, str]:
    """
    Score an article for drama potential.
    Higher weight on body content (more substance).
    """
    drama_title = _has_drama(title)
    drama_body = _has_drama(body)
    niche_title = _find_niche(title)
    niche_body = _find_niche(body)
    
    score = 0
    score += len(drama_title) * 15  # drama in title = stronger signal
    score += len(drama_body) * 5
    score += len(niche_title) * 10
    score += len(niche_body) * 5
    
    # COMBO: drama + niche in same article
    all_drama = drama_title + drama_body
    all_niche = niche_title + niche_body
    if all_drama and all_niche:
        score += 30
    
    parts = []
    if all_drama:
        parts.append(f"drama:{','.join(list(set(all_drama))[:3])}")
    if all_niche:
        parts.append(f"niche:{','.join(list(set(all_niche))[:3])}")
    
    return score, " | ".join(parts) if parts else "none"


if __name__ == "__main__":
    print("=" * 50)
    print("STRATEGY 1: General Trending Drama")
    print("=" * 50)
    dramas = detect_dramas()
    
    print(f"\n{'=' * 50}")
    print("STRATEGY 2: Test Article Drama Scoring")
    print("=" * 50)
    
    test_articles = [
        ("Aktor China Jadi Korban AI, Banting Setir Jualan Sayur", 
         "Kemajuan AI mengubah industri hiburan. Aktor Xu Peng kehilangan pekerjaan."),
        ("Investor Berlindung ke Bursa India Menyusul Badai AI",
         "Saham India jadi safe haven. Investor kabur dari saham AI."),
        ("Microsoft PHK 10.000 Karyawan, Fokus ke AI",
         "Microsoft umumkan PHK besar-besaran. Perusahaan fokus ke pengembangan AI."),
        ("Tips Pakai ChatGPT buat Kerja",
         "Cara pakai ChatGPT untuk produktivitas."),
    ]
    
    for title, body in test_articles:
        score, reason = score_article_drama(title, body)
        print(f"  [{score:3d}] {title[:50]} — {reason}")
