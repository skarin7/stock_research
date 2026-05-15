#!/usr/bin/env bash
# Run the stock intelligence pipeline locally (no Docker, no GCP).
# Usage:
#   bash run_local.sh              # full run
#   bash run_local.sh --dry-run    # 5 stocks only (quick test)
#   bash run_local.sh --skip-backtest

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

START=$(date +%s)

# Activate virtualenv if present
if [ -f "venv/bin/activate" ]; then
  source venv/bin/activate
elif [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
fi

# Install / sync dependencies
pip install -q -r requirements.txt

echo "========================================"
echo "Stock Intelligence — Local Run $(date)"
echo "========================================"

python main.py "$@"

ELAPSED=$(( $(date +%s) - START ))
echo ""
echo "Finished in ${ELAPSED}s ($(( ELAPSED / 60 ))m $(( ELAPSED % 60 ))s)"
