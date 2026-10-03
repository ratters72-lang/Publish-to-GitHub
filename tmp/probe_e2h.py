"""tmp/probe_e2h.py — print the E2h clamp scenario's real numbers."""

import sys

sys.path.insert(0, ".")

from player.player import Player
from game.physics import contact_space_miss as csm

DT = csm.DT_FRAME_S


class _LiveWorld:
    def __init__(self):
        self.frame_count = 0
        self.attacking_direction = 1


lw = _LiveWorld()
pc = Player(2.0, 35.0, player_id=1)
pd = Player(0.5, 35.0, player_id=2)
pc._world_ref = lw
pd._world_ref = lw
pc.vx, pd.vx = -6.0, 6.0

res = csm.apply_contact_space_miss(lw, pc, pd)
print("declared defender travel =", res.defender_travel_m)
print("declared carrier  travel =", res.carrier_travel_m)
print("start pd.x =", pd.x, " pc.x =", pc.x)

for i in range(csm.REBOUND_FRAMES):
    lw.frame_count += 1
    pc.update(DT)
    pd.update(DT)
    if i < 3 or i >= csm.REBOUND_FRAMES - 2:
        print("frame %2d: pd.x=%.4f pc.x=%.4f pd.rebound=%s"
              % (i, pd.x, pc.x, getattr(pd, "_csm_rebound", None) is not None))

print("end pd.x =", pd.x, " pc.x =", pc.x)
print("(0.5 - pd.x) =", 0.5 - pd.x)
print("(0.5 - pd.x) <= declared + 1e-9  ->",
      0.5 - pd.x <= res.defender_travel_m + 1e-9)
print("pd.frozen =", pd.frozen, "pd.freeze_timer =", pd.freeze_timer)
print("pd.target_x =", pd.target_x)
