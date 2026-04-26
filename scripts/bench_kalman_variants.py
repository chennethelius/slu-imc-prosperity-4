"""
Benchmark several Kalman-MR strategy variants for HYDROGEL/VELVETFRUIT.

For each variant, runs all 3 days of round 3 and reports per-day PnL
plus spread-capture rate (% of own volume that crossed the book vs
filled inside the spread).

Variants (each modifies KALMAN_MR_PRODUCTS in hybrid.py via env vars
read by a small monkey-patch wrapper, then runs the Rust backtester).

Usage:
    python scripts/bench_kalman_variants.py
"""

import csv
import json
import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HYBRID = REPO / "strategies" / "round3" / "hybrid.py"
BACKUP = HYBRID.with_suffix(".py.bench_bak")
DATA = REPO / "backtester" / "datasets" / "round3"


VARIANTS = [
    # baseline — current params
    {"name": "baseline",                "h": {}, "v": {}},
    # tighter take_max_pay
    {"name": "tmp_neg3",                "h": {"take_max_pay": -3}, "v": {"take_max_pay": -1}},
    {"name": "tmp_neg6",                "h": {"take_max_pay": -6}, "v": {"take_max_pay": -2}},
    # delta threshold — only take if |delta| >= N
    {"name": "min_delta_30",            "h": {"take_min_delta": 30}, "v": {"take_min_delta": 30}},
    {"name": "min_delta_60",            "h": {"take_min_delta": 60}, "v": {"take_min_delta": 60}},
    # earn-spread mode (passive only when |fair-mid|<thresh AND |pos|<pct*limit)
    {"name": "earn_2_50pct",            "h": {"earn_spread_thresh": 2, "earn_spread_pos_pct": 0.5}, "v": {"earn_spread_thresh": 1, "earn_spread_pos_pct": 0.5}},
    {"name": "earn_5_75pct",            "h": {"earn_spread_thresh": 5, "earn_spread_pos_pct": 0.75}, "v": {"earn_spread_thresh": 2, "earn_spread_pos_pct": 0.75}},
    # close at zero crossing
    {"name": "close_on_cross",          "h": {"close_on_zero_cross": True}, "v": {"close_on_zero_cross": True}},
    # combinations
    {"name": "earn5_+_close",           "h": {"earn_spread_thresh": 5, "earn_spread_pos_pct": 0.75, "close_on_zero_cross": True},
                                         "v": {"earn_spread_thresh": 2, "earn_spread_pos_pct": 0.75, "close_on_zero_cross": True}},
    {"name": "tmp_neg3_+_close",        "h": {"take_max_pay": -3, "close_on_zero_cross": True},
                                         "v": {"take_max_pay": -1, "close_on_zero_cross": True}},
]


def patch_config(h_overrides, v_overrides):
    """Insert override fields into the HYDROGEL and VELVETFRUIT configs."""
    src = HYBRID.read_text()
    # Find and patch each KALMAN_MR_PRODUCTS entry
    for product, ovs in [("HYDROGEL_PACK", h_overrides), ("VELVETFRUIT_EXTRACT", v_overrides)]:
        pattern = re.compile(
            r'(\{\s*"product":\s*"' + product + r'".*?"quote_size":\s*\d+,\s*\})',
            re.DOTALL,
        )
        m = pattern.search(src)
        if not m:
            raise RuntimeError(f"could not locate config block for {product}")
        block = m.group(1)
        # Strip any existing override keys we control
        for key in ("take_min_delta", "earn_spread_thresh", "earn_spread_pos_pct",
                    "close_on_zero_cross"):
            block = re.sub(rf'\s*"{key}":\s*[^,\n]+,', "", block)
        # Override take_max_pay if requested
        if "take_max_pay" in ovs:
            block = re.sub(
                r'"take_max_pay":\s*-?\d+',
                f'"take_max_pay": {ovs["take_max_pay"]}',
                block,
            )
        # Inject other overrides before closing brace
        new_keys = []
        for key in ("take_min_delta", "earn_spread_thresh", "earn_spread_pos_pct",
                    "close_on_zero_cross"):
            if key in ovs:
                v = ovs[key]
                v_str = "True" if v is True else ("False" if v is False else str(v))
                new_keys.append(f'        "{key}": {v_str},')
        if new_keys:
            block = block.rstrip("}").rstrip().rstrip(",")
            block = block + ",\n" + "\n".join(new_keys) + "\n    }"
        src = src.replace(m.group(1), block, 1)
    HYBRID.write_text(src)


def run_backtest(day):
    proc = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(HYBRID), "--dataset", "round3", f"--day={day}",
         "--queue-penetration", "1.0", "--persist", "--products", "off"],
        cwd=REPO / "backtester", capture_output=True, text=True, check=True,
    )
    # Parse last run dir from output
    for line in proc.stdout.splitlines():
        m = re.search(r"runs/(backtest-\d+(?:-round3-day[+-]?\d)?)", line)
        if m:
            return REPO / "backtester" / "runs" / m.group(1)
    raise RuntimeError(f"no run dir parsed:\n{proc.stdout}")


def measure_run(run_dir, day):
    metrics = json.loads((run_dir / "metrics.json").read_text())
    pnl_by_p = metrics["final_pnl_by_product"]
    h_pnl = pnl_by_p.get("HYDROGEL_PACK", 0.0)
    v_pnl = pnl_by_p.get("VELVETFRUIT_EXTRACT", 0.0)
    total_pnl = metrics["final_pnl_total"]
    n_trades = metrics["own_trade_count"]
    # Spread capture
    book = {"HYDROGEL_PACK": {}, "VELVETFRUIT_EXTRACT": {}}
    with (DATA / f"prices_round_3_day_{day}.csv").open() as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["product"] in book and r["bid_price_1"] and r["ask_price_1"]:
                book[r["product"]][int(r["timestamp"])] = (
                    int(r["bid_price_1"]), int(r["ask_price_1"]),
                )
    crossed = inside = 0
    crossed_p = {"HYDROGEL_PACK": 0, "VELVETFRUIT_EXTRACT": 0}
    inside_p = {"HYDROGEL_PACK": 0, "VELVETFRUIT_EXTRACT": 0}
    with (run_dir / "trades.csv").open() as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["symbol"] not in book: continue
            if r["buyer"] != "SUBMISSION" and r["seller"] != "SUBMISSION": continue
            ts = int(r["timestamp"])
            sp = book[r["symbol"]].get(ts)
            if not sp: continue
            bb, ba = sp
            px = float(r["price"])
            qty = int(r["quantity"])
            is_buy = r["buyer"] == "SUBMISSION"
            if (is_buy and px >= ba) or (not is_buy and px <= bb):
                crossed += qty
                crossed_p[r["symbol"]] += qty
            elif (is_buy and px > bb) or (not is_buy and px < ba):
                inside += qty
                inside_p[r["symbol"]] += qty
    return {
        "h_pnl": h_pnl, "v_pnl": v_pnl, "total_pnl": total_pnl,
        "trades": n_trades, "crossed": crossed, "inside": inside,
        "h_crossed": crossed_p["HYDROGEL_PACK"], "h_inside": inside_p["HYDROGEL_PACK"],
        "v_crossed": crossed_p["VELVETFRUIT_EXTRACT"], "v_inside": inside_p["VELVETFRUIT_EXTRACT"],
    }


def main():
    shutil.copy(HYBRID, BACKUP)
    print(f"Backup of original: {BACKUP.name}")
    rows = []
    try:
        for var in VARIANTS:
            patch_config(var["h"], var["v"])
            day_results = []
            for day in (0, 1, 2):
                run_dir = run_backtest(day)
                day_results.append(measure_run(run_dir, day))
            # restore
            shutil.copy(BACKUP, HYBRID)
            day_pnls = [r["total_pnl"] for r in day_results]
            h_pnls = [r["h_pnl"] for r in day_results]
            v_pnls = [r["v_pnl"] for r in day_results]
            mean = sum(day_pnls) / 3
            mn = min(day_pnls)
            tot_crossed = sum(r["crossed"] for r in day_results)
            tot_inside = sum(r["inside"] for r in day_results)
            tot_vol = tot_crossed + tot_inside
            inside_pct = 100 * tot_inside / tot_vol if tot_vol else 0
            rows.append({
                "name": var["name"],
                "h_pnl_3d": sum(h_pnls),
                "v_pnl_3d": sum(v_pnls),
                "tot_3d": sum(day_pnls),
                "mean": mean, "min": mn,
                "trades_3d": sum(r["trades"] for r in day_results),
                "inside_pct": inside_pct,
                "tot_vol": tot_vol,
            })
            print(f"  {var['name']:<22} mean/day={mean:>10.0f}  min/day={mn:>10.0f}  "
                  f"trades={rows[-1]['trades_3d']:>5}  inside={inside_pct:>5.1f}%  "
                  f"H_3d={sum(h_pnls):>9.0f} V_3d={sum(v_pnls):>9.0f}")
    finally:
        shutil.copy(BACKUP, HYBRID)
        print(f"\nRestored {HYBRID.name} from backup.")

    print("\n\n" + "=" * 110)
    print("SUMMARY (per-day PnL is what matters for IMC single-day submissions)")
    print("=" * 110)
    print(f"{'variant':<22} {'mean/day':>10} {'min/day':>10} {'tot_3d':>10} "
          f"{'H_3d':>9} {'V_3d':>9} {'trades':>6} {'%inside':>8}")
    for r in sorted(rows, key=lambda x: -x["mean"]):
        print(f"{r['name']:<22} {r['mean']:>10.0f} {r['min']:>10.0f} {r['tot_3d']:>10.0f} "
              f"{r['h_pnl_3d']:>9.0f} {r['v_pnl_3d']:>9.0f} "
              f"{r['trades_3d']:>6} {r['inside_pct']:>7.1f}%")


if __name__ == "__main__":
    main()
