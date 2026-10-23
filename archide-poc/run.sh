#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Bind 0.0.0.0 so sandbox containers can reach the sandbox + LLM gateway
# endpoints via host.docker.internal. Override with ARCHIDE_HOST if needed.
export ARCHIDE_HOST="${ARCHIDE_HOST:-0.0.0.0}"

# No third-party dependencies: server + agents use the Python stdlib only.
exec python3 server.py
