# TechBro Pipeline

Content automation pipeline for [@ryanhadiii](https://www.threads.com/@ryanhadiii) — "1% Better" personal branding on Threads.

Scrapes articles → scores by relevance → generates 6-slide carousel via LLM (9router/Deepseek → Mistral → Groq) → evaluator review with retry → posts to Threads. Fully automated, runs hourly via cron.

## Architecture

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐    ┌──────────────┐
│  1. SCRAPE  │───▶│ 2. GENERATE  │───▶│ 3. EVALUATE │───▶│  4. POST     │
│ Google News │    │ Deepseek LLM │    │ LLM review  │    │ Threads API  │
│ RSS + Trends│    │ 3-provider   │    │ max 3 retry │    │ thread chain │
│ 17-comp scr │    │ fallback     │    │ ground check│    │              │
└─────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
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

3. **Score** — 17-component scoring (Pressbox-adapted): keyword match (3-tier), category, recency, data/konkret, source tier (10/8/6/3), audience reach, drama signal, paradox bonus, niche penalty, western penalty, hot topic bonus, peak-hour, analytics boost, density bonus, **human interest**. Soft cap: diminishing returns above 100.

4. **Generate** — 3-provider fallback chain: 9router/Deepseek (no rate limits) → Mistral Large → Groq. Generator produces 6-slide carousel with **hook pattern selection** (controversy/curiosity/paradox/detail+emotion) chosen dynamically from article analysis. Slides: Hook → Setup → Twist → Deep → So What → CTA. Voice: casual Indonesian ("lu/gw"), anti-LinkedIn.

5. **Evaluate** — Independent LLM review checks hallucination, story arc flow, hook quality, grounding (fact vs speculation). Scoring pattern: accept ≥7/10, revise <7, reject <5 with automatic retry (max 3x with error feedback). Skipped for score ≥80 (saves 50s). Fails open on error.

6. **Post** — Carousel posted as thread chain. Slide 1 = root, slides 2-6 = replies. 20 posts/day limit + dynamic daily limit based on engagement analytics.

## Content Rules

- **Voice**: Casual Indonesian ("lu/gw"), empathetic, blunt, anti-corporate, anti-LinkedIn
- **Framework**: RCTOE v2 (Role, Context, Task, Output, Execution)
- **Format**: 6-slide carousel (Hook → Context → Escalation → Tips → Big Lesson → CTA)
- **Viral criteria**: 7 criteria per slide (Pro&Con, Relatable, Famous Figure, Trending, Ironi, Surprising Fact, Emotional Hook)
- **Hook patterns**: controversy, curiosity, paradox, data drop, quote, realization, contrast — auto-selected from article analysis
- **Sources**: 4 source tiers — premium (founder, finance, mindset_bisnis), strong (karir, habits, produktivitas, sidehustle), moderate (workplace, startup_tech, tech_bisnis, skill_tech), unknown
- **Celebrity cap**: 30% max per 48h window
- **Daily limit**: 20 posts/day, auto-adjusted from engagement
- **Banned patterns**: 84+ (cringe phrases, generic hooks, hallucinated facts, foreign book references)
- **Grounding rules**: 10 rules, auto-REJECT if grounding < 5/10, deterministic proper noun + number validator
- **Self-dev identity**: excludes famous startup founders (e.g. Elon Musk, Jack Ma) — targets relatable achievers

## Pressbox Parity (~98%)

TechBro mirrors [Pressbox](https://github.com/hadijayyy/pressbox-pipeline) architecture for self-dev niche:

| Feature | Status | Detail |
|---------|--------|--------|
| RCTOE framework | ✅ | Role, Context, Task, Output, Execution |
| 17-component scoring | ✅ | Added human interest + source tier upgrade |
| Evaluator loop | ✅ | Independent LLM review, max 3 retry with feedback |
| Grounding score | ✅ | Auto-REJECT if < 5/10 |
| Grounding validator | ✅ | Deterministic proper noun + number check in prompt |
| Viral criteria | ✅ | 7 criteria per slide |
| Worked example | ✅ | Full JSON GTA VI example in prompt |
| Local content rules | ✅ | 2/3 recommendations must be Indonesian-known |
| Foreign book detection | ✅ | Postprocess flags obscure foreign books |
| 4h article cache | ✅ | Persistent, rolling window |
| Hot topic detection | ✅ | Union-Find, 30+ entities |
| Hook analytics | ✅ | DB-based, activates at 20+ posts |
| Hook pattern selection | ✅ | Dynamic from article analysis |
| Banned patterns | ✅ | 84+ (stricter than Pressbox) |
| Grounding rules | ✅ | 10 anti-hallucination rules |
| Escalation arc | ✅ | Hook→Context→Escalation→Tips→Lesson→CTA |
| Caption rules | ✅ | 2-3 lines, zero emoji/hashtags |
| A/B testing | ✅ | 3 variants with hook quality scoring |
| Source fingerprints | ✅ | Skip unchanged RSS feeds |
| Title similarity dedup | ✅ | Jaccard + stopwords, 72h window, threshold 0.35 |
| Engagement feedback | ✅ | Pull views/likes/replies via Threads Graph API |
| Human interest scoring | ✅ | 72 keywords, max 15 boost |
| Source tier system | ✅ | 4 tiers (10/8/6/3) |
| Regex whitespace safety net | ✅ | Final pass: double spaces, punctuation gaps, trailing period |
| 9router/Deepseek support | ✅ | Tier 1 provider, no rate limits |
| Telegram notify | ✅ | Post confirmation to Telegram |
| Cover image selection | ⏭️ | Skipped — low impact for text posts |

## Files

| File | Purpose |
|------|---------|
| `scripts/pipeline.py` | Main orchestrator: scrape → score → generate → evaluate → stage |
| `scripts/scraper.py` | Google News RSS + Trends scraping, 17-comp scoring, hot topic detection |
| `scripts/generator.py` | LLM carousel generation (3-provider fallback) + evaluator with retry |
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
#   ROUTER_KEY=...     (9router/Deepseek — tier 1)
#   MISTRAL_API_KEY=...
#   GROQ_API_KEY=...
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
