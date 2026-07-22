#!/usr/bin/env python3
"""
RyanHadi Content Engine V6 — from prompt document spec.
4 pillars + daily life. OPINION mode default; FACT mode with source_packet.
Production-grade: truth policy, instruction priority, structured validation.
"""
import json, re, sys, time, random, logging, httpx
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
HOME = Path.home()
POSTED_FILE = BASE_DIR / "posted_topics_v2.json"
WIB = timezone(timedelta(hours=7))

log = logging.getLogger("ce6")
log.setLevel(logging.INFO)
_h = logging.StreamHandler(sys.stderr)
_h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
log.addHandler(_h)

DRY_RUN = "--dry-run" in sys.argv
MAX_CHARS = 495
GRAPH = "https://graph.threads.net/v1.0"

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

# ── Banned words ──
PROHIBITED = [
    "you won't believe", "shocking", "let that sink in", "gila banget", "link in bio",
    "self improvement", "keharusan", "terbakar", "mindset pertumbuhan",
    "berinvestasi pada diri sendiri", "ubah hidupmu", "rahasia sukses",
    "langkah nyata", "mindset", "growth mindset", "berkembang",
    "versi terbaik", "berani keluar dari", "zona nyaman",
    "ubah pola pikir", "positif thinking", "affirmation",
    "self love", "healing journey", "inner child",
]

# ── Seed pool — 3 categories: otak, hewan, tubuh manusia ──
SEEDS = [
    # Fakta unik (otak / tidur / memori)
    "Otak manusia lebih gampang inget hal negatif — ini mekanisme survival",
    "96% orang ngomong sendiri dalam 24 jam — tapi makin keras, makin tenang pikiran lo",
    "1 kebiasaan sehari: baca 10 halaman bisa naikin fokus 23%. Tapi kenapa susah konsisten?",
    "60% pekerja alami sleep inertia — 8 jam tidur masih lemes pas bangun",
    "30 pilihan di depan mata bikin otak freeze — bukan soal keputusan, ini overload kognitif",
    "Lagu lawas trigger otak 10x lebih kuat dari foto. Ini kenapa lo bisa nangis denger lagu SD",
    "70% orang bangun masih capek meski tidur 8 jam. Bukan kurang tidur — salah siklus REM",
    "7 dari 10 orang pernah ngalamin dejavu — bukan ingatan masa lalu, otak cuma error nulis timestamp",
    "Senyum palsu 30 detik doang lepasin serotonin. Otak lo gak bisa bedain mana yang genuine",
    "80% orang Indonesia doyan pedas. Padahal cabe trigger reseptor rasa sakit — bukan rasa",
    "Setelah 25 tahun, 1 tahun terasa cuma 6 bulan. Soal proporsi memori di otak lo",
    "Otak milih yg enak 3x lebih cepet daripada yg bener — dan keputusan itu terjadi sebelum lo sadar",
    "Setiap mata punya blind spot segede bola pingpong — tapi lo gak pernah sadar karena otak nge-fill otomatis",
    "Telinga denger semua suara — otak filter 90% noise. Lo selektif dengar nama sendiri dari keramaian dalam 0.5 detik",
    "80% lirik lagu SD masih diinget — tapi naruh kunci 5 menit lalu gak inget. Otak prioritasin emosi, bukan utilitas",
    "Otak replay pengalaman buruk 4x lebih sering pas REM — bukan nyiksa, ini latihan survival",
    "3 detik denger lagu udah cukup ubah mood — dan terjadi sebelum lo sadar lagi denger apa",
    "Merasa diamati pas sendirian? Otak lo nge-scan ancaman tanpa sadar",
    "Air dingin turunin detak jantung 15% dalam 10 detik. Cuci muka pas stres = reset instan",
    "Otak cuma 2% dari badan tapi makan 20% energi harian lo",
    "Lo gak bisa geli diri sendiri karena otak kecil udah prediksi jari nyentuh — error margin 0",
    "1 dari 3 orang alamin earworm tiap hari — makin lo usaha ngilangin, makin kuat nempel",
    "Bau hujan tembus ke memori 5x lebih dalam dari foto — jalurnya lewatin amigdala, pusat emosi",
    "70% orang alamin hypnic jerk — otak kaget detak jantung turun terlalu cepet, ngira lo sekarat",
    "Placebo effect: kenapa gula doang bisa ngurangin rasa sakit lo",
    # Fakta hewan & dunia (Indonesia)
    "Tokek sebesar jempol hasilin 100 desibel — setara motor. Rahasianya di rongga dada, bukan pita suara",
    "Semut Rangrang: ternyata tentara paling brutal di kerajaan serangga",
    "Cuma 1 dari 10 orang target utama nyamuk — golongan darah O punya 83% risiko lebih tinggi digigit",
    "Cicak nempel di dinding 100% tanpa lem — gaya Van der Waals di 1 juta rambut halus di telapak kakinya",
    "Lumba-lumba tidur setengah otak — ternyata manusia juga mirip",
    "Merpati pulang dari jarak 1.800 km tanpa GPS — magnet alami di paruh baca medan magnet bumi",
    "Ayam masih gerak 30 detik setelah dipenggal — brainstem masih hidup, ATP di otot belum habis",
    "Laron mati massal tiap malam hujan pertama — mereka kira bohlam itu rembulan, bukan bunuh diri",
    "Gajah Sumatra bisa deteksi gempa sebelum manusia — ini sebabnya",
    "Anjing bau perubahan hormon lo dalam 3 detik — jauh sebelum lo sadar lagi bad mood",
    "Capung: predator paling mematikan di dunia (lebih dari singa)",
    "Populasi kunang-kunang turun 70% di kota besar Indonesia — satu spesies hilang sebelum sempet diteliti",
    "Ternyata rayap bukan musuh — dia arsitek terbaik dunia serangga",
    "Kucing domestik gak pernah adaptasi air — cuma 1 dari 38 spesies kucing liar yang bisa berenang",
    "Fakta soal harimau Sumatra: dia bisa niruin suara mangsanya",
    "Bunglon berubah warna bukan buat kamuflase — ini alasan sebenarnya",
    "Kaki seribu punya 750 kaki — tapi tiap pasangan gerak bergelombang, bukan serempak. Mekanisme gerak paling efisien di serangga",
    "Bebek bisa jalan di air — rahasianya ada di struktur kaki",
    "Fakta soal kecoa: bisa hidup seminggu tanpa kepala. Mitos atau fakta?",
    "Ular bisa deteksi detak jantung mangsanya dari jarak 1 meter",
    "Lalat ngeliat 4x lebih lambat — tangan lo kayak gerakan lem buat mata majemuk mereka",
    "Kuda tidur berdiri 4 jam nonstop — stay apparatus di kaki otomatis ngunci sendi pas otot rileks",
    "Bintang laut: mulut di bagian bawah dan bisa regenerasi tubuh",
    "Burung hantu bisa muter kepala 270 derajat. Ini mekanisme di baliknya",
    "Siput lambat bukan kelemahan — itu strategi survival yang brilian",
    "Cumi-cumi bisa edit gen di tubuhnya sendiri — ini cara kerjanya",
    "Kucing ngasih lo tikus mati karena ngira lo kucing bodoh yang gak bisa berburu — ini pelatihan, bukan hadiah",
    "Hiu: bisa deteksi setetes darah dalam 100 liter air",
    "Burung tidur berdiri 8 jam tanpa jatuh — tendon di kaki otomatis ngunci pas otot rileks. Manusia gak punya fitur ini",
    "Paus — yang sering lo panggil 'ikan paus' ternyata mamalia dan dulunya jalan di darat",
    # Fakta tubuh manusia (di luar otak)
    "60% orang ikut menguap dalam 5 menit setelah liat orang lain — bukan soal oksigen, ini sinyal empati otomatis",
    "Jari keriput cuma butuh 3 menit di air — ini sinyal aktif dari sistem saraf, bukan reaksi pasif penyerapan air",
    "Cegukan tiap 3 detik selama 5 menit — diafragma kram karena saraf vagus irritation, lo gak bisa kontrol",
    "Sidik jari terbentuk di usia janin 10 minggu — bukan buat identitas, tapi buat nge-grip benda licin",
    "90% manusia dominan kanan. Tapi kenapa ada yang kidal? Jawabannya udah ditentukan sebelum lahir",
    "Bulu kuduk merinding: ternyata pesan dari otak purba",
    "Kuping tumbuh 0.22mm per tahun — setelah 60 tahun panjangnya bisa beda 2cm dari waktu bayi",
    "Demam 38°C bikin virus replikasi 2x lebih lambat — bukan kecelakaan, ini senjata tubuh yang terprogram",
    "Lapar 4 jam bikin lo 3x lebih impulsif — gula darah turun, prefrontal cortex mati duluan",
    "Gatal muncul di 3 titik spesifik pas lagi sendiri — area yang paling jarang disentuh, sensornya paling hipersensitif",
    "Alis bukan cuma ekspresi — 5.000 tahun evolusi bikin alis jadi pelindung mata dari keringat dan hujan",
    "Urat kelihatan biru padahal darah merah — ini penjelasan optiknya",
    "60% orang alamin kedutan pas tidur — otak kaget detak jantung turun drastis, ngira lo sekarat",
    "Bayi punya 300 tulang, dewasa cuma 206 — kemana sisanya?",
    "Manusia punya 2 ginjal padahal 1 berfungsi 100% — tapi 60% pasokan kena penyakit baru tau",
    "Lo rontok 50-100 helai per hari — normal, setiap folikel punya siklus 3 fase yang udah diprogram genetik",
    "Paru-paru kiri 10% lebih kecil dari kanan — jantung butuh ruang. Bukan desain error, ini efisiensi ruang",
    "Rambut kepala tumbuh 6 tahun nonstop — alis cuma 3 bulan. Beda genetik di fase anagen, bukan gizi atau perawatan",
    "Lidah — peta rasa tradisional yang diajarin di sekolah ternyata udah usang",
    "Blushing cuma butuh 2 detik — darah melonjak ke pipa akibat adrenalin. Lo malu, tapi tubuh lo siap fight",
    "Keringat gak bau — bakteri di kulit lo yang bikin bau",
    "Rata-rata tubuh produksi 200ml gas per hari — saat tidur sfingter rileks total, kontrol sadar off",
    "1 dari 12 pria buta warna — mayoritas bukan hitam-putih, cuma merah-hijau. Gen resesif di kromosom X",
    "Jantung denyut 100.000 kali per hari tanpa lo perintah, tanpa lo kontrol, tanpa lo ingat. Kenapa gak capek?",
    "15% manusia gak pernah digigit nyamuk — bukan soal darah manis/tawar, senyawa di kulit bikin mereka ogah",
]

def _pick_seed(data):
    """Pick seed with engagement weighting + cross-category balancing.
    
    Categories: otak=0, hewan=1, tubuh=2. Tracks last 3 categories to avoid
    consecutive repeats. Seeds with engagement data get +50% weight.
    Fallback: pure random if <5 engaged posts.
    """
    topics = data.get("topics", [])
    used_topics = [t.get("title", "") for t in topics[-100:]]
    unused = [s for s in SEEDS if s not in used_topics]
    if not unused:
        unused = list(SEEDS)

    # Build engagement weight map (likes + replies*2)
    eng_map = {}
    for t in topics:
        title = t.get("title", "")
        likes = t.get("likes", 0) or 0
        replies = t.get("replies", 0) or 0
        if title in SEEDS and likes + replies > 0:
            eng_map[title] = max(likes + replies * 2, 1)

    # Only activate if enough data
    MIN_DATA = 5
    eng_seeds = len(eng_map)
    use_weights = eng_seeds >= MIN_DATA

    # Retirement: exclude seeds used >=3x with below-median avg engagement
    RETIRE_THRESHOLD = 3
    if use_weights:
        # Count usage per seed
        usage = {}
        for t in topics:
            title = t.get("title", "")
            if title in SEEDS:
                usage.setdefault(title, 0)
                usage[title] += 1
        # Total engagement per seed (for multi-use aggregation)
        seed_eng = {}
        seed_count = {}
        for t in topics:
            title = t.get("title", "")
            likes = t.get("likes", 0) or 0
            replies = t.get("replies", 0) or 0
            if title in SEEDS and (likes + replies) > 0:
                score = likes + replies * 2
                seed_eng.setdefault(title, 0)
                seed_eng[title] += score
                seed_count.setdefault(title, 0)
                seed_count[title] += 1
        # Calc per-seed average & overall median
        avgs = []
        for title, total in seed_eng.items():
            cnt = seed_count.get(title, 1)
            avgs.append(total / cnt)
        median_eng = sorted(avgs)[len(avgs) // 2] if avgs else 0
        # Build retirement set
        retired = set()
        for title, avg in [(t, seed_eng[t] / seed_count[t]) for t in seed_eng if usage.get(t, 0) >= RETIRE_THRESHOLD]:
            if avg < median_eng:
                retired.add(title)
        if retired:
            log.info(f"Retired {len(retired)} underperforming seeds: {', '.join(list(retired)[:3])}...")
            unused = [s for s in unused if s not in retired]
            # If all seeds retired, fall back to full pool
            if not unused:
                unused = [s for s in SEEDS if s not in retired]
                if not unused:
                    unused = list(SEEDS)

    # Compute base weight per seed
    base_weight = 1.0
    weights = []
    for s in unused:
        w = base_weight
        if use_weights and s in eng_map:
            w *= 1.5  # +50% boost for engaged seeds
        weights.append(w)

    # Category balance: de-weight last 3 categories
    last_cats = [t.get("category", -1) for t in topics[-3:]]
    for i, s in enumerate(unused):
        cat = _categorize(s)
        if cat in last_cats:
            weights[i] *= 0.5  # -50% for recently used category

    choice = random.choices(unused, weights=weights, k=1)[0]
    return choice


# Seed→category mapping (indices: 0-24 otak, 25-54 hewan, 55-79 tubuh)
_SEED_CAT = {}
for _i, _s in enumerate(SEEDS):
    if _i < 25:
        _SEED_CAT[_s] = 0
    elif _i < 55:
        _SEED_CAT[_s] = 1
    else:
        _SEED_CAT[_s] = 2


def _categorize(seed):
    return _SEED_CAT.get(seed, -1)

def _clean_seed(s):
    """Hapus 1st person biar LLM gak ngarang cerita Ryan. Juga convert lo→kalian."""
    s = re.sub(r"^(gue|Gue)\s+", "", s)
    s = re.sub(r"^aku\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bgue\b", "kalian", s)
    s = re.sub(r"\bGue\b", "Kalian", s)
    s = re.sub(r"\baku\b", "kalian", s, flags=re.IGNORECASE)
    s = re.sub(r"\blo\b", "kalian", s)
    s = re.sub(r"\bLo\b", "Kalian", s)
    return s.strip()

def _convert_pov(text):
    """Normalize POV di generated text. Skip quotes biar dialog natural."""
    parts = re.split(r'("[^"]*"|\'[^\']*\')', text)
    for i, part in enumerate(parts):
        if i % 2 == 0:
            part = re.sub(r'\blo\b', 'kalian', part)
            part = re.sub(r'\bLo\b', 'Kalian', part)
            part = re.sub(r'\bkamu\b', 'kalian', part)
            part = re.sub(r'\bKamu\b', 'Kalian', part)
            part = re.sub(r'\bgue\b', 'gw', part)
            part = re.sub(r'\bGue\b', 'Gw', part)
            part = re.sub(r'\bgua\b', 'gw', part)
            part = re.sub(r'\bGua\b', 'Gw', part)
            parts[i] = part
    return ''.join(parts)



# ══════════════════════════════════════════════
#   GENERATOR — SYSTEM PROMPT (from document)
# ══════════════════════════════════════════════

SYSTEM_PROMPT = """# PERSONAL BRAND CONTENT ENGINE — @ryanhadiii

You are the content-writing engine for @ryanhadiii.
Turn the supplied input into one coherent Threads chain containing exactly six posts.

<instruction_priority>
1. Truth, source grounding, personal authenticity, and safety
2. Valid JSON and exact output structure
3. POV, prohibited language, and character limits
4. Narrative quality and relevance
5. Stylistic preferences
If two instructions conflict, follow the higher priority.
</instruction_priority>

<brand>
Account: @ryanhadiii
Positioning: membedah masalah sehari-hari yang sering dibikin rumit, lalu menyederhanakannya lewat cerita lokal, observasi, logika santai, dan langkah kecil yang realistis.
Core topics: kebiasaan & konsistensi, cara berpikir & pengambilan keputusan, dilema sehari-hari, kesehatan mental (edukasi umum), fakta unik hewan & dunia.
Default audience: Orang Indonesia usia produktif yang menyukai tulisan singkat, relatable, praktis, dan tidak menggurui.
Desired reader response: "Oh iya juga. Gw belum pernah ngeliatnya dari sisi itu."
</brand>

<truth_policy>
First-person:
- Never invent an experience, conversation, habit, or observation and present it as Ryan's.
- Use first-person only when supplied in `experience_packet`. Otherwise use hypothetical framing: "misalnya", "bayangin", "anggap aja".
- Fictional names are hypothetical characters, never real acquaintances.

OPINION mode (default):
- No external factual claims that require verification. No invented statistics, surveys, or quotes.
- Advice = suggestion, not guaranteed outcome. Permitted: OPINION, ADVICE, ILLUSTRATION, EXPERIENCE (only when supported).

FACT mode:
- Use only facts from `source_packet`. Every FACT claim must cite at least one source_id.
- If sources don't support the seed, return error JSON. Do not distort certainty, scope, dates, or populations.

Mental-health safety:
- Do not diagnose, prescribe, promise recovery, or discourage professional help.
- If topic involves crisis/self-harm/suicide/abuse, return error code `UNSAFE_TOPIC_REQUIRES_SPECIALIST_FLOW`.
</truth_policy>

<voice>
POV: Narrator = "gw". Audience = "kalian". Never "lo"/"kamu"/"anda"/"gue".
Tone: Informal Indonesian, conversational, observant, calm. Thoughtful friend, not lecturer/motivator. Mix short+medium sentences. Zero emoji, zero hashtags. No profanity or stereotypes. Don't over-explain.
Local detail: Zero or one Indonesian detail per thread (KRL, warteg, nasi Padang, kosan, ojol, Zoom). Optional — not a checklist. Avoid anything present in `recent_content`.
Dialogue: Optional, spontaneous, never fabricate a real person's quote.
</voice>

<anti_ai_writing>
SENTENCE VARIETY: Mix short (3-8 words), medium (10-15), occasional long (15-20). 1-2 fragments when natural. Uniform sentence length = AI tell.

TRANSITION + NO SLIDE-LABELING:
- Do NOT open 2+ slides with same word or formulaic label. Vary: S2="Misalnya"/"Contoh"/"Bayangin", S3="Gue perhatiin"/"Coba liat"/"Lucunya", S4="Terus X nggak penting?"/"Jangan salah", S5="Makanya..."/"Ambil contoh", S6="Intinya"/"Mulai aja".
- NEVER: rhetorical question transitions ("Hasil akhir?", "Dampaknya?", "Kedoknya?") or meta labels ("Ironisnya...", "Realitanya...", "Yang bikin [adj]"). State directly.

BREAK SYMMETRY: List 2 or 4+ items, never 3. Avoid "Ini bukan X — ini Y" — state Y directly.

PUNCTUATION & STEERING: Max 1 em dash per post. Don't tell readers how to feel ("Bikin geleng", "Angka spesifik yang bikin [adj]"). No "Padahal" as S1 second sentence.

DETAILS: Concrete > abstract: "indomie goreng + telur" not "makanan enak". 1-2 specifics per thread.
</anti_ai_writing>

<prohibited_output>
Banned (case-insensitive): you won't believe, shocking, let that sink in, gila banget, link in bio, self improvement, keharusan, terbakar, mindset pertumbuhan, berinvestasi pada diri sendiri, ubah hidupmu, rahasia sukses, langkah nyata, mindset, growth mindset, berkembang, versi terbaik, berani keluar dari, zona nyaman, ubah pola pikir, positif thinking, affirmation, self love, healing journey, inner child. No empty placeholders ("...", "Rp...", "$...").
</prohibited_output>

<thread_structure>
Six posts, one narrative arc.

post_1 — Hook: Max 150 chars, 1-2 sentences. REQUIRED: counter-intuitive claim + specific number. Hook tanpa angka = FAILED.
Examples: "kucing takut air" → "Kucing domestik: 95% takut air. Nenek moyang berasal dari gurun." "Singa gagal 7 dari 10 buruan. Capung? 95% sukses."

post_2 — Scenario: Max 350 chars. One concrete, picturable situation. Mark hypotheticals clearly.

post_3 — Observation: Max 350 chars. Reveal the behavior/assumption/tension. Don't generalize personal opinion as fact.

post_4 — Reframe: Max 350 chars. Acknowledge opposing view, then clearer frame. Optional relevant analogy.

post_5 — Application: Max 350 chars. One concrete application/example/small action. Explain without promising results.

post_6 — Closing: Max 300 chars. REQUIRED: genuine question inviting personal reply — not rhetorical.
Bad: "Pernah ngerasain hal yang sama?" — too generic.
Good: "Kucing lo gitu juga? Atau malah kebalikannya?" — specific, low-effort, comparison-driven.
Good: "Kapan terakhir kali kalian ngerasa dejavu, dan lagi ngapain waktu itu?" — personal experience, natural.
Rule: one question, references thread detail, feels like DM-ing a friend. No new argument.
</thread_structure>

<output_contract>
Return valid JSON only, no markdown fences, no commentary. Use exactly these keys:

Success:
{"status":"success","mode":"OPINION","seed":"...","angle":"...",
 "post_1":"...","post_2":"...","post_3":"...","post_4":"...","post_5":"...","post_6":"...",
 "claims_used":[{"post":"post_N","type":"OPINION|ADVICE|ILLUSTRATION|EXPERIENCE|FACT","claim":"...","source_ids":[]}],
 "source_ids_used":[]}

Error: {"status":"error","error_code":"INSUFFICIENT_SOURCE_PACKET|UNSAFE_TOPIC_REQUIRES_SPECIALIST_FLOW|INVALID_INPUT","message":"..."}

Rules:
- angle: one concise sentence describing chosen perspective.
- claims_used: substantive claims only, not every stylistic sentence.
- FACT claims must have source_ids populated; OPINION/ADVICE/ILLUSTRATION/EXPERIENCE must have [].
- source_ids_used lists each cited source once. Empty in OPINION mode.
</output_contract>

Silent pre-flight check before returning:
1. Mode follows truth policy. 2. Exactly six posts. 3. All within char limits.
4. POV/pronouns correct. 5. No prohibited expressions. 6. No invented experience/statistic.
7. Nothing from recent_content repeated. 8. Parseable JSON.

# REFERENCE EXAMPLE 1 — OTAK (dejavu)
{"posts": [{"title": "POST_1", "content": "7 dari 10 orang pernah ngalamin dejavu. Tapi bukan itu ramalan masa depan — otak kalian cuma lagi error nulis timestamp memori."}, {"title": "POST_2", "content": "Prosesnya gini: hippocampus nyimpen pengalaman baru, terus dikirim ke korteks. Kadang sinyalnya nyasar — memori barunya dikasih label udah pernah, padahal baru pertama kali."}, {"title": "POST_3", "content": "Peneliti Colorado State University bilang dejavu makin sering pas otak capek atau stress. Makin tinggi beban kognitif, makin gampang sistem memorinya korslet."}, {"title": "POST_4", "content": "Fakta tambahan: usia 15-25 adalah golden age dejavu. Setelah 40 intensitas turun drastis — hippocampus mulai lambat, jadi lebih jarang error. Bukan otak makin bagus, cuma makin pelan."}, {"title": "POST_5", "content": "Ada hipotesis lain: dejavu bisa jadi tanda otak lagi ngecek konsistensi memori. Kayak sistem file error-checking. Setiap kali ngerasa udah pernah, otak lagi maintenance."}, {"title": "POST_6", "content": "Intinya dejavu wajar, bukan mistis, bukan tanda kalian spesial. Cuma glitch sistem. Ngomong-ngomong, kapan terakhir kali kalian ngerasa dejavu, dan lagi ngapain waktu itu?"}], "claims_used": [{"claim": "7 dari 10 orang pernah alami dejavu", "type": "OPINION"}, {"claim": "Dejavu karena kesalahan pelabelan memori di hippocampus", "type": "OPINION"}, {"claim": "Usia 15-25 golden age dejavu, menurun setelah 40", "type": "OPINION"}], "source_ids_used": [], "angle": "Dejavu = memory timestamp error, bukan ramalan"}

# REFERENCE EXAMPLE 2 — HEWAN (nyamuk + golongan darah)
{"posts": [{"title": "POST_1", "content": "Cuma 1 dari 10 orang yang jadi target utama nyamuk. Yang punya golongan darah O ditargetin 2x lebih sering dari golongan A."}, {"title": "POST_2", "content": "Bayangin lo lagi duduk di taman sama temen. Ada nyamuk mondar-mandir. Temen lo aman, lo yang abis digigitin. Lo mikir: kok gue doang sih? Darah manis kali ya."}, {"title": "POST_3", "content": "Gue perhatiin, banyak yang masih percaya darah manis/asin. Padahal riset udah nunjukin: nyamuk milih target berdasarkan kombinasi karbon dioksida, asam laktat, dan amonia dari keringet."}, {"title": "POST_4", "content": "Golongan darah O keluar sinyal kimia 2x lebih banyak dari A dan B. Bukan soal manis — ini soal sinyal yang kebaca dari jarak 50 meter. Nyamuk betina nge-track molekul CO2 lo kayak GPS."}, {"title": "POST_5", "content": "Buat yang golongan darah O: pakai baju lengan panjang, hindari olahraga outdoor jam senja, pilih repellent dengan DEET. Bukan jaminan 100%, tapi nurunin kemungkinan digigit."}, {"title": "POST_6", "content": "Intinya bukan darah manis — nyamuk cuma jago baca sinyal. Kalian sering digigit nyamuk atau jarang? Golongan darah apa? Share pengalaman kalian."}], "claims_used": [{"claim": "1 dari 10 orang jadi target utama nyamuk", "type": "OPINION"}, {"claim": "Golongan darah O ditargetin 2x lebih sering", "type": "OPINION"}, {"claim": "Nyamuk mendeteksi karbon dioksida, asam laktat, dan amonia", "type": "OPINION"}], "source_ids_used": [], "angle": "Nyamuk pilih target berdasarkan sinyal kimia, bukan darah manis"}

END REFERENCE"""

# ══════════════════════════════════════════════
#   ENGAGEMENT TRACKING
# ══════════════════════════════════════════════

def load_data():
    try:
        return json.loads(POSTED_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"topics": [], "recent_content": {"openings": [], "ctas": [], "analogies": [], "characters": [], "local_details": [], "angles": []}}

def save_data(data):
    POSTED_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def pull_engagement():
    """Fetch per-post engagement metrics from Threads and save."""
    if not THREADS_TOKEN or DRY_RUN:
        return 0
    data = load_data()
    topics = data.get("topics", [])
    updated = 0
    for t in topics:
        media_id = t.get("media_id") or t.get("post_id")
        if not media_id or t.get("likes") is not None:
            continue
        try:
            r = httpx.get(
                f"{GRAPH}/{media_id}",
                params={"fields": "like_count,replies_count,permalink", "access_token": THREADS_TOKEN},
                timeout=10
            )
            if r.status_code == 200:
                info = r.json()
                t["likes"] = info.get("like_count", 0)
                t["replies"] = info.get("replies_count", 0)
                updated += 1
            else:
                t["likes"] = 0
                t["replies"] = 0
        except (httpx.RequestError, json.JSONDecodeError):
            t["likes"] = 0
            t["replies"] = 0
    if updated:
        save_data(data)
        log.info(f"Engagement: {updated} topics updated")
    return updated

# ══════════════════════════════════════════════
#   GENERATOR — FULL PROMPTS
# ══════════════════════════════════════════════

def build_system_prompt(seed):
    system = SYSTEM_PROMPT
    system += "\n\n# SEED\n" + seed + "\n"
    return system

def build_user_prompt(seed, mode="OPINION", **kwargs):
    """Build structured user prompt template."""
    inp = {
        "mode": mode,
        "seed": seed,
        "content_objective": kwargs.get("objective", "CONVERSATION"),
        "audience_context": kwargs.get("audience", "Orang Indonesia usia produktif"),
        "desired_takeaway": kwargs.get("takeaway", ""),
        "experience_packet": kwargs.get("experiences", []),
        "source_packet": kwargs.get("sources", []),
        "recent_content": kwargs.get("recent", {
            "openings": [], "ctas": [], "analogies": [],
            "characters": [], "local_details": [], "angles": []
        })
    }
    return f"""Generate one six-post Threads chain using the following input.

<input>
{json.dumps(inp, indent=2, ensure_ascii=False)}
</input>

Additional direction:
- {mode} mode. Gunakan narator "gw" + audiens "kalian".
- ANGLES TO AVOID (already recently used — do NOT repeat these patterns): {json.dumps(inp.get('recent', {}).get('angles', [])[:5], ensure_ascii=False)}
- Do NOT phrase the angle as "bukan X, tapi Y", "bukan X, melainkan Y", or "X bukan Y, tapi Z" if similar phrasing appears in the avoided angles."""

# ══════════════════════════════════════════════
#   GENERATION
# ══════════════════════════════════════════════

CHAR_LIMITS = {"post_1": 150, "post_2": 350, "post_3": 350, "post_4": 350, "post_5": 350, "post_6": 300}

def deterministic_validate(data):
    """Run deterministic checks on output. Returns (valid, violations)."""
    violations = []
    
    if data.get("status") != "success":
        return False, ["status not success"]
    
    posts = [data.get(f"post_{i}", "") for i in range(1, 7)]
    
    # Check exactly 6 non-empty posts
    for i, p in enumerate(posts, 1):
        if not p or len(p.strip()) < 10:
            violations.append(f"post_{i}: empty or too short")
        key = f"post_{i}"
        limit = CHAR_LIMITS.get(key, 350)
        if len(p) > limit:
            # post_1: auto-truncate instead of failing (LLM can't count chars)
            if key == "post_1":
                truncated = p[:limit].rsplit('.', 1)[0] + '.'
                data["post_1"] = truncated
                p = posts[0] = truncated
            else:
                violations.append(f"{key}: {len(p)} chars exceeds limit {limit}")
    
    if len(violations) > 0:
        return False, violations
    
    # Check prohibited words
    text_lower = " ".join(posts).lower()
    for word in PROHIBITED:
        if word.lower() in text_lower:
            violations.append(f"Prohibited term: '{word}'")
    
    # Check POV — no lo/kamu in narrator text (not inside quotes)
    combined = " | ".join(posts)
    for word in ['lo', 'kamu']:
        for m in re.finditer(rf'\b{re.escape(word)}\b', combined, re.IGNORECASE):
            text_before = combined[:m.start()]
            # If even number of quotes before match → outside quoted text
            dq = text_before.count('"')
            sq = text_before.count("'")
            if dq % 2 == 0 and sq % 2 == 0:
                violations.append(f"Invalid audience pronoun: '{word}'")
                break
    
    # S1 angka — MUST have at least one digit for hook strength
    s1 = posts[0]
    if not re.search(r'\d', s1):
        violations.append("post_1: no number — REQUIRED for hook engagement")
    
    # S6 reply-bait
    s6 = posts[5].strip()
    if not s6.endswith('?'):
        violations.append("post_6: must end with a question mark — reply-bait CTA REQUIRED")
    
    # Check mode consistency
    mode = data.get("mode", "OPINION")
    claims = data.get("claims_used", [])
    source_ids = data.get("source_ids_used", [])
    
    if mode == "OPINION":
        for c in claims:
            if c.get("type") == "FACT":
                violations.append(f"FACT claim in OPINION mode: {c.get('claim', '')[:50]}")
        if source_ids:
            violations.append(f"source_ids_used not empty in OPINION mode")
    elif mode == "FACT":
        fact_source_ids = set()
        for c in claims:
            if c.get("type") == "FACT" and c.get("source_ids"):
                fact_source_ids.update(c["source_ids"])
        if source_ids:
            extraneous = set(source_ids) - fact_source_ids
            if extraneous:
                violations.append(f"source_ids_used has IDs not referenced by any FACT claim: {extraneous}")
    
    # Check claims format
    for c in claims:
        if c.get("type") in ("OPINION", "ADVICE", "ILLUSTRATION", "EXPERIENCE"):
            if c.get("source_ids"):
                violations.append(f"Non-FACT claim has source_ids: {c.get('claim', '')[:50]}")
        if c.get("type") == "FACT" and not c.get("source_ids"):
            violations.append(f"FACT claim missing source_ids: {c.get('claim', '')[:50]}")
    
    return len(violations) == 0, violations


def generate_thread(seed, mode="OPINION", **kwargs):
    """Generate one thread. Returns (post_list, claims_used, angle) or None."""
    if not LLM_KEY:
        log.error("No LLM_KEY")
        return None

    system = build_system_prompt(seed)
    user = build_user_prompt(seed, mode=mode, **kwargs)
    
    for attempt in range(1, 4):
        log.info(f"  LLM attempt {attempt}/3")
        try:
            r = httpx.post(
                f"{LLM_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "mistral-large-latest",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user}
                    ],
                    "max_tokens": 2500,
                    "temperature": 0.7
                },
                timeout=90
            )
            if r.status_code == 429:
                time.sleep(min(2 ** attempt, 30))
                continue
            if r.status_code != 200:
                log.warning(f"  HTTP {r.status_code}")
                time.sleep(min(2 ** attempt, 10))
                continue

            raw = r.text.strip()
            content_raw = r.json()["choices"][0]["message"]["content"].strip()
            content_raw = re.sub(r"<think>.*?</think>", "", content_raw, flags=re.DOTALL).strip()
            content_raw = re.sub(r"^```(?:json)?\s*", "", content_raw)
            content_raw = re.sub(r"\s*```$", "", content_raw)

            data = json.loads(content_raw)
            
            # Check for error response
            if data.get("status") == "error":
                log.warning(f"  LLM returned error: {data.get('error_code')} — {data.get('message', '')[:100]}")
                return None  # don't retry — intentional refusal
            
            # Deterministic validation
            valid, violations = deterministic_validate(data)
            if not valid:
                log.warning(f"  Validation: {violations}")
                # Try revision first before fresh regenerate
                if attempt == 1:
                    input_data = {"mode": mode, "seed": seed, "recent": kwargs.get("recent", {})}
                    revised = revise_output(content_raw, violations, input_data)
                    if revised:
                        rev_valid, rev_violations = deterministic_validate(revised)
                        if rev_valid:
                            log.info("  Revision loop — accepted")
                            data = revised
                            valid = True
                        else:
                            log.warning(f"  Revision failed: {rev_violations}")
                
                if not valid and attempt < 3:
                    time.sleep(1)
                    continue
            
            # Semantic validation
            if attempt <= 2:  # only on first 2 attempts (avoid cost on last retry)
                sem_result = semantic_validate(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    {"mode": mode, "seed": seed, "recent": kwargs.get("recent", {})},
                    {"violations": violations if not valid else []}
                )
                if sem_result.get("score", 85) < 85 and attempt < 3:
                    sem_violations = sem_result.get("violations", [])
                    log.warning(f"  Semantic: score={sem_result.get('score')}, violations={len(sem_violations)}")
                    # Inject violations as feedback for revision
                    if sem_violations and attempt == 1:
                        revised = revise_output(
                            json.dumps(data, indent=2, ensure_ascii=False),
                            sem_violations,
                            {"mode": mode, "seed": seed, "recent": kwargs.get("recent", {})}
                        )
                        if revised:
                            rev_valid, rev_violations = deterministic_validate(revised)
                            if rev_valid:
                                log.info("  Semantic revision — accepted")
                                data = revised
                                valid = True
                            else:
                                log.warning(f"  Semantic revision failed: {rev_violations}")
                    if not valid:
                        log.info(f"  Semantic quality below threshold, regenerating...")
                        time.sleep(1)
                        continue
            
            # Parse posts
            mode = data.get("mode", mode)
            angle = data.get("angle", "")
            claims = data.get("claims_used", [])
            source_ids_used = data.get("source_ids_used", [])
            
            slides = []
            for i in range(1, 7):
                key = f"post_{i}"
                text = data.get(key, "").strip()
                if text and len(text) >= 10:
                    text = _convert_pov(text)
                    # Apply character limits
                    limit = CHAR_LIMITS.get(key, 350)
                    if len(text) > limit:
                        text = text[:limit].rsplit('.', 1)[0] + '.'
                    if len(text) > MAX_CHARS:
                        text = text[:MAX_CHARS-3] + "..."
                    slides.append({"title": f"S{i}", "content": text})
            
            if len(slides) != 6:
                log.warning(f"  Wrong slide count: {len(slides)}")
                continue
            
            # Format claims_used for backward compatibility
            formatted_claims = []
            for c in claims:
                label = c.get("type", "OPINION")
                claim_text = c.get("claim", "")
                formatted_claims.append(f"{label}: {claim_text}")
            
            return slides, formatted_claims, angle

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

# ══════════════════════════════════════════════
#   REVISION LOOP
# ══════════════════════════════════════════════

REVISION_PROMPT = """You are a surgical editor. Fix ONLY the validation violations in the JSON output below.
Keep everything that isn't flagged IDENTICAL — do not rewrite, rephrase, or restructure.

VIOLATION → FIX (apply ONLY to the flagged post, not others):

══════════════════════════════════════
POST_1 NO DIGIT — post_1 must contain at least one number.
  FIX: Inject a specific statistic or number from the seed into post_1.
  WRONG: "Otak manusia lebih gampang inget hal negatif."
  RIGHT: "Otak manusia 3x lebih gampang inget hal negatif dibanding positif. Ini mekanisme survival."
  Rule: find the numeric dimension — percentage, count, ratio, time — and surface it.

══════════════════════════════════════
POST_1 EXCEEDS 150 CHARS — post_1 character limit is 150.
  FIX: Trim filler words and subordinate clauses. Keep the counter-intuitive hook + number.
  WRONG: "Ternyata ada satu fakta menarik yang mungkin kalian belum tahu tentang otak manusia..."
  RIGHT: "Otak lo cuma 2% dari badan tapi makan 20% energi harian. Tanpa lo sadari."
  Strategy: remove "Ternyata...", "Fakta menarik...", "Tahukah kalian..." — go straight to the fact.

══════════════════════════════════════
POST_N EXCEEDS 350 CHARS (N=2-5) — posts 2-5 max 350 characters each.
  FIX: Cut the weakest sentence in that post. Keep the strongest claim.
  Strategy: remove redundant explanation, merge two short sentences, or trim adjectives.

══════════════════════════════════════
POST_6 EXCEEDS 300 CHARS — post_6 max 300 characters.
  FIX: Trim closing setup, keep only the question. No recap of previous points.
  WRONG: "Jadi itulah kenapa otak lo lebih suka inget yang negatif. Pertanyaannya sekarang: hal negatif apa yang paling lo inget minggu ini?"
  RIGHT: "Hal negatif apa yang masih lo inget dari minggu ini?"
  Rule: delete everything before the question mark if it repeats earlier content.

══════════════════════════════════════
POST_6 NO QUESTION — post_6 must end with a genuine question mark.
  FIX: Replace the last sentence with a question that invites personal reply.
  WRONG: "Itulah kenapa cegukan muncul tiba-tiba."
  RIGHT: "Kapan terakhir kali kalian cegukan di momen paling gak tepat?"
  Rule: NOT rhetorical (bukan "iya kan?", "gila kan?"). Must ask for personal experience.

══════════════════════════════════════
PROHIBITED WORD — output contains a banned LinkedIn/self-dev word.
  FIX: Replace with natural Indonesian casual equivalent.
  BANNED → REPLACE WITH: mindset → cara pikir, growth → berkembang, produktivitas → hasil kerja,
     konsisten → terus-terusan, kebiasaan → rutinitas, optimal → maksimal, transformasi → perubahan,
     perjalanan → proses, healing → pemulihan, bersyukur → berterima kasih
  Rule: use everyday Indonesian (warteg-level), not seminar-level.

══════════════════════════════════════
INVALID PRONOUN — "lo"/"lu"/"kamu"/"anda" found in narrator text.
  FIX: Replace with "kalian" (audience) or "gw/gue" (narrator). Keep inside quotes unchanged.
  WRONG: "Otak lo cuma 2% dari badan" (narrator text)
  RIGHT: "Otak kalian cuma 2% dari badan" (narrator addressing audience)
  EXCEPTION: Dialog inside quotes ("...") stays — "terus dia bilang 'lo gila ya'" is fine.

══════════════════════════════════════
MISSING POST_N — a required post is empty or missing from JSON.
  FIX: Generate the missing post. 1 sentence is enough.
  If the seed doesn't have enough material for that slide, use a bridging question or observation.

══════════════════════════════════════
INVALID CLAIM TYPE — a claim label is wrong or missing source_ids on FACT claims.
  FIX: Relabel or add source_ids. FACT claims MUST have at least one source_id.
  FACT without source → change label to OPINION. OPINION with source → remove source_ids from that claim.

══════════════════════════════════════
SEMANTIC ISSUES — hallucination, unsupported claim, or factual error.
  FIX: Remove the specific sentence. Do NOT invent data to fix it.
  Replace with a bridging sentence that transitions naturally: "Tapi ada sisi lain yang lebih menarik." or "Kenapa bisa gitu?"
  NEVER make up statistics, study names, or expert quotes to patch a hallucination.

══════════════════════════════════════
CRITICAL RULES:
- Change ONLY what's flagged. Every other post, claim, and field stays verbatim.
- Never remove post_1's counter-intuitive hook structure — that's the engagement driver.
- Return VALID JSON with identical schema: {status, mode, seed, angle, post_1..post_6, claims_used, source_ids_used}
- One revision only. Be precise.
"""

def revise_output(output_text, violations, input_data):
    """Send original failed output + errors to LLM for targeted fix."""
    if not LLM_KEY:
        return None
    
    system = REVISION_PROMPT
    user = f"""<original_input>
{json.dumps(input_data, indent=2, ensure_ascii=False)}
</original_input>

<generated_output>
{output_text}
</generated_output>

<validation_errors>
{json.dumps(violations, indent=2)}
</validation_errors>

Fix the generated output to pass validation. Return valid JSON only."""

    try:
        r = httpx.post(
            f"{LLM_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"},
            json={
                "model": "mistral-small-latest",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user}
                    ],
                    "max_tokens": 2500,
                    "temperature": 0.3
            },
            timeout=60
        )
        if r.status_code != 200:
            return None
        
        raw = r.text.strip()
        content = r.json()["choices"][0]["message"]["content"].strip()
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        content = re.sub(r"^```(?:json)?\\s*", "", content)
        content = re.sub(r"\\s*```$", "", content)
        data = json.loads(content)
        return data
    except Exception:
        return None

# ══════════════════════════════════════════════
#   SEMANTIC VALIDATOR + RECENT CONTENT EXTRACTOR
# ══════════════════════════════════════════════

LOCAL_KEYWORDS = ["KRL", "warteg", "indomie", "angkot", "ojol", "kosan",
    "nasi Padang", "pasar", "stasiun", "terminal", "gang", "kontrakan",
    "kampus", "kopi", "Indomaret", "Alfamart", "MRT", "gojek", "grab",
    "Jakarta", "Bandung", "Jogja", "Surabaya"]

def _extract_elements(slides, angle):
    """Extract structural elements from generated slides for repetition prevention."""
    if not slides:
        return {"openings": [], "ctas": [], "analogies": [], "characters": [], "local_details": [], "angles": []}
    
    s1 = slides[0]["content"] if len(slides) > 0 else ""
    s6 = slides[-1]["content"] if len(slides) > 0 else ""
    all_text = " ".join(s["content"] for s in slides)
    
    # Opening: first meaningful sentence of S1
    opening = s1.split(".")[0].strip() if s1 else ""
    opening = re.sub(r"[‘’'\"!?…,]", "", opening).strip().lower()
    opening = opening[:80]
    
    # CTA: last sentence of S6 (after "kalian bisa", "coba", "mulai")
    s6_sentences = [s.strip() for s in re.split(r'[.!?\n]', s6) if s.strip()]
    cta = s6_sentences[-1] if s6_sentences else ""
    cta = re.sub(r"[‘’'\"…]", "", cta).strip().lower()
    cta = cta[:80]
    
    # Analogies: sentences containing analogy markers
    analogies = []
    for s in slides:
        for sent in re.split(r'[.!?\n]', s["content"]):
            if any(m in sent.lower() for m in ["kayak", "seperti", "ibarat", "bagai", "laksana", "mirip", "sama kayak"]):
                cleaned = sent.strip()[:100]
                if len(cleaned) > 15 and cleaned not in analogies:
                    analogies.append(cleaned)
    
    # Characters: "si [proper noun]" patterns
    chars = list(set(re.findall(r'\bsi\s+[A-Z][a-z]+', all_text)))
    
    # Local details
    found_locals = [kw for kw in LOCAL_KEYWORDS if kw.lower() in all_text.lower()]
    
    return {
        "openings": [opening] if opening else [],
        "ctas": [cta] if cta else [],
        "analogies": analogies,
        "characters": chars,
        "local_details": found_locals,
        "angles": [angle] if angle else []
    }

def _update_recent(data, elements):
    """Append elements to recent_content, keep last 5."""
    recent = data.setdefault("recent_content", {
        "openings": [], "ctas": [], "analogies": [],
        "characters": [], "local_details": [], "angles": []
    })
    for key in ("openings", "ctas", "analogies", "characters", "local_details", "angles"):
        recent.setdefault(key, [])
        for item in elements.get(key, []):
            if item and item not in recent[key]:
                recent[key].append(item)
        recent[key] = recent[key][-5:]  # keep last 5
    
    # Trim empty items
    for key in ("openings", "ctas", "analogies", "characters", "local_details", "angles"):
        recent[key] = [x for x in recent[key] if x]
    
    data["recent_content"] = recent
# ══════════════════════════════════════════════

SEMANTIC_VALIDATOR = """You are a strict semantic reviewer for a six-post Threads chain written for @ryanhadiii.

You will receive:
1. the original generation input;
2. the generated JSON;
3. deterministic validation results.

Do not rewrite the content. Identify only actionable violations that are not already fully described by deterministic validation.

Review for:
- invented first-person experience or observation;
- factual claims unsupported by `source_packet`;
- altered certainty, causality, population, date, or scope;
- hypothetical illustrations presented as real events;
- advice presented as a promise;
- diagnosis, treatment, or unsafe mental-health framing;
- mismatch with the seed, objective, audience, or desired takeaway;
- weak continuity across the six posts;
- repetitive, robotic, preachy, or corporate voice;
- forced Indonesian details, dialogue, analogy, or CTA;
- an ending that introduces a new argument.

Score the chain from 0 to 100 using:
- Truth and authenticity: 30
- Relevance and narrative coherence: 25
- Natural voice: 20
- Hook and retention: 15
- Practical value: 10

Passing score: 85.

Return valid JSON only:
{
  "valid": true,
  "score": 0,
  "violations": [
    {
      "code": "SHORT_MACHINE_READABLE_CODE",
      "post": "post_1 through post_6 or metadata",
      "severity": "ERROR or WARNING",
      "explanation": "concise explanation",
      "required_change": "specific correction instruction"
    }
  ]
}

Set `valid` to true only when there are no ERROR violations and the score is at least 85.
Do not add Markdown or commentary outside the JSON."""

def semantic_validate(slides_text, input_data, deterministic_results):
    """Run LLM-based semantic validation."""
    if not LLM_KEY:
        return {"valid": True, "score": 85, "violations": []}
    
    system = SEMANTIC_VALIDATOR
    user = f"""<original_input>
{json.dumps(input_data, indent=2, ensure_ascii=False)}
</original_input>

<generated_output>
{slides_text}
</generated_output>

<deterministic_validation>
{json.dumps(deterministic_results, indent=2, ensure_ascii=False)}
</deterministic_validation>"""

    for attempt in range(1, 3):
        try:
            r = httpx.post(
                f"{LLM_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "mistral-small-latest",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user}
                    ],
                    "max_tokens": 1000,
                    "temperature": 0.1
                },
                timeout=30
            )
            if r.status_code != 200:
                if attempt < 2:
                    time.sleep(2)
                    continue
                return {"valid": True, "score": 85, "violations": []}
            
            content = r.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
            result = json.loads(content)
            return result
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
                continue
            return {"valid": True, "score": 85, "violations": [], "error": str(e)}

# ══════════════════════════════════════════════
#   THREADS POSTING
# ══════════════════════════════════════════════

def post_to_threads(slides):
    if not THREADS_TOKEN:
        log.error("No THREADS_ACCESS_TOKEN")
        return None, None
    
    def create_container(text, reply_to_id=None):
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', text)
        text = re.sub(r'(?<!Mr)(?<!Mrs)(?<!Ms)(?<!Dr)(?<!St)(?<!vs)(?<!Jr)(?<!Sr)(?<!Prof)([.?!])\s+(?=[A-Z])', r'\1\n\n', text)
        data = {"user_id": USER_ID, "text": text, "access_token": THREADS_TOKEN, "media_type": "TEXT"}
        if reply_to_id:
            data["reply_to_id"] = reply_to_id
        try:
            r = httpx.post(f"{GRAPH}/{USER_ID}/threads", data=data, timeout=15)
            return r.json().get("id") if r.status_code == 200 else None
        except (httpx.RequestError, json.JSONDecodeError, KeyError) as e:
            log.error(f"  Create container fail: {e}")
            return None

    # Flow: create, publish, get published ID, create next with reply_to
    ids = []
    parent_publish_id = None
    first_publish_id = None

    for i, s in enumerate(slides):
        # 1. Create container (with retry)
        container_id = None
        for retry in range(3):
            container_id = create_container(s["content"], parent_publish_id)
            if container_id:
                break
            log.warning(f"  Retry {retry+1}/3 create container for {s['title']}")
            time.sleep(2 * (1 + retry))
        if not container_id:
            log.error(f"  Failed to create container for {s['title']} after 3 retries")
            return None, ids
        time.sleep(1.5)

        # 2. Publish immediately (with retry)
        publish_data = None
        for retry in range(3):
            try:
                r = httpx.post(
                    f"{GRAPH}/{USER_ID}/threads_publish",
                    data={"access_token": THREADS_TOKEN, "creation_id": container_id},
                    timeout=15
                )
                if r.status_code == 200:
                    publish_data = r.json()
                    break
                log.warning(f"  Retry {retry+1}/3 publish {s['title']}: {r.status_code}")
            except (httpx.RequestError, json.JSONDecodeError, KeyError) as e:
                log.warning(f"  Retry {retry+1}/3 publish error: {e}")
            time.sleep(2 * (1 + retry))
        if not publish_data:
            log.error(f"  Publish failed for {s['title']} after 3 retries")
            return None, ids
        publish_id = publish_data.get("id") or publish_data.get("media_id")
        if not publish_id:
            log.error(f"  No id in publish response: {publish_data}")
            return None, ids

        if first_publish_id is None:
            first_publish_id = publish_id
        ids.append(container_id)
        parent_publish_id = publish_id  # next post replies to this published post
        time.sleep(1.5)

    media_id = ids[0] if ids else None
    return media_id, first_publish_id

# ══════════════════════════════════════════════
#   MAIN
# ══════════════════════════════════════════════

def main():
    log.info("=== DRY RUN ===" if DRY_RUN else "=== RYANHADI CONTENT ENGINE V6 ===")
    
    data = load_data()
    if not DRY_RUN:
        pull_engagement()
    
    seed_raw = _pick_seed(data)
    seed = _clean_seed(seed_raw)
    log.info(f"Seed: {seed}")
    
    # Load recent_content for repetition prevention
    recent_content = data.get("recent_content", {
        "openings": [], "ctas": [], "analogies": [],
        "characters": [], "local_details": [], "angles": []
    })
    
    # Generate
    mode = "OPINION"  # default; FACT mode uses --fact flag
    if "--fact" in sys.argv:
        mode = "FACT"
    
    result = generate_thread(seed, mode=mode, recent=recent_content)
    if result is None:
        log.error("Generation failed")
        sys.exit(1)
    
    slides, claims, angle = result
    
    # Log summary
    for s in slides:
        snippet = s["content"][:80].replace("\n", " ")
        log.info(f"  {s['title']}: {snippet}...")
    
    # Print slides
    for s in slides:
        print(f"\n--- {s['title']} ---\n{s['content']}")
    
    print(f"\nSeed: {seed}")
    if angle:
        print(f"Angle: {angle}")
    if claims:
        print(f"Claims: {', '.join(claims)}")
    
    # Extract structural elements to prevent repetition
    elements = _extract_elements(slides, angle)
    _update_recent(data, elements)
    
    if not DRY_RUN:
        log.info("Posting...")
        media_id, first_id = post_to_threads(slides)
        if media_id:
            # Save posted
            data.setdefault("topics", []).append({
                "title": seed, "posted": datetime.now(WIB).isoformat(),
                "claims": claims, "angle": angle,
                "category": _categorize(seed),
                "media_id": media_id, "post_id": first_id
            })
            save_data(data)
            log.info("  Done")
        else:
            log.error("  Post failed")
    
    log.info("Done.")


if __name__ == "__main__":
    main()
