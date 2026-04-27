#!/usr/bin/env python3
"""Monte-carlo robustness test: perturb prices in the round4 dataset by
small Gaussian noise and run the wrapper for v62 vs v85 across N seeds.
Checks PnL distribution to detect overfit (high variance = overfit).

Usage: python scripts/mc_perturb_test.py <strategy.py> <dataset> <n_runs>
"""
import csv
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def perturb_prices(src_dir: Path, dst_dir: Path, noise_sd: float, seed: int):
    rng = random.Random(seed)
    dst_dir.mkdir(parents=True, exist_ok=True)
    for f in src_dir.glob("prices_*.csv"):
        with open(f, newline="") as r, open(dst_dir / f.name, "w", newline="") as w:
            reader = csv.reader(r, delimiter=";")
            writer = csv.writer(w, delimiter=";")
            header = next(reader)
            writer.writerow(header)
            # Identify price columns (anything with "price" in header except product/timestamp)
            price_cols = [i for i, h in enumerate(header) if "price" in h.lower()]
            for row in reader:
                for i in price_cols:
                    if not row[i] or row[i] == "":
                        continue
                    try:
                        v = float(row[i])
                        # Add Gaussian noise; round to nearest integer (price grid)
                        v_new = round(v + rng.gauss(0, noise_sd))
                        row[i] = str(v_new) if v_new != int(v_new) else str(int(v_new))
                    except ValueError:
                        pass
                writer.writerow(row)
    # Also copy trades CSVs unmodified
    for f in src_dir.glob("trades_*.csv"):
        shutil.copy2(f, dst_dir / f.name)


def run_wrapper(strategy: Path, perturbed_dir: Path, day: int, output_root: Path):
    cmd = [
        sys.executable, "scripts/imcbt_wrapper.py",
        "--trader", str(strategy),
        "--dataset", "round4_data",
        f"--day={day}",
        "--persist", "--artifact-mode", "full",
        "--output-root", str(output_root),
    ]
    # Stage perturbed data into the location the wrapper expects
    repo_root = Path(__file__).resolve().parent.parent
    target = repo_root / "datasets_extra" / "round4_data_mc"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(perturbed_dir, target)
    # Run wrapper with the override
    cmd[cmd.index("--dataset")+1] = "round4_data_mc"
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    # Cleanup
    shutil.rmtree(target, ignore_errors=True)
    if r.returncode != 0:
        return None
    # Parse final PnL from metrics.json in output_root
    runs = sorted(output_root.glob("backtest-*"))
    if not runs:
        return None
    try:
        m = json.loads((runs[-1] / "metrics.json").read_text())
        return m.get("final_pnl_total", 0.0)
    except Exception:
        return None


def main():
    if len(sys.argv) < 3:
        print("usage: python mc_perturb_test.py <strategy.py> <day> [n_runs] [noise_sd]")
        sys.exit(1)
    strategy = Path(sys.argv[1])
    day = int(sys.argv[2])
    n_runs = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    noise_sd = float(sys.argv[4]) if len(sys.argv) > 4 else 0.5

    repo_root = Path(__file__).resolve().parent.parent
    src = repo_root / "datasets_extra" / "round4_data"

    pnls = []
    for seed in range(n_runs):
        with tempfile.TemporaryDirectory(prefix=f"mc-{seed}-") as td:
            td = Path(td)
            perturb_prices(src, td, noise_sd, seed)
            with tempfile.TemporaryDirectory(prefix=f"mc-out-{seed}-") as out:
                pnl = run_wrapper(strategy, td, day, Path(out))
        if pnl is not None:
            pnls.append(pnl)
            print(f"  seed={seed}  pnl={pnl:>10,.0f}", flush=True)
    if not pnls:
        print("no successful runs")
        return
    mu = sum(pnls) / len(pnls)
    var = sum((x - mu) ** 2 for x in pnls) / max(1, len(pnls) - 1)
    sd = var ** 0.5
    print(f"\n{strategy.name} on d{day}, noise_sd={noise_sd}, n={len(pnls)}")
    print(f"  mean={mu:>12,.0f}  std={sd:>10,.0f}  min={min(pnls):>10,.0f}  max={max(pnls):>10,.0f}")


if __name__ == "__main__":
    main()
