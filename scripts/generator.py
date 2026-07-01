#!/usr/bin/env python3
"""generator.py — Generate 6-slide carousel via Mistral (primary) / Groq (fallback).
Switch language with CONTENT_LANG=en|id in .env
"""
import httpx
import json
import re
from typing import Optional

import os

GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
MISTRAL_KEY = os.environ.get("MISTRAL_API_KEY", "")
CONTENT_LANG = os.environ.get("CONTENT_LANG", "en").lower()

# ─── Prompts ──────────────────────────────────────────────────────

PROMPT_EN = """[ROLE]
Act as "Bro", a 27-year-old Tech content creator on Threads targeting ambitious young professionals globally. You are a conversational storyteller, not a news anchor. You speak directly (I/you) in casual English, transforming tech/AI/career news into relatable life lessons and actionable advice.

[TASK]
Transform the provided article into a 6-slide Threads narrative. Extract the most counterintuitive takes, actionable tips, and real numbers from the text. Frame the information around how it directly impacts the reader's daily life, career, or productivity (e.g., turn "CEO resigns" into "Signs you should quit your job").

[OUTPUT]
Format strictly as a flat JSON with keys "slide_1" to "slide_6", "caption", "hashtags". Write in prose only (no bullets). Vary rhythm between short punchy sentences and longer ones.

- slide_1 (Hook, under 30 words, MAX 2 sentences): Hit hard with a shocking fact/number from the article. Capitalize exactly ONE word for emphasis. Vary hook style between posts:
  1. REALIZATION: "I just realized..."
  2. OPINION: "Honestly, I'm [emotion] about..."
  3. QUESTION: "Did you know...?"
  4. QUOTE: "[Name] said: '[insight]'"
  5. CONTRAST: "[Expectation]... But reality?"
  6. DATA DROP: "[Number] people [context]. Are you one of them?"

- slide_2 (Setup, 40-60 words, MAX 3 sentences): Bridge to the real problem using everyday analogies (9-5 grind, broke college student, hustle culture). Reader should think: "Yeah, I deal with this too"

- slide_3 (Twist, 40-60 words, MAX 3 sentences): Reveal a shocking root cause or fact. Explain simply without jargon.

- slide_4 (Tips, 40-60 words, MAX 3 sentences): Provide actionable advice derived directly from the article.

- slide_5 (Lesson, 30-50 words, MAX 3 sentences): Deliver a relatable mindset shift or punchline. One sentence that makes people share.

- slide_6 (CTA, 30-40 words, MAX 3 sentences): End with one of these to force comments:
  1. PROVOCATIVE: "Is [X]? Or [Y]?"
  2. PERSONAL: "Have you ever [action]? Drop it in the comments."
  3. DEBATE: "Hot take: [opinion]. Agree or disagree?"
  4. RANKING: "What matters more: [A] or [B]?"
  5. CHALLENGE: "Try [action] for a week. Let me know how it goes."

caption: 1-2 sentence summary + hashtags

[CONSTRAINTS]
- MUST NOT use emojis/emoticons.
- MUST NOT use em-dashes (—) or en-dashes (–); use commas instead.
- MUST NOT use "link in bio" or fabricated quotes ("my friend/family/coworker said" unless in article).
- MUST NOT fabricate stories, events, names, or statistics.
- MUST include specific numbers sourced directly from the article.
- MUST reject product promotions. If product launch/specs/pricing, output: {"error":"product_promo"}

Output strict JSON, no markdown fences:
{"slide_1":"","slide_2":"","slide_3":"","slide_4":"","slide_5":"","slide_6":"","caption":"","hashtags":""}
"""

PROMPT_ID = """[ROLE]
Lo adalah "Bro", content creator tech Indonesia umur 27 tahun di Threads. Target lo: anak muda ambitious, pekerja kantoran, dan tech enthusiast Indonesia. Lo bukan reporter berita — lo adalah storyteller yang ngebongkar berita tech/AI jadi cerita yang relate sama kehidupan sehari-hari. Bahasa lo: natural, conversational, pake "gue/lo", campuran Indonesia-Inggris (tech terms tetap Inggris: AI, startup, coding, agent). Gaya lo: kayak lagi ngobrol sama temen di kopi, bukan kayak baca koran.

[TASK]
Ubah artikel ini jadi 6-slide Threads narrative yang feels seperti cerita pribadi, bukan berita. Ambil data paling mengejutkan, insight yang counterintuitive, dan angka real dari artikel. Frame semuanya dari sudut pandang: "Gimana ini ngaruh ke hidup lo sehari-hari, karir, atau produktivitas?" Contoh: berita "CEO resign" → jadi "Tanda lo harus resign dari kantor lo". Jangan translate literal — rewrite dari nol dengan konteks Indonesia.

[STORYTELLING RULES]
- Setiap slide harus feel kayak chapter dalam cerita, bukan bullet point
- Gunakan analogi dari kehidupan Indonesia: macet Jakarta, cicilan motor, kopi sachet, WFH, side hustle
- Pace: slide 1-2 = tarik masuk, slide 3 = plot twist, slide 4 = solusi, slide 5 = wisdom, slide 6 = ajak diskusi
- Setiap slide harus ada minimal satu kalimat pendek yang punchy (3-7 kata) untuk emphasis
- Jangan pernah terdengar seperti mesin — terdengar seperti manusia yang lagi sharing pengalaman

[OUTPUT]
Format strictly as a flat JSON with keys "slide_1" to "slide_6", "caption", "hashtags". Tulis dalam prose (no bullets). Variasi ritme antara kalimat pendek yang nendang dan kalimat panjang yang mengalir.

- slide_1 (Hook, under 30 words, MAX 2 sentences): Langsung hantam dengan fakta/angka paling shocking dari artikel. Capitalize SATU kata untuk emphasis. Pilih salah satu format hook ini (jangan ulang yang sama):
  1. REALIZATION: "Gue baru sadar..."
  2. OPINION: "Jujur, gue [emosi] soal..."
  3. QUESTION: "Lo tau gak...?"
  4. SCENARIO: "Bayangin lo lagi [situasi]..."
  5. CONTRAST: "Katanya [ekspektasi]... Tapi kenyataannya?"
  6. DATA: "[Angka] orang [konteks]. Lo termasuk?"

- slide_2 (Setup, 40-60 words, MAX 3 sentences): Hubungin berita ini ke struggle sehari-hari orang Indonesia (9-5 grind, macet, cicilan, WFH culture, side hustle). Pembaca harus mikir: "Anjir, ini gue banget"

- slide_3 (Twist, 40-60 words, MAX 3 sentences): Bongkar root cause atau fakta yang bikin kaget. Jelasin pake bahasa sehari-hari tanpa jargon yang gatekeeping.

- slide_4 (Tips, 40-60 words, MAX 3 sentences): Kasih advice actionable yang bisa langsung lo praktekin. Spesifik, bukan generik.

- slide_5 (Lesson, 30-50 words, MAX 3 sentences): Deliver mindset shift atau punchline yang bikin orang mau share karena mereka relate.

- slide_6 (CTA, 30-40 words, MAX 3 sentences): Akhiri dengan pertanyaan yang provokatif, ajak debate, atau challenge yang bikin audience mau comment.

caption: 1-2 kalimat summary + hashtags

[STRICT RULES]
- JANGAN pake emoji/emoticon sama sekali
- JANGAN pake em-dash (—) atau en-dash (–); ganti koma
- JANGAN pake "link di bio" atau bikin quote palsu ("temen gue", "keluarga gue" kecuali dari artikel)
- JANGAN fabricate cerita, event, nama, atau statistik. Semua harus 100% akurat dari artikel
- JANGAN translate literal dari artikel Inggris. Rewrite dari nol
- WAJIB include angka spesifik dari artikel
- WAJIB reject product promotions. Kalau artikel pure promo/pricing, output: {"error":"product_promo"}

Output strict JSON, no markdown fences:
{"slide_1":"","slide_2":"","slide_3":"","slide_4":"","slide_5":"","slide_6":"","caption":"","hashtags":""}
"""

def _get_prompt() -> str:
    return PROMPT_ID if CONTENT_LANG == "id" else PROMPT_EN

# ─── Banned phrases (per language) ────────────────────────────────

BANNED_EN = [
    r'\bmy friend said\b', r'\bmy mom said\b', r'\bmy dad said\b',
    r'\bmy family said\b', r'\bmy coworker said\b', r'\bmy buddy said\b',
    r'\blink in bio\b', r'\bmy friend told me\b',
    r'\baccording to my\b', r'\bmy parents said\b',
    r'\bliterally\b', r'\blike\b(?=\s+literally)',
    r'\bepic\b', r'\binsane\b', r'\bcrazy\b(?!\s+thing)',
    r'\bthat\'s wild\b', r'\bno way\b',
]

BANNED_ID = [
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

BANNED_COMMON = [
    # Emojis (catch-all)
    r'[\U00010000-\U0010ffff\u2600-\u27bf\u200d\u20e3\u2702-\u27b0]+',
]

def _get_banned() -> list:
    lang_banned = BANNED_ID if CONTENT_LANG == "id" else BANNED_EN
    return lang_banned + BANNED_COMMON

# ─── API calls ────────────────────────────────────────────────────

# ─── Prompt rotation: different angles per article type ────────

ANGLES = {
    "news": [
        "Write as BREAKING NEWS — first paragraph is the most shocking fact.",
        "Write as EXCLUSIVE INSIDER — what most people missed about this story.",
        "Write as INDUSTRY ANALYSIS — why this matters for the next 5 years.",
    ],
    "product": [
        "Write as FIRST IMPRESSIONS — what it feels like to use this.",
        "Write as COMPARISON — how this stacks up against the competition.",
        "Write as EARLY ADOPTER — why you should care before everyone else does.",
    ],
    "impact": [
        "Write as CAREER ADVISORY — how this affects your job/skills.",
        "Write as INVESTOR THESIS — what this means for money/markets.",
        "Write as FUTURE PREDICTION — where this leads in 2-3 years.",
    ],
    "controversy": [
        "Write as DEVIL'S ADVOCATE — the contrarian take nobody's saying.",
        "Write as ETHICAL DEBATE — both sides of the argument.",
        "Write as PATTERN RECOGNITION — what history tells us about this.",
    ],
}

def _classify_article(title: str, body: str) -> str:
    """Classify article type for prompt rotation."""
    text = (title + " " + body[:500]).lower()
    
    product_words = {"launch", "release", "introduces", "unveils", "new feature", "update", "beta", "app", "tool", "product"}
    impact_words = {"layoff", "jobs", "career", "replace", "automation", "workforce", "salary", "hiring", "funding", "valuation", "ipo"}
    controversy_words = {"banned", "lawsuit", "regulation", "controversy", "ethical", "bias", "safety", "risk", "danger", "control", "censorship"}
    
    if any(w in text for w in controversy_words):
        return "controversy"
    if any(w in text for w in product_words):
        return "product"
    if any(w in text for w in impact_words):
        return "impact"
    return "news"

def _get_angle(article_type: str) -> str:
    """Get a rotating angle instruction for the article type."""
    import random
    angles = ANGLES.get(article_type, ANGLES["news"])
    return random.choice(angles)

def _build_user_msg(title: str, body: str, source: str = "", hook_instruction: str = "") -> str:
    article_type = _classify_article(title, body)
    angle = _get_angle(article_type)
    hook_part = f"\nHOOK STYLE: {hook_instruction}" if hook_instruction else ""
    return f"ANGLE: {angle}{hook_part}\n\nARTICLE: {body[:4000]}\nSOURCE: {title}"

def _call_mistral(title: str, body: str, source: str = "", hook_instruction: str = "") -> Optional[str]:
    prompt = _get_prompt()
    try:
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
            json={"model": "mistral-large-latest",
                  "messages": [{"role": "system", "content": prompt},
                               {"role": "user", "content": _build_user_msg(title, body, source, hook_instruction)}],
                  "temperature": 0.3, "max_tokens": 2000},
            timeout=120)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Mistral error: {e}")
    return None

def _call_groq(title: str, body: str, source: str = "", hook_instruction: str = "") -> Optional[str]:
    if not GROQ_KEY:
        print("Groq skipped (no GROQ_API_KEY)")
        return None
    prompt = _get_prompt()
    try:
        r = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "system", "content": prompt},
                               {"role": "user", "content": _build_user_msg(title, body, source, hook_instruction)}],
                  "temperature": 0.3, "max_tokens": 2000},
            timeout=120)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Groq error: {e}")
    return None

# ─── Post-processing ─────────────────────────────────────────────

def _clean(text: str) -> str:
    """Remove banned phrases, fix grammar issues, enforce whitespace."""
    out = text
    banned = _get_banned()
    for pat in banned:
        out = re.sub(pat, '', out, flags=re.I)

    # Remove em-dashes, en-dashes
    out = out.replace('—', ', ').replace('–', ', ')

    # Remove "link in bio" / "link di bio" variants
    out = re.sub(r'[\(\[]?link\s+(in|di)\s+bio[\)\]]?', '', out, flags=re.I)

    # Fix orphan punctuation: lines starting with , ; : . ! ?
    out = re.sub(r'(?m)^\s*[,;:.\!?]+\s*', '', out)

    # Enforce: one sentence per line, separated by double newline
    out = re.sub(r'\n+', ' ', out)
    out = re.sub(r' {2,}', ' ', out).strip()
    sentences = re.split(r'(?<=[.!?])\s+', out)
    out = '\n\n'.join(s.strip() for s in sentences if s.strip())

    return out

def _validate_hook(text: str) -> tuple[bool, list[str]]:
    """Check hook quality. Returns (valid, list of issues)."""
    issues = []
    words = text.split()
    
    # 1. Length: under 30 words
    if len(words) > 30:
        issues.append(f"too long ({len(words)} words, need under 30)")
    if len(words) < 10:
        issues.append(f"too short ({len(words)} words, need 10+)")
    
    # 2. Check for number/impact (increases engagement)
    has_number = bool(re.search(r'\d+', text))
    if not has_number:
        issues.append("no number (numbers increase engagement)")
    
    # 3. Check for curiosity/emotional triggers
    curiosity_words = {
        "secret", "hidden", "shocking", "surprising", "unexpected", "never",
        "actually", "real", "truth", "mistake", "wrong", "fail", "success",
        "breakthrough", "discovered", "revealed", "exposed", "leaked",
        "just", "new", "first", "only", "biggest", "most", "worst", "best"
    }
    text_lower = text.lower()
    has_curiosity = any(w in text_lower for w in curiosity_words)
    if not has_curiosity:
        issues.append("no curiosity trigger (try: secret, shocking, just, new, first)")
    
    # 4. Check for question or exclamation (engagement hook)
    has_hook_punctuation = text.rstrip().endswith(('?', '!'))
    
    # 5. Check for personal angle ("you", "your", "I", "we")
    personal_words = {"you", "your", "i", "we", "our", "my"}
    has_personal = any(w in text_lower.split() for w in personal_words)
    
    # Score the hook
    score = 0
    if not issues: score += 2
    if has_number: score += 1
    if has_curiosity: score += 1
    if has_hook_punctuation: score += 1
    if has_personal: score += 1
    
    # Hook is valid if score >= 3 and length in range
    valid = score >= 3 and 10 <= len(words) <= 30
    
    if not valid and not issues:
        issues.append(f"hook score {score}/6, need 3+")
    
    return valid, issues

def _score_hook(text: str) -> int:
    """Score a hook 0-6. Higher = better engagement."""
    score = 0
    words = text.split()
    if len(words) <= 30 and len(words) >= 10: score += 1  # sweet spot: 10-30 words
    if bool(re.search(r'\d+', text)): score += 1
    curiosity = {'secret', 'shocking', 'surprising', 'unexpected', 'never',
                 'actually', 'real', 'truth', 'mistake', 'breakthrough',
                 'just', 'new', 'first', 'only', 'biggest', 'most', 'worst', 'best'}
    if any(w in text.lower() for w in curiosity): score += 1
    if text.rstrip().endswith(('?', '!')): score += 1
    if any(w in text.lower().split() for w in {'you', 'your', 'i', 'we', 'our', 'my'}): score += 1
    if len(set(re.findall(r'[A-Z]{2,}', text))) > 0: score += 1  # emphasis words
    return score

def _generate_variant(title: str, body: str, source: str, provider: str, hook_instruction: str = "") -> Optional[dict]:
    """Generate one carousel variant. Returns parsed dict or None."""
    if provider == "mistral":
        raw = _call_mistral(title, body, source, hook_instruction)
    else:
        raw = _call_groq(title, body, source, hook_instruction)
    if raw is None:
        return None
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned)
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
        if key in data:
            data[key] = _clean(data[key])
    return data

def _get_recent_hook_patterns(limit: int = 5) -> list[str]:
    """Get hook patterns from last N posts to enforce variety."""
    try:
        from db import get_db
        conn = get_db()
        rows = conn.execute(
            "SELECT slide_hook FROM posts WHERE status='posted' AND slide_hook IS NOT NULL ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        patterns = []
        for r in rows:
            h = (r['slide_hook'] or '').strip().lower()
            if h.startswith("gau baru sadar") or h.startswith("i just realized"):
                patterns.append("REALIZATION")
            elif h.startswith("jujur") or h.startswith("honestly"):
                patterns.append("OPINION")
            elif h.startswith("lo tau") or h.startswith("did you know"):
                patterns.append("QUESTION")
            elif h.startswith("hot take"):
                patterns.append("DEBATE")
            elif h.startswith("bayangin") or h.startswith("imagine"):
                patterns.append("SCENARIO")
            elif h.startswith("data:") or h.startswith("[angka]"):
                patterns.append("DATA_DROP")
            else:
                patterns.append("OTHER")
        return patterns
    except Exception:
        return []

def _pick_hook_instruction(recent_patterns: list[str]) -> str:
    """Pick a hook instruction that avoids recent patterns."""
    import random
    all_hooks = [
        ("REALIZATION", "Start with: 'Gue baru sadar...' — shock them with a fact."),
        ("OPINION", "Start with: 'Jujur, gue [emosi] soal...' — honest reaction."),
        ("QUESTION", "Start with: 'Lo tau gak...?' — hit them with a question."),
        ("SCENARIO", "Start with: 'Bayangin lo lagi...' — paint a relatable scene."),
        ("DATA_DROP", "Start with a NUMBER and context — 'X orang [konteks]. Lo termasuk?'"),
        ("CONTRAST", "Start with expectation vs reality — 'Katanya [X]... Tapi kenyataannya?'"),
    ]
    # Filter out recently used patterns
    available = [(name, instr) for name, instr in all_hooks if name not in recent_patterns[-3:]]
    if not available:
        available = all_hooks  # fallback if all used
    chosen = random.choice(available)
    return chosen[1]

def generate_carousel(title: str, body: str, image: str = "", url: str = "", source: str = "") -> Optional[dict]:
    """Generate 6-slide carousel with A/B testing (2 variants, pick best hook)."""
    print(f"[LANG] {CONTENT_LANG}")

    # Hook variety: get recent patterns and pick unused one
    recent = _get_recent_hook_patterns(5)
    hook_instr = _pick_hook_instruction(recent)
    print(f"[HOOK] Recent patterns: {recent}")
    print(f"[HOOK] Chosen instruction: {hook_instr[:60]}...")

    # Determine primary provider
    primary = "mistral"
    fallback = "groq"

    # A/B: generate 2 variants
    variants = []
    for i, prov in enumerate([primary, primary], 1):  # both from same provider
        v = _generate_variant(title, body, source, prov, hook_instruction=hook_instr)
        if v and "slide_1" in v:
            v["_provider"] = prov
            hook_score = _score_hook(v["slide_1"])
            variants.append((v, hook_score))
            print(f"  [A/B] Variant {i}: hook score {hook_score}/6 via {prov}")

    # If primary fails both times, try fallback
    if len(variants) < 2:
        v = _generate_variant(title, body, source, fallback, hook_instruction=hook_instr)
        if v and "slide_1" in v:
            v["_provider"] = fallback
            hook_score = _score_hook(v["slide_1"])
            variants.append((v, hook_score))
            print(f"  [A/B] Fallback variant: hook score {hook_score}/6 via {fallback}")

    if not variants:
        return None

    # Pick best hook score
    variants.sort(key=lambda x: x[1], reverse=True)
    data, best_score = variants[0]
    print(f"  [A/B] Winner: hook score {best_score}/6 ({len(variants)} variants)")

    # Validate winning hook
    if "slide_1" in data:
        valid, issues = _validate_hook(data["slide_1"])
        if not valid:
            print(f"[HOOK] Issues: {', '.join(issues)}")
        else:
            print(f"[HOOK] Valid (score: {best_score}/6)")

    data["_provider"] = data.get("_provider", primary)
    data["_lang"] = CONTENT_LANG
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
