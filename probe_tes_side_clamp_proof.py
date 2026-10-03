"""probe_tes_side_clamp_proof.py — A/B proof for the TES side-ownership clamp.

Runs the identical TES drive harness TWICE over the same world bootstrap:

  A. LEGACY  — tuners.tes_side_ownership_clamp_enabled = False (default):
     ``assign_split_sides`` takes the ``n_left`` lowest-Y defenders, where
     ``n_left`` comes from the room-derived count authority.  When that count
     exceeds the bodies physically left of the ruck the surplus is dictated
     ACROSS the ruck, so a body standing right of the ruck is stamped
     ``depth_side = LEFT``.

  B. CLAMPED — tuners.tes_side_ownership_clamp_enabled = True: the dictated
     counts are clamped to the bodies physically on each side, so no body is
     ever dictated across the ruck.

For each run it reports, per TES chamber entry and across the chamber:

  S2  bodies whose ``depth_side`` contradicts their own live side
      (sign of ``d.y - ruck_y``) on the entry frame;
  S4  bodies whose ``depth_side`` contradicts their live side at ANY drive
      frame (per-frame mismatch total);
  S5  intra-chamber ``depth_side`` flips.

Exit 0 always (diagnostic).  Numbers are the deliverable.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import random
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame
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
from game.defence.defence_lvl_1 import tes_drive_structure as tds
from game.defence.defence_lvl_1.defensive_split_spacing import LEFT, RIGHT

DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
LINE_ROLES = ("DEF_LINE_READY", "DEF_CHASE", "DEF_RETREAT_10")
MAX_FRAMES = 3000
ENGINE_LOG = "tmp_tes_side_clamp_proof_engine.log"


def _role_name(p):
    role = getattr(p, "defender_role", None)
    return getattr(role, "name", None) or (str(role) if role is not None else None)


def boot_world():
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
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()
    return world, ball, open_play, kickoff_engine, collision_system


def line_bodies(world):
    bc = world.ball_carrier
    return [
        d for d in world.defending_team
        if _role_name(d) in LINE_ROLES
        and not getattr(d, "fullback_role_active", False)
        and d is not bc
    ]


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def run(clamp_on):
    """Return the side-ownership metrics for one canary setting."""
    # DETERMINISM (2026-09-12): the A/B comparison is only meaningful if both
    # runs reach the SAME world.  Both execute in one process, so without an
    # explicit seed the second run inherits whatever global PRNG state the
    # first left behind, and the two diverge for reasons unrelated to any
    # canary (measured: 8 vs 9 chambers, 6 vs 14 entry mismatches, across runs
    # of IDENTICAL engine code).  Seed per run; if the rows still differ, the
    # non-determinism lives somewhere other than the shared ``random`` state.
    random.seed(0)
    entry_mismatch = 0
    frame_mismatch = 0
    flips = 0
    frames = 0
    chambers = 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        tuners = getattr(open_play, "def_tuners", None)
        if tuners is not None:
            tuners.tes_side_ownership_clamp_enabled = bool(clamp_on)
        dt = 1.0 / 60.0
        prev_cid = None
        last_side = {}
        for _ in range(MAX_FRAMES):
            world.frame_count += 1
            ball.update(dt)
            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(dt)
                check_for_catch(world, ball, dt)
                continue
            if world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if world.phase != GamePhase.OPEN_PLAY:
                continue
            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)
            if open_play._phase not in DRIVE_PHASES:
                prev_cid = None
                continue

            frames += 1
            cid = getattr(world, tds.CHAMBER_ID_ATTR, None)
            ruck_y = _f(getattr(world, "ruck_y_m", 35.0))
            bodies = line_bodies(world)
            if cid != prev_cid:
                prev_cid = cid
                chambers += 1
                for d in bodies:
                    side = getattr(d, "depth_side", None)
                    live = LEFT if _f(d.y) < ruck_y else RIGHT
                    if side in (-1, 1) and side != live:
                        entry_mismatch += 1
                    last_side[getattr(d, "player_id", None)] = side
            for d in bodies:
                pid = getattr(d, "player_id", None)
                side = getattr(d, "depth_side", None)
                live = LEFT if _f(d.y) < ruck_y else RIGHT
                prev = last_side.get(pid)
                if side in (-1, 1) and side != live:
                    frame_mismatch += 1
                if side in (-1, 1) and prev in (-1, 1) and side != prev:
                    flips += 1
                last_side[pid] = side
    with open(ENGINE_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    return {
        "chambers": chambers,
        "frames": frames,
        "entry_mismatch": entry_mismatch,
        "frame_mismatch": frame_mismatch,
        "flips": flips,
    }


def main():
    legacy = run(False)
    clamped = run(True)
    print("=" * 78)
    print("TES SIDE-OWNERSHIP CLAMP — A/B PROOF")
    print("=" * 78)
    print(f"{'metric':<44}{'LEGACY':>12}{'CLAMPED':>12}")
    print("-" * 78)
    rows = (
        ("TES chambers observed", "chambers"),
        ("drive frames observed", "frames"),
        ("S2 bodies depth_side != live side AT ENTRY", "entry_mismatch"),
        ("S4 bodies depth_side != live side (any frame)", "frame_mismatch"),
        ("S5 intra-chamber depth_side flips", "flips"),
    )
    for label, key in rows:
        print(f"{label:<44}{legacy[key]:>12}{clamped[key]:>12}")
    print("-" * 78)
    print("RESULT: diagnostic complete.")
    return 0


if __name__ == "__main__":
    code = 0
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
