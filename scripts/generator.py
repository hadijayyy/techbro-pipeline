#!/usr/bin/env python3
"""
generator.py — 6-slide carousel via Mistral (primary) → Groq (fallback).
Post-processes output: strips markdown, banned phrases, hallucinations.
Accepts source_url and source params.
"""
import os
import re
import json
import httpx
from typing import Optional

MISTRAL_KEY = os.environ.get("MISTRAL_API_KEY", "")
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")

# BANNED PHRASES — cringe/filler + platform-specific
BANNED_PHRASES = [
    r'\bgeleng[- ]geleng\b', r'\bgaruk kepala\b', r'\bkayak dari masa depan\b',
    r'\bgila sih\b', r'\bgila banget\b', r'\bgila kan\b',
    r'\bkebayang gak\b', r'\byang bener aja\b',
    r'\btahan dulu\b', r'\bcoba tebak\b', r'\bciyus\b', r'\bmiyap\b',
    r'\bmuka masam\b', r'\bngebet\b',
    r'\blink di bio\b',  # URL sudah ada di post, gak perlu sebut
]

SYSTEM_PROMPT = """Lo "Bro" — Content Creator & Scriptwriter handal di Threads. Umur 27, ngobrolin AI tools, productivity hacks, career advice, mental health. Campur Indo-Inggris alami. Santai tapi insightful. Bukan news anchor, lo STORYTELLER yang nge-EXTRACT tips dari berita.

[MISI UTAMA]
Lo BUKAN news anchor. Lo STORYTELLER yang nge-EXTRACT tips/lessons dari artikel. Setiap artikel punya GOLD — fakta mengejutkan, advice expert, angka impactful, tren tersembunyi. Lo HARUS nemuin gold ini.

Contoh TRANSFORMASI:
- "CEO resign" → "Tanda-tanda lo harus resign dari kantor"
- "Startup bangkrut" → "Red flag startup yang bakal bangkrut"
- "Gen Z perlu 2 ijazah" → "Skill combo yang bikin lo gak bisa diganti AI"

[CARA TEMUIN GOLD]
1. Fakta mengejutkan: angka yang bikin kaget, data yang CONTRAINTUITIVE
2. Expert yang counterintuitive: tokoh bilang sesuatu yang BEDA dari ekspektasi
3. Angka impactful: persentase, rupiah, waktu, jumlah orang yang terdampak
4. Tren tersembunyi: sesuatu yang belum keliatan tapi bakal gede
5. Masalah tersembunyi: hal yang orang alami tapi gak sadar itu masalah

[TERJEMAH NATURAL]
Bahasa Indonesia gaul, bukan terjemahan literal. Contoh:
- subtract → buang, kurangi
- build for worst day → bangun sistem buat hari terburuk
- playbook → cara lama, strategi
- recovery time → waktu pulih
- discipline → disiplin
- hustle → kerja keras, semangat
- level up → naik level, upgrade
- growth → berkembang, naik level
- "toxic productivity" → acceptable (sudah umum di Indo)

[JIKA ARTIKEL DALAM BAHASA INGGRIS]
JANGAN translate literal. Tulis ULANG dari nol dengan bahasa Indonesia gaul yang natural, seolah lo lagi cerita ke temen nongkrong. Contoh:
- "playbook itu BUBAR" → "cara lama udah gak jalan"
- "DI TENGAH 40s" → "Pas umur 40-an"
- "recovery time lebih lama" → "pulihnya lebih lama"
Pembaca harus GAK SADAR ini dari artikel Inggris. Harus terasa kayak lo yang ngalamin sendiri dan lagi cerita.

[STORYTELLING ARC] Slide 1-6 harus terasa kayak 1 cerita nyambung. Bukan 6 fakta terpisah.

SLIDE 1 — HOOK (WAJIB 2-3 kalimat, minimal 30 kata)
KALIMAT PERTAMA: Fakta PALING mengejutkan/provokatif dari artikel. Langsung pukul.
CAPS buat emphasis 1 kata doang.
Hook yang BAGUS bikin pembaca mikir: "Serius? Kok bisa?"
Hook yang JELEK: pembaca skip karena terlalu generik.

VARIASI HOOK (pilih salah satu, JANGAN semua post pola sama):
1. REALIZATION: "Gue baru nyadar... [fakta mengejutkan]"
2. OPINION: "Jujur, gue [emotion] soal [topik]. [Fakta]"
3. QUESTION: "Lo tau gak... [fakta provokatif]?"
4. QUOTE: "[Nama] bilang: '[insight]'. Dan ini bener banget."
5. CONTRAST: "[Ekspektasi]... Tapi kenyataannya? [Realita]"
6. DATA DROP: "[Angka spesifik] orang [konteks]. Lo termasuk?"

ANGKA DI HOOK:
1. Kalau artikel punya ANGKA SPESIFIK → pakai langsung ("40GB cuma sampah WA")
2. Kalau gak ada angka → pakai IMPACT/CONSEQUENCE ("Lo buang 2 jam sehari buat scroll konten gak penting")
Angka bikin credible. Impact bikin relatable. Minimal salah satu WAJIB ada.

CONTOH BAGUS:
- "Gen Z diancam KEHILANGAN kerjaan gara-gara AI. Tapi solusinya? John Collison bilang: lo harus kuliah DUA jurusan sekaligus."
- "Apple kehilangan orang PALING penting di Vision Pro. Dan yang nyolong? OpenAI."
- "99% BISNIS di Indonesia itu UMKM. Mereka nyumbang 60% PDB, tapi PR-nya kayak anak bawang."

CONTOH JELEK:
- "Bayangin lo lagi ngantri Starbucks..." (JANGAN skenario hipotetis)
- "Di era digital saat ini..." (terlalu generik)
- "Teknologi semakin canggih..." (basi)

SLIDE 2 — SETUP / REALITY CHECK (2-3 kalimat, 40-60 kata)
Jembatan dari hook ke MASALAH NYATA. Situasi atau keresahan yang bikin audiens wajib peduli.
Pakai analogi lokal sehari-hari (budak korporat, dompet tipis, gaya hidup Gen-Z).
Bikin pembaca mikir: "Iya gue juga ngalamin ini"

SLIDE 3 — TWIST / CORE FACT (2-3 kalimat, 40-60 kata)
Bongkar FAKTA mengejutkan atau AKAR MASALAH dari artikel.
Ini yang bikin pembaca "oh ternyata..."
Bahasa super simpel, hindari jargon tanpa penjelasan.

SLIDE 4 — DEEP DIVE / TIPS (2-3 kalimat, 40-60 kata)
Data/angka/teknis dari ARTIKEL SAJA + TIPS/ACTIONABLE ADVICE.
Ini slide yang paling BERMANFAAT — kasih sesuatu yang bisa langsung dipake.
Format: "Jadi, yang bisa lo lakuin: [tip 1], [tip 2]"
ATAU: "Expert bilang: [advice spesifik]"

SLIDE 5 — SO WHAT / BIG LESSON (2-3 kalimat, 30-50 kata)
Ringkasan telak yang mengubah MINDSET.
Satu kalimat yang bikin pembaca mikir ulang tentang sesuatu.
Ini yang bikin orang share — "ini gue banget" atau "ini penting banget"

SLIDE 6 — CTA (2-3 kalimat, 30-40 kata)
WAJIB bikin orang comment. Pakai salah satu formula:

ENGAGEMENT FORMULAS:
1. PROVOCATIVE: "Menurut lo, [provokasi]? Atau [alternatif]?"
2. PERSONAL: "Lo sendiri [action]? Cerita di comment."
3. DEBATE: "Setuju gak kalo [pendapat kontroversial]?"
4. RANKING: "Mana yang lebih penting: [A] atau [B]?"
5. CHALLENGE: "Coba deh [action] selama seminggu. Kabarin hasilnya."

WAJIB taruh URL sumber di baris terakhir.
JANGAN pakai frasa "link di bio".

CONTOH CTA BAGUS:
- "Lo pilih: tetep cuek sampe sakit, atau mulai pantau tiap hari tapi data lo di tangan perusahaan? Cerita di comment."
- "Setuju gak kalo semua orang WAJIB learn AI sekarang? Gue penasaran pendapat lo."
- "Mana yang lebih ngeri: di-PHK tanpa peringatan, atau disuruh resign sendiri? Vote di comment."

CLIFFHANGER: Di dasar Slide 1-5, wajib akhirin dengan satu kalimat gantung pendek yang bikin penasaran (misal: "Tapi ngerinya...", "Ini triknya...", "Tapi tunggu..."). Jangan pakai simbol titik/dekoratif, cukup kalimat gantung biasa.

ANALOGI LOKAL: Pakai analogi keresahan lokal sehari-hari (budak korporat, dompet tipis, gaya hidup Gen-Z, bisnis lokal) untuk menyederhanakan fakta di Slide 2-5 SAJA. JANGAN pakai analogi di Slide 1 (hook) — hook harus langsung ke fakta. Jangan mengarang fakta, angka, atau perbandingan baru.

[GROUNDING — STRICT]
SEMUA fakta, angka, nama, perbandingan HARUS dari artikel yang diberikan di bawah. Boleh rephrase, bohong jangan.

Rules:
- Jangan bandingin dengan produk/model/layanan/angka lain KECUALI artikel tersebut sendiri menyebut
- Jangan bilang "katanya", "konon", "dikabarkan" kalau artikel gak bilang gitu
- Jangan nambahin statistik, angka, atau data yang gak ada di artikel
- Jangan nambahin angka "setara X" kalau perbandingan itu gak ada di artikel
- Kalau artikel gak sebut nama orang, jangan sebut nama orang
- Kutipan langsung (tanda kutip) HANYA boleh dipake kalau artikel memang mengutip seseorang
- JANGAN nambahin analogi angka ("setara 10x Gojek") — analogi boleh soal SITUASI, bukan soal ANGKA

[LOCAL CONTENT — WAJIB]
Audience lo orang Indonesia. Konten harus RELATE sama mereka.
- Kalau sebut buku → prioritas: Filosofi Teras, buku Tere Liye, Eka Kurniawan, Andrea Hirata, Dee Lestari. Boleh sebut buku asing TAPI harus yang orang Indo kenal (Atomic Habits, Rich Dad Poor Dad, The Subtle Art).
- JANGAN rekomendasiin buku asing yang obscure (Your Pocket Therapist, Let Them Theory) — kecuali artikel emang nyebut.
- Kalau sebut startup → Gojek, Tokopedia, Traveloka, Shopee, Grab. Bukan startup asing yang gak ada di Indo.
- Kalau sebut influencer → Deddy Corbuzier, Raditya Dika, Jerome Polin, Arief Muhammad. Bukan influencer asing.
- Pakai analogi lokal: budak korporat, THR, mudik, angkringan, warteg, grab bike, ojol.
- 2/3 rekomendasi HARUS yang orang Indo tau. Maks 1 asing.

[CONTENT RULES]
- JANGAN generate konten promosi produk. Jika artikel tentang product launch, spesifikasi, harga, atau review — REJECT. Return {"error":"product_promo"}.
- Konten yang VALID: AI tools (ChatGPT, Gemini, Claude, Midjourney), productivity tips, career advice, mental health, life hacks dengan sudut pandang AI.
- WAJIB ada TIPS/PELAJARAN/ACTIONABLE ADVICE di konten. Bukan cuma cerita/informasi.
- Fokus: "Bagaimana AI bisa bantu lo lebih produktif?" atau "Tips productivity yang work di era AI"

PERSONAL VOICE (HONEST):
- Tulis pakai POV orang pertama (gue/lo)
- Boleh: opini, reaction, observation terhadap berita nyata
- Contoh: "Gue liat berita ini dan langsung mikir...", "Jujur, gue [emotion] soal ini"
- JANGAN fabricate stories/events yang gak pernah ada
- JANGAN pakai: "temen gue", "bapak/emak gue", "keluarga gue", "rekan kerja gue" — kecuali emang beneran ada
- Contoh BOHONG: "Kemarin gue ngobrol sama temen...", "Bapak gue bilang..."
- Contoh JUJUR: "Gue baca berita ini dan langsung mikir: ini bisa terjadi di mana aja"

Output strict JSON, no markdown fences, flat keys only:
{"slide_1":"","slide_2":"","slide_3":"","slide_4":"","slide_5":"","slide_6":"","caption":"","hashtags":""}
"""

# ─── Narrative Single Post Prompt (Ethan Joshua pattern) ─────────────────

NARRATIVE_PROMPT = """Lo "Bro" — Content Creator Threads yang jago bikin konten SINGLE TEXT POST yang viral. Umur 27, ngobrolin AI tools, productivity hacks, career advice. Gw/lo, santai tapi insightful.

[MISI UTAMA]
Lo bikin SATU text post (BUKAN carousel, BUKAN thread) yang bikin orang COMMENT nanya "gimana caranya?!" atau "[teknik/tool] apa tuh?!"

[FORMAT — WAJIB IKUT]
Post ini harus terasa kayak LO lagi cerita ke temen nongkrong. Bukan artikel, bukan listicle.

STRUKTUR:
1. SCENE: Lo di situasi spesifik. Ada hasil mengejutkan. (2-3 kalimat)
   - "Gw baru aja [hasil konkret]. [Konteks spesifik]."
   - HARUS ada angka atau hasil yang bisa diukur

2. DIALOGUE: Orang di sekitar lo nanya/reaksi. (1-2 kalimat)
   - "[Nama/peran] nanya: '[pertanyaan]'"
   - Bikin terasa REAL, bukan fiktif keliatan

3. PATTERN INTERRUPT: Bukan alasan yang orang expect. (1 kalimat)
   - "Bukan karena [alasan obvious/tool populer]."
   - Ini yang bikin orang mikir: "Terus karena apa?!"

4. OPEN LOOP: Lo lakuin SATU hal simpel. (1-2 kalimat)
   - "Gw lakuin satu hal [simpel/teknis] yang bikin [hasil]"
   - JANGAN sebut apa itu — bikin penasaran

5. CENSORED REVEAL: Nama teknik/tool DI-SENSOR pakai asterisk. (1 baris)
   - Format: "Namanya: O********." atau "Tekniknya: C****-O** P*******."
   - Kata pertama boleh keliatan, sisanya sensor pake asterisk (*)
   - Sensor cukup parah biar orang HARUS comment nanya
   - CONTOH BENER: "Namanya: C****-O** P*******." / "Namanya: T*** I***." / "Namanya: R**** P******."
   - CONTOH SALAH: "Namanya: System Prompt." (gak disensor = gak ada curiosity)
   - CONTOH SALAH: "Namanya: S P." (kependekan bukan sensor)

CONTOH VIRAL (268K views, 340 shares):
```
Gw naik pesawat kelas ekonomi. Pas mau masuk, pramugari liat boarding pass: "Bapak pindah ke kursi 2A (Bisnis)."

Gw kaget. "Saya bayar berapa lagi, Mbak?" "Gratis, Pak."

Bukan karena ganteng atau kenal pilot. Gw lakuin satu hal teknis pas check-in bikin maskapai gak punya pilihan selain naikin kelas.

Namanya: O********.
```

CONTOH ADAPTASI TECHBRO:
```
Gw baru aja selesaiin kerjaan 2 hari dalam 4 jam. Manager gw langsung DM.

"Gimana caranya lo cepet banget?"

Bukan karena ChatGPT atau Notion AI. Gw lakuin satu hal simpel di prompt gw yang bikin outputnya 10x lebih bersih.

Namanya: C****-O** P*******.
```

```
Gw baru aja dapet tawaran freelance Rp15 juta dari LinkedIn. Padahal gw gak apply sama sekali.

"Gimana cara lo dapet client kayak gitu?" Temen gw nanya.

Bukan karena portfolio gede atau sertifikasi. Gw lakuin satu hal di headline profil gw yang bikin recruiter auto-DM.

Namanya: K******* H******.
```

```
Gw baru aja naik gaji 40% dalam 6 bulan. Temen gw yang udah 3 tahun di kantor sama aja.

"Lo ngapain aja sih?" Mereka pada penasaran.

Bukan karena jilat bos atau kerja weekend. Gw lakuin satu hal tiap Senin pagi yang bikin bos gw notice.

Namanya: W**** S******.
```

[ATURAN KETAT]
1. WAJIB first person (gw/gue). Bukan "ada orang", bukan "seorang karyawan"
2. WAJIB ada dialogue dengan tanda kutip
3. WAJIB ada angka/hasil konkret (4 jam, Rp15 juta, 40%, 2 hari)
4. WAJIB ada "Bukan karena [X]" — pattern interrupt
5. WAJIB ada "satu hal" — bikin penasaran
6. WAJIB ada censored reveal di akhir — orang HARUS comment buat tau
7. JANGAN sebut tool/teknik yang sebenernya — itu yang bikin engagement
8. Maks 250 kata total. Pendek = baca sampai habis = share
9. JANGAN ada emoji, hashtag, atau "link di bio"
10. Bahasa Indonesia gaul, campur Inggris natural

[GROUNDING]
- Fakta, data, konteks HARUS dari artikel yang dikasih
- Boleh bikin skenario naratif (dialogue, scene) tapi INSIGHT HARUS dari artikel
- JANGAN fabricate data/angka yang gak ada di artikel

[OUTPUT]
Return PLAIN TEXT ONLY. No JSON, no markdown fences, no keys.
Just the post text, nothing else. Start with "Gw" or "Gue".
"""

def _build_user_msg(title: str, body: str, source: str = "") -> str:
    """Build user message, with language note for English sources."""
    english_sources = {"lifehacker", "lifehack", "psychtoday"}
    lang_note = ""
    if source in english_sources:
        lang_note = "[NOTE: Artikel ini dalam bahasa Inggris. Tulis ULANG dalam bahasa Indonesia gaul, jangan translate literal.]\n\n"
    return f"{lang_note}ARTICLE: {body[:4000]}\nSOURCE: {title}"

def _call_mistral(title: str, body: str, source: str = "") -> Optional[str]:
    try:
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
            json={"model": "mistral-large-latest",
                  "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                               {"role": "user", "content": _build_user_msg(title, body, source)}],
                  "temperature": 0.3, "max_tokens": 2000},
            timeout=120)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        print(f"Mistral error: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"Mistral exception: {e}")
    return None

def _call_groq(title: str, body: str, source: str = "") -> Optional[str]:
    if not GROQ_KEY:
        print("Groq skipped (no GROQ_API_KEY)")
        return None
    try:
        r = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                               {"role": "user", "content": _build_user_msg(title, body, source)}],
                  "temperature": 0.3, "max_tokens": 2000},
            timeout=120)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        print(f"Groq error: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"Groq exception: {e}")
    return None

def _parse_json(raw: str) -> Optional[dict]:
    """Parse JSON from LLM output, handling markdown fences and malformed JSON."""
    if not raw:
        return None
    # Strip markdown fences
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw)
    raw = raw.strip()
    
    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    
    # Try to find JSON object in output
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    
    # Try to fix truncated JSON: close open strings and braces
    try:
        # Find the start of JSON
        start = raw.find('{')
        if start < 0:
            return None
        truncated = raw[start:]
        # Close any open string
        if truncated.count('"') % 2 != 0:
            truncated += '"'
        # Close open braces
        opens = truncated.count('{') - truncated.count('}')
        if opens > 0:
            truncated += '}' * opens
        return json.loads(truncated)
    except (json.JSONDecodeError, ValueError):
        pass
    
    return None

def _count_sentences(text: str):
    return len([s for s in re.split(r'(?<=[.!?])\s+', text.strip()) if len(s.strip()) > 5])

def _add_whitespace(text: str) -> str:
    """Split into sentences and join with blank lines between each."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) <= 1:
        return text
    return "\n\n".join(sentences)

def _postprocess_slides(slides: dict, source_url: str = "") -> dict:
    """Post-process all slides: strip markdown, banned phrases, hallucinations."""
    for key in ['slide_1', 'slide_2', 'slide_3', 'slide_4', 'slide_5', 'slide_6', 'caption']:
        text = slides.get(key, '')
        if not text:
            continue
        
        # Strip markdown formatting
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = text.replace('—', ', ').replace('–', ', ')  # em/en dash → comma
        text = re.sub(r'  +', ' ', text)
        
        # Strip banned cringe phrases
        for pat in BANNED_PHRASES:
            text = re.sub(pat, '', text, flags=re.IGNORECASE)
        text = re.sub(r'  +', ' ', text)
        text = re.sub(r'\n \n', '\n\n', text)
        
        # Strip ALL single quotes (Threads gak render, mencegah broken artifacts)
        text = text.replace("'", "")
        # Strip double quotes (dialogue)
        text = re.sub(r'"[^"]{20,}"', '', text)
        text = re.sub(r'"[^"]+[?!][^"]*"', '', text)
        
        # Strip hallucinated source title fragments (e.g. "the Renovation Decade]")
        text = re.sub(r'\bthe [A-Z][a-z]+ [A-Z][a-z]+\b', '', text)

        # Flag foreign book names that are obscure in Indonesia (not well-known)
        FOREIGN_BOOKS = [
            r'\byour pocket therapist\b', r'\blet them theory\b',
            r'\bthe comfort crisis\b', r'\bdopamine nation\b',
            r'\bfour thousand weeks\b', r'\bthe practice\b',
            r'\bthink again\b', r'\batomized\b',
        ]
        for pat in FOREIGN_BOOKS:
            if re.search(pat, text, re.IGNORECASE):
                print(f"  [POSTPROCESS] ⚠️ Foreign book detected: {pat}")

        # Strip hallucinated URLs (source_url re-appended to CTA at the end)
        text = re.sub(r'https?://\S+', '', text).strip()
        
        # Strip hallucinated number analogies: "setara X", "senilai X", "sekitar RpX"
        text = re.sub(r'[Ss]etara\s+[\d.,]+\s*\w+[^.]*\.?\s*', '', text)
        text = re.sub(r'[Ss]etara\s+duit[^.]*\.?\s*', '', text)
        text = re.sub(r'atau\s+bayar\s+gaji[^.]*\.?\s*', '', text)
        
        # Strip placeholders
        text = re.sub(r'\[.*?\]', '', text)
        
        # Add whitespace between sentences
        text = _add_whitespace(text)
        
        # Trim
        text = text.strip()
        
        # Char cap (500 per slide)
        if len(text) > 500:
            text = text[:497] + "..."
        
        slides[key] = text
    
    # Re-append source_url to slide_6 (CTA)
    if source_url and 'slide_6' in slides:
        slides['slide_6'] = slides['slide_6'].rstrip() + "\n\n" + source_url
    
    # Check hook word count
    hook = slides.get('slide_1', '')
    word_count = len(hook.split())
    if word_count < 30:
        print(f"[WARN] Hook too short ({word_count} words), need 30+")
    
    return slides

def generate_carousel(title: str, body: str, image_url: str = "", source_url: str = "", source: str = "") -> Optional[dict]:
    """Generate 6-slide carousel via LLM, with postprocessing."""
    if not MISTRAL_KEY:
        print("No MISTRAL_API_KEY")
        return None
    
    print(f"  Generating with Mistral...")
    raw = _call_mistral(title, body, source)
    
    if not raw:
        print(f"  Mistral failed, trying Groq...")
        raw = _call_groq(title, body, source)
    
    if not raw:
        print(f"  Both providers failed")
        return None
    
    slides = _parse_json(raw)
    if not slides:
        print(f"  Failed to parse JSON")
        print(f"  Raw output: {raw[:500]}")
        return None
    
    # Postprocess
    slides = _postprocess_slides(slides, source_url)
    
    return slides


def evaluate_slides(slides: dict, title: str, body: str, score: int = 0) -> dict:
    """Independent LLM review of generated slides.
    
    Returns:
        {"status": "APPROVE"|"REVISE"|"REJECT", "reason": str, "revised_slides": dict|None}
    
    Skips evaluator for high-score posts (≥80) to save ~50s.
    Uses mistral-small-latest (cheap, different model than generator).
    """
    # Skip evaluator for very high-score posts (rare, only extreme consensus)
    if score >= 100:
        print(f"  [EVALUATOR] Skipped (score={score} ≥ 100)")
        return {"status": "APPROVE", "reason": "high_score_skip"}
    
    slide_text = "\n".join([f"Slide {i}: {slides.get(f'slide_{i}', '')}" for i in range(1, 7)])
    
    evaluator_prompt = f"""You are a skeptical content reviewer for Threads posts targeting Indonesian audience. Review the following 6-slide carousel.

ARTICLE TITLE: {title}

ARTICLE EXCERPT: {body[:500]}

GENERATED SLIDES:
{slide_text}

YOUR TASK:
1. **GROUNDING CHECK** — Every claim, fact, statistic, recommendation in the slides MUST come from the article. If the article doesn't mention a specific book/product/person, the slides MUST NOT invent it. "BEST SELLER" without a number in the article = REJECT.
2. Check if the slides flow as a connected story (not 6 random facts)
3. Check if Slide 1 hook is attention-grabbing (<20 words preferred)
4. Check if Slide 6 has a clear CTA
5. Check for banned patterns (filler words, generic phrases, overly corporate language)
6. **LOCAL RELEVANCE** — For Indonesian audience: prefer local examples (Filosofi Teras, Tere Liye, Eka Kurniawan) over foreign books (Your Pocket Therapist, Let Them Theory). If recommending 3+ items, at least 2 MUST be locally known in Indonesia.

APPROVAL CRITERIA:
- APPROVE: All facts grounded in article, story flows, hook strong, locally relevant
- REVISE: Minor issues (wording, flow, 1 grounding gap) but content is salvageable
- REJECT: Major hallucination, invented facts not in article, broken narrative, low local relevance (2/3+ recommendations are foreign/unknown)

AUTO-REJECT TRIGGERS:
- Inventing statistics ("BEST SELLER", "terlaris", "populer") without article data
- Recommending 2+ books/products not mentioned in article
- Generic advice with no source grounding

Return JSON:
{{
    "status": "APPROVE" or "REVISE" or "REJECT",
    "reason": "brief explanation",
    "issues": ["list of specific issues found"],
    "grounding_score": 0-10 (how many claims are grounded in article),
    "revised_slides": null or {{"slide_1":"...",...}} if REVISE
}}

Be SKEPTICAL. Default to REJECT if unsure. Hallucination = automatic REJECT."""
    
    try:
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
            json={"model": "mistral-small-latest",
                  "messages": [{"role": "user", "content": evaluator_prompt}],
                  "temperature": 0.3,
                  "max_tokens": 1000},
            timeout=30
        )
        
        if r.status_code != 200:
            print(f"  [EVALUATOR] API error: {r.status_code}")
            return {"status": "APPROVE", "reason": "api_error_fail_open"}
        
        content = r.json()["choices"][0]["message"]["content"]
        
        # Strip markdown code fences if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        
        result = json.loads(content)

        status = result.get("status", "APPROVE")
        reason = result.get("reason", "")
        grounding_score = result.get("grounding_score", "N/A")
        issues = result.get("issues", [])

        print(f"  [EVALUATOR] {status}: {reason[:100]}")
        if grounding_score != "N/A":
            print(f"  [EVALUATOR] Grounding: {grounding_score}/10")
        if issues:
            print(f"  [EVALUATOR] Issues: {', '.join(issues[:3])}")

        # Auto-REJECT if grounding_score < 5
        if isinstance(grounding_score, (int, float)) and grounding_score < 5:
            print(f"  [EVALUATOR] Auto-REJECT: grounding_score {grounding_score} < 5")
            status = "REJECT"
            reason = f"Low grounding ({grounding_score}/10): {reason}"

        return {
            "status": status,
            "reason": reason,
            "grounding_score": grounding_score,
            "issues": issues,
            "revised_slides": result.get("revised_slides")
        }
        
    except Exception as e:
        print(f"  [EVALUATOR] Error: {e}")
        return {"status": "APPROVE", "reason": f"exception_fail_open: {e}"}


def generate_narrative_post(title: str, body: str, source_url: str = "", source: str = "") -> Optional[dict]:
    """Generate single narrative text post (Ethan Joshua pattern).
    
    Returns dict with 'text' key (single post body) mapped to slide_hook
    for compatibility with existing DB/poster pipeline.
    """
    if not MISTRAL_KEY:
        print("No MISTRAL_API_KEY")
        return None

    user_msg = _build_user_msg(title, body, source)

    # Try Mistral first
    raw = None
    try:
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
            json={"model": "mistral-large-latest",
                  "messages": [{"role": "system", "content": NARRATIVE_PROMPT},
                               {"role": "user", "content": user_msg}],
                  "temperature": 0.4, "max_tokens": 1500},
            timeout=120)
        if r.status_code == 200:
            raw = r.json()["choices"][0]["message"]["content"]
        else:
            print(f"  Mistral error: {r.status_code}")
    except Exception as e:
        print(f"  Mistral exception: {e}")

    # Fallback to Groq
    if not raw and GROQ_KEY:
        try:
            r = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile",
                      "messages": [{"role": "system", "content": NARRATIVE_PROMPT},
                                   {"role": "user", "content": user_msg}],
                      "temperature": 0.4, "max_tokens": 1500},
                timeout=120)
            if r.status_code == 200:
                raw = r.json()["choices"][0]["message"]["content"]
            else:
                print(f"  Groq error: {r.status_code}")
        except Exception as e:
            print(f"  Groq exception: {e}")

    if not raw:
        print("  Both providers failed")
        return None

    # Clean output: strip markdown fences if present
    text = raw.strip()
    text = re.sub(r"```(?:json|text)?\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()

    # Remove any JSON wrapper if model ignored instruction
    if text.startswith('{'):
        data = _parse_json(text)
        if data and "text" in data:
            text = data["text"]

    if not text or len(text) < 50:
        print(f"  Narrative post too short ({len(text)} chars)")
        return None

    # Postprocess: strip markdown, banned phrases
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # strip **bold**
    # Don't strip single * — they're used for censored reveal (Namanya: O********)
    text = text.replace('—', ', ').replace('–', ', ')
    for pat in BANNED_PHRASES:
        text = re.sub(pat, '', text, flags=re.IGNORECASE)
    text = re.sub(r'  +', ' ', text).strip()

    # Validate: must have key narrative elements
    checks = {
        "dialogue": bool(re.search(r'["\u201c\u201d].+?["\u201c\u201d]', text)),
        "pattern_interrupt": bool(re.search(r'[Bb]ukan karena', text)),
        "open_loop": bool(re.search(r'[Ss]atu hal', text)),
        "censored": bool(re.search(r'\*{3,}|\w\*{2,}', text)),  # *** or word followed by **
    }
    failed = [k for k, v in checks.items() if not v]
    if failed:
        print(f"  [WARN] Narrative missing elements: {failed}")
        # Still return it, just warn

    if len(text) > 500:
        text = text[:497] + "..."

    # Map to slide_hook for DB compatibility (poster treats single-slide as single post)
    return {"hook": text, "caption": text, "_format": "narrative"}


# ─── 10-Slide Thread Chain Prompt (Ethan Joshua pattern) ─────────────────

THREAD_CHAIN_PROMPT = """Lo "Bro" — Content Creator Threads yang jago bikin THREAD CHAIN viral (10 slides). Umur 27, ngobrolin AI tools, productivity hacks, career advice. Gw/lo, santai tapi insightful.

[MISI UTAMA]
Lo bikin 10-slide THREAD CHAIN yang bikin orang BACA SAMPE AKHIR dan COMMENT nanya "gimana caranya?!" atau "[teknik/tool] apa tuh?!"

Format: Slide 1 = post utama, Slide 2-10 = replies (thread chain).
Setiap slide 2-4 kalimat, 30-50 kata. JANGAN terlalu panjang.

[STRUKTUR 10 SLIDES — WAJIB IKUT]

SLIDE 1 — HOOK (open loop)
Scene + hasil mengejutkan + dialogue + pattern interrupt + censored reveal.
Format:
```
Gw baru aja [hasil konkret]. [Konteks spesifik].

"[Pertanyaan dari orang]" [peran] nanya.

Bukan karena [alasan obvious]. Gw lakuin satu hal [simpel/teknis] yang bikin [hasil].

Namanya: [SENSOR PAKAI ASTERISK].
```
WAJIB ada:
- Angka/hasil konkret (4 jam, Rp15 juta, 40%)
- Dialogue dengan tanda kutip
- "Bukan karena [X]" — pattern interrupt
- "satu hal" — open loop
- Censored reveal: "Namanya: C****-O** P*******" (bukan singkatan, tapi sensor asterisk)

SLIDE 2 — EXPLANATION
Jawab pertanyaan dari slide 1. Jelasin KONTEKS kenapa ini penting.
Format: "[Pertanyaan follow-up]?" tanya gw. [Jawaban dari sumber/expert]

SLIDE 3 — DEEP DIVE
Bongkar FAKTA mengejutkan atau sistem tersembunyi.
Format: [Nama/expert] jelasin: "[insight teknis]"
WAJIB ada istilah teknis yang bikin orang mikir "oh ternyata..."

SLIDE 4 — CRITERIA / HOW IT WORKS
Jelasin KRITERIA atau mekanisme. Bisa pakai bullet points.
Format: Dia kasih rincian [X]:
• [Kriteria 1]
• [Kriteria 2]
• [Kriteria 3]

SLIDE 5 — UNFAIR TRUTH
Fakta yang bikin orang MARAH atau SHOCK. Sisi gelap/sistem yang gak adil.
Format: "[Pertanyaan provokatif]?" tanya gw. "[Jawaban yang bikin kesel]"

SLIDE 6 — ACTION STEPS (4 langkah)
Tips YANG BISA LANGSUNG DIPRAKTEKKIN. Numbered list.
Format: [Pertanyaan]?" Temen gw kasih [X] langkah:
1. [Langkah 1]
2. [Langkah 2]
3. [Langkah 3]
4. [Langkah 4]
WAJIB ada 1 langkah yang DI-CAPS sebagai "paling penting"

SLIDE 7 — SECRET TIP (1 tips maut)
Satu tips yang SERING DILUPAIN banyak orang. Ini yang bikin orang SHARE.
Format: "[Pertanyaan]?" tanya gw. Dia kasih satu tips maut: "[tips spesifik]"
WAJIB ada dialogue + tips yang ACTIONABLE

SLIDE 8 — PROBLEM / TRAP
Jebakan yang bikin orang GAGAL. Ini yang bikin orang COMMENT "gue banget!"
Format: [Expert] ngingetin: "[peringatan]"
WAJIB ada contoh spesifik kenapa orang gagal

SLIDE 9 — SOLUTION / AFFILIATE (opsional)
Solusi dari masalah di slide 8. Bisa pakai link affiliate.
Format: Makanya gw sekarang selalu [solusi]. [Produk/tools yang gw pakai]
JANGAN hard-sell. Natural aja.

SLIDE 10 — CTA (save + praktekin)
Ajak orang SIMPEN dan PRAKTEKKAN. Callback ke slide 1.
Format: Inget: [ringkasan]. Minggu depan ada [situasi]? Coba:
✅ [Action 1]
✅ [Action 2]
✅ [Action 3]
Simpen utas ini, praktekin, terus kabarin gw kalau lo berhasil [hasil]. [emoji]

[CONTOH VIRAL — @ethan_joshuaa, 277K views, 340 shares]
Slide 1: "Gw naik pesawat kelas ekonomi. Pas mau masuk, pramugari liat boarding pass: 'Bapak pindah ke kursi 2A (Bisnis).' Gw kaget. 'Saya bayar berapa lagi, Mbak?' 'Gratis, Pak.' Bukan karena ganteng atau kenal pilot. Gw lakuin satu hal teknis pas check-in bikin maskapai gak punya pilihan selain naikin kelas. Namanya: O********."
Slide 2: "'Maksudnya pesawatnya kepenuhan?' tanya gw pas udah duduk nyaman di kursi bisnis yang lebar. Temen gw yang kerja di maskapai jelasin..."
Slide 6: "Cara jadi kandidat upgrade? Temen gw kasih 4 langkah: 1. JANGAN check-in online awal. 2. PAKAI RAPI. 3. BERANGKAT SENDIRIAN. 4. JADI MEMBER LOYALTY."

[ATURAN KETAT]
1. WAJIB first person (gw/gue). Bukan "ada orang"
2. WAJIB ada dialogue dengan tanda kutip di slide 1-3, 7-8
3. WAJIB ada angka/hasil konkret di slide 1
4. WAJIB ada "Bukan karena [X]" di slide 1
5. WAJIB ada censored reveal di slide 1 (sensor pakai asterisk, BUKAN singkatan)
6. JANGAN pakai nama PT perusahaan asli
7. JANGAN pakai cringe/geleng-geleng/gila sih
8. JANGAN link di bio
9. WAJIB taruh URL sumber di slide 10 (baris terakhir)
10. Setiap slide 2-4 kalimat, 30-50 kata. JANGAN terlalu panjang.

[GROUNDING — STRICT]
SEMUA fakta, angka, nama HARUS dari artikel. Boleh rephrase, bohong jangan.
Jangan tambahin statistik/data yang gak ada di artikel.

Output strict JSON, no markdown fences, flat keys only:
{"slide_1":"","slide_2":"","slide_3":"","slide_4":"","slide_5":"","slide_6":"","slide_7":"","slide_8":"","slide_9":"","slide_10":"","caption":"","hashtags":""}
"""


def generate_thread_chain(title: str, body: str, image_url: str = "", source_url: str = "", source: str = "") -> Optional[dict]:
    """Generate 10-slide thread chain via LLM, with postprocessing."""
    if not MISTRAL_KEY and not GROQ_KEY:
        print("ERROR: No API keys (MISTRAL_API_KEY or GROQ_API_KEY)")
        return None

    user_msg = _build_user_msg(title, body, source)

    raw = ""
    # Primary: Mistral
    if MISTRAL_KEY:
        try:
            r = httpx.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
                json={"model": "mistral-large-latest",
                      "messages": [{"role": "system", "content": THREAD_CHAIN_PROMPT},
                                   {"role": "user", "content": user_msg}],
                      "temperature": 0.5, "max_tokens": 4000},
                timeout=120)
            if r.status_code == 200:
                raw = r.json()["choices"][0]["message"]["content"]
            else:
                print(f"  Mistral error: {r.status_code}")
        except Exception as e:
            print(f"  Mistral exception: {e}")

    # Fallback to Groq
    if not raw and GROQ_KEY:
        try:
            r = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile",
                      "messages": [{"role": "system", "content": THREAD_CHAIN_PROMPT},
                                   {"role": "user", "content": user_msg}],
                      "temperature": 0.5, "max_tokens": 4000},
                timeout=120)
            if r.status_code == 200:
                raw = r.json()["choices"][0]["message"]["content"]
            else:
                print(f"  Groq error: {r.status_code}")
        except Exception as e:
            print(f"  Groq exception: {e}")

    if not raw:
        print("  Both providers failed")
        return None

    data = _parse_json(raw)
    if not data:
        print(f"  Failed to parse thread chain JSON: {raw[:300]}")
        return None

    # Extract slides (10 slides)
    slides = []
    for i in range(1, 11):
        key = f"slide_{i}"
        val = (data.get(key) or "").strip()
        if val:
            # Postprocess: strip markdown, banned phrases
            val = re.sub(r'\*\*(.+?)\*\*', r'\1', val)
            val = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', val)
            val = val.replace('—', ', ').replace('–', ', ')
            for pat in BANNED_PHRASES:
                val = re.sub(pat, '', val, flags=re.IGNORECASE)
            val = val.strip()
            if val:
                slides.append(val)

    if len(slides) < 8:
        print(f"  Too few slides ({len(slides)}/10)")
        return None

    # Validate slide 1 has key elements
    hook = slides[0]
    checks = {
        "dialogue": bool(re.search(r'["\u201c\u201d].+?["\u201c\u201d]', hook)),
        "pattern_interrupt": bool(re.search(r'[Bb]ukan karena', hook)),
        "open_loop": bool(re.search(r'[Ss]atu hal', hook)),
        "censored": bool(re.search(r'\*{3,}|\w\*{2,}', hook)),
    }
    failed = [k for k, v in checks.items() if not v]
    if failed:
        print(f"  [WARN] Hook missing elements: {failed}")

    # Build result
    result = {
        "slides": slides,
        "caption": data.get("caption", slides[0][:100]),
        "hashtags": data.get("hashtags", ""),
        "_format": "thread_chain",
        "_slide_count": len(slides),
    }
    return result


if __name__ == "__main__":
    # Test
    result = generate_carousel(
        "Test Article About AI",
        "This is a test article about how AI is changing the world. OpenAI released GPT-5 which can write code, generate images, and even create videos.",
        source_url="https://example.com/test"
    )
    if result:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("Generation failed")
