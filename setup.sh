#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# setup.sh — One-command setup after cloning the repo.
#
# Usage:
#   git clone --recursive https://github.com/YOUR_ORG/slu-imc-prosperity-4.git
#   cd slu-imc-prosperity-4
#   ./setup.sh
# ============================================================================

echo "============================================"
echo "  IMC Prosperity 4 — Workspace Setup"
echo "============================================"
echo ""

# --- 1. Submodules ---
echo "[1/5] Initializing submodules..."
git submodule update --init --recursive

# --- 2. Python venv ---
echo "[2/5] Setting up Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate
pip install --quiet matplotlib watchdog websockets jupyter
echo "  Python venv created at .venv/"

# --- 3. Visualizer ---
echo "[3/5] Installing visualizer dependencies..."
if command -v pnpm &> /dev/null; then
    cd visualizer && pnpm install && cd ..
elif command -v npm &> /dev/null; then
    cd visualizer && npm install && cd ..
else
    echo "  WARNING: Neither pnpm nor npm found. Install Node.js to use the visualizer."
fi

# --- 4. Apply local backtester patches ---
echo "[4/5] Applying local backtester patches..."
./scripts/patch_backtester.sh

# --- 5. Create directories ---
echo "[5/5] Creating local directories..."
mkdir -p runs datasets/round1 datasets/round2 datasets/round3 datasets/round4 datasets/round5

# --- Done ---
echo ""
echo "============================================"
echo "  Setup complete!"
echo "============================================"
echo ""
echo "  Quick start:"
echo "    source .venv/bin/activate"
echo ""
echo "  Start all services (visualizer + file server):"
echo "    VS Code: Cmd+Shift+P > 'Tasks: Run Task' > 'Start All Services'"
echo "    Or manually:"
echo "      cd visualizer && pnpm dev &"
echo "      python scripts/serve_runs.py &"
echo ""
echo "  Run a backtest:"
echo "    ./scripts/run_backtest.sh strategies/round1/my_strategy.py datasets/round1/"
echo ""
echo "  Analyze results:"
echo "    python scripts/analyze.py runs/latest"
echo ""
echo "  Open in VS Code:"
echo "    code ."
echo ""
