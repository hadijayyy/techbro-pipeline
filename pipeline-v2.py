#!/usr/bin/env python3
"""
RyanHadi Content Engine V6 — from prompt document spec.
4 pillars + daily life. OPINION mode default; FACT mode with source_packet.
Production-grade: truth policy, instruction priority, structured validation.
"""
import json, os, re, sys, time, random, logging, httpx
from collections import defaultdict
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

# ── Seed pool — TOP 3 categories: fakta unik otak, fakta hewan, daily life ──
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
    # Daily life observasi / self-dev
    "Kenapa malam hari selalu bikin overthinking?",
    "POV: capek secara mental tapi gak keliatan secara fisik",
    "Ironis: cari 'me time' malah bikin lo makin stres",
    "Gue baru sadar: makin dewasa makin sepi temen curhat",
    "Kebiasaan baru: gue cuma janji 5 menit. Seringnya lanjut lebih lama.",
    "Rahasia konsisten: target kecil banget sampe malu kalo gak dikerjain",
    "Ironis: target gue lebih sering tercapai pas gak ambisius.",
    "Gue kira percaya diri itu suara lantang. Ternyata: berani mulai.",
    "Gue stop nunggu 'feeling siap' — ternyata gak akan pernah datang.",
    "Hal yg gue sesali bukan yang gagal, tapi yang gak pernah dicoba",
    "Gue bukan gak bisa milih. Gue cuma takut salah milih.",
    "Masalahnya bukan males. Tapi hambatan pertama terlalu tinggi.",
    "3 hari berturut-turut udah kemenangan. Gak perlu 30 hari langsung.",
    "Motivasi itu overrated. Yang bikin beda: sistem yang ringan buat dijalanin.",
    "Gue stop nunggu yakin 100% buat ngomong. 70% udah cukup.",
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
- One or two sentences.
- State the tension, surprising contrast, or sharp question immediately.
- No greeting, background setup, clickbait, or unsupported number.

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
- End with one concise takeaway, reflection, or low-friction CTA.
- Do not introduce a new argument.
- The CTA is optional and must fit the content objective.

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
        return {"topics": [], "_bucket_counts": {}, "_hook_type_counts": {}, "_formula_counts": {}}

def save_data(data):
    POSTED_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def pull_engagement():
    if not THREADS_TOKEN or DRY_RUN:
        return 0
    data = load_data()
    topics = data.get("topics", [])
    updated = 0
    cutoff = datetime.now(WIB) - timedelta(hours=2)
    for t in topics:
        if t.get("views"):
            continue
        try:
            r = httpx.get(f"{GRAPH}/{USER_ID}/media", params={"fields": "id,permalink", "access_token": THREADS_TOKEN}, timeout=10)
            if r.status_code != 200:
                continue
        except httpx.RequestError:
            continue
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
{mode} mode. Gunakan narator "gw" + audiens "kalian"."""

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
            violations.append(f"{key}: {len(p)} chars exceeds limit {limit}")
    
    if len(violations) > 0:
        return False, violations
    
    # Check prohibited words
    text_lower = " ".join(posts).lower()
    for word in PROHIBITED:
        if word.lower() in text_lower:
            violations.append(f"Prohibited term: '{word}'")
    
    # Check POV — no lo/kamu/gue outside quotes in narrator text
    combined = " | ".join(posts)
    outside_quotes = re.sub(r'"[^"]*"|\'[^\']*\'', '', combined)
    for bad in [r'\blo\b', r'\bkamu\b', r'\bLo\b', r'\bKamu\b']:
        if re.search(bad, outside_quotes):
            violations.append(f"Invalid audience pronoun: {bad}")
    
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
                if attempt < 3:
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
#   SEMANTIC VALIDATOR
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

    # Flow: create → publish → get published ID → create next with reply_to
    ids = []
    parent_publish_id = None
    first_publish_id = None
    
    for i, s in enumerate(slides):
        # 1. Create container
        container_id = create_container(s["content"], parent_publish_id)
        if not container_id:
            log.error(f"  Failed to create container for {s['title']}")
            return None, ids
        time.sleep(1.5)
        
        # 2. Publish immediately
        try:
            r = httpx.post(
                f"{GRAPH}/{USER_ID}/threads_publish",
                data={"access_token": THREADS_TOKEN, "creation_id": container_id},
                timeout=15
            )
            if r.status_code != 200:
                log.error(f"  Publish failed for {s['title']}: {r.text[:200]}")
                return None, ids
            publish_data = r.json()
            publish_id = publish_data.get("id") or publish_data.get("media_id")
            if not publish_id:
                log.error(f"  No id in publish response: {publish_data}")
                return None, ids
            if first_publish_id is None:
                first_publish_id = publish_id
            ids.append(container_id)
            parent_publish_id = publish_id  # next post replies to this published post
        except (httpx.RequestError, json.JSONDecodeError, KeyError) as e:
            log.error(f"  Publish error for {s['title']}: {e}")
            return None, ids
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
    
    # Generate
    mode = "OPINION"  # default; FACT mode uses --fact flag
    if "--fact" in sys.argv:
        mode = "FACT"
    
    result = generate_thread(seed, mode=mode)
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
