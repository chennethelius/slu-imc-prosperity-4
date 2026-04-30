#!/usr/bin/env python3
"""Round-5 mean-drift overfit test."""
import csv, json, shutil, subprocess, sys, tempfile
from pathlib import Path


def shift_prices(src_dir, dst_dir, drift):
    dst_dir.mkdir(parents=True, exist_ok=True)
    for f in src_dir.glob("prices_*.csv"):
        with open(f, newline="") as r, open(dst_dir / f.name, "w", newline="") as w:
            reader = csv.reader(r, delimiter=";"); writer = csv.writer(w, delimiter=";")
            header = next(reader); writer.writerow(header)
            price_cols = [i for i, h in enumerate(header) if "price" in h.lower()]
            for row in reader:
                for i in price_cols:
                    if not row[i] or row[i] == "": continue
                    try:
                        v = float(row[i])
                        v_new = max(1, round(v + drift))
                        row[i] = str(v_new)
                    except ValueError: pass
                writer.writerow(row)
    for f in src_dir.glob("trades_*.csv"):
        shutil.copy2(f, dst_dir / f.name)


def run(strategy, perturbed_dir, day, output_root):
    repo_root = Path(__file__).resolve().parent.parent
    target = repo_root / "datasets_extra" / "round5_data_drift"
    if target.exists(): shutil.rmtree(target)
    shutil.copytree(perturbed_dir, target)
    cmd = [
        sys.executable, str(repo_root / "scripts" / "imcbt_wrapper.py"),
        "--trader", str(strategy), "--dataset", "round5_data_drift",
        f"--day={day}", "--persist", "--artifact-mode", "full",
        "--output-root", str(output_root),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    shutil.rmtree(target, ignore_errors=True)
    if r.returncode != 0: return None
    runs = sorted(output_root.glob("backtest-*"))
    if not runs: return None
    try:
        m = json.loads((runs[-1] / "metrics.json").read_text())
        return m.get("final_pnl_total", 0.0)
    except Exception: return None


def main():
    if len(sys.argv) < 3:
        print("usage: mc_drift_r5.py <strategy.py> <day> [drifts]"); sys.exit(1)
    strategy = Path(sys.argv[1]); day = int(sys.argv[2])
    drifts = [float(d) for d in sys.argv[3].split(",")] if len(sys.argv) > 3 else [-50, -20, -10, 0, 10, 20, 50]
    repo_root = Path(__file__).resolve().parent.parent
    src = repo_root / "datasets_extra" / "round5_data"
    print(f"{strategy.name} on d{day}, mean-drift sweep:")
    for drift in drifts:
        with tempfile.TemporaryDirectory(prefix=f"d-{drift}-") as td:
            td = Path(td); shift_prices(src, td, drift)
            with tempfile.TemporaryDirectory(prefix=f"d-out-{drift}-") as out:
                pnl = run(strategy, td, day, Path(out))
        if pnl is None: print(f"  drift={drift:+}  FAILED")
        else:           print(f"  drift={drift:+}  PnL={pnl:>12,.0f}")


if __name__ == "__main__":
    main()
