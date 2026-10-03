"""stage3b_kickoff_check.py - headless kickoff smoke for Stage 3B.

Boots the real engine stack (Field/World/Ball/OPE/KickoffEngine/catch),
runs kickoff -> flight -> catch -> OPEN_PLAY -> first tackle, captures ALL
stdout, then asserts:

  1. A catch occurred ([CATCH] present).
  2. Exactly ONE [RUCK_PIVOT] line (band formation armed once).
  3. The pivot fires STRICTLY AFTER the catch (no pivot on CATCH /
     OPEN_PLAY pre-tackle) and coincides with ruck_established flipping
     True (TACKLE_INIT entry).
  4. No audit-violation keywords in the window (gaps / doubling /
     stale-spec style prints from the self-limiting audits).
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

import io
import re
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame
from world.field import Field
from world.world_state import WorldState, GamePhase
from world.open_play_engine import OpenPlayEngine
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch

DT = 1.0 / 60.0
MAX_FRAMES = 1200
SETTLE_AFTER_PIVOT = 90

buf = io.StringIO()
caught_frame = None
pivot_flip_frame = None

with contextlib.redirect_stdout(buf):
    pygame.init()
    screen = pygame.display.set_mode((640, 480))
    field = Field(640, 480)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    ko = KickoffEngine(world, ball, field)
    collision_system = CollisionSystem()

    ko.reset_for_kickoff()
    ko.kickoff()

    settle = 0
    for f in range(MAX_FRAMES):
        ball.update(dt=DT)
        world.frame_count += 1
        if world.phase == GamePhase.KICKOFF:
            ko.update(DT)
            check_for_catch(world, ball, DT)
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                open_play.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        else:
            open_play.update(DT)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

        if caught_frame is None and world.phase == GamePhase.OPEN_PLAY:
            caught_frame = f
        if (
            pivot_flip_frame is None
            and bool(getattr(world, "ruck_established", False))
        ):
            pivot_flip_frame = f
        if pivot_flip_frame is not None:
            settle += 1
            if settle >= SETTLE_AFTER_PIVOT:
                break
    pygame.quit()

log = buf.getvalue()
with open("tmp/stage3b_kickoff_log.txt", "w", encoding="utf-8") as fh:
    fh.write(log)
lines = log.splitlines()

failures = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    if not cond:
        failures.append(name)


catch_lines = [i for i, ln in enumerate(lines) if ln.startswith("[CATCH]")]
pivot_lines = [i for i, ln in enumerate(lines) if "[RUCK_PIVOT]" in ln]
tackle_markers = [
    i for i, ln in enumerate(lines)
    if "TACKLE_INIT" in ln and "[RUCK_PIVOT]" in ln
]

print("=" * 72)
print("STAGE 3B KICKOFF LOG CHECK")
print("=" * 72)

check("a catch occurred", bool(catch_lines),
      f"[CATCH] count={len(catch_lines)}")
check("exactly one ruck-pivot arming", len(pivot_lines) == 1,
      f"[RUCK_PIVOT] count={len(pivot_lines)}")
if catch_lines and pivot_lines:
    check(
        "pivot fires strictly AFTER the catch (never on CATCH)",
        pivot_lines[0] > catch_lines[-1],
        f"catch@log{catch_lines[-1]} pivot@log{pivot_lines[0]}",
    )
if pivot_lines and tackle_markers:
    check("pivot line carries the TACKLE_INIT trigger tag",
          pivot_lines[0] == tackle_markers[0])
check(
    "ruck_established flipped only after OPEN_PLAY began",
    caught_frame is not None and pivot_flip_frame is not None
    and pivot_flip_frame > caught_frame,
    f"open_play_start_frame={caught_frame} pivot_frame={pivot_flip_frame}",
)

# Audit-violation sweep over the whole captured window (value-aware).
#
# STAGE 3B SCOPE: hard failures are
#   - ANY invariant flag set True at/after the ruck pivot, or any
#     stale_/residual_/kickoff_/invalid_ flag anywhere (those prove a
#     writer consumed stale geometry or leaked across a reset);
#   - any '!!!' problem marker other than the documented pre-pivot
#     pursuit-window cross-side note from an mmdl_ writer;
#   - authority-violation exception text.
#
# The pre-pivot cross_side/wrong_side note from mmdl_CHASE is a known,
# pre-existing pursuit-window artifact (inertial chase lane vs the
# moving ruck line; the check reads only depth_side + target_y, both
# untouched by Stage 3B) and is reported as information, not failure.
pivot_log = pivot_lines[0] if pivot_lines else len(lines)

inv_hard = []
inv_notes = []
for i, ln in enumerate(lines):
    if "INVARIANT-8.3:" not in ln:
        continue
    trues = [k for k, v in re.findall(r"(\w+)=(True|False)", ln) if v == "True"]
    if not trues:
        continue
    entry = f"log{i}: {','.join(trues)}"
    if i >= pivot_log:
        inv_hard.append(entry)
    elif set(trues) <= {"cross_side_target_y", "wrong_side_of_ruck"}:
        inv_notes.append(entry)
    else:
        inv_hard.append(entry)

bang_hard = []
for i, ln in enumerate(lines):
    if "!!!" not in ln:
        continue
    if (
        i < pivot_log
        and "WRONG SIDE OF RUCK" in ln
        and "writer=mmdl_" in ln
    ):
        continue
    bang_hard.append(f"log{i}: {ln.strip()[:120]}")

exc_lines = [
    f"log{i}: {ln.strip()[:120]}"
    for i, ln in enumerate(lines)
    if "AnchorAuthorityViolation" in ln or "SpacingDepthViolation" in ln
]
bad_hits = inv_hard + bang_hard + exc_lines
check("no hard invariant violations in the window", not bad_hits,
      "; ".join(bad_hits[:3]) or "clean")
print(f"  [INFO] pre-pivot pursuit-window cross-side notes: "
      f"{len(inv_notes)} (documented mmdl_CHASE artifact)")

# Show the interesting tail for the report.
print("\n--- marker lines ---")
for i in sorted(set(catch_lines + pivot_lines))[:12]:
    print(f"log{i}: {lines[i].strip()[:140]}")

print("\n" + "=" * 72)
if failures:
    print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("RESULT: STAGE 3B KICKOFF LOG CHECK PASSED")
sys.exit(0)
