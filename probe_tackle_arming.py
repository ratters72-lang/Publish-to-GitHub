"""probe_tackle_arming.py — measure whether a tackle ever ARMS across a real
kickoff -> catch -> OPEN_PLAY cycle, with anticipation ON vs OFF.

Run:  python probe_tackle_arming.py > tmp_tackle_arming.txt 2>&1

Answers:
  1. Does a tackle ever start (world.tes_active becomes True)?
  2. On frames where a defender is within the pre-tackle activation radius,
     is it promoted to pre_tackle_state COMMIT ("armed")?  If it reaches
     COMMIT but is_slot_tackle_eligible is False, the slot gate is killing it.
  3. What is the slot gap |bc.y - d.target_y| for that body — is target_y
     displaced by the anticipation lean and/or the commit-lateral offset,
     relative to the 0.3 m TACKLE_SLOT_THRESHOLD_M?
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
from game.defence.defence_lvl_1.slot_arbitration import is_slot_tackle_eligible

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
FPS = 60
MAX_FRAMES = 2000

# The slot threshold that the tackle-arm gate reads (0.3 m).  We want to see
# how often |bc.y - target_y| exceeds it at the arming moment.
SLOT_THRESHOLD_M = 0.3


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

    tes_ever = False           # did a tackle ever start?
    tes_first_frame = -1
    op_frames = 0
    slot_in_frames = 0
    slot_gap_hist = []         # every (gap, kind) sample when close
    commit_frames = 0          # frames a body was pre_tackle_state COMMIT
    commit_but_slot_out = 0    # COMMIT yet is_slot_tackle_eligible False
    arm_events = []            # (frame, phase, id, role, pts, ty, d.y, bc.y, gap, is_slot, ant_shift, ant_authority)
    tackle_formations = []     # frames a body was committed & slot-in (would feed TES)
    suppress_events = []       # radially-close but slot-out (likely suppression)

    # The tuners (for pre_tackle_trigger_radius).
    tuners = getattr(open_play, "def_tuners", None)

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
            op_frames += 1
            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

        if world.tes_active and not tes_ever:
            tes_ever = True
            tes_first_frame = i

        phase = str(getattr(world, "_ope_phase_label", "") or "")
        bc = getattr(world, "ball_carrier", None)
        if bc is None:
            continue

        bc_y = float(getattr(bc, "y", 0.0) or 0.0)
        bc_x = float(getattr(bc, "x", 0.0) or 0.0)

        # find the closest defender (by Euclidean distance)
        closest = None
        closest_d = None
        for d in getattr(world, "defending_team", None) or []:
            if d is None or getattr(d, "fullback_role_active", False):
                continue
            dist = math.hypot(float(d.x) - bc_x, float(d.y) - bc_y)
            if closest_d is None or dist < closest_d:
                closest_d = dist
                closest = d

        if closest is None:
            continue

        ty = float(getattr(closest, "target_y", getattr(closest, "y", 0.0)) or 0.0)
        gap = abs(bc_y - ty)
        gap_live = abs(bc_y - float(closest.y))
        pts = getattr(closest, "pre_tackle_state", None)
        pts_name = getattr(pts, "name", None) if pts is not None else None
        is_slot = is_slot_tackle_eligible(bc, closest, world)
        ant_shift = float(getattr(closest, "anticipation_shift_m", 0.0) or 0.0)
        ant_auth = bool(getattr(closest, "anticipation_authority", False))
        ant_ty = getattr(closest, "anticipation_target_y", None)

        if is_slot:
            slot_in_frames += 1

        # Activation radius breach (the tackle should be arming here).
        act_r = float(getattr(tuners, "pre_tackle_trigger_radius_m", 2.0)) if tuners else 2.0
        if closest_d <= act_r:
            if pts_name == "COMMIT":
                commit_frames += 1
                if not is_slot:
                    commit_but_slot_out += 1
                    if len(suppress_events) < 200:
                        suppress_events.append(
                            (i, phase, closest.player_id, closest.depth_role,
                             pts_name, ty, closest.y, bc_y, gap, ant_shift, ant_auth))
                else:
                    if len(tackle_formations) < 50:
                        tackle_formations.append(
                            (i, phase, closest.player_id, closest.depth_role,
                             pts_name, ty, closest.y, bc_y, gap))
            elif len(arm_events) < 400:
                # Close but not yet COMMIT — what state is it in?
                arm_events.append(
                    (i, phase, closest.player_id, closest.depth_role,
                     pts_name, ty, closest.y, bc_y, gap, is_slot,
                     ant_shift, ant_auth))

        if len(slot_gap_hist) < 6000:
            kind = "live" if gap_live < SLOT_THRESHOLD_M else "target"
            slot_gap_hist.append((i, phase, closest.player_id, closest.depth_role,
                                  pts_name, gap, gap_live, ty, closest.y, bc_y,
                                  ant_shift, ant_auth, kind))

    print(f"\n############ ANTICIPATION = {'ON' if anticipation_enabled else 'OFF'} ############")
    print(f"Open-play frames:               {op_frames}")
    print(f"TACKLE EVER STARTED (tes_active): {tes_ever}   (first frame {tes_first_frame})")
    print(f"Frames closest defender slot-in:  {slot_in_frames}")
    print(f"Closest in activation radius -> COMMIT frames:   {commit_frames}")
    print(f"COMMIT but slot-OUT (suppressed):                {commit_but_slot_out}")
    print(f"COMMIT and slot-IN (would feed TES):             {len(tackle_formations)}")

    print(f"\n-- SUPPRESSED COMMIT events (CAM) --")
    for ev in suppress_events[:20]:
        print(f"  f={ev[0]} ph={ev[1]} id={ev[2]} role={ev[3]} pts={ev[4]} "
              f"ty={ev[5]:.2f} y={ev[6]:.2f} bc.y={ev[7]:.2f} gap={ev[8]:.2f} "
              f"ant_shift={ev[9]:.3f} ant_auth={ev[10]}")
    if len(suppress_events) > 20:
        print(f"  ... ({len(suppress_events) - 20} more)")

    print(f"\n-- CLOSE-NOT-COMMIT samples (all in activation radius) --")
    for ev in arm_events[:25]:
        print(f"  f={ev[0]} ph={ev[1]} id={ev[2]} role={ev[3]} pts={ev[4]} "
              f"ty={ev[5]:.2f} y={ev[6]:.2f} bc.y={ev[7]:.2f} gap={ev[8]:.2f} "
              f"slot={ev[9]} ant_shift={ev[10]:.3f} ant_auth={ev[11]}")
    if len(arm_events) > 25:
        print(f"  ... ({len(arm_events) - 25} more)")

    # Aggregate the slot-gap histogram: how often is target_y displaced
    # beyond the slot threshold while the body is about to arm?
    if slot_gap_hist:
        target_gap_gt_slot = sum(1 for h in slot_gap_hist if h[5] >= SLOT_THRESHOLD_M)
        live_gap_gt_slot = sum(1 for h in slot_gap_hist if h[6] >= SLOT_THRESHOLD_M)
        ant_auth_samples = sum(1 for h in slot_gap_hist if h[11])
        max_target_gap = max((h[5] for h in slot_gap_hist), default=0.0)
        max_live_gap = max((h[6] for h in slot_gap_hist), default=0.0)
        print(f"\n-- SLOT-GAP HISTOGRAM (closest defender, sampled) --")
        print(f"  samples={len(slot_gap_hist)}")
        print(f"  |bc.y - target_y| >= 0.3: {target_gap_gt_slot}  (max {max_target_gap:.2f})")
        print(f"  |bc.y - live y|    >= 0.3: {live_gap_gt_slot}  (max {max_live_gap:.2f})")
        print(f"  samples with anticipation_authority=True: {ant_auth_samples}")
        print(f"  max anticipation_shift_m seen: {max((h[10] for h in slot_gap_hist), default=0.0):.3f}")

    return tes_ever, tes_first_frame, commit_frames, commit_but_slot_out, len(tackle_formations)


if __name__ == "__main__":
    on_res = run_one(True, "ON")
    off_res = run_one(False, "OFF")

    print("\n\n================= ANTICIPATION ON vs OFF =================")
    print(f"  TACKLE EVER STARTED:  ON={on_res[0]}  OFF={off_res[0]}")
    print(f"  First TES frame:      ON={on_res[1]}  OFF={off_res[1]}")
    print(f"  COMMIT frames:        ON={on_res[2]}  OFF={off_res[2]}")
    print(f"  COMMIT-but-slot-OUT:  ON={on_res[3]}  OFF={off_res[3]}")
    print(f"  COMMIT-and-slot-IN:   ON={on_res[4]}  OFF={off_res[4]}")

    pygame.quit()
