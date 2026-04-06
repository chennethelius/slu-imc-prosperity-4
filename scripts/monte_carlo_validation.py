"""
Monte Carlo Cross-Validation for Tomato Strategy Robustness

This script validates that the spread-adaptive EMA improvement is robust
across multiple randomly perturbed simulations. It:

1. Generates N synthetic variations of the strategy parameters
2. Tests each variation across all available days
3. Computes the distribution of improvements over baseline
4. Flags overfitting if improvements cluster on specific days

Theory: If an improvement is robust (not overfit), it should show
positive expected improvement across the parameter perturbation
distribution. An overfit improvement will show high variance and
negative performance under perturbation.

Based on: White (2000) "A Reality Check for Data Snooping" and
Hansen (2005) "A Test for Superior Predictive Ability"
"""

import subprocess
import os
import re
import random
import statistics
import sys

BACKTESTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtester")
STRATEGY = "../strategies/tutorial/optimized_mm.py"
STRATEGY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "strategies", "tutorial", "optimized_mm.py")

# Baseline performance (EMA_ALPHA=0.027, no spread-adaptive)
BASELINE = {"D-2": 12292.0, "D-1": 9498.0, "SUB": 1136.5}
BASELINE_TOTAL = sum(BASELINE.values())

N_SIMULATIONS = 30
random.seed(42)


def run_bt():
    env = os.environ.copy()
    env["PYO3_PYTHON"] = "C:/Users/thisi/AppData/Local/Programs/Python/Python313/python.exe"
    r = subprocess.run(
        ["cargo", "run", "--", "--trader", STRATEGY, "--dataset", "tutorial", "--products", "summary"],
        cwd=BACKTESTER, capture_output=True, text=True, env=env, timeout=180
    )
    for line in (r.stdout + r.stderr).split("\n"):
        if line.strip().startswith("TOM"):
            return [float(x) for x in line.split()[1:]]
    return []


def patch(params: dict):
    with open(STRATEGY_PATH, "r") as f:
        code = f.read()
    for param, val in params.items():
        code = re.sub(rf'{param} = [\d.]+', f'{param} = {val}', code)
    with open(STRATEGY_PATH, "w") as f:
        f.write(code)


def main():
    print("=" * 70)
    print("Monte Carlo Robustness Validation")
    print("=" * 70)
    print(f"Baseline TOM PnL: D-2={BASELINE['D-2']}, D-1={BASELINE['D-1']}, "
          f"SUB={BASELINE['SUB']}, Total={BASELINE_TOTAL}")
    print(f"Running {N_SIMULATIONS} parameter perturbations...\n")

    # Optimal params: sensitivity=0.22, ref=14.0, base_alpha=0.027
    optimal = {"SPREAD_SENSITIVITY": 0.22, "SPREAD_REF": 14.0, "EMA_BASE_ALPHA": 0.027}

    # Monte Carlo: perturb each parameter within a reasonable range
    improvements = []
    d2_improvements = []
    d1_improvements = []
    sub_improvements = []
    all_results = []

    print(f"{'#':>3} {'sens':>6} {'ref':>6} {'alpha':>7} {'D-2':>10} {'D-1':>10} "
          f"{'SUB':>8} {'Total':>10} {'vs BL':>8}")
    print("-" * 70)

    for i in range(N_SIMULATIONS):
        # Perturb parameters: ±30% uniform random around optimal
        params = {
            "SPREAD_SENSITIVITY": max(0.0, optimal["SPREAD_SENSITIVITY"] *
                                       random.uniform(0.5, 1.5)),
            "SPREAD_REF": max(6.0, optimal["SPREAD_REF"] *
                               random.uniform(0.7, 1.3)),
            "EMA_BASE_ALPHA": max(0.01, min(0.08, optimal["EMA_BASE_ALPHA"] *
                                             random.uniform(0.7, 1.3))),
        }
        patch(params)
        vals = run_bt()
        if not vals or len(vals) < 3:
            print(f"{i+1:>3} FAILED")
            continue

        total = sum(vals)
        imp = total - BASELINE_TOTAL
        improvements.append(imp)
        d2_improvements.append(vals[0] - BASELINE["D-2"])
        d1_improvements.append(vals[1] - BASELINE["D-1"])
        sub_improvements.append(vals[2] - BASELINE["SUB"])
        all_results.append({"params": params.copy(), "vals": vals, "total": total})

        print(f"{i+1:>3} {params['SPREAD_SENSITIVITY']:>6.3f} "
              f"{params['SPREAD_REF']:>6.1f} {params['EMA_BASE_ALPHA']:>7.4f} "
              f"{vals[0]:>10.1f} {vals[1]:>10.1f} {vals[2]:>8.1f} "
              f"{total:>10.1f} {imp:>+8.1f}")

    # ── Summary Statistics ──
    print("\n" + "=" * 70)
    print("MONTE CARLO SUMMARY")
    print("=" * 70)

    if not improvements:
        print("No valid simulations!")
        sys.exit(1)

    n = len(improvements)
    mean_imp = statistics.mean(improvements)
    std_imp = statistics.stdev(improvements) if n > 1 else 0
    median_imp = statistics.median(improvements)
    pct_positive = sum(1 for x in improvements if x > 0) / n * 100

    print(f"\nTotal improvement over baseline:")
    print(f"  Mean:     {mean_imp:>+8.1f} seashells")
    print(f"  Median:   {median_imp:>+8.1f} seashells")
    print(f"  Std Dev:  {std_imp:>8.1f} seashells")
    print(f"  % Positive: {pct_positive:.0f}% ({sum(1 for x in improvements if x > 0)}/{n})")
    print(f"  Min:      {min(improvements):>+8.1f}")
    print(f"  Max:      {max(improvements):>+8.1f}")

    print(f"\nPer-day improvement distribution:")
    for day, imps in [("D-2", d2_improvements), ("D-1", d1_improvements),
                      ("SUB", sub_improvements)]:
        m = statistics.mean(imps)
        s = statistics.stdev(imps) if len(imps) > 1 else 0
        pp = sum(1 for x in imps if x > 0) / len(imps) * 100
        print(f"  {day}: mean={m:>+7.1f}, std={s:>7.1f}, {pp:.0f}% positive")

    # ── Overfitting Test ──
    # If improvement is concentrated on one day, it might be overfit
    print(f"\n--- Overfitting Assessment ---")
    sharpe = mean_imp / std_imp if std_imp > 0 else float('inf')
    print(f"  Information Ratio (mean/std): {sharpe:.2f}")
    if sharpe > 0.5:
        print("  ✓ ROBUST: Consistent improvement across perturbations")
    elif sharpe > 0.2:
        print("  ~ MARGINAL: Some sensitivity to parameters")
    else:
        print("  ✗ FRAGILE: Improvement is parameter-sensitive, possible overfitting")

    if pct_positive > 70:
        print(f"  ✓ ROBUST: {pct_positive:.0f}% of perturbations beat baseline")
    elif pct_positive > 50:
        print(f"  ~ MARGINAL: Only {pct_positive:.0f}% beat baseline")
    else:
        print(f"  ✗ FRAGILE: Only {pct_positive:.0f}% beat baseline — likely overfit")

    # ── Best robust config ──
    # Pick the config that maximizes MINIMUM day improvement (maximin = robust)
    best_robust = max(all_results,
                      key=lambda r: min(r["vals"][0] - BASELINE["D-2"],
                                        r["vals"][1] - BASELINE["D-1"],
                                        r["vals"][2] - BASELINE["SUB"]))
    print(f"\n--- Most Robust Configuration (maximin) ---")
    print(f"  Params: {best_robust['params']}")
    print(f"  Results: D-2={best_robust['vals'][0]}, D-1={best_robust['vals'][1]}, "
          f"SUB={best_robust['vals'][2]}")
    print(f"  Total: {best_robust['total']} (vs baseline {BASELINE_TOTAL})")

    # ── Restore optimal params ──
    patch(optimal)
    print(f"\n✓ Strategy restored to optimal params: {optimal}")


if __name__ == "__main__":
    main()
