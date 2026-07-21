#!/usr/bin/env python3
"""Debug profile article scoring — standalone version."""
import json, pathlib, re, html as html_mod, time, xml.etree.ElementTree as ET
import httpx

HOME = pathlib.Path.home()
BASE_DIR = pathlib.Path("/home/ubuntu/techbro")
log_file = BASE_DIR / "debug_pipeline.log"

def _http_get(url, timeout=15):
    try:
        r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout, follow_redirects=True)
        return r.status_code, r.text
    except Exception as e:
        return 0, str(e)

def _decode_google_news_url(url):
    if "news.google.com" in url and "url=" in url:
        from urllib.parse import parse_qs, urlparse
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            return params.get("url", [url])[0]
        except Exception:
            return url
    return url

def scrape_rss(url, source, base_score=8):
    topics = []
    try:
        code, text = _http_get(url)
        if code != 200:
            return topics
        root = ET.fromstring(text)
        for item in root.findall(".//item")[:20]:
            te = item.find("title")
            le = item.find("link")
            if te is None or le is None:
                continue
            title = re.sub(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", r"\1", (te.text or "").strip())
            title = html_mod.unescape(title)
            if not title or len(title) < 20:
                continue
            link = (le.text or "").strip().split("?")[0]
            link = _decode_google_news_url(link)
            de = item.find("description")
            desc = re.sub(r"<[^>]+>", " ", (de.text or "")).strip()[:500] if de is not None else ""
            desc = html_mod.unescape(desc)
            topics.append(dict(title=title, source=source, url=link, score=base_score, description=desc))
    except Exception as e:
        print(f"  [ERROR] {source}: {e}")
    return topics

# Scoring functions (from pipeline-v2.py)
WORKPLACE_KW = {
    "high": {
        "phk": 15, "layoff": 15, "resign": 12, "gaji": 12, "upah": 12,
        "karyawan": 10, "pekerja": 10, "lembur": 10, "overtime": 10, "burnout": 10,
        "bos": 8, "manajer": 8, "hrd": 10, "human resource": 10,
        "kantor": 8, "workplace": 8, "kerja": 6, "tunjangan": 10,
        "bpjs": 8, "jamsostek": 8, "kontrak": 8, "pkwt": 10,
        "perjanjian kerja": 10, "demo": 8, "serikat": 8, "buruh": 8,
        "pph 21": 8, "pajak": 6, "pengangguran": 12, "kerja paksa": 15,
        "dana darurat": 12, "budgeting": 10, "investasi": 8,
        "nabung": 8, "tabungan": 8, "utang": 10, "hutang": 10,
        "cicilan": 8, "paylater": 10, "kpr": 6,
        "reksadana": 8, "saham": 6, "finansial": 8,
        "interview": 12, "wawancara": 8, "cv": 10, "resume": 8,
        "negosiasi": 12, "promosi": 10, "kenaikan gaji": 12,
        "career": 8, "skill": 8, "portofolio": 6,
        "tips": 8, "trik": 8, "rahasia": 6, "life hack": 10,
        "produktivitas": 8, "time management": 8, "work life balance": 8,
        "side hustle": 10, "freelance": 8,
        "pengusaha": 10, "wirausaha": 10, "entrepreneur": 10,
        "bisnis": 8, "usaha": 6, "kisah sukses": 12,
        "pengusaha sukses": 12, "pengusaha muda": 12, "investor sukses": 12,
        "kisah": 6, "profil": 6, "cerita": 5, "inspiratif": 8,
        "tokoh": 8, "dari nol": 10, "rahasia sukses": 10,
    },
    "medium": {
        "indonesia": 3, "jakarta": 3, "perusahaan": 5, "pt ": 3,
        "startup": 5, "tech": 3, "digital": 3, "ekonomi": 3,
        "inflasi": 4, "daya beli": 4, "karir": 5, "jabatan": 4,
        "toxic": 8, "diskriminasi": 8, "pelecehan": 10,
        "keuangan": 6, "pensiun": 8, "dana pensiun": 8,
        "asuransi": 6, "biaya hidup": 6,
        "jenjang karir": 8, "pelatihan": 6, "sertifikasi": 6,
        "magang": 6, "internship": 6, "fresh graduate": 8,
        "panduan": 6, "langkah": 6, "strategi": 6, "cara": 4,
        "tutorial": 6, "metode": 6, "teknik": 6, "praktik": 6,
        "wirausahawan": 5, "omzet": 6, "pendapatan": 6,
        "profit": 6, "rugi": 5, "laba": 5, "modal": 5,
        "investor": 6, "figur": 5, "sosok": 5,
        "karier": 5, "profesi": 4, "kaya": 6, "wealth": 6,
        "bangkit": 5, "perjalanan": 4,
    },
}

def _workplace_relevance(title, description=""):
    text = (title + " " + description).lower()
    score = 0
    for kw, val in WORKPLACE_KW["high"].items():
        if kw in text:
            score += val
    for kw, val in WORKPLACE_KW["medium"].items():
        if kw in text:
            score += val
    return score

def _is_workplace_relevant(title, description=""):
    text = (title + " " + description).lower()
    required = ["karyawan", "pekerja", "buruh", "gaji", "upah", "phk", "layoff",
                "resign", "lembur", "kantor", "kerja", "bos", "hrd", "burnout",
                "tunjangan", "bpjs", "kontrak", "perusahaan", "pt ", "pt.",
                "startup", "tech", "digital", "ekonomi", "pengangguran",
                "interview", "wawancara", "cv", "resume", "negosiasi",
                "promosi", "karir", "career", "skill", "pelatihan",
                "magang", "internship", "fresh graduate", "sertifikasi",
                "dana darurat", "budgeting", "investasi", "nabung", "tabungan",
                "utang", "hutang", "keuangan", "finansial", "pensiun",
                "asuransi", "biaya hidup", "pajak", "reksadana", "saham",
                "tips", "cara", "tutorial", "panduan", "langkah",
                "produktivitas", "time management", "work life balance",
                "habit", "life hack", "strategi",
                "pengusaha", "wirausaha", "wirausahawan", "entrepreneur",
                "bisnis", "usaha", "modal", "omzet", "pendapatan", "profit",
                "rugi", "laba", "investor", "tokoh", "figur", "sosok",
                "karier", "profesi", "kaya", "wealth",
                "pensiun dini", "financial freedom",
                "kisah", "profil", "cerita", "perjalanan", "inspiratif",
                "sukses", "bangkit", "dari nol", "rahasia sukses",
                "pengusaha sukses", "pengusaha muda", "investor muda",
                "anak muda sukses"]
    return any(kw in text for kw in required)

def _score_edu_formula(title):
    tl = title.lower()
    score = 0
    edu_hooks = {
        "curiosity": ["jangan", "hati-hati", "waspada", "ternyata", "yang tidak", 
                      "tanpa", "rahasia", "fakta", "kamu gak", "lo gak", "gak banyak"],
        "how_to": ["cara", "tips", "panduan", "langkah", "strategi", "trik",
                   "metode", "teknik", "mulai", "bagaimana"],
        "big_number": ["angka", "persen", "juta", "miliar", "triliun", "ribu"],
        "profile": ["kisah", "profil", "cerita", "perjalanan", "inspiratif",
                    "sukses", "bangkit", "perjuangan", "tokoh"],
    }
    for kw in edu_hooks["curiosity"]:
        if kw in tl: score += 12; break
    for kw in edu_hooks["how_to"]:
        if kw in tl: score += 10; break
    for kw in edu_hooks["big_number"]:
        if kw in tl: score += 8; break
    for kw in edu_hooks["profile"]:
        if kw in tl: score += 10; break
    if not any(kw in tl for kw in ["cara", "tips", "panduan", "kisah", "profil", "cerita", "jangan"]):
        score -= 15
    specific_number = re.search(r'\d+', title)
    if specific_number: score += 3
    return max(score, 0)

def _profile_content_bonus(title, description=""):
    tl = title.lower()
    text = (title + " " + description).lower()
    bonus = 0
    if any(kw in tl for kw in ["kisah", "profil", "cerita", "perjalanan", "inspiratif",
                                "perjuangan", "sukses", "dari nol", "bangkit",
                                "rahasia sukses", "pengusaha sukses", "wirausahawan",
                                "tokoh", "figur", "sosok"]):
        bonus += 10
    names = re.findall(r'([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)', text)
    real_names = [n for n in names if not any(w in n.lower() for w in 
                  ["yang", "dan", "dari", "untuk", "dengan", "tentang", "setelah",
                   "sebagai", "serta", "atau", "karena", "tetapi", "indonesia",
                   "jakarta", "bandung", "surabaya", "perusahaan", "dalam",
                   "antara", "pada", "bisa", "paling", "seperti", "lebih"])]
    if len(real_names) >= 2:
        bonus += 20
    elif len(real_names) >= 1:
        bonus += 10
    if any(kw in tl for kw in ["investasi", "finansial", "keuangan", "bisnis",
                                "pendapatan", "omzet", "profit", "rugi",
                                "pensiun dini", "kaya", "wealth"]):
        if bonus > 0:
            bonus += 5
    return min(bonus, 40)

# ── Test ──
SOURCES = {
    "gnews_kisah_sukses": {"url": "https://news.google.com/rss/search?q=%22kisah+sukses%22+%22pengusaha+muda%22+indonesia&hl=id&gl=ID&ceid=ID:id", "base_score": 12},
    "gnews_profil_karir": {"url": "https://news.google.com/rss/search?q=%22profil+karir%22+indonesia&hl=id&gl=ID&ceid=ID:id", "base_score": 12},
    "gnews_rahasia_sukses": {"url": "https://news.google.com/rss/search?q=%22rahasia+sukses%22+indonesia&hl=id&gl=ID&ceid=ID:id", "base_score": 11},
    "gnews_entrepreneur_cerita": {"url": "https://news.google.com/rss/search?q=%22cerita+pengusaha%22+indonesia&hl=id&gl=ID&ceid=ID:id", "base_score": 11},
}

t0 = time.time()
for name, cfg in SOURCES.items():
    topics = scrape_rss(cfg['url'], name, cfg['base_score'])
    print(f"\n{'='*60}")
    print(f"[{name}] base_score={cfg['base_score']}, topics={len(topics)}")
    for t in topics[:4]:
        title = t.get('title', '')
        desc = t.get('description', '')
        relevant = _is_workplace_relevant(title, desc)
        ws = _workplace_relevance(title, desc)
        profile_bonus = _profile_content_bonus(title, desc)
        edu_fs = _score_edu_formula(title)
        base = t.get('score', 5)
        penalty = -15 if (ws == 0 and profile_bonus == 0) else 0
        total = base + ws + penalty + profile_bonus + edu_fs
        print(f"\n  TITLE: {title[:80]}")
        print(f"  DESC: {desc[:100]}")
        print(f"  gate={'PASS' if relevant else 'FAIL'} | ws={ws} | edu_fs={edu_fs} | profile_bonus={profile_bonus} | penalty={penalty} | total={total}")

# Also compare with a generic article
print(f"\n{'='*60}")
print("\n=== COMPARISON: Generic article ===")
generic_title = "Cara Investasi Saham untuk Pemula: Panduan Lengkap dan Tips Anti Gagal"
generic_desc = "Panduan lengkap cara investasi saham untuk pemula. Pelajari tips dan trik investasi saham yang aman."
ws = _workplace_relevance(generic_title, generic_desc)
relevant = _is_workplace_relevant(generic_title, generic_desc)
profile_bonus = _profile_content_bonus(generic_title, generic_desc)
edu_fs = _score_edu_formula(generic_title)
penalty = -15 if (ws == 0 and profile_bonus == 0) else 0
total = 8 + ws + penalty + profile_bonus + edu_fs
print(f"  gate={'PASS' if relevant else 'FAIL'} | ws={ws} | edu_fs={edu_fs} | profile_bonus={profile_bonus} | penalty={penalty} | total={total}")

print(f"\nDone in {time.time()-t0:.1f}s")
