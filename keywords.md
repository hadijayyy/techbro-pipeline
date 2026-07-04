# Scoring Keywords — Techbro Pipeline

## TIER1 (3x weight on title, 1x on body) — 58 keywords

### Entertainment & Celebrity
- taylor swift, travis kelce, celebrity, wedding, marry, married
- netflix, disney, spotify, grammy, oscar, beyonce, drake
- concert, tour, album, movie, film, tv show, reality tv
- backlash, controversy, viral, trending

### AI & Tech
- ai, artificial intelligence, chatgpt, openai, google, meta
- apple, microsoft, nvidia, amazon, tesla, robot, deepfake
- playstation, xbox, nintendo, gta, gaming, esports
- cybersecurity, hack, data breach, privacy, surveillance

### Climate & Environment
- climate, heatwave, temperature, record-breaking, flood
- wildfire, drought, renewable, solar, wind, carbon
- extinction, endangered, pollution

### Science & Space
- nasa, spacex, mars, moon, space, telescope, satellite
- quantum, discovery, breakthrough, research, study
- brain, dna, vaccine, cancer, disease, health

### Politics & Global
- trump, starmer, election, parliament, government, pm
- ukraine, russia, china, iran, war, conflict, sanctions
- referendum, policy, law, ban, regulation

### Crime & Safety
- scam, fraud, smuggler, investigation, police, court
- prison, victims, abuse, safety, warning

---

## TIER2 (2x weight) — 35 keywords

### Indonesian Economy & Tech
- startup, funding, pendanaan, valuasi, ipo, akuisisi
- umkm, digital, transformasi digital, ecommerce, e-commerce

### Global Tech
- semiconductor, chip, gpu, nvidia, apple, google, meta
- microsoft, amazon, tesla, spacex

### Security
- cybersecurity, hack, breach, malware, scam, penipuan, phishing

### Social & Content
- tiktok, instagram, threads, twitter, x, youtube
- influencer, content creator, monetisasi

### Fintech & Finance
- bank, payment, pembayaran, transaksi, saldo
- reward, cashback, promo, voucher

### Karier & Produktivitas
- productivity, produktivitas, skill, upskilling
- resign, interview, wawancara, cv, resume
- gaji, umr, upah

### Geopolitics & Tech
- china, amerika, serikat, perang dagang, sanksi
- impor, ekspor, tarif

---

## TIER3 (1x weight) — 6 keywords

- technology, innovation, digital, platform, app
- teknologi, inovasi, aplikasi

---

## PENALTY (-5 per match) — 12 keywords

- unboxing, hands-on, review:, buying guide
- best of 2026, gift guide, coupon, discount
- earbuds, earphone, headphone, smartphone review
- battery life test, benchmark score

---

## EXCLUDE (auto-reject) — 18 keywords

- zodiak, horoscope, astrology, gossip, celebrity
- sports score, match schedule, recipe, cooking
- fashion week, beauty tips, weight loss
- pokemon, genshin, wuthering waves, volleyball legends
- mobile legends, free fire, pubg, valorant, fortnite
- roblox, minecraft, gacha, gameplay, let's play

---

## Scoring Formula

```
score = 0
score += TIER1_matches_in_title × 15
score += TIER1_matches_in_body × 5
score += TIER2_matches_in_title × 10
score += TIER2_matches_in_body × 3
score += TIER3_matches_in_title × 3
score += TIER3_matches_in_body × 1
score -= PENALTY_matches × 5
score -= title_too_long_penalty    # >100 chars = -10
```

Cross-source bonus: if same topic found in 2+ sources → +20 score
