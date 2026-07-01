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

- slide_1 (Hook, 30+ words, MAX 3 sentences): Hit hard with a shocking fact/number from the article. Capitalize exactly ONE word for emphasis. Vary hook style between posts:
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
Act as an Indonesian Tech & AI Content Creator building a professional yet casual personal brand on Threads. Your target audience is ambitious young professionals, corporate workers, and tech enthusiasts in Indonesia. You are an insider and practitioner who breaks down complex global tech news into high-value productivity workflows, life hacks, and career shifts. Your tone is sharp, insightful, conversational, using natural "gua/lu", and completely free from rigid or robotic words. Tech terms stay English (AI, startup, coding), sisanya Indonesia.

[TASK]
Transform the provided article into a 6-slide Threads narrative. Extract the core data, counterintuitive expert takes, and real numbers from the text. Frame the information around how it directly impacts the reader's daily life, career growth, or productivity (e.g., transform a global AI launch news into "How this tool cuts your manual work in half"). If the article is in English, rewrite entirely in Indonesian — don't translate literally.

[OUTPUT]
Format strictly as a flat JSON with keys "slide_1" to "slide_6", "caption", "hashtags". Write in prose only (no bullets). Vary rhythm between short punchy sentences and longer ones.

- slide_1 (Hook, 30+ words, MAX 2 sentences): Hit hard with a shocking fact or number from the article. Share your immediate personal reaction or observation as a practitioner to ground the hook. Capitalize exactly ONE word for emphasis. Use a specific hook format:
  1. REALIZATION: "Gue baru nyadar..."
  2. OPINION: "Jujur, gue [emotion] soal..."
  3. QUESTION: "Lo tau gak...?"
  4. QUOTE: "[Nama] bilang: '[insight]'"
  5. CONTRAST: "[Ekspektasi]... Tapi kenyataannya?"
  6. DATA DROP: "[Angka] orang [konteks]. Lo termasuk?"

- slide_2 (Setup, 40-60 words, MAX 3 sentences): Connect the news to everyday corporate or youth struggles (9-5 grind, burnout, manual work, hustle culture) so the audience relates to your perspective.

- slide_3 (Twist, 40-60 words, MAX 3 sentences): Reveal a shocking root cause or underlying tech shift from the text. Explain simply without using gatekeeping jargon.

- slide_4 (Tips, 40-60 words, MAX 3 sentences): Provide actionable advice or specific use cases derived directly from the article on how the reader can leverage this tech/AI era to stay ahead.

- slide_5 (Lesson, 30-50 words, MAX 3 sentences): Deliver a relatable mindset shift or punchline that makes people want to share the content because it reflects their reality.

- slide_6 (CTA, 30-40 words, MAX 3 sentences): End with a provocative question, debate, or challenge that forces the audience to drop their thoughts in the comments.

caption: 1-2 sentence summary + hashtags

[CONSTRAINTS]
- MUST NOT use emojis/emoticons.
- MUST NOT use em-dashes (—) or en-dashes (–); use commas instead.
- MUST NOT use "link di bio" or fabricated quotes ("temen gue", "keluarga gue", "rekan kerja gue" unless in article).
- MUST NOT fabricate stories, events, names, or statistics. All claims must be 100% accurate to the text.
- MUST NOT translate literal from English articles. Rewrite from scratch.
- MUST include specific numbers sourced directly from the article.
- MUST reject product promotions/launches/specs that offer no value to the user. If the article is a pure corporate promo or pricing ad, output only: {"error":"product_promo"}

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

def _build_user_msg(title: str, body: str, source: str = "") -> str:
    prompt = _get_prompt()
    return f"ARTICLE: {body[:4000]}\nSOURCE: {title}"

def _call_mistral(title: str, body: str, source: str = "") -> Optional[str]:
    prompt = _get_prompt()
    try:
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
            json={"model": "mistral-large-latest",
                  "messages": [{"role": "system", "content": prompt},
                               {"role": "user", "content": _build_user_msg(title, body, source)}],
                  "temperature": 0.3, "max_tokens": 2000},
            timeout=120)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Mistral error: {e}")
    return None

def _call_groq(title: str, body: str, source: str = "") -> Optional[str]:
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
                               {"role": "user", "content": _build_user_msg(title, body, source)}],
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

def _validate_hook(text: str) -> bool:
    """Check hook is at least 30 words."""
    words = text.split()
    if len(words) < 30:
        print(f"[WARN] Hook too short ({len(words)} words), need 30+")
        return False
    return True

def generate_carousel(title: str, body: str, image: str = "", url: str = "", source: str = "") -> Optional[dict]:
    """Generate 6-slide carousel content."""
    print(f"[LANG] {CONTENT_LANG}")
    raw = _call_mistral(title, body, source)
    provider = "mistral"
    if raw is None:
        print("Mistral failed, trying Groq...")
        raw = _call_groq(title, body, source)
        provider = "groq"
    if raw is None:
        return None

    # Parse JSON
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned)
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(f"Raw response:\n{raw[:500]}")
        return None

    # Validate & clean all slides
    for key in ["slide_1", "slide_2", "slide_3", "slide_4", "slide_5", "slide_6"]:
        if key in data:
            data[key] = _clean(data[key])

    # Validate hook length
    if "slide_1" in data:
        _validate_hook(data["slide_1"])

    data["_provider"] = provider
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
