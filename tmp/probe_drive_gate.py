"""DRIVE-phase break-gate probe (2026-08-22).

Boots the engine headlessly like run.py / verify_tes_drive_fix.py, then wraps:

  * OpenPlayEngine._check_break_tackle          (raw momentum gate)
  * OpenPlayEngine._check_broken_tackle_scoring (WEAK + drive>1.5 gate)
  * OpenPlayEngine._handle_broken_tackle        (break exit -> OPEN_PLAY)
  * OpenPlayEngine._set_phase                   (transition timeline)

Logs per-DRIVE-frame gate inputs (drive_dist, dur, derived bc_speed,
bc_momentum, def_resistance, tackle_result) for the first few tackles,
aggregates counts across a seed sweep, and reports whether RELEASE is
ever reached or the FSM ping-pongs DRIVE->OPEN_PLAY->DRIVE.

Also dumps open_play_engine.py lines 3242-3467 (_follow_attached_tacklers
through _update_tackle_contact) so tackler velocity handling can be read
alongside the gate data.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import math
import pygame

NL = chr(10)

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

# -- Dump the follow/attach + contact regions for offline reading --
_src = open(r"c:/Users/User/Desktop/RLM/world/open_play_engine.py",
            encoding="utf-8").read().splitlines()
with open(r"c:/Users/User/Desktop/RLM/tmp/follow_contact_region.txt", "w",
          encoding="utf-8") as f:
    for i in range(3242, min(3468, len(_src) + 1)):
        f.write("%5d  %s" % (i, _src[i - 1]) + NL)

# -- Instrumentation -------------------------------------------------
LOG = []
STATS = {
    "tackles_started": 0,
    "broken_tackles": 0,
    "release_reached": 0,
    "dominance_reached": 0,
    "max_consec_drive": 0,
    "frames_in_drive": 0,
}
_cur = {"consec_drive": 0}
_drive_logs_left = [90]   # per-frame gate logs budget


_orig_check_raw = OpenPlayEngine._check_break_tackle
_orig_check_score = OpenPlayEngine._check_broken_tackle_scoring
_orig_handle_break = OpenPlayEngine._handle_broken_tackle
_orig_set_phase = OpenPlayEngine._set_phase


def _log(line):
    LOG.append(line)


def wrapped_check_raw(self, dt):
    fired = _orig_check_raw(self, dt)
    ctx = self.tes_ctx
    if ctx is not None and self._phase == "TACKLE_DRIVE":
        STATS["frames_in_drive"] += 1
        _cur["consec_drive"] += 1
        if _cur["consec_drive"] > STATS["max_consec_drive"]:
            STATS["max_consec_drive"] = _cur["consec_drive"]
        if _drive_logs_left[0] > 0:
            _drive_logs_left[0] -= 1
            dtx = getattr(ctx, "drive_target_x", 0.0)
            dsx = getattr(ctx, "drive_start_x", 0.0)
            drive_dist = abs(dtx - dsx) if (dtx and dsx) else 0.0
            dur = max(0.1, self.tes_outcome.tackle_duration_s
                      if self.tes_outcome else 1.0)
            bc = self.ball_carrier
            bc_speed_derived = drive_dist / dur
            bc_speed_real = math.hypot(bc.vx, bc.vy) if bc else 0.0
            bc_mass = getattr(bc, "mass_kg", 90.0) if bc else 90.0
            bc_mom = bc_mass * bc_speed_derived
            def_mom = ctx.engagement.total_momentum()
            res = def_mom if def_mom > 0 else bc_mom * 0.5
            res_name = "engagement" if def_mom > 0 else "FALLBACK(0.5x)"
            _log("DRIVE f=%d dist=%.2f dur=%.2f spd_derived=%.2f "
                 "spd_real=%.2f bc_mom=%.1f def_res=%.1f(%s) "
                 "result=%s raw_fire=%s"
                 % (self.world.frame_count, drive_dist, dur,
                    bc_speed_derived, bc_speed_real, bc_mom, res,
                    res_name,
                    getattr(self.tes_outcome, "tackle_result", None),
                    fired))
    return fired


def wrapped_check_score(self, ctx, outcome):
    fired = _orig_check_score(self, ctx, outcome)
    if fired and _drive_logs_left[0] > -30:
        _drive_logs_left[0] -= 1
        _log("SCORE_BREAK f=%d result=%s drive_m=%.3f thr=1.5"
             % (self.world.frame_count,
                getattr(outcome, "tackle_result", None),
                getattr(outcome, "drive_distance_m", 0.0)))
    return fired


def wrapped_handle_break(self):
    STATS["broken_tackles"] += 1
    _log("BROKEN_TACKLE f=%d (exit to OPEN_PLAY, no ruck)"
         % self.world.frame_count)
    return _orig_handle_break(self)


def wrapped_set_phase(self, phase, sub_state, reason):
    prev = self._phase
    if prev != phase:
        if phase == "TACKLE_INIT":
            STATS["tackles_started"] += 1
        elif phase == "TACKLE_DOMINANCE":
            STATS["dominance_reached"] += 1
        elif phase == "RELEASE":
            STATS["release_reached"] += 1
        if prev == "TACKLE_DRIVE" and phase != "TACKLE_DOMINANCE":
            _log("DRIVE_EXIT f=%d %s -> %s (%s)"
                 % (self.world.frame_count, prev, phase, reason))
        _cur["consec_drive"] = 0
    return _orig_set_phase(self, phase, sub_state, reason)


OpenPlayEngine._check_break_tackle = wrapped_check_raw
OpenPlayEngine._check_broken_tackle_scoring = wrapped_check_score
OpenPlayEngine._handle_broken_tackle = wrapped_handle_break
OpenPlayEngine._set_phase = wrapped_set_phase


def run_seed(seed, max_frames=2400):
    import random
    random.seed(seed)
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)

    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    collision_system = CollisionSystem()
    dt = 1.0 / 60.0
    for frame in range(1, max_frames + 1):
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
    return world, open_play


seeds = [int(a) for a in sys.argv[1:]] or list(range(1, 9))
per_seed = []
for s in seeds:
    for k in STATS:
        STATS[k] = 0
    _cur["consec_drive"] = 0
    _drive_logs_left[0] = 90
    world, ope = run_seed(s)
    row = dict(STATS)
    row["seed"] = s
    row["final_phase"] = ope._phase
    per_seed.append(row)
    _log("--- seed %d done: final_phase=%s tackles=%d broken=%d "
         "release=%d max_consec_drive=%d"
         % (s, ope._phase, row["tackles_started"], row["broken_tackles"],
            row["release_reached"], row["max_consec_drive"]))

out_path = r"c:/Users/User/Desktop/RLM/tmp/drive_gate_probe_out.txt"
lines_out = []
lines_out.append("SEED SWEEP RESULTS")
lines_out.append("seed  tackles  broken  domin  release  maxConsecDrive  finalPhase")
for r in per_seed:
    lines_out.append("%4d  %6d  %6d  %5d  %7d  %14d  %s"
                     % (r["seed"], r["tackles_started"], r["broken_tackles"],
                        r["dominance_reached"], r["release_reached"],
                        r["max_consec_drive"], r["final_phase"]))
lines_out.append("")
lines_out.append("DETAILED LOG (first frames of each seed)")
lines_out.extend(LOG)
with open(out_path, "w", encoding="utf-8") as f:
    f.write(NL.join(lines_out))

print("wrote", out_path)
print(NL.join(LOG[-40:]))
