#!/bin/bash
# Techbro hourly pipeline wrapper — posts 1 thread per run
set -euo pipefail
cd /home/ubuntu/techbro

# Load environment
set -a
source /home/ubuntu/techbro/.env
set +a

# File lock — prevent concurrent runs
exec 200>/tmp/techbro-hourly.lock
flock -n 200 || { echo "⚠️ Techbro: another instance running — skip"; exit 0; }

# Cron output is durable. Never write bearer tokens into it.
redact_output() {
  sed -E "s/(access_token=)[^&[:space:]\"']+/\1[REDACTED]/g"
}

# One pipeline invocation per hourly tick. --prepare-next is deprecated and
# now aliases the full single-pass flow; calling it here would publish twice.
set +e
publish_output=$(python3 pipeline-v3.py 2>&1)
publish_rc=$?
set -e
publish_output=$(printf '%s\n' "$publish_output" | redact_output)
printf '%s\n' "$publish_output"
if echo "$publish_output" | grep -q 'Posted:'; then
  post_id=$(echo "$publish_output" | grep 'Posted:' | tail -1 | awk '{print $NF}')
  echo "✅ Techbro posted: $post_id"
fi
exit "$publish_rc"
