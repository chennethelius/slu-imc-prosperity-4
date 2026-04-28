"""MC test of 3 candidate improvements to the aggressive MR layer:
  A) Position-aware trigger (aggr_max_pos_for_fire)
  B) Harvest pairing (enable_harvest + harvest_boost)
  C) Tiered z-thresholds (aggr_tiers)

Each variant is run alone vs the current best (all_on, static+static, z=2.5,
t=90). Reports per-day combined HP+VFE+VEV_5000 mean+min over MC seeds.

Usage: python scripts/mc_aggr_improvements.py [n_seeds]
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
MC_DATA = BT_DIR / "datasets" / "round4_mc_aggr"
STRAT = REPO / "strategies" / "round4" / "no_marks.py"

PRODUCTS = ("HYDROGEL_PACK", "VELVETFRUIT_EXTRACT", "VEV_5000")
DAYS = ("1", "2", "3")

# Configs. Each is a dict of overrides applied to both HP_CFG and VFE_CFG.
CONFIGS = {
    "baseline":                  {},                                # current best
    # --- A: position-aware ---
    "A_pos40":                   {"aggr_max_pos_for_fire": 40},
    "A_pos80":                   {"aggr_max_pos_for_fire": 80},
    "A_pos120":                  {"aggr_max_pos_for_fire": 120},
    # --- B: harvest pairing (active take when revert achieved) ---
    "B_z1.0_t30":                {"enable_harvest": True, "harvest_z_thresh": 1.0, "harvest_take_size": 30},
    "B_z1.5_t30":                {"enable_harvest": True, "harvest_z_thresh": 1.5, "harvest_take_size": 30},
    "B_z2.0_t30":                {"enable_harvest": True, "harvest_z_thresh": 2.0, "harvest_take_size": 30},
    "B_z1.5_t60":                {"enable_harvest": True, "harvest_z_thresh": 1.5, "harvest_take_size": 60},
    "B_z2.0_t60":                {"enable_harvest": True, "harvest_z_thresh": 2.0, "harvest_take_size": 60},
    # --- C: tiered z-thresholds ---
    "C_t2_15-75":                {"aggr_tiers": "[(2.0, 15), (2.5, 75)]"},
    "C_t2_30-60":                {"aggr_tiers": "[(2.0, 30), (3.0, 60)]"},
    "C_t3_15-30-45":             {"aggr_tiers": "[(2.0, 15), (2.5, 30), (3.0, 45)]"},
    "C_t3_10-20-60":             {"aggr_tiers": "[(2.0, 10), (2.5, 20), (3.0, 60)]"},
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


def find_block(src: str, cfg_name: str) -> tuple[int, int]:
    """Return (open_brace_pos+1, close_brace_pos) of the cfg dict literal."""
    m = re.search(rf'{cfg_name}\s*=\s*\{{', src)
    if not m:
        return -1, -1
    start = m.end()
    depth = 1
    i = start
    while i < len(src) and depth > 0:
        c = src[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
        i += 1
    return start, i - 1   # close brace position


def patch_one(src: str, cfg_name: str, key: str, value) -> str:
    """Bracket-balanced replacement of `"key": <value>` in cfg block. If the
    key is missing, insert it before the closing brace."""
    val_str = value if isinstance(value, str) else repr(value)
    bs, be = find_block(src, cfg_name)
    if bs < 0:
        return src
    block = src[bs:be]
    # Find the key
    km = re.search(rf'"{re.escape(key)}"\s*:\s*', block)
    if km is None:
        new_block = block.rstrip() + f'\n    "{key}": {val_str},\n'
        return src[:bs] + new_block + src[be:]
    v_start = km.end()
    depth_b = 0
    j = v_start
    while j < len(block):
        c = block[j]
        if c in '({[':
            depth_b += 1
        elif c in ')}]':
            if depth_b == 0:
                break
            depth_b -= 1
        elif c in ',\n' and depth_b == 0:
            break
        j += 1
    new_block = block[:v_start] + val_str + block[j:]
    return src[:bs] + new_block + src[be:]


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
    n_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    noise_sd = 0.5

    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.mcbak")
    backup.write_text(original)

    results = {c: [] for c in CONFIGS}

    try:
        for seed in range(n_seeds):
            print(f"\n=== seed {seed} ===", flush=True)
            perturb_prices(noise_sd, seed)
            for cname, overrides in CONFIGS.items():
                STRAT.write_text(patch_config(original, overrides))
                per_day = [run_one_day(d) for d in DAYS]
                results[cname].append(per_day)
                print(f"  {cname:18s}  D1={per_day[0]:>9,.0f} D2={per_day[1]:>9,.0f} "
                      f"D3={per_day[2]:>9,.0f}  mean={sum(per_day)/3:>9,.0f}", flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)
        if MC_DATA.exists():
            shutil.rmtree(MC_DATA)

    print("\n" + "=" * 80)
    print(f"AGGRESSIVE-LAYER IMPROVEMENTS  ({n_seeds} seeds)  HP+VFE+VEV_5000")
    print("=" * 80)
    print(f"{'config':<18s}  {'mean (μ ± σ)':>20s}  {'min (μ)':>9s}  "
          f"{'mean+min':>10s}  {'Δ vs baseline':>15s}")
    print("-" * 80)

    def stats(seeds_data):
        per_seed_means = [sum(s) / 3 for s in seeds_data]
        per_seed_mins = [min(s) for s in seeds_data]
        mu_m = mean(per_seed_means)
        sd_m = stdev(per_seed_means) if len(per_seed_means) > 1 else 0.0
        mu_min = mean(per_seed_mins)
        return mu_m, sd_m, mu_min, mu_m + mu_min

    base_mu, base_sd, _, base_score = stats(results["baseline"])

    rows = []
    for cname in CONFIGS:
        mu, sd, mn, sc = stats(results[cname])
        gap = sc - base_score
        nsig = gap / max(1.0, base_sd) if base_sd > 0 else 0
        rows.append((cname, mu, sd, mn, sc, gap, nsig))

    rows.sort(key=lambda r: r[4], reverse=True)
    for c, mu, sd, mi, sc, gap, ns in rows:
        marker = ">>>" if c == "baseline" else "   "
        print(f"{marker} {c:<18s}  {mu:>10,.0f} ± {sd:>5,.0f}  {mi:>9,.0f}  "
              f"{sc:>10,.0f}  {gap:>+9,.0f} ({ns:+.2f}σ)")

    print(f"\nBaseline σ across seeds = {base_sd:,.0f}; |Δ| < σ is noise.")


if __name__ == "__main__":
    main()
