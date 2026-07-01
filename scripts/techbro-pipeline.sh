#!/bin/bash
# techbro-pipeline: scrape → generate → post (auto-posts to Threads)
set -euo pipefail
cd /home/ubuntu/techbro
mkdir -p logs

# Random delay 30-180s biar gak predictable
DELAY=$((RANDOM % 151 + 30))
echo "Sleeping ${DELAY}s..."
sleep "$DELAY"

# Load API keys from threads-agent .env
set -a
source /home/ubuntu/threads-agent/.env
source /home/ubuntu/techbro/.env
set +a

LOG="logs/pipeline-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

echo "=== Pipeline run: $(date) ==="

# 1. Scrape + score + generate + post (all in one)
python3 scripts/pipeline.py 2>&1

echo "=== Done: $(date) ==="
