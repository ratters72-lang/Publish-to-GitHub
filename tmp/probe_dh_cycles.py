"""tmp/probe_dh_cycles.py - per-cycle DH approach trace over a long run.

Records EVERY tackle cycle in a 6000-frame run and reports, per cycle:
  * the DH body id at the first TES frame and at RELEASE entry
  * gap@TES-entry and gap@RELEASE-entry to (bc.x - 1.0, bc.y)
  * whether the DH body CHANGED from the previous cycle
  * whether a broken tackle (TES -> OPEN_PLAY -> TES) immediately preceded it
  * the TES frame count and how many TES frames the DH actually moved on

Then it prints the worst cycles by gap@RELEASE, which is the population that
actually holds RELEASE open past the time gate.

Run:  python tmp/probe_dh_cycles.py
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import math
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
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

MAX_FRAMES = 6000
ROLLOUTS = 3
TES_PHASES = ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
              "TACKLE_DOMINANCE")


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


def gap_of(open_play, world):
    dh = open_play.dh
    bc = world.ball_carrier
    if dh is None or bc is None:
        return None
    return math.hypot((bc.x - ope_mod.DH_BEHIND_DISTANCE_M) - dh.x,
                      bc.y - dh.y)


def run_rollout():
    cycles = []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        dt = 1.0 / 60.0
        prev_phase = None
        cur = None
        prev_dh_id = None
        prev_dh_id_final = None
        saw_broken = False
        pending_broken = False
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

            phase = open_play._phase
            dh = open_play.dh
            dh_id = getattr(dh, "player_id", None)

            # BROKEN TACKLE: TES -> OPEN_PLAY without a ruck.
            if (prev_phase in TES_PHASES and phase == "OPEN_PLAY"):
                pending_broken = True

            if phase in TES_PHASES:
                if cur is None:
                    cur = {
                        "broken_before": pending_broken,
                        "prev_dh_id": prev_dh_id,
                        "dh_id_entry": dh_id,
                        "bc_entry": (
                            (world.ball_carrier.x, world.ball_carrier.y)
                            if world.ball_carrier is not None else None),
                        "gap_entry": gap_of(open_play, world),
                        "tes_frames": 0,
                        "moved": 0,
                        "frozen": 0,
                        "speeds": set(),
                        "last_x": None,
                        "x_entry": dh.x if dh is not None else None,
                    }
                    pending_broken = False
                    saw_broken = False
                cur["tes_frames"] += 1
                if dh is not None:
                    cur["speeds"].add(round(
                        float(getattr(dh, "move_speed", 0.0) or 0.0), 2))
                    if getattr(dh, "frozen", False):
                        cur["frozen"] += 1
                    if cur["last_x"] is not None and abs(dh.x - cur["last_x"]) > 1e-4:
                        cur["moved"] += 1
                    cur["last_x"] = dh.x
            elif phase == "RELEASE" and cur is not None and "gap_release" not in cur:
                cur["dh_id_release"] = dh_id
                cur["gap_release"] = gap_of(open_play, world)
                cur["x_release"] = dh.x if dh is not None else None
                cur["bc_release"] = (
                    (world.ball_carrier.x, world.ball_carrier.y)
                    if world.ball_carrier is not None else None)
            elif phase not in TES_PHASES and phase != "RELEASE" and cur is not None:
                if "gap_release" not in cur:
                    cur["dh_id_release"] = None
                    cur["gap_release"] = None
                    cur["x_release"] = None
                cur["speeds"] = sorted(cur["speeds"])
                cycles.append(cur)
                prev_dh_id = cur.get("dh_id_release") or cur["dh_id_entry"]
                cur = None
                pending_broken = False

            prev_phase = phase
    return cycles


def main():
    all_cycles = []
    for idx in range(ROLLOUTS):
        cs = run_rollout()
        for c in cs:
            c["rollout"] = idx
        print("rollout %d: %d cycles" % (idx, len(cs)))
        all_cycles.extend(cs)

    print()
    print("=" * 108)
    print("ALL CYCLES  (%d total)" % len(all_cycles))
    print("=" * 108)
    with_gap = [c for c in all_cycles if c.get("gap_release") is not None]
    print("cycles reaching RELEASE with a DH : %d / %d" % (
        len(with_gap), len(all_cycles)))
    if not with_gap:
        return 2
    gaps = sorted(c["gap_release"] for c in with_gap)
    print("gap@RELEASE  min/median/p90/max  : %.2f / %.2f / %.2f / %.2f m" % (
        gaps[0], gaps[len(gaps) // 2], gaps[int(0.9 * len(gaps))], gaps[-1]))
    entries = sorted(c["gap_entry"] for c in with_gap
                     if c.get("gap_entry") is not None)
    print("gap@TES-entry min/median/p90/max : %.2f / %.2f / %.2f / %.2f m" % (
        entries[0], entries[len(entries) // 2],
        entries[int(0.9 * len(entries))], entries[-1]))
    print("cycles with gap@RELEASE > 5 m     : %d" % sum(
        1 for c in with_gap if c["gap_release"] > 5.0))
    print("cycles with gap@TES-entry > 12 m  : %d" % sum(
        1 for c in with_gap
        if c.get("gap_entry") is not None and c["gap_entry"] > 12.0))
    print()

    print("-" * 108)
    print("WORST 10 CYCLES BY gap@RELEASE")
    print("-" * 108)
    print("roll cyc  gap_entry  gap_rel  TESfrm moved froz  dh_entry "
          "dh_rel  prev_dh  broken  x_entry  x_rel  speeds")
    for c in sorted(with_gap, key=lambda z: -z["gap_release"])[:10]:
        print("%4d %3d %10s %8.2f %6d %5d %4d %8s %7s %8s %7s %8s %7s" % (
            c["rollout"], all_cycles.index(c),
            ("%.2f" % c["gap_entry"]) if c.get("gap_entry") is not None else "-",
            c["gap_release"], c["tes_frames"], c["moved"], c["frozen"],
            str(c.get("dh_id_entry")), str(c.get("dh_id_release")),
            str(c.get("prev_dh_id")), str(c.get("broken_before")),
            ("%.1f" % c["x_entry"]) if c.get("x_entry") is not None else "-",
            ("%.1f" % c["x_release"]) if c.get("x_release") is not None else "-"))
        print("        bc@entry=%s bc@release=%s speeds=%s" % (
            c.get("bc_entry"), c.get("bc_release"), c.get("speeds")))
    print()
    print("-" * 108)
    print("CYCLES THAT CHANGED DH BODY MID-WINDOW (entry id != release id)")
    print("-" * 108)
    changed = [c for c in with_gap
               if c.get("dh_id_release") is not None
               and c.get("dh_id_entry") is not None
               and c["dh_id_entry"] != c["dh_id_release"]]
    print("count : %d" % len(changed))
    for c in changed[:10]:
        print("  rollout %d entry=%s release=%s gap_entry=%s gap_rel=%.2f" % (
            c["rollout"], c["dh_id_entry"], c["dh_id_release"],
            c.get("gap_entry"), c["gap_release"]))
    print()
    print("-" * 108)
    print("CYCLES PRECEDED BY A BROKEN TACKLE")
    print("-" * 108)
    broken = [c for c in with_gap if c.get("broken_before")]
    print("count : %d / %d" % (len(broken), len(with_gap)))
    for c in broken[:10]:
        print("  rollout %d gap_entry=%s gap_rel=%s prev_dh=%s entry_dh=%s" % (
            c["rollout"], c.get("gap_entry"),
            ("%.2f" % c["gap_release"]), c.get("prev_dh_id"),
            c.get("dh_id_entry")))
    print()
    return 0


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
