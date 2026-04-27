"""
Monte Carlo overfit test for strategies/round4/z_take.py.

Perturbs every price column in the round-4 CSVs with Gaussian noise, runs
the strategy unchanged against each perturbed dataset, and reports the
PnL distribution. If PnL is tight around the baseline, the static means
aren't fitted to noise. If it scatters or collapses, the strategy is
brittle to small market shifts.

Several noise levels are tested in sequence so you can see how the
strategy degrades as the price grid is increasingly disturbed.

Usage:  python scripts/mc_z_take_overfit.py [n_seeds]
"""
import csv
import random
import shutil
import subprocess
import sys
from pathlib import Path
from statistics import mean, stdev

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
SRC_DATA = BT_DIR / "datasets" / "round4"
MC_DATA = BT_DIR / "datasets" / "round4_mc_ztake"
STRAT = REPO / "strategies" / "round4" / "z_take.py"
DAYS = (1, 2, 3)
NOISE_SDS = (0.5, 1.0, 2.0)  # in price units


def perturb_prices(noise_sd: float, seed: int) -> None:
    rng = random.Random(seed)
    if MC_DATA.exists():
        shutil.rmtree(MC_DATA)
    MC_DATA.mkdir(parents=True)
    for f in SRC_DATA.glob("prices_*.csv"):
        with open(f, newline="") as r, open(MC_DATA / f.name, "w", newline="") as w:
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
                        row[i] = str(int(round(v + rng.gauss(0, noise_sd))))
                    except ValueError:
                        pass
                writer.writerow(row)
    for f in SRC_DATA.glob("trades_*.csv"):
        shutil.copy2(f, MC_DATA / f.name)


def run_one_day(day: int, dataset: str) -> float:
    r = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(STRAT), "--dataset", dataset,
         f"--day={day}", "--queue-penetration", "1.0",
         "--products", "summary", "--artifact-mode", "none"],
        capture_output=True, text=True, timeout=240, cwd=str(BT_DIR),
    )
    if r.returncode != 0:
        print(f"    [warn] backtest failed: {r.stderr.strip()[:200]}", flush=True)
    for line in r.stdout.splitlines():
        if line.startswith("D+"):
            parts = line.split()
            if len(parts) >= 5:
                try:
                    return float(parts[4])
                except ValueError:
                    pass
    return 0.0


def baseline() -> dict[int, float]:
    return {d: run_one_day(d, str(SRC_DATA)) for d in DAYS}


def main() -> None:
    n_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 8

    print("=== Baseline (unperturbed) ===")
    base = baseline()
    for d in DAYS:
        print(f"  day {d}: {base[d]:>10,.0f}")
    base_mean = mean(base.values())
    base_min = min(base.values())
    base_score = base_mean + base_min
    print(f"  per-day mean = {base_mean:>10,.0f}  min = {base_min:>10,.0f}  "
          f"mean+min = {base_score:>10,.0f}")

    print(f"\nRunning MC: {n_seeds} seeds × {len(NOISE_SDS)} noise levels × {len(DAYS)} days "
          f"= {n_seeds * len(NOISE_SDS) * len(DAYS)} backtests")

    # results[noise_sd] = list of per-seed [day1, day2, day3] tuples
    results: dict[float, list[list[float]]] = {n: [] for n in NOISE_SDS}

    try:
        for noise in NOISE_SDS:
            print(f"\n--- noise_sd = {noise} ---")
            for seed in range(n_seeds):
                perturb_prices(noise, seed)
                per_day = [run_one_day(d, str(MC_DATA)) for d in DAYS]
                results[noise].append(per_day)
                d1, d2, d3 = per_day
                print(f"  seed {seed}: day1={d1:>9,.0f}  day2={d2:>9,.0f}  "
                      f"day3={d3:>9,.0f}  mean={sum(per_day)/3:>9,.0f}  "
                      f"min={min(per_day):>9,.0f}", flush=True)
    finally:
        if MC_DATA.exists():
            shutil.rmtree(MC_DATA)

    print("\n" + "=" * 78)
    print(f"SUMMARY  ({n_seeds} seeds per noise level)")
    print("=" * 78)
    print(f"{'noise':>6}  {'mean μ ± σ':>22}  {'min μ':>10}  "
          f"{'mean+min':>11}  {'Δ vs base':>12}")
    print("-" * 78)
    print(f"{'base':>6}  {base_mean:>14,.0f}{'':>8}  {base_min:>10,.0f}  "
          f"{base_score:>11,.0f}  {'':>12}")

    for noise in NOISE_SDS:
        seed_means = [sum(s) / 3 for s in results[noise]]
        seed_mins = [min(s) for s in results[noise]]
        mu = mean(seed_means)
        sd = stdev(seed_means) if len(seed_means) > 1 else 0.0
        mu_min = mean(seed_mins)
        score = mu + mu_min
        gap = score - base_score
        print(f"{noise:>6.1f}  {mu:>10,.0f} ± {sd:>7,.0f}  {mu_min:>10,.0f}  "
              f"{score:>11,.0f}  {gap:>+12,.0f}")

    print("\nRobustness rule of thumb (per-noise-level):")
    print("  σ/μ < 2%  →  ROBUST   (PnL stable across perturbations)")
    print("  σ/μ < 5%  →  MAYBE    (some sensitivity)")
    print("  σ/μ ≥ 5%  →  OVERFIT  (PnL chasing specific price levels)")
    print()
    for noise in NOISE_SDS:
        seed_means = [sum(s) / 3 for s in results[noise]]
        mu = mean(seed_means)
        sd = stdev(seed_means) if len(seed_means) > 1 else 0.0
        cv = sd / max(1.0, abs(mu))
        verdict = ("ROBUST" if cv < 0.02
                   else "MAYBE" if cv < 0.05 else "OVERFIT-RISK")
        print(f"  noise_sd={noise:>4.1f}  σ/μ = {100*cv:>5.2f}%  →  {verdict}")


if __name__ == "__main__":
    main()
