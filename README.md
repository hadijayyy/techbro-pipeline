# TechBro V3 — Ekonomi Nasional Indonesia

Content automation pipeline for [@ryanhadiii](https://www.threads.net/@ryanhadiii) — Ekonomi Nasional Indonesia thread generator.

Scrapes CNBC/CNN/Detik Finance/Kompas Money → scores by economy relevance → generates 6-slide thread via Mistral → posts to Threads.

## Architecture

```
┌─────────────┐    ┌─────────────┐    ┌──────────────┐    ┌────────────┐
│  1. SCRAPE  │───▶│ 2. SCORE    │───▶│ 3. GENERATE  │───▶│ 4. POST    │
│ 6 RSS/HTML  │    │ 8 category  │    │ Mistral LLM  │    │ Threads    │
│ sources     │    │ entity      │    │ 7 arc prompt │    │ API        │
│             │    │ threshold   │    │ revision     │    │            │
└─────────────┘    └─────────────┘    └──────────────┘    └────────────┘
```

## Pipeline Flow

1. **Scrape** — 6 sources parallel (CNBC, CNN Ekonomi, Detik Finance, Detik Hukum, Kompas Money, CNN Nasional). RSS + HTML fallback. ~75 articles per run. Tempo excluded (always 403).

2. **Score** — 8 category system with per-category cap:
   - Dompet Langsung (30): gaji, upah, UMR, THR, PHK, pajak, PPN, PPh, BPJS, subsidi, BBM, KPR, pinjol, inflasi
   - Kebijakan Ekonomi (25): APBN, defisit, suku bunga, rupiah, ekspor, IP, PDB
   - Tenaga Kerja (25): PHK massal, pengangguran, upah minimum, bonus
   - Harga Pangan (25): beras, pangan, BBM, tarif listrik, biaya sekolah
   - Kredit & Utang (22): KPR, cicilan, paylater, pinjol, gagal bayar
   - Pasar Modal (18): IHSG, saham, emas, rupiah, dolar
   - Korupsi (18): korupsi, suap, gratifikasi, KPK, kejagung
   - Bonus Angka (max 15): Rp100jt +3, Rp1T +5, Rp10T+ +10

   Entity boost: Otoritas +10 (sri mulyani, perry warjiyo, presiden), Figur +7 (ahlis, lutfi), Institusi +5 (KPK, BI)
   Freshness multiplier: <6h=1.0, <12h=0.9, <24h=0.75, <48h=0.5, >48h=0.2
   Source quality: CNBC 1.1, Detik Finance 1.0, CNN 0.9
   Threshold: reject<45, backup 45-59, process≥60, priority≥75

3. **Generate** — 7-arc system (Dompet Kejepit, Market Shock, Policy Bomb, Global Domino, Personal Finance, Jobs Under Pressure, Public Money Trail, Debt Trap). Each arc has unique S1-S6 structure (Hook → Context → Why → Impact → Trade-off → CTA). Mistral LLM with revision gate for quality.

4. **Validate** — Deterministic post-gen checks: slop detection, Chinese filler, "baru aja" hook (article must be ≤48h), rhetorical questions (S1-5 only), empty post, CTA presence. Revision retry on failure.

5. **Post** — 6-slide thread chain via Threads Graph API. Slide 1=root, 2-6=replies. HD image from article og:image (skip if absent).

## Content Rules

- **Voice**: Casual Indonesian ("lu/gue"), ironi, angka real, no AI slop
- **Format**: 6-slide per arc. Each arc has specific slide purpose
- **Tag**: S1 Wajib `baru aja` untuk kejadian ≤48 jam
- **Banned**: Chinese filler, puja-puji pejabat, "Indonesia" repeated, "kalau/kita"
- **Reject**: olahraga, selebriti, bencana alam, pilkada/pilpres, parpol (hard -200)
- **Penalty**: hiburan, gempa, banjir, covid (soft -60)
- **CTA**: S6 wajib ada "?", "menurut lo", atau "pilih mana"

## Sources

| Source | Type | Score |
|--------|------|-------|
| CNBC Indonesia | RSS | 10 |
| Detik Finance | HTML | 9 |
| CNN Ekonomi | RSS | 9 |
| Detik Hukum | HTML | 8 |
| CNN Nasional | RSS | 8 |
| Kompas Money | HTML | 7 |

## Files

| File | Purpose |
|------|---------|
| `pipeline-v3.py` | Main pipeline: scrape → score → generate → post |
| `pipeline-v2.py` | Legacy self-dev pipeline (retired) |
| `posted_topics_v2.json` | Dedup tracker (URL + title hash) |

## Setup

```bash
# Clone
git clone https://github.com/hadijayyy/techbro-pipeline.git
cd techbro-pipeline

# Environment
cp .env.example .env
# Edit .env with:
#   MISTRAL_API_KEY=...
#   THREADS_ACCESS_TOKEN=...
#   THREADS_USER_ID=...

# Install deps (venv recommended)
pip install httpx beautifulsoup4 lxml

# Run
python3 pipeline-v3.py --dry-run  # test
python3 pipeline-v3.py            # live

# Cron (hourly 07:00-23:00 WIB)
# Uses Hermes cron job: techbro-daily (bbb505feb8ad)
# Schedule: 0 7-23 * * *
# Script: techbro-daily.sh
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_AGE_HOURS` | 48 | Article freshness cutoff |
| `SCORE_THRESHOLDS["process"]` | 60 | Minimum score to generate |
| `SCORE_THRESHOLDS["priority"]` | 75 | High-priority threshold |
| `IMAGE_REQUIRED` | False | Skip article if no og:image |

## Monitoring

```bash
# Check dedup state
cat posted_topics_v2.json | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d)} topics tracked')"

# Run dry-run
python3 pipeline-v3.py --dry-run
```

## License

MIT
