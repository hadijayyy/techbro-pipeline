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

# Publish only an immutable prepared draft. No draft means fail-closed no-post.
publish_output=$(python3 pipeline-v3.py 2>&1) || true
publish_output=$(printf '%s\n' "$publish_output" | redact_output)
printf '%s\n' "$publish_output"
if echo "$publish_output" | grep -q 'Posted:'; then
  post_id=$(echo "$publish_output" | grep 'Posted:' | tail -1 | awk '{print $NF}')
  echo "✅ Techbro posted: $post_id"
fi

# Pressbox pattern: retry one transient provider failure before no-post.
# Invalid drafts stay fail-closed; do not rerun them.
for attempt in 1 2; do
  prepare_output=$(python3 pipeline-v3.py --prepare-next 2>&1) || true
  prepare_output=$(printf '%s\n' "$prepare_output" | redact_output)
  printf '%s\n' "$prepare_output"
  if echo "$prepare_output" | grep -q 'Prepared:'; then
    exit 0
  fi
  if ! echo "$prepare_output" | grep -Eq 'Rate limit|Writer request failed'; then
    break
  fi
  [ "$attempt" -eq 1 ] && sleep 60
done

echo "ℹ️ Techbro no validated next draft; fail-closed no-post"
echo "NO_SAFE_CANDIDATE"
exit 0
