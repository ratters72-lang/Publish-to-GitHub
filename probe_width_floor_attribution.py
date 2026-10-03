"""
probe_width_floor_attribution.py — WIDTH FLOOR / WIDTH-AUTHORITY SATURATION AUDIT
(READ-ONLY against production: no engine file is modified or written).

QUESTION
--------
Extreme tuner REDUCTIONS stop reducing the realised defensive-line width:

    left_tryline_width = 25  -> visual line ~25 m
    left_tryline_width = 10  -> visual line ~25 m
    left_tryline_width =  1  -> visual line ~25 m
    left_tryline_width = 0.1 -> visual line ~25 m

Where does the REQUESTED width stop propagating?  This probe measures the full
chain every OPEN_PLAY frame:

    requested  width_left_tryline_m / width_halfway_m / width_right_tryline_m
 -> interpolated  defensive_width.interpolated_defensive_width(tuners, bc.x)
 -> world         world.def_line_width_m      (governor publish — the sole writer)
 -> governed      defensive_width.governed_width_m(world, tuners)
 -> published     formation_width_authority.publish_formation_width {left,right}
 -> realised      abs(lane_max_y - lane_min_y) from the authoritative lane system

and reports the FIRST stage where requested_width != actual_width.

PARTS
-----
  A  DETERMINISTIC CHAIN — pinned stub world + line; exact rung numbers.
  B  LIVE ENGINE — real headless world; per-OPEN_PLAY-frame emit of the
     requested line, plus an in-place tuner matrix 0.1 -> 30.
  C  CLAMP AUDIT — every live/dead reader-writer of the width clamp symbols.

Run:  python probe_width_floor_attribution.py
Out:  stdout report + width_floor_rungs.csv + width_floor_live.csv
      + width_floor_summary.txt
"""

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

from player.player import Player
from world.world_state import GameSubState
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    interpolated_defensive_width,
    governed_width_m,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets,
    side_widths,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.formation_width_authority import (
    publish_formation_width,
)
from game.defence.defence_lvl_1 import population_authority as pa

# ─────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────
RUCK_X = 60.0
RUCK_Y = 35.0
FPS = 60.0
DT = 1.0 / FPS

# The test matrix requested by the task: all three width tuners held equal.
TUNER_MATRIX = (0.1, 1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0)

_SIDE_ROLES = ("A", "B", "C", "D", "CENTRE", "WINGER")

# Live engine bounds.
MAX_FRAMES = 4200
LIVE_EDIT_STRIDE = 450      # frames between in-place tuner edits
LIVE_EDIT_START = 350
LIVE_WINDOW = 260           # settled window length after each edit
LIVE_SETTLE = 45            # frames to skip after an edit (rate clamp settle)

SMALL_SPAN_EPS = 0.01


# ─────────────────────────────────────────────────────────────────────
# STUB WORLD + LINE BUILDERS (deterministic)
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

    def __init__(self, ruck_y, commanded, phase="OPEN_PLAY"):
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
        self.frame_side_lanes = None
        self.line_population = None
        self.line_population_ids = None
        self.published_count_left = None
        self.published_count_right = None


def make_line(n_left, n_right, ruck_y, *, base_pid=100, x=70.0):
    """Build a line of real Players with depth_role/depth_side pre-stamped."""
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


def make_tuners(value):
    """All three width tuners held equal at *value*."""
    t = DefenceTuners()
    t.width_left_tryline_m = float(value)
    t.width_halfway_m = float(value)
    t.width_right_tryline_m = float(value)
    return t


def lane_span(world, targets):
    """abs(lane_max_y - lane_min_y) from the authoritative lane system.

    Prefers ``world.frame_side_lanes`` (the per-side ladder compute_targets
    ACTUALLY used this frame).  Falls back to the spacing-target dict span
    when no ladder was published.
    """
    ladders = getattr(world, "frame_side_lanes", None)
    ys = []
    if isinstance(ladders, dict):
        for _side, lanes in ladders.items():
            for v in (lanes or []):
                ys.append(float(v))
    if len(ys) >= 2:
        return float(max(ys) - min(ys)), "ladder"
    vals = [float(v) for v in (targets or {}).values()]
    if len(vals) >= 2:
        return float(max(vals) - min(vals)), "targets"
    return 0.0, "none"


def measure_chain(value, bc_x, ruck_y=RUCK_Y, n_left=4, n_right=4):
    """Measure all six rungs for one tuner value at a pinned scenario."""
    t = make_tuners(value)
    world = RungWorld(ruck_y, 0.0)
    world.frame_count = 1
    world.ball_carrier = StubCarrier(bc_x, ruck_y)
    line = make_line(n_left, n_right, ruck_y)
    world.defending_team = list(line)

    # Rung 1 — requested (the tuner value as written by the tester).
    requested = float(value)
    # Rung 2 — interpolated.
    interp = float(interpolated_defensive_width(t, bc_x))

    # Model the governor having published the interpolated value this frame
    # (DefensiveLineGovernor.compute_line_state step 4a is the SOLE writer).
    world.def_line_width_m = interp
    world.governor_line_width_m = interp
    world.governor_width_active = True

    # Rung 3 — world.def_line_width_m (the published raw width).
    world_pub = float(getattr(world, "def_line_width_m", 0.0))

    # Rung 4 — governed (clamped).
    governed = float(governed_width_m(world, t))

    # Rung 5 — published per-side span (+ named reducers).
    pub = publish_formation_width(world, t)
    left_m = float(pub["left_m"])
    right_m = float(pub["right_m"])
    published = 0.5 * (left_m + right_m)
    reducers = dict(pub.get("reducers") or {})

    # Rung 6 — realised (authoritative lane system).
    targets = compute_targets(world, line, t, governor=None)
    realised, src = lane_span(world, targets)

    # Extra provenance.
    lw, rw = side_widths(world, t, ruck_y=ruck_y)
    return {
        "requested": requested,
        "interpolated": interp,
        "world": world_pub,
        "governed": governed,
        "published": published,
        "realised": realised,
        "realised_src": src,
        "pub_left": left_m,
        "pub_right": right_m,
        "side_left": float(lw),
        "side_right": float(rw),
        "reducers": ";".join(
            f"{k}={v:+.4f}" for k, v in sorted(reducers.items())
        ),
        "bc_x": float(bc_x),
        "ruck_y": float(ruck_y),
        "n_left": int(n_left),
        "n_right": int(n_right),
    }


def first_divergence(row, tol=1e-6):
    """Return the name of the FIRST rung that differs from the requested width."""
    chain = (
        ("requested", row["requested"]),
        ("interpolated", row["interpolated"]),
        ("world", row["world"]),
        ("governed", row["governed"]),
        ("published", row["published"]),
        ("realised", row["realised"]),
    )
    req = row["requested"]
    for name, val in chain:
        if abs(float(val) - float(req)) > tol:
            return name
    return "NONE"
# ─────────────────────────────────────────────────────────────────────
# PART A — DETERMINISTIC CHAIN TABLE
# ─────────────────────────────────────────────────────────────────────

def run_part_a():
    """Deterministic rung table across the tuner matrix, task print form."""
    print("\n" + "=" * 78)
    print("PART A — DETERMINISTIC CHAIN (pinned stub world, bc.x=60, ruck_y=35, 4-4)")
    print("=" * 78)
    print("    %8s %10s %12s %9s %9s %10s %9s  %s" % (
        "request", "interp", "world", "governed", "published", "realised",
        "firstdiv", "reducers"))
    rows = []
    for value in TUNER_MATRIX:
        row = measure_chain(value, RUCK_Y)
        row["first_div"] = first_divergence(row)
        rows.append(row)
        print("    %8.3f %10.3f %12.3f %9.3f %9.3f %10.3f %9s  %s" % (
            row["requested"], row["interpolated"], row["world"],
            row["governed"], row["published"], row["realised"],
            row["first_div"], row["reducers"]))
    # The task's requested emit line (deterministic form).
    print("\n  Emit line (deterministic, one per matrix value):")
    for row in rows:
        print(
            f"    bc_x={row['bc_x']:.1f} "
            f"interp={row['interpolated']:.2f} "
            f"world={row['world']:.2f} "
            f"governed={row['governed']:.2f} "
            f"published={row['published']:.2f} "
            f"realised={row['realised']:.2f}"
        )
    return rows


# ─────────────────────────────────────────────────────────────────────
# PART B — LIVE ENGINE (real headless world)
# ─────────────────────────────────────────────────────────────────────

_ENGINE = {"mods": None}


def _engine_imports():
    """Import the real engine headless (mirrors the Stage-4/5/7/8 bootstrap)."""
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


def _is_open_play(world):
    sub = getattr(world, "sub_state", None)
    return str(getattr(sub, "value", sub)).lower() == "open_play"


def _live_eligible(world):
    """True on a frame where the width channel can actually realise."""
    if not _is_open_play(world):
        return False
    if getattr(world, "in_pursuit_mode", False):
        return False
    return getattr(world, "ball_carrier", None) is not None


def _live_lane_span(world):
    """Realised span from the authoritative lane system (world.frame_side_lanes)."""
    ladders = getattr(world, "frame_side_lanes", None)
    ys = []
    if isinstance(ladders, dict):
        for _side, lanes in ladders.items():
            for v in (lanes or []):
                ys.append(float(v))
    if len(ys) >= 2:
        return float(max(ys) - min(ys)), "ladder"
    # Fallback: span of the line-population body Y positions.
    bodies = pa.line_population(getattr(world, "defending_team", None) or [])
    ys2 = [float(getattr(d, "y", 0.0)) for d in bodies]
    if len(ys2) >= 2:
        return float(max(ys2) - min(ys2)), "bodies"
    return 0.0, "none"


def _live_record(world, tuners):
    """One per-frame snapshot of the six rungs + realised lane span."""
    bc = getattr(world, "ball_carrier", None)
    bc_x = float(getattr(bc, "x", 0.0)) if bc is not None else None
    requested = float(getattr(tuners, "width_halfway_m", 0.0))
    interp = (
        float(interpolated_defensive_width(tuners, bc_x))
        if bc_x is not None else None
    )
    world_w = float(getattr(world, "def_line_width_m", 0.0) or 0.0)
    governed = float(governed_width_m(world, tuners))
    pub = getattr(world, "formation_width", None)
    published = None
    pub_left = pub_right = None
    if (isinstance(pub, dict)
            and pub.get("frame") == getattr(world, "frame_count", None)):
        pub_left = pub.get("left_m")
        pub_right = pub.get("right_m")
        if pub_left is not None and pub_right is not None:
            published = 0.5 * (float(pub_left) + float(pub_right))
    realised, src = _live_lane_span(world)
    return {
        "frame": int(getattr(world, "frame_count", 0)),
        "bc_x": bc_x,
        "requested": requested,
        "interpolated": interp,
        "world": world_w,
        "governed": governed,
        "published": published,
        "realised": realised,
        "realised_src": src,
        "pub_left": pub_left,
        "pub_right": pub_right,
        "ruck_y": float(getattr(world, "ruck_y_m", 0.0) or 0.0),
        "governor_active": bool(getattr(world, "governor_width_active", False)),
        "governor_line_width": float(
            getattr(world, "governor_line_width_m", 0.0) or 0.0),
    }


def _live_line(labels):
    """Format the task's required per-frame emit line."""
    return (
        f"bc_x={labels['bc_x']:.1f} "
        f"interp={labels['interpolated']:.2f} "
        f"world={labels['world']:.2f} "
        f"governed={labels['governed']:.2f} "
        f"published={labels['published']:.2f} "
        f"realised={labels['realised']:.2f}"
    )


def run_live_once(run_index, attempt=0):
    """Boot, run headless, edit the three width tuners in place per matrix value.

    Emits the task's required line on EVERY width-live OPEN_PLAY frame (buffered
    so the engine's own debug chatter, captured to a buffer, never collides).
    Returns per-value settled aggregates plus the emitted lines.
    """
    mods = _engine_imports()
    GamePhase = mods["GamePhase"]
    random.seed(24680 + run_index * 131 + attempt * 17)
    world, ball, ope, kickoff_engine, collision_system = _boot_world(mods)
    tuners = getattr(ope, "def_tuners", None)

    # CYCLE SCHEDULER: OPEN_PLAY width-live frames are sparse across a match,
    # so a timer-based edit misses values.  Instead hold each matrix value for
    # FRAMES_PER_VALUE width-live frames, then advance to the next value.  Every
    # matrix value is therefore exercised on real OPEN_PLAY frames.
    FRAMES_PER_VALUE = 30
    cycle_i = 0
    frames_in_value = 0
    warmed = False
    warm_frames = 0
    applied = []
    records = []
    emitted = []
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

                if not _live_eligible(world):
                    continue

                # GOVERNOR WARM-UP: the very first matrix value starts from the
                # boot default (width_halfway_m = 45) and the governor's 1.0 m
                # per-frame rate clamp needs ~45 frames to reach 0.1.  Hold the
                # first value and DISCARD frames until the published width has
                # converged onto the command (or a hard cap), so the first group
                # is not polluted by the down-ramp transient.
                if not warmed:
                    _v0 = float(TUNER_MATRIX[0])
                    if tuners is not None:
                        tuners.width_left_tryline_m = _v0
                        tuners.width_halfway_m = _v0
                        tuners.width_right_tryline_m = _v0
                    _bc = getattr(world, "ball_carrier", None)
                    _cmd = float(interpolated_defensive_width(
                        tuners, float(getattr(_bc, "x", 0.0)))
                    ) if _bc is not None else _v0
                    _pub_w = float(getattr(world, "def_line_width_m", 0.0) or 0.0)
                    warm_frames += 1
                    if abs(_pub_w - _cmd) <= 1.0 or warm_frames >= 120:
                        warmed = True
                    continue

                # Advance the cycle after FRAMES_PER_VALUE width-live frames.
                if frames_in_value >= FRAMES_PER_VALUE:
                    cycle_i = (cycle_i + 1) % len(TUNER_MATRIX)
                    frames_in_value = 0
                v = float(TUNER_MATRIX[cycle_i])
                if tuners is not None:
                    tuners.width_left_tryline_m = v
                    tuners.width_halfway_m = v
                    tuners.width_right_tryline_m = v
                if not applied or applied[-1]["value"] != v:
                    applied.append({
                        "frame": int(getattr(world, "frame_count", 0)),
                        "value": v,
                    })
                frames_in_value += 1

                rec = _live_record(world, tuners)
                records.append(rec)
                emitted.append(_live_line(rec))
    except Exception as exc:  # pre-existing engine fault — never mask silently
        error = f"{type(exc).__name__}: {exc}"
    return {
        "run": run_index,
        "error": error,
        "records": records,
        "edits": applied,
        "emitted": emitted,
        "engine_log_len": len(engine_log.getvalue()),
    }


def _mean(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return float(statistics.mean(vals))


def _median(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return float(statistics.median(vals))


def _settled_window(records, edit_frame):
    """Records inside [edit+SETTLE, edit+SETTLE+WINDOW]."""
    lo = edit_frame + LIVE_SETTLE
    hi = edit_frame + LIVE_SETTLE + LIVE_WINDOW
    return [r for r in records if lo <= r["frame"] <= hi]


def summarise_matrix(runs):
    """Group every width-live record by its ACTIVE tuner value.

    A width-live OPEN_PLAY frame is sparse across a match (kickoff, tackle,
    PTB and pursuit frames are excluded), so a fixed time window around an
    edit can be empty.  Each record already carries the tuner value in force
    when it was emitted (``requested``), so grouping by that value is exact
    and window-free: every frame measured under value V lands in group V.
    """
    groups = {}
    for r in runs:
        for x in (r.get("records") or []):
            key = round(float(x["requested"]), 3)
            groups.setdefault(key, []).append(x)
    out = []
    for req in sorted(groups):
        win = groups[req]
        row = {
            "run": 1,
            "value": req,
            "edit_frame": None,
            "n": len(win),
            "requested": req,
            "interpolated": _mean([x["interpolated"] for x in win]),
            "world": _mean([x["world"] for x in win]),
            # SETTLED world rung: the MEDIAN of the group.  The governor's
            # per-frame rate clamp (line_width_max_delta_per_frame_m) ramps the
            # published width during the first couple of frames of a value
            # switch, which inflates the MEAN of a short group; the median is
            # the value the group spends its frames at.
            "world_settled": _median([x["world"] for x in win]),
            "governed": _mean([x["governed"] for x in win]),
            "published": _mean([x["published"] for x in win]),
            "realised": _mean([x["realised"] for x in win]),
            "bc_x": _mean([x["bc_x"] for x in win]),
            "ruck_y": _mean([x["ruck_y"] for x in win]),
            "gov_active_frac": _mean(
                [1.0 if x["governor_active"] else 0.0 for x in win]),
            "realised_src_top": _src_top(win),
        }
        # first_div on the SETTLED chain (median world) so the rate-clamp
        # transient cannot misreport the divergent rung.
        row["first_div"] = first_divergence({
            "requested": row["requested"],
            "interpolated": row["interpolated"],
            "world": row["world_settled"],
            "governed": row["governed"],
            "published": row["published"],
            "realised": row["realised"],
        })
        row["first_div_raw"] = first_divergence(row)
        out.append(row)
    return out


def _src_top(win):
    """Most common realised-span source across a group (ladder vs bodies)."""
    counts = {}
    for x in win:
        s = x.get("realised_src") or "none"
        counts[s] = counts.get(s, 0) + 1
    if not counts:
        return "none"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def print_part_b(rows):
    print("\n" + "=" * 78)
    print("PART B — LIVE ENGINE MATRIX (in-place tuner edits, settled windows)")
    print("=" * 78)
    if not rows:
        print("  NO width-live windows produced.")
        return
    print("    %5s %8s %10s %12s %9s %9s %10s %9s  %s" % (
        "run", "request", "interp", "world", "governed", "published",
        "realised", "firstdiv", "n"))
    for row in rows:
        def _f(v):
            return "n/a" if v is None else ("%8.3f" % v)
        print("    %5d %8.3f %10s %12s %9s %9s %10s %9s  %d" % (
            row["run"], row["value"],
            _f(row["interpolated"]), _f(row["world"]), _f(row["governed"]),
            _f(row["published"]), _f(row["realised"]),
            row["first_div"], row["n"]))


def print_emitted(runs):
    """Show the task's required emit line for every width-live frame."""
    print("\n" + "-" * 78)
    print("PART B — PER-OPEN_PLAY-FRAME EMIT (bc_x/interp/world/governed/"
          "published/realised)")
    print("-" * 78)
    total = 0
    for r in runs:
        lines = r.get("emitted") or []
        total += len(lines)
        print(f"  run {r['run'] + 1}: {len(lines)} width-live OPEN_PLAY frames"
              + (f"  ERROR: {r['error']}" if r.get("error") else ""))
        # Print the first 12 lines of each edit window so the chain is visible.
        recs = r.get("records") or []
        for e in (r.get("edits") or []):
            win = _settled_window(recs, e["frame"])[:6]
            for x in win:
                print("      " + _live_line(x))
    print(f"  total emitted lines: {total}")
# ─────────────────────────────────────────────────────────────────────
# PART C — WIDTH CLAMP AUDIT (static, from the source)
# ─────────────────────────────────────────────────────────────────────
#
# Classification: LIVE / DEAD / VERIFIER ONLY / LEGACY COMPAT, and whether
# the symbol can alter width BEFORE publish_formation_width(...).

CLAMP_AUDIT = (
    # (symbol, value, status, alters_width_before_publish, evidence)
    ("min_def_width_m", "30.0", "LIVE", "YES — THE FLOOR",
     "defensive_width.governed_width_m._clamp(): "
     "v = max(min_def_width_m, min(max_def_width_m, v))"),
    ("max_def_width_m", "65.0", "LIVE", "yes (upper rail; effective 64)",
     "same _clamp; effective cap = field_width_m - 2*sideline_margin_m"),
    ("governor_line_width_m", "-", "LIVE", "no (it IS the raw pre-clamp value)",
     "written by governor step 4a; read by governed_width_m"),
    ("governor_width_active", "True (tuner)", "LIVE (gate)",
     "no (selects branch / structured-close clamp gate)",
     "defensive_tuners + world flag written by governor"),
    ("line_width_max_delta_per_frame_m", "1.0", "LIVE", "smoothing only",
     "governor compute_line_state; caps per-frame width change"),
    ("min_defender_gap_m", "3.0", "LIVE (COUNT feasibility only)",
     "no span authority — bounds n_side",
     "decide_side_counts: n_side <= floor((width/2 - 2)/min_gap) + 1"),
    ("max_defender_gap_m", "30.0", "DEAD (removed from lane_gap_m)", "no",
     "lane_gap_m has no ceiling clamp since 2026-09-14"),
    ("min_line_width_m", "28.0", "DEAD (legacy _compute_line_width)",
     "no — method unreachable", "defensive_line_governor._compute_line_width"),
    ("base_line_width_m", "40.0", "LEGACY COMPAT",
     "only in no-BC / kickoff zero-state",
     "governor _zero_state / seed_for_kickoff"),
    ("DEF_LINE_TARGET_WIDTH_M", "40.0", "LEGACY / near-dead",
     "no (macro governor disabled)",
     "_publish_macro_width_tuners; macro_width_governor_enabled=False"),
    ("frame_width_targets", "-", "LIVE (X-depth authority)", "no (not Y width)",
     "recompute_width_x publisher"),
)


def print_part_c():
    print("\n" + "=" * 78)
    print("PART C — WIDTH CLAMP AUDIT (readers/writers of the width clamps)")
    print("=" * 78)
    print("  %-34s %-8s %-26s %s" % (
        "symbol", "value", "status", "can alter width before publish?"))
    for sym, val, status, alters, _ev in CLAMP_AUDIT:
        print("  %-34s %-8s %-26s %s" % (sym, val, status, alters))
    print("\n  Evidence:")
    for sym, _val, _status, _alters, ev in CLAMP_AUDIT:
        print(f"    {sym:34s} {ev}")


# ─────────────────────────────────────────────────────────────────────
# CSV + SUMMARY WRITERS
# ─────────────────────────────────────────────────────────────────────

RUNG_COLUMNS = (
    "requested", "interpolated", "world", "governed", "published", "realised",
    "first_div", "realised_src", "pub_left", "pub_right", "side_left",
    "side_right", "bc_x", "ruck_y", "n_left", "n_right", "reducers",
)

LIVE_COLUMNS = (
    "run", "value", "edit_frame", "n", "requested", "interpolated", "world",
    "governed", "published", "realised", "bc_x", "ruck_y", "gov_active_frac",
    "first_div",
)


def write_rung_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=RUNG_COLUMNS)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: r.get(k) for k in RUNG_COLUMNS})


def write_live_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=LIVE_COLUMNS)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: r.get(k) for k in LIVE_COLUMNS})


def write_summary(path, part_a, part_b, emitted_total, runs):
    lines = []
    lines.append("WIDTH FLOOR / WIDTH-AUTHORITY SATURATION — SUMMARY")
    lines.append("=" * 78)
    lines.append("")
    lines.append("DETERMINISTIC CHAIN (all three tuners equal):")
    lines.append("  %8s %10s %10s %10s %10s %10s  %s" % (
        "request", "interp", "world", "governed", "published", "realised",
        "first_div"))
    for r in part_a:
        lines.append("  %8.3f %10.3f %10.3f %10.3f %10.3f %10.3f  %s" % (
            r["requested"], r["interpolated"], r["world"], r["governed"],
            r["published"], r["realised"], r["first_div"]))
    lines.append("")
    lines.append("LIVE ENGINE MATRIX (settled windows):")
    if part_b:
        lines.append("  %5s %8s %10s %10s %10s %10s %10s  %s" % (
            "run", "request", "interp", "world", "governed", "published",
            "realised", "first_div"))
        for r in part_b:
            def _f(v):
                return "n/a" if v is None else ("%10.3f" % v)
            lines.append("  %5d %8.3f %10s %10s %10s %10s %10s  %s" % (
                r["run"], r["value"],
                _f(r["interpolated"]), _f(r["world"]), _f(r["governed"]),
                _f(r["published"]), _f(r["realised"]), r["first_div"]))
    else:
        lines.append("  (no width-live windows)")
    lines.append("")
    lines.append(f"emitted width-live OPEN_PLAY lines: {emitted_total}")
    lines.append("")
    lines.append("run status:")
    for r in runs:
        state = ("ABORT " + r["error"]) if r.get("error") else "ok"
        lines.append(f"  run {r['run'] + 1}: records={len(r['records'])} "
                     f"edits={len(r['edits'])} emitted={len(r['emitted'])} "
                     f"{state}")
    lines.append("")
    lines.append("CLAMP AUDIT:")
    for sym, val, status, alters, ev in CLAMP_AUDIT:
        lines.append(f"  {sym:34s} value={val:8s} {status:26s} "
                     f"alters={alters}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 78)
    print("WIDTH FLOOR / WIDTH-AUTHORITY SATURATION — ATTRIBUTION PROBE")
    print("=" * 78)

    part_a = run_part_a()
    write_rung_csv("width_floor_rungs.csv", part_a)

    print_part_c()

    runs = []
    res = None
    for attempt in range(3):
        res = run_live_once(0, attempt)
        if not res.get("error"):
            break
        print(f"[retry] live run aborted: {res['error']}")
    runs.append(res)
    part_b = summarise_matrix(runs)
    print_part_b(part_b)
    print_emitted(runs)
    write_live_csv("width_floor_live.csv", part_b)

    emitted_total = sum(len(r.get("emitted") or []) for r in runs)
    summary = write_summary(
        "width_floor_summary.txt", part_a, part_b, emitted_total, runs)
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(summary)
    print("artefacts: width_floor_rungs.csv, width_floor_live.csv, "
          "width_floor_summary.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
