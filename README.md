# TechBro Pipeline

Content automation pipeline for [@ryanhadiii](https://www.threads.com/@ryanhadiii) — "1% Better" personal branding on Threads.

## What It Does

Scrapes articles → scores by relevance → generates 6-slide carousel via LLM → evaluator review → posts to Threads. Fully automated, runs hourly via cron.

## Architecture

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐    ┌──────────────┐
│  1. SCRAPE  │───▶│ 2. GENERATE  │───▶│ 3. EVALUATE │───▶│  4. POST     │
│ Google News │    │ Mistral LLM  │    │ Independent │    │ Threads API  │
│ RSS + Trends│    │ 6-slide caro │    │ LLM review  │    │ thread chain │
└─────────────┘    └──────────────┘    └─────────────┘    └──────────────┘
       │
       ▼
┌─────────────┐
│ HOT TOPIC   │  Union-Find clustering, 4h rolling cache
│ DETECTION   │  30+ entity recognition, +25 boost
└─────────────┘
```

## Pipeline Flow

1. **Scrape** — 6 Google News RSS feeds (mindset, tech, career, entrepreneur, celebrity ID/global) + 10 Google Trends queries. 50 articles scraped, shuffled for diversity.

2. **Hot Topic Detection** — Union-Find clustering by entity overlap. 30+ known entities (Indonesian + international names, companies). 4h rolling cache sees 80-120 articles vs 20 per run. Boost: +25 (3+ sources) or +15 (2 sources).

3. **Score** — 15-component scoring system (Pressbox-adapted): keyword match (3-tier), category, recency, data/konkret, source tier, audience reach, drama signal, paradox bonus, niche penalty, hot topic, peak-hour, density bonus, soft cap.

4. **Generate** — Mistral Large generates 6-slide carousel. Slides: Hook → Setup → Twist → Deep → So What → CTA. Voice: casual Indonesian ("lu/gw"), anti-LinkedIn.

5. **Evaluate** — Independent LLM review (mistral-small) checks for hallucination, story flow, hook quality. APPROVE/REVISE/REJECT. Skipped for score ≥80 (saves 50s). Fails open on error.

6. **Post** — Carousel posted as thread chain. Slide 1 = root, slides 2-6 = replies. 20 posts/day limit.

## Content Rules

- **Voice**: Casual Indonesian ("lu/gw"), empathetic, blunt, anti-corporate
- **Framework**: RCTOE v2 (Role, Context, Task, Output, Execution)
- **Format**: 6-slide carousel (Hook → Context → Escalation → Tips → Big Lesson → CTA)
- **Viral criteria**: 7 kriteria per slide (Pro&Con, Relatable, Famous Figure, Trending, Ironi, Surprising Fact, Emotional Hook)
- **Sources**: 6 feeds (celebrity global, celebrity ID, entrepreneur, mindset, tech, career)
- **Celebrity cap**: 30% max per 48h window
- **Daily limit**: 20 posts/day
- **Banned patterns**: 84+ (cringe phrases, generic hooks, hallucinated facts)
- **Grounding**: 10 rules, auto-REJECT if grounding < 5/10
- **Worked example**: Full JSON example in prompt for consistency

## Pressbox Parity (~95%)

TechBro mirrors [Pressbox](https://github.com/hadijayyy/pressbox-pipeline) architecture for self-dev niche:

| Feature | Status | Detail |
|---------|--------|--------|
| RCTOE framework | ✅ | Role, Context, Task, Output, Execution |
| 15-component scoring | ✅ | Adapted for self-dev keywords |
| Evaluator loop | ✅ | Independent LLM review, always runs |
| Grounding score | ✅ | Auto-REJECT if < 5/10 |
| Viral criteria | ✅ | 7 criteria per slide |
| Worked example | ✅ | Full JSON GTA VI example in prompt |
| Local content rules | ✅ | 2/3 recommendations must be Indonesian-known |
| Foreign book detection | ✅ | Postprocess flags obscure foreign books |
| 4h article cache | ✅ | Persistent, rolling window |
| Hot topic detection | ✅ | Union-Find, 30+ entities |
| Hook analytics | ✅ | DB-based, activates at 20+ posts |
| Banned patterns | ✅ | 84+ (stricter than Pressbox) |
| Grounding rules | ✅ | 10 anti-hallucination rules |
| Escalation arc | ✅ | Hook→Context→Escalation→Tips→Lesson→CTA |
| Caption rules | ✅ | 2-3 lines, zero emoji/hashtags |
| A/B testing | ✅ | 3 variants with hook quality scoring |
| Source fingerprints | ✅ | Skip unchanged RSS feeds |
| Title similarity dedup | ✅ | Jaccard + stopwords, 72h window, threshold 0.35 |
| Engagement feedback | ✅ | Pull views/likes/replies via Threads Graph API |
| Telegram notify | ✅ | Post confirmation to Telegram |
| Cover image selection | ⏭️ | Skipped — low impact for text posts |

## Files

| File | Purpose |
|------|---------|
| `scripts/pipeline.py` | Main orchestrator: scrape → score → generate → evaluate → stage |
| `scripts/scraper.py` | Google News RSS + Trends scraping, scoring, hot detection |
| `scripts/generator.py` | LLM carousel generation + evaluator loop |
| `scripts/poster.py` | Threads API wrapper for posting staged content |
| `scripts/db.py` | SQLite database operations |
| `scripts/techbro-pipeline.sh` | Cron wrapper script |

## Setup

```bash
# Clone
git clone https://github.com/hadijayyy/techbro-pipeline.git
cd techbro-pipeline

# Environment
cp .env.example .env
# Edit .env with your keys:
#   MISTRAL_API_KEY=...
#   THREADS_ACCESS_TOKEN=...
#   THREADS_USER_ID=...

# Install deps
pip install httpx beautifulsoup4 lxml google-news-decoder

# Run
python3 scripts/pipeline.py --dry-run  # test
python3 scripts/pipeline.py            # live

# Cron (hourly)
crontab -e
0 * * * * cd ~/techbro && bash scripts/techbro-pipeline.sh
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TOP_N` | 1 | Articles per run |
| `DAILY_LIMIT` | 20 | Max posts per day |
| `ARTICLE_CACHE_HOURS` | 4 | Rolling cache window |
| `MAX_AGE_HOURS` | 720 | Article freshness (30 days) |
| `celebrity_cap` | 30% | Max celebrity content per 48h |

## Monitoring

```bash
# Check stats
python3 -c "import sys; sys.path.insert(0,'scripts'); from db import get_stats, get_db; print(get_stats(get_db()))"

# Check daily posts
python3 -c "import sqlite3; conn=sqlite3.connect('pipeline.db'); conn.row_factory=sqlite3.Row; print(conn.execute(\"SELECT COUNT(*) FROM posts WHERE status='posted' AND date(posted_at)=date('now')\").fetchone()[0])"

# Check cache size
wc -l ~/.hermes/techbro/article-cache.json
```

## License

MIT
