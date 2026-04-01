#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# run_backtest.sh — Execute a strategy against a dataset and collect results
#
# Usage:
#   ./scripts/run_backtest.sh <strategy.py> <dataset_dir> [--open]
#
# Examples:
#   ./scripts/run_backtest.sh strategies/round1/mm.py datasets/round1/
#   ./scripts/run_backtest.sh strategies/round1/mm.py datasets/round1/ --open
#
# The --open flag auto-opens the visualizer with the result.
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKTESTER_DIR="$PROJECT_ROOT/backtester/prosperity_rust_backtester"

# --- Args ---
STRATEGY="${1:?Usage: run_backtest.sh <strategy.py> <dataset_dir> [--open]}"
DATASET="${2:?Usage: run_backtest.sh <strategy.py> <dataset_dir> [--open]}"
OPEN_VIZ="${3:-}"

STRATEGY_NAME="$(basename "$STRATEGY" .py)"
RUN_ID="$(date +%Y%m%d_%H%M%S)_${STRATEGY_NAME}"
RUN_DIR="$PROJECT_ROOT/runs/$RUN_ID"

mkdir -p "$RUN_DIR"

# --- Resolve absolute paths ---
STRATEGY_ABS="$(cd "$(dirname "$STRATEGY")" && pwd)/$(basename "$STRATEGY")"
DATASET_ABS="$(cd "$DATASET" && pwd)"

echo "============================================"
echo "  Backtest: $STRATEGY_NAME"
echo "  Dataset:  $DATASET"
echo "  Run ID:   $RUN_ID"
echo "============================================"
echo ""

# --- Check backtester exists ---
if [ ! -d "$BACKTESTER_DIR" ]; then
    echo "ERROR: Rust backtester not found at $BACKTESTER_DIR"
    echo "Run: git submodule update --init --recursive"
    exit 1
fi

# --- Run backtester ---
echo "[1/3] Running backtester..."
START_TIME=$(date +%s)

cd "$BACKTESTER_DIR"

# Copy strategy to backtester's expected location
cp "$STRATEGY_ABS" ./trader.py

# Run the backtester (adjust command based on actual backtester CLI)
if [ -f "Makefile" ]; then
    make backtest 2>&1 | tee "$RUN_DIR/backtest.log"
elif [ -f "Cargo.toml" ]; then
    cargo run --release -- --trader ./trader.py --data "$DATASET_ABS" 2>&1 | tee "$RUN_DIR/backtest.log"
else
    python -m backtester ./trader.py "$DATASET_ABS" 2>&1 | tee "$RUN_DIR/backtest.log"
fi

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

cd "$PROJECT_ROOT"

# --- Collect outputs ---
echo ""
echo "[2/3] Collecting outputs..."

# Move any generated output files to the run directory
for f in submission.log metrics.json pnl_by_product.csv trades.csv activity.csv; do
    if [ -f "$BACKTESTER_DIR/$f" ]; then
        cp "$BACKTESTER_DIR/$f" "$RUN_DIR/"
    fi
done

# Also check for outputs in common alternative locations
for f in output/*.log output/*.csv output/*.json results/*.log results/*.csv results/*.json; do
    if [ -f "$BACKTESTER_DIR/$f" ]; then
        cp "$BACKTESTER_DIR/$f" "$RUN_DIR/"
    fi
done

# Clean up copied strategy
rm -f "$BACKTESTER_DIR/trader.py"

# --- Generate analysis ---
echo "[3/3] Generating analysis..."
python "$SCRIPT_DIR/analyze.py" "$RUN_DIR" > "$RUN_DIR/summary.txt" 2>&1 || true

# --- Symlink latest ---
ln -sfn "$RUN_DIR" "$PROJECT_ROOT/runs/latest"

# --- Report ---
echo ""
echo "============================================"
echo "  COMPLETE in ${ELAPSED}s"
echo "  Output:  runs/$RUN_ID/"
echo "  Latest:  runs/latest -> $RUN_ID"
echo "============================================"
echo ""

if [ -f "$RUN_DIR/summary.txt" ]; then
    cat "$RUN_DIR/summary.txt"
fi

# --- Auto-open visualizer ---
if [ "$OPEN_VIZ" = "--open" ]; then
    SERVE_PORT=8080
    VIZ_PORT=5173
    echo ""
    echo "Opening visualizer..."
    open "http://localhost:${VIZ_PORT}/?open=http://localhost:${SERVE_PORT}/${RUN_ID}/submission.log"
fi
