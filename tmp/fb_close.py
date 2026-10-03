"""Does the tagged fullback CLOSE on the carrier, and does it ever TACKLE?

Read-only live measurement on the shipped engine.  Boots exactly like
``verify_fullback_recover_live.py`` (seed 20260827, 6000 frames, dt=1/60).

Reports, for the tagged fullback only:
  * how often a genuine break is live from its own anchor,
  * the state it is in and its REAL speed (shared-model velocity),
  * the carrier distance: first / last / min, and whether it closed,
  * whether tackle CANDIDACY was ever granted,
  * every tackle_role it held, and how often TES armed it as a tackler.
"""
import collections
import io
import math
import os

os.environ["SDL_VIDEODRIVER"] = "dummy"

import sys

sys.path.insert(0, ".")

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False
ope_mod.DEBUG_OPE_TRACE = False

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
from game.defence.defence_lvl_1 import fullback_role as fb
from game.defence.defence_lvl_1 import contact_authority as ca

SEED = 20260827
MAX_FRAMES = 6000
DT = 1.0 / 60.0

_REAL_STDOUT = sys.stdout


def _quiet():
    sys.stdout = io.TextIOWrapper(
        open(os.devnull, "wb"), encoding="utf-8", errors="replace"
    )


def _loud():
    sys.stdout = _REAL_STDOUT


def _state_name(actor):
    ms = getattr(actor, "movement_state", None)
    return getattr(ms, "name", None) if ms is not None else None


def _run():
    import random

    random.seed(SEED)

    if not pygame.display.get_init():
        pygame.display.init()
    pygame.display.set_mode((1, 1), pygame.NOFRAME)

    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)

    t = collections.defaultdict(int)
    roles = collections.Counter()
    states = collections.Counter()
    speeds = []
    dists = []
    min_dist = None
    first_dist = None

    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    for frame in range(1, MAX_FRAMES + 1):
        world.frame_count = frame
        ball.update(DT)
        if world.phase == GamePhase.KICKOFF:
            kickoff_engine.update(DT)
            check_for_catch(world, ball, DT)
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                world.open_play.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        elif world.phase == GamePhase.OPEN_PLAY:
            open_play.update(DT)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)
        else:
            continue

        active = [p for p in world.defending_team
                  if getattr(p, "fullback_role_active", False)]
        if len(active) != 1:
            continue
        actor = active[0]
        if getattr(actor, "tes_attached", False):
            continue
        bc = getattr(world, "ball_carrier", None)
        if bc is None:
            continue

        t["active"] += 1
        pid = getattr(actor, "player_id", None)
        states[_state_name(actor)] += 1
        roles[getattr(actor, "tackle_role", None)] += 1
        if getattr(actor, "can_acquire_tackle", False):
            t["candidacy"] += 1
        if getattr(actor, "_fb_emergency_engaged", False):
            t["engaged"] += 1
        if getattr(world, "tes_active", False):
            t["tes"] += 1
        if pid is not None and pid in getattr(world, "tackler_ids", set()):
            t["fb_in_tackler_ids"] += 1
        if pid is not None and getattr(world, "primary_tackler_id", None) == pid:
            t["fb_primary"] += 1

        try:
            depth_break = fb.fullback_depth_break_active(world, actor, bc)
            lateral_break = fb.fullback_lateral_break_active(world, actor, bc)
        except Exception:
            depth_break = lateral_break = False
        lead = bool(depth_break or lateral_break)
        if lead:
            t["lead"] += 1

        spd = math.hypot(float(getattr(actor, "vx", 0.0) or 0.0),
                         float(getattr(actor, "vy", 0.0) or 0.0))
        dist = math.hypot(float(getattr(actor, "x", 0.0)) - float(getattr(bc, "x", 0.0)),
                          float(getattr(actor, "y", 0.0)) - float(getattr(bc, "y", 0.0)))

        # ── THE CONTACT AUTHORITY'S OWN VERDICT FOR THIS FRAME ──
        # This is the measurement that is INDEPENDENT of how the match then
        # evolves: it reads the decider's published decision directly, so it
        # says whether the authority admitted the fullback, not whether the
        # resulting 6000-frame trajectory happened to look good.
        try:
            _radius = ca.contact_radius_m()
        except Exception:
            _radius = 1.5
        if dist < _radius:
            t["fb_in_radius"] += 1

        _dec = getattr(world, "contact_authority_decision", None)
        if _dec is not None and int(getattr(_dec, "frame", -1) or -1) == frame:
            _outcome = getattr(getattr(_dec, "outcome", None), "name", "")
            if _outcome == "CONTACT_FORMED":
                t["ca_formed"] += 1
                if pid is not None and getattr(_dec, "winner_id", None) == pid:
                    t["ca_fb_win"] += 1

        if _state_name(actor) == "CHASE":
            t["chase"] += 1
            speeds.append(spd)
        if lead:
            dists.append(dist)
            if min_dist is None or dist < min_dist:
                min_dist = dist
            if first_dist is None:
                first_dist = dist

    out = dict(t)
    out["role_counter"] = dict(roles)
    out["state_counter"] = dict(states)
    out["chase_speed_mean"] = (sum(speeds) / len(speeds)) if speeds else 0.0
    out["chase_speed_max"] = max(speeds) if speeds else 0.0
    out["lead_dist_first"] = first_dist
    out["lead_dist_last"] = dists[-1] if dists else None
    out["lead_dist_min"] = min_dist
    out["lead_dist_mean"] = (sum(dists) / len(dists)) if dists else 0.0
    out["lead_frames_with_dist"] = len(dists)
    return out


def main():
    _quiet()
    res = _run()
    _loud()

    print("=" * 66)
    print("FULLBACK CLOSURE / TACKLE-ADMISSION PROBE (seed %d, %d frames)"
          % (SEED, MAX_FRAMES))
    print("=" * 66)
    for key in sorted(res):
        print("  %-22s %s" % (key, res[key]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
