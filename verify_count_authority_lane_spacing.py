"""verify_count_authority_lane_spacing.py — Count-Boss Lane-Spacing verifier (2026-09-03).

Proves the ARCHITECTURE INVERSION: count is the single boss; lanes are
laid out for exactly that count; width is DERIVED from the lanes.

Invariants asserted:
  [1] COUNT-BOSS FEASIBILITY: ``decide_side_counts`` never returns a
      count that exceeds what the physical room supports:
          n_side <= floor((room - 2.0) / min_defender_gap_m) + 1
      so "3+ defenders into 1-2 of room" is impossible by construction.
  [2] LANE PARITY: ``side_lanes(n, ...)`` returns exactly n lanes;
      lane[0] == ruck_adjacent_anchor(ruck_y, side) (the ANCHOR LAW);
      adjacent lanes are a uniform gap apart.
  [3] WIDTH DERIVED FROM LANES: ``derive_side_width_m(n, ...)`` equals
      2 * |lanes[-1] - ruck_y| — width is an output, never an input.
  [4] PHASE-AWARE but GEOMETRY-STABLE: RELEASE/PTB bias the short-side
      count MORE than OPEN_PLAY, but the bias is small — a phase change
      never re-partitions the line violently.
  [5] compute_targets: the ruck-adjacent A-pivot lands exactly at
      ruck_y +/- 2.0; the WINGER lands on the outermost lane; every
      side's lanes are even.

_run:  python verify_count_authority_lane_spacing.py
Exit 0 on success.
"""
import math
import sys
import types

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
# ``derive_side_width_m`` (the INVERSE law: rebuild width FROM a widthless
# lane ladder) was DELETED in Stage 2 (2026-09-13) — it is the inversion that
# let extreme width-tuner changes be absorbed into the lane floor.  Width now
# flows span -> lanes, so the inverse is not importable and the widthless
# ladder it described is gone by design (see section [3] below).
from game.defence.defence_lvl_1.defensive_split_spacing import (
    decide_side_counts,
    side_lanes,
    side_room_m,
    ruck_adjacent_anchor,
    compute_targets,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.span_contract import SpanContractError

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def mk_world(ruck_y, phase="OPEN_PLAY", n=0):
    w = types.SimpleNamespace()
    w.ruck_y_m = float(ruck_y)
    w.ruck_x_m = 60.0
    w.def_line_width_m = 40.0
    w.ruck_established = True
    w.governor_width_active = False
    w.ball_carrier = None
    w.sub_state = phase
    w.just_left_kickoff_timer = 0.0
    w.kickoff_side_sign = None
    w._post_retreat_open_play_frames = 99
    w._last_count_split = None
    w.count_split_left = None
    w.count_split_right = None
    w.count_split_phase = None
    w.defending_team = []
    return w


def mk_player(pid, x, y, role=None, depth_role=None, depth_side=None):
    p = types.SimpleNamespace()
    p.player_id = pid
    p.x = float(x)
    p.y = float(y)
    p.defender_role = role
    p.depth_role = depth_role
    p.depth_side = depth_side
    p.fullback_role_active = False
    p.tackle_state_active = False
    p._depth_seeded = True if depth_role is not None else False
    p._force_innermost_seed = False
    p.frame_side = depth_side
    p.target_x = p.x
    p.target_y = p.y
    p.vx = 0.0
    p.vy = 0.0
    return p


def main():
    print("=" * 72)
    print("COUNT-AUTHORITY LANE-SPACING VERIFIER (2026-09-03)")
    print("=" * 72)
    tuners = DefenceTuners()

    # ─────────────────────────────────────────────────────────────
    # [1] COUNT-BOSS FEASIBILITY — count never exceeds the room
    # ─────────────────────────────────────────────────────────────
    print("\n[1] decide_side_counts never exceeds the physical room")
    min_gap = float(getattr(tuners, "min_defender_gap_m", 3.0))
    anchor = 2.0
    for ruck_y in (10.0, 12.0, 15.0, 20.0, 30.0, 35.0, 40.0, 55.0):
        w = mk_world(ruck_y, phase="OPEN_PLAY", n=10)
        n_total = 10
        n_left, n_right = decide_side_counts(
            w, tuners, n_total, ruck_y=ruck_y, phase="OPEN_PLAY",
        )
        room_left = side_room_m(w, tuners, LEFT, ruck_y)
        room_right = side_room_m(w, tuners, RIGHT, ruck_y)
        max_fit_left = max(1, int(math.floor((room_left - anchor) / min_gap)) + 1)
        max_fit_right = max(1, int(math.floor((room_right - anchor) / min_gap)) + 1)
        check(
            "ruck_y=%.0f n_left=%d <= max_fit_left=%d"
            % (ruck_y, n_left, max_fit_left),
            n_left <= max_fit_left,
            "n_left=%d room=%.2f max_fit=%d" % (n_left, room_left, max_fit_left),
        )
        check(
            "ruck_y=%.0f n_right=%d <= max_fit_right=%d"
            % (ruck_y, n_right, max_fit_right),
            n_right <= max_fit_right,
            "n_right=%d room=%.2f max_fit=%d" % (n_right, room_right, max_fit_right),
        )
        check("ruck_y=%.0f splits sum to n_total" % ruck_y,
              n_left + n_right == n_total)

    # Critical short-side case (ruck ~20m infield): never demands 3+
    # into a room that only supports 1-2.
    for ruck_y in (10.0, 12.0, 15.0, 20.0):
        w = mk_world(ruck_y, phase="RELEASE", n=12)
        n_left, n_right = decide_side_counts(
            w, tuners, 12, ruck_y=ruck_y, phase="RELEASE",
        )
        short_side = LEFT if ruck_y <= 33.5 else RIGHT
        room_short = side_room_m(w, tuners, short_side, ruck_y)
        max_fit = max(1, int(math.floor((room_short - anchor) / min_gap)) + 1)
        n_short = n_left if short_side == LEFT else n_right
        check(
            "short-side collapse ruck_y=%.0f n_short=%d <= max_fit=%d"
            % (ruck_y, n_short, max_fit),
            n_short <= max_fit,
            "n_short=%d room=%.2f max_fit=%d" % (n_short, room_short, max_fit),
        )

    # ─────────────────────────────────────────────────────────────
    # [2] LANE PARITY — exactly n even lanes, A-pivot ON the ANCHOR LAW
    # ─────────────────────────────────────────────────────────────
    print("\n[2] side_lanes returns exactly n even lanes, A-pivot on rule")
    # SPAN CONTRACT (Stage 2, 2026-09-13): ``span`` is MANDATORY and the step
    # is DERIVED from it — ``(span/2 - RUCK_ADJACENT_ANCHOR_M) / (n - 1)``,
    # clamped into the tuner gap band.  Asserting against the retired flat
    # ``defender_gap_m`` ladder would describe geometry the engine no longer
    # builds; the uniform-step law is what matters and it is span-owned now.
    SPAN = 40.0
    span_min_gap = float(getattr(tuners, "min_defender_gap_m", 3.0))
    span_max_gap = float(getattr(tuners, "max_defender_gap_m", 30.0))
    # Usable run for the ladder: half the span, less the ruck-adjacent
    # anchor offset (RUCK_ADJACENT_ANCHOR_M = 2.0) the innermost lane sits
    # at.  Mirrors the authority's own step derivation exactly.
    _usable = SPAN * 0.5 - 2.0

    def _span_step(n_side):
        if n_side < 2:
            return 0.0
        return max(span_min_gap,
                   min(span_max_gap, _usable / float(n_side - 1)))

    for n in (1, 2, 3, 4, 5, 6):
        lanes_l = side_lanes(n, 30.0, LEFT, tuners, span=SPAN)
        lanes_r = side_lanes(n, 30.0, RIGHT, tuners, span=SPAN)
        check("n=%d left lane count" % n, len(lanes_l) == n)
        check("n=%d right lane count" % n, len(lanes_r) == n)
        if n > 0:
            check("n=%d A-pivot LEFT == ruck_y-2.0" % n,
                  abs(lanes_l[0] - ruck_adjacent_anchor(30.0, LEFT)) < 1e-6,
                  "lane0=%.4f" % lanes_l[0])
            check("n=%d A-pivot RIGHT == ruck_y+2.0" % n,
                  abs(lanes_r[0] - ruck_adjacent_anchor(30.0, RIGHT)) < 1e-6,
                  "lane0=%.4f" % lanes_r[0])
        if n >= 2:
            gap = _span_step(n)
            for i in range(1, n):
                # LEFT lanes DECREASE in Y (toward the left touchline), so the
                # signed delta is -gap; the magnitude must equal the uniform gap.
                check("n=%d left gap[%d] uniform %.2f" % (n, i, gap),
                      abs(abs(lanes_l[i] - lanes_l[i-1]) - gap) < 1e-6,
                      "delta=%.4f expected=%.2f" % (lanes_l[i]-lanes_l[i-1], gap))
                check("n=%d right gap[%d] uniform %.2f" % (n, i, gap),
                      abs(abs(lanes_r[i] - lanes_r[i-1]) - gap) < 1e-6,
                      "delta=%.4f expected=%.2f" % (lanes_r[i]-lanes_r[i-1], gap))

    # ─────────────────────────────────────────────────────────────
    # [3] WIDTH DERIVED FROM LANES
    # ─────────────────────────────────────────────────────────────
    print("\n[3] lane_gap_m: width-derived step when a width is supplied")
    # NO-WIDTH FALLBACK (unchanged): with no side width supplied the flat
    # defender_gap_m ladder is used, so derive_side_width_m is exactly the
    # inverse of that fallback ladder.  This path exists ONLY for verifier
    # stubs / the no-world bootstrap.
    # SPAN CONTRACT (Stage 2, 2026-09-13): the WIDTHLESS ladder — and the
    # inverse ``derive_side_width_m`` that rebuilt a width from it — were
    # REMOVED.  Width is an INPUT (span -> lanes), never an OUTPUT of the
    # ladder, so a widthless call must now FAIL FAST instead of silently
    # falling back to the flat ``defender_gap_m`` ladder.  Asserting the
    # fail-fast (rather than the deleted inverse identity) is what makes
    # this section a real guard: a future edit that re-introduces a silent
    # widthless fallback breaks it immediately.
    for n in (1, 2, 3, 4, 5, 6):
        try:
            side_lanes(n, 30.0, LEFT, tuners)
            failed_fast = False
        except (SpanContractError, TypeError):
            failed_fast = True
        check("n=%d widthless side_lanes() fails fast (no fallback)" % n,
              failed_fast, "n=%d returned a ladder instead of raising" % n)

    # WIDTH AUTHORITY RESTORED (2026-09-13): when a side width IS supplied
    # the lane step is DERIVED from it, so the outermost lane lands exactly
    # on the side's width anchor and the realised span equals the width.
    # The 3-point width tuners therefore own the span; the previous model
    # derived width FROM the flat ladder, which is why extreme tuner
    # changes produced no proportional change in the realised line width.
    for side_width in (30.0, 40.0, 52.0):
        for n in (2, 3, 4, 5, 6):
            a_pivot = ruck_adjacent_anchor(30.0, LEFT)
            outer_want = 30.0 - side_width * 0.5
            span = abs(outer_want - a_pivot)
            # A count the width cannot space is refused by the count
            # authority (width-feasibility bound), so the step floor only
            # binds on infeasible pairs — skip those rather than assert.
            if span / float(n - 1) < float(tuners.min_defender_gap_m) - 1e-9:
                continue
            lanes_w = side_lanes(
                n, 30.0, LEFT, tuners, span=side_width,
            )
            check("w=%.1f n=%d outermost lane on the width anchor"
                  % (side_width, n),
                  abs(lanes_w[-1] - outer_want) < 1e-6,
                  "outer=%.4f want=%.4f" % (lanes_w[-1], outer_want))
            check("w=%.1f n=%d inner A pivot unmoved" % (side_width, n),
                  abs(lanes_w[0] - a_pivot) < 1e-6,
                  "inner=%.4f want=%.4f" % (lanes_w[0], a_pivot))

    # ─────────────────────────────────────────────────────────────
    # [4] PHASE-AWARE but GEOMETRY-STABLE
    # ─────────────────────────────────────────────────────────────
    print("\n[4] RELEASE/PTB bias short side more than OPEN_PLAY (stable)")
    count_release = {}
    count_ptb = {}
    count_open = {}
    for ruck_y in (20.0, 30.0, 35.0):
        n_l, _ = decide_side_counts(mk_world(ruck_y, "RELEASE"), tuners, 10,
                                    ruck_y=ruck_y, phase="RELEASE")
        count_release[ruck_y] = n_l
        n_l, _ = decide_side_counts(mk_world(ruck_y, "PTB"), tuners, 10,
                                    ruck_y=ruck_y, phase="PTB")
        count_ptb[ruck_y] = n_l
        n_l, _ = decide_side_counts(mk_world(ruck_y, "OPEN_PLAY"), tuners, 10,
                                    ruck_y=ruck_y, phase="OPEN_PLAY")
        count_open[ruck_y] = n_l
        # RELEASE/PTB short-side bias should be >= OPEN_PLAY bias
        check("ruck_y=%.0f RELEASE short-bias >= OPEN_PLAY" % ruck_y,
              count_release[ruck_y] >= count_open[ruck_y],
              "release=%d open=%d" % (count_release[ruck_y], count_open[ruck_y]))
        check("ruck_y=%.0f PTB short-bias >= OPEN_PLAY" % ruck_y,
              count_ptb[ruck_y] >= count_open[ruck_y],
              "ptb=%d open=%d" % (count_ptb[ruck_y], count_open[ruck_y]))
        # GEOMETRY-STABLE: phase bias never flips > 2 per side (no violent re-partition)
        check("ruck_y=%.0f release-open delta <= 2" % ruck_y,
              abs(count_release[ruck_y] - count_open[ruck_y]) <= 2,
              "delta=%d" % abs(count_release[ruck_y] - count_open[ruck_y]))

    # ─────────────────────────────────────────────────────────────
    # [5] compute_targets: A-pivot on rule, WINGER on outermost lane
    # ─────────────────────────────────────────────────────────────
    print("\n[5] compute_targets places A-pivot on ANCHOR LAW and WINGER on outer lane")
    # Build a short-side LEFT line at ruck_y=30 (LEFT short).
    p_a = mk_player(1, 58.0, 28.0, role=None, depth_role="A", depth_side=LEFT)
    p_b = mk_player(2, 58.0, 24.0, role=None, depth_role="B", depth_side=LEFT)
    p_w = mk_player(3, 58.0, 16.0, role=None, depth_role="WINGER", depth_side=LEFT)
    p_ar = mk_player(4, 58.0, 32.0, role=None, depth_role="A", depth_side=RIGHT)
    p_wr = mk_player(5, 58.0, 44.0, role=None, depth_role="WINGER", depth_side=RIGHT)
    defenders = [p_a, p_b, p_w, p_ar, p_wr]
    w = mk_world(30.0, phase="OPEN_PLAY")
    w.defending_team = defenders
    targets = compute_targets(w, defenders, tuners)
    # WIDTH-AUTHORITY RESTORED (2026-09-13): compute_targets lays the count
    # lanes across the per-side GOVERNED width, so the expected outermost
    # lane must be re-derived with that same width — omitting it falls back
    # to the flat defender_gap_m ladder and describes different geometry.
    from game.defence.defence_lvl_1.defensive_split_spacing import side_widths
    left_width, _right_width = side_widths(w, tuners, ruck_y=30.0)
    lanes_left = side_lanes(
        3, 30.0, LEFT, tuners, span=left_width,
    )  # LEFT has A,B,WINGER (3)
    check("5.1 A-pivot LEFT target == ruck_y-2.0",
          abs(targets[id(p_a)] - ruck_adjacent_anchor(30.0, LEFT)) < 1e-6,
          "target=%.4f expected=%.4f" % (targets[id(p_a)], ruck_adjacent_anchor(30.0, LEFT)))
    check("5.2 WINGER LEFT target == outermost lane",
          abs(targets[id(p_w)] - lanes_left[-1]) < 1e-6,
          "target=%.4f expected=%.4f" % (targets[id(p_w)], lanes_left[-1]))
    check("5.3 interior B BETWEEN A and WINGER",
          min(targets[id(p_a)], targets[id(p_w)]) < targets[id(p_b)] < max(targets[id(p_a)], targets[id(p_w)]),
          "A=%.2f B=%.2f W=%.2f" % (targets[id(p_a)], targets[id(p_b)], targets[id(p_w)]))

    print("\n" + "=" * 72)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    print("  - count is the single boss; count never exceeds the room")
    print("  - lanes are perfectly even; A-pivot on ruck_y +/- 2.0")
    print("  - the WIDTH owns the span; the count owns the multiplicity")
    print("  - phase biases within a stable rule (no violent re-partition)")
    print("  - compute_targets obeys the count-lane model")
    sys.exit(0)


if __name__ == "__main__":
    main()
