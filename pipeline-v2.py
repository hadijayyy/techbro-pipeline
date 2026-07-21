#!/usr/bin/env python3
"""
RyanHadi Daily Life / Self-Dev Pipeline V5 - PRD Phase 1
4 self-dev pillars (overthinking, disiplin, confidence, career) + daily life observasi.
V1/V2 prompts: hook patterns, POV guide, CTA library, 6-slide PRD structure, claim labeling.
"""
import json, os, re, sys, time, random, logging, httpx
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
HOME = Path.home()
POSTED_FILE = BASE_DIR / "posted_topics_v2.json"
WIB = timezone(timedelta(hours=7))

log = logging.getLogger("dlv5")
log.setLevel(logging.INFO)
_h = logging.StreamHandler(sys.stderr)
_h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
log.addHandler(_h)

DRY_RUN = "--dry-run" in sys.argv
MAX_CHARS = 495
GRAPH = "https://graph.threads.net/v1.0"

# ── Env ──
ENV = {}
for env_path in [HOME / ".hermes" / ".env", BASE_DIR / ".env"]:
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                ENV[k.strip()] = v.strip().strip("\"'")
USER_ID = "27755289527427776"
LLM_BASE = "https://api.mistral.ai/v1"
LLM_KEY = ENV.get("MISTRAL_API_KEY", "")
THREADS_TOKEN = ENV.get("THREADS_ACCESS_TOKEN", "")

# ── ANTI-LINKEDIN: banned motivational words ──
ANTI_LINKEDIN = [
    "self improvement", "keharusan", "terbakar", "mindset pertumbuhan",
    "berinvestasi pada diri sendiri", "ubah hidupmu", "rahasia sukses",
    "langkah nyata", "mindset", "growth mindset", "berkembang",
    "versi terbaik", "berani keluar dari", "zona nyaman",
    "ubah pola pikir", "positif thinking", "affirmation",
    "self love", "healing journey", "inner child",
]

# ── Seed pool — 4 self-dev pillars + daily life ──
SEEDS = [
    # ── PILLAR 1: OVER-THINKING ──
    "Overthinking sebelum tidur: otak gue muter-muter masa lalu yang gabisa diubah",
    "Gue kapan lalu sadar: overthinking itu bukan solusi, cuma simulasi bencana",
    "Keputusan kecil gue pikirin berhari-hari, tapi keputusan besar malah impulsif",
    "Paradoks overthinking: makin dipikirin makin gak jelas solusinya",
    "Capek bikin pro-contra list, tapi ujung-ujungnya milih yang mana juga?",
    "Overthinking itu seperti kursi goyang — banyak gerak tapi gak kemana-mana",
    "Gue baru sadar: 90% hal yg gue khawatirin gak pernah terjadi",
    "Bedanya mikir serius sama overthinking: timing-nya",
    "Keputusan paling gue sesali itu yang gue overthink paling lama?",
    "Gue bukan gak bisa milih. Gue cuma takut salah milih.",
    # ── PILLAR 2: DISIPLIN REALISTIS ──
    "Konsisten bukan berarti setiap hari. Konsisten berarti gak berhenti.",
    "Gue dulu pikir disiplin = keras sama diri sendiri. Ternyata: pinter sama diri sendiri.",
    "Rahasia konsisten: target kecil banget sampe malu kalo gak dikerjain",
    "Gagal konsisten? Mungkin target lo bukan terlalu besar, tapi terlalu abstrak",
    "Kebiasaan baru: gue cuma janji 5 menit. Seringnya lanjut lebih lama.",
    "Motivasi itu overrated. Yang bikin beda: sistem yang ringan buat dijalanin.",
    "Gue stop nunggu 'feeling siap' — ternyata gak akan pernah datang.",
    "Masalahnya bukan males. Tapi hambatan pertama terlalu tinggi.",
    "3 hari berturut-turut udah kemenangan. Gak perlu 30 hari langsung.",
    "Ironis: target gue lebih sering tercapai pas gak ambisius.",
    # ── PILLAR 3: CONFIDENCE ──
    "Gue kira percaya diri itu suara lantang. Ternyata: berani mulai.",
    "Impostor syndrome: gue merasa belum layak padahal buktinya udah ada",
    "Percaya diri bukan berarti gak takut. Tapi takut tetap jalan.",
    "Orang paling PD seringkali cuma paling jago nutupin insecure-nya",
    "Gue stop nunggu yakin 100% buat ngomong. 70% udah cukup.",
    "Perbandingan sosial: yg lo liat itu hasil akhir, bukan perjuangan mereka",
    "Keberanian bukan gak ada takut. Tapi: gue takut, tapi gue lakuin.",
    "Hal yg gue sesali bukan yang gagal, tapi yang gak pernah dicoba",
    "Pas gue berhenti peduli apa kata orang, baru gue mulai maju",
    # kesehatan mental
    "Kenapa malam hari selalu bikin overthinking?",
    "POV: capek secara mental tapi gak keliatan secara fisik",
    "Mitasi soal healing: liburan bukan solusi semua masalah",
    "Burnout bukan berarti lo lemah, tapi...",
    "FOMO medsos bikin lo insecure tanpa sadar",
    "Challenge: sehari tanpa ngeliat Instagram",
    "Ironis: cari 'me time' malah bikin lo makin stres",
    "Gue baru sadar: makin dewasa makin sepi temen curhat",
    # fakta unik
    "Otak manusia lebih gampang inget hal negatif — ini mekanisme survival",
    "Kenapa manusia ngomong sendiri? Ternyata cara otak ngatur pikiran",
    "Kebiasaan kecil yg bikin otak lo lebih optimal",
    "Alasan kenapa lo susah bangun tidur — bukan karena males",
    "Paradoks pilihan: makin banyak pilihan makin susah milih",
    "Cara kerja memori: kenapa lagu lawas bisa bikin nostalgia",
    "Fakta soal tidur: yg bikin lo lemes pas bangun padahal udah 8 jam",
    "Kenapa manusia punya dejavu? Penjelasan ilmiahnya",
    "Fakta tentang senyum: ngefek ke otak lo tanpa lo sadari",
    "Alasan kenapa lo suka makanan pedas padahal sakit",
    "Kenapa makin dewasa waktu berasa makin cepet?",
    "Fakta: otak milih yg enak bukan yg bener — ini alasannya",
    # fakta hewan & dunia (relate ke indo)
    "Kenapa suara tokek bisa segede itu? Fakta soal reptil kecil bersuara raksasa",
    "Semut Rangrang: ternyata tentara paling brutal di kerajaan serangga",
    "Kenapa nyamuk lebih suka gigit orang tertentu? Ilmu di baliknya",
    "Cicak bisa nempel di dinding, kenapa manusia gabisa?",
    "Lumba-lumba tidur setengah otak — ternyata manusia juga mirip",
    "Kenapa burung merpati bisa pulang walau dilepas ribuan kilometer?",
    "Kenapa ayam bisa jalan-jalan padahal kepalanya udah putus?",
    "Fakta soal laron: kenapa dia bunuh diri ke lampu?",
    "Gajah Sumatra bisa deteksi gempa sebelum manusia — ini sebabnya",
    "Kenapa hewan peliharaan lo bisa tau mood lo duluan?",
    "Capung: predator paling mematikan di dunia (lebih dari singa)",
    "Kenapa kunang-kunang udah mulai langka di kota lo?",
    "Ternyata rayap bukan musuh — dia arsitek terbaik dunia serangga",
    "Kenapa kucing takut air tapi anjing enggak?",
    "Fakta soal harimau Sumatra: dia bisa niruin suara mangsanya",
    # ATM: life tips / numbered list (self-improvement arc)
    "KALAU USIAMU 25-30++ DAN LAGI BERUSAHA MEMPERBAIKI HIDUP — mulai dari sini",
    "5 kebiasaan kecil yg efeknya gede banget buat hidup lo",
    "Hal yg gak diajarin pas sekolah tapi penting banget pas dewasa",
    "Dari 10 hal ini, mana yang paling sering lo tunda?",
    "6 hal yg pengangguran produktif lakuin setiap hari",
    "Skill dasar yg wajib lo kuasai sebelum umur 30",
    "Investasi paling murah yang returnnya gede: diri sendiri",
    "Cara bedain mana yg produktif dan mana yang sibuk doang",
    "5 tanda lo sebenarnya gak maju-maju — padahal ngerasa sibuk",
    "Hal yang harus lo stop lakuin kalo mau hidup lo naik level",
    "Prioritas hidup di umur 25 vs 30 — bedanya jauh",
    "Ghost phase: kenapa lo perlu ngilang dulu biar naik level",
    "Dari healing ke growing: mindset shift yg lo butuhin",
]

def _pick_seed(data):
    """Pick a random seed, preferring less-used ones."""
    topics = data.get("topics", [])
    used_topics = [t.get("title", "") for t in topics[-100:]]
    # Try to pick unused seed first
    unused = [s for s in SEEDS if s not in used_topics]
    if unused:
        return random.choice(unused)
    return random.choice(SEEDS)

def _clean_seed(s):
    """Hapus 1st person biar LLM gak ngarang cerita Ryan Hadi."""
    # Prefix removal
    s = re.sub(r"^(gue|Gue)\s+", "", s)
    s = re.sub(r"^aku\s+", "", s, flags=re.IGNORECASE)
    # Mid-sentence 1st person → lo
    s = re.sub(r"\bgue\b", "lo", s)
    s = re.sub(r"\bGue\b", "Lo", s)
    s = re.sub(r"\baku\b", "lo", s, flags=re.IGNORECASE)
    return s.strip()

# ── Engagement tracking ──

def load_data():
    try:
        return json.loads(POSTED_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"topics": [], "_bucket_counts": {}, "_hook_type_counts": {}, "_formula_counts": {}}

def save_data(data):
    POSTED_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def pull_engagement():
    """Fetch metrics for posts without views data. Returns number of updated posts."""
    if not THREADS_TOKEN or DRY_RUN:
        return 0
    data = load_data()
    topics = data.get("topics", [])
    updated = 0
    cutoff = datetime.now(WIB) - timedelta(hours=2)  # only posts >2h old

    for post in topics:
        if "views" in post:
            continue  # already have metrics
        try:
            posted = datetime.fromisoformat(post.get("posted_at", ""))
        except (ValueError, TypeError):
            continue
        if posted > cutoff:
            continue  # too fresh, might not have data yet

        post_id = post.get("post_id", "")
        if not post_id:
            continue

        try:
            r = httpx.get(f"{GRAPH}/{post_id}/insights", params={
                "metric": "views,likes,replies,shares",
                "access_token": THREADS_TOKEN,
            }, timeout=15)
            if r.status_code != 200:
                continue
            metrics = {}
            for item in r.json().get("data", []):
                name = item.get("name")
                vals = item.get("values", [])
                if vals:
                    metrics[name] = vals[0].get("value", 0)
            if metrics:
                post["views"] = metrics.get("views", 0)
                post["likes"] = metrics.get("likes", 0)
                post["replies"] = metrics.get("replies", 0)
                post["shares"] = metrics.get("shares", 0)
                updated += 1
                log.info(f"  Metrics: {post.get('title','')[:40]} → {metrics.get('views',0)} views")
        except Exception as e:
            log.warning(f"  Metrics failed for {post_id[:12]}: {e}")
        time.sleep(0.3)  # rate limit safety

    if updated:
        save_data(data)
        log.info(f"Updated {updated} posts with metrics")
    return updated


# ── System prompt ──

SYSTEM_PROMPT = """# Techbro — Dialogic Explainer Style

## [MUST] ROLE
Lo @ryanhadiii — storyteller yg ngebahas dilema sehari-hari pake sudut pandang logis.
Kayak "Mana yg lebih [A] atau [B]?", terus dibedah step-by-step.
Gaya: dialogis, kayak ngobrol sama temen. "Misalnya gini...", "Terus...", "Makanya..."

COVERAGE: self development, kesehatan mental, mindset, fakta unik hewan & dunia.

## [MUST] SEED TOPIC
{seed_topic}

## [MUST] POV
DILARANG KERAS pake "lo" di narrator! Aturan:
- **Narator: "gw"** — wajar. "Menurut gw...", "Gw liat...", identitas penulis.
- **Audiens: "kalian"** — efek ke banyak orang. "Kalian pernah...", "Coba kalian...".
- **Dialog internal pembaca**: pake "kalian" atau pake "lo" di dalam *quote* orang lain. Contoh: "Eh, lo nonton Drakor itu belom?"
- **Quote orang lain**: boleh pake "lo" karena itu karakter ngomong. Contoh: 'si Budi bilang, "Lo kerja di mana?"'
- PENTING: narrator jangan pake "lo". Hanya orang lain (dalam quote) yg boleh.

## [MUST] 6-SLIDE STRUCTURE — RHETORICAL ARC
WAJIB 6 slide. Tiap slide punya fungsi retoris spesifik:

| Slide | Fungsi | Max chars | WAJIB jawab |
|-------|--------|-----------|-------------|
| S1 | Question hook | 150 | **1 kalimat doang.** TAPI KADANG BOLEH 2 kalimat pendek (kalo lebih natural). Pertanyaan kontras ATAU judul angka spesifik. Contoh: "3 Cara Turun 10 Kilo dalam 30 Hari" atau "Mana yang Lebih Cuan, Ngejer Duit atau Skill?". JANGAN pake setup. |
| S2 | Open scenario | 350 | Mulai dengan "Misalnya gini:" ATAU variasi lain: "Contoh paling gampang...", "Pernah gak sih...", "Bayangin...". Gambarin situasi konkret. JANGAN setiap thread pake "Misalnya gini" terus. VARY! |
| S3 | Social observation | 350 | Observasi sosial + kutipan relatable. "Kita juga...[quote]..." ATAU variasi: "Coba liat sekitar lo...", "Gue perhatiin...", "Fenomena menarik...". Jangan tiap thread pake "Kita juga" mulu. |
| S4 | Objection + reframe | 350 | "Terus [X] nggak penting? Penting. Tapi [reframe]." ATAU variasi: "Bukan berarti [X] gak berguna. Cuma...", "Jangan salah. [X] penting. Tapi...". Akui sisi lain, baru belok. |
| S5 | Case study + analysis | 350 | "Makanya [real example, boleh pake nama/karakter]." ATAU variasi: "Pas gw liat temen gw...", "Coba deh liat...", "Ambil contoh...". Bukan karena [A]. Tapi karena [B]. |
| S6 | Framework + CTA | 60 kata | OPSIONAL rumus arrow chain. Bisa juga: pesan penutup langsung, ajakan reflektif. "Mulai [action] aja dulu. Gak paham 100% juga gapapa." GAK WAJIB pake "rumusnya simple". VARY closing style. |

**KRITIS: HARUS EXACT 6 SLIDE.** Output json wajib punya 6 key: slide_1 s.d. slide_6. GAK BOLEH 5 atau 7. Kalo evaluator detect jumlah slide bukan 6 → REJECT.

## [MUST] ANTI-AI WRITING — PENTING!
Biar gak kedengeran kayak ChatGPT:
- **Variasi transisi**: jangan tiap thread pake kata pembuka yg sama. Campur: kadang "Misalnya gini", kadang "Contoh", kadang "Bayangin".
- **Gak selalu rapi**: kadang kalimat bisa rough, gak harus sempurna grammarnya.
- **Detail spesifik**: daripada "makanan enak", bilang "indomie goreng + telur + kecap". Daripada "teman", bilang "si Budi".
- **Hindari pola A->B->C**: gak semua thread perlu rumus arrow chain. Kadang tutup aja langsung.
- **Quote harus kasar**: kutipan orang harus terdengar kayak obrolan beneran, bukan kalimat pabrikan.
- **Spasi & nafas**: ada kalimat panjang, ada pendek. Gak seragam.
- **Jangan over-explain**: kadang tinggalin misteri dikit, gak perlu semuanya dibedah.

Contoh transisi alternatif (GUNAIN BERGANTIAN, jangan pakai yg sama terus):
- S2: "Misalnya gini:" / "Contoh paling gampang:" / "Pernah gak sih:" / "Bayangin lo lagi..."
- S3: "Kita juga..." / "Coba liat sekitar..." / "Gue perhatiin..." / "Lucunya..."
- S4: "Terus [X] nggak penting?" / "Bukan berarti [X] gak guna." / "Jangan salah."
- S5: "Makanya..." / "Pas gw liat..." / "Ambil contoh..." / "Coba liat [nama]..."
- S6: "Menurut gw..." / "Intinya..." / "Mulai aja dulu dari..." / "Paling gampang: mulai [action]" / "Gak usah ribet."

Contoh detail spesifik yang relate ke orang Indo:
- Makanan: indomie goreng, nasi padang, sambal terasi, es teh manis, gorengan pinggir jalan
- Lokasi: KRL, angkot, terminal, pasar tradisional, gang sempit, kosan 3x3
- Aktivitas: ngecas HP sambil main game, begadang karena FOMO, meeting Zoom pake background blur
- Tokoh: si Budi (temen kuliah), Mbak Indah (kost), Mas RT, ojol langganan

## [MUST] HUMAN-FLAW WRITING — ANTI-DETECTION
Biar makin susah kedetect AI:
- **Kadang slide gak 100% rapi**: S5 bisa blended sama S6, gak harus tegas batasnya.
- **Kalimat gak selesai**: sesekali kasih kalimat yg putus di tengah: "Padahal kenyataannya? Ya..." atau "Gitu aja ribet. Ngapain?"
- **Kata isian natural**: "ya gitulah", "ntahlah", "pokoknya gitu", "dah lah" — sesekali di S3/S6.
- **Typo ringan** (kalo cocok): contoh "meeting" tulis "meting", "banget" jadi "bngt" — cuma kalo natural, jangan dipaksain.
- **S1 bisa 2 kalimat pendek**: gak harus selalu 1 kalimat sempurna. Kadang: "Mending mana? Ngejar duit atau skill? Dua-duanya penting."
- **Overlap antar slide**: Kadang poin di S3 udah dikit nyentuh S4, atau S5 udah mulai ngasih solusi sebelum S6.
- **Gak semua slide perlu "perfect paragraph"**: boleh ada slide yg cuma 1-2 kalimat pendek.

## [MUST] S1 = HOOK ONLY
S1 isi 1 kalimat (atau 2 kalo lebih natural). Bisa pertanyaan kontras atau judul dengan angka spesifik.
**WAJIB** pake angka konkret kalo relevan — jumlah, durasi, bobot, dll.
Contoh:
- "Mana yang Lebih Cuan, Ngejer Duit atau Skill?"
- "3 Cara Turun 10 Kilo dalam 30 Hari"
- "5 Tanda Kalian Butuh Liburan, Bukan Healing"
1 kalimat doang. Gak ada setup, gak ada "Bayangin deh". Langsung selesai di S1. S2 bisa mulai dengan "Misalnya gini..." atau variasi lain (lihat ANTI-AI WRITING di atas).

## [MUST] DIALOG & QUOTES
- Kasih 1-2 kutipan relatable per thread: "McLaren lu warna apa boss?" atau "Profit trading gede langsung margin call"
- Kutipan harus terdengar realistis, kayak omongan orang beneran.

## [MUST] ANTI-HALLUCINATION — BACA BAIK-BAIK
DILARANG KERAS menyebut:
- ✗ Studi/riset/survei apa pun (pasti palsu)
- ✗ Nama jurnal, universitas, profesor, lembaga riset
- ✗ Angka persentase ("63%", "40%", "80% orang...")
- ✗ Data survei, "menurut penelitian", "berdasarkan studi"
- ✗ Nama perusahaan/kantor sebagai contoh riset
- ✗ **Rp... / $... / nominal...** (placeholder kosong)

YANG DIPERBOLEHKAN:
- ✓ Common knowledge tanpa label riset
- ✓ Angka observasi: "Dari 10 orang, biasanya 7 ngerasa..."
- ✓ Pengalaman: tulis sbg OPINION, bukan FACT
Kalo gak yakin faktanya → tulis sbg OPINION.

## [MUST] CLAIM TYPE
- FACT = bisa diuji (butuh sumber atau common knowledge)
- OPINION = pandangan creator (tanpa sumber, jelas sbg opini)
- EXPERIENCE = pengamatan pribadi (jangan digeneralisasi)
- ADVICE = saran praktis (hindari janji hasil)
Kalo FACT gak yakin → tulis sbg OPINION.

## [COULD] WRITING STYLE
- Bahasa Indonesia. Pake "kalian" + "gw". Zero emoji. No hashtags.
- Dialogis, kayak ngobrol. Bukan ceramah.
- Kalimat pendek, tajam. Tapi kadang boleh ada yg panjang dikit.
- Kutipan relatable: "quote" — ini yg bikin slide kalian nempel.
- S6: OPSIONAL arrow chain. Bisa juga pesan langsung, ajakan, atau refleksi. **Jangan tiap thread pake "rumusnya simple"**.
- CTA: VARY! Kadang "Mulai [action] aja dulu", kadang "Gak usah ribet", kadang "Coba [action] minggu ini".

## [COULD] OUTPUT FORMAT
```json
{{"slide_1":"", "slide_2":"", "slide_3":"", "slide_4":"", "slide_5":"", "slide_6":"", "claims_used": []}}
```
claims_used: array tipe claim. Kalimat dipisah \\n\\n.

## [COULD] BANNED PATTERNS
You won't believe / Shocking / Let that sink in / Gila banget / Link in bio
**Rp... / $... / angka...** (placeholder kosong)
"""
# ── LLM Generation ──

def generate_slides(seed_topic):
    if not LLM_KEY:
        log.error("No LLM_KEY")
        return None

    system = SYSTEM_PROMPT.format(seed_topic=seed_topic)
    system += (
        "\n## ANTI-LINKEDIN BANNED WORDS\n"
        + "\n".join(f"- '{w}'" for w in ANTI_LINKEDIN)
        + "\nJANGAN pake kata-kata di atas.\n"
    )

    for attempt in range(1, 4):
        log.info(f"  LLM attempt {attempt}/3")
        try:
            r = httpx.post(
                f"{LLM_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"},
                json={"model": "mistral-large-latest", "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Generate 6-slide thread for seed: {seed_topic}"}
                ], "max_tokens": 2000, "temperature": 0.7},
                timeout=60
            )
            if r.status_code == 429:
                time.sleep(min(2 ** attempt, 30))
                continue
            if r.status_code != 200:
                log.warning(f"  HTTP {r.status_code}")
                time.sleep(min(2 ** attempt, 10))
                continue

            content = r.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

            data = json.loads(content)
            slides = []
            for i in range(1, 7):
                key = f"slide_{i}"
                text = data.get(key, "").strip()
                if text and len(text) >= 10:
                    text = text.replace("\u2014", " - ").replace("\u2013", " - ")
                    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
                    # S1: max 300 char (min 3 kalimat)
                    if i == 1 and len(text) > 300:
                        text = text[:300].rsplit('.', 1)[0] + '.'
                    # S2-S5: max 350 char (min 3 kalimat)
                    if i in (2, 3, 4, 5) and len(text) > 350:
                        text = text[:350].rsplit('.', 1)[0] + '.'
                    # S6: max 350 char (60 kata ~ min 3 kalimat)
                    if i == 6 and len(text) > 350:
                        text = text[:350].rsplit('.', 1)[0] + '.'
                    if len(text) > MAX_CHARS:
                        text = text[:MAX_CHARS-3] + "..."
                    slides.append({"title": f"S{i}", "content": text})

            if len(slides) != 6:
                log.warning(f"  Wrong slide count: {len(slides)} (expected 6)")
                continue

            caption = data.get("caption", "").strip()
            if caption:
                slides[0]["caption"] = caption

            claims_used = data.get("claims_used", [])
            if not isinstance(claims_used, list):
                claims_used = []
            return slides, claims_used

        except json.JSONDecodeError as e:
            log.warning(f"  JSON parse failed: {e}")
            time.sleep(1)
            continue
        except Exception as e:
            log.error(f"  LLM error: {e}")
            time.sleep(1)
            continue

    log.error("Failed after 3 attempts")
    return None

# ── Evaluator (anti-halusinasi + anti-LinkedIn) ──

ANTI_LINKEDIN_EVAL = "\n".join(f"- '{w}'" for w in ANTI_LINKEDIN)

def evaluator_check(slides_text):
    if not LLM_KEY:
        return "APPROVE", ["no API key"]
    system = (
        "Kamu adalah editor skeptis untuk akun Threads @ryanhadiii — niche daily life observasi + fakta unik relatable. "
        "Bahasa: Indonesia informal. Tugas: cek slides untuk hallucinated content.\n\n"
        "HANYA TOLAK kalau ada:\n"
        "1. STATISTIK PALSU: '75% orang...', 'penelitian di [universitas gelap]...', 'menurut survei [sumber palsu]'\n"
        "2. KLAIM MEDIS BERBAHAYA: klaim kesehatan tanpa dasar common knowledge\n"
        "3. NAMA PALSU: tokoh fiktif, 'seorang psikolog di...' tanpa identitas jelas\n"
        "4. FAKTA SEJARAH/SAINS YG SALAH: klaim faktual bertentangan dgn pengetahuan umum\n"
        "5. BAHASA MOTIVATOR LINKEDIN: kalimat motivasi kosong, self-help jargon — HATI-HATI ini\n\n"
        "Kata-kata motivator LinkedIn yg WAJIB ditolak:\n"
        f"{ANTI_LINKEDIN_EVAL}\n\n"
        "JANGAN TOLAK kalau:\n"
        "- POV personal: 'gue perhatiin...', 'pernah gak sih...', 'kata gue sih...' — ini opini, aman\n"
        "- Common knowledge: 'kata sains...', 'secara psikologi...' — tanpa sumber spesifik, aman\n"
        "- Gaya bahasa gue/lo, santai, ALL CAPS — intentional style\n"
        "- CTA interaktif: 'Lo tim mana?', polling\n\n"
        'RESPON EXACTLY:\n'
        '{"decision": "APPROVE|REJECT", "reasons": ["alasan1", "alasan2"]}\n'
        'APPROVE = boleh post. REJECT = hallucinated/banned content.'
    )
    user = f"CEK SLIDES INI:\n\n{slides_text}"

    for attempt in range(1, 4):
        try:
            r = httpx.post(
                f"{LLM_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"},
                json={"model": "mistral-small-latest", "messages": [
                    {"role": "system", "content": system}, {"role": "user", "content": user}
                ], "max_tokens": 500, "temperature": 0.1},
                timeout=30
            )
            if r.status_code != 200:
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                return "REJECT", [f"HTTP {r.status_code}"]
            raw = r.text.strip()
            resp_data, _ = json.JSONDecoder(strict=False).raw_decode(raw)
            content = resp_data["choices"][0]["message"]["content"].strip()
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            content = re.sub(r"```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```", "", content)
            data, _ = json.JSONDecoder(strict=False).raw_decode(content)
            decision = data.get("decision", "APPROVE").upper()
            reasons = data.get("reasons", [])
            if decision not in ("APPROVE", "REJECT"):
                decision = "APPROVE"
            return decision, reasons
        except Exception as e:
            if attempt < 3:
                time.sleep(2 * attempt)
                continue
            return "REJECT", [f"error: {e}"]

# ── Threads Posting ──

def post_to_threads(slides):
    if not THREADS_TOKEN:
        log.error("No THREADS_ACCESS_TOKEN")
        return None, None

    def create_container(text, reply_to_id=None):
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', text)
        text = re.sub(r'(?<!Mr)(?<!Mrs)(?<!Ms)(?<!Dr)(?<!St)(?<!vs)(?<!Jr)(?<!Sr)(?<!Prof)([.?!])\s+(?=[A-Z])', r'\1\n\n', text)
        data = {"user_id": USER_ID, "text": text, "access_token": THREADS_TOKEN,
                "media_type": "TEXT"}
        if reply_to_id:
            data["reply_to_id"] = reply_to_id
        try:
            r = httpx.post(f"{GRAPH}/{USER_ID}/threads", data=data, timeout=30)
            if r.status_code == 200:
                return r.json().get("id")
            log.warning(f"Create failed: {r.status_code} {r.text[:200]}")
        except Exception as e:
            log.error(f"Create error: {e}")
        return None

    def publish_container(creation_id):
        try:
            r = httpx.post(f"{GRAPH}/{USER_ID}/threads_publish", data={
                "creation_id": creation_id, "access_token": THREADS_TOKEN
            }, timeout=30)
            if r.status_code == 200:
                return r.json().get("id")
            log.warning(f"Publish failed: {r.status_code} {r.text[:200]}")
        except Exception as e:
            log.error(f"Publish error: {e}")
        return None

    results = []
    reply_to = None
    for i, slide in enumerate(slides):
        text = slide["content"]
        log.info(f"  Slide {i+1}/{len(slides)}: {text[:60]}...")
        creation_id = create_container(text, reply_to_id=reply_to)
        if not creation_id:
            log.error(f"  Failed at slide {i+1}")
            break
        time.sleep(2)
        post_id = publish_container(creation_id)
        if not post_id:
            log.error(f"  Failed to publish slide {i+1}")
            break
        results.append({"text": text, "post_id": post_id})
        reply_to = post_id
        log.info(f"  Posted: {post_id}")
        if i < len(slides) - 1:
            time.sleep(3)
    return results

# ── Prime hour optimizer ──
PRIME_WINDOWS = [
    (7, 9),   # pagi sebelum kerja
    (12, 14), # jam istirahat
    (19, 21), # malam prime time
]

def _calc_delay():
    """Calculate delay in seconds until next prime hour window."""
    now = datetime.now(WIB)
    current_hour = now.hour
    current_min = now.minute

    for start, end in PRIME_WINDOWS:
        if start <= current_hour < end:
            return 0  # already in prime window
        if current_hour < start:
            # Wait until start of next window
            wait_min = (start - current_hour) * 60 - current_min
            return max(0, int(wait_min * 60) + random.randint(0, 600))  # 0-10min jitter
    # After all windows, wait until first window tomorrow
    wait_min = (7 + 24 - current_hour) * 60 - current_min
    return max(0, int(wait_min * 60) + random.randint(0, 600))

# ── Main ──

def main():
    START = time.time()
    log.info("=== RYANHADI DAILY LIFE V5 ===")

    # 1. Always pull engagement first (even outside prime hours)
    log.info("Pulling engagement metrics...")
    pulled = pull_engagement()
    log.info(f"  Updated {pulled} posts")

    # 2. Prime hour check — skip posting if outside prime hours and no --force
    if not DRY_RUN and "--force" not in sys.argv:
        delay = _calc_delay()
        if delay > 0:
            log.info(f"Outside prime hours. Next window in {delay//60}m. Use --force to skip.")
            # Still post if delay is short (<45min)
            if delay > 2700:
                print(f"Skipped: outside prime hours. Next in {delay//60}m")
                return
            log.info(f"Waiting {delay//60}m for prime window...")
            if delay <= 600:
                time.sleep(delay)
            else:
                print(f"Skipped: next prime in {delay//60}m, too long to wait")
                return

    # 2. Load data + pick seed
    data = load_data()
    seed_topic = _clean_seed(_pick_seed(data))
    log.info(f"Seed: {seed_topic[:80]}")

    # 3. Generate slides
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        result = generate_slides(seed_topic)
        if result is None:
            log.warning(f"Attempt {attempt}/{max_attempts}: generation failed, trying different seed...")
            seed_topic = _clean_seed(_pick_seed(data))
            continue
        slides, claims_used = result

        gen_time = time.time() - START
        log.info(f"Generated {len(slides)} slides in {gen_time:.1f}s")

        # 4. Evaluator
        slides_text = " ".join(s["content"] for s in slides)
        decision, reasons = evaluator_check(slides_text)
        log.info(f"Evaluator: {decision} — {'; '.join(reasons[:3])}")
        if decision == "REJECT":
            log.warning(f"Attempt {attempt}/{max_attempts}: rejected ({'; '.join(reasons[:3])}), trying different seed...")
            seed_topic = _clean_seed(_pick_seed(data))
            continue

        # Approved — proceed
        break
    else:
        log.error("All attempts failed — exhausted seeds")
        print("Gagal: semua seed habis/error", flush=True)
        sys.exit(1)

    # 6. Preview
    for i, s in enumerate(slides):
        log.info(f"  S{i+1}: {s['content'][:80]}...")

    # 7. Dry run or post
    if DRY_RUN:
        for i, s in enumerate(slides):
            print(f"\n--- Slide {i+1} ---\n{s['content']}")
        if slides[0].get("caption"):
            print(f"\n--- Caption ---\n{slides[0]['caption']}")
        print(f"\nSeed: {seed_topic}")
        if claims_used:
            print(f"Claims: {', '.join(claims_used)}")
        print(f"Done in {time.time()-START:.1f}s")
        return

    # 8. Post
    results = post_to_threads(slides)
    if not results:
        log.error("Post failed")
        print("Post failed", flush=True)
        sys.exit(1)

    root_id = results[0]["post_id"]
    # Fetch permalink
    try:
        pr = httpx.get(f"{GRAPH}/{root_id}", params={
            "fields": "permalink", "access_token": THREADS_TOKEN
        }, timeout=10)
        permalink = pr.json().get("permalink", "")
    except Exception:
        permalink = ""
    if not permalink:
        permalink = f"https://www.threads.net/@ryanhadiii/post/{root_id}"

    # Save to tracking
    data = load_data()
    if "topics" not in data:
        data["topics"] = []
    entry = {
        "title": seed_topic,
        "post_id": root_id,
        "permalink": permalink,
        "claims_used": claims_used,
        "posted_at": datetime.now(WIB).isoformat(),
    }
    data["topics"].append(entry)
    data["topics"] = data["topics"][-200:]
    save_data(data)

    total = time.time() - START
    log.info(f"Posted: {permalink}")
    log.info(f"Total: {total:.1f}s (gen: {gen_time:.1f}s)")
    print(f"Posted: {seed_topic[:60]}\n{permalink}", flush=True)

if __name__ == "__main__":
    if "--with-jitter" in sys.argv:
        time.sleep(random.randint(0, 30))
    main()
