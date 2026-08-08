#!/usr/bin/env python3
"""Viral Economy Article Hunter — techbro integration of skill viral-economy-article-hunter.

Scans Google News (when:24h) across 6 query clusters (A–F), scores candidates
deterministically: Viral Score + Economic Relevance (0–100, both >= 75 eligible),
clusters same-story across >= 2 independent sources, writes hot_econ.json for
pipeline boost, prints markdown report (skill doc section 18).

Usage: python viral-econ-hunter.py [--hours 24|48|72] [--quiet] [--top N]
"""

import json, logging, re, sys, time
import urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("viral-econ-hunter")

BASE_DIR = Path(__file__).parent
WIB = timezone(timedelta(hours=7))
_GNEWS_BASE = "https://news.google.com/rss/search"

# ── Skill doc section 4: search clusters A–F ─────────────────────────────────
_GNEWS_QUERIES = {
    "A_umum": [
        "ekonomi Indonesia hari ini", "daya beli masyarakat",
        "kelas menengah Indonesia", "ekonomi digital Indonesia",
    ],
    "B_consumer_pain": [
        "harga naik", "tarif naik", "pajak naik", "BBM naik", "listrik naik",
        "harga beras", "biaya hidup", "cicilan", "bunga kredit", "daya beli turun",
    ],
    "C_jobs_income": [
        "PHK", "lowongan kerja", "pengangguran", "upah", "UMP", "gaji",
        "pabrik tutup", "efisiensi perusahaan",
    ],
    "D_gov_policy": [
        "APBN", "subsidi", "bansos", "pajak", "insentif", "bea masuk",
        "ekspor", "impor",
    ],
    "E_financial": [
        "BI rate", "Bank Indonesia", "rupiah", "dolar", "kredit", "bank",
        "pinjaman", "likuiditas", "suku bunga",
    ],
    "F_trending": [
        "viral ekonomi", "polemik ekonomi", "dikeluhkan", "protes",
        "kontroversi", "melonjak", "anjlok", "rekor", "tertinggi", "terendah",
        "PHK massal", "rugi", "bangkrut",
    ],
}

# ── Skill doc section 9: economic relevance (niche keywords) ─────────────────
_ECON_NICHE = (
    "harga", "inflasi", "daya beli", "upah", "gaji", "ump", "phk", "pekerja",
    "pengangguran", "pajak", "subsidi", "bansos", "bbm", "listrik", "tarif",
    "rupiah", "suku bunga", "kredit", "cicilan", "bank", "bi rate", "apbn",
    "utang", "kebijakan", "ekspor", "impor", "umkm", "investasi", "industri",
    "properti", "biaya hidup", "kelas menengah", "ekonomi rumah tangga",
    "bunga kredit", "bank indonesia", "bpjs", "pangan", "beras", "minyak goreng",
    "gula", "daging", "bawang", "saham", "ihsg", "emiten", "neraca", "defisit",
)

# ── Skill doc section 7: momentum/tension keywords ───────────────────────────
_MOMENTUM_KW = (
    "viral", "ramai", "trending", "polemik", "dikeluhkan", "protes",
    "kontroversi", "melonjak", "anjlok", "rekor", "tertinggi", "terendah",
    "massal", "rugi", "bangkrut", "heboh", "sorot", "disorot",
)
_TENSION_KW = (
    "tolak", "gugat", "bantah", "protes", "lawan", "desak", "tuntut",
    "vs", "konflik", "polemik", "terancam", "gagal", "jeblok", "meroket",
    "turun drastis", "naik drastis", "mengeluh", "keluhkan",
)
_SURPRISE_KW = ("mengejutkan", "kaget", "ternyata", "fakta", "diam-diam",
                "rahasia", "bongkar", "terungkap", "tak terduga", "tembus",
                "melejit", "meroket", "meledak", "membludak", "gila", "viral")
_STAKE_KW = ("pajak", "gaji", "phk", "cicilan", "bunga", "subsidi", "bansos",
             "tarif", "bbm", "listrik", "pangan", "beras", "umkm", "bpjs")

_NUMBER_RE = re.compile(r"\b\d{2,}(?:[.,]\d+)?\s*(?:%|triliun|miliar|juta|ribu)\b", re.I)

# Clustering stopwords: keep negation/direction words ("tidak", "naik") — they
# carry story identity (BBM "tidak naik" vs "naik" are different stories).
_CLUSTER_STOP = {
    "yang", "dengan", "untuk", "akan", "pada", "saat", "para", "dari", "ke",
    "di", "dan", "ini", "itu", "hari", "terbaru", "baru", "resmi", "sebut",
    "pastikan", "catat", "soal", "dalam", "per", "juta", "ribu", "miliar",
    "triliun", "rp", "agus", "agustus", "september", "oktober", "2026", "2025",
    "indonesia", "semua", "lagi", "masih",
}


def _content_tokens(title: str) -> set:
    words = re.sub(r"[^a-z0-9\s%]", " ", title.lower()).split()
    return {w for w in words if len(w) >= 4 and w not in _CLUSTER_STOP}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0
    return len(a & b) / len(a | b)


def _gnews_fetch(query: str, max_items: int = 15) -> list:
    params = urllib.parse.urlencode({
        "q": f"{query} when:24h", "hl": "id", "gl": "ID", "ceid": "ID:id",
    })
    out = []
    try:
        with urllib.request.urlopen(f"{_GNEWS_BASE}?{params}", timeout=15) as r:
            root = ET.fromstring(r.read().decode("utf-8", "replace"))
    except Exception as e:
        log.warning(f"gnews {query!r} failed: {e}")
        return out
    for it in root.findall(".//item")[:max_items]:
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        src_el = it.find("source")
        src = (src_el.get("url") or "") if src_el is not None else ""
        src = re.sub(r"^https?://(www\.)?", "", src).rstrip("/")
        title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title)
        try:
            ts = parsedate_to_datetime(pub).timestamp() if pub else None
        except Exception:
            ts = None
        if not title or not link:
            continue
        out.append({"title": title, "url": link, "source": src,
                    "published_ts": ts, "description": ""})
    return out


def _score_article(title: str, desc: str, published_ts: float, source_count: int = 1) -> dict:
    """Deterministic Viral Score + Economic Relevance (skill doc sections 8-9)."""
    tl = title.lower()
    full = f"{tl} {desc.lower()}"

    # Economic relevance (0-100): niche hit density + number evidence
    niche_hits = sum(1 for kw in _ECON_NICHE if re.search(rf"\b{re.escape(kw)}\b", tl))
    econ = min(40 + niche_hits * 12, 80)
    if _NUMBER_RE.search(full):
        econ += 15
    if any(re.search(rf"\b{re.escape(kw)}\b", tl) for kw in
           ("kebijakan", "resmi", "ditetapkan", "berlaku", "putusan")):
        econ += 5
    econ = min(econ, 100)

    # Viral score (0-100) — skill doc section 8 weights
    now = time.time()
    age_h = (now - published_ts) / 3600 if published_ts else 999
    recency = 15 if age_h <= 24 else (10 if age_h <= 48 else 5)
    cross_pub = 15 if source_count >= 2 else 0
    momentum = min(15, sum(5 for kw in _MOMENTUM_KW if re.search(rf"\b{re.escape(kw)}\b", tl)))
    tension = min(10, sum(4 for kw in _TENSION_KW if re.search(rf"\b{re.escape(kw)}\b", full)))
    surprise = min(10, sum(5 for kw in _SURPRISE_KW if re.search(rf"\b{re.escape(kw)}\b", full)))
    stakes = min(20, sum(4 for kw in _STAKE_KW if re.search(rf"\b{re.escape(kw)}\b", full)))
    data_strength = 10 if _NUMBER_RE.search(full) else 0
    impact = econ // 10  # economic relevance feeds public-impact weight (20 max)
    discussion = min(5, sum(2 for kw in ("kata netizen", "komentar", "dibahas", "ramai dibicarakan")
                            if kw in full))
    viral = min(100, recency + cross_pub + momentum + tension + surprise + stakes + data_strength + impact + discussion)

    return {"viral_score": viral, "econ_score": econ, "age_hours": round(age_h, 1)}


def _cluster_stories(articles: list, threshold: float = 0.40) -> list:
    """Greedy Jaccard clustering on title content tokens (skill doc section 12).

    Returns list of [token_set, [articles]] clusters. Same story across
    outlets merges even when headlines differ; threshold keeps distinct
    stories apart.
    """
    clusters = []
    for t in articles:
        tk = _content_tokens(t.get("title", ""))
        best, best_j = None, 0.0
        for i, cl in enumerate(clusters):
            j = _jaccard(tk, cl[0])
            if j > best_j:
                best, best_j = i, j
        if best is not None and best_j >= threshold:
            clusters[best][0] |= tk
            clusters[best][1].append(t)
        else:
            clusters.append([tk, [t]])
    return clusters


def _s_tier_stories(stories: list) -> list:
    """Keep only verified discovery stories meeting both S-tier thresholds."""
    return [s for s in stories if s["viral_score"] >= 75 and s["econ_score"] >= 75]


def _content_angles(title: str, econ_score: int) -> list:
    """Skill doc section 19: deterministic A/B/C angles."""
    tl = title.lower()
    angles = []
    if any(re.search(rf"\b{re.escape(kw)}\b", tl) for kw in
           ("harga", "tarif", "pajak", "bbm", "listrik", "cicilan", "bunga",
            "biaya hidup", "daya beli")):
        angles.append("A: dampak ke dompet — berapa tambahan pengeluaran bulanan")
    if any(re.search(rf"\b{re.escape(kw)}\b", tl) for kw in
           ("tumbuh", "naik", "rekor", "tertinggi", "baik", "membaik", "surplus")):
        angles.append("B: hidden contradiction — angka bagus di headline vs kondisi di lapangan")
    if any(re.search(rf"\b{re.escape(kw)}\b", tl) for kw in
           ("akan", "berlaku", "ditetapkan", "resmi", "mulai", "rencana")):
        angles.append("C: what happens next — konsekuensi lanjutan bagi masyarakat")
    if len(angles) < 3:
        angles.append("C: what happens next — siapa yang kena, kapan, seberapa besar")
    return angles[:3]


def main():
    hours = 24
    top_n = 10
    quiet = False
    for arg in sys.argv[1:]:
        if arg.startswith("--hours="):
            hours = int(arg.split("=")[1])
        elif arg.startswith("--top="):
            top_n = int(arg.split("=")[1])
        elif arg == "--quiet":
            quiet = True

    cutoff = time.time() - hours * 3600

    # Discovery (skill doc section 6)
    all_items = []
    for cluster, queries in _GNEWS_QUERIES.items():
        for q in queries:
            all_items.extend(_gnews_fetch(q))
    # Dedup by title signature
    seen, items = set(), []
    for t in all_items:
        sig = t["title"][:50].lower().strip()
        if sig not in seen:
            seen.add(sig)
            items.append(t)
    log.info(f"Discovery: {len(items)} unique items from {len(_GNEWS_QUERIES)} clusters")

    # Freshness filter (skill doc section 3)
    fresh = [t for t in items if t.get("published_ts") and t["published_ts"] >= cutoff]

    # Cluster same story (skill doc section 12) — 2+ independent sources = confirmed
    clusters = _cluster_stories(fresh)

    # Score after clustering: cross-publisher coverage feeds viral score
    for cl_tokens, arts in clusters:
        sources = {a.get("source", "").strip().lower() for a in arts if a.get("source")}
        sources.discard("")
        for t in arts:
            info = _score_article(t["title"], t.get("description", ""),
                                  t.get("published_ts") or 0, len(sources))
            t["viral_score"] = info["viral_score"]
            t["econ_score"] = info["econ_score"]
            t["age_hours"] = info["age_hours"]
            t["_source_count"] = len(sources)

    stories = []
    for cl_tokens, arts in clusters:
        sources = {a.get("source", "").strip().lower() for a in arts if a.get("source")}
        sources.discard("")
        top = max(arts, key=lambda a: a["viral_score"])
        stories.append({
            "signature": " ".join(sorted(cl_tokens))[:80],
            "headline": top["title"],
            "publisher": top.get("source", "?"),
            "published_ts": top.get("published_ts"),
            "url": top.get("url", ""),
            "sources": sorted(sources),
            "source_count": len(sources),
            "viral_score": top["viral_score"],
            "econ_score": top["econ_score"],
            "content_angles": _content_angles(top["title"], top["econ_score"]),
        })
    stories.sort(key=lambda s: (s["source_count"] >= 2, s["viral_score"]), reverse=True)

    # Only S-tier stories feed ranking/boosts. Sparse output is correct; never
    # fill with weak discovery matches because this file affects live selection.
    eligible = _s_tier_stories(stories)
    ranked = eligible[:top_n]
    log.info(f"Stories: {len(stories)} | eligible (>=75/75): {len(eligible)} | ranked: {len(ranked)}")

    # ── hot_econ.json for pipeline merge ─────────────────────────────────────
    # gnews URLs are JS redirect stubs; pipeline matches by domain + ts window.
    boosts = {}
    for i, s in enumerate(ranked[:5]):
        boosts[s["url"]] = max(40 - i * 8, 8)  # rank1=40, rank2=32, ...
    data = {
        "date": datetime.now(WIB).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(WIB).isoformat(),
        "search_window_hours": hours,
        "method": "gnews 6-cluster 24h scan; viral+econ score >=75; 2+ independent sources",
        "stories": [
            {
                "rank": i + 1,
                "headline": s["headline"],
                "publisher": s["publisher"],
                "domain": s["publisher"].lower().lstrip("www."),
                "title_tokens": sorted(_content_tokens(s["headline"])),
                "published_ts": s["published_ts"],
                "url": s["url"],
                "sources": s["sources"],
                "source_count": s["source_count"],
                "viral_score": s["viral_score"],
                "economic_relevance_score": s["econ_score"],
                "confidence": "high" if s["source_count"] >= 2 else "medium",
                "content_angles": s["content_angles"],
            }
            for i, s in enumerate(ranked[:top_n])
        ],
        "boosts": boosts,
    }
    (BASE_DIR / "hot_econ.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
    log.info(f"Wrote hot_econ.json ({len(data['stories'])} stories, {len(boosts)} boosts)")

    if quiet:
        return

    # ── Markdown report (skill doc section 18) ───────────────────────────────
    lines = [f"# Top Viral Economy Stories", f"_{datetime.now(WIB).strftime('%d %b %Y %H:%M WIB')} | window {hours}h | eligible ≥75/75_", ""]
    if not ranked:
        lines.append("Tidak ada story lolos threshold 75/75. Coba --hours 48/72.")
    for i, s in enumerate(ranked[:top_n], 1):
        conf = "High" if s["source_count"] >= 2 else "Medium"
        lines.append(f"## {i}. {s['headline']}")
        lines.append(f"**Viral Score:** {s['viral_score']}/100 | **Economic Relevance:** {s['econ_score']}/100 | **Confidence:** {conf}")
        lines.append(f"**Published:** {datetime.fromtimestamp(s['published_ts'], WIB).strftime('%Y-%m-%d %H:%M') if s['published_ts'] else '?'} | **Source:** {s['publisher']}")
        lines.append(f"**Sumber independen:** {s['source_count']} — {', '.join(s['sources'][:4])}")
        lines.append(f"**URL:** {s['url']}")
        for a in s["content_angles"]:
            lines.append(f"- Angle: {a}")
        lines.append("")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
