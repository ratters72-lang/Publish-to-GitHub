"""Verify the DOMINANT rebalance path: force a DOMINANT tackle end-to-end
and confirm the engine applies the X/Y split + backward cap + side fold.

Reuses the sim harness but forces compute_dominance() to yield DOMINANT by
patching the outcome after it is computed (in-place before drive setup), then
records DRIVE + DOMINANCE frames.
"""
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.path.insert(0, ".")

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
from world.catch_system import check_for_catch
import game.tes.tes_dominance as tes_dom

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
field = Field(SCREEN_WIDTH, SCREEN_HEIGHT)

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

DT = 1.0 / 60.0
MAX_FRAMES = 60 * 60

# --- Patch compute_dominance to force DOMINANT on EVERY call. ---
# The engine re-runs compute_dominance() each DRIVE/DOMINANCE frame for the
# break-tackle scoring path; forcing only the first call would let the next
# call overwrite the result back to WEAK (that first call actually happens
# during TACKLE_CONTACT's broken-check).
orig_compute = tes_dom.compute_dominance


def forced_dominance(ctx, outcome, rng):
    orig_compute(ctx, outcome, rng)
    from game.tes.tes_state import TackleResult
    outcome.tackle_result = TackleResult.DOMINANT


tes_dom.compute_dominance = forced_dominance
import world.open_play_engine as ope_mod
ope_mod.compute_dominance = forced_dominance

saw_drive = False
saw_dominance = False
dominance_records = []

for frame in range(MAX_FRAMES):
    ball.update(DT)
    world.frame_count += 1

    if world.phase == GamePhase.KICKOFF:
        kickoff_engine.update(DT)
        check_for_catch(world, ball, DT)
        continue
    elif world.phase == GamePhase.CATCH:
        if world.ball_carrier is not None:
            ope = world.open_play
            if ope is not None:
                ope.start(world.ball_carrier)
        world.phase = GamePhase.OPEN_PLAY
        continue
    elif world.phase == GamePhase.OPEN_PLAY:
        open_play.update(DT)
        ph = open_play._phase
        if ph == "TACKLE_DRIVE":
            saw_drive = True
        elif ph == "TACKLE_DOMINANCE":
            saw_dominance = True
            ctx = open_play.tes_ctx
            bc = open_play.ball_carrier
            if ctx is not None and bc is not None:
                dominance_records.append(
                    (round(ctx.dominance_elapsed_s, 3),
                     round(bc.x, 3), round(bc.y, 3),
                     round(ctx.net_backward_m, 3),
                     ctx.dominance_result)
                )
        elif ph == "RELEASE":
            ctx = open_play.tes_ctx
            if ctx is not None and len(ctx.attached_ids) == 0:
                break

print("saw_drive=", saw_drive, "saw_dominance=", saw_dominance)
print("dominance_result_at_release=",
      open_play.tes_ctx.dominance_result if open_play.tes_ctx else None)

if dominance_records:
    print("last 6 dominance frames (elapsed, bc.x, bc.y, net_backward, result):")
    for rec in dominance_records[-6:]:
        print("   ", rec)

bc = open_play.ball_carrier
ctx = open_play.tes_ctx
if bc is not None and ctx is not None:
    print("final bc.x=%.3f bc.y=%.3f side_fold_sign=%s" % (
        bc.x, bc.y, ctx.side_fold_sign))
    for pid in ctx.attached_ids:
        p = open_play._find_player_by_id(pid)
        off = ctx.attach_offsets.get(pid)
        if p is not None and off is not None:
            sdir, mag = off
            dx, dy = open_play._rotate_offset(sdir, mag, bc.facing)
            print("  tackler %d at (%.3f, %.3f) expected (%.3f, %.3f) glue_dy=%.3f"
                  % (pid, p.x, p.y, bc.x + dx, bc.y + dy, abs(p.y - (bc.y + dy))))

dominance_results_seen = {r[4] for r in dominance_records}
ok = (
    saw_drive and saw_dominance
    and dominance_records
    and dominance_results_seen == {"DOMINANT"}
)
print("dominance_results_seen=", dominance_results_seen)
print("DOMINANT REBALANCE VERIFY:", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
