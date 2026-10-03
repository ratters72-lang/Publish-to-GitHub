"""Headless engine probe — RUCK-ADJACENT SLOT-1 + SLOT-2 AUDIT.

Boots the real engine exactly like run.py / tmp/chase_capture_engine.py,
runs MAX_FRAMES headless, and prints ONE audit line per OPEN_PLAY frame
measuring, per side of the ruck:

  slot-1 (A): MEASURED Y-distance |pos - ruck_y| — must hold 2.00 m.
  slot-2 (B): MEASURED GAP |pos - anchor| from the A-lane — must stay
              within 3.5 m (soft max-gap constraint).

    [RUCK-PIN] f=1234 sub=OPEN_PLAY ruck=35.00
        | L id=7 role=A tgt=33.00 pos=33.02 d=2.02 spc=33.00 n=6
        | L2 id=9 role=B gap=2.80 OK
        | R id=12 ... | R2 id=14 ...

Fields:
    id/role  player_id + depth_role of the measured defender
    tgt      final target_y after ALL writers ran this frame
    pos      actual y position this frame (post-collision)
    d        slot-1: |pos - ruck_y|   (compliance number)
    gap      slot-2: |pos - anchor|  (soft-bound compliance)
    spc      frame_spacing_targets value (pure chain output)

Slot-1 whose d stays far from 2.0 while spc == 2.0 has a DOWNSTREAM
WRITER overriding the chain; slot-1 whose spc != 2.0 has a CHAIN problem.
Slot-2 with gap > 3.5 (+0.25 collision tolerance) is an UNBOUNDED DRIFT.

Usage:
  python tmp/ruck_adjacent_audit_probe.py > tmp/ruck_pin_out.txt 2>&1
"""
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

# Silence per-frame SDEA physics spam.
ope_mod.DEBUG_SDEA_PHYSICS = False

from world.field import Field
from world.world_state import WorldState, GamePhase
from world.open_play_engine import OpenPlayEngine
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch
from player.player import DefenderRole

collision_system = CollisionSystem()
field = Field(1920, 1080)
world = WorldState()
ball = Ball(field)
world.ball = ball

open_play = OpenPlayEngine(world)
world.open_play = open_play
world.open_play_engine = open_play

kickoff_engine = KickoffEngine(world, ball, field)

kickoff_engine.reset_for_kickoff()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff_engine.kickoff()

MAX_FRAMES = 1500
dt = 1.0 / 60.0

SLOT1_TARGET_M = 2.0
SLOT1_TOL_M = 0.5
SLOT2_MAX_GAP_M = 3.5
SLOT2_TOL_M = 0.25  # post-collision tolerance on the soft bound

# Roles that never hold a line slot (mirror the spacing-chain exclusions).
_EXCLUDED_ROLES = frozenset({
    DefenderRole.MARKER_A,
    DefenderRole.MARKER_B,
    DefenderRole.DUMMY_HALF,
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
    DefenderRole.DEF_CHASE,
})


def _eligible_line_defenders():
    out = []
    tackler_ids = getattr(world, "tackler_ids", None) or set()
    for d in getattr(world, "defending_team", []) or []:
        if d is None:
            continue
        if getattr(d, "fullback_role_active", False):
            continue
        if getattr(d, "frozen", False) and getattr(d, "freeze_timer", 0.0) > 0.0:
            continue
        if getattr(d, "defender_role", None) in _EXCLUDED_ROLES:
            continue
        if getattr(d, "player_id", None) in tackler_ids:
            continue
        if hasattr(d, "chase_target_x") or hasattr(d, "chase_target_y"):
            continue
        out.append(d)
    return out


def _side_reports(defs, ruck_y, side):
    """Two nearest eligible defenders on *side* of ruck_y + Y sources."""
    if side < 0:
        pool = [d for d in defs if d.y < ruck_y]
    else:
        pool = [d for d in defs if d.y > ruck_y]
    if not pool:
        return None, None
    ordered = sorted(
        pool, key=lambda d: (abs(d.y - ruck_y), getattr(d, "player_id", 0))
    )
    dct = getattr(world, "frame_spacing_targets", None) or {}
    fresh = getattr(world, "frame_geometry_ready", False) and (
        getattr(world, "frame_geometry_frame", None) == getattr(world, "frame_count", None)
    )

    def _rep(d):
        spc = dct.get(id(d))
        return {
            "id": getattr(d, "player_id", "?"),
            "role": getattr(d, "depth_role", None) or "-",
            "tgt": float(getattr(d, "target_y", float("nan"))),
            "pos": float(d.y),
            "d": abs(float(d.y) - ruck_y),
            "spc": (float(spc) if spc is not None else None),
            "fresh": fresh,
            "n_side": len(pool),
        }

    first = _rep(ordered[0])
    second = _rep(ordered[1]) if len(ordered) > 1 else None
    return first, second


def _fmt(rep):
    if rep is None:
        return "none"
    spc = "None" if rep["spc"] is None else f"{rep['spc']:.2f}"
    return (
        f"id={rep['id']} role={rep['role']} tgt={rep['tgt']:.2f} "
        f"pos={rep['pos']:.2f} d={rep['d']:.2f} spc={spc} n={rep['n_side']}"
    )


def _fmt2(rep, anchor):
    if rep is None:
        return "none"
    gap = abs(rep["pos"] - anchor)
    return f"id={rep['id']} role={rep['role']} gap={gap:.2f}"


frames_open_play = 0
worst_left = (0.0, None)     # slot-1 distance
worst_right = (0.0, None)
worst_l2 = (0.0, None)       # slot-2 gap from A-lane
worst_r2 = (0.0, None)
deviant_frames = 0           # slot-1 |d-2| > 0.5
slot2_violation_frames = 0   # slot-2 gap > 3.75

for frame in range(1, MAX_FRAMES + 1):
    world.frame_count = frame
    ball.update(dt)

    if world.phase == GamePhase.KICKOFF:
        kickoff_engine.update(dt)
        check_for_catch(world, ball, dt)
        continue
    elif world.phase == GamePhase.CATCH:
        if world.ball_carrier is not None:
            world.open_play.start(world.ball_carrier)
        world.phase = GamePhase.OPEN_PLAY
        continue

    # GamePhase.OPEN_PLAY
    open_play.update(dt)
    if not world.tes_active:
        collision_system.resolve_all(world.players, world=world)

    ruck_y = getattr(world, "ruck_y_m", None)
    if ruck_y is None:
        continue
    ruck_y = float(ruck_y)
    defs = _eligible_line_defenders()
    if not defs:
        continue

    left, left2 = _side_reports(defs, ruck_y, -1)
    right, right2 = _side_reports(defs, ruck_y, +1)
    frames_open_play += 1

    sub = str(getattr(world, "sub_state", "?")).split(".")[-1]
    print(
        f"[RUCK-PIN] f={frame} sub={sub} ruck={ruck_y:.2f} "
        f"| L {_fmt(left)} | L2 {_fmt2(left2, ruck_y - SLOT1_TARGET_M)} "
        f"| R {_fmt(right)} | R2 {_fmt2(right2, ruck_y + SLOT1_TARGET_M)}"
    )

    for rep, slot in ((left, "L"), (right, "R")):
        if rep is None:
            continue
        key = worst_left if slot == "L" else worst_right
        if rep["d"] > key[0]:
            entry = (rep["d"], frame, rep)
            if slot == "L":
                worst_left = entry
            else:
                worst_right = entry
        if abs(rep["d"] - SLOT1_TARGET_M) > SLOT1_TOL_M:
            deviant_frames += 1
            break

    for rep2, slot in ((left2, "L2"), (right2, "R2")):
        if rep2 is None:
            continue
        anchor = ruck_y - SLOT1_TARGET_M if slot == "L2" else ruck_y + SLOT1_TARGET_M
        gap = abs(rep2["pos"] - anchor)
        key = worst_l2 if slot == "L2" else worst_r2
        if gap > key[0]:
            entry = (gap, frame, rep2)
            if slot == "L2":
                worst_l2 = entry
            else:
                worst_r2 = entry
        if gap > SLOT2_MAX_GAP_M + SLOT2_TOL_M:
            slot2_violation_frames += 1
            break

print()
print("=== RUCK-PIN SUMMARY ===")
print(f"frames_run={MAX_FRAMES}")
print(f"open_play_frames_audited={frames_open_play}")
print(f"deviant_frames(any side |d-2|>0.5m)={deviant_frames}")
print(f"slot2_violation_frames(any side gap>{SLOT2_MAX_GAP_M + SLOT2_TOL_M})={slot2_violation_frames}")
if worst_left[1] is not None:
    print(f"worst L: frame={worst_left[1]} d={worst_left[0]:.2f} {worst_left[2]}")
else:
    print("worst L: none")
if worst_right[1] is not None:
    print(f"worst R: frame={worst_right[1]} d={worst_right[0]:.2f} {worst_right[2]}")
else:
    print("worst R: none")
if worst_l2[1] is not None:
    print(f"worst L2-gap: frame={worst_l2[1]} gap={worst_l2[0]:.2f} {worst_l2[2]}")
else:
    print("worst L2-gap: none")
if worst_r2[1] is not None:
    print(f"worst R2-gap: frame={worst_r2[1]} gap={worst_r2[0]:.2f} {worst_r2[2]}")
else:
    print("worst R2-gap: none")
print("=== END RUCK-PIN ===")
pygame.quit()
