"""
verify_anticipation_reconnected.py — Verify the anticipation authority window
and the modernised pre-tackle slowdown threshold (2026-09-08).

Component under test:
  - ``anticipation_layer.apply_anticipation_pre_shift`` stamps
    ``anticipation_target_y`` / ``anticipation_authority`` /
    ``_anticipation_since_frame`` / ``_anticipation_since_time_s`` on in-band
    defenders during the drive.
  - ``anticipation_layer.anticipation_authority_active`` returns True only
    while the authority window holds (drive + decay window + NOT committed /
    pursuit-committed / TES-managed / depth-locked).
  - ``anticipation_layer.apply_anticipation_authority`` reasserts the lean on
    ``target_y`` (bounded per-frame blend) so the boundary writers cannot
    snap it off.
  - ``anticipation_layer.anticipation_slowdown_point`` resolves the
    modernised slowdown distance anchor (bc_predicted default /
    role_depth_x / bc_live).

Asserts:
  1. STAMPS      — in-band drive defenders carry anticipation_target_y +
                   anticipation_authority; out-of-band / RELEASE clear them.
  2. AUTHORITY   — anticipation_authority_active is True for an in-band
                   non-committed defender, False for a committed tackler,
                   False for a pursuit-committed defender, False for a
                   TES-managed / depth-locked body, False after decay.
  3. APPLY       — apply_anticipation_authority blends target_y toward the
                   anticipated Y with a bounded per-frame delta (no snap).
  4. SLOWDOWN    — anticipation_slowdown_point("bc_predicted") returns the
                   point the line leaned toward; "bc_live" returns the live
                   BC; "role_depth_x" returns (bc.x + WIDTH_OFFSETS[role],
                   bc.y).  ANCHOR FOLLOWS THE GATE (fix D, 2026-09-15): while
                   ``anticipation_enabled`` is False (the shipped state after
                   the even-ladder rework) NO anchor projects -- every one
                   resolves to the live BC so the slowdown taper keeps its
                   full band instead of being eaten by a lead that nothing
                   leans toward.

Exit 0 = PASS, exit 1 = FAIL.
"""

import math
import sys
import types

from player.player import Player, DefenderRole
from world.world_state import GameSubState, GamePhase
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.anticipation_layer import (
    apply_anticipation_pre_shift,
    anticipation_authority_active,
    apply_anticipation_authority,
    anticipation_slowdown_point,
    threat_lane_y,
)

failures = []


def make_defender(x, y, pid, world, depth_role, depth_side, defender_role=None):
    d = Player(x, y, pid)
    d._world_ref = world
    d.vx = 0.0
    d.vy = 0.0
    d.max_speed_mps = 9.2
    d.move_speed = 9.2
    d.frozen = False
    d.freeze_timer = 0.0
    d.target_x = x
    d.target_y = y
    d.depth_role = depth_role
    d.depth_side = depth_side
    d.frame_side = depth_side
    d.defender_role = defender_role
    d.fullback_role_active = False
    d.tackle_state_active = False
    d.frame_spacing_y = None
    d._tackle_commit_active = False
    d._pursuit_commit_active = False
    d.depth_locked = False
    d.tackle_role = "LINE"
    d.pre_tackle_state = None
    return d


def make_world(sub_state=GameSubState.OPEN_PLAY, ruck_y=35.0, ruck_x=60.0,
               frame=0, sim_time=0.0):
    return types.SimpleNamespace(
        ball_carrier=None,
        defending_team=[],
        attacking_team=[],
        sub_state=sub_state,
        phase=GamePhase.OPEN_PLAY,
        frame_count=frame,
        sim_time_s=sim_time,
        ruck_x_m=ruck_x,
        ruck_y_m=ruck_y,
        defensive_line_anchor=ruck_x + 10.0,
        def_line_width_m=40.0,
        governor_width_active=False,
        tes_active=False,
        ruck_established=True,
        dt=1.0 / 60.0,
        open_play_engine=None,
        fps=60.0,
    )


def make_bc(world, x, y, vx, vy):
    bc = Player(x, y, 999)
    bc._world_ref = world
    bc.x = float(x)
    bc.y = float(y)
    bc.vx = float(vx)
    bc.vy = float(vy)
    bc.max_speed_mps = 9.2
    world.ball_carrier = bc
    return bc


def spacing_dict(*pairs):
    return {id(d): float(y) for d, y in pairs}


def approx(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


def report(msg):
    failures.append(msg)


# ─────────────────────────────────────────────────────────────────────
# TEST 1 — STAMPS
# ─────────────────────────────────────────────────────────────────────

def test_stamps():
    tuners = DefenceTuners()
    tuners.anticipation_enabled = True
    world = make_world(GameSubState.OPEN_PLAY, frame=10, sim_time=1.0)
    bc = make_bc(world, 60.0, 35.0, 2.0, 3.0)   # threat_y = 35 + 3*0.35 = 36.05
    a = make_defender(70.0, 33.0, 1, world, "A", -1, DefenderRole.LINE)
    spacing = spacing_dict((a, 33.0))
    shifted = apply_anticipation_pre_shift(world, [a], tuners, spacing)

    if approx(shifted[id(a)], 33.0):
        report("STAMPS: in-band A did not shift")
    if not bool(getattr(a, "anticipation_authority", False)):
        report("STAMPS: in-band A no anticipation_authority")
    target_y = getattr(a, "anticipation_target_y", None)
    if target_y is None:
        report("STAMPS: in-band A no anticipation_target_y")
    else:
        if approx(target_y, 33.0):
            report(f"STAMPS: anticipation_target_y == base (no lean recorded) target={target_y}")
        if not approx(target_y, shifted[id(a)]):
            report("STAMPS: anticipation_target_y != shifted spacing Y")
    if getattr(a, "_anticipation_since_time_s", None) != 1.0:
        report(f"STAMPS: _anticipation_since_time_s={getattr(a, '_anticipation_since_time_s', None)} != 1.0")

    # Out-of-band defender -> authority False.
    world2 = make_world(GameSubState.OPEN_PLAY)
    bc2 = make_bc(world2, 60.0, 35.0, 0.0, 0.0)  # threat = 35
    far = make_defender(70.0, 45.0, 2, world2, "A", 1, DefenderRole.LINE)
    spacing2 = spacing_dict((far, 45.0))
    shifted2 = apply_anticipation_pre_shift(world2, [far], tuners, spacing2)
    if bool(getattr(far, "anticipation_authority", False)):
        report("STAMPS: out-of-band defender has anticipation_authority")

    # RELEASE clears stamps.
    world3 = make_world(GameSubState.TACKLE_RELEASE)
    bc3 = make_bc(world3, 60.0, 35.0, 0.0, 3.0)
    a3 = make_defender(70.0, 33.0, 3, world3, "A", -1, DefenderRole.LINE)
    a3.anticipation_authority = True
    a3.anticipation_target_y = 31.0
    spacing3 = spacing_dict((a3, 33.0))
    shifted3 = apply_anticipation_pre_shift(world3, [a3], tuners, spacing3)
    if bool(getattr(a3, "anticipation_authority", False)):
        report("STAMPS: RELEASE left anticipation_authority stamped")
    if not approx(shifted3[id(a3)], 33.0):
        report("STAMPS: RELEASE modified spacing (must be no-op)")


# ─────────────────────────────────────────────────────────────────────
# TEST 2 — AUTHORITY ACTIVE
# ─────────────────────────────────────────────────────────────────────

def test_authority_active():
    tuners = DefenceTuners()
    tuners.anticipation_enabled = True
    world = make_world(GameSubState.OPEN_PLAY, frame=10, sim_time=1.0)
    bc = make_bc(world, 60.0, 35.0, 2.0, 3.0)
    a = make_defender(70.0, 33.0, 1, world, "A", -1, DefenderRole.LINE)
    apply_anticipation_pre_shift(world, [a], tuners, spacing_dict((a, 33.0)))

    # Normal in-band -> active.
    if not anticipation_authority_active(world, a, tuners):
        report("AUTHORITY: in-band defender NOT active")

    # Committed tackler -> inactive.
    a2 = make_defender(70.0, 33.0, 2, world, "A", -1, DefenderRole.LINE)
    apply_anticipation_pre_shift(world, [a2], tuners, spacing_dict((a2, 33.0)))
    a2._tackle_commit_active = True
    if anticipation_authority_active(world, a2, tuners):
        report("AUTHORITY: committed tackler still active (must yield)")

    # Pursuit-committed -> inactive.
    a3 = make_defender(70.0, 33.0, 3, world, "A", -1, DefenderRole.LINE)
    apply_anticipation_pre_shift(world, [a3], tuners, spacing_dict((a3, 33.0)))
    a3._pursuit_commit_active = True
    if anticipation_authority_active(world, a3, tuners):
        report("AUTHORITY: pursuit-committed defender still active (must yield)")

    # Depth-locked -> inactive.
    a4 = make_defender(70.0, 33.0, 4, world, "A", -1, DefenderRole.LINE)
    apply_anticipation_pre_shift(world, [a4], tuners, spacing_dict((a4, 33.0)))
    a4.depth_locked = True
    if anticipation_authority_active(world, a4, tuners):
        report("AUTHORITY: depth-locked defender still active (must yield)")

    # Decay window elapsed -> inactive.
    a5 = make_defender(70.0, 33.0, 5, world, "A", -1, DefenderRole.LINE)
    apply_anticipation_pre_shift(world, [a5], tuners, spacing_dict((a5, 33.0)))
    world5 = make_world(GameSubState.OPEN_PLAY, frame=10, sim_time=1.0 + tuners.anticipation_authority_decay_s + 1.0)
    world5.ball_carrier = bc
    if anticipation_authority_active(world5, a5, tuners):
        report("AUTHORITY: decayed defender still active (must be False)")


# ─────────────────────────────────────────────────────────────────────
# TEST 3 — APPLY
# ─────────────────────────────────────────────────────────────────────

def test_apply():
    tuners = DefenceTuners()
    tuners.anticipation_enabled = True
    world = make_world(GameSubState.OPEN_PLAY, frame=10, sim_time=1.0)
    bc = make_bc(world, 60.0, 35.0, 2.0, 3.0)
    a = make_defender(70.0, 33.0, 1, world, "A", -1, DefenderRole.LINE)
    apply_anticipation_pre_shift(world, [a], tuners, spacing_dict((a, 33.0)))
    # Simulate a later writer snapping target_y to bc.y (live carrier).
    a.target_y = 35.0
    before = a.target_y
    target_y = float(getattr(a, "anticipation_target_y"))
    applied = apply_anticipation_authority(world, a, tuners, dt=1.0/60.0)
    if not applied:
        report("APPLY: authority did not apply a blend for in-band defender")
    else:
        blend = max(0.0, min(1.0, tuners.anticipation_authority_blend))
        max_delta = tuners.anticipation_authority_max_delta_m
        # The apply clamps the per-frame delta to max_delta, so the
        # expected value must honour that clamp (the lean never snaps).
        raw_delta = (target_y - before) * blend
        clamped_delta = raw_delta
        if abs(raw_delta) > max_delta:
            clamped_delta = math.copysign(max_delta, raw_delta if abs(raw_delta) > 1e-12 else 1.0)
        expected = before + clamped_delta
        delta = abs(a.target_y - before)
        if delta > max_delta + 1e-6:
            report(f"APPLY: per-frame delta {delta:.4f} > max_delta {max_delta:.4f}")
        if not approx(a.target_y, expected, tol=1e-4):
            report(f"APPLY: blended target_y={a.target_y:.4f} != expected {expected:.4f}")

    # Committed tackler -> no-op.
    a2 = make_defender(70.0, 33.0, 2, world, "A", -1, DefenderRole.LINE)
    apply_anticipation_pre_shift(world, [a2], tuners, spacing_dict((a2, 33.0)))
    a2._tackle_commit_active = True
    a2.target_y = 40.0
    before2 = a2.target_y
    applied2 = apply_anticipation_authority(world, a2, tuners, dt=1.0/60.0)
    if applied2:
        report("APPLY: authority applied for committed tackler (must no-op)")
    if not approx(a2.target_y, before2):
        report("APPLY: committed tackler target_y changed")


# ─────────────────────────────────────────────────────────────────────
# TEST 4 — SLOWDOWN POINT
# ─────────────────────────────────────────────────────────────────────

def test_slowdown_point():
    tuners = DefenceTuners()
    bc = types.SimpleNamespace(x=60.0, y=35.0, vx=2.0, vy=3.0)

    # -- ANCHOR FOLLOWS THE GATE (fix D, 2026-09-15) --
    # With the anticipation layer DISABLED (its shipped default after the
    # even-ladder rework) nothing leans toward a projected point, so every
    # anchor must degrade to the LIVE carrier; otherwise the projection is
    # added to the distance the taper measures and collapses the slowdown
    # band.  Asserted for all three configured anchor names.
    gate_off = DefenceTuners()
    gate_off.anticipation_enabled = False
    for anchor in ("bc_predicted", "role_depth_x", "bc_live"):
        gd = make_defender(70.0, 33.0, 7, types.SimpleNamespace(),
                           "B", -1, DefenderRole.LINE)
        gx, gy = anticipation_slowdown_point(
            types.SimpleNamespace(pre_tackle_slowdown_anchor=anchor),
            bc, gate_off, d=gd)
        if not (approx(gx, 60.0) and approx(gy, 35.0)):
            report(f"SLOWDOWN: anticipation disabled + anchor={anchor} -> "
                   f"({gx:.4f},{gy:.4f}) != live BC (60,35)")

    # Projections are honoured again once the layer is re-enabled.
    tuners.anticipation_enabled = True

    # bc_predicted (default): (bc.x + vx*lead, bc.y + vy*lead) clamped Y.
    lead = float(tuners.anticipation_lead_time_s)
    px, py = anticipation_slowdown_point(types.SimpleNamespace(), bc, tuners)
    exp_y = max(float(tuners.sideline_margin_m),
                min(float(tuners.field_width_m) - float(tuners.sideline_margin_m),
                    35.0 + 3.0 * lead))
    if not approx(px, 60.0 + 2.0 * lead):
        report(f"SLOWDOWN: bc_predicted px={px:.4f} != {60.0 + 2.0 * lead:.4f}")
    if not approx(py, exp_y):
        report(f"SLOWDOWN: bc_predicted py={py:.4f} != {exp_y:.4f}")

    # bc_live.
    lx, ly = anticipation_slowdown_point(
        types.SimpleNamespace(pre_tackle_slowdown_anchor="bc_live"), bc, tuners)
    if not (approx(lx, 60.0) and approx(ly, 35.0)):
        report(f"SLOWDOWN: bc_live point=({lx:.4f},{ly:.4f}) != (60,35)")

    # role_depth_x.
    d = make_defender(70.0, 33.0, 1, types.SimpleNamespace(), "B", -1, DefenderRole.LINE)
    WIDTH_OFFSETS = tuners.WIDTH_OFFSETS
    rx, ry = anticipation_slowdown_point(
        types.SimpleNamespace(pre_tackle_slowdown_anchor="role_depth_x"),
        bc, tuners, d=d)
    if not approx(rx, 60.0 + float(WIDTH_OFFSETS.get("B", 2.0))):
        report(f"SLOWDOWN: role_depth_x rx={rx:.4f} != {60.0 + float(WIDTH_OFFSETS.get('B', 2.0)):.4f}")
    if not approx(ry, 35.0):
        report(f"SLOWDOWN: role_depth_x ry={ry:.4f} != 35.0")


def test_tuning():
    """Regression guard for the strengthened pre-tackle slowdown tuning.

    The 2026-09-08 tuning pass widened the slowdown band, made the taper
    more assertive, and raised the authority window/blend.  These asserts
    lock in the new values so a future change that accidentally reverts
    them (or desyncs the pre_tackle_* vs pretackle_* paths) trips here.
    """
    tuners = DefenceTuners()
    # Wider slowdown band -> braking begins earlier (longer glide phase).
    if not approx(tuners.pre_tackle_slowdown_band_m, 4.0):
        report(f"TUNING: pre_tackle_slowdown_band_m={tuners.pre_tackle_slowdown_band_m} != 4.0")
    if not approx(tuners.pretackle_slowdown_band_m, 4.0):
        report(f"TUNING: pretackle_slowdown_band_m={tuners.pretackle_slowdown_band_m} != 4.0 (authority path desynced)")
    # More assertive taper -> lower floor at the stance radius.
    if not approx(tuners.pre_tackle_slowdown_scale, 0.32):
        report(f"TUNING: pre_tackle_slowdown_scale={tuners.pre_tackle_slowdown_scale} != 0.32")
    if not approx(tuners.pretackle_slowdown_scale, 0.32):
        report(f"TUNING: pretackle_slowdown_scale={tuners.pretackle_slowdown_scale} != 0.32 (authority path desynced)")
    # Assert the new taper is strictly stronger than the pre-tune defaults.
    if tuners.pretackle_slowdown_scale >= 0.45:
        report("TUNING: slowdown scale not strengthened (must be < 0.45)")
    if tuners.pretackle_slowdown_band_m <= 3.0:
        report("TUNING: slowdown band not widened (must be > 3.0)")
    # Raised authority window + blend.
    if not approx(tuners.anticipation_authority_decay_s, 0.65):
        report(f"TUNING: anticipation_authority_decay_s={tuners.anticipation_authority_decay_s} != 0.65")
    if not approx(tuners.anticipation_authority_blend, 0.55):
        report(f"TUNING: anticipation_authority_blend={tuners.anticipation_authority_blend} != 0.55")
    if tuners.anticipation_authority_decay_s <= 0.5:
        report("TUNING: authority window not raised (must be > 0.5)")
    if tuners.anticipation_authority_blend <= 0.4:
        report("TUNING: authority blend not increased (must be > 0.4)")


def main():
    test_stamps()
    test_authority_active()
    test_apply()
    test_slowdown_point()
    test_tuning()

    if failures:
        print("VERIFY_ANTICIPATION_RECONNECTED: FAIL")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("VERIFY_ANTICIPATION_RECONNECTED: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
