#!/usr/bin/env bash
# Start morning paper-trading stack: IB Gateway (paper :4002) + Streamlit cockpit (:8501).
# Idempotent — reuses whatever is already listening. Does NOT Start Algo (cockpit click).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PIT_PYTHON:-/Users/badremhiouah/miniconda3/envs/py314/bin/python}"
COCKPIT_PORT="${PIT_COCKPIT_PORT:-8501}"
GATEWAY_PORT="${PIT_GATEWAY_PORT:-4002}"
GATEWAY_HOST="${PIT_GATEWAY_HOST:-127.0.0.1}"
COCKPIT_URL="http://localhost:${COCKPIT_PORT}"
LOG_DIR="${TMPDIR:-/tmp}/positive-impact-trading"
COCKPIT_LOG="${LOG_DIR}/cockpit.log"
LAUNCHER_LOG="${LOG_DIR}/launcher.log"

# Preferred Gateway app (user install under ~/Applications). Override with PIT_GATEWAY_APP.
GATEWAY_APP="${PIT_GATEWAY_APP:-$HOME/Applications/IB Gateway 10.51/IB Gateway 10.51.app}"

mkdir -p "$LOG_DIR"

port_listening() {
  local host="$1" port="$2"
  if command -v nc >/dev/null 2>&1; then
    nc -z -G 1 "$host" "$port" >/dev/null 2>&1
    return $?
  fi
  # Fallback without nc
  "$PYTHON" - "$host" "$port" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(0.5)
try:
    raise SystemExit(0 if s.connect_ex((host, port)) == 0 else 1)
finally:
    s.close()
PY
}

wait_for_port() {
  local host="$1" port="$2" label="$3" seconds="${4:-90}"
  local i
  for ((i = 1; i <= seconds; i++)); do
    if port_listening "$host" "$port"; then
      echo "  ✓ ${label} listening on ${host}:${port}"
      return 0
    fi
    sleep 1
  done
  echo "  … ${label} not listening on ${host}:${port} after ${seconds}s"
  return 1
}

echo "=== Positive Impact Trading — morning start $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
echo "  repo: ${REPO_ROOT}"
echo "  log:  ${LAUNCHER_LOG}"
echo

# --- IB Gateway (paper API socket) ------------------------------------------
if port_listening "$GATEWAY_HOST" "$GATEWAY_PORT"; then
  echo "IB Gateway: already up on ${GATEWAY_HOST}:${GATEWAY_PORT} — reusing"
else
  if [[ ! -d "$GATEWAY_APP" ]]; then
    echo "IB Gateway: app not found at:"
    echo "  ${GATEWAY_APP}"
    echo "Open IB Gateway manually (paper login), or set PIT_GATEWAY_APP to the .app path."
  else
    echo "IB Gateway: launching ${GATEWAY_APP}"
    open "$GATEWAY_APP"
    echo "  Log into the paper account if prompted (API sockets on port ${GATEWAY_PORT})."
    if ! wait_for_port "$GATEWAY_HOST" "$GATEWAY_PORT" "IB Gateway" 120; then
      echo "  Gateway still needs login/API — cockpit will start anyway; Start Algo later."
    fi
  fi
fi

echo

# --- Streamlit cockpit ------------------------------------------------------
# Fingerprint the checkout so a long-lived :8501 process cannot hide master
# updates (Desktop launcher used to "reuse" a days-old Streamlit).
COCKPIT_REV_FILE="${LOG_DIR}/cockpit.rev"
CURRENT_REV="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
FORCE_RESTART="${PIT_FORCE_COCKPIT_RESTART:-0}"

stop_cockpit_on_port() {
  local pids
  pids="$(lsof -t -iTCP:"${COCKPIT_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return 0
  fi
  echo "  stopping stale cockpit PID(s): ${pids}"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 1
  pids="$(lsof -t -iTCP:"${COCKPIT_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 1
  fi
}

start_cockpit() {
  if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: Python not found at ${PYTHON}"
    echo "Set PIT_PYTHON to the project interpreter (conda env py314)."
    exit 1
  fi
  if [[ ! -f "${REPO_ROOT}/scripts/run_cockpit.py" ]]; then
    echo "ERROR: missing ${REPO_ROOT}/scripts/run_cockpit.py"
    echo "Check out master (or pull) so the repo scripts are present."
    exit 1
  fi
  echo "Cockpit: starting on port ${COCKPIT_PORT} (rev ${CURRENT_REV:0:7}, log: ${COCKPIT_LOG})"
  # Background so this script can open the browser and return.
  nohup "$PYTHON" "${REPO_ROOT}/scripts/run_cockpit.py" --port "$COCKPIT_PORT" \
    >"$COCKPIT_LOG" 2>&1 &
  disown || true
  echo "$CURRENT_REV" >"$COCKPIT_REV_FILE"
  if ! wait_for_port "127.0.0.1" "$COCKPIT_PORT" "Cockpit" 45; then
    echo "ERROR: cockpit did not come up. Tail of log:"
    tail -n 40 "$COCKPIT_LOG" || true
    exit 1
  fi
}

if port_listening "127.0.0.1" "$COCKPIT_PORT"; then
  PREV_REV="$(cat "$COCKPIT_REV_FILE" 2>/dev/null || echo "")"
  if [[ "$FORCE_RESTART" == "1" ]]; then
    echo "Cockpit: PIT_FORCE_COCKPIT_RESTART=1 — restarting ${COCKPIT_URL}"
    stop_cockpit_on_port
    start_cockpit
  elif [[ -z "$PREV_REV" || "$PREV_REV" != "$CURRENT_REV" ]]; then
    echo "Cockpit: code changed (${PREV_REV:-unknown} → ${CURRENT_REV:0:7}) — restarting (stops in-process algo if any)"
    stop_cockpit_on_port
    start_cockpit
  else
    echo "Cockpit: already serving ${COCKPIT_URL} @ ${CURRENT_REV:0:7} — reusing"
  fi
else
  start_cockpit
fi

echo
echo "Opening ${COCKPIT_URL}"
open "$COCKPIT_URL"

echo
echo "Ready. In the cockpit: Sync from IBKR (once/day) → Start algo when you want the loop."
echo "This launcher does not Start Algo automatically."
