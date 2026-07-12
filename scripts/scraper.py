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
SOURCE_NAMES = ["google_news", "celebrity", "celebrity_id", "entrepreneur", "mindset", "tech", "career"]

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
    # BLOCKED: Crime / violence / politics / sensitive
    "kriminal", "pembunuhan", "pembunuh", "bunuh", "mayat", "jenazah",
    "pemerkosaan", "pencabulan", "kekerasan", "penganiayaan",
    "tawuran", "perkelahian", "bentrokan", "rusuh", "kerusuhan",
    "penembakan", "senjata", "bom", "teroris", "terorisme",
    "narkoba", "narkotika", "sabu", "ganja", "ekstasi",
    "korupsi", "koruptor", "suap", "gratifikasi",
    "pencurian", "maling", "copet", "penipuan", "scam",
    "politik", "partai", "pemilu", "pilkada", "gubernur", "bupati", "walikota",
    "presiden", "menteri", "dpr", "mpr", "parlemen", "legislatif",
    "demo", "unjuk rasa", "mahasiswa", "aktivis", "reformasi",
    "sara", "agama", "rasisme", "intoleransi", "radikalisme",
    # Book listicles — "rekomendasi buku" alone blocks genuine articles that mention books
    # Only block pure listicles (title starts with number + "rekomendasi")
    "5 rekomendasi buku", "10 rekomendasi buku", "7 rekomendasi buku",
    "5 buku wajib", "10 buku wajib",
]

_TIER1_SET = {k.lower() for k in TIER1}
_TIER2_SET = {k.lower() for k in TIER2}
_TIER3_SET = {k.lower() for k in TIER3}
_PENALTY_RE = re.compile("|".join(re.escape(k.lower()) for k in PENALTY))
_EXCL_RE = re.compile("(?:^|\\b|\\s)(?:" + "|".join(re.escape(k.lower()) for k in EXCLUDE) + ")(?:\\b|\\s|$)")

# ─── 15-Component Scoring Keywords (Pressbox-adapted) ────────────

# Drama signal: controversy, debate, conflict
DRAMA_SIGNALS = [
    "kontroversi", "kontroversional", "debat", "perdebatan", "konflik",
    "pertentangan", "pro kontra", "pro-kontra", "dikritik", "kritik",
    "kecaman", "kecam", "skandal", "heboh", "viral", "gemparkan",
    "menggemparkan", "geger", "gempar", "mengejutkan", "kejutan",
    "ternyata", "fakta", "bantah", "bantahan", "sanggah",
    "controversy", "debate", "controversial", "divisive", "backlash",
    "scandal", "shocking", "surprising", "unexpected", "reveal",
]

# Audience reach: big names in self-dev/mindset
AUDIENCE_REACH = [
    # Indonesian
    "eva alicia", "mario teguh", "jaya setiabudi", "rex maung", "ardi bakrie",
    "chairul tanjung", "susilo bambang", "jokowi", "ridwan kamil", "anies baswedan",
    # International
    "alex hormozi", "theo derick", "naval ravikant", "james clear", "jordan peterson",
    "tony robbins", "brené brown", "tim ferriss", "joe rogan", "mark manson",
    "lionel messi", "cristiano ronaldo", "kylian mbappe", "lebron james", "kobe bryant",
    "elon musk", "jeff bezos", "bill gates", "warren buffett", "steve jobs",
    "mark zuckerberg", "sam altman", "mark cuban", "gary vaynerchuk",
    # Celebrities / influencers
    "raffi nagita", "boy william", "deddy corbuzier", "raditya dika",
    "sule", "andre taulany", "vincent rompies", "desta",
]

_DRAMA_SET = {k.lower() for k in DRAMA_SIGNALS}
_REACH_SET = {k.lower() for k in AUDIENCE_REACH}

# Paradox keywords: contradiction / counter-intuitive
PARADOX_SIGNALS = [
    "meskipun", "walaupun", "padahal", "sementara", "tetapi", "namun",
    "walau", "biarpun", "although", "despite", "however", "while", "yet",
    "barely", "only", "just", "unexpected", "ironi", "ironis",
]
_PARADOX_SET = {k.lower() for k in PARADOX_SIGNALS}

# Niche penalty: off-topic for self-dev
NICHE_PENALTY_KW = [
    "sepatu", "jersey", "kostum", "tiket", "stadion", "konser",
    "skor", "pertandingan", "liga", "turnamen",
    "boots", "kit", "stadium", "ticket", "score", "match", "league",
]
_NICHE_SET = {k.lower() for k in NICHE_PENALTY_KW}

# Category keywords (for category bonus)
_CAT_MINDSET = {"mindset", "pola pikir", "perspektif", "sudut pandang", "growth mindset", "fixed mindset"}
_CAT_CAREER = {"karir", "career", "gaji", "salary", "promosi", "promotion", "phk", "layoff", "resign", "interview"}
_CAT_FINANCE = {"investasi", "investing", "keuangan", "finansial", "tabungan", "savings", "utang", "debt", "budget"}
_CAT_HABITS = {"kebiasaan", "habits", "rutinitas", "routine", "disiplin", "discipline", "produktif", "productive"}
_CAT_FIGURES = {"ceo", "founder", "entrepreneur", "pengusaha", "startup", "atlet", "athlete"}

# ─── Stemming (simple suffix stripper for Jaccard dedup) ───────

_STEM_SUFFIXES = ("ing", "tion", "sion", "ment", "ness", "able", "ible",
                   "ies", "ied", "ers", "est", "ful", "ous",
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


def check_article_quality(body: str, source: str = "") -> str | None:
    """3-layer content quality filter (from pressbox). Returns rejection reason or None."""
    text = body.strip()
    # Layer 1: character count — 500 for Indonesian sources (paywalled/JS-heavy), 1000 for English
    min_chars = 500 if source in ("mindset", "tech", "career", "entrepreneur", "celebrity_id", "google_news") else 1000
    if len(text) < min_chars:
        return f"too short ({len(text)} chars < {min_chars})"
    # Layer 2: word count (pressbox: 150)
    words = len(text.split())
    if words < 80:
        return f"too few words ({words} < 80)"
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


def score_article(title: str, body: str, date=None, hot_boost: int = 0, analytics_boost: int = 0, source: str = "") -> int:
    """15-component scoring (Pressbox-adapted for self-dev niche)."""
    title_l = title.lower()
    body_l = body[:1500].lower()
    text = title_l + " " + body_l

    # ── Component 1: Keyword Match (3-tier, max 40) ──
    t1 = _unique_matches(title_l, _TIER1_SET) * 30 + _unique_matches(body_l, _TIER1_SET) * 10
    t2 = _unique_matches(title_l, _TIER2_SET) * 15 + _unique_matches(body_l, _TIER2_SET) * 5
    t3 = _unique_matches(title_l, _TIER3_SET) * 5 + _unique_matches(body_l, _TIER3_SET) * 2
    keyword_score = min(t1 + t2 + t3, 40)

    # ── Component 2: Category (20/10/0) ──
    cat_score = 0
    for cat_set, bonus in [(_CAT_MINDSET, 20), (_CAT_CAREER, 15), (_CAT_FINANCE, 15),
                           (_CAT_HABITS, 15), (_CAT_FIGURES, 10)]:
        if _unique_matches(text, cat_set) >= 1:
            cat_score = max(cat_score, bonus)

    # ── Component 3: Recency (15/10/5/0) ──
    recency = 0
    if date:
        if isinstance(date, str):
            try:
                from email.utils import parsedate_to_datetime
                date = parsedate_to_datetime(date)
            except Exception:
                date = None
        if date:
            hours_old = (datetime.now(UTC) - date).total_seconds() / 3600
            if hours_old < 6:
                recency = 15
            elif hours_old < 12:
                recency = 10
            elif hours_old < 24:
                recency = 5

    # ── Component 4: Data/Konkret (15/7/0) ──
    numbers = len(re.findall(r'\b\d+[.,]?\d*\b', title))
    data_score = 15 if numbers >= 2 else (7 if numbers >= 1 else 0)

    # ── Component 5: Source Tier (10/7/5/0) ──
    # Celebrity gets moderate boost, NOT dominant. Mindset/tech get equal weight.
    source_score = 5
    if source in ("celebrity", "celebrity_id", "athlete"):
        source_score = 10  # moderate — don't let sports dominate
    elif source in ("mindset", "tech", "career"):
        source_score = 7   # actual self-dev content deserves fair chance
    elif source == "entrepreneur":
        source_score = 7

    # ── Component 6: Audience Reach (max 20) ──
    reach_count = _unique_matches(text, _REACH_SET)
    reach_score = min(reach_count * 10, 20)

    # ── Component 7: Drama Signal (max 15) ──
    drama_count = _unique_matches(text, _DRAMA_SET)
    drama_score = min(drama_count * 5, 15)

    # ── Component 8: Paradox Bonus (+12) ──
    paradox = 12 if _unique_matches(text, _PARADOX_SET) >= 2 else 0

    # ── Component 9: Niche Penalty (-30) ──
    niche_penalty = -30 if _unique_matches(text, _NICHE_SET) >= 1 else 0

    # ── Component 10: Hot Topic (from Union-Find, passed in) ──
    # hot_boost is +25 (≥5 hotness) or +15 (≥3), 0 otherwise

    # ── Component 11: Peak-Hour (+10) ──
    now_wib = datetime.now(timezone(timedelta(hours=7)))
    hour = now_wib.hour
    peak = 10 if hour in range(10, 13) or hour in range(17, 22) else 0

    # ── Component 12: Analytics Boost (from hook/topic performance) ──
    # analytics_boost is passed in from pipeline

    # ── Component 13: Density bonus (+20 if title has 2+ TIER1) ──
    density = 20 if _unique_matches(title_l, _TIER1_SET) >= 2 else 0

    # ── Sum ──
    s = (keyword_score + cat_score + recency + data_score + source_score +
         reach_score + drama_score + paradox + niche_penalty + hot_boost +
         peak + analytics_boost + density)

    # ── Soft cap: diminishing returns above 100 ──
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

# Google News RSS feeds for Indonesia (3 focused feeds + trending fetched dynamically)
_GNEWS_FEEDS = [
    # Global entrepreneurs & tech leaders (dominant — this is self-dev account, not sports)
    ("https://news.google.com/rss/search?q=Bezos+OR+Musk+OR+Zuckerberg+OR+Altman+OR+Hormozi+OR+Naval+OR+Ferriss+OR+Vaynerchuk+OR+Jobs+OR+Gates+OR+Cook+OR+Nadella+OR+Pichai+OR+Dorsey+OR+Systrom&hl=en&gl=US&ceid=US:en", "celebrity"),
    # Indonesian public figures — entrepreneurs, creators (NOT politics)
    ("https://news.google.com/rss/search?q=Raffi+Ahmad+OR+Deddy+Corbuzier+OR+Jerome+Polin+OR+Arief+Muhammad+OR+Eva+Alicia+OR+William+Tanuwijaya+OR+Nadiem+Makarim+OR+Tokopedia+OR+Gojek+OR+Traveloka&hl=id&gl=ID&ceid=ID:id", "celebrity_id"),
    # Entrepreneurs — startup, business journey
    ("https://news.google.com/rss/search?q=pengusaha+OR+startup+OR+founder+OR+CEO+OR+bisnis+sukses+OR+modal+OR+investasi+OR+unicorn&hl=id&gl=ID&ceid=ID:id", "entrepreneur"),
    # Self-improvement / mindset
    ("https://news.google.com/rss/search?q=self+improvement+OR+mindset+OR+motivasi+OR+pengembangan+diri+OR+produktivitas+OR+kebiasaan+baik&hl=id&gl=ID&ceid=ID:id", "mindset"),
    # Tech innovation / AI (non-political)
    ("https://news.google.com/rss/search?q=inovasi+OR+teknologi+OR+AI+OR+startup+OR+digital+OR+aplikasi&hl=id&gl=ID&ceid=ID:id", "tech"),
    # Career / professional development
    ("https://news.google.com/rss/search?q=karier+OR+karir+OR+pekerjaan+OR+interview+OR+gaji+OR+promosi+OR+resign&hl=id&gl=ID&ceid=ID:id", "career"),
]

async def _fetch_trending_queries(client: httpx.AsyncClient) -> list[str]:
    """Fetch top trending search queries from Google Trends RSS (Indonesia)."""
    try:
        r = await client.get("https://trends.google.com/trending/rss?geo=ID", timeout=10)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "xml")
        items = soup.find_all("item")
        queries = []
        for item in items[:10]:  # Top 10 trends
            title = item.find("title")
            if title:
                queries.append(title.text.strip())
        print(f"  [TRENDS] {len(queries)} trending queries from Google Trends")
        return queries
    except Exception as e:
        print(f"  [TRENDS] Failed: {e}")
        return []

async def _scrape_trending_articles(client: httpx.AsyncClient, queries: list[str]) -> list[dict]:
    """Search Google News for articles matching trending queries, with self-dev angle."""
    if not queries:
        return []

    # Add self-dev angle keywords to trending queries
    angle_keywords = ["mindset", "disiplin", "mental", "kebiasaan", "sukses", "gagal", "resilien"]
    all_articles = []

    for query in queries[:5]:  # Top 5 trends only
        # Search Google News for the trending topic + angle keyword
        search_query = f"{query} {angle_keywords[hash(query) % len(angle_keywords)]}"
        feed_url = f"https://news.google.com/rss/search?q={search_query.replace(' ', '+')}&hl=id&gl=ID&ceid=ID:id"
        try:
            r = await client.get(feed_url, timeout=10)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "xml")
            items = soup.find_all("item")
            for item in items[:3]:  # Top 3 per trend
                title_el = item.find("title")
                link_el = item.find("link")
                if not title_el or not link_el:
                    continue
                all_articles.append({
                    "title": title_el.text.strip(),
                    "link": link_el.text.strip() if link_el.text else "",
                    "source_name": "trending",
                    "category": "trending",
                    "date": item.find("pubDate").text if item.find("pubDate") else "",
                    "is_trending": True,  # Flag for hybrid angle
                })
        except Exception:
            continue

    print(f"  [TRENDS] {len(all_articles)} articles from trending queries")
    return all_articles

# Generic body extraction — tries common selectors across Indonesian news sites
_BODY_SELECTORS = [
    "article", ".detail-content", ".read__content", ".article-content",
    ".text-cnn_black", ".article-body", ".news-content", ".story-body",
    "#article_content", ".post-content", ".entry-content",
]

def _extract_body(soup: BeautifulSoup) -> str:
    """Extract article body text from soup using common selectors."""
    for sel in _BODY_SELECTORS:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 100:
            # Remove script/style tags
            for tag in el.find_all(["script", "style", "nav", "footer", "aside"]):
                tag.decompose()
            return el.get_text(separator="\n", strip=True)[:3000]
    # Fallback: all p tags
    ps = soup.find_all("p")
    text = "\n".join(p.get_text(strip=True) for p in ps if len(p.get_text(strip=True)) > 20)
    return text[:3000] if len(text) > 100 else ""


def _extract_image(soup: BeautifulSoup) -> str:
    """Extract og:image or first article image."""
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return og["content"]
    img = soup.select_one("article img, .detail img, .read img")
    if img and img.get("src"):
        return img["src"]
    return ""


async def scrape_google_news(client: httpx.AsyncClient) -> list[dict]:
    """Scrape articles from Google News RSS (Indonesia).
    Decodes Google News URLs to get actual article URLs, then scrapes content."""
    from googlenewsdecoder import new_decoderv1
    import asyncio as _aio

    articles = []

    # 1. Fetch all RSS feeds
    feed_results = await asyncio.gather(*[
        client.get(url, timeout=15) for url, _ in _GNEWS_FEEDS
    ])

    # 2. Parse items from all feeds
    items = []
    for (feed_url, category), resp in zip(_GNEWS_FEEDS, feed_results):
        if resp.status_code != 200:
            continue
        for item_match in re.finditer(r'<item>(.*?)</item>', resp.text, re.DOTALL):
            item_xml = item_match.group(1)
            title_m = re.search(r'<title>([^<]+)</title>', item_xml)
            link_m = re.search(r'<link>([^<]+)</link>', item_xml)
            source_m = re.search(r'<source[^>]*>([^<]+)</source>', item_xml)
            pub_m = re.search(r'<pubDate>([^<]+)</pubDate>', item_xml)

            if not title_m or not link_m:
                continue

            title = title_m.group(1).strip()
            # Skip feed-level titles
            if "Google" in title and "Berita" in title:
                continue

            items.append({
                "title": title,
                "link": link_m.group(1),
                "source_name": source_m.group(1) if source_m else "unknown",
                "date": pub_m.group(1) if pub_m else "",
                "category": category,
            })

    print(f"  [GNEWS] {len(items)} items from {len(feed_results)} feeds")

    # 2b. Also fetch trending articles from Google Trends
    trending_queries = await _fetch_trending_queries(client)
    trending_articles = await _scrape_trending_articles(client, trending_queries)
    # Add trending articles directly (they have real URLs, no decode needed)
    for ta in trending_articles:
        items.append(ta)
    print(f"  [GNEWS] Total items after trending: {len(items)}")

    # 3. Decode URLs in parallel (sync decoder, run in thread pool)
    #    Round-robin across feeds to ensure diversity (not just first feed)
    import random
    random.shuffle(items)  # shuffle to avoid feed-order bias
    items = items[:50]  # more items = better diversity across feeds
    seen_urls = set()
    unique_items = []
    for item in items:
        if item["link"] not in seen_urls:
            seen_urls.add(item["link"])
            unique_items.append(item)

    async def _decode(item):
        try:
            result = await _aio.to_thread(new_decoderv1, item["link"])
            if result and result.get("status") and result.get("decoded_url"):
                item["decoded_url"] = result["decoded_url"]
                return item
        except Exception:
            pass
        # Fallback: follow redirect with httpx
        try:
            r = await client.get(item["link"], timeout=10, follow_redirects=True)
            if r.status_code == 200 and "news.google.com" not in str(r.url):
                item["decoded_url"] = str(r.url)
        except Exception:
            pass
        return item

    # Decode all in parallel with timeout
    try:
        await _aio.wait_for(
            _aio.gather(*[_decode(item) for item in unique_items]),
            timeout=60
        )
    except _aio.TimeoutError:
        print("  [GNEWS] Decode timeout, using what we have")

    decoded = [i for i in unique_items if "decoded_url" in i]
    print(f"  [GNEWS] {len(decoded)} URLs decoded")

    # 4. Scrape article content (parallel, max 15 at a time)
    async def _scrape_one(item: dict) -> dict | None:
        url = item["decoded_url"]
        try:
            r = await client.get(url, timeout=12, follow_redirects=True)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")
            body = _extract_body(soup)
            if len(body) < 100:
                return None
            # Extract date from article page if RSS date missing
            date = item["date"]
            # Parse RFC 2822 date string to datetime
            if isinstance(date, str) and date:
                try:
                    from email.utils import parsedate_to_datetime
                    date = parsedate_to_datetime(date)
                except Exception:
                    date = None
            if not date:
                time_el = soup.find("time")
                if time_el and time_el.get("datetime"):
                    date = time_el["datetime"]
            return {
                "title": item["title"],
                "url": url,
                "body": body,
                "source": item.get("category", "google_news"),  # Use feed tag (celebrity, athlete, etc.)
                "date": date,
                "image": _extract_image(soup),
                "gnews_category": item["category"],
                "gnews_source": item["source_name"],
            }
        except Exception:
            return None

    # Scrape in batches of 15
    batch_size = 15
    article_seen = set()
    for i in range(0, len(decoded), batch_size):
        batch = decoded[i:i + batch_size]
        results = await asyncio.gather(*[_scrape_one(item) for item in batch])
        for r in results:
            if r and r["url"] not in article_seen:
                article_seen.add(r["url"])
                articles.append(r)

    print(f"  [GNEWS] {len(articles)} articles scraped with content")
    return articles


async def get_google_trending_keywords(client: httpx.AsyncClient) -> set[str]:
    """Fetch trending topic keywords from Google News RSS (Indonesia).
    Returns stemmed keyword set for score boosting."""
    keywords = set()
    feeds = [
        "https://news.google.com/rss?hl=id&gl=ID&ceid=ID:id",
        "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB?hl=id&gl=ID&ceid=ID:id",  # Tech
        "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtVnVHZ0pWVXlnQVAB?hl=id&gl=ID&ceid=ID:id",  # Business
    ]
    stop = {"yang", "dan", "ini", "itu", "dengan", "untuk", "dari", "pada", "adalah",
            "juga", "sudah", "masih", "belum", "akan", "bisa", "tidak", "gak", "bukan",
            "the", "is", "are", "was", "and", "or", "but", "in", "on", "at", "to",
            "for", "of", "with", "by", "a", "an", "that", "this", "it", "not", "has",
            "have", "been", "was", "were", "be", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "must", "shall", "can", "need", "dare",
            "ought", "used", "about", "after", "again", "all", "also", "any", "because",
            "before", "between", "both", "come", "each", "even", "first", "from",
            "get", "give", "go", "good", "great", "her", "here", "him", "his", "how",
            "if", "into", "just", "keep", "know", "last", "let", "like", "long", "look",
            "make", "many", "me", "most", "much", "must", "my", "new", "no", "now",
            "old", "only", "other", "our", "out", "over", "own", "people", "say", "see",
            "she", "so", "some", "take", "tell", "than", "their", "them", "then",
            "there", "these", "they", "thing", "think", "time", "two", "up", "us",
            "use", "very", "want", "way", "we", "well", "what", "when", "where",
            "which", "who", "why", "will", "with", "work", "year", "you", "your",
            "secara", "oleh", "telah", "karena", "namun", "agar", "yakni", "yaitu",
            "serta", "hingga", "sejak", "tentang", "melalui", "setelah", "antara"}
    for feed_url in feeds:
        try:
            r = await client.get(feed_url, timeout=12)
            if r.status_code != 200:
                continue
            # Extract titles from RSS <item><title>...</title>
            for m in re.finditer(r'<title>([^<]+)</title>', r.text):
                title = m.group(1).strip()
                # Skip feed-level title
                if "Google" in title and "Berita" in title:
                    continue
                # Extract meaningful words (3+ chars)
                words = set(w.lower() for w in re.findall(r'[a-zA-Z\u00C0-\u024F]{3,}', title) if w.lower() not in stop)
                keywords.update(_stem(w) for w in words)
        except Exception:
            continue
    return keywords


async def scrape_all_async(top_n: int = TOP_N) -> list[dict]:
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        # Google News only — scrape trending articles from Indonesia
        all_articles = await scrape_google_news(client)

    # Score and sort
    articles = []
    seen = set()
    for art in all_articles:
        if art["url"] in seen:
            continue
        seen.add(art["url"])
        # Parse date string to datetime for scoring
        date = art.get("date")
        if isinstance(date, str) and date:
            try:
                from email.utils import parsedate_to_datetime
                date = parsedate_to_datetime(date)
            except Exception:
                date = None
        art["score"] = score_article(art["title"], art.get("body", ""), date, source=art.get("source", ""))
        if art["score"] > 10:
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
