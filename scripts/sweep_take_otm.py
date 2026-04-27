"""
Take-size sweep with OTM dampening + MC robustness check.

Variants tested (all with z_thresh=1.0, 4-day pooled means):
  baseline_50      everyone at 50  (current z_take.py)
  flat_20          everyone at 20  (winner of size sweep)
  flat_10          everyone at 10
  otm_15           5200+ at 15, rest at 20
  otm_10           5200+ at 10, rest at 20
  otm_5            5200+ at  5, rest at 20
  near_30          5000-5100 at 30, rest at 20  (bigger on best products)
  near_50          5000-5100 at 50, rest at 20

For each variant: baseline (unperturbed) + 4-seed MC at noise_sd=1.0,
all 4 days. Score by per-day mean+min.

Usage: python scripts/sweep_take_otm.py
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
MC_R3 = BT_DIR / "datasets" / "round3_mc_otm"
MC_R4 = BT_DIR / "datasets" / "round4_mc_otm"
STRAT = REPO / "strategies" / "round4" / "z_take.py"
DAY_KEYS = [("round3", 0), ("round4", 1), ("round4", 2), ("round4", 3)]
N_SEEDS = 4
NOISE_SD = 1.0

# variant: dict from symbol → take_size
NEAR = {"VEV_5000", "VEV_5100"}
OTM = {"VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500"}
ALL_SYMS = ["HYDROGEL_PACK", "VELVETFRUIT_EXTRACT",
            "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100",
            "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500"]


def make_var(default: int, near: int | None = None, otm: int | None = None) -> dict[str, int]:
    out = {s: default for s in ALL_SYMS}
    if near is not None:
        for s in NEAR:
            out[s] = near
    if otm is not None:
        for s in OTM:
            out[s] = otm
    return out


VARIANTS = {
    "baseline_50":   make_var(50),
    "flat_20":       make_var(20),
    "flat_10":       make_var(10),
    "otm_15":        make_var(20, otm=15),
    "otm_10":        make_var(20, otm=10),
    "otm_5":         make_var(20, otm=5),
    "near_30":       make_var(20, near=30),
    "near_50":       make_var(20, near=50),
}


def perturb_one(src_dir: Path, dst_dir: Path, rng: random.Random) -> None:
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    dst_dir.mkdir(parents=True)
    for f in src_dir.glob("prices_*.csv"):
        with open(f, newline="") as r, open(dst_dir / f.name, "w", newline="") as w:
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
                        row[i] = str(int(round(v + rng.gauss(0, NOISE_SD))))
                    except ValueError:
                        pass
                writer.writerow(row)
    for f in src_dir.glob("trades_*.csv"):
        shutil.copy2(f, dst_dir / f.name)


def perturb_all(seed: int) -> None:
    rng = random.Random(seed)
    perturb_one(DATA_R3, MC_R3, rng)
    perturb_one(DATA_R4, MC_R4, rng)


def patch_per_product(src: str, sym_to_take: dict[str, int]) -> str:
    out = src
    for sym, take in sym_to_take.items():
        pat = rf'(\{{\s*"symbol":\s*"{re.escape(sym)}"[^}}]*?"take_size"\s*:\s*)\d+'
        out = re.sub(pat, rf'\g<1>{take}', out, count=1, flags=re.S)
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


def run_4(use_mc: bool) -> list[float]:
    out = []
    for ds, d in DAY_KEYS:
        target = (str(MC_R3) if ds == "round3" else str(MC_R4)) if use_mc else ds
        out.append(run_day(target, d))
    return out


def main() -> None:
    original = STRAT.read_text()
    backup = STRAT.with_suffix(".py.osbak")
    backup.write_text(original)

    base_pnl: dict[str, list[float]] = {}
    mc_pnl: dict[str, list[list[float]]] = {v: [] for v in VARIANTS}

    try:
        # Baselines
        print("=== BASELINES (unperturbed) ===")
        for vname, sym_to_take in VARIANTS.items():
            STRAT.write_text(patch_per_product(original, sym_to_take))
            per_day = run_4(use_mc=False)
            base_pnl[vname] = per_day
            mn, mi = mean(per_day), min(per_day)
            print(f"  {vname:<14}  d0={per_day[0]:>8,.0f}  d1={per_day[1]:>8,.0f}  "
                  f"d2={per_day[2]:>8,.0f}  d3={per_day[3]:>8,.0f}  "
                  f"mean={mn:>8,.0f}  min={mi:>8,.0f}  m+m={mn+mi:>10,.0f}",
                  flush=True)

        # MC
        print(f"\n=== MC ({N_SEEDS} seeds, noise_sd={NOISE_SD}) ===")
        for seed in range(N_SEEDS):
            perturb_all(seed)
            print(f"\n  seed {seed}:")
            for vname, sym_to_take in VARIANTS.items():
                STRAT.write_text(patch_per_product(original, sym_to_take))
                per_day = run_4(use_mc=True)
                mc_pnl[vname].append(per_day)
                mn, mi = mean(per_day), min(per_day)
                print(f"    {vname:<14}  mean={mn:>8,.0f}  min={mi:>8,.0f}  "
                      f"m+m={mn+mi:>10,.0f}", flush=True)
    finally:
        STRAT.write_text(original)
        backup.unlink(missing_ok=True)
        for d in (MC_R3, MC_R4):
            if d.exists():
                shutil.rmtree(d)

    # Summary
    print("\n" + "=" * 90)
    print(f"SUMMARY  ({N_SEEDS} MC seeds @ noise_sd={NOISE_SD})")
    print("=" * 90)
    print(f"{'variant':<14}  {'base m+m':>10}  {'mc m+m μ':>10}  {'mc σ':>9}  "
          f"{'σ/μ':>7}  {'verdict':>9}")
    print("-" * 90)
    rows = []
    for v in VARIANTS:
        b = base_pnl[v]
        b_score = mean(b) + min(b)
        seed_scores = [mean(s) + min(s) for s in mc_pnl[v]]
        mu = mean(seed_scores)
        sd = stdev(seed_scores) if len(seed_scores) > 1 else 0.0
        cv = sd / max(1.0, abs(mu))
        verdict = "ROBUST" if cv < 0.02 else "MAYBE" if cv < 0.05 else "OVERFIT"
        rows.append((v, b_score, mu, sd, cv, verdict))
    rows.sort(key=lambda r: r[2], reverse=True)
    for v, b, mu, sd, cv, verdict in rows:
        print(f"{v:<14}  {b:>10,.0f}  {mu:>10,.0f}  {sd:>9,.0f}  "
              f"{100*cv:>5.2f}%  {verdict:>9}")


if __name__ == "__main__":
    main()
