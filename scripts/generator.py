#!/usr/bin/env python3
"""generator.py — Generate 6-slide carousel via Mistral (primary) / Groq (fallback).
Switch language with CONTENT_LANG=en|id in .env
"""
import httpx
import json
import re
from typing import Optional

import os

GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
MISTRAL_KEY = os.environ.get("MISTRAL_API_KEY", "")
CONTENT_LANG = os.environ.get("CONTENT_LANG", "en").lower()

# ─── Prompts ──────────────────────────────────────────────────────

PROMPT_EN = """[ROLE]
Act as "Bro", a 27-year-old Tech content creator on Threads targeting ambitious young professionals globally. You are a conversational storyteller, not a news anchor. You speak directly (I/you) in casual English, transforming tech/AI/career news into relatable life lessons and actionable advice.

[TASK]
Transform the provided article into a 6-slide Threads narrative. Extract the most counterintuitive takes, actionable tips, and real numbers from the text. Frame the information around how it directly impacts the reader's daily life, career, or productivity (e.g., turn "CEO resigns" into "Signs you should quit your job").

[OUTPUT]
Format strictly as a flat JSON with keys "slide_1" to "slide_6", "caption", "hashtags". Write in prose only (no bullets). Vary rhythm between short punchy sentences and longer ones.

- slide_1 (Hook, under 30 words, MAX 2 sentences): Hit hard with a shocking fact/number from the article. Capitalize exactly ONE word for emphasis. Vary hook style between posts:
  1. REALIZATION: "I just realized..."
  2. OPINION: "Honestly, I'm [emotion] about..."
  3. QUESTION: "Did you know...?"
  4. QUOTE: "[Name] said: '[insight]'"
  5. CONTRAST: "[Expectation]... But reality?"
  6. DATA DROP: "[Number] people [context]. Are you one of them?"

- slide_2 (Setup, 40-60 words, MAX 3 sentences): Bridge to the real problem using everyday analogies (9-5 grind, broke college student, hustle culture). Reader should think: "Yeah, I deal with this too"

- slide_3 (Twist, 40-60 words, MAX 3 sentences): Reveal a shocking root cause or fact. Explain simply without jargon.

- slide_4 (Tips, 40-60 words, MAX 3 sentences): Provide actionable advice derived directly from the article.

- slide_5 (Lesson, 30-50 words, MAX 3 sentences): Deliver a relatable mindset shift or punchline. One sentence that makes people share.

- slide_6 (CTA, 30-40 words, MAX 3 sentences): End with one of these to force comments:
  1. PROVOCATIVE: "Is [X]? Or [Y]?"
  2. PERSONAL: "Have you ever [action]? Drop it in the comments."
  3. DEBATE: "Hot take: [opinion]. Agree or disagree?"
  4. RANKING: "What matters more: [A] or [B]?"
  5. CHALLENGE: "Try [action] for a week. Let me know how it goes."

caption: 1-2 sentence summary + hashtags

[CONSTRAINTS]
- MUST NOT use emojis/emoticons.
- MUST NOT use em-dashes (—) or en-dashes (–); use commas instead.
- MUST NOT use "link in bio" or fabricated quotes ("my friend/family/coworker said" unless in article).
- MUST NOT fabricate stories, events, names, or statistics.
- MUST include specific numbers sourced directly from the article.
- MUST reject product promotions. If product launch/specs/pricing, output: {"error":"product_promo"}

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
§3  ROLE
═══════════════════════════════════════════════
Kamu "Bro" — temen tongkrongan yang suka sharing tips & tricks.
Lo tau banyak soal tech, AI, finance, dan produktivitas.
Lo BUKAN guru, BUKAN motivator, BUKAN sales.
Lo juga BUKAN jurnalis — lo orang yang abis baca berita terus nulis reaksi personalnya di Threads.
Gaya lo: organik, kasual, "mentah" (raw), blak-blakan. Anti gaya copywriting korporat yang kaku.
Lo cuma temen yang lagi ngasih tau: "Bro, gue nemu ini, nih manfaatnya buat lo."

Gaya: kasual, santai, kayak ngobrol di warung kopi.
Bahasa: SESEDERHANA mungkin. Anak kecil harus ngerti.
Jangan pernah pake kata-kata yang bikin orang mikir keras.
Kalau ada istilah teknis → jelasin pake bahasa sehari-hari.

Contoh tone:
✅ "ChatGPT bisa bikin CV lo dalam 5 menit. Gak perlu jago desain."
❌ "Leverage AI-powered tools to optimize your professional documentation workflow."

═══════════════════════════════════════════════
§3b  STORYTELLING MODE — HOW-TO STEP BY STEP
═══════════════════════════════════════════════
Konten WAJIB berupa tips/tricks/life hacks yang PRAKTIS.
Bukan berita, bukan opini, bukan motivasi.

Format: "Ini yang bisa lo lakuin" → step by step → hasilnya apa
Bayangin lo lagi ajarin temen yang GAPTEK.

Kalau artikel bahas tools/AI:
→ Cari "cara pakenya buat apa" yang relevan sama orang Indonesia
→ Kasih step konkret, bukan saran umum

Kalau artikel bahas finance/investasi:
→ Cari "apa yang bisa lo mulai sekarang"
→ Kasih angka konkret kalau ada

Kalau artikel bahas cybersecurity/scam:
→ Kasih tau "gimana cara hindarinnya" step by step

JANGAN pernah generate konten yang cuma "berita doang" tanpa actionable tips.
Kalau artikel gak ada tips/practical angle → pilih angle yang paling bisa dijadiin tips.

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

SLIDE 1 — THE HOOK (Stop the Scroll)
  • TEPAT 2 kalimat, <20 kata (bukan 30 — makin pendek makin nendang)
  • WAJIB mulai dari FAKTA/ANGKA yang ADA di artikel, BUKAN dari emosi
  • Format: [ANGKA/FAKTA DARI ARTIKEL] + [KONSEKUENSI ATAU PERTANYAAN]
  • KAPITAL 1 kata aja (yang paling shocking)
  • DILARANG mulai dengan: "Gue [emotion]", "Lo tau gak", "Bayangin", "Tahukah kamu"
  • Contoh benar: "Harga DDR5 naik 40%, tapi Meta malah pake DDR4 lawas."
  • Contoh salah: "Gue GILA, ternyata Meta pake RAM lama!"

{hook_instruction}

SLIDE 2 — THE PROBLEM (Kenapa Ini Relevan)
  • MAX 3 kalimat, <40 kata
  • Validasi masalah/kebutuhan pembaca + data pendukung dari artikel
  • Ini alasan KENAPA mereka harus lanjut baca, bukan penjelasan cara

SLIDE 3 — TIP 1 (Tips/Trick Pertama)
  • MAX 3 kalimat, <40 kata
  • Tips pertama yang PALING actionable — bisa langsung dipraktekkan
  • Spesifik: apa yang diklik/dibuka/ditulis/diinstall
  • Grounded di fakta artikel, bukan karangan

SLIDE 4 — TIP 2 (Tips/Trick Kedua)
  • MAX 3 kalimat, <40 kata
  • Tips kedua — angle berbeda dari Tip 1
  • Kalau ada data/angka pendukung dari artikel, tambahin

SLIDE 5 — TIP 3 (Tips/Trick Ketiga)
  • MAX 3 kalimat, <40 kata
  • Tips ketiga — yang paling surprising atau out of the box
  • Boleh kasih 2 sisi (pro & con) biar orang COMMENT: setuju atau enggak
  • Contoh: "Meta buktiin: kadang solusi paling murah itu yang udah ada di depan mata. Tapi risikonya, lo ketinggalan rally AI kalau tren balik."

SLIDE 6 — THE CTA (Closing)
  • MAX 2 kalimat, <30 kata
  • Ajakan action yang santai, bukan closing formal
  • Contoh pola: "Udah pernah coba [cara ini]? Share pengalaman lo di komen" atau "Lo lebih milih cara [A] apa [B]? Bilang di komen"
  • Boleh reflektif tapi tetep ngundang orang buat balas, bukan sekadar penutup

═══════════════════════════════════════════════
§7b  INTRA-SLIDE COHERENCE
═══════════════════════════════════════════════
Dalam 1 slide, kalimat harus NYAMBUNG:
• 2+ angka/statistik → HARUS ada kata penghubung (tapi, namun, sedangkan, hasilnya, padahal)
• Kalimat terakhir gak boleh ngulang ide kalimat pertama
• Setiap kalimat punya hubungan logis ke kalimat sebelumnya

═══════════════════════════════════════════════
§7c  VIRAL CRITERIA (WAJIB PER SLIDE)
═══════════════════════════════════════════════
Tiap slide HARUS hit minimal 1 kriteria viral di bawah ini:

1. PRO & CON — kasih 2 sisi: "Ini bagus karena... tapi risikonya..."
2. RELATABLE — "Lo yang jualan di Shopee pasti ngalamin..."
3. FAMOUS FIGURE — sebut brand/personaliti besar: ChatGPT, Elon Musk, Apple, Meta
4. SURPRISING FACT — fakta yang bikin orang "anjir, seriusan?"

Slide 1: WAJIB surprising fact ATAU famous figure
Slide 3-5 (Tips): masing-masing WAJIB hit ≥1 kriteria
Slide 5: WAJIB pro & con (2 sisi, bikin orang diskusi)
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
□ Ada actionable tips di konten ini?
□ Slide 1 mulai dari FAKTA/ANGKA, bukan emosi?
□ Slide 5 ada 2 sisi (pro & con)?
□ Tiap slide hit ≥1 viral criteria (§7c)?
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
  Zero emoji. Zero hashtags.
  Contoh: "DDR5 harganya 2x lipat DDR4. Tapi Meta malah pake yang lama di server AI. Lo masih pikir baru = lebih baik?"
Field "hashtags": isi maksimal 1 hashtag saja (bukan list).
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
QTY_EN_MAP = {'juta': 'million', 'miliar': 'billion', 'triliun': 'trillion', 'ribu': 'thousand', 'ratus': 'hundred', 'jutaan': 'millions', 'miliaran': 'billions', 'triliunan': 'trillions', 'ribuan': 'thousands', 'ratusan': 'hundreds', 'puluhan': 'tens'}

BANNED_ID = [
    r'\bgeleng[- ]geleng\b', r'\bgaruk kepala\b', r'\bkayak dari masa depan\b',
    r'\bkebayang gak\b', r'\byang bener aja\b',
    r'\bgokil\b', r'\bmantap jiwa\b', r'\bsultan\b',
    r'\bauto\b', r'\bskuy\b', r'\bcuy\b',
    r'\bini gak nyangka\b', r'\bsurprise banget\b',
    r'\bhebat\b', r'\bkeren banget\b',
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
        "Write as BREAKING NEWS — first paragraph is the most shocking fact.",
        "Write as EXCLUSIVE INSIDER — what most people missed about this story.",
        "Write as INDUSTRY ANALYSIS — why this matters for the next 5 years.",
    ],
    "product": [
        "Write as FIRST IMPRESSIONS — what it feels like to use this.",
        "Write as COMPARISON — how this stacks up against the competition.",
        "Write as EARLY ADOPTER — why you should care before everyone else does.",
    ],
    "impact": [
        "Write as CAREER ADVISORY — how this affects your job/skills.",
        "Write as INVESTOR THESIS — what this means for money/markets.",
        "Write as FUTURE PREDICTION — where this leads in 2-3 years.",
    ],
    "controversy": [
        "Write as DEVIL'S ADVOCATE — the contrarian take nobody's saying.",
        "Write as ETHICAL DEBATE — both sides of the argument.",
        "Write as PATTERN RECOGNITION — what history tells us about this.",
    ],
    "drama": [
        "Write as DRAMA EXTRACTOR — take the viral/hot story, extract 3 actionable tips that readers can learn from it. The drama is the hook, tips are the value.",
        "Write as LESSON LEARNER — what can orang biasa learn from this drama? 3 practical takeaways, grounded in facts.",
        "Write as DRAMA ANALYST — break down WHY this drama happened, then give 3 tips to avoid similar situation.",
    ],
}

def _classify_article(title: str, body: str) -> str:
    """Classify article type for prompt rotation. Drama takes priority."""
    text = (title + " " + body[:500]).lower()

    # Drama detection (highest priority — viral/hot stories)
    drama_signals = [
        "viral", "heboh", "kontroversial", "korban", "banting setir",
        "nganggur", "dipecat", "resign", "phk", "gugat", "skandal",
        "bongkar", "terungkap", "ternyata", "marah", "protes", "boikot",
        "gagal", "kolaps", "anjlok", "didenda", "dituntut", "ditangkap",
        "layoff", "scandal", "controversy", "exposed", "shutdown",
        "fired", "bankrupt", "crisis",
    ]
    drama_count = sum(1 for w in drama_signals if re.search(r'\b' + re.escape(w) + r'\b', text))
    if drama_count >= 2:  # at least 2 drama signals = drama mode
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

def _build_user_msg(title: str, body: str, source: str = "", hook_instruction: str = "") -> str:
    article_type = _classify_article(title, body)
    angle = _get_angle(article_type)
    hook_part = f"\nHOOK STYLE: {hook_instruction}" if hook_instruction else ""
    return f"ANGLE: {angle}{hook_part}\n\nARTICLE: {body[:4000]}\nSOURCE: {title}"

def _call_mistral(title: str, body: str, source: str = "", hook_instruction: str = "") -> Optional[str]:
    prompt = _get_prompt()
    try:
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
            json={"model": "mistral-large-latest",
                  "messages": [{"role": "system", "content": prompt},
                               {"role": "user", "content": _build_user_msg(title, body, source, hook_instruction)}],
                  "temperature": 0.3, "max_tokens": 2000},
            timeout=120)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Mistral error: {e}")
    return None

def _call_groq(title: str, body: str, source: str = "", hook_instruction: str = "") -> Optional[str]:
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
                               {"role": "user", "content": _build_user_msg(title, body, source, hook_instruction)}],
                  "temperature": 0.3, "max_tokens": 2000},
            timeout=120)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Groq error: {e}")
    return None

# ─── Post-processing ─────────────────────────────────────────────

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
                      "lo", "gue", "kita", "lu", "kamu", "elo"}
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
    """Score a hook 0-10. v2 weights: length=2, number=2, rest=1."""
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

    # 5. Personal pronouns (ID: gue/lo/kita, EN: you/your/i/we)
    personal = {'you', 'your', 'i', 'we', 'our', 'my', 'gue', 'lo', 'kita', 'lu'}
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

    return score


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
- Capitalize ONE key word
- End with ? or ! if it's a question/exclamation
- Mix Indonesian-English naturally
- Sound like a real person texting, not an AI
- NEVER start with "gue [emotion]" (gue gila, gue kaget, etc.) — meaningless filler
- Format: [ANGKA/FAKTA] + [KONSEKUENSI ATAU PERTANYAAN]
- If no numbers in excerpt, start with the most surprising fact instead

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
                  "temperature": 0.4, "max_tokens": 100},
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

def _generate_variant(title: str, body: str, source: str, provider: str, hook_instruction: str = "") -> Optional[dict]:
    """Generate one carousel variant. Returns parsed dict or None."""
    if provider == "mistral":
        raw = _call_mistral(title, body, source, hook_instruction)
    else:
        raw = _call_groq(title, body, source, hook_instruction)
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
    # Extract all numbers from article
    article_numbers = set(re.findall(r'\d[\d.,]*%?', article_body))
    article_nums_flat = set()
    for n in article_numbers:
        clean = n.replace('.', '').replace(',', '').rstrip('%')
        if clean.isdigit():
            article_nums_flat.add(clean)

    # Word-based quantities that imply large numbers (ID + EN equivalents)
    quantity_words = re.compile(r'\b(jutaan|ribuan|ratusan|puluhan|miliaran|triliunan|juta|ribu|ratus)\b', re.I)

    for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
        if key not in slides:
            continue
        text = slides[key]

        # Check digit numbers
        slide_numbers = re.findall(r'\d[\d.,]*%?', text)
        time_units = r'(?:tahun|bulan|hari|minggu|jam|dekade|abad|detik|menit|weeks?|months?|years?|days?|hours?|decades?|centur)'
        for sn in slide_numbers:
            sn_clean = sn.replace('.', '').replace(',', '').rstrip('%')
            if not sn_clean.isdigit():
                continue
            # Skip 4-digit numbers in year range 2000-2099 — treat as years, not quantities
            if len(sn_clean) == 4 and 2000 <= int(sn_clean) <= 2099:
                continue
            if sn_clean not in article_nums_flat:
                if int(sn_clean) <= 5:
                    pattern = re.escape(sn) + r'\s*' + time_units
                    if not re.search(pattern, text, re.I):
                        continue
                violations.append(f"{key}: number '{sn}' not in article")

        # Check word-based quantities (also check EN equivalent for cross-language articles)
        for m in quantity_words.finditer(text):
            word = m.group().lower()
            if word not in article_body.lower():
                en_word = QTY_EN_MAP.get(word, '')
                if en_word and en_word in article_body.lower():
                    continue  # EN equivalent found — not fabricated
                violations.append(f"{key}: quantity '{word}' not in article")

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
            # Fact-first pattern detection
            if re.search(r'\d+%|\d+\.\d+|\d+ juta|\d+ miliar', h):
                patterns.append("DATA_DROP")
            elif re.search(r'tapi|kenyataannya|ternyata|padahal', h):
                patterns.append("CONTRAST")
            elif h.endswith('?') and re.search(r'lo masih|lo yang|lo tau', h):
                patterns.append("QUESTION")
            elif re.search(r'\b(meta|google|apple|openai|chatgpt|shopee|tokopedia)\b', h):
                patterns.append("IMPACT")
            elif re.search(r'\b(ternyata|fakta|heran|gak nyangka)\b', h):
                patterns.append("SURPRISING")
            elif re.search(r'\b(harga|rp|juta|bayar|gratis|murah)\b', h):
                patterns.append("MONEY")
            else:
                patterns.append("OTHER")
        return patterns
    except Exception:
        return []

def _pick_hook_instruction(recent_patterns: list[str]) -> str:
    """Pick a hook instruction that avoids recent patterns. All fact-first."""
    import random
    all_hooks = [
        ("DATA_DROP", "Start with a NUMBER from the article + consequence — 'X% [fakta], [dampaknya]. Lo termasuk?'"),
        ("CONTRAST", "Start with contradiction from article — '[Ekspektasi]... Tapi [kenyataan dari artikel]?'"),
        ("QUESTION", "Start with a provocation based on article fact — '[Fakta dari artikel]... Lo masih [A]?'"),
        ("IMPACT", "Start with direct impact — '[Brand/teknologi] baru [action]. Lo yang [target] kena dampaknya.'"),
        ("SURPRISING", "Start with the most surprising fact — 'Ternyata [fakta dari artikel]. [Konsekuensi]?'"),
        ("MONEY", "Start with money/numbers — 'Harga [X] [angka]. Tapi [contradiction dari artikel]. Lo tau?'"),
    ]
    # Filter out recently used patterns
    available = [(name, instr) for name, instr in all_hooks if name not in recent_patterns[-3:]]
    if not available:
        available = all_hooks  # fallback if all used
    chosen = random.choice(available)
    return chosen[1]

def generate_carousel(title: str, body: str, image: str = "", url: str = "", source: str = "") -> Optional[dict]:
    """Generate 6-slide carousel with A/B testing (2 variants, pick best hook)."""
    print(f"[LANG] {CONTENT_LANG}")

    # Hook variety: get recent patterns and pick unused one
    recent = _get_recent_hook_patterns(5)
    hook_instr = _pick_hook_instruction(recent)
    print(f"[HOOK] Recent patterns: {recent}")
    print(f"[HOOK] Chosen instruction: {hook_instr[:60]}...")

    # Determine primary provider
    primary = "mistral"
    fallback = "groq"

    # A/B: generate 2 variants
    variants = []
    for i, prov in enumerate([primary, primary], 1):  # both from same provider
        v = _generate_variant(title, body, source, prov, hook_instruction=hook_instr)
        if v and "slide_1" in v:
            v["_provider"] = prov
            hook_score = _score_hook(v["slide_1"])
            variants.append((v, hook_score))
            print(f"  [A/B] Variant {i}: hook score {hook_score}/10 via {prov}")

    # If primary fails both times, try fallback
    if len(variants) < 2:
        v = _generate_variant(title, body, source, fallback, hook_instruction=hook_instr)
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
    violations = _check_fabricated_numbers(data, body)
    if violations:
        for v in violations:
            print(f"[GROUNDING] ⚠️ {v}")
        quantity_words = re.compile(r'\b(jutaan|ribuan|ratusan|puluhan|miliaran|triliunan|juta|ribu|ratus)\b', re.I)
        for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
            if key not in data:
                continue
            # Strip fabricated digit numbers
            slide_nums = re.findall(r'\d[\d.,]*%?', data[key])
            article_nums = set()
            for n in re.findall(r'\d[\d.,]*%?', body):
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
                    data[key] = re.sub(r'(?:Rp|USD|\$)\s*' + re.escape(sn) + r'\s*(?:juta|miliar|triliun|million|billion|trillion)?', '', data[key], flags=re.I)
                    # Also strip [number] [quantity word] without currency prefix
                    data[key] = re.sub(re.escape(sn) + r'\s*(?:juta|miliar|triliun|million|billion|trillion)\b', '', data[key], flags=re.I)
                    # Fallback: strip just the number
                    data[key] = data[key].replace(sn, '')
                    data[key] = re.sub(r' +', ' ', data[key]).strip()
                    data[key] = re.sub(r'\s+([,.!?])', r'\1', data[key])  # "  ." → "."
            # Strip fabricated word-based quantities (with cross-language check)
            matches = list(quantity_words.finditer(data[key]))
            for m in reversed(matches):
                word = m.group().lower()
                if word not in body.lower():
                    en_word = QTY_EN_MAP.get(word, '')
                    if en_word and en_word in body.lower():
                        continue  # EN equivalent found — not fabricated
                    data[key] = data[key][:m.start()] + data[key][m.end():]
                    data[key] = re.sub(r' +', ' ', data[key]).strip()

    # Coherence check: validate intra-slide sentence flow
    coherence_issues = _check_slide_coherence(data)
    if coherence_issues:
        for issue in coherence_issues:
            print(f"[COHERENCE] ⚠️ {issue}")

    # Final cleanup: strip orphaned markdown artifacts after grounding stripped content
    for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
        if key not in data:
            continue
        text = data[key]
        
        # 1. Strip markdown artifacts
        text = re.sub(r'\*+', '', text)
        
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

    data["_provider"] = data.get("_provider", primary)
    data["_lang"] = CONTENT_LANG
    return data

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
