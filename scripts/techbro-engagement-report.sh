#!/bin/sh
# Techbro daily engagement report -> Telegram DM (origin delivery).
set -eu
exec flock -n /tmp/techbro-engagement-report.lock python3 -u "$HOME/.hermes/scripts/techbro-engagement-report.py"
