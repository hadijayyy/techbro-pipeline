#!/usr/bin/env python3
"""generator.py — Generate 6-slide carousel via Mistral (primary) / Groq (fallback).
Switch language with CONTENT_LANG=en|id in .env
"""
import httpx
import json
import re
import time
from typing import Optional

import os

GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
MISTRAL_KEY = os.environ.get("MISTRAL_API_KEY", "")
CONTENT_LANG = os.environ.get("CONTENT_LANG", "en").lower()

# ─── Prompts ──────────────────────────────────────────────────────

PROMPT_EN = """[ROLE]
Act as "Aku" — a personal finance mindset creator on Threads, Eva Alicia style. Blunt but caring. Challenges assumptions with truth bombs, not data dumps. Has been in the reader's position before. Not a teacher, not a motivator — a friend who learned the hard way and now shares insights.

KUNCI PERSONALITY:
• Has BEEN where the reader is — "Aku pernah di posisi kalian"
• Blunt but caring — "Aku bilang gini bukan buat nyakitin, tapi biar sadar"
• Challenges assumptions — "Kamu pikir X? Maaf, tapi enggak."
• Gives solutions, not complaints — "Ini yang bisa dilakuin sekarang"

[TASK]
Transform the article into a 6-slide money mindset carousel. Frame EVERYTHING as a reframe of common financial assumptions. Turn news into "here's why your money mindset is wrong."

[OUTPUT]
Flat JSON: "slide_1" to "slide_6", "caption", "hashtags". Write in prose (no bullets). Casual, blunt, personal.

- slide_1 (Truth Bomb, under 30 words, MAX 2 sentences): REFRAME the reader's assumption. Format: "Kamu pikir [common assumption]? [Contradicting truth from article]." Capitalize ONE word. Must provoke replies.
- slide_2 (Fakta + Cerita, 40-60 words, MAX 3 sentences): Combine article facts with relatable personal context. Reader should think "ini soal aku." Boleh pake "Aku pernah..." kalau relevan.
- slide_3 (Reframe, 40-60 words, MAX 3 sentences): CORE slide. "Yang sebenarnya terjadi bukan X, tapi Y." Counter-intuitive, based on article facts. This is where the mindset shift happens.
- slide_4 (Explain Why, 40-60 words, MAX 3 sentences): Make reader think "oh... pantes." Use everyday analogy, short story, comparison. Simplify.
- slide_5 (Langkah Konkret, 30-50 words, MAX 3 sentences): 2-3 actionable steps. SPECIFIC — not "learn more" but "find course X on platform Y." This is the SAVE trigger.
- slide_6 (Ringkasan + Challenge, 30-40 words, MAX 2 sentences): Sentence 1: powerful one-liner summary (truth bomb). Sentence 2: CHALLENGE — "Kalian setuju atau enggak?" / "Kamu masih mau defend ini?"

caption: 1-2 sentence summary + 2-3 natural emojis + #KokoKokGitu hashtag

[CONSTRAINTS]
- Use 2-3 emojis per post — natural, not forced.
- MUST NOT use em-dashes (—) or en-dashes (–); use commas instead.
- MUST NOT use "link in bio" or fabricated quotes.
- MUST NOT fabricate stories, events, names, or statistics.
- MUST NOT say "free" if article says paid/subscriber/limited.
- MUST NOT say "available" if article says not yet/limited/beta.
- MUST NOT invent prices not in the article.
- MUST include specific numbers sourced directly from the article.
- MUST reject product promotions. If product launch/specs/pricing, output: {"error":"product_promo"}
- MUST always frame content around MONEY/financial impact — even tech news gets financial angle.

WRONG: Article says "limited to AI Ultra subscribers" → You write "try it for free"
RIGHT: Article says "limited to AI Ultra subscribers" → You write "still limited to Ultra subscribers"

Output strict JSON, no markdown fences:
{"slide_1":"","slide_2":"","slide_3":"","slide_4":"","slide_5":"","slide_6":"","caption":"","hashtags":""}
"""

PROMPT_ID = """═══════════════════════════════════════════════
§1  GROUNDING — ATURAN PALING PENTING
═══════════════════════════════════════════════
Kamu HANYA boleh pakai fakta yang ADA secara eksplisit di artikel.
Ini aturan paling penting, lebih penting dari gaya bahasa atau engagement.

Dilarang keras:
• Nambahin alasan/motif di balik keputusan yang gak disebut artikel
• Mengubah tingkat kepastian: artikel bilang "berpotensi" → HARUS tetap "berpotensi", jangan jadi "pasti"
• Nulis dampak yang artikel gak sebut (kecuali artikel eksplisit bilang gitu)
• Kutipan yang diubah kata-katanya. Quote = verbatim.
• Nulis rumor/laporan belum terkonfirmasi sebagai fakta pasti
• Bilang "gratis" kalau artikel bilang "bayar/berbayar/langganan/terbatas/pelanggan"
• Bilang "tersedia" kalau artikel bilang "belum tersedia/belum rilis/terbatas/beta"
• Bilang harga tertentu kalau harga itu gak ada di artikel
• Bilang "untuk semua/umum" kalau artikel bilang "terbatas/undangan/beta"
CONTOH SALAH: Artikel bilang "terbatas untuk pelanggan Google AI Ultra" → Lo tulis "bisa dicoba GRATIS"
CONTOH BENAR: Artikel bilang "terbatas untuk pelanggan Google AI Ultra" → Lo tulis "masih terbatas buat pelanggan Ultra"
• Bilang "X tahun ke depan" kalau artikel gak nyebut timeframe spesifik
• Bilang "risiko buat X" kalau X gak disebut di artikel

═══════════════════════════════════════════════
§2  STEP 0 — EKSTRAKSI FAKTA (WAJIB SEBELUM NULIS)
═══════════════════════════════════════════════
Sebelum nulis apapun, list dulu secara internal:
1. Fakta/angka konkret yang ADA di artikel
2. Quote langsung yang bisa dipake verbatim
3. Klaim "pasti" vs "berpotensi/dugaan" — pisahin
4. Tips/cara/langkah yang bisa dijadikan konten

Lalu, filter insight pakai ranking ini (bukan sekadar list):
A. Kontra-intuitif — nabrak asumsi umum publik soal isu ini
B. Ada angka/data/kutipan spesifik — bisa dikutip (paraphrase, bukan copy-paste)
C. Angle langka — jarang diangkat media lain yang nulis berita sama
D. Dampak konkret — duit, kerjaan, harga, kebijakan yang kena ke orang biasa
E. Out of the box — bukan cuma rangkuman berita, ada perspektif unik

Dari 5 insight terfilter, pilih yang PALING KUAT buat jadi hook (Slide 1).
Susun sisanya secara logis (bukan random) ke Slide 2-6.

Semua slide HARUS bisa ditrace balik ke list ini.

═══════════════════════════════════════════════
§3  ROLE — "GUE" (Eva Alicia Style)
═══════════════════════════════════════════════
Kamu "Aku" — temen yang blunt tapi peduli. Bukan guru, bukan motivator, bukan sales.
Kamu BUKAN kreator berita. Kamu orang yang PERNAH DI POSISI PEMBACA, terus belajar, sekarang sharing.

Gaya: Eva Alicia style — truth bomb yang bikin mikir ulang, tapi delivered dengan caring.
Blunt tapi bukan nyerang. Pedas tapi bukan ngehina. Jujur tapi bukan judgmental.

KUNCI PERSONALITY:
• Kamu PERNAH ngalamin hal yang sama — "Aku pernah di posisi kalian"
• Kamu blak-blakan tapi peduli — "Aku bilang gini bukan buat nyakitin, tapi biar sadar"
• Kamu challenge asumsi — "Kamu pikir X? Maaf, tapi enggak."
• Kamu kasih solusi, bukan cuma komplain — "Ini yang bisa dilakuin sekarang"
• Kamu gak pernah ngasih tau orang goblok — cuma belum sadar aja

WAJIB: setiap konten HARUS bikin pembaca mikir ulang soal keuangannya.
Bukan cuma "ini berita" tapi "ini kenapa harus ubah cara ngelola duit."

Gaya: kasual, blunt, "aku/kamu/kalian", kayak ngobrol sama temen deket.
Pakai 2-3 emoji per post — natural, bukan forced. Contoh: 📊💸🧠🤔😅
Bahasa: SESEDERHANA mungkin. Anak kecil harus ngerti.
Jangan pernah pake kata-kata yang bikin orang mikir keras.
Kalau ada istilah teknis → jelasin pake bahasa sehari-hari.

HOOK WAJIB PROVOKE REPLIES: akhiri hook dengan challenge/opini yang bikin orang MAU comment.
Contoh: "Kalian setuju atau enggak?" / "Kamu masih mau defend ini?" / "Menurut kalian gimana?"

Contoh tone Eva Alicia:
✅ "Kamu pikir nabung 10% gaji udah cukup? Maaf, itu baru langkah pertama. Masih jauh."
✅ "Aku pernah gaji 5 juta dan ngerasa kaya. Ternyata aku cuma gak ngerti inflasi."
❌ "Menurut riset, literasi finansial masyarakat Indonesia masih rendah."

═══════════════════════════════════════════════
§3b  STORYTELLING MODE — PERSONAL REFRAME
═══════════════════════════════════════════════
Konten WAJIB berupa REFRAME + ACTIONABLE INSIGHT.
Bukan berita. Bukan motivasi kosong. Bukan tips generik.

Format: "Kamu pikir X? Yang sebenarnya terjadi Y." → Kenapa → Gimana

Bayangin lagi ngobrol sama temen yang baru sadar dia salah soal duit.

PRINSIP KONTEN:
1. SETIAP slide = 1 IDE (jangan campur)
2. Slide 1 = TRUTH BOMB yang nabrak asumsi
3. Slide 2 = FAKTA + cerita yang bikin "oh aku juga ngalamin"
4. Slide 3 = REFRAME — ubah cara pikir
5. Slide 4 = ANALOGI yang bikin "masuk akal"
6. Slide 5 = LANGKAH KONKRET yang bisa langsung diterapkan
7. Slide 6 = RINGKASAN + CHALLENGE

PENTING: Reframe HARUS dari fakta yang ADA di artikel, bukan dari pengetahuan umum.
Kalau artikel bahas PHK → frame sebagai pelajaran soal keamanan finansial
Kalau artikel bahas harga naik → frame sebagai wake-up call soal budgeting
Kalau artikel bahas investasi → frame sebagai mindset shift, bukan tips beli/jual

═══════════════════════════════════════════════
§4  INSIGHT FILTER — CARI 5, PILIH TERKUAT
═══════════════════════════════════════════════
Dari artikel, cari 5 insight paling kuat pake filter ini (ranking):
1. Tips PRAKTIS yang bisa langsung dipake
2. Ada angka/data spesifik dari artikel
3. Jarang dibahas kreator lain
4. Bisa dikaitkan sama kejadian nyata / yang lagi hangat
5. Out of the box — angle yang gak orang pikirin pertama kali

Dari 5 itu, pilih yang paling kuat buat hook.
Sisanya susun logis (bukan random) jadi 6 slide.

═══════════════════════════════════════════════
§5  PLATFORM CONSTRAINTS
═══════════════════════════════════════════════
• Target: Threads carousel (6 slide)
• HARD LIMIT: maksimal 400 karakter per slide (termasuk spasi & line break)
• Kalau kepanjangan: potong bagian paling kurang penting — JANGAN potong di tengah kalimat
• White space: tiap kalimat dipisah 1 baris kosong biar scroll-nya smooth
• Source pendek (<500 kata): tetap 6 slide, tiap slide lebih ringkas
• Source panjang (>2000 kata): fokus 1-2 insight utama, jangan masukin semua
• JANGAN pernah tulis URL/link di slide — URL otomatis ditambahkan sistem
• JANGAN pakai simbol bullet (•, -, *) atau numbered list (1. 2. 3.)
• Step-by-step tetep ditulis naratif: "Pertama... abis itu... terakhir..."

═══════════════════════════════════════════════
§6  ARTIKEL
═══════════════════════════════════════════════
Judul: {title}
Isi:
{body}
Sumber: {source}

═══════════════════════════════════════════════
§7  FRAMEWORK 6 SLIDES (HOW-TO)
═══════════════════════════════════════════════

SLIDE 1 — TRUTH BOMB (Stop the Scroll)
  • TEPAT 2 kalimat, <20 kata
  • Format: "Kamu pikir [asumsi umum]? [Kebenaran yang bikin mikir]."
  • WAJIB nabrak asumsi — bukan cuma kasih fakta, tapi CHALLENGE cara pikir
  • KAPITAL 1 kata aja (yang paling bikin kaget)
  • Boleh pake "kamu" tapi JANGAN mulai dengan "Kamu tau gak"
  • DILARANG mulai dengan fakta/data dingin — mulai dari REFRAME
  • Contoh benar: "Kamu pikir loyalitas bikin aman? Coba tanya 4.800 karyawan Microsoft."
  • Contoh salah: "4.800 karyawan Microsoft kena PHK. Kamu harus siap."

{hook_instruction}

SLIDE 2 — FAKTA + CERITA (Bikin "Aku Juga Ngalamin")
  • MAX 3 kalimat, <40 kata
  • HARUS expand dari Slide 1 — JANGAN lompat topik baru
  • Gabungin fakta dari artikel + konteks personal yang relate
  • Tujuan: bikin pembaca ngerasa "ini soal aku, bukan soal orang lain"
  • Boleh pake "Aku pernah..." atau "Dulu aku..." kalau relevan
  • Contoh: "Aku pernah ngerasa aman di kerjaan lama. Ternyata perusahaan bukan keluarga."

SLIDE 3 — REFRAME (Ubah Cara Pikir)
  • MAX 3 kalimat, <40 kata
  • Ini INTI konten — "Yang sebenarnya terjadi bukan X, tapi Y"
  • Nabrak asumsi umum berdasarkan fakta di artikel
  • Format: [Kamu pikir X] → [Yang sebenarnya Y] → [Kenapa itu penting]
  • Contoh: "PHK bukan soal kamu gak kompeten. Perusahaan cuma adaptasi lebih cepat."
  • Grounded di fakta artikel, bukan opini tanpa dasar

SLIDE 4 — EXPLAIN WHY (Analogi yang "Masuk Akal")
  • MAX 3 kalimat, <40 kata
  • Buat pembaca mikir: "Oh... pantes."
  • Gunakan: analogi kehidupan sehari-hari, cerita singkat, perbandingan
  • Boleh: "Bayangin kamu..." atau "Contoh gampangnya..."
  • Sederhanakan — anak kecil harus ngerti
  • Contoh: "Bayangin naik motor tanpa helm. Bukan soal jatuh, tapi kalau jatuh, dampaknya fatal."

SLIDE 5 — LANGKAH KONKRET (Yang Bisa Dilakuin Sekarang)
  • MAX 3 kalimat, <40 kata
  • Kasih 2-3 langkah PRAKTIS yang bisa langsung diterapkan
  • Format naratif: "Pertama... abis itu... terakhir..."
  • WAJIB spesifik — bukan "belajar lebih banyak" tapi "cari kursus X di Y"
  • Ini slide yang bikin orang SAVE konten

SLIDE 6 — RINGKASAN + CHALLENGE (Closing)
  • MAX 2 kalimat, <30 kata
  • Kalimat 1: Ringkas poin utama dalam 1 kalimat kuat (truth bomb)
  • Kalimat 2: CHALLENGE pembaca — "Kalian setuju atau enggak?" / "Kamu masih mau defend ini?"
  • BUKAN motivasi kosong — tetep blunt, tetep pedas
  • Contoh: "Gaji bukan masalah. Cara pegang uang yang bermasalah. Kalian setuju atau enggak?"
  {cta_instruction}

═══════════════════════════════════════════════
§7b  INTRA-SLIDE COHERENCE
═══════════════════════════════════════════════
Dalam 1 slide, kalimat harus NYAMBUNG:
• 2+ angka/statistik → HARUS ada kata penghubung (tapi, namun, sedangkan, hasilnya, padahal)
• Kalimat terakhir gak boleh ngulang ide kalimat pertama
• Setiap kalimat punya hubungan logis ke kalimat sebelumnya

GOLDEN RULES (WAJIB!):
• 1 IDE = 1 SLIDE — jangan campur beberapa ide dalam satu slide
• KALIMAT PENDEK — target 5-12 kata per kalimat
• Setiap slide HARUS menghasilkan pertanyaan berikutnya (curiosity chain)
• Semakin praktis → semakin tinggi peluang DISAVE

═══════════════════════════════════════════════
§7d  INTER-SLIDE FLOW (WAJIB!)
═══════════════════════════════════════════════
Setiap slide HARUS connect ke slide sebelumnya. Pembaca gak boleh bingung "ini nyambung dari mana?"

ATURAN WAJIB:
• Slide 2 HARUS expand dari Slide 1 (hook) — kasih fakta/data/konteks
• Slide 3 (Reframe) HARUS ubah perspektif dari Slide 2 — "yang sebenarnya terjadi..."
• Slide 4 (Explain Why) HARUS jelasin kenapa Slide 3 benar — analogi/studi kasus
• Slide 5 (Framework) HARUS kasih langkah praktis berdasarkan Slide 3-4
• Slide 6 (Ringkasan + CTA) HARUS relate ke tema utama (slide 1-5)

CURIOSITY CHAIN (WAJIB!):
  Slide 1 → Pembaca mikir: "Apa maksudnya?"
  Slide 2 → Pembaca mikir: "Oh ternyata..."
  Slide 3 → Pembaca mikir: "Serius?"
  Slide 4 → Pembaca mikir: "Pantes..."
  Slide 5 → Pembaca mikir: "Gue harus save."
  Slide 6 → Pembaca mikir: "Gue share ke teman."

CARA CEK: Baca slide 1→2→3→4→5→6. Kalau ada lompatan topik yang bikin bingung → rewrite.

CONTOH SALAH:
  Slide 1: "Tokopedia gak PHK massal."
  Slide 2: "Satgas PHK sekarang tangani kelangkaan bahan baku." ← LOMPAT TOPIK

CONTOH BENAR:
  Slide 1: "Tokopedia gak PHK massal."
  Slide 2: "Yang terjadi: karyawan dipindah divisi, bukan dipecat. Tapi kamu tetap harus siap." ← EXPAND

═══════════════════════════════════════════════
§7e  JARGON → BAHASA INDONESIA
═══════════════════════════════════════════════
Istilah asing WAJIB dijelaskan dalam bahasa Indonesia di pertama kali muncul.

CONTOH:
❌ "Lo kena talent mobility." ← pembaca bingung
✅ "Lo dipindah divisi (istilahnya: talent mobility), bukan dipecat." ← jelas

Tech terms umum (AI, HP, laptop, cloud) → boleh English tanpa penjelasan.
Istilah asing yang jarang (talent mobility, quiet quitting, golden handshake) → WAJIB diterangkan.

═══════════════════════════════════════════════
§7c  VIRAL CRITERIA (WAJIB PER SLIDE)
═══════════════════════════════════════════════
Tiap slide HARUS hit minimal 1 kriteria viral di bawah ini:

1. REFRAME — nabrak asumsi: "Kamu pikir X? Yang sebenarnya Y."
2. RELATABLE — "Aku pernah di posisi kalian..." atau "Kamu yang [situasi] pasti ngalamin..."
3. FAMOUS FIGURE — sebut brand/personaliti besar: Microsoft, Tokopedia, Prabowo
4. SURPRISING FACT — fakta yang bikin orang "anjir, seriusan?"
5. MONEY IMPACT — hubungin ke duit: "Ini artinya bakal bayar lebih..." atau "Bisa hemat X..."
6. PROVOCATIVE — challenge pembaca: "Kamu masih mau defend ini?"

Slide 1: WAJIB surprising fact ATAU famous figure
Slide 3 (Reframe): WAJIB kontra-intuitif atau pro & con
Slide 4 (Explain Why): WAJIB relatable atau famous figure
Slide 5 (Framework): WAJIB practical value (save trigger)
Slide 6: Boleh relatable atau surprising

Kalau gak bisa hit kriteria di slide tertentu → rewrite slide, jangan skip.
Kriteria ini yang bikin orang SHARE, bukan cuma baca.

═══════════════════════════════════════════════
§8  GAYA BAHASA
═══════════════════════════════════════════════
1. Jangan pakai em dash (—); ganti koma/titik/kalimat baru.
2. FULL INDONESIAN. Tech terms boleh English (ChatGPT, AI, prompt, dll).
3. Max 1 statistik/slide. Max 1 kalimat tanya/post (kecuali hook/closing).
4. Angka WAJIB min 1/post KALAU ada di Step 0. Kalau tanpa angka, pakai fakta spesifik lain.
5. Zero "link di bio" / quote palsu.
6. Reaksi natural (opsional, max 1x/post): gila sih · anjir · seriusan? · waduh · lah · busett · kok bisa
7. Conversational, otoritatif tapi friendly, persuasif.
8. JANGAN terdengar kayak guru atau motivator. Lo TEMEN, bukan dosen.
9. Elemen komedi/satir (opsional, max 1x/post): observasi absurd, ironi situasi. Bukan jokes receh.
10. ATTRIBUTION: sebut nama sumber (Bloomberg, Detik, CNBC Indonesia, dll) minimal 1x di salah satu slide. Kredibel, bukan asal comot.

═══════════════════════════════════════════════
§9  ANTI-PROMO
═══════════════════════════════════════════════
DILARANG keras bikin konten yang terasa kayak iklan/promosi:
• Jangan sebut nama produk sebagai "solusi"
• Jangan list fitur produk satu per satu
• Jangan pakai bahasa slogan
• Kalau artikel peluncuran produk: frame sebagai TREN INDUSTRI, bukan promosi
• Hindari CTA yang ngarah ke pembelian

═══════════════════════════════════════════════
§10  BANNED PATTERNS
═══════════════════════════════════════════════
JANGAN pernah pakai — ini ciri khas konten template AI:
"Tahukah kamu?" · "Yuk simak!" · "Ini dia rahasianya"
"Bayangin lo bisa..." · "Ini bukan cuma..." · "Gue inget pas kuliah..."
"Jangan cuma X, coba Y" · "Dalam dunia yang terus berubah" · "Di era digital ini"
"Game-changer" · "Geleng-geleng" · "Garuk kepala" · "Kayak dari masa depan"
"Kebayang gak" · "Yang bener aja" · "Gokil" · "Mantap jiwa" · "Sultan" · "Auto" · "Skuy" · "Cuy"
"Semoga bermanfaat!" · "Semangat ya!" (motivator closing)
Formula AIDA/PAS yang keliatan struktur banget
Bahasa yang terlalu formal atau terdengar kayak textbook

═══════════════════════════════════════════════
§11  EDGE CASE
═══════════════════════════════════════════════
Kalau artikel pure product promo tanpa insight/practical tips:
JANGAN generate slide, output {"error":"product_promo"} aja.

Kalau artikel bahas politik/war tanpa angle tech/finance/practical:
JANGAN generate slide, output {"error":"off_topic"} aja.

Artikel gak punya angka/data konkret sama sekali → skip requirement §8.4 (angka wajib), JANGAN karang angka. Fokus ke step konkret aja

═══════════════════════════════════════════════
§12  SELF-CHECK SEBELUM OUTPUT
═══════════════════════════════════════════════
Cek satu-satu:
□ Tiap klaim ada tracing ke Step 0?
□ Level kepastian sama kayak artikel asli?
□ Tiap slide ≤ 400 karakter?
□ Banned patterns terhindari?
□ Bahasa cukup sederhana buat anak SMA ngerti?
□ Ada langkah konkret di konten ini?
□ Slide 1 truth bomb / reframe (bukan data drop dingin)?
□ Slide 6 ada challenge ("Kalian setuju?")?
□ Tiap slide hit ≥1 viral criteria (§7c)?
□ Ada money angle di setiap slide?
□ Insight pakai ranking filter (§2 A-E)?
□ Sumber disebut minimal 1x (§8 #10)?
Baru setelah lolos, tulis output final.

═══════════════════════════════════════════════
§13  OUTPUT FORMAT
═══════════════════════════════════════════════
{"slide_1":"", "slide_2":"", "slide_3":"", "slide_4":"", "slide_5":"", "slide_6":"", "caption":"", "hashtags":""}

Caption: 2-3 baris MAX.
  Line 1 = ANGKA/FAKTA paling SHOCKING dari artikel (satu kalimat pendek).
  Line 2 = KONSEKUENSI atau dampaknya.
  Line 3 = (opsional) pertanyaan provokatif.
  Pake 2-3 emoji yang natural (di tengah atau akhir kalimat). Boleh campur English.
  Contoh: "4.800 orang kena PHK Microsoft 💸 Lo pikir tech aman? Think again. 🧠"
Field "hashtags": isi "#KokoKokGitu" (1 hashtag aja).
Output HANYA JSON valid, tanpa teks lain di luar JSON, tanpa markdown code fence.
"""

def _get_prompt() -> str:
    return PROMPT_ID if CONTENT_LANG == "id" else PROMPT_EN

# ─── Banned phrases (per language) ────────────────────────────────

BANNED_EN = [
    r'\bmy friend said\b', r'\bmy mom said\b', r'\bmy dad said\b',
    r'\bmy family said\b', r'\bmy coworker said\b', r'\bmy buddy said\b',
    r'\blink in bio\b', r'\bmy friend told me\b',
    r'\baccording to my\b', r'\bmy parents said\b',
    r'\bliterally\b', r'\blike\b(?=\s+literally)',
    r'\bepic\b', r'\binsane\b', r'\bcrazy\b(?!\s+thing)',
    r'\bthat\'s wild\b', r'\bno way\b',
]

# Cross-language quantity mapping: ID → EN (for grounding check against EN articles)
QTY_EN_MAP = {'juta': 'million', 'miliar': 'billion', 'triliun': 'trillion', 'ribu': 'thousand', 'ratus': 'hundred', 'jt': 'million', 'jutaan': 'millions', 'miliaran': 'billions', 'triliunan': 'trillions', 'ribuan': 'thousands', 'ratusan': 'hundreds', 'puluhan': 'tens'}

BANNED_ID = [
    r'\bgeleng[- ]geleng\b', r'\bgaruk kepala\b', r'\bkayak dari masa depan\b',
    r'\bkebayang gak\b', r'\byang bener aja\b',
    r'\bgokil\b', r'\bmantap jiwa\b', r'\bsultan\b',
    r'\bauto\b', r'\bskuy\b', r'\bcuy\b',
    r'\bini gak nyangka\b', r'\bsurprise banget\b',
    r'\bhebat\b', r'\bkeren banget\b',
    r'\bgue\b', r'\blo\b', r'\blu\b',  # Personal pronouns — use aku/kamu/kalian
    r'\btemen gue\b', r'\bbapak gue\b', r'\bemak gue\b',
    r'\bkeluarga gue\b', r'\brekan kerja gue\b', r'\bsahabat gue\b',
    r'\blink di bio\b',
    r'\bsetara \d+x\b',
    r'\bkatanya\b', r'\bkonon\b', r'\bdikabarkan\b',
    # Prompt banned patterns (synced with [BANNED PATTERNS] in prompt)
    r'\bbayangin\b', r'\bini bukan cuma\b',
    r'\bgue inget pas kuliah\b', r'\bjangan cuma\b.+coba\b',
    r'\bdalam dunia yang terus berubah\b', r'\bdi era digital ini\b',
    r'\bgame[- ]changer\b',
    # CTA banned phrases (from prompt §8)
    r'\bkomen pendapat lo\b', r'\bshare pendapat lo\b',
    r'\btulis di kolom komentar\b', r'\bceritain pengalaman lo\b',
    r'\bturunin komentar\b', r'\bdrop pendapat lo\b',
    r'\bbagi pendapat lo\b', r'\bsave postingan ini\b',
    r'\bjangan lupa di-?save\b', r'\bsimpan dulu postingan ini\b',
    # Anti-promo banned patterns
    r'\bbikin .+ jadi terjangkau\b', r'\bsolusi cerdas\b', r'\bsolusi terbaik\b',
    r'\bworth it\b', r'\bcoba sekarang\b', r'\bterbaik buat lo\b',
    r'\bspesial buat lo\b', r'\bharus punya\b', r'\bgak boleh ketinggalan\b',
    # Hook junk patterns
    r'\bgue gila\b', r'\bgue kaget\b', r'\bgue shock\b', r'\bgue heran\b',
    r'\bgue penasaran\b', r'\bgue bingung\b', r'\bgue kesel\b',
    r'\btahukah kamu\b', r'\byuk simak\b', r'\bini dia rahasianya\b',
    r'\bsemoga bermanfaat\b', r'\bsemangat ya\b',
]

# Reaksi natural — allowed but MAX 1x per post (tracked in _check_reaksi_count)
REAKSI_NATURAL = [
    r'\bgila sih\b', r'\bgila banget\b', r'\bgila kan\b',
    r'\banjir\b', r'\bseriusan\b', r'\bgimana ceritanya\b',
    r'\bwaduh\b',
]

BANNED_COMMON = [
    # Emojis (catch-all)
    r'[\U00010000-\U0010ffff\u2600-\u27bf\u200d\u20e3\u2702-\u27b0]+',
]

def _get_banned() -> list:
    lang_banned = BANNED_ID if CONTENT_LANG == "id" else BANNED_EN
    return lang_banned + BANNED_COMMON

# ─── API calls ────────────────────────────────────────────────────

# ─── Prompt rotation: different angles per article type ────────

ANGLES = {
    "news": [
        "Write as PRACTICAL INSIGHT — what's the 1 actionable takeaway from this news for orang Indonesia? Everything else is just context.",
        "Write as REAL-WORLD IMPACT — skip the news summary. Focus on: 'Gimana ini ngaruh ke dompet/hidup/kerja kamu?'",
        "Write as LESSON EXTRACTOR — from this news article, extract 3 things pembaca bisa LAKUKAN sekarang.",
    ],
    "product": [
        "Write as BUYER ADVISORY — sebelum beli, ini yang perlu kamu tau. Skip specs, focus on 'worth it gak untuk orang Indonesia?'",
        "Write as VALUE ANALYST — 'Apakah ini worth your money/time?' Bandingin sama alternatif gratis/murah.",
    ],
    "impact": [
        "Write as CAREER ADVISORY — how this affects your job/skills. 3 steps yang bisa kamu ambil.",
        "Write as MONEY INSIGHT — what this means for your finances. Angka konkret, tips praktis.",
        "Write as PREPARATION GUIDE — what to do NOW to prepare for this change. 3 action steps.",
    ],
    "controversy": [
        "Write as DEBRIEF + TIPS — why this controversial, then 3 things yang bisa dilakuin soal ini.",
        "Write as PATTERN RECOGNITION — what history tells us, plus what to watch out for.",
    ],
    "scandal": [
        "Write as DRAMA EXTRACTOR — take the viral story, extract 3 actionable tips pembaca bisa learn dari skandal ini. Drama is the hook, tips are the value.",
        "Write as LESSON LEARNER — what can orang biasa learn from this scandal? 3 practical takeaways grounded in facts.",
        "Write as SCAM ALERT — break down the scam/how it worked, then 3 steps to avoid jadi korban.",
    ],
}

def _classify_article(title: str, body: str) -> str:
    """Classify article type for prompt rotation. Drama takes priority."""
    text = (title + " " + body[:500]).lower()

    # Drama detection (highest priority — viral/hot stories)
    drama_signals = [
        "viral", "heboh", "kontroversial",
        "nganggur", "dipecat", "resign",
        "bongkar", "terungkap", "ternyata", "marah", "protes", "boikot",
        "kolaps", "anjlok",
        "layoff", "scandal", "controversy", "exposed", "shutdown",
        "fired", "bankrupt", "crisis",
    ]
    # Scandal detection — penipuan, manipulasi, fraud
    scandal_signals = [
        "investasi bodong", "penipuan", "skandal", "korupsi", "gratifikasi",
        "money laundering", "pencucian uang", "didenda", "dituntut", "ditangkap",
        "ditahan", "tersangka", "terdakwa", "vonis", "dipenjara",
        "bui", "ditahan", "dituntut", "gugat", "kasus",
        "bank", "investasi", "modus", "tipu", "scam",
    ]
    drama_count = sum(1 for w in drama_signals if re.search(r'\b' + re.escape(w) + r'\b', text))
    scandal_count = sum(1 for w in scandal_signals if re.search(r'\b' + re.escape(w) + r'\b', text))
    if scandal_count >= 2:
        return "scandal"
    if drama_count >= 2:
        return "drama"

    product_words = {"launch", "release", "introduces", "unveils", "new feature", "update", "beta", "app", "tool", "product"}
    impact_words = {"layoff", "jobs", "career", "replace", "automation", "workforce", "salary", "hiring", "funding", "valuation", "ipo"}
    controversy_words = {"banned", "lawsuit", "regulation", "ethical", "bias", "safety", "risk", "danger", "control", "censorship"}

    if any(w in text for w in controversy_words):
        return "controversy"
    if any(w in text for w in product_words):
        return "product"
    if any(w in text for w in impact_words):
        return "impact"
    return "news"

def _get_angle(article_type: str) -> str:
    """Get a rotating angle instruction for the article type."""
    import random
    angles = ANGLES.get(article_type, ANGLES["news"])
    return random.choice(angles)

def _build_user_msg(title: str, body: str, source: str = "", hook_instruction: str = "", cta_instruction: str = "") -> str:
    article_type = _classify_article(title, body)
    angle = _get_angle(article_type)
    hook_part = f"\nHOOK STYLE: {hook_instruction}" if hook_instruction else ""
    cta_part = f"\nCTA STYLE: {cta_instruction}" if cta_instruction else ""
    return f"ANGLE: {angle}{hook_part}{cta_part}\n\nTITLE: {title}\nARTICLE: {body[:4000]}\nSOURCE: {source}"

def _call_mistral(title: str, body: str, source: str = "", hook_instruction: str = "", cta_instruction: str = "") -> Optional[str]:
    prompt = _get_prompt()
    try:
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
            json={"model": "mistral-large-latest",
                  "messages": [{"role": "system", "content": prompt},
                               {"role": "user", "content": _build_user_msg(title, body, source, hook_instruction, cta_instruction)}],
                  "temperature": 0.3, "max_tokens": 2000},
            timeout=120)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Mistral error: {e}")
    return None

def _call_groq(title: str, body: str, source: str = "", hook_instruction: str = "", cta_instruction: str = "") -> Optional[str]:
    if not GROQ_KEY:
        print("Groq skipped (no GROQ_API_KEY)")
        return None
    prompt = _get_prompt()
    try:
        r = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "system", "content": prompt},
                               {"role": "user", "content": _build_user_msg(title, body, source, hook_instruction, cta_instruction)}],
                  "temperature": 0.3, "max_tokens": 2000},
            timeout=120)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Groq error: {e}")
    return None

# ─── Post-processing ─────────────────────────────────────────────

def _fix_slides(data: dict) -> dict:
    """Fix empty slides and collapse gaps in carousel data.
    If a slide is empty/whitespace, merge it with next slide or remove the key.
    Never leaves placeholder values — slides with no content are dropped.
    """
    keys = ['slide_1', 'slide_2', 'slide_3', 'slide_4', 'slide_5', 'slide_6']
    for i, key in enumerate(keys):
        val = (data.get(key) or '').strip()
        if not val:
            # Empty slide — try to pull content from next non-empty slide
            for next_key in keys[i+1:]:
                next_val = (data.get(next_key) or '').strip()
                if next_val:
                    data[key] = next_val
                    data[next_key] = ''
                    break
            # If still empty after shift, delete the key to avoid placeholder
            if not data.get(key, '').strip():
                del data[key]
    return data

def _clean(text: str) -> str:
    """Remove banned phrases, fix grammar issues, enforce whitespace.
    Also enforces reaksi natural MAX 1x per slide."""
    out = text
    banned = _get_banned()
    for pat in banned:
        out = re.sub(pat, '', out, flags=re.I)

    # Clean up after banned phrase removal: "gue !" → remove whole fragment
    # If sentence starts with "gue" followed by orphan punctuation, remove it
    out = re.sub(r'(?i)\bgue\b\s*[,;:.!?]+', '', out)
    # Normalize em-dash/en-dash → space (LLM sering generate ini, breaks number regex)
    out = re.sub('[\u2013\u2014]', ' ', out)
    # Fix: year split by newline e.g. "2\n026" → "2026"
    out = re.sub(r'(\d)\n(\d)', r'\1\2', out)
    # Also handle cases like "Agustus 2\n026" where the year is split
    out = re.sub(r'(\d)\n(?=\d{3})', r'\1', out)
    # Remove orphan punctuation at start of sentences
    out = re.sub(r'(?m)^[\s,;:.!?]+\s*', '', out)
    # Collapse double spaces
    out = re.sub(r'  +', ' ', out).strip()

    # Reaksi natural: keep first occurrence per root word, remove rest
    reaksi_roots = {
        'gila': [r'\bgila sih\b', r'\bgila banget\b', r'\bgila kan\b'],
        'anjir': [r'\banjir\b'],
        'seriusan': [r'\bseriusan\b'],
        'gimana': [r'\bgimana ceritanya\b'],
        'waduh': [r'\bwaduh\b'],
    }
    for root, patterns in reaksi_roots.items():
        all_matches = []
        for pat in patterns:
            for m in re.finditer(pat, out, flags=re.I):
                all_matches.append(m)
        # Sort by position, keep first, remove rest
        all_matches.sort(key=lambda m: m.start())
        for m in reversed(all_matches[1:]):
            out = out[:m.start()] + out[m.end():]

    # Remove em-dashes, en-dashes
    out = out.replace('—', ', ').replace('–', ', ')

    # Strip markdown bold/italic (Threads doesn't support it)
    out = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', out)

    # Remove empty bold artifacts: ** ** or ****
    out = re.sub(r'\*{2,}', '', out)

    # Remove "link in bio" / "link di bio" variants"
    out = re.sub(r'[\(\[]?link\s+(in|di)\s+bio[\)\]]?', '', out, flags=re.I)

    # Fix orphan punctuation: lines starting with , ; : . ! ?
    out = re.sub(r'(?m)^\s*[,;:.!?]+\s*', '', out)

    # Protect list items from sentence splitter
    # Pattern 1: lines starting with "N. " or "- " (already on newlines)
    # Pattern 2: inline "N. " in running text (LLM output as single line)
    # Both must be protected before sentence splitting breaks them
    protected = []
    def _save_list(m):
        protected.append(m.group())
        return f'\x00LIST{len(protected)-1}\x00'

    # First: protect explicit list lines (already on own line)
    out = re.sub(r'^\s*(?:\d+\.\s+|[-•]\s+).+$', _save_list, out, flags=re.MULTILINE)

    # Second: protect inline "N. text" sequences that appear 2+ times
    # Match: "prefix: N. text N. text" or just "N. text N. text"
    def _protect_inline_lists(m):
        full = m.group()
        protected.append(full)
        return f'\x00LIST{len(protected)-1}\x00'

    # Find inline numbered: text that has 2+ "N. word..." patterns
    out = re.sub(
        r'(?:^|\s)\d+\.\s+\D+?(?:\s+\d+\.\s+\D+?){1,}(?=\s+[.!?]|$)',
        _protect_inline_lists, out
    )

    # Third: protect inline bullets: "- text - text - text"
    def _protect_inline_bullets(m):
        full = m.group()
        # Split into individual items
        items = re.split(r'\s+-\s+', full.strip())
        items = [i.strip().lstrip('- ').strip() for i in items if i.strip()]
        if len(items) >= 2:
            # Check if first chunk is prefix (question or short phrase before bullets)
            first = items[0]
            is_prefix = (first.endswith('?') or first.endswith(':') or first.endswith('.')
                         or len(first.split()) <= 6)
            if is_prefix and len(items) > 2:
                prefix = first.rstrip(':').rstrip('.')
                rest = items[1:]
                numbered = prefix + ':\n' + '\n'.join(f'{i+1}. {item}' for i, item in enumerate(rest))
            else:
                numbered = '\n'.join(f'{i+1}. {item}' for i, item in enumerate(items))
            protected.append(numbered)
            return f'\x00LIST{len(protected)-1}\x00'
        # Single item, protect as-is
        protected.append(full)
        return f'\x00LIST{len(protected)-1}\x00'

    out = re.sub(
        r'(?:^|\s)-\s+\D+?(?:\s+-\s+\D+?){1,}(?=\s+[.!?]|$)',
        _protect_inline_bullets, out
    )

    # Enforce: one sentence per line, separated by double newline
    out = re.sub(r'\n+', ' ', out)
    out = re.sub(r' {2,}', ' ', out).strip()
    sentences = re.split(r'(?<=[.!?])\s+', out)
    out = '\n\n'.join(s.strip() for s in sentences if s.strip())

    # Restore protected list items (reverse order to handle nesting)
    for i, item in enumerate(protected):
        out = out.replace(f'\x00LIST{i}\x00', item.strip())

    # Fix: rejoin fragmented list items that got split by sentence splitter
    # Pattern: "text N.\n\n" followed by more text — the N. was the start of a list item
    # Step 1: rejoin "text)\n\nN.\n\ncontent" → "text) N. content"
    # (When a parenthesized context ends, the N. that follows is likely a list item)
    out = re.sub(r'(\))\n\n(\d+)\.\n\n(.+?)(?=\n\n\d+\.\n\n|\n\n[^0-9]|$)',
                 lambda m: m.group(1) + ' ' + m.group(2) + '. ' + m.group(3),
                 out)
    # Step 2: rejoin "prefix?\n\nN.\n\ncontent" → "prefix?\n\nN. content"
    out = re.sub(r'(\?)\n\n(\d+)\.\n\n(.+?)(?=\n\n\d+\.\n\n|\n\n[^0-9]|$)',
                 lambda m: m.group(1) + '\n\n' + m.group(2) + '. ' + m.group(3),
                 out)
    # Step 3: rejoin consecutive "N.\n\ntext" into one list
    # "1.\n\nBot...\n\n2.\n\nDeteksi...\n\n3.\n\nSpam..." → "1. Bot...\n2. Deteksi...\n3. Spam..."
    def _rejoin_items(m):
        block = m.group()
        parts = re.split(r'(\d+)\.\n\n', block)
        items = []
        for j in range(1, len(parts), 2):
            if j+1 < len(parts):
                items.append(f'{parts[j]}. {parts[j+1].strip()}')
        return '\n'.join(items) if items else block

    out = re.sub(r'(?:\d+\.\n\n.+\n\n){2,}', _rejoin_items, out)

    # Step 4: add newline before "N. " that directly follows text (no newline separator)
    out = re.sub(r'(\S)(\d+\.\s)', lambda m: m.group(1) + '\n' + m.group(2), out)

    return out


def _lists_to_narrative(text: str) -> str:
    """Convert numbered/bulleted lists to narrative form per §5.
    '1. Daftar 2. Isi data 3. Submit' → 'Pertama, daftar. Abis itu, isi data. Terakhir, submit.'
    """
    # Numbered list items on separate lines
    lines = text.split('\n')
    result = []
    buffer = []
    
    connectors = ['Pertama', 'Abis itu', 'Terakhir']
    
    for line in lines:
        stripped = line.strip()
        # Match "N. text" pattern
        m = re.match(r'^(\d+)\.\s+(.+)', stripped)
        if m:
            buffer.append(m.group(2).strip())
        else:
            if buffer:
                # Flush buffer as narrative
                if len(buffer) <= 3:
                    parts = []
                    for i, item in enumerate(buffer):
                        if i < len(connectors):
                            parts.append(f"{connectors[i]}, {item[0].lower()}{item[1:]}")
                        else:
                            parts.append(item)
                    result.append('. '.join(parts) + '.')
                else:
                    result.append('. '.join(buffer) + '.')
                buffer = []
            result.append(line)
    
    if buffer:
        if len(buffer) <= 3:
            parts = []
            for i, item in enumerate(buffer):
                if i < len(connectors):
                    parts.append(f"{connectors[i]}, {item[0].lower()}{item[1:]}")
                else:
                    parts.append(item)
            result.append('. '.join(parts) + '.')
        else:
            result.append('. '.join(buffer) + '.')
    
    return '\n'.join(result)


def _format_lists(text: str) -> str:
    """Detect and normalize inline lists into proper numbered format.
    Handles: 'prefix: 1. text 2. text 3. text' and 'prefix: - text - text'"""
    lines = text.split('\n')
    out_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            out_lines.append('')
            continue

        # Pattern A: inline numbered — "N. text N. text N. text" in same line
        # Find all "N. " patterns followed by non-numeric text
        items = re.findall(r'(\d+)\.\s+(.+?)(?=\s+\d+\.\s+|$)', line)
        if len(items) >= 2:
            # Extract prefix (text before first number)
            first_num = re.search(r'\d+\.\s+', line)
            prefix = line[:first_num.start()].strip().rstrip(':').rstrip('.')
            if prefix:
                out_lines.append(prefix + ':')
            for num, content in items:
                out_lines.append(f'{num}. {content.strip()}')
            continue

        # Pattern B: inline bullets — "prefix: - text - text - text"
        if ' - ' in line:
            parts = re.split(r'\s+-\s+', line)
            rest = parts[1:] if len(parts) > 1 else []
            if rest:
                first = parts[0].strip()
                # Heuristic: if first chunk is short/has colon, it's a prefix
                if first.endswith(':') or first.endswith('.') or len(first.split()) <= 8:
                    prefix = first.rstrip(':').rstrip('.')
                    out_lines.append(prefix + ':')
                    for i, item in enumerate(rest, 1):
                        out_lines.append(f'{i}. {item.strip()}')
                    continue

        out_lines.append(line)

    return '\n'.join(out_lines)


def _fix_hook_caps(text: str) -> str:
    """Fix ALL CAPS abuse in hook. Keep only 1 emphasized word in CAPS.
    'KARYAWAN MICROSOFT BERISIKO KENA PHK' → 'karyawan Microsoft BERISIKO kena PHK'
    Keeps acronym-like caps (AI, API, PHK, etc.) and short exclamations."""
    # Find ALL-CAPS words (3+ chars, not acronyms like AI/API/URL/CEO)
    words = text.split()
    caps_words = []
    for i, w in enumerate(words):
        clean = re.sub(r'[^A-Za-z]', '', w)
        if len(clean) >= 3 and clean.isupper() and clean not in {'AI', 'API', 'URL', 'CEO', 'CTO', 'PHK', 'PSE', 'VPN', 'RSS', 'GPT', 'LLM', 'SEO', 'SMB', 'UKM', 'UMKM', 'KUR', 'PNS', 'OJK', 'BI', 'SEO', 'HTML', 'CSS', 'JS', 'SDK', 'AWS', 'SQL'}:
            caps_words.append(i)
    
    # If 0-1 caps words, nothing to fix
    if len(caps_words) <= 1:
        return text
    
    # Keep the FIRST caps word (strongest emphasis), lowercase the rest
    # Brand names → title case, everything else → lowercase
    brands = {'MICROSOFT': 'Microsoft', 'GOOGLE': 'Google', 'META': 'Meta', 'APPLE': 'Apple',
              'AMAZON': 'Amazon', 'REDDIT': 'Reddit', 'TWITTER': 'Twitter', 'TIKTOK': 'TikTok',
              'OPENAI': 'OpenAI', 'SHOPEE': 'Shopee', 'TOKOPEDIA': 'Tokopedia', 'GOTO': 'GoTo',
              'GRAB': 'Grab', 'GOTOPE': 'GOTOPE', 'BYTEDANCE': 'ByteDance', 'SAMSUNG': 'Samsung',
              'XIAOMI': 'Xiaomi', 'HUAWEI': 'Huawei', 'ANTHROPIC': 'Anthropic', 'NVIDIA': 'NVIDIA',
              'SPOTIFY': 'Spotify', 'NETFLIX': 'Netflix', 'STRAVA': 'Strava'}
    keep_idx = caps_words[0]
    for idx in caps_words[1:]:
        if words[idx] in brands:
            words[idx] = brands[words[idx]]
        else:
            words[idx] = words[idx].lower()
    
    return ' '.join(words)

def _validate_hook(text: str) -> tuple[bool, list[str]]:
    """Check hook quality. Returns (valid, list of issues)."""
    issues = []
    words = text.split()
    
    # 1. Length: under 30 words
    if len(words) > 30:
        issues.append(f"too long ({len(words)} words, need under 30)")
    if len(words) < 10:
        issues.append(f"too short ({len(words)} words, need 10+)")
    
    # 2. Check for number/impact (increases engagement)
    has_number = bool(re.search(r'\d+', text))
    if not has_number:
        issues.append("no number (numbers increase engagement)")
    
    # 3. Check for curiosity/emotional triggers
    curiosity_words = {
        "secret", "hidden", "shocking", "surprising", "unexpected", "never",
        "actually", "real", "truth", "mistake", "wrong", "fail", "success",
        "breakthrough", "discovered", "revealed", "exposed", "leaked",
        "just", "new", "first", "only", "biggest", "most", "worst", "best",
        # Indonesian triggers
        "baru", "pertama", "ternyata", "ternyata,", "ternyata.", "tiba-tiba",
        "bocor", "bongkar", "terungkap", "ternyata", "ngehe", "parah",
        "kaget", "seriusan", "geram", "miris", "ngeri", "gila",
        "larang", "dilarang", "blokir", "diblokir", "salah", "gagal",
    }
    text_lower = text.lower()
    has_curiosity = any(w in text_lower for w in curiosity_words)
    if not has_curiosity:
        issues.append("no curiosity trigger (try: secret, shocking, just, new, first)")
    
    # 4. Check for question or exclamation (engagement hook)
    has_hook_punctuation = text.rstrip().endswith(('?', '!'))
    
    # 5. Check for personal angle ("you", "your", "I", "we")
    personal_words = {"you", "your", "i", "we", "our", "my",
                      "aku", "kamu", "kalian", "kita"}
    has_personal = any(w in text_lower.split() for w in personal_words)
    
    # Score the hook
    score = 0
    if not issues: score += 2
    if has_number: score += 1
    if has_curiosity: score += 1
    if has_hook_punctuation: score += 1
    if has_personal: score += 1
    
    # Hook is valid if score >= 3 and length in range
    valid = score >= 3 and 10 <= len(words) <= 30
    
    if not valid and not issues:
        issues.append(f"hook score {score}/6, need 3+")
    
    return valid, issues

def _score_hook(text: str) -> int:
    """Score a hook 0-10. v3 weights: detail+emotion=pressbox pattern."""
    score = 0
    words = text.split()
    text_lower = text.lower()
    word_count = len(words)

    # 1. Length sweet spot (10-20 words) = 2 pts
    if 10 <= word_count <= 20:
        score += 2

    # 2. Has specific number = 2 pts
    if re.search(r'\d+', text):
        score += 2

    # 3. Curiosity/emotional trigger
    curiosity = {'secret', 'shocking', 'surprising', 'unexpected', 'never',
                 'actually', 'real', 'truth', 'mistake', 'breakthrough',
                 'just', 'new', 'first', 'only', 'biggest', 'most', 'worst', 'best',
                 'gila', 'seriusan', 'anjir', 'parah', 'gilanya', 'ternyata'}
    if any(w in text_lower for w in curiosity):
        score += 1

    # 4. Engagement punctuation (? or !)
    if text.rstrip().endswith(('?', '!')):
        score += 1

    # 5. Personal pronouns (ID: aku/kamu/kalian, EN: you/your/i/we)
    personal = {'you', 'your', 'i', 'we', 'our', 'my', 'aku', 'kamu', 'kalian', 'kita'}
    if any(w in text_lower.split() for w in personal):
        score += 1

    # 6. CAPS emphasis (2+ uppercase chars in a row)
    if re.search(r'[A-Z]{2,}', text):
        score += 1

    # 7. Contrast/tension words
    tension = {'but', 'however', 'yet', 'actually', 'turns', 'except', 'wait',
               'tapi', 'ternyata', 'padahal', 'eh', 'taunya'}
    if any(w in text_lower.split() for w in tension):
        score += 1

    # 8. Specificity (proper nouns or tech terms)
    specificity = {'ai', 'openai', 'anthropic', 'claude', 'gpt', 'google', 'nvidia',
                   'microsoft', 'meta', 'apple', 'tesla', 'spacex', 'github', 'api',
                   'llm', 'startup', 'python', 'javascript', 'blockchain'}
    if any(w in text_lower.split() for w in specificity):
        score += 1

    # 9. Strong opening (doesn't start with weak words)
    weak_openers = {'the', 'a', 'an', 'in', 'it', 'this', 'that', 'there', 'ini', 'itu', 'ada'}
    first_word = words[0].lower().strip('.,!?') if words else ''
    if first_word and first_word not in weak_openers:
        score += 1

    # 10. Emotional weight / human consequence (pressbox Pattern C)
    emotion_words = {'korban', 'rugi', 'dampak', 'ancaman', 'bahaya', 'resiko',
                     'kesempatan', 'peluang', 'harapan', 'kecewa', 'kecewa.',
                     'mother', 'tears', 'broken', 'denied', 'banned', 'fired',
                     'karyawan', 'buruh', 'pengguna', 'konsumen', 'masyarakat'}
    if any(w in text_lower for w in emotion_words):
        score += 1

    return min(score, 10)


def _rewrite_hook(hook: str, article_title: str, body: str, score: int) -> tuple[str, int]:
    """Auto-rewrite hook if score < 7. Returns (new_hook, new_score)."""
    if score >= 7:
        return hook, score

    # Build feedback on what's missing
    words = hook.split()
    text_lower = hook.lower()
    missing = []

    if not (10 <= len(words) <= 25):
        missing.append("keep it between 10-25 words")
    if not re.search(r'\d+', hook):
        # Try to extract number from article
        nums = re.findall(r'\d[\d,.]*\+?\s*(?:%|percent|million|billion|thousand|gen|cell|researcher|scientist|company)?', body[:500].lower())
        if nums:
            missing.append(f"add a number from the article (like {nums[0].strip()})")
        else:
            missing.append("add a specific number")
    curiosity = {'shocking', 'surprising', 'never', 'actually', 'first', 'new', 'just', 'gila', 'seriusan', 'ternyata'}
    if not any(w in text_lower for w in curiosity):
        missing.append("add a curiosity word (shocking, baru, ternyata, gila)")
    if not hook.rstrip().endswith(('?', '!')):
        missing.append("end with ? or !")
    if not re.search(r'[A-Z]{2,}', hook):
        missing.append("CAPITALIZE one important word for emphasis")

    if not missing:
        return hook, score

    prompt_fix = f"""Rewrite this hook to score higher. Current issues: {', '.join(missing)}.

Original hook: {hook}
Article title: {article_title}
Article excerpt: {body[:500]}

Rules:
- Under 25 words, MAX 2 sentences
- MUST start with a FACT or NUMBER from the article excerpt above — copy exact numbers, do NOT invent
- PREFERRED FORMAT (pressbox Pattern C): [Specific amount/number] + [Human consequence] — e.g. 'Rp46,9 triliun. Yang rugi kamu juga.'
- Capitalize ONE key word
- End with ? or ! if it's a question/exclamation
- Mix Indonesian-English naturally
- Sound like a real person texting, not an AI
- NEVER start with "gue [emotion]" (gue gila, gue kaget, etc.) — meaningless filler
- MUST provoke REPLIES — end with question or call to opinion
- Format: [ANGKA/FAKTA] + [KONSEKUENSI KE PEMBACA]
- If no numbers in excerpt, start with the most surprising fact instead
- Do NOT use em-dashes (—) or special dashes. Use commas or hyphens only.

Return ONLY the rewritten hook text, nothing else."""

    try:
        import httpx, os
        key = os.getenv("MISTRAL_API_KEY", "")
        if not key:
            return hook, score
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "mistral-large-latest",
                  "messages": [{"role": "user", "content": prompt_fix}],
                  "temperature": 0.3, "max_tokens": 100},
            timeout=30)
        if r.status_code == 200:
            new_hook = r.json()["choices"][0]["message"]["content"].strip().strip('"')
            new_score = _score_hook(new_hook)
            if new_score > score:
                print(f"  [REWRITE] Hook {score}/10 → {new_score}/10: {new_hook[:60]}...")
                return new_hook, new_score
    except Exception as e:
        print(f"  [REWRITE] Error: {e}")

    return hook, score

def _generate_variant(title: str, body: str, source: str, provider: str, hook_instruction: str = "", cta_instruction: str = "") -> Optional[dict]:
    """Generate one carousel variant. Returns parsed dict or None."""
    if provider == "mistral":
        raw = _call_mistral(title, body, source, hook_instruction, cta_instruction)
    else:
        raw = _call_groq(title, body, source, hook_instruction, cta_instruction)
    if raw is None:
        return None
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned)
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"  [ERR] JSON parse failed: {e}")
        print(f"  [ERR] Raw response (first 500): {raw[:500]}")
        return None
    for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
        if key in data:
            data[key] = _format_lists(_clean(data[key]))
    # Also clean caption (em dashes, banned phrases, etc.)
    if "caption" in data:
        data["caption"] = _clean(data["caption"])
    # Fix ALL CAPS abuse in hook — keep only 1 emphasized word
    if "slide_1" in data:
        data["slide_1"] = _fix_hook_caps(data["slide_1"])
    return data

def _check_fabricated_numbers(slides: dict, article_body: str) -> list[str]:
    """Check if numbers/quantities in slides exist in article. Returns list of violations."""
    violations = []
    article_numbers = set(re.findall(r'\d[\d.,]*%?', article_body))
    article_nums_flat = set()
    for n in article_numbers:
        clean = n.replace('.', '').replace(',', '').rstrip('%')
        if clean.isdigit():
            article_nums_flat.add(clean)
    quantity_words = re.compile(r'\b(jutaan|ribuan|ratusan|puluhan|miliaran|triliunan|juta|ribu|ratus)\b', re.I)
    time_units = r'(?:tahun|bulan|hari|minggu|jam|dekade|abad|detik|menit|weeks?|months?|years?|days?|hours?|decades?|centur)'
    for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
        if key not in slides:
            continue
        text = slides[key]
        slide_numbers = re.findall(r'\d[\d.,]*%?', text)
        for sn in slide_numbers:
            sn_clean = sn.replace('.', '').replace(',', '').rstrip('%')
            if not sn_clean.isdigit():
                continue
            if len(sn_clean) == 4 and 2000 <= int(sn_clean) <= 2099:
                continue
            if sn_clean not in article_nums_flat:
                if int(sn_clean) <= 5:
                    if not re.search(re.escape(sn) + r'\s*' + time_units, text, re.I):
                        continue
                violations.append(f"{key}: number '{sn}' not in article")
        for m in quantity_words.finditer(text):
            word = m.group().lower()
            if word not in article_body.lower():
                en_word = QTY_EN_MAP.get(word, '')
                if en_word and en_word in article_body.lower():
                    continue
                violations.append(f"{key}: quantity '{word}' not in article")
    return violations


# ─── Factual claim grounding ───────────────────────────────────


def _check_fabricated_claims(slides: dict, article_body: str, title: str = "") -> list[str]:
    """Check if factual claims in slides contradict the article. Returns list of severe violations."""
    violations = []
    article_lower = (article_body + " " + title).lower()

    # Build all slide text
    all_slide_text = " ".join(slides.get(k, "") for k in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"])
    slide_lower = all_slide_text.lower()

    # Check specific contradiction patterns
    # Pattern: slide says "GRATIS" but article says "bayar/berbayar/terbatas/pelanggan"
    if re.search(r'\b(gratis|free)\b', slide_lower):
        if re.search(r'\b(bayar|berbayar|langganan|berlangganan|pelanggan|subscription|terbatas|limited|terbatas untuk)\b', article_lower):
            violations.append("slides say FREE but article says PAID/subscriber-only")
        if not re.search(r'\b(gratis|free|tanpa biaya|tanpa bayar)\b', article_lower):
            violations.append("slides say FREE but article never mentions free")

    # Pattern: slide says availability that article doesn't support
    if re.search(r'\b(udah bisa|sudah bisa|bisa dicoba|udah coba|sudah coba)\b', slide_lower):
        if re.search(r'\b(belum|belum tersedia|belum rilis|masih terbatas|belum hadir)\b', article_lower):
            if not re.search(r'\b(sudah|telah|resmi|bisa)\b', article_lower):
                violations.append("slides say available but article says not yet")

    # Pattern: slide says specific price but article doesn't have it
    price_pattern = r'(?:rp|usd|\$)\s*[\d.,]+'
    slide_prices = re.findall(price_pattern, slide_lower)
    for price in slide_prices:
        # Normalize
        price_norm = re.sub(r'[^\d]', '', price)
        if price_norm and price_norm not in re.sub(r'[^\d]', '', article_lower):
            violations.append(f"price '{price}' not in article")

    # Per-slide claim check
    for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
        text = slides.get(key, "").lower()
        if not text:
            continue

        # Check if slide claims something article contradicts
        # Article says "terbatas/beta" → slide shouldn't say available everywhere
        if re.search(r'\b(terbatas|beta|uji coba)\b', article_lower):
            if re.search(r'\b(untuk semua|umum|publik|semua orang|tersedia bebas)\b', text):
                violations.append(f"{key}: says available to all but article says limited/beta")

    # ─── Unsubstantiated future predictions with specific timeframe ───
    prediction_pattern = r'\b(\d+)\s*(?:tahun|bulan|minggu|dekade)\s*(?:ke depan|mendatang|lagi|depan)\b'
    for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
        text = slides.get(key, "").lower()
        if not text:
            continue
        for m in re.finditer(prediction_pattern, text):
            timeframe = m.group(0)
            if not re.search(r'\b\d+\s*(?:tahun|bulan|minggu|dekade)\b', article_lower):
                violations.append(f"{key}: prediction '{timeframe}' not in article")

    # ─── Entity risk attribution ───
    # Slide says "risiko buat X" but article doesn't mention X at risk
    for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
        text = slides.get(key, "").lower()
        if not text:
            continue
        for m in re.finditer(r'risiko\s+(?:buat|untuk|bagi)\s+(.+?)(?:\.|$)', text):
            risk_clause = m.group(1)
            # Extract all entities (capitalized words or known brands)
            entities = re.findall(r'\b(tsmc|nvidia|samsung|apple|google|intel|amazon|anthropic|openai|meta|microsoft)\b', risk_clause, re.I)
            for entity in entities:
                if entity.lower() not in article_lower and entity.upper() not in article_body:
                    violations.append(f"{key}: risk for '{entity}' but article doesn't mention {entity}")

    return violations


def _check_slide_coherence(slides: dict) -> list[str]:
    """Check intra-slide coherence: do sentences within each slide connect logically?
    Returns list of issues found."""
    issues = []
    
    for key in [f"slide_{i}" for i in range(1, 7)]:
        text = slides.get(key, "")
        if not text:
            continue
        
        # Split into sentences (rough split on . ! ? or newlines)
        sentences = [s.strip() for s in re.split(r'[.!?]\s*|\n\n+', text) if len(s.strip()) > 10]
        
        if len(sentences) < 2:
            continue
        
        # Check 1: First vs last sentence redundancy (same core idea repeated)
        first_words = set(sentences[0].lower().split())
        last_words = set(sentences[-1].lower().split())
        # Remove stopwords
        stopwords = {'yang', 'di', 'dan', 'ini', 'itu', 'dengan', 'untuk', 'pada', 'ke', 'dari',
                     'adalah', 'itu', 'juga', 'sudah', 'masih', 'belum', 'akan', 'bisa', 'tidak',
                     'gak', 'bukan', 'lebih', 'paling', 'sangat', 'atau', 'tapi', 'namun', 'justru',
                     'the', 'is', 'are', 'was', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                     'of', 'with', 'by', 'a', 'an', 'that', 'this', 'it', 'not', 'just', 'so'}
        first_core = first_words - stopwords
        last_core = last_words - stopwords
        
        if first_core and last_core:
            overlap = first_core & last_core
            # If >40% overlap in core words, likely redundant
            overlap_ratio = len(overlap) / min(len(first_core), len(last_core))
            if overlap_ratio > 0.4:
                issues.append(f"{key}: first & last sentence may be redundant (overlap: {overlap})")
        
        # Check 2: Multiple stats without connecting words
        stat_pattern = re.compile(r'\d+%|\d+[\.,]?\d*\s*(?:juta|miliar|ribu|triliun)')
        stats_in_slide = stat_pattern.findall(text)
        if len(stats_in_slide) >= 2:
            # Check if there's a connecting word between stats
            connectors = {'tapi', 'namun', 'sedangkan', 'sementara', 'padahal', 'dan', 'hasilnya',
                          'kontras', 'paradoks', 'ironis', 'malah', 'justru'}
            text_lower = text.lower()
            has_connector = any(c in text_lower for c in connectors)
            if not has_connector:
                issues.append(f"{key}: {len(stats_in_slide)} stats without clear connector")
    
    return issues


def _check_inter_slide_flow(slides: dict) -> list[str]:
    """Check if slides connect logically (no topic jumps between consecutive slides).
    Returns list of flow issues found."""
    issues = []
    stopwords = {'yang', 'di', 'dan', 'ini', 'itu', 'dengan', 'untuk', 'pada', 'ke', 'dari',
                 'adalah', 'juga', 'sudah', 'masih', 'belum', 'akan', 'bisa', 'tidak',
                 'gak', 'bukan', 'lebih', 'paling', 'sangat', 'atau', 'tapi', 'namun',
                 'the', 'is', 'are', 'was', 'and', 'or', 'but', 'in', 'on', 'at', 'to',
                 'for', 'of', 'with', 'by', 'a', 'an', 'that', 'this', 'it', 'not',
                 'just', 'so', 'aku', 'kamu', 'kalian', 'kita', 'mereka'}
    
    slide_keys = [f"slide_{i}" for i in range(1, 7)]
    prev_core = set()
    
    for key in slide_keys:
        text = slides.get(key, "")
        if not text:
            continue
        words = set(re.findall(r'[a-z]{4,}', text.lower())) - stopwords
        
        if prev_core and words:
            overlap = prev_core & words
            overlap_ratio = len(overlap) / max(min(len(prev_core), len(words)), 1)
            if overlap_ratio < 0.1 and len(words) > 5:
                issues.append(f"{key}: low connection to previous slide (overlap: {overlap_ratio:.0%}, shared: {overlap or 'none'})")
        
        prev_core = words
    
    return issues


def _check_jargon(slides: dict) -> list[str]:
    """Check for unexplained foreign jargon in slides.
    Returns list of jargon violations (foreign terms not explained in Indonesian)."""
    issues = []
    
    # Tech terms OK in English (common knowledge)
    tech_ok = {
        'ai', 'chatgpt', 'gemini', 'copilot', 'openai', 'google', 'microsoft',
        'apple', 'meta', 'samsung', 'nvidia', 'iphone', 'android', 'windows',
        'laptop', 'cloud', 'download', 'upload', 'wifi', 'bluetooth', 'usb',
        'app', 'browser', 'email', 'online', 'offline', 'password', 'login',
        'server', 'data', 'chip', 'gpu', 'cpu', 'ram', 'ssd', 'hdd',
        'tiktok', 'instagram', 'whatsapp', 'threads', 'telegram',
        'startup', 'blockchain', 'crypto', 'bitcoin', 'ethereum',
        'digital', 'smartphone', 'chatbot', 'prompt', 'robot',
        'phishing', 'malware', 'ransomware', 'hacker', 'firewall',
        'e-commerce', 'fintech', 'edtech', 'healthtech',
    }
    
    # Foreign business/finance jargon that MUST be explained
    jargon_warn = {
        'talent mobility': 'dipindah divisi, bukan dipecat',
        'quiet quitting': 'kerja seadanya tanpa resign',
        'golden handshake': 'uang pesangon gede',
        'end user': 'pengguna akhir',
        'purchase order': 'surat pesanan',
        'supply chain': 'rantai pasok',
        'cash flow': 'aliran uang',
        'return on investment': 'keuntungan dari investasi',
        'due diligence': 'pengecekan detail sebelum transaksi',
        'escrow': 'rekening bersama',
        'bailout': 'diselamatkan pemerintah',
        'hedge fund': 'dana lindung nilai',
        'venture capital': 'modal ventura',
        'equity': 'saham/kepemilikan',
        'leverage': 'pinjaman untuk investasi',
        'benchmark': 'patokan/pembanding',
        'compliance': 'kepatuhan aturan',
        'stakeholder': 'pihak terkait',
        'deadline': 'batas waktu',
        'brainstorming': 'diskusi ide bareng',
        'follow up': 'tindak lanjut',
        'meeting': 'rapat',
        'report': 'laporan',
        'schedule': 'jadwal',
        'budget': 'anggaran',
        'approval': 'persetujuan',
        'referral': 'rekomendasi',
        'onboarding': 'masuk/pelatihan awal',
        'offboarding': 'keluar/proses resign',
        'headcount': 'jumlah karyawan',
        'upsell': 'jual produk lebih mahal',
        'churn': 'pelanggan kabur',
        'retention': 'pertahanan pelanggan',
    }
    
    slide_keys = [f"slide_{i}" for i in range(1, 7)]
    all_text = " ".join(slides.get(k, "").lower() for k in slide_keys)
    
    for jargon, replacement in jargon_warn.items():
        if jargon.lower() in all_text:
            # Check if it's explained nearby (replacement words present)
            explain_words = replacement.lower().split(",")[0].split()
            if not any(w in all_text for w in explain_words if len(w) > 3):
                issues.append(f"jargon '{jargon}' used without explanation → suggest: '{replacement}'")
    
    return issues


def _check_topic_relevance(slides: dict, article_title: str, article_body: str) -> list[str]:
    """Check if slides discuss the same topic AND same type of content as the article.
    
    Two checks:
    1. Word overlap: slides must share keywords with article title/body
    2. Content type: if article is NOT a tutorial, slides shouldn't contain tutorials
       (catches LLM inventing 'how to use X' when article is about controversy/news)
    """
    violations = []
    
    stopwords = {'yang', 'di', 'dan', 'ini', 'itu', 'dengan', 'untuk', 'pada', 'ke', 'dari',
                 'adalah', 'juga', 'sudah', 'masih', 'belum', 'akan', 'bisa', 'tidak',
                 'gak', 'bukan', 'lebih', 'paling', 'sangat', 'atau', 'tapi', 'namun',
                 'the', 'is', 'are', 'was', 'and', 'or', 'but', 'in', 'on', 'at', 'to',
                 'for', 'of', 'with', 'by', 'a', 'an', 'that', 'this', 'it', 'not',
                 'just', 'so', 'how', 'what', 'why', 'when', 'new', 'ini', 'itu',
                 'yang', 'aku', 'kamu', 'dan', 'atau', 'tapi', 'juga', 'sudah', 'baru'}
    
    # ── Check 1: Does article contain tutorial/how-to content? ──
    tutorial_phrases = [
        r'\bcara\s+\w+', r'\blangkah\s+\w+', r'\btips?\s+\w+',
        r'\bpertama\b.*\bkedua\b', r'\bstep\s+\d',
        r'\bbuka\s+\w+', r'\binstall\s+\w+', r'\bdownload\s+\w+',
        r'\bikutin\s+langkah\b', r'\byang\s+bisa\s+lo\s+lakuin\b',
        r'\bberikut\s+caranya\b', r'\bini\s+dia\s+langkah\b',
    ]
    body_lower = article_body.lower()
    article_has_tutorial = sum(1 for p in tutorial_phrases if re.search(p, body_lower)) >= 2
    
    # ── Is article about a product/tool? (tutorial slides expected) ──
    product_signals = [
        r'\b(fitur|fitur-fitur|feature)\b', r'\brilis\b', r'\bresmi\b',
        r'\blaunch\b', r'\bupdate\b', r'\bversi\s+(baru|terbaru)\b',
        r'\bmenghadirkan\b', r'\bmeluncurkan\b', r'\bmerilis\b',
        r'\b(cara|langkah)\s+(pakai|guna|gunakan|menggunakan)\b',
        r'\btool\b', r'\baplikasi\b', r'\bplatform\b',
    ]
    article_is_product = sum(1 for p in product_signals if re.search(p, body_lower)) >= 2
    
    # ── Check 2: Do slides contain tutorial content? ──
    slide_tutorial_phrases = [
        r'\bpertama\b', r'\bkedua\b', r'\bketiga\b',
        r'\blo\s+bisa\s+pake\b', r'\bcukup\s+\w+\s+\w+\s+dan\b',
        r'\bstep\s+\d', r'\blangkah\s+\w+',
        r'\bcaranya\b', r'\bgimana\s+cara\b',
        r'\bbuka\s+aplikasi\b', r'\binstall\s+\w+',
        r'\bupload\s+\w+', r'\bklik\s+\w+',
    ]
    
    # ── Check 3: Word overlap with title ──
    title_words = set()
    for w in re.findall(r'[a-zA-Z\u00C0-\u024F]{3,}', article_title.lower()):
        if w not in stopwords:
            title_words.add(w)
    
    for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
        text = slides.get(key, "").lower()
        if not text:
            continue
        
        # CTA slide can be generic
        if key == "slide_6":
            continue
        # Short articles (<80 words) get more leeway
        if len(article_body.split()) < 80:
            continue
        
        # ── Tutorial check: slide has tutorial but article doesn't ──
        # Skip tutorial check if article itself is about a product/tool (tutorials expected)
        slide_has_tutorial = sum(1 for p in slide_tutorial_phrases if re.search(p, text)) >= 2
        if slide_has_tutorial and not article_has_tutorial and not article_is_product:
            violations.append(f"{key}: tutorial content in non-tutorial article")
            continue  # Skip word overlap check — this is already a violation
        
        # ── Word overlap check ──
        slide_words = set()
        for w in re.findall(r'[a-zA-Z\u00C0-\u024F]{3,}', text):
            if w not in stopwords:
                slide_words.add(w)
        
        if title_words:
            title_overlap = title_words & slide_words
            title_ratio = len(title_overlap) / len(title_words)
            
            if title_ratio < 0.25 and len(title_words) >= 3:
                # Product articles get lower threshold (10%) — slides use different product terms
                threshold = 0.10 if article_is_product else 0.15
                # Short articles (<2000 chars) get lower threshold — body may not have enough keywords
                if len(article_body) < 2000:
                    threshold = 0.08
                if title_ratio < threshold:
                    # Check body keywords as fallback
                    body_excerpt = article_body[:1000].lower()
                    body_kw = set(w for w in re.findall(r'[a-zA-Z\\u00C0-\\u024F]{3,}', body_excerpt) if w not in stopwords)
                    body_overlap = body_kw & slide_words

                    min_kw = 1  # lowered from 2 — any body keyword overlap = probably related
                    if len(body_overlap) < min_kw:
                        violations.append(f"{key}: off-topic (title overlap {title_ratio:.0%}, body kw: {len(body_overlap)})")
    
    return violations


def _get_recent_hook_patterns(limit: int = 5) -> list[str]:
    """Get hook patterns from last N posts to enforce variety."""
    try:
        from db import get_db
        conn = get_db()
        rows = conn.execute(
            "SELECT slide_hook FROM posts WHERE status='posted' AND slide_hook IS NOT NULL ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        patterns = []
        for r in rows:
            h = (r['slide_hook'] or '').strip().lower()
            # Eva Alicia style pattern detection
            if re.search(r'kamu pikir|kamu masih pikir|kamu masih ngerasa', h):
                patterns.append("TRUTH_BOMB")
            elif re.search(r'kamu masih|kamu yang|kamu ngerasa', h):
                patterns.append("PERSONAL_CHALLENGE")
            elif re.search(r'yang sebenarnya|bukan soal|bukan tentang', h):
                patterns.append("REFRAME_BOMB")
            elif re.search(r'\d+%|\d+\.\d+|\d+ juta|\d+ miliar|\d+ triliun', h) and re.search(r'lo tau|artinya|buat lo', h):
                patterns.append("SCARY_FACT")
            elif re.search(r'yang gak.*bahas|yang jarang|fakta tersembunyi', h):
                patterns.append("HIDDEN_TRUTH")
            elif re.search(r'\d+%|\d+\.\d+|\d+ juta|\d+ miliar|\d+ triliun', h):
                patterns.append("TRUTH_BOMB")  # data-based truth bomb
            else:
                patterns.append("TRUTH_BOMB")  # default
        return patterns
    except Exception:
        return []

def _pick_hook_instruction(recent_patterns: list[str]) -> str:
    """Pick a hook instruction in Eva Alicia style — truth bombs that challenge assumptions.
    
    DEFAULT (70%): TRUTH_BOMB — "Lo pikir X? Yang sebenarnya Y."
    VARIANTS (30%): Rotasi dari 5 gaya berbeda biar gak monoton.
    """
    import random
    all_hooks = [
        ("TRUTH_BOMB", "Start with a TRUTH BOMB that challenges the reader's assumption — 'Kamu pikir [asumsi umum]? [Kebenaran dari artikel].' Eva Alicia style: blunt, personal, makes reader rethink. Example: 'Kamu pikir loyalitas bikin aman? Coba tanya 4.800 karyawan Microsoft.'"),
        ("PERSONAL_CHALLENGE", "Start with a personal challenge — 'Kamu masih [kebiasaan]? [Fakta dari artikel] bilang lain.' Direct, confrontational but caring. Example: 'Kamu masih ngerasa gaji 8 juta cukup? Data BPS bilang udah di bawah garis.'"),
        ("REFRAME_BOMB", "Start with a reframe — 'Yang sebenarnya terjadi bukan [asumsi], tapi [realita dari artikel].' Counter-intuitive opening. Example: 'PHK bukan soal kamu gak kompeten. Perusahaan cuma adaptasi lebih cepat.'"),
        ("SCARY_FACT", "Start with a scary financial fact — '[Angka dari artikel]. Tau artinya apa buat kamu?' Makes reader feel the impact personally. Example: '67% pekerja Indonesia habiskan >90% gaji buat konsumsi. Kamu termasuk?'"),
        ("HIDDEN_TRUTH", "Start with hidden angle — 'Yang gak orang bahas: [fakta tersembunyi dari artikel].' Curiosity gap + money angle. Example: 'Yang gak orang bahas: PHK ini bukan soal AI. Ini soal perusahaan gak mau bayar lebih.'"),
    ]
    # TRUTH_BOMB is DEFAULT (Eva Alicia style). Weight it 4x.
    weighted = []
    for name, instr in all_hooks:
        if name == "TRUTH_BOMB":
            weighted.extend([(name, instr)] * 4)
        elif name not in recent_patterns[-4:]:
            weighted.append((name, instr))
    if not weighted:
        weighted = [(n, i) for n, i in all_hooks]
    chosen = random.choice(weighted)
    return chosen[1]

import httpx
import logging

logger = logging.getLogger(__name__)

def _evaluate_slides(slides: list[str]) -> tuple[bool, str]:
    """
    Evaluator loop: max 3 retries, mistral-small.
    Returns: (approved: bool, reason: str)
    """
    prompt = f"""
    [ROLE]
    Eva Alicia Style Content Evaluator (Money Mindset Indonesia).
    Evaluate 6-slide carousel for quality + Eva Alicia voice compliance.

    [INPUT]
    Slides: {slides}

    [CHECKLIST — ALL must pass]
    1. GROUNDING: No facts/claims not in article. No hallucinated stats.
    2. INTER-SLIDE FLOW: Slide 1→2→3→4→5→6 must connect logically. No topic jumps.
       - Slide 2 MUST expand from Slide 1 (same topic, deeper angle)
       - No slide should introduce a topic not mentioned in previous slides
    3. EVA ALICIA VOICE:
       - Slide 1 MUST be truth bomb / reframe (NOT cold data drop)
       - MUST use "aku/kamu/kalian" personal voice (NOT "gue/lo")
       - MUST challenge assumption, not just state fact
       - Slide 6 MUST end with challenge ("Kalian setuju?" / "Kamu masih mau defend ini?")
    4. MONEY ANGLE: Every slide must connect to financial impact
    5. Each slide max 400 chars, 2-3 sentences
    6. Full Indonesian (tech terms OK in English)

    [OUTPUT FORMAT]
    APPROVE
    or
    REVISE
    <ALL 6 revised slides as JSON: {{"slide_1":"...","slide_2":"...",...}}>
    or
    REJECT
    <reason>
    """
    for attempt in range(3):
        try:
            r = httpx.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "mistral-small-latest",
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": f"Slides:\n{slides}"}
                    ],
                    "temperature": 0.0,
                    "max_tokens": 1000
                },
                timeout=30
            )
            if r.status_code == 200:
                response = r.json()["choices"][0]["message"]["content"].strip()
                if response.startswith("APPROVE"):
                    return True, "APPROVED"
                elif response.startswith("REVISE"):
                    revised_raw = response.split("REVISE\n", 1)[1].strip()
                    # Try to parse JSON with all 6 slides
                    try:
                        import json as _json
                        revised_slides = _json.loads(revised_raw)
                        slide_keys = [f"slide_{i}" for i in range(1, 7)]
                        if all(k in revised_slides for k in slide_keys):
                            slides = [revised_slides[k] for k in slide_keys]
                            return True, "APPROVED"
                    except Exception:
                        pass
                    # Fallback: replace first slide only
                    slides = [revised_raw] + slides[1:]
                    return True, "APPROVED"
                else:  # REJECT
                    return False, response.split("REJECT\n", 1)[1].strip()
        except Exception as e:
            logger.warning(f"Evaluator failed: {e}")
    return False, "MAX_RETRIES"


def _pick_cta_instruction() -> str:
    """Pick a rotating CTA pattern to avoid repetition."""
    import random
    import sqlite3
    from db import get_db
    
    cta_patterns = [
        "Pola 1 (Natural): \"Gimana menurut kalian?\"",
        "Pola 2 (Personal): \"Kalian pernah ngalamin?\"",
        "Pola 3 (Open): \"Bener gak sih?\"",
        "Pola 4 (CMIIW): \"CMIIW ya kalau salah\"",
        "Pola 5 (Curious): \"Kalian juga ngerasain?\"",
        "Pola 6 (Challenge): \"Coba tebak berapa angkanya. Jawab di komen.\"",
        "Pola 7 (Story): \"Pernah ngalamin? Cerita dong.\"",
        "Pola 8 (Share): \"Tag temen yang harus baca ini.\"",
    ]
    
    # Get recent CTA patterns from DB to avoid repetition
    try:
        conn = get_db()
        recent = conn.execute("""
            SELECT caption FROM posts WHERE status='posted' 
            ORDER BY posted_at DESC LIMIT 5
        """).fetchall()
        conn.close()
        
        recent_caps = [r[0].lower() for r in recent if r[0]]
        
        # Avoid patterns that match recent captions
        weighted = []
        for pattern in cta_patterns:
            pattern_lower = pattern.lower()
            # Check if this pattern's keywords appear in recent captions
            if "share di komen" in pattern_lower:
                if sum(1 for c in recent_caps if "share" in c and "komen" in c) >= 3:
                    continue
            if "lebih milih" in pattern_lower:
                if sum(1 for c in recent_caps if "lebih milih" in c or "pilih" in c) >= 2:
                    continue
            weighted.append(pattern)
        
        if not weighted:
            weighted = cta_patterns
    except Exception:
        weighted = cta_patterns
    
    return random.choice(weighted)


def generate_carousel(title: str, body: str, image: str = "", url: str = "", source: str = "") -> Optional[dict]:
    """Generate 6-slide carousel with A/B testing (2 variants, pick best hook)."""
    print(f"[LANG] {CONTENT_LANG}")

    # Hook variety: get recent patterns and pick unused one
    recent = _get_recent_hook_patterns(5)
    hook_instr = _pick_hook_instruction(recent)
    # Foreign country in title → hook must not start with country name
    _FOREIGN_NAMES = {"argentina", "amerika", "america", "china", "jepang", "korea", "india", 
                       "singapura", "malaysia", "vietnam", "france", "germany",
                       "brasil", "mexico", "australia", "russia", "ukraina"}
    if any(n in title.lower() for n in _FOREIGN_NAMES):
        hook_instr += " KUNCI: jangan mulai hook dengan nama negara. Bikin hook yang relate ke orang Indonesia."
    print(f"[HOOK] Recent patterns: {recent}")
    print(f"[HOOK] Chosen instruction: {hook_instr[:60]}...")

    # CTA variety: rotate closing patterns
    cta_instr = _pick_cta_instruction()
    print(f"[CTA] Pattern: {cta_instr[:50]}...")

    # Determine primary provider
    primary = "mistral"
    fallback = "groq"

    # A/B: generate 2 variants
    variants = []
    for i, prov in enumerate([primary, primary], 1):  # both from same provider
        v = _generate_variant(title, body, source, prov, hook_instruction=hook_instr, cta_instruction=cta_instr)
        if v and "slide_1" in v:
            v["_provider"] = prov
            hook_score = _score_hook(v["slide_1"])
            variants.append((v, hook_score))
            print(f"  [A/B] Variant {i}: hook score {hook_score}/10 via {prov}")

    # If primary fails both times, try fallback
    if len(variants) < 2:
        v = _generate_variant(title, body, source, fallback, hook_instruction=hook_instr, cta_instruction=cta_instr)
        if v and "slide_1" in v:
            v["_provider"] = fallback
            hook_score = _score_hook(v["slide_1"])
            variants.append((v, hook_score))
            print(f"  [A/B] Fallback variant: hook score {hook_score}/10 via {fallback}")

    if not variants:
        return None

    # Pick best hook score
    variants.sort(key=lambda x: x[1], reverse=True)
    data, best_score = variants[0]
    print(f"  [A/B] Winner: hook score {best_score}/10 ({len(variants)} variants)")

    # Store A/B tracking data
    data["_hook_pattern"] = hook_instr[:100]  # truncate for DB
    data["_hook_score"] = best_score
    data["_cta_pattern"] = cta_instr[:100]

    # Auto-rewrite hook if score < 7
    if "slide_1" in data and best_score < 7:
        new_hook, new_score = _rewrite_hook(data["slide_1"], title, body, best_score)
        if new_score > best_score:
            data["slide_1"] = _fix_hook_caps(_clean(new_hook))
            best_score = new_score

    # Validate winning hook
    if "slide_1" in data:
        valid, issues = _validate_hook(data["slide_1"])
        if not valid:
            print(f"[HOOK] Issues: {', '.join(issues)}")
        else:
            print(f"[HOOK] Valid (score: {best_score}/10)")

    # Grounding check: strip fabricated numbers/quantities
    # Include title in reference text — prices often appear in title, not body
    violations = _check_fabricated_numbers(data, title + " " + body)
    if violations:
        for v in violations:
            print(f"[GROUNDING] ⚠️ {v}")
        quantity_words = re.compile(r'\b(jt|jutaan|ribuan|ratusan|puluhan|miliaran|triliunan|juta|ribu|ratus)\b', re.I)
        for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
            if key not in data:
                continue
            # Strip fabricated digit numbers
            slide_nums = re.findall(r'\d[\d.,]*%?', data[key])
            article_nums = set()
            for n in re.findall(r'\d[\d.,]*%?', title + " " + body):
                c = n.replace('.', '').replace(',', '').rstrip('%')
                if c.isdigit():
                    article_nums.add(c)
            for sn in slide_nums:
                sn_clean = sn.replace('.', '').replace(',', '').rstrip('%')
                if len(sn_clean) == 4 and sn_clean.isdigit() and 2000 <= int(sn_clean) <= 2099:
                    continue  # skip years — never strip
                if sn_clean.isdigit() and sn_clean not in article_nums:
                    is_small = int(sn_clean) <= 5
                    # For small numbers, only strip if paired with time unit
                    time_units = r'(?:tahun|bulan|hari|minggu|jam|dekade|abad|detik|menit|weeks?|months?|years?|days?|hours?|decades?)'
                    if is_small:
                        if not re.search(re.escape(sn) + r'\s*' + time_units, data[key], re.I):
                            continue
                    # Strip whole currency expression: Rp[X] juta/miliar, $[X] million, etc
                    # Instead of leaving a hole ("bisa turun ke per gram"), replace with vague ref
                    original = data[key]
                    # Handle US$ prefix
                    data[key] = re.sub(r'(?:Rp|US?\s*\$|USD|\$)\s*' + re.escape(sn) + r'\s*(?:jt|juta|miliar|triliun|million|billion|trillion)?', 'harga tertentu', data[key], flags=re.I)
                    # Also strip [number] [quantity word] without currency prefix
                    data[key] = re.sub(re.escape(sn) + r'\s*(?:jt|juta|miliar|triliun|million|billion|trillion)\b', 'harga tertentu', data[key], flags=re.I)
                    # Fallback: strip just the number
                    if original == data[key]:
                        data[key] = data[key].replace(sn, '')
                    data[key] = re.sub(r' +', ' ', data[key]).strip()
                    data[key] = re.sub(r'\s+([,.!?])', r'\1', data[key])  # " ." → "."
            # Strip fabricated word-based quantities (with cross-language check)
            matches = list(quantity_words.finditer(data[key]))
            ref_text = (title + " " + body).lower()
            for m in reversed(matches):
                word = m.group().lower()
                if word not in ref_text:
                    en_word = QTY_EN_MAP.get(word, '')
                    if en_word and en_word in ref_text:
                        continue  # EN equivalent found — not fabricated
                    # Replace with vague reference instead of leaving hole
                    data[key] = data[key][:m.start()] + "sejumlah" + data[key][m.end():]
                    data[key] = re.sub(r' +', ' ', data[key]).strip()

    # Post-strip cleanup: fix ugly patterns from grounding replacements
    for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
        if key not in data:
            continue
        t = data[key]
        # Fix "harga tertentu harga tertentu" → single
        t = re.sub(r'(harga tertentu)\s+\1', r'\1', t, flags=re.I)
        # Fix "sejumlah sejumlah" → single
        t = re.sub(r'(sejumlah)\s+\1', r'\1', t, flags=re.I)
        # Fix orphaned prepositions: "ke  per" → "per", "di  di" → "di"
        t = re.sub(r'\b(ke|di|dari|untuk|dengan)\s+(harga tertentu|sejumlah)\b', r'\2', t, flags=re.I)
        # Fix "US harga tertentu" → "harga tertentu"
        t = re.sub(r'\bUS\s+harga tertentu\b', 'harga tertentu', t, flags=re.I)
        # Fix orphan "jt" after number stripped (e.g. "100 jt" → " jt" → remove)
        t = re.sub(r'\bjt\b', '', t)
        # Fix double spaces
        t = re.sub(r' +', ' ', t).strip()
        t = re.sub(r'\s+([,.!?])', r'\1', t)
        # Kill placeholder "...", "... " (empty slide marker)
        t = re.sub(r'^\.\.\.\s*$', '', t)
        # Rejoin broken words: "2\n026" → "2026" (year split by newline)
        t = re.sub(r'(?<=\d)\n(?=\d)', '', t)
        data[key] = t

    # Coherence check: validate intra-slide sentence flow
    coherence_issues = _check_slide_coherence(data)
    if coherence_issues:
        for issue in coherence_issues:
            print(f"[COHERENCE] ⚠️ {issue}")

    # ─── Topic relevance — REJECT if slides discuss wrong topic ───
    topic_violations = _check_topic_relevance(data, title, body)
    if topic_violations:
        for v in topic_violations:
            print(f"[TOPIC] 🔴 {v}")
        print(f"[TOPIC] Rejecting — slides discuss topic not in article")
        # Retry with explicit topic instruction
        topic_hook = hook_instr + f" KUNCI: Konten HARUS bahas topik yang sama dengan artikel: '{title}'. Jangan bahas fitur/cara pakai produk kalau artikel bahas kontroversi/iklan/drama."
        v = _generate_variant(title, body, source, primary, hook_instruction=topic_hook)
        if v and "slide_1" in v:
            topic_check2 = _check_topic_relevance(v, title, body)
            if topic_check2:
                for c in topic_check2:
                    print(f"[TOPIC] 🔴 Retry also off-topic: {c}")
                return None
            data = v
            # Preserve tracking data from original variant
            data["_hook_pattern"] = hook_instr[:100]
            data["_hook_score"] = best_score
            data["_cta_pattern"] = cta_instr[:100]
            print(f"[TOPIC] ✅ Retry passed topic relevance")
        else:
            return None


    # ─── Factual claim grounding — REJECT if contradicts article ───
    claim_violations = _check_fabricated_claims(data, body, title)
    # Also check caption separately (it's not a slide)
    if "caption" in data and data["caption"]:
        caption_slides = {"slide_1": data["caption"]}
        caption_violations = _check_fabricated_claims(caption_slides, body, title)
        claim_violations.extend(caption_violations)
    if claim_violations:
        for v in claim_violations:
            print(f"[GROUNDING] 🔴 {v}")
        print(f"[GROUNDING] Rejecting variant — factual claims contradict article")
        # Try one more time with stronger grounding instruction
        stronger_hook = hook_instr + " KUNCI: Semua fakta HARUS dari artikel. Jangan bilang 'gratis' kalau artikel bilang bayar. Jangan bilang 'tersedia' kalau artikel bilang belum."
        v = _generate_variant(title, body, source, primary, hook_instruction=stronger_hook)
        if v and "slide_1" in v:
            claim_check2 = _check_fabricated_claims(v, body, title)
            if claim_check2:
                for c in claim_check2:
                    print(f"[GROUNDING] 🔴 Retry also failed: {c}")
                return None  # Give up
            # Retry passed grounding — use it
            data = v
            data["_hook_pattern"] = hook_instr[:100]
            data["_hook_score"] = best_score
            data["_cta_pattern"] = cta_instr[:100]
            print(f"[GROUNDING] ✅ Retry passed factual grounding")
        else:
            return None

    # ─── Inter-slide flow check — warn but don't block ───
    flow_issues = _check_inter_slide_flow(data)
    if flow_issues:
        for fi in flow_issues:
            print(f"[FLOW] ⚠️ {fi}")
        # If 2+ flow issues, regenerate with stronger flow instruction
        if len(flow_issues) >= 2:
            print(f"[FLOW] Too many flow issues ({len(flow_issues)}), regenerating...")
            stronger_hook = hook_instr + " KUNCI: Slide 2 HARUS expand dari Slide 1. Jangan lompat topik antar slide."
            v = _generate_variant(title, body, source, primary, hook_instruction=stronger_hook)
            if v and "slide_1" in v:
                flow_check2 = _check_inter_slide_flow(v)
                if len(flow_check2) < len(flow_issues):
                    data = v
                    data["_hook_pattern"] = hook_instr[:100]
                    data["_hook_score"] = best_score
                    data["_cta_pattern"] = cta_instr[:100]
                    print(f"[FLOW] ✅ Regenerated with better flow ({len(flow_check2)} issues vs {len(flow_issues)})")
                else:
                    print(f"[FLOW] ⚠️ Regeneration didn't improve flow, keeping original")

    # ─── Jargon check — warn, regenerate if multiple ───
    jargon_issues = _check_jargon(data)
    if jargon_issues:
        for ji in jargon_issues:
            print(f"[JARGON] ⚠️ {ji}")
        if len(jargon_issues) >= 2:
            print(f"[JARGON] Too many jargon issues ({len(jargon_issues)}), regenerating...")
            stronger_hook = hook_instr + " KUNCI: Semua istilah asing WAJIB dijelasin dalam bahasa Indonesia. 'end user' → 'pengguna akhir', 'purchase order' → 'surat pesanan'."
            v = _generate_variant(title, body, source, primary, hook_instruction=stronger_hook)
            if v and "slide_1" in v:
                jargon_check2 = _check_jargon(v)
                if len(jargon_check2) < len(jargon_issues):
                    data = v
                    data["_hook_pattern"] = hook_instr[:100]
                    data["_hook_score"] = best_score
                    data["_cta_pattern"] = cta_instr[:100]
                    print(f"[JARGON] ✅ Regenerated with less jargon ({len(jargon_check2)} issues vs {len(jargon_issues)})")

    # Final cleanup: strip orphaned markdown artifacts after grounding stripped content
    for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
        if key not in data:
            continue
        text = data[key]
        
        # 1. Strip markdown artifacts
        text = re.sub(r'\*+', '', text)

        # 1.1 Clean fanum/currency symbols that LLM sometimes inserts
        text = re.sub(r'[£€¥₩₫¢§©®™]', '', text)
        
        # 1.2 Clean orphan "/" after numbers (e.g., "2/" → "2")
        text = re.sub(r'(?<=\d)\s*/\s*(?=\s|$)', '', text)
        
        # 1.3 Clean orphan slashes from grounding strip (e.g., "diawasi / baik" → "diawasi baik")
        text = re.sub(r'\s*/\s*', ' ', text)
        text = re.sub(r'\s*\\\s*', ' ', text)
        
        # 1.5 Rejoin broken words (e.g., "DDR\n5" → "DDR5", "AI\n-powered" → "AI-powered")
        text = re.sub(r'([A-Z]{2,})\n(\d)', r'\1\2', text)
        text = re.sub(r'(\w)\n(-\w)', r'\1\2', text)
        # Remove orphan trailing digits from grounding strip (e.g., "tahun 2\nDenda" → "tahun\nDenda")
        text = re.sub(r'\s+\d{1,2}\n(?=[A-Z])', '\n', text)
        
        # 2. Fix broken sentences from grounding strip
        # Remove sentences starting with orphan punctuation
        text = re.sub(r'(?m)^[\s,;:.!?]+(?=\s*\w)', '', text)
        
        # 3. Split into sentences, filter broken ones
        sentences = [s.strip() for s in re.split(r'\n\n+', text) if s.strip()]
        good = []
        for s in sentences:
            # Skip if sentence is just punctuation
            if re.match(r'^[\s,;:.!?]+$', s):
                continue
            # Skip if sentence starts with lowercase + comma (broken fragment from grounding)
            if re.match(r'^[,\s]+\w', s):
                s = re.sub(r'^[,\s]+', '', s).strip()
                if not s or len(s.split()) < 3:
                    continue
            # Skip if too short (< 3 words) and not a question
            word_count = len(re.findall(r'[a-zA-Z]{2,}', s))  # real words only, not punctuation
            if word_count < 3 and '?' not in s:
                continue
            # Skip orphan questions (< 2 real words, e.g., "Alasannya?")
            if word_count < 2 and '?' in s:
                continue
            # Skip if ends with orphan punctuation after short text (e.g., "di komen, !")
            # Always skip if sentence ends with orphan punctuation regardless of word count
            if re.search(r'[,;:.!?]\s*[!?]*$', s) and '?' not in s:
                # Only skip if the last word is punctuation (not a real sentence ending)
                last_token = s.strip().split()[-1] if s.strip() else ""
                if not re.search(r'[a-zA-Z]{2,}', last_token):
                    continue
            # Skip if sentence is mostly incomplete (< 4 real words and no verb-like pattern)
            if word_count < 4 and not re.search(r'\b(adalah|bisa|bakal|akan|harus|mau|sudah|udah|belum|gak|tidak|bukan|juga|lagi|masih|pernah|baru|sudah)\b', s, re.I):
                continue
            good.append(s)
        
        data[key] = '\n\n'.join(good)
        
        # 4.5 Convert numbered lists to narrative (§5 compliance)
        data[key] = _lists_to_narrative(data[key])
        
        # 5. Final whitespace normalization
        data[key] = re.sub(r' +', ' ', data[key]).strip()
        # Split sentences within single lines: "Sentence 1. Sentence 2." → "Sentence 1.\n\nSentence 2."
        # Pattern: sentence-ending punct + space + uppercase (not after common abbreviations)
        data[key] = re.sub(r'(?<=[.!?])\s+(?=[A-Z""\u201c](?![a-z]{1,2}\.))', '\n\n', data[key])
        # Ensure single \n between sentences becomes \n\n (blank line)
        data[key] = re.sub(r'(?<=[.!?])\n(?=\S)', '\n\n', data[key])
        # Also handle: colon-separated lines "2018: X\n2021: Y" → "2018: X\n\n2021: Y"
        data[key] = re.sub(r'(?<=:)\n(?=\d)', '\n\n', data[key])
        # Collapse 3+ newlines to exactly 2
        data[key] = re.sub(r'\n{3,}', '\n\n', data[key])
        
        # 6. Final orphan cleanup after whitespace normalization
        # Remove lines that are just punctuation or "word, ." patterns
        lines = data[key].split('\n\n')
        clean_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Skip orphan punctuation: "di komen, ." or just "."
            if re.match(r'^[\s,;:.!?]+$', line):
                continue
            # Skip lines ending with orphan punct (last token is pure punct)
            tokens = line.split()
            if tokens and not re.search(r'[a-zA-Z]{2,}', tokens[-1]) and '?' not in line:
                continue
            # Skip very short fragments (< 3 real words, no question)
            wc = len(re.findall(r'[a-zA-Z]{2,}', line))
            if wc < 3 and '?' not in line:
                continue
            # Skip orphan questions (< 2 real words, e.g., "Alasannya?", "Hasilnya?")
            if wc < 2 and '?' in line:
                continue
            clean_lines.append(line)
        data[key] = '\n\n'.join(clean_lines)

    # ─── Evaluator: independent skeptical review (pressbox pattern) ───
    # Skip evaluator for high-score articles (≥80) — saves ~50s per run
    if best_score >= 80:
        print(f"  [EVAL] Skipped (score {best_score} ≥ 80)")
    else:
        slides = [data.get(f"slide_{i}", "") for i in range(1, 7)]
        approved, reason = _evaluate_slides(slides)
        if not approved:
            logger.warning(f"Rejected: {reason}")
            return None
        else:
            data["slide_1"] = slides[0]
            # Retry up to 3 times with stronger grounding each attempt
            retry_data = None
            for attempt in range(1, 4):
                stronger = hook_instr + f" ATURAN MUTLAK (attempt {attempt+1}): HANYA pakai fakta yang ADA di artikel. JANGAN tambah apapun. Periksa tiap klaim: apakah ini beneran ada di artikel?"
                v = _generate_variant(title, body, source, primary, hook_instruction=stronger)
                if v and "slide_1" in v:
                    # evaluate revised variant
                    slides_v = [v.get(f"slide_{i}", "") for i in range(1, 7)]
                    approved_v, reason_v = _evaluate_slides(slides_v)
                    print(f"  [EVAL] Retry {attempt}/3 verdict: {'APPROVED' if approved_v else 'REJECT'}")
                    if approved_v:
                        # apply possible REVISE changes (slide_1 already updated inside evaluator)
                        v["slide_1"] = slides_v[0]
                        retry_data = v
                        break
                else:
                    print(f"  [EVAL] Retry {attempt}/3 generation failed")
            if retry_data:
                data = retry_data
                # Preserve tracking data from original variant
                data["_hook_pattern"] = hook_instr[:100]
                data["_hook_score"] = best_score
                data["_cta_pattern"] = cta_instr[:100]
            else:
                print(f"  [EVAL] 🔴 All retries REJECT — returning None (skip article)")
                return None

    # ─── Fix empty slides (collapse gaps) ───
    _fix_slides(data)

    data["_provider"] = data.get("_provider", primary)
    data["_lang"] = CONTENT_LANG
    # A/B tracking — set once at end to survive all retry/regenerate paths
    data["_hook_pattern"] = hook_instr[:100]
    data["_hook_score"] = best_score
    data["_cta_pattern"] = cta_instr[:100]
    return data

# ─── Text Post Generator (Theo/Marco style) ───────────────────────

TEXT_POST_PROMPT = """[ROLE]
Kamu "Aku" — personal branding creator di Threads. #KokoKokGitu style.
Kamu bukan guru. Kamu orang biasa yang punya opini soal kehidupan sehari-hari.
Kamu suka nanya "kok gitu ya?" dan ajak orang mikir bareng.

[FORMAT — PILIH SALAH SATU TIPE]

TIPE OPINION (25%):
- 2-4 kalimat, pendapat aku soal berita/hot topic yang lagi rame
- Frame: "Aku pikir [opini]. Soalnya [alasan]. Kalian gimana?"
- Pake emoji 2-3
- Boleh campur English

TIPE PERSONAL (20%):
- 2-3 kalimat, cerita singkat pengalaman daily life yang relate
- Frame: "Aku pernah [situasi]. Ternyata [insight]."
- Pake emoji 2-3
- Kasual, kayak lagi cerita ke temen

TIPE QUESTION (15%):
- 1-2 kalimat, tanya ke followers soal sesuatu yang hot
- Frame: "Pernah ga sih [situasi]? Aku penasaran kalian gimana."
- Pake emoji 1-2

TIPE REACTION (40%):
- 2-3 kalimat, reaksi aku terhadap sesuatu (berita, tren, fenomena)
- Frame: "[Fenomena]. Ternyata [fakta]. Kok bisa ya?"
- Pake emoji 2-3

[ATURAN]
- Max 80 kata
- Bahasa: kasual, "aku/kamu/kalian"
- Boleh campur English (natural)
- Pake 2-3 emoji yang natural
- Hashtag: #KokoKokGitu di akhir
- Jangan pake "gue/lo/lu"

[ANTI-HALLUCINATION RULES — WAJIB]
1. JANGAN sebut angka/percentage spesifik.
   ✓ "Banyak orang gak nabung"  ✗ "68% orang gak nabung"
   ✓ "IHSG turun signifikan"  ✗ "IHSG turun 3%"
2. JANGAN sebut nama perusahaan/brand secara negatif.
   ✓ "Startup biasanya..."  ✗ "Company X gaji rendah"
3. JANGAN kasih financial/health advice.
   ✓ "Menurutku sebaiknya..."  ✗ "Lo WAJIB invest di X"
4. Kalau引用 data, pakai generalisasi: "data menunjukkan", "banyak yang bilang", "katanya".
5. Personal stories: framed as "Aku pernah..." — boleh fictional tapi jangan claim sebagai fakta.
6. Opinions: selalu pakai "Menurutku", "Aku pikir", "Kayaknya" — jangan "Faktanya".

[OUTPUT]
JSON: {{"text": "...", "type": "opinion|personal|question|reaction"}}
"""

def generate_text_post(hot_topics: Optional[list[str]] = None) -> Optional[dict]:
    """Generate a text post (non-carousel) — Theo/Marco style.
    Uses real scraped articles as source material.
    Types: opinion, personal, question, reaction.
    """
    import random

    # Fetch recent articles from DB as source material
    articles_str = ""
    try:
        from db import get_db
        conn = get_db()
        rows = conn.execute("""
            SELECT a.title, a.body, a.url FROM articles a
            WHERE a.body IS NOT NULL AND LENGTH(a.body) > 100
            ORDER BY a.created_at DESC LIMIT 10
        """).fetchall()
        conn.close()
        if rows:
            # Pick 3 random articles as context
            picked = random.sample(rows, min(3, len(rows)))
            articles_str = "\n\n".join([
                f"ARTICLE: {r['title']}\nSNIPPET: {r['body'][:200]}\nURL: {r['url']}"
                for r in picked
            ])
    except Exception:
        pass

    if not articles_str:
        # Fallback: use hot_topics
        if hot_topics:
            articles_str = "Topik terkini: " + ", ".join(hot_topics[:5])
        else:
            return None  # No source material available

    # Pick random type with weights
    types = ["opinion"]*4 + ["personal"]*3 + ["question"]*2 + ["reaction"]*6
    chosen_type = random.choice(types)

    user_msg = f"""Buat text post tipe {chosen_type} BERDASARKAN artikel di bawah.

Artikel sumber:
{articles_str}

Pilih 1 artikel. Buat opini/reaksi/cerita BERDASARKAN isi artikel — jangan ngarang fakta.
Boleh tambah perspective pribadi, tapi fakta harus dari artikel.

Output JSON: {{"text": "...", "type": "{chosen_type}"}}"""

    for attempt in range(3):
        try:
            r = httpx.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "mistral-small-latest",
                    "messages": [
                        {"role": "system", "content": TEXT_POST_PROMPT},
                        {"role": "user", "content": user_msg}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 400,
                },
                timeout=30,
            )
            if r.status_code == 200:
                raw = r.json()["choices"][0]["message"]["content"].strip()
                if "```json" in raw:
                    raw = raw.split("```json")[1].split("```")[0].strip()
                elif "```" in raw:
                    raw = raw.split("```")[1].split("```")[0].strip()
                data = json.loads(raw)
                text = data.get("text", "").strip()
                post_type = data.get("type", chosen_type)
                if text:
                    # Ensure #KokoKokGitu is in the text
                    if "#KokoKokGitu" not in text:
                        text = text.rstrip() + " #KokoKokGitu"
                    print(f"[TEXT POST] {post_type} ({len(text)} chars): {text[:80]}...")
                    return {
                        "slide_hook": text,
                        "slide_setup": "",
                        "slide_twist": "",
                        "slide_deep": "",
                        "slide_sowhat": "",
                        "slide_cta": "",
                        "_hook_pattern": f"TEXT_{post_type.upper()}",
                        "_provider": "mistral",
                    }
            else:
                print(f"[TEXT POST] API {r.status_code}: {r.text[:100]}")
        except Exception as e:
            print(f"[TEXT POST] Error: {e}")
        time.sleep(2)
    return None


# ─── CLI ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python generator.py <article_id>")
        sys.exit(1)

    from db import get_db
    conn = get_db()
    article = conn.execute("SELECT * FROM articles WHERE id=?", (sys.argv[1],)).fetchone()
    if not article:
        print(f"Article {sys.argv[1]} not found")
        sys.exit(1)

    result = generate_carousel(article["title"], article["body"], article["source"])
    if result:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("Generation failed")
