"""
Head-to-head: baseline z_take.py vs per-asset variants in tmp/.

Runs each strategy on the 4 observed days (round3 d0 + round4 d1-3),
parses per-product per-day PnL, and reports a flat comparison table.

Usage:  python scripts/compare_z_take_variants.py
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]

STRATS = [
    ("baseline    (z_take.py)",         REPO / "strategies" / "round4" / "z_take.py"),
    ("mix tuned   (z-take + HP-only)",  REPO / "strategies" / "round4" / "z_take_per_asset_mix.py"),
]


def run_one_day(strat: Path, dataset: str, day: int) -> tuple[float, dict[str, float]]:
    """Returns (total_pnl, {product: pnl})."""
    r = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(strat), "--dataset", dataset, f"--day={day}",
         "--queue-penetration", "1.0", "--products", "full",
         "--artifact-mode", "none"],
        capture_output=True, text=True, timeout=240, cwd=str(BT_DIR),
    )
    total = 0.0
    per_prod: dict[str, float] = {}
    in_table = False
    for line in r.stdout.splitlines():
        if line.startswith("D") and "TICKS" not in line:
            parts = line.split()
            if len(parts) >= 5:
                try:
                    total = float(parts[4])
                except ValueError:
                    pass
        if line.startswith("PRODUCT"):
            in_table = True
            continue
        if in_table:
            if not line.strip():
                break
            parts = line.split()
            if len(parts) >= 2:
                try:
                    per_prod[parts[0]] = float(parts[1])
                except ValueError:
                    pass
    return total, per_prod


def fmt(x: float, w: int = 9) -> str:
    return f"{x:>{w},.0f}"


def main() -> None:
    # results[strat_label] = (per_day_total[4], per_day_per_prod[4])
    results: dict[str, tuple[list[float], list[dict[str, float]]]] = {}

    for label, strat in STRATS:
        per_day_total: list[float] = []
        per_day_pp: list[dict[str, float]] = []
        for ds, day in DAY_KEYS:
            tot, pp = run_one_day(strat, ds, day)
            per_day_total.append(tot)
            per_day_pp.append(pp)
        results[label] = (per_day_total, per_day_pp)
        mn = sum(per_day_total) / 4
        mi = min(per_day_total)
        print(f"{label}  d0={fmt(per_day_total[0])} d1={fmt(per_day_total[1])} "
              f"d2={fmt(per_day_total[2])} d3={fmt(per_day_total[3])}  "
              f"mean={fmt(mn)} min={fmt(mi)} m+m={fmt(mn+mi,10)}",
              flush=True)

    # ===== Portfolio table =====
    print("\n" + "=" * 100)
    print("PORTFOLIO TOTAL m+m")
    print("=" * 100)
    print(f"{'strategy':<34}  {'d0':>9}  {'d1':>9}  {'d2':>9}  {'d3':>9}  "
          f"{'mean':>9}  {'min':>9}  {'m+m':>10}  {'Δ vs base':>11}")
    print("-" * 100)
    base_score = None
    for label, _ in STRATS:
        per_day, _ = results[label]
        mn, mi = sum(per_day) / 4, min(per_day)
        score = mn + mi
        if base_score is None:
            base_score = score
            delta_str = ""
        else:
            delta_str = f"{score-base_score:>+11,.0f}"
        print(f"{label:<34}  {fmt(per_day[0])}  {fmt(per_day[1])}  "
              f"{fmt(per_day[2])}  {fmt(per_day[3])}  "
              f"{fmt(mn)}  {fmt(mi)}  {fmt(score,10)}  {delta_str}")

    # ===== Per-product m+m table — winner per asset =====
    all_products = set()
    for label, _ in STRATS:
        for pp in results[label][1]:
            all_products.update(pp.keys())
    products = sorted(all_products)

    print("\n" + "=" * 110)
    print("PER-PRODUCT m+m by strategy (winner ★)")
    print("=" * 110)
    header_strats = "  ".join(f"{lab[:14]:>14}" for lab, _ in STRATS)
    print(f"{'PRODUCT':<22}  {header_strats}   winner")
    print("-" * 110)

    winners: dict[str, str] = {}
    portfolio_best: list[float] = [0.0, 0.0, 0.0, 0.0]
    portfolio_combined: list[float] = [0.0, 0.0, 0.0, 0.0]

    for p in products:
        cells = []
        scores = []
        per_days_per_strat = []
        for label, _ in STRATS:
            pp_list = results[label][1]
            pd = [pp.get(p, 0.0) for pp in pp_list]
            mm = sum(pd) / 4 + min(pd)
            cells.append(f"{mm:>+14,.0f}")
            scores.append((label, mm))
            per_days_per_strat.append((label, pd, mm))

        if all(s[1] == 0 for s in scores):
            continue

        best_label, best_mm = max(scores, key=lambda s: s[1])
        winners[p] = best_label

        # mark winner
        out_cells = []
        for (label, _), c in zip(STRATS, cells):
            mark = "★" if label == best_label else " "
            out_cells.append(f"{mark}{c}")
        print(f"{p:<22}  {'  '.join(out_cells)}   {best_label[:14]}")

        # accumulate per-day totals: winner-per-asset and combined-as-reference
        for label, pd, _ in per_days_per_strat:
            if label == best_label:
                for i in range(4):
                    portfolio_best[i] += pd[i]
            if "combined" in label.lower():
                for i in range(4):
                    portfolio_combined[i] += pd[i]

    # ===== Implied portfolio if we picked winner per asset =====
    print("\n" + "=" * 100)
    print("IMPLIED PORTFOLIO (per-asset winner mix)")
    print("=" * 100)
    mn = sum(portfolio_best) / 4
    mi = min(portfolio_best)
    print(f"  per-asset best   d0={fmt(portfolio_best[0])} d1={fmt(portfolio_best[1])} "
          f"d2={fmt(portfolio_best[2])} d3={fmt(portfolio_best[3])}  "
          f"mean={fmt(mn)} min={fmt(mi)} m+m={fmt(mn+mi,10)}")
    cmn = sum(portfolio_combined) / 4
    cmi = min(portfolio_combined)
    print(f"  combined ref     d0={fmt(portfolio_combined[0])} d1={fmt(portfolio_combined[1])} "
          f"d2={fmt(portfolio_combined[2])} d3={fmt(portfolio_combined[3])}  "
          f"mean={fmt(cmn)} min={fmt(cmi)} m+m={fmt(cmn+cmi,10)}")
    print(f"\n  Δ m+m vs combined: {(mn+mi)-(cmn+cmi):+,.0f}")

    print("\n" + "=" * 100)
    print("Winners summary:")
    print("=" * 100)
    by_strat: dict[str, list[str]] = {}
    for p, w in winners.items():
        by_strat.setdefault(w, []).append(p)
    for label, _ in STRATS:
        if label in by_strat:
            print(f"  {label:<32}  →  {', '.join(by_strat[label])}")


if __name__ == "__main__":
    main()
