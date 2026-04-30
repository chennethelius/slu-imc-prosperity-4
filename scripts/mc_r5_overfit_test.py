#!/usr/bin/env python3
"""Round 5 overfitting stress test.

Tests three perturbations against v19's per-product trend-riding alpha:

  1. **noise**: add Gaussian noise to all prices (sd=1, 3, 5)
     - tests robustness to micro-noise
  2. **flip**: per-product, mirror the mid trajectory around its day-1 open price
     - simulates the random-walk going the OPPOSITE direction
     - directly tests whether trend-riding is an overfit
  3. **shuffle**: permute the per-product drift assignment among products
     - simulates "wrong product, wrong direction"

Reports baseline PnL vs perturbed PnL for each test.
If baseline >> mean perturbed → overfit signal is strong.
If baseline ~= mean perturbed → alpha is structural (good).
"""
import csv
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from collections import defaultdict


REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "datasets_extra" / "round5_data"


def parse_prices_csv(path):
    """Yield rows from prices CSV with parsed price columns."""
    with open(path, newline="") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        price_cols = [i for i, h in enumerate(header) if "price" in h.lower()]
        rows = list(reader)
    return header, rows, price_cols


def write_prices_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def stage(src, dst_root):
    """Copy src dataset into dst_root/round5/ for imc-p4-bt."""
    dst = dst_root / "round5"
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.glob("*.csv"):
        shutil.copy2(f, dst / f.name)
    return dst


def run_bt(strategy, data_root, day):
    """Run imc-p4-bt on the staged dataset, return final_pnl_total."""
    cmd = ["imc-p4-bt", str(strategy), f"5-{day}", "--data", str(data_root), "--no-out"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                       env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    if r.returncode != 0:
        print(f"  bt failed rc={r.returncode}: {r.stderr[:200]}", flush=True)
        return None
    for line in r.stdout.splitlines():
        if line.strip().startswith("final_pnl:"):
            return float(line.split(":")[1].strip().replace(",", ""))
    return None


def perturb_noise(src, dst, noise_sd, seed):
    rng = random.Random(seed)
    dst_root = dst
    target = stage(src, dst_root)
    for f in target.glob("prices_*.csv"):
        header, rows, price_cols = parse_prices_csv(f)
        for row in rows:
            for i in price_cols:
                if not row[i]:
                    continue
                try:
                    v = float(row[i])
                    row[i] = str(max(1, round(v + rng.gauss(0, noise_sd))))
                except ValueError:
                    pass
        write_prices_csv(f, header, rows)


def perturb_flip(src, dst):
    """Per-product, mirror mid trajectory around the day-1 open price.

    For each product, compute mid_open = first observed mid_price for that
    product. Then for each row of that product, replace prices p with
    (2 * mid_open - p), preserving the bid/ask structure but inverting drift.
    """
    target = stage(src, dst)
    for f in sorted(target.glob("prices_*.csv")):
        header, rows, price_cols = parse_prices_csv(f)
        prod_idx = header.index("product")
        opens = {}
        for row in rows:
            prod = row[prod_idx]
            if prod in opens:
                continue
            try:
                mid_idx = header.index("mid_price")
                opens[prod] = float(row[mid_idx])
            except (ValueError, KeyError):
                pass
        for row in rows:
            prod = row[prod_idx]
            if prod not in opens:
                continue
            anchor = opens[prod]
            for i in price_cols:
                if not row[i]:
                    continue
                try:
                    v = float(row[i])
                    row[i] = str(max(1, round(2 * anchor - v)))
                except ValueError:
                    pass
        write_prices_csv(f, header, rows)


def perturb_shuffle(src, dst, seed):
    """Per-product, swap mid trajectories with another random product.

    Builds product->trajectory map, shuffles assignment, writes back.
    Bids/asks scale with mid since mirroring won't preserve cross-product
    relationships, but for our trend-riding test the key is whether the
    direction matches our target_pos.
    """
    rng = random.Random(seed)
    target = stage(src, dst)
    for f in sorted(target.glob("prices_*.csv")):
        header, rows, price_cols = parse_prices_csv(f)
        prod_idx = header.index("product")
        mid_idx = header.index("mid_price")
        # Group rows by product, preserve order
        prod_rows = defaultdict(list)
        for row in rows:
            prod_rows[row[prod_idx]].append(row)
        products = list(prod_rows.keys())
        shuffled = products.copy()
        rng.shuffle(shuffled)
        # Replace each product's trajectory with the shuffled-product's
        # delta-from-open trajectory, preserving the original product's open.
        new_rows = []
        for orig in products:
            src_prod = shuffled[products.index(orig)]
            orig_seq = prod_rows[orig]
            src_seq = prod_rows[src_prod]
            # Open prices
            try:
                orig_open = float(orig_seq[0][mid_idx])
                src_open = float(src_seq[0][mid_idx])
            except (ValueError, IndexError):
                new_rows.extend(orig_seq)
                continue
            offset = orig_open - src_open
            n = min(len(orig_seq), len(src_seq))
            for i in range(n):
                row = list(orig_seq[i])  # copy
                src_row = src_seq[i]
                for ci in price_cols:
                    if not src_row[ci]:
                        row[ci] = src_row[ci]
                        continue
                    try:
                        v = float(src_row[ci])
                        row[ci] = str(max(1, round(v + offset)))
                    except ValueError:
                        row[ci] = src_row[ci]
                new_rows.append(row)
            # Tail rows from orig_seq if longer
            for i in range(n, len(orig_seq)):
                new_rows.append(orig_seq[i])
        # Sort new_rows by timestamp to preserve interleaving
        ts_idx = header.index("timestamp")
        try:
            new_rows.sort(key=lambda r: (int(r[ts_idx]), r[prod_idx]))
        except (ValueError, KeyError):
            pass
        write_prices_csv(f, header, new_rows)


def run_perturbation(strategy, day, name, perturb_fn):
    with tempfile.TemporaryDirectory(prefix=f"r5mc-{name}-") as td:
        td = Path(td)
        perturb_fn(SRC, td)
        return run_bt(strategy, td, day)


def main():
    if len(sys.argv) < 2:
        print("usage: mc_r5_overfit_test.py <strategy.py> [day]")
        sys.exit(1)
    strategy = Path(sys.argv[1])
    day = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    print(f"\n=== Round 5 overfitting stress test for {strategy.name} on d{day} ===\n")

    # Baseline
    with tempfile.TemporaryDirectory(prefix="r5mc-base-") as td:
        td = Path(td)
        stage(SRC, td)
        baseline = run_bt(strategy, td, day)
    print(f"Baseline (untouched data):  PnL = {baseline:>10,.0f}\n", flush=True)

    # Noise
    print("--- Test 1: Gaussian price noise (robustness check) ---", flush=True)
    for sd in [1, 3]:
        pnls = []
        for seed in range(2):
            with tempfile.TemporaryDirectory(prefix=f"r5mc-noise-{sd}-{seed}-") as td:
                td = Path(td)
                perturb_noise(SRC, td, sd, seed)
                p = run_bt(strategy, td, day)
            if p is not None:
                pnls.append(p)
                print(f"    seed={seed}  pnl={p:>10,.0f}", flush=True)
        if pnls:
            mu = sum(pnls) / len(pnls)
            ratio = mu / baseline if baseline else 0
            print(f"  noise_sd={sd}  mean={mu:>10,.0f}  ratio={ratio:.2%}  pnls={[round(p) for p in pnls]}", flush=True)

    # Flip — the key overfitting test
    print("\n--- Test 2: Mirror per-product mid trajectory (TREND REVERSAL) ---")
    p = run_perturbation(strategy, day, "flip", perturb_flip)
    if p is not None:
        ratio = p / baseline if baseline else 0
        delta = p - baseline
        print(f"  flipped     PnL = {p:>10,.0f}  ratio={ratio:.2%}  delta={delta:+,.0f}", flush=True)
        print(f"  interpretation: if alpha is overfit to historical drift direction,", flush=True)
        print(f"  flipped PnL should drop sharply (or go negative). If alpha is structural,", flush=True)
        print(f"  flipped PnL should remain similar.", flush=True)

    # Shuffle
    print("\n--- Test 3: Shuffle per-product trajectories (WRONG TARGETS) ---", flush=True)
    pnls = []
    for seed in range(2):
        p = run_perturbation(strategy, day, f"shuf-{seed}", lambda s, d, se=seed: perturb_shuffle(s, d, se))
        if p is not None:
            pnls.append(p)
            print(f"    seed={seed}  pnl={p:>10,.0f}", flush=True)
    if pnls:
        mu = sum(pnls) / len(pnls)
        ratio = mu / baseline if baseline else 0
        print(f"  shuffled    mean={mu:>10,.0f}  ratio={ratio:.2%}  pnls={[round(p) for p in pnls]}", flush=True)

    print(f"\n=== Summary ===")
    print(f"Baseline: {baseline:>10,.0f}")
    print(f"If noise_sd=5 ratio < 50%: micro-overfit to specific paths (bad)")
    print(f"If flip ratio < 0% (negative): heavy overfit to drift direction (bad)")
    print(f"If shuffle ratio < 30%: target_pos assignment is alpha-bearing (mixed)")
    print(f"If all > 70%: structural alpha, robust to perturbations (good)")


if __name__ == "__main__":
    main()
