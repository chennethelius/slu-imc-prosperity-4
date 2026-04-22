"""MC parameter stability test for Round 1 strategies (see mc_base.py)."""
import sys
from mc_base import run_mc

if __name__ == "__main__":
    run_mc(
        dataset="round1",
        days=("-2", "-1", "0"),
        default_strategies=["disc_meta_v1", "disc_spread_regime", "disc_spartan_band"],
        argv=sys.argv[1:],
    )
