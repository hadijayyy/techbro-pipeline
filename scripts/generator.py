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
Kamu content strategist Threads AI/tech buat Indonesia, usia 20-35. Praktisi ngobrol, bukan dosen. Kredibel tapi gak sok pinter.

[CONTEXT]
Audiens awam-menengah soal AI, scroll cepat, atensi pendek. Butuh insight yang connect ke hidup/kerjaan mereka, bukan jargon berat.

[ARTICLE]
Judul: {title}
Isi:
{body}
Sumber: {source}

[STORY SELECTION]
Kalau article punya >1 cerita/kasus, pilih SATU yang terkuat: paling spesifik (angka/detail konkret) > paling relevan ke hidup/kerjaan audiens > paling punya hook (kontradiksi/kejutan) > paling fresh (tiebreaker). Cerita lain boleh disebut max 1 kalimat sebagai konteks, jangan dikembangin. 1 post = 1 narasi.

[TASK]
6 slides:
- Slide 1 (Hook): Max 2 kalimat, <30 kata, capitalize 1 kata. Variasikan struktur: kontradiksi / angka kejutan / klaim berani / retorik tajam / before-after — jangan selalu pola sama.
- Slide 2-4 (Isi): Max 3 kalimat/slide, total <40 kata/slide. 1 ide per slide dari artikel.
- Slide 5 (Opini lo): Pilih SATU sudut: implikasi konkret ke audiens / blind spot dari klaim artikel / analogi baru. Hindari opini generik.
- Slide 6 (Closing+CTA): Kesimpulan + ajak komentar/save/follow. 1 pertanyaan singkat.

{hook_instruction}

[OUTPUT FORMAT]
{{"slide_1":"", "slide_2":"", "slide_3":"", "slide_4":"", "slide_5":"", "slide_6":"", "caption":"", "hashtags":""}}
Caption: 1 kalimat ringkas & provokatif. Zero emoji. Maks 1 hashtag.

[REAKSI NATURAL] (opsional, max 1x/post, jangan ulang dari post sebelumnya)
gila sih · anjir · seriusan? · waduh · lah · busett · kok bisa

[RULES (WAJIB)]
1. Jangan pakai em dash (—); ganti koma/titik/kalimat baru.
2. Campur Indo-Inggris; tech terms tetap English.
3. Hindari template AI ("di era digital ini", dll).
4. Tiap slide berdiri sendiri tapi WAJIB nyambung linear: slide 2-4 harus langsung nge-build dari klaim/pertanyaan di hook slide 1 (bukan fakta lepas), slide 5 harus respon ke insight slide 2-4 (bukan opini baru yang gak nyambung), slide 6 nutup loop balik ke hook. Kalau slide 3 dihapus, slide 4 harus tetep masuk akal — itu tandanya udah nyambung, bukan cuma numpuk fakta. Fokus 1 insight besar.
5. Fakta dari artikel. Angka WAJIB min 1/post KALAU ada di artikel; kalau artikel kualitatif, pakai fakta spesifik lain (jangan ngarang angka).
6. Zero "link di bio" / quote palsu.
7. Pure product promo → {{"error":"product_promo"}}
8. Max 1 statistik/slide, max 1 kalimat tanya/post (kecuali hook/closing).

[BANNED PATTERNS]
"Bayangin lo bisa..." · "Ini bukan cuma..." · "Gue inget pas kuliah..." · "Jangan cuma X, coba Y" · "Dalam dunia yang terus berubah" · "Di era digital ini" · "Game-changer" · "Geleng-geleng" · "Garuk kepala" · "Kayak dari masa depan" · "Kebayang gak" · "Yang bener aja" · "Gokil" · "Mantap jiwa" · "Sultan" · "Auto" · "Skuy" · "Cuy"

[EXAMPLE]
Topik: AI bikin orang malas mikir?

1: AI bukan bikin kamu bodoh. Cara kamu pakai AI yang nentuin itu.
2: Ada 2 tipe orang pakai ChatGPT. Tipe pertama copy paste jawaban langsung. Tipe kedua pakai AI buat mikir lebih dalam.
3: Riset MIT nunjukkin orang yang terlalu bergantung ke AI buat nulis, critical thinking-nya menurun dalam beberapa bulan. Tapi ini bukan salah AI-nya.
4: Ini soal habit. Sama kayak GPS bikin orang lupa jalan, bukan berarti GPS-nya jahat.
5: Cara sehat pakai AI: mikir dulu sebelum nanya. Bikin draft sendiri, baru minta AI perbaiki atau kasih perspektif baru.
6: AI itu tools, bukan tukang gantiin otak kamu. Kamu tim yang mana? Share di komen ya.
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
    r'\bkebayang gak\b', r'\byang bener aja\b',
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

# Reaksi natural — allowed but MAX 1x per post (tracked in _check_reaksi_count)
REAKSI_NATURAL = [
    r'\bgila sih\b', r'\bgila banget\b', r'\bgila kan\b',
    r'\banjir\b', r'\bseriusan\b', r'\bgimana ceritanya\b',
    r'\bwaduh\b',
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
    """Remove banned phrases, fix grammar issues, enforce whitespace.
    Also enforces reaksi natural MAX 1x per slide."""
    out = text
    banned = _get_banned()
    for pat in banned:
        out = re.sub(pat, '', out, flags=re.I)

    # Reaksi natural: keep first occurrence per root word, remove rest
    reaksi_roots = {
        'gila': [r'\bgila sih\b', r'\bgila banget\b', r'\bgila kan\b'],
        'anjir': [r'\banjir\b'],
        'seriusan': [r'\bseriusan\b'],
        'gimana': [r'\bgimana ceritanya\b'],
        'waduh': [r'\bwaduh\b'],
    }
    for root, patterns in reaksi_roots.items():
        all_matches = []
        for pat in patterns:
            for m in re.finditer(pat, out, flags=re.I):
                all_matches.append(m)
        # Sort by position, keep first, remove rest
        all_matches.sort(key=lambda m: m.start())
        for m in reversed(all_matches[1:]):
            out = out[:m.start()] + out[m.end():]

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
    """Score a hook 0-10. v2 weights: length=2, number=2, rest=1."""
    score = 0
    words = text.split()
    text_lower = text.lower()
    word_count = len(words)

    # 1. Length sweet spot (10-20 words) = 2 pts
    if 10 <= word_count <= 20:
        score += 2

    # 2. Has specific number = 2 pts
    if re.search(r'\d+', text):
        score += 2

    # 3. Curiosity/emotional trigger
    curiosity = {'secret', 'shocking', 'surprising', 'unexpected', 'never',
                 'actually', 'real', 'truth', 'mistake', 'breakthrough',
                 'just', 'new', 'first', 'only', 'biggest', 'most', 'worst', 'best',
                 'gila', 'seriusan', 'anjir', 'parah', 'gilanya', 'ternyata'}
    if any(w in text_lower for w in curiosity):
        score += 1

    # 4. Engagement punctuation (? or !)
    if text.rstrip().endswith(('?', '!')):
        score += 1

    # 5. Personal pronouns (ID: gue/lo/kita, EN: you/your/i/we)
    personal = {'you', 'your', 'i', 'we', 'our', 'my', 'gue', 'lo', 'kita', 'lu'}
    if any(w in text_lower.split() for w in personal):
        score += 1

    # 6. CAPS emphasis (2+ uppercase chars in a row)
    if re.search(r'[A-Z]{2,}', text):
        score += 1

    # 7. Contrast/tension words
    tension = {'but', 'however', 'yet', 'actually', 'turns', 'except', 'wait',
               'tapi', 'ternyata', 'padahal', 'eh', 'taunya'}
    if any(w in text_lower.split() for w in tension):
        score += 1

    # 8. Specificity (proper nouns or tech terms)
    specificity = {'ai', 'openai', 'anthropic', 'claude', 'gpt', 'google', 'nvidia',
                   'microsoft', 'meta', 'apple', 'tesla', 'spacex', 'github', 'api',
                   'llm', 'startup', 'python', 'javascript', 'blockchain'}
    if any(w in text_lower.split() for w in specificity):
        score += 1

    # 9. Strong opening (doesn't start with weak words)
    weak_openers = {'the', 'a', 'an', 'in', 'it', 'this', 'that', 'there', 'ini', 'itu', 'ada'}
    first_word = words[0].lower().strip('.,!?') if words else ''
    if first_word and first_word not in weak_openers:
        score += 1

    return score


def _rewrite_hook(hook: str, article_title: str, body: str, score: int) -> tuple[str, int]:
    """Auto-rewrite hook if score < 7. Returns (new_hook, new_score)."""
    if score >= 7:
        return hook, score

    # Build feedback on what's missing
    words = hook.split()
    text_lower = hook.lower()
    missing = []

    if not (10 <= len(words) <= 25):
        missing.append("keep it between 10-25 words")
    if not re.search(r'\d+', hook):
        # Try to extract number from article
        nums = re.findall(r'\d[\d,.]*\+?\s*(?:%|percent|million|billion|thousand|gen|cell|researcher|scientist|company)?', body[:500].lower())
        if nums:
            missing.append(f"add a number from the article (like {nums[0].strip()})")
        else:
            missing.append("add a specific number")
    curiosity = {'shocking', 'surprising', 'never', 'actually', 'first', 'new', 'just', 'gila', 'seriusan', 'ternyata'}
    if not any(w in text_lower for w in curiosity):
        missing.append("add a curiosity word (shocking, baru, ternyata, gila)")
    if not hook.rstrip().endswith(('?', '!')):
        missing.append("end with ? or !")
    if not re.search(r'[A-Z]{2,}', hook):
        missing.append("CAPITALIZE one important word for emphasis")

    if not missing:
        return hook, score

    prompt_fix = f"""Rewrite this hook to score higher. Current issues: {', '.join(missing)}.

Original hook: {hook}
Article title: {article_title}

Rules:
- Under 25 words, MAX 2 sentences
- Must include at least one specific number
- Capitalize ONE key word
- End with ? or ! if it's a question/exclamation
- Mix Indonesian-English naturally
- Sound like a real person texting, not an AI

Return ONLY the rewritten hook text, nothing else."""

    try:
        import httpx, os
        key = os.getenv("MISTRAL_API_KEY", "")
        if not key:
            return hook, score
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "mistral-large-latest",
                  "messages": [{"role": "user", "content": prompt_fix}],
                  "temperature": 0.4, "max_tokens": 100},
            timeout=30)
        if r.status_code == 200:
            new_hook = r.json()["choices"][0]["message"]["content"].strip().strip('"')
            new_score = _score_hook(new_hook)
            if new_score > score:
                print(f"  [REWRITE] Hook {score}/10 → {new_score}/10: {new_hook[:60]}...")
                return new_hook, new_score
    except Exception as e:
        print(f"  [REWRITE] Error: {e}")

    return hook, score

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
            print(f"  [A/B] Variant {i}: hook score {hook_score}/10 via {prov}")

    # If primary fails both times, try fallback
    if len(variants) < 2:
        v = _generate_variant(title, body, source, fallback, hook_instruction=hook_instr)
        if v and "slide_1" in v:
            v["_provider"] = fallback
            hook_score = _score_hook(v["slide_1"])
            variants.append((v, hook_score))
            print(f"  [A/B] Fallback variant: hook score {hook_score}/10 via {fallback}")

    if not variants:
        return None

    # Pick best hook score
    variants.sort(key=lambda x: x[1], reverse=True)
    data, best_score = variants[0]
    print(f"  [A/B] Winner: hook score {best_score}/10 ({len(variants)} variants)")

    # Auto-rewrite hook if score < 7
    if "slide_1" in data and best_score < 7:
        new_hook, new_score = _rewrite_hook(data["slide_1"], title, body, best_score)
        if new_score > best_score:
            data["slide_1"] = new_hook
            best_score = new_score

    # Validate winning hook
    if "slide_1" in data:
        valid, issues = _validate_hook(data["slide_1"])
        if not valid:
            print(f"[HOOK] Issues: {', '.join(issues)}")
        else:
            print(f"[HOOK] Valid (score: {best_score}/10)")

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
