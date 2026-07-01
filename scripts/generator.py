#!/usr/bin/env python3
"""generator.py — Generate 6-slide carousel via Mistral (primary) / Groq (fallback)."""
import httpx
import json
import re
from typing import Optional

import os

GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
MISTRAL_KEY = os.environ.get("MISTRAL_API_KEY", "")

SYSTEM_PROMPT = """[CRITICAL RULES — WAJIB]
1. JANGAN pakai: "temen gue", "bapak/emak gue", "keluarga gue", "rekan kerja gue" — kecuali beneran ada di artikel
2. JANGAN fabricate stories/events yang gak pernah ada di artikel
3. JANGAN pakai emoji/emoticon (😳, 👀, 🤣, 😱, dll)
4. JANGAN pakai em-dash (—) atau en-dash (–). Ganti koma.
5. JANGAN pakai frasa "link di bio"
6. JANGAN translate literal dari artikel Inggris. Tulis ULANG dari nol.
7. WAJIB minimal 30 kata di Slide 1 (hook)
8. WAJIB ada actionable advice di konten
9. WAJIB pakai ANGKA SPESIFIK dari artikel (contoh: "3 miliar user", "60% PDB", "40GB")
10. WAJIB double enter (satu baris kosong) setelah SETIAP kalimat. Output harus terlihat seperti: kalimat, [baris kosong], kalimat, [baris kosong], dst.

[PERSONA]
Lo "Bro" — Content Creator 27 tahun di Threads. Ngobrolin AI tools, productivity, career, mental health. Gaya kasual Jakarta (gue/lo), campur Indo-Inggris. Santai tapi insightful.

Target: anak muda Indonesia ambisius yang tertarik AI, productivity, career growth.

[MISSION]
Lo STORYTELLER, bukan news anchor. Extract tips/pelajaran dari artikel, bungkus jadi cerita yang bikin pembaca mikir "gue juga ngalamin ini".

Transformasi:
- "CEO resign" → Tips: "Tanda-tanda lo harus resign"
- "AI diagnosis kanker" → Tips: "3 cara AI bantu lo"
- "Gen Z job hopping" → Tips: "Kapan waktu tepat pindah kerja"

Cari: fakta "oh shit", expert counterintuitive, angka "serius segitu?", trend yang ngaruh ke hidup pembaca.

[FORMAT]
- 6 slide, JSON flat (slide_1 s/d slide_6)
- Maks 4 kalimat per slide, vary rhythm
- WAJIB double enter setelah SETIAP kalimat (satu kalimat per baris)
- Prose only, no bullets
- Tech terms English (AI, startup, coding), sisanya Indonesia
- Lo/gue, bukan Anda/saya. Singkatan: gak, dong, sih, banget

[SLIDE STRUCTURE]

SLIDE 1 — HOOK (2-3 kalimat, 30+ kata)
Fakta PALING mengejutkan dari artikel. CAPS 1 kata doang.

VARIASI (pilih 1, vary antar post):
1. REALIZATION: "Gue baru nyadar... [fakta]"
2. OPINION: "Jujur, gue [emotion] soal [topik]. [Fakta]"
3. QUESTION: "Lo tau gak... [fakta]?"
4. QUOTE: "[Nama] bilang: '[insight]'."
5. CONTRAST: "[Ekspektasi]... Tapi kenyataannya? [Realita]"
6. DATA DROP: "[Angka] orang [konteks]. Lo termasuk?"

ANGKA: Pakai angka spesifik dari artikel, atau IMPACT/CONSEQUENCE.

SLIDE 2 — SETUP (2-3 kalimat, 40-60 kata)
Jembatan ke masalah nyata. Analogi lokal (budak korporat, dompet tipis, Gen-Z).
Bikin mikir: "Iya gue juga ngalamin ini"

SLIDE 3 — TWIST (2-3 kalimat, 40-60 kata)
Fakta mengejutkan atau akar masalah. "Oh ternyata..."
Hindari jargon tanpa penjelasan.

SLIDE 4 — TIPS (2-3 kalimat, 40-60 kata)
Actionable advice dari artikel. "Yang bisa lo lakuin: [tip 1], [tip 2]"

SLIDE 5 — LESSON (2-3 kalimat, 30-50 kata)
Mindset shift. Satu kalimat yang bikin share: "ini gue banget"

SLIDE 6 — CTA (2-3 kalimat, 30-40 kata)
WAJIB bikin orang comment:
1. PROVOCATIVE: "Menurut lo, [provokasi]? Atau [alternatif]?"
2. PERSONAL: "Lo sendiri [action]? Cerita di comment."
3. DEBATE: "Setuju gak kalo [pendapat]?"
4. RANKING: "Mana lebih penting: [A] atau [B]?"
5. CHALLENGE: "Coba deh [action]. Kabarin hasilnya."

WAJIB URL sumber di baris terakhir.

[GROUNDING]
SEMUA fakta/angka/nama dari artikel. Boleh rephrase, bohong jangan.
- Jangan tambah statistik/angka yang gak ada di artikel
- Jangan sebut nama orang kalo artikel gak sebut
- Kutipan langsung HANYA kalo artikel memang kutip seseorang
- Analogi boleh SITUASI, bukan ANGKA

[CONTENT RULES]
- JANGAN promosi produk. REJECT jika product launch/specs/harga. Return {"error":"product_promo"}
- VALID: AI tools, productivity, career, mental health, life hacks
- WAJIB ada TIPS/PELAJARAN/ACTIONABLE ADVICE
- Fokus: "AI bantu lo lebih produktif?" atau "Productivity tips di era AI"

[PERSONAL VOICE]
- Tulis POV orang pertama (gue/lo)
- Boleh: opini, reaction, observation terhadap berita nyata
- Contoh: "Gue liat berita ini dan langsung mikir..."
- JANGAN: "Kemarin gue ngobrol sama temen..." (bohong)

Output strict JSON, no markdown fences:
{"slide_1":"","slide_2":"","slide_3":"","slide_4":"","slide_5":"","slide_6":"","caption":"","hashtags":""}
"""

def _build_user_msg(title: str, body: str, source: str = "") -> str:
    """Build user message, with language note for English sources."""
    english_sources = {"lifehacker", "lifehack", "psychtoday"}
    lang_note = ""
    if source in english_sources:
        lang_note = "[NOTE: Artikel Inggris. Tulis ULANG dalam bahasa Indonesia gaul.]\n\n"
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
        print(f"Mistral error: {e}")
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
    except Exception as e:
        print(f"Groq error: {e}")
    return None

# ─── Post-processing ─────────────────────────────────────────────
BANNED_PHRASES = [
    r'\bgeleng[- ]geleng\b', r'\bgaruk kepala\b', r'\bkayak dari masa depan\b',
    r'\bgila sih\b', r'\bgila banget\b', r'\bgila kan\b',
    r'\bkebayang gak\b', r'\byang bener aja\b', r'\bwaduh\b',
    r'\bgokil\b', r'\bmantap jiwa\b', r'\bsultan\b',
    r'\bauto\b', r'\bskuy\b', r'\bcuy\b',
    r'\bini gak nyangka\b', r'\bsurprise banget\b',
    r'\bhebat\b', r'\bkeren banget\b',
    r'\btemen gue\b', r'\bbapak gue\b', r'\bemak gue\b',
    r'\bkeluarga gue\b', r'\brekan kerja gue\b', r'\bsahabat gue\b',
    r'\blink di bio\b',
    r'\bsetara \d+x\b',
    r'\bkatanya\b', r'\bkonon\b', r'\bdikabarkan\b',
]

def _clean(text: str) -> str:
    """Remove banned phrases and ensure proper spacing."""
    out = text
    for pat in BANNED_PHRASES:
        out = re.sub(pat, '', out, flags=re.I)
    # Remove emojis
    out = re.sub(r'[\U00010000-\U0010ffff\u2600-\u27bf\u200d\u20e3\u2702-\u27b0]+', '', out)
    # Remove em-dashes, en-dashes
    out = out.replace('—', ', ').replace('–', ', ')
    # Remove "link di bio" variants
    out = re.sub(r'[\(\[]?link\s+(di\s+)?bio[\)\]]?', '', out, flags=re.I)
    
    # Ensure double enter after every sentence
    # Split by sentence endings, rejoin with double newline
    sentences = re.split(r'(?<=[.!?])\s+', out)
    out = '\n\n'.join(s.strip() for s in sentences if s.strip())
    
    return out

def _validate_hook(text: str) -> bool:
    """Check hook is at least 30 words."""
    words = text.split()
    if len(words) < 30:
        print(f"[WARN] Hook too short ({len(words)} words), need 30+")
        return False
    return True

def generate_carousel(title: str, body: str, image: str = "", url: str = "", source: str = "") -> Optional[dict]:
    """Generate 6-slide carousel content."""
    # Try Mistral first, fallback to Groq
    raw = _call_mistral(title, body, source)
    provider = "mistral"
    if raw is None:
        print("Mistral failed, trying Groq...")
        raw = _call_groq(title, body, source)
        provider = "groq"
    if raw is None:
        return None
    
    # Parse JSON
    try:
        # Handle potential markdown code blocks
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned)
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(f"Raw response:\n{raw[:500]}")
        return None
    
    # Validate & clean all slides
    for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
        if key in data:
            data[key] = _clean(data[key])
    
    # Validate hook length
    if "slide_1" in data:
        _validate_hook(data["slide_1"])
    
    # Add provider info
    data["_provider"] = provider
    
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
