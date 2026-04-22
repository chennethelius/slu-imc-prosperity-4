"""MC parameter stability test for Round 2 strategies (see mc_base.py)."""
import sys
from mc_base import run_mc

if __name__ == "__main__":
    run_mc(
        dataset="round2",
        days=("-1", "0", "1"),
        default_strategies=["disc_meta_v1", "disc_spread_regime", "disc_spartan_band"],
        argv=sys.argv[1:],
    )
