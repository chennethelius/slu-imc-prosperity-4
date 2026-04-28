"""Monte-carlo 6-corner ablation for no_marks aggressive-MR layer + decay.

For each MC seed, perturbs round4 prices with small Gaussian noise, then runs
the cargo backtester for the configurations:

  baseline          aggr=off,    no decay
  decay_only        aggr=off,    hard_cap decays start→end
  aggr_only         aggr=static, no decay
  aggr+hc_decay     aggr=static, hard_cap decays
  aggr+take_decay   aggr=static, aggr_max_take decays to 0
  aggr+both_decay   aggr=static, both decay

Reports per-day HP+VFE PnL by mode, then per-mode aggregate (mean across 3
days within each seed, then mean and min across seeds). Score = mean+min.

Usage: python scripts/mc_no_marks_ablate.py [n_seeds] [noise_sd]
       python scripts/mc_no_marks_ablate.py 6 0.5
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
MC_DATA = BT_DIR / "datasets" / "round4_mc_ablate"
STRAT = REPO / "strategies" / "round4" / "no_marks.py"

PRODUCTS = ("HYDROGEL_PACK", "VELVETFRUIT_EXTRACT")
DAYS = ("1", "2", "3")

# Each config: dict of overrides applied identically to HP_CFG and VFE_CFG.
# Keys map to dict entries we know exist with these names.
HC_END_DECAY = 0.30   # decay hard_cap to 30% of limit by decay_end_tick
TAKE_END_DECAY = 0    # decay aggr_max_take to 0 by decay_end_tick

CONFIGS = {
    "baseline":         {"aggr_mean_mode": '"off"'},
    "decay_only":       {"aggr_mean_mode": '"off"',
                         "hard_cap_end_pct": HC_END_DECAY},
    "aggr_only":        {"aggr_mean_mode": '"static"'},
    "aggr+hc_decay":    {"aggr_mean_mode": '"static"',
                         "hard_cap_end_pct": HC_END_DECAY},
    "aggr+take_decay":  {"aggr_mean_mode": '"static"',
                         "aggr_max_take_end": TAKE_END_DECAY},
    "aggr+both_decay":  {"aggr_mean_mode": '"static"',
                         "hard_cap_end_pct": HC_END_DECAY,
                         "aggr_max_take_end": TAKE_END_DECAY},
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


def patch_one(src: str, cfg_name: str, key: str, value) -> str:
    """Replace "key": <old> within HP_CFG or VFE_CFG block."""
    val_str = str(value) if not isinstance(value, str) else value
    pat = rf'({cfg_name}\s*=\s*\{{[^}}]*?"{re.escape(key)}"\s*:\s*)([^,}}\n]+)'
    return re.sub(pat, rf'\g<1>{val_str}', src, count=1, flags=re.S)


def patch_config(src: str, overrides: dict) -> str:
    out = src
    for key, val in overrides.items():
        out = patch_one(out, "HP_CFG", key, val)
        out = patch_one(out, "VFE_CFG", key, val)
    return out


def run_one_day(day: str) -> dict[str, float]:
    r = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(STRAT), "--dataset", str(MC_DATA),
         f"--day={day}", "--queue-penetration", "1.0",
         "--products", "summary", "--artifact-mode", "none"],
        capture_output=True, text=True, timeout=180, cwd=str(BT_DIR),
    )
    if r.returncode != 0:
        print(f"    [warn] failed day={day}: {r.stderr.strip()[:200]}", flush=True)
    out = {p: 0.0 for p in PRODUCTS}
    for line in r.stdout.splitlines():
        for p in PRODUCTS:
            if line.startswith(p):
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

    # results[cfg][seed] = list of per-day combined HP+VFE PnL for this seed
    results = {c: [] for c in CONFIGS}

    try:
        for seed in range(n_seeds):
            print(f"\n=== seed {seed} (noise_sd={noise_sd}) ===", flush=True)
            perturb_prices(noise_sd, seed)
            for cname, overrides in CONFIGS.items():
                STRAT.write_text(patch_config(original, overrides))
                per_day_combined = []
                hp_days, vfe_days = [], []
                for d in DAYS:
                    pnl = run_one_day(d)
                    hp_days.append(pnl["HYDROGEL_PACK"])
                    vfe_days.append(pnl["VELVETFRUIT_EXTRACT"])
                    per_day_combined.append(pnl["HYDROGEL_PACK"] + pnl["VELVETFRUIT_EXTRACT"])
                results[cname].append(per_day_combined)
                print(f"  {cname:18s}  HP {hp_days[0]:>7,.0f}/{hp_days[1]:>7,.0f}/{hp_days[2]:>7,.0f}  "
                      f"VFE {vfe_days[0]:>7,.0f}/{vfe_days[1]:>7,.0f}/{vfe_days[2]:>7,.0f}  "
                      f"comb-mean {sum(per_day_combined)/3:>8,.0f}", flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)
        if MC_DATA.exists():
            shutil.rmtree(MC_DATA)

    # ─── Aggregate ──────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print(f"SUMMARY  ({n_seeds} seeds, noise_sd={noise_sd})  HP+VFE combined")
    print("=" * 78)
    print(f"{'config':<18s}  {'day-mean (μ ± σ)':>22s}  {'day-min (μ ± σ)':>22s}  "
          f"{'mean+min':>10s}")
    print("-" * 78)
    rows = []
    for cname in CONFIGS:
        seeds_data = results[cname]              # list[seed] of list[3 days]
        per_seed_means = [sum(s) / 3 for s in seeds_data]
        per_seed_mins = [min(s) for s in seeds_data]
        mu_mean = mean(per_seed_means)
        sd_mean = stdev(per_seed_means) if len(per_seed_means) > 1 else 0.0
        mu_min = mean(per_seed_mins)
        sd_min = stdev(per_seed_mins) if len(per_seed_mins) > 1 else 0.0
        score = mu_mean + mu_min
        rows.append((cname, mu_mean, sd_mean, mu_min, sd_min, score))
        print(f"{cname:<18s}  {mu_mean:>10,.0f} ± {sd_mean:>7,.0f}  "
              f"{mu_min:>10,.0f} ± {sd_min:>7,.0f}  {score:>10,.0f}")

    rows.sort(key=lambda r: r[5], reverse=True)
    print("\nRanked by score (mean + min):")
    for i, (c, mn, sm, mi, smi, sc) in enumerate(rows):
        print(f"  {i+1}. {c:<18s}  score={sc:>10,.0f}")


if __name__ == "__main__":
    main()
