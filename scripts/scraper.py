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
MAX_AGE_HOURS = 720  # 30 days max
FALLBACK_HOURS = 720  # same for fallback
TOP_N = 1

# Source names used by scrape_all_async — single of truth
SOURCE_NAMES = [
    "detik", "mark_manson", "james_clear", "ryan_holiday",
    "darius_foroux", "scott_young"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}

# ─── Scoring Keywords (1% Better Niche) ───────────────────────

# TIER1 = core niche — scored 3x on title match
TIER1 = [
    # Mindset & Self-Development
    "mindset", "pola pikir", "pemikiran", "perspektif", "sudut pandang",
    "motivasi", "inspirasi", "semangat", "kemauan", "tekad",
    "disiplin", "konsisten", "istiqomah", "komitmen",
    "discipline", "consistency", "commitment", "dedication",
    "kebiasaan", "habits", "rutinitas", "daily routine",
    "habit", "routine", "practice", "ritual",
    "produktif", "produktivitas", "efisien", "efektif",
    "productive", "productivity", "efficient", "effective",
    "fokus", "konsentrasi", "perhatian",
    "focus", "concentration", "attention", "deep work",
    # Personal Growth
    "pengembangan diri", "self improvement", "self development",
    "personal growth", "personal development", "self help", "self mastery",
    "belajar", "belajar hal baru", "upgrade diri",
    "learn", "learning", "study", "skill", "skills",
    "goal setting", "tujuan", "target", "mimpi", "visi",
    "goal", "goals", "ambition", "vision", "aspire",
    "resolusi", "perubahan", "transformasi",
    "change", "growth", "improve", "improvement", "transform",
    # Ikigai & Purpose
    "ikigai", "tujuan hidup", "purpose", "makna hidup",
    "meaning", "meaningful", "fulfillment", "calling", "passion",
    "passion", "minat", "bakat", "potensi",
    # Life Hacks & Tips
    "life hack", "tips", "trik", "cara", "panduan", "tutorial",
    "hack", "shortcut", "trick", "guide", "method",
    "langkah", "strategi", "metode", "teknik", "framework",
    "strategy", "technique", "framework", "system", "step",
    # Productivity
    "time management", "manajemen waktu", "prioritas",
    "priority", "schedule", "planning", "organize",
    "to do list", "checklist", "deadline",
    "prokrastinasi", "procrastination", "menunda",
    "procrastinate", "lazy", "laziness", "delay", "overcome",
    # Mindset Shifts
    "reframe", "perspektif baru", "cara pikir baru",
    "mindset shift", "ubah pola pikir",
    "growth mindset", "fixed mindset",
    "mindset", "perspective", "paradigm",
    # Stoicism & Philosophy
    "stoic", "stoicism", "filosofi", "kebijaksanaan", "wisdom",
    "sabar", "ikhlas", "redha", "tawakal",
    "philosophy", "virtue", "resilience", "endure", "patience",
    # Success & Failure
    "gagal", "kegagalan", "sukses", "keberhasilan",
    "resilien", "ketahanan", "bangkit", "pantang menyerah",
    "success", "failure", "fail", "resilient", "bounce back", "persevere",
    "win", "winner", "achieve", "achievement", "overcome",
    # Habits & Systems
    "atomic habits", "kebiasaan kecil", "1% better",
    "compound effect", "efek majemuk",
    "sistem", "proses", "jalur",
    "system", "process", "compound", "tiny", "marginal", "incremental",
    # Work & Career
    "work", "career", "job", "money", "rich", "wealth",
    "kerjakeras", "hard work", "burnout", "hustle",
    "audience", "brand", "personal brand", "marketing",
    "entrepreneur", "business", "startup", "freelance",
]

TIER2 = [
    # Wellness & Mental Health
    "kesehatan mental", "mental health", "anxiety", "stres",
    "burnout", "kelelahan", "work life balance",
    "meditasi", "mindfulness", "jurnal", "refleksi",
    "mental", "wellbeing", "well-being", "wellness", "meditation", "journal",
    "stress", "exhaustion", "tired", "overwhelm", "calm",
    # Books & Learning
    "buku", "membaca", "literasi", "pengetahuan",
    "podcast", "audiobook", "kursus", "pelatihan",
    "book", "reading", "read", "knowledge", "course", "lesson", "insight",
    # Finance (mindset angle)
    "keuangan", "uang", "duit", "investasi", "nabung",
    "dana darurat", "budgeting", "financial freedom",
    "money", "save", "saving", "invest", "wealth", "financial", "budget", "income",
    # Career (growth angle)
    "karier", "karir", "pekerjaan", "kerja",
    "skill", "keahlian", "kompetensi",
    "interview", "cv", "resume", "linkedin",
    "career", "job", "work", "hire", "salary", "promotion", "employer",
    # Relationships & Social
    "hubungan", "relasi", "komunikasi",
    "leadership", "kepemimpinan", "influence",
    "relationship", "communicate", "lead", "leader", "influence", "social",
    # Digital & Tech (life angle)
    "digital detox", "screen time", "teknologi",
    "sosial media", "dopamine", "kecanduan",
    "phone", "phone addiction", "distraction", "productive", "unplug",
]

# TIER3 = general support — scored 1x
TIER3 = [
    "tips", "trik", "cara", "panduan", "tutorial", "langkah",
    "strategi", "rahasia", "ternyata", "kesalahan umum",
    "peluang", "kesempatan", "tren", "perubahan", "dampak",
    "studi", "riset", "data", "fakta", "angka", "survey",
    "indonesia", "jakarta", "ri", "nasional",
    "rahasia", "ternyata", "kesalahan umum", "mistake",
]

# PENALTY = off-brand / low value
PENALTY = [
    "unboxing", "hands-on", "review:", "buying guide", "gift guide",
    "coupon", "discount", "earbuds", "earphone", "headphone", "smartphone review",
    "battery life test", "benchmark score",
    "zodiak", "horoscope", "gossip", "celebrity",
    # Low-relatability for Indonesian audience
    "wall street", "silicon valley", "us election", "senate", "congress",
    "eu regulation", "gdpr", "dma", "ftc",
    "developer conference", "hackathon", "tech meetup",
    "patent", "ip lawsuit", "copyright strike",
    "data center", "cloud infrastructure", "server rack",
    "quantum computing", "edge computing", "web3", "metaverse",
    "nft", "blockchain", "crypto mining", "dao", "defi",
    "robotics", "lidar", "autonomous vehicle", "self-driving",
]

# EXCLUDE = auto-reject
EXCLUDE = [
    "sports score", "match schedule", "recipe", "cooking",
    "fashion week", "beauty tips", "weight loss",
    "pokemon", "genshin", "wuthering waves", "volleyball legends",
    "mobile legends", "free fire", "pubg", "valorant", "fortnite",
    "roblox", "minecraft", "gacha", "gameplay", "let's play",
    "taylor swift", "travis kelce", "wedding", "married", "concert", "tour", "album",
    "election", "parliament", "war", "sanctions",
    # Product launches / self-promo from blog sources
    "we're launching", "i've been building", "introducing purpose",
    "meet purpose", "sign up for purpose", "try purpose",
    "i'm launching", "we're releasing", "early access",
    "join the waitlist", "get early access", "pre-order now",
    "i'm excited to announce", "proud to announce",
    "today we launch", "today i'm launching",
    "limited time offer", "special discount", "use code",
    "affiliate", "sponsored", "partnership with",
    # Niche dev/infra — gak relate orang Indonesia biasa
    "linux distro", "phosh", "fedora", "arch linux", "ubuntu release", "debian",
    "kernel update", "wayland", "x11", "gnome release", "kde plasma",
    "open source release", "github stars", "npm package", "pypi",
    "rust crate", "golang module", "docker image", "kubernetes",
    "ci/cd", "devops", "terraform", "ansible",
    "programming language", "compiler", "runtime", "sdk release",
    "api documentation", "deprecation notice",
    "series a", "series b", "series c", "funding round", "raises million",
    "raises billion", "seed round", "pre-seed", "valuation",
    "benchmark test", "spec sheet", "processor specs", "chip architecture",
    # Niche product-only — no educational value for most
    "macbook", "macos", "mac only", "iphone 17", "samsung galaxy s27", "samsung galaxy s26",
    "ipad", "apple watch", "airpods", "mac mini", "mac pro", "mac studio",
    "review spesifikasi", "perbandingan spesifikasi", "unboxing",
    # Product rumors & launches — pure consumer news
    "iphone baru", "iphone lipat", "iphone fold", "model iphone", "galaxy z fold",
    "5 model iphone", "iphone 18", "iphone se", "google pixel",
    "hp baru", "smartphone baru", "ponsel baru", "diluncurkan", "luncurkan",
    "meluncurkan", "perkenalkan", "resmi rilis", "resmi dijual",
    "pre-order", "bisa pre order", "bocoran spesifikasi", "spesifikasi bocor",
    # App launches without educational value
    "app launch", "app release", "new app", "app baru",
    "fitur baru", "feature update", "update terbaru", "versi baru",
    "software update", "firmware update", "pembaruan",
    "technical paper", "whitepaper", "rfc", "specification",
    "changelog", "release notes", "patch notes",
    "fork", "pull request", "merge request",
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


def check_article_quality(body: str) -> str | None:
    """3-layer content quality filter (from pressbox). Returns rejection reason or None."""
    text = body.strip()
    # Layer 1: character count (pressbox: 1000)
    if len(text) < 1000:
        return f"too short ({len(text)} chars < 1000)"
    # Layer 2: word count (pressbox: 150)
    words = len(text.split())
    if words < 150:
        return f"too few words ({words} < 150)"
    # Layer 3: sentence count (pressbox: 8, min 20 chars each to count)
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if len(s.strip()) >= 20]
    if len(sentences) < 8:
        return f"too few sentences ({len(sentences)} < 8)"
    return None


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

    # Recency: exponential decay. 0h = +50, 30d = +0
    if date:
        hours_old = (datetime.now(UTC) - date).total_seconds() / 3600
        recency_bonus = max(0, 50 - int(hours_old * 50 / MAX_AGE_HOURS))
        s += recency_bonus

    # Soft cap: diminishing returns above 100 (from pressbox pattern)
    if s > 100:
        s = int(100 + (s - 100) * 0.3)
    return max(0, s)


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
    # Fallback: cari img pertama di body artikel
    body_img = soup.find("article") or soup.find("div", class_=re.compile(r"article|content|post"))
    if body_img:
        img = body_img.find("img")
        if img and img.get("src"):
            return fix_image_url(img["src"].strip())
    return "https://i.imgur.com/placeholder.jpg"  # fallback default
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
        r = await client.get(url, timeout=15)
        if r.status_code not in (200, 202):  # 202 = accepted (Ars Technica)
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

        dt = parse_date_iso(
            (soup.find("meta", property="article:published_time") or {}).get("content", "")
        ) or rss_date  # prefer meta date (more accurate) over RSS date
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
        elif source in ("cnbc_id", "cnbc_id_bisnis", "detik", "detik_finance", "liputan6", "liputan6_bisnis", "kumparan", "antara", "republika", "cnnindonesia", "merdeka", "kompas", "kompas_bisnis", "hipwee"):
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
        elif source == "techcrunch":
            body = extract_body(soup, [("div", "article-content"), ("div", "entry-content"), ("article", None)])
        elif source == "wired":
            body = extract_body(soup, [("div", "body__inner-container"), ("article", None)])
        elif source == "cnbc_id":
            body = extract_body(soup, [("div", "detail-text"), ("article", None)])
        elif source == "detik_inet":
            body = extract_body(soup, [("div", "detail__body-text"), ("div", "detail-text"), ("article", None)])
        elif source in ("liputan6_tekno", "liputan6_bisnis"):
            body = extract_body(soup, [("div", "article-content"), ("div", "read__content"), ("article", None)])
        elif source == "the_verge":
            body = extract_body(soup, [("div", "article-body"), ("div", "caas-body"), ("article", None)])
        elif source == "hn":
            body = extract_body(soup, [("div", "fatitem"), ("article", None), ("td", None)])
        else:
            # Generic fallback
            body = extract_body(soup, [("article", None), ("div", "content"), ("div", "post-content")])

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
    """Generic RSS 2.0 + Atom link extractor. Returns [(url, pub_date|None), ...]."""
    items = []
    try:
        r = await client.get(url, timeout=12, follow_redirects=True)
        text = r.text

        # RSS 2.0: <item> blocks
        for item_block in re.finditer(r"<item>(.*?)</item>", text, re.DOTALL):
            block = item_block.group(1)
            # Try <link> first, then <guid isPermaLink="true">, then any <guid>http...
            link_m = re.search(r"<link>([^<]+)</link>", block)
            if not link_m:
                link_m = re.search(r'<guid[^>]*isPermaLink="true"[^>]*>([^<]+)</guid>', block)
            if not link_m:
                link_m = re.search(r"<guid>(https?://[^<]+)</guid>", block)
            if not link_m:
                continue
            item_url = link_m.group(1).strip().split("?")[0]
            dt = None
            date_m = re.search(r"<pubDate>(.*?)</pubDate>", block)
            if date_m:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(date_m.group(1).strip()).astimezone(UTC)
                except Exception:
                    pass
            items.append((item_url, dt))

        # Atom: <entry> blocks
        if not items:
            for entry_block in re.finditer(r"<entry(.*?)</entry>", text, re.DOTALL):
                block = entry_block.group(1)
                link_m = re.search(r'<link[^>]+href=["\']([^"\']+)["\']', block)
                if not link_m:
                    continue
                item_url = link_m.group(1).strip().split("?")[0]
                dt = None
                for tag in ("updated", "published"):
                    date_m = re.search(rf"<{tag}>([^<]+)</{tag}>", block)
                    if date_m:
                        try:
                            dt = datetime.fromisoformat(date_m.group(1).strip().replace("Z", "+00:00")).astimezone(UTC)
                        except Exception:
                            pass
                        break
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


# ─── Educator Niche Sources ─────────────────────────────────────

async def get_links_techcrunch(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """TechCrunch — AI/tech news, startup stories."""
    return await _rss_links(client, "https://techcrunch.com/feed/")

async def get_links_wired(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Wired — tech culture, gadgets, AI, cybersecurity."""
    return await _rss_links(client, "https://www.wired.com/feed/rss")

async def get_links_cnbc_id_bisnis(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """CNBC Indonesia — business/finance/economy news."""
    return await _rss_links(client, "https://www.cnbcindonesia.com/market-and-economy/rss")

async def get_links_detik_finance(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Detik Finance — Indonesian finance/money news."""
    return await _rss_links(client, "https://finance.detik.com/rss")

async def get_links_liputan6_bisnis(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Liputan6 Bisnis — Indonesian business/finance news."""
    return await _rss_links(client, "https://feed.liputan6.com/rss/bisnis")

async def get_links_kompas_bisnis(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Kompas Bisnis — Indonesian business/finance/economy."""
    return await _rss_links(client, "https://biz.kompas.com/rss")

async def get_links_the_verge(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """The Verge — international tech news."""
    return await _rss_links(client, "https://www.theverge.com/rss/index.xml")

async def get_links_hn(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Hacker News — top stories."""
    return await _rss_links(client, "https://news.ycombinator.com/rss")

async def get_links_ars_technica(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Ars Technica — tech/science deep dives."""
    return await _rss_links(client, "https://feeds.arstechnica.com/arstechnica/index")

async def get_links_engadget(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Engadget — consumer tech, gadgets."""
    return await _rss_links(client, "https://www.engadget.com/rss.xml")

async def get_links_mashable(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Mashable — tech, culture, science."""
    return await _rss_links(client, "https://mashable.com/feeds/rss/all")

async def get_links_reuters_tech(client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Reuters Technology — global tech news."""
    return await _rss_links(client, "https://www.reuters.com/technology/rss")

# ─── Bloomberg Technoz ──────────────────────────────────────────

async def scrape_bloomberg_technoz(client: httpx.AsyncClient) -> list[dict]:
    """Scrape Bloomberg Technoz articles for image."""
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
    
    articles = []
    for url, rss_date in items[:20]:
        try:
            r = await client.get(url, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.find("meta", property="og:title")
            title = title["content"] if title else "Untitled"
            image = get_og_image(soup)  # gunakan fallback gambar default
            articles.append({
                "title": title,
                "url": url,
                "date": rss_date,
                "image": image,
                "source": "bloomberg_technoz"
            })
        except Exception:
            continue
    return articles


async def scrape_detik(client: httpx.AsyncClient) -> list[dict]:
    """Scrape Detik.com/edu articles via HTML (RSS broken)."""
    DETIK_SELECTORS = [
        ("div", "detail__body-text"),
        ("div", "detail-text"),
        ("article", None),
    ]
    articles = []
    try:
        r = await client.get("https://www.detik.com/edu", timeout=15)
        if r.status_code != 200:
            return []
        # Extract article links: /edu/.../d-NNNNNNN/...
        links = list(set(re.findall(r'href="(https://www\.detik\.com/edu/[^"]+/d-\d+[^"]*?)"', r.text)))
        links = [l.split("?")[0] for l in links[:20]]  # dedupe query params
    except Exception:
        return []

    for url in links:
        try:
            r = await client.get(url, timeout=15)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            # Title
            og = soup.find("meta", property="og:title")
            title = og["content"].split(" - ")[0].strip() if og else ""
            if not title:
                h1 = soup.find("h1")
                title = h1.get_text(strip=True) if h1 else ""
            if not title:
                continue
            # Body
            body = extract_body(soup, DETIK_SELECTORS)
            if not body or len(body) < 300:
                continue
            body = clean_body(body)
            # Date
            dt = parse_date_iso(
                (soup.find("meta", property="article:published_time") or {}).get("content", "")
            )
            image = get_og_image(soup)
            articles.append({
                "title": title,
                "url": url,
                "date": dt,
                "body": body,
                "image": image,
                "source": "detik",
            })
        except Exception:
            continue
        if len(articles) >= 10:
            break
    return articles


async def scrape_gramedia(client: httpx.AsyncClient) -> list[dict]:
    """Scrape Gramedia Blog articles (books, reading, self-dev)."""
    items = []
    try:
        r = await client.get("https://www.gramedia.com/blog/feed/", timeout=12)
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

    articles = []
    GRAMEDIA_SELECTORS = [("article", None), ("div", "entry-content"), ("div", "post-content")]
    for url, rss_date in items[:15]:
        try:
            r = await client.get(url, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.find("meta", property="og:title")
            if not title:
                title = soup.find("h1")
                title = title.get_text(strip=True) if title else None
            else:
                title = title["content"]
            if not title:
                title_tag = soup.find("title")
                title = title_tag.get_text(strip=True) if title_tag else "Untitled"
            body = extract_body(soup, GRAMEDIA_SELECTORS)
            image = get_og_image(soup)
            articles.append({
                "title": title,
                "url": url,
                "date": rss_date,
                "body": body,
                "image": image,
                "source": "gramedia",
            })
        except Exception:
            continue
    return articles


async def scrape_mark_manson(client: httpx.AsyncClient) -> list[dict]:
    """Scrape Mark Manson articles."""
    items = []
    try:
        r = await client.get("https://markmanson.net/feed", timeout=12)
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
    
    articles = []
    MM_SELECTORS = [("article", None), ("div", "entry-content"), ("div", "post-content")]
    for url, rss_date in items[:10]:
        try:
            r = await client.get(url, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.find("meta", property="og:title")
            title = title["content"] if title else "Untitled"
            body = extract_body(soup, MM_SELECTORS)
            image = get_og_image(soup)
            articles.append({
                "title": title,
                "url": url,
                "date": rss_date,
                "body": body,
                "image": image,
                "source": "mark_manson"
            })
        except Exception:
            continue
    return articles


async def scrape_james_clear(client: httpx.AsyncClient) -> list[dict]:
    """Scrape James Clear articles."""
    items = []
    try:
        r = await client.get("https://jamesclear.com/feed", timeout=12)
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
    
    articles = []
    JC_SELECTORS = [("div", "page__content"), ("div", "page-content-style"), ("article", None)]
    for url, rss_date in items[:10]:
        try:
            r = await client.get(url, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.find("meta", property="og:title")
            title = title["content"] if title else "Untitled"
            body = extract_body(soup, JC_SELECTORS)
            image = get_og_image(soup)
            articles.append({
                "title": title,
                "url": url,
                "date": rss_date,
                "body": body,
                "image": image,
                "source": "james_clear"
            })
        except Exception:
            continue
    return articles


async def scrape_ryan_holiday(client: httpx.AsyncClient) -> list[dict]:
    """Scrape Ryan Holiday articles."""
    items = []
    try:
        r = await client.get("https://ryanholiday.net/feed/", timeout=12)
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
    
    articles = []
    RH_SELECTORS = [("div", "sentry"), ("div", "posttext"), ("div", "blogpost")]
    for url, rss_date in items[:10]:
        try:
            r = await client.get(url, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.find("meta", property="og:title")
            title = title["content"] if title else "Untitled"
            body = extract_body(soup, RH_SELECTORS)
            image = get_og_image(soup)
            articles.append({
                "title": title,
                "url": url,
                "date": rss_date,
                "body": body,
                "image": image,
                "source": "ryan_holiday"
            })
        except Exception:
            continue
    return articles


async def scrape_darius_foroux(client: httpx.AsyncClient) -> list[dict]:
    """Scrape Darius Foroux articles (productivity, habits, wealth mindset)."""
    items = []
    try:
        r = await client.get("https://dariusforoux.com/feed/", timeout=12)
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

    articles = []
    DF_SELECTORS = [("div", "entry-content"), ("article", None), ("div", "post-content")]
    for url, rss_date in items[:10]:
        try:
            r = await client.get(url, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.find("meta", property="og:title")
            if not title:
                title = soup.find("h1")
                title = title.get_text(strip=True) if title else None
            else:
                title = title["content"]
            if not title:
                title_tag = soup.find("title")
                title = title_tag.get_text(strip=True) if title_tag else "Untitled"
            body = extract_body(soup, DF_SELECTORS)
            image = get_og_image(soup)
            articles.append({
                "title": title,
                "url": url,
                "date": rss_date,
                "body": body,
                "image": image,
                "source": "darius_foroux"
            })
        except Exception:
            continue
    return articles


async def scrape_scott_young(client: httpx.AsyncClient) -> list[dict]:
    """Scrape Scott Young articles (learning, habits, productivity)."""
    items = []
    try:
        r = await client.get("https://www.scotthyoung.com/blog/feed/", timeout=12)
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

    articles = []
    SY_SELECTORS = [("div", "entry-content"), ("article", None), ("div", "post-content")]
    for url, rss_date in items[:10]:
        try:
            r = await client.get(url, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.find("meta", property="og:title")
            if not title:
                title = soup.find("h1")
                title = title.get_text(strip=True) if title else None
            else:
                title = title["content"]
            if not title:
                title_tag = soup.find("title")
                title = title_tag.get_text(strip=True) if title_tag else "Untitled"
            body = extract_body(soup, SY_SELECTORS)
            image = get_og_image(soup)
            articles.append({
                "title": title,
                "url": url,
                "date": rss_date,
                "body": body,
                "image": image,
                "source": "scott_young"
            })
        except Exception:
            continue
    return articles


# ─── Main Scraper ────────────────────────────────────────────────

async def scrape_all_async(top_n: int = TOP_N) -> list[dict]:
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        # 1. Gather articles from all sources (new functions return dicts directly)
        link_tasks = await asyncio.gather(
            scrape_detik(client),
            scrape_mark_manson(client),
            scrape_james_clear(client),
            scrape_ryan_holiday(client),
            scrape_darius_foroux(client),
            scrape_scott_young(client),
        )

    # 2. Collect all articles (new functions already return full article dicts)
    all_articles = []
    for src_name, articles in zip(SOURCE_NAMES, link_tasks):
        if not isinstance(articles, list):
            continue
        for art in articles:
            if isinstance(art, dict) and 'url' in art and 'title' in art:
                all_articles.append(art)

    # 3. Score and sort with source diversity
    articles = []
    seen = set()
    for art in all_articles:
        if art["url"] in seen:
            continue
        seen.add(art["url"])
        art["score"] = score_article(art["title"], art.get("body", ""), art.get("date"))
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
    MAX_PER_SOURCE = 20  # 20 per source, 14 sources = up to 280 articles
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
