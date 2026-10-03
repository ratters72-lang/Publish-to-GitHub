"""Confirm RUCK_ADJACENT_A_Y_OFFSET_M exists in DefenceTuners."""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

t = DefenceTuners()
print("has_attr =", hasattr(t, "RUCK_ADJACENT_A_Y_OFFSET_M"))
print("value =", getattr(t, "RUCK_ADJACENT_A_Y_OFFSET_M", None))
print("width_curve_factor =", t.width_curve_factor)
