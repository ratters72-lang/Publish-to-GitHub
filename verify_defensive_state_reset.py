"""verify_defensive_state_reset.py — Full defensive-state reset verifier (2026-09-07/08).

Confirms the reset half of the out-of-band fix: a full defensive-state reset at
TACKLE_RELEASE → PTB → TACKLE_INIT clears the per-defender DRIVE-PATH state that
leaks from one tackle into the next, so the second tackle starts fresh (mirrors
the first-tackle-clean behaviour).  The stale drive-path fields (depth-lock,
role-depth targets, spacing-frame stamp, retreat-support movement state, MMDL /
authority bookkeeping, pre-tackle stamps) must ALL be wiped, while the
PERSISTENT depth-role bands (depth_role / depth_side / _depth_seeded) and the
anchor lock are handled by the surviving reset authorities.

Run:  python verify_defensive_state_reset.py     (exit 1 = reset is broken)
"""
import math
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from game.defence.defence_lvl_1.defensive_pipeline import (
    reset_defensive_drive_state,
)
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _anchor_m,
)

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ─────────────────────────────────────────────────────────────────────────
# STUBS
# ─────────────────────────────────────────────────────────────────────────

def mk_def(pid, x, y):
    """Build a defender carrying STALE drive-path state (tackle #2 start).

    Every field the reset must clear is pre-loaded with a value that would
    otherwise leak into the next tackle.  The persistent depth-role band
    fields (depth_role / depth_side / _depth_seeded) and the anchor lock are
    deliberately SET so we can prove the reset leaves them intact.
    """
    d = SimpleNamespace()
    d.player_id = pid
    d.x = float(x)
    d.y = float(y)
    d.vx = 3.0
    d.vy = 1.5
    d.frozen = False
    d.freeze_timer = 0.0

    # ── STALE DRIVE-PATH FIELDS (must be wiped) ──
    d.depth_locked = True
    d.depth_lock_x = float(x) + 12.0        # ahead of the body (BC-tracked pin)
    d.depth_lock_y = float(y) + 8.0         # drive-lateral hold
    d.role_depth_target_x = float(x) - 5.0  # stale BC-tracked depth
    d.role_depth_target_y = float(y) - 3.0
    d.frame_spacing_y = float(y) + 4.0      # applied spacing stamp
    d.movement_state = "RETREAT"
    d.target_source = "spacing"
    d.retreating = True
    d._sd_vx = 2.0
    d._sd_vy = -1.0
    d.authoritative_until_frame = 999
    d._mmdl_state_until_frame = 999
    d._mmdl_moved = True
    d._force_innermost_seed = True
    d._flipped_in_release = True
    d._defer_retreat_10 = True
    d.role_depth_class = "PASSIVE"

    # ── PERSISTENT DEPTH-ROLE BAND (must survive) ──
    d.depth_role = "C"
    d.depth_side = -1
    d._depth_seeded = True

    return d


def mk_world(defenders):
    """Minimal world with a live ruck + the stale frame geometry stamp."""
    w = SimpleNamespace()
    w.ruck_x_m = 60.0
    w.ruck_y_m = 35.0
    w.def_line_width_m = 40.0
    w.ball_carrier = None
    w.defending_team = list(defenders)
    w.frame_count = 12
    w.sub_state = "RELEASE"
    w._ope_phase_label = "RELEASE"

    # Stale published frame geometry (must be cleared).
    w.frame_geometry_ready = True
    w.frame_geometry_frame = 11
    w.frame_spacing_targets = {id(d): float(d.y) + 4.0 for d in defenders}
    w.frame_width_targets = {id(d): float(d.x) + 12.0 for d in defenders}
    w.frame_side_frame = 11

    # Stale engagement state + anchor lock (must be cleared by reset).
    w.depth_role_state = {
        id(d): SimpleNamespace(role="PASSIVE", side=-1, locked=True,
                               last_switch_frame=8)
        for d in defenders
    }
    w.anchor_locked = True
    w.defensive_line_anchor = float(w.ruck_x_m) + 10.0

    return w


# ─────────────────────────────────────────────────────────────────────────
# TEST 1 — every defender's drive-path field is wiped
# ─────────────────────────────────────────────────────────────────────────

def test_drive_path_wipe():
    print("=" * 64)
    print("TEST 1 — drive-path fields wiped on every defender")
    print("=" * 64)

    defenders = [mk_def(101, 66.0, 25.0), mk_def(102, 70.0, 45.0)]
    world = mk_world(defenders)

    # Capture the expected anchor BEFORE reset (ruck_x + 10).
    anchor_x = _anchor_m(world)
    print("  anchor_x = %.2f" % anchor_x)

    reset_defensive_drive_state(world)

    for d in defenders:
        print("  id=%d x=%.2f y=%.2f" % (d.player_id, d.x, d.y))
        check("id%d.depth_locked cleared" % d.player_id, d.depth_locked is False)
        check("id%d.depth_lock_x pinned to x" % d.player_id,
              abs(float(d.depth_lock_x) - float(d.x)) < 1e-9,
              "depth_lock_x=%.2f x=%.2f" % (d.depth_lock_x, d.x))
        check("id%d.depth_lock_y is None" % d.player_id,
              d.depth_lock_y is None)
        check("id%d.role_depth_target_x is None" % d.player_id,
              d.role_depth_target_x is None,
              "role_depth_target_x=%r" % (d.role_depth_target_x,))
        check("id%d.role_depth_target_y is None" % d.player_id,
              d.role_depth_target_y is None)
        check("id%d.frame_spacing_y cleared" % d.player_id,
              d.frame_spacing_y is None)
        check("id%d.movement_state cleared" % d.player_id,
              d.movement_state is None)
        check("id%d.target_source cleared" % d.player_id,
              d.target_source is None)
        check("id%d.retreating cleared" % d.player_id,
              d.retreating is False)
        check("id%d._sd_vx zeroed" % d.player_id,
              abs(float(d._sd_vx)) < 1e-9)
        check("id%d._sd_vy zeroed" % d.player_id,
              abs(float(d._sd_vy)) < 1e-9)
        check("id%d.authoritative_until_frame reset" % d.player_id,
              int(d.authoritative_until_frame) == -1)
        check("id%d._mmdl_state_until_frame reset" % d.player_id,
              int(d._mmdl_state_until_frame) == -1)
        check("id%d._mmdl_moved cleared" % d.player_id,
              d._mmdl_moved is False)
        check("id%d._force_innermost_seed cleared" % d.player_id,
              d._force_innermost_seed is False)
        check("id%d._flipped_in_release cleared" % d.player_id,
              d._flipped_in_release is False)
        check("id%d._defer_retreat_10 cleared" % d.player_id,
              d._defer_retreat_10 is False)


# ─────────────────────────────────────────────────────────────────────────
# TEST 2 — persistent depth-role bands survive
# ─────────────────────────────────────────────────────────────────────────

def test_band_preserved():
    print("\n" + "=" * 64)
    print("TEST 2 — persistent depth-role bands preserved")
    print("=" * 64)

    defenders = [mk_def(201, 66.0, 25.0), mk_def(202, 70.0, 42.0)]
    world = mk_world(defenders)
    reset_defensive_drive_state(world)

    for d in defenders:
        check("id%d.depth_role preserved" % d.player_id,
              d.depth_role == "C", "role=%s" % d.depth_role)
        check("id%d.depth_side preserved" % d.player_id,
              d.depth_side == -1, "side=%s" % d.depth_side)
        check("id%d._depth_seeded preserved" % d.player_id,
              d._depth_seeded is True)


# ─────────────────────────────────────────────────────────────────────────
# TEST 3 — world-level frame geometry + engagement state + anchor lock cleared
# ─────────────────────────────────────────────────────────────────────────

def test_world_level():
    print("\n" + "=" * 64)
    print("TEST 3 — world frame geometry / engagement / anchor lock cleared")
    print("=" * 64)

    defenders = [mk_def(301, 66.0, 25.0)]
    world = mk_world(defenders)
    reset_defensive_drive_state(world)

    check("frame_geometry_ready cleared",
          world.frame_geometry_ready is False)
    check("frame_geometry_frame cleared",
          world.frame_geometry_frame is None)
    check("frame_spacing_targets cleared",
          world.frame_spacing_targets is None)
    check("frame_width_targets cleared",
          world.frame_width_targets is None)
    check("frame_side_frame cleared",
          world.frame_side_frame is None)
    check("depth_role_state cleared",
          getattr(world, "depth_role_state", None) is None)
    check("anchor_locked cleared",
          world.anchor_locked is False)
    # After reset the anchor recomputes from the live ruck (ruck_x + 10).
    anchor_x = _anchor_m(world)
    check("anchor recomputes from live ruck",
          abs(anchor_x - (60.0 + 10.0)) < 1e-9,
          "anchor=%.2f" % anchor_x)


# ─────────────────────────────────────────────────────────────────────────
# TEST 4 — reset is idempotent (safe to call at all three transition points)
# ─────────────────────────────────────────────────────────────────────────

def test_idempotent():
    print("\n" + "=" * 64)
    print("TEST 4 — reset is idempotent (RELEASE → PTB → TACKLE_INIT)")
    print("=" * 64)

    defenders = [mk_def(401, 66.0, 25.0)]
    world = mk_world(defenders)

    reset_defensive_drive_state(world)
    reset_defensive_drive_state(world)
    reset_defensive_drive_state(world)

    d = defenders[0]
    check("id.depth_locked cleared after 3x reset", d.depth_locked is False)
    check("id.depth_lock_y is None after 3x reset", d.depth_lock_y is None)
    check("id.frame_spacing_y cleared after 3x reset",
          d.frame_spacing_y is None)
    check("id.movement_state cleared after 3x reset",
          d.movement_state is None)
    check("id.role_depth_target_x is None after 3x reset",
          d.role_depth_target_x is None)
    check("frame_geometry_ready cleared after 3x reset",
          world.frame_geometry_ready is False)


# ─────────────────────────────────────────────────────────────────────────

def main():
    test_drive_path_wipe()
    test_band_preserved()
    test_world_level()
    test_idempotent()

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED — defensive drive-state reset is complete")
    sys.exit(0)


if __name__ == "__main__":
    main()
