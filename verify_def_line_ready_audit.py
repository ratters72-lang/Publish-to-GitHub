"""Verifier for the per-frame DEF_LINE_READY audit dump (Task XIII addendum).

Boots the real engine exactly like run.py, runs until a TACKLE_DRIVE, and
captures the ``[DRIVE-AUDIT]`` lines emitted by
``game.debug.def_line_ready_audit.dump_def_line_ready_audit`` (wired into
``DefenceManager.update_tes_drive`` at the end of every drive frame).

Asserts:
  P1  the engine reaches a TACKLE_DRIVE (the audit window is armed).
  P2  at least one ``[DRIVE-AUDIT]`` frame block is emitted.
  P3  every per-defender ``[DRIVE-AUDIT]`` line carries ALL six required
      fields: eligibility (elig=), PTA ownership (pta=), FOLLOW active
      (follow=), freeze state (frozen=), spacing/dx (dx=), target source
      (src=).
  P4  at least one PTA-MANAGED body is shown (pta=COMMIT/PLANTED/SLOW/HOLD).
  P5  at least one NON-managed body is shown (pta=-) — both branches are
      exercised (ownership vs not).
  P6  the FOLLOW flag takes both truthful values (at least one follow=True
      and one follow=False) — the audit distinguishes the follow path.
  P7  frozen parses as bool and dx parses as float (numeric sanity).
  P8  the target-source field is a non-empty string.
"""
import contextlib
import io
import math
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

failures = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


collision_system = CollisionSystem()
field = Field(1920, 1080)
world = WorldState()
world.ruck_established = True

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

MAX_FRAMES = 1200
dt = 1.0 / 60.0

drive_reached = False
captured = io.StringIO()

# Redirect stdout for the whole run so the audit lines (and any other
# prints) are captured into `captured` for parsing.
with contextlib.redirect_stdout(captured):
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
            if cur not in ("TACKLE_DRIVE", "TACKLE_DOMINANCE"):
                if drive_reached:
                    break  # drive ended; stop
                continue
            drive_reached = True

text = captured.getvalue()
pygame.quit()

# P1: drive reached
check("P1: engine reached a TACKLE_DRIVE", drive_reached)

# P2: at least one audit frame block
audit_lines = [ln for ln in text.splitlines() if ln.startswith("[DRIVE-AUDIT]")]
frame_blocks = [ln for ln in text.splitlines() if ln.startswith("== [DRIVE-AUDIT]")]
check("P2: audit emitted at least one [DRIVE-AUDIT] frame block",
      len(frame_blocks) >= 1, f"{len(frame_blocks)} block(s)")

# Per-defender lines are the ones with ` id=`
def_lines = [ln for ln in audit_lines if " id=" in ln]
check("P3a: at least one per-defender audit line emitted",
      len(def_lines) >= 1, f"{len(def_lines)} line(s)")

# P3: every per-defender line has all six fields
required = ("elig=", "pta=", "follow=", "frozen=", "dx=", "src=")
missing_fields = []
for ln in def_lines:
    for f in required:
        if f not in ln:
            missing_fields.append(f"{f}(missing) in line: {ln}")
check("P3b: every per-defender line carries elig/pta/follow/frozen/dx/src",
      not missing_fields, " | ".join(missing_fields[:3]))

def _has_pta_state(ln):
    for st in ("COMMIT", "PLANTED", "SLOW", "HOLD"):
        if f"pta={st}" in ln:
            return True
    return False


# P4: at least one PTA-managed body
pta_managed = [ln for ln in def_lines if _has_pta_state(ln)]
check("P4: at least one PTA-MANAGED body shown (pta=COMMIT/PLANTED/SLOW/HOLD)",
      len(pta_managed) >= 1, f"{len(pta_managed)} managed line(s)")


# P5: at least one non-managed body
pta_unmanaged = [ln for ln in def_lines if "pta=-" in ln]
check("P5: at least one NON-managed body shown (pta=-)",
      len(pta_unmanaged) >= 1, f"{len(pta_unmanaged)} unmanaged line(s)")

# P6: FOLLOW flag takes both values
follow_t = [ln for ln in def_lines if "follow=True" in ln]
follow_f = [ln for ln in def_lines if "follow=False" in ln]
check("P6: FOLLOW flag exercised both ways (follow=True and follow=False)",
      len(follow_t) >= 1 and len(follow_f) >= 1,
      f"True={len(follow_t)} False={len(follow_f)}")

# P7: frozen parses as bool, dx parses as float
bool_ok = True
float_ok = True
for ln in def_lines:
    # frozen=
    fpos = ln.find("frozen=")
    if fpos >= 0:
        tok = ln[fpos:].split()[0].split("=", 1)[1]
        if tok not in ("True", "False"):
            bool_ok = False
    # dx=
    dpos = ln.find("dx=")
    if dpos >= 0:
        tok = ln[dpos:].split()[0].split("=", 1)[1]
        try:
            float(tok)
        except ValueError:
            float_ok = False
check("P7: frozen values are True/False", bool_ok)
check("P7: dx values parse as float", float_ok)

# P8: target source is a non-empty string
src_ok = True
for ln in def_lines:
    spos = ln.find("src=")
    if spos >= 0:
        tok = ln[spos:].split()[0].split("=", 1)[1]
        if not tok:
            src_ok = False
check("P8: every line carries a non-empty target source", src_ok)

# Show a couple of sample lines for the record
print("\n--- sample audit lines ---")
for ln in def_lines[:6]:
    print(ln[:160])

print("=" * 72)
if failures:
    print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
sys.exit(0)
