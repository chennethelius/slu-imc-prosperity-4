#!/usr/bin/env python3
"""Collect backtest results into a manifest for the dashboard."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    strategies_str = os.environ.get("STRATEGIES", "")
    author = os.environ.get("AUTHOR", "unknown")
    commit = os.environ.get("COMMIT", "?")[:7]
    timestamp = os.environ.get("TIMESTAMP", "")
    repo_root = Path(os.environ.get("REPO_ROOT", ".")).resolve()
    backtester_dir = repo_root / "backtester"
    backtester_bin = backtester_dir / "target" / "release" / "rust_backtester"
    results_dir = repo_root / "backtest-results"
    results_dir.mkdir(exist_ok=True)

    # Stage extra datasets (e.g. round1_slow) from repo root into backtester
    extra_ds_root = repo_root / "datasets_extra"
    ds_root = backtester_dir / "datasets"
    if extra_ds_root.is_dir():
        for extra in sorted(extra_ds_root.iterdir()):
            if not extra.is_dir():
                continue
            target = ds_root / extra.name
            if not target.exists():
                shutil.copytree(extra, target)
                print(f"Staged extra dataset: {extra.name}")

    # Find available datasets
    datasets = []
    for d in sorted(ds_root.iterdir()):
        if d.is_dir() and any(d.iterdir()):
            datasets.append(d.name)
    print(f"Available datasets: {datasets}")

    strategies = [s.strip() for s in strategies_str.strip().splitlines() if s.strip()]
    if not strategies:
        print("No strategies to run")
        sys.exit(0)

    manifest = []

    for strategy_rel in strategies:
        strategy_path = repo_root / strategy_rel
        if not strategy_path.exists():
            print(f"Skipping missing: {strategy_rel}")
            continue
        strat_name = strategy_path.stem
        if strat_name == "template":
            print(f"Skipping template: {strategy_rel}")
            continue

        # Match strategy to its round's dataset
        # strategies/round1/foo.py → only run against round1
        # strategies/round0/foo.py or strategies/tutorial/foo.py → tutorial
        # strategies/foo.py (no round folder) → all available datasets
        parent = strategy_path.parent.name
        if parent.startswith("round"):
            round_num = parent.replace("round", "")
            if round_num == "0":
                target_datasets = [d for d in datasets if "tutorial" in d]
            else:
                target_datasets = [d for d in datasets if d == parent or d.startswith(f"{parent}_")]
        elif parent == "tutorial":
            target_datasets = [d for d in datasets if "tutorial" in d]
        else:
            target_datasets = datasets

        if not target_datasets:
            print(f"No matching dataset for {strategy_rel} (round={parent}), skipping")
            continue

        for ds in target_datasets:
            print(f"=== {strat_name} vs {ds} ===")

            # Clean previous runs
            runs_dir = backtester_dir / "runs"
            if runs_dir.exists():
                for old in runs_dir.glob("backtest-*"):
                    shutil.rmtree(old, ignore_errors=True)

            # Run backtester
            try:
                result = subprocess.run(
                    [
                        str(backtester_bin),
                        "--trader", str(strategy_path),
                        "--dataset", ds,
                        "--queue-penetration", "0.0",
                        "--persist",
                        "--artifact-mode", "full",
                    ],
                    cwd=str(backtester_dir),
                    capture_output=True, text=True, timeout=120,
                )
                print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
                if result.stderr:
                    print(result.stderr[-300:])
            except Exception as e:
                print(f"Error running backtester: {e}")
                continue

            # Collect results from each run the backtester created
            if not runs_dir.exists():
                continue
            for brun in sorted(runs_dir.glob("backtest-*"), reverse=True):
                metrics_file = brun / "metrics.json"
                if not metrics_file.exists():
                    continue
                # Skip bundle dirs (no submission.log)
                if not (brun / "submission.log").exists():
                    continue

                try:
                    metrics = json.loads(metrics_file.read_text())
                except (json.JSONDecodeError, OSError):
                    continue

                sub_id = f"{author}_{strat_name}_{ds}_{brun.name}"
                sub_dir = results_dir / sub_id
                sub_dir.mkdir(exist_ok=True)

                # Copy artifacts (skip large activity.csv)
                for fname in ["metrics.json", "pnl_by_product.csv", "trades.csv"]:
                    src = brun / fname
                    if src.exists():
                        shutil.copy2(src, sub_dir / fname)

                # Save strategy source code with the result
                shutil.copy2(strategy_path, sub_dir / "strategy.py")

                manifest.append({
                    "id": sub_id,
                    "author": author,
                    "strategy": strat_name,
                    "dataset": Path(metrics.get("dataset_path", "?")).name,
                    "day": metrics.get("day", "?"),
                    "pnl": metrics.get("final_pnl_total", 0),
                    "pnl_by_product": metrics.get("final_pnl_by_product", {}),
                    "trades": metrics.get("own_trade_count", 0),
                    "ticks": metrics.get("tick_count", 0),
                    "timestamp": timestamp,
                    "commit": commit,
                })
                print(f"  Collected: {sub_id} PnL={metrics.get('final_pnl_total', 0)}")

    # Write manifest
    manifest_path = results_dir / "new_results.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nManifest: {len(manifest)} results written to {manifest_path}")


if __name__ == "__main__":
    main()
