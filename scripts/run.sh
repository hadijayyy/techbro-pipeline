#!/bin/bash
# techbro-pipeline: scrape → generate → post (1 article per run)
# Random delay for anti-bot: 0-2400s (0-40min)
# Combined with hourly cron → actual interval ~60-100 min
set -euo pipefail

# Random jitter: 0-40 minutes
JITTER=$(( RANDOM % 2401 ))
echo "=== Sleeping ${JITTER}s (~$((JITTER/60))min) jitter ==="
sleep "$JITTER"

cd /home/ubuntu/techbro
mkdir -p logs

# Load API keys from threads-agent .env
set -a
source /home/ubuntu/threads-agent/.env
set +a

LOG="logs/pipeline-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

echo "=== Pipeline run: $(date) ==="

# 1. Scrape + score + generate (1 article)
python3 scripts/pipeline.py --top 1 2>&1

# 2. Post staged
python3 scripts/poster.py 2>&1

echo "=== Done: $(date) ==="
