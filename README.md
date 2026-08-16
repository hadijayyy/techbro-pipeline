# TechBro V5 — Ekonomi Indonesia + International

Content automation pipeline for [@ryanhadiii](https://www.threads.net/@ryanhadiii) — ekonomi Indonesia + international economy thread generator, reverse-engineered from viral Threads post patterns.

Scrapes CNBC/CNN/Detik Finance → scores by economy relevance → generates 6-slide thread via LLM → posts to Threads. Hourly cron 07:00-23:00 WIB.

## Architecture

```
┌─────────────┐    ┌──────────────┐    ┌──────────────┐    ┌────────────┐
│  1. SCRAPE  │───▶│ 2. CANDIDATE │───▶│ 3. GENERATE  │───▶│ 4. POST    │
│ 5 RSS/HTML  │    │ gate + rank  │    │ LLM V5       │    │ Threads    │
│ sources     │    │ title/body   │    │ formula      │    │ API        │
│             │    │ score≥3      │    │ anti-halus   │    │ chain 6    │
└─────────────┘    └──────────────┘    └──────────────┘    └────────────┘
```

## V5 Formula — Ryan Hadi Style

Reverse-engineered from viral @ryanhadiii post (Pelita Air → Garuda). 6-slide structure:

| Slide | Formula | Example |
|-------|---------|---------|
| **S1** | "Baru aja [event]. [dampak ke lo]" | "Baru aja Pelita Air dipindahin ke Garuda. Bisa ubah nasib 10.000 karyawan dan harga tiket lo." |
| **S2** | "[Angka A] vs [Angka B]. Siapa deg-degan?" | "Pelita Air 3.200 vs Garuda 11.000 karyawan. Siapa paling deg-degan?" |
| **S3** | "[Alasan resmi]. Tapi [realita]. [bukti]" | "Alasannya biar penerbangan kuat. Tapi efisiensi = PHK. Garuda aja tutup rute tahun lalu." |
| **S4** | "Yang kena: [profesi A,B,C]. Aman: [profesi X,Y]" | "Paling kena: petugas check-in, mekanik, pilot. Aman: IT, manajemen." |
| **S5** | "Bisa [buruk] kalau [X]. Bisa [baik] kalau [Y]" | "Harga tiket? Bisa naik kalau Garuda monopoli. Bisa turun kalau efisiensi berhasil." |
| **S6** | "Lo kerja di [niche]? Cerita dong." + URL | "Lo kerja di maskapai BUMN? Cerita dong suasana kantor sekarang." |

## Voice

- **"Lo"** not "kalian" — DM-style, 1:1 feel
- 1-2 sentences per slide, phone-optimized
- Opinionated, takes the side of regular people
- Translates economy jargon: "holding company → perusahaan induk yang ngatur anak perusahaan"
- **Banned words**: akselerasi, mitigasi, implementasi, optimalisasi, signifikan, komprehensif, bayangin, foto ini, terlihat

## Anti-Hallucination

Multi-layer defense:
1. Body ≥ 500 chars required
2. Topic score ≥ 3 (economy + impact)
3. LLM fact-extraction pre-step before writing
4. Post-generation validator: every number, name, institution checked against article body
5. Max 2 revision attempts per article
6. Retry on next candidate if generation fails

## Candidate Gates

| Gate | Threshold |
|------|-----------|
| Title economy signals | Must match keyword list |
| Body length | ≥ 500 chars |
| Topic score | ≥ 3/10 |
| Image | HD required (1200×670+) |
| Source publish time | ≤ 24h, not future |
| Repeat detection | Skip same issue within 72h; same entity alone allowed |

## Sources

| Source | Type | Items |
|--------|------|-------|
| CNN Ekonomi | RSS | 100 |
| Detik Finance | RSS | 100 |
| CNBC Global | RSS | 100 |
| CNBC Market | RSS | 100 |
| BBC Business | RSS | 100 |
| Detik Finance | HTML fallback | 68 |

## Setup

```bash
git clone https://github.com/hadijayyy/techbro-pipeline.git
cd techbro-pipeline

# Environment
cp .env.example .env
# THREADS_ACCESS_TOKEN=...
# THREADS_USER_ID=...

pip install httpx beautifulsoup4 lxml

# Test
python3 pipeline-v3.py --dry-run

# Live
python3 pipeline-v3.py

# Cron: Hermes cron job "Techbro Hourly" (0 7-23 * * *)
```

## Files

| File | Purpose |
|------|---------|
| `pipeline-v3.py` | Main pipeline (V5 prompt, anti-hallucination, retry logic) |
| `posted_topics_v2.json` | Dedup tracker |
| `pov_affiliate.json` | S7 affiliate rotation |
| `keywords.json` | Economy keyword categories |

## License

MIT
