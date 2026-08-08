#!/bin/bash
# Compatibility wrapper. Active runtime is pipeline-v3.py.
set -euo pipefail
cd /home/ubuntu/techbro
exec python3 pipeline-v3.py "$@"
