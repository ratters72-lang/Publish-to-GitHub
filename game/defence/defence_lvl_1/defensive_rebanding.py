"""
defensive_rebanding.py — Post-Stage-3F band re-assignment (TASK IX, 2026-08-26).

PURPOSE
-------
With TES no longer overriding Y-axis geometry (FX-4 / TD-4), depth-role
mis-seeds that used to be masked by the hard TES snap are now visible as
uneven spacing during TACKLE_DRIVE / TACKLE_DOMINANCE: two defenders can
end up seeded into the SAME band (a double claim on one single-capacity
slot), and spacing/retreat only try to correct them later in the PTB
preamble — too late for the tackle that is already forming.

This module adds a REBANDING step that runs immediately AFTER Stage-3F
timing stabilisation (the post-ruck quiet window inside
``DefensiveTiming.update()``) and BEFORE the next TES_INIT
(``OpenPlayEngine._start_tackle``):

    1. Re-run the count-driven SIDE split on the SETTLED positions
       (the same ``assign_split_sides`` authority TASK VIII installed).
       A defender whose side flips under settled geometry has its stale
       seed cleared so the next ``assign_depth_roles`` bootstraps it.
    2. Enforce the per-side SLOT INVARIANTS (the shared allocator
       helpers): at most ONE occupant per single-capacity slot
       (WINGER/CENTRE/A/B/C) and EXACTLY one A per side — eliminating
       same-band doubling and outer-lane gaps before the tackle starts.

GATING (both required):
    - ``world.ruck_established`` True  — bands exist only post-TACKLE_INIT
      (Stage 3B ruck-pivot gate; kickoff resets it to False).
    - The Stage-3F quiet window has ELAPSED:
      ``seconds_since_ptb >= tuners.post_ruck_quiet_window_s``
      (frames_since_ptb/60.0 fallback when the seconds stamp is absent,
      mirroring defensive_timing.py).  Inside the window positions are
      still settling and any correction would fight the retreat.

CONVERGENCE + SPACING + DEPTH ROLES
-----------------------------------
The task spec asks for ideal membership recomputed from convergence +
spacing + depth roles.  This module consumes them through:

  - TIMING (Stage-3F): the gate above keys off the exact quiet-window
    clock ``DefensiveTiming`` uses, so corrections fire only once the
    line's timing locks have stabilised.
  - SPACING: callers republish the frame geometry via
    ``run_defensive_pipeline`` when this function returns True, so
    ``world.frame_spacing_targets`` is rebuilt FROM the corrected roles
    in the same frame (no stale-dict dual-source drift — the Task 2
    invariant).
  - DEPTH ROLES: the fit law is IDENTICAL to the canonical allocator
    (|y − ruck_y| for interior A/B/C, structural width anchors for
    WINGER/CENTRE) by reusing the shared helpers, so the reband can
    never disagree with maintenance on what "correct" means.

PURE W.R.T. POSITIONS: never writes d.x/d.y/target_*/movement — only
depth_role / depth_side / _depth_seeded / role_depth_target_x/y, the
same fields ``assign_depth_roles`` owns.

Returns True when ANY persistent assignment changed (so the caller can
republish frame geometry); False on no-op frames.
"""

from __future__ import annotations

from game.defence.defence_lvl_1.defensive_depth_roles import (
    EXCLUDED_ROLES,
    RUCK_X_FALLBACK,
    RUCK_Y_FALLBACK,
    _dedupe_single_capacity_claims,
    _ensure_exactly_one_a,
    _get_role_anchors,
    _resolve_tuners,
)
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    assign_split_sides,
    side_widths,
)
# TASK X BALL-SIDE COLLAPSE (2026-08-26): the count-driven side split is
# ball-side biased by the SAME authority the dynamic refit uses, so the
# intra-ruck repair can never undo the collapse counts.  (ball_side_
# collapse imports quiet_window_elapsed lazily at call time — no cycle.)
from game.defence.defence_lvl_1.ball_side_collapse import (
    resolve_ball_side_bias as _resolve_ball_side_bias,
)

__all__ = ["reband_after_stabilisation", "quiet_window_elapsed"]


# ─────────────────────────────────────────────────────────────────────
# STAGE-3F GATE — shared with DefensiveTiming.update()
# ─────────────────────────────────────────────────────────────────────

def quiet_window_elapsed(world, tuners=None) -> bool:
    """True when the Stage-3F post-ruck quiet window has fully elapsed.

    Mirrors the clock DefensiveTiming.update() reads:
        seconds_since_ptb >= tuners.post_ruck_quiet_window_s   (0.5 s)
    with the frames_since_ptb / 60.0 fallback for worlds that only
    carry the legacy frame stamp.  Absent both stamps -> False (never
    reband on an unknown clock).
    """
    if tuners is None:
        tuners = _resolve_tuners(world)
    quiet_s = float(getattr(tuners, "post_ruck_quiet_window_s", 0.5))
    seconds_since_ptb = getattr(world, "seconds_since_ptb", None)
    if seconds_since_ptb is None:
        frames_since_ptb = getattr(world, "frames_since_ptb", None)
        if frames_since_ptb is None:
            return False
        seconds_since_ptb = float(frames_since_ptb) / 60.0
    return float(seconds_since_ptb) >= quiet_s


# ─────────────────────────────────────────────────────────────────────
# MAIN ENTRY
# ─────────────────────────────────────────────────────────────────────

def reband_after_stabilisation(world, defenders) -> bool:
    """Post-stabilisation band correction; True when anything changed.

    Call site contract (DBU OPEN_PLAY pipelines, BOTH structured and
    unified modes)::

        self.def_timing.update(world, <line>, dt)      # Stage-3F
        ... immediate PASSIVE override / priority gates ...
        if reband_after_stabilisation(world, line_defenders):
            run_defensive_pipeline(world, line_defenders, tuners,
                                   governor=...)           # republish
        ... later in this method: select_tacklers -> _start_tackle ...

    The republish keeps ``frame_spacing_targets`` consistent with the
    corrected roles inside the same frame (Task 2 single-source law).
    """
    # ── GATE 1: bands exist only once the ruck pivot armed ──
    if not bool(getattr(world, "ruck_established", True)):
        return False

    # ── GATE 2: Stage-3F timing stabilisation must have elapsed ──
    if not quiet_window_elapsed(world):
        return False

    # Eligible line — the SAME exclusion contract as assign_depth_roles
    # (excluded roles, fullback actor, TACKLE-STATE holders).
    # STAGE 5 (2026-09-13): the reband operates on the SINGLE canonical line
    # population — the exact set the count / side / spacing authority
    # publishes.  The old hand-rolled filter omitted DEF_CHASE, so the repair
    # could act on a different body set than the published count.
    from game.defence.defence_lvl_1.population_authority import (
        line_population as _line_population,
    )
    eligible = _line_population(defenders)
    if not eligible:
        return False

    tuners = _resolve_tuners(world)
    ruck_x = float(getattr(world, "ruck_x_m", RUCK_X_FALLBACK))
    ruck_y = float(getattr(world, "ruck_y_m", RUCK_Y_FALLBACK))
    # COUNT-AUTHORITY WIDTH (Stage 3, 2026-09-03): the reband anchors are
    # built from count-derived width — the per-side count is the eligible
    # line count, and width is derived from the lane model.
    # TASK X: the settled-geometry split honours the ball-side collapse
    # counts; resolve the SAME bias BEFORE side_widths so the count split
    # it publishes (and the hysteresis it feeds) is the biased one — the
    # unbiased split previously swallowed the collapse bias.
    _ball_side, _ball_bias = _resolve_ball_side_bias(world, tuners)
    left_width, right_width = side_widths(
        world, tuners, n_total=len(eligible), ruck_y=ruck_y,
        ball_side=_ball_side, ball_side_bias=_ball_bias,
    )
    anchors = _get_role_anchors(
        ruck_x, ruck_y, left_width, right_width, tuners
    )

    changed = False

    # ── STEP 1 — SIDE AUTHORITY on SETTLED positions ──
    # Re-run the count-driven split (TASK VIII).  It stamps depth_side
    # for every eligible defender nearest-first; a defender whose side
    # differs from its pre-call stamp was mis-split while positions were
    # still settling — clear its seed so the next assign_depth_roles
    # bootstraps it onto the corrected side instead of carrying a stale
    # band through hysteresis.
    before_sides = {
        id(d): getattr(d, "depth_side", None) for d in eligible
    }
    flipped_ids: set = set()
    # TASK X: the settled-geometry split honours the ball-side collapse
    # counts (single bias authority shared with the dynamic refit), so a
    # repair frame can never silently undo the collapse.  The bias was
    # already resolved above and threaded through side_widths.
    assign_split_sides(
        world, eligible, tuners, ruck_y=ruck_y,
        ball_side=_ball_side, ball_side_bias=_ball_bias,
    )
    for d in eligible:
        now = getattr(d, "depth_side", None)
        if now in (-1, 1) and before_sides.get(id(d)) != now:
            changed = True
            flipped_ids.add(id(d))
            d._depth_seeded = False
            d.depth_role = None

    # ── STEP 2 — PER-SIDE SLOT INVARIANTS (doubling / missing A) ──
    # Shared allocator helpers: de-duplicate contested single-capacity
    # claims (best-fit keeps, others fall to D) and guarantee exactly
    # one A per side.  After the quiet window these run on settled
    # positions, so the best-fit choice is the stable lane truth.
    left = [d for d in eligible if getattr(d, "depth_side", None) == -1]
    right = [d for d in eligible if getattr(d, "depth_side", None) == 1]
    for side_defs, side in ((left, -1), (right, 1)):
        if not side_defs:
            continue
        snapshot = {
            id(d): getattr(d, "depth_role", None) for d in side_defs
        }
        _dedupe_single_capacity_claims(side_defs, side, anchors, ruck_y)
        _ensure_exactly_one_a(side_defs, ruck_y)
        for d in side_defs:
            if snapshot.get(id(d)) != getattr(d, "depth_role", None):
                changed = True
                # A re-fitted defender must be re-seeded against the
                # corrected band on the next assign_depth_roles call.
                d._depth_seeded = True

    # ── STEP 2b — SIDE-FLIPPED HOLDERS BOOTSTRAP THROUGH ──
    # Even if the slot-invariant pass above handed a flipped defender an
    # interior band, its persistent claim was earned on the WRONG side.
    # Force it through the positional bootstrap on its CORRECTED side at
    # the next assign_depth_roles (any unseeded defender triggers the
    # whole-set bootstrap, which is deterministic on settled positions).
    if flipped_ids:
        for d in eligible:
            if id(d) in flipped_ids:
                d._depth_seeded = False
                d.depth_role = None

    # ── STEP 3 — RESTAMP ROLE-DEPTH X/Y (Task A-2 step 8 law) ──
    # Identical to the tail of assign_depth_roles: X from the anchor
    # authority, Y from the structural band anchor of the (possibly
    # corrected) role/side pair.
    from game.defence.defence_lvl_1.defensive_depth_roles import (
        _depth_role_target_x as _depth_x,
    )
    anchor_x = defensive_line_anchor_m(world)
    for d in eligible:
        side = getattr(d, "depth_side", None)
        if side not in (-1, 1):
            continue
        role = getattr(d, "depth_role", None) or "D"
        # DRIVE-AWARE (2026-09-05 / 2026-09-06): X comes from the same
        # single source as assign_depth_roles — bc.x + WIDTH_OFFSETS[role]
        # DURING the drive, ruck_x + 10 AFTER.  This reband only runs once
        # the Stage-3F post-ruck quiet window has elapsed (post-drive), so
        # it is exactly the "reband snaps to the 10 m after the drive ends"
        # contract.  Previously it always stamped anchor_x (ruck_x + 10),
        # which re-anchored the line to the stale pre-drive ruck.
        d.role_depth_target_x = _depth_x(world, d)
        d.role_depth_target_y = anchors[side].get(role, anchors[side]["D"])

    return changed
