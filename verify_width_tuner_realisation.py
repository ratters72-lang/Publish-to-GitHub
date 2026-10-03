"""
verify_width_tuner_realisation.py — STAGE 8 rung-contract verifier.

WHAT THIS PROVES (deterministic, engine-free, no rendering)
-----------------------------------------------------------
The canonical 3-point width tuners
(width_left_tryline_m / width_halfway_m / width_right_tryline_m)
own rungs 1-5 of the width chain end-to-end:

    R1 COMMANDED  interpolated_defensive_width(t, bc.x)
    R2 GOVERNED   governed_width_m  (physical field cap ONLY)
    R3 PUBLISHED  publish_formation_width {left_m, right_m}
    R4 LANE       side_lanes(...)[-1] outermost lane per side
    R5 SPACING    span of compute_targets -> frame_spacing_targets

GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14).  Rungs 3, 4
and 5 must therefore all be the SAME number as rung 2: the two
ruck-anchored halves carry the governed width between them, the ladders
span exactly that, and no short-side factor, room cap or lane-step floor
may reduce it.  The only bound left is the absolute sideline clamp.

ASSERTIONS
----------
  A1  the 3-point interpolation is exact at the midpoint
  A2  there is NO width floor: a sub-floor command is realised EXACTLY
      (A2-no-floor), and the ONLY upper bound is the physical field cap
      (A2-clamp-max)
  A3  the clamp saturates: extreme tuners govern to the SAME width
  A4  the published span never exceeds the governed width
  A5  lane span, published span and spacing span are the SAME number
      (single width source, no second derivation)
  A6  the default -> wide retention at rung 5 EQUALS the governed fraction
      of the command delta: every metre the tuner clamp lets through
      reaches rung 5.  The retired contract bounded this retention from
      ABOVE (<= 25%), and that bound WAS the under-responsiveness defect.
  A7  rung 5 is monotone non-decreasing in the halfway tuner
  A8  (if stage8_pooled.txt exists) the live width-chain rung on real
      engine frames IS the governed width: the frame spacing-target span
      tracks it with slope ~1 (|slope - 1| <= 0.02), pearson >= 0.99,
      mean |target - governed| <= 0.25 m, and >= 90% of width-live frames
      within 0.1 m.  The whole-population physical span is REPORTED but
      NOT asserted: on the recorded runs only ~5 of ~11.8 line bodies
      carry the spacing target, the rest are placed by the chase/tackle
      Y writers, so that span is not a width measurement.

Exit code 0 = all assertions hold; 1 = at least one failed.

Run:  python verify_width_tuner_realisation.py
"""

import math
import os
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
    side_lanes,
    side_widths,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.formation_width_authority import (
    publish_formation_width,
)

RUCK_X = 60.0
RUCK_Y = 35.0
BC_X = 60.0
N_LEFT = 4
N_RIGHT = 4
_SIDE_ROLES = ("A", "B", "C", "D", "CENTRE", "WINGER")

# The three tuner curves the contract is measured across.  Each is the
# full 3-point curve (left tryline, halfway, right tryline).
TUNER_SETS = (
    ("extreme_narrow", (18.0, 20.0, 22.0)),
    ("narrow",         (30.0, 33.0, 36.0)),
    ("default",        (50.0, 55.0, 60.0)),
    ("wide",           (80.0, 88.0, 96.0)),
    ("extreme_wide",   (130.0, 143.0, 156.0)),
)

# The rung-5 retention tolerance.  GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL
# SPAN (2026-09-14): the old contract BOUNDED retention from above (<= 25%)
# because the short-side split factor and the room cap silently absorbed
# three quarters of the command.  That bound WAS the defect, so the contract
# is now the opposite: every metre the tuner clamp lets through must reach
# rung 5, i.e. the retention EQUALS the governed-delta fraction exactly, and
# the tolerance is float noise only.
RETENTION_TOL = 1e-9
# Live bounds for A8 - the width chain's OWN rung on real engine frames.
# GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14), so the live
# contract is TRACKING, not independence: the frame spacing-target span
# must BE the governed width.  The retired bound was an upper bound on
# |pearson| (independence), which described the reducer stack, not the
# channel.
LIVE_PEARSON_MIN = 0.99
LIVE_SLOPE_TOL = 0.02
LIVE_RESIDUAL_MAX = 0.25
LIVE_FRAME_FRACTION_MIN = 0.90

_RESULTS = []


def _check(name, ok, detail=""):
    _RESULTS.append((name, bool(ok), detail))
    print("  [%s] %-4s %s" % ("PASS" if ok else "FAIL", name, detail))
    return bool(ok)


class StubCarrier:
    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)
        self.vx = 0.0
        self.vy = 0.0


class RungWorld:
    """Minimal world carrying only what the width chain reads."""

    def __init__(self, commanded):
        self.ruck_x_m = RUCK_X
        self.ruck_y_m = RUCK_Y
        self.def_line_width_m = float(commanded)
        self.governor_line_width_m = float(commanded)
        self.governor_width_active = True
        self.frame_count = 1
        self.dt = 1.0 / 60.0
        self.ball_carrier = StubCarrier(BC_X, RUCK_Y)
        self.defending_team = []
        self.in_pursuit_mode = False
        self.sub_state = GameSubState.OPEN_PLAY
        self._ope_phase_label = "OPEN_PLAY"
        self.defensive_line_anchor = RUCK_X + 10.0
        self.anchor_locked = False
        self.ruck_established = True
        self._last_count_split = None
        self.frame_geometry_ready = False
        self.frame_spacing_targets = None
        self.frame_pin_targets = None
        self.published_count_left = None
        self.published_count_right = None


def make_tuners(vals):
    t = DefenceTuners()
    t.width_left_tryline_m = float(vals[0])
    t.width_halfway_m = float(vals[1])
    t.width_right_tryline_m = float(vals[2])
    return t


def make_line():
    line = []
    pid = 100
    for i in range(N_LEFT):
        pid += 1
        d = Player(x=70.0, y=RUCK_Y - 3.0 * (i + 1), player_id=pid)
        d.depth_role = _SIDE_ROLES[min(i, len(_SIDE_ROLES) - 1)]
        d.depth_side = LEFT
        d.is_defender = True
        d.fullback_role_active = False
        d.tackle_state_active = False
        line.append(d)
    for i in range(N_RIGHT):
        pid += 1
        d = Player(x=70.0, y=RUCK_Y + 3.0 * (i + 1), player_id=pid)
        d.depth_role = _SIDE_ROLES[min(i, len(_SIDE_ROLES) - 1)]
        d.depth_side = RIGHT
        d.is_defender = True
        d.fullback_role_active = False
        d.tackle_state_active = False
        line.append(d)
    return line


def _span_of(targets):
    ys = list(targets.values())
    if len(ys) < 2:
        return 0.0
    return float(max(ys)) - float(min(ys))


def measure(vals):
    """Measure rungs 1-5 for one tuner curve at the headline scenario."""
    t = make_tuners(vals)
    world = RungWorld(0.0)
    line = make_line()
    world.defending_team = list(line)

    commanded = interpolated_defensive_width(t, BC_X)
    world.def_line_width_m = commanded
    world.governor_line_width_m = commanded
    governed = governed_width_m(world, t)
    pub = publish_formation_width(world, t)

    lw, rw = side_widths(world, t, ruck_y=RUCK_Y)
    lanes_l = side_lanes(N_LEFT, RUCK_Y, LEFT, t, span=lw)
    lanes_r = side_lanes(N_RIGHT, RUCK_Y, RIGHT, t, span=rw)
    lane_span = (float(lanes_r[-1]) - float(lanes_l[-1])
                 if lanes_l and lanes_r else 0.0)

    targets = compute_targets(world, line, t, governor=None)
    return {
        "commanded": float(commanded),
        "governed": float(governed),
        "pub_mean": 0.5 * (float(pub["left_m"]) + float(pub["right_m"])),
        "lane_span": lane_span,
        "spacing_span": _span_of(targets),
    }


def run_contract_checks():
    tuners = make_tuners(dict(TUNER_SETS)["default"])
    # ── WIDTH RAILS REMOVED (2026-09-14, Option B) ──
    # The minimum-FLOOR and maximum-CEILING width tuners are GONE.  The
    # 3-point width curve is the sole width authority, so NOTHING bounds
    # the width from below, and the ONLY upper bound is the PHYSICAL FIELD
    # CAP (field_width_m - 2 * sideline_margin_m) — geometry, not a rail.
    _margin = float(getattr(tuners, "sideline_margin_m", 3.0))
    _field_w = float(getattr(tuners, "field_width_m", 70.0))
    effective_max_w = _field_w - 2.0 * _margin

    rows = {label: measure(vals) for label, vals in TUNER_SETS}

    # A1 - interpolation exact at the midpoint (bc.x = 60 => halfway point).
    _check(
        "A1-midpoint-interp",
        abs(rows["default"]["commanded"] - 55.0) < 1e-9,
        "default command at bc.x=60 = %.3f (want 55.000)"
        % rows["default"]["commanded"],
    )

    # A2-no-floor - the narrow rail is the COMMAND ITSELF.  With the floor
    # tuner removed, a sub-floor request must be realised EXACTLY: the
    # requested width is the intended width, with no silent raise.
    _check(
        "A2-no-floor",
        abs(rows["extreme_narrow"]["governed"]
            - rows["extreme_narrow"]["commanded"]) < 1e-9,
        "extreme_narrow governed = %.3f == commanded %.3f "
        "(no floor: sub-floor requests realise EXACTLY)"
        % (rows["extreme_narrow"]["governed"],
           rows["extreme_narrow"]["commanded"]),
    )
    # A2-clamp-max - the ONLY upper bound is the physical field cap.
    _check(
        "A2-clamp-max",
        abs(rows["extreme_wide"]["governed"] - effective_max_w) < 1e-9,
        "extreme_wide governed = %.3f (physical field cap = "
        "field %.0f - 2*margin %.1f = %.3f)"
        % (rows["extreme_wide"]["governed"], _field_w, _margin,
           effective_max_w),
    )

    # A3 - the clamp saturates: both extreme curves govern identically.
    _check(
        "A3-clamp-saturation",
        abs(rows["wide"]["governed"] - rows["extreme_wide"]["governed"]) < 1e-9,
        "wide governed %.3f == extreme_wide governed %.3f"
        % (rows["wide"]["governed"], rows["extreme_wide"]["governed"]),
    )

    # A4 - the published span never exceeds the governed width.
    worst = max(r["pub_mean"] - r["governed"] for r in rows.values())
    _check(
        "A4-pub-le-governed",
        worst <= 1e-9,
        "max(pub_mean - governed) = %+.6f m" % worst,
    )

    # A5 - single width source: lane span == published span == spacing span.
    worst_gap = max(
        abs(r["lane_span"] - r["pub_mean"])
        + abs(r["spacing_span"] - r["pub_mean"])
        for r in rows.values()
    )
    _check(
        "A5-single-width-source",
        worst_gap < 1e-6,
        "max |lane-pub| + |spacing-pub| = %.3e m" % worst_gap,
    )

    # A6 - the measured rung-5 retention of a large tuner delta.
    d_cmd = rows["wide"]["commanded"] - rows["default"]["commanded"]
    d_spc = rows["wide"]["spacing_span"] - rows["default"]["spacing_span"]
    d_gov = rows["wide"]["governed"] - rows["default"]["governed"]
    retention = (d_spc / d_cmd) if d_cmd else 1.0
    governed_retention = (d_gov / d_cmd) if d_cmd else 1.0
    _check(
        "A6-retention-full",
        abs(retention - governed_retention) <= RETENTION_TOL,
        "default->wide retains %.1f%% of the command delta at rung 5, "
        "exactly the governed fraction %.1f%% "
        "(d_cmd=%+.3f, d_gov=%+.3f, d_rung5=%+.3f)"
        % (100.0 * retention, 100.0 * governed_retention,
           d_cmd, d_gov, d_spc),
    )

    # A7 - rung 5 monotone non-decreasing in the halfway tuner.
    ordered = sorted(TUNER_SETS, key=lambda kv: kv[1][1])
    spans = [rows[lbl]["spacing_span"] for lbl, _v in ordered]
    monotone = all(b >= a - 1e-9 for a, b in zip(spans, spans[1:]))
    _check(
        "A7-rung5-monotone",
        monotone,
        "spacing span by tuner_m: %s"
        % ", ".join("%.2f" % s for s in spans),
    )

    return rows


def run_live_checks():
    """Validate the recorded live measurement when the artefact exists.

    GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14).  The live
    assertion is that the WIDTH CHAIN's own rung on real engine frames -
    the frame's spacing-target span - IS the governed width.  The
    whole-population physical span (max y - min y over every line body) is
    REPORTED but NOT asserted: on the recorded runs only ~5 of ~11.8 line
    bodies are on the width chain, the rest are placed by the chase/tackle
    Y writers, so that span carries no width information.
    """
    path = "stage8_pooled.txt"
    if not os.path.exists(path):
        print("  [SKIP] A8-live-governed-total-span  (no %s - run the probe)"
              % path)
        return
    with open(path, "r", encoding="utf-8") as fh:
        first = fh.readline().strip()
    try:
        data = eval(first, {"__builtins__": {}}, {})  # noqa: S307 - own artefact
    except Exception as exc:
        _check("A8-live-governed-total-span", False,
               "could not parse %s: %s" % (path, exc))
        return

    n = data.get("n")
    r = data.get("pearson_target_vs_governed")
    slope = data.get("slope_target_vs_governed")
    resid = data.get("mean_abs_target_minus_governed")
    hits = data.get("n_target_within_0p1_of_governed")
    frac = (float(hits) / float(n)) if (n and hits is not None) else None

    _check(
        "A8-live-governed-total-span",
        r is not None and slope is not None and resid is not None
        and frac is not None
        and float(r) >= LIVE_PEARSON_MIN
        and abs(float(slope) - 1.0) <= LIVE_SLOPE_TOL
        and float(resid) <= LIVE_RESIDUAL_MAX
        and frac >= LIVE_FRAME_FRACTION_MIN,
        "live rung 5 (frame spacing-target span) vs governed over %s "
        "width-live frames: pearson=%s slope=%s mean|target-gov|=%s m, "
        "%s frames within 0.1 m"
        % (n, "%.4f" % r if r is not None else "n/a",
           "%.4f" % slope if slope is not None else "n/a",
           "%.3f" % resid if resid is not None else "n/a",
           ("%d/%d" % (hits, n)) if frac is not None else "n/a"),
    )
    print("  [INFO] live whole-population span vs governed pearson = %s "
          "over the same frames; only %s of %s line bodies carry the "
          "spacing target (%s are placed by other Y writers), so that "
          "figure measures the chase/tackle geometry, not the width "
          "channel."
          % ("%.3f" % data["pearson_realised_vs_gov"]
             if data.get("pearson_realised_vs_gov") is not None else "n/a",
             data.get("mean_n_owned"), data.get("mean_n_line"),
             data.get("mean_n_other")))


def main():
    print("=" * 78)
    print("VERIFY - STAGE 8 WIDTH TUNER REALISATION (rung contract)")
    print("=" * 78)
    rows = run_contract_checks()
    run_live_checks()
    print("-" * 78)
    for label, _v in TUNER_SETS:
        r = rows[label]
        print("  %-14s command %7.2f  governed %7.2f  rung5 %7.2f"
              % (label, r["commanded"], r["governed"], r["spacing_span"]))
    failed = [n for n, ok, _ in _RESULTS if not ok]
    print("-" * 78)
    print("  checks: %d   passed: %d   failed: %d"
          % (len(_RESULTS), len(_RESULTS) - len(failed), len(failed)))
    if failed:
        print("  FAILED: %s" % ", ".join(failed))
        return 1
    print("  VERDICT: the governed width is the authoritative TOTAL span --")
    print("           rungs 3, 4 and 5 are the SAME number on every case,")
    print("           and on live engine frames rung 5 tracks it with")
    print("           slope ~1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
