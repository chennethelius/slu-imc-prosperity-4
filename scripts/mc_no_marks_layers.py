"""Layer-ablation MC for no_marks _conviction_orders.

Runs both leave-one-out (LOO — what does each layer contribute on the
margin) and add-one-in (solo — does each layer pay off in isolation).

LOO configs:    all_on, -unwind, -primary, -aggr, -vfe_spill, -mm
SOLO configs:   only_unwind, only_primary, only_aggr, only_vfe_spill, only_mm
                (vfe_spill solo is degenerate — depends on primary overflow)

Reports per-config combined HP+VFE day-mean over MC seeds, with Δ vs
all_on (LOO) or all_off (SOLO) and σ-confidence.

Usage: python scripts/mc_no_marks_layers.py [n_seeds]
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
MC_DATA = BT_DIR / "datasets" / "round4_mc_layers"
STRAT = REPO / "strategies" / "round4" / "no_marks.py"

PRODUCTS = ("HYDROGEL_PACK", "VELVETFRUIT_EXTRACT", "VEV_5000")
DAYS = ("1", "2", "3")

# Helpers to build override dicts for each layer-on/off state. Aggressive
# layer is toggled via aggr_mean_mode (existing); the rest use enable_*.
LAYER_KEYS = ["unwind", "primary", "aggr", "vfe_spillover", "mm"]


def cfg_for(state: dict[str, bool]) -> dict:
    """state maps layer_name -> bool. Returns override dict to apply to
    HP_CFG and VFE_CFG (note: vfe_spillover only affects VFE in practice
    but harmless to set on both)."""
    o = {}
    o["enable_unwind"] = state["unwind"]
    o["enable_primary"] = state["primary"]
    o["enable_vfe_spillover"] = state["vfe_spillover"]
    o["enable_mm"] = state["mm"]
    o["aggr_mean_mode"] = '"static"' if state["aggr"] else '"off"'
    return o


# All layers on
ALL_ON = {k: True for k in LAYER_KEYS}
# All layers off (degenerate — never trades)
ALL_OFF = {k: False for k in LAYER_KEYS}

CONFIGS = {"all_on": cfg_for(ALL_ON)}
# LOO: drop one layer
for k in LAYER_KEYS:
    state = dict(ALL_ON); state[k] = False
    CONFIGS[f"-{k}"] = cfg_for(state)
# SOLO: only one layer on
CONFIGS["all_off"] = cfg_for(ALL_OFF)
for k in LAYER_KEYS:
    state = dict(ALL_OFF); state[k] = True
    CONFIGS[f"only_{k}"] = cfg_for(state)


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
    # If the key is missing, insert it before the closing brace (handles new
    # enable_* keys that aren't in the file by default).
    pat = rf'({cfg_name}\s*=\s*\{{[^}}]*?"{re.escape(key)}"\s*:\s*)([^,}}\n]+)'
    new, n = re.subn(pat, rf'\g<1>{val_str}', src, count=1, flags=re.S)
    if n == 0:
        # Insert "key": value, before the closing brace
        ins = rf'    "{key}": {val_str},\n}}'
        new = re.sub(rf'({cfg_name}\s*=\s*\{{[^}}]*?)\n\}}',
                     rf'\1\n    "{key}": {val_str},\n}}', src, count=1, flags=re.S)
    return new


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
    print(f"LAYER ABLATION  ({n_seeds} seeds)  HP+VFE combined per-day score")
    print("=" * 80)
    print(f"{'config':<18s}  {'mean (μ ± σ)':>20s}  {'min (μ)':>9s}  "
          f"{'mean+min':>10s}  {'Δ vs ref':>14s}")
    print("-" * 80)

    def stats(seeds_data):
        per_seed_means = [sum(s) / 3 for s in seeds_data]
        per_seed_mins = [min(s) for s in seeds_data]
        mu_m = mean(per_seed_means)
        sd_m = stdev(per_seed_means) if len(per_seed_means) > 1 else 0.0
        mu_min = mean(per_seed_mins)
        return mu_m, sd_m, mu_min, mu_m + mu_min

    base_mu, base_sd, _, base_score = stats(results["all_on"])
    off_mu, off_sd, _, off_score = stats(results["all_off"])

    def show(cname, ref_score, ref_label):
        mu, sd, mn, sc = stats(results[cname])
        gap = sc - ref_score
        nsig = gap / max(1.0, base_sd) if base_sd > 0 else 0
        print(f"{cname:<18s}  {mu:>10,.0f} ± {sd:>5,.0f}  {mn:>9,.0f}  "
              f"{sc:>10,.0f}  {gap:>+8,.0f} ({nsig:+.2f}σ vs {ref_label})")

    print(">>> reference: all_on")
    show("all_on", base_score, "self")
    print("\nLeave-one-out (Δ measures the layer's marginal contribution):")
    for k in LAYER_KEYS:
        show(f"-{k}", base_score, "all_on")
    print("\nSolo (does the layer pay off alone?):")
    show("all_off", off_score, "self")
    for k in LAYER_KEYS:
        show(f"only_{k}", off_score, "all_off")

    print(f"\nBaseline σ across seeds = {base_sd:,.0f}; "
          f"|Δ| < σ is noise.")


if __name__ == "__main__":
    main()
