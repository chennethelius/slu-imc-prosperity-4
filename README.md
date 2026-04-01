# SLU IMC Prosperity 4

Automated development workflow for the IMC Prosperity 4 algorithmic trading challenge.

**Write strategies -> backtest -> visualize -> iterate** with Claude as your co-pilot.

## Setup

```bash
git clone https://github.com/YOUR_ORG/slu-imc-prosperity-4.git
cd slu-imc-prosperity-4
chmod +x setup.sh scripts/*.sh
./setup.sh
```

`setup.sh` handles everything — submodules, Python venv, visualizer install, and local patching.

Requires: Python 3.10+, Node.js 18+, pnpm (or npm), Rust (for backtester)

## Workflow

```
1. Write strategy in strategies/roundN/
2. Run backtest:     ./scripts/run_backtest.sh strategies/round1/my_strat.py datasets/round1/
3. View results:     Visualizer auto-opens at localhost:5173
4. Read analysis:    python scripts/analyze.py runs/latest
5. Compare runs:     python scripts/compare.py runs/<old> runs/<new>
6. Export charts:    python scripts/export_charts.py runs/latest
7. Iterate with Claude reading runs/latest/summary.txt
```

## VS Code Tasks

`Cmd+Shift+B` — Run backtest on the currently open strategy file.

`Cmd+Shift+P` > "Tasks: Run Task":
- **Start All Services** — Visualizer + file server + file watcher
- **Backtest: Run Current Strategy** — Backtest + auto-open visualizer
- **Analyze: Latest Run** — Print summary
- **Analyze: Export Charts** — Generate PNGs for Claude to read
- **Compare: Pick Two Runs** — Side-by-side diff

## Project Structure

```
strategies/         Your Trader class files (one per strategy)
backtester/         Rust backtester (submodule)
visualizer/         Prosperity visualizer (submodule, patched for local dev)
datasets/           Round data (download separately, gitignored)
runs/               Backtest outputs (gitignored, auto-generated)
scripts/            Pipeline tooling
notebooks/          Jupyter notebooks for data exploration
discord-bot/        Discord scraper for community intel (optional)
CLAUDE.md           Full IMC datamodel reference for Claude
```

## Claude Integration

CLAUDE.md contains the complete IMC Prosperity 4 datamodel, position limits, constraints, and strategy patterns. When you open this repo in VS Code with Claude Code, Claude can:

- Write valid strategies that respect all competition constraints
- Read backtest outputs (metrics.json, trades.csv, PnL CSVs)
- Analyze performance and suggest improvements
- Compare runs and identify regressions
- Read exported chart PNGs for visual analysis

## Team Workflow

- Push strategies to `strategies/roundN/` with descriptive names
- Push findings and notes (not raw run data — it's gitignored)
- Use branches for experimental strategies
- Discord bot scrapes community intel to `discord-bot/storage/`

## Adding Datasets

Download round data from the Prosperity platform and place in `datasets/roundN/`. The exact format depends on the backtester — see `backtester/README.md`.

## Discord Bot (Optional)

See `discord-bot/README.md` for setup. Runs as a GitHub Actions cron job to periodically scrape strategy discussions from the IMC Prosperity Discord.
