#!/usr/bin/env bash
# NSE/BSE Stock Intelligence — Daily cron job
# Runs at 7:00 AM IST (1:30 AM UTC) Mon–Fri
# Crontab entry:
#   30 1 * * 1-5 /path/to/stock-intelligence/scheduler/cron.sh >> /path/to/stock-intelligence/logs/cron.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

LOGFILE="$LOG_DIR/pipeline_$(date +%Y-%m-%d).log"

echo "========================================" | tee -a "$LOGFILE"
echo "Stock Intelligence Pipeline — $(date)" | tee -a "$LOGFILE"
echo "========================================" | tee -a "$LOGFILE"

cd "$PROJECT_DIR"

# Activate virtualenv if present
if [ -f "venv/bin/activate" ]; then
  source venv/bin/activate
fi

python main.py 2>&1 | tee -a "$LOGFILE"

echo "Pipeline finished — $(date)" | tee -a "$LOGFILE"
