#!/bin/bash
# techbro-pipeline: scrape → generate → post (auto-posts to Threads)
set -euo pipefail
cd /home/ubuntu/techbro
mkdir -p logs

# Kill stuck pipeline from previous run (older than 10 min)
OLD_PID=$(cat .pipeline.pid 2>/dev/null || echo "")
if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    AGE=$(($(date +%s) - $(stat -c %Y .pipeline.pid 2>/dev/null || echo 0)))
    if [ "$AGE" -gt 600 ]; then
        echo "[WRAPPER] Killing stale pipeline (PID $OLD_PID, age ${age}s)"
        kill -9 "$OLD_PID" 2>/dev/null || true
        rm -f .pipeline.lock
    fi
fi
echo $$ > .pipeline.pid

# Random delay 30-180s biar gak predictable
delay=$((RANDOM % 151 + 30))
sleep "$delay"

# Load API keys from threads-agent .env
set -a
source /home/ubuntu/threads-agent/.env
source /home/ubuntu/techbro/.env
set +a

LOG="logs/pipeline-$(date +%Y%m%d-%H%M%S).log"

echo "=== Pipeline run: $(TZ='Asia/Jakarta' date '+%H:%M WIB %d %b %Y') ===" >> "$LOG"

# Python exit code determines our exit code
EXIT_CODE=0
python3 scripts/pipeline.py >> "$LOG" 2>&1 || EXIT_CODE=$?

echo "=== Done (exit: $EXIT_CODE) ===" >> "$LOG"

# Summary to stdout (delivered to Telegram — don't override pipeline exit)
python3 << 'PYEOF' 2>/dev/null || true
import sqlite3
from datetime import datetime

conn = sqlite3.connect('pipeline.db')
conn.row_factory = sqlite3.Row

today = datetime.now().strftime('%Y-%m-%d')
posted = conn.execute('SELECT id, slide_hook, thread_post_id FROM posts WHERE status="posted" AND date(posted_at)=? ORDER BY id DESC', (today,)).fetchall()
staged = conn.execute('SELECT id, slide_hook FROM posts WHERE status="staged" ORDER BY id').fetchall()
articles = conn.execute('SELECT COUNT(*) as c FROM articles WHERE date(scraped_at)=?', (today,)).fetchone()
total_today = conn.execute('SELECT COUNT(*) as c FROM posts WHERE status="posted" AND date(posted_at)=?', (today,)).fetchone()
conn.close()

print('📊 TechBro Pipeline Report')
print(f'🕐 {datetime.now().strftime("%H:%M WIB")}')
print(f'📰 Articles today: {articles["c"]}')
print(f'✅ Posts today: {total_today["c"]}/12')
print()

if posted:
    print('Recent posts:')
    for p in posted[:3]:
        hook = (p['slide_hook'] or 'no hook')[:60]
        post_id = p['thread_post_id'] or 'pending'
        print(f'  #{p["id"]} → {post_id}')
        print(f'    {hook}')
    print()

if staged:
    print(f'⏳ Staged ({len(staged)}):')
    for s in staged:
        hook = (s['slide_hook'] or 'no hook')[:60]
        print(f'  #{s["id"]}: {hook}')
else:
    print('⏳ Nothing staged')

print()
print('Next: top of next hour')
PYEOF

exit $EXIT_CODE
