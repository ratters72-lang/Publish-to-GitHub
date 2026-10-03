"""verify_width_tuner_responsiveness.py — WIDTH TUNER AUTHORITY CHECK.

READ-ONLY.  Asserts the contract repaired on 2026-09-13:

    The 3-point width tuners (width_left_tryline_m / width_halfway_m /
    width_right_tryline_m) OWN the lateral span of the defensive line.
    The per-side defender COUNT decides only how many defenders share
    that span.  A large tuner change MUST produce a proportional change
    in the realised defensive width.

Reproduces the original defect measurement: before the fix, sweeping the
tuners 27.5 -> 88.0 left the realised span pinned at ~28.00 m because the
lane generator read a flat ``defender_gap_m`` and the governed width only
ever entered as a one-way inward clamp.
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets,
    side_lanes,
    side_widths,
    lane_gap_m,
    ruck_adjacent_anchor,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.defensive_line_governor import (
    DefensiveLineGovernor,
)
from game.defence.defence_lvl_1.span_contract import SpanContractError
from game.defence.defence_lvl_1.defensive_width import (
    interpolated_defensive_width,
    governed_width_m,
)
from world.world_state import GameSubState

RUCK_X = 60.0
RUCK_Y = 35.0
_ROLES = ("WINGER", "CENTRE", "D", "C", "B", "A")

# Tuner sets spanning the full documented range (tryline / halfway / tryline).
TUNER_SETS = (
    ("-50%", (25.0, 27.5, 30.0)),
    ("-25%", (37.5, 41.25, 45.0)),
    ("default", (50.0, 55.0, 60.0)),
    ("+20%", (60.0, 66.0, 72.0)),
    ("+60%", (80.0, 88.0, 96.0)),
)

failures = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f" -- {detail}" if detail else ""))
    if not cond:
        failures.append(name)


class MiniWorld:
    """Minimal world stub for the spacing pipeline (see probe harness)."""

    def __init__(self, ruck_y=RUCK_Y):
        self.ruck_x_m = RUCK_X
        self.ruck_y_m = ruck_y
        self.def_line_width_m = 40.0
        self.governor_width_active = True
        self.governor_line_width_m = 40.0
        self.frame_count = 1
        self.dt = 1.0 / 60.0
        self.ball_carrier = None
        self.defending_team = []
        self.in_pursuit_mode = False
        self.sub_state = GameSubState.OPEN_PLAY
        self.defensive_line_anchor = 70.0
        self.anchor_locked = False
        self.frame_geometry_ready = False
        self.frame_geometry_frame = None
        self.frame_spacing_targets = None
        self.frame_width_targets = None
        self.frame_side_frame = None


def role_defender(role, side, player_id, ruck_y=RUCK_Y):
    d = Player(x=70.0, y=ruck_y + side * 10.0, player_id=player_id)
    d.depth_role = role
    d.depth_side = side
    d.is_defender = True
    return d


def side_population(n_left, n_right, ruck_y=RUCK_Y):
    line = []
    pid = 100
    for i in range(n_left):
        pid += 1
        line.append(role_defender(_ROLES[min(i, 5)], -1, pid, ruck_y))
    for i in range(n_right):
        pid += 1
        line.append(role_defender(_ROLES[min(i, 5)], 1, pid, ruck_y))
    return line


def build(tuner_vals=None):
    """Return (tuners, world, governor) with the governed width published."""
    t = DefenceTuners()
    if tuner_vals is not None:
        t.width_left_tryline_m = tuner_vals[0]
        t.width_halfway_m = tuner_vals[1]
        t.width_right_tryline_m = tuner_vals[2]
    w = MiniWorld()
    w.def_line_width_m = interpolated_defensive_width(t, RUCK_X)
    w.governor_line_width_m = w.def_line_width_m
    return t, w, DefensiveLineGovernor(t, None)


def measure(tuner_vals=None, n_left=4, n_right=4):
    """Return (governed_width, realised_span, left_targets, right_targets)."""
    t, w, gov = build(tuner_vals)
    line = side_population(n_left, n_right)
    targets = compute_targets(w, line, t, governor=gov)
    lefts = sorted(
        targets[id(d)] for d in line
        if getattr(d, "depth_side", None) == LEFT and id(d) in targets
    )
    rights = sorted(
        targets[id(d)] for d in line
        if getattr(d, "depth_side", None) == RIGHT and id(d) in targets
    )
    ys = list(targets.values())
    span = (max(ys) - min(ys)) if ys else 0.0
    return w.def_line_width_m, span, lefts, rights


def main():
    print("=" * 78)
    print("WIDTH TUNER RESPONSIVENESS - AUTHORITY CONTRACT")
    print("=" * 78)

    print("\n[1] Realised span responds monotonically to the width tuners")
    rows = []
    for label, vals in TUNER_SETS:
        governed, span, _l, _r = measure(vals)
        rows.append((label, vals, governed, span))
        print(f"      {label:8s} tuners={str(vals):22s} "
              f"governed={governed:6.2f} realised={span:6.2f}")
    spans = [r[3] for r in rows]
    # ``governed_width_m`` caps the line at the usable field width
    # (field_width_m - 2 * sideline_margin_m = 70 - 6 = 64 m), so tuner
    # sets ABOVE that ceiling legitimately saturate.  The contract is:
    #   * the span is NON-DECREASING across the whole sweep, and
    #   * it STRICTLY increases wherever the effective width increases.
    effective = []
    for _lbl, vals in TUNER_SETS:
        t_e, w_e, _g_e = build(vals)
        effective.append(governed_width_m(w_e, t_e))
    print(f"      effective governed widths = "
          f"{[round(v, 2) for v in effective]}")
    check("realised span is non-decreasing across the tuner sweep",
          all(b >= a for a, b in zip(spans, spans[1:])), f"spans={spans}")
    check("realised span strictly increases wherever the effective width does",
          all(
              (spans[i] < spans[i + 1])
              if effective[i] < effective[i + 1] - 1e-9 else True
              for i in range(len(spans) - 1)
          ),
          f"spans={spans} effective={[round(v, 2) for v in effective]}")
    check("full-range tuner swing moves the realised span by >= 15 m",
          spans[-1] - spans[0] >= 15.0,
          f"delta={spans[-1] - spans[0]:.2f} m")
    _t_cap = DefenceTuners()
    field_cap = _t_cap.field_width_m - 2.0 * _t_cap.sideline_margin_m
    check("saturation is the physical field cap, not tuner absorption",
          abs(effective[-1] - field_cap) < 1e-6 and spans[-1] > spans[0],
          f"effective_max={effective[-1]:.2f} field_cap={field_cap:.2f}")

    print("\n[2] The old defect is gone (extreme tuners are not absorbed)")
    _g_narrow, span_narrow, _ln, _rn = measure((25.0, 27.5, 30.0))
    _g_wide, span_wide, _lw, _rw = measure((80.0, 88.0, 96.0))
    check("narrow vs wide tuners differ by >= 15 m (was 0.00 m)",
          span_wide - span_narrow >= 15.0,
          f"narrow={span_narrow:.2f} wide={span_wide:.2f}")

    print("\n[3] defender_gap_m no longer owns the span")
    t_def, w_def, gov_def = build((50.0, 55.0, 60.0))
    line_d = side_population(4, 4)
    base_targets = compute_targets(w_def, line_d, t_def, governor=gov_def)
    base_ys = list(base_targets.values())
    base_span = max(base_ys) - min(base_ys)
    print(f"      baseline (defender_gap_m={t_def.defender_gap_m}) "
          f"realised={base_span:6.2f}")
    gap_spans = []
    for gap in (3.0, 4.0, 6.0, 8.0):
        t_g, w_g, gov_g = build((50.0, 55.0, 60.0))
        t_g.defender_gap_m = gap
        line_g = side_population(4, 4)
        tg = compute_targets(w_g, line_g, t_g, governor=gov_g)
        ys = list(tg.values())
        gap_spans.append(max(ys) - min(ys))
        print(f"      defender_gap_m={gap:4.1f} realised={gap_spans[-1]:6.2f}")
    check("defender_gap_m is inert while a governed width is supplied",
          max(gap_spans) - min(gap_spans) < 1e-6,
          f"spread={max(gap_spans) - min(gap_spans):.6f} m")

    print("\n[4] A-pivot invariance (ruck_y +/- 2.0 m, never moved)")
    _g, _span, lefts, rights = measure((50.0, 55.0, 60.0))
    check("innermost LEFT target is exactly ruck_y - 2.0",
          abs(lefts[-1] - ruck_adjacent_anchor(RUCK_Y, LEFT)) < 1e-9,
          f"got {lefts[-1]:.6f} want {ruck_adjacent_anchor(RUCK_Y, LEFT):.6f}")
    check("innermost RIGHT target is exactly ruck_y + 2.0",
          abs(rights[0] - ruck_adjacent_anchor(RUCK_Y, RIGHT)) < 1e-9,
          f"got {rights[0]:.6f} want {ruck_adjacent_anchor(RUCK_Y, RIGHT):.6f}")

    print("\n[5] Lane integrity - distinct, monotone, outer lane on the anchor")
    for vals in ((25.0, 27.5, 30.0), (50.0, 55.0, 60.0), (80.0, 88.0, 96.0)):
        t, w, _gov = build(vals)
        lw, rw = side_widths(w, t, ruck_y=RUCK_Y)
        # SPAN CONTRACT (Stage 2, 2026-09-13): ``side_lanes`` takes the
        # per-side width as the MANDATORY ``span=`` keyword; the retired
        # ``side_width=`` name was removed when the ladder became
        # span-derived.
        lanes_l = side_lanes(4, RUCK_Y, LEFT, t, span=lw)
        lanes_r = side_lanes(4, RUCK_Y, RIGHT, t, span=rw)
        tag = f"tuners={vals[1]:.1f}"
        check(f"{tag}: LEFT lanes distinct",
              len(set(lanes_l)) == len(lanes_l))
        check(f"{tag}: RIGHT lanes distinct",
              len(set(lanes_r)) == len(lanes_r))
        # LEFT lanes run ruck_y - 2 - i*gap, so they are strictly DECREASING
        # in Y as the slot index rises; RIGHT lanes run ruck_y + 2 + i*gap
        # and are strictly INCREASING.  The law is strict MONOTONICITY away
        # from the ruck on each side, not a shared direction.
        check(f"{tag}: LEFT lanes strictly monotone away from the ruck",
              all(b < a for a, b in zip(lanes_l, lanes_l[1:])))
        check(f"{tag}: RIGHT lanes strictly monotone away from the ruck",
              all(b > a for a, b in zip(lanes_r, lanes_r[1:])))
        check(f"{tag}: outer lane lands on the side width anchor",
              abs(lanes_l[-1] - (RUCK_Y - lw * 0.5)) < 1e-6
              and abs(lanes_r[-1] - (RUCK_Y + rw * 0.5)) < 1e-6,
              f"L outer={lanes_l[-1]:.4f} want={RUCK_Y - lw * 0.5:.4f} | "
              f"R outer={lanes_r[-1]:.4f} want={RUCK_Y + rw * 0.5:.4f}")

    # GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14): the
    # ``min_defender_gap_m`` FLOOR is REMOVED from ``lane_gap_m``.  It used to
    # pin the step above ``span / (n-1)`` so the outer lane overran the span,
    # hit the sideline clamp and re-spaced INWARD — the ladder stopped
    # spanning the published width.  The contract is now that the derived step
    # IS the ladder's actual step at every width and count, with no gap clamp.
    print("\n[6] Derived step is FLOORLESS: the published span owns the ladder")
    for vals in ((25.0, 27.5, 30.0), (50.0, 55.0, 60.0), (80.0, 88.0, 96.0)):
        t, w, _gov = build(vals)
        lw, rw = side_widths(w, t, ruck_y=RUCK_Y)
        bad = []
        for width, side_name, side in ((lw, "LEFT", LEFT), (rw, "RIGHT", RIGHT)):
            for n in (2, 3, 4, 5, 6, 8):
                gap = lane_gap_m(width, n, t)
                lanes = side_lanes(n, RUCK_Y, side, t, span=width)
                realised = (abs(float(lanes[-1]) - float(lanes[0]))
                            / float(n - 1))
                if abs(float(gap) - realised) > 1e-9:
                    bad.append(f"{side_name} n={n} step={gap:.4f} "
                               f"ladder={realised:.4f}")
        check(f"tuners={vals[1]:.1f}: derived step == the pure span step "
              f"(no min/max gap clamp)",
              not bad, "; ".join(bad))

    print("\n[7] Widthless call path now FAILS FAST (span is mandatory)")
    t7 = DefenceTuners()
    # SPAN CONTRACT (Stage 2, 2026-09-13): ``span`` is MANDATORY — a
    # widthless call must raise rather than silently fall back to the flat
    # ``defender_gap_m`` ladder (that fallback was the retired inverse law
    # which let extreme width-tuner changes be absorbed).
    try:
        side_lanes(4, RUCK_Y, LEFT, t7)
        failed_fast = False
    except TypeError:
        failed_fast = True
    check("side_lanes() without span fails fast (no silent fallback)",
          failed_fast)
    # ``lane_gap_m(None, ...)`` is the SAME retired widthless path: it must
    # raise under the span contract, not fall back to ``defender_gap_m``.
    # Asserting the raise (rather than the old compat return) keeps this a
    # real guard against re-introducing a silent widthless fallback.
    try:
        lane_gap_m(None, 4, t7)
        gap_failed_fast = False
    except (SpanContractError, TypeError):
        gap_failed_fast = True
    check("lane_gap_m(None, ...) fails fast (no widthless fallback)",
          gap_failed_fast)

    print("\n[8] Field position still behaves (ruck_y sweep)")
    for ruck_y in (15.0, 25.0, 35.0, 45.0, 55.0):
        t = DefenceTuners()
        w = MiniWorld(ruck_y)
        w.def_line_width_m = interpolated_defensive_width(t, RUCK_X)
        w.governor_line_width_m = w.def_line_width_m
        gov = DefensiveLineGovernor(t, None)
        line_r = side_population(4, 4, ruck_y)
        tg = compute_targets(w, line_r, t, governor=gov)
        ys = list(tg.values())
        span = (max(ys) - min(ys)) if ys else 0.0
        check(f"ruck_y={ruck_y:.0f} produces a finite in-field span",
              0.0 < span < 70.0, f"span={span:.2f}")
        print(f"      ruck_y={ruck_y:5.1f} realised={span:6.2f}")

    print("\n[9] Realised span tracks the count too (shared authority)")
    for n_left, n_right in ((3, 3), (4, 4), (5, 5), (6, 5)):
        _g_c, span_c, _lc, _rc = measure((50.0, 55.0, 60.0), n_left, n_right)
        print(f"      split {n_left}-{n_right} "
              f"(n={n_left + n_right}) realised={span_c:6.2f}")
        check(f"split {n_left}-{n_right} is finite and in-field",
              0.0 < span_c < 70.0, f"span={span_c:.2f}")

    print("\n" + "=" * 78)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)")
        for f in failures:
            print(f"   - {f}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
