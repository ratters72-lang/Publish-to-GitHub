"""probe_structure_hold_lifecycle.py — PHASE 1c: why does the hold survive?

The release probe showed 99.97% of every "release owed" tackle-state frame
is a body carrying a LIVE TES STRUCTURE HOLD (``_tes_hold_active``).
``tackle_state_gate.update_tackle_state`` skips such a body BEFORE the
release test, so its ``tackle_state_active`` flag cannot clear while the
hold is live -- and the rebuild pool (``_line_defenders``) drops
``tackle_state_active`` bodies.  That is the participation collapse.

This probe instruments the hold's LIFECYCLE (read-only: the engine's own
functions are wrapped at runtime, never modified):

  * every ``apply_tes_drive_structure`` call (who re-establishes holds),
  * every ``clear_tes_drive_structure`` call (chamber exit),
  * every ``open_tes_drive_chamber`` call,
  * every ``sync_tes_drive_chamber`` call (seq advance / stale drop),
  * per frame: the phase label, live hold count, live flag count, chamber
    seq, and whether the engine is inside the drive chamber.

Then it reports, per body, the hold's first/last live frame, how many of
those frames were OUTSIDE the chamber, and how many clear calls landed in
that window -- which distinguishes "clear never called" from "clear called
and ineffective".

Run:  python probe_structure_hold_lifecycle.py
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import sys
from collections import Counter

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
import game.managers.defence_manager as dm

MAX_FRAMES = 4000
OUT_LOG = "tmp_probe_structure_hold_lifecycle.log"

CALLS = []
CURRENT = {"frame": 0, "phase": ""}


def _install_wrappers():
    """Wrap the manager's hold API to count calls (no behaviour change)."""
    orig_apply = dm.apply_tes_drive_structure
    orig_clear = dm.clear_tes_drive_structure
    orig_open = dm.open_tes_drive_chamber
    orig_sync = tds.sync_tes_drive_chamber

    def w_apply(world, defenders, bc, dt, tuners):
        held = orig_apply(world, defenders, bc, dt, tuners)
        CALLS.append((CURRENT["frame"], "apply", CURRENT["phase"],
                      "pool=%d held=%d" % (len(defenders), len(held))))
        return held

    def w_clear(world, tuners=None):
        before = sum(
            1 for p in (getattr(world, "defending_team", None) or [])
            if getattr(p, tds.HOLD_ACTIVE_ATTR, False))
        n = orig_clear(world, tuners)
        CALLS.append((CURRENT["frame"], "clear", CURRENT["phase"],
                      "held_before=%d cleared=%d" % (before, n)))
        return n

    def w_open(world, tuners=None):
        n = orig_open(world, tuners)
        CALLS.append((CURRENT["frame"], "open_chamber", CURRENT["phase"],
                      "invalidated=%d" % n))
        return n

    def w_sync(world, tuners=None):
        n = orig_sync(world, tuners)
        if n:
            CALLS.append((CURRENT["frame"], "sync", CURRENT["phase"],
                          "cleared=%d" % n))
        return n

    dm.apply_tes_drive_structure = w_apply
    dm.clear_tes_drive_structure = w_clear
    dm.open_tes_drive_chamber = w_open
    tds.sync_tes_drive_chamber = w_sync


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


def collect():
    frames = []
    keep = []
    _install_wrappers()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        keep.extend(world.defending_team)
        dt = 1.0 / 60.0
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

            phase = open_play._phase
            CURRENT["frame"] = int(world.frame_count)
            CURRENT["phase"] = phase
            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            in_chamber = tds.is_tes_drive_substate(world)
            holds = []
            flags = []
            for p in world.defending_team:
                if p is None:
                    continue
                pid = getattr(p, "player_id", None)
                if getattr(p, tds.HOLD_ACTIVE_ATTR, False):
                    holds.append(pid)
                if getattr(p, "tackle_state_active", False):
                    flags.append(pid)
            frames.append({
                "frame": int(world.frame_count),
                "phase": phase,
                "in_chamber": in_chamber,
                "cid": getattr(world, tds.CHAMBER_ID_ATTR, None),
                "holds": sorted(holds),
                "flags": sorted(flags),
            })
    with open(OUT_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    return frames


def main():
    frames = collect()
    print("=" * 78)
    print("TES STRUCTURE-HOLD LIFECYCLE PROBE")
    print("=" * 78)
    if not frames:
        print("RESULT: no frames observed.")
        return 2

    print("frames observed: %d" % len(frames))
    print()

    kinds = Counter(c[1] for c in CALLS)
    print("-" * 78)
    print("HOLD-API CALLS")
    print("-" * 78)
    for k, n in kinds.most_common():
        print("  %-14s %6d calls" % (k, n))
    print()
    print("  clear calls by phase:")
    for ph, n in Counter(c[2] for c in CALLS if c[1] == "clear").most_common():
        print("    %-22s %6d" % (ph, n))
    print()
    print("  apply calls by phase:")
    for ph, n in Counter(c[2] for c in CALLS if c[1] == "apply").most_common():
        print("    %-22s %6d" % (ph, n))
    print()
    print("  clear calls that cleared NOTHING: %d" % sum(
        1 for c in CALLS if c[1] == "clear" and "cleared=0" in c[3]))
    print()
    print("  first 30 calls:")
    for c in CALLS[:30]:
        print("    f=%-6d %-13s %-20s %s" % c)

    print()
    print("-" * 78)
    print("HOLDS LIVE OUTSIDE THE DRIVE CHAMBER (the leak)")
    print("-" * 78)
    any_hold = [f for f in frames if f["holds"]]
    outside = [f for f in frames if f["holds"] and not f["in_chamber"]]
    print("  frames with >=1 live hold        : %d" % len(any_hold))
    print("  ... of which OUTSIDE the chamber : %d" % len(outside))
    if outside:
        print("  outside-hold frames by phase:")
        for ph, n in Counter(f["phase"] for f in outside).most_common():
            print("    %-22s %6d" % (ph, n))
        leaked = sorted({pid for f in outside for pid in f["holds"]})
        print("  bodies holding OUTSIDE the chamber: %d %s" % (
            len(leaked), leaked))
        print()
        print("  first 10 outside-hold frames:")
        for f in outside[:10]:
            print("    f=%-6d %-20s holds=%s flags=%s" % (
                f["frame"], f["phase"], f["holds"], f["flags"]))
        print("  last 5 outside-hold frames:")
        for f in outside[-5:]:
            print("    f=%-6d %-20s holds=%s flags=%s" % (
                f["frame"], f["phase"], f["holds"], f["flags"]))

    print()
    print("-" * 78)
    print("PER-BODY HOLD LIFETIME")
    print("-" * 78)
    first_hold = {}
    last_hold = {}
    for f in frames:
        for pid in f["holds"]:
            first_hold.setdefault(pid, f["frame"])
            last_hold[pid] = f["frame"]
    for pid in sorted(first_hold):
        lo, hi = first_hold[pid], last_hold[pid]
        live = sum(1 for f in frames
                   if lo <= f["frame"] <= hi and pid in f["holds"])
        out = sum(1 for f in frames
                  if lo <= f["frame"] <= hi and pid in f["holds"]
                  and not f["in_chamber"])
        clears = sum(1 for c in CALLS if c[1] == "clear" and lo <= c[0] <= hi)
        print("  pid %-5s hold frames %5d..%-6d live=%-5d outside=%-5d "
              "clear_calls_in_span=%d" % (pid, lo, hi, live, out, clears))

    print()
    print("-" * 78)
    print("FLAGGED BODIES WITH NO HOLD (release path reachable)")
    print("-" * 78)
    free = [f for f in frames if f["flags"] and not f["holds"]]
    print("  frames with a flag and NO hold: %d" % len(free))
    for f in free[:10]:
        print("    f=%-6d %-20s flags=%s" % (f["frame"], f["phase"], f["flags"]))

    print()
    print("engine log written to %s" % OUT_LOG)
    return 0


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
