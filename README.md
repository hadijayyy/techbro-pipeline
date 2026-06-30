# 🇮🇩 TechBro Pipeline

Indonesian tech/AI + lifestyle + entrepreneurship carousel generator for Threads (@ryanhadiii). Scrapes, scores, generates, and posts 6-slide narrative carousels automatically.

## How It Works

```
7 Sources (3 Niches) → Scrape + Score → LLM Generate → Post to Threads
```

1. **Scrape** — Pulls articles from 7 Indonesian news sources across 3 niches
2. **Score** — 3-tier keyword scoring (T1/T2/T3) + penalty filters + Jaccard title dedup (threshold >25)
3. **Generate** — 6-slide narrative carousel via Mistral-large (primary) / Groq (fallback) with anti-hallucination rules
4. **Post** — Posts as threaded carousel to Threads with image pre-validation + TEXT fallback

## Niches

| Niche | Sources | Keywords |
|-------|---------|----------|
| Tech/AI | Kompas, Detik, CNN Tekno | chatgpt, openai, hack, deepfake, llm, startup |
| Lifestyle + Psikologi | CNN Gaya Hidup, CNBC Lifestyle | produktif, burnout, depresi, kesehatan mental, motivasi |
| Entrepreneurship | CNBC MyMoney, Liputan6 Bisnis | umkm, wirausaha, entrepreneur, bisnis online, passive income |

## Slide Structure

| Slide | Role | Words |
|-------|------|-------|
| 1 | **Hook** — langsung ke fakta paling mengejutkan dari artikel | 50+ |
| 2 | **Setup / Reality Check** — jembatan hook ke isi berita | 40-60 |
| 3 | **Twist / Core Fact** — fakta mengejutkan, bahasa simpel | 40-60 |
| 4 | **Deep Dive / Impact** — data + dampak nyata dari artikel | 40-60 |
| 5 | **So What / Big Lesson** — mindset shift, kenapa penting buat lo | 30-50 |
| 6 | **CTA** — pertanyaan debat + source URL | 30-40 |

## Content Rules (v2 — Prompt Merged 30 Jun 2026)

- **Hook langsung ke fakta** — kalimat pertama langsung pukul, tanpa "bayangin lo lagi..." atau analogi random
- **Cliffhanger** — slide 1-5 wajib akhir dengan kalimat gantung yang bikin penasaran
- **Analogi lokal** — pakai keresahan lokal (budak korporat, dompet tipis, Gen-Z) di slide 2-5 SAJA
- **Anti-hallucination** — semua fakta/angka dari artikel, no invented stats, no "setara X" tanpa sumber
- **No product promo** — auto-zero score untuk artikel product launch/review (≥3 penalty keywords)
- **No fake quotes** — kutipan hanya dari narasumber asli, postprocessor strips imajiner dialogue
- **No em-dash** — postprocessor replaces (—) → koma
- **No cringe** — banned phrases stripped (geleng-geleng, gila sih, kebayang gak, dll)
- **Whitespace** — blank line antar setiap kalimat
- **Voice** — Content Creator/Scriptwriter, 27 y/o, casual Jakarta, "lo/gua"
- **JSON flat** — output `slide_1` s/d `slide_6`, no nested objects

## Anti-Bot: Random Jitter

```bash
# Script sleeps 0-40 minutes before executing
# Combined with hourly cron → actual interval ~50-100 minutes
# Prevents detection as automated bot
JITTER=$(( RANDOM % 2401 ))
sleep "$JITTER"
```

## Setup

```bash
# Clone
git clone https://github.com/hadijayyy/techbro-pipeline.git
cd techbro-pipeline

# Install deps
pip install httpx

# Set env vars (in .env or export)
export MISTRAL_API_KEY="your-key"
export GROQ_API_KEY="your-key"
export THREADS_ACCESS_TOKEN="your-token"
export THREADS_USER_ID="your-user-id"

# Test scoring only
python3 scripts/pipeline.py --dry-run

# Full run (scrape → generate → post)
bash scripts/run.sh

# Smoke test (20 tests)
python3 scripts/test_smoke.py
```

## Cron (Hermes Agent)

```
Schedule: 0 * * * * (every hour)
Jitter:   0-40 min random delay in script
Actual:   ~50-100 min between posts
Delivery: telegram:1022032312:55007 (summary report)
Mode:     no_agent=true (script-only, zero LLM cost)
```

## Architecture

```
scripts/
├── scraper.py       # Async scraper for 7 Indo sources (RSS + sitemap)
├── generator.py     # LLM carousel generation + postprocessing (v2 prompt)
├── poster.py        # Threads API: 2-step (container → publish) + chain replies
├── pipeline.py      # Orchestrator: scrape → score → dedup → generate → stage
├── db.py            # SQLite ORM: articles, posts, performance (WAL mode)
├── threads_auth.py  # OAuth: auth URL → code exchange → long-lived token
├── test_smoke.py    # 20-test smoke suite (DB, scraper, generator, poster)
└── run.sh           # Cron wrapper with random jitter
```

## Token Source

Token lives at `/home/ubuntu/threads-agent/.env` (shared with threads-agent pipeline). Both `poster.py` and `threads_auth.py` auto-load from this file.

## Git

```
Repo:   https://github.com/hadijayyy/techbro-pipeline.git
Branch: main
.gitignore: __pycache__/, *.db, *.sqlite, .env, output/, *.log
```

**DO NOT push techbro code to pressbox-pipeline** — that repo is for pressbox only.

## License

MIT
