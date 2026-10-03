"""probe_tackle_state_release.py — PHASE 1b: is the tackle-state flag stuck?

The line-participation probe showed the rebuild pool collapsing from 12
eligible bodies to 2 across 13 TES chambers, with slot owners staying at
11.  The rebuild pool is ``_line_defenders``, which drops
``tackle_state_active`` holders, so the collapse means a GROWING subset of
the team carries the flag.

A flag is LEGITIMATE while the body sits inside the tackle-state radius of
the live ruck: ``tackle_state_gate.update_tackle_state`` releases a holder
as soon as ``dist > radius + TACKLE_STATE_EXIT_HYSTERESIS_M`` (0.25 m),
independently of freeze / phase / peel refusals.

This probe therefore measures, per flagged body-frame:

  * distance to the live ruck/tackle point,
  * the release bound (radius + hysteresis),
  * whether the distance EXCEEDS the bound (a frame where the release
    test should have fired),
  * the flag AGE in frames since that body was first seen flagged,
  * whether the ruck fields were even readable (a None ruck makes
    ``distance_to_tackle_point`` return None -> the sweep ``continue``s
    and NOTHING can ever be released).

Read-only.  Run:  python probe_tackle_state_release.py
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import math
import sys
from collections import Counter, defaultdict

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
from game.defence.defence_lvl_1.tackle_state_gate import (
    TACKLE_STATE_EXIT_HYSTERESIS_M,
    tackle_state_radius_m,
    distance_to_tackle_point,
    is_registered_tes_tackler,
)
from game.defence.defence_lvl_1 import tes_drive_structure as tds

MAX_FRAMES = 4000
DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
OUT_LOG = "tmp_probe_tackle_state_release.log"


def _role_name(d):
    role = getattr(d, "defender_role", None)
    return getattr(role, "name", None) or str(role)


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
    samples = []          # one row per flagged body-frame
    keep = []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        keep.extend(world.defending_team)
        dt = 1.0 / 60.0
        first_flag = {}       # pid -> frame first seen flagged
        last_clear = {}       # pid -> frame last seen unflagged (after flag)
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

            frame = int(world.frame_count)
            phase = open_play._phase
            radius = tackle_state_radius_m(world)
            bound = radius + TACKLE_STATE_EXIT_HYSTERESIS_M
            ruck_x = getattr(world, "ruck_x_m", None)
            ruck_y = getattr(world, "ruck_y_m", None)
            ruck_ok = (ruck_x is not None) and (ruck_y is not None)

            for p in world.defending_team:
                if p is None:
                    continue
                pid = getattr(p, "player_id", None)
                flagged = bool(getattr(p, "tackle_state_active", False))
                if flagged:
                    first_flag.setdefault(pid, frame)
                    dist = distance_to_tackle_point(p, world)
                    samples.append({
                        "frame": frame,
                        "pid": pid,
                        "phase": phase,
                        "dist": dist,
                        "bound": bound,
                        "radius": radius,
                        "ruck_ok": ruck_ok,
                        "age": frame - first_flag[pid],
                        "role": _role_name(p),
                        "frozen": bool(getattr(p, "frozen", False)),
                        "marker_freeze": float(
                            getattr(p, "marker_freeze_timer", 0.0) or 0.0),
                        "tackler": is_registered_tes_tackler(world, p),
                        "struct_hold": bool(
                            getattr(p, tds.HOLD_ACTIVE_ATTR, False)),
                        "depth_role": getattr(p, "depth_role", None),
                    })
                else:
                    if pid in first_flag:
                        last_clear[pid] = frame
    with open(OUT_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    return samples, first_flag, last_clear


def main():
    samples, first_flag, last_clear = collect()
    print("=" * 78)
    print("TACKLE-STATE RELEASE PROBE")
    print("=" * 78)
    if not samples:
        print("RESULT: no flagged body-frames observed (flag never set).")
        return 2

    total = len(samples)
    print("flagged body-frames observed : %d" % total)
    print("distinct flagged bodies      : %d  %s" % (
        len(first_flag), sorted(first_flag)))
    print("bodies later cleared         : %d  %s" % (
        len(last_clear), sorted(last_clear)))
    never_cleared = sorted(set(first_flag) - set(last_clear))
    print("bodies NEVER cleared after flagging: %d  %s" % (
        len(never_cleared), never_cleared))
    print()

    # ── ruck readability ──
    no_ruck = sum(1 for s in samples if not s["ruck_ok"])
    no_dist = sum(1 for s in samples if s["dist"] is None)
    print("-" * 78)
    print("RUCK READABILITY (a None ruck makes release impossible)")
    print("-" * 78)
    print("  frames with ruck fields None : %d / %d" % (no_ruck, total))
    print("  frames with dist None        : %d / %d" % (no_dist, total))
    print()

    # ── distance vs release bound ──
    print("-" * 78)
    print("DISTANCE TO RUCK vs RELEASE BOUND (should release when dist > bound)")
    print("-" * 78)
    over = [s for s in samples if s["dist"] is not None and s["dist"] > s["bound"]]
    under = [s for s in samples if s["dist"] is not None and s["dist"] <= s["bound"]]
    print("  flagged AND dist > bound (release owed) : %d (%.1f%%)" % (
        len(over), 100.0 * len(over) / total))
    print("  flagged AND dist <= bound (legit hold)  : %d (%.1f%%)" % (
        len(under), 100.0 * len(under) / total))
    if over:
        print()
        print("  release-owed frames by phase:")
        by_phase = Counter(s["phase"] for s in over)
        for ph, n in by_phase.most_common():
            print("    %-22s %6d" % (ph, n))
        print()
        print("  release-owed frames: why might the test not have fired?")
        print("    struct_hold=True : %d" % sum(1 for s in over if s["struct_hold"]))
        print("    frozen=True      : %d" % sum(1 for s in over if s["frozen"]))
        print("    marker_freeze>0  : %d" % sum(1 for s in over if s["marker_freeze"] > 0.0))
        print("    registered tackler: %d" % sum(1 for s in over if s["tackler"]))
        print("    role DEF_RETREAT_10: %d" % sum(
            1 for s in over if s["role"] == "DEF_RETREAT_10"))
        print("    max distance from ruck in release-owed set: %.2f m" % max(
            s["dist"] for s in over))
        print()
        print("  release-owed: worst offenders (per body)")
        worst = defaultdict(lambda: {"n": 0, "maxd": 0.0, "maxage": 0})
        for s in over:
            w = worst[s["pid"]]
            w["n"] += 1
            w["maxd"] = max(w["maxd"], s["dist"])
            w["maxage"] = max(w["maxage"], s["age"])
        for pid, w in sorted(worst.items(), key=lambda kv: -kv[1]["n"])[:15]:
            print("    pid %-5s frames=%-6d max_dist=%6.2f m  max_flag_age=%d" % (
                pid, w["n"], w["maxd"], w["maxage"]))

    # ── flag age ──
    print()
    print("-" * 78)
    print("FLAG AGE (frames since this body was first seen flagged)")
    print("-" * 78)
    ages = [s["age"] for s in samples]
    ages.sort()
    def pct(p):
        if not ages:
            return 0
        return ages[min(len(ages) - 1, int(p * len(ages)))]
    print("  median %d  p90 %d  p99 %d  max %d" % (
        pct(0.5), pct(0.9), pct(0.99), ages[-1]))
    print()
    print("  per-body flag lifetime:")
    per_body = defaultdict(lambda: {"n": 0, "min": 10 ** 9, "max": 0,
                                    "maxd": 0.0, "over": 0})
    for s in samples:
        b = per_body[s["pid"]]
        b["n"] += 1
        b["min"] = min(b["min"], s["age"])
        b["max"] = max(b["max"], s["age"])
        b["maxd"] = max(b["maxd"], s["dist"] or 0.0)
        if s["dist"] is not None and s["dist"] > s["bound"]:
            b["over"] += 1
    for pid, b in sorted(per_body.items(), key=lambda kv: -kv[1]["max"]):
        print("    pid %-5s frames=%-6d age %4d..%-6d max_dist=%6.2f  "
              "release_owed=%-6d cleared=%s" % (
                  pid, b["n"], b["min"], b["max"], b["maxd"], b["over"],
                  "yes" if pid in last_clear else "NO"))

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
