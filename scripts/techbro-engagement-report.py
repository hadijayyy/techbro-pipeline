#!/usr/bin/env python3
"""Techbro daily engagement report -> Telegram via cron delivery.

Reads posted_topics_v2.json (v3 ledger: views/likes/replies/reposts/quotes
filled by pipeline's sync_ledger_metrics). Prints compact report to stdout;
cron deliver=origin forwards it to the Telegram DM.
"""
import json, os, time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

WIB = timezone(timedelta(hours=7))
POSTED = os.path.expanduser("~/techbro/posted_topics_v2.json")
data = json.load(open(POSTED))
topics = data if isinstance(data, list) else data.get("topics", [])
now = time.time()


def ts(t):
    s = t.get("timestamp") or t.get("posted")
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def window(days):
    return [t for t in topics if (v := ts(t)) and now - v < days * 86400]


def measured(t):
    return t.get("views") is not None


for label, days in (("24h", 1), ("7d", 7)):
    w = window(days)
    m = [t for t in w if measured(t)]
    pend = [t for t in w if not measured(t)]
    views = sorted([v or 0 for v in (t.get("views") for t in m)])
    med = views[len(views) // 2] if views else 0
    avg = sum(views) / len(views) if views else 0
    print(f"[{label}] posts={len(w)} measured={len(m)} pending={len(pend)}")
    print(f"  views: median {med:,} | avg {avg:,.0f} | range {min(views) if views else 0:,}-{max(views) if views else 0:,}")
    if label == "24h":
        ranked = sorted(w, key=lambda x: (x.get("views") or 0), reverse=True)
        print("  top:", "; ".join(f"{(t.get('views') or 0):,} {t.get('title', '?')[:45]}" for t in ranked[:3]))
        print("  bad:", "; ".join(f"{(t.get('views') or 0):,} {t.get('title', '?')[:45]}" for t in ranked[-3:]))

# 7d lane/pattern engagement quality (≥100 views)
m7 = [t for t in window(7) if measured(t) and (t.get("views") or 0) >= 100]
by = {"lane": defaultdict(list), "pattern": defaultdict(list)}
for t in m7:
    v = t.get("views") or 0
    q = ((t.get("likes") or 0) + 2 * (t.get("replies") or 0) + 3 * (t.get("reposts") or 0) + 2 * (t.get("quotes") or 0)) / v
    by["lane"][t.get("lane") or "?"].append(q)
    by["pattern"][t.get("pattern") or "?"].append(q)
print("7d engagement/lane (≥100 views):")
for k, vals in sorted(by["lane"].items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True)[:6]:
    print(f"  {k}: {sum(vals)/len(vals):.4f} ({len(vals)} posts)")
print("7d engagement/pattern:")
for k, vals in sorted(by["pattern"].items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True)[:6]:
    print(f"  {k}: {sum(vals)/len(vals):.4f} ({len(vals)} posts)")

# Health
last_v = [ts(t) for t in topics if ts(t)]
last = max(last_v) if last_v else 0
print(f"health: last_post {datetime.fromtimestamp(last, WIB).strftime('%d %b %H:%M WIB') if last else 'never'} | total={len(topics)}")
print(f"⏰ {datetime.now(WIB).strftime('%H:%M WIB, %d %b %Y')}")
