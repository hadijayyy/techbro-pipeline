# Scoring Keywords — Educator Pipeline (AI/Tech Life Hacks + Personal Finance)

Niche: life hacks, tips & tricks seputar AI/tech dan finance/keuangan.
Filosofi: prioritas ke evergreen "how-to" & practical value, bukan viral news generik.

---

## TIER1 (3x weight on title, 1x on body) — Core Niche, High Value

### AI & Tech Tools (Practical)
- ai, chatgpt, gemini, claude, copilot, ai tools, prompt, prompt engineering
- automation, otomatisasi, no-code, ai productivity, ai agent
- ai untuk kerja, cara pakai ai, ai gratis

### Personal Finance Core
- investasi, saham, reksadana, obligasi, deposito, emas
- nabung, menabung, budgeting, anggaran, dana darurat, emergency fund
- compound interest, bunga majemuk, passive income, cuan
- financial freedom, bebas finansial, financial planning

### Cybersecurity & Digital Safety
- scam, penipuan, phishing, data breach, privacy, keamanan data
- password manager, 2fa, cyber hygiene

### Productivity & Career
- productivity, produktivitas, upskilling, skill, cv, resume
- interview, wawancara kerja, gaji, negosiasi gaji, side hustle
- freelance, remote work, digital nomad

---

## TIER2 (2x weight) — Supporting Context

### Indonesian Economy & Fintech
- startup, pendanaan, umkm, digital, ecommerce, e-commerce
- bank digital, e-wallet, payment, transaksi, cashback, promo, voucher

### Global Tech (relevan ke tools/trend)
- openai, google, microsoft, meta, apple, nvidia
- semiconductor, chip, app, aplikasi, platform

### Career & Workplace Trends
- resign, quiet quitting, kerja remote, wfh, hybrid work
- linkedin, personal branding, portofolio

### Money Mindset & Behavior
- fomo, literasi keuangan, utang, cicilan, kartu kredit, paylater

---

## TIER3 (1x weight) — General Support

- technology, innovation, digital, teknologi, inovasi
- tips, trik, cara, panduan, tutorial, step by step
- rahasia, ternyata, kesalahan umum, mistake

---

## PENALTY (-5 per match) — Off-brand / Low Value for Niche

- unboxing, hands-on, review:, buying guide, gift guide
- coupon, discount, earbuds, earphone, headphone, smartphone review
- battery life test, benchmark score
- zodiak, horoscope, gossip, celebrity

---

## EXCLUDE (auto-reject) — Not Niche At All

- sports score, match schedule, recipe, cooking
- fashion week, beauty tips, weight loss
- pokemon, genshin, wuthering waves, volleyball legends
- mobile legends, free fire, pubg, valorant, fortnite
- roblox, minecraft, gacha, gameplay, let's play
- taylor swift, travis kelce, wedding, married, concert, tour, album
- election, parliament, war, sanctions (kecuali angle "dampak ke ekonomi/pasar")

---

## Scoring Formula

```
score = 0
score += TIER1_matches_in_title × 30
score += TIER1_matches_in_body × 10
score += TIER2_matches_in_title × 15
score += TIER2_matches_in_body × 5
score += TIER3_matches_in_title × 5
score += TIER3_matches_in_body × 2
score -= PENALTY_matches × 5
score -= title_too_long_penalty    # >100 chars = -10
```

Density bonus: if title has 2+ TIER1 keywords → +20 score
Cross-source bonus: if same topic found in 2+ sources → +20 score
Recency bonus: 0h = +30, 12h = +0 (exponential decay)
Hard cap: 150 points max

---

## Sources (10 active)

| Source | Niche | RSS |
|--------|-------|-----|
| Lifehacker | Tech life hacks | https://lifehacker.com/rss |
| TechCrunch | AI/tech news | https://techcrunch.com/feed/ |
| NerdWallet | Personal finance | https://www.nerdwallet.com/blog/feed/ |
| Wired | Tech culture | https://www.wired.com/feed/rss |
| Android Authority | Mobile tech | https://www.androidauthority.com/feed/ |
| Ars Technica | Deep tech | https://feeds.arstechnica.com/arstechnica/index |
| BBC | Global news | https://feeds.bbci.co.uk/news/.../rss.xml |
| Bloomberg Technoz | ID business | https://www.bloombergtechnoz.com/rss |
| CNBC Indonesia | ID tech | https://www.cnbcindonesia.com/tech/rss |
| Detik Inet | ID tech | https://inet.detik.com/rss |

---

## Catatan Strategis

1. **Fokus 3 pilar**: AI/tech tools, personal finance, produktivitas/karier.
2. **Prioritaskan "how-to" framing** — konten yang jawab pertanyaan konkret lebih evergreen.
3. **Hindari drift ke niche gosip/politik/gaming** — EXCLUDE sudah dipasang.
4. **Cybersecurity masuk TIER1** karena overlap kuat sama "tech life hacks".
5. **Money mindset & behavior** (utang, paylater) di TIER2 — pain point besar milenial/gen Z.
