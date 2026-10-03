"""
probe_width_floor_20m_followup.py
WIDTH-FLOOR FOLLOW-UP — 30.0 m vs 20.0 m FLOOR (READ-ONLY against production).

CONTEXT
-------
`probe_width_floor_attribution.py` located the width floor at

    game/defence/defence_lvl_1/defensive_width.py :: governed_width_m._clamp()
        min_w = DefenceTuners.min_def_width_m        # 30.0

and proved that every sub-30 request is clamped UP to exactly 30.0 m at the
`governed` rung (governed = published = realised = 30.0).

QUESTION THIS PROBE ANSWERS
---------------------------
If the floor is lowered to 20.0 m, (a) does the realised span now TRACK the
sub-30 request (down to the new floor), and (b) does the engine remain stable
(finite geometry, no abort)?

METHOD — no production file is modified
---------------------------------------
The floor lives on the tuner INSTANCE (`ope.def_tuners.min_def_width_m`), so the
follow-up is an IN-MEMORY attribute write on the live tuner object.  The source
files on disk are never touched.  Two arms are run on the SAME seed and the
SAME frame budget:

    ARM A (baseline)   min_def_width_m = 30.0   (the shipped floor)
    ARM B (follow-up)  min_def_width_m = 20.0   (the reduced floor)

Each arm drives the three width tuners through the matrix on real width-live
OPEN_PLAY frames and records the six-rung width chain plus a stability scan of
every defender / ball / ruck position.

Also a DETERMINISTIC rung table for both floors (pinned stub world), so the
exact clamp arithmetic is visible without engine noise.

Run:  python probe_width_floor_20m_followup.py
Out:  stdout report + width_floor_20m.csv + width_floor_20m_summary.txt
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

# The two floors under comparison.
FLOOR_BASELINE = 30.0
FLOOR_FOLLOWUP = 20.0

# The matrix: sub-30 values, the two floor boundaries, and the first value
# that clears BOTH floors.
TUNER_MATRIX = (0.1, 1.0, 5.0, 10.0, 15.0, 18.0, 20.0, 22.0, 25.0, 28.0, 30.0)

_SIDE_ROLES = ("A", "B", "C", "D", "CENTRE", "WINGER")

# Live engine bounds (mirrors probe_width_floor_attribution.py).
MAX_FRAMES = 4200
LIVE_FRAMES_PER_VALUE = 30

# Stability tolerances.
FINITE_ABS_LIMIT_M = 1000.0


# ─────────────────────────────────────────────────────────────────────
# STUB WORLD + LINE BUILDERS (deterministic arm)
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


def make_tuners(value, floor):
    """All three width tuners held equal at *value*, with *floor* applied."""
    t = DefenceTuners()
    t.width_left_tryline_m = float(value)
    t.width_halfway_m = float(value)
    t.width_right_tryline_m = float(value)
    t.min_def_width_m = float(floor)
    return t


def lane_span(world, targets):
    """abs(lane_max_y - lane_min_y) from the authoritative lane system."""
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


def measure_chain(value, floor, bc_x, ruck_y=RUCK_Y, n_left=4, n_right=4):
    """Measure the six width rungs for one tuner value at one floor."""
    t = make_tuners(value, floor)
    world = RungWorld(ruck_y, 0.0)
    world.ball_carrier = StubCarrier(bc_x, ruck_y)
    line = make_line(n_left, n_right, ruck_y)
    world.defending_team = list(line)

    requested = float(value)
    interp = float(interpolated_defensive_width(t, bc_x))

    # Model the governor having published the interpolated value this frame.
    world.def_line_width_m = interp
    world.governor_line_width_m = interp
    world.governor_width_active = True

    world_pub = float(getattr(world, "def_line_width_m", 0.0))
    governed = float(governed_width_m(world, t))

    pub = publish_formation_width(world, t)
    left_m = float(pub["left_m"])
    right_m = float(pub["right_m"])
    published = 0.5 * (left_m + right_m)
    reducers = dict(pub.get("reducers") or {})

    targets = compute_targets(world, line, t, governor=None)
    realised, src = lane_span(world, targets)

    lw, rw = side_widths(world, t, ruck_y=ruck_y)
    return {
        "floor": float(floor),
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
    }


def first_divergence(row, tol=1e-6):
    """The name of the FIRST rung that differs from the requested width."""
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
# DETERMINISTIC ARM
# ─────────────────────────────────────────────────────────────────────

def run_deterministic(floor):
    """Deterministic rung rows for one floor."""
    rows = []
    for value in TUNER_MATRIX:
        row = measure_chain(value, floor, RUCK_Y)
        row["first_div"] = first_divergence(row)
        rows.append(row)
    return rows


def print_deterministic(floor, rows):
    print("\n" + "-" * 78)
    print(f"DETERMINISTIC CHAIN @ floor = {floor:.1f} m "
          f"(pinned stub, bc.x={RUCK_Y:.0f}, ruck_y={RUCK_Y:.0f}, 4-4)")
    print("-" * 78)
    print("    %8s %10s %10s %10s %10s %10s  %-10s %s" % (
        "request", "interp", "world", "governed", "published", "realised",
        "firstdiv", "reducers"))
    for r in rows:
        print("    %8.3f %10.3f %10.3f %10.3f %10.3f %10.3f  %-10s %s" % (
            r["requested"], r["interpolated"], r["world"], r["governed"],
            r["published"], r["realised"], r["first_div"], r["reducers"]))
