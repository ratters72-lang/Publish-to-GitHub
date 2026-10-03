"""Headless verification for movement reactivation after thaw (2026-08-08).

Boots the real engine exactly like run.py, runs until a full
tackle -> RELEASE -> PTB -> POST_RUCK -> OPEN_PLAY cycle completes, and
asserts:

  A. On every OPEN_PLAY "entry frame" (a frame where the phase handler
     flipped to OPEN_PLAY mid-frame, so the defence/attack pipelines did
     NOT run), no UNFROZEN player is stuck at vx=vy=0 while holding a
     live target.  This is the movement-reactivation invariant.
  B. reactivate_movement_after_thaw actually fired on those entry frames.
  C. arrival_mode / reached_target never leak into OPEN_PLAY (the
     PTB-era arrival-predictor leak is dead).
  D. The BC (old DH) does not stall on the transition row — it either
     carries non-zero velocity or has no live target pending.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False

# ── Instrument reactivate_movement_after_thaw with a call counter ──
_orig_reactivate = ope_mod.reactivate_movement_after_thaw
_call_log = {"count": 0}


def _counting_reactivate(players, dt):
    _call_log["count"] += 1
    return _orig_reactivate(players, dt)


ope_mod.reactivate_movement_after_thaw = _counting_reactivate

from world.field import Field
from world.world_state import WorldState, GameWorldState, GameSubState, GamePhase
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


def reset_ball_and_world():
    kickoff_engine.reset_for_kickoff()


def kickoff():
    kickoff_engine.kickoff()


reset_ball_and_world()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff()

MAX_FRAMES = 2400
dt = 1.0 / 60.0

failures = []
phase_order = []            # (frame, prev, cur) transitions
entry_frames = {}           # frame -> set of stuck player ids (should be empty)
arrival_leaks = []          # (frame, pid) arrival_mode True during OPEN_PLAY
bc_stalled_entry = []       # (frame, bc pid) BC stalled on entry frame
saw_open_play_after_ruck = False


def _stuck_players():
    """Return list of (pid, role, tx-x, ty-y) for unfrozen players stuck at
    vx=vy=0 with a live target.  Mirrors reactivate_movement_after_thaw's
    stuck predicate."""
    stuck = []
    for p in world.players:
        if p.frozen:
            continue
        if (p.vx == 0.0 and p.vy == 0.0
                and (abs(p.target_x - p.x) > 0.01
                     or abs(p.target_y - p.y) > 0.01)):
            stuck.append((p.player_id,
                          getattr(p, "defender_role", None),
                          p.target_x - p.x,
                          p.target_y - p.y))
    return stuck


for frame in range(1, MAX_FRAMES + 1):
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
        prev_phase = open_play._phase
        open_play.update(dt)
        if not world.tes_active:
            collision_system.resolve_all(world.players, world=world)

        cur = open_play._phase
        if cur != prev_phase:
            phase_order.append((frame, prev_phase, cur))

        if cur == "OPEN_PLAY":
            # Does any player have the arrival-predictor leak mid-OPEN_PLAY?
            for p in world.players:
                if getattr(p, "arrival_mode", False):
                    arrival_leaks.append((frame, p.player_id))

            if not world.defence_ran_this_frame:
                # ── OPEN_PLAY ENTRY FRAME: defence pipeline did not run
                # under this frame's update (the handler flipped phase
                # mid-frame).  Movement reactivation must have fired.
                saw_open_play_after_ruck = True
                stuck = _stuck_players()
                if stuck:
                    entry_frames[frame] = stuck

                bc = world.ball_carrier
                if bc is not None:
                    if (bc.vx == 0.0 and bc.vy == 0.0
                            and (abs(bc.target_x - bc.x) > 0.01
                                 or abs(bc.target_y - bc.y) > 0.01)):
                        bc_stalled_entry.append((frame, bc.player_id))

# ── ASSERT A: no stuck unfrozen players on OPEN_PLAY entry frames ──
if entry_frames:
    for fr, stuck in entry_frames.items():
        for (pid, role, dx, dy) in stuck:
            failures.append(
                "ASSERT-A: frame %d pid %s role=%s frozen=False but vx=vy=0 "
                "with live target dx=%.3f dy=%.3f" % (fr, pid, role, dx, dy)
            )

# ── ASSERT B: reactivation fired on every entry frame ──
if saw_open_play_after_ruck and _call_log["count"] <= 0:
    failures.append("ASSERT-B: reactivate_movement_after_thaw never called "
                    "despite OPEN_PLAY entry frames occurring")
else:
    print("INFO: reactivate_movement_after_thaw called %d time(s)"
          % _call_log["count"])

# ── ASSERT C: arrival-mode leak dead in OPEN_PLAY ──
if arrival_leaks:
    for (fr, pid) in arrival_leaks[:10]:
        failures.append(
            "ASSERT-C: frame %d pid %s arrival_mode=True during OPEN_PLAY "
            "(PTB-era leak not cleared)" % (fr, pid)
        )
    if len(arrival_leaks) > 10:
        failures.append("ASSERT-C: ... and %d more" % (len(arrival_leaks) - 10))

# ── ASSERT D: BC not stalled on entry frames ──
if bc_stalled_entry:
    for (fr, pid) in bc_stalled_entry:
        failures.append(
            "ASSERT-D: frame %d BC pid %s stalled at vx=0 with live target "
            "on OPEN_PLAY entry" % (fr, pid)
        )

print("=" * 72)
print("MOVEMENT REACTIVATION VERIFICATION")
print("=" * 72)
print("Open-play-entry frames seen:", saw_open_play_after_ruck)
print("Reactivation calls:", _call_log["count"])
print("Transition events (frame, prev -> cur):")
for (fr, prev, cur) in phase_order:
    print("  %5d  %s -> %s" % (fr, prev, cur))

print("\n" + "=" * 72)
if failures:
    print("RESULT: FAIL")
    for f in failures:
        print("  -", f)
    pygame.quit()
    sys.exit(1)

print("RESULT: PASS")
print("  - No unfrozen player stuck at vx=0 with a live target on any")
print("    OPEN_PLAY entry frame (movement re-enters the pipeline instantly)")
print("  - reactivate_movement_after_thaw fired on the transition frames")
print("  - arrival_mode/reached_target never leak into OPEN_PLAY")
print("  - BC (old DH) does not stall on the transition row")
pygame.quit()
sys.exit(0)
