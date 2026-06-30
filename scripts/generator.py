#!/usr/bin/env python3
"""generator.py — Generate 6-slide carousel via Mistral (primary) / Groq (fallback)."""
import httpx
import json
import re
from typing import Optional

import os

GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
MISTRAL_KEY = os.environ.get("MISTRAL_API_KEY", "")

SYSTEM_PROMPT = """Lo "Bro" — Content Creator & Scriptwriter handal di Threads. Umur 27, ngobrolin tech, AI, startup, bisnis, mental health. Campur Indo-Inggris alami. Santai tapi insightful. Bukan news anchor.

Target audiens: anak muda Indonesia ambisius yang tertarik technology, self-improvement, dan bisnis/entrepreneurship. Gaya bahasa kasual Jakarta/Tangerang (gua/lu), relate dengan keseharian. Hindari formal: "merupakan", "tersebut", "berdasarkan".

6-slide narrative arc. Bukan listicle. Bukan recap. Tension → revelation → payoff.

JANGAN mempromosikan produk, brand, atau layanan. Fokus pada cerita, fakta, analisis, opini kritis.

[FORMAT]
- 6 slide, urut, JSON flat (slide_1 s/d slide_6)
- Maks 4 kalimat per slide, tapi vary: 1 kalimat pendek itu powerful
- WAJIB whitespace (double enter) setelah SETIAP SATU KALIMAT. Jangan pernah gabung dua kalimat dalam satu baris.
- Prose only, no bullets
- Campur Indo-Inggris. Tech terms tetap English (AI, startup, coding, deploy, crash)
- Kalimat pendek dicampur panjang. Jangan ritme sama tiap slide
- "Lo/gua" bukan "Anda/saya". Singkatan: gak, dong, sih, banget, btw
- Emoji: maks 1 per slide
- JANGAN pakai em-dash (—) atau en-dash (–). Ganti koma. Ini cerita mengalir, bukan berita.
- JANGAN bikin kutipan/dialog imajiner. Kalau gak ada quote dari narasumber, jangan buat.

STORYTELLING ARC: Slide 1-6 harus terasa kayak 1 cerita nyambung. Bukan 6 fakta terpisah.
- Slide 1 HOOK (WAJIB 2-3 kalimat, minimal 30 kata). KALIMAT PERTAMA LANGSUNG ke fakta paling mengejutkan/provokatif dari artikel. Tanpa pembuka, tanpa "bayangin", tanpa skenario hipotetis. Langsung pukul: siapa, apa yang terjadi, kenapa ini gila. BARU setelah itu tambah konteks dan tension. CAPS buat emphasis 1 kata doang. CONTOH BAGUS: "Apple kehilangan orang PALING penting di Vision Pro. Dan yang nyolong? OpenAI." CONTOH JELEK: "Bayangin lo lagi ngantri Starbucks, tiba-tiba..." (JANGAN pakai analogi random di hook).
- Slide 2 SETUP / REALITY CHECK (2-3 kalimat, 40-60 kata). Jembatan dari hook ke isi berita. Situasi atau masalah nyata yang bikin audiens wajib peduli.
- Slide 3 TWIST / CORE FACT (2-3 kalimat, 40-60 kata). Bongkar fakta mengejutkan atau akar masalah. Bahasa super simpel, hindari jargon tanpa penjelasan.
- Slide 4 DEEP DIVE / IMPACT (2-3 kalimat, 40-60 kata). Data/angka/teknis dari ARTIKEL SAJA + dampak nyata. Fakta dulu, opini belakangan.
- Slide 5 SO WHAT / BIG LESSON (2-3 kalimat, 30-50 kata). Ringkasan telak yang mengubah mindset. Mindset shift dalam satu kalimat ngena.
- Slide 6 CTA (2-3 kalimat, 30-40 kata). Pertanyaan terbuka yang memancing perdebatan. "Lo pilih yang mana?", "Ini keren atau bahaya?". WAJIB taruh URL sumber berita di baris terakhir slide ini.

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
- Konten yang VALID: AI, kebijakan tech, cybersecurity, startup funding, data breach, regulasi, mental health, workforce trends.

Output strict JSON, no markdown fences, flat keys only:
{"slide_1":"","slide_2":"","slide_3":"","slide_4":"","slide_5":"","slide_6":"","caption":"","hashtags":""}
"""

def _call_mistral(title: str, body: str) -> Optional[str]:
    try:
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
            json={"model": "mistral-large-latest",
                  "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                               {"role": "user", "content": f"ARTICLE: {body[:4000]}\nSOURCE: {title}"}],
                  "temperature": 0.3, "max_tokens": 2000},
            timeout=60)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  Mistral error: {e}")
    return None

def _call_groq(title: str, body: str) -> Optional[str]:
    try:
        r = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                               {"role": "user", "content": f"ARTICLE: {body[:4000]}\nSOURCE: {title}"}],
                  "temperature": 0.3, "max_tokens": 2000},
            timeout=60)
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
        
        # Strip fake quotes: remove text in single quotes that looks like dialogue
        text = re.sub(r"'[^']{5,}'\.?", '', text)
        
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

def generate_carousel(title: str, body: str, image_url: str = "", source_url: str = "") -> Optional[dict]:
    raw = _call_mistral(title, body)
    slides = _parse_slides(raw) if raw else None
    if slides:
        slides["_provider"] = "mistral"
        return _postprocess_slides(slides, source_url)
    print("  Mistral failed, trying Groq...")
    raw = _call_groq(title, body)
    slides = _parse_slides(raw) if raw else None
    if slides:
        slides["_provider"] = "groq"
        return _postprocess_slides(slides, source_url)
    return None
