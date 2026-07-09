# Techbro Pipeline — Dry Run Report

**Date:** 2026-07-10, 00:00 WIB  
**Status:** ✅ Completed in 39.9s  
**DB:** 185 articles | 0 staged | 148 posted

---

## Pipeline Status

| Metric | Value |
|--------|-------|
| Dynamic Limit | 20/day (median views: 864) |
| Posted Today | 20/20 (at limit) |
| Posting Window | Active (0:00 WIB) |

---

## Analytics Summary

**Median Views:** 920 (from 29 posts)

### Top Performing Categories

| Category | Avg Views | Posts | Boost |
|----------|-----------|-------|-------|
| phk_layoff | 42,889 | 3 | +20 |
| ecommerce_reg | 26,927 | 6 | +20 |
| ai_tech | 19,212 | 10 | +20 |
| indo_gov | 15,210 | 13 | +20 |
| indo_local | 6,187 | 6 | +20 |
| ojol_ridehail | 3,861 | 2 | +20 |
| niche_ngo | 3,861 | 2 | +20 |
| bigtech_microsoft | 1,432 | 2 | +5 |

### Hook Performance

| Hook Type | Avg Views | Posts | Boost |
|-----------|-----------|-------|-------|
| TRUTH_BOMB | 7,211 | 25 | +15 |
| HIDDEN_TRUTH | 6,027 | 4 | +15 |

**Top Hook Keywords:** karyawan, kerja, global, microsoft, orang, dibahas, buruh, masih

---

## Scraping Results

**Total Scraped:** 50 articles

### Hot Articles (+15 boost)

| Source | Title |
|--------|-------|
| james_clear | Happy 1st Birthday, Atomic Habits! (plus 3 gifts for you...) |
| hipwee | 20 Kado untuk Sahabat Perempuan Murah tapi Berkesan |
| hipwee | Wajib Mampir! 9 Kopi Tiam Jogja yang Cocok buat Sarapan |
| hipwee | Kini Giliran Winwin yang Meninggalkan NCT |
| ryan_holiday | 20 Years Ago, I Spent $8 on This. My Life Was Never The Same |

### Drop Reasons

| Reason | Count | Examples |
|--------|-------|---------|
| Excluded keyword | 13 | Kebakaran TPA, Drama Jepang, Tempat Nongkrong |
| Quality (too short) | 1 | Liburan Tanpa Handphone (920 chars) |
| Relatability <3/5 | 20 | CV tips, Gaji Dosen, OOTD Camping |

---

## Relatability Filter

**Threshold:** ≥3/5 to pass

### Passed (✅)

| Score | Title |
|-------|-------|
| 5/5 | — |
| 4/5 | Pesepak Bola Erling Haaland Beli Buku Sejarah |
| 3/5 | Happy 1st Birthday, Atomic Habits! |
| 3/5 | 30 One-Sentence Stories From People Who Have Built Better Habits |
| 3/5 | How to Make Your Future Habits Easy |
| 3/5 | The Ultimate Habit Tracker Guide |
| 3/5 | The Habits Scorecard |

### Failed (❌) — Top Reasons

| Score | Title | Issue |
|-------|-------|-------|
| 2/5 | CV Kamu Cuma Dilirik 6 Detik | Low keyword match |
| 1/5 | Sering Pusing Urus Laporan Pajak | Off-niche |
| 1/5 | 14 OOTD Camping Wanita | Off-niche |
| 1/5 | 20 Kado untuk Sahabat Perempuan | Off-niche |

---

## Top Scored Articles

| Source | Score | Title |
|--------|-------|-------|
| james_clear | 150 | Happy 1st Birthday, Atomic Habits! (plus 3 gifts for you...) |
| detik_edu | 150 | Pesepak Bola Erling Haaland Beli Buku Sejarah Rp 2,4 Miliar |
| james_clear | 110 | How to Make Your Future Habits Easy |
| james_clear | 90 | 30 One-Sentence Stories From People Who Have Built Better Habits |
| james_clear | 85 | The Ultimate Habit Tracker Guide: Why and How to Track Your Habits |
| james_clear | 80 | The Habits Scorecard: Use This Simple Exercise to Discover Your Habits |

---

## Issues Found

1. **High drop rate:** 13 articles dropped by EXCLUDE filter — mostly lifestyle/travel/celebrity content from Hipwee
2. **Low relatability pass rate:** Only 7/32 articles passed relatability filter (22%)
3. **James Clear dominance:** 5/6 top articles from James Clear — other sources underperforming
4. **Engagement fetch failing:** 400 errors on engagement API calls

---

## Recommendations

1. **Tighten EXCLUDE keywords** — add more lifestyle/travel/celebrity patterns to reduce noise
2. **Lower relatability threshold** — consider 2/5 for self-dev niche (currently 3/5)
3. **Diversify sources** — James Clear content in English, may not resonate with Indonesian audience
4. **Fix engagement API** — 400 errors blocking analytics feedback

---

## Next Steps

- [ ] Review EXCLUDE keyword list for false positives
- [ ] Test relatability threshold at 2/5
- [ ] Add more Indonesian self-dev sources
- [ ] Debug engagement API 400 errors
