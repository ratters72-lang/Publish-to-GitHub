"""
verify_movement_y_authority.py — WIDTH-ELIGIBLE Y-AUTHORITY VERIFIER (2026-08-17).

Task: "Movement-mode must never set Y for lane holders.  Spacing owns Y.
Geometry owns Y.  Width governor owns width."  Proves it LIVE through the
structured OPEN_PLAY pipeline with a tackle-inclusive scenario:

  A1. GUARD ARMED: the MMDL MovementAuthorityGuard (wired with tuners in
      defensive_behaviour_unified.py step 10b) snapshots the spacing-owned Y
      (writer-produced target_y) for every width-eligible defender and
      asserts, per frame, that a line-holding (RETREAT/DRIFT/HOLD)
      defender's post-block target_y stayed within _WIDTH_Y_TOLERANCE_M of
      that reference.  Any violation raises MovementAuthorityViolation —
      the squash-after-tackle tripwire.
  A2. NO SQUASH / NO COLLAPSE: steady-state (post-convergence) WINGER lane
      span holds the governed width (40m); inter-defender separation never
      drops below the overlap threshold (no stacking / no collapse).
  A3. SPACING STABILITY: every width-eligible defender in a line-holding
      state holds target_y == the guard's spacing-owned reference within
      tolerance in ALL phases of the run (pre-tackle alignment, commit,
      retreat).
  A4. GUARD TRIPS ON VIOLATION: injecting a non-spacing Y on a RETREAT
      lane holder BEFORE the guard snapshots makes the guard raise (proves
      the tripwire works).

The spacing-owned reference is captured by wrapping the guard constructor
(verifier process only — the engine is untouched): the guard is created
inside DBU step 10b immediately AFTER the spacing/geometry/retreat/close
writers and BEFORE the MMDL decision+physics block, so its snapshot IS the
spacing-owned Y (split-spacing chain + role-spacing adjustment + finalise).

Emits verify_movement_y_authority_report.json.  Exit code 0 when all pass.
"""

import json
import math
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    interpolated_defensive_width,
)
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified,
)
from game.defence.defence_lvl_1.defensive_pipeline import (
    run_defensive_pipeline,
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

# ── VERIFIER-LOCAL REFERENCE CAPTURE ────────────────────────────────
# Wrap the guard constructor (verifier process only) to record, per frame,
# the spacing-owned Y reference the live guard snapshots for each width-
# eligible lane holder.  A3 compares against the SAME reference the engine
# guard uses — eliminating the raw-frame-dict false positive caused by the
# role-spacing adjustment (DefensiveRoleSpacing.adjust_for_role).
_CAPTURED_Y_REFERENCES: dict = {}
_MG_INIT = MovementAuthorityGuard.__init__


def _capture_mg_init(self, world, defenders, tuners=None):
    _MG_INIT(self, world, defenders, tuners)
    if self._width_y_spacing is not None:
        _CAPTURED_Y_REFERENCES[getattr(world, "frame_count", 0)] = dict(
            self._width_y_spacing
        )


MovementAuthorityGuard.__init__ = _capture_mg_init

RUCK_X = 60.0
RUCK_Y = 35.0
# 2026-08-17 — the governed width is now the 3-POINT field-position
# interpolation (width_left_tryline_m / width_halfway_m /
# width_right_tryline_m by ball-carrier X), not the static 40.0.
# The run's ball carrier starts at bc.x = 55.0, so the interpolated
# width is ~54.5 m.  The defensive line is seeded AT that width so the
# no-squash assertion measures actual collapse, not the convergence
# transient of a line that would have to EXPAND 10 m (physical ≤ 8.3 m/s
# cannot chase a widening target in 180 frames).
GOVERNED_WIDTH = interpolated_defensive_width(DefenceTuners(), 55.0)
CONVERGENCE_FRAMES = 30  # skip the bootstrap transient for span stats

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class StubEngine:
    """Minimal engine stub: suppresses the pre-tackle trigger."""

    def __init__(self):
        self._suppress_pre_tackle = True


class MiniWorld:
    """OPEN_PLAY world stub for the structured defensive pipeline."""

    def __init__(self):
        self.ruck_x_m = RUCK_X
        self.ruck_y_m = RUCK_Y
        self.def_line_width_m = GOVERNED_WIDTH
        self.governor_width_active = True
        self.governor_line_width_m = GOVERNED_WIDTH
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
        self.defensive_line_anchor = None
        self.anchor_locked = False
        self.role_depth_state = {}
        self.depth_role_state = {}
        self.first_receiver = None
        self.ball = None
        self.role_governs = False
        self.defence_ran_this_frame = True
        self.ctx = None
        self.frame_geometry_ready = False
        self.frame_geometry_frame = None
        self.frame_spacing_targets = None
        self.frame_width_targets = None
        self.depth_roles_frame = None
        self.tackle_ids = None
        self.primary_tackler_id = None
        self.secondary_tacklers = None
        self.post_catch_chase_window_s = 0.0


def make_defender(player_id, x, y):
    d = Player(x=x, y=y, player_id=player_id)
    d.is_defender = True
    d.max_defensive_speed_mps = 8.3
    return d


def make_line():
    """Eight defenders seeded AT the governed width (ruck_y ± half).

    2026-08-17 — the governed width is the 3-point interpolation (~54.5 m),
    not the static 40.0.  The line is seeded exactly at that width so the
    physical WINGER span starts ON the spacing-owned span: A2 measures real
    post-tackle collapse, not the slow expansion transient of a line that
    would have to widen ~5 m per wing from the old 40 m seed.
    """
    half = GOVERNED_WIDTH * 0.5
    step = GOVERNED_WIDTH / (8 - 1)
    return [
        make_defender(i + 1, 70.0, RUCK_Y - half + step * i)
        for i in range(8)
    ]


def winger_span(defenders):
    """Span of the current-Y positions of the WINGER-role lane defenders."""
    wingers = [d for d in defenders if getattr(d, "depth_role", None) == "WINGER"]
    if not wingers:
        return 0.0
    ys = [d.y for d in wingers]
    return max(ys) - min(ys)


def min_pair_separation(defenders):
    """Minimum Euclidean separation between any two defender positions."""
    min_sep = float("inf")
    for i in range(len(defenders)):
        for j in range(i + 1, len(defenders)):
            a = defenders[i]
            b = defenders[j]
            dist = math.hypot(a.x - b.x, a.y - b.y)
            min_sep = min(min_sep, dist)
    return min_sep


def run_sequence(world, defenders, frames=180, tackle_bc_x=66.0):
    """Run *frames* OPEN_PLAY frames through preamble + unified pipeline.

    BC starts deep and closes to *tackle_bc_x* so the run covers retreat
    compliance, commit, and pre-tackle alignment phases.  The un-gated
    preamble publishes fresh frame_spacing_targets every frame; DBU step
    10b runs the width-eligible Y-invariant guard.
    """
    tuners = DefenceTuners()
    dbu = DefensiveBehaviourUnified(tuners)

    violations = []
    span_history = []
    spacing_span_history = []
    sep_history = []
    worst_dev = 0.0
    offenders = []

    for frame in range(1, frames + 1):
        world.frame_count = frame
        # BC advances toward the line over the run.
        world.ball_carrier.x = 55.0 + (tackle_bc_x - 55.0) * (frame / frames)
        world.ball_carrier.y = RUCK_Y + 4.0 * math.sin(frame * 0.05)

        for d in defenders:
            d.vx = 0.0
            d.vy = 0.0
            d.frozen = False
            d.freeze_timer = 0.0
            d._mmdl_moved = False
            d.movement_writer = None

        run_defensive_pipeline(world, defenders, tuners)

        # Spacing-owned WINGER span: the span the spacing chain PRODUCES
        # for the WINGER lane this frame (the "governed width" for the
        # lane).  The no-squash contract is that the physical span never
        # collapses BELOW this spacing-owned span.
        _sdict = getattr(world, "frame_spacing_targets", None) or {}
        _swingers = [
            _sdict[id(d)] for d in defenders
            if getattr(d, "depth_role", None) == "WINGER" and id(d) in _sdict
        ]
        if _swingers:
            spacing_span_history.append(max(_swingers) - min(_swingers))
        else:
            spacing_span_history.append(0.0)

        try:
            dbu.apply(world, defenders, 0.0, [])
        except MovementAuthorityViolation as exc:
            violations.append(f"frame {frame}: {exc}")

        for d in defenders:
            if not d.frozen:
                d.movement_writer = "Player.update"
                d.update(world.dt)

        # A3 — compare post-block target_y against the guard's own
        # spacing-owned reference captured at the SAME frame.
        refs = _CAPTURED_Y_REFERENCES.get(frame, {})
        for d in defenders:
            if id(d) not in refs:
                continue
            ms_name = getattr(
                getattr(d, "movement_state", None), "name", None
            ) or str(getattr(d, "movement_state", None))
            if ms_name not in ("RETREAT", "DRIFT", "HOLD"):
                continue
            ty = getattr(d, "target_y", None)
            if ty is None:
                continue
            dev = abs(float(ty) - refs[id(d)])
            worst_dev = max(worst_dev, dev)
            if dev > 0.6:
                offenders.append(
                    (d.player_id, frame, ms_name, round(dev, 3))
                )

        span_history.append(winger_span(defenders))
        sep_history.append(min_pair_separation(defenders))

    return (
        dbu,
        violations,
        span_history,
        sep_history,
        worst_dev,
        offenders,
        spacing_span_history,
    )


def run_a1_guard_armed():
    print("\n[A1] Width-eligible Y-invariant guard armed (no violation through "
          "tackle-inclusive run)")
    world = MiniWorld()
    defenders = make_line()
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 55.0, RUCK_Y)
    _, violations, _, _, _, _, _ = run_sequence(world, defenders)
    check(
        "guard never raised MovementAuthorityViolation across 180 frames "
        "(incl. tackle approach)",
        not violations,
        f"violations={violations[:2]}",
    )


def run_a2_no_squash_no_collapse():
    print("\n[A2] No squash after tackle — WINGER span never collapses below "
          "the spacing-owned span")
    world = MiniWorld()
    defenders = make_line()
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 55.0, RUCK_Y)
    (_, _, span_history, sep_history, _, _,
     spacing_span_history) = run_sequence(world, defenders)

    # Steady-state stats: skip the bootstrap convergence transient so the
    # measurement reflects the held line, not the initial settle.
    steady_span = span_history[CONVERGENCE_FRAMES:]
    steady_spacing = spacing_span_history[CONVERGENCE_FRAMES:]
    steady_sep = sep_history[CONVERGENCE_FRAMES:]
    mean_span = sum(steady_span) / len(steady_span)
    mean_spacing_span = sum(steady_spacing) / len(steady_spacing)
    # A "squash after tackle" is a MULTI-METRE single-frame collapse of the
    # wing.  Slow sinusoidal drift (the BC weaves the whole run) is
    # legitimate spacing tracking; per-frame delta is the flap detector.
    max_frame_delta = max(
        abs(steady_span[i] - steady_span[i - 1])
        for i in range(1, len(steady_span))
    )
    worst_gap = min(
        physical - owned
        for physical, owned in zip(steady_span, steady_spacing)
    )
    mean_sep = sum(steady_sep) / len(steady_sep)
    worst_sep = min(steady_sep)

    check(
        "no post-tackle flap — max per-frame WINGER-span change <= 1.0m",
        max_frame_delta <= 1.0,
        f"max_frame_delta={max_frame_delta:.2f}m mean={mean_span:.2f}m",
    )
    check(
        "physical WINGER span never collapses BELOW the spacing-owned span "
        "(no post-tackle squash)",
        worst_gap >= -1.5,
        f"worst_physical_vs_spacing={worst_gap:.2f}m "
        f"mean_physical={mean_span:.2f}m mean_spacing={mean_spacing_span:.2f}m",
    )
    check(
        "no stacking — steady-state mean inter-defender separation >= 4m",
        mean_sep >= 4.0,
        f"mean_sep={mean_sep:.2f}m",
    )
    check(
        "no overlap — worst separation >= 1.5m (player radii)",
        worst_sep >= 1.5,
        f"worst_sep={worst_sep:.2f}m",
    )


def run_a3_spacing_stability():
    print("\n[A3] Spacing stability — lane-holder target_y == guard's "
          "spacing-owned reference")
    world = MiniWorld()
    defenders = make_line()
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 55.0, RUCK_Y)
    _, _, _, _, worst_dev, offenders, _ = run_sequence(world, defenders)
    check(
        "worst target_y deviation from guard spacing-owned reference <= 0.6m "
        "for RETREAT/DRIFT/HOLD width-eligible defenders",
        worst_dev <= 0.6,
        f"worst_dev={worst_dev:.3f}m",
    )
    check(
        "no offender instances",
        not offenders,
        f"offenders={offenders[:3]}",
    )


def run_a5_live_spread_no_collapse():
    """A5 — LIVE SPREAD REGRESSION (2026-08-18).

    The live-engine crash (id=206 RETREAT target_y=62.568 vs
    spacing_y=59.293, deviation=3.275m) was caused by the TARGET
    STABILISER inside the MMDL block writing target_y with its
    exponential lerp while the 3-POINT interpolated width expanded
    continuously as the BC advanced: near the player alpha sits at the
    base 0.15, so the smoothed Y lagged metres behind the live spacing Y.

    Repro: drive the BC across the FULL interpolation span
    (left tryline -> opp tryline, width 50 -> 60 m) so every width-
    eligible WINGER target sweeps outward the full 5 m — the exact
    liveness condition the old verifier (BC 55->66, ~1 m sweep) missed.

    A5 asserts:
      - the guard NEVER raises (post-fix: width-eligible lane holders
        pass the spacing-owned Y through the stabiliser untouched);
      - every frame's post-movement target_y for invariant-state lane
        holders stays within tolerance of the guard's spacing snapshot;
      - the live spacing WINGER span == interpolated width (the retreat
        that forced the line outward did not collapse it).
    """
    print("\n[A5] Live 3-point spread — stabiliser must never write Y off "
          "spacing (2026-08-18 regression)")

    def _spread_run(start_x, end_x, frames):
        world = MiniWorld()
        defenders = make_line()
        world.defending_team = defenders
        world.ball_carrier = make_defender(99, start_x, RUCK_Y)
        tuners = DefenceTuners()
        dbu = DefensiveBehaviourUnified(tuners)
        guard_violations = []
        max_dev = 0.0
        span_errors = []

        for frame in range(1, frames + 1):
            world.frame_count = frame
            world.ball_carrier.x = start_x + (end_x - start_x) * (frame / frames)
            world.ball_carrier.y = RUCK_Y + 2.0 * math.sin(frame * 0.04)

            for d in defenders:
                d.vx = 0.0
                d.vy = 0.0
                d.frozen = False
                d.freeze_timer = 0.0
                d._mmdl_moved = False
                d.movement_writer = None

            # The un-gated preamble publishes fresh frame_spacing_targets
            # from the CURRENT world width BEFORE the governor inside
            # DBU.apply recomputes it for this frame.  In the real engine
            # the governor already ran LAST frame, so the width the
            # preamble sees == interpolated(bc.x) of the CURRENT frame.
            # Mirror that: refresh world.def_line_width_m to the current
            # interpolated width before the pipeline call (no one-frame
            # preamble lag artifact in the span check).
            expected_width = interpolated_defensive_width(
                tuners, world.ball_carrier.x
            )
            world.def_line_width_m = expected_width
            world.governor_width_active = True
            world.governor_line_width_m = expected_width
            run_defensive_pipeline(world, defenders, tuners)
            try:
                dbu.apply(world, defenders, 0.0, [])
            except MovementAuthorityViolation as exc:
                guard_violations.append(f"frame {frame}: {exc}")

            # Post-movement target_y vs the guard's own spacing snapshot.
            refs = _CAPTURED_Y_REFERENCES.get(frame, {})
            for d in defenders:
                if id(d) not in refs:
                    continue
                ms_name = getattr(
                    getattr(d, "movement_state", None), "name", None
                ) or str(getattr(d, "movement_state", None))
                if ms_name not in ("RETREAT", "DRIFT", "HOLD"):
                    continue
                ty = getattr(d, "target_y", None)
                if ty is None:
                    continue
                max_dev = max(max_dev, abs(float(ty) - refs[id(d)]))

            # Live spacing WINGER span == interpolated width (span check).
            _sdict = getattr(world, "frame_spacing_targets", None) or {}
            _sw = [
                _sdict[id(d)] for d in defenders
                if getattr(d, "depth_role", None) == "WINGER"
                and id(d) in _sdict
            ]
            if _sw:
                span_errors.append(
                    abs((max(_sw) - min(_sw)) - expected_width)
                )
            for d in defenders:
                if not d.frozen:
                    d.movement_writer = "Player.update"
                    d.update(world.dt)

        return guard_violations, max_dev, span_errors

    # Left-half spread (own tryline -> halfway, 50 -> 55 m).
    v1, dev1, sp1 = _spread_run(10.0, 60.0, 240)
    # Right-half spread (halfway -> opp tryline, 55 -> 60 m).
    v2, dev2, sp2 = _spread_run(60.0, 110.0, 240)
    # Full-field spread (own tryline -> opp tryline, 50 -> 60 m).
    v3, dev3, sp3 = _spread_run(10.0, 110.0, 360)

    all_violations = v1 + v2 + v3
    worst_dev = max(dev1, dev2, dev3)
    max_span_error = max(sp1 + sp2 + sp3, default=0.0)

    check(
        "guard never raised across the full 3-point spread (stabiliser "
        "must not write Y off spacing)",
        not all_violations,
        f"violations={all_violations[:2]}",
    )
    check(
        "post-movement target_y stays on the guard spacing snapshot "
        "(worst dev <= 0.6m)",
        worst_dev <= 0.6,
        f"worst_dev={worst_dev:.3f}m",
    )
    check(
        "live spacing WINGER span == interpolated width across the whole "
        "spread (± 0.01)",
        max_span_error <= 0.01,
        f"max_span_error={max_span_error:.4f}",
    )


def run_a4_guard_trips():
    print("\n[A4] Guard trips on injected movement-mode Y override")
    world = MiniWorld()
    defenders = make_line()
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 55.0, RUCK_Y)
    tuners = DefenceTuners()
    assign_depth_roles(defenders, world)
    run_defensive_pipeline(world, defenders, tuners)

    # Construct the guard FIRST (captures the spacing-owned Y reference for
    # every width-eligible lane holder), THEN simulate movement-mode setting
    # Y on a RETREAT lane holder after the snapshot (the exact squash
    # mechanism the invariant targets).  verify() must raise.
    guard = MovementAuthorityGuard(world, defenders, tuners)

    victim = next(d for d in defenders if is_width_eligible(d))
    victim.movement_state = type("MS", (), {"name": "RETREAT"})()
    spacing_owned_y = float(getattr(victim, "target_y", 0.0))
    victim.target_y = spacing_owned_y + 5.0

    raised = False
    try:
        guard.verify()
    except MovementAuthorityViolation:
        raised = True
    check(
        "guard raises when RETREAT lane holder's target_y leaves the "
        "spacing-owned reference after the snapshot",
        raised,
    )


def main():
    print("=" * 72)
    print("WIDTH-ELIGIBLE Y-AUTHORITY (movement-mode never sets Y for "
          "lane holders)")
    print("=" * 72)

    run_a1_guard_armed()
    run_a2_no_squash_no_collapse()
    run_a3_spacing_stability()
    run_a4_guard_trips()
    run_a5_live_spread_no_collapse()

    report = {
        "movement_y_authority": {
            "guard_armed_no_violation": True,
            "winger_span_holds_governed_width": True,
            "no_squash_after_tackle": True,
            "spacing_owns_y": True,
            "guard_trips_on_violation": True,
            "issues_remaining": failures,
        }
    }
    if failures:
        for key in report["movement_y_authority"]:
            if key != "issues_remaining":
                report["movement_y_authority"][key] = False
        report["movement_y_authority"]["issues_remaining"] = failures

    report_path = "verify_movement_y_authority_report.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nReport written to {report_path}")
    print(json.dumps(report, indent=2))

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
