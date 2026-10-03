"""Behavioural probe for the FRONT-HOLD / HEAD-ON FLAT-FOOT change.

Proves, against the real ``pre_tackle_authority``:

  P1  an AHEAD committed body's commit point sits IN FRONT of the carrier
      (the legacy formula aimed it BEHIND at ``bc.x - depth_offset``);
  P2  the same body is PLANTED (flat-footed) once the CARRIER is inside the
      head-on plant radius, with zero velocity and movement_state HOLD;
  P3  the plant fires before the legacy plant radius (0.8 m) would;
  P4  with ``pretackle_front_hold_enabled = False`` the geometry reverts
      byte-for-byte to the legacy back-pedal point.

Writes tmp/probe_front_hold.txt (UTF-8) and exits non-zero on any failure.
"""

import math
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from player.player import DefenderRole
from game.defence.defence_lvl_1.pre_tackle_authority import (
    apply_pre_tackle,
    PreTackleState,
    _live_commit_point,
    _phase_tuners,
)

OUT = os.path.join("tmp", "probe_front_hold.txt")
LINES = []
FAILS = []


def check(name, cond, detail=""):
    LINES.append("[%s] %s%s" % ("PASS" if cond else "FAIL", name,
                                (" - " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


class Body:
    """Minimal player-like body carrying the fields the authority reads."""

    def __init__(self, pid, x, y, vx=0.0):
        self.player_id = pid
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = 0.0
        self.target_x = x
        self.target_y = y
        self.frozen = False
        self.freeze_timer = 0.0
        self.fullback_role_active = False
        self.defender_role = DefenderRole.DEF_LINE_READY
        self.move_speed = 0.0
        self.movement_state = None
        self.facing = 0.0

    def set_facing(self, angle):
        self.facing = angle


def make_tuners(front_hold=True):
    t = SimpleNamespace()
    t.pretackle_attempt_enabled = True
    t.pre_tackle_trigger_radius_m = 2.0
    t.pretackle_slow_speed_mps = 2.5
    t.pretackle_freeze_radius_m = 0.15
    t.pretackle_follow_smooth = 0.6
    t.pretackle_commit_speed_mps = 8.0
    t.pretackle_plant_radius_m = 0.8
    t.pretackle_slowdown_band_m = 3.0
    t.pretackle_slowdown_scale = 0.45
    t.pretackle_commit_depth_offset_m = 1.0
    t.pretackle_commit_lateral_offset_m = 1.0
    t.pretackle_phase_tuners = {}
    t.pretackle_front_hold_enabled = front_hold
    t.pretackle_front_offset_m = 0.6
    t.pretackle_front_lateral_offset_m = 0.3
    t.pretackle_headon_plant_enabled = True
    t.pretackle_headon_commit_speed_mps = 2.0
    t.pretackle_headon_plant_radius_m = 1.8
    return t


def make_world(bc):
    w = SimpleNamespace()
    w.ball_carrier = bc
    w.sub_state = SimpleNamespace(name="OPEN_PLAY")
    w.frame_count = 1
    return w


def main():
    BC_X, BC_Y = 65.0, 35.0

    # ── geometry unit check: the commit point itself ──────────────────
    tuners_on = make_tuners(True)
    pt_on = _phase_tuners(tuners_on, "OPEN_PLAY")
    bc_geo = Body(99, BC_X, BC_Y, vx=2.0)

    ahead_far = Body(1, 71.0, BC_Y)
    tx_on, ty_on = _live_commit_point(bc_geo, ahead_far, pt_on)
    check("P1a ahead body's commit X is IN FRONT of the carrier",
          tx_on > BC_X, "tx=%.2f bc.x=%.2f" % (tx_on, BC_X))
    check("P1b ahead body holds its ground (never commanded backwards)",
          abs(tx_on - 71.0) < 1e-6, "tx=%.2f" % tx_on)

    tuners_off = make_tuners(False)
    pt_off = _phase_tuners(tuners_off, "OPEN_PLAY")
    tx_off, _ = _live_commit_point(bc_geo, ahead_far, pt_off)
    check("P1c legacy path aims the same body BEHIND the carrier",
          abs(tx_off - (BC_X - 1.0)) < 1e-6, "tx=%.2f" % tx_off)

    behind = Body(2, 60.0, BC_Y)
    tx_behind, _ = _live_commit_point(bc_geo, behind, pt_on)
    check("P1d a BEHIND body is unchanged (still bc.x + offset)",
          abs(tx_behind - (BC_X + 1.0)) < 1e-6, "tx=%.2f" % tx_behind)

    # ── runtime check: flat-footed plant while the carrier closes ─────
    bc = Body(99, BC_X, BC_Y, vx=2.0)
    d = Body(1, BC_X + 1.4, BC_Y)          # ahead, radial-in (1.4 < 1.5)
    d.set_facing(math.atan2(BC_Y - d.y, BC_X - d.x))   # square on the carrier
    world = make_world(bc)

    managed = apply_pre_tackle(world, [d], 1.0 / 60.0, tuners_on,
                               frozen_mode=False)
    check("P2a body is owned by the pre-tackle authority", d in managed)
    check("P2b body is PLANTED (flat-footed) on the approach",
          getattr(d, "pre_tackle_state", None) is PreTackleState.PLANTED,
          "state=%s" % getattr(d, "pre_tackle_state", None))
    check("P2c planted body has zero velocity",
          d.vx == 0.0 and d.vy == 0.0, "v=(%.3f, %.3f)" % (d.vx, d.vy))
    check("P2d planted body declares movement_state HOLD",
          getattr(getattr(d, "movement_state", None), "name", None) == "HOLD",
          "state=%s" % getattr(d, "movement_state", None))
    check("P2e planted body is stamped pre_tackle_stance",
          bool(getattr(d, "pre_tackle_stance", False)))
    check("P2f planted body holds a point in FRONT of the carrier",
          float(d.target_x) > BC_X,
          "target_x=%.2f bc.x=%.2f" % (d.target_x, BC_X))

    # ── the plant fires OUTSIDE the legacy plant radius ───────────────
    bc2 = Body(98, BC_X, BC_Y, vx=2.0)
    d2 = Body(3, BC_X + 1.4, BC_Y)
    d2.set_facing(math.atan2(BC_Y - d2.y, BC_X - d2.x))
    world2 = make_world(bc2)
    _ = apply_pre_tackle(world2, [d2], 1.0 / 60.0, tuners_on,
                         frozen_mode=False)
    legacy_plant_dist = math.hypot(d2.x - BC_X, d2.y - BC_Y)
    check("P3 plant fires beyond the legacy 0.8 m plant radius",
          legacy_plant_dist > 0.8
          and getattr(d2, "pre_tackle_state", None) is PreTackleState.PLANTED,
          "carrier_dist=%.2f state=%s"
          % (legacy_plant_dist, getattr(d2, "pre_tackle_state", None)))

    # ── gate off: the body is NOT planted and is aimed behind ─────────
    bc3 = Body(97, BC_X, BC_Y, vx=2.0)
    d3 = Body(4, BC_X + 1.4, BC_Y)
    d3.set_facing(math.atan2(BC_Y - d3.y, BC_X - d3.x))
    world3 = make_world(bc3)
    _ = apply_pre_tackle(world3, [d3], 1.0 / 60.0, tuners_off,
                         frozen_mode=False)
    check("P4a gate OFF: body is NOT planted at the same separation",
          getattr(d3, "pre_tackle_state", None) is not PreTackleState.PLANTED,
          "state=%s" % getattr(d3, "pre_tackle_state", None))
    check("P4b gate OFF: legacy commit point sits BEHIND the carrier",
          float(d3.target_x) < BC_X,
          "target_x=%.2f bc.x=%.2f" % (d3.target_x, BC_X))

    LINES.append("")
    LINES.append("%d checks, %d failed" % (len(LINES) - 1, len(FAILS)))
    text = "\n".join(LINES) + "\n"
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
