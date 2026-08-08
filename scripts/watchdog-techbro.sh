#!/bin/bash
# Legacy compatibility wrapper. Scheduling is handled by Hermes Techbro Hourly.
set -euo pipefail
cd /home/ubuntu/techbro
exec python3 pipeline-v3.py "$@"
