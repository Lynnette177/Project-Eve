#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/logs"
LOG_FILE="$LOG_DIR/main.log"
PID_FILE="$ROOT_DIR/main.pid"

mkdir -p "$LOG_DIR"

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "main.py is already running, pid=$OLD_PID"
    echo "tail -f '$LOG_FILE'"
    exit 0
  else
    rm -f "$PID_FILE"
  fi
fi

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python not found: $PYTHON_BIN"
  exit 1
fi

export PYTHONUNBUFFERED=1
export TZ="${TZ:-Asia/Shanghai}"

nohup "$PYTHON_BIN" -u main.py >> "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

sleep 1
if kill -0 "$PID" 2>/dev/null; then
  echo "Started main.py, pid=$PID"
  echo "Log file: $LOG_FILE"
  echo "Watch logs: tail -f '$LOG_FILE'"
else
  echo "Failed to start main.py"
  exit 1
fi
