"""
tmp/tes_retreat_capture.py -- Headless TACKLE_DRIVE retreat capture.

Boots the full sim chain (kickoff -> catch -> OPEN_PLAY -> first tackle),
then captures 10 consecutive TACKLE_DRIVE frames for every defender:

    id, defender_role, frozen, vx, x, dx, target_x, movement_writer, target_source

plus the FreezeAndMovementAuditAgent._violations observed at the END of each
captured frame (same point the audit itself evaluates).

Usage:
    python tmp/tes_retreat_capture.py [--frames N]

Exit code 0 = reached TACKLE_DRIVE and printed all captures.
Exit code 1 = did not reach TACKLE_DRIVE within the frame cap.
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
os.environ["SDL_VIDEODRIVER"] = "dummy"

# Ensure the project root (RLM) is importable regardless of script location
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import random  # noqa: E402

# Seed global random so the kickoff trajectory is reproducible
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

CAPTURE_FRAMES = 10
MAX_FRAMES = 9000
DT = 1.0 / 60.0

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


# ---- Boot exactly like run.py ----
reset_ball_and_world()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)

world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff()

captures = []
prev_x = {}
drive_frames = 0
reached_drive = False


def record_drive_frame():
    global drive_frames
    row = {
        "frame": world.frame_count,
        "phase_timer": round(open_play._phase_timer, 4),
        "bc_x": round(world.ball_carrier.x, 3) if world.ball_carrier else None,
        "players": [],
        "violations": list(open_play.freeze_audit._violations),
    }
    for d in world.defending_team:
        role = getattr(d, "defender_role", None)
        role_name = role.name if role is not None else "None"
        tx = getattr(d, "target_x", None)
        source = getattr(d, "target_source", None)
        writer = getattr(d, "movement_writer", None)
        entry = {
            "id": d.player_id,
            "role": role_name,
            "frozen": bool(d.frozen),
            "vx": round(d.vx, 4),
            "x": round(d.x, 4),
            "dx": round(d.x - prev_x.get(d.player_id, d.x), 4),
            "target_x": round(tx, 4) if isinstance(tx, (int, float)) else tx,
            "writer": writer,
            "source": source,
        }
        prev_x[d.player_id] = d.x
        row["players"].append(entry)
    captures.append(row)
    drive_frames += 1


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
        if open_play._phase == "TACKLE_DRIVE":
            record_drive_frame()
            if drive_frames >= CAPTURE_FRAMES:
                reached_drive = True
                break

if not reached_drive:
    print(f"[CAPTURE] FAIL: never reached TACKLE_DRIVE (frames={world.frame_count})")
    sys.exit(1)

# ---- Print captures ----
print(f"===== TACKLE_DRIVE CAPTURE: {len(captures)} frames "
      f"(start frame {captures[0]['frame']}) =====")
for row in captures:
    print(f"\n-- frame {row['frame']} phase_timer={row['phase_timer']} "
          f"bc_x={row['bc_x']} --")
    for p in row["players"]:
        print(
            f"  id={p['id']:>3} role={p['role']:<14} frozen={str(p['frozen']):<5} "
            f"vx={p['vx']:>7.4f} x={p['x']:>8.4f} dx={p['dx']:>8.4f} "
            f"target_x={str(p['target_x']):>10} writer={p['writer']} source={p['source']}"
        )
    print(f"  violations={row['violations'] if row['violations'] else '[]'}")

print(f"\n===== FREEZE AUDIT VIOLATIONS ACROSS WINDOW =====")
all_violations = []
for row in captures:
    all_violations.extend(row["violations"])
if all_violations:
    seen = set()
    for v in all_violations:
        if v not in seen:
            print("  " + v)
            seen.add(v)
else:
    print("  (none)")

print(f"\n===== CAPTURE COMPLETE: {len(captures)} drive frames captured =====")

# ---- Write machine-readable summary (bypasses PowerShell redirection issues) ----
summary_path = "tmp/tes_retreat_summary.txt"
with open(summary_path, "w", encoding="utf-8") as f:
    f.write("===== TES_RETREAT POST-FIX SUMMARY =====\n")
    f.write(f"captured_frames={len(captures)}\n")
    all_df = 0
    all_other = 0
    for row in captures:
        viol_def = [v for v in row["violations"] if "STUCK_NOT_FROZEN" in v and
                    any(f"id={d['id']}" in v for d in row["players"] if d["id"] >= 200)]
        viol_other = [v for v in row["violations"] if v not in viol_def]
        all_df += len(viol_def)
        all_other += len(viol_other)
        f.write(f"\nframe={row['frame']} phase_timer={row['phase_timer']} "
                f"bc_x={row['bc_x']} violations_total={len(row['violations'])} "
                f"violations_defenders={len(viol_def)} violations_other={len(viol_other)}\n")
        for p in row["players"]:
            f.write(
                f"  id={p['id']:>4} role={p['role']:<14} frozen={str(p['frozen']):<5} "
                f"vx={p['vx']:>8.4f} x={p['x']:>9.4f} dx={p['dx']:>9.4f} "
                f"target_x={str(p['target_x']):>10} moving={abs(p['dx']) > 1e-9}\n"
            )
    f.write("\n===== SUMMARY COUNTS =====\n")
    f.write(f"STUCK_NOT_FROZEN defenders: {all_df}\n")
    f.write(f"STUCK_NOT_FROZEN other: {all_other}\n")
    retreaters_moved = 0
    retreaters_total = 0
    for row in captures:
        for p in row["players"]:
            if p["role"] == "DEF_RETREAT_10":
                retreaters_total += 1
                if abs(p["dx"]) > 1e-9:
                    retreaters_moved += 1
    f.write(f"DEF_RETREAT_10 rows: {retreaters_total}  moved (>1e-9): {retreaters_moved}\n")
print(f"[CAPTURE] Wrote {summary_path}")
sys.exit(0)
