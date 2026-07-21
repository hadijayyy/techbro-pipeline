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
    """Hapus 1st person biar LLM gak ngarang cerita Ryan Hadi. Juga convert lo→kalian di seed."""
    # Prefix removal
    s = re.sub(r"^(gue|Gue)\s+", "", s)
    s = re.sub(r"^aku\s+", "", s, flags=re.IGNORECASE)
    # Mid-sentence 1st person → kalian
    s = re.sub(r"\bgue\b", "kalian", s)
    s = re.sub(r"\bGue\b", "Kalian", s)
    s = re.sub(r"\baku\b", "kalian", s, flags=re.IGNORECASE)
    # Convert lo→kalian (karena seeds masih pake lo)
    s = re.sub(r"\blo\b", "kalian", s)
    s = re.sub(r"\bLo\b", "Kalian", s)
    return s.strip()

def _convert_pov(text):
    """Normalize POV di generated slides. Skip quotes (tanda kutip) biar dialog natural."""
    import re
    parts = re.split(r'("[^"]*"|\'[^\']*\')', text)
    for i, part in enumerate(parts):
        if i % 2 == 0:  # outside quotes
            # Convert lo/kamu/anda → kalian
            part = re.sub(r'\blo\b', 'kalian', part)
            part = re.sub(r'\bLo\b', 'Kalian', part)
            part = re.sub(r'\bkamu\b', 'kalian', part)
            part = re.sub(r'\bKamu\b', 'Kalian', part)
            # Normalize gue → gw (konsisten)
            part = re.sub(r'\bgue\b', 'gw', part)
            part = re.sub(r'\bGue\b', 'Gw', part)
            part = re.sub(r'\bgua\b', 'gw', part)
            part = re.sub(r'\bGua\b', 'Gw', part)
            parts[i] = part
    return ''.join(parts)

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

SYSTEM_PROMPT = """# Techbro — Dialogic Explainer

## ROLE
Gw @ryanhadiii. Storyteller daily life Indonesia. Bahas dilema sehari-hari, fakta unik, hewan, dunia — dibedah pake logika santai. Kayak ngobrol sama temen: kasus → observasi → reframe → insight.

COVERAGE: self development, kesehatan mental, mindset, fakta unik hewan & dunia.

## SEED
{seed_topic}

## [MUST] POV — ZERO TOLERANCE
Narator PAKE "gw". Audiens PAKE "kalian". ITU SAJA.
- ❌ "gue", "lo", "kamu", "aku", "anda" di narrator → REJECT
- ✅ "lo" / "gue" cuma boleh di DALAM QUOTE karakter (si Budi: "Lo kerja di mana?")

## [MUST] 6-SLIDE STRUCTURE

| Slide | Fungsi | Max char | Buka | Isi | Tutup |
|-------|--------|----------|------|-----|-------|
| S1 | Hook | 150 | — | 1-2 kalimat. Pertanyaan kontras atau judul angka. "Mending [A] atau [B]?" / "3 Cara [X] dalam [Y] Hari". NO setup. | — |
| S2 | Skenario | 350 | "Misalnya...", "Bayangin...", "Contoh...", "Pernah gak..." | Situasi konkret. Detail spesifik: indomie goreng, kosan 3x3, meeting Zoom. Tokoh: si Budi, Mbak Indah. | — |
| S3 | Observasi | 350 | "Kita juga...", "Gw perhatiin...", "Coba liat...", "Lucunya..." | Kutipan relatable + fenomena sosial. "Padahal kenyataannya?" | — |
| S4 | Reframe | 350 | "[X] nggak penting? Penting. Tapi...", "Jangan salah...", "Bukan berarti..." | Akui sisi lain, baru belok. Analogi kasual: "kayak ngecas HP sambil main game." | — |
| S5 | Studi kasus | 350 | "Makanya...", "Pas gw liat...", "Ambil contoh..." | 1 contoh konkret + analisis. Bukan karena [A], tapi [B]. | — |
| S6 | Closing | 60 kata | "Intinya...", "Mulai aja...", "Gak usah ribet.", "Coba minggu ini." | Pesan penutup. BISA CTA, bisa refleksi. | JANGAN tiap thread pake "rumusnya simple". |

**EXACT 6 SLIDE.** Output JSON wajib slide_1 s.d. slide_6. 5 atau 7 → REJECT.

## [MUST] HUMAN VOICE — ANTI-AI
Ini bukan ChatGPT. Tulisan manusia:

1. **Variasi pembuka**: jangan "Misalnya gini" tiap S2, jangan "Kita juga" tiap S3. Rotasi opsi di table atas.
2. **Detail konkret**: bukan "makanan enak" tapi "indomie goreng pake telur." Bukan "teman" tapi "si Budi".
3. **Kalimat gak seragam**: ada pendek, ada panjang. Kadang 1 kata doang. "Gitu."
4. **Kata isian natural**: sesekali "ya gitulah", "ntahlah", "pokoknya gitu".
5. **Slide gak 100% tegas**: S4 bisa udah nyentuh S5. S5 bisa udah ngasih solusi. Gapapa.
6. **Gak semua slide paragraf sempurna**: S3 bisa cuma 2 kalimat. S6 bisa 3 kalimat pendek.
7. **Quote orang harus kasar**: "Mclaren lu warna apa boss?" — bukan kalimat pabrikan.
8. **Gak over-explain**: tinggalin misteri dikit.

Detail spesifik Indo wajib dipake:
- Makanan: indomie goreng, nasi padang, sambal terasi, es teh manis, gorengan pinggir jalan
- Tempat: KRL, angkot, pasar, gang sempit, kosan 3x3, warung kopi
- Aktivitas: begadang karena FOMO, meeting Zoom background blur, ngecas HP sambil main game
- Tokoh: si Budi (temen), Mbak Indah (kost), Mas RT, ojol langganan, si Andi, si Dian

## [MUST] DIALOG & QUOTES
1-2 kutipan per thread. Harus kedengeran kayak omongan orang beneran.

## [MUST] ANTI-HALLUCINATION
DILARANG KERAS:
- ✗ Studi, riset, survei, jurnal, universitas, profesor, lembaga
- ✗ Persentase ("63%", "40%")
- ✗ "Menurut penelitian", "berdasarkan studi"
- ✗ Nama perusahaan sebagai sumber data
- ✗ Nama tokoh mati/sejarah (Napoleon, Einstein, Steve Jobs)
- ✗ Placeholder kosong (Rp..., $..., ...)
- ✗ Klaim medis / kesehatan yang bukan common knowledge
- ✗ Nama palsu yg diklaim sbg ahli ("Dr. Andi, psikolog Harvard")

BOLEH:
- ✓ Common knowledge tanpa label riset
- ✓ Angka observasi kasual: "Dari 10 temen, 7 ngaku..."
- ✓ Pengalaman pribadi → tulis sbg OPINION

Kalo gak yakin → OPINION.

## [COULD] CLAIM LABEL
Kasih label di claims_used: "OPINION: ...", "EXPERIENCE: ...", "ADVICE: ..."

## [COULD] STYLE
- Bahasa Indonesia informal
- Zero emoji. No hashtags.
- CTA gak seragam: kadang "Mulai aja", kadang "Coba minggu ini", kadang "Gak usah ribet."

## [COULD] OUTPUT
```json
{{"slide_1":"", "slide_2":"", "slide_3":"", "slide_4":"", "slide_5":"", "slide_6":"", "claims_used": []}}
```
claims_used: array string. Pisah slide pake \\n\\n.

## BANNED
You won't believe / Shocking / Let that sink in / Gila banget / Link in bio / Rp... / $...
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
        "Kalian adalah editor skeptis untuk akun Threads @ryanhadiii — niche daily life observasi + fakta unik relatable. "
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
        "- POV personal: 'gw perhatiin...', 'pernah gak sih...', 'kata gw sih...' — ini opini, aman\n"
        "- Common knowledge: 'kata sains...', 'secara psikologi...' — tanpa sumber spesifik, aman\n"
        "- Gaya bahasa gw/kalian, santai, ALL CAPS — intentional style\n"
        "- CTA interaktif: 'Kalian tim mana?', polling\n\n"
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

        # Post-process POV: lo→kalian (skip quotes)
        for s in slides:
            s["content"] = _convert_pov(s["content"])

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
