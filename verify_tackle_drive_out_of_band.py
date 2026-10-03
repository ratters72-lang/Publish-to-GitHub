"""verify_tackle_drive_out_of_band.py — Focused out-of-band motion verifier (2026-09-07).

Reproduces the TACKLE_DRIVE / TACKLE_DOMINANCE "out-of-band" defender-motion leak:
a non-tackler defender BEYOND R_tackle (3.5 m tackle-state gate) is still written by
the spacing/line writers (the live-BC depth track + frame-spacing Y), so it can move
forward in X and outward in Y during the sealed tackle — reading as a teleport before
the retreat logic reclaims it.

The verifier hand-calls the TES-drive writer chain on constructed defenders / worlds
(no live engine) and asserts:

  PART 1 — a three-tackler drive: every defender beyond R_tackle stays monotone-back
           or stationary in X (x_next <= x_prev + eps).
  PART 2 — trace every Y-writer / spacing update for roles C/D/B/WINGER and FAIL if a
           non-tackler gains forward X (target_x > x) or outward Y
           (|target_y - ruck_y| > |y - ruck_y|, signed outward).
  PART 3 — once a defender is N/A-pivot (no snap) outside R_tackle, only retreat /
           line-reset writers may touch its position until PTB (write-audit via the
           `_target_writer` tag).

Run:  python verify_tackle_drive_out_of_band.py     (exit 1 = reproduces the leak)
"""
import math
import sys
import types
from types import SimpleNamespace

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_depth_roles import stamp_drive_depth_x
from game.defence.defence_lvl_1.tackle_state_gate import (
    tackle_state_radius_m,
    distance_to_tackle_point,
    is_tes_drive_hold,
)
from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y
from game.managers.retreat_manager import apply_tes_support_retreat
from game.managers.defence_manager import (
    _refresh_depth_lock_x,
    DEPTH_LOCK_GLIDE_SPEED_MPS,
)
from game.debug.target_write_log import stamp_target_write
from player.movement_states import MovementState

# ── R_TACKLE — the real authority boundary during DRIVE/DOMINANCE ──────────
# Per the user directive: R_tackle = 3.5 m (the tackle-state gate /
# RUCK_BUBBLE_RADIUS_M).  A defender beyond this radius is "outside the active
# tackle radius" and must NOT be written by spacing/line writers.
R_TACKLE: float = 3.5

# Attack direction: attack runs +X (toward the attacking tryline), so
# "monotone-back" = non-increasing X.
ATTACK_DIR: int = +1

# The writers that are ALLOWED to touch an N/A-pivot (no-snap) body.
# Everything else (spacing / attach_band / structured_close / geometry) is a
# leak if it mutates x / y / target_x / target_y for such a body.
RETREAT_WRITERS = frozenset({
    "tes_support_retreat",
    "retreat_manager",
})

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ─────────────────────────────────────────────────────────────────────────
# STUBS
# ─────────────────────────────────────────────────────────────────────────

def mk_def(pid, x, y, role=None, *, depth_locked=False, depth_lock_y=None,
           side=None, frame_spacing_y_val=None):
    """Build a minimal line-body defender."""
    d = SimpleNamespace()
    d.player_id = pid
    d.x = float(x)
    d.y = float(y)
    d.vx = 0.0
    d.vy = 0.0
    d.frozen = False
    d.freeze_timer = 0.0
    d.depth_role = role
    d.depth_side = side
    d.depth_locked = depth_locked
    d.depth_lock_x = float(x)
    d.depth_lock_y = depth_lock_y
    d.role_depth_target_x = None
    d.target_x = float(x)
    d.target_y = float(y)
    d.target_source = None
    d.movement_state = None
    d.move_speed = 0.0
    d.max_speed_mps = 8.3
    d.max_defensive_speed_mps = 8.3
    d.frame_spacing_y = frame_spacing_y_val
    d.defender_role = None
    d.attack_role = None
    d.fullback_role_active = False
    d.tackle_state_active = False
    d.depth_class = "PASSIVE"
    d._target_writer = None
    d._target_writer_detail = None
    return d


def mk_bc(x, y, vx):
    """Build a minimal ball carrier driving +X."""
    bc = SimpleNamespace()
    bc.player_id = 900
    bc.x = float(x)
    bc.y = float(y)
    bc.vx = float(vx)
    bc.vy = 0.0
    bc.attack_role = "ball_carrier"
    return bc


def mk_ope(tuners, primary, joiners):
    """Minimal OpenPlayEngine stub carrying whatever the drive helpers read."""
    ope = SimpleNamespace()
    ope.def_tuners = tuners
    ope.primary = primary
    ope.joiners = list(joiners)
    ope._attached_player_slots = {}
    return ope


def mk_team(tuners, bc, defenders, primary=None, joiners=None):
    """Minimal WorldState stub."""
    w = SimpleNamespace()
    w.ruck_x_m = 60.0
    w.ruck_y_m = 35.0
    w.def_line_width_m = 40.0
    w.ball_carrier = bc
    w.defending_team = list(defenders)
    w.attacking_team = []
    w.frame_count = 0
    w.dt = 1.0 / 60.0
    w.sub_state = "TACKLE_DRIVE"
    w._ope_phase_label = "TACKLE_DRIVE"
    w.ruck_established = True
    w.governor_width_active = False
    w.frame_geometry_ready = False
    w.frame_geometry_frame = None
    w.frame_spacing_targets = {}
    w.frame_width_targets = {}
    w.frame_side_frame = None
    w._target_write_log = []
    w.tackler_ids = set()
    w.primary_tackler_id = None
    w.secondary_tacklers = []
    w._last_count_split = None
    w.just_left_kickoff_timer = 0.0
    w.kickoff_side_sign = None
    ope = mk_ope(tuners, primary, joiners or [])
    w.open_play_engine = ope
    return w


def reg_tackler(w, d):
    """Register *d* as a TES tackler (primary or joiner).

    Uses the tackle registry (``primary_tackler_id`` / ``tackler_ids`` /
    ``secondary_tacklers``) as the authority; ``defender_role`` is left
    hashable (``None``) so the ``role_anchor_y_side`` n_total sweep (which
    tests ``defender_role not in EXCLUDED_ROLES``) never chokes on a
    non-hashable stub.
    """
    if w.primary_tackler_id is None:
        w.primary_tackler_id = d.player_id
    else:
        w.secondary_tacklers.append(d.player_id)
    w.tackler_ids.add(d.player_id)
    d.defender_role = None
    d.attack_role = None


# ─────────────────────────────────────────────────────────────────────────
# THE HAND-CALLED DRIVE WRITER CHAIN (reproduces update_tes_drive order)
# ─────────────────────────────────────────────────────────────────────────

def drive_frame(world, defenders, tuners, dt):
    """Run one frame of the TES-drive writer chain on *defenders*.

    Replicates the writer order in ``DefenceManager.update_tes_drive``:
      1. stamp_drive_depth_x      -> refresh role_depth_target_x (bc.x + offset)
      2. _refresh_depth_lock_x     -> refresh depth_lock_x (BC-tracked)
      3. apply_tes_support_retreat -> set target_x / target_y (X = role-depth,
                                       Y = frame-spacing)
      4. depth-lock clamp          -> direct glide-write of x / y for
                                       depth_locked bodies
    """
    bc = world.ball_carrier

    # 1. Stamp the live-BC depth X onto every body.
    stamp_drive_depth_x(world, defenders)

    # 2. Refresh each depth-locked body's X pin from the live BC.
    for d in defenders:
        _refresh_depth_lock_x(d, world)

    # 3. The TES-support retreat writer sets targets.
    for d in defenders:
        if d.frozen:
            continue
        apply_tes_support_retreat(
            world, d, tuners, governor=None, track_bc_x=True,
        )

    # 4. Depth-lock post-writer clamp (direct position write, glided).
    _dl_max_step = DEPTH_LOCK_GLIDE_SPEED_MPS * dt
    for _dlp in world.defending_team:
        if not getattr(_dlp, "depth_locked", False):
            continue
        # ── OUT-OF-RADIUS HOLD GATE (2026-09-07) ──
        # Replicate the live update_tes_drive clamp gate: a defender beyond
        # R_tackle that is not a registered TES tackler is NEVER depth-locked
        # during the drive.  Skipping the clamp stops the untagged forward-X
        # glide (the primary out-of-band leak).
        if is_tes_drive_hold(world, _dlp):
            continue
        _refresh_depth_lock_x(_dlp, world)
        _dlx = float(getattr(_dlp, "depth_lock_x", _dlp.x))
        _dx = _dlx - float(_dlp.x)
        if abs(_dx) > _dl_max_step:
            _dlp.x = float(_dlp.x) + (1.0 if _dx > 0 else -1.0) * _dl_max_step
        else:
            _dlp.x = _dlx
        _dlp.vx = 0.0
        _dlp.target_x = _dlx
        _dly = getattr(_dlp, "depth_lock_y", None)
        if _dly is not None:
            _dlyf = float(_dly)
            _dy = _dlyf - float(_dlp.y)
            if abs(_dy) > _dl_max_step:
                _dlp.y = float(_dlp.y) + (1.0 if _dy > 0 else -1.0) * _dl_max_step
            else:
                _dlp.y = _dlyf
            _dlp.vy = 0.0
            _dlp.target_y = _dlyf


def drive_frames(world, defenders, tuners, n_frames, bc_dx=0.05):
    """Advance the drive *n_frames* frames; advance the BC +X each frame."""
    bc = world.ball_carrier
    positions = {d.player_id: [] for d in defenders}
    targets = {d.player_id: [] for d in defenders}
    for f in range(n_frames):
        world.frame_count = f
        bc.x = float(bc.x) + bc_dx  # drive the carrier forward (+X)
        drive_frame(world, defenders, tuners, world.dt)
        for d in defenders:
            positions[d.player_id].append(float(d.x))
            targets[d.player_id].append((float(d.target_x), float(d.target_y)))
    return positions, targets


# ─────────────────────────────────────────────────────────────────────────
# PART 1 — three-tackler drive: monotone-back / stationary X beyond R_tackle
# ─────────────────────────────────────────────────────────────────────────

def part1():
    print("=" * 64)
    print("PART 1 — three-tackler drive: monotone-back X beyond R_tackle")
    print("=" * 64)

    tuners = DefenceTuners()
    bc = mk_bc(70.0, 35.0, vx=2.0)

    # THREE TACKLERS inside R_tackle (the register).
    t1 = mk_def(201, 68.6, 34.6)
    t2 = mk_def(202, 68.9, 35.4)
    t3 = mk_def(203, 69.3, 35.2)

    # Line bodies BEYOND R_tackle (dist > 3.5 from the tackle point).
    # Depth-role X offset places depth_lock_x AHEAD of the BC, so the clamp
    # glides these bodies FORWARD (+X) — the leak.
    line = [
        mk_def(300, 66.0, 25.0, "D", depth_locked=True,
               depth_lock_y=25.0, side=-1),
        mk_def(301, 67.0, 45.0, "WINGER", depth_locked=True,
               depth_lock_y=45.0, side=1),
        mk_def(302, 65.0, 30.0, "C", depth_locked=True,
               depth_lock_y=30.0, side=-1),
        mk_def(303, 68.0, 40.0, "B", depth_locked=True,
               depth_lock_y=40.0, side=1),
    ]
    team = [t1, t2, t3] + line

    world = mk_team(tuners, bc, team, primary=t1, joiners=[t2, t3])
    for t in (t1, t2, t3):
        reg_tackler(world, t)

    # Confirm the boundary geometry first.
    for d in line:
        dist = distance_to_tackle_point(d, world) or 0.0
        print("  id=%s role=%s dist=%.2f (R_tackle=%.1f)" % (
            d.player_id, d.depth_role, dist, R_TACKLE))
        check("P1.setup %s beyond R_tackle" % d.player_id,
              dist > R_TACKLE, "dist=%.2f" % dist)

    n_frames = 30
    positions, targets = drive_frames(world, line, tuners, n_frames)

    for d in line:
        xs = positions[d.player_id]
        worst_fwd = max(xs[i + 1] - xs[i] for i in range(len(xs) - 1))
        total = xs[-1] - xs[0]
        print("  id=%d role=%s x0=%.2f x_last=%.2f total=%.2f worst_fwd=%.2f" % (
            d.player_id, d.depth_role, xs[0], xs[-1], total, worst_fwd))
        # Monotone-back or stationary: x_next <= x_prev (+ tiny eps).
        v = [(i, xs[i + 1] - xs[i]) for i in range(len(xs) - 1)
             if xs[i + 1] - xs[i] > 1e-6]
        check("P1.id%s (%s) monotone-back/stationary in X" % (
            d.player_id, d.depth_role), not v,
            "forward steps: %s" % v)


# ─────────────────────────────────────────────────────────────────────────
# PART 2 — trace every Y-writer / spacing update; FAIL on forward-X/outward-Y
# ─────────────────────────────────────────────────────────────────────────

def _outward(y, ruck_y, side):
    """True if *y* is farther from ruck_y than the side-neutral origin.

    "Outward" = |y - ruck_y| grows in the SAME side direction.  Comparing
    against ruck_y directly is the cleanest outward test for a single body.
    """
    return abs(float(y) - float(ruck_y)) > 1e-6


def part2():
    print("\n" + "=" * 64)
    print("PART 2 — Y-writer / spacing trace: FAIL on forward-X or outward-Y")
    print("=" * 64)

    tuners = DefenceTuners()
    bc = mk_bc(70.0, 35.0, vx=2.0)
    ruck_y = 35.0

    # One body per role, all BEYOND R_tackle, on the RIGHT (side=+1) so a
    # larger |y - ruck_y| is genuinely outward (further from the ruck line).
    defers = [
        mk_def(401, 66.0, 25.0, "D", depth_locked=True,
               depth_lock_y=None, side=-1),   # LEFT (y < ruck_y)
        mk_def(402, 67.0, 45.0, "C", depth_locked=True,
               depth_lock_y=None, side=1),    # RIGHT (y > ruck_y)
        mk_def(403, 65.0, 30.0, "B", depth_locked=True,
               depth_lock_y=None, side=-1),
        mk_def(404, 68.0, 40.0, "WINGER", depth_locked=True,
               depth_lock_y=None, side=1),
    ]
    prim = mk_def(201, 68.6, 34.6)
    world = mk_team(tuners, bc, defers, primary=prim)
    reg_tackler(world, prim)

    for d in defers:
        dist = distance_to_tackle_point(d, world) or 0.0
        check("P2.setup %s beyond R_tackle" % d.player_id,
              dist > R_TACKLE, "dist=%.2f" % dist)

        # stamp phase so is_drive_phase sees the drive alive (bc.x target).
        world.sub_state = "TACKLE_DRIVE"
        world._ope_phase_label = "TACKLE_DRIVE"

        # Writer 1: the TES-support retreat writer sets target_x/target_y.
        stamp_drive_depth_x(world, [d])
        _refresh_depth_lock_x(d, world)
        apply_tes_support_retreat(world, d, tuners, governor=None,
                                  track_bc_x=True)

        tx = float(d.target_x)
        ty = float(d.target_y)
        # Forward-X: target_x > current x (attack runs +X).
        check("P2.%s forward-X: target_x <= x" % d.player_id,
              tx <= float(d.x) + 1e-6,
              "target_x=%.2f x=%.2f" % (tx, d.x))
        # Outward-Y: |target_y - ruck_y| <= |y - ruck_y| (inward/stationary).
        got = abs(ty - ruck_y)
        cur = abs(float(d.y) - ruck_y)
        check("P2.%s outward-Y: |target_y-ruck_y| <= |y-ruck_y|" % d.player_id,
              got <= cur + 1e-6,
              "target_y=%.2f (|%.2f-%.1f|=%.2f) y=%.2f (|%.2f-%.1f|=%.2f)" % (
                  ty, ty, ruck_y, got, d.y, d.y, ruck_y, cur))

        # Writer 2: the frame-spacing Y authority directly.
        sy = frame_spacing_y(world, d, tuners, governor=None)
        sgot = abs(float(sy) - ruck_y)
        check("P2.%s frame_spacing_y inward/stationary" % d.player_id,
              sgot <= cur + 1e-6,
              "spacing_y=%.2f (|%.2f-%.1f|=%.2f) y=%.2f" % (
                  sy, sy, ruck_y, sgot, d.y))

        # Reset the body to its frame-start position for the next probe.
        d.x = float(getattr(d, "_p2_x0", d.x))
        d.y = float(getattr(d, "_p2_y0", d.y))


# ─────────────────────────────────────────────────────────────────────────
# PART 3 — N/A-pivot (no snap): only retreat/line-reset may touch it until PTB
# ─────────────────────────────────────────────────────────────────────────

def part3():
    print("\n" + "=" * 64)
    print("PART 3 — N/A-pivot (no snap): only retreat/line-reset touch it")
    print("=" * 64)

    tuners = DefenceTuners()
    bc = mk_bc(70.0, 35.0, vx=2.0)

    # An N/A-pivot body: beyond R_tackle, not a registered tackler / marker /
    # DH / fullback, and NOT a ruck-adjacent A-pivot candidate.  It gets no
    # snap radius, so no attach-band nudge should ever fire for it.  It sits
    # BEHIND the BC-tracked depth pin (depth_lock_x = bc.x + offset), so the
    # untagged depth-lock clamp glides it FORWARD (+X) each frame — the leak.
    na = mk_def(501, 66.0, 25.0, "D", depth_locked=True,
                depth_lock_y=25.0, side=-1)   # gap ~11.66 from the ruck
    prim = mk_def(201, 68.6, 34.6)
    world = mk_team(tuners, bc, [na], primary=prim)
    reg_tackler(world, prim)

    dist = distance_to_tackle_point(na, world) or 0.0
    print("  N/A-pivot id=%d role=%s gap=%.2f (R_tackle=%.1f)" % (
        na.player_id, na.depth_role, dist, R_TACKLE))
    check("P3.setup N/A-pivot beyond R_tackle", dist > R_TACKLE,
          "gap=%.2f" % dist)
    check("P3.setup N/A-pivot NOT a registered tackler",
          na.player_id not in world.tackler_ids)

    # Run several drive frames and audit the writer tags + the position.
    write_tags = {}
    x_hist = []
    n_frames = 15
    for f in range(n_frames):
        world.frame_count = f
        bc.x = float(bc.x) + 0.05
        # start a fresh write-log bucket per frame for this body
        world._target_write_log = []
        drive_frame(world, [na], tuners, world.dt)
        x_hist.append(float(na.x))
        # collect every writer tag that wrote this body
        for rec in world._target_write_log:
            if rec.get("player_id") == na.player_id:
                write_tags[rec.get("writer")] = write_tags.get(
                    rec.get("writer"), 0) + 1
        # also read the live tag stamp
        wt = getattr(na, "_target_writer", None)
        if wt:
            write_tags[wt] = write_tags.get(wt, 0) + 1

    print("  writers that touched the N/A-pivot body:")
    for k, v in sorted(write_tags.items()):
        print("      %-24s x%d" % (k, v))

    # Only retreat / line-reset writers may be TAGGED on an N/A-pivot body.
    offenders = [k for k in write_tags if k not in RETREAT_WRITERS]
    check("P3.only retreat/line-reset TAGGED writers touch N/A-pivot",
          not offenders,
          "offending writers: %s" % (offenders or "none"))
    check("P3.no spacing/attach_band/geometry write on N/A-pivot",
          not any(k in write_tags for k in
                  ("spacing", "attach_band", "structured_close",
                   "geometry", "width")),
          "write tags: %s" % sorted(write_tags))

    # THE CATCH: the untagged depth-lock clamp still moves the position.
    # N/A-pivot bodies must be monotone-back / stationary in X (retreat /
    # line-reset only), so a forward glide is a leak even if no blessed tag
    # attributed it.
    total_x = x_hist[-1] - x_hist[0]
    fwd_steps = [(i, x_hist[i + 1] - x_hist[i])
                 for i in range(len(x_hist) - 1)
                 if x_hist[i + 1] - x_hist[i] > 1e-6]
    print("  N/A-pivot x: x0=%.2f x_last=%.2f total=%.2f fwd_steps=%s" % (
        x_hist[0], x_hist[-1], total_x, fwd_steps))
    check("P3.N/A-pivot position monotone-back/stationary in X (no leak)",
          not fwd_steps,
          "forward steps: %s (untagged depth-lock clamp glides it fwd)" % (
              fwd_steps or "none"))


# ─────────────────────────────────────────────────────────────────────────

def main():
    part1()
    part2()
    part3()

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        print("These failures are the PRE-FIX reproduction of the TES-drive "
              "out-of-band motion leak.  After a fix they must all PASS.")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED — out-of-band motion is contained")
    sys.exit(0)


if __name__ == "__main__":
    main()
