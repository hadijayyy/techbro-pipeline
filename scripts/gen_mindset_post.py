#!/usr/bin/env python3
"""Generate short mindset text post and post to Threads."""
import sys, os, json, random
from pathlib import Path

# Load env
for line in Path(__file__).parent.parent.joinpath('.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(Path(__file__).parent))
import httpx
from generator import MISTRAL_KEY, _clean
from poster import _post_container

FIGURES = [
    "Cristiano Ronaldo", "Elon Musk", "David Goggins",
    "Kobe Bryant", "Mark Zuckerberg", "Jeff Bezos",
    "Lionel Messi", "Kylian Mbappe", "LeBron James",
    "Jokowi", "Basuki Tjahaja Purnama (Ahok)", "Putri Tanjung",
    "Nadiem Makarim", "William Tanuwijaya", "Susanto Purnomo (Garena)",
]

prompt_template = '''Lu "Ryan" — creator Threads, 1% Better style. Blunt, realistis, gak toxic positivity.

Buat text post pendek (2 kalimat MAX, pisah dengan whitespace) yang:
- Hook provokatif (pertanyaan challenge asumsi)
- Sebut nama: {figure}
- Fakta surprising soal origin story mereka
- Mindset reframe dari cerita itu
- Bahasa "lu/gw", natural Indonesia
- TANPA emoji
- Max 35 kata TOTAL (termasuk nama)

Contoh winning (JANGAN copy, bikin baru):
"Kamu masih nunggu bakat muncul sendiri?
Mbappe mulai dari kamar tidur penuh poster Ronaldo."

PASTIKAN:
- Kalimat 1 = hook provokatif (pertanyaan)
- Kalimat 2 = fakta surprising dari origin story {figure}
- Total MAX 35 kata
- Ada spasi setelah nama: "{figure} dulunya..." BUKAN "{figure}dulunya..."
Output JSON: {{"text": "..."}}'''

figure = random.choice(FIGURES)
prompt = prompt_template.format(figure=figure)

print(f"[FIGURE] {figure}")

for attempt in range(3):
    try:
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
            json={
                "model": "mistral-small-latest",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.8,
                "max_tokens": 200,
            },
            timeout=30,
        )
        if r.status_code == 200:
            raw = r.json()["choices"][0]["message"]["content"].strip()
            # Parse JSON from response
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            data = json.loads(raw, strict=False)
            text = _clean(data.get("text", ""))
            if not text or len(text) < 20:
                print(f"  [RETRY] Too short: {len(text)} chars")
                continue

            print(f"\n[POST] ({len(text)} chars)")
            print(text)
            print()

            # Post to Threads
            post_id = _post_container(text)
            if post_id:
                print(f"✅ Posted: https://www.threads.net/@ryanhadiii/post/{post_id}")
            else:
                print("❌ Post failed")
            break
    except json.JSONDecodeError as e:
        print(f"  [RETRY] JSON parse error: {e}")
        continue
    except Exception as e:
        print(f"  [RETRY] Error: {e}")
        continue
else:
    print("❌ All attempts failed")
