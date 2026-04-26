"""
Calibrate the local Rust backtester against the most recent real
Prosperity sandbox log.

Workflow:
  1. Find the most-recently-modified numbered folder at the repo root
     (e.g. 446770/, 450745/) — these are dropped in by the user from the
     IMC sandbox UI.
  2. Read the real PnL from <id>.json (the "profit" field).
  3. Run the actually-submitted trader file <id>.py through the Rust
     backtester against round3 day 2 with --max-timestamp=99900 (the
     sandbox is the first 1,000 ticks of day 2).
  4. Sweep --queue-penetration ∈ {0.0, 0.25, 0.5, 0.75, 1.0} and report
     the value that matches real PnL most closely.

The backtester is *calibrated* if the closest-matching QP delta is
within ~5% of real PnL. If it drifts further, investigate fill model
or check whether IMC changed sandbox behavior.
"""

import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def find_latest_log_folder() -> Path:
    candidates = [
        p for p in REPO.iterdir()
        if p.is_dir() and re.fullmatch(r"\d+", p.name)
        and (p / f"{p.name}.json").is_file()
        and (p / f"{p.name}.py").is_file()
    ]
    if not candidates:
        raise SystemExit("No numbered sandbox-log folders found at repo root.")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def detect_day_and_max_ts(log_path: Path) -> tuple[int, int]:
    """Read first/last activitiesLog row to learn which day and how many ticks
    the sandbox actually ran. Newer sandboxes run a full 10k-tick day; older
    ones ran only the first 1k ticks of an existing dataset day."""
    payload = json.loads(log_path.read_text())
    activities = payload["activitiesLog"]
    lines = activities.split("\n")
    # lines[0] = header, lines[1] = first row, last non-empty = last row
    first = lines[1].split(";")
    last = next(l for l in reversed(lines) if l).split(";")
    day = int(first[0])
    max_ts = int(last[1])
    return day, max_ts


def main():
    log_dir = find_latest_log_folder()
    log_id = log_dir.name
    json_path = log_dir / f"{log_id}.json"
    py_path = log_dir / f"{log_id}.py"
    log_path = log_dir / f"{log_id}.log"

    payload = json.loads(json_path.read_text())
    real_pnl = float(payload["profit"])
    round_num = int(payload.get("round", 3))
    day, max_ts = detect_day_and_max_ts(log_path)
    print(f"Reference: {log_dir.name}/  status={payload.get('status')}  round={round_num}  day={day}  max_ts={max_ts}")
    print(f"Real PnL:  {real_pnl:+,.2f}")
    print()

    dataset = f"round{round_num}"
    day_prefix_plus = f"D+{day}"
    day_prefix_eq = f"D={day}"

    print(f"{'QP':>5}  {'backtester':>12}  {'delta':>10}  {'pct_err':>8}")
    best = None
    for qp in (0.0, 0.25, 0.5, 0.75, 1.0):
        proc = subprocess.run(
            ["cargo", "run", "--release", "--quiet", "--",
             "--trader", str(py_path),
             "--dataset", dataset, f"--day={day}",
             f"--max-timestamp={max_ts}",
             f"--queue-penetration={qp}",
             "--products", "summary"],
            cwd=REPO / "backtester", capture_output=True, text=True,
        )
        if proc.returncode != 0:
            print(f"  qp={qp} FAILED: {proc.stderr[:200]}")
            continue
        # Parse FINAL_PNL from "D+<day> ... <ticks> <trades> <pnl>" row
        pnl = None
        for line in proc.stdout.splitlines():
            if line.lstrip().startswith(day_prefix_plus) or line.lstrip().startswith(day_prefix_eq):
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        pnl = float(parts[4])
                    except ValueError:
                        pass
        if pnl is None:
            print(f"  qp={qp} could not parse PnL; raw output:\n{proc.stdout}")
            continue
        delta = pnl - real_pnl
        pct = 100.0 * abs(delta) / abs(real_pnl) if real_pnl else float("inf")
        if best is None or abs(delta) < abs(best[1] - real_pnl):
            best = (qp, pnl)
        print(f"  {qp:>3}  {pnl:>12,.2f}  {delta:>+10,.2f}  {pct:>7.2f}%")

    if best is None:
        raise SystemExit("No backtests succeeded.")

    bqp, bpnl = best
    bdelta = bpnl - real_pnl
    bpct = 100.0 * abs(bdelta) / abs(real_pnl) if real_pnl else float("inf")
    print()
    print(f"Best calibration: --queue-penetration {bqp}")
    print(f"  backtester {bpnl:+,.2f}  vs  real {real_pnl:+,.2f}  ({bdelta:+,.2f}, {bpct:.2f}% error)")
    if bpct > 10:
        print("  WARNING: error >10%; investigate fill-model drift before trusting backtests.")


if __name__ == "__main__":
    main()
