"""
tmp/verify_marker_alignment.py -- Headless marker alignment verification.

Boots the full sim chain (kickoff -> catch -> OPEN_PLAY -> tackle -> RELEASE),
then tracks MARKER_A and MARKER_B positions/targets every frame from RELEASE
entry through PTB_PREP / PTB_EXEC.

Engine-spec assertion:
    MARKER_A settles at (ruck_x + 1.0, ruck_y)
    MARKER_B settles at (ruck_x + 2.0, ruck_y)
    both with no Y offset (|y - ruck_y| < 0.25 after settling)

Writes tmp/marker_alignment_summary.txt and prints a PASS/FAIL verdict.
Exit code 0 = PASS, 1 = FAIL.
"""

import os
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
from player.player import DefenderRole  # noqa: E402

MAX_FRAMES = 9000
DT = 1.0 / 60.0
SAMPLES_NEEDED = 240  # enough to span RELEASE -> PTB_EXEC

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

# ---- Boot exactly like run.py / tmp/tes_retreat_capture.py ----
kickoff_engine.reset_for_kickoff()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff_engine.kickoff()

samples = []
tracking = False


def record_sample():
    rx = getattr(world, "ruck_x_m", None)
    ry = getattr(world, "ruck_y_m", None)
    row = {
        "frame": world.frame_count,
        "phase": open_play._phase,
        "timer": round(open_play._phase_timer, 4),
        "ruck_x": round(rx, 4) if rx is not None else None,
        "ruck_y": round(ry, 4) if ry is not None else None,
        "markers": [],
    }
    for d in world.defending_team:
        role = getattr(d, "defender_role", None)
        role_name = role.name if role is not None else "None"
        if role_name in ("MARKER_A", "MARKER_B"):
            tx = getattr(d, "target_x", None)
            ty = getattr(d, "target_y", None)
            row["markers"].append({
                "id": d.player_id,
                "role": role_name,
                "x": round(d.x, 4),
                "y": round(d.y, 4),
                "tx": round(tx, 4) if isinstance(tx, (int, float)) else None,
                "ty": round(ty, 4) if isinstance(ty, (int, float)) else None,
                "frozen": bool(d.frozen),
            })
    samples.append(row)


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

        if open_play._phase == "RELEASE":
            tracking = True
        if tracking:
            record_sample()
            if len(samples) >= SAMPLES_NEEDED:
                break

if not tracking or len(samples) < 5:
    print(f"[FAIL] Never reached RELEASE with markers (samples={len(samples)})")
    sys.exit(1)


def markers_in(sample, role):
    return [m for m in sample["markers"] if m["role"] == role]


# ---- Settled window: last 60 samples that still have marker roles ----
# (markers are cleared to DEF_LINE_READY at POST_RUCK->OPEN_PLAY, so any
# trailing OPEN_PLAY samples have no markers — exclude those).
marker_samples = [s for s in samples if s["markers"]]
settle_window = marker_samples[-60:]
settle_a = [s for s in settle_window if markers_in(s, "MARKER_A")]
settle_b = [s for s in settle_window if markers_in(s, "MARKER_B")]

summ = []
summ.append("===== MARKER ALIGNMENT SUMMARY =====")
summ.append(f"samples={len(samples)}  first_frame={samples[0]['frame']}  "
            f"phases_seen={sorted({s['phase'] for s in samples})}")

verdict_lines = []
if settle_a and settle_b:
    last_a = settle_a[-1]
    last_b = settle_b[-1]
    ma = markers_in(last_a, "MARKER_A")[0]
    mb = markers_in(last_b, "MARKER_B")[0]
    rxa = last_a["ruck_x"]
    rya = last_a["ruck_y"]
    rxb = last_b["ruck_x"]
    ryb = last_b["ruck_y"]

    err_a_x = ma["x"] - (rxa + 1.0)
    err_a_y = ma["y"] - rya
    err_b_x = mb["x"] - (rxb + 2.0)
    err_b_y = mb["y"] - ryb

    summ.append(f"MARKER_A  last id={ma['id']} x={ma['x']} y={ma['y']} "
                f"target=({ma['tx']},{ma['ty']}) ruck=({rxa},{rya}) "
                f"off_x={round(ma['x'] - rxa, 4)} off_y={round(ma['y'] - rya, 4)}")
    summ.append(f"MARKER_A  expected=(ruck+1, ruck_y)=({round(rxa + 1.0, 4)},{round(rya, 4)}) "
                f"err_x={round(err_a_x, 4)} err_y={round(err_a_y, 4)}")
    summ.append(f"MARKER_B  last id={mb['id']} x={mb['x']} y={mb['y']} "
                f"target=({mb['tx']},{mb['ty']}) ruck=({rxb},{ryb}) "
                f"off_x={round(mb['x'] - rxb, 4)} off_y={round(mb['y'] - ryb, 4)}")
    summ.append(f"MARKER_B  expected=(ruck+2, ruck_y)=({round(rxb + 2.0, 4)},{round(ryb, 4)}) "
                f"err_x={round(err_b_x, 4)} err_y={round(err_b_y, 4)}")

    ok_a = abs(err_a_x) < 0.25 and abs(err_a_y) < 0.25
    ok_b = abs(err_b_x) < 0.25 and abs(err_b_y) < 0.25
    verdict_lines.append(
        f"MARKER_A alignment {'PASS' if ok_a else 'FAIL'} "
        f"(x_err={round(err_a_x, 3)}, y_err={round(err_a_y, 3)})"
    )
    verdict_lines.append(
        f"MARKER_B alignment {'PASS' if ok_b else 'FAIL'} "
        f"(x_err={round(err_b_x, 3)}, y_err={round(err_b_y, 3)})"
    )
else:
    verdict_lines.append("FAIL: insufficient MARKER_A/MARKER_B settled samples")

summ.extend(verdict_lines)
verdict = "PASS" if all("PASS" in v for v in verdict_lines) else "FAIL"

# ---- Write summary ----
with open("tmp/marker_alignment_summary.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(summ) + "\n")
    f.write("\n===== SAMPLE WINDOW (every sample) =====\n")
    for s in samples:
        f.write(f"\nframe={s['frame']} phase={s['phase']} timer={s['timer']} "
                f"ruck=({s['ruck_x']},{s['ruck_y']})\n")
        for m in s["markers"]:
            f.write(f"  {m['role']} id={m['id']} x={m['x']} y={m['y']} "
                    f"target=({m['tx']},{m['ty']}) frozen={m['frozen']}\n")

print(f"\n===== MARKER ALIGNMENT VERDICT: {verdict} =====")
for line in summ:
    print(line)
print(f"[WRITTEN] tmp/marker_alignment_summary.txt")
sys.exit(0 if verdict == "PASS" else 1)
