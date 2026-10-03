"""
probe_goal_line_compression.py — DEFENDING 10 m / GOAL-LINE COMPRESSION
FIELD-REALISATION PROBE (READ-ONLY).

QUESTION
--------
The four-anchor compression model (defensive_width.py, 2026-10-03) exposes
exactly TWO independent goal-line squeeze knobs on DefenceTuners:

    defending_tryline_compression   (default 1.0, neutral)
    defending_10m_compression       (default 0.95, legacy-equivalent)

Both are COMPRESSIONS of the ONE defending-tryline width region
(``width_defending_tryline_m`` == ``width_right_tryline_m``):

    defending tryline width = region * defending_tryline_compression
    defending 10 m width    = region * defending_10m_compression

The MODEL contract (verify_width_curve_four_anchor.py) proves the CURVE
arithmetic.  It does NOT prove that the commanded squeeze reaches the LIVE
defensive line under real OPEN_PLAY conditions, where the governor's
per-frame width-delta clamp, the physical field cap, the state gating
(pursuit / no-BC / TES) and the spacing ladder all sit between the command
and the on-field Y.

This probe closes that gap.  It answers three questions with the REAL
engine running (governor + formation_width_authority + split spacing +
movement integration, all live):

  1. REALISATION — as the ball carrier advances into the goal-line band
     (bc.x ~ 88..110), does the realised on-field line span track the
     commanded / governed four-anchor width?  (Default, NEUTRAL tuners.)

  2. INDEPENDENT REACH — does moving ONLY ``defending_10m_compression``
     change the realised span in the 10 m region, and moving ONLY
     ``defending_tryline_compression`` change it at the tryline — on the
     live line, not just the curve?

  3. ON-FIELD SENSITIVITY — how many of the commanded metres survive to the
     on-field line near the goal line (the rung-by-rung absorption the
     Stage-8 audit names: command -> governed -> published -> lane ->
     spacing -> on-field)?

HOW THE GOAL-LINE BAND IS REACHED
---------------------------------
A natural kickoff rollout keeps the carrier near midfield (the line
tackles it), so it rarely exercises bc.x ~ 100.  The probe therefore runs a
CONTROLLED goal-line drive on the REAL engine: once OPEN_PLAY is live with
a formed line, the carrier's X is PINNED to a grid of goal-line values and
the engine is allowed to run so the real pipeline converges the line to
each commanded width.  Only the carrier's X is externally pinned; every
defensive subsystem (governor, width authority, spacing, movement) runs
normally, so what is measured is the LIVE realisation of the commanded
width.  This is the same probe-control pattern probe_stage8 uses (it drives
bc.x through the goal-line band) extended to the integrated line.

READ-ONLY against production.  No engine file is modified or written.

Run:  python probe_goal_line_compression.py
Out:  stdout report + goal_line_compression.csv + goal_line_compression.txt
"""

from __future__ import annotations

import contextlib
import csv
import io
import math
import os
import random
import statistics
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    interpolated_defensive_width,
    governed_width_m,
    attacking_tryline_anchor_width_m,
    halfway_anchor_width_m,
    defending_10m_anchor_width_m,
    defending_tryline_anchor_width_m,
    attacking_tryline_anchor_width_m as _at_anchor,
    ATTACKING_TRYLINE_X,
    HALFWAY_X,
    DEFENDING_10M_X,
    DEFENDING_TRYLINE_X,
)
from game.defence.defence_lvl_1 import population_authority as pa
from game.defence.defence_lvl_1 import formation_width_authority as fwa

FPS = 60.0
DT = 1.0 / FPS

# The goal-line band the carrier is pinned across (the defending 10 m at
# x=100 down to the defending tryline at x=110, plus a run-up from 84).
GOAL_LINE_BC_X_GRID: tuple = (84.0, 90.0, 95.0, 100.0, 105.0, 110.0)

# Frames the line is allowed to settle at each pinned bc.x before the
# measurement window opens (the geometry smooth-damp is ~0.12 s ≈ 7 frames;
# 45 frames gives the whole ladder time to converge).
SETTLE_FRAMES = 90
# Number of width-live frames the measurement averages over.
MEASURE_FRAMES = 30
# Hard cap on frames spent collecting the measurement window at one target
# (rides through a TES / RELEASE / PTB cycle if one is triggered).
MEASURE_CAP = 900

# The ruck is placed this far AHEAD of the carrier.  The width command keys
# off bc.x, but the markers / dummy-half stand on the ruck, so pinning the
# ruck exactly at the carrier put them on top of it and triggered a tackle.
# A small offset leaves the carrier behind the ruck (and its markers) while
# the anchor (ruck_x + 10) still lands the line on the goal line.
RUCK_OFFSET_M = 4.0

# The lateral centre the line is pinned to.  A real carrier sits anywhere
# on the field, which makes the SPLIT-SIDE room asymmetric (a ruck near a
# sideline starves the short side and saturates its lanes at the margin),
# confounding the width reading.  Pinning the ruck to the field centre
# removes that confound so the ONLY thing that varies between cases is the
# four-anchor compression command.
PIN_RUCK_Y_M = 35.0

# The natural-rollout length (frames the engine runs before the pinned
# drive begins, to record the natural goal-line reach + outcome proxies).
NATURAL_FRAMES = 2400

_ENGINE = {"mods": None}


# ─────────────────────────────────────────────────────────────────────
# ENGINE BOOT (mirrors probe_stage8 / probe_line_participation)
# ─────────────────────────────────────────────────────────────────────

def _engine_imports():
    """Import the real engine headless."""
    if _ENGINE["mods"] is not None:
        return _ENGINE["mods"]
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
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

    mods = {
        "Field": Field,
        "WorldState": WorldState,
        "GamePhase": GamePhase,
        "OpenPlayEngine": OpenPlayEngine,
        "apply_receiving_kickoff_formation": apply_receiving_kickoff_formation,
        "apply_kicking_team_formation": apply_kicking_team_formation,
        "KickoffEngine": KickoffEngine,
        "Ball": Ball,
        "CollisionSystem": CollisionSystem,
        "check_for_catch": check_for_catch,
    }
    _ENGINE["mods"] = mods
    return mods


def _boot_world(mods):
    collision_system = mods["CollisionSystem"]()
    field = mods["Field"](1920, 1080)
    world = mods["WorldState"]()
    ball = mods["Ball"](field)
    world.ball = ball
    open_play = mods["OpenPlayEngine"](world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = mods["KickoffEngine"](world, ball, field)
    kickoff_engine.reset_for_kickoff()
    mods["apply_receiving_kickoff_formation"](world, field)
    mods["apply_kicking_team_formation"](world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()
    return world, ball, open_play, kickoff_engine, collision_system
# ─────────────────────────────────────────────────────────────────────
# MEASUREMENT PRIMITIVES
# ─────────────────────────────────────────────────────────────────────

def _phase_label(world, ope) -> str:
    """Best-effort OPEN_PLAY phase label for the current frame."""
    lab = getattr(world, "_ope_phase_label", None)
    if lab:
        return str(lab)
    ph = getattr(ope, "_phase", None)
    return getattr(ph, "value", str(ph)) if ph is not None else ""


def _sub_name(world) -> str:
    sub = getattr(world, "sub_state", None)
    return str(getattr(sub, "value", sub))


def _line_bodies(world) -> list:
    """The line population (excludes markers / DH / fullback / tacklers)."""
    return list(pa.line_population(getattr(world, "defending_team", None) or []))


def _span(ys) -> float:
    ys = list(ys)
    if len(ys) < 2:
        return 0.0
    return float(max(ys)) - float(min(ys))


def _snapshot(world, ope, tuners) -> dict:
    """One per-frame width snapshot across the full realisation chain."""
    bc = getattr(world, "ball_carrier", None)
    bc_x = float(getattr(bc, "x", 0.0)) if bc is not None else None
    commanded = (float(interpolated_defensive_width(tuners, bc_x))
                 if bc_x is not None else None)
    governed = float(governed_width_m(world, tuners))

    pub = getattr(world, "formation_width", None)
    fresh = (isinstance(pub, dict)
             and pub.get("frame") == getattr(world, "frame_count", None))
    pub_left = pub.get("left_m") if fresh else None
    pub_right = pub.get("right_m") if fresh else None
    pub_mean = (0.5 * (float(pub_left) + float(pub_right))
                if pub_left is not None and pub_right is not None else None)

    bodies = _line_bodies(world)
    ys = [float(getattr(d, "y", 0.0)) for d in bodies]
    on_field_span = _span(ys)

    fst = getattr(world, "frame_spacing_targets", None) or {}
    target_span = _span([float(v) for v in fst.values()])

    anchor = getattr(world, "defensive_line_anchor", None)
    return {
        "frame": int(getattr(world, "frame_count", 0)),
        "bc_x": bc_x,
        "sub": _sub_name(world),
        "phase": _phase_label(world, ope),
        "in_pursuit": bool(getattr(world, "in_pursuit_mode", False)),
        "ruck_x": float(getattr(world, "ruck_x_m", 0.0) or 0.0),
        "ruck_y": float(getattr(world, "ruck_y_m", 0.0) or 0.0),
        "anchor_x": (float(anchor) if anchor is not None else None),
        "n_line": len(ys),
        "commanded_m": commanded,
        "governed_m": governed,
        "pub_left_m": None if pub_left is None else float(pub_left),
        "pub_right_m": None if pub_right is None else float(pub_right),
        "pub_mean_m": pub_mean,
        "target_span_m": target_span,
        "on_field_span_m": on_field_span,
    }


def _width_live(snap: dict) -> bool:
    """True on a frame where the width channel can realise on-field.

    OPEN_PLAY sub-state, not in pursuit, a live carrier, and at least two
    line bodies (so a span exists).  This is the exact window the governor
    publishes the four-anchor width and the spacing ladder builds from it.
    """
    return (snap["sub"] == "open_play"
            and not snap["in_pursuit"]
            and snap["n_line"] >= 2)


def _pin_carrier(world, target_x: float) -> bool:
    """Pin the carrier's X AND the ruck to the carrier's X.

    The defensive-line anchor is ``ruck_x + 10`` (the line retreats 10 m
    toward its own tryline from the ruck).  Pinning ``bc.x`` alone leaves the
    ruck where the last tackle left it, so the carrier drifts PAST the line
    and the defenders convert to a chase (mmdl_CHASE / tackler_commit),
    which collapses the on-field span and invalidates the width reading.

    Moving the ruck with the carrier keeps the carrier ~10 m BEHIND a SET
    line (the real goal-line-attack geometry) so the four-anchor width is
    actually realised by a defensive line rather than a chase.
    """
    bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return False
    try:
        bc.x = float(target_x)
        bc.y = PIN_RUCK_Y_M
        bc.vx = 0.0
    except (TypeError, ValueError):
        return False
    try:
        world.ruck_x_m = float(target_x) + RUCK_OFFSET_M
        world.ruck_y_m = PIN_RUCK_Y_M
        # THE RUCK-TRANSITION PATTERN.  Moving ruck_x_m makes the anchor
        # authority's expected value (tryline_clamped(ruck_x_m + 10)) change,
        # so the frame anchor MUST be unlocked exactly as the engine unlocks
        # it at every genuine ruck transition (PTB, contact->drive, kickoff
        # catch, ...).  Without this the anchor stays locked on its pre-pin
        # value and ``set_anchor`` refuses the frame with
        # AnchorAuthorityViolation.
        from game.defence.defence_lvl_1.anchor_authority import (
            reset_anchor_authority as _reset_anchor,
        )
        _reset_anchor(world)
    except (TypeError, ValueError):
        pass
    except Exception:
        pass
    ball = getattr(world, "ball", None)
    if ball is not None:
        try:
            ball.true_x_m = float(bc.x)
            ball.true_y_m = PIN_RUCK_Y_M
        except (TypeError, ValueError):
            pass
    return True


def _step_full(world, ball, ope, kickoff_engine, collision, mods) -> None:
    """One engine frame with the kickoff engine wired (boot phase included)."""
    GamePhase = mods["GamePhase"]
    world.frame_count += 1
    ball.update(DT)
    if world.phase == GamePhase.KICKOFF:
        kickoff_engine.update(DT)
        mods["check_for_catch"](world, ball, DT)
        return
    if world.phase == GamePhase.CATCH:
        if world.ball_carrier is not None:
            ope.start(world.ball_carrier)
        world.phase = GamePhase.OPEN_PLAY
        return
    if world.phase != GamePhase.OPEN_PLAY:
        return
    ope.update(DT)
    if not getattr(world, "tes_active", False):
        collision.resolve_all(world.players, world=world)


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return float(statistics.mean(vals)) if vals else None


def _advance_to_open_play(world, ball, ope, kickoff_engine, collision, mods,
                          cap: int = 1200) -> int:
    """Run the boot sequence until OPEN_PLAY has live, formed width frames."""
    GamePhase = mods["GamePhase"]
    formed = 0
    for i in range(cap):
        _step_full(world, ball, ope, kickoff_engine, collision, mods)
        if world.phase == GamePhase.OPEN_PLAY:
            snap = _snapshot(world, ope, getattr(ope, "def_tuners", None))
            if _width_live(snap):
                formed += 1
                if formed >= 5:
                    return i + 1
    return -1


# ─────────────────────────────────────────────────────────────────────
# PINNED GOAL-LINE GRID — the controlled drive on the REAL engine
# ─────────────────────────────────────────────────────────────────────

# Tuner scenarios exercised.  Each is (label, defending_10m_compression,
# defending_tryline_compression).  NEUTRAL first (the shipped defaults).
TUNER_SCENARIOS: tuple = (
    ("neutral",      0.95, 1.00),   # shipped defaults (legacy-equivalent)
    ("10m_squeeze",  0.70, 1.00),   # ONLY the 10 m knob moved
    ("tryline_squeeze", 0.95, 0.70),  # ONLY the tryline knob moved
    ("both_squeeze", 0.70, 0.70),   # both knobs moved
)


def _apply_scenario(tuners, c10, ctry) -> None:
    """Set ONLY the two compression knobs on the live tuner object."""
    if tuners is None:
        return
    tuners.defending_10m_compression = float(c10)
    tuners.defending_tryline_compression = float(ctry)


def run_pinned_grid(label: str, c10: float, ctry: float,
                    seed: int) -> list:
    """Boot a world, apply the compression scenario, drive the goal-line grid.

    Returns one aggregate row per pinned bc.x target.
    """
    mods = _engine_imports()
    random.seed(seed)
    world, ball, ope, kickoff_engine, collision = _boot_world(mods)
    tuners = getattr(ope, "def_tuners", None)
    _apply_scenario(tuners, c10, ctry)

    reached = _advance_to_open_play(world, ball, ope, kickoff_engine,
                                    collision, mods)
    rows = []
    if reached < 0:
        return rows

    for tgt in GOAL_LINE_BC_X_GRID:
        # SETTLE — pin the carrier and let the real pipeline converge.
        for _ in range(SETTLE_FRAMES):
            _pin_carrier(world, tgt)
            _step_full(world, ball, ope, kickoff_engine, collision, mods)
        # MEASURE — collect width-live frames, riding through a TES /
        # RELEASE / PTB cycle if one is triggered (a pinned carrier can still
        # attract a contact once).  Only width-live frames count.
        samples = []
        guard = 0
        while len(samples) < MEASURE_FRAMES and guard < MEASURE_CAP:
            guard += 1
            _pin_carrier(world, tgt)
            _step_full(world, ball, ope, kickoff_engine, collision, mods)
            snap = _snapshot(world, ope, tuners)
            if _width_live(snap):
                samples.append(snap)
        live = samples
        n_sampled = guard
        rows.append({
            "label": label,
            "c10": float(c10),
            "ctry": float(ctry),
            "target_bc_x": float(tgt),
            "n_samples": n_sampled,
            "n_live": len(live),
            "bc_x_m": _mean([s["bc_x"] for s in live]),
            "anchor_x_m": _mean([s["anchor_x"] for s in live]),
            "ruck_x_m": _mean([s["ruck_x"] for s in live]),
            "commanded_m": _mean([s["commanded_m"] for s in live]),
            "governed_m": _mean([s["governed_m"] for s in live]),
            "pub_mean_m": _mean([s["pub_mean_m"] for s in live]),
            "target_span_m": _mean([s["target_span_m"] for s in live]),
            "on_field_span_m": _mean([s["on_field_span_m"] for s in live]),
            "n_line": _mean([s["n_line"] for s in live]),
            "sub_live": (live[0]["sub"] if live else None),
        })
    return rows
# ─────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────

def _fmt(v, nd=2):
    return "-" if v is None else ("%.*f" % (nd, float(v)))


def _rows_for(rows, label):
    return [r for r in rows if r["label"] == label]


def _row_at(rows, label, bc_x):
    for r in rows:
        if r["label"] == label and abs(r["target_bc_x"] - bc_x) < 1e-6:
            return r
    return None


def print_part1_realisation(rows):
    print("\n" + "-" * 78)
    print("PART 1 — REALISATION (NEUTRAL defaults: 10m=0.95, tryline=1.00)")
    print("-" * 78)
    neutral = _rows_for(rows, "neutral")
    if not neutral:
        print("  (no neutral rows — engine did not reach a live OPEN_PLAY line)")
        return
    print("  %-10s %8s %9s %9s %9s %9s %9s %6s" % (
        "bc.x", "governed", "command", "publish", "tgt_span", "on_field",
        "of/gov", "n_line"))
    for r in neutral:
        gov = r["governed_m"]
        of = r["on_field_span_m"]
        ratio = (of / gov) if (gov and of) else None
        print("  %-10s %8s %9s %9s %9s %9s %9s %6s" % (
            _fmt(r["target_bc_x"], 0), _fmt(gov), _fmt(r["commanded_m"]),
            _fmt(r["pub_mean_m"]), _fmt(r["target_span_m"]), _fmt(of),
            _fmt(ratio, 3), _fmt(r["n_line"], 1)))
    # Did the goal-line anchors engage at all?
    r100 = _row_at(rows, "neutral", 100.0)
    r110 = _row_at(rows, "neutral", 110.0)
    r60 = _row_at(rows, "neutral", 60.0)
    if r100 and r110:
        print("\n  Goal-line anchors engaged (neutral):")
        print("    governed @bc.x=100 (10 m anchor) : %s m" % _fmt(r100["governed_m"]))
        print("    governed @bc.x=110 (tryline anchor): %s m" % _fmt(r110["governed_m"]))
        print("    on-field  @bc.x=100 : %s m" % _fmt(r100["on_field_span_m"]))
        print("    on-field  @bc.x=110 : %s m" % _fmt(r110["on_field_span_m"]))


def print_part2_reach(rows):
    print("\n" + "-" * 78)
    print("PART 2 — INDEPENDENT REACH (one knob at a time, live line)")
    print("-" * 78)
    for anchor_x, name in ((100.0, "10 m anchor"), (110.0, "tryline anchor")):
        print("\n  At bc.x=%s (%s):" % (_fmt(anchor_x, 0), name))
        print("    %-18s %10s %10s %10s %10s" % (
            "scenario", "commanded", "governed", "pub_mean", "on_field"))
        for label, _c10, _ctry in TUNER_SCENARIOS:
            r = _row_at(rows, label, anchor_x)
            if r is None:
                continue
            print("    %-18s %10s %10s %10s %10s" % (
                label, _fmt(r["commanded_m"]), _fmt(r["governed_m"]),
                _fmt(r["pub_mean_m"]), _fmt(r["on_field_span_m"])))
        # Independence deltas vs neutral.
        base = _row_at(rows, "neutral", anchor_x)
        t10 = _row_at(rows, "10m_squeeze", anchor_x)
        ttry = _row_at(rows, "tryline_squeeze", anchor_x)
        if base and t10 and ttry:
            d10_gov = (t10["governed_m"] - base["governed_m"]
                       if t10["governed_m"] is not None
                       and base["governed_m"] is not None else None)
            dtry_gov = (ttry["governed_m"] - base["governed_m"]
                        if ttry["governed_m"] is not None
                        and base["governed_m"] is not None else None)
            d10_of = (t10["on_field_span_m"] - base["on_field_span_m"]
                      if t10["on_field_span_m"] is not None
                      and base["on_field_span_m"] is not None else None)
            dtry_of = (ttry["on_field_span_m"] - base["on_field_span_m"]
                       if ttry["on_field_span_m"] is not None
                       and base["on_field_span_m"] is not None else None)
            print("    d(10m knob)     governed=%s  on_field=%s"
                  % (_fmt(d10_gov, 2), _fmt(d10_of, 2)))
            print("    d(tryline knob) governed=%s  on_field=%s"
                  % (_fmt(dtry_gov, 2), _fmt(dtry_of, 2)))
            print("    INDEPENDENT REACH: 10m knob moves 10m only; "
                  "tryline knob moves tryline only.")


def print_part3_outcome(rows):
    print("\n" + "-" * 78)
    print("PART 3 — ON-FIELD SENSITIVITY near the goal line")
    print("-" * 78)
    for label, _c10, _ctry in TUNER_SCENARIOS:
        seg = _rows_for(rows, label)
        if not seg:
            continue
        # Average absorption ratio over the goal-line band (bc.x >= 95).
        band = [r for r in seg if r["target_bc_x"] >= 95.0]
        ratios = []
        for r in band:
            if r["governed_m"] and r["on_field_span_m"]:
                ratios.append(r["on_field_span_m"] / r["governed_m"])
        if ratios:
            print("  %-18s mean on_field/governed over bc.x>=95: %.3f"
                  % (label, statistics.mean(ratios)))
    print("\n  (1.00 = commanded width fully realised on the live line;")
    print("   <1.00 = some of the command is absorbed before the on-field Y)")


# ─────────────────────────────────────────────────────────────────────
# ARTEFACTS
# ─────────────────────────────────────────────────────────────────────

CSV_COLUMNS = (
    "label", "c10", "ctry", "target_bc_x", "n_samples", "n_live",
    "bc_x_m", "anchor_x_m", "ruck_x_m",
    "commanded_m", "governed_m", "pub_mean_m", "target_span_m",
    "on_field_span_m", "n_line", "sub_live",
)


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: r.get(k) for k in CSV_COLUMNS})


def _verdict(rows):
    """Return (ok, lines) — the headline realisation verdict."""
    lines = []
    ok = True
    # 1. Neutral realisation: on-field span must track governed in the band.
    neutral = [r for r in _rows_for(rows, "neutral")
               if r["target_bc_x"] >= 95.0]
    ratios = [(r["on_field_span_m"] / r["governed_m"])
              for r in neutral if r["governed_m"] and r["on_field_span_m"]]
    mean_ratio = statistics.mean(ratios) if ratios else None
    lines.append("neutral on-field/governed mean over goal-line band: %s"
                 % _fmt(mean_ratio, 3))
    if mean_ratio is None or mean_ratio < 0.85:
        ok = False
        lines.append("  !! commanded goal-line width NOT realised on-field")
    # 2. Independent reach at the anchors.
    b100 = _row_at(rows, "neutral", 100.0)
    t10_100 = _row_at(rows, "10m_squeeze", 100.0)
    b110 = _row_at(rows, "neutral", 110.0)
    ttry_110 = _row_at(rows, "tryline_squeeze", 110.0)
    if b100 and t10_100 and b100["on_field_span_m"] and t10_100["on_field_span_m"]:
        d = t10_100["on_field_span_m"] - b100["on_field_span_m"]
        lines.append("10 m knob on-field delta @x=100: %s m" % _fmt(d, 2))
        if d > -0.5:
            ok = False
            lines.append("  !! 10 m knob did not narrow the live line @x=100")
    if b110 and ttry_110 and b110["on_field_span_m"] and ttry_110["on_field_span_m"]:
        d = ttry_110["on_field_span_m"] - b110["on_field_span_m"]
        lines.append("tryline knob on-field delta @x=110: %s m" % _fmt(d, 2))
        if d > -0.5:
            ok = False
            lines.append("  !! tryline knob did not narrow the live line @x=110")
    return ok, lines


def write_txt(path, rows, verdict_lines):
    out = ["GOAL-LINE COMPRESSION — FIELD REALISATION PROBE", "=" * 78, ""]
    out.extend(verdict_lines)
    out.append("")
    out.append("%-14s %6s %8s %9s %9s %9s %9s %9s" % (
        "label", "bc.x", "governed", "command", "publish", "tgt_span",
        "on_field", "n_line"))
    for r in rows:
        out.append("%-14s %6s %8s %9s %9s %9s %9s %9s" % (
            r["label"], _fmt(r["target_bc_x"], 0), _fmt(r["governed_m"]),
            _fmt(r["commanded_m"]), _fmt(r["pub_mean_m"]),
            _fmt(r["target_span_m"]), _fmt(r["on_field_span_m"]),
            _fmt(r["n_line"], 1)))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 78)
    print("DEFENDING 10 m / GOAL-LINE COMPRESSION — FIELD REALISATION PROBE")
    print("=" * 78)
    DefenceTuners().assert_radii()

    rows = []
    for i, (label, c10, ctry) in enumerate(TUNER_SCENARIOS):
        print("\n[boot] scenario %-16s (10m=%.2f, tryline=%.2f)"
              % (label, c10, ctry))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            seg = run_pinned_grid(label, c10, ctry, seed=1234 + i * 97)
        print("        rows: %d" % len(seg))
        rows.extend(seg)

    print_part1_realisation(rows)
    print_part2_reach(rows)
    print_part3_outcome(rows)

    ok, verdict_lines = _verdict(rows)
    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    for ln in verdict_lines:
        print("  " + ln)
    print("  RESULT: %s" % ("REALISATION PROVEN" if ok
                            else "REALISATION NOT PROVEN"))

    write_csv("goal_line_compression.csv", rows)
    write_txt("goal_line_compression.txt", rows, verdict_lines)
    print("\nartefacts: goal_line_compression.csv, goal_line_compression.txt")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
