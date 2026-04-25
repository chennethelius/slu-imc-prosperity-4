"""
Monte Carlo sensitivity test for general_mm.py.

Perturbs the AGGRESSOR_LAMBDA values per product by random ±FRAC, runs the
backtester, records total PnL. Repeats N times to give a distribution of
outcomes around the chosen parameters. A robust strategy has tight
distribution; an overfit one swings wildly with small perturbations.

    python scripts/mc_general_mm.py            # 20 trials, ±20%
    python scripts/mc_general_mm.py 50 0.30    # 50 trials, ±30%
"""

import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRADER = REPO / "strategies" / "round3" / "general_mm.py"


def find_lambdas(text: str) -> list[tuple[int, str, float]]:
    """Find AGGRESSOR_LAMBDA = X lines. Returns (line_no, line, value)."""
    out = []
    for i, line in enumerate(text.splitlines()):
        m = re.match(r"^(\s*AGGRESSOR_LAMBDA\s*=\s*)([+-]?\d+\.?\d*)", line)
        if m:
            out.append((i, m.group(1), float(m.group(2))))
    return out


def perturb_text(text: str, frac: float, rng: random.Random) -> tuple[str, dict]:
    lines = text.splitlines()
    perturbations = {}
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*AGGRESSOR_LAMBDA\s*=\s*)([+-]?\d+\.?\d*)(.*)", line)
        if m:
            prefix, value, suffix = m.group(1), float(m.group(2)), m.group(3)
            perturbed = value * (1 + rng.uniform(-frac, frac))
            lines[i] = f"{prefix}{perturbed:.6f}{suffix}"
            perturbations[i] = (value, perturbed)
    return "\n".join(lines) + "\n", perturbations


def run_backtest(trader_path: Path) -> float | None:
    """Run `make round3` and return total PnL, or None on failure."""
    result = subprocess.run(
        ["make", "round3", f"TRADER=../{trader_path.relative_to(REPO)}"],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
    )
    for line in result.stdout.splitlines():
        if line.startswith("TOTAL"):
            parts = line.split()
            try:
                return float(parts[-2])
            except (ValueError, IndexError):
                pass
    return None


def main():
    n_trials = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    frac = float(sys.argv[2]) if len(sys.argv) > 2 else 0.20

    original = TRADER.read_text()
    backup = TRADER.with_suffix(".py.mc_backup")
    shutil.copy2(TRADER, backup)

    pnls: list[float] = []
    print(f"Running {n_trials} trials with AGGRESSOR_LAMBDA perturbed ±{frac:.0%}")
    print(f"{'trial':>5}  {'pnl':>10}  {'delta_from_baseline':>20}")
    print("-" * 50)

    rng = random.Random(42)
    try:
        baseline = run_backtest(TRADER)
        if baseline is None:
            print("Could not get baseline PnL — abort")
            return
        print(f"{'base':>5}  {baseline:>10.0f}  {'(reference)':>20}")
        for trial in range(n_trials):
            perturbed_text, _ = perturb_text(original, frac, rng)
            TRADER.write_text(perturbed_text)
            pnl = run_backtest(TRADER)
            if pnl is None:
                continue
            pnls.append(pnl)
            print(f"{trial+1:>5}  {pnl:>10.0f}  {pnl - baseline:>+20.0f}")
    finally:
        TRADER.write_text(original)
        backup.unlink()

    if pnls:
        pnls.sort()
        n = len(pnls)
        mean = sum(pnls) / n
        median = pnls[n // 2]
        p10 = pnls[max(0, n // 10)]
        p90 = pnls[min(n - 1, 9 * n // 10)]
        std = (sum((p - mean) ** 2 for p in pnls) / n) ** 0.5
        print()
        print(f"Trials:   {n}")
        print(f"Baseline: {baseline:>10.0f}")
        print(f"Mean:     {mean:>10.0f}")
        print(f"Median:   {median:>10.0f}")
        print(f"Std dev:  {std:>10.0f}  ({std/abs(mean)*100:.1f}% of mean)")
        print(f"P10/P90:  {p10:>10.0f} / {p90:.0f}")
        print(f"Worst:    {pnls[0]:>10.0f}")
        print(f"Best:     {pnls[-1]:>10.0f}")


if __name__ == "__main__":
    main()
