#!/usr/bin/env bash
#
# One command: Python environment, dependencies, the mock accounting API, and the ingest run.
#
#     ./run.sh
#
# An accounting API already listening is reused as it is, ledger and all. Otherwise one is
# started for this run and stopped again at the end.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV_PY="$ROOT/.venv/bin/python"
API_URL="${ACCOUNTING_API_URL:-http://localhost:8080}"

# Set only when THIS script starts the server, so cleanup can never kill one that was
# already running when we arrived.
API_PID=""
cleanup() {
  if [[ -n "$API_PID" ]]; then
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

api_healthy() { curl -fs "$API_URL/health" >/dev/null 2>&1; }

if [[ -x "$VENV_PY" ]]; then
  echo "==> Using existing .venv"
else
  echo "==> Creating .venv"
  python3 -m venv "$ROOT/.venv"
fi

echo "==> Installing dependencies"
"$VENV_PY" -m pip install --quiet --disable-pip-version-check -r requirements.txt

if api_healthy; then
  echo "==> Accounting API already running at $API_URL — using it"
else
  echo "==> No accounting API at $API_URL — starting one"
  # Silenced, not logged: the mock prints a line per request, and those would land in the
  # middle of the pipeline's progress table below.
  python3 accounting_api.py >/dev/null 2>&1 &
  API_PID=$!
  for _ in $(seq 1 40); do api_healthy && break; sleep 0.25; done
  api_healthy || { echo "accounting API failed to start" >&2; exit 1; }
fi

echo "==> Ingesting invoices"
"$VENV_PY" ingest/main.py
