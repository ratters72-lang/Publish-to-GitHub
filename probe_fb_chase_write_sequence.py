"""probe_fb_chase_write_sequence.py — WHO WRITES THE FB'S COMMAND, IN WHAT ORDER.

THE QUESTION
------------
The carry-crossing probe (``probe_fb_carrier_crossing.py``) samples the frame's
FINAL state and showed something that frame-level sampling cannot explain: on a
live chase the tagged fullback's realised speed collapses from 8.30 m/s to 0.71
and then settles at ~2.0 m/s, while

  * the break verdict stays DEPTH and the occupancy gate stays TRUE,
  * ``target_source`` stays ``fullback_geometry`` and the state stays CHASE,
  * and the printed command stays ~2 m BEHIND the body.

A body commanded to a point behind itself cannot produce forward velocity.  So
the command the body ACTUALLY INTEGRATED that frame was not the command the
final sample shows: at least two writers command the same body in one frame, and
the LOSER of the last-write contest is the one that got integrated.

WHAT IT RECORDS
---------------
A write ledger for the tagged fullback, one row per frame, with a snapshot taken
at each point in the frame where the command can change hands:

    A  after ``fullback_role.assign_fullback_role``      (pre-pin)
    B  after the engine's anchor pin                     (post-pin)
       -- sampled at the next engine call the pin precedes,
          ``DefensiveBehaviourUnified._force_immediate_defence_reaction``
    C  before ``fullback_role.apply_fullback_geometry``  (pre-geometry-authority)
    D  after  ``fullback_role.apply_fullback_geometry``  (post-geometry-authority)
    E  immediately before ``Player.update``              (THE INTEGRATED COMMAND)
    F  immediately after  ``Player.update``              (the resulting velocity)

Each snapshot is ``(target_x, target_y, movement_state, move_speed,
target_source, _target_writer, frozen, tackle_role)``.  Comparing D with E is
what answers the question: if E is not D, a writer ran between the geometry
authority and the integration, and E names the command that actually moved the
body.  F shows the pace that command produced.

METHOD
------
Read-only: every wrapper calls the real function unchanged.  The engine is
booted exactly as ``probe_fb_carrier_crossing`` boots it.

Run:    python probe_fb_chase_write_sequence.py
Report: tmp_fb_writes.txt
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import random
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

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

from game.defence.defence_lvl_1 import fullback_role as fbr
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified,
)
from player.player import Player


# ── CONFIG ───────────────────────────────────────────────────────────
SEED = int(os.environ.get("RLM_FBW_SEED", "20260827"))
WARM_FRAMES = int(os.environ.get("RLM_FBW_WARM", "905"))
DT = 1.0 / 60.0
REPORT_PATH = os.environ.get("RLM_FBW_REPORT", "tmp_fb_writes.txt")

# Only frames in this band are printed (the chase episode around the crossing).
FROM_FRAME = int(os.environ.get("RLM_FBW_FROM", "780"))
TO_FRAME = int(os.environ.get("RLM_FBW_TO", "870"))

# Print EVERY frame in the band rather than only mismatches and round numbers.
PRINT_ALL = os.environ.get("RLM_FBW_ALL", "0") not in ("0", "", "false", "no")

_REPORT = None
_REAL_STDOUT = sys.stdout


class _tee:
    def write(self, s):
        try:
            if _REPORT is not None:
                _REPORT.write(s)
        except Exception:
            pass
        return _REAL_STDOUT.write(s)

    def flush(self):
        try:
            if _REPORT is not None:
                _REPORT.flush()
        except Exception:
            pass
        return _REAL_STDOUT.flush()


def fnum(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def optnum(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def name_of(obj):
    if obj is None:
        return None
    return getattr(obj, "name", None) or str(obj)


def fmt(value, places=2):
    if value is None:
        return "--"
    if isinstance(value, float):
        return ("%%.%df" % places) % value
    return str(value)


def find_fullback(world):
    for d in getattr(world, "defending_team", None) or ():
        if getattr(d, "fullback_role_active", False):
            return d
    return None


def snap(d):
    """The command vector as it stands at this instant of the frame."""
    if d is None:
        return None
    return {
        "tx": optnum(getattr(d, "target_x", None)),
        "ty": optnum(getattr(d, "target_y", None)),
        "mode": name_of(getattr(d, "movement_state", None)),
        "speed": optnum(getattr(d, "move_speed", None)),
        "src": getattr(d, "target_source", None),
        "writer": getattr(d, "_target_writer", None),
        "frozen": bool(getattr(d, "frozen", False)),
        "trole": getattr(d, "tackle_role", None),
    }


def render_snap(tag, s):
    if s is None:
        print("    %-3s (no fullback)" % tag)
        return
    print(
        "    %-3s tx=%-8s ty=%-8s mode=%-7s mspd=%-7s src=%-18s writer=%-18s"
        " frz=%s trole=%s"
        % (
            tag, fmt(s["tx"]), fmt(s["ty"]), fmt(s["mode"]), fmt(s["speed"]),
            fmt(s["src"]), fmt(s["writer"]),
            "T" if s["frozen"] else ".", fmt(s["trole"]),
        )
    )


def main():
    global _REPORT
    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    sys.stdout = _tee()

    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball

    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)

    random.seed(SEED)
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    marks = {}
    frame_log = {}

    # ── the hooks ────────────────────────────────────────────────────
    real_assign = fbr.assign_fullback_role
    real_geom = fbr.apply_fullback_geometry
    real_reaction = DefensiveBehaviourUnified._force_immediate_defence_reaction
    real_update = Player.update

    def assign(*a, **kw):
        res = real_assign(*a, **kw)
        try:
            marks["A"] = snap(find_fullback(world))
        except Exception:
            marks["A"] = None
        return res

    def reaction(self, *a, **kw):
        # Runs immediately AFTER the engine's anchor pin: this is the first
        # point in the frame at which the pin's write is observable.
        try:
            marks["B"] = snap(find_fullback(world))
        except Exception:
            marks["B"] = None
        return real_reaction(self, *a, **kw)

    def geom(world_arg, defender, *a, **kw):
        is_fb = bool(getattr(defender, "fullback_role_active", False))
        if is_fb:
            marks["C"] = snap(defender)
        res = real_geom(world_arg, defender, *a, **kw)
        if is_fb:
            marks["D"] = snap(defender)
        return res

    def update(self, dt, *a, **kw):
        is_fb = bool(getattr(self, "fullback_role_active", False))
        if is_fb:
            marks["E"] = snap(self)
            marks["E_v"] = (
                optnum(getattr(self, "vx", None)),
                optnum(getattr(self, "vy", None)),
            )
            marks["E_pos"] = (
                optnum(getattr(self, "x", None)),
                optnum(getattr(self, "y", None)),
            )
        res = real_update(self, dt, *a, **kw)
        if is_fb:
            marks["F_v"] = (
                optnum(getattr(self, "vx", None)),
                optnum(getattr(self, "vy", None)),
            )
            marks["F_pos"] = (
                optnum(getattr(self, "x", None)),
                optnum(getattr(self, "y", None)),
            )
        return res

    fbr.assign_fullback_role = assign
    fbr.apply_fullback_geometry = geom
    DefensiveBehaviourUnified._force_immediate_defence_reaction = reaction
    Player.update = update

    try:
        for frame in range(1, WARM_FRAMES + 1):
            world.frame_count = frame
            ball.update(DT)
            marks = {}

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
                    players = getattr(world, "players", None) or []
                    if players:
                        collision_system.resolve_all(players, world=world)

            if FROM_FRAME <= frame <= TO_FRAME:
                fb = find_fullback(world)
                bc = getattr(world, "ball_carrier", None)
                row = {
                    "frame": frame,
                    "A": marks.get("A"),
                    "B": marks.get("B"),
                    "C": marks.get("C"),
                    "D": marks.get("D"),
                    "E": marks.get("E"),
                    "E_v": marks.get("E_v"),
                    "F_v": marks.get("F_v"),
                    "E_pos": marks.get("E_pos"),
                    "F_pos": marks.get("F_pos"),
                    "fb": snap(fb),
                    "fb_x": optnum(getattr(fb, "x", None)) if fb else None,
                    "bc_x": optnum(getattr(bc, "x", None)) if bc else None,
                }
                frame_log[frame] = row
    finally:
        fbr.assign_fullback_role = real_assign
        fbr.apply_fullback_geometry = real_geom
        DefensiveBehaviourUnified._force_immediate_defence_reaction = real_reaction
        Player.update = real_update

    # ── report ───────────────────────────────────────────────────────
    print("=" * 118)
    print("FB CHASE WRITE SEQUENCE — the command at each hand-off point in the frame")
    print("=" * 118)
    print("  seed %d, frames %d..%d" % (SEED, FROM_FRAME, TO_FRAME))
    print("  A after assign_fullback_role | B after the engine anchor pin")
    print("  C before apply_fullback_geometry | D after it")
    print("  E THE INTEGRATED COMMAND (immediately before Player.update)")
    print("  F the resulting velocity / position after Player.update")
    print("  INTEGRATED != POST-GEOMETRY means a writer ran between the geometry")
    print("  authority and the integration, and E names the command that moved the")
    print("  body.  A blank 'X' row means that call did not run that frame.")
    print("")

    disagreements = 0
    shown = 0
    for frame in range(FROM_FRAME, TO_FRAME + 1):
        row = frame_log.get(frame)
        if row is None:
            continue
        d, e = row.get("D"), row.get("E")
        mismatch = (
            d is not None and e is not None
            and (d["tx"] != e["tx"] or d["mode"] != e["mode"])
        )
        if mismatch:
            disagreements += 1
        # Print the interesting rows: every frame when PRINT_ALL is set,
        # otherwise only the disagreements plus a short lead-in.
        if not PRINT_ALL:
            if not mismatch and not (frame <= FROM_FRAME + 12 or frame % 10 == 0):
                continue
        shown += 1
        print("-" * 118)
        print(
            "f=%-5s fb_x=%-7s bc_x=%-7s lead=%-7s v_before=(%s,%s)"
            " v_after=(%s,%s)%s"
            % (
                frame, fmt(row.get("fb_x")), fmt(row.get("bc_x")),
                fmt((row.get("fb_x") or 0.0) - (row.get("bc_x") or 0.0)),
                fmt((row.get("E_v") or (None, None))[0]),
                fmt((row.get("E_v") or (None, None))[1]),
                fmt((row.get("F_v") or (None, None))[0]),
                fmt((row.get("F_v") or (None, None))[1]),
                "   <== INTEGRATED != POST-GEOMETRY" if mismatch else "",
            )
        )
        for tag in ("A", "B", "C", "D", "E"):
            render_snap(tag, row.get(tag))
        # The frame's FINAL state, for comparison with E.
        render_snap("FIN", row.get("fb"))

    print("")
    print("=" * 118)
    print("VERDICT")
    print("=" * 118)
    print("  frames with INTEGRATED(E) != POST-GEOMETRY(D): %d" % disagreements)
    if disagreements:
        print("  => the body is integrated with a command ANOTHER writer installed")
        print("     after the geometry authority, every such frame.  The E rows")
        print("     above name that command.")
    else:
        print("  => no writer intervenes between the geometry authority and the")
        print("     integration on the traced frames; the deficit is downstream of")
        print("     the command (physics / clamp / facing), not of the write order.")
    print("")

    try:
        sys.stdout = _REAL_STDOUT
    except Exception:
        pass
    try:
        _REPORT.close()
    except Exception:
        pass
    print("  report written to %s" % REPORT_PATH)


if __name__ == "__main__":
    main()
