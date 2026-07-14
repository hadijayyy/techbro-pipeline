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

# ── Banned lists ──────────────────────────────────────────────────────────
# STYLE: cringe, filler, platform-specific — strip from ALL formats
BANNED_STYLE = [
    r'\bgeleng[- ]geleng\b', r'\bgaruk kepala\b', r'\bkayak dari masa depan\b',
    r'\bgila sih\b', r'\bgila banget\b', r'\bgila kan\b',
    r'\bkebayang gak\b', r'\byang bener aja\b',
    r'\btahan dulu\b', r'\bcoba tebak\b', r'\bciyus\b', r'\bmiyap\b',
    r'\bmuka masam\b', r'\bngebet\b',
    r'\blink di bio\b',  # URL sudah ada di post, gak perlu sebut
    r'\blo tau gak\b', r'\blo tau gak\?\s',  # quiz-show hook, kills engagement
    r'\blu tau gak\b', r'\blu tau gak\?\s',  # same, Lu variant
    r'\blo\b',   # catches lo tau gak + gua lo
    r'\bak\b', r'\bkalian\b', # wrong voice — must be gw/lu
]
# PERSONAL: fabricated attribution markers — strip from carousel + narrative ONLY
# Thread chain intentionally uses first-person, so skip these for that format
BANNED_PERSONAL = [
    r'\bgue\b',  # wrong voice for carousel/narrative
    r'\bkampung gue\b', r'\bkantor gue\b', r'\btemen gue\b', r'\bibunda\b', r'\bibu gue\b',
    r'\bayah gue\b', r'\bak gue\b', r'\bkakak gue\b',
    r'\bkampung gw\b', r'\bkantor gw\b', r'\btemen gw\b', r'\bibu gw\b',
    r'\bayah gw\b', r'\bak gw\b', r'\bkakak gw\b',
]
# BANNED_PHRASES = BANNED_STYLE + BANNED_PERSONAL (carousel/narrative default)
BANNED_PHRASES = BANNED_STYLE + BANNED_PERSONAL

SYSTEM_PROMPT = """#  RCTOE Framework v4 — Indonesian Self-Dev / Tech Edition

## 1. ROLE
Gw "Bro" — Content Creator Threads niche self-improvement + tech Indonesia. Umur 27.
Nulis kayak temen nongkrong abis baca artikel terus langsung reaksi. Bukan news anchor, bukan motivator LinkedIn, bukan akun clickbait.
Campur Indo-Inggris alami. Santai tapi insightful.
Gw STORYTELLER yang nge-EXTRACT tips/actionable lessons — bukan cuma summarize berita.
PAKAI "gw"/"lu" EXCLUSIVELY. TIDAK BOLEH "gue"/"lo"/"aku"/"kalian".

## 2. CONTEXT
Audience: Gen-Z & millennial Indonesia. Tau nama besar (Deddy Corbuzier, Jerome Polin, Elon Musk, ChatGPT) tapi gak ngikutin berita teknis. Scroll cepet.
Mereka bosen sama motivasi kosong. Mau TIPS yang bisa langsung DIPRAKTEKKIN.
Keresahan lokal: budak korporat, gaji UMR, THR, toxic productivity, side hustle, overthinking, quarter life crisis, FOMO.

Goal: Extract GOLD — fakta mengejutkan, advice expert, angka impactful — packaging kayak curhat temen, bukan artikel berita.

## 3. ARTICLE
Lo dikasih 1 artikel. Baca → pilih 1 angle → develop jadi 6-slide story. Jangan summarize seluruh artikel.

## 4. STORY SELECTION
Kalau artikel banyak topik, PILIH SATU angle yang:
1. Punya angka/data spesifik bikin kaget
2. Dampak personal ke pembaca (karir, uang, mental, produktivitas)
3. Counter-intuitive / beda dari asumsi umum
4. Bisa dikaitkan sama keresahan lokal Indonesia
5. Punya tokoh yang dikenal (atau bisa dijelasin 1 kalimat)

Satu post = satu cerita. Jangan 2+ storyline.

## 5. INSIGHT FILTER
Sebelum nulis, cek 3 hal:
1. Ada ANGLE KONTROVERSIAL? (bukan generic recap berita)
2. Ada ANGKA/DATA backing? (bukan klaim kosong)
3. Ada DAMPAK ke pembaca? (uang, karir, skill, mental)

Kalau gak kena 3, cerita gak cukup kuat — cari angle lain.

## 6. VIRAL CRITERIA (WAJIB 2 per slide)
Setiap slide WAJIB hit minimal 2 dari 8:

1. **Pro & Con** — Ada debat? Frame sekitar tensi.
2. **Relatable** — Pembaca peduli? Hubungin ke uang/karir/mental/produktivitas.
3. **Famous figure** — Nama besar di awal stop scroll.
4. **Viral/trending** — Lagi rame? Masuk ke buzz.
5. **Ironi/twist** — Angle lucu/absurd? Pakai.
6. **Surprising fact** — "Gw gak tau itu." Reframe.
7. **Emotional hook** — Marah, simpati, frustasi, harapan. Bikin FEEL.
8. **Absurd detail** — Fakta aneh bikin share. Detail kecil > angka gede.

## 7. WRITING STYLE
- Bahasa: Indonesia gaul campur Inggris natural. Gw/Lu.
- PARAPHRASE quote — JANGAN copy-paste kalimat asli.
- Sebut nama sumber berita minimal 1x di body (credibility).
- Artikel Inggris: tulis ULANG dari nol, jangan translate literal.
- Buku asing obscure → REJECT. Hanya yang orang Indo kenal (Atomic Habits, Filosofi Teras).
- Startup/Influencer: prioritas lokal (Gojek, Tokopedia, Jerome, Deddy).
- JANGAN generate konten promosi produk.

## 8. TASK — 6-SLIDE STORY ARC
Bikin 6 slide. SATU cerita yang MENGALIR. Bukan 6 fakta terpisah.

### SLIDE 1 — HOOK (80% engagement)
[TEPAT] 2 kalimat. <20 kata.
Pattern: [ANGKA SPESIFIK] → [CONSEQUENCE]. DUA-DUANYA WAJIB.
Kalimat 1 = angka mengejutkan. Kalimat 2 = consequence / "ini gue banget".
NO "Di era digital..." / "Lu tau gak?" / intro fluff / "Here's why" / "Bayangin lo..."
CAPS untuk emphasis 1 kata.
WAJIB ada angka. TANPA ANGKA = FAIL. Tes: gak bikin "WTF?!" dalam 2 detik → REWRITE.

Hook patterns (rotate):
1. **CONTRARIAN**: "[Angka/Fakta mengejutkan]. Yang gak orang sadar: [twist]"
2. **DATA DROP**: "[Angka spesifik]. Dampaknya ke lo: [consequence]"
3. **TIMING DROP**: "[Entity] baru aja [past-tense action]. Alasannya: [punchline]"
4. **CONTRAST**: "[Ekspektasi]. Kenyataannya? [Angka mengejutkan]"
5. **REALIZATION**: "Gw baru tau: [angka spesifik]. Artinya buat lo: [consequence]"
6. **CURIOSITY GAP**: "[Angka mengejutkan] — [timing]. The reason? [Open loop]"

❌ JANGAN: "Lu tau gak?" / quiz-show / opinion tanpa angka / generic filler

### SLIDE 2 — CONTEXT
EXACTLY 3 kalimat. <40 kata.
Apa yang terjadi. Situasi realita. 1 insight baru.

### SLIDE 3 — STRUGGLE
EXACTLY 3 kalimat. <40 kata.
Konflik / masalah / "oh ternyata..." / twist yang bikin kaget.

### SLIDE 4 — DEEP (Hard Data)
EXACTLY 3 kalimat. <40 kata.
Perbandingan ANGKA yang bikin KAGET. Market size, revenue, growth — data yang bisa divisualisasikan.
Contoh: "GMV Tokopedia US$9 miliar vs Shopee US$83,2 miliar."
Bikin orang share karena datanya undeniable.

### SLIDE 5 — SO WHAT (Impact Angle)
EXACTLY 3 kalimat. <40 kata.
Frame sebagai isu yang lebih besar — bukan cuma [headline], tapi [dampak sistemik].
PILIH SALAH SATU (sesuai topik):
- **Nasional/Sovereignty:** "Ini bukan cuma soal [perusahaan], tapi [masa depan Indonesia/ekonomi digital]"
- **Personal Lesson:** Satu big lesson yang ACTIONABLE. Bisa langsung dipraktekkin. Pake contoh konkret.

Kalau topiknya bisa di-frame sebagai isu nasional → PAKAI FRAME NASIONAL. Lebih viral.

### SLIDE 6 — CTA (Debate Fire)
TEPAT 1 kalimat pertanyaan + 3 opsi A/B/C.
Format WAJIB:
A) [opsi 1]
B) [opsi 2]
C) [opsi 3]

Pertanyaan yang MEMAKSA orang MILIH dan DEBAT. Gak ada jawaban benar.
Contoh: "5 tahun ke depan, siapa dominasi?"
A) Startup lokal
B) Raksasa asing
C) Kolaborasi keduanya

## 9. CAPTION RULES
2-3 baris. Baris 1 = angka/fakta mengejutkan. Baris 2 = consequence.
Zero emoji. Zero hashtags.
URL sumber di baris terakhir caption. JANGAN "link di bio".

## 10. HOOK REWRITING RULES
Kalau judul generik, rewrite concrete past-tense:
- "Startup XYZ Raises Funding" → "Startup XYZ baru aja dapat Rp500M. Tapi yang menarik bukan dananya..."
- "AI Tool Launches" → "OpenAI baru aja rilis tool baru. Yang bikin kaget: harganya GRATIS."
- "Expert Shares Career Tips" → "Gw baru nyadar: 90% orang salah paham soal promosi karir."

## 11. OUTPUT FORMAT
Return ONLY valid JSON, no markdown fences:
{"slide_1":"", "slide_2":"", "slide_3":"", "slide_4":"", "slide_5":"", "slide_6":"", "caption":""}

Setiap kalimat dalam slide dipisah \\\\n\\\\n (double newline — Threads render \\n sebagai spasi).

## 12. WORKED EXAMPLE

Input: "Waspada Penipuan Pre-Order GTA VI, Hacker Incar Rekening hingga Kripto. Kaspersky menemukan situs web palsu yang meniru PlayStation Store. Korban diminta data pribadi hingga nomor identitas wajib pajak."

Output:
{
  "slide_1": "GTA VI BELUM RILIS, tapi rekening lu udah bisa KOSONG.\\\\n\\\\nKaspersky: penipu manfaatin hype pre-order buat jebak gamer.",
  "slide_2": "Mereka bikin situs palsu mirip PlayStation Store.\\\\n\\\\nKorban diminta data pribadi sampe nomor pajak.\\\\n\\\\nIni bukan phising biasa — ini targeted attack ke gamer.",
  "slide_3": "Modus lain: beta version GTA VI = MALWARE.\\\\n\\\\nKripto token palsu juga beredar buat nyuri wallet lu.\\\\n\\\\nPenipu paham psikologi: rasa takut kehabisan bikin lu klik tanpa mikir.",
  "slide_4": "Situs palsu PlayStation Store vs asli: beda URL 1 karakter.\\\\n\\\\nHarga pre-order palsu: Rp250.000 — harga asli: estimasi Rp1.200.000.\\\\n\\\\nSelisih diskon 80% tapi 100% scam.",
  "slide_5": "Ini bukan cuma soal gamer ketipu.\\\\n\\\\nPenipuan model begini naik 200% sejak hype GTA VI.\\\\n\\\\nIndonesia belum ada regulasi kuat buat lindungin konsumen game digital.",
  "slide_6": "Gamer Indonesia lebih rentan kena scam digital. Setuju?\\\\n\\\\nA) Iya — edukasi cybersecurity masih minim\\\\nB) Enggak — gamer udah pinter bedain scam\\\\nC) Relatif — tergantung platform yang dipake",
  "caption": "GTA VI belum rilis, tapi rekening lu udah bisa kosong.\\\\nKaspersky: penipu manfaatin hype pre-order."
}

## 13. GROUNDING RULES
SEMUA fakta HARUS dari artikel. Never invent.

1. NO INVENTED REASONING — jangan klaim motivasi kecuali artikel bilang.
2. NO EXAGGERATED PARAPHRASING — "called for changes" ≠ "demanded". Preserve strength asli.
3. NO SPECULATIVE CONSEQUENCES — jangan prediksi masa depan.
4. QUOTES word-for-word dari artikel. Paraphrase pakai indirect speech.
5. NO PARTIAL LISTS — include SEMUA item kalau list.
6. RUMOR → bilang eksplisit ("menurut laporan" / "belum dikonfirmasi").
7. TEST EACH SLIDE: "Bisa gw tunjuk kalimat spesifik di artikel?" Kalau gak, hapus.
8. NO INVENTED ANGKA — jangan "setara Rp500 juta" kecuali artikel bilang.
9. NO INVENTED INVOLVEMENT — jangan tambahin tokoh yang gak disebut.
10. PRESERVE HEDGING — "kemungkinan besar" ≠ "pasti".
11. NO FAKE PERSONAL STORIES — artikel tentang orang lain, JANGAN rewrite jadi "ibu gw", "temen gw", "kantor gw". Reaksi lu ke fakta = OK. Fabricate pengalaman lu = REJECT.

## 14. BANNED PATTERNS

[VOICE — ANTI-LINKEDIN]
JANGAN pake corporate/motivational fluff:
- ❌ "self improvement", "keharusan", "investasi terbaik"
- ❌ "keluar dari zona nyaman", "mindset pertumbuhan", "mengubah hidup"
- ❌ "transformasi diri", "karir impian", "sukses itu proses", "mimpi besar"
- ❌ "potensi diri", "versi terbaik", "burn-out", "ubah cara pikir"

PAKAI konteks Indonesia REAL:
- Gaji UMR, THR, side hustle, kerja kantoran, nganggur, freelance
- Mager, scroll TikTok, nge-game, nongkrong, ngopi
- Tekanan ortu, nikah, cicilan, kontrakan, kos-kosan

Pattern: ❌ "Lu bisa mulai dengan hal kecil hari ini." ✅ "Gw mulai dari 1 hal: tiap pagi, gw nulis 1 tujuan sebelum buka HP."

[GENERIC BANS]
JANGAN: "Lu tau gak?" / "Did you know?" / "Let's dive in!" / "Here's the secret" / "Teknologi semakin canggih" / "Di era digital saat ini" / "This is a game-changer" / "Tahukah kamu?" / "Yuk simak!" / "Ini dia rahasianya" / "Shocking!" / "Gila sih!" / "Gila banget!" / "Kebayang gak?" / "Yang bener aja" / AIDA/PAS / Motivational closing lines / "link di bio" / "Let that sink in" / "Say what you want, but..."

## 15. STORYTELLING MODE
Artikel EVENT (launch, PHK, announcement) → cerita dari POV perusahaan/event. JANGAN jadikan self-help tips.
Artikel TREN/RISET → boleh POV personal + lesson.
Fokus: SIAPA, KENAPA, DAMPAKNYA.
- ❌ "AI bisa jadi partner kreatif lo."
- ✅ "Tri baru aja luncurin 3TechMate. Programnya gratis, targetnya anak muda yang cuma tau AI buat nyontek doang."
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
                  "temperature": 0.3, "max_tokens": 3000},
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
                  "temperature": 0.3, "max_tokens": 3000},
            timeout=120)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        print(f"Groq error: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"Groq exception: {e}")
    return None

def _call_narrative(title: str, body: str, source: str = "") -> Optional[str]:
    """Call Mistral with narrative prompt."""
    try:
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
            json={"model": "mistral-large-latest",
                  "messages": [{"role": "system", "content": NARRATIVE_PROMPT},
                               {"role": "user", "content": _build_user_msg(title, body, source)}],
                  "temperature": 0.3, "max_tokens": 3000},
            timeout=120)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        print(f"Mistral error (narrative): {r.status_code}")
    except Exception as e:
        print(f"Mistral exception (narrative): {e}")
    return None

def _call_thread_chain(title: str, body: str, image_url: str = "", source: str = "") -> Optional[str]:
    """Call Mistral with thread chain prompt."""
    try:
        user_msg = _build_user_msg(title, body, source)
        if image_url:
            user_msg += f"\n\nArticle image: {image_url}"
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
            json={"model": "mistral-large-latest",
                  "messages": [{"role": "system", "content": THREAD_CHAIN_PROMPT},
                               {"role": "user", "content": user_msg}],
                  "temperature": 0.3, "max_tokens": 4000},
            timeout=120)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        print(f"Mistral error (thread_chain): {r.status_code}")
    except Exception as e:
        print(f"Mistral exception (thread_chain): {e}")
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


# ── Deterministic fact-check: cross-reference slides against source article ──

_NUM_RE = re.compile(
    r'(?:(?:Rp|IDR|USD|\$)\s?\d[\d.,]*\b'     # currency: Rp500, $1.2M
    r'|\d+[\d.,]*\s*(?:%|persen|persennya)'    # percentages
    r'|\d+[\d.,]*\s*(?:x|kali|lipat)'          # multipliers
    r'|\d+[\d.,]*\s*(?:juta|miliar|triliun|ribu)' # Indonesian large numbers
    r'|\b\d{2,}\b)'                             # standalone 2+ digit numbers
)

_CAP_RE = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b')
_COMMON_CAPS = {
    # English
    'Slide', 'The', 'This', 'That', 'What', 'When', 'Where', 'How', 'Why',
    'But', 'And', 'For', 'Not', 'You', 'Your', 'Its', 'Our', 'Their',
    'Here', 'There', 'Then', 'Now', 'Still', 'Also', 'Just', 'Even',
    'Can', 'Will', 'May', 'Let', 'Get', 'Got', 'Has', 'Had', 'Was',
    'Are', 'Were', 'Been', 'Being', 'Does', 'Did', 'Could', 'Would',
    'Should', 'Might', 'Must', 'All', 'Any', 'Each', 'Every', 'Both',
    'Few', 'More', 'Most', 'Other', 'Some', 'Such', 'Than', 'Too',
    'Very', 'Own', 'Same', 'Only', 'After', 'Before', 'Between',
    'Under', 'Over', 'Into', 'Through', 'About', 'Against', 'Among',
    # Indonesian
    'Apa', 'Ini', 'Itu', 'Kalau', 'Karena', 'Tapi', 'Maka', 'Jadi',
    'Bukan', 'Sudah', 'Belum', 'Masih', 'Hanya', 'Setiap', 'Semua',
    'Pertama', 'Kedua', 'Ketiga', 'Menurut', 'Selain', 'Tanpa',
    'Cara', 'Yang', 'Dengan', 'Dari', 'Ke', 'Di', 'Pada', 'Untuk',
    'Atau', 'Serta', 'Namun', 'Meski', 'Walaupun', 'Sebelum', 'Sesudah',
    'Setelah', 'Ketika', 'Saat', 'Hingga', 'Sampai', 'Lalu', 'Kemudian',
    'Aman', 'Rentan', 'Gak', 'Gagal', 'Jumlah', 'Update', 'Beli',
    'Pasrah', 'Pilih', 'Mau', 'Bisa', 'Bakal', 'Perlu', 'Harus',
    'Coba', 'Kasih', 'Langsung', 'Biar', 'Supaya', 'Agar',
    'Jangan', 'Dulu', 'Lagi', 'Saja', 'Aja', 'Kok', 'Lah', 'Deh',
    'Dong', 'Sih', 'Nih', 'Tuh', 'Nah', 'Wah', 'Adoh', 'Eh',
    'Oleh', 'Antara', 'Melalui', 'Terhadap', 'Mengenai', 'Tentang',
    'Data', 'Hasil', 'Fakta', 'Masalah', 'Solusi', 'Contoh',
    'Alasan', 'Dampak', 'Resiko', 'Manfaat', 'Tujuan', 'Proses',
    # CTA patterns
    'A)', 'B)', 'C)', 'Option',
    # Countries/regions (local relevance is intentional)
    'Indonesia', 'Jakarta', 'Asia', 'Amerika', 'Eropa', 'China',
    'Amerika', 'Serikat', 'Inggris', 'Jepang', 'Korea', 'India',
    'Singapura', 'Malaysia', 'Thailand', 'Vietnam', 'Filipina',
    # Common tech entities
    'Google', 'Apple', 'Microsoft', 'Amazon', 'Meta', 'Facebook',
    'Twitter', 'Instagram', 'TikTok', 'YouTube', 'Netflix',
    'OpenAI', 'ChatGPT', 'Claude', 'Copilot', 'Gemini',
    'Gojek', 'Grab', 'Tokopedia', 'Shopee', 'Traveloka',
    'Bukalapap', 'Blibli', 'BRI', 'BCA', 'Mandiri',
}


def _verify_against_source(slides: dict, article_text: str, title: str = "") -> list:
    """Deterministic fact-check: extract numbers + named entities from slides,
    verify each exists in the source article.

    Returns list of violations: [{slide, type, value, found}]
    """
    if not article_text:
        return []

    art_lower = article_text.lower()
    art_digits = re.sub(r'[^\d]', '', art_lower)  # digits-only for number matching

    violations = []

    for key in ['slide_1', 'slide_2', 'slide_3', 'slide_4', 'slide_5', 'slide_6']:
        text = slides.get(key, '')
        if not text:
            continue

        # Strip URLs before checking (avoid URL path number false positives)
        text = re.sub(r'https?://\S+', '', text).strip()

        # ── Check numbers ──
        for match in _NUM_RE.finditer(text):
            num = match.group().strip()
            digit_core = re.search(r'[\d.,]+', num)
            if not digit_core:
                continue
            digits = re.sub(r'[^\d]', '', digit_core.group())
            if not digits or len(digits) < 2:
                continue
            # Allow year numbers and common small numbers
            if int(digits) in {2024, 2025, 2026}:
                continue
            if digits not in art_digits:
                violations.append({
                    'slide': key, 'type': 'number',
                    'value': num, 'found': False
                })

        # ── Check named entities ──
        for match in _CAP_RE.finditer(text):
            entity = match.group(1)
            if entity in _COMMON_CAPS:
                continue
            if entity.lower() not in art_lower:
                if title and entity.lower() in title.lower():
                    continue
                violations.append({
                    'slide': key, 'type': 'entity',
                    'value': entity, 'found': False
                })

    return violations

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
        
        # Strip emoji (Zero emoji policy — caption rules)
        text = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF\U0000FE0F\U0000200D\U00002764\U0000FE0F]+', '', text)
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
        
        # ENFORCE SLIDE LENGTH LIMITS (Pressbox style)
        if key in ('slide_1',):
            # Hook: MAX 2 sentences, <25 words
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
            if len(sentences) > 2:
                text = ' '.join(sentences[:2])
                print(f"  [POSTPROCESS] Hook truncated to 2 sentences")
            words = text.split()
            if len(words) > 25:
                text = ' '.join(words[:25])
                if not text.endswith(('.', '!', '?')):
                    text += '...'
                print(f"  [POSTPROCESS] Hook truncated to 25 words")
        elif key in ('slide_2', 'slide_3', 'slide_4', 'slide_5'):
            # Body: MAX 3 sentences, <40 words
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
            if len(sentences) > 3:
                text = ' '.join(sentences[:3])
                print(f"  [POSTPROCESS] {key} truncated to 3 sentences")
            words = text.split()
            if len(words) > 40:
                text = ' '.join(words[:40])
                if not text.endswith(('.', '!', '?')):
                    text += '...'
                print(f"  [POSTPROCESS] {key} truncated to 40 words")
        elif key == 'slide_6':
            # CTA: MAX 2 sentences, <30 words
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
            if len(sentences) > 2:
                text = ' '.join(sentences[:2])
                print(f"  [POSTPROCESS] CTA truncated to 2 sentences")
            words = text.split()
            if len(words) > 30:
                text = ' '.join(words[:30])
                if not text.endswith(('.', '!', '?')):
                    text += '...'
                print(f"  [POSTPROCESS] CTA truncated to 30 words")
        
        # Strip placeholders
        text = re.sub(r'\[.*?\]', '', text)
        
        # Add whitespace between sentences
        text = _add_whitespace(text)
        
        # Trim
        text = text.strip()
        
        # Auto-trim body slides (2-5) to max 3 sentences — matches Pressbox
        slide_num = int(key.split('_')[1]) if key.startswith('slide_') else 0
        if slide_num >= 2 and slide_num <= 5:
            n = _count_sentences(text)
            if n > 3:
                parts = re.split(r'(?<=[.!?])\s+', text.strip())
                text = " ".join(parts[:3])
                text = _add_whitespace(text)
        
        # Char cap (350 per slide — tighter than old 500, matches Pressbox density)
        if len(text) > 350:
            # Try trimming to 3 sentences first
            parts = re.split(r'(?<=[.!?])\s+', text.strip())
            text = " ".join(parts[:3])
            text = _add_whitespace(text)
            if len(text) > 350:
                text = text[:347] + "..."
        
        slides[key] = text
    
    # Re-append source_url to slide_6 (CTA)
    if source_url and 'slide_6' in slides:
        slides['slide_6'] = slides['slide_6'].rstrip() + "\n\n" + source_url
    
    # Check hook word count
    hook = slides.get('slide_1', '')
    word_count = len(hook.split())
    if word_count < 15:
        print(f"[WARN] Hook too short ({word_count} words), need 15+")
    elif word_count > 25:
        print(f"[WARN] Hook too long ({word_count} words), should be <25")
    
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

    # ── Layer 5: Deterministic fact-check against source ──
    violations = _verify_against_source(slides, body, title)
    if violations:
        num_violations = [v for v in violations if v['type'] == 'number']
        ent_violations = [v for v in violations if v['type'] == 'entity']
        for v in violations[:5]:  # log max 5
            print(f"  [FACT-CHECK] ⚠️ {v['slide']}: {v['type']} '{v['value']}' not in source")
        # Hard reject if 3+ ungrounded numbers (likely hallucinated stats)
        if len(num_violations) >= 3:
            print(f"  [FACT-CHECK] REJECT: {len(num_violations)} ungrounded numbers")
            return None
        # Warn but allow if only entities (could be general knowledge)
        if len(violations) >= 5:
            print(f"  [FACT-CHECK] WARN: {len(violations)} total violations (passing to evaluator)")

    # Gate: evaluator check (anti-hallucination, fail-safe)
    if MISTRAL_KEY:
        eval_result = evaluate_slides(slides, title, body)
        if eval_result["status"] == "REJECT":
            print(f"  [EVALUATOR] REJECTED: {eval_result['reason'][:100]}")
            return None

    return slides


# ─── A/B Testing: Generate 3 hook variants, pick best ─────────────────

_HOOK_PATTERNS = [
    "REALIZATION: \"Gw baru nyadar... [fakta mengejutkan]\"",
    "OPINION: \"Jujur, gw [emotion] soal [topik]. [Fakta]\"",
    "CONTRAST: \"[Ekspektasi]... Tapi kenyataannya? [Realita]\"",
    "DATA DROP: \"[Angka spesifik] orang [konteks]. Lu termasuk?\"",
    "QUOTE: \"[Nama] bilang: '[insight]'. Dan ini bener banget.\"",
    "CONTRARIAN: \"[Fakta umum] tapi [twist melawan asumsi]\"",
]

def generate_ab_variants(title: str, body: str, image_url: str = "", source_url: str = "", source: str = "", n_variants: int = 3, format: str = "carousel") -> Optional[dict]:
    """Generate n_variants with different hook patterns, pick best by hook quality.
    
    format: "carousel" (6-slide), "narrative" (single post), "thread_chain" (10-slide)
    """
    import random
    
    if not MISTRAL_KEY:
        if format == "narrative":
            return generate_narrative_post(title, body, source_url, source)
        elif format == "thread_chain":
            return generate_thread_chain(title, body, image_url, source_url, source)
        else:
            return generate_carousel(title, body, image_url, source_url, source)
    
    # Pick n random hook patterns
    patterns = random.sample(_HOOK_PATTERNS, min(n_variants, len(_HOOK_PATTERNS)))
    
    variants = []
    for i, pattern in enumerate(patterns, 1):
        print(f"  [A/B] Variant {i}/{len(patterns)}: {pattern[:40]}...")
        
        # Build modified prompt with specific hook instruction
        hook_instruction = f"\n\n[A/B OVERRIDE] For Slide 1 (Hook), use THIS pattern: {pattern}"
        
        # Generate with format-appropriate function
        if format == "narrative":
            raw = _call_narrative(title, body + hook_instruction, source)
        elif format == "thread_chain":
            raw = _call_thread_chain(title, body + hook_instruction, image_url, source)
        else:
            raw = _call_mistral(title, body + hook_instruction, source)
        
        if not raw:
            continue
        
        slides = _parse_json(raw)
        if not slides:
            continue
        
        slides = _postprocess_slides(slides, source_url)
        
        # Score hook quality (simple heuristic)
        hook = slides.get("slide_1", "") or slides.get("hook", "") or ""
        hook_score = 0
        # Has number/specific data
        if re.search(r'\d', hook):
            hook_score += 3
        # Has CAPS emphasis
        if re.search(r'[A-Z]{3,}', hook):
            hook_score += 2
        # Word count sweet spot (20-35)
        wc = len(hook.split())
        if 20 <= wc <= 35:
            hook_score += 2
        elif 15 <= wc <= 40:
            hook_score += 1
        # Has question mark (engagement)
        if '?' in hook:
            hook_score += 1
        
        variants.append((hook_score, slides))
        print(f"    Hook score: {hook_score}, words: {wc}")
    
    if not variants:
        print("  [A/B] All variants failed, falling back to single generation")
        if format == "narrative":
            return generate_narrative_post(title, body, source_url, source)
        elif format == "thread_chain":
            return generate_thread_chain(title, body, image_url, source_url, source)
        else:
            return generate_carousel(title, body, image_url, source_url, source)
    
    # Pick best variant by hook_score, but evaluate for hallucination
    # Try candidates in descending hook_score order until one passes
    variants.sort(key=lambda x: x[0], reverse=True)
    for rank, (score, slides) in enumerate(variants, 1):
        print(f"  [A/B] Candidate #{rank}: hook_score={score}")

        # Evaluator gate
        if MISTRAL_KEY:
            eval_result = evaluate_slides(slides, title, body)
            if eval_result["status"] == "REJECT":
                print(f"  [A/B] REJECTED candidate #{rank}: {eval_result['reason'][:80]}")
                continue
            print(f"  [A/B] APPROVED candidate #{rank}")

        print(f"  [A/B] Winner: hook_score={score} from {len(variants)} variants")
        return slides

    # All candidates failed evaluation — fall back to single generation (self-evaluates)
    print(f"  [A/B] All {len(variants)} variants rejected, falling back to single generation")
    if format == "narrative":
        return generate_narrative_post(title, body, source_url, source)
    elif format == "thread_chain":
        return generate_thread_chain(title, body, image_url, source_url, source)
    else:
        return generate_carousel(title, body, image_url, source_url, source)


def evaluate_slides(slides: dict, title: str, body: str, score: int = 0) -> dict:
    """Independent LLM review of generated slides.
    
    Returns:
        {"status": "APPROVE"|"REVISE"|"REJECT", "reason": str, "revised_slides": dict|None}
    
    Uses mistral-small-latest (cheap, different model than generator).
    """
    # ═══ DETERMINISTIC PRE-CHECK (no API call) ═══
    # Detect format early for pre-check text extraction
    is_thread_chain = slides.get("_format") == "thread_chain" or "slides" in slides
    is_narrative = slides.get("_format") == "narrative"

    if is_thread_chain:
        slide_list = slides.get("slides", [])
        all_slide_text = "\n".join(slide_list)
    elif is_narrative:
        all_slide_text = slides.get("hook", slides.get("caption", ""))
    else:
        all_slide_text = "\n".join([str(slides.get(f"slide_{i}", "")) for i in range(1, 7)])
    voice_bans = [
        (r'\bgue\b', '"gue" instead of "gw"'),
        (r'\blo tau gak\b|\blu tau gak\b', '"lo/lu tau gak?" banned quiz-show hook'),
        (r'\bak\b', '"ak" instead of "gw"'),
        (r'\bkalian\b', '"kalian" instead of "lu/lu semua"'),
        (r'\baku\b', '"aku" instead of "gw"'),
    ]
    for pat, desc in voice_bans:
        if re.search(pat, all_slide_text, re.IGNORECASE):
            print(f"  [EVALUATOR] PRE-CHECK REJECT: {desc}")
            return {"status": "REJECT", "reason": f"Voice violation: {desc}", "grounding_score": 0, "issues": [desc], "revised_slides": None}
    
    # Cap-only check: keyword density abuse ("LU PASTI", "INI FAKTA", etc.)
    # Count INDIVIDUAL all-caps words (4+ chars), not sequences
    caps_words = re.findall(r'\b[A-Z]{4,}\b', all_slide_text)
    if len(caps_words) > 6:
        print(f"  [EVALUATOR] PRE-CHECK REJECT: excessive ALL-CAPS ({len(caps_words)} words: {caps_words[:5]})")
        return {"status": "REJECT", "reason": f"Excessive ALL-CAPS: {len(caps_words)} words", "grounding_score": 0, "issues": [f"{len(caps_words)} ALL-CAPS words"], "revised_slides": None}
    # Always run evaluator — no skip threshold
    # (Old: skip ≥100, but grounding issues found even at high scores)

    # Detect format: thread_chain uses slides list, narrative uses hook/caption, carousel uses slide_1..slide_6 keys
    is_thread_chain = slides.get("_format") == "thread_chain" or "slides" in slides
    is_narrative = slides.get("_format") == "narrative"
    
    if is_thread_chain:
        slide_list = slides.get("slides", [])
        slide_text = "\n".join([f"Slide {i+1}: {t}" for i, t in enumerate(slide_list)])
    elif is_narrative:
        slide_text = slides.get("hook", slides.get("caption", ""))
    else:
        slide_text = "\n".join([f"Slide {i}: {slides.get(f'slide_{i}', '')}" for i in range(1, 7)])
    
    if is_thread_chain:
        evaluator_prompt = f"""You are a skeptical content reviewer. Review this {len(slide_list)}-slide thread chain against the source article.

ARTICLE TITLE: {title}

ARTICLE EXCERPT: {body[:2000]}

GENERATED SLIDES:
{slide_text}

YOUR TASK:
1. **GROUNDING CHECK** — Every claim, fact, statistic, recommendation, dialogue MUST come from the article. No invented quotes, stats, or first-person experiences.
2. **FAKE PERSONAL STORY** — If article is about someone else's experience, slides MUST NOT rewrite as "gw baru aja...", "temen gw...", "kantor gw...". Reacting to facts = OK. Fabricating = REJECT.
3. **NUMBER CHECK** — Any specific number or statistic MUST be verifiable in the article. "\"BEST SELLER\"", "\"terlaris\"", "\"paling populer\"" without source numbers = REJECT.

AUTO-REJECT TRIGGERS:
- Inventing statistics or facts not in article
- Fabricating dialogue or quotes
- Rewriting third-person experience as first-person
- "\"Lu tau gak?\"" quiz-show pattern
- Recommending products/books not mentioned in article

Return JSON:
{{
    "status": "APPROVE" or "REVISE" or "REJECT",
    "reason": "brief explanation",
    "issues": ["list of specific issues found"],
    "grounding_score": 0-10 (how many claims are grounded in article),
    "revised_slides": null
}}

Be SKEPTICAL. Default to REJECT if unsure. Hallucination = automatic REJECT.""" 
    elif is_narrative:
        evaluator_prompt = f"""You are a skeptical content reviewer for Indonesian Threads posts. Review this SINGLE narrative post against the source article.

ARTICLE TITLE: {title}

ARTICLE EXCERPT: {body[:2000]}

GENERATED POST:
{slide_text}

YOUR TASK:
1. **GROUNDING CHECK** — Every claim, fact, statistic MUST come from the article. No invented numbers, quotes, or recommendations.
2. **NUMBER CHECK** — Any specific number MUST be verifiable in the article. "BEST SELLER", "terlaris", "populer" without source numbers = REJECT.
3. **FAKE PERSONAL STORY** — If article is about someone else (CEO, founder, expert), post MUST NOT rewrite as "gw baru aja...", "temen gw...", "kantor gw...". The "Bro" persona can REACT to facts ("gw kaget pas baca..."), but cannot FABRICATE first-person experience of someone else's story.
4. **DIALOGUE CHECK** — Any dialogue/quote in the post MUST come from the article. Invented conversations = REJECT.
5. **VOICE CHECK** — Must use gw/lu consistently. No gue/lo/aku/kalian.
6. **STRUCTURE CHECK** — Narrative = single flowing story. Must have: hook with angka → context → twist/insight → lesson → open loop. Not just summarizing article.
7. **LOCAL RELEVANCE** — Foreign concepts must be reframed for Indonesian audience (gaji UMR, side hustle, kos-kosan, cicilan).

AUTO-REJECT TRIGGERS:
- Inventing statistics or facts not in article
- Fabricating dialogue or quotes not in article
- Rewriting third-person experience as first-person
- "Lu tau gak?" quiz-show pattern
- Recommending products/books not mentioned in article
- Generic advice with no source grounding
- Post shorter than 200 characters (too thin)

Return JSON:
{{
    "status": "APPROVE" or "REVISE" or "REJECT",
    "reason": "brief explanation",
    "issues": ["list of specific issues found"],
    "grounding_score": 0-10 (how many claims are grounded in article),
    "revised_slides": null or {{"hook": "revised text"}} if REVISE
}}

Be SKEPTICAL. Default to REJECT if unsure. Hallucination = automatic REJECT."""
    else:
        evaluator_prompt = f"""You are a skeptical content reviewer for Threads posts targeting Indonesian audience. Review the following 6-slide carousel against the RCTOE v4 framework.

STRUCTURE: Slide 1=Hook, Slide 2=Context, Slide 3=Struggle, Slide 4=Deep (Hard Data), Slide 5=So What, Slide 6=CTA (A/B/C debate).

ARTICLE TITLE: {title}

ARTICLE EXCERPT: {body[:2000]}

GENERATED SLIDES:
{slide_text}

YOUR TASK:
1. **GROUNDING CHECK** — Every claim, fact, statistic, recommendation MUST come from the article. "BEST SELLER" without a number = REJECT.
2. **STORY ARC** — Check slides flow Hook→Context→Struggle→Deep→So What→CTA. Not 6 random facts. Each slide builds on previous.
3. **HOOK CHECK** — Slide 1: TEPAT 2 kalimat, <20 kata. WAJIB angka spesifik + consequence. NO "Lu tau gak?" / intro fluff / solo opinion. TANPA ANGKA = REJECT.
4. **CONTEXT CHECK** — Slide 2: EXACTLY 3 sentences, <40 words. What happened + 1 new insight.
5. **STRUGGLE CHECK** — Slide 3: conflict/twist/"oh ternyata..." moment. EXACTLY 3 sentences.
6. **DEEP CHECK** — Slide 4: HARD COMPARISON DATA. Perbandingan angka yang bikin kaget (market size, revenue, growth). Bukan dampak subjektif.
7. **SO WHAT CHECK** — Slide 5: Either national/sovereignty angle ("bukan cuma soal [X], tapi [masa depan Indonesia]") OR actionable personal lesson. Pilih sesuai topik.
8. **CTA CHECK** — Slide 6: TEPAT 1 pertanyaan + A/B/C opsi debate. Format: formatted on separate lines. Bikin orang milih dan debat. Not single question.
9. **LOCAL RELEVANCE** — Indonesian audience: local books/startups/influencers preferred. Foreign only if widely known (Atomic Habits). 2/3 recs must be local.
10. **RELATABILITY** — Flag Western concepts: "winter blues", "Thanksgiving", "Super Bowl", "401k", "credit score", "Ivy League", "prom night". REJECT unless reframed with local equivalent.
11. **FAKE PERSONAL STORY** — If article is about someone else's experience, slides MUST NOT rewrite as "ibu gw", "temen gw", "kantor gw". React to facts = OK. Fabricate = REJECT.
12. **ANTI-LINKEDIN** — Check for banned corporate fluff: "self improvement", "keharusan", "keluar zona nyaman", "mindset pertumbuhan", "transformasi diri", etc. Must use Indonesian real context (gaji UMR, side hustle, mager, cicilan, kos-kosan).

APPROVAL CRITERIA:
- APPROVE: All facts grounded, story arc flows, hook has angka+consequence, Deep has hard data, CTA is A/B/C debate
- REVISE: Minor issues (wording, flow, 1 grounding gap) but salvageable
- REJECT: Major hallucination, invented facts, broken narrative, hook tanpa angka, Deep=subjective, CTA=bukan A/B/C

AUTO-REJECT TRIGGERS:
- Inventing statistics ("BEST SELLER", "terlaris", "populer") without article data
- Slide 1 without specific number ("tanpa angka")
- Slide 4 as subjective impact instead of hard data comparison
- Slide 6 without A/B/C debate format
- "Lu tau gak?" or any quiz-show pattern in ANY slide
- Recommending 2+ books/products not mentioned in article
- Generic advice with no source grounding
- Western concept as main angle with no local reframing
- Fake personal story: rewriting someone else's experience as first-person
- LinkedIn corporate fluff vocabulary

Return JSON:
{{
    "status": "APPROVE" or "REVISE" or "REJECT",
    "reason": "brief explanation",
    "issues": ["list of specific issues found"],
    "grounding_score": 0-10 (how many claims are grounded in article),
    "arc_score": 0-10 (how well the 6-slide story arc flows Hook→Context→Struggle→Deep→So What→CTA debates),
    "revised_slides": null or {{"slide_1":"...",...}} if REVISE
}}

Be SKEPTICAL. Default to REJECT if unsure. Hallucination = automatic REJECT."""
    
    # ── Evaluator API call with retry ────
    import time as _time
    for attempt in range(1, 4):
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
                print(f"  [EVALUATOR] API error: {r.status_code} (attempt {attempt}/3)")
                if attempt < 3:
                    _time.sleep(2 * attempt)  # exponential backoff
                    continue
                return {"status": "REJECT", "reason": f"api_error_fail_safe (HTTP {r.status_code})", "grounding_score": 0, "issues": [f"Evaluator API HTTP {r.status_code}"], "revised_slides": None}
        
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
            print(f"  [EVALUATOR] Error (attempt {attempt}/3): {e}")
            if attempt < 3:
                _time.sleep(2 * attempt)
                continue
            return {"status": "REJECT", "reason": f"exception_fail_safe: {e}", "grounding_score": 0, "issues": [str(e)], "revised_slides": None}


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

    result = {"hook": text, "caption": text, "_format": "narrative"}

    # ── Layer 5: Deterministic fact-check against source ──
    violations = _verify_against_source({"slide_1": text}, body, title)
    if violations:
        for v in violations[:5]:
            print(f"  [FACT-CHECK] ⚠️ {v['type']} '{v['value']}' not in source")
        num_v = [v for v in violations if v['type'] == 'number']
        if len(num_v) >= 3:
            print(f"  [FACT-CHECK] REJECT: {len(num_v)} ungrounded numbers in narrative")
            return None

    # Gate: evaluator check (anti-hallucination, fail-safe)
    if MISTRAL_KEY:
        eval_result = evaluate_slides(result, title, body)
        if eval_result["status"] == "REJECT":
            print(f"  [EVALUATOR] REJECTED: {eval_result['reason'][:100]}")
            return None

    return result


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
   ⚠️ TAPI: Kalau artikel tentang pengalaman/pencapaian ORANG LAIN (CEO, founder, expert), JANGAN karang ulang jadi "gw baru aja". Tetap first person tapi sebagai REAKSI/OPINI terhadap fakta di artikel, bukan sebagai pelaku. Contoh: "Gw baca artikel tentang [nama] yang [fakta]. Gila sih..." — bukan: "Gw baru aja [fakta orang lain]".
2. WAJIB ada dialogue dengan tanda kutip di slide 1-3, 7-8
   ⚠️ TAPI: Dialogue HARUS disadur dari artikel (atau bisa diverifikasi di sumber). JANGAN karang dialogue palsu. Kalau gak ada kutipan langsung di sumber, pakai paraphrase: "[Nama] bilang intinya: [poin]".
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
            # Postprocess: strip markdown, STYLE-only banned phrases (thread chain uses first-person)
            val = re.sub(r'\*\*(.+?)\*\*', r'\1', val)
            val = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', val)
            val = val.replace('—', ', ').replace('–', ', ')
            for pat in BANNED_STYLE:
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

    # ── Layer 5: Deterministic fact-check against source ──
    slides_dict = {}
    for i, s in enumerate(result["slides"], 1):
        slides_dict[f"slide_{i}"] = s
    violations = _verify_against_source(slides_dict, body, title)
    if violations:
        for v in violations[:5]:
            print(f"  [FACT-CHECK] ⚠️ {v['slide']} {v['type']} '{v['value']}' not in source")
        num_v = [v for v in violations if v['type'] == 'number']
        if len(num_v) >= 3:
            print(f"  [FACT-CHECK] REJECT: {len(num_v)} ungrounded numbers in thread chain")
            return None

    # ═══ EVALUATOR CHECK (anti-hallucination) ═══
    eval_result = evaluate_slides(slides_dict, title, body)
    if eval_result["status"] == "REJECT":
        print(f"  [EVALUATOR] REJECTED: {eval_result['reason'][:100]}")
        return None

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
