"""
probe_goal_line_outcomes.py — GOAL-LINE COMPRESSION A/B (OUTCOME PROXY) PROBE
(READ-ONLY).

QUESTION
--------
probe_goal_line_compression.py proved the FIELD REALISATION of the two
goal-line compression knobs (defending_10m_compression /
defending_tryline_compression) on the live line: the governed width, the
published width, the spacing targets and the realised on-field span all
track the command, and each knob moves its own anchor.  It did NOT answer
the OUTCOME question.

This probe answers it: with the attack pinned to a goal-line ruck (anchor
~ x=98), does SQUEEZING the defending line (10 m and/or tryline knob to
0.70) change the DEFENSIVE OUTCOME — measured as line breaks, tries
conceded, metres conceded and whether/when the carrier is stopped?

METHOD — a CONTROLLED A/B ON THE REAL ENGINE
--------------------------------------------
For each scenario (neutral / 10m_squeeze / tryline_squeeze / both_squeeze),
each seed, and each of TWO GOAL-LINE BANDS, the probe:

  1. Boots the live engine and advances to OPEN_PLAY with a formed line
     (same boot as probe_goal_line_compression.py).
  2. Sets a GOAL-LINE RUCK.  The two knobs scale DIFFERENT segments of the
     four-anchor width curve, so the ruck is set to put the contest inside
     each knob's own segment:
         band "10m_band"     ruck_x = 88 -> line anchor ~98  (10 m segment)
         band "tryline_band" ruck_x = 98 -> line anchor ~108 (tryline seg)
     ruck_y = 35 (field centre, so the split-side room is symmetric and
     does not confound the reading).
  3. Drives the BALL CARRIER along a fixed CHANNEL (constant Y) toward the
     defending tryline (x = 110) at a fixed attacking speed, while EVERY
     defensive subsystem (governor, width authority, split spacing,
     movement, tackle pipeline, TES) runs normally.  Only bc.x / bc.y are
     externally commanded — the same controlled-attack pattern the width
     probe uses, extended to the outcome.

The ONLY difference between scenarios is the two compression knobs, so any
outcome difference is attributable to the goal-line squeeze.

OUTCOME PROXIES (all single-sourced from the engine)
----------------------------------------------------
  * TACKLED   — the tackle pipeline armed (``world.tes_active``) before the
                carrier reached the tryline.
  * CONTACT_X — the carrier's x when the tackle first armed.
  * BREAK     — ``carrier_past_defensive_line`` (``bc.x > anchor_x + 1.0``)
                fired at any frame (the canonical line-break predicate).
  * TRY       — the carrier reached the defending tryline (x >= 110) without
                being stopped.
  * METRES    — furthest x reached − the carrier's start x (metres conceded
                before the phase ended).
  * CLOSEST   — the minimum distance from any defender to the carrier over
                the run (the continuous "did the line get to the ball"
                signal; smaller is better for the defence).

READ-ONLY against production.  No engine file is modified or written.

Run:  python probe_goal_line_outcomes.py
Out:  stdout A/B report + goal_line_outcomes.csv + goal_line_outcomes.txt
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
    DEFENDING_TRYLINE_X,
)
from game.defence.defence_lvl_1.anchor_authority import (
    reset_anchor_authority,
)

FPS = 60.0
DT = 1.0 / FPS

# ── THE CONTROLLED GOAL-LINE ATTACK ──────────────────────────────────
# The ruck sits at x=88 so the line anchor (ruck_x + 10) is ~98 and the
# carrier's whole run happens inside the defending-10m / goal-line band
# (bc.x ~ 92..110) where the two compression knobs act.
RUCK_X_START: float = 88.0
RUCK_Y_MID: float = 35.0
# The carrier starts BEHIND the ruck (a ball-carrier taking off from the
# base) and runs straight at the line.  Negative = behind the ruck.
BC_START_OFFSET_M: float = -7.0
# The carrier's attacking speed (m/s) while the line is still standing.
ATTACK_SPEED_MPS: float = 6.8
# Frames to advance the line before the attack begins.  The boot leaves the
# line near the kickoff (x~63) and the goal-line anchor is ~98, so the line
# needs several seconds to retreat onto the anchor; without this the carrier
# runs past a line that never set.  300 frames = 5 s.
PRE_ATTACK_FRAMES: int = 300
# Horizon of the phase (frames).  600 = 10 s, ample for 22 m of travel plus
# a break and a chase.
HORIZON_FRAMES: int = 600

# Channels (attacking lanes) as Y offsets from the ruck centre.  0 is the
# ruck channel; the wider offsets attack the line's flanks.
CHANNEL_OFFSETS: tuple = (0.0, -10.0, 10.0, -18.0, 18.0)

# THE TWO GOAL-LINE BANDS.  The two knobs scale DIFFERENT segments of the
# four-anchor curve: the 10 m knob the [60,100] segment and the tryline knob
# the [100,110] segment.  A ruck whose anchor sits at ~98 exercises ONLY the
# 10 m knob — the contest is resolved (contact ~x=87) before the carrier
# reaches x=100 — so the tryline knob can only be judged with the line set
# INSIDE its own segment: an anchor at ~108.
BANDS: tuple = (("10m_band", 88.0), ("tryline_band", 98.0))

# Seeds driven per scenario.
SEEDS: tuple = (11, 29, 47, 83)

# Tuner scenarios: (label, defending_10m_compression, defending_tryline_compression)
TUNER_SCENARIOS: tuple = (
    ("neutral",         0.95, 1.00),
    ("10m_squeeze",     0.70, 1.00),
    ("tryline_squeeze", 0.95, 0.70),
    ("both_squeeze",    0.70, 0.70),
)

# MATERIALITY FLOOR for an outcome claim.  A metres delta smaller than this
# is not a real effect at this sample size: the tryline knob moves metres by
# fractions of a centimetre through the goal-line window, and without a floor
# a sub-millimetre difference (e.g. -0.0001 m) would be reported as an
# "improvement".  Every claimed improvement must clear this.
MIN_EFFECT_M: float = 0.01

_ENGINE = {"mods": None}


# ─────────────────────────────────────────────────────────────────────
# ENGINE BOOT (mirrors probe_goal_line_compression.py)
# ─────────────────────────────────────────────────────────────────────

def _engine_imports():
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


def _seed_engine_rng(ope, seed: int) -> None:
    """Seed the ENGINE'S OWN RNG (``OpenPlayEngine.rng``).

    ``random.seed()`` only governs the module-global RNG (the kickoff draw).
    The engine keeps a PRIVATE ``random.Random`` whose draws drive the TES
    drive/dominance/skill rolls, so without this two identical runs of the
    same scenario differ in metres conceded and whether the carrier breaks
    the line — the A/B would then compare noise instead of the knobs.
    Reseeding it here makes the whole run (kickoff -> attack) reproducible.
    """
    rng = getattr(ope, "rng", None)
    if rng is not None and hasattr(rng, "seed"):
        try:
            rng.seed(int(seed))
        except (TypeError, ValueError):
            pass
    world_rng = getattr(getattr(ope, "world", None), "rng", None)
    if world_rng is not None and hasattr(world_rng, "seed"):
        try:
            world_rng.seed(int(seed))
        except (TypeError, ValueError):
            pass


def _step_full(world, ball, ope, kickoff_engine, collision, mods) -> None:
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


def _open_play_live(world, mods) -> bool:
    GamePhase = mods["GamePhase"]
    if world.phase != GamePhase.OPEN_PLAY:
        return False
    sub = getattr(world, "sub_state", None)
    sub_name = str(getattr(sub, "value", sub))
    return (sub_name == "open_play"
            and not bool(getattr(world, "in_pursuit_mode", False))
            and world.ball_carrier is not None)


def _advance_to_open_play(world, ball, ope, kickoff_engine, collision,
                          mods, cap: int = 1200) -> bool:
    formed = 0
    for _ in range(cap):
        _step_full(world, ball, ope, kickoff_engine, collision, mods)
        if _open_play_live(world, mods):
            formed += 1
            if formed >= 5:
                return True
    return False


def _anchor_x(world) -> float:
    a = getattr(world, "defensive_line_anchor", None)
    if a is not None:
        try:
            return float(a)
        except (TypeError, ValueError):
            pass
    return float(getattr(world, "ruck_x_m", 60.0)) + 10.0


def _line_break(world, bc) -> bool:
    """Canonical line-break predicate: bc.x > anchor_x + 1.0."""
    if bc is None:
        return False
    return float(getattr(bc, "x", 0.0)) > _anchor_x(world) + 1.0


def _closest_defender_gap(world, bc) -> float:
    """Minimum distance from any defender to the carrier this frame."""
    if bc is None:
        return float("inf")
    bx, by = float(bc.x), float(bc.y)
    best = float("inf")
    for d in (getattr(world, "defending_team", None) or []):
        if d is None:
            continue
        dx = float(getattr(d, "x", 0.0)) - bx
        dy = float(getattr(d, "y", 0.0)) - by
        gap = math.hypot(dx, dy)
        if gap < best:
            best = gap
    return best


def _place_line_at_goal_line(world, tuners, ruck_x: float,
                             ruck_y: float) -> int:
    """Put the defending LINE on its commanded goal-line lanes.

    The boot is a kickoff: the defending (kicking) side starts deep (x~55)
    and the carrier is teleported to the goal-line ruck, so without this the
    carrier would simply out-run a line that is 35 m away and every scenario
    would trivially concede — measuring the boot, not the width command.

    This places every line body on the GEOMETRY THE ENGINE ITSELF COMMANDS:
    the role depth-lane X (``ruck_x + WIDTH_OFFSETS[depth_role]``, capped at
    the anchor) and the pipeline's spacing Y.  Roles + spacing come from the
    engine's own ``run_defensive_pipeline``, so the A/B is unchanged: the
    ONLY difference between scenarios remains the two compression knobs,
    which reshape exactly these lanes.

    Returns the number of bodies placed.
    """
    from game.defence.defence_lvl_1.defensive_pipeline import (
        run_defensive_pipeline,
    )
    line = [d for d in (world.defending_team or [])
            if not getattr(d, "fullback_role_active", False)]
    if not line or tuners is None:
        return 0
    try:
        spacing, _width = run_defensive_pipeline(world, line, tuners)
    except Exception:                                    # noqa: BLE001
        spacing = {}
    offsets = getattr(tuners, "WIDTH_OFFSETS", None) or {}
    anchor = _anchor_x(world)
    placed = 0
    for d in line:
        role = getattr(d, "depth_role", None) or "D"
        offset = float(offsets.get(role, 0.0))
        tx = min(float(ruck_x) + offset, anchor)
        ty = float(spacing.get(id(d), getattr(d, "y", ruck_y)))
        try:
            d.x = tx
            d.y = ty
            d.target_x = tx
            d.target_y = ty
            d.vx = 0.0
            d.vy = 0.0
            d.frozen = False
            placed += 1
        except (TypeError, ValueError):
            continue
    return placed
# ─────────────────────────────────────────────────────────────────────
# THE CONTROLLED ATTACK RUN
# ─────────────────────────────────────────────────────────────────────

def _apply_scenario(tuners, c10: float, ctry: float) -> None:
    if tuners is None:
        return
    tuners.defending_10m_compression = float(c10)
    tuners.defending_tryline_compression = float(ctry)


def _position_carrier(world, x: float, y: float, ball) -> bool:
    bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return False
    try:
        bc.x = float(x)
        bc.y = float(y)
        bc.vx = 0.0
        bc.vy = 0.0
    except (TypeError, ValueError):
        return False
    if ball is not None:
        try:
            ball.true_x_m = float(x)
            ball.true_y_m = float(y)
        except (TypeError, ValueError):
            pass
    return True


def run_one(label: str, c10: float, ctry: float, seed: int,
            channel: float, ruck_x: float = RUCK_X_START,
            band: str = "10m_band") -> dict:
    """One controlled goal-line attack on the live engine; return outcomes.

    *ruck_x* selects the goal-line BAND: 88 puts the line anchor at ~98 (the
    10 m segment), 98 puts it at ~108 (the tryline segment, where the
    tryline knob actually bites).
    """
    mods = _engine_imports()
    random.seed(seed)
    world, ball, ope, kickoff_engine, collision = _boot_world(mods)
    # Determinism: seed BOTH the global RNG (kickoff draw) and the engine's
    # private RNG (TES drive/dominance rolls) before any frame is stepped.
    _seed_engine_rng(ope, seed)
    tuners = getattr(ope, "def_tuners", None)
    _apply_scenario(tuners, c10, ctry)

    result = {
        "band": band, "label": label, "c10": float(c10), "ctry": float(ctry),
        "seed": int(seed), "channel": float(channel),
        "booted": False, "tackled": False, "contact_x": None,
        "contact_frame": None, "broke": False, "try": False,
        "max_bc_x": None, "metres": None, "closest_m": None,
        "governed_at_start": None, "governed_at_break": None,
    }
    if not _advance_to_open_play(world, ball, ope, kickoff_engine,
                                 collision, mods):
        return result
    result["booted"] = True
    if getattr(world, "ball_carrier", None) is None:
        return result

    # ── SET THE GOAL-LINE RUCK (a ruck transition, so unlock the anchor) ──
    world.ruck_x_m = ruck_x
    world.ruck_y_m = RUCK_Y_MID
    try:
        reset_anchor_authority(world)
    except Exception:                                    # noqa: BLE001
        pass

    bc_y = max(4.0, min(66.0, RUCK_Y_MID + float(channel)))
    start_x = ruck_x + BC_START_OFFSET_M

    # ── PRE-ATTACK: let the line form at the new anchor ──
    for _ in range(PRE_ATTACK_FRAMES):
        _position_carrier(world, start_x, bc_y, ball)
        _step_full(world, ball, ope, kickoff_engine, collision, mods)

    # ── PLACE THE LINE ON ITS GOAL-LINE LANES ──
    # The boot is a kickoff, so the defending side starts ~35 m upfield of
    # the goal-line ruck; without this the carrier out-runs it and every
    # scenario trivially concedes.  Place the line on the geometry the
    # engine itself commands (role depth-lane X + pipeline spacing Y) so the
    # ONLY difference between scenarios remains the two compression knobs.
    placed = _place_line_at_goal_line(world, tuners, ruck_x, RUCK_Y_MID)
    result["line_placed"] = placed
    for _ in range(10):
        _position_carrier(world, start_x, bc_y, ball)
        _step_full(world, ball, ope, kickoff_engine, collision, mods)

    bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return result
    commanded0 = (float(interpolated_defensive_width(tuners, start_x))
                  if tuners is not None else None)
    result["governed_at_start"] = commanded0

    # ── THE ATTACK ──
    bc_x = start_x
    max_x = start_x
    closest = float("inf")
    broke = False
    tackled = False
    contact_x = None
    contact_frame = None
    governed_at_break = None

    for frame in range(HORIZON_FRAMES):
        if not tackled:
            bc_x = min(DEFENDING_TRYLINE_X + 2.0,
                       bc_x + ATTACK_SPEED_MPS * DT)
            _position_carrier(world, bc_x, bc_y, ball)
        _step_full(world, ball, ope, kickoff_engine, collision, mods)

        bc = getattr(world, "ball_carrier", None)
        if bc is None:
            break
        cur_x = float(getattr(bc, "x", bc_x))
        if cur_x > max_x:
            max_x = cur_x
        gap = _closest_defender_gap(world, bc)
        if gap < closest:
            closest = gap

        if _line_break(world, bc) and not broke:
            broke = True
            if tuners is not None:
                governed_at_break = float(
                    interpolated_defensive_width(tuners, cur_x))
        if bool(getattr(world, "tes_active", False)) and not tackled:
            tackled = True
            contact_x = cur_x
            contact_frame = frame
        if cur_x >= DEFENDING_TRYLINE_X:
            break

    result.update({
        "tackled": tackled,
        "contact_x": contact_x,
        "contact_frame": contact_frame,
        "broke": broke,
        "try": bool(max_x >= DEFENDING_TRYLINE_X),
        "max_bc_x": max_x,
        "metres": max_x - start_x,
        "closest_m": (closest if closest != float("inf") else None),
        "governed_at_break": governed_at_break,
    })
    return result


# ─────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────

def _fmt(v, nd=2):
    return "-" if v is None else ("%.*f" % (nd, float(v)))


def _rate(runs, key) -> float:
    if not runs:
        return 0.0
    return sum(1 for r in runs if r.get(key)) / float(len(runs))


def _mean_key(runs, key):
    vals = [float(r[key]) for r in runs if r.get(key) is not None]
    return float(statistics.mean(vals)) if vals else None


def _aggregate(results, label, band):
    runs = [r for r in results
            if r["label"] == label and r.get("band") == band
            and r.get("booted")]
    return {
        "label": label,
        "n": len(runs),
        "tackle_rate": _rate(runs, "tackled"),
        "try_rate": _rate(runs, "try"),
        "break_rate": _rate(runs, "broke"),
        "mean_metres": _mean_key(runs, "metres"),
        "mean_closest": _mean_key(runs, "closest_m"),
        "mean_contact_x": _mean_key(runs, "contact_x"),
        "runs": runs,
    }


def print_ab(results, band):
    print("\n" + "-" * 96)
    print("GOAL-LINE COMPRESSION A/B — DEFENSIVE OUTCOMES  [band=%s]"
          "  (%d seeds x %d channels per scenario)"
          % (band, len(SEEDS), len(CHANNEL_OFFSETS)))
    print("-" * 96)
    print("  %-16s %5s %9s %8s %8s %11s %11s %12s" % (
        "scenario", "n", "tackled%", "try%", "break%", "mean_metres",
        "mean_closest", "mean_contact"))
    agg = {}
    for label, _c10, _ctry in TUNER_SCENARIOS:
        a = _aggregate(results, label, band)
        agg[label] = a
        print("  %-16s %5d %8.1f%% %7.1f%% %7.1f%% %11s %11s %12s" % (
            label, a["n"], 100.0 * a["tackle_rate"],
            100.0 * a["try_rate"], 100.0 * a["break_rate"],
            _fmt(a["mean_metres"]), _fmt(a["mean_closest"]),
            _fmt(a["mean_contact_x"])))
    print("\n  (tackled% = tackle pipeline armed before the tryline; "
          "try% = carrier reached x=110;")
    print("   break% = canonical line break fired; mean_metres = metres "
          "conceded to furthest x;")
    print("   mean_closest = closest any defender got to the carrier, "
          "smaller is better;")
    print("   mean_contact = carrier x when the tackle first armed.)")
    return agg


def print_verdict(agg):
    base = agg.get("neutral")
    lines = []
    if not base or base["n"] == 0:
        lines.append("no neutral baseline — cannot compare")
        return lines, False
    lines.append("baseline (neutral): try%%=%.1f%% break%%=%.1f%% "
                 "mean_metres=%s mean_closest=%s"
                 % (100.0 * base["try_rate"], 100.0 * base["break_rate"],
                    _fmt(base["mean_metres"]), _fmt(base["mean_closest"])))
    improves = 0
    for label in ("10m_squeeze", "tryline_squeeze", "both_squeeze"):
        a = agg.get(label)
        if not a or a["n"] == 0:
            continue
        d_try = a["try_rate"] - base["try_rate"]
        d_break = a["break_rate"] - base["break_rate"]
        d_metres = ((a["mean_metres"] - base["mean_metres"])
                    if a["mean_metres"] is not None
                    and base["mean_metres"] is not None else None)
        d_closest = ((a["mean_closest"] - base["mean_closest"])
                     if a["mean_closest"] is not None
                     and base["mean_closest"] is not None else None)
        # A ROBUST improvement must not trade one defensive outcome for
        # another: the squeeze has to give up no ground on try% or break%
        # while conceding fewer metres BY AT LEAST MIN_EFFECT_M.  (An earlier,
        # looser test counted a metres-only gain even when the break rate
        # rose, and a 1e-9 threshold let a sub-millimetre shift — far below
        # any real effect at this sample size — register as an improvement.)
        no_worse = (d_try <= 1e-9) and (d_break <= 1e-9)
        material = (d_metres is not None and d_metres < -MIN_EFFECT_M)
        better = no_worse and (d_try < -1e-9 or material)
        if better:
            improves += 1
        caveat = ""
        if better and d_closest is not None and d_closest > 1e-9:
            caveat = "  [closest worse: defenders finish further out]"
        note = ""
        if (not better and d_metres is not None
                and abs(d_metres) < MIN_EFFECT_M
                and no_worse and d_try >= -1e-9):
            note = "  [below materiality floor %.0f mm]" % (
                1000.0 * MIN_EFFECT_M)
        lines.append("%-16s dtry=%+.1f%% dbreak=%+.1f%% dmetres=%s "
                     "dclosest=%s %s%s%s"
                     % (label, 100.0 * d_try, 100.0 * d_break,
                        _fmt(d_metres), _fmt(d_closest),
                        ("IMPROVES" if better else "no improvement"),
                        caveat, note))
    return lines, (improves > 0)


# ─────────────────────────────────────────────────────────────────────
# ARTEFACTS
# ─────────────────────────────────────────────────────────────────────

CSV_COLUMNS = (
    "band", "label", "c10", "ctry", "seed", "channel", "booted", "tackled",
    "contact_x", "contact_frame", "broke", "try", "max_bc_x", "metres",
    "closest_m", "governed_at_start", "governed_at_break",
)


def write_csv(path, results):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        wr.writeheader()
        for r in results:
            wr.writerow({k: r.get(k) for k in CSV_COLUMNS})


def write_txt(path, band, agg, verdict_lines):
    out = ["GOAL-LINE COMPRESSION A/B — OUTCOME PROXY PROBE  [band=%s]"
           % band, "=" * 78, ""]
    out.append("scenario          n    tackled%  try%   break%  "
               "mean_metres  mean_closest  mean_contact")
    for label, _c10, _ctry in TUNER_SCENARIOS:
        a = agg.get(label)
        if not a:
            continue
        out.append("%-16s %4d  %6.1f%%  %5.1f%%  %5.1f%%  %11s  %12s  %12s"
                   % (label, a["n"], 100.0 * a["tackle_rate"],
                      100.0 * a["try_rate"], 100.0 * a["break_rate"],
                      _fmt(a["mean_metres"]), _fmt(a["mean_closest"]),
                      _fmt(a["mean_contact_x"])))
    out.append("")
    out.extend(verdict_lines)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


def load_csv(path):
    """Re-read a previous run's CSV so the report can be re-derived WITHOUT
    re-booting the engine (``--report-only``).  Types are restored so the
    loaded rows are drop-in compatible with ``_aggregate``/``print_verdict``.
    """
    bool_cols = ("booted", "tackled", "broke", "try")
    float_cols = ("c10", "ctry", "channel", "contact_x", "contact_frame",
                  "max_bc_x", "metres", "closest_m", "governed_at_start",
                  "governed_at_break")
    results = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            r = dict(row)
            r["seed"] = int(r["seed"])
            for k in bool_cols:
                r[k] = (r[k] == "True")
            for k in float_cols:
                v = r.get(k)
                r[k] = None if v in (None, "", "None") else float(v)
            results.append(r)
    return results


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    report_only = "--report-only" in sys.argv[1:]
    print("=" * 96)
    print("GOAL-LINE COMPRESSION — A/B OUTCOME PROXY PROBE%s"
          % ("  [report-only: re-deriving from CSV]" if report_only else ""))
    print("=" * 96)
    DefenceTuners().assert_radii()

    if report_only:
        # Re-derive the tables/verdict from the committed CSV.  The run is
        # deterministic, so this is equivalent to a fresh A/B — it just skips
        # the ~11 min of engine boots.
        results = load_csv("goal_line_outcomes.csv")
    else:
        results = []
        per_run = len(TUNER_SCENARIOS) * len(SEEDS) * len(CHANNEL_OFFSETS)
        total = per_run * len(BANDS)
        done = 0
        for band, ruck_x in BANDS:
            for label, c10, ctry in TUNER_SCENARIOS:
                for seed in SEEDS:
                    for channel in CHANNEL_OFFSETS:
                        buf = io.StringIO()
                        with contextlib.redirect_stdout(buf):
                            r = run_one(label, c10, ctry, seed, channel,
                                        ruck_x=ruck_x, band=band)
                        results.append(r)
                        done += 1
                print("[run] %-9s %-16s complete (%d/%d runs)"
                      % (band, label, done, total))

    summaries = {}
    overall_any = False
    for band, ruck_x in BANDS:
        agg = print_ab(results, band)
        verdict_lines, any_improvement = print_verdict(agg)
        summaries[band] = (agg, verdict_lines, any_improvement)
        overall_any = overall_any or any_improvement

    print("\n" + "=" * 96)
    print("VERDICT")
    print("=" * 96)
    for band, _ruck_x in BANDS:
        agg, verdict_lines, any_improvement = summaries[band]
        print("\n  [band=%s]" % band)
        for ln in verdict_lines:
            print("    " + ln)
        print("    band result: %s"
              % ("SQUEEZE IMPROVES THE OUTCOME PROXY" if any_improvement
                 else "no outcome improvement from squeezing"))
    print("\n  OVERALL: %s" % (
        "AT LEAST ONE BAND SHOWS SQUEEZE IMPROVING THE OUTCOME PROXY"
        if overall_any else
        "NO OUTCOME IMPROVEMENT FROM SQUEEZING — defaults stay NEUTRAL"))

    write_csv("goal_line_outcomes.csv", results)
    for band, _ruck_x in BANDS:
        agg, verdict_lines, _imp = summaries[band]
        write_txt("goal_line_outcomes_%s.txt" % band, band, agg,
                  verdict_lines)
    # Keep the single-file form too (10 m band = the primary reading).
    write_txt("goal_line_outcomes.txt", BANDS[0][0],
              summaries[BANDS[0][0]][0], summaries[BANDS[0][0]][1])
    print("\nartefacts: goal_line_outcomes.csv, goal_line_outcomes.txt, "
          + ", ".join("goal_line_outcomes_%s.txt" % b for b, _r in BANDS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
