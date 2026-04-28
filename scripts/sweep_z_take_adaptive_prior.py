"""
Sweep PRIOR_WEIGHT for z_take_adaptive_mean.py.

Larger PRIOR_WEIGHT → trust the static prior longer (PRIOR=∞ → equivalent
to plain z_take.py). Smaller → empirical mean takes over fast.

For each prior weight: run 4 days, parse per-product PnL, score by per-day
mean + per-day min on the portfolio total. Also report each product's
best prior individually (in case fast/slow products want different
schedules).

Usage:  python scripts/sweep_z_take_adaptive_prior.py
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
STRAT = REPO / "strategies" / "round4" / "tmp" / "z_take_adaptive_mean.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]
PRIORS = [200, 500, 1000, 2000, 5000, 10000, 50000, 1_000_000_000]

PRODUCTS = [
    "HYDROGEL_PACK", "VELVETFRUIT_EXTRACT",
    "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100",
    "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500",
]


def patch(src: str, prior: int) -> str:
    return re.sub(r'(PRIOR_WEIGHT\s*=\s*)\d+', rf'\g<1>{prior}', src)


def run_one_day(dataset: str, day: int) -> dict[str, float]:
    r = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(STRAT), "--dataset", dataset, f"--day={day}",
         "--queue-penetration", "1.0", "--products", "full",
         "--artifact-mode", "none"],
        capture_output=True, text=True, timeout=240, cwd=str(BT_DIR),
    )
    out: dict[str, float] = {}
    in_table = False
    for line in r.stdout.splitlines():
        if line.startswith("PRODUCT"):
            in_table = True
            continue
        if in_table:
            if not line.strip():
                break
            parts = line.split()
            if len(parts) >= 2:
                try:
                    out[parts[0]] = float(parts[1])
                except ValueError:
                    pass
    return out


def fmt(x: float, w: int = 9) -> str:
    return f"{x:>{w},.0f}"


def main() -> None:
    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.adapbak")
    backup.write_text(original)

    # results[prior][product] = [pnl_d0, pnl_d1, pnl_d2, pnl_d3]
    results: dict[int, dict[str, list[float]]] = {}

    print(f"Sweeping PRIOR_WEIGHT ∈ {PRIORS}")
    print(f"4 days each = {len(PRIORS) * 4} backtests\n")

    try:
        for i, prior in enumerate(PRIORS, 1):
            STRAT.write_text(patch(original, prior))
            per_prod: dict[str, list[float]] = {p: [0.0] * 4 for p in PRODUCTS}
            for d, (ds, day) in enumerate(DAY_KEYS):
                got = run_one_day(ds, day)
                for p in PRODUCTS:
                    per_prod[p][d] = got.get(p, 0.0)
            results[prior] = per_prod
            tot = [sum(per_prod[p][d] for p in PRODUCTS) for d in range(4)]
            mn = sum(tot) / 4
            mi = min(tot)
            label = "static" if prior >= 1_000_000_000 else str(prior)
            print(f"[{i:>2}/{len(PRIORS)}] PRIOR={label:>10}  "
                  f"d0={fmt(tot[0])} d1={fmt(tot[1])} d2={fmt(tot[2])} d3={fmt(tot[3])}  "
                  f"mean={fmt(mn)} min={fmt(mi)} m+m={fmt(mn+mi,10)}", flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)

    # Portfolio summary
    print("\n" + "=" * 92)
    print("PORTFOLIO m+m by PRIOR_WEIGHT")
    print("=" * 92)
    print(f"{'prior':>10}  {'d0':>9}  {'d1':>9}  {'d2':>9}  {'d3':>9}  "
          f"{'mean':>9}  {'min':>9}  {'m+m':>10}")
    print("-" * 92)
    rows = []
    for prior in PRIORS:
        per_prod = results[prior]
        tot = [sum(per_prod[p][d] for p in PRODUCTS) for d in range(4)]
        mn, mi = sum(tot) / 4, min(tot)
        rows.append((prior, tot, mn, mi, mn + mi))
        label = "static" if prior >= 1_000_000_000 else str(prior)
        print(f"{label:>10}  {fmt(tot[0])}  {fmt(tot[1])}  {fmt(tot[2])}  {fmt(tot[3])}  "
              f"{fmt(mn)}  {fmt(mi)}  {fmt(mn+mi,10)}")

    best = max(rows, key=lambda r: r[4])
    static = next(r for r in rows if r[0] >= 1_000_000_000)
    delta = best[4] - static[4]
    print(f"\nBest portfolio m+m: PRIOR={best[0]}  m+m={best[4]:,.0f}")
    print(f"vs static-only:     m+m={static[4]:,.0f}  → Δ = {delta:+,.0f}")

    # Per-product best
    print("\n" + "=" * 92)
    print("PER-PRODUCT best PRIOR (each scored on its own per-day mean+min)")
    print("=" * 92)
    print(f"{'PRODUCT':<22} {'best prior':>12}  {'m+m':>10}  vs static")
    print("-" * 92)
    for p in PRODUCTS:
        best_prior, best_score = None, -1e18
        static_score = None
        for prior in PRIORS:
            pd = results[prior][p]
            s = sum(pd) / 4 + min(pd)
            if prior >= 1_000_000_000:
                static_score = s
            if s > best_score:
                best_score = s
                best_prior = prior
        label = "static" if best_prior >= 1_000_000_000 else str(best_prior)
        delta_p = best_score - static_score
        print(f"{p:<22} {label:>12}  {fmt(best_score,10)}  {delta_p:>+10,.0f}")


if __name__ == "__main__":
    main()
