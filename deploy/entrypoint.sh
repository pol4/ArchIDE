#!/usr/bin/env sh
# ArchIDE container entrypoint.
#
# Seeds the persistent configuration on first start (so the container works
# standalone) and then starts the server. Everything is overridable with
# environment variables — that is how the Kubernetes deployment configures the
# same image without a second build.
set -eu

DATA="${ARCHIDE_DATA:-/data}"
mkdir -p "$DATA"

CFG="$DATA/config.json"
if [ ! -f "$CFG" ]; then
  # `runner=local` by default: a single container has no Docker to talk to, and
  # the built-in agent runs in process. On a workstation with a Docker daemon
  # you may set ARCHIDE_RUNNER=docker to isolate the agent instead.
  cat > "$CFG" <<JSON
{
  "llm": {
    "base_url": "${ARCHIDE_LLM_BASE_URL:-http://host.docker.internal:11434/v1}",
    "api_key": "${ARCHIDE_LLM_API_KEY:-}",
    "model": "${ARCHIDE_LLM_MODEL:-qwen2.5-coder:7b}",
    "mode": "${ARCHIDE_LLM_MODE:-live}"
  },
  "agent": {
    "runner": "${ARCHIDE_RUNNER:-local}",
    "max_repairs": ${ARCHIDE_MAX_REPAIRS:-3}
  }
}
JSON
  echo "archide: seeded $CFG (llm mode=${ARCHIDE_LLM_MODE:-live}, runner=${ARCHIDE_RUNNER:-local})"
fi

# the seed agent manifest must exist for the registry to load
if [ ! -f "$DATA/agents.json" ] && [ -f /app/data/agents.json ]; then
  cp /app/data/agents.json "$DATA/agents.json"
fi

cd /app
exec python3 server.py
