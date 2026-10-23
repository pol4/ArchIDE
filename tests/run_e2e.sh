#!/usr/bin/env bash
# ArchIDE e2e runner: starts the server in a deterministic test profile
# (stub docker, isolated config, mock LLM) and runs the API + browser suites.
set -uo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
POC="$ROOT/archide-poc"
PORT="${ARCHIDE_TEST_PORT:-8901}"
DATA="$(mktemp -d)"
STUB="$(mktemp -d)"

export no_proxy="127.0.0.1,localhost" NO_PROXY="127.0.0.1,localhost"
export ARCHIDE_TEST_URL="http://127.0.0.1:$PORT"
export ARCHIDE_DATA="$DATA"
export ARCHIDE_HOST=127.0.0.1
export ARCHIDE_PORT="$PORT"
export ARCHIDE_PUBLIC_URL="http://127.0.0.1:$PORT"
export FAKE_PROJECT="$POC"
export STUB_STATE="$STUB"
export PATH="$STUB:$PATH"

cp "$ROOT/tests/stub/docker" "$STUB/docker"
chmod +x "$STUB/docker"

echo "== starting server on :$PORT (stub docker, isolated config) =="
python3 "$POC/server.py" > "$STUB/server.log" 2>&1 &
SERVER_PID=$!
cleanup() { kill "$SERVER_PID" 2>/dev/null || true; rm -rf "$DATA" "$STUB"; }
trap cleanup EXIT

ready=0
for _ in $(seq 1 60); do
  if curl -s -o /dev/null "$ARCHIDE_TEST_URL/api/agents"; then ready=1; break; fi
  sleep 0.2
done
if [ "$ready" != 1 ]; then echo "server did not start:"; cat "$STUB/server.log"; exit 1; fi

fail=0
echo; echo "=== API e2e ==="
python3 "$ROOT/tests/api/test_api.py" || fail=1

echo; echo "=== Browser e2e ==="
if [ ! -d "$ROOT/tests/browser/node_modules" ]; then
  echo "(installing puppeteer-core…)"
  (cd "$ROOT/tests/browser" && npm install --silent --no-audit --no-fund) || fail=1
fi
node "$ROOT/tests/browser/test_ui.mjs" || fail=1

echo
if [ "$fail" -eq 0 ]; then echo "E2E RESULT: PASS"; else echo "E2E RESULT: FAIL"; fi
exit "$fail"
