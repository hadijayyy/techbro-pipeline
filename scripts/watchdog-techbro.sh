#!/bin/bash
# watchdog-techbro.sh — re-run pipeline if last run was >61m ago or never ran
set -euo pipefail
cd /home/ubuntu/techbro

STATUS_FILE="/tmp/techbro-last-post"

# What time did pipeline last succeed (exit 0)?
if [ -f "$STATUS_FILE" ]; then
    LAST=$(cat "$STATUS_FILE" 2>/dev/null || echo 0)
else
    LAST=0
fi

NOW=$(date +%s)
AGE=$((NOW - LAST))

# If last success was >61 minutes ago (or never), re-run
if [ "$AGE" -gt 3660 ] || [ "$LAST" -eq 0 ]; then
    echo "[WATCHDOG] Last post $((AGE / 60))m ago — re-running pipeline" >> watchdog.log
    # Run pipeline; --jitter 0 because watchdog runs at fixed :15
    python3 scripts/pipeline.py --jitter 0 2>&1 | tee -a watchdog.log
    EXIT=$?
    if [ "$EXIT" -eq 0 ]; then
        date +%s > "$STATUS_FILE"
    fi
else
    echo "[WATCHDOG] Last post ${AGE}s ago — OK, skipping" >> watchdog.log
fi
