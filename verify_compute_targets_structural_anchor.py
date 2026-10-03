"""verify_compute_targets_structural_anchor.py — Focused verifier.

Confirms compute_targets() (the UN-GATED spacing preamble's path) anchors the
inner interpolation boundary off the STRUCTURAL tuner-driven role-anchor width
(ruck_adjacent_anchor = ruck_y +/- 2.0 m), NOT off a live "wrong hinge body".

The bug: compute_targets was the one path still doing its own inline
interpolation off a_y = the clamped live hinge (_side_hinge_body + _clamp_hinge_y).
During RELEASE/PTB the nearest chain-eligible body can sit at a "wrong" Y that is
INSIDE the A-anchor band (e.g. ruck_y - 1.0 on LEFT), so _clamp_hinge_y returns it
unchanged and the side fans from ruck_y - 1.0 instead of ruck_y - 2.0, spreading
every interior lane over a larger range -> spacing blows out.

_run:  python verify_compute_targets_structural_anchor.py
Exit 0 on success; prints a PASS/FAIL line per assertion.
"""
import math
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from world.world_state import WorldState
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets,
    ruck_adjacent_anchor,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    role_anchor_y_side,
)


failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def make_player(pid, x, y, role=None, depth_role=None, depth_side=None):
    p = Player(x=x, y=y, player_id=pid)
    p.defender_role = role
    p.depth_role = depth_role
    p.depth_side = depth_side
    p.fullback_role_active = False
    p.frozen = False
    p.freeze_timer = 0.0
    p.target_x = x
    p.target_y = y
    p.vx = 0.0
    p.vy = 0.0
    p.is_tackler = False
    p.in_contact = False
    p.tes_locked = False
    p.tackle_locked = False
    p.tes_tackler_role = None
    p.tackle_role = "PASSIVE"
    p._tackle_commit_active = False
    p._pursuit_commit_active = False
    p.movement_mode = None
    p.collision_priority = None
    p.max_speed_mps = 8.3
    p.max_defensive_speed_mps = 8.3
    p.mass_kg = 90.0
    p.tackle_state_active = False
    p.frame_side = depth_side
    return p


def make_world():
    w = WorldState()
    # Ruck near the LEFT touchline -> LEFT is the short side.
    w.ruck_x_m = 60.0
    w.ruck_y_m = 30.0
    w.defensive_line_anchor = 70.0
    w.anchor_locked = True
    w.frame_count = 0
    w.dt = 1.0 / 60.0
    w.sim_time_s = 0.0
    w.attacking_direction = -1
    w.ball_owner = "BC"
    w.tes_active = False
    w.tes_locked = False
    w.defence_enabled = True
    w.marker_freeze_timer = 0.75
    w.marker_lock_duration_s = 0.75
    w._post_retreat_open_play_frames = 99
    return w


def main():
    print("=" * 64)
    print("COMPUTE_TARGETS STRUCTURAL ROLE-ANCHOR (2026-09-02)")
    print("=" * 64)

    w = make_world()
    tuners = DefenceTuners()

    # ==========================================================
    # BUILD: a chain-eligible "WRONG HINGE BODY" at ruck_y - 1.0 (LEFT).
    # It's physically-nearest on the LEFT (min |y - 30|), chain-eligible
    # (DEF_RETREAT_10 NOT excluded; depth_role="B" in _SPACING_ROLES),
    # and NOT within the straddle deadband (|30 - 29| = 1.0 > 0.75).
    # Pre-fix: _clamp_hinge_y(29.0, 30, LEFT) => max(min(29, 30), 28) = 29.0
    #   (returned unchanged), so the side fanned from 29.0, not 28.0.
    # Post-fix: a_y_left = ruck_adjacent_anchor(30, LEFT) = 28.0.
    # ==========================================================
    wrong_hinge = make_player(11, x=58.0, y=29.0, role=DefenderRole.DEF_RETREAT_10,
                              depth_role="B", depth_side=LEFT)

    # Full LEFT side (short side).  WINGER outermost, A innermost.
    winger_l = make_player(12, x=58.0, y=42.0, role=DefenderRole.DEF_RETREAT_10,
                           depth_role="WINGER", depth_side=LEFT)
    centre_l = make_player(13, x=58.0, y=36.0, role=DefenderRole.DEF_RETREAT_10,
                           depth_role="CENTRE", depth_side=LEFT)
    a_l = make_player(14, x=58.0, y=28.0, role=DefenderRole.DEF_RETREAT_10,
                      depth_role="A", depth_side=LEFT)

    # A small RIGHT side so the world is balanced (not what's under test here).
    winger_r = make_player(21, x=58.0, y=52.0, role=DefenderRole.DEF_RETREAT_10,
                           depth_role="WINGER", depth_side=RIGHT)
    a_r = make_player(22, x=58.0, y=34.0, role=DefenderRole.DEF_RETREAT_10,
                      depth_role="A", depth_side=RIGHT)

    # A ball carrier so the engagement-band pull can engage (it shouldn't hit
    # the A/WINGER anchors anyway -- they are pinned).
    bc = make_player(100, x=55.0, y=30.0)
    bc.attack_role = "ball_carrier"

    defenders = [wrong_hinge, winger_l, centre_l, a_l, winger_r, a_r]
    w.defending_team = defenders
    w.attacking_team = [bc]
    w.ball_carrier = bc
    w.players = list(defenders) + [bc]

    # ==========================================================
    # TEST 1 — the INNERMOST slot (the physically-nearest body, which is the
    #          wrong hinge at y=29) lands at the STRUCTURAL A-anchor 28.0,
    #          NOT at its own live Y (29.0).  This is the "don't fan off the
    #          wrong hinge body" guarantee.
    # ==========================================================
    print("\n[1] compute_targets anchors the inner boundary off the structural role-anchor width")
    # Isolate the interpolation boundary: null the ball carrier so the
    # engagement-band pull (_band_spacing_pull) is a no-op.  Without this the
    # AGGRESSIVE pull (-0.5m) shifts the B-role innermost body off the pure
    # interpolation result (27.5 instead of 28.0) -- a separate, intentional
    # feature, not the hinge-anchor bug under test.
    w.ball_carrier = None
    targets = compute_targets(w, defenders, tuners)

    struct_left = ruck_adjacent_anchor(30.0, LEFT)   # 28.0
    # left_defs ordered by _side_inside_out: real WINGER(12) slot0, then
    # |y-ruck_y| DESCENDING: centre_l(36->6.0) slot1, a_l(28->2.0) slot2,
    # wrong_hinge(29->1.0) slot3 (innermost, physically-nearest).
    check("1.1 wrong_hinge (innermost slot) target_y == ruck_adjacent_anchor (28.0)",
          abs(targets[id(wrong_hinge)] - struct_left) < 1e-6,
          "target_y=%.4f expected=%.4f" % (targets[id(wrong_hinge)], struct_left))
    check("1.2 wrong_hinge body is NOT the inner anchor (target != 29.0)",
          abs(targets[id(wrong_hinge)] - 29.0) > 1e-6,
          "target_y=%.4f should NOT be the wrong hinge 29.0" % targets[id(wrong_hinge)])

    # ==========================================================
    # TEST 2 — the WINGER lands on the side's WIDTH anchor (2026-09-13).
    # COUNT AND WIDTH SHARE THE AUTHORITY: compute_targets builds
    # side_lanes for EXACTLY the number of eligible defenders on the side,
    # and the 3-point width tuners (via governed_width_m -> side_widths)
    # own the SPAN those lanes are laid across, so the outermost lane IS
    # the WINGER and lands on the width anchor.  The count decides only
    # how many lanes share the span.
    # ==========================================================
    print("\n[2] WINGER lands on the side's governed-width outermost lane")
    from game.defence.defence_lvl_1.defensive_split_spacing import (
        side_lanes, side_widths, _clamped_winger_y,
    )
    # Per-side eligible count (the count-boss input compute_targets uses).
    n_left_eligible = sum(
        1 for d in defenders if getattr(d, "depth_side", None) == LEFT
    )
    # WIDTH-AUTHORITY RESTORED (2026-09-13): the lane SPAN is the per-side
    # governed width (governed_width_m x split factor, room-capped); the
    # COUNT only decides HOW MANY lanes share it.  Re-deriving the ladder
    # WITHOUT passing that width falls back to the flat defender_gap_m and
    # no longer describes the geometry the engine produces.
    left_width, _right_width = side_widths(w, tuners, ruck_y=30.0)
    count_lanes = side_lanes(
        n_left_eligible, 30.0, LEFT, tuners, span=left_width,
    )
    winger_y = count_lanes[-1]
    check("2.1 winger_l target_y == governed-width outermost lane",
          abs(targets[id(winger_l)] - winger_y) < 1e-6,
          "target_y=%.4f expected=%.4f (side_width=%.4f n=%d)" % (
              targets[id(winger_l)], winger_y, left_width, n_left_eligible))
    # SINGLE WIDTH SOURCE (2026-09-13): role_anchor_y_side derives the
    # WINGER anchor from the SAME per-side governed width the lane model
    # uses, so the structural role anchor and the outermost lane anchor
    # agree — width is now an INPUT to the lane step as well as the anchor,
    # and the count picks only how many lanes share the span.  ``n_total``
    # no longer selects a different width source (side_widths reads the
    # governed width only), so both sides of this check read one value.
    ra_wl = role_anchor_y_side(winger_l, 30.0, w, tuners)
    n_total = sum(
        1 for d in defenders if not getattr(d, "fullback_role_active", False)
    )
    ca_left_width, _ = side_widths(w, tuners, n_total=n_total, ruck_y=30.0)
    ca_winger_y = _clamped_winger_y(ca_left_width, 30.0, LEFT, tuners)
    check("2.2 role_anchor_y_side(winger_l) == governed-width winger anchor",
          abs(ra_wl - ca_winger_y) < 1e-6,
          "side-anchor=%.4f expected=%.4f (governed width)" % (ra_wl, ca_winger_y))

    # ==========================================================
    # TEST 3 — interior lane is a strict interpolation between the structural
    #          A anchor and the winger anchor (no fan-off-the-wrong-hinge).
    # ==========================================================
    print("\n[3] interior lane interpolates between structural A and winger")
    # left_defs ordered by _side_inside_out: WINGER(12) slot0, then
    # |y-ruck_y| DESCENDING -> centre_l(13) slot1 (6.0), a_l(14) slot2 (2.0),
    # wrong_hinge(11) slot3 (1.0, innermost = a_y exactly).
    # So a_l (slot2, t=2/3) is the genuine interior lane.
    a_l_y = targets[id(a_l)]
    check("3.1 a_l (interior slot2) is strictly between struct A and winger",
          min(struct_left, winger_y) < a_l_y < max(struct_left, winger_y),
          "a_l_y=%.4f struct_A=%.4f winger=%.4f" % (a_l_y, struct_left, winger_y))

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
