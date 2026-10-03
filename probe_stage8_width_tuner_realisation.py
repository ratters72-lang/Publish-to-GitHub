"""
probe_stage8_width_tuner_realisation.py — STAGE 8
WIDTH TUNER REALISATION AUDIT (READ-ONLY).

QUESTION
--------
The canonical 3-point width tuners

    width_left_tryline_m / width_halfway_m / width_right_tryline_m

own the lateral span (Stage 6 proved ownership, publication, consumption).
The FIELD FINDING is that LARGE tuner changes produce LITTLE OR NO visible
change in the on-field defensive line.  This probe determines whether the
tuners materially affect REALISED defensive-line geometry, and if the
on-field line under-responds, names the rung that absorbs the signal.

THE RUNG CHAIN (each rung is a place the signal can be lost)
------------------------------------------------------------
  1 COMMANDED  interpolated_defensive_width(t, bc.x)
  2 GOVERNED   governed_width_m (clamp [min_def,max_def] + field cap)
  3 PUBLISHED  formation_width_authority {left_m,right_m} (+ named reducers)
  4 LANE       side_lanes(...)[-1] per side (span-derived; lane floor 3.0)
  5 SPACING    span of compute_targets -> frame_spacing_targets
  6 ON-FIELD   max(d.y)-min(d.y) over line bodies the engine integrated

PARTS
-----
  A  DETERMINISTIC RUNG TABLE — pinned scenarios x tuner sweep; rungs 1-5.
  B  GOVERNOR LIVE PUBLISH — does world.def_line_width_m reach the tuner
     command under the per-frame 1.0 m delta clamp, over a bc.x drive?
  C  LIVE IN-PLACE A/B — on ONE real engine world, snapshot the line,
     edit ONLY the tuner, run K frames, snapshot again.  Immune to the
     documented run-to-run non-determinism because both arms share the
     same world up to the edit.

READ-ONLY against production.  No engine file is modified or written.

Run:  python probe_stage8_width_tuner_realisation.py
Out:  stdout report + stage8_rung_table.csv + stage8_live_ab.csv
      + stage8_summary.txt
"""

import contextlib
import csv
import io
import math
import os
import random
import statistics
import sys
import types

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from world.world_state import GameSubState
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    interpolated_defensive_width,
    governed_width_m,
)
from game.defence.defence_lvl_1.defensive_line_governor import (
    DefensiveLineGovernor,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets,
    side_lanes,
    side_widths,
    lane_gap_m,
    ruck_adjacent_anchor,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.formation_width_authority import (
    publish_formation_width,
    formation_width_value,
    lane_step_floor_binds,
)
from game.defence.defence_lvl_1 import formation_width_authority as fwa
from game.defence.defence_lvl_1 import population_authority as pa

# ─────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────
RUCK_X = 60.0
FPS = 60.0
DT = 1.0 / FPS

# The tuner sets exercised.  Each is the full 3-point curve
# (width_left_tryline_m, width_halfway_m, width_right_tryline_m).
TUNER_SETS = (
    ("extreme_narrow", (18.0, 20.0, 22.0)),
    ("narrow",         (30.0, 33.0, 36.0)),
    ("default",        (50.0, 55.0, 60.0)),
    ("wide",           (80.0, 88.0, 96.0)),
    ("extreme_wide",   (130.0, 143.0, 156.0)),
)

# Scenario grid.
BC_X_GRID = (10.0, 30.0, 50.0, 60.0, 70.0, 90.0, 110.0)
RUCK_Y_GRID = (15.0, 27.5, 35.0, 42.5, 55.0)
SPLIT_GRID = ((3, 3), (4, 4), (5, 5), (6, 5))

_SIDE_ROLES = ("A", "B", "C", "D", "CENTRE", "WINGER")

# Part C — live engine.
MAX_FRAMES = 2100
LIVE_RUNS = 3
LIVE_AB_WINDOW = 60          # frames each side of an edit for the A/B mean
LIVE_EDIT_FRAMES = (600, 1100, 1600)
LIVE_EDIT_SETS = ("wide", "narrow", "default")
SMALL_SPAN_EPS = 0.01        # m — below this a side has no realised span


# ─────────────────────────────────────────────────────────────────────
# STUB WORLD + LINE BUILDERS
# ─────────────────────────────────────────────────────────────────────

class StubCarrier:
    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)
        self.vx = 0.0
        self.vy = 0.0
        self.in_contact = False


class RungWorld:
    """Minimal world carrying only what the width chain reads."""

    def __init__(self, ruck_y, phase="OPEN_PLAY", commanded=55.0):
        self.ruck_x_m = RUCK_X
        self.ruck_y_m = float(ruck_y)
        self.def_line_width_m = float(commanded)
        self.governor_line_width_m = float(commanded)
        self.governor_width_active = True
        self.frame_count = 1
        self.dt = DT
        self.ball_carrier = None
        self.defending_team = []
        self.attacking_team = []
        self.players = []
        self.in_pursuit_mode = False
        self.sub_state = GameSubState.OPEN_PLAY
        self._ope_phase_label = phase
        self.defensive_line_anchor = RUCK_X + 10.0
        self.anchor_locked = False
        self.ruck_established = True
        self._last_count_split = None
        self.frame_geometry_ready = False
        self.frame_geometry_frame = None
        self.frame_spacing_targets = None
        self.frame_width_targets = None
        self.frame_pin_targets = None
        self.frame_side_frame = None
        self.line_population = None
        self.line_population_ids = None
        self.published_count_left = None
        self.published_count_right = None


def make_line(n_left, n_right, ruck_y, *, base_pid=100, x=70.0):
    """Build a line of real Players with depth_role/depth_side pre-stamped.

    Sides are stamped explicitly so the count partition is exact and
    independent of the physical Y spread.
    """
    line = []
    pid = base_pid
    for i in range(n_left):
        pid += 1
        d = Player(x=x, y=ruck_y - 3.0 * (i + 1), player_id=pid)
        d.depth_role = _SIDE_ROLES[min(i, len(_SIDE_ROLES) - 1)]
        d.depth_side = LEFT
        d.is_defender = True
        d.fullback_role_active = False
        d.tackle_state_active = False
        line.append(d)
    for i in range(n_right):
        pid += 1
        d = Player(x=x, y=ruck_y + 3.0 * (i + 1), player_id=pid)
        d.depth_role = _SIDE_ROLES[min(i, len(_SIDE_ROLES) - 1)]
        d.depth_side = RIGHT
        d.is_defender = True
        d.fullback_role_active = False
        d.tackle_state_active = False
        line.append(d)
    return line


def make_tuners(tuner_vals):
    t = DefenceTuners()
    t.width_left_tryline_m = float(tuner_vals[0])
    t.width_halfway_m = float(tuner_vals[1])
    t.width_right_tryline_m = float(tuner_vals[2])
    return t


def _span_of(targets):
    ys = list(targets.values())
    if len(ys) < 2:
        return 0.0
    return float(max(ys)) - float(min(ys))


def _outer_lane(n_side, ruck_y, side, tuners, span):
    if n_side is None or int(n_side) <= 0:
        return None
    lanes = side_lanes(int(n_side), ruck_y, side, tuners, span=span)
    return float(lanes[-1]) if lanes else None


# ─────────────────────────────────────────────────────────────────────
# PART A — DETERMINISTIC RUNG TABLE
# ─────────────────────────────────────────────────────────────────────

def measure_rung_row(phase, bc_x, ruck_y, n_left, n_right, label, tuner_vals):
    """Measure rungs 1-5 for one (scenario, tuner) pair."""
    t = make_tuners(tuner_vals)
    world = RungWorld(ruck_y, phase, 0.0)
    world.frame_count = 1
    world.ball_carrier = StubCarrier(bc_x, ruck_y)
    line = make_line(n_left, n_right, ruck_y)
    world.defending_team = list(line)

    # Rung 1 — commanded.
    commanded = interpolated_defensive_width(t, bc_x)

    # Model the governor having published the interpolated command.
    world.def_line_width_m = commanded
    world.governor_line_width_m = commanded
    world.governor_width_active = True

    # Rung 2 — governed.
    governed = governed_width_m(world, t)

    # Rung 3 — published per-side span (+ named reducers).
    pub = publish_formation_width(world, t)
    left_m = float(pub["left_m"])
    right_m = float(pub["right_m"])
    reducers = dict(pub.get("reducers") or {})

    # Rung 4 — lane ladder (outermost lane per side), + floor/room flags.
    lw, rw = side_widths(world, t, ruck_y=ruck_y)
    outer_l = _outer_lane(n_left, ruck_y, LEFT, t, lw)
    outer_r = _outer_lane(n_right, ruck_y, RIGHT, t, rw)
    lane_span = (0.0 if outer_l is None or outer_r is None
                 else float(outer_r) - float(outer_l))
    floor_l = lane_step_floor_binds(pub, LEFT, n_left, t)
    floor_r = lane_step_floor_binds(pub, RIGHT, n_right, t)
    gap_l = lane_gap_m(lw, n_left, t)
    gap_r = lane_gap_m(rw, n_right, t)

    # Rung 5 — spacing target span (compute_targets).
    targets = compute_targets(world, line, t, governor=None)
    spacing_span = _span_of(targets)

    return {
        "scenario": f"{phase} bc.x={bc_x} ruck_y={ruck_y} {n_left}-{n_right}",
        "phase": phase,
        "bc_x": float(bc_x),
        "ruck_y": float(ruck_y),
        "n_left": int(n_left),
        "n_right": int(n_right),
        "tuner": label,
        "tuner_l": float(tuner_vals[0]),
        "tuner_m": float(tuner_vals[1]),
        "tuner_r": float(tuner_vals[2]),
        "r1_commanded_m": float(commanded),
        "r2_governed_m": float(governed),
        "r3_left_m": left_m,
        "r3_right_m": right_m,
        "r3_published_mean_m": 0.5 * (left_m + right_m),
        "r4_lane_span_m": lane_span,
        "r4_outer_left": outer_l if outer_l is not None else float("nan"),
        "r4_outer_right": outer_r if outer_r is not None else float("nan"),
        "r4_gap_left_m": float(gap_l),
        "r4_gap_right_m": float(gap_r),
        "r4_floor_left": bool(floor_l),
        "r4_floor_right": bool(floor_r),
        "r5_spacing_span_m": float(spacing_span),
        "reducers": ";".join(
            f"{k}={v:+.3f}" for k, v in sorted(reducers.items())
        ),
    }


def run_part_a():
    """Return the full rung table (list of dict rows)."""
    rows = []
    for phase in ("OPEN_PLAY",):
        for bc_x in BC_X_GRID:
            for ruck_y in RUCK_Y_GRID:
                for n_left, n_right in SPLIT_GRID:
                    for label, vals in TUNER_SETS:
                        rows.append(measure_rung_row(
                            phase, bc_x, ruck_y, n_left, n_right,
                            label, vals,
                        ))
    return rows


def print_part_a(rows):
    print("\n" + "-" * 78)
    print("PART A — DETERMINISTIC RUNG TABLE (OPEN_PLAY, pinned scenarios)")
    print("-" * 78)
    # Headline: a centred ruck, symmetric 4-4 line, bc.x at halfway.
    print("  Headline rung table — bc.x=60 (halfway), ruck_y=35, 4-4 line")
    print("    %-14s %8s %8s %8s %8s %8s" % (
        "tuner", "command", "governed", "pub_mean", "lane", "spacing"))
    for label, _vals in TUNER_SETS:
        row = next(
            (r for r in rows
             if r["tuner"] == label and r["bc_x"] == 60.0
             and r["ruck_y"] == 35.0 and r["n_left"] == 4
             and r["n_right"] == 4),
            None,
        )
        if row is None:
            continue
        print("    %-14s %8.2f %8.2f %8.2f %8.2f %8.2f" % (
            label, row["r1_commanded_m"], row["r2_governed_m"],
            row["r3_published_mean_m"], row["r4_lane_span_m"],
            row["r5_spacing_span_m"],
        ))

    # Sensitivity: d(spacing)/d(tuner_m) at the headline scenario.
    base = next(
        (r for r in rows if r["tuner"] == "default" and r["bc_x"] == 60.0
         and r["ruck_y"] == 35.0 and r["n_left"] == 4 and r["n_right"] == 4),
        None,
    )
    wide = next(
        (r for r in rows if r["tuner"] == "wide" and r["bc_x"] == 60.0
         and r["ruck_y"] == 35.0 and r["n_left"] == 4 and r["n_right"] == 4),
        None,
    )
    if base and wide:
        d_cmd = wide["r1_commanded_m"] - base["r1_commanded_m"]
        d_gov = wide["r2_governed_m"] - base["r2_governed_m"]
        d_pub = wide["r3_published_mean_m"] - base["r3_published_mean_m"]
        d_lane = wide["r4_lane_span_m"] - base["r4_lane_span_m"]
        d_spc = wide["r5_spacing_span_m"] - base["r5_spacing_span_m"]
        print("\n  Sensitivity default -> wide (bc.x=60, ruck_y=35, 4-4):")
        print("    d(commanded)  = %+.3f m" % d_cmd)
        print("    d(governed)   = %+.3f m" % d_gov)
        print("    d(published)  = %+.3f m" % d_pub)
        print("    d(lane)       = %+.3f m" % d_lane)
        print("    d(spacing)    = %+.3f m" % d_spc)
        for name, d, ref in (
            ("commanded", d_cmd, d_cmd or 1.0),
            ("governed", d_gov, d_cmd or 1.0),
            ("published", d_pub, d_cmd or 1.0),
            ("lane", d_lane, d_cmd or 1.0),
            ("spacing", d_spc, d_cmd or 1.0),
        ):
            ratio = 100.0 * d / ref if ref else 0.0
            print("      %-10s retains %6.1f%% of the command delta"
                  % (name, ratio))

    # Absorber census across the whole table.
    n_rows = len(rows)
    floor_rows = sum(1 for r in rows if r["r4_floor_left"] or r["r4_floor_right"])
    # saturation: commanded not equal to governed
    sat_rows = sum(
        1 for r in rows
        if abs(r["r1_commanded_m"] - r["r2_governed_m"]) > 1e-6
    )
    # short-side split attenuation: published mean < governed
    split_rows = sum(
        1 for r in rows
        if r["r3_published_mean_m"] < r["r2_governed_m"] - 1e-6
    )
    print("\n  Absorber census over %d rows:" % n_rows)
    print("    lane-step floor binds (either side) : %d (%.1f%%)"
          % (floor_rows, 100.0 * floor_rows / max(1, n_rows)))
    print("    governed != commanded (clamp)      : %d (%.1f%%)"
          % (sat_rows, 100.0 * sat_rows / max(1, n_rows)))
    print("    published mean < governed (split/room): %d (%.1f%%)"
          % (split_rows, 100.0 * split_rows / max(1, n_rows)))


# ─────────────────────────────────────────────────────────────────────
# PART B — GOVERNOR LIVE PUBLISH (rate clamp / convergence)
# ─────────────────────────────────────────────────────────────────────

def run_part_b():
    """Drive the governor across a bc.x drive; measure the published width.

    Returns {label: {frames_to_99, final_published, commanded_final,
                     max_step, converged}}.
    """
    out = {}
    for label, vals in TUNER_SETS:
        t = make_tuners(vals)
        world = RungWorld(35.0, "OPEN_PLAY", 55.0)
        gov = DefensiveLineGovernor(t, None)
        line = make_line(4, 4, 35.0)
        world.defending_team = list(line)
        # Drive bc.x from 20 m to 100 m over ~200 frames (0.4 m/frame).
        bc = StubCarrier(20.0, 35.0)
        world.ball_carrier = bc
        published = []
        frame = 0
        bc.x = 20.0
        while bc.x <= 100.0 and frame < 400:
            frame += 1
            world.frame_count = frame
            gov.compute_line_state(world, line)
            published.append(float(getattr(world, "def_line_width_m", 0.0)))
            bc.x += 0.4
        commanded_final = interpolated_defensive_width(t, bc.x)
        final_pub = published[-1] if published else 0.0
        target = commanded_final
        # frames to reach within 0.1 m of the FIXED-command target
        cmd_traj = [
            interpolated_defensive_width(t, 20.0 + 0.4 * (i + 1))
            for i in range(len(published))
        ]
        reach = None
        for i, p in enumerate(published):
            if abs(p - cmd_traj[i]) <= 0.1:
                reach = i + 1
                break
        max_step = 0.0
        for a, b in zip(published, published[1:]):
            max_step = max(max_step, abs(b - a))
        out[label] = {
            "frames_to_within_0p1": reach,
            "final_published_m": final_pub,
            "commanded_final_m": commanded_final,
            "max_step_m": max_step,
            "converged": reach is not None,
        }
    return out


def print_part_b(res):
    print("\n" + "-" * 78)
    print("PART B — GOVERNOR LIVE PUBLISH (per-frame width delta clamp)")
    print("-" * 78)
    print("    %-14s %12s %12s %12s %10s" % (
        "tuner", "cmd_final", "pub_final", "frames->0.1", "max_step"))
    for label, _v in TUNER_SETS:
        r = res[label]
        reach = r["frames_to_within_0p1"]
        print("    %-14s %12.2f %12.2f %12s %10.3f" % (
            label, r["commanded_final_m"], r["final_published_m"],
            (str(reach) if reach is not None else "NEVER"),
            r["max_step_m"]))
    print("    note: clamp = line_width_max_delta_per_frame_m (1.0 m/frame);")
    print("          a value that NEVER converges is a value absorber.")
# ─────────────────────────────────────────────────────────────────────
# PART C — LIVE IN-PLACE A/B (the definitive on-field measurement)
# ─────────────────────────────────────────────────────────────────────

_ENGINE = {"mods": None}


def _engine_imports():
    """Import the real engine headless (mirrors the Stage-4/5/7 bootstrap)."""
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


def _live_eligible(world):
    """True in the window where the width channel is actually live."""
    sub = getattr(world, "sub_state", None)
    sub_name = str(getattr(sub, "value", sub))
    if sub_name != "open_play":
        return False
    if getattr(world, "in_pursuit_mode", False):
        return False
    return getattr(world, "ball_carrier", None) is not None


def _live_line_span(world):
    """Realised span of the line population (max(y) - min(y))."""
    bodies = pa.line_population(getattr(world, "defending_team", None) or [])
    ys = [float(getattr(d, "y", 0.0)) for d in bodies]
    if len(ys) < 2:
        return 0.0, len(ys), bodies
    return max(ys) - min(ys), len(ys), bodies


def _live_record(world, ope):
    """One per-frame compact snapshot of the live width state + line span."""
    span, n_line, bodies = _live_line_span(world)
    pub = getattr(world, "formation_width", None)
    commanded = governed = left_m = right_m = None
    if (isinstance(pub, dict)
            and pub.get("frame") == getattr(world, "frame_count", None)):
        commanded = pub.get("commanded_m")
        governed = pub.get("governed_m")
        left_m = pub.get("left_m")
        right_m = pub.get("right_m")
    # spacing-owned count: line bodies whose target_y equals the published
    # frame-spacing Y (the width writer that actually realised).
    fst = getattr(world, "frame_spacing_targets", None) or {}
    owned = 0
    _tys = []
    _owned_ys = []
    _other_ys = []
    _src = {}
    for d in bodies:
        sy = fst.get(id(d))
        ty = getattr(d, "target_y", None)
        _s = getattr(d, "target_source", None) or "none"
        _src[_s] = _src.get(_s, 0) + 1
        if sy is not None:
            _tys.append(float(sy))
        if sy is not None and ty is not None:
            try:
                if abs(float(ty) - float(sy)) < 0.5:
                    owned += 1
                    _owned_ys.append(float(getattr(d, "y", 0.0)))
                else:
                    _other_ys.append(float(getattr(d, "y", 0.0)))
            except (TypeError, ValueError):
                pass
    target_span = (max(_tys) - min(_tys)) if len(_tys) >= 2 else 0.0
    owned_span = (max(_owned_ys) - min(_owned_ys)) if len(_owned_ys) >= 2 else 0.0
    other_span = (max(_other_ys) - min(_other_ys)) if len(_other_ys) >= 2 else 0.0
    _src_top = ";".join(
        "%s=%d" % (k, v) for k, v in
        sorted(_src.items(), key=lambda kv: -kv[1])[:4]
    )
    bc = getattr(world, "ball_carrier", None)
    return {
        "frame": int(getattr(world, "frame_count", 0)),
        "sub": str(getattr(getattr(world, "sub_state", None), "value",
                           getattr(world, "sub_state", None))),
        "phase": str(getattr(world, "_ope_phase_label", "")
                     or getattr(ope, "_phase", "")),
        "in_pursuit": bool(getattr(world, "in_pursuit_mode", False)),
        "bc_x": (float(getattr(bc, "x", 0.0)) if bc is not None else None),
        "ruck_y": float(getattr(world, "ruck_y_m", 0.0) or 0.0),
        "live_span": float(span),
        "n_line": int(n_line),
        "commanded_m": commanded,
        "governed_m": governed,
        "pub_left_m": left_m,
        "pub_right_m": right_m,
        "spacing_owned": int(owned),
        "target_span": float(target_span),
        "owned_span": float(owned_span),
        "other_span": float(other_span),
        "n_other": int(len(_other_ys)),
        "src_top": _src_top,
        "def_width_m": float(getattr(world, "def_line_width_m", 0.0) or 0.0),
    }


def run_live_once(run_index, attempt=0):
    """Boot, run headless, apply in-place tuner edits at fixed frames.

    Engine stdout for the whole run is captured to a buffer so the probe
    report is not drowned by the per-frame engine debug prints (the
    Stage-4/5/7 pattern).
    """
    mods = _engine_imports()
    GamePhase = mods["GamePhase"]
    random.seed(1234 + run_index * 131 + attempt * 17)
    world, ball, ope, kickoff_engine, collision_system = _boot_world(mods)
    tuners = getattr(ope, "def_tuners", None)
    edit_map = dict(TUNER_SETS)
    edit_i = 0
    edits = []
    recs = []
    engine_log = io.StringIO()
    error = None
    try:
        with contextlib.redirect_stdout(engine_log):
            for _ in range(MAX_FRAMES):
                ball.update(DT)
                if world.phase == GamePhase.KICKOFF:
                    kickoff_engine.update(DT)
                    mods["check_for_catch"](world, ball, DT)
                    continue
                if world.phase == GamePhase.CATCH:
                    if world.ball_carrier is not None:
                        ope.start(world.ball_carrier)
                    world.phase = GamePhase.OPEN_PLAY
                    continue
                if world.phase != GamePhase.OPEN_PLAY:
                    continue
                ope.update(DT)
                if not getattr(world, "tes_active", False):
                    collision_system.resolve_all(world.players, world=world)

                if (edit_i < len(LIVE_EDIT_FRAMES)
                        and getattr(world, "frame_count", 0)
                        >= LIVE_EDIT_FRAMES[edit_i]
                        and _live_eligible(world)):
                    label = LIVE_EDIT_SETS[edit_i]
                    vals = edit_map[label]
                    if tuners is not None:
                        tuners.width_left_tryline_m = vals[0]
                        tuners.width_halfway_m = vals[1]
                        tuners.width_right_tryline_m = vals[2]
                    edits.append({
                        "frame": int(getattr(world, "frame_count", 0)),
                        "to": label,
                    })
                    edit_i += 1

                recs.append(_live_record(world, ope))
    except Exception as exc:  # pre-existing engine fault — never mask silently
        error = f"{type(exc).__name__}: {exc}"
    return {
        "run": run_index,
        "error": error,
        "records": recs,
        "edits": edits,
        "engine_log": engine_log.getvalue(),
    }


def _is_width_window(rec):
    """True on a frame where the width channel can realise on-field."""
    return (rec["sub"] == "open_play"
            and not rec["in_pursuit"]
            and rec["n_line"] >= 2)


def _mean(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return float(statistics.mean(vals))


def summarise_ab(runs):
    """Build the per-edit A/B rows across all seeded runs.

    For each edit at frame E: the CONTROL arm is the width-live frames in
    [E - W, E); the TREATMENT arm is the width-live frames in [E, E + W).
    Both arms share the SAME world up to E, so the delta is the pure
    tuner effect on the realised line.
    """
    W = LIVE_AB_WINDOW
    ab_rows = []
    for r in runs:
        if r.get("error"):
            continue
        recs = r["records"]
        live_idx = [i for i, x in enumerate(recs) if _is_width_window(x)]
        if not live_idx:
            continue
        for e in r["edits"]:
            E = e["frame"]
            idx_E = next((i for i, x in enumerate(recs)
                          if x["frame"] >= E), None)
            if idx_E is None:
                continue
            ctrl_idx = [i for i in live_idx if i < idx_E][-W:]
            trt_idx = [i for i in live_idx if i >= idx_E][:W]
            if not ctrl_idx or not trt_idx:
                continue
            ctrl = [recs[i] for i in ctrl_idx]
            trt = [recs[i] for i in trt_idx]
            ab_rows.append({
                "run": r["run"] + 1,
                "edit_frame": E,
                "to": e["to"],
                "ctrl_frames": len(ctrl),
                "trt_frames": len(trt),
                "ctrl_live_span": _mean([x["live_span"] for x in ctrl]),
                "trt_live_span": _mean([x["live_span"] for x in trt]),
                "d_live_span": (_mean([x["live_span"] for x in trt])
                                - _mean([x["live_span"] for x in ctrl])),
                "ctrl_gov": _mean([x["governed_m"] for x in ctrl]),
                "trt_gov": _mean([x["governed_m"] for x in trt]),
                "ctrl_cmd": _mean([x["commanded_m"] for x in ctrl]),
                "trt_cmd": _mean([x["commanded_m"] for x in trt]),
                "ctrl_pub_mean": _mean([
                    0.5 * (x["pub_left_m"] + x["pub_right_m"])
                    for x in ctrl if x["pub_left_m"] is not None
                    and x["pub_right_m"] is not None]),
                "trt_pub_mean": _mean([
                    0.5 * (x["pub_left_m"] + x["pub_right_m"])
                    for x in trt if x["pub_left_m"] is not None
                    and x["pub_right_m"] is not None]),
                "ctrl_spacing_owned": _mean([x["spacing_owned"] for x in ctrl]),
                "trt_spacing_owned": _mean([x["spacing_owned"] for x in trt]),
            })
    return ab_rows


def print_part_c(runs, ab_rows):
    print("\n" + "-" * 78)
    print("PART C — LIVE IN-PLACE A/B (one world, edit ONLY the tuner)")
    print("-" * 78)
    for r in runs:
        if r.get("error"):
            print("  run %d: ENGINE ABORT (pre-existing fault): %s"
                  % (r["run"] + 1, r["error"]))
        else:
            n_open = sum(1 for x in r["records"] if _is_width_window(x))
            print("  run %d: frames=%d  width-live frames=%d  edits=%d"
                  % (r["run"] + 1, len(r["records"]), n_open, len(r["edits"])))
    if not ab_rows:
        print("  NO A/B rows produced (no paired width-live windows).")
        return
    print("\n    %-4s %-8s %-14s %10s %10s %10s %9s %9s" % (
        "run", "frame", "to", "ctrl_span", "trt_span", "d_span",
        "ctrl_gov", "trt_gov"))
    for row in ab_rows:
        print("    %-4d %-8d %-14s %10.2f %10.2f %+10.2f %9.2f %9.2f" % (
            row["run"], row["edit_frame"], row["to"],
            row["ctrl_live_span"] or 0.0, row["trt_live_span"] or 0.0,
            row["d_live_span"] or 0.0,
            row["ctrl_gov"] or 0.0, row["trt_gov"] or 0.0))

    # Aggregate: on-field realised sensitivity vs governed (published) change.
    print("\n  Aggregate on-field response to a live tuner edit:")
    for to_label in sorted({row["to"] for row in ab_rows}):
        rows_l = [row for row in ab_rows if row["to"] == to_label]
        d_span = _mean([row["d_live_span"] for row in rows_l])
        d_gov = _mean([
            (row["trt_gov"] - row["ctrl_gov"])
            for row in rows_l
            if row["trt_gov"] is not None and row["ctrl_gov"] is not None])
        d_cmd = _mean([
            (row["trt_cmd"] - row["ctrl_cmd"])
            for row in rows_l
            if row["trt_cmd"] is not None and row["ctrl_cmd"] is not None])
        ratio = (100.0 * d_span / d_gov) if d_gov else 0.0
        print("    edit -> %-10s d(commanded)=%+7.2f  d(governed)=%+7.2f  "
              "d(on-field span)=%+7.2f  (%5.1f%% of governed delta)"
              % (to_label, d_cmd or 0.0, d_gov or 0.0, d_span or 0.0, ratio))


def _slope(xs, ys):
    """Least-squares slope of ys on xs (None when undefined)."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 1e-12:
        return None
    return num / den


def _pearson(xs, ys):
    """Pearson correlation of xs and ys (None when undefined)."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 1e-12 or syy <= 1e-12:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def _pooled_rows(runs):
    """Every width-live frame carrying a published (governed) width."""
    rows = []
    for r in runs:
        if r.get("error"):
            continue
        for x in r["records"]:
            if _is_width_window(x) and x.get("governed_m") is not None:
                rows.append(x)
    return rows


def pooled_analysis(runs):
    """Realised on-field span vs the published / governed / target span.

    Pools EVERY width-live frame across all seeded runs.  If the realised
    on-field line tracks the published width, the slopes are ~1 and the
    realised/target ratio is ~1; a slope near 0 is the on-field absorption
    the field note describes.  This is the statistic immune to the
    control/treatment phase contamination of the raw edit deltas.
    """
    rows = []
    for r in runs:
        if r.get("error"):
            continue
        for x in r["records"]:
            if _is_width_window(x) and x.get("governed_m") is not None:
                rows.append(x)
    if not rows:
        return None
    gov = [float(x["governed_m"]) for x in rows]
    pub = [float(x["pub_left_m"]) + float(x["pub_right_m"]) for x in rows
           if x.get("pub_left_m") is not None
           and x.get("pub_right_m") is not None]
    tgt = [float(x.get("target_span") or 0.0) for x in rows]
    real = [float(x["live_span"]) for x in rows]
    ratios = [x["live_span"] / x["target_span"]
              for x in rows if (x.get("target_span") or 0.0) > 0.1]
    # WIDTH-CHAIN PAIR — governed width vs THIS frame's own spacing-target
    # span.  This is the only pair in the pooled set where both sides are
    # the width chain's own output; ``live_span`` is measured over EVERY
    # line-population body, most of which are moved by the chase/tackle Y
    # writers and therefore carry no width information.
    chain = [(float(x["governed_m"]), float(x.get("target_span") or 0.0))
             for x in rows]
    chain_gov = [c[0] for c in chain]
    chain_tgt = [c[1] for c in chain]
    chain_delta = [abs(a - b) for a, b in chain]
    return {
        "n": len(rows),
        "mean_gov": _mean(gov),
        "mean_pub_sum": _mean(pub),
        "mean_target_span": _mean(tgt),
        "mean_realised": _mean(real),
        "pearson_target_vs_governed": _pearson(chain_gov, chain_tgt),
        "slope_target_vs_governed": _slope(chain_gov, chain_tgt),
        "mean_abs_target_minus_governed": _mean(chain_delta),
        "max_abs_target_minus_governed": (max(chain_delta)
                                          if chain_delta else None),
        "n_target_within_0p1_of_governed": sum(
            1 for d in chain_delta if d <= 0.1),
        "slope_realised_vs_gov": _slope(gov, real),
        "slope_realised_vs_target": _slope(tgt, real),
        "pearson_realised_vs_gov": _pearson(gov, real),
        "pearson_realised_vs_target": _pearson(tgt, real),
        "mean_realised_over_target": _mean(ratios),
        "mean_n_line": _mean([x.get("n_line") for x in rows]),
        "mean_n_owned": _mean([x.get("spacing_owned") for x in rows]),
        "mean_n_other": _mean([x.get("n_other") for x in rows]),
        "mean_owned_span": _mean([
            x["owned_span"] for x in rows
            if (x.get("owned_span") or 0.0) > 0.1]),
        "mean_other_span": _mean([
            x["other_span"] for x in rows
            if (x.get("other_span") or 0.0) > 0.1]),
        "slope_owned_vs_target": _slope(
            [float(x["target_span"]) for x in rows
             if (x.get("owned_span") or 0.0) > 0.1],
            [float(x["owned_span"]) for x in rows
             if (x.get("owned_span") or 0.0) > 0.1]),
        "pearson_owned_vs_target": _pearson(
            [float(x["target_span"]) for x in rows
             if (x.get("owned_span") or 0.0) > 0.1],
            [float(x["owned_span"]) for x in rows
             if (x.get("owned_span") or 0.0) > 0.1]),
        "std_gov": (statistics.pstdev(gov) if len(gov) > 1 else 0.0),
        "std_realised": (statistics.pstdev(real) if len(real) > 1 else 0.0),
    }


def print_pooled(res):
    print("\n" + "-" * 78)
    print("PART C (POOLED) — realised on-field span vs published width")
    print("-" * 78)
    if not res:
        print("  no width-live frames with a published width.")
        return
    print("  width-live frames  : %d" % res["n"])
    print("  mean governed      : %8.2f m  (std %.2f)"
          % (res["mean_gov"], res["std_gov"]))
    print("  mean published sum : %8.2f m" % res["mean_pub_sum"])
    print("  mean target span   : %8.2f m  (frame_spacing_targets)"
          % res["mean_target_span"])
    print("  mean realised span : %8.2f m  (on-field line)  (std %.2f)"
          % (res["mean_realised"], res["std_realised"]))
    print("  slope realised~gov    : %s"
          % ("%.3f" % res["slope_realised_vs_gov"]
             if res["slope_realised_vs_gov"] is not None else "n/a"))
    print("  slope realised~target : %s"
          % ("%.3f" % res["slope_realised_vs_target"]
             if res["slope_realised_vs_target"] is not None else "n/a"))
    print("  mean realised/target  : %s"
          % ("%.3f" % res["mean_realised_over_target"]
             if res["mean_realised_over_target"] is not None else "n/a"))
    print("  pearson realised~gov    : %s"
          % ("%.3f" % res["pearson_realised_vs_gov"]
             if res["pearson_realised_vs_gov"] is not None else "n/a"))
    print("  pearson realised~target : %s"
          % ("%.3f" % res["pearson_realised_vs_target"]
             if res["pearson_realised_vs_target"] is not None else "n/a"))
    print("  --- width-chain rung on live frames ---")
    print("  pearson target~governed : %s"
          % ("%.3f" % res["pearson_target_vs_governed"]
             if res.get("pearson_target_vs_governed") is not None else "n/a"))
    print("  slope target~governed   : %s"
          % ("%.3f" % res["slope_target_vs_governed"]
             if res.get("slope_target_vs_governed") is not None else "n/a"))
    print("  mean |target-governed|  : %s"
          % ("%.3f m" % res["mean_abs_target_minus_governed"]
             if res.get("mean_abs_target_minus_governed") is not None
             else "n/a"))
    print("  max  |target-governed|  : %s"
          % ("%.3f m" % res["max_abs_target_minus_governed"]
             if res.get("max_abs_target_minus_governed") is not None
             else "n/a"))
    print("  frames target == governed (<=0.1 m): %s of %d"
          % (res.get("n_target_within_0p1_of_governed"), res["n"]))
    print("  --- attribution: which writer owns the line Y ---")
    print("  mean line bodies        : %6.2f" % (res["mean_n_line"] or 0.0))
    print("  mean on width chain     : %6.2f  (target_y == spacing target)"
          % (res["mean_n_owned"] or 0.0))
    print("  mean overwritten        : %6.2f  (target_y != spacing target)"
          % (res["mean_n_other"] or 0.0))
    print("  mean span of chain-owned: %6.2f m" % (res["mean_owned_span"] or 0.0))
    print("  mean span of overwritten: %6.2f m" % (res["mean_other_span"] or 0.0))
    print("  slope owned~target      : %s"
          % ("%.3f" % res["slope_owned_vs_target"]
             if res["slope_owned_vs_target"] is not None else "n/a"))
    print("  pearson owned~target    : %s"
          % ("%.3f" % res["pearson_owned_vs_target"]
             if res["pearson_owned_vs_target"] is not None else "n/a"))


def binned_by_governed(runs):
    """Mean target / realised span per governed-width bin (flatness test).

    If the on-field line responded to the width, mean realised span would
    rise monotonically across the bins.  A flat profile is the on-field
    insensitivity the field note describes.
    """
    rows = _pooled_rows(runs)
    bins = ((30.0, 40.0), (40.0, 45.0), (45.0, 50.0),
            (50.0, 55.0), (55.0, 60.0), (60.0, 65.0))
    out = []
    for lo, hi in bins:
        sel = [x for x in rows if lo <= float(x["governed_m"]) < hi]
        if not sel:
            continue
        out.append({
            "lo": lo, "hi": hi, "n": len(sel),
            "mean_target": _mean([x["target_span"] for x in sel]),
            "mean_realised": _mean([x["live_span"] for x in sel]),
        })
    return out


def print_bins(bins):
    print("\n  Realised span by governed-width bin (flatness test):")
    print("    %-12s %6s %12s %12s"
          % ("gov bin", "n", "mean_target", "mean_realised"))
    for b in bins:
        print("    %-12s %6d %12.2f %12.2f" % (
            "[%.0f,%.0f)" % (b["lo"], b["hi"]), b["n"],
            b["mean_target"] or 0.0, b["mean_realised"] or 0.0))


def run_part_c():
    runs = []
    for i in range(LIVE_RUNS):
        res = None
        for attempt in range(3):
            res = run_live_once(i, attempt)
            if not res.get("error"):
                break
            print("[retry] live run %d aborted: %s" % (i + 1, res["error"]))
        runs.append(res)
    ab_rows = summarise_ab(runs)
    print_part_c(runs, ab_rows)
    return runs, ab_rows


# ─────────────────────────────────────────────────────────────────────
# CSV + SUMMARY WRITERS
# ─────────────────────────────────────────────────────────────────────

RUNG_COLUMNS = (
    "scenario", "phase", "bc_x", "ruck_y", "n_left", "n_right", "tuner",
    "tuner_l", "tuner_m", "tuner_r",
    "r1_commanded_m", "r2_governed_m", "r3_left_m", "r3_right_m",
    "r3_published_mean_m", "r4_lane_span_m", "r4_outer_left", "r4_outer_right",
    "r4_gap_left_m", "r4_gap_right_m", "r4_floor_left", "r4_floor_right",
    "r5_spacing_span_m", "reducers",
)


def write_rung_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=RUNG_COLUMNS)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: r.get(k) for k in RUNG_COLUMNS})


AB_COLUMNS = (
    "run", "edit_frame", "to", "ctrl_frames", "trt_frames",
    "ctrl_live_span", "trt_live_span", "d_live_span",
    "ctrl_gov", "trt_gov", "ctrl_cmd", "trt_cmd",
    "ctrl_pub_mean", "trt_pub_mean",
    "ctrl_spacing_owned", "trt_spacing_owned",
)


def write_ab_csv(path, ab_rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=AB_COLUMNS)
        wr.writeheader()
        for r in ab_rows:
            wr.writerow({k: r.get(k) for k in AB_COLUMNS})


def write_summary(path, rows, part_b, ab_rows, runs):
    lines = []
    lines.append("STAGE 8 — WIDTH TUNER REALISATION AUDIT — SUMMARY")
    lines.append("=" * 78)
    lines.append("rung table rows: %d" % len(rows))
    # headline sensitivity (default -> wide, centred, 4-4)
    def find(lbl, bx, ry, nl, nr):
        return next((r for r in rows
                     if r["tuner"] == lbl and r["bc_x"] == bx
                     and r["ruck_y"] == ry and r["n_left"] == nl
                     and r["n_right"] == nr), None)
    b = find("default", 60.0, 35.0, 4, 4)
    w = find("wide", 60.0, 35.0, 4, 4)
    if b and w:
        lines.append("headline default->wide (bc.x=60, ruck_y=35, 4-4):")
        for k, name in (
            ("r1_commanded_m", "commanded"),
            ("r2_governed_m", "governed"),
            ("r3_published_mean_m", "published_mean"),
            ("r4_lane_span_m", "lane_span"),
            ("r5_spacing_span_m", "spacing_span"),
        ):
            lines.append("  d(%s) = %+.3f m" % (name, w[k] - b[k]))
    lines.append("")
    lines.append("PART B (governor publish convergence):")
    for label, r in part_b.items():
        lines.append("  %-14s cmd=%7.2f pub=%7.2f frames->0.1=%s"
                     % (label, r["commanded_final_m"], r["final_published_m"],
                        r["frames_to_within_0p1"]))
    lines.append("")
    lines.append("PART C (live in-place A/B):")
    for r in runs:
        if r.get("error"):
            lines.append("  run %d: ABORT %s" % (r["run"] + 1, r["error"]))
        else:
            lines.append("  run %d: frames=%d edits=%d"
                         % (r["run"] + 1, len(r["records"]), len(r["edits"])))
    for row in ab_rows:
        lines.append("  edit->%-10s d(on-field span)=%+.2f  d(governed)=%+.2f"
                     % (row["to"], row["d_live_span"] or 0.0,
                        (row["trt_gov"] or 0.0) - (row["ctrl_gov"] or 0.0)))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 78)
    print("STAGE 8 — WIDTH TUNER REALISATION AUDIT")
    print("=" * 78)

    rows = run_part_a()
    print_part_a(rows)
    write_rung_csv("stage8_rung_table.csv", rows)

    part_b = run_part_b()
    print_part_b(part_b)

    runs, ab_rows = run_part_c()
    write_ab_csv("stage8_live_ab.csv", ab_rows)
    pooled = pooled_analysis(runs)
    print_pooled(pooled)
    bins = binned_by_governed(runs)
    print_bins(bins)
    with open("stage8_pooled.txt", "w", encoding="utf-8") as fh:
        fh.write(repr(pooled) + "\n")
        for _b in bins:
            fh.write(repr(_b) + "\n")

    summary = write_summary("stage8_summary.txt", rows, part_b, ab_rows, runs)
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(summary)
    print("artefacts: stage8_rung_table.csv, stage8_live_ab.csv, "
          "stage8_summary.txt")


if __name__ == "__main__":
    main()
