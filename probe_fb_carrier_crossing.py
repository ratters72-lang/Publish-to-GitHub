"""probe_fb_carrier_crossing.py — WHAT HAPPENS WHEN THE CARRIER PASSES THE FB.

THE QUESTION
------------
Once the ball carrier draws level with and then runs PAST the tagged fullback
(``bc.x`` crosses ``fb.x`` on a live line break), does the fullback keep
pursuing -- or does the pursuing body stall / drop to a fraction of its pace?
And if it does, WHICH gate on the pursuit path flipped on that frame:

    OCCUPANCY      ``fullback_defensive_line_occupied`` went False, so the
                   break verdict was WITHDRAWN mid-chase (the 2026-09-29
                   gate: at least one non-FB defender must sit at or behind
                   ``anchor + FB_LINE_OCCUPIED_BAND_M``, and the fullback
                   itself is never counted).
    LONGITUDINAL   the one-sided ``bc.x > anchor + 1.0`` test flipped because
                   the tackle cycle RELOCKED the anchor forward past the
                   carrier (the anchor is ``ruck_x + 10`` and ``ruck_x`` is
                   rewritten onto the carrier at CONTACT -> DRIVE entry, at
                   the DRIVE-END lock and at RELEASE).
    PIN            the engine's fullback anchor pin ran its ``else`` arm --
                   ``target_source = "fullback"`` with ``MovementState.DRIFT``
                   and ``target_x = anchor_x`` (a STATIC per-ruck constant) --
                   because ``fullback_breakaway_live`` said no.
    LATERAL_FLOOR  the chase class fell back from DEPTH to LATERAL, and
                   ``fullback_chase_point`` then floored ``target_x`` at
                   ``anchor_x`` -- a point that can sit BEHIND the pursuer.
    ARRIVAL        the commanded point landed on the body, so
                   ``apply_movement_state``'s ``dist < 0.05`` branch zeroed the
                   velocity every frame while the state still read CHASE.

METHOD
------
Read-only.  The engine is booted exactly as ``probe_fb_authority_timeline``
boots it and stepped frame by frame.  Every frame is recorded into a rolling
buffer; the CARRIER-CROSSING frames (the sign of ``fb.x - bc.x`` flipping while
a break is live) are noted as they happen, and a window of frames around each
crossing is printed in full at the end.

Nothing is wrapped or monkey-patched except the anchor-reset recorder, so the
frame's printed values are the FINAL applied state of that frame.

Run:    python probe_fb_carrier_crossing.py
Report: tmp_fb_crossing.txt
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import math
import random
import sys
from collections import deque

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
from game.defence.defence_lvl_1 import anchor_authority as aa
from game.defence.defence_lvl_1.fullback_role import (
    fullback_tackle_authority_active,
    fullback_beaten_ownership_loss_active,
)
from game.defence.defence_lvl_1.movement_mode_decision import (
    carrier_past_defensive_line,
)


# ── ROLLOUT CONFIG ───────────────────────────────────────────────────
MAX_FRAMES = int(os.environ.get("RLM_FBX_FRAMES", "9000"))
SEED = int(os.environ.get("RLM_FBX_SEED", "20260827"))
DT = 1.0 / 60.0

# Rolling per-frame buffer, and how much of a tail to keep for slicing.
BUFFER_FRAMES = int(os.environ.get("RLM_FBX_BUFFER", "3600"))

# Frames printed either side of a detected crossing.
WINDOW_BEFORE = int(os.environ.get("RLM_FBX_BEFORE", "40"))
WINDOW_AFTER = int(os.environ.get("RLM_FBX_AFTER", "80"))

# Cap on the number of crossings printed in full.
MAX_CROSSINGS = int(os.environ.get("RLM_FBX_CROSSINGS", "6"))

REPORT_PATH = os.environ.get("RLM_FBX_REPORT", "tmp_fb_crossing.txt")

# A body is "stalled" when its realised speed is below this, on a frame where
# the chase contract is supposed to be running.
STALL_SPEED_MPS = float(os.environ.get("RLM_FBX_STALL", "0.35"))

DEFENDING_TRYLINE_X = 110.0
BREAK_NONE = fbr.FB_BREAK_NONE

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


def banner(title):
    print("")
    print("=" * 118)
    print(title)
    print("=" * 118)


def sub(title):
    print("")
    print("-" * 118)
    print(title)
    print("-" * 118)


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


def bools(flag):
    return "T" if flag else "."


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


def occupancy_count(world, anchor_x, fb):
    """(occupied, bodies_at_or_behind, bodies_read) for *anchor_x*.

    Mirrors ``fullback_role.fullback_defensive_line_occupied`` exactly, but
    also hands back the COUNT so the report can show how close the gate was to
    flipping rather than only that it did.
    """
    band = fbr.FB_LINE_OCCUPIED_BAND_M
    at_or_behind = 0
    read = 0
    for body in getattr(world, "defending_team", None) or ():
        if body is None or body is fb:
            continue
        raw = getattr(body, "x", None)
        if raw is None:
            continue
        try:
            body_x = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(body_x):
            continue
        read += 1
        if body_x - anchor_x <= band:
            at_or_behind += 1
    if read == 0:
        return True, 0, 0
    return (at_or_behind > 0), at_or_behind, read


def sample(world, fb, bc):
    """One frame's full gate vector, read through the single-source helpers."""
    if fb is None or bc is None:
        return None

    line_x = fbr.line_anchor_x(world)
    test_x = fbr.break_test_line_anchor_x(world, fb, line_x)
    anchor_x, anchor_y = fbr.compute_fullback_anchor(bc, line_x)

    kind_live = fbr.classify_fullback_break(bc, fb, line_x, anchor_y)
    kind_test = fbr.classify_fullback_break(bc, fb, test_x, anchor_y)
    occupied, at_or_behind, bodies = occupancy_count(world, test_x, fb)

    fb_x = fnum(getattr(fb, "x", 0.0))
    bc_x = fnum(getattr(bc, "x", 0.0))
    vx = fnum(getattr(fb, "vx", 0.0))
    vy = fnum(getattr(fb, "vy", 0.0))
    speed = math.hypot(vx, vy)
    target_x = optnum(getattr(fb, "target_x", None))

    return {
        "frame": int(getattr(world, "frame_count", 0) or 0),
        # relative geometry
        "fb_x": fb_x,
        "bc_x": bc_x,
        "lead": fb_x - bc_x,          # > 0: FB still goal-side of the carrier
        "dist": math.hypot(
            fb_x - bc_x, fnum(getattr(fb, "y", 0.0)) - fnum(getattr(bc, "y", 0.0))
        ),
        # the command
        "mode": name_of(getattr(fb, "movement_state", None)),
        "target_x": target_x,
        "target_y": optnum(getattr(fb, "target_y", None)),
        "source": getattr(fb, "target_source", None),
        "writer": getattr(fb, "_target_writer", None),
        "vx": vx,
        "vy": vy,
        "speed": speed,
        "closing": (vx > 0.0),        # +X is the attack direction
        # the break verdicts
        "kind_live": kind_live,
        "kind_test": kind_test,
        "past_live": bool(carrier_past_defensive_line(world, bc)),
        "past_test": bool(carrier_past_defensive_line(world, bc, anchor_x=test_x)),
        "occupied": bool(occupied),
        "occupied_n": at_or_behind,
        "bodies": bodies,
        # the authority predicates
        "breakaway": bool(fbr.fullback_breakaway_live(world, fb, bc)),
        "depth_active": bool(fbr.fullback_depth_break_active(world, fb, bc)),
        "latest_active": bool(fbr.fullback_lateral_break_active(world, fb, bc)),
        "beaten": bool(fullback_beaten_ownership_loss_active(world, fb, bc)),
        "authority": bool(fullback_tackle_authority_active(fb)),
        "engaged": bool(fbr.is_fullback_emergency_engaged(fb)),
        "tackle_role": getattr(fb, "tackle_role", None),
        "frozen": bool(getattr(fb, "frozen", False)),
        "tes_active": bool(getattr(world, "tes_active", False)),
        # the anchors
        "line_x": line_x,
        "test_x": test_x,
        "latched_x": fbr.latched_fullback_break_anchor_x(fb),
        "latched": bool(fbr.is_fullback_break_latched(fb)),
        "anchor_x": anchor_x,
        "ruck_x": optnum(getattr(world, "ruck_x_m", None)),
        "locked": bool(getattr(world, "anchor_locked", False)),
    }


def classify_collapse(row, prev):
    """Name the gate that withdrew the chase on *row* (given *prev*)."""
    if prev is None:
        return None, None
    was = (prev["mode"] == "CHASE" and prev["writer"] == "fullback_geometry")
    now = (row["mode"] == "CHASE" and row["writer"] == "fullback_geometry")
    if not was or now:
        return None, None
    if prev["occupied"] and not row["occupied"]:
        return ("OCCUPANCY", "occupancy True -> False (n=%d bodies=%d)"
                % (row["occupied_n"], row["bodies"]))
    if prev["past_test"] and not row["past_test"]:
        return ("LONGITUDINAL", "carrier_past_defensive_line True -> False")
    if row["source"] == "fullback" and row["mode"] == "DRIFT":
        return ("PIN", "anchor pin won: source=fullback mode=DRIFT")
    if prev["kind_test"] == fbr.FB_BREAK_DEPTH and row["kind_test"] != fbr.FB_BREAK_DEPTH:
        return ("LATERAL_FLOOR", "class %s -> %s"
                % (prev["kind_test"], row["kind_test"]))
    return ("UNCLASSIFIED", "mode %s/%s -> %s/%s"
            % (prev["mode"], prev["writer"], row["mode"], row["writer"]))


def render(row, tag=""):
    print(
        "  f=%-6s%s lead=%-7s fb_x=%-7s bc_x=%-7s dist=%-6s | mode=%-6s"
        " tgt=(%s,%s) src=%-17s writer=%-17s | v=(%s,%s) spd=%-5s closing=%s"
        % (
            row["frame"], tag, fmt(row["lead"]), fmt(row["fb_x"]),
            fmt(row["bc_x"]), fmt(row["dist"]), fmt(row["mode"]),
            fmt(row["target_x"]), fmt(row["target_y"]), fmt(row["source"]),
            fmt(row["writer"]), fmt(row["vx"]), fmt(row["vy"]),
            fmt(row["speed"]), bools(row["closing"]),
        )
    )
    print(
        "           gates| kind_live=%-8s kind_test=%-8s past_live=%s past_test=%s"
        " occ=%s(n=%d/%d) latched=%s(%s)"
        % (
            row["kind_live"], row["kind_test"], bools(row["past_live"]),
            bools(row["past_test"]), bools(row["occupied"]),
            row["occupied_n"], row["bodies"], bools(row["latched"]),
            fmt(row["latched_x"]),
        )
    )
    print(
        "           preds| breakaway=%s depth=%s lateral=%s beaten=%s auth=%s"
        " eng=%s trole=%-9s frz=%s tes=%s | line=%s test=%s anchor=%s ruck=%s"
        % (
            bools(row["breakaway"]), bools(row["depth_active"]),
            bools(row["latest_active"]), bools(row["beaten"]),
            bools(row["authority"]), bools(row["engaged"]),
            fmt(row["tackle_role"]), bools(row["frozen"]), bools(row["tes_active"]),
            fmt(row["line_x"]), fmt(row["test_x"]), fmt(row["anchor_x"]),
            fmt(row["ruck_x"]),
        )
    )


def run_rollout(frames=MAX_FRAMES, seed=SEED):
    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball

    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play

    kickoff_engine = KickoffEngine(world, ball, field)

    random.seed(seed)
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    # ── anchor-relock recorder (a read-only spy) ──
    real_reset = aa.reset_anchor_authority
    relocks = []

    def reset_anchor(world_arg, *a, **kw):
        res = real_reset(world_arg, *a, **kw)
        try:
            relocks.append(int(getattr(world_arg, "frame_count", 0) or 0))
        except Exception:
            pass
        return res

    aa.reset_anchor_authority = reset_anchor

    rows = []
    crossings = []
    prev_lead = None
    prev_row = None
    all_rows = {}
    frame = 0

    try:
        for frame in range(1, frames + 1):
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
                    players = getattr(world, "players", None) or []
                    if players:
                        collision_system.resolve_all(players, world=world)

            fb = find_fullback(world)
            bc = getattr(world, "ball_carrier", None)
            row = sample(world, fb, bc)
            if row is None:
                prev_lead = None
                prev_row = None
                continue

            # A break "era" exists while the carrier is past the line by the
            # UNGATED predicate, or the FB is chasing.  Crossings are only
            # interesting inside it.
            era = (
                row["past_live"]
                or row["mode"] == "CHASE"
                or row["dist"] < 30.0
            )
            if era and prev_lead is not None:
                if prev_lead > 0.0 >= row["lead"]:
                    crossings.append((row["frame"], "FB_PASSED"))
                elif prev_lead < 0.0 <= row["lead"]:
                    crossings.append((row["frame"], "FB_REGAINED"))

            prev_lead = row["lead"]
            row["collapse"] = classify_collapse(row, prev_row)
            prev_row = row

            all_rows[row["frame"]] = row
            rows.append(row)
            if len(rows) > BUFFER_FRAMES:
                rows.pop(0)
    finally:
        aa.reset_anchor_authority = real_reset

    return {
        "rows": rows,
        "all_rows": all_rows,
        "crossings": crossings,
        "relocks": relocks,
        "frames": frame,
        "order": [r["frame"] for r in rows],
    }


def report(res):
    rows = res["rows"]
    all_rows = res["all_rows"]
    crossings = res["crossings"]

    # ── A. the whole-run counters, over the frames still in the buffer ──
    break_frames = [r for r in rows if r["kind_test"] != BREAK_NONE or r["past_test"]]
    chase_frames = [r for r in rows if r["mode"] == "CHASE"
                    and r["writer"] == "fullback_geometry"]
    pin_frames = [r for r in rows
                  if r["source"] == "fullback" and r["mode"] == "DRIFT"]
    stall_frames = [r for r in rows
                    if r["speed"] < STALL_SPEED_MPS
                    and r["dist"] < 40.0
                    and r["kind_test"] != BREAK_NONE]
    revoke_frames = [r for r in rows
                     if r["past_live"] and not r["occupied"]]
    both = [r for r in stall_frames if not r["occupied"]]

    banner("FB CARRIER-CROSSING PROBE — does the pursuit survive the crossing?")
    print("  seed                        : %d" % SEED)
    print("  frames stepped              : %d" % res["frames"])
    print("  frames in the rolling buffer: %d" % len(rows))
    print("  anchor relocks observed     : %d" % len(res["relocks"]))
    print("  carrier-crossing events     : %d" % len(crossings))

    sub("A. THE POPULATION (over the buffered frames)")
    print("  frames with a live break verdict (latched test) : %d" % len(break_frames))
    print("  frames owned as CHASE by fullback_geometry      : %d" % len(chase_frames))
    print("  frames the ANCHOR PIN won (src=fullback, DRIFT) : %d" % len(pin_frames))
    print("  frames the chase is OCCUPANCY-revoked           : %d"
          "  (past_live True AND occupied False)" % len(revoke_frames))
    print("  frames the pursuer is STALLED (spd < %.2f)      : %d"
          % (STALL_SPEED_MPS, len(stall_frames)))
    print("  of those stalled frames, occupancy False        : %d" % len(both))

    if crossings:
        sub("B. THE CROSSING EVENTS")
        for frame, kind in crossings[:MAX_CROSSINGS]:
            print("  %-12s f=%d" % (kind, frame))

    # ── C. the full window around each crossing ──
    for idx, (frame, kind) in enumerate(crossings[:MAX_CROSSINGS]):
        sub("C.%d  %s at f=%d — %d frames before / %d after"
            % (idx + 1, kind, frame, WINDOW_BEFORE, WINDOW_AFTER))
        print("  legend: lead = fb.x - bc.x (>0 = FB still goal-side of the"
              " carrier); closing = vx > 0 (+X is the attack direction)")
        window = range(frame - WINDOW_BEFORE, frame + WINDOW_AFTER + 1)
        prev = None
        for f in window:
            row = all_rows.get(f)
            if row is None:
                continue
            tag = " <== CROSS" if f == frame else ""
            if row["collapse"] and row["collapse"][0]:
                render(row, tag)
                print("           >>> COLLAPSE: %s :: %s"
                      % (row["collapse"][0], row["collapse"][1]))
            else:
                render(row, tag)
            prev = row

    # ── D. every collapse observed anywhere in the buffer ──
    sub("D. EVERY CHASE COLLAPSE IN THE BUFFER (mode/writer left the chase)")
    n = 0
    for row in rows:
        if row["collapse"] and row["collapse"][0]:
            n += 1
            print("  f=%-6s %-14s %s"
                  % (row["frame"], row["collapse"][0], row["collapse"][1]))
    if n == 0:
        print("  (no collapse observed: the chase held whenever it was armed)")

    # ── E. verdict ──
    sub("E. VERDICT")
    if not crossings:
        print("  No carrier-crossing event was observed in this rollout, so the")
        print("  probe cannot discriminate between the gates.  Widen the")
        print("  rollout (RLM_FBX_FRAMES) or change the seed (RLM_FBX_SEED).")
    elif len(both) and len(stall_frames):
        print("  %d of %d stalled pursuit frames occur with the OCCUPANCY gate"
              % (len(both), len(stall_frames)))
        print("  False, i.e. the break verdict was WITHDRAWN mid-chase.")
        print("  => the carrier-crossing stall is the occupancy gate.")
    elif len(pin_frames):
        print("  The ANCHOR PIN won %d frames while the carrier was close."
              % len(pin_frames))
        print("  => the stall is the pin: the engine wrote a STATIC anchor")
        print("     target with MovementState.DRIFT in place of the pursuit.")
    else:
        print("  Stalls observed but neither the occupancy gate nor the pin")
        print("  explains them; see section D for the collapse classes.")
    print("")


def main():
    global _REPORT
    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    sys.stdout = _tee()
    try:
        res = run_rollout()
        report(res)
    finally:
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
