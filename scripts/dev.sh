#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Claude Duet - one-command development launcher (Linux / macOS / WSL / Git Bash)
#
#   ./scripts/dev.sh
#   API_PORT=9000 HEALTH_TIMEOUT=60 ./scripts/dev.sh
#   SKIP_WEB=1 ./scripts/dev.sh
#
# Creates .venv if missing, installs apps/api/requirements.txt, starts uvicorn in
# the background, waits for /api/health, installs web deps only when
# node_modules is absent, then runs `npm run dev` in the foreground.
# The EXIT trap kills the API process on Ctrl+C, on error, and on normal exit.
# -----------------------------------------------------------------------------
set -euo pipefail

API_PORT="${API_PORT:-8787}"
WEB_PORT="${WEB_PORT:-3000}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-40}"
SKIP_WEB="${SKIP_WEB:-0}"
SKIP_INSTALL="${SKIP_INSTALL:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
API_DIR="${ROOT}/apps/api"
WEB_DIR="${ROOT}/apps/web"
VENV_DIR="${ROOT}/.venv"
HEALTH_URL="http://127.0.0.1:${API_PORT}/api/health"
API_LOG="${ROOT}/.data/dev-api.log"

API_PID=""

# Colours only when stdout is a terminal.
if [ -t 1 ]; then
  C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m';  C_DIM=$'\033[90m';   C_OFF=$'\033[0m'
else
  C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_DIM=""; C_OFF=""
fi

step() { printf '\n%s==> %s%s\n' "$C_CYAN" "$1" "$C_OFF"; }
ok()   { printf '    %s%s%s\n' "$C_GREEN" "$1" "$C_OFF"; }
note() { printf '    %s%s%s\n' "$C_YELLOW" "$1" "$C_OFF"; }
bad()  { printf '    %s%s%s\n' "$C_RED" "$1" "$C_OFF" >&2; }

cleanup() {
  local code=$?
  if [ -n "$API_PID" ] && kill -0 "$API_PID" 2>/dev/null; then
    printf '\n%s==> Stopping API (pid %s)%s\n' "$C_CYAN" "$API_PID" "$C_OFF"
    # uvicorn --reload spawns children; signal the whole process group if we can.
    kill -TERM "-$API_PID" 2>/dev/null || kill -TERM "$API_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$API_PID" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$API_PID" 2>/dev/null; then
      kill -KILL "-$API_PID" 2>/dev/null || kill -KILL "$API_PID" 2>/dev/null || true
    fi
    printf '    %sstopped%s\n' "$C_DIM" "$C_OFF"
  fi
  exit "$code"
}
trap cleanup EXIT INT TERM

printf '%sClaude Duet - dev launcher%s\n' "$C_CYAN" "$C_OFF"
echo "repo: ${ROOT}"

[ -d "$API_DIR" ] || { bad "API directory not found: ${API_DIR}"; exit 1; }
mkdir -p "$(dirname "$API_LOG")"

# --- 1. virtualenv -----------------------------------------------------------
step "Python environment"
PY="${VENV_DIR}/bin/python"
[ -x "$PY" ] || PY="${VENV_DIR}/Scripts/python.exe"   # Git Bash on Windows

if [ -x "$PY" ]; then
  ok "reusing ${VENV_DIR}"
else
  BASE_PY=""
  for candidate in python3.12 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then BASE_PY="$candidate"; break; fi
  done
  [ -n "$BASE_PY" ] || { bad "No Python interpreter found on PATH. Install Python 3.12."; exit 1; }
  note "creating virtualenv at ${VENV_DIR} (${BASE_PY})"
  "$BASE_PY" -m venv "$VENV_DIR"
  PY="${VENV_DIR}/bin/python"
  [ -x "$PY" ] || PY="${VENV_DIR}/Scripts/python.exe"
  [ -x "$PY" ] || { bad "virtualenv created but no python found under ${VENV_DIR}"; exit 1; }
  ok "virtualenv ready"
fi

# --- 2. API dependencies -----------------------------------------------------
if [ "$SKIP_INSTALL" = "1" ]; then
  step "API dependencies (skipped: SKIP_INSTALL=1)"
else
  step "API dependencies"
  [ -f "${API_DIR}/requirements.txt" ] || { bad "requirements.txt not found"; exit 1; }
  "$PY" -m pip install --upgrade pip --quiet --disable-pip-version-check
  "$PY" -m pip install -r "${API_DIR}/requirements.txt" --quiet --disable-pip-version-check
  ok "dependencies installed"
fi

# --- 3. start the API in the background --------------------------------------
step "Starting API (uvicorn duet.main:app on :${API_PORT})"
: > "$API_LOG"
(
  cd "$API_DIR"
  export DUET_HOST=127.0.0.1
  export DUET_PORT="$API_PORT"
  export PYTHONUNBUFFERED=1
  # setsid gives the reloader its own process group so cleanup() can kill it all.
  if command -v setsid >/dev/null 2>&1; then
    exec setsid "$PY" -m uvicorn duet.main:app --host 127.0.0.1 --port "$API_PORT" --reload
  else
    exec "$PY" -m uvicorn duet.main:app --host 127.0.0.1 --port "$API_PORT" --reload
  fi
) >"$API_LOG" 2>&1 &
API_PID=$!
ok "pid ${API_PID}, logging to ${API_LOG}"

# --- 4. wait for /api/health -------------------------------------------------
step "Waiting for ${HEALTH_URL} (timeout ${HEALTH_TIMEOUT}s)"
healthy=0
attempts=0
while [ "$attempts" -lt "$HEALTH_TIMEOUT" ]; do
  attempts=$((attempts + 1))
  if ! kill -0 "$API_PID" 2>/dev/null; then
    bad "the API process exited early"
    break
  fi
  if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 1
done

if [ "$healthy" -ne 1 ]; then
  echo
  bad "--------------------------------------------------------------"
  bad " API DID NOT BECOME HEALTHY"
  bad "--------------------------------------------------------------"
  bad " url    : ${HEALTH_URL}"
  bad " waited : ${HEALTH_TIMEOUT}s over ${attempts} attempts"
  bad ""
  bad " Last 40 lines of ${API_LOG}:"
  tail -n 40 "$API_LOG" 2>/dev/null | sed 's/^/   | /' >&2 || echo "   | (no output)" >&2
  bad ""
  bad " Common causes:"
  bad "   * duet.main:app does not exist yet, or importing it fails"
  bad "   * /api/health is not registered on the app"
  bad "   * port ${API_PORT} is already in use"
  bad "   * a dependency is missing (re-run without SKIP_INSTALL=1)"
  bad "--------------------------------------------------------------"
  exit 1
fi
ok "API is healthy"

# --- 5. web dependencies -----------------------------------------------------
if [ "$SKIP_WEB" = "1" ]; then
  step "Web app skipped (SKIP_WEB=1). Tailing the API log; Ctrl+C to stop."
  echo
  printf '  %sAPI    http://127.0.0.1:%s%s\n' "$C_GREEN" "$API_PORT" "$C_OFF"
  printf '  %sHealth %s%s\n' "$C_GREEN" "$HEALTH_URL" "$C_OFF"
  echo
  tail -f "$API_LOG"
  exit 0
fi

[ -d "$WEB_DIR" ] || { bad "Web directory not found: ${WEB_DIR}"; exit 1; }
command -v npm >/dev/null 2>&1 || { bad "npm not found on PATH. Install Node.js 22+ (or SKIP_WEB=1)."; exit 1; }

step "Web dependencies"
if [ -d "${WEB_DIR}/node_modules" ]; then
  ok "node_modules present - skipping npm install"
else
  note "node_modules missing - running npm install (this takes a while)"
  (cd "$WEB_DIR" && npm install)
  ok "web dependencies installed"
fi

# --- 6. run the web dev server in the foreground -----------------------------
echo
printf '%s--------------------------------------------------------------%s\n' "$C_GREEN" "$C_OFF"
printf '%s  Claude Duet is starting%s\n' "$C_GREEN" "$C_OFF"
printf '%s--------------------------------------------------------------%s\n' "$C_GREEN" "$C_OFF"
printf '%s  Web    http://localhost:%s%s\n' "$C_GREEN" "$WEB_PORT" "$C_OFF"
printf '%s  API    http://127.0.0.1:%s%s\n' "$C_GREEN" "$API_PORT" "$C_OFF"
printf '%s  Docs   http://127.0.0.1:%s/docs%s\n' "$C_GREEN" "$API_PORT" "$C_OFF"
printf '%s  Health %s%s\n' "$C_GREEN" "$HEALTH_URL" "$C_OFF"
printf '%s--------------------------------------------------------------%s\n' "$C_GREEN" "$C_OFF"
printf '%s  API logs: tail -f %s%s\n' "$C_DIM" "$API_LOG" "$C_OFF"
printf '%s  Ctrl+C stops both.%s\n' "$C_DIM" "$C_OFF"
echo

cd "$WEB_DIR"
npm run dev
