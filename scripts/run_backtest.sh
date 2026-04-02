#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# run_backtest.sh — Run a strategy against a dataset via the Rust backtester
#
# Usage:
#   ./scripts/run_backtest.sh <strategy.py> [dataset] [extra args...]
#
# Examples:
#   ./scripts/run_backtest.sh strategies/round1/mm.py tutorial
#   ./scripts/run_backtest.sh strategies/round1/mm.py round1 --persist
#   ./scripts/run_backtest.sh strategies/round1/mm.py round2 --day=-1
#   ./scripts/run_backtest.sh strategies/round1/mm.py  # defaults to tutorial
#
# Dataset can be an alias (tutorial, round1, r1, etc.) or a path.
# Extra args are passed through to rust_backtester.
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKTESTER_DIR="$PROJECT_ROOT/backtester"

# --- Args ---
STRATEGY="${1:?Usage: run_backtest.sh <strategy.py> [dataset] [extra args...]}"
DATASET="${2:-tutorial}"
shift 2 2>/dev/null || shift 1 2>/dev/null || true
EXTRA_ARGS=("$@")

# Resolve strategy to absolute path
STRATEGY_ABS="$(cd "$(dirname "$STRATEGY")" && pwd)/$(basename "$STRATEGY")"
STRATEGY_NAME="$(basename "$STRATEGY" .py)"

echo "============================================"
echo "  Strategy: $STRATEGY_NAME"
echo "  Dataset:  $DATASET"
echo "============================================"
echo ""

# --- Run backtester ---
cd "$BACKTESTER_DIR"

# Use the backtester's cargo wrapper for macOS compatibility
./scripts/cargo_local.sh run -- \
    --trader "$STRATEGY_ABS" \
    --dataset "$DATASET" \
    --persist \
    "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"

# Find the latest run directory the backtester created
LATEST_RUN="$(ls -td runs/backtest-* 2>/dev/null | head -1)"

if [ -z "$LATEST_RUN" ]; then
    echo "Warning: No run output found in backtester/runs/"
    exit 0
fi

# Copy/link to project-level runs/ for easy access
RUN_ID="$(date +%Y%m%d_%H%M%S)_${STRATEGY_NAME}"
PROJECT_RUN_DIR="$PROJECT_ROOT/runs/$RUN_ID"
cp -r "$LATEST_RUN" "$PROJECT_RUN_DIR"

# Symlink latest
ln -sfn "$PROJECT_RUN_DIR" "$PROJECT_ROOT/runs/latest"

echo ""
echo "============================================"
echo "  Output:  runs/$RUN_ID/"
echo "  Latest:  runs/latest"
echo "============================================"

# --- Generate analysis if available ---
if [ -f "$SCRIPT_DIR/analyze.py" ] && [ -f "$PROJECT_RUN_DIR/metrics.json" ]; then
    echo ""
    python3 "$SCRIPT_DIR/analyze.py" "$PROJECT_RUN_DIR" 2>/dev/null || true
fi

# --- Auto-open visualizer if serve_runs is running ---
SERVE_PORT=8080
if curl -s -o /dev/null -w "" "http://localhost:$SERVE_PORT/" 2>/dev/null; then
    # Find all per-day runs with submission.log (skip bundles)
    cd "$BACKTESTER_DIR"
    for RUN in $(ls -td runs/backtest-* 2>/dev/null); do
        if [ -f "$RUN/submission.log" ] && [ -f "$RUN/metrics.json" ]; then
            LOG_URL="http://localhost:${SERVE_PORT}/$(basename "$RUN")/submission.log"
            VIZ_URL="http://localhost:5173/?open=$(python3 -c "from urllib.parse import quote; print(quote('$LOG_URL', safe=''))")"
            echo ""
            echo "  Visualize: $VIZ_URL"
            # Open the first (most recent) one
            open "$VIZ_URL" 2>/dev/null || true
            break
        fi
    done
else
    echo ""
    echo "  Tip: Run 'python scripts/serve_runs.py' for auto-visualization"
    echo "  Dashboard: http://localhost:$SERVE_PORT/"
fi
