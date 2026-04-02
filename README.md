# SLU IMC Prosperity 4

Algorithmic trading workspace for IMC Prosperity 4. Write strategies locally with Claude Code, push to run backtests automatically, view results on the team dashboard.

**Team Dashboard**: https://chennethelius.github.io/slu-imc-prosperity-4/

---

## Getting Started

```bash
git clone --recursive https://github.com/chennethelius/slu-imc-prosperity-4.git
cd slu-imc-prosperity-4
```

Build the backtester (one-time):
```bash
cd backtester && make build-release && cd ..
```

Install the visualizer (optional, for local charts):
```bash
cd visualizer && pnpm install && cd ..
```

Requires: **Python 3.10+**, **Rust** (for backtester), **Node.js 18+** + **pnpm** (for visualizer)

---

## Workflow

```
 1. Copy the template       cp strategies/template.py strategies/round1/my_strat.py
 2. Edit your strategy      (use VSCode + Claude Code)
 3. Test locally            cd backtester && make tutorial TRADER=../strategies/round1/my_strat.py
 4. Iterate                 tweak strategy → re-run → check PnL
 5. Push                    git add strategies/round1/my_strat.py && git commit && git push
 6. CI runs automatically   backtest runs on GitHub Actions (~1-2 min)
 7. View results            https://chennethelius.github.io/slu-imc-prosperity-4/
```

That's it. Push your strategy, results appear on the dashboard.

---

## Strategy Files

All strategies live in `strategies/`. The folder name determines which dataset CI runs against:

```
strategies/
  template.py              ← copy this to start
  tutorial/                ← runs against tutorial data (EMERALDS, TOMATOES)
    my_mm.py
  round1/                  ← runs against round1 data (when available)
    max_arb.py
    alice_mm.py
  round2/                  ← runs against round2 data
    ...
```

Each strategy is a single Python file with a `Trader` class. See `template.py` for the skeleton, and `CLAUDE.md` for the full data model reference.

### Rules (from IMC)
- **Single file** — your entire strategy must be one `.py` file
- **Standard library only** — no numpy, pandas, scipy
- **No network access** — pure computation
- **~100ms per tick** — keep it fast

---

## Local Testing

Run a strategy against the tutorial dataset:
```bash
cd backtester && make tutorial TRADER=../strategies/tutorial/my_strat.py
```

Run against a specific round:
```bash
cd backtester && make round1 TRADER=../strategies/round1/my_strat.py
```

Run a specific day only:
```bash
cd backtester && make round1 TRADER=../strategies/round1/my_strat.py DAY=-1
```

### Local Dashboard (optional)

For a richer local view with inline charts:
```bash
python scripts/serve_runs.py      # dashboard at http://localhost:8080
cd visualizer && pnpm dev          # visualizer at http://localhost:5173
```

### Analyze a run
```bash
python scripts/analyze.py backtester/runs/<run_id>/
```

---

## Team Dashboard

Every `git push` that changes files in `strategies/` triggers a GitHub Actions backtest. Results are automatically deployed to:

**https://chennethelius.github.io/slu-imc-prosperity-4/**

The dashboard shows:
- **Who** pushed (git author)
- **Which strategy** and dataset
- **PnL** total and per-product
- **Expandable details** — PnL charts, trades table, and the full strategy source code

Each submission stores a copy of the strategy code, so any result can be reproduced.

---

## Adding Round Data

When IMC releases new round data, download the CSVs and add them:

```bash
# Place price + trade CSV pairs in the matching round folder:
cp prices_round_1_day_0.csv  backtester/datasets/round1/
cp trades_round_1_day_0.csv  backtester/datasets/round1/

# Commit so CI can use them
git add backtester/datasets/round1/ && git commit -m "Add round1 data" && git push
```

Tutorial data is already bundled in `backtester/datasets/tutorial/`.

---

## Project Structure

```
strategies/         Your trading strategies (one .py per strategy)
  template.py       Starting point — copy this
backtester/         Rust backtester (submodule)
  datasets/         Round data (CSVs) go here
  runs/             Backtest outputs
visualizer/         Local chart viewer (submodule)
scripts/            Tooling (analyze, compare, serve dashboard)
.github/            CI pipeline + deployed dashboard
CLAUDE.md           Full IMC data model reference for Claude Code
```

## Claude Code Integration

Open this repo with Claude Code. `CLAUDE.md` gives Claude the full IMC Prosperity 4 data model — position limits, order execution rules, the `Trader` class interface, and strategy patterns. Claude can:

- Write strategies that respect all competition constraints
- Read backtest outputs and suggest improvements
- Analyze PnL patterns across products
- Compare runs and identify regressions
