"""
tmp/verify_post_ruck_quiet.py - Headless verification of post-ruck
timing/reactivity/jiggle quieting + pre-tackle jiggle fix.

Boots the full sim chain (kickoff -> catch -> OPEN_PLAY -> tackle cycle ->
PTB -> post-ruck OPEN_PLAY), captures ALL stdout the real engine emits,
then classifies every FREEZE_AUDIT block by its frame number into two
windows using frame boundaries captured in-loop:

  pre-tackle-1 : [0, first_tackle_frame)
  post-ruck    : [post_ruck_start_frame, end)

No string slicing on sub-state names, so a change in sub-state naming or
flow cannot masquerade as a pass/fail.  The distinct phase/sub-state
sequence is printed so flow changes are visible.

Exit 0 = full cycle completed AND:
    - post-ruck window: all AGENT/VIOLATION keys == 0
    - pre-tackle-1 window: TARGET_JIGGLE == 0 (raw behaviour, no grace)
"""

import argparse
import contextlib
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
os.environ["SDL_VIDEODRIVER"] = "dummy"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import random  # noqa: E402

random.seed(42)

import pygame  # noqa: E402

pygame.init()
pygame.display.set_mode((64, 64))

from world.field import Field  # noqa: E402
from world.world_state import WorldState, GamePhase  # noqa: E402
from world.open_play_engine import OpenPlayEngine  # noqa: E402
from kick_off.kickoff_formations import (  # noqa: E402
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine  # noqa: E402
from ball.ball import Ball  # noqa: E402
from game.physics.collision_system import CollisionSystem  # noqa: E402
from world.catch_system import check_for_catch  # noqa: E402
from ai_agents.defence_reactivity_agent import DefenceReactivityAgent  # noqa: E402


parser = argparse.ArgumentParser()
parser.add_argument("--max-frames", type=int, default=15000)
parser.add_argument("--post-ruck-settle", type=int, default=90)
args = parser.parse_args()

DT = 1.0 / 60.0
MAX_FRAMES = args.max_frames
SETTLE = args.post_ruck_settle

collision_system = CollisionSystem()
field = Field(1920, 1080)
world = WorldState()
ball = Ball(field)
world.ball = ball

open_play = OpenPlayEngine(world, seed=42)
world.open_play = open_play
world.open_play_engine = open_play

kickoff_engine = KickoffEngine(world, ball, field)
reactivity_agent = DefenceReactivityAgent(world)


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

seen_tackle_init = False
first_tackle_frame = None
post_ruck_start_frame = None
post_ruck_frames = 0
completed = False
phase_sequence = []
_last_seq = None

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    for _ in range(MAX_FRAMES):
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
            reactivity_agent.update()
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            phase_now = open_play._phase
            sub_now = getattr(world, "sub_state", None)
            sub_label = sub_now.value if hasattr(sub_now, "value") else sub_now
            tagged = f"{phase_now}/{sub_label}"
            if tagged != _last_seq:
                _last_seq = tagged
                phase_sequence.append((world.frame_count, tagged))

            if phase_now == "TACKLE_INIT" and first_tackle_frame is None:
                seen_tackle_init = True
                first_tackle_frame = world.frame_count

            if seen_tackle_init and phase_now == "OPEN_PLAY":
                if post_ruck_start_frame is None:
                    post_ruck_start_frame = world.frame_count
                post_ruck_frames += 1

            if post_ruck_start_frame is not None and post_ruck_frames >= SETTLE:
                completed = True
                break

        else:
            if hasattr(world, "open_play") and world.open_play is not None:
                world.open_play.update(DT)

output = buf.getvalue()

with open("tmp/verify_full_capture.txt", "w", encoding="utf-8") as fh:
    fh.write(output)

if not completed:
    print(f"[VERIFY] FAIL: no full cycle + {SETTLE} post-ruck OPEN_PLAY frames "
          f"within {MAX_FRAMES} frames (last ope={open_play._phase}, "
          f"frames={world.frame_count})")
    sys.exit(1)

KEYS = (
    "LATE_DEPTH_SWITCH", "STATIC_TARGET_AFTER_LINE_BREAK",
    "TIMING_LOCK_POST_RUCK", "BAD_DECISION_ON_LINE_BREAK",
    "TARGET_JIGGLE", "STUCK_NOT_FROZEN", "STUCK_SOFT_FREEZE",
    "VX_ZERO_JIGGLE", "UNEXPECTED_FREEZE",
    "DEFENDER_FROZEN_OPEN_PLAY", "MARKER_FROZEN_OPEN_PLAY",
    "DUAL_WRITER", "GLOBAL_MOVEMENT_LOCK", "DEFENCE_GATE_VIOLATION",
)

pre_counts = {k: 0 for k in KEYS}
post_counts = {k: 0 for k in KEYS}

header_re = re.compile(r"\[FREEZE_AUDIT @ frame (\d+)\]")
matches = list(header_re.finditer(output))
for mi, m in enumerate(matches):
    frame = int(m.group(1))
    start = m.start()
    end = matches[mi + 1].start() if mi + 1 < len(matches) else len(output)
    block = output[start:end]

    if frame < first_tackle_frame:
        bucket = pre_counts
    elif post_ruck_start_frame is not None and frame >= post_ruck_start_frame:
        bucket = post_counts
    else:
        continue

    for key in KEYS:
        bucket[key] += block.count(key)

print("=" * 60)
print(f"[VERIFY] frames_total={world.frame_count}  "
      f"first_tackle={first_tackle_frame}  "
      f"post_ruck_start={post_ruck_start_frame}")
print("[VERIFY] phase/sub-state sequence:")
for fr, tag in phase_sequence:
    print(f"    frame {fr:5d}  {tag}")
print("=" * 60)
print("[VERIFY] pre-tackle-1 window (kick-return approach):")
for k in KEYS:
    suffix = "  <-- VIOLATION" if pre_counts[k] > 0 else ""
    print(f"    {k}: {pre_counts[k]}{suffix}")
print("=" * 60)
print("[VERIFY] post-ruck window (post-ruck OPEN_PLAY):")
for k in KEYS:
    suffix = "  <-- VIOLATION" if post_counts[k] > 0 else ""
    print(f"    {k}: {post_counts[k]}{suffix}")
print("=" * 60)

failures = []
for k in KEYS:
    if post_counts[k] > 0:
        failures.append(f"post-ruck {k} count={post_counts[k]}")
if pre_counts["TARGET_JIGGLE"] > 0:
    failures.append(f"pre-tackle-1 TARGET_JIGGLE count={pre_counts['TARGET_JIGGLE']}")

if failures:
    print("[VERIFY] FAILED:")
    for f in failures:
        print(f"    - {f}")
    print("[VERIFY] full capture saved to tmp/verify_full_capture.txt")
    sys.exit(1)

print("[VERIFY] PASS: pre-tackle-1 and post-ruck OPEN_PLAY both quiet")
print("[VERIFY] (pre TARGET_JIGGLE=0; post LATE_DEPTH_SWITCH=0, "
      "TIMING_LOCK_POST_RUCK=0, TARGET_JIGGLE=0, STUCK=0)")
sys.exit(0)
