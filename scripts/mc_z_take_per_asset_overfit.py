"""
MC overfit check for the per-asset (z_thresh, take_size) tuned variant.

Compares baseline z_take.py (uniform z=1.0, t=17) against the per-asset-tuned
variant under Gaussian price perturbation. Both strategies see the SAME
perturbed datasets (same RNG seed across noise levels) so their numbers are
directly comparable.

Output: per-noise-level (μ, σ, σ/μ, m+m) for both strategies, plus the
per-day-mean+min DELTA between tuned and baseline. The hope: tuned gap stays
positive (or ≈zero) and σ/μ stays small at every noise level. If the gap
flips negative as noise grows, the tuning was overfit.

Usage:  python scripts/mc_z_take_per_asset_overfit.py [n_seeds]
"""
import csv
import random
import shutil
import subprocess
import sys
from pathlib import Path
from statistics import mean, stdev

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
DATA_R3 = BT_DIR / "datasets" / "round3"
DATA_R4 = BT_DIR / "datasets" / "round4"
MC_R3 = BT_DIR / "datasets" / "round3_mc_perasset"
MC_R4 = BT_DIR / "datasets" / "round4_mc_perasset"

STRATS = {
    "baseline":  REPO / "strategies" / "round4" / "z_take.py",
    "per_asset": REPO / "strategies" / "round4" / "tmp" / "z_take_per_asset.py",
}

DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]
NOISE_SDS = (0.5, 1.0, 2.0)


def perturb_one(src: Path, dst: Path, rng: random.Random, noise_sd: float) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for f in src.glob("prices_*.csv"):
        with open(f, newline="") as r, open(dst / f.name, "w", newline="") as w:
            reader = csv.reader(r, delimiter=";")
            writer = csv.writer(w, delimiter=";")
            header = next(reader)
            writer.writerow(header)
            price_cols = [i for i, h in enumerate(header) if "price" in h.lower()]
            for row in reader:
                for i in price_cols:
                    if not row[i]:
                        continue
                    try:
                        v = float(row[i])
                        row[i] = str(int(round(v + rng.gauss(0, noise_sd))))
                    except ValueError:
                        pass
                writer.writerow(row)
    for f in src.glob("trades_*.csv"):
        shutil.copy2(f, dst / f.name)


def perturb_all(seed: int, noise_sd: float) -> None:
    rng = random.Random(seed)
    perturb_one(DATA_R3, MC_R3, rng, noise_sd)
    perturb_one(DATA_R4, MC_R4, rng, noise_sd)


def run_one_day(strat: Path, dataset: str, day: int) -> float:
    r = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--",
         "--trader", str(strat), "--dataset", dataset, f"--day={day}",
         "--queue-penetration", "1.0", "--products", "summary",
         "--artifact-mode", "none"],
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


def run_4_days(strat: Path, use_mc: bool) -> list[float]:
    out = []
    for ds, d in DAY_KEYS:
        target = (str(MC_R3) if ds == "round3" else str(MC_R4)) if use_mc else ds
        out.append(run_one_day(strat, target, d))
    return out


def fmt(x: float, w: int = 9) -> str:
    return f"{x:>{w},.0f}"


def main() -> None:
    n_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 6

    # ===== Baselines (unperturbed) =====
    print("=" * 78)
    print("BASELINES (unperturbed)")
    print("=" * 78)
    base: dict[str, list[float]] = {}
    for tag, strat in STRATS.items():
        per_day = run_4_days(strat, use_mc=False)
        base[tag] = per_day
        mn, mi = sum(per_day) / 4, min(per_day)
        print(f"  {tag:<10}  d0={fmt(per_day[0])}  d1={fmt(per_day[1])}  "
              f"d2={fmt(per_day[2])}  d3={fmt(per_day[3])}  "
              f"mean={fmt(mn)}  min={fmt(mi)}  m+m={fmt(mn+mi,10)}")
    base_gap = (sum(base["per_asset"]) / 4 + min(base["per_asset"])) - \
               (sum(base["baseline"]) / 4 + min(base["baseline"]))
    print(f"\n  per_asset − baseline m+m gap: {base_gap:+,.0f}")

    # ===== MC sweep =====
    # results[(tag, noise)] = list of n_seeds × per-day [d0,d1,d2,d3]
    results: dict[tuple[str, float], list[list[float]]] = {
        (t, n): [] for t in STRATS for n in NOISE_SDS
    }
    n_runs = n_seeds * len(NOISE_SDS) * 4 * len(STRATS)
    print(f"\nMC: {n_seeds} seeds × {len(NOISE_SDS)} noise × 4 days × "
          f"{len(STRATS)} strats = {n_runs} backtests\n")

    try:
        for noise in NOISE_SDS:
            print(f"--- noise_sd = {noise} ---")
            for seed in range(n_seeds):
                perturb_all(seed, noise)
                row = []
                for tag, strat in STRATS.items():
                    per_day = run_4_days(strat, use_mc=True)
                    results[(tag, noise)].append(per_day)
                    mn, mi = sum(per_day) / 4, min(per_day)
                    row.append((tag, mn, mi, mn + mi))
                bt = next(r for r in row if r[0] == "baseline")
                pa = next(r for r in row if r[0] == "per_asset")
                print(f"  seed {seed}:  base m+m={fmt(bt[3])}  "
                      f"per_asset m+m={fmt(pa[3])}  "
                      f"Δ={pa[3]-bt[3]:>+10,.0f}", flush=True)
            print()
    finally:
        for d in (MC_R3, MC_R4):
            if d.exists():
                shutil.rmtree(d)

    # ===== Summary =====
    print("=" * 92)
    print(f"SUMMARY  ({n_seeds} seeds per noise level)")
    print("=" * 92)
    print(f"{'noise':>6}  {'strat':<10}  {'mean μ':>10}  {'mean σ':>9}  "
          f"{'σ/μ':>6}  {'min μ':>10}  {'m+m μ':>11}  {'verdict':>9}")
    print("-" * 92)

    rows = []
    for noise in NOISE_SDS:
        for tag in STRATS:
            seed_means = [sum(s) / 4 for s in results[(tag, noise)]]
            seed_mins = [min(s) for s in results[(tag, noise)]]
            mu = mean(seed_means)
            sd = stdev(seed_means) if len(seed_means) > 1 else 0.0
            cv = sd / max(1.0, abs(mu))
            mu_min = mean(seed_mins)
            score = mu + mu_min
            verdict = "ROBUST" if cv < 0.02 else "MAYBE" if cv < 0.05 else "OVERFIT"
            rows.append((noise, tag, mu, sd, cv, mu_min, score, verdict))
            print(f"{noise:>6.1f}  {tag:<10}  {fmt(mu,10)}  {fmt(sd,9)}  "
                  f"{100*cv:>5.2f}%  {fmt(mu_min,10)}  {fmt(score,11)}  {verdict:>9}")

    # Per-noise gap (per_asset − baseline)
    print("\n" + "=" * 78)
    print("GAP: per_asset m+m − baseline m+m, per noise level")
    print("=" * 78)
    print(f"{'noise':>6}  {'base m+m':>11}  {'per_asset m+m':>14}  "
          f"{'Δ m+m':>11}  {'verdict':>22}")
    print("-" * 78)
    for noise in NOISE_SDS:
        bt = next(r for r in rows if r[0] == noise and r[1] == "baseline")
        pa = next(r for r in rows if r[0] == noise and r[1] == "per_asset")
        delta = pa[6] - bt[6]
        if delta >= base_gap * 0.5:
            v = "HOLDS UP"
        elif delta > 0:
            v = "PARTIAL DECAY"
        else:
            v = "OVERFIT — REJECT"
        print(f"{noise:>6.1f}  {fmt(bt[6],11)}  {fmt(pa[6],14)}  "
              f"{fmt(delta,11)}  {v:>22}")

    print(f"\nIn-sample gap (unperturbed): {base_gap:+,.0f}")
    print("If MC Δ collapses or flips negative as noise grows → overfit.")


if __name__ == "__main__":
    main()
