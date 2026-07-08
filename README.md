# 🇮🇩 TechBro Pipeline

Indonesian tech/AI carousel generator for Threads (@ryanhadiii). Scrapes, scores, generates, and posts 6-slide narrative carousels automatically. Pressbox-pattern architecture.

## How It Works

```
5 Sources → Scrape + Score → LLM Generate → Quality Gates → Post to Threads
```

1. **Scrape** — Pulls articles from 5 Indonesian news sources (Bloomberg Technoz, CNBC, Detik, Liputan6 Tekno, Liputan6 Bisnis)
2. **Score** — 3-tier keyword scoring (T1/T2/T3) + penalty filters + keyword-based relatability + Jaccard title dedup (threshold 0.5)
3. **Generate** — 6-slide narrative carousel via Mistral-large (primary) / Groq (fallback) with anti-hallucination rules
4. **Quality Gates** — Hook scoring (7/10 min), grounding check, jargon validator, inter-slide flow check
5. **Post** — Posts as threaded carousel to Threads with image pre-validation

## Sources

| Source | Niche | HD Images |
|--------|-------|-----------|
| Bloomberg Technoz | Tech/AI | ✅ |
| CNBC | Finance/Tech | ✅ |
| Detik | General Tech | ✅ |
| Liputan6 Tekno | Tech/AI | ✅ |
| Liputan6 Bisnis | Business | ✅ |

## Slide Structure

Mengikuti **Formula HISSC** (Hook → Insight → Support → Solution → CTA) + viral equation `Attention × Emotion × Novelty × Practical Value`:

| Slide | Role | Words |
|-------|------|-------|
| 1 | **Hook** — fakta paling mengejutkan dari artikel, data-driven pattern | <25 |
| 2 | **Problem/Insight** — expand dari hook, JANGAN lompat topik baru | <40 |
| 3 | **Contoh Nyata** — studi kasus / real-life example (brand, perusahaan, situasi konkret) | <40 |
| 4 | **Solusi/Tips** — advice praktis dari artikel | <40 |
| 5 | **Framework/Checklist** — 2-3 langkah konkret yang bisa langsung diterapkan | <40 |
| 6 | **Ringkasan + CTA** — poin utama + rotating closing pattern | <30 |

## Content Rules

- **Hook langsung ke fakta** — tanpa "bayangin lo lagi..." atau analogi random
- **Pattern C (Default)** — `[Specific number] + [Human consequence] + [Reply bait]` — proven 500K+ views
- **Contoh nyata wajib** — slide 3 HARUS kasih studi kasus/brand/situasi konkret, bukan teori
- **Framework/Checklist** — slide 5 kasih langkah praktis yang bisa langsung diterapkan
- **Ringkasan** — slide 6 mulai dengan poin utama sebelum CTA
- **Inter-slide flow** — slide 2 HARUS expand dari slide 1, JANGAN lompat topik
- **Jargon validator** — istilah asing WAJIB diterangkan dalam bahasa Indonesia
- **Cliffhanger** — slide 1-5 wajib akhir dengan kalimat gantung
- **Anti-hallucination** — semua fakta/angka dari artikel, grounding check strips unverified claims
- **No product promo** — auto-zero score untuk artikel product launch/review
- **No fake quotes** — kutipan hanya dari narasumber asli
- **No em-dash** — postprocessor replaces (—) → koma
- **No cringe** — banned phrases stripped (geleng-geleng, gila sih, kebayang gak, dll)
- **Voice** — Content Creator, 27 y/o, casual Jakarta, "lo/gua"
- **JSON flat** — output `slide_1` s/d `slide_6`, no nested objects

## Hook System (Pressbox Pattern)

Weighted pattern selection (data-driven, not random):

| Pattern | Weight | Description | Example |
|---------|--------|-------------|---------|
| **PATTERN_C** | 5x (43%) | `[Number] + [Consequence] + [Reply bait]` | "4.800 karyawan Microsoft di-PHK. Lo yang kerja di tech kena dampaknya?" |
| CONTRAST | 2x | Reality vs expectation | "APBN surplus, tapi SAL-nya gak dipake buat rakyat" |
| IMPACT | 1x | Direct impact on reader | "Gaji lo bisa kena potong 2% mulai Juli" |
| QUESTION | 1x | Provocative question | "Lo tau gak sih berapa pajak yang lo bayar sebenarnya?" |
| CURIOSITY | 1x | Hidden angle reveal | "Yang gak dibahas: lo juga bisa kena dampaknya" |

**Hook scoring** (0-10): word count (2pt) + number/specific (2pt) + emotional weight (1pt) + impact verbs (1pt) + Indonesia context (1pt) + strong opening (1pt) + reply bait (1pt) + proper casing (1pt). Minimum 7/10 to pass.

## CTA Rotation

8 closing patterns, DB-aware (avoids repeating recent patterns):

1. **Pengalaman** — "Lo pernah [situasi]? Share di komen."
2. **Pilihan A/B** — "Lo lebih milih [A] atau [B]?"
3. **Provokatif** — "Menurut lo ini bagus atau berbahaya?"
4. **Prediksi** — "Lo prediksi tren ini bakal gimana?"
5. **Challenge** — "Coba tebak [fakta]. Jawab di komen."
6. **Reflektif** — "Kalau lo di posisi ini, lo bakal gimana?"
7. **Hot take** — "Unpopular opinion: [take]. Setuju atau enggak?"
8. **Story** — "Pernah ngalamin [situasi]? Cerita dong."

## Quality Gates

| Gate | Threshold | Action |
|------|-----------|--------|
| Hook score | 7/10 min | Auto-rewrite if below |
| Hook validation | Must pass | Regenerate variant if fails |
| Grounding check | 0 violations | Block fabricated numbers/quantities |
| Jargon check | 2+ unexplained terms | Regenerate slides |
| Inter-slide flow | 2+ issues | Regenerate with stronger flow instruction |
| Evaluator | APPROVE/REVISE | Up to 3 retries, 7/10 quality gate |
| Topic relevance | Score ≥ 0.15 | Reject if unrelated |

## Dynamic Daily Limit

Post frequency adapts to performance (7-day median views):

| Median Views | Daily Limit |
|-------------|-------------|
| ≥ 5,000 | 25 posts/day |
| ≥ 2,000 | 20 posts/day |
| ≥ 1,000 | 18 posts/day |
| ≥ 500 | 15 posts/day |
| < 500 | 12 posts/day |

## A/B Tracking

Each post stores tracking data for performance analysis:

| Column | Type | Description |
|--------|------|-------------|
| `hook_pattern` | TEXT | Which hook pattern was used |
| `hook_score` | INTEGER | Hook quality score (0-10) |
| `cta_pattern` | TEXT | Which CTA pattern was used |

## Anti-Bot: Random Jitter

```bash
# Script sleeps 0-40 minutes before executing
# Combined with hourly cron → actual interval ~50-100 minutes
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
├── scraper.py       # Async scraper for 5 Indo sources (RSS + sitemap)
├── generator.py     # LLM carousel generation + hook scoring + quality gates
├── poster.py        # Threads API: 2-step (container → publish) + chain replies
├── pipeline.py      # Orchestrator: scrape → score → dedup → generate → stage
├── db.py            # SQLite ORM: articles, posts, performance (WAL mode)
├── threads_auth.py  # OAuth: auth URL → code exchange → long-lived token
├── watchdog-techbro.sh  # Health check + auto-recovery
└── techbro-pipeline.sh  # Cron wrapper with random jitter
```

## Token Source

Token lives at `/home/ubuntu/threads-agent/.env` (shared with threads-agent pipeline). Both `poster.py` and `threads_auth.py` auto-load from this file.

## Git

```
Repo:   https://github.com/hadijayyy/techbro-pipeline.git
Branch: main
.gitignore: __pycache__/, *.db, *.sqlite, .env, output/, *.log, .pipeline.pid, hot-cache.json
```

**DO NOT push techbro code to pressbox-pipeline** — that repo is for pressbox only.

## License

MIT
