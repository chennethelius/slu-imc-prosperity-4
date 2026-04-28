"""Sweep aggr_z_thresh × aggr_max_take for the aggressive MR layer.

For each (z, take) combo, runs the same MC ablation harness, comparing
against baseline (aggr=off). Reports per-mode score (day-mean + day-min,
averaged over seeds) and the gap vs baseline.

Usage: python scripts/mc_no_marks_sweep.py [n_seeds]
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
SRC_DATA = BT_DIR / "datasets" / "round4"
MC_DATA = BT_DIR / "datasets" / "round4_mc_sweep"
STRAT = REPO / "strategies" / "round4" / "no_marks.py"

PRODUCTS = ("HYDROGEL_PACK", "VELVETFRUIT_EXTRACT")
DAYS = ("1", "2", "3")

Z_GRID = [1.5, 2.0, 2.5, 3.0]
TAKE_GRID = [30, 60, 90]   # applied as both aggr_max_take and *_end (no decay)
SD_SOURCES = ["ewma", "static"]   # ewma = adaptive (current); static = stdev_init


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


def patch_one(src: str, cfg_name: str, key: str, value) -> str:
    val_str = str(value) if not isinstance(value, str) else value
    pat = rf'({cfg_name}\s*=\s*\{{[^}}]*?"{re.escape(key)}"\s*:\s*)([^,}}\n]+)'
    return re.sub(pat, rf'\g<1>{val_str}', src, count=1, flags=re.S)


def patch_config(src: str, overrides: dict) -> str:
    out = src
    for key, val in overrides.items():
        out = patch_one(out, "HP_CFG", key, val)
        out = patch_one(out, "VFE_CFG", key, val)
    return out


def run_one_day(day: str) -> float:
    r = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(STRAT), "--dataset", str(MC_DATA),
         f"--day={day}", "--queue-penetration", "1.0",
         "--products", "summary", "--artifact-mode", "none"],
        capture_output=True, text=True, timeout=180, cwd=str(BT_DIR),
    )
    if r.returncode != 0:
        print(f"    [warn] failed day={day}: {r.stderr.strip()[:200]}", flush=True)
    combined = 0.0
    for line in r.stdout.splitlines():
        for p in PRODUCTS:
            if line.startswith(p):
                parts = line.split()
                try:
                    combined += float(parts[1])
                except (IndexError, ValueError):
                    pass
                break
    return combined


def main():
    n_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    noise_sd = 0.5

    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.mcbak")
    backup.write_text(original)

    # Build configs: baseline + Z×TAKE×SD combos
    configs = {"baseline": {"aggr_mean_mode": '"off"'}}
    for sd_src in SD_SOURCES:
        for z in Z_GRID:
            for tk in TAKE_GRID:
                name = f"{sd_src[:3]}_z{z:.1f}_t{tk}"
                configs[name] = {
                    "aggr_mean_mode": '"static"',
                    "aggr_sd_source": f'"{sd_src}"',
                    "aggr_z_thresh": z,
                    "aggr_max_take": tk,
                    "aggr_max_take_end": tk,
                }

    # results[cfg][seed] = list of 3 per-day combined HP+VFE PnL
    results = {c: [] for c in configs}

    try:
        for seed in range(n_seeds):
            print(f"\n=== seed {seed} ===", flush=True)
            perturb_prices(noise_sd, seed)
            for cname, overrides in configs.items():
                STRAT.write_text(patch_config(original, overrides))
                per_day = [run_one_day(d) for d in DAYS]
                results[cname].append(per_day)
                print(f"  {cname:14s}  {per_day[0]:>8,.0f} {per_day[1]:>8,.0f} {per_day[2]:>8,.0f}  "
                      f"mean={sum(per_day)/3:>8,.0f}", flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)
        if MC_DATA.exists():
            shutil.rmtree(MC_DATA)

    print("\n" + "=" * 80)
    print(f"SWEEP SUMMARY  ({n_seeds} seeds)  HP+VFE combined per-day score")
    print("=" * 80)
    print(f"{'config':<14s}  {'mean (μ ± σ)':>20s}  {'min (μ)':>9s}  "
          f"{'mean+min':>10s}  {'Δ vs baseline':>14s}")
    print("-" * 80)

    base_seeds = results["baseline"]
    base_means = [sum(s) / 3 for s in base_seeds]
    base_mins = [min(s) for s in base_seeds]
    base_score = mean(base_means) + mean(base_mins)
    base_sigma = stdev(base_means) if len(base_means) > 1 else 0.0

    rows = []
    for cname in configs:
        seeds_data = results[cname]
        per_seed_means = [sum(s) / 3 for s in seeds_data]
        per_seed_mins = [min(s) for s in seeds_data]
        mu_mean = mean(per_seed_means)
        sd_mean = stdev(per_seed_means) if len(per_seed_means) > 1 else 0.0
        mu_min = mean(per_seed_mins)
        score = mu_mean + mu_min
        gap = score - base_score
        n_sigma = gap / max(1.0, base_sigma) if base_sigma > 0 else 0
        rows.append((cname, mu_mean, sd_mean, mu_min, score, gap, n_sigma))

    rows.sort(key=lambda r: r[4], reverse=True)
    for c, mu, sd, mi, sc, gap, ns in rows:
        marker = ">>>" if c == "baseline" else "   "
        print(f"{marker} {c:<14s}  {mu:>10,.0f} ± {sd:>5,.0f}  {mi:>9,.0f}  "
              f"{sc:>10,.0f}  {gap:>+10,.0f} ({ns:+.2f}σ)")

    print(f"\nBaseline mean σ across seeds = {base_sigma:,.0f}; "
          f"|Δ| < σ is noise.")


if __name__ == "__main__":
    main()
