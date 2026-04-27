"""
Extended cross-validation for z_take.py with round-3 day 0 added.

Round-3 days 1-3 are byte-identical to round-4 days 1-3 (same products,
same prices), but round-3 day 0 is genuinely unseen by the means
currently hardcoded in z_take.py (which were fit on round-4 days 1-3).

This script:
  1. Tests z_take on day 0 with the CURRENT (3-day) means → true OOS check
  2. Compares 3-day pooled means vs 4-day pooled means (with day 0)
  3. Runs leave-one-day-out CV across all 4 days, refitting per fold
  4. Reports the in-sample vs out-of-sample gap

Day key:   ("round3", 0)  ("round4", 1)  ("round4", 2)  ("round4", 3)
"""
import re
import subprocess
from pathlib import Path
from statistics import mean as stat_mean

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
DATA_R3 = BT_DIR / "datasets" / "round3"
DATA_R4 = BT_DIR / "datasets" / "round4"
STRAT = REPO / "strategies" / "round4" / "z_take.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]


def load_mids() -> pd.DataFrame:
    frames = []
    for ds, d in DAY_KEYS:
        path = (DATA_R3 if ds == "round3" else DATA_R4) / f"prices_{ds[:5]}_{ds[5:]}_day_{d}.csv"
        df = pd.read_csv(path, sep=";")[["product", "mid_price"]].dropna()
        df["daykey"] = f"{ds}_d{d}"
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def fit_params(df: pd.DataFrame, train_keys: list[str]) -> dict[str, tuple[int, float]]:
    sub = df[df["daykey"].isin(train_keys)]
    out: dict[str, tuple[int, float]] = {}
    for prod, g in sub.groupby("product"):
        m, s = g["mid_price"].mean(), g["mid_price"].std()
        if pd.isna(s) or s <= 0:
            continue
        out[prod] = (int(round(m)), float(s))
    return out


def patch_cfgs(src: str, params: dict[str, tuple[int, float]]) -> str:
    out = src
    for prod, (m, s) in params.items():
        row_pat = rf'(\{{\s*"symbol":\s*"{re.escape(prod)}"[^}}]*?\}})'

        def repl(mo, mean_val=m, sd_val=s):
            row = mo.group(1)
            row = re.sub(r'("mean"\s*:\s*)[\d.eE+\-]+', rf'\g<1>{mean_val}', row)
            row = re.sub(r'("sd"\s*:\s*)[\d.eE+\-]+', rf'\g<1>{sd_val:.3f}', row)
            return row

        out = re.sub(row_pat, repl, out, count=1, flags=re.S)
    return out


def run_day(dataset: str, day: int) -> float:
    r = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(STRAT), "--dataset", dataset,
         f"--day={day}", "--queue-penetration", "1.0",
         "--products", "summary", "--artifact-mode", "none"],
        capture_output=True, text=True, timeout=240, cwd=str(BT_DIR),
    )
    for line in r.stdout.splitlines():
        if line.startswith("D"):
            parts = line.split()
            if len(parts) >= 5:
                try:
                    return float(parts[4])
                except ValueError:
                    pass
    return 0.0


def main() -> None:
    df = load_mids()
    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.cvbak")
    backup.write_text(original)

    print("=" * 70)
    print("Per-day mid means (PRE-CV)")
    print("=" * 70)
    print(f"{'product':<22} {'d0':>9} {'d1':>9} {'d2':>9} {'d3':>9}  "
          f"{'3-day μ':>9}  {'4-day μ':>9}  {'Δ':>6}")
    print("-" * 90)
    keys_3 = ["round4_d1", "round4_d2", "round4_d3"]
    keys_4 = ["round3_d0"] + keys_3
    for prod in sorted(df["product"].unique()):
        per_day = {k: df[(df["product"] == prod) & (df["daykey"] == k)]["mid_price"].mean()
                   for k in keys_4}
        m3 = sum(per_day[k] for k in keys_3) / 3
        m4 = sum(per_day.values()) / 4
        delta = m4 - m3
        if pd.isna(per_day["round3_d0"]):
            continue
        print(f"{prod:<22} "
              f"{per_day['round3_d0']:>9.2f} {per_day['round4_d1']:>9.2f} "
              f"{per_day['round4_d2']:>9.2f} {per_day['round4_d3']:>9.2f}  "
              f"{m3:>9.2f}  {m4:>9.2f}  {delta:>+6.2f}")

    try:
        # 1. Day 0 with CURRENT means (in-sample for 1,2,3; OOS for 0)
        print("\n" + "=" * 70)
        print("Strategy with CURRENT (3-day) means")
        print("=" * 70)
        STRAT.write_text(original)
        cur_pnl = {}
        for ds, d in DAY_KEYS:
            cur_pnl[(ds, d)] = run_day(ds, d)
            tag = "OOS" if (ds, d) == ("round3", 0) else "in-sample"
            print(f"  {ds} day {d}  ({tag:<10}): {cur_pnl[(ds, d)]:>10,.0f}")

        # 2. Refit means on all 4 days, run on each
        print("\n" + "=" * 70)
        print("Strategy with 4-day pooled means")
        print("=" * 70)
        params_4 = fit_params(df, keys_4)
        STRAT.write_text(patch_cfgs(original, params_4))
        pooled4_pnl = {}
        for ds, d in DAY_KEYS:
            pooled4_pnl[(ds, d)] = run_day(ds, d)
            print(f"  {ds} day {d}: {pooled4_pnl[(ds, d)]:>10,.0f}")

        # 3. LOO-CV across all 4 days
        print("\n" + "=" * 70)
        print("Leave-one-day-out CV (4 folds)")
        print("=" * 70)
        loo_pnl = {}
        for held_ds, held_d in DAY_KEYS:
            held_key = f"{held_ds}_d{held_d}"
            train_keys = [k for k in keys_4 if k != held_key]
            params = fit_params(df, train_keys)
            STRAT.write_text(patch_cfgs(original, params))
            loo_pnl[(held_ds, held_d)] = run_day(held_ds, held_d)
            print(f"  fit on {len(train_keys)} other days → test {held_ds} d{held_d}: "
                  f"{loo_pnl[(held_ds, held_d)]:>10,.0f}")
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"{'day':<14} {'cur means':>11} {'4-day pool':>12} {'LOO-CV':>10}")
    print("-" * 50)
    for ds, d in DAY_KEYS:
        k = (ds, d)
        print(f"{ds} d{d:<8} {cur_pnl[k]:>11,.0f} "
              f"{pooled4_pnl[k]:>12,.0f} {loo_pnl[k]:>10,.0f}")
    print("-" * 50)
    cur_mean = stat_mean(cur_pnl.values())
    p4_mean = stat_mean(pooled4_pnl.values())
    loo_mean = stat_mean(loo_pnl.values())
    cur_min = min(cur_pnl.values())
    p4_min = min(pooled4_pnl.values())
    loo_min = min(loo_pnl.values())
    print(f"{'mean':<14} {cur_mean:>11,.0f} {p4_mean:>12,.0f} {loo_mean:>10,.0f}")
    print(f"{'min':<14} {cur_min:>11,.0f} {p4_min:>12,.0f} {loo_min:>10,.0f}")
    print(f"{'mean+min':<14} {cur_mean+cur_min:>11,.0f} "
          f"{p4_mean+p4_min:>12,.0f} {loo_mean+loo_min:>10,.0f}")


if __name__ == "__main__":
    main()
