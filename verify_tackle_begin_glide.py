"""verify_tackle_begin_glide.py — TACKLE-BEGIN GLIDE VERIFIER (2026-09-07).

Proves the single authoritative tackle-begin law enforced by
``game/defence/defence_lvl_1/role_depth_glide.py``:

  When a tackle begins, ONLY explicitly committed tacklers may snap into
  TES/tackle geometry.  Every other defender treats role depth as a
  DESTINATION and glides toward it under its current movement mode, with
  per-frame movement capped at TACKLE_BEGIN_GLIDE_SPEED_MPS * dt —
  preventing the 10-15 m snaps that previously broke shape.

Runs standalone:  python verify_tackle_begin_glide.py
"""

import math
import os
import sys

# Ensure the repo root is importable regardless of CWD.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from game.defence.defence_lvl_1.role_depth_glide import (
    TACKLE_BEGIN_GLIDE_SPEED_MPS,
    tackle_begin_glide_enabled,
    is_committed_tackler,
    is_tackle_begin_glide_eligible,
    in_tackle_begin_window,
    enforce_tackle_begin_glide,
    clear_role_depth_glide,
    assist_radius_m,
    snap_radius_m,
    tes_slot_band,
)
from game.defence.defence_lvl_1.attach_band import (
    attach_band_enabled,
    reserve_attach_slot,
    attach_slot_conflicts,
    stamp_joiner_grace,
    decay_joiner_grace,
    joiner_grace_remaining,
    apply_defender_contact_damp,
    enforce_attach_band,
    set_reserved_slot,
    get_reserved_slot,
)
from player.player import DefenderRole


# ── NAMESPACE OBJECTS ────────────────────────────────────────────────
class _NS:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class FakeWorld(_NS):
    def __init__(self, dt=1.0 / 60.0, frame=0, sub_state=None):
        super().__init__(dt=dt, frame_count=frame, sub_state=sub_state)


def make_world(phase_name):
    """Build a world whose OPE phase resolves to ``phase_name``."""
    sub = _NS(value=phase_name.lower())
    return FakeWorld(sub_state=sub)


class FakeTuners(_NS):
    def __init__(self, enabled=True):
        super().__init__(tackle_begin_glide_enabled=enabled)


def make_player(pid, x, y, role=None, vx=0.0, vy=0.0):
    """Build a minimal defender with the attributes the glide reads."""
    return _NS(
        player_id=pid,
        x=float(x),
        y=float(y),
        vx=float(vx),
        vy=float(vy),
        frozen=False,
        freeze_timer=0.0,
        fullback_role_active=False,
        defender_role=role,
        depth_role="WINGER",
        movement_state=_NS(name="RETREAT"),
    )


def fake_ope(phase, primary=None, joiners=None, attached_ids=None,
             tes_ctx_attached=None):
    """Build a minimal OPE with the TES participant registries."""
    return _NS(
        _phase=phase,
        primary=primary,
        joiners=joiners or [],
        tes_ctx=_NS(
            attached_ids=attached_ids or set(),
            attached=tes_ctx_attached or set(),
        ),
    )


# ── TEST HELPERS ─────────────────────────────────────────────────────
_checks = []


def check(name, cond):
    _checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name}")
    return bool(cond)


def summarize():
    failed = [n for n, c in _checks if not c]
    total = len(_checks)
    print(f"\n{'=' * 60}")
    print(f"TACKLE-BEGIN GLIDE VERIFIER: {total - len(failed)}/{total} PASS")
    if failed:
        print("FAILED:")
        for n in failed:
            print(f"  - {n}")
        return 1
    return 0


# ── TESTS ────────────────────────────────────────────────────────────
def test_constants_and_flag():
    print("[1] constants + feature flag")
    check("TACKLE_BEGIN_GLIDE_SPEED_MPS == 6.0",
          TACKLE_BEGIN_GLIDE_SPEED_MPS == 6.0)
    check("flag OFF when tuners is None",
          not tackle_begin_glide_enabled(None))
    check("flag ON by default",
          tackle_begin_glide_enabled(FakeTuners(enabled=True)))
    check("flag OFF when disabled",
          not tackle_begin_glide_enabled(FakeTuners(enabled=False)))


def test_window():
    print("[2] tackle-begin window resolution")
    for name in ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
                 "TACKLE_DOMINANCE"):
        _w = make_world(name)
        _ope = fake_ope(name)
        check(f"window True for {name}", in_tackle_begin_window(_w, _ope))
    for name in ("OPEN_PLAY", "RELEASE", "PTB"):
        _w = make_world(name)
        _ope = fake_ope(name)
        check(f"window False for {name}", not in_tackle_begin_window(_w, _ope))


def test_committed_tackler():
    print("[3] committed-tackler exemption")
    # Non-committed line body.
    line = make_player(201, 50.0, 30.0, role=DefenderRole.DEF_LINE_READY)
    ope = fake_ope("TACKLE_DRIVE", primary=None, joiners=[])
    check("line body is NOT committed", not is_committed_tackler(line, ope))

    # Primary.
    primary = make_player(202, 55.0, 32.0, role=DefenderRole.TACKLER_1)
    ope_p = fake_ope("TACKLE_DRIVE", primary=primary)
    check("primary IS committed", is_committed_tackler(primary, ope_p))

    # Joiner.
    joiner = make_player(203, 54.0, 31.0, role=DefenderRole.TACKLER_2)
    ope_j = fake_ope("TACKLE_DRIVE", primary=primary, joiners=[joiner])
    check("joiner IS committed", is_committed_tackler(joiner, ope_j))

    # Attach-slot registry.
    attached = make_player(204, 53.0, 30.5, role=DefenderRole.DEF_LINE_READY)
    ope_a = fake_ope("TACKLE_DRIVE", primary=primary,
                     attached_ids={attached.player_id})
    check("attach-slot body IS committed", is_committed_tackler(attached, ope_a))

    # TESContext.attached registry.
    ctx_attached = make_player(205, 52.0, 30.0, role=DefenderRole.DEF_LINE_READY)
    ope_c = fake_ope("TACKLE_DRIVE", primary=primary,
                     tes_ctx_attached={ctx_attached.player_id})
    check("TESContext.attached body IS committed",
          is_committed_tackler(ctx_attached, ope_c))

    # Explicit TACKLER_3 role even without OPE registries.
    t3 = make_player(206, 51.0, 29.0, role=DefenderRole.TACKLER_3)
    check("TACKLER_3 role IS committed", is_committed_tackler(t3, fake_ope("TACKLE_DRIVE")))


def test_eligibility():
    print("[4] eligibility (non-committed, unfrozen, non-marker/fb/dh, in window)")
    world = make_world("TACKLE_DRIVE")
    ope = fake_ope("TACKLE_DRIVE")
    line = make_player(301, 50.0, 30.0, role=DefenderRole.DEF_LINE_READY)
    check("line body eligible in drive",
          is_tackle_begin_glide_eligible(line, world, ope))

    # Frozen.
    frz = make_player(302, 50.0, 30.0, role=DefenderRole.DEF_LINE_READY)
    frz.frozen = True
    check("frozen body NOT eligible", not is_tackle_begin_glide_eligible(frz, world, ope))

    # Marker.
    mk = make_player(303, 50.0, 30.0, role=DefenderRole.MARKER_A)
    check("marker NOT eligible", not is_tackle_begin_glide_eligible(mk, world, ope))

    # DUMMY_HALF.
    dh = make_player(304, 50.0, 30.0, role=DefenderRole.DUMMY_HALF)
    check("dummy half NOT eligible", not is_tackle_begin_glide_eligible(dh, world, ope))

    # Fullback.
    fb = make_player(305, 50.0, 30.0, role=DefenderRole.DEF_LINE_READY)
    fb.fullback_role_active = True
    check("fullback NOT eligible", not is_tackle_begin_glide_eligible(fb, world, ope))

    # Committed.
    prim = make_player(306, 50.0, 30.0, role=DefenderRole.TACKLER_1)
    ope_prim = fake_ope("TACKLE_DRIVE", primary=prim)
    check("committed NOT eligible", not is_tackle_begin_glide_eligible(prim, world, ope_prim))

    # Outside window.
    world_op = make_world("OPEN_PLAY")
    ope_op = fake_ope("OPEN_PLAY")
    check("outside window NOT eligible",
          not is_tackle_begin_glide_eligible(line, world_op, ope_op))


def test_enforce_glide():
    print("[5] enforce_tackle_begin_glide — no teleport")
    dt = 1.0 / 60.0
    max_step = TACKLE_BEGIN_GLIDE_SPEED_MPS * dt  # 0.1 m

    # (a) A 15 m teleport clamped to <= 0.1 m.
    d = make_player(401, 50.0, 30.0, role=DefenderRole.DEF_LINE_READY, vx=8.0, vy=0.0)
    world = make_world("TACKLE_DRIVE")
    world.dt = dt
    ope = fake_ope("TACKLE_DRIVE")
    # Simulate a writer teleporting the body 15 m to its role depth.
    d.x = 65.0
    d.y = 35.0
    pre_pos = {d.player_id: (50.0, 30.0)}
    clamped = enforce_tackle_begin_glide(
        world, [d], dt, FakeTuners(enabled=True), ope, pre_pos=pre_pos,
    )
    moved = math.hypot(d.x - 50.0, d.y - 30.0)
    check("teleport clamped (moved <= max_step)", moved <= max_step + 1e-9)
    check("body reported as clamped", d in clamped)
    check("velocity direction preserved (vx > 0)", d.vx > 0.0)
    check("velocity capped at glide ceiling (|v| <= 6.0)",
          math.hypot(d.vx, d.vy) <= TACKLE_BEGIN_GLIDE_SPEED_MPS + 1e-9)
    check("movement mode not erased to zero (vx != 0)", d.vx != 0.0)

    # (b) Committed tackler exempt — 15 m snap allowed.
    t = make_player(402, 55.0, 32.0, role=DefenderRole.TACKLER_1)
    ope_t = fake_ope("TACKLE_DRIVE", primary=t)
    t.x = 70.0
    t.y = 32.0
    pre_t = {t.player_id: (55.0, 32.0)}
    clamped_t = enforce_tackle_begin_glide(
        world, [t], dt, FakeTuners(enabled=True), ope_t, pre_pos=pre_t,
    )
    moved_t = math.hypot(t.x - 55.0, t.y - 32.0)
    check("committed tackler NOT clamped (snap allowed)", t not in clamped_t)
    check("committed tackler reached target (moved ~15 m)", moved_t > 10.0)

    # (c) Feature flag OFF → strict no-op (teleport survives).
    d2 = make_player(403, 50.0, 30.0, role=DefenderRole.DEF_LINE_READY, vx=8.0, vy=0.0)
    d2.x = 65.0
    d2.y = 35.0
    pre2 = {d2.player_id: (50.0, 30.0)}
    clamped_off = enforce_tackle_begin_glide(
        world, [d2], dt, FakeTuners(enabled=False), ope, pre_pos=pre2,
    )
    moved_off = math.hypot(d2.x - 50.0, d2.y - 30.0)
    check("flag OFF → no clamp", not clamped_off and moved_off > 14.0)

    # (d) Sub-ceiling movement NOT clamped.
    d3 = make_player(404, 50.0, 30.0, role=DefenderRole.DEF_LINE_READY)
    d3.x = 50.05  # 5 cm move < 0.1 m cap
    pre3 = {d3.player_id: (50.0, 30.0)}
    clamped_d3 = enforce_tackle_begin_glide(
        world, [d3], dt, FakeTuners(enabled=True), ope, pre_pos=pre3,
    )
    check("sub-ceiling movement NOT clamped", d3 not in clamped_d3)


def test_interface_and_cleanup():
    print("[6] baseline clear + import chain")
    d = make_player(501, 50.0, 30.0, role=DefenderRole.DEF_LINE_READY)
    d.x = 50.2
    clear_role_depth_glide(d)
    check("clear_role_depth_glide drops baseline attrs",
          not hasattr(d, "_tackle_begin_glide_base_x")
          and not hasattr(d, "_tackle_begin_glide_base_y"))


def test_two_radius():
    print("[7] R_assist / R_tackle resolution + attach-band gate")
    t_on = FakeTuners(enabled=True)
    t_on.tackle_assist_radius_m = 5.0
    t_on.tackle_snap_radius_m = 2.0
    t_on.attach_slot_reserve_enabled = True
    check("assist_radius_m default 5.0", assist_radius_m(t_on) == 5.0)
    check("snap_radius_m default 2.0", snap_radius_m(t_on) == 2.0)
    check("assist default when None", assist_radius_m(None) == 5.0)
    check("snap default when None", snap_radius_m(None) == 2.0)
    check("attach band enabled by default", attach_band_enabled(t_on))
    t_off = FakeTuners(enabled=True)
    t_off.attach_slot_reserve_enabled = False
    check("attach band disabled when flag off", not attach_band_enabled(t_off))


def test_slot_reservation():
    print("[8] slot reservation conflict registry")
    world = make_world("TACKLE_DRIVE")
    world.tackle_slot_threshold_m = 0.3
    check("first reserve OK", reserve_attach_slot(world, 10, 0, 101))
    check("same writer re-reserve OK", reserve_attach_slot(world, 10, 0, 101))
    check("different writer same frame conflict",
          not reserve_attach_slot(world, 10, 0, 102))
    check("conflict surfaced",
          attach_slot_conflicts(world) == [0])
    check("next frame re-resolve OK",
          reserve_attach_slot(world, 11, 0, 102))


def test_joiner_grace():
    print("[9] joiner grace window")
    d = make_player(601, 60.0, 30.0, role=DefenderRole.DEF_LINE_READY)
    t = FakeTuners(enabled=True)
    t.joiner_grace_s = 0.1
    check("grace starts at 0", joiner_grace_remaining(d) == 0.0)
    stamp_joiner_grace(d, t)
    check("grace stamped ~0.1", abs(joiner_grace_remaining(d) - 0.1) < 1e-9)
    decay_joiner_grace(d, 0.05)
    check("grace decays to ~0.05", abs(joiner_grace_remaining(d) - 0.05) < 1e-9)
    decay_joiner_grace(d, 0.2)
    check("grace floors at 0", joiner_grace_remaining(d) == 0.0)


def test_contact_damp():
    print("[10] defender contact damp (once per tackle)")
    d = make_player(701, 60.0, 30.0, role=DefenderRole.TACKLER_1, vx=8.0, vy=2.0)
    t = FakeTuners(enabled=True)
    t.defender_contact_damp = 0.1
    apply_defender_contact_damp(d, t)
    check("velocity damped to 10%", abs(d.vx - 0.8) < 1e-9 and abs(d.vy - 0.2) < 1e-9)
    apply_defender_contact_damp(d, t)
    check("damp only fires once", abs(d.vx - 0.8) < 1e-9)


def test_width_consistency():
    print("[11] width-consistency assertion (behind tackle_begin_glide_enabled)")
    dt = 1.0 / 60.0
    max_step = TACKLE_BEGIN_GLIDE_SPEED_MPS * dt  # 0.1 m
    world = make_world("TACKLE_DRIVE")
    world.dt = dt
    world.tackle_slot_threshold_m = 0.3
    ope = fake_ope("TACKLE_DRIVE")
    # A defender teleported wide: y pushed far outside its slot band.
    d = make_player(801, 50.0, 30.0, role=DefenderRole.DEF_LINE_READY)
    d.x = 64.0
    d.y = 42.0     # 12 m above slot_y=30
    d.target_y = 30.0  # slot band reference
    pre_pos = {d.player_id: (50.0, 30.0)}
    clamped = enforce_tackle_begin_glide(
        world, [d], dt, FakeTuners(enabled=True), ope, pre_pos=pre_pos,
    )
    # Vector clamp caps the move to <= 0.1 m AND the Y-band clamp keeps y in
    # the slot band (30 ± 0.3).  Both hold only when the glide is enabled.
    y_in_band = 29.7 <= d.y <= 30.3
    moved = math.hypot(d.x - 50.0, d.y - 30.0)
    check("Y clamped into slot band (width-consistency)", y_in_band)
    check("teleport still capped", moved <= max_step + 1e-9)
    # Flag OFF -> width consistency assertion must FAIL (no clamp).
    d2 = make_player(802, 50.0, 30.0, role=DefenderRole.DEF_LINE_READY)
    d2.x = 64.0
    d2.y = 42.0
    d2.target_y = 30.0
    pre2 = {d2.player_id: (50.0, 30.0)}
    enforce_tackle_begin_glide(
        world, [d2], dt, FakeTuners(enabled=False), ope, pre_pos=pre2,
    )
    check("flag OFF -> width consistency not enforced (y survives)",
          d2.y > 40.0)


def test_attach_band():
    print("[12] enforce_attach_band — reserve + nudge, no snap outside R_tackle")
    dt = 1.0 / 60.0
    world = make_world("TACKLE_DRIVE")
    world.dt = dt
    world.tackle_slot_threshold_m = 0.3
    t = FakeTuners(enabled=True)
    t.tackle_assist_radius_m = 5.0
    t.tackle_snap_radius_m = 2.0
    t.attach_slot_reserve_enabled = True
    bc = make_player(900, 60.0, 30.0)
    ope = fake_ope("TACKLE_DRIVE")
    ope._attached_player_slots = {}
    # Defender 4 m from BC -> inside R_assist, outside R_tackle -> reserved+nudged.
    d = make_player(901, 56.0, 30.0, role=DefenderRole.DEF_LINE_READY, vx=0.0, vy=0.0)
    d.target_y = 30.0
    pre_pos = {d.player_id: (56.0, 30.0)}
    managed = enforce_attach_band(
        world, [d], dt, t, ope, bc=bc, pre_pos=pre_pos, glide_speed_mps=6.0,
    )
    check("assist-band body reserved + nudged", d in managed)
    check("reserved slot stamped (0/1/2)", get_reserved_slot(d) in (0, 1, 2))
    # Nudge inward toward the BC at <= 0.1 m/frame.
    inward = (d.x > 56.0)  # pulled toward bc.x=60
    check("nudged inward toward BC", inward)
    # Defender within R_tackle -> snap-eligible, NOT nudged/reserved.
    d2 = make_player(902, 58.5, 30.0, role=DefenderRole.DEF_LINE_READY)
    d2.target_y = 30.0
    pre2 = {d2.player_id: (58.5, 30.0)}
    managed2 = enforce_attach_band(
        world, [d2], dt, t, ope, bc=bc, pre_pos=pre2, glide_speed_mps=6.0,
    )
    check("inside R_tackle NOT nudged (snap-eligible)", d2 not in managed2)
    check("inside R_tackle reservation cleared",
          get_reserved_slot(d2) is None)
    # Flag OFF -> strict no-op.
    t_off = FakeTuners(enabled=True)
    t_off.attach_slot_reserve_enabled = False
    d3 = make_player(903, 56.0, 30.0, role=DefenderRole.DEF_LINE_READY)
    d3.target_y = 30.0
    pre3 = {d3.player_id: (56.0, 30.0)}
    managed3 = enforce_attach_band(
        world, [d3], dt, t_off, ope, bc=bc, pre_pos=pre3, glide_speed_mps=6.0,
    )
    check("flag OFF -> attach band no-op", d3 not in managed3)


def main():
    test_constants_and_flag()
    test_window()
    test_committed_tackler()
    test_eligibility()
    test_enforce_glide()
    test_interface_and_cleanup()
    test_two_radius()
    test_slot_reservation()
    test_joiner_grace()
    test_contact_damp()
    test_width_consistency()
    test_attach_band()
    return summarize()


if __name__ == "__main__":
    sys.exit(main())
