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
    "Kenapa mata bisa liat titik kosong sendiri — fenomena blind spot",
    "Otak punya filter kebisingan: lo bisa selektif dengar meski lagi ramai",
    "Kenapa lupa bawa kunci tapi inget lirik lagu masa kecil?",
    "Mimpi buruk: kenapa otak lo nyiksa diri sendiri pas tidur",
    "Kenapa musik bikin mood lo berubah dalam hitungan detik",
    "Merasa diamati pas sendirian? Otak lo nge-scan ancaman tanpa sadar",
    "Kenapa lo langsung ngerasa lebih baik pas cuci muka pas stres",
    "Otak cuma 2% dari badan tapi makan 20% energi harian lo",
    "Kenapa kita gak bisa geli diri sendiri? Jawabannya ada di otak kecil",
    "Earworm: kenapa lagu stuck di kepala dan susah banget dihilangin",
    "Kenapa bau bisa langsung ngingetin memori spesifik dari masa kecil",
    "Kenapa badan tiba-tiba kejang pas mau tidur — hypnic jerk",
    "Placebo effect: kenapa gula doang bisa ngurangin rasa sakit lo",
    # Fakta hewan & dunia (Indonesia)
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
    "Bunglon berubah warna bukan buat kamuflase — ini alasan sebenarnya",
    "Kaki seribu: ternyata gak segitakut yang lo kira",
    "Bebek bisa jalan di air — rahasianya ada di struktur kaki",
    "Fakta soal kecoa: bisa hidup seminggu tanpa kepala. Mitos atau fakta?",
    "Ular bisa deteksi detak jantung mangsanya dari jarak 1 meter",
    "Kenapa lalat susah banget dipukul? Mereka ngeliat dunia dalam gerakan lambat",
    "Kenapa kuda tidur sambil berdiri — dan kenapa manusia gak bisa",
    "Bintang laut: mulut di bagian bawah dan bisa regenerasi tubuh",
    "Burung hantu bisa muter kepala 270 derajat. Ini mekanisme di baliknya",
    "Siput lambat bukan kelemahan — itu strategi survival yang brilian",
    "Cumi-cumi bisa edit gen di tubuhnya sendiri — ini cara kerjanya",
    "Kenapa kucing suka ngasih hadiah tikus mati ke pemiliknya?",
    "Hiu: bisa deteksi setetes darah dalam 100 liter air",
    "Kenapa burung tidur sambil berdiri dan gak pernah jatuh",
    "Paus — yang sering lo panggil 'ikan paus' ternyata mamalia dan dulunya jalan di darat",
    # Fakta tubuh manusia (di luar otak)
    "Kenapa kita menguap — dan kenapa nular secara nggak sadar",
    "Kenapa jari keriput pas di air: bukan karena basah, ini mekanisme survival",
    "Kenapa cegukan susah dihentiin dan tiba-tiba ilang sendiri",
    "Kenapa kita punya sidik jari: bukan cuma buat KTP",
    "Alasan kenapa ada tangan dominan — kanan vs kidal",
    "Bulu kuduk merinding: ternyata pesan dari otak purba",
    "Kenapa kuping kita terus tumbuh seumur hidup?",
    "Kenapa tubuh kita demam — bukan penyakit, tapi senjata",
    "Fakta soal nafsu: kenapa lapar juga ngaruh ke emosi dan keputusan",
    "Kenapa tubuh gatal di tempat yang susah dijangkau pas lagi sepi",
    "Kenapa kita punya alis — bukan cuma buat ekspresi",
    "Urat kelihatan biru padahal darah merah — ini penjelasan optiknya",
    "Kenapa badan kedutan pas mau tidur? Otak ngira lo mau mati",
    "Bayi punya 300 tulang, dewasa cuma 206 — kemana sisanya?",
    "Kenapa manusia punya 2 ginjal padahal 1 aja cukup?",
    "Kenapa rambut rontok tiap hari tapi gak botak?",
    "Paru-paru kiri lebih kecil dari kanan — ini alasannya",
    "Kenapa rambut kepala bisa panjang tapi alis dan bulu badan cuma pendek?",
    "Lidah — peta rasa tradisional yang diajarin di sekolah ternyata udah usang",
    "Kenapa badan bisa ngerasa panas pas malu — blushing effect",
    "Keringat gak bau — bakteri di kulit lo yang bikin bau",
    "Kenapa gak bisa nahan kentut waktu tidur? Otot rileks total",
    "Kenapa beberapa orang buta warna — gak cuma hitam putih",
    "Jantung gak berhenti meski lo gak sadar — gimana cara passive survival",
    "Kenapa ada orang yang gak bisa digigit nyamuk — faktor genetik",
]

def _pick_seed(data):
    topics = data.get("topics", [])
    used_topics = [t.get("title", "") for t in topics[-100:]]
    unused = [s for s in SEEDS if s not in used_topics]
    if unused:
        return random.choice(unused)
    return random.choice(SEEDS)

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

Your only task is to turn the supplied input into one coherent Threads chain containing exactly six posts. Follow the instruction hierarchy and output contract below.

<instruction_priority>
1. Truth, source grounding, personal authenticity, and safety
2. Valid JSON and exact output structure
3. POV, prohibited language, and character limits
4. Narrative quality and relevance
5. Stylistic preferences

If two instructions conflict, follow the instruction with the higher priority.
</instruction_priority>

<brand>
Account: @ryanhadiii
Positioning: membedah masalah sehari-hari yang sering dibikin rumit, lalu menyederhanakannya lewat cerita lokal, observasi, logika santai, dan langkah kecil yang realistis.

Core topics:
- kebiasaan dan konsistensi
- cara berpikir dan pengambilan keputusan
- dilema sehari-hari
- kesehatan mental dalam batas edukasi umum
- fakta unik hewan dan dunia

Default audience:
Orang Indonesia usia produktif yang menyukai tulisan singkat, relatable, praktis, dan tidak menggurui.

Desired reader response:
"Oh iya juga. Gw belum pernah ngeliatnya dari sisi itu."
</brand>

<truth_policy>
First-person authenticity:
- Never invent an experience, conversation, habit, achievement, observation, relationship, or event and present it as something Ryan personally experienced.
- Use first-person experience only when it is explicitly supplied in `experience_packet`.
- When no real experience is supplied, use clearly hypothetical framing such as "misalnya", "bayangin", or "anggap aja".
- Fictional names may be used only as hypothetical characters. Never imply that they are real acquaintances.

OPINION mode:
- Do not introduce external factual claims that require verification.
- Do not invent statistics, informal surveys, quotations, or numerical observations.
- Advice must be framed as a suggestion, not a guaranteed outcome.
- Permitted claim types: OPINION, ADVICE, ILLUSTRATION, and EXPERIENCE only when supported by `experience_packet`.

FACT mode:
- Use only facts explicitly stated in `source_packet`.
- Never use memory or outside knowledge to add factual details.
- Every verifiable factual claim must reference at least one supplied source ID in `claims_used`.
- If the supplied sources do not support the seed, return the defined error JSON instead of guessing.
- Do not distort certainty, scope, dates, populations, or causal relationships from the sources.

Mental-health safety:
- Do not diagnose the reader or another person.
- Do not prescribe medication or treatment.
- Do not promise recovery or universal results.
- Do not discourage professional help.
- If the topic involves crisis, self-harm, suicide, abuse, or another high-risk situation, return the defined error JSON with code `UNSAFE_TOPIC_REQUIRES_SPECIALIST_FLOW`.
</truth_policy>

<voice>
POV:
- Narrator uses "gw".
- Audience is addressed as "kalian".
- Do not address the audience as "lo", "lu", "kamu", "Anda", "anda", or "gue".
- Other pronouns may appear only inside dialogue attributed to a hypothetical character.

Tone:
- Informal Indonesian, conversational, observant, and calm.
- Sound like a thoughtful friend, not a lecturer, therapist, motivational speaker, or corporate copywriter.
- Use a natural mix of short and medium-length sentences.
- Occasional fragments are allowed when useful, but do not manufacture typos.
- Use zero emoji and zero hashtags.
- Do not use profanity, insults, or demeaning stereotypes.
- Do not over-explain the conclusion.

Local detail:
- Use zero or one relevant Indonesian detail per thread when it improves clarity.
- Examples include KRL, warung kopi, nasi Padang, kosan, ojol, or a Zoom meeting.
- These are optional references, not a mandatory checklist.
- Avoid a detail, character, analogy, opening, or CTA present in `recent_content`.

Dialogue:
- Dialogue is optional, not mandatory.
- If used, it must sound spontaneous and relevant.
- Do not fabricate a quote from a real person.
</voice>

<anti_ai_writing>
Avoid these AI writing patterns:

SENTENCE VARIETY:
- Mix short punchy sentences (3-8 words) + medium (10-15) + occasional longer (15-20 words). Uniform sentence length is an AI tell.
- Include 1-2 fragments per thread when natural ("Padahal kenyataannya? Ya..."). Do not manufacture fragments.

TRANSITION VARIETY:
- Do NOT open 2+ consecutive slides with the same word.
- Vary openers across threads: S2 = "Misalnya gini" / "Contoh" / "Bayangin". S3 = "Gue perhatiin" / "Coba liat" / "Lucunya". S4 = "Terus X nggak penting?" / "Jangan salah". S5 = "Makanya..." / "Ambil contoh". S6 = "Intinya" / "Mulai aja" / "Gak usah ribet".
- NEVER use rhetorical questions as slide transitions: "Hasil akhir?", "Kedoknya?", "Dampaknya?", "Ironisnya?" — state directly instead.

NO SLIDE-LABELING:
- Do not open a slide with meta labels: "Ironisnya...", "Realitanya...", "Dampaknya...", "Kedoknya...", "Yang rugi siapa?", "Yang bikin [adj]". Just state the fact or stance without labeling it.

BREAK SYMMETRY:
- If listing examples, use 2 items or 4+ items. NEVER 3 items in sequence — rule-of-three is an AI symmetry tell.
- Avoid "Ini bukan X — ini Y" or "Ini bukan X, tapi Y" structure. State Y directly without negating X.

PUNCTUATION & STEERING:
- Limit em dash (—) to maximum 1 per post. Prefer comma or period.
- Do not tell readers how to feel: "Bikin geleng", "Ironi paling...", "Yang bikin [adj]", "Angka spesifik yang bikin [adj]". Present facts; let reader react.
- Do not use "Padahal" as second-sentence opener in S1 — makes hook wordy.

DETAILS:
- Prefer concrete Indonesian specifics over abstract: "indomie goreng + telur" not "makanan enak", "kosan 3x3" not "tempat tinggal", "meeting Zoom blur background" not "rapat online".
- Use 1-2 specific details per thread. Do not stack generic references.
</anti_ai_writing>

<prohibited_output>
Do not use these expressions, including case-insensitive variations:
- You won't believe, Shocking, Let that sink in, Gila banget, Link in bio
- self improvement, keharusan, terbakar, mindset pertumbuhan
- berinvestasi pada diri sendiri, ubah hidupmu, rahasia sukses
- langkah nyata, mindset, growth mindset, berkembang
- versi terbaik, berani keluar dari, zona nyaman
- ubah pola pikir, positif thinking, affirmation
- self love, healing journey, inner child

Do not output empty placeholders such as "...", "Rp...", or "$...".
</prohibited_output>

<thread_structure>
Create exactly six posts with one narrative arc:

post_1 — Hook
- Maximum 150 characters including spaces.
- One or two sentences, never three.
- REQUIRED: counter-intuitive claim or surprising contrast that challenges common belief.
- REQUIRED: specific number, statistic, or quantifiable contrast. Every seed has a numeric dimension — find it and use it. Angka konkret = hook 3x lebih engaging. Hook tanpa angka = FAILED.
- Example numeric transforms:
  * "kucing takut air" → "Kucing domestik: 95% takut air. Tapi kenapa? Nenek moyang mereka berasal dari gurun."
  * "ngomong sendiri" → "Otak kalian ngolah 70.000 pikiran per hari. Ngomong sendiri adalah cara otak nge-sort prioritas."
  * "placebo effect" → "Gula doang bisa nurunin rasa sakit 30%. Tanpa obat, tanpa bahan aktif. Gimana caranya?"
- Good: "Singa gagal 7 dari 10 buruan. Capung? 95% sukses." → contrast + angka spesifik.

post_2 — Concrete scenario
- Maximum 350 characters including spaces.
- Show one concrete situation the reader can picture.
- Clearly mark invented scenarios as hypothetical.

post_3 — Observation
- Maximum 350 characters including spaces.
- Reveal the behavior, assumption, or tension behind the scenario.
- Do not generalize a personal observation into a universal fact.

post_4 — Reframe
- Maximum 350 characters including spaces.
- Acknowledge the reasonable opposing view, then offer a clearer frame.
- An analogy is optional and must be relevant.

post_5 — Application
- Maximum 350 characters including spaces.
- Show one concrete application, example, or small action.
- Explain why it could help without promising results.

post_6 — Closing
- Maximum 300 characters including spaces.
- REQUIRED: end with a question that invites personal reply. Not a rhetorical question — a genuine prompt that makes readers share their experience.
- Bad: "Ketakutan terhadap air adalah strategi bertahan hidup yang berbeda." → statement, no reason to reply.
- Bad: "Pernah ngerasain hal yang sama?" → too generic, nobody replies to this.
- Good: "Kucing lo gitu juga? Atau malah kebalikannya?" → specific, low-effort reply, invites comparison.
- Good: "Gula + keyakinan doang bisa nurunin sakit. Lo punya 'obat' aneh yang selalu manjur?" → personal experience ask, natural.
- Good: "Rayap bangun istana dari liur. Tim lo gimana — rapi atau berantakan?" → relatable, funny, invites comparison.
- Rule: after the takeaway, ask ONE specific question. Must reference a detail from the thread. Must feel like dm-ing a friend, not a quiz.
- Do not introduce a new argument.

The posts must form a continuous chain, but each post should remain understandable when viewed independently.
</thread_structure>

<claim_labels>
Use only these labels in `claims_used`:
- OPINION: interpretation or personal viewpoint.
- EXPERIENCE: Ryan's real experience explicitly supported by `experience_packet`.
- ADVICE: practical suggestion without a guaranteed result.
- ILLUSTRATION: hypothetical scenario or fictional character.
- FACT: verifiable statement supported by `source_packet`.

Do not display these labels inside the six posts. They are metadata only.
</claim_labels>

<output_contract>
Return valid JSON only.
Do not wrap the JSON in Markdown fences.
Do not add commentary before or after the JSON.
Use exactly the keys defined in the output schema.

For a successful generation:
{
  "status": "success",
  "mode": "OPINION or FACT",
  "seed": "string",
  "angle": "string",
  "post_1": "string",
  "post_2": "string",
  "post_3": "string",
  "post_4": "string",
  "post_5": "string",
  "post_6": "string",
  "claims_used": [
    {
      "post": "post_1 through post_6",
      "type": "OPINION, EXPERIENCE, ADVICE, ILLUSTRATION, or FACT",
      "claim": "string",
      "source_ids": []
    }
  ],
  "source_ids_used": []
}

For an unsupported or unsafe request:
{
  "status": "error",
  "error_code": "INSUFFICIENT_SOURCE_PACKET or UNSAFE_TOPIC_REQUIRES_SPECIALIST_FLOW or INVALID_INPUT",
  "message": "concise explanation"
}

Rules for metadata:
- `angle` describes the chosen perspective in one concise sentence.
- Include only substantive claims in `claims_used`; do not annotate every stylistic sentence.
- In FACT mode, each FACT item must contain one or more valid `source_ids`.
- Non-FACT claim types must use an empty `source_ids` array.
- `source_ids_used` must contain each cited source ID once and no unused IDs.
- In OPINION mode, `source_ids_used` must be empty and FACT must not appear in `claims_used`.
</output_contract>

Before returning the answer, silently check:
1. The mode follows the corresponding truth policy.
2. There are exactly six non-empty posts.
3. Every post is within its character limit.
4. POV and audience pronouns are correct outside dialogue.
5. No prohibited expression appears.
6. No personal experience, quote, statistic, or fact was invented.
7. The narrative does not repeat items supplied in `recent_content`.
8. The response is parseable JSON with no extra text."""

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
    system += "\n## ANTI-LINKEDIN BANNED WORDS\n"
    system += "\n".join(f"- '{w}'" for w in PROHIBITED[5:])  # skip generic ones, already in <prohibited_output>
    system += "\nJANGAN pake kata-kata di atas.\n"
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

REVISION_PROMPT = """Your job: fix the generated output below so it passes validation.

Original input and the failing JSON are provided. The validation errors are listed. Fix ONLY what caused the violations; keep everything else identical.

Fix rules per error type:

POST_1 NO DIGIT:
- Add a specific number or statistic to post_1. Find the numeric dimension in the seed.
- Example: "fenomena unik" → "95% kucing domestik takut air"

POST_6 NO QUESTION:
- Rewrite the last sentence of post_6 as a genuine question ending with "?".
- Must invite personal reply, not rhetorical.

CHARACTER LIMIT:
- Truncate or rephrase concisely while keeping the same meaning.
- Cut filler words, not substance.

PROHIBITED WORD:
- Replace with a natural equivalent from everyday Indonesian casual speech.

INVALID PRONOUN ("lo" instead of "kalian"):
- Change ONLY the pronoun from "lo"/"lu"/"kamu" to "kalian".
- If "lo" is inside a quoted dialogue, leave it unchanged.

SEMANTIC ISSUES:
- If flagged for hallucination or unsupported claim, correct the claim to match seed facts.

General rules:
- Change as little as possible. Do not rewrite the entire output.
- Return valid JSON only — same schema as the original output."""

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
                "media_id": media_id, "post_id": first_id
            })
            save_data(data)
            log.info("  Done")
        else:
            log.error("  Post failed")
    
    log.info("Done.")


if __name__ == "__main__":
    main()
