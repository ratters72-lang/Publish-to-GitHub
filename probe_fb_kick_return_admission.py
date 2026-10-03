"""probe_fb_kick_return_admission.py — WHY THE FULLBACK RUNS IN ON A KICK RETURN.

THE QUESTION
------------
After a kickoff reception, with NO line break, the tagged fullback leaves its
DRIFT anchor and commits to a tackle.  This probe finds the FIRST frame the FB
acquires tackle ownership (lane stamp / emergency engagement / TACKLER role)
and prints the preceding window frame by frame, with every value the admission
gates read, so the exact leg that admitted it is measured rather than guessed.

WHAT IT RECORDS, PER FRAME
--------------------------
    geometry   bc / fb positions, 2D distance, both speeds, the FB command
    authority  tag, can_acquire_tackle, tackle_role, commit, stance, engaged,
               defender_role, movement_state
    gates      live line anchor, LATCHED (break-test) anchor, ruck_x,
               anchor_locked, carrier gain since the catch,
               depth margin (bc.x - (anchor + FB_LINE_BREAK_MARGIN_M)),
               level margin (bc.x - (anchor - FB_LATERAL_BREAK_LEVEL_M)),
               channel breach |bc.y - fb.y|, 2D reach
    verdicts   classify_fullback_break on the LIVE anchor and on the LATCHED
               anchor, plus fullback_depth_break_active,
               fullback_lateral_break_active, fullback_emergency_defender_active

METHOD
------
Read-only.  Boots the engine exactly as probe_fb_authority_timeline does and
steps it; nothing is spied on and nothing is written to the engine.

Run:    python probe_fb_kick_return_admission.py
Report: tmp_fb_kick_return_admission.txt
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

MAX_FRAMES = int(os.environ.get("RLM_FBKRA_FRAMES", "1500"))
SEED = int(os.environ.get("RLM_FBKRA_SEED", "20260827"))
DT = 1.0 / 60.0

# Frames printed before the admission frame.
BACKTRACE_FRAMES = int(os.environ.get("RLM_FBKRA_BACKTRACE", "120"))

REPORT_PATH = os.environ.get(
    "RLM_FBKRA_REPORT", "tmp_fb_kick_return_admission.txt"
)

_REAL_STDOUT = sys.stdout
_REPORT = None


class _tee:
    """Write to the report file and the real stdout at once."""

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
    """Float-coerce an attribute without raising."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def name_of(obj):
    """``obj.name`` when present, else ``str(obj)``, else None."""
    if obj is None:
        return None
    return getattr(obj, "name", None) or str(obj)


def find_fullback(world):
    """The tagged fullback this frame (or None)."""
    for d in (getattr(world, "defending_team", None) or ()):
        if getattr(d, "fullback_role_active", False):
            return d
    return None


def classify_safe(bc, body, anchor_x, anchor_y):
    """``classify_fullback_break`` with its arguments guarded."""
    if bc is None or body is None or anchor_x is None:
        return fbr.FB_BREAK_NONE
    try:
        return fbr.classify_fullback_break(
            bc, body, float(anchor_x), float(anchor_y)
        )
    except Exception:
        return fbr.FB_BREAK_NONE


def sample(world, open_play, body, catch_x):
    """One frame's worth of admission evidence for *body*."""
    bc = getattr(world, "ball_carrier", None)
    row = {
        "frame": int(getattr(world, "frame_count", 0) or 0),
        "phase": name_of(getattr(world, "phase", None)),
        "subphase": name_of(getattr(open_play, "_phase", None)),
        "ruck_x": getattr(world, "ruck_x_m", None),
        "anchor_locked": bool(getattr(world, "anchor_locked", False)),
        "bc": None,
        "fb": None,
    }
    if bc is not None:
        row["bc"] = {
            "x": fnum(getattr(bc, "x", 0.0)),
            "y": fnum(getattr(bc, "y", 0.0)),
            "speed": (fnum(getattr(bc, "vx", 0.0)) ** 2
                      + fnum(getattr(bc, "vy", 0.0)) ** 2) ** 0.5,
            "gain": (fnum(getattr(bc, "x", 0.0)) - float(catch_x)
                     if catch_x is not None else None),
        }
    if body is None:
        return row

    live_anchor = fnum(fbr.line_anchor_x(world), 0.0)
    try:
        latched_anchor = fnum(fbr.break_test_line_anchor_x(world, body), 0.0)
    except Exception:
        latched_anchor = 0.0

    anchor_y = None
    if bc is not None:
        try:
            _ax, anchor_y = fbr.compute_fullback_anchor(bc, live_anchor)
        except Exception:
            anchor_y = fnum(getattr(body, "y", 0.0))

    fb_x = fnum(getattr(body, "x", 0.0))
    fb_y = fnum(getattr(body, "y", 0.0))
    dist = None
    channel = None
    depth_margin = None
    level_margin = None
    if bc is not None:
        bc_x = fnum(getattr(bc, "x", 0.0))
        bc_y = fnum(getattr(bc, "y", 0.0))
        dist = ((bc_x - fb_x) ** 2 + (bc_y - fb_y) ** 2) ** 0.5
        channel = abs(bc_y - fb_y)
        depth_margin = bc_x - (live_anchor + fbr.FB_LINE_BREAK_MARGIN_M)
        level_margin = bc_x - (live_anchor - fbr.FB_LATERAL_BREAK_LEVEL_M)

    # ── IS THERE A DEFENSIVE LINE AT ALL? ──
    # The admission is only meaningful if the anchor is a line the DEFENCE
    # occupies rather than a number derived from the ball.  Count the other
    # defenders either side of the anchor and how close the nearest one is.
    occ = {"near": 0, "behind": 0, "ahead": 0, "min_abs": None,
           "nearest": None}
    for d in (getattr(world, "defending_team", None) or ()):
        if d is body:
            continue
        dx = fnum(getattr(d, "x", 0.0)) - live_anchor
        if abs(dx) <= 3.0:
            occ["near"] += 1
        if dx < 0.0:
            occ["behind"] += 1
        else:
            occ["ahead"] += 1
        if occ["min_abs"] is None or abs(dx) < occ["min_abs"]:
            occ["min_abs"] = abs(dx)
            occ["nearest"] = fnum(getattr(d, "x", 0.0))
    row["occ"] = occ

    row["fb"] = {
        "x": fb_x,
        "y": fb_y,
        "vx": fnum(getattr(body, "vx", 0.0)),
        "vy": fnum(getattr(body, "vy", 0.0)),
        "speed": (fnum(getattr(body, "vx", 0.0)) ** 2
                  + fnum(getattr(body, "vy", 0.0)) ** 2) ** 0.5,
        "move_speed": fnum(getattr(body, "move_speed", 0.0)),
        "state": name_of(getattr(body, "movement_state", None)),
        "role": name_of(getattr(body, "defender_role", None)),
        "target_x": fnum(getattr(body, "target_x", 0.0)),
        "target_y": fnum(getattr(body, "target_y", 0.0)),
        "writer": (getattr(body, "_target_writer", None)
                   or getattr(body, "target_source", None)),
        "tag": bool(getattr(body, "fullback_role_active", False)),
        "can_acquire_tackle": bool(
            getattr(body, "can_acquire_tackle", False)),
        "tackle_role": getattr(body, "tackle_role", None),
        "commit": bool(getattr(body, "_tackle_commit_active", False)),
        "stance": bool(getattr(body, "pre_tackle_stance", False)),
        "engaged": bool(fbr.is_fullback_emergency_engaged(body)),
        "is_tackler": bool(getattr(body, "is_tackler", False)),
        "frozen": bool(getattr(body, "frozen", False)),
        "tes_attached": bool(getattr(body, "tes_attached", False)),
        "in_contact": bool(getattr(body, "in_contact", False)),
        "authority_active": bool(fbr.fullback_tackle_authority_active(body)),
        "dist": dist,
        "channel": channel,
        "depth_margin": depth_margin,
        "level_margin": level_margin,
        "live_anchor": live_anchor,
        "latched_anchor": latched_anchor,
        "latched": bool(fbr.is_fullback_break_latched(body)),
        "live_kind": classify_safe(bc, body, live_anchor, anchor_y),
        "latched_kind": classify_safe(bc, body, latched_anchor, anchor_y),
        "depth_active": bool(fbr.fullback_depth_break_active(world, body, bc)),
        "lateral_active": bool(
            fbr.fullback_lateral_break_active(world, body, bc)),
        "emergency": bool(
            fbr.fullback_emergency_defender_active(world, body, bc)),
    }
    return row


def admission_of(row):
    """The stamp that first granted the FB tackle ownership (or None)."""
    fb = row.get("fb")
    if not fb:
        return None
    if fb["engaged"]:
        return "engaged"
    if fb["tackle_role"] in ("PRIMARY", "SECONDARY"):
        return "tackle_role=%s" % fb["tackle_role"]
    if fb["role"] in ("TACKLER_1", "TACKLER_2", "TACKLER_3"):
        return "role=%s" % fb["role"]
    if fb["commit"]:
        return "commit"
    return None


def _d(value, places=2):
    """Format an optional float with a fixed precision."""
    return "--" if value is None else ("%%.%df" % places) % value


def render(row, marker=""):
    """Print one frame's full admission evidence."""
    fb = row.get("fb")
    bc = row.get("bc")
    if not fb:
        print("  f=%-6s %-10s NO TAGGED FULLBACK" % (row["frame"],
                                                     row["phase"]))
        return
    print("  f=%-6s %-10s %-11s%s"
          % (row["frame"], row["phase"], row["subphase"] or "-", marker))
    print("      bc  x=%-7s y=%-7s spd=%-6s gain=%s"
          % (_d(bc["x"]) if bc else "--",
             _d(bc["y"]) if bc else "--",
             _d(bc["speed"], 2) if bc else "--",
             _d(bc["gain"], 2) if bc else "--"))
    print("      fb  x=%-7s y=%-7s spd=%-6s cmd=%-6s state=%-8s role=%-13s"
          " writer=%s"
          % (_d(fb["x"]), _d(fb["y"]), _d(fb["speed"], 2),
             _d(fb["move_speed"], 2), fb["state"], fb["role"], fb["writer"]))
    print("      fb  tgt=(%s,%s) dist=%-7s channel=%s"
          % (_d(fb["target_x"]), _d(fb["target_y"]), _d(fb["dist"]),
             _d(fb["channel"])))
    print("      auth tag=%s ctl=%s trole=%-8s commit=%s stance=%s eng=%s"
          " tackler=%s auth=%s"
          % (int(fb["tag"]), int(fb["can_acquire_tackle"]),
             fb["tackle_role"] or "-", int(fb["commit"]), int(fb["stance"]),
             int(fb["engaged"]), int(fb["is_tackler"]),
             int(fb["authority_active"])))
    print("      gate anchor=%-7s latched=%-7s ruck=%-8s locked=%s"
          " d_margin=%-8s l_margin=%s"
          % (_d(fb["live_anchor"]), _d(fb["latched_anchor"]),
             _d(row.get("ruck_x")) if row.get("ruck_x") is not None
             else "None",
             int(row.get("anchor_locked", False)),
             _d(fb["depth_margin"]), _d(fb["level_margin"])))
    print("      gate live_kind=%-8s latch_kind=%-8s depth=%s lateral=%s"
          " EMERGENCY=%s"
          % (fb["live_kind"], fb["latched_kind"], int(fb["depth_active"]),
             int(fb["lateral_active"]), int(fb["emergency"])))
def run_rollout(seed=SEED):
    """Boot the engine, kick off, step it, return the sampled rows."""
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

    result = {
        "seed": seed,
        "rows": [],
        "catch_frame": None,
        "catch_x": None,
        "admission": None,
        "admission_frame": None,
        "open_play": open_play,
        "world": world,
    }

    # The engine's own per-frame chatter would drown the report.
    devnull = open(os.devnull, "w", encoding="utf-8")
    saved = sys.stdout
    sys.stdout = devnull
    try:
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
                    players = getattr(world, "players", None) or []
                    if players:
                        collision_system.resolve_all(players, world=world)

            if (result["catch_frame"] is None
                    and world.ball_carrier is not None):
                result["catch_frame"] = frame
                result["catch_x"] = fnum(
                    getattr(world.ball_carrier, "x", 0.0))

            if result["catch_frame"] is None:
                continue

            row = sample(world, open_play, find_fullback(world),
                         result["catch_x"])
            result["rows"].append(row)
            if result["admission"] is None:
                result["admission"] = admission_of(row)
                if result["admission"] is not None:
                    result["admission_frame"] = frame
                    break
    finally:
        sys.stdout = saved
        devnull.close()

    return result


def report(result):
    """Write the admission report."""
    rows = result["rows"]
    catch_frame = result["catch_frame"]
    catch_x = result["catch_x"]
    admission = result["admission"]

    print("=" * 108)
    print("FB KICK-RETURN ADMISSION - why the fullback runs in with no break")
    print("=" * 108)
    print("  seed                     : %d" % result.get("seed", SEED))
    print("  kickoff reception frame  : %s" % catch_frame)
    print("  carrier x at reception   : %s" % _d(catch_x))
    print("  rows sampled post-catch  : %d" % len(rows))
    print("")

    if catch_frame is None:
        print("  !! NO reception in this rollout - nothing to report.")
        return

    if admission is None:
        print("  NO ADMISSION: the FB never acquired tackle ownership within")
        print("  the horizon.  That is the CORRECT behaviour for a return")
        print("  where no line break occurs.")
        print("")
        print("  -- last 10 sampled frames --")
        for row in rows[-10:]:
            render(row)
            print("")
        return

    idx = len(rows) - 1
    admit_row = rows[idx]
    fb = admit_row["fb"]
    bc = admit_row["bc"]

    print("-" * 108)
    print("ADMISSION at f=%s  (%s)"
          % (admit_row["frame"], admission))
    print("-" * 108)
    render(admit_row, marker="   <-- FIRST OWNERSHIP")
    print("")
    print("  carrier gain since reception : %s m"
          % (_d(bc["gain"]) if bc else "--"))
    print("  DEPTH leg needs gain         : > %.2f m"
          % (fbr.FB_LINE_BREAK_MARGIN_M + 10.0))
    print("  LEVEL exclusion releases at  : >= %.2f m"
          % (10.0 - fbr.FB_LATERAL_BREAK_LEVEL_M))
    print("")
    print("  LEG THAT ADMITTED : %s"
          % ("DEPTH" if fb["depth_active"]
             else ("LATERAL" if fb["lateral_active"] else "NEITHER")))
    print("     depth_active=%s lateral_active=%s emergency=%s"
          % (fb["depth_active"], fb["lateral_active"], fb["emergency"]))
    print("     live_kind=%s latched_kind=%s latched=%s"
          % (fb["live_kind"], fb["latched_kind"], fb["latched"]))

    first_lateral = None
    first_emergency = None
    first_depth = None
    for row in rows:
        rfb = row.get("fb")
        if not rfb:
            continue
        if first_depth is None and rfb["depth_active"]:
            first_depth = row
        if first_lateral is None and rfb["lateral_active"]:
            first_lateral = row
        if first_emergency is None and rfb["emergency"]:
            first_emergency = row

    print("")
    for label, hit in (("FIRST depth_active   ", first_depth),
                       ("FIRST lateral_active ", first_lateral),
                       ("FIRST emergency gate ", first_emergency)):
        if hit is None:
            print("  %s : never" % label)
            continue
        hbc = hit["bc"]
        print("  %s : f=%-6s gain=%-7s dist=%-7s channel=%s"
              % (label, hit["frame"],
                 _d(hbc["gain"]) if hbc else "--",
                 _d(hit["fb"]["dist"]), _d(hit["fb"]["channel"])))

    print("")
    print("-" * 108)
    print("BACKTRACE - up to %d frames ending on the admission frame"
          % BACKTRACE_FRAMES)
    print("-" * 108)
    start = max(0, idx - BACKTRACE_FRAMES + 1)
    for row in rows[start:idx + 1]:
        render(row, marker="   <-- ADMIT" if row is admit_row else "")
        print("")

    print("-" * 108)
    print("COMPACT TABLE - state changes, plus every 20th frame")
    print("  'near'  = other defenders within 3 m of the anchor X")
    print("  'bhd/ahd' = other defenders behind / ahead of the anchor X")
    print("-" * 108)
    print("  %-6s %-8s %-7s %-8s %-8s %-8s %-9s %-5s %-6s %-5s %-5s %-5s %-5s"
          % ("frame", "gain", "bc_x", "d_marg", "live", "latch", "mode",
             "dep", "lat", "emg", "near", "bhd", "ahd"))
    prev_key = None
    for i, row in enumerate(rows):
        rfb = row.get("fb")
        rbc = row.get("bc")
        if not rfb:
            continue
        key = (rfb["live_kind"], rfb["latched_kind"],
               int(rfb["depth_active"]), int(rfb["lateral_active"]),
               int(rfb["emergency"]))
        if key == prev_key and i % 20 != 0:
            continue
        prev_key = key
        occ = row.get("occ") or {}
        print("  %-6d %-8s %-7s %-8s %-8s %-8s %-9s %-5s %-6s %-5s %-5s %-5s"
              " %-5s"
              % (row["frame"],
                 _d(rbc["gain"]) if rbc else "--",
                 _d(rbc["x"]) if rbc else "--",
                 _d(rfb["depth_margin"]),
                 rfb["live_kind"], rfb["latched_kind"], rfb["state"],
                 int(rfb["depth_active"]), int(rfb["lateral_active"]),
                 int(rfb["emergency"]),
                 occ.get("near", "--"), occ.get("behind", "--"),
                 occ.get("ahead", "--")))
    print("")
    print("  Report written to %s" % REPORT_PATH)


def main():
    global _REPORT
    real = sys.stdout

    # The engine is NOT reproducible across processes for this fixture (the
    # multi-seed notes in probe_fb_authority_timeline record three runs of one
    # seed producing three different populations), so the sweep runs several
    # seeds in ONE process and reports which of them exercised the question.
    raw = os.environ.get("RLM_FBKRA_SEEDS", str(SEED)).replace(" ", "")
    seeds = [int(s) for s in raw.split(",") if s]

    # PAIRED A/B: the engine is not reproducible across processes, so the
    # only sound comparison is ON vs OFF over the SAME in-process realisation
    # of the same seed.
    ab = os.environ.get("RLM_FBKRA_AB", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

    chosen = None
    last = None
    summaries = []
    ab_rows = []
    devnull = open(os.devnull, "w", encoding="utf-8")
    for seed in seeds:
        saved = sys.stdout
        sys.stdout = devnull
        try:
            fbr.FB_LINE_OCCUPIED_REQUIRED = True
            result = run_rollout(seed=seed)
            off = None
            if ab:
                fbr.FB_LINE_OCCUPIED_REQUIRED = False
                off = run_rollout(seed=seed)
        finally:
            fbr.FB_LINE_OCCUPIED_REQUIRED = True
            sys.stdout = saved
        last = result
        if off is not None:
            ab_rows.append(
                (seed, result["admission"] or "-",
                 result["admission_frame"] or "-",
                 off["admission"] or "-",
                 off["admission_frame"] or "-")
            )
            print("  ... seed %d PAIRED: ON admit=%s@%s  |  OFF admit=%s@%s"
                  % (seed, result["admission"], result["admission_frame"],
                     off["admission"], off["admission_frame"]),
                  file=real, flush=True)
        rows = result["rows"]
        first_depth = next(
            (r["frame"] for r in rows
             if r.get("fb") and r["fb"]["depth_active"]), None)
        first_lat = next(
            (r["frame"] for r in rows
             if r.get("fb") and r["fb"]["lateral_active"]), None)
        summaries.append(
            (seed, result["catch_frame"], result["admission"] or "-",
             result["admission_frame"] or "-", first_depth or "-",
             first_lat or "-", len(rows)))
        print("  ... seed %d: catch=%s admit=%s@%s depth_from=%s lat_from=%s"
              % (seed, result["catch_frame"], result["admission"],
                 result["admission_frame"], first_depth, first_lat),
              file=real, flush=True)
        if chosen is None and result["admission"] is not None:
            chosen = result
    try:
        devnull.close()
    except Exception:
        pass

    if chosen is None:
        chosen = last

    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    sys.stdout = _tee()
    try:
        print("=" * 108)
        print("SEED SWEEP")
        print("=" * 108)
        print("  %-10s %-8s %-12s %-10s %-11s %-11s %-6s"
              % ("seed", "catch", "admission", "at frame", "depth_from",
                 "lateral_from", "rows"))
        for row in summaries:
            print("  %-10d %-8s %-12s %-10s %-11s %-11s %-6d" % row)
        print("")
        if ab_rows:
            print("=" * 108)
            print("PAIRED A/B - SAME in-process realisation, occupancy gate "
                  "ON vs OFF")
            print("=" * 108)
            print("  %-10s %-16s %-10s %-16s %-10s"
                  % ("seed", "ON admission", "ON frame", "OFF admission",
                     "OFF frame"))
            for row in ab_rows:
                print("  %-10d %-16s %-10s %-16s %-10s" % row)
            print("")
        report(chosen)
    finally:
        sys.stdout = real
        try:
            _REPORT.flush()
            _REPORT.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
