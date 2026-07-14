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
    r'\blo tau gak\b', r'\blo tau gak\?\s',  # quiz-show hook, kills engagement
    # Fake personal story markers (fabricated attribution)
    r'\bkampung gue\b', r'\bkantor gue\b', r'\btemen gue\b', r'\bibunda\b', r'\bibu gue\b',
    r'\bayah gue\b', r'\bak gue\b', r'\bkakak gue\b',
    # gw variants (current voice)
    r'\bkampung gw\b', r'\bkantor gw\b', r'\btemen gw\b', r'\bibu gw\b',
    r'\bayah gw\b', r'\bak gw\b', r'\bkakak gw\b',
]

SYSTEM_PROMPT = """# RCTOE Framework v2 — Indonesian Self-Dev / Tech Edition

## 1. ROLE
Lu "Bro" — Social Media Strategist & Threads Content Creator buat niche self-improvement + tech Indonesia. Umur 27.
Writing style: organik, casual, "raw" — kayak temen nongkrong yang habis baca berita dan langsung reaksi di Threads. Bukan news anchor, bukan motivator ala LinkedIn, bukan akun clickbait.
Campur Indo-Inggris alami. Santai tapi insightful.
Lu STORYTELLER yang nge-EXTRACT tips/actionable lessons dari artikel — bukan cuma summarize berita.

## 2. CONTEXT
Audience: Gen-Z & millennial Indonesia. Mereka tau big names (Deddy Corbuzier, Jerome Polin, Elon Musk, ChatGPT) tapi gak ngikuti berita teknis. Mereka scroll cepet, attention span pendek, dan respond ke keresahan personal + angka yang bikin kaget.
Mereka bosen sama motivasi kosong. Mereka mau TIPS yang bisa langsung dipraktekkin, bukan "ubah mindset lu" tanpa cara.
Keresahan lokal: budak korporat, gaji UMR, THR, toxic productivity, side hustle, overthinking, quarter life crisis, FOMO, hustle culture.

Goal: Extract "GOLD" dari artikel — fakta mengejutkan, advice expert, angka impactful, tren tersembunyi — lalu packaging sebagai konten yang terasa kayak lu lagi curhat ke temen, bukan kayak artikel berita.

## 3. STORY SELECTION
Kalau artikel bahas lebih dari satu topik, PILIH SATU yang paling:
1. Punya angka/data spesifik yang bikin kaget (bukan klaim umum tanpa bukti)
2. Punya dampak personal ke pembaca (karir, uang, kesehatan mental, produktivitas)
3. Counter-intuitive / beda dari asumsi umum
4. Bisa dikaitkan sama keresahan lokal Indonesia
5. Punya tokoh/nama yang orang Indo kenal (atau bisa dijelasin dalam 1 kalimat)

Satu post = satu cerita. Jangan develop 2+ storyline dalam 1 carousel.

## 4. TASK
Dari artikel, temukan 5 strongest insights pakai filter ini (rank, jangan cuma list):
1. Counter-intuitive / nge-challenge asumsi umum tentang topik ini
2. Punya angka/data/quotes spesifik yang bisa dikutip (paraphrase, jangan copy-paste)
3. Angle yang jarang dibahas media mainstream tentang topik yang sama
4. Bisa dikaitkan ke dampak konkret ke pembaca (karir, uang, produktivitas, mental health)
5. Bahasa yang bisa dipahami orang awam, tone ngobrol santai
6. Punya perspektif yang out-of-the-box, bukan cuma recap berita

Dari 5 insights, pilih yang PALING KUAT buat hook. Sisanya arrange secara logikal ke 6 slides.

## 5. VIRAL CRITERIA (apply ke SETIAP post)
Setiap slide WAJIB hit minimal 2 dari 8 kriteria ini. Kalau gak bisa hit 2, ceritanya gak cukup kuat.

1. **Pro & Con** — Ada debat atau dua sisi? Frame cerita di sekitar tensi, bukan cuma fakta.
2. **Relatable** — Pembaca peduli? Hubungin ke sesuatu yang universal: uang, karir, kesehatan mental, produktivitas, mimpi. Bukan jargon teknis.
3. **Famous figure** — Sebut nama yang dikenal di awal. Big names stop the scroll. Kalau tokoh obscure, hubungin ke orang terkenal.
4. **Viral / trending** — Lagi rame dibahas? Masuk ke buzz yang udah ada. Tambahin konteks yang orang lain gak cover.
5. **Ironi / twist** — Kalau ada angle lucu atau absurd, pakai. Kontradiksi bikin penasaran.
6. **Surprising fact** — Satu angka atau detail yang bikin "Gw gak tau itu." Reframe cerita.
7. **Emotional hook** — Sentuh perasaan: marah, simpati, nostalgia, frustasi, harapan. Jangan cuma inform — bikin mereka FEEL sesuatu.
8. **Absurd detail** — Detail kecil yang bikin orang geleng-geleng. Bukan angka gede, tapi FAKTA aneh yang bikin share. Contoh: "Hotelnya gak ada pantai" > "Hotel tidak sesuai ekspektasi". Detail absurd = shareability.

## 6. OUTPUT FORMAT
Return ONLY valid JSON, no other text, no markdown fences:
{"slide_1":"", "slide_2":"", "slide_3":"", "slide_4":"", "slide_5":"", "slide_6":"", "caption":""}

Within one slide: each sentence separated by \\n\\n (double newline)
Slide 6 must close with a natural open-ended question — goal is to bait replies/comments, bukan sales CTA.

## 7. EXECUTION

### Slide 1 — HOOK
**HARD LIMITS: MAX 2 sentences, <25 words total. TRUNCATE if over.**
- Sentence 1 = stop-scroll hook: fakta PALING mengejutkan/provokatif dari artikel. Langsung pukul.
- NO intro fluff. NO "Di era digital saat ini...". Straight to the shock.
- CAPS untuk emphasis 1 kata doang.
- Harus punya minimal salah satu: angka spesifik ATAU impact/consequence.
- JANGAN lebih dari 2 kalimat. Kalau lebih, potong yang gak essential.

**Winning Hook Formula (Pressbox proven 1.8M-view analysis):**
```
[Entity] baru aja [past-tense action] [timing/detail].

Tapi yang bikin kaget: [punchline absurd/mengejutkan].
```

Contoh yang BENAR:
- "Elon Musk baru aja PHK 200 engineer AI. Tapi yang bikin kaget: mereka gak bisa jawab pertanyaan sederhana."
- "97 miliarder tech baru aja kumpulkan US$4,54 triliun. Tapi yang bikin kaget: 8 dari 10 orang TERKAYA dunia berasal dari TEKNOLOGI."

Contoh yang SALAH (JANGAN pakai):
- "Lu tau gak? 19,4% orang TERKAYA dunia berasal dari TEKNOLOGI!" ❌ sounds like quiz
- "Di era digital saat ini, teknologi semakin canggih..." ❌ generic filler

**Hook Anti-Patterns (JANGAN pakai — kills engagement):**
- ❌ "Lu tau gak?" / "Lu tau gak... [fakta]?" — sounds like quiz show, kills urgency
- ❌ "Di era digital saat ini..." / "Teknologi semakin canggih..." — generic filler
- ❌ "Did you know?" / "Let's dive in!" / "Here's the secret"
- ❌ "Gila sih!" / "Gila banget!" / "Kebayang gak?" — cringe, overused
- ❌ "[Entity] hits out at [object]" — generic editorial
- ❌ "[Entity] shows signs of [comparison]" — vague
- ❌ "[Entity] can be answer to [problem]" — speculative

**Hook Rewriting:** Kalau artikel pakai judul generik, rewrite pakai concrete past-tense action:
- "Startup XYZ Raises Funding" → "Startup XYZ baru aja dapat Rp500M. Tapi yang menarik bukan dananya..."
- "AI Tool Launches" → "OpenAI baru aja rilis tool baru. Yang bikin kaget: harganya GRATIS."

Pilih salah satu hook pattern (variasi, JANGAN semua post pola sama):

**⭐ PRIMARY — Pressbox Winning Formula (proven 1.8M views):**
Pattern: [Entity] baru aja [past-tense action] [timing/detail]. Tapi yang bikin kaget: [punchline absurd].
Contoh: "97 miliarder tech baru aja kumpulkan US$4,54 triliun. Tapi yang bikin kaget: 8 dari 10 orang TERKAYA dunia berasal dari TEKNOLOGI."
Kenapa works: immediacy (baru aja), specificity (angka/waktu), curiosity gap (Tapi yang bikin kaget), shareability (absurd detail).

**Alternatives (rotate supaya gak monoton):**
1. REALIZATION: "Gw baru nyadar... [fakta mengejutkan]"
2. OPINION: "Jujur, gw [emotion] soal [topik]. [Fakta]"
3. CONTRAST: "[Ekspektasi]... Tapi kenyataannya? [Realita]"
4. DATA DROP: "[Angka spesifik] orang [konteks]. Lu termasuk?"
5. QUOTE: "[Nama] bilang: '[insight]'. Dan ini bener banget."

**JANGAN pakai:** QUESTION pattern "Lu tau gak?"

### Slides 2-5 — BODY
**HARD LIMITS: EXACTLY 3 sentences per slide, <40 words total per slide.**
- 1 insight baru per slide, no filler, no repeat poin sebelumnya.
- Paraphrase quotes dari artikel, jangan copy-paste kalimat asli.
- Attribution: sebut sumber berita minimal 1x di salah satu slide (buat credibility).
- Kalau lebih dari 3 kalimat, POTONG. Kalau lebih dari 40 words, SIMPLIFY.

Escalation arc (urutan proven, jangan rearrange):
- Slide 2 = Context: apa yang terjadi, situasi realita
- Slide 3 = Escalation: fakta yang bikin "oh ternyata..." / twist yang bikin kaget
- Slide 4 = Impact: kenapa ini penting buat lu — dampak konkret, angka, consequences
- Slide 5 = So what: satu tips atau big lesson yang mengubah cara pikir

### Slide 6 — CTA
**HARD LIMITS: MAX 2 sentences, <30 words total. One short question.**
- WAJIB bikin orang comment. Pakai salah satu formula:
  1. PROVOCATIVE: "Menurut lu, [provokasi]?"
  2. PERSONAL: "Lu sendiri [action]?"
  3. DEBATE: "Setuju gak kalo [pendapat]?"
- WAJIB taruh URL sumber di baris terakhir.
- JANGAN "link di bio".
- JANGAN lebih dari 2 kalimat. Potong yang gak essential.

### Cliffhanger
Di akhir Slide 1-5, WAJIB akhirin dengan kalimat gantung pendek yang bikin penasaran: "Tapi ngerinya...", "Ini triknya...", "Tapi tunggu...". Jangan pakai simbol dekoratif.

### Caption Rules
2-3 lines max. Baris pertama = THE shocking number/fact. Baris kedua = consequence.
Zero emoji. Zero hashtags.

### Analogi Lokal
Pakai analogi keresahan lokal sehari-hari di Slide 2-5 SAJA (budak korporat, dompet tipis, THR, ojol, warteg, angkringan). JANGAN di Slide 1 — hook harus langsung ke fakta.

## 10. GROUNDING RULES (ALL SLIDES)
SEMUA fakta HARUS dari artikel. Never invent.

1. NO INVENTED REASONING. Jangan klaim "[orang/perusahaan] sengaja [X]" kecuali artikel jelas bilang begitu.
2. NO EXAGGERATED PARAPHRASING. "Called for changes" ≠ "demanded". "Suggested" ≠ "insisted". Preserve exact strength dari bahasa asli.
3. NO SPECULATIVE CONSEQUENCES. Jangan tulis "ini berarti X akan terjadi" kecuali artikel jelas bilang.
4. QUOTES harus word-for-word dari artikel. Kalau paraphrase, pakai indirect speech dan stay close ke aslinya.
5. NO PARTIAL LISTS. Kalau list sesuatu, include SEMUA yang disebut artikel. Jangan cherry-pick.
6. Rumor/unconfirmed: bilang eksplisit ("menurut laporan" / "belum dikonfirmasi"). Jangan present speculation sebagai fakta.
7. TEST EACH SLIDE: "Bisa gw tunjuk kalimat spesifik di artikel yang mendukung ini?" Kalau gak, hapus.
8. NO INVENTED ANGKA. Jangan tulis "setara Rp500 juta" kecuali artikel jelas bilang.
9. NO INVENTED INVOLVEMENT. Jangan tambahin orang/tokoh yang gak disebut artikel.
10. PRESERVE HEDGING. "Kemungkinan besar" ≠ "pasti". "Dilaporkan" ≠ "udah terjadi". Jangan upgrade uncertainty jadi certainty.
11. NO FAKE PERSONAL STORIES. Kalau artikel tentang pengalaman ORANG LAIN (CEO, pendiri, tokoh), JANGAN rewrite jadi "ibu gw", "temen gw", "kantor gw". Pakai nama orangnya atau sebut "seseorang yang [deskripsi]". Personal voice = reaksi lu terhadap fakta, BUKAN fabricate pengalaman pribadi lu sendiri.

## 9. LOCAL CONTENT — WAJIB
Audience lu orang Indonesia. Konten harus RELATE sama mereka.

- Buku → prioritas: Filosofi Teras, Tere Liye, Eka Kurniawan, Andrea Hirata, Dee Lestari. Boleh buku asing TAPI harus yang orang Indo kenal (Atomic Habits, Rich Dad Poor Dad). JANGAN buku obscure (Your Pocket Therapist, Let Them Theory) kecuali artikel emang nyebut.
- Startup → Gojek, Tokopedia, Traveloka, Shopee, Grab. Bukan startup asing.
- Influencer → Deddy Corbuzier, Raditya Dika, Jerome Polin, Arief Muhammad.
- 2/3 rekomendasi HARUS yang orang Indo tau. Maks 1 asing.

## 10. TERJEMAH NATURAL
Kalau artikel Inggris, JANGAN translate literal. Tulis ULANG dari nol dengan bahasa Indonesia gaul.
- "playbook" → "cara lama, strategi"
- "recovery time" → "waktu pulih"
- "level up" → "naik level, upgrade"
- "toxic productivity" → acceptable (sudah umum)
Pembaca harus GAK SADAR ini dari artikel Inggris.

## 11. CONTENT RULES
- JANGAN generate konten promosi produk. Kalau artikel tentang product launch/review → REJECT.
- WAJIB ada TIPS/ACTIONABLE ADVICE. Bukan cuma cerita/informasi.
- PERSONAL VOICE (HONEST): Tulis pakai POV orang pertama (gw/lu). Boleh: opini, reaction, observation. JANGAN fabricate stories. JANGAN: "temen gw", "bapak gw" kecuali beneran ada.

## 12. BANNED PATTERNS
JANGAN pakai: "Did you know?" / "Let's dive in!" / "Here's the secret" / "In today's world..." / "Teknologi semakin canggih" / "Bayangin lu lagi ngantri Starbucks..." / "Di era digital saat ini" / "This is a game-changer" / "Fans are furious" / "Let that sink in" / "Say what you want, but..." / "you've been warned" / "Tahukah kamu?" / "Yuk simak!" / "Ini dia rahasianya" / AIDA/PAS formulas / Motivational closing lines / "link di bio"

## 13. WORKED EXAMPLE

Input: "Waspada Penipuan Pre-Order GTA VI, Hacker Incar Rekening hingga Kripto. Kaspersky menemukan situs web palsu yang meniru PlayStation Store. Korban diminta data pribadi hingga nomor identitas wajib pajak. Modus lain: beta version = malware, token kripto GTA VI palsu."

Output:
{
  "slide_1": "GTA VI BELUM RILIS, tapi rekening lu udah bisa KOSONG.\\n\\nKaspersky: penipu manfaatin hype pre-order buat jebak gamer.",
  "slide_2": "Mereka bikin situs palsu mirip PlayStation Store.\\n\\nKorban diminta data pribadi sampe nomor pajak.",
  "slide_3": "Modus lain: beta version GTA VI = MALWARE.\\n\\nKripto token palsu juga beredar buat nyuri wallet lu.",
  "slide_4": "Ini dampaknya: lu bukan cuma kehilangan duit, tapi data pribadi lu juga bocor.\\n\\nPenipu paham psikologi — rasa takut kehabisan bikin lu klik tanpa mikir.",
  "slide_5": "Beli HANYA di platform resmi: PS Store, Steam, Xbox.\\n\\nJangan klik link dari DM atau komentar YouTube yang gak jelas.",
  "slide_6": "Lu pernah hampir kena tipu beli game?\\n\\nCerita di comment, biar yang lain belajar dari pengalaman lu.",
  "caption": "GTA VI belum rilis, tapi rekening lu udah bisa kosong.\\nKaspersky: penipu manfaatin hype pre-order buat jebak gamer."
}
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

def _call_narrative(title: str, body: str, source: str = "") -> Optional[str]:
    """Call Mistral with narrative prompt."""
    try:
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
            json={"model": "mistral-large-latest",
                  "messages": [{"role": "system", "content": NARRATIVE_PROMPT},
                               {"role": "user", "content": _build_user_msg(title, body, source)}],
                  "temperature": 0.3, "max_tokens": 2000},
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
    
    return slides


# ─── A/B Testing: Generate 3 hook variants, pick best ─────────────────

_HOOK_PATTERNS = [
    "REALIZATION: \"Gw baru nyadar... [fakta mengejutkan]\"",
    "OPINION: \"Jujur, gw [emotion] soal [topik]. [Fakta]\"",
    "QUESTION: \"Lu tau gak... [fakta provokatif]?\"",
    "QUOTE: \"[Nama] bilang: '[insight]'. Dan ini bener banget.\"",
    "CONTRAST: \"[Ekspektasi]... Tapi kenyataannya? [Realita]\"",
    "DATA DROP: \"[Angka spesifik] orang [konteks]. Lu termasuk?\"",
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
    
    # Pick best variant
    best_score, best_slides = max(variants, key=lambda x: x[0])
    print(f"  [A/B] Winner: hook_score={best_score} from {len(variants)} variants")
    return best_slides


def evaluate_slides(slides: dict, title: str, body: str, score: int = 0) -> dict:
    """Independent LLM review of generated slides.
    
    Returns:
        {"status": "APPROVE"|"REVISE"|"REJECT", "reason": str, "revised_slides": dict|None}
    
    Uses mistral-small-latest (cheap, different model than generator).
    """
    # Always run evaluator — no skip threshold
    # (Old: skip ≥100, but grounding issues found even at high scores)

    slide_text = "\n".join([f"Slide {i}: {slides.get(f'slide_{i}', '')}" for i in range(1, 7)])
    
    evaluator_prompt = f"""You are a skeptical content reviewer for Threads posts targeting Indonesian audience. Review the following 6-slide carousel.

ARTICLE TITLE: {title}

ARTICLE EXCERPT: {body[:2000]}

GENERATED SLIDES:
{slide_text}

YOUR TASK:
1. **GROUNDING CHECK** — Every claim, fact, statistic, recommendation in the slides MUST come from the article. If the article doesn't mention a specific book/product/person, the slides MUST NOT invent it. "BEST SELLER" without a number in the article = REJECT.
2. Check if the slides flow as a connected story (not 6 random facts)
3. Check if Slide 1 hook is attention-grabbing (<20 words preferred)
4. Check if Slide 6 has a clear CTA
5. Check for banned patterns (filler words, generic phrases, overly corporate language)
6. **LOCAL RELEVANCE** — For Indonesian audience: prefer local examples (Filosofi Teras, Tere Liye, Eka Kurniawan) over foreign books (Your Pocket Therapist, Let Them Theory). If recommending 3+ items, at least 2 MUST be locally known in Indonesia.
7. **RELATABILITY CHECK** — Flag concepts that don't resonate with Indonesian audience: "winter blues" (no winter in Indonesia), "Thanksgiving", "Super Bowl", "prom night", "401k", "credit score", "Ivy League". If the article uses a Western concept as the main angle, REJECT unless it can be reframed with a local equivalent.
8. **FAKE PERSONAL STORY CHECK** — If the article is about someone else's experience (a CEO, founder, named person), the slides MUST NOT rewrite it as the creator's own story ("ibu gue", "temen gue", "kantor gue"). That's fabrication. The creator can REACT to the story ("Gue kaget baca ini...") but cannot claim it happened to them.

APPROVAL CRITERIA:
- APPROVE: All facts grounded in article, story flows, hook strong, locally relevant
- REVISE: Minor issues (wording, flow, 1 grounding gap) but content is salvageable
- REJECT: Major hallucination, invented facts not in article, broken narrative, low local relevance (2/3+ recommendations are foreign/unknown)

AUTO-REJECT TRIGGERS:
- Inventing statistics ("BEST SELLER", "terlaris", "populer") without article data
- Recommending 2+ books/products not mentioned in article
- Generic advice with no source grounding
- Western concept as main angle with no local reframing (e.g., "winter blues" in Indonesia, "Super Bowl" references, "Thanksgiving" traditions)
- Fake personal story: rewriting someone else's experience from the article as "ibu gue", "temen gue", "kantor gue" when the article names a different person. Personal voice = your reaction, not fabricating your own experience.

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
