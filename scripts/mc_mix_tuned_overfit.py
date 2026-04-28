"""
MC overfit check for the final tuned mix.

Compares three strategies under identical Gaussian price perturbation:
  - baseline   (z_take.py:               uniform z=1.0, t=17, static mean)
  - hybrid     (z_take_hybrid.py:        previous best non-tmp trader)
  - mix_tuned  (tmp/z_take_per_asset_mix.py: tuned z-take + HP from no_marks
                                         + buy-and-hold VEV_6000/6500)

Same seed across strategies and noise levels so deltas are directly
comparable. The tuned mix stacks several layers of in-sample tuning
(per-asset z, t, prior all jointly optimized), so the gain should hold
up under perturbation OR shrink monotonically as noise grows; a flip
to negative would mean overfit.

Usage:  python scripts/mc_mix_tuned_overfit.py [n_seeds]
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
MC_R3 = BT_DIR / "datasets" / "round3_mc_mix"
MC_R4 = BT_DIR / "datasets" / "round4_mc_mix"

STRATS = {
    "baseline":  REPO / "strategies" / "round4" / "z_take.py",
    "hybrid":    REPO / "strategies" / "round4" / "z_take_hybrid.py",
    "mix_tuned": REPO / "strategies" / "round4" / "z_take_per_asset_mix.py",
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
    def _mm(per_day):
        return sum(per_day) / 4 + min(per_day)
    base_score_unperturbed = _mm(base["baseline"])
    base_gaps = {tag: _mm(base[tag]) - base_score_unperturbed
                 for tag in STRATS if tag != "baseline"}
    print()
    for tag, g in base_gaps.items():
        print(f"  {tag} − baseline m+m gap: {g:+,.0f}")

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
                by_tag = {r[0]: r for r in row}
                bt = by_tag["baseline"]
                hy = by_tag["hybrid"]
                mx = by_tag["mix_tuned"]
                print(f"  seed {seed}:  base={fmt(bt[3])}  "
                      f"hybrid={fmt(hy[3])} (Δ={hy[3]-bt[3]:>+9,.0f})  "
                      f"mix_tuned={fmt(mx[3])} (Δ={mx[3]-bt[3]:>+9,.0f})",
                      flush=True)
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

    # Per-noise gap vs baseline, for each tuned variant
    print("\n" + "=" * 92)
    print("GAP vs baseline m+m, per noise level")
    print("=" * 92)
    print(f"{'noise':>6}  {'strat':<10}  {'base m+m':>11}  {'tuned m+m':>11}  "
          f"{'Δ m+m':>11}  {'in-sample Δ':>12}  {'verdict':>18}")
    print("-" * 92)
    for noise in NOISE_SDS:
        bt = next(r for r in rows if r[0] == noise and r[1] == "baseline")
        for tag in STRATS:
            if tag == "baseline":
                continue
            tu = next(r for r in rows if r[0] == noise and r[1] == tag)
            delta = tu[6] - bt[6]
            ref = base_gaps[tag]
            if delta >= ref * 0.5:
                v = "HOLDS UP"
            elif delta > 0:
                v = "PARTIAL DECAY"
            else:
                v = "OVERFIT — REJECT"
            print(f"{noise:>6.1f}  {tag:<10}  {fmt(bt[6],11)}  {fmt(tu[6],11)}  "
                  f"{fmt(delta,11)}  {fmt(ref,12)}  {v:>18}")

    print("\nIn-sample gaps (unperturbed):")
    for tag, g in base_gaps.items():
        print(f"  {tag}: {g:+,.0f}")
    print("If MC Δ collapses or flips negative as noise grows → overfit.")


if __name__ == "__main__":
    main()
