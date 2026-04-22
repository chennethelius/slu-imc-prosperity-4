#!/usr/bin/env python3
"""Collect backtest results into a manifest for the dashboard."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def resolve_target_datasets(strategy_path: Path, datasets: list[str]) -> list[str]:
    """Match a strategy file to its round's dataset(s)."""
    parent = strategy_path.parent.name
    if parent.startswith("round"):
        round_num = parent.replace("round", "")
        if round_num == "0":
            return [d for d in datasets if "tutorial" in d]
        return [d for d in datasets if d == parent or d.startswith(f"{parent}_")]
    if parent == "tutorial":
        return [d for d in datasets if "tutorial" in d]
    return datasets


def run_pair(strategy_path: Path, ds: str, backtester_bin: Path, backtester_dir: Path,
             author: str, commit: str, timestamp: str, results_dir: Path) -> list[dict]:
    """Run one (strategy, dataset) pair in its own output dir. Returns manifest entries."""
    strat_name = strategy_path.stem
    out_root = Path(tempfile.mkdtemp(prefix=f"bt-{strat_name}-{ds}-", dir=backtester_dir / "runs"))
    try:
        print(f"=== {strat_name} vs {ds} ===", flush=True)
        try:
            result = subprocess.run(
                [
                    str(backtester_bin),
                    "--trader", str(strategy_path),
                    "--dataset", ds,
                    "--persist",
                    "--artifact-mode", "full",
                    "--output-root", str(out_root),
                ],
                cwd=str(backtester_dir),
                capture_output=True, text=True, timeout=240,
            )
            tail = result.stdout[-400:] if len(result.stdout) > 400 else result.stdout
            print(f"[{strat_name}/{ds}]\n{tail}", flush=True)
            if result.stderr:
                print(result.stderr[-200:], flush=True)
        except Exception as e:
            print(f"Error running {strat_name}/{ds}: {e}", flush=True)
            return []

        entries = []
        for brun in sorted(out_root.glob("backtest-*"), reverse=True):
            metrics_file = brun / "metrics.json"
            if not metrics_file.exists() or not (brun / "submission.log").exists():
                continue
            try:
                metrics = json.loads(metrics_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue

            sub_id = f"{author}_{strat_name}_{ds}_{brun.name}"
            sub_dir = results_dir / sub_id
            sub_dir.mkdir(exist_ok=True)

            for fname in ["metrics.json", "pnl_by_product.csv", "trades.csv", "activity.csv"]:
                src = brun / fname
                if src.exists():
                    shutil.copy2(src, sub_dir / fname)
            shutil.copy2(strategy_path, sub_dir / "strategy.py")

            entries.append({
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
            print(f"  Collected: {sub_id} PnL={metrics.get('final_pnl_total', 0)}", flush=True)
        return entries
    finally:
        shutil.rmtree(out_root, ignore_errors=True)


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
    (backtester_dir / "runs").mkdir(exist_ok=True)

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

    datasets = []
    for d in sorted(ds_root.iterdir()):
        if d.is_dir() and any(d.iterdir()):
            datasets.append(d.name)
    print(f"Available datasets: {datasets}")

    strategies = [s.strip() for s in strategies_str.strip().splitlines() if s.strip()]
    if not strategies:
        print("No strategies to run")
        sys.exit(0)

    # First-author-wins ownership: if another author already has entries for a
    # strategy on the live dashboard, skip it so we don't overwrite or duplicate
    # their work. Only the original owner can keep refreshing that strategy.
    strategy_owners: dict[str, str] = {}
    existing_manifest_path = os.environ.get("EXISTING_MANIFEST")
    if existing_manifest_path and os.path.exists(existing_manifest_path):
        try:
            existing = json.loads(Path(existing_manifest_path).read_text())
        except (json.JSONDecodeError, OSError):
            existing = []
        for r in existing:
            s, a = r.get("strategy"), r.get("author")
            if s and a and s not in strategy_owners:
                strategy_owners[s] = a
        print(f"Loaded {len(strategy_owners)} strategy owners from existing manifest")

    # Build the full work list of (strategy, dataset) pairs
    pairs = []
    skipped_owned = []
    for strategy_rel in strategies:
        strategy_path = repo_root / strategy_rel
        if not strategy_path.exists():
            print(f"Skipping missing: {strategy_rel}")
            continue
        strat_name = strategy_path.stem
        if strat_name == "template":
            print(f"Skipping template: {strategy_rel}")
            continue
        owner = strategy_owners.get(strat_name)
        if owner and owner != author:
            skipped_owned.append((strat_name, owner))
            continue
        target_datasets = resolve_target_datasets(strategy_path, datasets)
        if not target_datasets:
            print(f"No matching dataset for {strategy_rel}, skipping")
            continue
        for ds in target_datasets:
            pairs.append((strategy_path, ds))

    if skipped_owned:
        print(f"\nSkipped {len(skipped_owned)} strategies owned by other authors:")
        for s, o in skipped_owned:
            print(f"  {s} (owner: {o})")

    # Parallelize. The backtester is CPU-bound; one worker per vCPU works well.
    workers = max(1, min(len(pairs), os.cpu_count() or 2))
    print(f"\nRunning {len(pairs)} backtests across {workers} workers\n", flush=True)

    manifest = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(run_pair, sp, ds, backtester_bin, backtester_dir,
                             author, commit, timestamp, results_dir) for sp, ds in pairs]
        for f in as_completed(futures):
            try:
                manifest.extend(f.result())
            except Exception as e:
                print(f"Worker failed: {e}", flush=True)

    manifest_path = results_dir / "new_results.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nManifest: {len(manifest)} results written to {manifest_path}")


if __name__ == "__main__":
    main()
