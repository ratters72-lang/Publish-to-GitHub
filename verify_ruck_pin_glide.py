"""verify_ruck_pin_glide.py — Capped-convergence verifier for the ruck-pin slot-1 snap / slot-2 clamp.

The reform-window pin previously wrote slot1.y = anchor / slot2.y = clamped,
teleporting a far body onto its lane in ONE frame (RUCK-PIN f=182 gap
12.93->3.50 ~9 m; f=212 slot-1 25.73->29.38 ~3.65 m).  The 2026-09-07 fix caps
convergence: a body farther than RUCK_PIN_GLIDE_THRESHOLD_M from its lane glides
by at most RUCK_PIN_GLIDE_SPEED_MPS * dt per frame; a body within the threshold
still snaps so the pin owns convergence when close.

Run:  python verify_ruck_pin_glide.py
Exit 0 on success.
"""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from world.world_state import WorldState, GameSubState
from game.defence.defence_lvl_1.ruck_adjacent_pin import (
    enforce_ruck_adjacent_pin,
    LEFT,
    RIGHT,
    ruck_adjacent_anchor,
    RUCK_PIN_GLIDE_THRESHOLD_M,
    RUCK_PIN_GLIDE_SPEED_MPS,
)


failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def make_defender(pid, x, y, depth_role=None, defender_role=None,
                  frozen=False, tackle_state=False):
    d = Player(x=x, y=y, player_id=pid)
    d.depth_role = depth_role
    d.defender_role = defender_role
    d.fullback_role_active = False
    d.tackle_state_active = tackle_state
    d.frozen = frozen
    d.freeze_timer = 0.0
    d.target_x = x
    d.target_y = y
    d.vx = 0.0
    d.vy = 0.0
    d.depth_class = "PASSIVE"
    return d


def make_world(ruck_y, defenders, phase_label="RELEASE", dt=1.0 / 60.0):
    w = WorldState()
    w.ruck_y_m = ruck_y
    w.ruck_x_m = 50.0
    w.defending_team = defenders
    w.frame_spacing_targets = {}
    w._ope_phase_label = phase_label
    w.sub_state = GameSubState.OPEN_PLAY
    w.tackler_ids = set()
    w.frame_count = 0
    w.post_tes_settle_frames = 0
    w.post_tes_settle_enabled = False
    w.dt = dt
    w.defensive_line_anchor = 60.0
    w.def_line_width_m = 40.0
    w.governor_width_active = False
    return w


def main():
    print("=" * 64)
    print("RUCK-PIN CAPPED CONVERGENCE (GLIDE) VERIFIER (2026-09-07)")
    print("=" * 64)

    ruck_y = 32.86
    dt = 1.0 / 60.0
    max_step = RUCK_PIN_GLIDE_SPEED_MPS * dt  # 6.0 * (1/60) = 0.1
    anchor_left = ruck_adjacent_anchor(ruck_y, LEFT)  # 30.86

    # [1] FAR slot-1 A-pivot glides <= max_step (no teleport)
    print("\n[1] far slot-1 A-pivot glides <= max_step")
    a = make_defender(1, x=50.0, y=25.0, depth_role="A")  # far LEFT A-pivot
    b = make_defender(2, x=50.0, y=20.0, depth_role="B")  # far interior
    w = make_world(ruck_y, [a, b], dt=dt)
    before_a = a.y
    enforce_ruck_adjacent_pin(w)
    moved_a = abs(a.y - before_a)
    check("1.1 far A-pivot moved <= max_step (no teleport)",
          moved_a > 0.0 and moved_a <= max_step + 1e-6,
          "y=%.4f moved=%.4f (cap %.4f)" % (a.y, moved_a, max_step))

    # [2] NEAR slot-1 A-pivot snaps to anchor
    print("\n[2] near slot-1 A-pivot snaps to anchor")
    a2 = make_defender(3, x=50.0, y=anchor_left - 0.2, depth_role="A")  # 30.66
    b2 = make_defender(4, x=50.0, y=20.0, depth_role="B")
    w2 = make_world(ruck_y, [a2, b2], dt=dt)
    enforce_ruck_adjacent_pin(w2)
    check("2.1 near A-pivot snapped to anchor (within threshold)",
          abs(a2.y - anchor_left) < 1e-6,
          "y=%.4f (expected %.4f)" % (a2.y, anchor_left))

    # [3] FAR slot-2 glides toward nearest band bound (no teleport)
    print("\n[3] far slot-2 glides toward nearest band bound")
    a3 = make_defender(5, x=50.0, y=anchor_left, depth_role="A")  # on anchor
    b3 = make_defender(6, x=50.0, y=20.0, depth_role="B")  # 10.86 from anchor
    w3 = make_world(ruck_y, [a3, b3], dt=dt)
    before_b3 = b3.y
    enforce_ruck_adjacent_pin(w3)
    moved_b3 = abs(b3.y - before_b3)
    check("3.1 far slot-2 moved <= max_step (no teleport)",
          moved_b3 > 0.0 and moved_b3 <= max_step + 1e-6,
          "y=%.4f moved=%.4f (cap %.4f)" % (b3.y, moved_b3, max_step))

    # [4] slot-2 exactly at a band edge (close) is NOT teleported past the band
    print("\n[4] slot-2 near band edge stays within band (no cross-band jump)")
    a4 = make_defender(7, x=50.0, y=anchor_left, depth_role="A")
    band_lo = anchor_left - 3.5  # LEFT band lo = 27.36
    b4 = make_defender(8, x=50.0, y=band_lo + 0.4, depth_role="B")  # 27.76 (inside band)
    w4 = make_world(ruck_y, [a4, b4], dt=dt)
    enforce_ruck_adjacent_pin(w4)
    # Inside the band -> untouched (or a capped glide if pushed); no jump to a bound.
    check("4.1 slot-2 inside band untouched",
          abs(b4.y - (band_lo + 0.4)) < 1e-6,
          "y=%.4f (expected %.4f)" % (b4.y, band_lo + 0.4))

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
