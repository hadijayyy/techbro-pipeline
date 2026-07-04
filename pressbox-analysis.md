# Analisis: Pressbox vs Techbro — Workflow Viral & Winning Patterns

> **Tujuan:** Pelajari workflow Pressbox (football content, 100K+ views) dan adaptasi ke pipeline Techbro (AI/Tech Life Hacks + Personal Finance).

---

## 1. Pressbox — Kenapa Kontennya Viral?

### Engagement Data (Real Numbers)

| Post | Topic Type | Views | Likes | Comments | Shares |
|------|-----------|-------|-------|----------|--------|
| Olise €223m | Money/price tag | 104K | 1,000 | 50 | 6 |
| England altitude | Physical/extreme | **114K** | 520 | 64 | **26** |
| Croatia VAR | Controversy | 74.9K | 89 | 16 | 2 |

### Viral Formula yang Terbukti

```
[SHOCKING NUMBER/FACT] + [ONE-LINE CONSEQUENCE] = Hook
```

**Winning hooks:**
- "Michael Olise has a €223m price tag on his head."
- "England's next World Cup game is at 7,220ft. The air's so thin."

**Key insight:** Angka spesifik + konsekuensi = engagement. Bukan "gue GILA" atau "tahukah kamu?"

---

## 2. Scoring System — Pressbox vs Techbro

### Pressbox: 12-Component Scoring (Efektif 30–110 pts)

| # | Komponen | Poin | Techbro Equivalent |
|---|----------|------|-------------------|
| 1 | Keyword Match | +8/keyword (max 40) | ✅ Ada (TIER1/2/3) |
| 2 | Category | 20/10/0 | ❌ **Gak ada** |
| 3 | Recency | 15/10/5/0 | ⚠️ Ada tapi sederhana |
| 4 | Data/Konkret | 15/7/0 | ❌ **Gak ada** |
| 5 | Source Tier | 10/5/0 | ⚠️ Ada tapi flat |
| 6 | Audience Reach | +10/big name (max 40) | ❌ **Gak ada** |
| 7 | Drama Signal | +5/word (max 15) | ❌ **Gak ada** |
| 8 | First Ever | +20/+10 | ❌ **Gak ada** |
| 9 | Niche Nation | -15 | ⚠️ Ada (PENALTY) |
| 10 | Paradox Bonus | +12 | ❌ **Gak ada** |
| 11 | Warning Bonus | +8 | ❌ **Gak ada** |
| 12 | Hot Topic | +25/+15 | ❌ **Gak ada** |
| 13 | Peak-Hour | +10 | ❌ **Gak ada** |
| 14 | Niche Topic | -30 | ❌ **Gak ada** |
| 15 | wc_related | +40/+10 | ❌ **Gak ada** |
| — | Soft Cap | 100 + (score-100)*0.3 | ❌ **Gak ada** |
| — | Auto-Tuning | ±15 keyword, ±10 audience | ❌ **Gak ada** |

**Gap besar:** Techbro cuma punya keyword matching. Pressbox punya 15 komponen scoring + soft cap + auto-tuning.

### Rekomendasi: Tambah Komponen Scoring ke Techbro

| Komponen | Implementasi | Impact |
|----------|-------------|--------|
| **Data/Konkret** | Cek angka di judul/body: +15 ada angka, +7 ada data, 0 | Tinggi — konten dengan angka 2x lebih engaging |
| **Audience Reach** | Nama brand/personaliti besar: +10 (max 40) | Tinggi — "ChatGPT", "Elon Musk", "Apple" = instant reach |
| **Drama Signal** | Kata kontroversi: +5/kata (max 15) | Sedang — "banned", "denda", "rugi", "gagal" |
| **Paradox Bonus** | Kontradiksi: +12 | Sedang — "gratis tapi bahaya", "murah tapi powerful" |
| **Hot Topic** | 3+ source bahas topik sama: +25 | Tinggi — trending detection |
| **Soft Cap** | Atas 100: `100 + (score-100)*0.3` | Sedang — prevent score runaway |

---

## 3. Content Quality Filtering — Defense-in-Depth

### Pressbox: 3-Layer Hardcoded Filters

| Layer | Threshold | Purpose |
|-------|-----------|---------|
| 1. Char count | < 1000 chars | Thin content |
| 2. Word count | < 150 words | Boilerplate |
| 3. Sentence count | < 8 sentences | No real substance |

**Techbro sekarang:** Hanya `fast_content_filter()` (body < 500 chars = skip). Tidak ada word count atau sentence count check.

**Rekomendasi:** Tambah 3-layer filter:
```python
def article_quality_filter(body: str) -> bool:
    if len(body.strip()) < 800: return False  # Too short
    if len(body.split()) < 120: return False   # Too few words
    sentences = [s for s in re.split(r'[.!?]+', body) if len(s.strip()) > 15]
    if len(sentences) < 6: return False        # Too few sentences
    return True
```

---

## 4. Hook Quality — Techbro's Biggest Weakness

### Pressbox Hook Patterns (Proven)

| Pattern | Contoh | Views |
|---------|--------|-------|
| **Specific Detail + Emotional Weight** | "Olise has a €223m price tag" | 104K |
| **Surprising Fact + Consequence** | "England at 7,220ft — players will gas out" | 114K |
| **Controversy + Stakes** | "VAR ruled it out for a *possible* flick-on" | 74.9K |

### Techbro Hook Patterns (Sekarang)

| Pattern | Contoh | Problem |
|---------|--------|---------|
| "Gue [emotion]" | "Gue GILA, China mau revisi..." | ❌ Meaningless filler |
| "Lo tau gak..." | "Lo tau gak... server AI Meta malah PAKE DDR4?" | ⚠️ OK tapi generic |
| Angka fabricated | "Ternyata 1,3 JUTA seller..." | ❌ Grounding violation |

### Rekomendasi: Hook Formula Baru

```
[ANGKA/FAKTA DARI ARTIKEL] + [KONSEKUENSI YANG RELEVAN]
```

**Contoh:**
- ✅ "ChatGPT bisa bikin CV lo dalam 5 menit. Gak perlu jago desain."
- ✅ "Harga DDR5 naik 40%, tapi Meta malah pake DDR4 lawas di server AI."
- ❌ "Gue GILA, teknologi baru bikin lo kaget!"
- ❌ "Tahukah kamu? AI bisa mengubah hidup lo!"

**Implementasi:** Update `_rewrite_hook()` prompt untuk:
1. WAJIB mulai dari fakta/angka di artikel
2. DILARANG mulai dari emosi ("gue gila", "gue kaget")
3. Format: `[FAKTA] + [KONSEKUENSI]` atau `[FAKTA] + [PERTANYAAN]`

---

## 5. Viral Criteria — 7 Kriteria yang Pressbox Pakai

Pressbox punya 7 viral criteria di prompt LLM:

| # | Kriteria | Contoh |
|---|----------|--------|
| 1 | **Pro & Con** | "Ini bagus karena... tapi risikonya..." |
| 2 | **Relatable** | "Lo yang jualan di Shopee pasti ngalamin..." |
| 3 | **Famous Figure** | "Elon Musk bilang...", "Meta lakuin..." |
| 4 | **Viral/Trending** | "Lagi rame dibahas...", "Baru aja kejadian..." |
| 5 | **Comedy/Irony** | "DDR4 yang lo anggap sampah ternyata..." |
| 6 | **Surprising Fact** | "Ternyata server AI pake RAM lawas..." |
| 7 | **Emotional Hook** | "Lo yang jualan kecil-kecilan, gimana..." |

**Techbro sekarang:** Tidak ada viral criteria di prompt. Hanya HOW-TO framework.

**Rekomendasi:** Tambah "Viral Criteria" section ke PROMPT_ID:
- Tiap slide WAJIB hit ≥1 kriteria
- Slide 1: WAJIB surprising fact atau famous figure
- Slide 5-6: WAJIB relatable atau emotional hook

---

## 6. Content Structure — Slide 5 = "Take" (Opini Grounded)

### Pressbox: Slide 5 = Take (Grounded Opinion)

Bukan "hasil" atau "manfaat" — tapi **opini yang grounded di fakta artikel**.

**Contoh:**
- "Meta buktiin: kadang solusi paling murah itu yang udah ada di depan mata."
- "Ini bukan cuma soal RAM — ini soal mindset: buang atau daur ulang?"

**Techbro sekarang:** Slide 5 = "THE RESULT (Hasil/Manfaat Nyata)". Terlalu deskriptif, kurang engaging.

**Rekomendasi:** Ubah Slide 5 dari "Result" ke "Take":
- Slide 5 = opini/perspektif yang grounded di artikel
- Format: "Menurut gue, [opini] karena [fakta dari artikel]"
- Harus bikin orang COMMENT (setuju/tidak setuju)

---

## 7. Caption Formula — Shock-First

### Pressbox Caption (Proven)

```
Line 1: THE shocking number/fact
Line 2: Consequence
Line 3: (optional) Context
```

**Zero hashtags.** Zero emoji.

**Techbro sekarang:** Caption = "1 kalimat ringkas & provokatif". Terlalu vague.

**Rekomendasi:** Caption format:
```
[ANGKA/FAKTA SHOCKING] + [KONSEKUENSI].
[PERTANYAAN PROVOKATIF]?
```

Contoh:
- "Meta bisa potong 25% server AI cuma dengan daur ulang RAM DDR4. Lo masih buang hardware lama?"
- "China mau awasi SEMUA penjual online, bahkan yang jualan di grup WA. Lo siap?"

---

## 8. Auto-Tuning & Analytics Feedback Loop

### Pressbox: One-Way Analytics Feedback

```
Pipeline run:
  1. pull_engagement() → update metrics untuk post >12h
  2. get_analytics_summary() → classify hooks/topics, compute boosts
  3. Scoring + hook/topic boosts applied
  4. Post new content
```

**Techbro sekarang:** Tidak ada analytics feedback. Scoring statis.

**Rekomendasi (Future):**
1. Track engagement per post (views, likes, comments)
2. Analisis topik/hook mana yang perform
3. Auto-adjust scoring weights berdasarkan data
4. Penalize topik yang consistently underperform

---

## 9. Grounding & Anti-Hallucination

### Pressbox: 5 Grounding Rules (ALL Slides)

1. Jangan invent tactical reasoning
2. Jangan exaggerate paraphrasing
3. Jangan speculate consequences
4. Quote = verbatim
5. Level kepastian = artikel asli

### Techbro: Grounding di §1 PROMPT_ID

**Sudah bagus** tapi enforcement lemah. Grounding check cuma warning, gak block.

**Rekomendasi:** Strengthen grounding enforcement:
- Setelah generate, cek tiap klaim ada di artikel
- Kalau angka tidak ada di artikel → auto-strip (sudah ada)
- Kalau klaim fabricated → auto-strip atau regenerate slide

---

## 10. Summary: Prioritas Implementasi

### High Impact (Implementasi Sekarang)

| # | Perubahan | Effort | Impact |
|---|----------|--------|--------|
| 1 | **Hook formula**: `[FAKTA] + [KONSEKUENSI]` | Low | 🔥 Tinggi |
| 2 | **Viral criteria** di prompt (7 kriteria) | Low | 🔥 Tinggi |
| 3 | **Slide 5 = Take** (opini grounded) | Low | 🔥 Tinggi |
| 4 | **Caption shock-first** format | Low | 🔥 Tinggi |
| 5 | **3-layer quality filter** | Low | Sedang |

### Medium Impact (Next Sprint)

| # | Perubahan | Effort | Impact |
|---|----------|--------|--------|
| 6 | **Data/Konkret scoring** (+15 ada angka) | Sedang | Tinggi |
| 7 | **Audience Reach scoring** (+10 big name) | Sedang | Tinggi |
| 8 | **Hot Topic detection** (3+ source) | Sedang | Tinggi |
| 9 | **Soft cap** (atas 100: diminishing returns) | Low | Sedang |
| 10 | **Drama Signal scoring** (+5/kata kontroversi) | Low | Sedang |

### Low Priority (Future)

| # | Perubahan | Effort | Impact |
|---|----------|--------|--------|
| 11 | Analytics feedback loop | Tinggi | Tinggi |
| 12 | Auto-tuning scoring weights | Tinggi | Sedang |
| 13 | Peak-hour boost | Low | Rendah |
| 14 | Evaluator LLM (independent review) | Sedang | Sedang |

---

## 11. Key Takeaways

### What Pressbox Does RIGHT That Techbro Doesn't

1. **Scoring granularity** — 15 components vs 1 (keyword only)
2. **Hook quality** — data-driven, not emotion-driven
3. **Viral criteria** — explicit checklist for LLM
4. **Grounding enforcement** — all slides, not just warnings
5. **Analytics feedback** — self-improving over time
6. **Content quality gates** — 3-layer defense
7. **Caption formula** — shock-first, zero hashtags

### What Techbro Does RIGHT That Pressbox Doesn't

1. **HOW-TO framework** — actionable content (Pressbox is news/drama)
2. **Indonesian market** — localized for target audience
3. **Finance + Tech niche** — evergreen topics (Pressbox is sports news)
4. **Whitespace enforcement** — `\n\n` between sentences
5. **Banned patterns** — comprehensive list

### The Gap

Techbro's **content generation** is solid (HOW-TO framework, grounding, whitespace). But **content selection** (scoring) and **hook quality** are weak. Pressbox's 15-component scoring + viral criteria + hook formula can 2-3x engagement.

---

*Generated: 2026-07-04 15:xx WIB*
*Source: pressbox-cheatsheet skill + winning-carousel-analysis-20260704.md*
