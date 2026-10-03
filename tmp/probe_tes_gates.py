"""tmp/probe_tes_gates.py -- Per-frame TES gate probe.

Boots the full sim chain (kickoff -> catch -> OPEN_PLAY -> first tackle),
runs until the first TES cycle resolves, and logs EVERY TES frame:
  - tackler roles/flags/positions (proving frozen or not)
  - BC momentum vs defender resistance (raw break gate)
  - broken-tackle-scoring gate result
  - TES outcome fields (dominance, drive target, tackle_result)
  - phase transitions

The two break gates are PURE READ-ONLY (no state mutation), so calling
them from the probe is safe and tells us exactly which gate is failing.

Output: tmp/_tes_gate_frames.csv + tmp/_tes_gate_summary.txt
"""

import argparse
import csv
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
os.environ["SDL_VIDEODRIVER"] = "dummy"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import random
random.seed(42)

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

parser = argparse.ArgumentParser()
parser.add_argument("--max-frames", type=int, default=12000)
args = parser.parse_args()

DT = 1.0 / 60.0
MAX_FRAMES = args.max_frames

collision_system = CollisionSystem()
field = Field(1920, 1080)
world = WorldState()
ball = Ball(field)
world.ball = ball

open_play = OpenPlayEngine(world, seed=42)
world.open_play = open_play
world.open_play_engine = open_play

kickoff_engine = KickoffEngine(world, ball, field)

def reset_ball_and_world():
    kickoff_engine.reset_for_kickoff()

reset_ball_and_world()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)

world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff_engine.kickoff()

TES_PHASES = ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")

rows = []
state_changes = []
last_phase = None
seen_tes = False
done_cycle = False

for frame in range(1, MAX_FRAMES + 1):
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

        phase_now = open_play._phase

        if phase_now != last_phase:
            state_changes.append((frame, phase_now, getattr(world, "sub_state", None)))
            last_phase = phase_now

        if phase_now in TES_PHASES:
            seen_tes = True

        # ── Per-frame TES gate capture ──
        if phase_now in TES_PHASES:
            bc = world.ball_carrier
            d = open_play.primary
            joiners = list(open_play.joiners)
            ctx = open_play.tes_ctx
            outcome = open_play.tes_outcome

            # Roles: record all defenders with TACKLER_* roles
            tacklers = {}
            for p in world.defending_team:
                role = getattr(p, "defender_role", None)
                rn = role.name if hasattr(role, "name") else str(role)
                if rn.startswith("TACKLER"):
                    tacklers[p.player_id] = (rn, p)

            bc_spd = math.hypot(bc.vx, bc.vy) if bc is not None else 0.0
            bc_mass = getattr(bc, "mass_kg", 90.0) if bc is not None else 90.0
            bc_mom = bc_mass * bc_spd if bc is not None else 0.0

            def_mom = 0.0
            if ctx is not None:
                def_mom = ctx.engagement.total_momentum()
            def_res = def_mom if def_mom > 0 else bc_mom * 0.5

            # Raw break gate (pure read)
            raw_break = False
            try:
                raw_break = open_play._check_break_tackle(0.0)
            except Exception as e:
                raw_break = f"ERR:{e}"

            # Scoring break gate (pure read)
            score_break = False
            result_name = ""
            if ctx is not None and outcome is not None:
                score_break = open_play._check_broken_tackle_scoring(ctx, outcome)
                result_name = outcome.tackle_result.name if hasattr(outcome.tackle_result, "name") else str(outcome.tackle_result)

            drive_info = {}
            if ctx is not None:
                drive_info = {
                    "drive_start_x": getattr(ctx, "drive_start_x", 0.0),
                    "drive_target_x": getattr(ctx, "drive_target_x", 0.0),
                    "drive_elapsed_s": getattr(ctx, "drive_elapsed_s", None),
                    "stability": round(getattr(ctx, "stability", 1.0), 3),
                    "collapsed": getattr(ctx, "collapsed", False),
                    "joiner_gate_open": getattr(ctx, "joiner_gate_open", False),
                    "engaged": ctx.engaged_count,
                }

            outcome_info = {}
            if outcome is not None:
                outcome_info = {
                    "att_dom": round(outcome.attacker_dominance, 3),
                    "def_dom": round(outcome.defender_dominance, 3),
                    "drive_dist": round(outcome.drive_distance_m, 3),
                    "tackle_result": result_name,
                }

            # Compact per-defender string
            def_dump = []
            for pid, (rn, p) in sorted(tacklers.items()):
                def_dump.append(
                    f"{rn}#{pid} fr={int(p.frozen)} x={p.x:.2f} y={p.y:.2f} "
                    f"vx={p.vx:.2f} vy={p.vy:.2f} c={int(getattr(p,'in_contact',False))} "
                    f"t={int(getattr(p,'tes_locked',False))}"
                )

            primary_dump = ""
            if d is not None:
                primary_dump = (
                    f"PRIMARY#{d.player_id} fr={int(d.frozen)} x={d.x:.2f} y={d.y:.2f} "
                    f"vx={d.vx:.2f} vy={d.vy:.2f}"
                )

            rows.append({
                "frame": frame,
                "phase": phase_now,
                "timer": round(open_play._phase_timer, 3),
                "bc_x": round(bc.x, 2) if bc else "",
                "bc_y": round(bc.y, 2) if bc else "",
                "bc_spd": round(bc_spd, 2),
                "bc_mom": round(bc_mom, 2),
                "def_mom": round(def_mom, 2),
                "def_res": round(def_res, 2),
                "raw_break": raw_break,
                "score_break": score_break,
                "result": result_name,
                "primary": primary_dump,
                "tacklers": "; ".join(def_dump),
                "drive_start_x": drive_info.get("drive_start_x", ""),
                "drive_target_x": drive_info.get("drive_target_x", ""),
                "drive_elapsed": drive_info.get("drive_elapsed_s", ""),
                "stability": drive_info.get("stability", ""),
                "collapsed": drive_info.get("collapsed", ""),
                "joiner_gate": drive_info.get("joiner_gate_open", ""),
                "engaged": drive_info.get("engaged", ""),
                "att_dom": outcome_info.get("att_dom", ""),
                "def_dom": outcome_info.get("def_dom", ""),
                "drive_dist": outcome_info.get("drive_dist", ""),
            })

        # Cycle complete: exited a TES phase back into OPEN_PLAY
        if seen_tes and phase_now == "OPEN_PLAY":
            done_cycle = True
            break
    else:
        if hasattr(world, "open_play") and world.open_play is not None:
            world.open_play.update(DT)

# ── Write outputs ──
csv_path = "tmp/_tes_gate_frames.csv"
with open(csv_path, "w", newline="", encoding="utf-8") as fh:
    if rows:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

summary = ["== TES GATE PROBE =="]
summary.append(f"frames_run={world.frame_count} done_cycle={done_cycle} tes_frames_captured={len(rows)}")
summary.append("")
summary.append("Phase transitions:")
for fr, ph, sub in state_changes:
    summary.append(f"  frame {fr}: {ph} (sub={sub})")
summary.append("")

if rows:
    raw_breaks = [r for r in rows if r["raw_break"] is True]
    score_breaks = [r for r in rows if r["score_break"] is True]
    summary.append(f"raw_break fired on frames: {[r['frame'] for r in raw_breaks]}")
    summary.append(f"score_break fired on frames: {[r['frame'] for r in score_breaks]}")
    summary.append("")
    # Per-phase reduction
    by_phase = {}
    for r in rows:
        by_phase.setdefault(r["phase"], []).append(r)
    for ph, phase_rows in by_phase.items():
        bcs = [r["bc_spd"] for r in phase_rows if isinstance(r["bc_spd"], (int, float))]
        moms = [r["bc_mom"] for r in phase_rows if isinstance(r["bc_mom"], (int, float))]
        defs = [r["def_res"] for r in phase_rows if isinstance(r["def_res"], (int, float))]
        summary.append(
            f"phase={ph}: frames={len(phase_rows)} "
            f"bc_speed_max={max(bcs):.2f} bc_mom_max={max(moms):.2f} "
            f"def_res_max={max(defs):.2f} ratio_max="
            f"{max((m/d if d > 0 else 0) for m, d in zip(moms, defs)):.3f}"
        )
    summary.append("")
    # Last few drive frames detail
    drive_rows = [r for r in rows if r["phase"] == "TACKLE_DRIVE"]
    if drive_rows:
        summary.append("Last 5 TACKLE_DRIVE frames:")
        for r in drive_rows[-5:]:
            summary.append(
                f"  f{r['frame']} timer={r['timer']} drive_start_x={r['drive_start_x']} "
                f"drive_target_x={r['drive_target_x']} elapsed={r['drive_elapsed']} "
                f"bc_x={r['bc_x']} raw_break={r['raw_break']} score={r['score_break']}"
            )
    summary.append("")
    # First 3 CONTACT frames detail
    contact_rows = [r for r in rows if r["phase"] == "TACKLE_CONTACT"]
    if contact_rows:
        summary.append("First 5 TACKLE_CONTACT frames:")
        for r in contact_rows[:5]:
            summary.append(
                f"  f{r['frame']} timer={r['timer']} result={r['result']} "
                f"att_dom={r['att_dom']} def_dom={r['def_dom']} "
                f"raw_break={r['raw_break']} score={r['score_break']} "
                f"bc_spd={r['bc_spd']} def_res={r['def_res']}"
            )
    summary.append("")
    # Tackler frozen flags by phase
    summary.append("Tackler frozen flags (TACKLER_1/2/3) per phase:")
    for ph, phase_rows in by_phase.items():
        frozen_tally = {}
        for r in phase_rows:
            if not r["tacklers"]:
                continue
            for chunk in r["tacklers"].split("; "):
                if "TACKLER" in chunk and "fr=" in chunk:
                    role = chunk.split("#")[0]
                    fr = chunk.split("fr=")[1].split(" ")[0]
                    frozen_tally.setdefault(role, {}).setdefault(fr, 0)
                    frozen_tally[role][fr] += 1
        parts = []
        for role, tally in sorted(frozen_tally.items()):
            parts.append(f"{role}:{dict(tally)}")
        summary.append(f"  {ph}: {', '.join(parts)}")

tmp_path = "tmp/_tes_gate_summary.txt"
with open(tmp_path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(summary))

print("\n".join(summary))
print(f"\n[csv] {csv_path}")
print(f"[summary] {tmp_path}")
sys.exit(0 if done_cycle else 1)
