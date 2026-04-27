"""
Sweep z_thresh × MC price-perturbation for z_take.py.

For each z_thresh in Z_GRID:
  1. Patch CFGS (set z_thresh on every product)
  2. Baseline: unperturbed PnL across all 4 days (round-3 d0 + round-4 d1-3)
  3. MC: N_SEEDS perturbations at noise_sd=1.0, all 4 days each
  4. Score: per-day mean+min on baseline, plus σ/μ across seeds for robustness

Pick the z_thresh with the best baseline mean+min that ALSO has σ/μ < 5%
(stable under perturbation).

Usage:  python scripts/mc_z_take_zsweep.py [n_seeds]
"""
import csv
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path
from statistics import mean, stdev

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
DATA_R3 = BT_DIR / "datasets" / "round3"
DATA_R4 = BT_DIR / "datasets" / "round4"
MC_R3 = BT_DIR / "datasets" / "round3_mc_zsweep"
MC_R4 = BT_DIR / "datasets" / "round4_mc_zsweep"
STRAT = REPO / "strategies" / "round4" / "z_take.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]
Z_GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
NOISE_SD = 1.0


def perturb_one(src_dir: Path, dst_dir: Path, rng: random.Random) -> None:
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    dst_dir.mkdir(parents=True)
    for f in src_dir.glob("prices_*.csv"):
        with open(f, newline="") as r, open(dst_dir / f.name, "w", newline="") as w:
            reader = csv.reader(r, delimiter=";")
            writer = csv.writer(w, delimiter=";")
            header = next(reader)
            writer.writerow(header)
            price_cols = [i for i, h in enumerate(header) if "price" in h.lower()]
            for row in reader:
                for i in price_cols:
                    if not row[i]:
                        continue
                    try:
                        v = float(row[i])
                        row[i] = str(int(round(v + rng.gauss(0, NOISE_SD))))
                    except ValueError:
                        pass
                writer.writerow(row)
    for f in src_dir.glob("trades_*.csv"):
        shutil.copy2(f, dst_dir / f.name)


def perturb_all(seed: int) -> None:
    # Same RNG state across both source dirs so noise is reproducible per-seed.
    rng = random.Random(seed)
    perturb_one(DATA_R3, MC_R3, rng)
    perturb_one(DATA_R4, MC_R4, rng)


def patch_z(src: str, z: float) -> str:
    return re.sub(r'("z_thresh"\s*:\s*)[\d.]+', rf'\g<1>{z}', src)


def run_one_day(dataset: str, day: int) -> float:
    r = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(STRAT), "--dataset", dataset,
         f"--day={day}", "--queue-penetration", "1.0",
         "--products", "summary", "--artifact-mode", "none"],
        capture_output=True, text=True, timeout=240, cwd=str(BT_DIR),
    )
    for line in r.stdout.splitlines():
        if line.startswith("D"):
            parts = line.split()
            if len(parts) >= 5:
                try:
                    return float(parts[4])
                except ValueError:
                    pass
    return 0.0


def run_4_days(use_mc: bool) -> list[float]:
    out = []
    for ds, d in DAY_KEYS:
        target = (str(MC_R3) if ds == "round3" else str(MC_R4)) if use_mc else ds
        out.append(run_one_day(target, d))
    return out


def main() -> None:
    n_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 6

    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.zsbak")
    backup.write_text(original)

    # baseline_pnl[z] = list of 4 per-day PnLs (unperturbed)
    # mc_pnl[z]       = list of n_seeds × list of 4 per-day PnLs
    baseline_pnl: dict[float, list[float]] = {}
    mc_pnl: dict[float, list[list[float]]] = {z: [] for z in Z_GRID}

    print(f"Sweeping z_thresh ∈ {Z_GRID}")
    print(f"MC: {n_seeds} seeds × noise_sd={NOISE_SD} × 4 days = "
          f"{n_seeds * 4} backtests per z (+ 4 baseline)")
    print()

    try:
        # 1. Baselines for every z
        print("=== BASELINES (unperturbed) ===")
        for z in Z_GRID:
            STRAT.write_text(patch_z(original, z))
            per_day = run_4_days(use_mc=False)
            baseline_pnl[z] = per_day
            mn = sum(per_day) / 4
            mi = min(per_day)
            print(f"  z={z:>4}  d0={per_day[0]:>9,.0f}  d1={per_day[1]:>9,.0f}  "
                  f"d2={per_day[2]:>9,.0f}  d3={per_day[3]:>9,.0f}  "
                  f"mean={mn:>9,.0f}  min={mi:>9,.0f}  m+m={mn+mi:>10,.0f}")

        # 2. MC runs per z
        print(f"\n=== MC (noise_sd={NOISE_SD}) ===")
        for seed in range(n_seeds):
            perturb_all(seed)
            print(f"\n  seed {seed}:")
            for z in Z_GRID:
                STRAT.write_text(patch_z(original, z))
                per_day = run_4_days(use_mc=True)
                mc_pnl[z].append(per_day)
                print(f"    z={z:>4}  d0={per_day[0]:>9,.0f}  d1={per_day[1]:>9,.0f}  "
                      f"d2={per_day[2]:>9,.0f}  d3={per_day[3]:>9,.0f}  "
                      f"mean={sum(per_day)/4:>9,.0f}  min={min(per_day):>9,.0f}",
                      flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)
        if MC_R3.exists():
            shutil.rmtree(MC_R3)
        if MC_R4.exists():
            shutil.rmtree(MC_R4)

    # 3. Summary
    print("\n" + "=" * 92)
    print(f"SUMMARY  ({n_seeds} seeds @ noise_sd={NOISE_SD})")
    print("=" * 92)
    print(f"{'z':>5}  {'base mean':>10}  {'base min':>10}  {'base m+m':>10}  "
          f"{'mc μ':>10}  {'mc σ':>9}  {'σ/μ':>7}  {'mc m+m μ':>10}  {'verdict':>9}")
    print("-" * 92)

    rows = []
    for z in Z_GRID:
        base_per_day = baseline_pnl[z]
        b_mean = sum(base_per_day) / 4
        b_min = min(base_per_day)
        b_score = b_mean + b_min

        seed_means = [sum(s) / 4 for s in mc_pnl[z]]
        seed_mins = [min(s) for s in mc_pnl[z]]
        seed_scores = [m + mi for m, mi in zip(seed_means, seed_mins)]
        mu = mean(seed_means)
        sd = stdev(seed_means) if len(seed_means) > 1 else 0.0
        cv = sd / max(1.0, abs(mu))
        verdict = "ROBUST" if cv < 0.02 else "MAYBE" if cv < 0.05 else "OVERFIT"
        rows.append((z, b_mean, b_min, b_score, mu, sd, cv, mean(seed_scores), verdict))

    for z, bm, bi, bs, mu, sd, cv, mcs, v in rows:
        print(f"{z:>5.2f}  {bm:>10,.0f}  {bi:>10,.0f}  {bs:>10,.0f}  "
              f"{mu:>10,.0f}  {sd:>9,.0f}  {100*cv:>5.2f}%  {mcs:>10,.0f}  {v:>9}")

    # Pick best by baseline mean+min, also report best MC mean+min
    best_base = max(rows, key=lambda r: r[3])
    best_mc = max(rows, key=lambda r: r[7])
    print()
    print(f"Best baseline mean+min: z={best_base[0]}  ({best_base[3]:,.0f}, σ/μ={100*best_base[6]:.2f}%, {best_base[8]})")
    print(f"Best MC mean+min:       z={best_mc[0]}  ({best_mc[7]:,.0f}, σ/μ={100*best_mc[6]:.2f}%, {best_mc[8]})")


if __name__ == "__main__":
    main()
