#!/usr/bin/env python3
"""
RYANHADI CONTENT ENGINE V6
Threads content pipeline — 6-slide viral fact chain.
Google News RSS trending integration. Seed engagement weighting.
"""

import httpx, json, random, re, sys, time
from pathlib import Path

DRY_RUN = "--dry-run" in sys.argv
TREND_ENABLED = "--no-trend" not in sys.argv

BASE = Path(__file__).parent
POSTED_FILE = BASE / "posted_topics_v2.json"
HOT_CACHE = BASE / "hot-cache.json"

GRAPH = "https://graph.threads.net/v1.0"
THREADS_TOKEN = None
try:
    import os
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
    THREADS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN")
except Exception:
    pass


# ── Logging ──
import logging
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("techbro")


# ── Banned / dedup lists ──
BANNED_TITLES = [
    "self improvement", "keharusan", "terbakar", "mindset pertumbuhan",
    "berinvestasi pada diri sendiri", "ubah hidupmu", "rahasia sukses",
    "langkah nyata", "mindset", "growth mindset", "berkembang",
    "versi terbaik", "berani keluar dari", "zona nyaman", "ubah pola pikir",
    "positif thinking", "affirmation", "self love", "healing journey",
    "inner child",
]
RECENT_KWS = [
    "anda", "self", "improvement", "healing", "mindset",  # not needed here
]


# ── Seed pool — 3 categories: otak, hewan, kesehatan ──
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
    # Fakta kesehatan & gaya hidup
    "5.000 langkah per hari 50% lebih efektif turunin tekanan darah dr 10.000 — bukan makin banyak makin bagus",
    "Vitamin D dari sinar matahari pagi 15 menit setara 10.000 IU — tapi 70% orang Indonesia tetep defisit",
    "Garam di indomie 1 bungkus udah 60% batas harian — bukan cuma bikin haus, ini silent killer ginjal",
    "Duduk 8 jam per hari naikin risiko penyakit jantung 40% meski lo olahraga 1 jam",
    "Minum air putih 2 liter sehari itu mitos — kebutuhan cairan beda-beda per orang, gak ada angka universal",
    "Sakit kepala tegang 90% bukan di otak — otot leher dan bahu yang kaku, sarafnya sampe ke kepala",
    "Gula aren lebih sehat dari gula pasir? Mitos. Glukosa+fruktosa tetap sama, cuma beda rasa doang",
    "Tidur 6 jam 30 menit bisa lebih nyenyak dr 9 jam — kuncinya siklus REM, bukan durasi total",
    "Asam lambung naik bukan karena pedas — 80% dipicu stres, posisi tidur, dan porsi makan",
    "Kolesterol tinggi 70% faktor genetik — pola makan cuma 30%. Jangan bully orang gemuk",
    "Olahraga malam bikin susah tidur? Mitos. 20 menit yoga ringan 1 jam sebelum tidur malah bikin lelap",
    "Flu dan batuk sembuh 7 hari tanpa obat kalo imun lo jalan. Antibiotik guna buat bakteri, bukan virus",
    "Berat badan turun drastis 2 minggu pertama diet — 80% air, bukan lemak. Jangan seneng dulu",
    "Mata minus makin parah bukan karena main HP — 60% genetik, sisanya jarak baca + cahaya",
    "Hidung mampet salah satu sisi bukan sinus — tubuh sengaja shift sirkulasi tiap 2-4 jam",
    "Sarapan ternyata gak wajib buat turun BB. Puasa 14 jam bisa reset metabolisme",
    "Telinga berdenging 90% bukan penyakit — otot kecil di dalam telinga kram akibat stres atau kafein",
    "Kaki bengkak abis jalan jauh bukan lemak — cairan limfatik numpuk. Angkat kaki 20 menit = reset",
    "Makan 1x sehari bisa lebih sehat dr 6x kalo total kalori sama — bukan frekuensi, totalnya yang penting",
    "Jerawat di dagu 80% hormon — cuci muka 5x sehari gak ngaruh kalo dalemannya masih bermasalah",
    "Sendawa 20-30x per hari normal. Kalo lebih — bakteri usus lagi produksi gas berlebih",
    "Lemak gak bisa dikonversi jadi otot — push-up gak bikin lengan kecil mengecil, cuma ngencengin",
    "Minum es abis olahraga bikin radang tenggorokan? Mitos. Air es justru cepetin recovery otot",
    "Imun turun 30% kalo tidur <6 jam — lebih gampang sakit dibanding yang tidur 7-9 jam",
    "Makanan fermentasi (tempe, tape, kimchi) bikin bakteri usus sehat — 90% imun berasal dari usus",
]


def _pick_seed(data):
    """Pick seed with engagement weighting + cross-category balancing.
    
    Categories: otak=0, hewan=1, kesehatan=2. Tracks last 3 categories to avoid
    consecutive repeats. Seeds with engagement data get +50% weight.
    Fallback: pure random if <5 engaged posts.
    """
    topics = data.get("topics", [])
    used_topics = [t.get("title", "") for t in topics[-100:]]
    unused = [s for s in SEEDS if s not in used_topics]
    if not unused:
        unused = list(SEEDS)

    # category balancing
    last_cats = []
    for t in topics[-3:]:
        s = t.get("title", "")
        c = _SEED_CAT.get(s, -1)
        if c >= 0:
            last_cats.append(c)

    weights = []
    for s in unused:
        w = 1.0
        cat = _SEED_CAT.get(s, -1)

        # penalty for repeat category
        if cat in last_cats:
            w *= 0.5 + 0.5 / (1 + last_cats.count(cat))
        # bonus for fresh category
        elif last_cats and cat not in last_cats:
            w *= 1.5

        # viral potential boost
        vp = _viral_potential(s)
        w *= (0.7 + 0.3 * vp)

        # engagement boost
        post_count = sum(1 for t in topics[-50:] if t.get("title", "") == s)
        if post_count > 0:
            w *= 1.5

        weights.append(max(w, 0.1))

    choice = random.choices(unused, weights=weights, k=1)[0]
    return choice


# Seed→category mapping (indices: 0-24 otak, 25-54 hewan, 55-79 kesehatan)
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


def _viral_potential(seed):
    """Score seed viral potential 1-5 (keyword heuristics). 5 = high WTF."""
    score = 1
    s = seed.lower()
    if re.search(r'\d+', s): score += 1           # angka = specific
    if any(w in s for w in ['kenapa','ternyata','padahal','bikin','tanpa']):
        score += 1                                  # counter-intuitive framing
    if any(w in s for w in ['kalian','hidup','tubuh','otak','rasa','sehari','sehat','kesehatan']):
        score += 1                                  # personal relevance
    if any(w in s for w in ['hewan','kucing','burung','serangga','laut','mata']):
        score += 1                                  # visual/novelty factor
    # subtle wording → higher potential
    if seed[-1] not in '?!.' and len(seed.split()) > 8:
        score += 1                                  # complex claim
    return min(score, 5)


# ══════════════════════════════════════════════
#   SYSTEM PROMPT — Viral Fact Framework
# ══════════════════════════════════════════════

SYSTEM_PROMPT = """# VIRAL FACT FRAMEWORK — @ryanhadiii Threads Engine

You are the content engine for @ryanhadiii. Turn the supplied input into exactly six posts forming one narrative arc.

<instruction_priority>
1. Truth, source grounding, safety
2. Valid JSON and exact output structure
3. POV, prohibited language, character limits
4. Narrative flow — one idea per slide, progress across chain
5. Stylistic preferences
If instructions conflict, higher priority wins.
</instruction_priority>

<brand>
Account: @ryanhadiii
Promise: "Mengungkap fakta tersembunyi di balik hal yang terlihat biasa, lalu menjelaskan kenapa fakta itu penting."
Positioning: Curated curiosity — membedah fakta sehari-hari dengan perspektif lokal Indonesia. Bukan sekadar trivia, tapi koneksi ke pengalaman yang dirasa familiar.
Core pillars:
- Otak, Tubuh, dan Perilaku (20%) — memory, sleep, bias, social behavior
- Sains, Alam, dan Semesta (15%) — animals, physics, nature, counterintuitive science
- Teknologi dan Penemuan (15%) — accidental inventions, design choices
- Mitos vs Fakta (25%) — mythbusting sehari-hari, kesehatan, makanan
- Kedengarannya Bohong, Tapi Benar (15%) — verified historical/scientific twists
- Hal Biasa, Fakta Luar Biasa (10%) — hidden reasons behind familiar things
Audience: Indonesia usia 18-35, suka tulisan singkat, relatable, gak menggurui.
Desired reader response: "Oh iya juga. Gw belum pernah ngeliatnya dari sisi itu."
</brand>

<truth_policy>
- OPINION mode (default): no invented statistics, surveys, or quotes. Advice = suggestion.
- Never present first-person experience as Ryan's unless supplied as `experience_packet`.
- For health/psychology/nutrition claims: state uncertainty. Avoid advice beyond evidence.
- Use at least two credible sources for the central claim when available.
- Trace claims back: reject trivia found only in other social posts.
- Number hygiene: always include unit, population, period, or comparison context.
</truth_policy>

<voice>
POV: Narrator = "gw". Audience = "lu". Never "lo"/"kalian"/"kamu"/"anda"/"gue".
Tone: Indonesian conversational, curious, grounded. Short+medium sentences. Zero emoji, zero hashtags.
No moral lessons from evidence. No generic CTA like "setuju gak?".
Dialogues optional, only if natural.
</voice>

<anti_ai_writing>
SENTENCE VARIETY: Mix short (3-8), medium (10-15), occasional long (15-20). 1-2 fragments.
TRANSITIONS: Vary each slide's opener. Never repeat same word across 2+ slides.
Never: rhetorical question transitions ("Hasil akhir?", "Dampaknya?") or meta labels ("Ironisnya...", "Realitanya...").
BREAK SYMMETRY: Lists of 2 or 4+ items, never 3.
PUNCTUATION: Max 1 em dash per post. Don't tell readers how to feel ("Bikin geleng", "Nomor terakhir paling gila").
DETAILS: Concrete > abstract. "Indomie + telur" not "makanan enak".
</anti_ai_writing>

<prohibited_output>
Banned (case-insensitive): you won't believe, shocking, let that sink in, gila banget, link in bio, self improvement, keharusan, terbakar, mindset pertumbuhan, berinvestasi pada diri sendiri, ubah hidupmu, rahasia sukses, langkah nyata, mindset, growth mindset, berkembang, versi terbaik, berani keluar dari, zona nyaman, ubah pola pikir, positif thinking, affirmation, self love, healing journey, inner child. No empty placeholders ("...", "Rp...", "$...").
</prohibited_output>

<slide_structure>
Six slides, one narrative arc. Each slide has exactly one job.

Slide 1 — Stop (max 150 chars, 1-2 sentences)
OPEN with a specific number or concrete claim. The preview text (~80 chars) must contain the surprising claim, not setup.
FORMULA: Familiar anchor + contradiction + unanswered question.
Bad: "Alis manusia punya 250 rambut. Fungsinya..." — too long.
Good: "7 dari 10 orang pernah ngalamin dejavu."
Good: "250 rambut. Fungsi alis: nahan keringat."
Rule: max 12 words. Keep the central fact visible, don't hide it.

Slide 2 — Set up (max 350 chars)
Make reader care. Recognizable context + common assumption + what's at stake.
REQUIRED: use a UNIVERSAL Indonesian setting when relevant.
Good: kosan, KRL, warteg, nasi Padang, ojek online, macet Jakarta, antrian, ujian, Zoom meeting, hujan-hujanan.

Slide 3 — Reveal (max 350 chars)
Deliver the central fact. Verified fact + concrete detail + evidence cue.
State the answer clearly. Include date, number, or comparison when useful.

Slide 4 — Explain (max 350 chars)
Make the fact understandable. Cause/mechanism + plain-language explanation + analogy/example.
Explain "why" or "how", not merely what happened.

Slide 5 — Deepen (max 350 chars)
Add a second reward. Implication + supporting twist + relevance to reader.
Build on the main fact, never introduce unrelated trivia.

Slide 6 — Land (max 300 chars)
Close the loop and invite response. One-sentence takeaway + creator lens + specific CTA.
CTA choices:
- Experience: "Lo pernah ngalamin versi kecil dari fenomena ini?"
- Choice: "Kalo harus pilih, lo lebih penasaran sama [A] atau [B]?"
- Belief check: "Sebelum baca ini, lo percaya yang mana?"
- Observation: "Hal biasa apa yang pengin lo tahu alasan di baliknya?"
- Share prompt: "Tag temen yang [relate]."
Don't start with "Pernah nggak". Speak to one person, not crowd.
</slide_structure>

<hook_formulas>
Use one of these patterns for Slide 1. Replace brackets with concrete content.

1. Belief reversal: "Kebanyakan orang kira [belief]. Padahal [verified reversal]—dan penyebabnya bukan [obvious answer]."
2. Everyday blind spot: "Lo [familiar action] hampir tiap hari, tapi mungkin gak sadar kalau [surprising claim]."
3. Sounds fake, but verified: "Fakta ini kedengarannya ngarang: [specific claim]. Tapi [evidence cue] nunjukin sebaliknya."
4. Hidden cause: "Ada alasan kenapa [relatable phenomenon] selalu [unexpected behavior]. Jawabannya di [unexpected field]."
5. Myth breaker: "[Popular claim] ternyata bukan fakta utuh. Yang sebenarnya terjadi jauh lebih menarik."
6. Specific number shock: "[Concrete number] terdengar biasa—sampai lo bandingin sama [familiar scale]."
7. Accidental origin: "[Common thing] yang kita pake sekarang ternyata lahir gara-gara [mistake/accident]."
8. Unexpected proximity: "Salah satu fakta paling aneh soal [topic] ternyata dekat banget: [open loop]."
9. Counterfactual question: "Apa yang terjadi kalo [familiar condition] tiba-tiba [specific change]? Jawabannya bukan [expected outcome]."
10. Two facts, one twist: "[Fact A] dan [fact B] keliatan gak berhubungan. Ternyata keduanya terhubung oleh [open loop]."
</hook_formulas>

<trending_rule>
If `<trending_context>` is provided, you MAY reference 1 trending topic naturally in Slide 2 (set up) or Slide 3 (reveal) if it connects to the seed. Reference style: "Baru-baru ini ramai soal X..." — never "Menurut trending topic..." or "Berdasarkan tren...". Never force. If no trend fits, ignore.
</trending_rule>

<quality_gate>
Before returning, verify:
1. Central fact is supported and accurately worded.
2. Hook and conclusion don't overstate sources.
3. Slide 1 is immediately understandable.
4. Each slide has exactly one job (Stop/Set up/Reveal/Explain/Deepen/Land).
5. Main reveal appears by Slide 3.
6. Slide 4 explains a mechanism, not just what.
7. Slide 5 adds a relevant second reward, not unrelated trivia.
8. Slide 6 closes original loop and asks a specific question.
9. No slide starts with "Pernah nggak".
10. Voice sounds human when read aloud.
</quality_gate>

<output_contract>
Return valid JSON only, no markdown fences, no commentary. Use these exact keys:

Success:
{"status":"success","pillar":"...","angle":"...",
 "post_1":"...","post_2":"...","post_3":"...",
 "post_4":"...","post_5":"...","post_6":"...",
 "claims_used":[{"post":"post_N","type":"OPINION|ADVICE|ILLUSTRATION|EXPERIENCE","claim":"..."}],
 "hook_pattern":"..."}

Error:
{"status":"error","error_code":"INSUFFICIENT_SOURCE_PACKET|UNSAFE_TOPIC|INVALID_INPUT","message":"..."}

Rules:
- angle: one concise sentence describing chosen perspective.
- pillar: which core pillar this thread belongs to.
- claims_used: substantive claims only, not every stylistic sentence.
- hook_pattern: which hook formula pattern was used (e.g. "belief_reversal", "myth_breaker", "number_shock").
</output_contract>

# REFERENCE EXAMPLE 1 — OTAK (dejavu)
{"status":"success","pillar":"Mitos vs Fakta","angle":"Dejavu = memory timestamp error, bukan ramalan. Otak error ini normal dan umum.",
 "post_1":"7 dari 10 orang pernah ngalamin dejavu. Tapi itu bukan ramalan—otak lu cuma lagi error nulis timestamp memori.",
 "post_2":"Bayangin lu lagi di kosan, denger lagu yang gak pernah didenger. Tapi tiba-tiba ngerasa: \\"Gw udah pernah ngalamin ini persis.\\" Padahal gak. Apa yang sebenarnya terjadi?",
 "post_3":"Otak lu punya dua sistem memori: satu nyimpen pengalaman baru, satu ngecek kalo udah pernah. Kadang sinyal nyasar—memori baru dikasih label \\"udah pernah\\", padahal baru pertama kali. Namanya dejavu.",
 "post_4":"Peneliti di Colorado State nemuin dejavu makin sering pas otak capek atau stres. Makin tinggi beban kognitif, makin gampang sistem memorinya error. Bukan mistis.",
 "post_5":"Bonus: usia 15-25 golden age dejavu. Setelah 40, hippocampus mulai lambat—frekuensinya turun drastis. Bukan otak makin bagus, cuma makin pelan prosesnya.",
 "post_6":"Intinya dejavu wajar, bukan mistis. Cuma glitch sistem. Lo pernah ngerasa dejavu pas lagi ngapain? Share di komen.",
 "claims_used":[{"post":"post_1","type":"OPINION","claim":"7 dari 10 orang pernah alami dejavu"},{"post":"post_3","type":"OPINION","claim":"Dejavu karena kesalahan pelabelan memori"},{"post":"post_5","type":"OPINION","claim":"Frekuensi dejavu menurun setelah usia 40"}],
 "hook_pattern":"belief_reversal"}

# REFERENCE EXAMPLE 2 — SAINS ALAM (nyamuk + golongan darah)
{"status":"success","pillar":"Mitos vs Fakta","angle":"Nyamuk pilih target berdasarkan sinyal kimia, bukan darah manis. Penjelasan mekanisme di balik pilihan nyamuk.",
 "post_1":"Cuma 1 dari 10 orang yang jadi target utama nyamuk. Yang punya golongan darah O ditargetin 2x lebih sering dari golongan A.",
 "post_2":"Lo pernah nongkrong di taman sama temen. Nyamuk mondar-mandir. Temen lu aman, lu yang digigitin. Lo mikir: \\"Kok gw doang sih? Darah manis kali ya.\\"",
 "post_3":"Padahal riset udah nunjukin: nyamuk milih target berdasarkan karbon dioksida, asam laktat, dan amonia dari keringet. Golongan darah O keluar sinyal kimia 2x lebih banyak.",
 "post_4":"Gak ada hubungan sama rasa darah. Nyamuk betina nge-track molekul CO2 dari napas lu kayak GPS dari jarak 50 meter. Sinyal golongan O lebih kuat kebaca—itu doang.",
 "post_5":"Implikasinya menarik: kalo lo jarang digigit, bukan berarti darah lo \\"tawar\\". Bisa jadi komposisi kimia kulit lo kurang detectable. 15% manusia gak pernah digigit.",
 "post_6":"Intinya bukan darah manis—nyamuk cuma jago baca sinyal. Golongan darah lo apa? Share di komen kalo lo sering atau jarang digigit nyamuk.",
 "claims_used":[{"post":"post_1","type":"OPINION","claim":"1 dari 10 orang jadi target utama nyamuk"},{"post":"post_3","type":"OPINION","claim":"Nyamuk mendeteksi karbon dioksida, asam laktat, dan amonia"}],
 "hook_pattern":"number_shock"}

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
#   TRENDING CONTEXT (Google News Indonesia)
# ══════════════════════════════════════════════

_TREND_CACHE = None

def _fetch_trending_context(seed):
    """Get Indonesia trending topics related to seed via Google News RSS. Returns dict or None."""
    global _TREND_CACHE
    if not TREND_ENABLED:
        return None

    try:
        if _TREND_CACHE is None:
            import xml.etree.ElementTree as ET
            url = 'https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRFZxYUdjU0FtVnVHZ0pWVXlnQVAB?hl=id&gl=ID&ceid=ID:id'
            r = httpx.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                items = root.findall('.//item')
                all_trends = []
                for item in items:
                    title = item.findtext('title', '')
                    if title:
                        all_trends.append(title)
                _TREND_CACHE = all_trends[:50]
            else:
                return None

        trends = _TREND_CACHE
        if not trends:
            return None

        # Keyword matching against seed
        seed_lower = seed.lower()
        seed_words = re.findall(r'[a-z]+', seed_lower)
        stopwords = {'yang', 'di', 'ke', 'dari', 'dan', 'ini', 'itu', 'nya', 'dengan', 'untuk',
                     'tidak', 'ada', 'akan', 'bisa', 'dalam', 'pada', 'lebih', 'setelah', 'sampai',
                     'bagi', 'oleh', 'atau', 'sebagai', 'karena', 'telah', 'saja', 'juga', 'hanya',
                     'saya', 'dia', 'mereka', 'kita', 'kami', 'saat', 'banyak', 'antara', 'punya',
                     'baru', 'program', 'ternyata', 'rupanya', 'memang', 'justru', 'bahkan',
                     'nyatanya', 'lagi', 'pula', 'kembali', 'terjadi', 'mungkin'}
        seed_kws = [w for w in re.findall(r'\w+', seed_lower)
                    if w not in stopwords and len(w) > 4]

        # Category-level boost: match against broader topic area
        category_map = {
            'otak': ['otak', 'otak', 'pikiran', 'memori', 'tidur', 'mimpi', 'dejavu', 'sadar', 'bawah sadar', 'psikologi'],
            'hewan': ['hewan', 'kucing', 'anjing', 'burung', 'ikan', 'serangga', 'nyamuk', 'lalat', 'bintang', 'laut', 'alam', 'satwa'],
            'kesehatan': ['kesehatan', 'sehat', 'vitamin', 'darah', 'jantung', 'ginjal', 'imun', 'diet', 'lemak', 'kalori', 'gula', 'olahraga', 'tidur', 'stres', 'pencernaan', 'usus', 'metabolisme'],
            'kebiasaan': ['kebiasaan', 'rutinitas', 'kerja', 'produktif', 'waktu', 'usia', 'tua', 'dewasa', 'sehari'],
        }
        cat_kws = set()
        for cat, words in category_map.items():
            if any(w in seed_lower for w in words):
                cat_kws.update(w for w in words if len(w) > 3)

        best_match = None
        best_score = 0
        for trend in trends:
            trend_lower = trend.lower()
            score = 0
            for kw in set(seed_kws):
                if kw in trend_lower:
                    score += 3
            for kw in cat_kws:
                if kw in trend_lower:
                    score += 2
            if score > best_score:
                best_score = score
                best_match = trend

        if best_score >= 7 and best_match:
            return {"headline": best_match, "score": best_score}
        return None

    except Exception as e:
        log.warning(f"Trend fetch error: {e}")
        return None


def _clean_seed(seed):
    """Normalize seed pronouns for POV consistency."""
    s = seed.replace("lo ", "lu ").replace(" lo", " lu")
    s = s.replace("kalian ", "lu ").replace(" kalian", " lu")
    s = s.replace("gue ", "gw ").replace(" gue", " gw")
    return s.strip()


def _convert_pov(text):
    """Normalize pronouns to gw/lu. Skip quoted dialog."""
    parts = re.split(r'("[^"]*")', text)
    for i, p in enumerate(parts):
        if i % 2 == 0:  # non-quoted parts
            p = re.sub(r'\blo\b', 'lu', p)
            p = re.sub(r'\bkalian\b', 'lu', p)
            p = re.sub(r'\bkamu\b', 'lu', p)
            p = re.sub(r'\banda\b', 'lu', p)
            p = re.sub(r'\bgue\b', 'gw', p)
            p = re.sub(r'\bgua\b', 'gw', p)
            p = re.sub(r'\baku\b', 'gw', p)
            parts[i] = p
    return ''.join(parts)


def _pick_seed_with_hot_cache(data, hot_cache=None):
    """Pick seed using hot_cache engagement overlay + category balance."""
    return _pick_seed(data)


# ══════════════════════════════════════════════
#   VALIDATION
# ══════════════════════════════════════════════

INVALID_WORDS = {'lo', 'kalian', 'kamu', 'anda', 'gue', 'gua', 'aku', 'kita'}

def deterministic_validate(posts, recent_content):
    """Apply deterministic checks. Return warnings list."""
    warnings = []
    for key in ['post_1','post_2','post_3','post_4','post_5','post_6']:
        p = posts.get(key, '')
        if not p.strip():
            warnings.append(f"{key}: empty")
            continue
        # Check pronouns only outside quoted text
        outside_quotes = re.sub(r'"[^"]*"', '', p)
        words = set(re.findall(r'\b[a-z]+\b', outside_quotes.lower()))
        invalid = words & INVALID_WORDS
        if invalid:
            warnings.append(f"Invalid pronoun in {key}: {','.join(sorted(invalid))}")

    # post_6 must have CTA (question or tag invite)
    p6 = posts.get('post_6', '')
    if '?' not in p6 and 'tag' not in p6.lower() and 'kirim' not in p6.lower():
        warnings.append('post_6: missing CTA — question, tag, or share prompt REQUIRED')

    # post_1 must start with number or concrete claim
    p1 = posts.get('post_1', '')
    if p1 and not re.search(r'\d', p1.split()[0].replace(',','').replace('.','')):
        if not any(w in p1.lower() for w in ['cuma','hanya','satu']):
            warnings.append('post_1: missing number in first token')

    # Check for banned repeat from recent content
    if recent_content:
        for key in ['openings','ctas','analogies','local_details']:
            if posts.get(key, '') in recent_content.get(key, []):
                warnings.append(f"{key}: repeat from recent content")
    return warnings


# ══════════════════════════════════════════════
#   USER PROMPT BUILDER
# ══════════════════════════════════════════════

REVISION_PROMPT = """Previous output failed validation. Apply ONLY the specific fixes requested below. Keep everything else identical.

Revision instructions: {revision_notes}"""

def build_user_prompt(seed, mode="OPINION", trending=None, recent_content=None):
    prompt_parts = [f"Seed: {seed}"]
    prompt_parts.append(f"Mode: {mode} mode. Gunakan narator 'gw' + audiens 'lu'.")
    # Add trending if available
    if trending and isinstance(trending, dict):
        prompt_parts.append(f"<trending_context>\nHeadline: {trending['headline']}\nMatch score: {trending['score']}/10\nYou MAY reference this naturally in Slide 2 or 3 if it connects to the seed.\n</trending_context>")
    if recent_content and any(recent_content.values()):
        prompts = recent_content.get("openings", [])
        ctas = recent_content.get("ctas", [])
        if prompts or ctas:
            prompt_parts.append("<recent_content>")
            if prompts:
                prompt_parts.append(f"Recent openings: {' | '.join(prompts[-3:])}")
            if ctas:
                prompt_parts.append(f"Recent CTAs: {' | '.join(ctas[-3:])}")
            prompt_parts.append("Avoid repeating these patterns.</recent_content>")

    prompt_parts.append("Generate exactly 6 posts following the slide structure and output contract.")
    return "\n".join(prompt_parts)


# ══════════════════════════════════════════════
#   LLM CALL — Mistral / OpenRouter
# ══════════════════════════════════════════════

MISTRAL_SMALL = "mistral-small-latest"
MISTRAL_LARGE = "mistral-large-latest"

def _get_api_key():
    """Get Mistral API key from environment."""
    for var in ["MISTRAL_API_KEY", "MISTRAL_KEY"]:
        key = os.getenv(var)
        if key:
            return key
    # Fallback
    for var in ["OPENROUTER_API_KEY", "OPENROUTER_KEY"]:
        key = os.getenv(var)
        if key:
            return key
    return None

def _call_llm(system, user, model=MISTRAL_SMALL, max_retries=3):
    """Call Mistral API with system prompt."""
    api_key = _get_api_key()
    if not api_key:
        return None, "No API key found"

    base_url = "https://api.mistral.ai/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.8,
        "max_tokens": 1500,
    }

    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            r = httpx.post(base_url, headers=headers, json=payload, timeout=60)
            if r.status_code == 200:
                data = r.json()
                content = data["choices"][0]["message"]["content"].strip()
                return content, None
            elif r.status_code == 401:
                log.warning(f"  Auth error {r.status_code} for {model}: {r.text[:100]}")
                last_error = f"Auth error {r.status_code}"
                break
            elif r.status_code == 429:
                log.warning(f"  Rate limited {r.status_code} for {model}, retry {attempt}/{max_retries}")
                last_error = f"Rate limit {r.status_code}"
                if attempt < max_retries:
                    time.sleep(5)
            else:
                last_error = f"HTTP {r.status_code}: {r.text[:120]}"
                log.warning(f"  LLM attempt {attempt}/{max_retries} — {last_error}")
                if attempt < max_retries:
                    time.sleep(2)
        except (httpx.RequestError, json.JSONDecodeError) as e:
            last_error = str(e)[:120]
            log.warning(f"  LLM attempt {attempt}/{max_retries} — {last_error}")
            if attempt < max_retries:
                time.sleep(2)

    return None, f"{model} failed: {last_error}"


def generate_thread(seed, trending=None, recent_content=None):
    """Generate 6-post thread from seed. Returns (data, error)."""
    seed = _clean_seed(seed)
    mode = "OPINION"

    # Trend-as-seed: 30% chance override when trend match >= 10
    if trending and isinstance(trending, dict) and trending.get("score", 0) >= 10:
        if random.random() < 0.3:
            override = trending["headline"]
            # Clean up: shorten, make conversational
            override = re.sub(r' - .*$', '', override)
            override = re.sub(r'\s+', ' ', override).strip()
            if len(override) > 10 and len(override) < 80:
                log.info(f"  Trend-as-seed: \"{override}\" (replaces \"{seed}\")")
                seed = override

    # Build prompts
    system = SYSTEM_PROMPT
    user = build_user_prompt(seed, mode, trending, recent_content)

    # Call LLM
    for attempt in range(1, 4):
        content, error = _call_llm(system, user, max_retries=2)
        if error:
            log.warning(f"  LLM attempt {attempt}/3 — {error[:80]}")
            if attempt < 3:
                time.sleep(3)
            continue

        # Parse JSON
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r'^```(?:json)?\s*', '', content)
            content = re.sub(r'\s*```$', '', content)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            log.warning(f"  LLM attempt {attempt}/3 — bad JSON")
            if attempt < 3:
                time.sleep(3)
            continue

        if data.get("status") == "error":
            return None, data.get("message", "LLM error")

        # Extract posts
        posts = {k: data[k] for k in ['post_1','post_2','post_3','post_4','post_5','post_6'] if k in data}

        # Apply POV conversion
        for k in posts:
            posts[k] = _convert_pov(posts[k])

        # Validate
        warnings = deterministic_validate(posts, recent_content or {})
        if warnings:
            log.warning(f"  Validation: {warnings}")
            if attempt < 3 and len(warnings) <= 3:
                # Try revision
                rev_user = build_user_prompt(seed, mode, trending, recent_content)
                rev_user += f"\n\n{REVISION_PROMPT.format(revision_notes='; '.join(warnings))}"
                content2, error2 = _call_llm(system, rev_user, max_retries=1)
                if error2:
                    continue
                content2 = content2.strip()
                if content2.startswith("```"):
                    content2 = re.sub(r'^```(?:json)?\s*', '', content2)
                    content2 = re.sub(r'\s*```$', '', content2)
                try:
                    data2 = json.loads(content2)
                    if data2.get("status") == "success":
                        posts2 = {k: data2[k] for k in ['post_1','post_2','post_3','post_4','post_5','post_6'] if k in data2}
                        for k in posts2:
                            posts2[k] = _convert_pov(posts2[k])
                        w2 = deterministic_validate(posts2, recent_content or {})
                        if not w2 or len(w2) < len(warnings):
                            data = data2
                            posts = posts2
                            log.info("  Revision fixed validation")
                except json.JSONDecodeError:
                    pass
            if not posts:
                continue

        # Return success
        return {
            "seed": seed,
            "mode": mode,
            "angle": data.get("angle", ""),
            "pillar": data.get("pillar", ""),
            "posts": posts,
            "claims_used": data.get("claims_used", []),
            "hook_pattern": data.get("hook_pattern", ""),
        }, None

    return None, "LLM failed after 3 attempts"


# ══════════════════════════════════════════════
#   THREADS PUBLISHER
# ══════════════════════════════════════════════

def post_to_threads(seed, posts):
    """Post 6-slide chain to Threads via Graph API."""
    if not THREADS_TOKEN:
        log.error("No THREADS_ACCESS_TOKEN in environment")
        return None
    if DRY_RUN:
        log.info("DRY RUN — skipping post")
        return None

    media_ids = []
    error = None

    for i in range(1, 7):
        key = f"post_{i}"
        text = posts.get(key, "")
        if not text:
            continue

        payload = {
            "media_type": "TEXT",
            "text": text,
            "access_token": THREADS_TOKEN,
        }

        # Chain to previous post
        if media_ids:
            payload["reply_to_id"] = media_ids[-1]

        try:
            r = httpx.post(f"{GRAPH}/me/threads", data=payload, timeout=15)
            if r.status_code == 200:
                result = r.json()
                mid = result.get("id")
                if mid:
                    media_ids.append(mid)
                    log.info(f"  {key} → {mid}")
                    time.sleep(1.5)
                else:
                    error = f"{key}: no id in response"
                    break
            else:
                error = f"{key}: HTTP {r.status_code} — {r.text[:200]}"
                log.error(error)
                break
        except httpx.RequestError as e:
            error = f"{key}: {e}"
            log.error(error)
            break

    if not media_ids or error:
        # Publish what we have (retry logic omitted for now)
        return {"error": error, "media_ids": media_ids}

    # Publish all
    post_ids = []
    for mid in media_ids:
        try:
            r = httpx.post(f"{GRAPH}/{mid}/publish",
                          data={"access_token": THREADS_TOKEN}, timeout=15)
            if r.status_code == 200:
                pid = r.json().get("id")
                if pid:
                    post_ids.append(pid)
                    time.sleep(1.5)
            else:
                log.warning(f"Publish failed for {mid}: {r.status_code}")
        except httpx.RequestError as e:
            log.warning(f"Publish error for {mid}: {e}")

    if post_ids:
        return {"post_ids": post_ids, "media_ids": media_ids}
    return {"error": "publish failed", "media_ids": media_ids}


# ══════════════════════════════════════════════
#   MAIN
# ══════════════════════════════════════════════

def main():
    data = load_data()
    hot_cache = None
    try:
        if HOT_CACHE.exists():
            hot_cache = json.loads(HOT_CACHE.read_text())
    except (json.JSONDecodeError, OSError):
        pass

    # Pick seed
    seed = _pick_seed_with_hot_cache(data, hot_cache)
    log.info(f"Seed: {seed}")

    # Fetch trending
    trending = _fetch_trending_context(seed)
    if trending:
        log.info(f"  Trend matched: [{trending['score']}/10] {trending['headline']}")

    recent = data.get("recent_content", {})

    # Generate
    result, error = generate_thread(seed, trending, recent)
    if error:
        log.error(f"Generation failed: {error}")
        return

    # Display
    posts = result["posts"]
    for i in range(1, 7):
        k = f"post_{i}"
        txt = posts.get(k, "")
        first_line = txt.split('\n')[0][:80] if txt else "(empty)"
        log.info(f"  S{k[5:]}: {first_line}")

    # Post or dry-run
    if not DRY_RUN:
        pub_result = post_to_threads(seed, posts)
        if pub_result and pub_result.get("post_ids"):
            log.info(f"Posted: {pub_result['post_ids'][0]}")
            # Save topic
            topic = {
                "title": seed,
                "angle": result.get("angle", ""),
                "pillar": result.get("pillar", ""),
                "hook_pattern": result.get("hook_pattern", ""),
                "post_id": pub_result["post_ids"][0],
                "media_id": pub_result["media_ids"][0] if pub_result.get("media_ids") else None,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+07:00"),
                "likes": None,
                "replies": None,
            }
            data.setdefault("topics", []).insert(0, topic)
            # Update recent content
            rc = data.setdefault("recent_content", {})
            rc.setdefault("openings", []).insert(0, posts.get("post_1", "")[:100])
            rc.setdefault("ctas", []).insert(0, posts.get("post_6", "")[:100])
            rc.setdefault("pillars", []).insert(0, result.get("pillar", ""))
            rc.setdefault("hooks", []).insert(0, result.get("hook_pattern", ""))
            for k in ["openings", "ctas", "pillars", "hooks"]:
                rc[k] = rc[k][:10]
            save_data(data)
        elif pub_result and pub_result.get("error"):
            log.error(f"Post error: {pub_result['error']}")
    else:
        print()
        for i in range(1, 7):
            k = f"post_{i}"
            print(f"--- S{i} ---")
            print(posts.get(k, ""))
            print()
        print(f"Seed: {seed}")
        print(f"Angle: {result.get('angle', '')}")
        print(f"Pillar: {result.get('pillar', '')}")
        print(f"Hook: {result.get('hook_pattern', '')}")
        claims = result.get("claims_used", [])
        if claims:
            claim_strs = [f"{c['type']}: {c['claim'][:50]}" for c in claims]
            print(f"Claims: {', '.join(claim_strs)}")


if __name__ == "__main__":
    main()
