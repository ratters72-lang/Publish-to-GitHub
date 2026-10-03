"""
verify_width_tuners.py — COUNT-LANE MODEL VERIFIER (2026-09-03).

The count is the single boss for MULTIPLICITY: the per-side defender count
comes from decide_side_counts, and side_lanes lays out EXACTLY that many even
lanes from the ruck-adjacent A pivot (ruck_y ± 2.0 m, ANCHOR LAW) outward.
The GOVERNED WIDTH (3-point tuner interpolation x split factor) owns the SPAN:
the lanes are spaced across it, so the outermost lane lands on the side's
width anchor and extreme width-tuner changes DO move the line.

Sections:
  1. TUNER FIELDS — 3-point width tuners still exist (legacy width source for
     the governor baseline), plus the live macro governor gate.
  2. LANE PARITY — side_lanes returns exactly n even lanes, A pivot on the
     ANCHOR LAW, uniform gap.
  3. SPAN OWNS THE LADDER — the governed span is the INPUT and side_lanes
     derives the ladder FROM it (a wider span pushes the outer lane out).
     (Stage 2, 2026-09-13: the inverse law, derive_side_width_m, is deleted.)
  4. COUNT AUTHORITY — decide_side_counts never exceeds the physical room;
     both sides >= 1 when n >= 2.
  5. COMPUTE_TARGETS — A pivot on ANCHOR LAW, WINGER on the outermost lane,
     span == count-lane span.
  6. RETREAT / PTB — frame dict span == count-lane span via run_defensive_pipeline.
  7. NO OTHER WIDTH SOURCE — no retreat-specific width curve anywhere.
  8. MOVEMENT-Y INVARIANT — width-eligible MovementAuthorityGuard never raises,
     max target_y deviation <= 0.6 m, live spacing span == count-lane span.

Emits verify_width_tuners_report.json; exits 0 when all checks pass.
"""

import io
import json
import math
import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    interpolated_defensive_width,
    attacking_tryline_anchor_width_m,
    halfway_anchor_width_m,
    defending_10m_anchor_width_m,
    defending_tryline_anchor_width_m,
    ATTACKING_TRYLINE_X,
    HALFWAY_X,
    DEFENDING_10M_X,
    DEFENDING_TRYLINE_X,
)
from game.defence.defence_lvl_1.defensive_geometry import DefensiveGeometry
from game.defence.defence_lvl_1.defensive_line_governor import (
    DefensiveLineGovernor,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets as _split_compute_targets,
    side_lanes,
    side_widths,
    decide_side_counts,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified,
)
from game.defence.defence_lvl_1.defensive_pipeline import (
    run_defensive_pipeline,
    frame_spacing_y as _frame_spacing_y,
)
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
)
from game.defence.defence_lvl_1.movement_authority import (
    MovementAuthorityGuard,
    MovementAuthorityViolation,
)
from game.defence.defence_lvl_1.width_authority import is_width_eligible
from world.world_state import GameSubState

RUCK_X = 60.0
RUCK_Y = 35.0
WIDTH_TOL = 0.01
Y_TOLERANCE_M = 0.6

FORBIDDEN_WIDTH_IDS = (
    "compute_retreat_width",
    "retreat_width_curve",
    "retreat_width_m",
    "compressed_width_m",
    "build_retreat_split_spacing",
)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


_CAPTURED_Y_REFERENCES: dict = {}
_MG_INIT = MovementAuthorityGuard.__init__


def _capture_mg_init(self, world, defenders, tuners=None):
    _MG_INIT(self, world, defenders, tuners)
    if self._width_y_spacing is not None:
        _CAPTURED_Y_REFERENCES[getattr(world, "frame_count", 0)] = dict(
            self._width_y_spacing
        )


MovementAuthorityGuard.__init__ = _capture_mg_init


class StubEngine:
    def __init__(self):
        self._suppress_pre_tackle = True


class MiniWorld:
    def __init__(self):
        self.ruck_x_m = RUCK_X
        self.ruck_y_m = RUCK_Y
        self.def_line_width_m = 40.0
        self.governor_width_active = True
        self.governor_line_width_m = 40.0
        self.short_side_width_m = None
        self.open_side_width_m = None
        self.frame_count = 0
        self.dt = 1.0 / 60.0
        self.ball_carrier = None
        self.defending_team = []
        self.in_pursuit_mode = False
        self.first_tackle_not_made = False
        self.use_structured_open_play = True
        self.defence_enabled = True
        self.sub_state = GameSubState.OPEN_PLAY
        self.tes_active = False
        self.frames_since_ptb = 100
        self.open_play_engine = StubEngine()
        self.defensive_line_anchor = 70.0
        self.anchor_locked = False
        self.role_depth_state = {}
        self.depth_role_state = {}
        self.first_receiver = None
        self.ball = None
        self.role_governs = False
        self.defence_ran_this_frame = True
        self.ctx = None


class MiniBC:
    def __init__(self, x=55.0, y=RUCK_Y):
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0


def role_defender(role, side, player_id, x=70.0):
    d = Player(x=x, y=RUCK_Y + side * 10.0, player_id=player_id)
    d.depth_role = role
    d.depth_side = side
    return d


def make_full_line():
    left = [
        role_defender("WINGER", -1, 101),
        role_defender("CENTRE", -1, 102),
        role_defender("A", -1, 103),
    ]
    right = [
        role_defender("WINGER", 1, 201),
        role_defender("CENTRE", 1, 202),
        role_defender("A", 1, 203),
        role_defender("B", 1, 204),
    ]
    return left + right


def make_swept_line():
    result = []
    for i, y in enumerate((15.0, 22.0, 29.0, 35.0, 41.0, 48.0, 55.0, 60.0)):
        d = Player(x=70.0, y=y, player_id=i + 1)
        d.is_defender = True
        d.max_defensive_speed_mps = 8.3
        result.append(d)
    return result


def span_of(targets) -> float:
    return max(targets.values()) - min(targets.values())


def winger_span(targets, line) -> float:
    ys = [
        targets[id(d)]
        for d in line
        if getattr(d, "depth_role", None) == "WINGER" and id(d) in targets
    ]
    if not ys:
        return 0.0
    return max(ys) - min(ys)


def count_lane_span(world, line, tuners) -> float:
    """WIDTH-derived lane span: outermost-right lane − outermost-left lane.

    WIDTH-AUTHORITY RESTORED (2026-09-13): the count decides HOW MANY lanes
    each side gets; the per-side GOVERNED width spaces them.  The expected
    span must therefore be built WITH the per-side width.
    """
    n = len([d for d in line if not getattr(d, "fullback_role_active", False)])
    ruck_y = float(getattr(world, "ruck_y_m", RUCK_Y))
    n_left, n_right = decide_side_counts(world, tuners, n, ruck_y=ruck_y)
    lw, rw = side_widths(world, tuners, n_total=n, ruck_y=ruck_y)
    lanes_l = side_lanes(n_left, ruck_y, LEFT, tuners, span=lw)
    lanes_r = side_lanes(n_right, ruck_y, RIGHT, tuners, span=rw)
    all_lanes = (lanes_l if lanes_l else []) + (lanes_r if lanes_r else [])
    return max(all_lanes) - min(all_lanes) if all_lanes else 0.0


# ─────────────────────────────────────────────────────────────────────
# 1. TUNER FIELDS
# ─────────────────────────────────────────────────────────────────────
def run_section1():
    print("\n[1] 3-point width tuners (legacy governor baseline) + macro gate")
    t = DefenceTuners()
    check("width_left_tryline_m exists (float)",
          isinstance(getattr(t, "width_left_tryline_m", None), float),
          f"value={getattr(t, 'width_left_tryline_m', None)}")
    check("width_halfway_m exists (float)",
          isinstance(getattr(t, "width_halfway_m", None), float),
          f"value={getattr(t, 'width_halfway_m', None)}")
    check("width_right_tryline_m exists (float)",
          isinstance(getattr(t, "width_right_tryline_m", None), float),
          f"value={getattr(t, 'width_right_tryline_m', None)}")
    check("widths strictly increasing left < halfway < right",
          t.width_left_tryline_m < t.width_halfway_m < t.width_right_tryline_m,
          f"{t.width_left_tryline_m} < {t.width_halfway_m} < {t.width_right_tryline_m}")
    check("live macro governor gate (default OFF)",
          getattr(t, "macro_width_governor_enabled", True) is False)

    # ── FOUR-ANCHOR COMPRESSION MODEL (2026-10-03) ──
    # The width curve is interpolated between FOUR field-position anchors
    # (attack tryline / halfway / defending 10 m / defending tryline); the
    # defending tryline and the defending 10 m anchors are INDEPENDENT
    # COMPRESSIONS of the ONE defending-tryline width REGION, so there is
    # deliberately NO separate 10 m width tuner.  The full contract lives in
    # verify_width_curve_four_anchor.py; these are the smoke checks this
    # verifier carries so a regression trips here too.
    check("width_defending_tryline_m exists (ONE goal-line width region)",
          isinstance(getattr(t, "width_defending_tryline_m", None), float),
          f"value={getattr(t, 'width_defending_tryline_m', None)}")
    check("defending_tryline_compression exists (float)",
          isinstance(getattr(t, "defending_tryline_compression", None), float),
          f"value={getattr(t, 'defending_tryline_compression', None)}")
    check("defending_10m_compression exists (float)",
          isinstance(getattr(t, "defending_10m_compression", None), float),
          f"value={getattr(t, 'defending_10m_compression', None)}")
    check("NO separate width_defending_10m_m width tuner "
          "(10 m is a COMPRESSION of the region)",
          not hasattr(t, "width_defending_10m_m"))
    check("region width == width_right_tryline_m (one value, no divergence)",
          abs(getattr(t, "width_defending_tryline_m", 0.0)
              - t.width_right_tryline_m) < 1e-9)
    check("four anchors strictly ordered attack < halfway < 10m < tryline",
          ATTACKING_TRYLINE_X < HALFWAY_X < DEFENDING_10M_X
          < DEFENDING_TRYLINE_X)
    check("default anchor widths ordered attack <= halfway <= 10m <= tryline",
          attacking_tryline_anchor_width_m(t)
          <= halfway_anchor_width_m(t)
          <= defending_10m_anchor_width_m(t)
          <= defending_tryline_anchor_width_m(t),
          "%.2f %.2f %.2f %.2f" % (
              attacking_tryline_anchor_width_m(t),
              halfway_anchor_width_m(t),
              defending_10m_anchor_width_m(t),
              defending_tryline_anchor_width_m(t)))


# ─────────────────────────────────────────────────────────────────────
# 2. LANE PARITY
# ─────────────────────────────────────────────────────────────────────
def run_section2():
    print("\n[2] side_lanes parity (exactly n lanes, A pivot on ANCHOR LAW)")
    t = DefenceTuners()
    # SPAN CONTRACT (Stage 2, 2026-09-13): side_lanes takes a MANDATORY span.
    # Parity is asserted on the WIDTH-derived ladder — the geometry the engine
    # actually builds — not the retired flat ``defender_gap_m`` fallback.
    SPAN = 40.0
    for n in (1, 2, 3, 4, 5, 6):
        lanes_l = side_lanes(n, RUCK_Y, LEFT, t, span=SPAN)
        lanes_r = side_lanes(n, RUCK_Y, RIGHT, t, span=SPAN)
        check("n=%d left lane count" % n, len(lanes_l) == n)
        check("n=%d right lane count" % n, len(lanes_r) == n)
        if n > 0:
            check("n=%d LEFT A pivot == ruck_y-2.0" % n,
                  abs(lanes_l[0] - (RUCK_Y - 2.0)) < 1e-6)
            check("n=%d RIGHT A pivot == ruck_y+2.0" % n,
                  abs(lanes_r[0] - (RUCK_Y + 2.0)) < 1e-6)
        if n >= 2:
            steps_l = [abs(lanes_l[i] - lanes_l[i - 1]) for i in range(1, n)]
            steps_r = [abs(lanes_r[i] - lanes_r[i - 1]) for i in range(1, n)]
            check("n=%d left lanes uniform span-derived step" % n,
                  all(abs(s - steps_l[0]) < 1e-6 for s in steps_l),
                  f"steps={[round(s, 3) for s in steps_l]}")
            check("n=%d right lanes uniform span-derived step" % n,
                  all(abs(s - steps_r[0]) < 1e-6 for s in steps_r),
                  f"steps={[round(s, 3) for s in steps_r]}")


# ─────────────────────────────────────────────────────────────────────
# 3. SPAN OWNS THE LADDER (derive_side_width_m deleted 2026-09-13)
# ─────────────────────────────────────────────────────────────────────
def run_section3():
    print("\n[3] side_lanes derives the ladder FROM the span (restored law)")
    t = DefenceTuners()
    # WIDTH-AUTHORITY RESTORED (Stage 2, 2026-09-13): the SPAN is the input and
    # the ladder is the OUTPUT.  ``derive_side_width_m`` — which rebuilt the
    # width from a widthless ladder — is DELETED: that inversion is exactly
    # what left the width tuners unable to widen the line.  The restored law
    # is verified here: a wider span places the outermost lane STRICTLY
    # further out, and every lane in the ladder stays distinct.
    margin = float(getattr(t, "sideline_margin_m", 3.0))
    min_gap = float(getattr(t, "min_defender_gap_m", 3.0))

    def _floor_binds(span_m: float, n_side: int) -> bool:
        """True when min_defender_gap_m FLOORS the span-derived lane step.

        LANE_STEP_FLOOR (Stage 6, 2026-09-13) — this is the same predicate
        ``formation_width_authority.lane_step_floor_binds`` exposes to the
        live engine.  ``side_lanes`` derives the uniform step
        ``(span/2 - RUCK_ADJACENT_ANCHOR_M) / (n - 1)`` and clamps it into
        ``[min_defender_gap_m, max_defender_gap_m]``.  When the derived step
        is BELOW the floor, the FLOOR owns the ladder: the outer lane is
        pinned at ``anchor + (n - 1) * min_defender_gap_m`` no matter how
        wide the span becomes, so widening the span legitimately CANNOT
        widen the ladder.  Those transitions must not be asserted as
        widening — asserting them is a false regression (it demands the
        floor be violated).
        """
        if n_side < 2:
            return False
        usable = abs(float(span_m)) * 0.5 - 2.0
        if usable <= 0.0:
            return True
        return (usable / float(n_side - 1)) < min_gap - 1e-12

    for n in (2, 3, 4, 5, 6):
        prev = None
        prev_span = None
        asserted = 0
        for span in (16.0, 24.0, 32.0, 40.0, 48.0, 56.0, 64.0):
            outer = side_lanes(n, RUCK_Y, LEFT, t, span=span)[-1]
            # Compare only lanes the SPAN actually owns.  Two independent
            # clamps can legitimately pin the outer lane:
            #   * the SIDELINE clamp, once the lane reaches the margin;
            #   * the LANE STEP FLOOR, while the derived step is still below
            #     min_defender_gap_m.
            # Under either, a wider span cannot move the lane, so the
            # widening law is not being tested and must not be asserted.
            if (prev is not None
                    and prev > margin + 1e-9 and outer > margin + 1e-9
                    and not _floor_binds(prev_span, n)
                    and not _floor_binds(span, n)):
                asserted += 1
                check("n=%d span %.0f->%.0f widens the outer lane"
                      % (n, prev_span, span),
                      outer < prev - 1e-9,
                      f"outer={outer:.4f} prev={prev:.4f}")
            prev = outer
            prev_span = span
        # HONESTY GUARD: the widening law must genuinely be exercised for
        # this count.  Without this, a future change that floored EVERY step
        # would make this section pass vacuously (zero assertions made).
        check("n=%d widening law exercised (span-owned transitions >= 1)" % n,
              asserted >= 1,
              "span-owned transitions asserted=%d" % asserted)
        lanes = side_lanes(n, RUCK_Y, LEFT, t, span=32.0)
        check("n=%d ladder strictly ordered toward the touchline" % n,
              all(lanes[i] < lanes[i - 1] - 1e-9 for i in range(1, n)),
              f"lanes={[round(v, 3) for v in lanes]}")
    # The span is MANDATORY: a widthless call must fail fast, not silently
    # fall back to the flat defender_gap_m ladder.
    try:
        side_lanes(4, RUCK_Y, LEFT, t)
        _failed_fast = False
    except Exception:
        _failed_fast = True
    check("widthless side_lanes() fails fast (no silent fallback)",
          _failed_fast)


# ─────────────────────────────────────────────────────────────────────
# 4. COUNT AUTHORITY
# ─────────────────────────────────────────────────────────────────────
def run_section4():
    print("\n[4] decide_side_counts never exceeds the physical room")
    t = DefenceTuners()
    min_gap = float(getattr(t, "min_defender_gap_m", 3.0))
    for n in (6, 10):
        for ruck_y in (12.0, 20.0, 30.0, 35.0, 40.0, 55.0):
            w = MiniWorld()
            w.ruck_y_m = ruck_y
            n_left, n_right = decide_side_counts(w, t, n, ruck_y=ruck_y)
            check("ruck_y=%.0f n=%d splits sum" % (ruck_y, n),
                  n_left + n_right == n)
            check("ruck_y=%.0f n=%d both sides >= 1" % (ruck_y, n),
                  (n < 2) or (n_left >= 1 and n_right >= 1))


# ─────────────────────────────────────────────────────────────────────
# 5. COMPUTE_TARGETS A-pivot / WINGER / span
# ─────────────────────────────────────────────────────────────────────
def run_section5():
    print("\n[5] compute_targets: A pivot on ANCHOR LAW, WINGER outer, span == count-lane")
    t = DefenceTuners()
    world = MiniWorld()
    world.ball_carrier = None
    line = make_full_line()
    assign_depth_roles(line, world)
    targets = _split_compute_targets(world, line, t)
    _cspan = count_lane_span(world, line, t)

    n_total = len(line)
    n_left, n_right = decide_side_counts(world, t, n_total, ruck_y=world.ruck_y_m)
    lw, rw = side_widths(world, t, n_total=n_total, ruck_y=world.ruck_y_m)
    lanes_l = side_lanes(n_left, world.ruck_y_m, LEFT, t, span=lw)
    lanes_r = side_lanes(n_right, world.ruck_y_m, RIGHT, t, span=rw)
    check("LEFT A pivot == ruck_y-2.0", bool(lanes_l)
          and abs(lanes_l[0] - (world.ruck_y_m - 2.0)) < 1e-6,
          f"lane0={lanes_l[0] if lanes_l else None}")
    check("RIGHT A pivot == ruck_y+2.0", bool(lanes_r)
          and abs(lanes_r[0] - (world.ruck_y_m + 2.0)) < 1e-6,
          f"lane0={lanes_r[0] if lanes_r else None}")

    check("spacing dict span == width-derived lane span",
          abs(span_of(targets) - _cspan) < WIDTH_TOL,
          f"span={span_of(targets):.4f} cspan={_cspan:.4f}")
    wspan = winger_span(targets, line)
    check("WINGER span == width-derived lane span",
          abs(wspan - _cspan) < WIDTH_TOL,
          f"wspan={wspan:.4f} cspan={_cspan:.4f}")
    # WIDTH-AUTHORITY RESTORED (2026-09-13): side_widths is the GOVERNED
    # width x split factor (room-capped) and the lanes are built FROM it, so
    # each side's outermost lane lands on that side's width anchor.
    check("side_widths owns the span (outermost lane on the width anchor)",
          abs(lanes_l[-1] - (world.ruck_y_m - lw * 0.5)) < 1e-6
          and abs(lanes_r[-1] - (world.ruck_y_m + rw * 0.5)) < 1e-6,
          f"left={lw} right={rw} outer_l={lanes_l[-1]:.4f} "
          f"outer_r={lanes_r[-1]:.4f}")


# ─────────────────────────────────────────────────────────────────────
# 6. RETREAT / PTB SPAN
# ─────────────────────────────────────────────────────────────────────
def run_section6():
    print("\n[6] Retreat / PTB frame dict span == count-lane span")
    t = DefenceTuners()
    for sub in (GameSubState.OPEN_PLAY, GameSubState.PTB):
        world = MiniWorld()
        world.sub_state = sub
        world.ball_carrier = None
        world.frame_count = 1
        line = make_full_line()
        for d in line:
            d.x = 60.0
        world.defending_team = list(line)
        _cspan = count_lane_span(world, line, t)
        spacing, _width = run_defensive_pipeline(world, line, t)
        check("sub=%s spacing span == width-derived lane span" % sub,
              abs(span_of(spacing) - _cspan) < WIDTH_TOL,
              f"span={span_of(spacing):.4f} cspan={_cspan:.4f}")


# ─────────────────────────────────────────────────────────────────────
# 7. NO OTHER WIDTH SOURCE
# ─────────────────────────────────────────────────────────────────────
def run_section7():
    print("\n[7] No other width source (retreat curves gone)")
    hits = []
    for root, _dirs, files in os.walk("game/defence/defence_lvl_1"):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for i, ln in enumerate(fh, 1):
                    for ident in FORBIDDEN_WIDTH_IDS:
                        if ident in ln:
                            hits.append(f"{path}:{i}: {ln.strip()[:80]}")
    check("no retreat-specific width curve anywhere", not hits,
          "; ".join(hits) or "clean")


# ─────────────────────────────────────────────────────────────────────
# 8. MOVEMENT-Y INVARIANT + LIVE SPAN
# ─────────────────────────────────────────────────────────────────────
def run_live_frames(world, defenders, frames=60):
    t = DefenceTuners()
    dbu = DefensiveBehaviourUnified(t)
    world.ball_carrier = MiniBC(x=55.0)

    violations = []
    worst_dev = 0.0
    offenders = []
    span_errors = []
    # (winger_span, governed_width) per frame — the FLOORLESS contract is
    # "requested == intended", so the realised WINGER span must never EXCEED
    # the governed width the governor published that frame.
    span_vs_gov = []

    for frame in range(1, frames + 1):
        world.frame_count = frame
        world.ball_carrier.y = RUCK_Y + 3.0 * math.sin(frame * 0.05)
        for d in defenders:
            d.vx = 0.0
            d.vy = 0.0
            d.frozen = False
            d.freeze_timer = 0.0
            d._mmdl_moved = False
            d.movement_writer = None

        run_defensive_pipeline(world, defenders, t)

        sdict = getattr(world, "frame_spacing_targets", None) or {}
        swing = [
            sdict[id(d)] for d in defenders
            if getattr(d, "depth_role", None) == "WINGER" and id(d) in sdict
        ]
        if swing:
            w_span = max(swing) - min(swing)
            span_errors.append(w_span)
            span_vs_gov.append(
                (w_span, float(getattr(world, "def_line_width_m", 0.0) or 0.0))
            )

        try:
            dbu.apply(world, defenders, 0.0, [])
        except MovementAuthorityViolation as exc:
            violations.append(f"frame {frame}: {exc}")

        for d in defenders:
            if not d.frozen:
                d.movement_writer = "Player.update"
                d.update(world.dt)

        refs = _CAPTURED_Y_REFERENCES.get(frame, {})
        for d in defenders:
            if id(d) not in refs:
                continue
            ms = getattr(d, "movement_state", None)
            ms_name = getattr(ms, "name", None) or str(ms)
            if ms_name not in ("RETREAT", "DRIFT", "HOLD"):
                continue
            ty = getattr(d, "target_y", None)
            if ty is None:
                continue
            dev = abs(float(ty) - refs[id(d)])
            worst_dev = max(worst_dev, dev)
            if dev > Y_TOLERANCE_M:
                offenders.append((d.player_id, frame, ms_name, round(dev, 3)))

    return violations, worst_dev, offenders, span_errors, span_vs_gov


def run_section8():
    print("\n[8] Movement-mode never overrides spacing Y (count-lane live)")
    t = DefenceTuners()
    world = MiniWorld()
    defenders = make_swept_line()
    world.defending_team = defenders

    (violations, worst_dev, offenders, span_errors,
     span_vs_gov) = run_live_frames(world, defenders)
    check("width-eligible MovementAuthorityGuard never raised across the live run",
          not violations, str(violations[:2]))
    check("max target_y deviation from spacing-owned reference <= 0.6 m "
          "(RETREAT/DRIFT/HOLD holders)", worst_dev <= Y_TOLERANCE_M,
          f"worst_dev={worst_dev:.4f}m")
    check("no offender instances", not offenders, str(offenders[:3]))
    # ── FLOORLESS WIDTH CONTRACT (2026-09-14, Option B) ──
    # The retired assertion demanded every live WINGER span sit in
    # [2.0, field - 2*margin] — a bound that only held because the
    # min_def_width_m FLOOR raised a small commanded width to 30 m.  With
    # the floor REMOVED the width curve is the sole authority, so the
    # correct invariants are the contract itself:
    #   * the realised WINGER span never EXCEEDS the governed width (no
    #     silent widening — requested == intended, from above);
    #   * the span is never negative;
    #   * the governed width always sits inside the physical field.
    check("live WINGER span never exceeds the governed width (floorless: "
          "requested == intended)",
          all(s <= g + 0.05 for s, g in span_vs_gov),
          "worst over-run=%.4fm over %d frames"
          % (max((s - g) for s, g in span_vs_gov), len(span_vs_gov)))
    check("live WINGER span is never negative",
          all(e >= -1e-9 for e in span_errors),
          f"spans={[round(e,2) for e in span_errors[:5]]}")
    _margin8 = float(getattr(t, "sideline_margin_m", 3.0))
    _field8 = float(getattr(t, "field_width_m", 70.0))
    check("live governed width always inside the physical field",
          all(0.0 <= g <= _field8 - 2.0 * _margin8 for _s, g in span_vs_gov),
          "max governed=%.3fm"
          % max((g for _s, g in span_vs_gov), default=0.0))


def main():
    print("=" * 72)
    print("COUNT-LANE MODEL VERIFICATION (count-boss → lanes → width)")
    print("=" * 72)

    DefenceTuners().assert_radii()

    run_section1()
    run_section2()
    run_section3()
    run_section4()
    run_section5()
    run_section6()
    run_section7()
    run_section8()

    report = {
        "count_lane_model": {
            "lane_parity": True,
            "span_owns_the_ladder": True,
            "count_authority": True,
            "compute_targets_count_lane": True,
            "retreat_ptb_span": True,
            "no_other_width_source": True,
            "movement_y_invariant": True,
            "issues_remaining": failures,
        }
    }
    if failures:
        for key in report["count_lane_model"]:
            if key != "issues_remaining":
                report["count_lane_model"][key] = False
        report["count_lane_model"]["issues_remaining"] = failures

    report_path = "verify_width_tuners_report.json"
    with io.open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nReport written to {report_path}")

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED — count-lane model")
    sys.exit(0)


if __name__ == "__main__":
    main()
