"""Headless verification for the 2026-08-07 TES defensive-spec correction.

Boots the real engine exactly like run.py, runs until a tackle cycle, and
samples defender roles at:

  * Last OPEN_PLAY frame before the tackle (baseline pre-tackle roles)
  * TACKLE_DRIVE        (must be UNCHANGED for non-ruck defenders)
  * TACKLE_DOMINANCE    (must be UNCHANGED for non-ruck defenders)
  * RELEASE             (all non-marker, non-DH defenders -> DEF_RETREAT_10)

Fails loudly if:
  A. Any non-tackler/non-marker defender was re-rolled to DEF_RETREAT_10
     during TACKLE_DRIVE / TACKLE_DOMINANCE (the old bug).
  B. RELEASE does not convert surviving line roles to DEF_RETREAT_10.
  C. The TES phase machine never reaches RELEASE.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

import sys
# Same UTF-8 stdout protection as run.py — the engine's freeze_audit
# prints "✅ No violations" every frame and crashes on cp1252 consoles.
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
# Silence per-frame SDEA physics spam — observational prints only.
ope_mod.DEBUG_SDEA_PHYSICS = False

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

TES_PHASES = frozenset({
    "TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
    "TACKLE_DOMINANCE", "RELEASE",
})
TACKLER_ROLES = frozenset({
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
})
MARKER_ROLES = frozenset({DefenderRole.MARKER_A, DefenderRole.MARKER_B})
OTHER_ROLES = frozenset({DefenderRole.DUMMY_HALF, DefenderRole.DEF_RETREAT_10})

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

MAX_FRAMES = 900
dt = 1.0 / 60.0

phase_samples = {}     # name -> {player_id: defender_role}
phase_order = []
last_openplay_roles = None   # baseline: roles on the last OPEN_PLAY frame
tackle_started = False
baseline_taken = False
reached_release = False
failures = []


def snapshot(name):
    if name in phase_samples:
        return
    phase_order.append(name)
    phase_samples[name] = {
        p.player_id: getattr(p, "defender_role", None)
        for p in world.defending_team
    }


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
        open_play.update(dt)
        if not world.tes_active:
            collision_system.resolve_all(world.players, world=world)

        cur = open_play._phase
        if cur == "OPEN_PLAY":
            # Roll baseline forward until tackle entry.
            last_openplay_roles = {
                p.player_id: getattr(p, "defender_role", None)
                for p in world.defending_team
            }
        elif cur in TES_PHASES:
            tackle_started = True
            # Baseline = pre-tackle open-play roles (first frame we see a
            # tackle phase may be mid-FSM; only baseline once, from the
            # last pure OPEN_PLAY sample).
            if not baseline_taken and last_openplay_roles is not None:
                baseline_taken = True
                phase_order.append("BASELINE_OPEN_PLAY")
                phase_samples["BASELINE_OPEN_PLAY"] = dict(last_openplay_roles)

            if cur == "TACKLE_DRIVE":
                snapshot("TACKLE_DRIVE")
            elif cur == "TACKLE_DOMINANCE":
                snapshot("TACKLE_DOMINANCE")
            elif cur == "RELEASE":
                snapshot("RELEASE")
                reached_release = True
                break

if not tackle_started:
    print("FAIL: no tackle cycle started within", MAX_FRAMES, "frames")
    pygame.quit()
    sys.exit(1)
if not reached_release:
    print("FAIL: FSM never reached RELEASE. Phases seen:", phase_order)
    pygame.quit()
    sys.exit(1)

baseline = phase_samples["BASELINE_OPEN_PLAY"]

# ── ASSERT A: non-ruck defenders keep their pre-tackle role through drive ──
# Tackle participants legitimately change from DEF_LINE_READY (baseline) to
# TACKLER_1/2/3 the moment _start_tackle assigns them — TES is ALLOWED to
# affect the ball-carrier and tackle participants.  Every defender that is
# NOT a tackler in the drive snapshot must be unchanged from baseline and
# must NEVER be re-rolled to DEF_RETREAT_10 during the drive.
for drive_phase in ("TACKLE_DRIVE", "TACKLE_DOMINANCE"):
    drive_roles = phase_samples[drive_phase]
    drive_tacklers = {
        pid for pid, r in drive_roles.items() if r in TACKLER_ROLES
    }
    for pid, role in baseline.items():
        if pid in drive_tacklers:
            continue  # TES affects tackle participants — allowed
        if role in TACKLER_ROLES or role in OTHER_ROLES or role is None:
            continue  # baseline tacklers/DH/RETREAT allowed to change
        new_role = drive_roles.get(pid)
        if new_role != role:
            failures.append(
                "ASSERT-A: pid %s role changed %s -> %s during %s (must stay %s)"
                % (pid, role, new_role, drive_phase, role)
            )
        if new_role == DefenderRole.DEF_RETREAT_10:
            failures.append(
                "ASSERT-A-BUG: pid %s re-rolled to DEF_RETREAT_10 during %s — "
                "the old _start_tackle re-role is still active" % (pid, drive_phase)
            )

# ── ASSERT B: RELEASE converts all surviving line roles to DEF_RETREAT_10 ──
# FULLBACK EXEMPTION (2026-08-13): the dynamic fullback is NOT a line/retreat
# actor.  `_prepare_release()` intentionally keeps it DEF_LINE_READY and its
# backfield anchor is re-asserted by the phase handlers.  Match the engine's
# contract (see verify_mmdl_fullback_exclusion.py).
release_roles = phase_samples["RELEASE"]
release_fullback_ids = {
    p.player_id
    for p in world.defending_team
    if getattr(p, "fullback_role_active", False)
}
for pid, role in release_roles.items():
    if role in MARKER_ROLES or role == DefenderRole.DUMMY_HALF or role is None:
        continue
    if pid in release_fullback_ids:
        continue  # fullback keeps DEF_LINE_READY by design
    if role != DefenderRole.DEF_RETREAT_10:
        failures.append(
            "ASSERT-B: pid %s has role %s at RELEASE (expected DEF_RETREAT_10)"
            % (pid, role)
        )

# ── ASSERT C: drive/dominance/release all visited, in order ──
ordered = [p for p in phase_order
           if p in ("TACKLE_DRIVE", "TACKLE_DOMINANCE", "RELEASE")]
expected = ["TACKLE_DRIVE", "TACKLE_DOMINANCE", "RELEASE"]
if ordered != expected:
    failures.append("ASSERT-C: TES phase sequence corrupted: %s (expected %s)"
                    % (ordered, expected))

print("=" * 72)
print("TES DRIVE DEFENSIVE-SPEC VERIFICATION")
print("=" * 72)
print("Phases seen:", phase_order)
for p in ("BASELINE_OPEN_PLAY", "TACKLE_DRIVE", "TACKLE_DOMINANCE", "RELEASE"):
    if p not in phase_samples:
        continue
    print("\n-- %s --" % p)
    for pid, role in phase_samples[p].items():
        print("   pid %-3s %s" % (pid, role))

print("\n" + "=" * 72)
if failures:
    print("RESULT: FAIL")
    for f in failures:
        print("  -", f)
    pygame.quit()
    sys.exit(1)

print("RESULT: PASS")
print("  - No defender re-rolled to DEF_RETREAT_10 during DRIVE/DOMINANCE")
print("  - Non-ruck roles unchanged through the drive (open-play AI intact)")
print("  - RELEASE feeds DEF_RETREAT_10 to all non-marker, non-DH defenders")
print("  - TES phase machine clean: DRIVE -> DOMINANCE -> RELEASE")
pygame.quit()
sys.exit(0)
