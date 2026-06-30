#!/usr/bin/env python3
"""Send pipeline run summary to Telegram (@Szejay_bot)."""
import os, sys, glob, re, httpx
from datetime import datetime

LOG_DIR = os.path.expanduser("~/techbro/logs")

# Read @Szejay_bot token from Hermes .env
SZEJAY_TOKEN = ""
hermes_env = os.path.expanduser("~/.hermes/.env")
if os.path.exists(hermes_env):
    with open(hermes_env) as f:
        for line in f:
            if line.strip().startswith("SZEJAY_BOT_TOKEN="):
                SZEJAY_TOKEN = line.strip().split("=", 1)[1]
                break

CHAT_ID = "1022032312"

def get_latest_log():
    logs = sorted(glob.glob(f"{LOG_DIR}/pipeline-*.log"), key=os.path.getmtime, reverse=True)
    return logs[0] if logs else None

def parse_log(path):
    with open(path) as f:
        content = f.read()
    info = {
        "time": datetime.now().strftime("%H:%M WIB"),
        "staged": False, "posted": False, "title": "",
        "hook": "", "source": "", "score": 0, "error": "", "fallback": False,
    }
    if re.search(r'Post #\d+ staged in DB', content):
        info["staged"] = True
    if re.search(r'Thread root: \d+', content):
        info["posted"] = True
    m = re.search(r'\[(\w+)\] score=(\d+) \| (.+?)$', content, re.M)
    if m:
        info["source"] = m.group(1)
        info["score"] = int(m.group(2))
        info["title"] = m.group(3).strip()
    if "Checking DB for unposted" in content:
        info["fallback"] = True
    m = re.search(r'\[DB\] score=(\d+) \| (.+?)$', content, re.M)
    if m:
        info["title"] = m.group(2).strip()
        info["score"] = int(m.group(1))
    m = re.search(r'Hook: (.+?)$', content, re.M)
    if m:
        info["hook"] = m.group(1).strip()[:80]
    m = re.search(r'ERROR: (.+?)$', content, re.M)
    if m:
        info["error"] = m.group(1).strip()
    if "[DEDUP]" in content and not info["staged"]:
        info["error"] = info["error"] or "All articles deduped"
    return info

def send_report(info):
    if not SZEJAY_TOKEN:
        print("No SZEJAY_BOT_TOKEN, skipping report")
        return
    if info["posted"]:
        emoji, status = "✅", "POSTED"
    elif info["staged"]:
        emoji, status = "⏳", "STAGED (pending post)"
    elif info["error"]:
        emoji, status = "❌", f"FAILED: {info['error']}"
    else:
        emoji, status = "⏭️", "SKIPPED (no articles)"
    lines = [f"{emoji} **TechBro Report** — {info['time']}", f"Status: {status}"]
    if info["title"]:
        lines.append(f"Article: {info['title'][:60]}")
        lines.append(f"Source: {info['source']} | Score: {info['score']}")
    if info["fallback"]:
        lines.append("📌 Used DB fallback (no fresh articles)")
    if info["hook"]:
        lines.append(f"\n🪝 Hook: {info['hook']}")
    msg = "\n".join(lines)
    r = httpx.post(
        f"https://api.telegram.org/bot{SZEJAY_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
        timeout=10
    )
    if r.status_code == 200:
        print(f"Report sent to @Szejay_bot")
    else:
        print(f"Report failed: {r.status_code} {r.text[:100]}")

if __name__ == "__main__":
    log = get_latest_log()
    if not log:
        print("No logs found")
        sys.exit(0)
    info = parse_log(log)
    send_report(info)
