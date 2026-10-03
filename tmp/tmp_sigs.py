"""Throwaway: print the exact signatures of the two adapters the suite needs."""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game.defence.defence_lvl_1 import defensive_depth_roles as dr
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    _fullback_tackle_candidates,
)

print("assign_depth_roles:", inspect.signature(dr.assign_depth_roles))
print("force_reseed_depth_roles:", inspect.signature(dr.force_reseed_depth_roles))
print("_fullback_tackle_candidates:", inspect.signature(_fullback_tackle_candidates))
print("_fullback_tackle_candidates doc:")
print((_fullback_tackle_candidates.__doc__ or "").strip()[:1200])
