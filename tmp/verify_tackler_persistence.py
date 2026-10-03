"""
Tackler Persistence Verification Harness.

Proves that once a defender becomes a tackler (is_tackler = True) they
remain a tackler for the whole TES engagement (CONTACT -> DRIVE ->
DOMINANCE -> RELEASE), or reports the EXACT frame + phase + defender
where tackler status drops.

Mirrors tmp/sim_full_tackle.py wiring (headless, real OpenPlayEngine).
Only READS engine state — no production code is modified.
"""
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.path.insert(0, ".")

import pygame

pygame.init()
pygame.display.set_mode((64, 64))

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

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
field = Field(SCREEN_WIDTH, SCREEN_HEIGHT)

world = WorldState()
ball = Ball(field)
world.ball = ball
open_play = OpenPlayEngine(world)
world.open_play = open_play
world.open_play_engine = open_play
kickoff_engine = KickoffEngine(world, ball, field)
collision_system = CollisionSystem()

kickoff_engine.reset_for_kickoff()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff_engine.kickoff()

DT = 1.0 / 60.0
MAX_FRAMES = 60 * 60  # 60 sim-seconds

TES_PHASES = frozenset({
    "TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
    "TACKLE_DOMINANCE", "RELEASE",
})

# Frame-by-frame tackler flag timeline (player_id -> list of (frame, phase, is_tackler))
timeline = {}
# First frame each player was observed as a tackler (is_tackler True in an active TES phase)
qualified_frame = {}
# Drops = (frame, phase, player_id, dist_to_bc) at the instant is_tackler flipped True->False
drops = []
# Fresh tacklers seen mid-engagement (late joiners / defence-subsystem re-adds)
new_tacklers_seen = {}
phases_seen = set()
tackle_observed = False
release_observed = False
release_reset_failed = None  # (frame, player_id) if any defender still is_tackler after RELEASE


def is_tes_phase(phase):
    return phase in TES_PHASES


for frame in range(MAX_FRAMES):
    ball.update(DT)
    world.frame_count += 1

    if world.phase == GamePhase.KICKOFF:
        kickoff_engine.update(DT)
        check_for_catch(world, ball, DT)
    elif world.phase == GamePhase.CATCH:
        if world.ball_carrier is not None:
            ope = world.open_play
            if ope is not None:
                ope.start(world.ball_carrier)
        world.phase = GamePhase.OPEN_PLAY
    elif world.phase == GamePhase.OPEN_PLAY:
        open_play.update(DT)
        if not world.tes_active:
            collision_system.resolve_all(world.players, world=world)

    phase = open_play._phase
    if not is_tes_phase(phase):
        if phase == "PTB_PREP" and release_observed:
            break
        continue

    phases_seen.add(phase)
    if phase != "RELEASE":
        tackle_observed = True
    else:
        release_observed = True

    bc = world.ball_carrier

    # Track every defender that is (or has been) a tackler
    defenders = list(world.defending_team)
    tracked = [d for d in defenders if d.is_tackler or d.player_id in timeline]

    for d in tracked:
        pid = d.player_id
        flag = bool(d.is_tackler)
        timeline.setdefault(pid, []).append((frame, phase, flag))

        prev = timeline[pid][-2] if len(timeline[pid]) >= 2 else None

        if flag and pid not in qualified_frame:
            qualified_frame[pid] = frame
            new_tacklers_seen[pid] = (frame, phase)

        # Detect a True->False transition while engagement is active.
        # "Active" = not yet at RELEASE clean sweep (we validate RELEASE separately).
        if prev is not None and prev[2] and not flag and phase != "RELEASE":
            dist = 0.0
            if bc is not None:
                dist = ((bc.x - d.x) ** 2 + (bc.y - d.y) ** 2) ** 0.5
            drops.append((frame, phase, pid, dist))

# Final check: run a few extra frames of RELEASE and confirm no tacklers remain
for _ in range(30):
    if world.phase != GamePhase.OPEN_PLAY or open_play._phase != "RELEASE":
        break
    ball.update(DT)
    world.frame_count += 1
    if open_play._phase == "RELEASE":
        open_play.update(DT)
    phase = open_play._phase
    if phase == "RELEASE":
        leftovers = [p.player_id for p in world.defending_team
                     if getattr(p, "is_tackler", False)]
        if leftovers:
            release_reset_failed = (world.frame_count, leftovers)
            break

print("=" * 70)
print("TACKLER PERSISTENCE VERIFICATION")
print("=" * 70)
print(f"TES phases observed: {sorted(phases_seen)}")
print(f"tackle_observed={tackle_observed}  release_observed={release_observed}")
print()
print("Tackler qualification timeline (first frame seen as tackler):")
for pid, (fr, ph) in sorted(new_tacklers_seen.items()):
    n = len(timeline[pid])
    last_frame, last_phase, last_flag = timeline[pid][-1]
    drop_here = [x for x in drops if x[2] == pid]
    print(f"  tackler {pid:3d}: qualified at frame {fr} ({ph:16s}) "
          f"tracked {n:4d} frames | last={last_phase}, is_tackler={last_flag}"
          + (f" | DROPS: {len(drop_here)}" if drop_here else ""))

print()
if drops:
    print(f"PERSISTENCE FAILURES: {len(drops)} True->False transition(s)")
    for fr, ph, pid, dist in drops:
        print(f"  frame {fr:5d}  {ph:16s}  tackler {pid}  dist_to_bc={dist:.2f}")
else:
    print("PERSISTENCE: no tackler lost tackler status during CONTACT/DRIVE/DOMINANCE")

print()
if release_reset_failed is not None:
    print(f"RELEASE RESET FAILURE: tacklers still flagged at frame "
          f"{release_reset_failed[0]}: {release_reset_failed[1]}")
else:
    print("RELEASE RESET: all tacklers cleared at RELEASE")

print()
ok = (
    tackle_observed
    and release_observed
    and not drops
    and release_reset_failed is None
)
print("RESULT:", "PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)
