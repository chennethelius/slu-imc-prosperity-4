#!/usr/bin/env python3
"""Monte Carlo robustness test over (queue-penetration, slippage) space."""
import argparse
import random
import re
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BT = ROOT / "backtester"


def run_once(trader, qp, sl):
    trader_path = str(Path(trader).resolve()) if not Path(trader).is_absolute() else trader
    r = subprocess.run(
        [
            "./scripts/cargo_local.sh", "run", "--",
            "--trader", trader_path,
            "--products", "summary",
            "--persist",
            "--queue-penetration", str(qp),
            "--price-slippage-bps", str(sl),
            "--dataset", "round1",
        ],
        cwd=str(BT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    out = r.stdout + r.stderr
    m = re.search(r"TOTAL\s+-\s+\d+\s+\d+\s+([\d.]+)", out)
    if m:
        return float(m.group(1))
    print(f"    DEBUG: no match. rc={r.returncode} stdout_tail={r.stdout[-200:]!r}", file=sys.stderr)
    return None


def mc(trader, n, seed):
    random.seed(seed)
    results = []
    for i in range(n):
        qp = random.uniform(0.0, 0.5)
        sl = random.uniform(0, 3)
        pnl = run_once(trader, qp, sl)
        if pnl is not None:
            results.append((qp, sl, pnl))
        print(f"  [{i+1}/{n}] qp={qp:.3f} sl={sl:.2f}  pnl={pnl}", file=sys.stderr)
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("traders", nargs="+")
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    for t in args.traders:
        print(f"\n=== {t} ===", file=sys.stderr)
        results = mc(t, args.n, args.seed)
        pnls = [r[2] for r in results]
        pnls_sorted = sorted(pnls)
        p5 = pnls_sorted[max(0, int(len(pnls) * 0.05))]
        print(f"\n{t}")
        print(f"  mean={statistics.mean(pnls):,.0f}  stdev={statistics.stdev(pnls):,.0f}")
        print(f"  min={min(pnls):,.0f}  p5={p5:,.0f}  median={statistics.median(pnls):,.0f}  max={max(pnls):,.0f}")
