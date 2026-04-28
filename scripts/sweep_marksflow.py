"""
Sweep Mark-flow gating parameters on z_take_marksflow.py.

(signal_ttl, boost, suppress)
  signal_ttl: ticks the Mark trade signal stays "fresh" before decay
  boost:      take_size multiplier when same-side smart Mark flow
  suppress:   take_size multiplier on adverse mark flow (0 = skip)
"""
import re, subprocess
from pathlib import Path
from statistics import mean as stat_mean

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
STRAT = REPO / "strategies" / "round4" / "tmp" / "z_take_marksflow.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]

VARIANTS = {
    "off":               (250,  1.0,  1.0),   # all gating disabled = z_take
    "ttl100_b15_s00":    (100,  1.5,  0.0),
    "ttl250_b15_s00":    (250,  1.5,  0.0),
    "ttl500_b15_s00":    (500,  1.5,  0.0),
    "ttl1000_b15_s00":   (1000, 1.5,  0.0),
    "ttl250_b20_s00":    (250,  2.0,  0.0),
    "ttl250_b15_s05":    (250,  1.5,  0.5),
    "ttl250_b10_s00":    (250,  1.0,  0.0),  # suppress only, no boost
    "ttl500_b10_s00":    (500,  1.0,  0.0),
    "ttl250_b10_s05":    (250,  1.0,  0.5),  # suppress half
    "ttl250_b20_s05":    (250,  2.0,  0.5),
    "ttl500_b20_s00":    (500,  2.0,  0.0),
}


def patch(src, ttl, b, s):
    out = src
    out = re.sub(r'DEFAULT_SIGNAL_TTL\s*=\s*\d+', f'DEFAULT_SIGNAL_TTL = {ttl}', out)
    out = re.sub(r'DEFAULT_BOOST\s*=\s*[\d.]+', f'DEFAULT_BOOST = {b}', out)
    out = re.sub(r'DEFAULT_SUPPRESS\s*=\s*[\d.]+', f'DEFAULT_SUPPRESS = {s}', out)
    return out


def run_day(dataset, day):
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


def main():
    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.mfbak")
    backup.write_text(original)

    results = {}
    try:
        for name, (ttl, b, s) in VARIANTS.items():
            STRAT.write_text(patch(original, ttl, b, s))
            per_day = [run_day(ds, d) for ds, d in DAY_KEYS]
            results[name] = per_day
            mn = stat_mean(per_day); mi = min(per_day)
            d0, d1, d2, d3 = per_day
            print(f"{name:<22}  d0={d0:>9,.0f}  d1={d1:>9,.0f}  "
                  f"d2={d2:>9,.0f}  d3={d3:>9,.0f}  mean={mn:>9,.0f}  "
                  f"min={mi:>9,.0f}  m+m={mn+mi:>10,.0f}", flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)

    rows = sorted(((n, stat_mean(p), min(p), stat_mean(p)+min(p)) for n,p in results.items()),
                  key=lambda r: r[3], reverse=True)
    print("\n" + "=" * 70)
    print(f"{'variant':<22}  {'mean':>10}  {'min':>10}  {'m+m':>11}")
    print("-" * 70)
    for n, mn, mi, ms in rows:
        marker = "  ←" if (n, mn, mi, ms) == rows[0] else ""
        print(f"{n:<22}  {mn:>10,.0f}  {mi:>10,.0f}  {ms:>11,.0f}{marker}")
    print("\n[reference] z_take.py @ take=17:  m+m=434,272")


if __name__ == "__main__":
    main()
