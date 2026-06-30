#!/bin/bash
# techbro-pipeline: scrape → generate → post (1 article per run)
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
set +a

LOG="logs/pipeline-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

echo "=== Pipeline run: $(date) ==="

# 1. Scrape + score + generate (1 article)
python3 scripts/pipeline.py --top 1 2>&1

# 2. Post staged
python3 scripts/poster.py 2>&1

# 3. Send report to @Szejay_bot
python3 scripts/report.py 2>&1

echo "=== Done: $(date) ==="
