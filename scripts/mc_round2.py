"""
Monte Carlo parameter stability test for Round 1 strategies.

For each strategy:
  1. Read class-level parameters (ALL_CAPS attrs)
  2. Generate N perturbations (±20% uniform)
  3. Run backtest, record PnL
  4. Report mean, std, min/max
  5. Flag if std/mean > 5% (suggests overfit)

Output goes to stdout. No file writes.
"""
import os
import random
import re
import subprocess
import sys
from statistics import mean, stdev

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BT = os.path.join(REPO, "backtester", "target", "release", "rust_backtester")
STRAT_DIR = os.path.join(REPO, "strategies", "round2")

N_SIMS = 15
PERTURB = 0.20  # ±20%

PARAM_RE = re.compile(r"^(\s*)([A-Z][A-Z0-9_]+)\s*=\s*([\d.]+)\s*(#.*)?$", re.M)


def find_params(src: str) -> dict[str, float]:
    params = {}
    for m in PARAM_RE.finditer(src):
        name, val = m.group(2), m.group(3)
        try:
            params[name] = float(val)
        except ValueError:
            pass
    return params


def patch_params(src: str, overrides: dict[str, float]) -> str:
    out = src
    for name, val in overrides.items():
        out = re.sub(rf'^(\s*){re.escape(name)}\s*=\s*[\d.]+', rf'\g<1>{name} = {val:g}', out, flags=re.M)
    return out


def run_backtest(strategy_path: str) -> tuple[float, float, float, float]:
    """Returns (pep_pnl, osm_pnl, total, avg_per_day) aggregated over 3 days."""
    pep_sum = osm_sum = 0.0
    for day in ("-1", "0", "1"):
        r = subprocess.run(
            [BT, "--trader", strategy_path, "--dataset", "round2", f"--day={day}",
             "--queue-penetration", "0.0", "--products", "full", "--artifact-mode", "none"],
            capture_output=True, text=True, timeout=60, cwd=os.path.join(REPO, "backtester"),
        )
        for line in r.stdout.split("\n"):
            if "INTARIAN_PEPPER" in line:
                pep_sum += float(line.split()[-1])
            elif "ASH_COATED_OSMIUM" in line:
                osm_sum += float(line.split()[-1])
    return pep_sum, osm_sum, pep_sum + osm_sum, (pep_sum + osm_sum) / 3.0


def mc_test(strategy_name: str):
    path = os.path.join(STRAT_DIR, f"{strategy_name}.py")
    backup_path = path + ".bak"
    with open(path) as f:
        original = f.read()
    with open(backup_path, "w") as f:
        f.write(original)

    try:
        base_params = find_params(original)
        # Only perturb PEP_ and OSM_ prefixed numeric params.
        # Exclude observed market constants (fair price, drift rate) — perturbing
        # them destroys the model because they aren't hyperparameters.
        EXCLUDE = {"OSM_FAIR_STATIC", "OSM_FAIR", "PEP_DRIFT", "PEP_FAIR_STATIC"}
        tunable = {k: v for k, v in base_params.items()
                   if (k.startswith("PEP_") or k.startswith("OSM_")) and v > 0 and v < 20000
                   and k not in EXCLUDE}

        print(f"\n{'='*60}\n{strategy_name}\n{'='*60}")
        print(f"Tunable params: {list(tunable.keys())}")

        # Baseline
        with open(path, "w") as f:
            f.write(original)
        b_pep, b_osm, b_tot, b_avg = run_backtest(path)
        print(f"\nBaseline: PEP={b_pep:.0f}  OSM={b_osm:.0f}  TOT={b_tot:.0f}  avg/day={b_avg:.0f}")

        # MC runs
        rng = random.Random(42)
        results = []
        for i in range(N_SIMS):
            overrides = {}
            for name, val in tunable.items():
                pct = 1.0 + rng.uniform(-PERTURB, PERTURB)
                new_val = val * pct
                # Round to nearest integer if original was integer
                if val == int(val):
                    new_val = max(1, int(round(new_val)))
                overrides[name] = new_val
            patched = patch_params(original, overrides)
            with open(path, "w") as f:
                f.write(patched)
            try:
                p, o, t, a = run_backtest(path)
                results.append(t)
                print(f"  run {i+1:2d}: TOT={t:.0f}  (PEP={p:.0f} OSM={o:.0f})")
            except Exception as e:
                print(f"  run {i+1:2d}: FAILED {e}")

        if results:
            m = mean(results)
            s = stdev(results) if len(results) > 1 else 0
            print(f"\nSummary over {len(results)} runs:")
            print(f"  Mean:     {m:.0f}  (baseline {b_tot:.0f}, diff {m - b_tot:+.0f})")
            print(f"  Std:      {s:.0f}  ({100*s/m:.2f}% of mean)")
            print(f"  Min/Max:  {min(results):.0f} / {max(results):.0f}")
            print(f"  Robust:   {'YES' if s/m < 0.02 else 'MAYBE' if s/m < 0.05 else 'OVERFIT-RISK'}")

    finally:
        with open(path, "w") as f:
            f.write(original)
        os.remove(backup_path)


if __name__ == "__main__":
    strategies = sys.argv[1:] or ["disc_meta_v1", "disc_spread_regime", "disc_spartan_band"]
    for s in strategies:
        mc_test(s)
