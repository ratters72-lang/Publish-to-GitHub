"""verify_open_play_stall_authority.py — pin the OPEN_PLAY target-reachability contract.

WHAT THIS PINS
--------------
Every target a defensive authority AUTHORS for an OPEN_PLAY body must lie
INSIDE the playable field, and no non-frozen OPEN_PLAY body may hold a live
(unreached) target for a sustained run of frames.

THE MEASURED DEFECT THIS LOCKS OUT
----------------------------------
``probe_open_play_stall_authority.py`` (seed 20260827, 6000 frames) captured
1404 non-tackle OPEN_PLAY frames and found 14 stalled body-frames — bodies
with ``frozen`` False, speed below 0.05 m/s, and a target they were NOT
parked on.  Thirteen carried ``target_source == "pre_tackle"``.

The owner was ``pre_tackle_authority``, which authored its COMMIT target as
``bc.x -/+ pretackle_commit_depth_offset_m`` with NO field bound.  With the
carrier at/past ``FIELD_MAX_X`` (120.0) the target became e.g. 121.0;
``Player._clamp_position`` forbids x > 120.0, so the commanded move was
UNREACHABLE: the body pressed against the boundary, the clamp cancelled the
displacement every frame, and it sat still with a live target while its
authority kept commanding a forward approach.

Signature recorded live: pid 204 pinned at ``x == 120.001`` with
``tx == 121.0``, ``spd == 0.0``, ``move_speed == 0.80``, ``off_x`` between
0.987 and 1.009 (≈ the 1.0 m commit offset), repeated at frames 2543, 4184,
4436, 4687, 4934, 5183, 5433 and 5939.  The sibling
``defensive_behaviour_unified._apply_tackler_commit`` shares the shape
(it consumes ``bc.x +/- ConvergenceManager`` offsets from
``world.frame_convergence_targets``).

THE FIX
-------
``game/defence/defence_lvl_1/target_field_bound.py`` — a pure leaf clamp —
applied at every authored-target write site:
``pre_tackle_authority._live_commit_point`` / ``_seed_body`` / ``_slow_step``
/ ``_lock_depth`` / the frozen-mode hold read, and
``defensive_behaviour_unified._apply_tackler_commit``.

A target outside the field is unreachable BY CONSTRUCTION, so clamping one
can only ever make a commanded move more achievable: this is a
reachability fix, not a physics or speed change.  The clamp is the identity
for an in-field point, so every in-field frame is byte-identical.

CHECKS
------
  1. the clamp helper is an identity in-field and bounds out-of-field values
  2. ``target_is_in_field`` agrees with the bounds
  3. ``_live_commit_point`` returns an in-field point for a carrier at and
     past the tryline (ahead AND behind), and the historical value mid-field
  4. ``_seed_body`` bounds the anchor and the commit point it stores
  5. ``_lock_depth`` and ``_slow_step`` bound the targets they write
  6. ``_apply_tackler_commit`` bounds the published convergence point, and
     leaves an in-field point byte-identical
  6b. a zero-speed HOLD is NOT band-eligible (its owner's hold target must
     survive the frame), while a moving body still is; and ``_freeze_stance``
     pins the target to the body's own position
  7. LIVE ROLLOUT: no non-frozen OPEN_PLAY body holds an out-of-field
     target, and no non-frozen body holds a live unreached target for a
     sustained run

Run:  python verify_open_play_stall_authority.py
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import math
import random
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

ope_mod.DEBUG_SDEA_PHYSICS = False
ope_mod.DEBUG_OPE_TRACE = False

from player.player import (
    Player,
    Team,
    FIELD_MIN_X,
    FIELD_MAX_X,
    FIELD_MIN_Y,
    FIELD_MAX_Y,
)
from world.world_state import WorldState, GamePhase, GameSubState
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    _apply_tackler_commit,
)
from game.defence.defence_lvl_1.target_field_bound import (
    clamp_target_to_field,
    clamp_target_x,
    clamp_target_y,
    target_is_in_field,
)
from game.defence.defence_lvl_1.pre_tackle_authority import (
    _live_commit_point,
    _lock_depth,
    _seed_body,
    _slow_step,
    ANCHOR_X_ATTR,
    ANCHOR_Y_ATTR,
    COMMIT_X_ATTR,
    COMMIT_Y_ATTR,
)

FRAMES = 6000
SEED = 20260827

# A stored target this far outside the bounds is a violation.
TOL = 1e-6

# The stalled predicate, identical to the probe's, so the verifier and the
# instrument that found the defect agree by construction.
STALL_SPEED_MPS = 0.05
TARGET_EPS_M = 0.05
# A stall must persist this many CONSECUTIVE frames to count: a single-frame
# lag across a phase transition is not the reported "remains stationary".
PERSISTENT_STALL_FRAMES = 5

# Commit offsets, so the unit checks do not restate tuner defaults twice.
DEFAULT_DEPTH_OFFSET_M = 1.0
DEFAULT_LATERAL_OFFSET_M = 1.0

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s"
          % ("PASS" if cond else "FAIL", name,
             (" — %s" % detail) if detail else ""))
    if not cond:
        failures.append(name)


def make_defender(pid, x, y):
    """A bare line defender, exactly as verify_tackle_commit builds one."""
    d = Player(x=x, y=y, player_id=pid)
    d.team = Team.RECEIVING
    d.is_defender = True
    d.max_defensive_speed_mps = 8.3
    d.defender_role = None
    d.depth_role = "A"
    d.depth_side = 1
    return d


def make_world():
    """A minimal OPEN_PLAY world for the unit fixtures."""
    w = WorldState()
    w.ruck_x_m = 55.0
    w.ruck_y_m = 35.0
    w.def_line_width_m = 40.0
    w.defensive_line_anchor = 65.0
    w.anchor_locked = True
    w.in_pursuit_mode = False
    w.use_structured_open_play = True
    w.defence_enabled = True
    w.tes_active = False
    w.phase = GamePhase.OPEN_PLAY
    w.sub_state = GameSubState.OPEN_PLAY
    w.ball_owner = "BC"
    w.role_governs = False
    w.ctx = None
    w.frame_count = 0
    return w


def pt_defaults():
    """The phase-tuner dict ``_live_commit_point`` / ``_seed_body`` read."""
    return {
        "pretackle_commit_depth_offset_m": DEFAULT_DEPTH_OFFSET_M,
        "pretackle_commit_lateral_offset_m": DEFAULT_LATERAL_OFFSET_M,
    }


def in_field(tx, ty):
    """True when a target is inside the field, tolerating float noise."""
    if tx is None or ty is None:
        return False
    return (FIELD_MIN_X - TOL <= float(tx) <= FIELD_MAX_X + TOL
            and FIELD_MIN_Y - TOL <= float(ty) <= FIELD_MAX_Y + TOL)


def is_tackle_phase(world):
    """True while the tackle sub-state machine owns the frame."""
    sub = getattr(world, "sub_state", None)
    name = getattr(sub, "name", None) or (str(sub) if sub is not None else None)
    return name in ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
                    "TACKLE_DOMINANCE", "TACKLE_RELEASE")


def check_clamp_helper():
    """[1]+[2] The leaf clamp: identity in-field, bounded out-of-field."""
    print("")
    print("[1] target_field_bound.clamp_target_to_field — pure, identity in-field")
    inside = [(50.0, 35.0), (FIELD_MIN_X, FIELD_MIN_Y),
              (FIELD_MAX_X, FIELD_MAX_Y), (119.999, 69.999)]
    ok_identity = all(
        clamp_target_to_field(tx, ty) == (tx, ty) for tx, ty in inside
    )
    check("in-field point returned BYTE-IDENTICAL", ok_identity,
          "fixtures %s" % (inside[:3],))

    check("x past the tryline clamps to FIELD_MAX_X",
          abs(clamp_target_x(FIELD_MAX_X + 1.0) - FIELD_MAX_X) < TOL
          and abs(clamp_target_x(121.0) - 120.0) < TOL,
          "clamp_target_x(121.0)=%s" % clamp_target_x(121.0))
    check("x behind the dead-ball line clamps to FIELD_MIN_X",
          abs(clamp_target_x(FIELD_MIN_X - 40.0) - FIELD_MIN_X) < TOL)
    check("y past the touchline clamps to FIELD_MAX_Y",
          abs(clamp_target_y(FIELD_MAX_Y + 30.0) - FIELD_MAX_Y) < TOL)
    check("y behind the touchline clamps to FIELD_MIN_Y",
          abs(clamp_target_y(-12.0) - FIELD_MIN_Y) < TOL)

    print("")
    print("[2] target_is_in_field agrees with the bounds")
    check("out-of-field point reported False",
          not target_is_in_field(121.0, 35.0)
          and not target_is_in_field(50.0, 99.0))
    check("in-field point reported True",
          target_is_in_field(50.0, 35.0)
          and target_is_in_field(FIELD_MAX_X, FIELD_MAX_Y))
    check("None-safe",
          not target_is_in_field(None, 35.0)
          and not target_is_in_field(50.0, None))


def check_live_commit_point():
    """[3] The OPEN_PLAY COMMIT point is always reachable."""
    print("")
    print("[3] pre_tackle_authority._live_commit_point — in-field for a "
          "carrier at/past the tryline")
    pt = pt_defaults()

    # CARRIER PAST THE TRYLINE, DEFENDER BEHIND (the measured defect):
    # bc.x = 121.0 and the defender behind -> tx = bc.x + 1.0 = 122.0.
    bc_past = make_defender(900, FIELD_MAX_X + 1.0, 35.0)
    behind = make_defender(1, FIELD_MAX_X - 3.0, 34.0)
    tx, ty = _live_commit_point(bc_past, behind, pt)
    check("carrier past the tryline, defender behind -> in-field",
          in_field(tx, ty),
          "bc.x=%.3f -> (%.3f, %.3f)" % (bc_past.x, tx, ty))
    check("... and the clamp bound the X, not the offset",
          abs(tx - FIELD_MAX_X) < TOL,
          "tx=%.6f (FIELD_MAX_X=%.3f)" % (tx, FIELD_MAX_X))

    # CARRIER AT THE TRYLINE, DEFENDER AHEAD -> tx = bc.x - 1.0 (in-field).
    ahead = make_defender(2, FIELD_MAX_X + 5.0, 36.0)
    tx2, ty2 = _live_commit_point(bc_past, ahead, pt)
    check("carrier past the tryline, defender ahead -> in-field",
          in_field(tx2, ty2),
          "(%.3f, %.3f)" % (tx2, ty2))
    check("... ahead offset preserved while it stays reachable",
          abs(tx2 - (bc_past.x - DEFAULT_DEPTH_OFFSET_M)) < TOL
          or abs(tx2 - FIELD_MAX_X) < TOL,
          "tx=%.3f" % tx2)

    # MID-FIELD: the clamp must be the IDENTITY, so the historical value is
    # byte-identical and no in-field behaviour moved.
    bc_mid = make_defender(901, 63.0, 35.0)
    d_mid = make_defender(3, 60.0, 34.0)
    tx3, ty3 = _live_commit_point(bc_mid, d_mid, pt)
    check("mid-field commit point is EXACTLY the historical value",
          tx3 == bc_mid.x + DEFAULT_DEPTH_OFFSET_M
          and ty3 == bc_mid.y + DEFAULT_LATERAL_OFFSET_M * -1,
          "(%.6f, %.6f)" % (tx3, ty3))

    # LATERAL: a carrier on the touchline must not author a lateral point
    # outside the field either.
    bc_edge_y = make_defender(902, 60.0, FIELD_MAX_Y + 2.0)
    d_edge_y = make_defender(4, 60.0, FIELD_MAX_Y - 1.0)
    tx4, ty4 = _live_commit_point(bc_edge_y, d_edge_y, pt)
    check("carrier past the touchline -> lateral target in-field",
          in_field(tx4, ty4), "(%.3f, %.3f)" % (tx4, ty4))

    # ... and at the dead-ball end.
    bc_left = make_defender(903, FIELD_MIN_X - 5.0, 35.0)
    d_left = make_defender(5, FIELD_MIN_X - 8.0, 35.0)
    tx5, ty5 = _live_commit_point(bc_left, d_left, pt)
    check("carrier behind the dead-ball line -> target in-field",
          in_field(tx5, ty5), "(%.3f, %.3f)" % (tx5, ty5))


def check_seed_body():
    """[4] The seeded anchor and commit point are both bounded."""
    print("")
    print("[4] pre_tackle_authority._seed_body — anchor + commit bounded")
    w = make_world()
    pt = pt_defaults()
    tuners = DefenceTuners()

    d = make_defender(10, FIELD_MAX_X - 1.0, 35.0)
    # An absurd inherited target (what a stale/unclamped writer could leave).
    d.target_x = FIELD_MAX_X + 80.0
    d.target_y = FIELD_MAX_Y + 30.0
    bc = make_defender(910, FIELD_MAX_X + 1.0, 35.0)

    _seed_body(w, d, bc, tuners, False, pt)

    ax = getattr(d, ANCHOR_X_ATTR)
    ay = getattr(d, ANCHOR_Y_ATTR)
    cx = getattr(d, COMMIT_X_ATTR)
    cy = getattr(d, COMMIT_Y_ATTR)
    check("anchor stored in-field", in_field(ax, ay),
          "anchor=(%.3f, %.3f)" % (ax, ay))
    check("commit point stored in-field", in_field(cx, cy),
          "commit=(%.3f, %.3f)" % (cx, cy))
    check("a grossly out-of-field inherited target was bounded",
          ax <= FIELD_MAX_X + TOL and ay <= FIELD_MAX_Y + TOL)

    # An in-field seed must be preserved exactly (identity / no regression).
    d2 = make_defender(11, 60.0, 34.0)
    d2.target_x = 60.0
    d2.target_y = 34.0
    bc2 = make_defender(911, 63.0, 35.0)
    _seed_body(w, d2, bc2, tuners, False, pt)
    check("in-field anchor preserved byte-identically",
          getattr(d2, ANCHOR_X_ATTR) == 60.0
          and getattr(d2, ANCHOR_Y_ATTR) == 34.0,
          "anchor=(%s, %s)" % (getattr(d2, ANCHOR_X_ATTR),
                               getattr(d2, ANCHOR_Y_ATTR)))
    check("in-field commit preserved byte-identically",
          getattr(d2, COMMIT_X_ATTR) == 64.0
          and getattr(d2, COMMIT_Y_ATTR) == 34.0,
          "commit=(%s, %s)" % (getattr(d2, COMMIT_X_ATTR),
                               getattr(d2, COMMIT_Y_ATTR)))


def check_lock_depth_and_slow_step():
    """[5] The depth-lock and the slow-contract step write bounded targets."""
    print("")
    print("[5] pre_tackle_authority._lock_depth / _slow_step — bounded writes")

    d = make_defender(20, FIELD_MAX_X, 35.0)
    d.depth_locked = True
    d.depth_lock_x = FIELD_MAX_X + 1.0      # a stale drive-entry depth
    d.depth_lock_y = FIELD_MAX_Y + 12.0     # a stale drive-entry lane
    _lock_depth(d)
    check("_lock_depth bounds the depth target",
          in_field(d.target_x, d.target_y),
          "target=(%.3f, %.3f)" % (d.target_x, d.target_y))
    check("_lock_depth clamps the Y hold lane too",
          d.target_y <= FIELD_MAX_Y + TOL,
          "target_y=%.3f" % d.target_y)

    d2 = make_defender(21, FIELD_MAX_X - 2.0, 35.0)
    _slow_step(d2, FIELD_MAX_X + 40.0, FIELD_MIN_Y - 20.0, 1.0 / 60.0,
               2.5, 1.0)
    check("_slow_step bounds the stamped target",
          in_field(d2.target_x, d2.target_y),
          "target=(%.3f, %.3f)" % (d2.target_x, d2.target_y))

    # Identity: an in-field step target is stored byte-identically.
    d3 = make_defender(22, 60.0, 35.0)
    _slow_step(d3, 62.0, 36.0, 1.0 / 60.0, 2.5, 1.0)
    check("_slow_step preserves an in-field target byte-identically",
          d3.target_x == 62.0 and d3.target_y == 36.0,
          "target=(%s, %s)" % (d3.target_x, d3.target_y))


def check_tackler_commit():
    """[6] The sibling commit writer bounds the published convergence point."""
    print("")
    print("[6] defensive_behaviour_unified._apply_tackler_commit — bounded")
    pt = pt_defaults()  # noqa: F841  (kept for symmetry with the other checks)

    # CORRIDOR OFF: the commit writer owns the body and actually writes.
    tuners = DefenceTuners()
    tuners.pretackle_corridor_owns_speed = False

    # (a) the published convergence point is OUTSIDE the field.
    w = make_world()
    line = [make_defender(30, FIELD_MAX_X - 2.0, 34.0)]
    bc = make_defender(930, FIELD_MAX_X + 1.0, 35.0)
    bc.possessed = True
    w.ball_carrier = bc
    line[0].tackle_role = "PRIMARY"
    w.frame_convergence_targets = {id(line[0]): (FIELD_MAX_X + 1.0, 35.0)}
    _apply_tackler_commit(w, line, tuners)
    check("published out-of-field point -> stored target in-field",
          in_field(line[0].target_x, line[0].target_y),
          "target=(%.3f, %.3f)" % (line[0].target_x, line[0].target_y))
    check("... and the writer still stamped its own source",
          line[0].target_source == "tackler_commit",
          "target_source=%r" % line[0].target_source)

    # (b) the published point is INSIDE the field -> byte-identical.
    w2 = make_world()
    line2 = [make_defender(31, 60.0, 34.0)]
    bc2 = make_defender(931, 63.0, 35.0)
    bc2.possessed = True
    w2.ball_carrier = bc2
    line2[0].tackle_role = "PRIMARY"
    w2.frame_convergence_targets = {id(line2[0]): (63.0, 35.0)}
    _apply_tackler_commit(w2, line2, tuners)
    check("published in-field point preserved byte-identically",
          line2[0].target_x == 63.0 and line2[0].target_y == 35.0,
          "target=(%s, %s)" % (line2[0].target_x, line2[0].target_y))


def check_zero_speed_hold_is_band_exempt():
    """[6b] A zero-speed HOLD must never be re-targeted by the band arbiter.

    THE MEASURED DEFECT THIS LOCKS OUT: 274 of the 279 stalled body-frames
    the probe attributed were ``movement_state HOLD`` with
    ``move_speed == 0.0`` and ``target_source == "pre_tackle"`` — a body its
    owner had explicitly told to stand still, whose ``target_x`` was pinned
    to its own ``x`` while ``target_y`` had been moved 0.15 m away by band
    arbitration.  A body commanded not to move can never satisfy a target
    that is not its own position, so the hold target must snap.
    """
    print("")
    print("[6b] writer_priority_bands.is_band_eligible — zero-speed HOLD snaps")
    from game.defence.defence_lvl_1.writer_priority_bands import (
        is_band_eligible,
    )
    from game.defence.defence_lvl_1.pre_tackle_authority import _freeze_stance
    from player.movement_states import MovementState

    held = make_defender(40, 60.0, 35.0)
    held.movement_state = MovementState.HOLD
    held.move_speed = 0.0
    check("HOLD at move_speed 0 is NOT band-eligible (its target must snap)",
          is_band_eligible(held, None) is False)
    check("... and it is still HOLD at zero speed (the measured signature)",
          getattr(held.movement_state, "name", None) == "HOLD"
          and held.move_speed == 0.0)

    moving = make_defender(41, 60.0, 35.0)
    moving.movement_state = MovementState.CHASE
    moving.move_speed = 7.5
    check("a CHASE body at speed is still band-eligible",
          is_band_eligible(moving, None) is True)

    zero_speed_chase = make_defender(42, 60.0, 35.0)
    zero_speed_chase.movement_state = MovementState.CHASE
    zero_speed_chase.move_speed = 0.0
    check("CHASE at zero speed is unaffected (only HOLD is exempt)",
          is_band_eligible(zero_speed_chase, None) is True)

    # The other half of the contract: the HOLD writer must pin the target to
    # the body's own position, so an exempt HOLD body reports target == pos.
    body = make_defender(43, 61.5, 34.25)
    body.vx = 3.0
    body.vy = -2.0
    _freeze_stance(body)
    check("_freeze_stance pins the target to the body's own position",
          body.target_x == 61.5 and body.target_y == 34.25,
          "target=(%s, %s)" % (body.target_x, body.target_y))
    check("_freeze_stance zeroes the speed budget and velocity",
          body.move_speed == 0.0 and body.vx == 0.0 and body.vy == 0.0)


# ═════════════════════════════════════════════════════════════════════
# [7] LIVE ROLLOUT — the real engine, booted exactly like the probe
# ═════════════════════════════════════════════════════════════════════

def _silence():
    sys.stdout = open(os.devnull, "w", encoding="utf-8")


def _restore(real):
    sys.stdout = real


def run_rollout(frames=FRAMES, seed=SEED):
    """Step the real engine; record every non-tackle OPEN_PLAY body-frame."""
    from world.field import Field
    from world.open_play_engine import OpenPlayEngine
    from kick_off.kickoff_formations import (
        apply_receiving_kickoff_formation,
        apply_kicking_team_formation,
    )
    from game.kickoff_engine import KickoffEngine
    from ball.ball import Ball
    from game.physics.collision_system import CollisionSystem
    from world.catch_system import check_for_catch

    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball

    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play

    kickoff_engine = KickoffEngine(world, ball, field)

    random.seed(seed)
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    open_frames = 0
    records = []          # one dict per body-frame captured
    out_of_field = []     # (frame, pid, tx, ty, source, frozen)
    dt = 1.0 / 60.0

    for frame in range(1, frames + 1):
        world.frame_count = frame
        ball.update(dt)
        if world.phase == GamePhase.KICKOFF:
            kickoff_engine.update(dt)
            check_for_catch(world, ball, dt)
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                world.open_play.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        elif world.phase == GamePhase.OPEN_PLAY:
            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)
        else:
            continue

        if getattr(open_play, "_phase", None) != "OPEN_PLAY":
            continue
        if world.phase != GamePhase.OPEN_PLAY:
            continue
        if is_tackle_phase(world):
            continue

        open_frames += 1
        for d in (getattr(world, "defending_team", None) or []):
            tx = getattr(d, "target_x", None)
            ty = getattr(d, "target_y", None)
            frozen = bool(getattr(d, "frozen", False))
            x = float(getattr(d, "x", 0.0) or 0.0)
            y = float(getattr(d, "y", 0.0) or 0.0)
            off_x = None if tx is None else abs(float(tx) - x)
            off_y = None if ty is None else abs(float(ty) - y)
            rec = {
                "frame": frame,
                "pid": getattr(d, "player_id", None),
                "frozen": frozen,
                "x": x,
                "y": y,
                "tx": None if tx is None else float(tx),
                "ty": None if ty is None else float(ty),
                "off_x": off_x,
                "off_y": off_y,
                "speed": math.hypot(float(getattr(d, "vx", 0.0) or 0.0),
                                    float(getattr(d, "vy", 0.0) or 0.0)),
                "source": getattr(d, "target_source", None),
                "move_speed": float(getattr(d, "move_speed", 0.0) or 0.0),
                "state": getattr(
                    getattr(d, "movement_state", None), "name", None
                ) or str(getattr(d, "movement_state", None)),
                "frozen_attr": bool(getattr(d, "frozen", False)),
            }
            # The stall predicate, stated once: not frozen, barely moving,
            # and holding a target the body has NOT reached.  A body parked
            # ON its target is excluded by the offset test.
            rec["live_target"] = bool(
                off_x is not None and off_y is not None
                and (off_x > TARGET_EPS_M or off_y > TARGET_EPS_M)
            )
            records.append(rec)
            if not frozen and not in_field(rec["tx"], rec["ty"]):
                out_of_field.append((frame, rec["pid"], rec["tx"], rec["ty"],
                                     rec["source"], frozen))

    return {
        "open_frames": open_frames,
        "records": records,
        "out_of_field": out_of_field,
        "frames": frames,
    }


def scan_live_stalls(res):
    """Count stalled body-frames and the longest sustained stall per player.

    A body-frame is STALLED when it is NOT frozen, is barely moving
    (speed < STALL_SPEED_MPS) and holds a target it has NOT reached
    (|target - position| > TARGET_EPS on either axis).  That is the exact
    predicate the probe used to find the defect.

    Returns ``(n_stalled, runs)`` where ``runs`` maps ``pid`` to
    ``(longest_consecutive_run, first_frame, last_frame)``.
    """
    stalls_by_pid = {}
    n_stalled = 0
    for r in res["records"]:
        if r["frozen"]:
            continue
        if not r.get("live_target", False):
            continue
        if r["speed"] >= STALL_SPEED_MPS:
            continue
        n_stalled += 1
        stalls_by_pid.setdefault(r["pid"], []).append(r["frame"])

    runs = {}
    for pid, frames in stalls_by_pid.items():
        frames = sorted(frames)
        best = 1
        cur = 1
        run_start = frames[0]
        best_first, best_last = frames[0], frames[0]
        for i in range(1, len(frames)):
            if frames[i] == frames[i - 1] + 1:
                cur += 1
            else:
                cur = 1
                run_start = frames[i]
            if cur > best:
                best = cur
                best_first, best_last = run_start, frames[i]
        runs[pid] = (best, best_first, best_last)
    return n_stalled, runs


def check_live_rollout():
    """[7] The engine: no out-of-field target, no sustained live stall."""
    print("")
    print("[7] LIVE ROLLOUT — seed %d, %d frames" % (SEED, FRAMES))
    real = sys.stdout
    _silence()
    res = run_rollout()
    _restore(real)

    n_of = len(res["out_of_field"])
    detail = ""
    if n_of:
        f, pid, tx, ty, src = res["out_of_field"][0][:5]
        # Report EVERY offending writer in one histogram, so the fix can
        # cover all of them in a single pass instead of one module per
        # rollout.
        by_src = {}
        for _f, _pid, _tx, _ty, _src, _fz in res["out_of_field"]:
            key = str(_src or "<none>")
            by_src[key] = by_src.get(key, 0) + 1
        hist = ", ".join(
            "%s=%d" % (k, v)
            for k, v in sorted(by_src.items(), key=lambda kv: -kv[1])[:8]
        )
        detail = ("%d violation(s); first frame=%s pid=%s tx=%.4f ty=%.4f "
                  "src=%s | by source: %s"
                  % (n_of, f, pid, tx, ty, src, hist))
    check("no non-frozen OPEN_PLAY body holds an out-of-field target",
          n_of == 0, detail)

    n_stalled, runs = scan_live_stalls(res)
    sustained = {p: v for p, v in runs.items()
                 if v[0] >= PERSISTENT_STALL_FRAMES}
    check("no non-frozen body holds a live target through a sustained "
          "stall (>= %d consecutive frames)" % PERSISTENT_STALL_FRAMES,
          not sustained,
          "%d stalled body-frame(s); %d sustained run(s) %s"
          % (n_stalled, len(sustained),
             {p: v[0] for p, v in sorted(sustained.items())} if sustained
             else ""))

    print("        OPEN_PLAY frames captured (non-tackle) = %d"
          % res["open_frames"])
    print("        body-frames recorded                   = %d"
          % len(res["records"]))
    print("        stalled body-frames                    = %d" % n_stalled)
    print("        longest stall run per player            = %s"
          % ({p: v[0] for p, v in sorted(runs.items())} or "{}"))

    # ── THE SUSTAINED RUNS, VERBATIM ─────────────────────────────────
    # Named so the check-7 verdict can be read without re-running anything:
    # for each sustained run, the first stalled body-frame of that run, with
    # the authority that owned its target and the speed budget it was given.
    if sustained:
        print("        --- first stalled frame of each sustained run ---")
        by_pid = {}
        for r in res["records"]:
            by_pid.setdefault(r["pid"], []).append(r)
        for pid in sorted(sustained, key=lambda p: -sustained[p][0]):
            _first = sustained[pid][1]
            row = next(
                (r for r in by_pid.get(pid, [])
                 if r["frame"] == _first and r.get("live_target")
                 and not r["frozen"] and r["speed"] < STALL_SPEED_MPS),
                None,
            )
            if row is None:
                continue
            print("          pid=%s frame=%s run=%d x=%.2f y=%.2f "
                  "tx=%s ty=%s off_x=%.3f off_y=%.3f spd=%.3f "
                  "move_speed=%.2f state=%s src=%s"
                  % (pid, _first, sustained[pid][0], row["x"], row["y"],
                     ("%.2f" % row["tx"]) if row["tx"] is not None else None,
                     ("%.2f" % row["ty"]) if row["ty"] is not None else None,
                     row["off_x"], row["off_y"], row["speed"],
                     row.get("move_speed", 0.0), row.get("state"),
                     row["source"]))
    return res


def main():
    print("=" * 96)
    print("VERIFY — OPEN_PLAY target reachability (the stationary-body defect)")
    print("=" * 96)
    print("  FIELD bounds  = [%.3f, %.3f] x [%.3f, %.3f]"
          % (FIELD_MIN_X, FIELD_MAX_X, FIELD_MIN_Y, FIELD_MAX_Y))
    print("  stalled       = frozen False AND speed < %.2f AND an unreached "
          "target" % STALL_SPEED_MPS)
    print("  persistent    = a stall run of >= %d consecutive frames"
          % PERSISTENT_STALL_FRAMES)

    check_clamp_helper()
    check_live_commit_point()
    check_seed_body()
    check_lock_depth_and_slow_step()
    check_tackler_commit()
    check_zero_speed_hold_is_band_exempt()
    check_live_rollout()

    print("")
    print("=" * 96)
    if failures:
        print("RESULT: FAIL — %d check(s): %s" % (len(failures), failures))
        print("=" * 96)
        return 1
    print("RESULT: PASS — every authored OPEN_PLAY target is inside the field")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())
