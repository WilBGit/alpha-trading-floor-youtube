#!/bin/bash
# Alpha Trading Floor — Daily Cron Launcher
# Runs at 4:30 PM ET (1:30 PM PST) on trading days only (Mon-Fri)
# Weekend runs use 'advice' mode for finance education content
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${ALPHA_YOUTUBE_PROJECT_DIR:-$SCRIPT_DIR}"
LOG_DIR="$PROJECT_DIR/logs"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

# Determine mode from day of week
DAY_NUM=$(date +%u)  # 1=Mon ... 7=Sun
if [ "$DAY_NUM" -ge 6 ]; then
    MODE="advice"
    echo "[$(date)] Weekend mode: Finance Advice" >> "$LOG_DIR/cron.log"
else
    MODE="levels"
    echo "[$(date)] Weekday mode: Stock Analysis with Alpha Scores" >> "$LOG_DIR/cron.log"
fi

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Use the project's own interpreter directly. Sourcing activation scripts from
# an external volume can fail under Hermes/launchd before Python starts.
PYTHON="$PROJECT_DIR/venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
    echo "[$(date)] Missing project interpreter: $PYTHON" >> "$LOG_DIR/cron.log"
    exit 1
fi

# Set Python path
export PYTHONPATH="$PROJECT_DIR:$PROJECT_DIR/app:$PROJECT_DIR/config"

# Load environment
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# Do not source the legacy money-machines .env here: it contains shell-unquoted
# values and can abort the cron before the pipeline starts. YouTube credentials
# are loaded by the ATF project/configuration itself.

# Clear __pycache__ to pick up code changes
find "$PROJECT_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Set Python path (remove hermes-agent paths to avoid numpy conflicts)
export PYTHONPATH="$PROJECT_DIR:$PROJECT_DIR/app:$PROJECT_DIR/config"

# Idempotence guard: a verified recovery upload for today means the scheduled
# job must exit successfully without publishing a duplicate video.
UPLOAD_MARKER="$LOG_DIR/last_successful_upload.json"
if [ -f "$UPLOAD_MARKER" ] && "$PYTHON" - "$UPLOAD_MARKER" "$(date +%F)" <<'PY'
import json
import sys
try:
    data = json.load(open(sys.argv[1]))
    ok = (
        data.get("status") == "verified_uploaded"
        and str(data.get("recorded_at", ""))[:10] == sys.argv[2]
        and bool(data.get("video_id"))
        and bool(data.get("url"))
    )
except Exception:
    ok = False
sys.exit(0 if ok else 1)
PY
then
    echo "[$(date)] Verified upload already exists for today; skipping duplicate YouTube upload." >> "$LOG_DIR/cron.log"
    exit 0
fi

# Run the active preflight check with bounded auto-fixes enabled.
echo "[$(date)] Running preflight + auto-fix..." >> "$LOG_DIR/cron.log"
PREFLIGHT_ARGS=(--no-fix)
if [ "${PREFLIGHT_ENABLE_FIXES:-false}" = "true" ]; then
    PREFLIGHT_ARGS=(--fix)
fi
if ! "$PYTHON" "$PROJECT_DIR/preflight_check.py" "${PREFLIGHT_ARGS[@]}" >> "$LOG_DIR/cron.log" 2>&1; then
    echo "[$(date)] PREFLIGHT FAILED AFTER AUTO-FIX — skipping pipeline. Check logs for unresolved blockers." >> "$LOG_DIR/cron.log"
    exit 1
fi
echo "[$(date)] Preflight passed after auto-fix. Starting pipeline..." >> "$LOG_DIR/cron.log"

# Run the pipeline
echo "[$(date)] Starting Alpha Trading Floor pipeline (mode=$MODE)..." >> "$LOG_DIR/cron.log"

cd "$PROJECT_DIR"
UPLOAD_ARGS=(--no-upload)
if [ "${YOUTUBE_UPLOAD_ENABLED:-false}" = "true" ]; then
    UPLOAD_ARGS=(--upload)
fi
"$PYTHON" run.py --mode "$MODE" "${UPLOAD_ARGS[@]}" >> "$LOG_DIR/cron.log" 2>&1

EXIT_CODE=$?
echo "[$(date)] Pipeline finished with exit code: $EXIT_CODE" >> "$LOG_DIR/cron.log"

exit $EXIT_CODE
