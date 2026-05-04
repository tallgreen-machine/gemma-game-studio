#!/usr/bin/env bash
# Bulletproof supervisor launcher.
# Kills any existing instance, cleans up stale state, then starts fresh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/supervisor.pid"
LOG_FILE="$SCRIPT_DIR/supervisor.log"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"

echo "=== Gemma Supervisor Launcher ==="

# 1. Kill any running supervisor process via PID file
if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Stopping existing supervisor (PID $OLD_PID)..."
        kill "$OLD_PID"
        # Wait up to 10s for it to exit cleanly
        for i in $(seq 1 10); do
            kill -0 "$OLD_PID" 2>/dev/null || break
            sleep 1
        done
        # Force-kill if still alive
        if kill -0 "$OLD_PID" 2>/dev/null; then
            echo "Force-killing stubborn process $OLD_PID..."
            kill -9 "$OLD_PID" 2>/dev/null || true
        fi
    else
        echo "Stale PID file found (process $OLD_PID is dead). Cleaning up."
    fi
    rm -f "$PID_FILE"
fi

# 2. Also kill any other stray supervisor.py processes not in the PID file
STRAY_PIDS=$(pgrep -f "supervisor.py" 2>/dev/null || true)
if [[ -n "$STRAY_PIDS" ]]; then
    echo "Killing stray supervisor.py processes: $STRAY_PIDS"
    echo "$STRAY_PIDS" | xargs kill 2>/dev/null || true
    sleep 2
    # Force-kill anything still alive
    STRAY_PIDS=$(pgrep -f "supervisor.py" 2>/dev/null || true)
    if [[ -n "$STRAY_PIDS" ]]; then
        echo "$STRAY_PIDS" | xargs kill -9 2>/dev/null || true
    fi
fi

# 3. Verify venv exists
if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "ERROR: venv not found at $VENV_PYTHON"
    exit 1
fi

# 4. Launch supervisor in background, append to log
echo "Starting supervisor..."
nohup "$VENV_PYTHON" "$SCRIPT_DIR/supervisor.py" >> "$LOG_FILE" 2>&1 &
NEW_PID=$!

# 5. Verify it actually started (give it 3 seconds)
sleep 3
if kill -0 "$NEW_PID" 2>/dev/null; then
    echo "Supervisor started successfully (PID $NEW_PID)."
    echo "Log: $LOG_FILE"
else
    echo "ERROR: Supervisor failed to start. Check $LOG_FILE for details."
    tail -20 "$LOG_FILE"
    exit 1
fi
