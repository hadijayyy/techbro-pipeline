#!/usr/bin/env python3
"""generator.py — Generate 6-slide carousel via Mistral (primary) / Groq (fallback)."""
import httpx
import json
import re
from typing import Optional

import os

GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
MISTRAL_KEY = os.environ.get("MISTRAL_API_KEY", "")

SYSTEM_PROMPT = """Lo "Bro" — Content Creator & Scriptwriter handal di Threads. Umur 27, ngobrolin AI tools, productivity hacks, career advice, mental health. Campur Indo-Inggris alami. Santai tapi insightful. Bukan news anchor.

Target audiens: anak muda Indonesia ambisius yang tertarik AI tools, productivity, career growth, dan mental health. Gaya bahasa kasual Jakarta/Tangerang (gua/lu), relate dengan keseharian. Hindari formal: "merupakan", "tersebut", "berdasarkan".

[JIKA ARTIKEL DALAM BAHASA INGGRIS]
JANGAN translate literal. Tulis ULANG dari nol dengan bahasa Indonesia gaul yang natural, seolah lo lagi cerita ke temen nongkrong. Contoh:
- JELEK: "playbook itu BUBAR" → BAGUS: "cara lama udah gak jalan"
- JELEK: "DI TENGAH 40s" → BAGUS: "Pas umur 40-an"
- JELEK: "recovery time lebih lama" → BAGUS: "pulihnya lebih lama"
- JELEK: "kapasitas lo beneran berubah" → BAGUS: "badan lo emang udah beda"
Prinsip: pembaca harus GAK SADAR ini dari artikel Inggris. Harus terasa kayak lo yang ngalamin sendiri dan lagi cerita.

[MISI UTAMA]
Lo BUKAN news anchor yang ngulang berita. Lo adalah STORYTELLER yang nge-EXTRACT tips, pelajaran, dan actionable advice dari artikel.

Setiap artikel — berita, riset, trend, opini — punya GOLD di dalamnya: pelajaran yang bisa lo pake. Tugas lo adalah:
1. Baca artikel
2. Temuin GOLD-nya: apa yang bisa dipelajari, diaplikasikan, atau diwaspadai
3. Bungkus jadi cerita yang bikin pembaca mikir "oh gue juga ngalamin ini" atau "gue harus coba ini"

CONTOH TRANSFORMASI:
- Berita "CEO Apple resign" → Tips: "Tanda-tanda lo harus resign dari kerjaan"
- Riset "AI bisa diagnosis kanker" → Tips: "3 cara AI bisa bantu lo sekarang"
- Trend "Gen Z job hopping" → Tips: "Kapan waktu yang tepat buat pindah kerja"
- News "Startup X bangkrut" → Tips: "Red flag startup yang bakal bangkrut"
- News "Pendiri Stripe: Gen Z perlu 2 ijazah" → Tips: "Skill combo yang bikin lo gak bisa diganti AI"

CARA TEMUIN GOLD:
- Cari FAKTA yang bikin lo "oh shit" atau "wah gak tau gue"
- Cari ORANG/EXPERT yang ngomong sesuatu yang counterintuitive
- Cari ANGKA yang bikin lo "serius segitu?"
- Cari TREND yang ngaruh langsung ke hidup pembaca
- Cari MASALAH yang pembaca gak sadar mereka punya

6-slide narrative arc. Bukan listicle. Bukan recap. Tension → revelation → payoff.

JANGAN mempromosikan produk, brand, atau layanan. Fokus pada cerita, fakta, analisis, opini kritis.

[FORMAT]
- 6 slide, urut, JSON flat (slide_1 s/d slide_6)
- Maks 4 kalimat per slide, tapi vary: 1 kalimat pendek itu powerful
- WAJIB whitespace (double enter) setelah SETIAP SATU KALIMAT. Jangan pernah gabung dua kalimat dalam satu baris.
- Prose only, no bullets
- Campur Indo-Inggris. Tech terms tetap English (AI, startup, coding, deploy, crash)
- Kalimat pendek dicampur panjang. Jangan ritme sama tiap slide
- Lo/gua bukan "Anda/saya". Singkatan: gak, dong, sih, banget, btw
- JANGAN pakai emoji/emoticon (😳, 👀, 🤣, 😱, dll). Tulis tanpa simbol visual.
- JANGAN pakai em-dash (—) atau en-dash (–). Ganti koma. Ini cerita mengalir, bukan berita.
- JANGAN pakai frasa "link di bio" — URL sudah ada di post, gak perlu sebut.
- JANGAN bikin kutipan/dialog imajiner. Kalau gak ada quote dari narasumber, jangan buat.

[TERJEMAH NATURAL]
Bahasa Indonesia dulu. Tech terms English boleh (AI, startup, coding, deploy, crash), tapi:
- "subtract" → "buang" / "kurangi"
- "build for worst day" → "bangun sistem buat hari terburuk"
- "playbook" → "cara lama" / "strategi"
- "recovery time" → "waktu pulih"
- "discipline" → "disiplin"
- "hustle" → "kerja keras" / "semangat"
- "level up" → "naik level" / "upgrade"
- "toxic productivity" → acceptable (sudah umum di Indo)
- "growth" → "berkembang" / "naik level"

STORYTELLING ARC: Slide 1-6 harus terasa kayak 1 cerita nyambung. Bukan 6 fakta terpisah.

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
- Contoh BOHONG: "Kemarin gue ngobrol sama temen..." (kalo emang gak ada)
- Contoh JUJUR: "Gue baca berita ini dan langsung mikir: ini bisa terjadi di mana aja"

Output strict JSON, no markdown fences, flat keys only:
{"slide_1":"","slide_2":"","slide_3":"","slide_4":"","slide_5":"","slide_6":"","caption":"","hashtags":""}
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
    except Exception as e:
        print(f"  Mistral error: {e}")
    return None

def _call_groq(title: str, body: str, source: str = "") -> Optional[str]:
    if not GROQ_KEY:
        print("  Groq skipped (no GROQ_API_KEY)")
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
    except Exception as e:
        print(f"  Groq error: {e}")
    return None

def _parse_slides(raw: str) -> Optional[dict]:
    if not raw:
        return None
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw)
    raw = raw.strip()
    # Handle Mistral's {{ }} quirk
    if raw.startswith("{{"):
        raw = raw.replace("{{","{").replace("}}","}")
    # Fix missing closing brace
    missing = raw.count('{') - raw.count('}')
    if missing > 0: raw += '}' * missing
    
    try:
        data = json.JSONDecoder().raw_decode(raw.lstrip())[0]
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                data = json.JSONDecoder().raw_decode(m.group().lstrip())[0]
            except json.JSONDecodeError:
                return None
        else:
            return None
    
    # Accept both slide_1 format and hook/setup format
    slide_keys = [f"slide_{i}" for i in range(1,7)]
    alt_keys = ["hook", "setup", "twist", "deep", "sowhat", "cta"]
    
    slides = {}
    if all(k in data for k in slide_keys):
        for i, k in enumerate(slide_keys):
            slides[alt_keys[i]] = data[k]
    elif all(k in data for k in alt_keys[:6]):
        for k in alt_keys[:6]:
            slides[k] = data[k]
    else:
        print(f"  Missing slide keys")
        return None
    
    slides["caption"] = data.get("caption", "")
    slides["hashtags"] = data.get("hashtags", "")
    return slides

MAX_CHARS = 490  # Threads per-slide limit
SENTENCE_COUNTS = {"hook": (2,3), "setup": (2,3), "twist": (2,3), "deep": (2,3), "sowhat": (2,3), "cta": (2,3)}
HOOK_MIN_WORDS = 30

# Cringe/filler phrases only — NOT comparison or content words
BANNED_PHRASES = [
    r'\bgeleng[- ]geleng\b', r'\bgaruk kepala\b', r'\bkayak dari masa depan\b',
    r'\bgila sih\b', r'\bgila banget\b', r'\bgila kan\b',
    r'\bkebayang gak\b', r'\byang bener aja\b',
    r'\btahan dulu\b', r'\bcoba tebak\b', r'\bciyus\b', r'\bmiyap\b',
    r'\bmuka masam\b', r'\bngebet\b',
    r'\blink di bio\b',  # URL sudah ada di post, gak perlu sebut
]

def _count_sentences(text):
    return len([s for s in re.split(r'(?<=[.!?])\s+', text.strip()) if len(s.strip()) > 5])

def _add_whitespace(text: str) -> str:
    """Split into sentences and join with blank lines between each."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) <= 1:
        return text
    return "\n\n".join(sentences)

def _postprocess_slides(slides: dict, source_url: str = "") -> dict:
    """Pressbox-style post-processing: enforce blank lines, trim, strip markdown."""
    for key in ["hook", "setup", "twist", "deep", "sowhat", "cta"]:
        text = slides.get(key, "")
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
        
        # Strip hallucinated URLs (source_url re-appended to CTA at the end)
        text = re.sub(r'https?://\S+', '', text).strip()
        
        # Strip hallucinated number analogies: "setara X", "senilai X", "sekitar RpX"
        text = re.sub(r'[Ss]etara\s+[\d.,]+\s*\w+[^.]*\.?\s*', '', text)
        text = re.sub(r'[Ss]etara\s+duit[^.]*\.?\s*', '', text)
        text = re.sub(r'atau\s+bayar\s+gaji[^.]*\.?\s*', '', text)
        
        # Strip placeholder text like "[URL sumber berita]"
        text = re.sub(r'\[.*?\]', '', text)
        
        # Enforce blank line after EVERY sentence
        text = _add_whitespace(text)
        
        # Auto-trim by sentence count
        mn, mx = SENTENCE_COUNTS.get(key, (2,3))
        n = _count_sentences(text)
        if n > mx:
            parts = re.split(r'(?<=[.!?])\s+', text.strip())
            trimmed = [p for p in parts if len(p.strip()) > 5][:mx]
            text = "\n\n".join(trimmed)
        
        # Char cap
        if len(text) > MAX_CHARS:
            txt = text[:MAX_CHARS]
            lp = max(txt.rfind(". "), txt.rfind("! "), txt.rfind("? "))
            text = txt[:lp+1] if lp > 50 else txt.rstrip() + "..."
        
        slides[key] = text.strip()
    
    # Enforce hook minimum word count — retry if too short
    hook_words = len(slides.get("hook", "").split())
    if hook_words < HOOK_MIN_WORDS:
        print(f"  [WARN] Hook too short ({hook_words} words), need {HOOK_MIN_WORDS}+")
    
    # Ensure article URL on slide 6
    if source_url and source_url not in slides.get("cta", ""):
        slides["cta"] = slides["cta"].rstrip() + "\n\n" + source_url
    
    return slides

def generate_carousel(title: str, body: str, image_url: str = "", source_url: str = "", source: str = "") -> Optional[dict]:
    raw = _call_mistral(title, body, source)
    slides = _parse_slides(raw) if raw else None
    if slides:
        slides["_provider"] = "mistral"
        return _postprocess_slides(slides, source_url)
    print("  Mistral failed, trying Groq...")
    raw = _call_groq(title, body, source)
    slides = _parse_slides(raw) if raw else None
    if slides:
        slides["_provider"] = "groq"
        return _postprocess_slides(slides, source_url)
    return None
