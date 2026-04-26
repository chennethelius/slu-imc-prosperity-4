#!/usr/bin/env python3
"""Push a strategy to the team dashboard with IMC-server-generated logs.

Workflow:
    1. Submit the strategy to IMC via the prosperity-cli auth.
    2. Poll until simulation finishes.
    3. Download the resulting submission.log from IMC (richer than the local
       rust_backtester output — includes lambdaLog with full tick-by-tick
       compressed state when the trader uses the visualizer Logger class).
    4. Stash the log under datasets_extra/imc_runs/<author>_<strategy>/<sub_id>.log
       so collect_results.py picks it up (TODO: extend collect_results to read
       from this dir if present).
    5. git add + commit the strategy file (if uncommitted) and the IMC log,
       then `git push`.

Requires:
    pip install prosperity-cli (already installed)
    `prosperity config` already run (~/.prosperity/config.json with email/password)

Usage:
    python scripts/push_with_imc_logs.py strategies/round3/v27_test1_thresh_informed.py
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IMC_LOGS_ROOT = REPO_ROOT / "datasets_extra" / "imc_runs"


def run(cmd, **kw):
    print(f"$ {' '.join(str(c) for c in cmd)}", flush=True)
    return subprocess.run(cmd, check=True, **kw)


def submit_and_download(trader_path: Path) -> Path:
    """Run prosperity submit (which polls and saves the log) and return the path."""
    # prosperity-cli saves to ./backtests/<timestamp>-live.log by default;
    # capture its output to find the path.
    backtests = REPO_ROOT / "backtests"
    backtests.mkdir(exist_ok=True)
    before = set(backtests.glob("*.log"))
    run(["prosperity", "submit", str(trader_path), "--no-vis"], cwd=str(REPO_ROOT))
    after = set(backtests.glob("*.log"))
    new = after - before
    if not new:
        raise RuntimeError("no new IMC log file appeared — submit may have failed")
    # Pick the newest of the new
    return max(new, key=lambda p: p.stat().st_mtime)


def stash_imc_log(log_path: Path, trader_path: Path) -> Path:
    author_strategy = f"{trader_path.parent.name}_{trader_path.stem}"
    dest_dir = IMC_LOGS_ROOT / author_strategy
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / log_path.name
    shutil.copy2(log_path, dest)
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trader", type=Path, help="Path to Trader file (e.g. strategies/round3/v27.py)")
    ap.add_argument("--no-push", action="store_true", help="Stage and commit but skip git push")
    args = ap.parse_args()

    trader = args.trader.resolve()
    if not trader.exists():
        sys.exit(f"trader not found: {trader}")

    print(f"Submitting {trader} to IMC...", flush=True)
    log = submit_and_download(trader)
    print(f"IMC log saved locally: {log}", flush=True)

    stashed = stash_imc_log(log, trader)
    print(f"Stashed for dashboard: {stashed.relative_to(REPO_ROOT)}", flush=True)

    rel = stashed.relative_to(REPO_ROOT).as_posix()
    run(["git", "add", str(stashed)], cwd=str(REPO_ROOT))
    # Stage trader file too if it has uncommitted changes
    diff = subprocess.run(["git", "diff", "--quiet", "--", str(trader)], cwd=str(REPO_ROOT))
    if diff.returncode != 0:
        run(["git", "add", str(trader)], cwd=str(REPO_ROOT))

    msg = f"Round 3: {trader.stem} + IMC server log\n\nIMC log: {rel}"
    try:
        run(["git", "commit", "-m", msg], cwd=str(REPO_ROOT))
    except subprocess.CalledProcessError:
        print("Nothing new to commit (log was already tracked).", flush=True)
        return

    if not args.no_push:
        run(["git", "push", "origin", "master"], cwd=str(REPO_ROOT))


if __name__ == "__main__":
    main()
