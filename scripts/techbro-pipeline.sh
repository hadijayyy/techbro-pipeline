#!/bin/bash
# techbro-pipeline: scrape → generate → post (auto-posts to Threads)
set -euo pipefail
cd /home/ubuntu/techbro
mkdir -p logs

# Random delay 30-180s biar gak predictable
DELAY=$((RANDOM % 151 + 30))
sleep "$DELAY"

# Load API keys from threads-agent .env
set -a
source /home/ubuntu/threads-agent/.env
source /home/ubuntu/techbro/.env
set +a

LOG="logs/pipeline-$(date +%Y%m%d-%H%M%S).log"

# Pipeline runs silently — all output to log only
echo "=== Pipeline run: $(TZ='Asia/Jakarta' date '+%H:%M WIB %d %b %Y') ===" >> "$LOG"
python3 scripts/pipeline.py >> "$LOG" 2>&1
echo "=== Done ===" >> "$LOG"

# Summary to stdout (delivered to Telegram)
python3 -c "
import sqlite3
from datetime import datetime

conn = sqlite3.connect('pipeline.db')
conn.row_factory = sqlite3.Row

today = datetime.now().strftime('%Y-%m-%d')
posted = conn.execute('SELECT id, slide_hook, thread_post_id FROM posts WHERE status=\"posted\" AND date(posted_at)=? ORDER BY id DESC', (today,)).fetchall()
staged = conn.execute('SELECT id, slide_hook FROM posts WHERE status=\"staged\" ORDER BY id').fetchall()
articles = conn.execute('SELECT COUNT(*) as c FROM articles WHERE date(scraped_at)=?', (today,)).fetchone()
total_today = conn.execute('SELECT COUNT(*) as c FROM posts WHERE status=\"posted\" AND date(posted_at)=?', (today,)).fetchone()
conn.close()

print('📊 TechBro Pipeline Report')
print(f'🕐 {datetime.now().strftime(\"%H:%M WIB\")}')
print(f'📰 Articles today: {articles[\"c\"]}')
print(f'✅ Posts today: {total_today[\"c\"]}/12')
print()

if posted:
    print('Recent posts:')
    for p in posted[:3]:
        hook = (p['slide_hook'] or 'no hook')[:60]
        post_id = p['thread_post_id'] or 'pending'
        print(f'  #{p[\"id\"]} → {post_id}')
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
"
