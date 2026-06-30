# 🇮🇩 TechBro Pipeline

Indonesian tech news carousel generator for Threads. Scrapes, generates, and posts 6-slide narrative carousels automatically.

## How It Works

```
RSS Sources → Scrape + Score → Generate Slides → Post to Threads
```

1. **Scrape** — Pulls articles from Indonesian tech RSS feeds (Kompas Tekno, CNN Indonesia Tekno, Liputan6, CNBC Indonesia)
2. **Score** — Keyword-based scoring with T1/T2/T3 priority tiers + product promo auto-zero filter
3. **Generate** — 6-slide narrative carousel via Mistral (primary) / Groq (fallback) with anti-hallucination rules
4. **Post** — Posts as threaded carousel to Threads with image validation

## Slide Structure

| Slide | Role | Words |
|-------|------|-------|
| 1 | **Hook** — fakta yang bikin "hah, serius?" | 50+ |
| 2 | **Setup** — backstory: siapa, apa, kenapa sekarang | 40-60 |
| 3 | **Twist** — konflik atau fakta gak disangka | 40-60 |
| 4 | **Deep Dive** — data/angka/teknis dari artikel | 40-60 |
| 5 | **So What** — kenapa ini penting buat lo | 30-50 |
| 6 | **CTA** — pertanyaan debat + source URL | 30-40 |

## Content Rules

- **Anti-hallucination** — semua fakta harus dari artikel, no invented stats
- **No product promo** — auto-zero score untuk artikel product launch/review
- **No fake quotes** — kutipan hanya dari narasumber asli
- **No cringe** — banned phrases stripped di postprocessor
- **Whitespace** — blank line antar setiap kalimat (pressbox pattern)
- **Voice** — Tech Bro Indonesia, 27 y/o, casual Indo-Inggris, "lo/gue"

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

# Run
bash scripts/run.sh
```

## Cron

```bash
# Every 6 hours
0 */6 * * * /home/ubuntu/techbro/scripts/run.sh
```

## Architecture

```
scripts/
├── scraper.py      # RSS fetch + keyword scoring
├── generator.py    # LLM carousel generation + postprocessing
├── poster.py       # Threads API posting with chain threading
├── pipeline.py     # Orchestrator: scrape → generate → stage
├── db.py           # SQLite storage (articles + posts)
├── threads_auth.py # Token management
└── run.sh          # Cron wrapper
```

## License

MIT
