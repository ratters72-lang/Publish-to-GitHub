"""Temp: why is a bare Player exempt from the anchor pin?"""

import sys
from types import SimpleNamespace as NS
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1 import retreat_obligation as ro

world = NS(ruck_x_m=30.0, frame_count=1, ball_carrier=None, ball_owner="BC",
           tes_active=False, defensive_line_anchor=None)

d = Player(x=35.0, y=35.0, player_id=1)
d.defender_role = DefenderRole.DEF_LINE_READY
d.depth_class = "PASSIVE"

print("depth_role        =", repr(getattr(d, "depth_role", "<MISSING>")))
print("depth_side        =", repr(getattr(d, "depth_side", "<MISSING>")))
print("depth_class       =", repr(d.depth_class))
print("defender_role     =", getattr(d.defender_role, "name", d.defender_role))
print("fullback_active   =", getattr(d, "fullback_role_active", None))
print("frozen            =", getattr(d, "frozen", None),
      "freeze_timer =", getattr(d, "freeze_timer", None))

lane_x_of, is_drive_phase, tuners = ro._pin_helpers()
print("helpers available =", lane_x_of is not None)
print("WIDTH_OFFSETS     =", dict(getattr(tuners, "WIDTH_OFFSETS", {})))

try:
    print("lane_x_of(d)      =", lane_x_of(d, world, tuners))
except Exception as exc:
    print("lane_x_of RAISED  :", type(exc).__name__, exc)

try:
    print("is_drive_phase    =", is_drive_phase(world))
except Exception as exc:
    print("is_drive_phase RAISED:", type(exc).__name__, exc)

try:
    from game.defence.defence_lvl_1.width_authority import is_width_eligible
    print("is_width_eligible =", is_width_eligible(d, world, tuners))
except Exception as exc:
    print("is_width_eligible RAISED:", type(exc).__name__, exc)

print("role in PIN_EXEMPT=", ro._role_name(d) in ro._PIN_EXEMPT_ROLE_NAMES)
print("ENGAGED           =", d.depth_class in ro._ENGAGED_DEPTH_CLASSES)
print("is_anchor_pin_exempt =", ro.is_anchor_pin_exempt(d, world))
