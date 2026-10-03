"""
dump_defensive_geometry.py — produce defensive_geometry_dump.txt.

Plain-text debug dump of the defensive geometry / split-spacing model,
matching the ad-hoc "*_dump.txt" artifacts in the repo root.

Dumps:
  1. Environment / tuner inputs relevant to defensive geometry.
  2. Ruck-geometry split model sweep (n=6 and n=10) across ruck_y 0..70:
     dist_to_left, dist_to_right, dist_to_short_side, n_short, n_left, n_right.
  3. Per-scenario deep dump (ruck_y in {10, 35, 60} x line size {6, 10}):
     governed width, side_widths, band centres, per-defender depth roles +
     compute_targets spacing Y.

Run from the repo root:
    python debug_archive/dump_defensive_geometry.py

Writes defensive_geometry_dump.txt in the current working directory.
"""

import sys
import datetime

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles
from game.defence.defence_lvl_1.defensive_width import governed_width_m
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets,
    compute_spacing_offset,
    side_widths,
    required_side_counts,
    side_band_centre,
    spacing_target_y,
    _compute_ruck_geometry_split,
    _clamped_winger_y,
    ruck_adjacent_anchor,
    LEFT,
    RIGHT,
    SPACING_CHAIN_LENGTH,
    CHAIN_ROLE_ORDER,
)

OUT = "defensive_geometry_dump.txt"
lines = []


def emit(s=""):
    lines.append(s)


class MiniWorld:
    """Minimal world stub — mirrors the verifier MiniWorld constructor."""

    def __init__(self, ruck_x=60.0, ruck_y=35.0, width=40.0):
        self.phase = "OPEN_PLAY"
        self.sub_state = "OPEN_PLAY"
        self.ball_carrier = None
        self.ball = None
        self.ruck_x_m = ruck_x
        self.ruck_y_m = ruck_y
        self.def_line_width_m = width
        self.ball_x_m = ruck_x
        self.ball_y_m = ruck_y
        self.defensive_line_anchor = ruck_x + 10.0
        self.in_pursuit_mode = False
        self.first_tackle_not_made = False
        self.governor_width_active = False
        self.governor_line_width_m = None
        self.sim_time_s = 0.0
        self.frame_count = 0
        self.short_side_width_m = None
        self.open_side_width_m = None
        self.fullback_default_x = 60.0
        self.fullback_default_y = 35.0


def make_line(n, ruck_y=35.0):
    """n defenders spread vertically around ruck_y."""
    defs = []
    for i in range(n):
        # Spread y around the ruck so depth roles form.
        y = ruck_y + (i - (n - 1) / 2.0) * 6.0
        d = Player(x=60.0 + i * 0.5, y=y, player_id=i + 1)
        defs.append(d)
    return defs


def dump_header():
    emit("=" * 72)
    emit("DEFENSIVE GEOMETRY DUMP")
    emit(f"Generated: {datetime.datetime.now().isoformat(timespec='seconds')}")
    emit("=" * 72)
    emit()


def dump_tuners(tuners):
    emit("-" * 72)
    emit("1. TUNER INPUTS")
    emit("-" * 72)
    emit(f"  sideline_margin_m            = {tuners.sideline_margin_m}")
    emit(f"  field_width_m                = {tuners.field_width_m}")
    emit(f"  min_def_width_m              = {tuners.min_def_width_m}")
    emit(f"  max_def_width_m              = {tuners.max_def_width_m}")
    emit(f"  tuner_short_side_factor      = {tuners.tuner_short_side_factor}")
    emit(f"  tuner_open_side_factor       = {tuners.tuner_open_side_factor}")
    emit(f"  governor_width_active        = {tuners.governor_width_active}")
    emit(f"  SPACING_CHAIN_LENGTH         = {SPACING_CHAIN_LENGTH}")
    emit(f"  CHAIN_ROLE_ORDER             = {CHAIN_ROLE_ORDER}")
    emit(f"  ruck_adjacent anchor M       = 2.0 (RUCK_ADJACENT_ANCHOR_M, LAW)")
    emit(f"  role_width_anchor            = {tuners.role_width_anchor}")
    emit(f"  width_curve_factor           = {tuners.width_curve_factor}")
    emit(f"  ENGAGEMENT_SPACING_Y_OFFSET  = {getattr(tuners, 'ENGAGEMENT_SPACING_Y_OFFSET', {})}")
    emit()


def dump_split_sweep(tuners):
    emit("-" * 72)
    emit("2. RUCK-GEOMETRY SPLIT MODEL SWEEP (distance-to-sideline)")
    emit("-" * 72)
    emit("  ruck_y | dist_L | dist_R | dist_short | n_total=6 -> n_short | n_left,n_right | n_total=10 -> n_short | n_left,n_right")
    emit("  ------ | ------ | ------ | ---------- | ------------------------- | ------------------------------ | ---------------------------------")
    margin = tuners.sideline_margin_m
    field_max = tuners.field_width_m - margin
    for ruck_y in range(0, 71, 5):
        dist_left = max(0.0, float(ruck_y) - margin)
        dist_right = max(0.0, field_max - float(ruck_y))
        dist_short = min(dist_left, dist_right)
        is_left_short = dist_left <= dist_right
        n6 = _compute_ruck_geometry_split(6, dist_short)
        n10 = _compute_ruck_geometry_split(10, dist_short)
        n6_l = n6 if is_left_short else 6 - n6
        n6_r = 6 - n6_l
        n10_l = n10 if is_left_short else 10 - n10
        n10_r = 10 - n10_l
        emit(f"  {ruck_y:6.1f} | {dist_left:6.2f} | {dist_right:6.2f} | {dist_short:10.2f} | "
             f"{n6:2d} -> {n6_l:2d},{n6_r:2d} | "
             f"{n10:2d} -> {n10_l:2d},{n10_r:2d}")
    emit()


def dump_scenario(tuners, ruck_y, n):
    emit("-" * 72)
    emit(f"3. SCENARIO: ruck_y={ruck_y}, line size={n}")
    emit("-" * 72)
    world = MiniWorld(ruck_x=60.0, ruck_y=ruck_y, width=40.0)
    line = make_line(n, ruck_y)
    assign_depth_roles(line, world)

    gw = governed_width_m(world, tuners)
    left_w, right_w = side_widths(world, tuners)
    margin = tuners.sideline_margin_m
    field_max = tuners.field_width_m - margin
    dist_left = max(0.0, float(ruck_y) - margin)
    dist_right = max(0.0, field_max - float(ruck_y))
    n_left, n_right = required_side_counts(world, tuners, n, ruck_y=ruck_y)

    emit(f"  world.ruck_y_m              = {ruck_y}")
    emit(f"  governed_width_m            = {gw:.4f}")
    emit(f"  dist_to_left_sideline       = {dist_left:.4f}")
    emit(f"  dist_to_right_sideline      = {dist_right:.4f}")
    emit(f"  required_side_counts        = ({n_left}, {n_right})  [left, right]")
    emit(f"  side_widths (left,right)    = ({left_w:.4f}, {right_w:.4f})")
    emit(f"  left band centre            = {side_band_centre(LEFT, ruck_y, left_w, tuners):.4f}")
    emit(f"  right band centre           = {side_band_centre(RIGHT, ruck_y, right_w, tuners):.4f}")
    emit(f"  left  A anchor / winger Y   = {ruck_adjacent_anchor(ruck_y, LEFT):.4f} / {_clamped_winger_y(left_w, ruck_y, LEFT, tuners):.4f}")
    emit(f"  right A anchor / winger Y   = {ruck_adjacent_anchor(ruck_y, RIGHT):.4f} / {_clamped_winger_y(right_w, ruck_y, RIGHT, tuners):.4f}")

    targets = compute_targets(world, line, tuners)
    emit()
    emit("  PER-DEFENDER COMPUTE_TARGETS:")
    emit("    pid | depth_role | depth_side | target_y")
    for d in sorted(line, key=lambda d: d.player_id):
        ty = targets.get(id(d))
        ty_s = f"{ty:.4f}" if ty is not None else "NONE"
        emit(f"  {d.player_id:4d} | {getattr(d, 'depth_role', None)!s:9s} | "
             f"{getattr(d, 'depth_side', None)!s:10s} | {ty_s}")

    # Canonical even side-lane spacing (WINGER-INWARD) for a full-width side.
    emit()
    emit("  CANONICAL EVEN SIDE-LANE SPACING (spacing_target_y, no live side list):")
    for role in CHAIN_ROLE_ORDER:
        for side, sw in ((LEFT, left_w), (RIGHT, right_w)):
            d = Player(x=60.0, y=ruck_y + side * 10.0, player_id=0)
            d.depth_role = role
            d.depth_side = side
            y = spacing_target_y(d, sw, ruck_y, tuners)
            off = compute_spacing_offset(role, sw, tuners)
            emit(f"    role={role:7s} side={'LEFT' if side == LEFT else 'RIGHT':5s} "
                 f"side_width={sw:6.2f} -> y={y:8.4f}  offset={off:8.4f}")
    emit()


def main():
    tuners = DefenceTuners()
    dump_header()
    dump_tuners(tuners)
    dump_split_sweep(tuners)
    for ruck_y in (10.0, 35.0, 60.0):
        for n in (6, 10):
            dump_scenario(tuners, ruck_y, n)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"Wrote {OUT} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
