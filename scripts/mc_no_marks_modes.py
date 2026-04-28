"""Monte-carlo overfit test for no_marks aggressive-MR mean source.

For each MC seed, perturbs round4 prices by small Gaussian noise, then runs
the cargo backtester on the perturbed dataset for three configurations of
the aggressive-MR layer:
  A) HP=static, VFE=static
  B) HP=ema,    VFE=ema
  C) HP=static, VFE=ema   (the candidate winner from the unperturbed run)

Reports per-day HP and VFE PnL for each (seed, mode), then per-mode summary
(mean, std, min) computed as **mean across the 3 days** within each seed,
averaged over seeds. High std relative to per-mode gap = overfit risk.

Usage: python scripts/mc_no_marks_modes.py [n_seeds] [noise_sd]
"""
import csv
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path
from statistics import mean, stdev

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
SRC_DATA = BT_DIR / "datasets" / "round4"
MC_DATA = BT_DIR / "datasets" / "round4_mc_modes"
STRAT = REPO / "strategies" / "round4" / "no_marks.py"

PRODUCTS = ("HYDROGEL_PACK", "VELVETFRUIT_EXTRACT")
DAYS = ("1", "2", "3")
MODES = {
    "all_static": ("static", "static"),
    "all_ema":    ("ema",    "ema"),
    "split":      ("static", "ema"),
}


def perturb_prices(noise_sd: float, seed: int):
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


def patch_modes(src: str, hp_mode: str, vfe_mode: str) -> str:
    """Set HP_CFG and VFE_CFG aggr_mean_mode independently."""
    out = re.sub(
        r'(HP_CFG\s*=\s*\{[^}]*?"aggr_mean_mode"\s*:\s*)"\w+"',
        rf'\1"{hp_mode}"', src, count=1, flags=re.S,
    )
    out = re.sub(
        r'(VFE_CFG\s*=\s*\{[^}]*?"aggr_mean_mode"\s*:\s*)"\w+"',
        rf'\1"{vfe_mode}"', out, count=1, flags=re.S,
    )
    return out


def run_one_day(day: str) -> dict[str, float]:
    """Run backtester on the perturbed dataset for one day, return per-product PnL."""
    r = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(STRAT), "--dataset", str(MC_DATA),
         f"--day={day}", "--queue-penetration", "1.0",
         "--products", "summary", "--artifact-mode", "none"],
        capture_output=True, text=True, timeout=180, cwd=str(BT_DIR),
    )
    if r.returncode != 0:
        print(f"    [warn] backtester failed for day={day}: {r.stderr.strip()[:200]}",
              flush=True)
    out = {p: 0.0 for p in PRODUCTS}
    for line in r.stdout.splitlines():
        for p in PRODUCTS:
            if line.startswith(p):
                # PRODUCT line is summary across all days, so we want
                # single-day rows. With --day, only one day column is shown.
                # Format: "HYDROGEL_PACK    52562.00   52562.00" (D+N, TOTAL)
                parts = line.split()
                try:
                    out[p] = float(parts[1])
                except (IndexError, ValueError):
                    pass
                break
    return out


def main():
    n_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    noise_sd = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5

    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.mcbak")
    backup.write_text(original)

    # results[mode][product] = list of per-seed (mean across days) PnL
    results = {m: {p: [] for p in PRODUCTS} for m in MODES}
    # detail[mode][product][day] = list of per-seed PnL
    detail = {m: {p: {d: [] for d in DAYS} for p in PRODUCTS} for m in MODES}

    try:
        for seed in range(n_seeds):
            print(f"\n=== seed {seed} (noise_sd={noise_sd}) ===", flush=True)
            perturb_prices(noise_sd, seed)
            for mname, (hp_m, vfe_m) in MODES.items():
                STRAT.write_text(patch_modes(original, hp_m, vfe_m))
                per_day = {p: [] for p in PRODUCTS}
                for d in DAYS:
                    pnl = run_one_day(d)
                    for p in PRODUCTS:
                        per_day[p].append(pnl[p])
                        detail[mname][p][d].append(pnl[p])
                for p in PRODUCTS:
                    m = mean(per_day[p])
                    results[mname][p].append(m)
                    print(f"  {mname:10s} {p:22s}  D1={per_day[p][0]:>9,.0f}  "
                          f"D2={per_day[p][1]:>9,.0f}  D3={per_day[p][2]:>9,.0f}  "
                          f"day-mean={m:>9,.0f}", flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)
        if MC_DATA.exists():
            shutil.rmtree(MC_DATA)

    print("\n" + "=" * 70)
    print(f"SUMMARY ({n_seeds} seeds, noise_sd={noise_sd})")
    print("=" * 70)
    for p in PRODUCTS:
        print(f"\n{p} (per-seed mean across {len(DAYS)} days, then aggregated):")
        for mname in MODES:
            xs = results[mname][p]
            mu = mean(xs)
            sd = stdev(xs) if len(xs) > 1 else 0.0
            print(f"  {mname:10s}  mean={mu:>10,.0f}  std={sd:>8,.0f}  "
                  f"min={min(xs):>10,.0f}  max={max(xs):>10,.0f}")
        # Pairwise win rates per seed (which mode's day-mean was higher?)
        print(f"  per-seed wins (day-mean):")
        wins = {m: 0 for m in MODES}
        for i in range(n_seeds):
            ranked = sorted(MODES, key=lambda m: results[m][p][i], reverse=True)
            wins[ranked[0]] += 1
        for m in MODES:
            print(f"    {m:10s} {wins[m]}/{n_seeds}")

    # Combined HP+VFE day-mean per seed
    print("\nCOMBINED HP+VFE (per-seed day-mean of HP+VFE):")
    for mname in MODES:
        xs = [results[mname]["HYDROGEL_PACK"][i] + results[mname]["VELVETFRUIT_EXTRACT"][i]
              for i in range(n_seeds)]
        mu = mean(xs)
        sd = stdev(xs) if len(xs) > 1 else 0.0
        print(f"  {mname:10s}  mean={mu:>10,.0f}  std={sd:>8,.0f}  "
              f"min={min(xs):>10,.0f}  max={max(xs):>10,.0f}  "
              f"std/mean={100 * sd / mu:.2f}%")


if __name__ == "__main__":
    main()
