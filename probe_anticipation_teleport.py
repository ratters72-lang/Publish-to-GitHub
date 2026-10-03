"""probe_anticipation_teleport.py — measure per-defender single-frame deltas with
anticipation ON vs OFF across a real kickoff->tackle->PTB cycle.

Run:  python probe_anticipation_teleport.py > tmp_anticipation_teleport.txt 2>&1

Answers two questions:
  1. Does anticipation_enabled change the magnitude / frequency of large
     (teleport-scale: > 2.0 m) single-frame defender moves?
  2. On the jump frames, which target_source wrote, and at what phase?
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import sys
import math

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

import pygame
pygame.init()
pygame.display.set_mode((1, 1))

from world.field import Field
from world.world_state import WorldState, GameSubState, GamePhase
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
FPS = 60
MAX_FRAMES = 1500
TELEPORT_THRESHOLD_M = 2.0   # "teleport-scale" single-frame jump


def make_engine(anticipation_enabled):
    field = Field(SCREEN_WIDTH, SCREEN_HEIGHT)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)
    collision_system = CollisionSystem()

    # Toggle anticipation on the defence tuners (the value apply_anticipation
    # reads via getattr(tuners, "anticipation_enabled", True)).
    dt_ = getattr(open_play, "def_tuners", None)
    if dt_ is not None:
        dt_.anticipation_enabled = bool(anticipation_enabled)

    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()
    return world, open_play, kickoff_engine, collision_system, ball


def run_one(anticipation_enabled, label):
    world, open_play, kickoff_engine, collision_system, ball = make_engine(
        anticipation_enabled,
    )
    dt = 1.0 / FPS
    stats = {}
    phase_counts = {}
    y_div = []            # (frame, phase, id, role, depth_class, source, |ty - fsy|)
    teleport_events = []  # (frame, phase, id, axis, delta, source)

    # Seed the tracker on the CURRENT defender set (before any movement).
    def _seed(d):
        st = stats.get(id(d))
        if st is None:
            stats[id(d)] = {
                "id": d.player_id,
                "max_d": 0.0,
                "axis": "",
                "frame": -1,
                "src": "",
                "phase": "",
                "prev_x": d.x,
                "prev_y": d.y,
                "teleports": 0,
            }
        return stats[id(d)]

    for d in world.defending_team:
        _seed(d)

    for i in range(MAX_FRAMES):
        ball.update(dt)

        if world.phase == GamePhase.KICKOFF:
            kickoff_engine.update(dt)
            check_for_catch(world, ball, dt)
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                ope = world.open_play
                if ope is not None:
                    ope.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        elif world.phase == GamePhase.OPEN_PLAY:
            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

        phase = str(getattr(world, "_ope_phase_label", "") or "")
        phase_counts[phase] = phase_counts.get(phase, 0) + 1

        for d in world.defending_team:
            st = _seed(d)
            dx = abs(d.x - st["prev_x"])
            dy = abs(d.y - st["prev_y"])

            # Track max per-frame delta (teleport proxy).
            if max(dx, dy) > st["max_d"]:
                st["max_d"] = max(dx, dy)
                st["axis"] = "X" if dx >= dy else "Y"
                st["frame"] = i
                st["src"] = getattr(d, "target_source", "")
                st["phase"] = phase

            # Track teleport-scale events (any axis > threshold).
            if max(dx, dy) > TELEPORT_THRESHOLD_M:
                st["teleports"] += 1
                if len(teleport_events) < 200:
                    teleport_events.append(
                        (i, phase, d.player_id,
                         "X" if dx >= dy else "Y", max(dx, dy),
                         getattr(d, "target_source", ""))
                    )

            st["prev_x"] = d.x
            st["prev_y"] = d.y

        # Divergence: final target_y vs the spacing authority (frame_spacing_y).
        for d in world.defending_team:
            fsy = getattr(d, "frame_spacing_y", None)
            if fsy is None or getattr(d, "fullback_role_active", False):
                continue
            ty = getattr(d, "target_y", None)
            if ty is None:
                continue
            y_div.append(
                (i, phase, d.player_id, getattr(d, "depth_role", None),
                 getattr(d, "depth_class", None),
                 getattr(d, "target_source", ""), abs(ty - float(fsy)))
            )

    print(f"\n############ ANTICIPATION = {'ON' if anticipation_enabled else 'OFF'} ############")
    print(f"-- PHASE COUNTS --")
    for k in sorted(phase_counts):
        print(f"  {k}: {phase_counts[k]}")

    print(f"\n-- TELEPORT-SCALE EVENTS (> {TELEPORT_THRESHOLD_M:.1f} m single frame) [{len(teleport_events)}] --")
    for ev in teleport_events[:30]:
        print(f"  frame={ev[0]} phase={ev[1]} id={ev[2]} axis={ev[3]} delta={ev[4]:.2f}m src={ev[5]}")
    if len(teleport_events) > 30:
        print(f"  ... ({len(teleport_events) - 30} more)")

    print(f"\n-- TOP PER-DEFENDER MAX SINGLE-FRAME DELTA --")
    top = sorted(stats.values(), key=lambda s: -s["max_d"])[:10]
    for s in top:
        print(f"  id={s['id']} max_d={s['max_d']:.2f}m axis={s['axis']} frame={s['frame']} "
              f"phase={s['phase']} src={s['src']} teleports={s['teleports']}")

    print(f"\n-- TARGET_Y vs FRAME_SPACING_Y DIVERGENCE (does movement override anticipation?) --")
    if y_div:
        max_ydiv = max(y_div, key=lambda t: t[6])
        avg_ydiv = sum(t[6] for t in y_div) / len(y_div)
        big = [t for t in y_div if t[6] > 1.0]
        print(f"  samples={len(y_div)} avg={avg_ydiv:.3f}m max={max_ydiv[6]:.2f}m "
              f"frame={max_ydiv[0]} phase={max_ydiv[1]} id={max_ydiv[2]} "
              f"role={max_ydiv[3]} class={max_ydiv[4]} src={max_ydiv[5]}")
        print(f"  samples with |target_y - frame_spacing_y| > 1.0 m: {len(big)}")
        if big:
            print("  -- biggest divergence sources --")
            src_counts = {}
            for t in big:
                src_counts[t[5]] = src_counts.get(t[5], 0) + 1
            for k, v in sorted(src_counts.items(), key=lambda kv: -kv[1]):
                print(f"    {k}: {v}")

    return stats, teleport_events, y_div, phase_counts


if __name__ == "__main__":
    on_stats, on_tele, on_ydiv, on_phases = run_one(True, "ON")
    off_stats, off_tele, off_ydiv, off_phases = run_one(False, "OFF")

    print("\n\n================= ANTICIPATION ON vs OFF COMPARISON =================")
    on_tele_count = len(on_tele)
    off_tele_count = len(off_tele)
    print(f"Teleport-scale events (> {TELEPORT_THRESHOLD_M:.1f} m):  ON={on_tele_count}  OFF={off_tele_count}")
    on_max = max((s["max_d"] for s in on_stats.values()), default=0.0)
    off_max = max((s["max_d"] for s in off_stats.values()), default=0.0)
    print(f"Largest single-frame delta:   ON={on_max:.2f} m  OFF={off_max:.2f} m")

    # Does the divergence from the spacing authority differ?
    def _divergence_summary(ydiv):
        if not ydiv:
            return 0.0, 0, 0.0
        maxd = max(t[6] for t in ydiv)
        big = sum(1 for t in ydiv if t[6] > 1.0)
        avg = sum(t[6] for t in ydiv) / len(ydiv)
        return maxd, big, avg
    on_maxd, on_big, on_avg = _divergence_summary(on_ydiv)
    off_maxd, off_big, off_avg = _divergence_summary(off_ydiv)
    print(f"target_y vs spacing_y:        ON  max={on_maxd:.2f} big={on_big} avg={on_avg:.3f}")
    print(f"                              OFF max={off_maxd:.2f} big={off_big} avg={off_avg:.3f}")

    pygame.quit()
