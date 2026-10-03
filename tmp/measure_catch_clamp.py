"""
tmp/measure_catch_clamp.py — Option A instrumentation probe (2026-08-15).

Boots the REAL engine headlessly (exactly like verify_fullback_engine.py),
plays a kickoff -> catch -> OPEN_PLAY sequence, and measures the post-catch
defensive line clamp path WITHOUT modifying game code:

  - when catch_system._perform_catch() arms defended_speed_cap_mps /
    defended_cap_remaining_s on every defender
  - how long the clamp window stays armed (incl. OPE re-arms in PTB era)
  - what the cap value actually is (post_catch_cap_mps) vs the unified
    ceiling (max_defensive_speed_mps)
  - per-defender real speeds vs the cap: is the clamp actually throttling?
  - per-defender stall detection (|v| < 1.0 m/s for > 1s in structured
    OPEN_PLAY after the catch) with the movement_state / depth_class seen
    during the stall
  - the full sub-state timeline after the catch (TACKLE -> RELEASE -> PTB
    -> POST_RUCK -> OPEN_PLAY) so we can see what phase the "2 second"
    window actually spans

Engine stdout during the loop is redirected to tmp/catch_clamp_engine.log
so audit prints cannot pollute the summary.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import math
import io
import contextlib
import random
import traceback

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False
ope_mod.DEBUG_OPE_TRACE = False

from world.field import Field
from world.world_state import WorldState, GameSubState, GamePhase
from world.open_play_engine import OpenPlayEngine
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

random.seed(7)

collision_system = CollisionSystem()
field = Field(1920, 1080)
world = WorldState()
ball = Ball(field)
world.ball = ball

open_play = OpenPlayEngine(world)
world.open_play = open_play
world.open_play_engine = open_play

kickoff_engine = KickoffEngine(world, ball, field)

kickoff_engine.reset_for_kickoff()
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff_engine.kickoff()

tuners = DefenceTuners()

MAX_FRAMES = 2400
dt = 1.0 / 60.0

# ── Analysis state ──────────────────────────────────────────────────
catch_frame = None
clamp_first_frame = {}   # id -> first frame remaining > 0
clamp_last_frame = {}    # id -> last frame remaining > 0
clamp_armed_frames = {}  # id -> count of frames remaining > 0
throttled_frames = {}    # id -> frames where speed stayed near the cap
stall_by_id = {}         # id -> list of (start_frame, end_frame) stalls
_throttle_triggered = {}  # id -> True if |v| within 0.2 of cap while armed

per_frame_min_speed = []   # (frame, min_speed, mean_speed) over line defenders
sub_state_history = []     # (frame, ope_phase, world_sub_state)
sample_rows = []           # sparse per-defender dump after catch

# fast stall detection internals
cur_stall_start = {}


def spd(p):
    return math.hypot(getattr(p, "vx", 0.0), getattr(p, "vy", 0.0))


# ── Frame loop ──────────────────────────────────────────────────────
engine_log_path = os.path.join("tmp", "catch_clamp_engine.log")
os.makedirs("tmp", exist_ok=True)

try:
    with open(engine_log_path, "w", encoding="utf-8") as engine_log, \
         contextlib.redirect_stdout(engine_log):
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

            # ── sampling ──
            if catch_frame is None and getattr(world, "just_caught_this_frame", False):
                # logged on the frame the flag is consumed (first OPEN_PLAY)
                pass
            if catch_frame is None and world.ball_carrier is not None \
                    and world.phase == GamePhase.OPEN_PLAY \
                    and open_play._phase == "OPEN_PLAY":
                catch_frame = frame
                print_to_self = True  # (mirror below outside redirect)

            if catch_frame is not None and frame >= catch_frame:
                # sub-state timeline (record changes only)
                key = (open_play._phase, world.sub_state)
                if not sub_state_history or sub_state_history[-1][1:] != (key[0], str(key[1])):
                    sub_state_history.append((frame, key[0], str(key[1])))

                line = [d for d in world.defending_team
                        if not getattr(d, "fullback_role_active", False)
                        and not getattr(d, "frozen", False)]
                if line:
                    speeds = [spd(d) for d in line]
                    per_frame_min_speed.append(
                        (frame, min(speeds), sum(speeds) / len(speeds))
                    )

                for d in world.defending_team:
                    pid = d.player_id
                    remaining = getattr(d, "defended_cap_remaining_s", 0.0)
                    cap = getattr(d, "defended_speed_cap_mps", 0.0)
                    s = spd(d)

                    if remaining > 0.0:
                        clamp_first_frame.setdefault(pid, frame)
                        clamp_last_frame[pid] = frame
                        clamp_armed_frames[pid] = clamp_armed_frames.get(pid, 0) + 1
                        if cap > 0.0 and s >= cap - 0.2:
                            throttled_frames[pid] = throttled_frames.get(pid, 0) + 1

                    # sample table every 15 frames, first 6 line defenders
                    # (defending team = kicking team, pids 200+)
                    if (frame % 15 == 0
                            and not getattr(d, "fullback_role_active", False)
                            and pid <= 212):
                        ms = getattr(d, "movement_state", None)
                        ms_name = getattr(ms, "name", None) or str(ms)
                        sample_rows.append((
                            frame, pid,
                            round(getattr(d, "x", 0.0), 2),
                            round(getattr(d, "y", 0.0), 2),
                            round(s, 2),
                            round(getattr(d, "vx", 0.0), 2),
                            round(remaining, 2),
                            round(cap, 2),
                            ms_name,
                            getattr(d, "depth_class", "?"),
                            str(open_play._phase),
                        ))

                    # stall detection: non-frozen line defender |v| < 1.0
                    if (not getattr(d, "fullback_role_active", False)
                            and not getattr(d, "frozen", False)
                            and open_play._phase == "OPEN_PLAY"
                            and getattr(world, "use_structured_open_play", True)):
                        if s < 1.0:
                            if cur_stall_start.get(pid) is None:
                                cur_stall_start[pid] = frame
                        else:
                            start = cur_stall_start.get(pid)
                            if start is not None:
                                if frame - start >= 60:  # >= 1.0s
                                    stall_by_id.setdefault(pid, []).append((start, frame - 1))
                                cur_stall_start[pid] = None

    # flush open stalls
    for pid, start in cur_stall_start.items():
        if start is not None and MAX_FRAMES - start >= 60:
            stall_by_id.setdefault(pid, []).append((start, MAX_FRAMES))

except Exception:
    with open(os.path.join("tmp", "catch_clamp_crash.log"), "w", encoding="utf-8") as fh:
        fh.write(traceback.format_exc())
    print("PROBE CRASHED — see tmp/catch_clamp_crash.log")
    print(traceback.format_exc())
    pygame.quit()
    sys.exit(2)


# ── Summary ─────────────────────────────────────────────────────────
print("=" * 100)
print("POST-CATCH CLAMP MEASUREMENT (catch frame %s)" % catch_frame)
print("=" * 100)
print()
print("TUNERS: post_catch_cap_mps=%.1f  max_defensive_speed_mps=%.1f"
      % (tuners.post_catch_cap_mps, tuners.max_defensive_speed_mps))
print("PLAYER: DEFENDED_CAP_HARD_LIMIT_S=3.0  DEFENDED_CAP_BLEND_S=0.5")
print()

# Sub-state / phase timeline after catch
print("--- PHASE / SUB-STATE TIMELINE AFTER CATCH ---")
for frame, ope_phase, sub in sub_state_history:
    print("  frame %5d  ope_phase=%-22s world.sub_state=%s" % (frame, ope_phase, sub))
print()

# Clamp window per defender
print("--- CLAMP WINDOW PER DEFENDER (post-catch) ---")
clamped_any = False
for d in world.defending_team:
    pid = d.player_id
    first = clamp_first_frame.get(pid)
    if first is None:
        continue
    clamped_any = True
    armed = clamp_armed_frames.get(pid, 0)
    thr = throttled_frames.get(pid, 0)
    last = clamp_last_frame.get(pid)
    dur_s = (last - first + 1) * dt
    print("  pid %3d  armed %5d -> %5d  (%5.2fs, %4d frames)  |v|>=cap-0.2 on %4d/%-4d frames"
          % (pid, first, last, dur_s, armed, thr, armed))
if not clamped_any:
    print("  (no defender ever saw defended_cap_remaining_s > 0 after catch)")
print()

# Min/mean line speed immediately after catch (does the line go slow?)
print("--- MIN/MEAN LINE SPEED AFTER CATCH (first 150 OPEN_PLAY frames) ---")
for frame_, mn, mean in per_frame_min_speed:
    if frame_ >= catch_frame + 150:
        break
    if (frame_ - catch_frame) % 10 == 0:
        print("  +%3d frame -> min=%.2f m/s  mean=%.2f m/s"
              % (frame_ - catch_frame, mn, mean))
print()

# Stall report
print("--- STALL REPORT (|v| < 1.0 m/s for >= 1.0s in structured OPEN_PLAY) ---")
any_stall = False
for d in world.defending_team:
    pid = d.player_id
    stalls = stall_by_id.get(pid)
    if stalls:
        any_stall = True
        for start, end in stalls:
            print("  pid %3d  stalled frames %5d - %5d  (%.2fs)"
                  % (pid, start, end, (end - start + 1) * dt))
if not any_stall:
    print("  (no defender stalled for >= 1.0s in structured OPEN_PLAY)")
print()

# Sparse sample table
print("--- SPARSE SAMPLE (pid, x, y, |v|, vx, remaining, cap, state, depth, ope_phase) ---")
for row in sample_rows[:40]:
    print("  f%4d pid%3d x=%7.2f y=%6.2f |v|=%5.2f vx=%6.2f rem=%4.2f cap=%4.1f %-8s %-9s %s"
          % row)
print()

# Verdict
print("=" * 100)
if not clamped_any:
    print("VERDICT: The clamp was NEVER ARMED after the catch — it cannot be the")
    print("         cause of any post-catch stall in this run.")
elif catch_frame is not None:
    max_armed = max(clamp_armed_frames.values()) if clamp_armed_frames else 0
    max_arm_s = max_armed * dt
    print("VERDICT: Clamp armed for up to %.2fs at post_catch_cap_mps=%.1f vs ceiling %.1f."
          % (max_arm_s, tuners.post_catch_cap_mps, tuners.max_defensive_speed_mps))
    print("         Delta cap-vs-ceiling = %.2f m/s — %s."
          % (tuners.max_defensive_speed_mps - tuners.post_catch_cap_mps,
             "tiny, cannot produce a visible ~2s stall"
             if tuners.post_catch_cap_mps >= 7.0 else "large, clamp is a real throttle"))
    if any_stall:
        print("         Stalls WERE detected — check the phase timeline above to see")
        print("         what phase they fall in (likely TACKLE/PTB-era stasis, not the clamp).")
    else:
        print("         No >=1.0s stalls detected in structured OPEN_PLAY after catch.")
print()
print("Engine stdout captured in:", engine_log_path)
print("RESULTS SUMMARY ABOVE")
pygame.quit()
sys.exit(0)
