"""Direct MC comparison: take=10 vs take=17 (vs current take=50 baseline).

6 seeds × 3 variants × 4 days = 72 perturbed backtests.

Usage: python scripts/mc_take_10_vs_17.py
"""
import csv
import random
import re
import shutil
import subprocess
from pathlib import Path
from statistics import mean, stdev

REPO = Path(__file__).resolve().parent.parent
BT_DIR = REPO / "backtester"
DATA_R3 = BT_DIR / "datasets" / "round3"
DATA_R4 = BT_DIR / "datasets" / "round4"
MC_R3 = BT_DIR / "datasets" / "round3_mc_t1017"
MC_R4 = BT_DIR / "datasets" / "round4_mc_t1017"
STRAT = REPO / "strategies" / "round4" / "z_take.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]
TAKES = [10, 17, 50]
N_SEEDS = 6
NOISE_SD = 1.0


def perturb_one(src_dir, dst_dir, rng):
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    dst_dir.mkdir(parents=True)
    for f in src_dir.glob("prices_*.csv"):
        with open(f, newline="") as r, open(dst_dir / f.name, "w", newline="") as w:
            reader = csv.reader(r, delimiter=";")
            writer = csv.writer(w, delimiter=";")
            header = next(reader)
            writer.writerow(header)
            cols = [i for i, h in enumerate(header) if "price" in h.lower()]
            for row in reader:
                for i in cols:
                    if not row[i]:
                        continue
                    try:
                        v = float(row[i])
                        row[i] = str(int(round(v + rng.gauss(0, NOISE_SD))))
                    except ValueError:
                        pass
                writer.writerow(row)
    for f in src_dir.glob("trades_*.csv"):
        shutil.copy2(f, dst_dir / f.name)


def perturb_all(seed):
    rng = random.Random(seed)
    perturb_one(DATA_R3, MC_R3, rng)
    perturb_one(DATA_R4, MC_R4, rng)


def patch_take(src, take):
    return re.sub(r'("take_size"\s*:\s*)\d+', rf'\g<1>{take}', src)


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


def run_4(use_mc):
    out = []
    for ds, d in DAY_KEYS:
        target = (str(MC_R3) if ds == "round3" else str(MC_R4)) if use_mc else ds
        out.append(run_day(target, d))
    return out


def main():
    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.cmpbak")
    backup.write_text(original)

    base = {}
    mc = {t: [] for t in TAKES}

    try:
        print("=== BASELINE (unperturbed) ===")
        for t in TAKES:
            STRAT.write_text(patch_take(original, t))
            per_day = run_4(use_mc=False)
            base[t] = per_day
            mn, mi = mean(per_day), min(per_day)
            print(f"  take={t:>2}  d0={per_day[0]:>9,.0f}  d1={per_day[1]:>9,.0f}  "
                  f"d2={per_day[2]:>9,.0f}  d3={per_day[3]:>9,.0f}  "
                  f"mean={mn:>9,.0f}  min={mi:>9,.0f}  m+m={mn+mi:>10,.0f}",
                  flush=True)

        print(f"\n=== MC ({N_SEEDS} seeds, noise_sd={NOISE_SD}) ===")
        for seed in range(N_SEEDS):
            perturb_all(seed)
            print(f"\n  seed {seed}:")
            for t in TAKES:
                STRAT.write_text(patch_take(original, t))
                per_day = run_4(use_mc=True)
                mc[t].append(per_day)
                mn, mi = mean(per_day), min(per_day)
                print(f"    take={t:>2}  mean={mn:>9,.0f}  min={mi:>9,.0f}  "
                      f"m+m={mn+mi:>10,.0f}", flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)
        for d in (MC_R3, MC_R4):
            if d.exists():
                shutil.rmtree(d)

    print("\n" + "=" * 75)
    print(f"SUMMARY  (baseline + {N_SEEDS} MC seeds @ noise_sd={NOISE_SD})")
    print("=" * 75)
    print(f"{'take':>5}  {'base mean':>10}  {'base min':>10}  "
          f"{'mc mean μ':>10}  {'mc min μ':>10}  {'mc m+m μ':>10}  {'σ/μ':>6}")
    print("-" * 75)
    for t in TAKES:
        b = base[t]
        b_mean = mean(b)
        b_min = min(b)
        seed_means = [mean(s) for s in mc[t]]
        seed_mins = [min(s) for s in mc[t]]
        seed_scores = [m + mi for m, mi in zip(seed_means, seed_mins)]
        mu_mean = mean(seed_means)
        mu_min = mean(seed_mins)
        mu_score = mean(seed_scores)
        sd_score = stdev(seed_scores) if len(seed_scores) > 1 else 0.0
        cv = sd_score / max(1.0, mu_score)
        print(f"{t:>5}  {b_mean:>10,.0f}  {b_min:>10,.0f}  "
              f"{mu_mean:>10,.0f}  {mu_min:>10,.0f}  {mu_score:>10,.0f}  "
              f"{100*cv:>4.2f}%")


if __name__ == "__main__":
    main()
