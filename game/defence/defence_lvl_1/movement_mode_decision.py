"""
game/defence/defence_lvl_1/movement_mode_decision.py — Movement Mode Decision Layer (MMDL).

Decides the best per-defender MovementState each OPEN_PLAY frame in
structured mode, BEFORE TES.  Chooses RETREAT / SLIDE / SHOOT / CHASE /
DRIFT / HOLD using the existing MovementState machine (player/movement_states.py)
whose physics are NOT modified here.

Decision tree (per defender, per frame):
  -1. FULLBACK ROLE-PERSISTENCE (B2, 2026-09-16): the fullback is
       geometry-driven while it holds the role, but movement state NEVER
       demotes the role.  fullback_role_active -> DRIFT (or CHASE once the
       carrier is past the anchor / inside the tackle radius); the tag is
       handed back only when the actor is actually armed as a TES tackler.
       Tackle candidacy is gated by can_acquire_tackle, not by movement state.
  0. bc is None
       PASSIVE  -> RETREAT (hold line / retreat to anchor)
       else     -> HOLD
  1. TES guard: world.tes_active and this defender is a registered tackler
       -> leave movement_state as-is (TES owns tackler movement)
  1.5 TACKLE-RADIUS CHASE SWITCH (2026-09-05): a genuine line defender
       that has breached the tackle radius (dist < tackle_radius_m) is
       committing to the tackle.  It MUST be CHASE (forward closing) —
       never RETREAT (backpedal into contact), DRIFT (wrong-side drift),
       or HOLD (early pre-tackle freeze).  Fullback retains its dedicated
       geometry / breakaway-chase logic; markers / DH / TES tacklers keep
       their dedicated owners.
  2. Retreat compliance: defender illegally in front of ruck_x
       (d.x < ruck_x + retreat_compliance_distance_m) AND depth_class == PASSIVE
       -> RETREAT
  3. Line hold: deep PASSIVE defenders
       bc far ahead (bc.x - d.x > deep_passive_distance_m) -> HOLD
       otherwise                                             -> DRIFT
  4. PRIMARY/SECONDARY (engageable):
       lateral = |d.y - bc.y|
       lateral > lateral_slide_threshold_m and dist < engage_distance_m -> SLIDE
       dist < commit_distance_m  and lateral <= lateral_slide_threshold_m -> SHOOT
       otherwise -> CHASE

Transitions respect VALID_TRANSITIONS via is_valid_transition(); invalid
transitions fall back to CHASE (primary/secondary) or HOLD (passive/no-BC).

Marker defenders (MARKER_A/B) are preserved by the depth-classification
gate in DefensiveBehaviourUnified; the decision layer here additionally
holds markers on the ruck so MMDL can never pull them off their targets.

TASK I-B RETREAT/CLOSE OSCILLATION KILL (2026-08-11)
----------------------------------------------------
  - RETREAT entry uses a hysteresis band around the retreat-compliance
    line: a PASSIVE defender enters RETREAT only when below
    ruck_x + compliance - HYST and exits only when above
    ruck_x + compliance + HYST.  Hovering exactly at ruck_x + 10 no
    longer flips RETREAT↔DRIFT every frame.
  - A per-defender decision lock persists oscillating states
    (RETREAT/CHASE/SLIDE/SHOOT) for `_DECISION_LOCK_FRAMES` frames unless
    a HARD threshold (BC crossing the anchor ± band edges) overrides.
    Sub-threshold lateral drift never releases a locked state.

TASK IV PATCH 1 — PRE-TACKLE STUCK_NOT_FROZEN FIX (2026-08-14)
--------------------------------------------------------------
The original spec targeted ``movement_state_resolver.py``, which does not
exist in this codebase — the OPEN_PLAY movement-state writer is the MMDL
block in defensive_behaviour_unified.py.  The fix is therefore exposed here
as ``resolve_pre_tackle_stuck``: a LAST-RESORT fallback the MMDL writer
applies AFTER the full tree (incl. lerp blend + resolve) has produced the
frame's state.  Any defender in OPEN_PLAY with a live target and zero
total velocity still resolved to HOLD/DRIFT is assigned RETREAT (PASSIVE)
or SLIDE (engaged) so the state machine always produces motion — killing
the STUCK_NOT_FROZEN freeze-audit violation directly.
"""

import math

from player.movement_states import MovementState, is_valid_transition
from player.collapse_controls import should_commit

# NOTE (2026-09-18): The MMDL carries NO retreat-obligation veto, deliberately.
# An earlier revision forced ``MovementState.RETREAT`` here for any obligated
# body, ABOVE every other branch.  The live probe disproved it: in this
# coordinate convention ``RETREAT`` has NEGATIVE vx, while the obligation
# target is ``ruck_x + lane offset`` (a LARGER x), so the veto moved the body
# AWAY from the very target it owed -- measured on a WINGER as vx = -5.5 m/s,
# x falling 68.90 -> 67.81, deficit GROWING 0.31 -> 1.46 m -- and the body
# then stalled.  The obligation is an AUTHORITY gate: the five authority
# grants (lane candidacy, engage machine, pre-tackle ownership, corridor,
# corridor incumbent bypass) veto the body without touching its movement, so
# its own geometry/width/depth writers keep running and the deficit closes.


# ── Default thresholds (used when tuners are absent) ──────────────────
# These mirror the DefenceTuners values so the MMDL degrades safely if
# instantiated with a bare-namespace tuners object.
_DEFAULT_ENGAGE_DISTANCE_M: float = 20.0
_DEFAULT_COMMIT_DISTANCE_M: float = 8.0
_DEFAULT_LATERAL_SLIDE_THRESHOLD_M: float = 3.0
_DEFAULT_DEEP_PASSIVE_DISTANCE_M: float = 15.0
# ── PURSUIT/COMMIT HARD OVERRIDE RADIUS (metres) ──────────────────
# Any non-marker/fullback/TES/tackle-state line defender within this
# Euclidean distance of the ball carrier is forced onto a BC-relative
# pursuit/commit target + CHASE/SHOOT state + full ceiling speed,
# overriding all spacing/role/retreat/fallback logic until CONTACT
# (TES start) fires.  Mirrors DefenceTuners.pursuit_commit_radius_m so
# the MMDL gate and the target override share ONE radius.
_DEFAULT_PURSUIT_COMMIT_RADIUS_M: float = 12.0
# ── TACKLE RADIUS CHASE SWITCH (2026-09-05) ────────────────────────
# A genuine line defender that has breached the tackle radius switches
# immediately to CHASE, preventing retreat-into-contact, wrong-side
# stance, early HOLD, and TES inheriting a corrupted movement_state.
# Mirrors DefenceTuners.tackle_radius_m (1.5) so the MMDL switch and the
# TES tackle trigger share ONE radius.
_DEFAULT_TACKLE_RADIUS_M: float = 1.5

# ── TASK I-B RETREAT HYSTERESIS BAND (metres) ────────────────────────
# A PASSIVE defender ENTERS RETREAT only when x < ruck_x + compliance - HYST
# and EXITS only when x >= ruck_x + compliance + HYST.
_RETREAT_HYSTERESIS_M: float = 0.5

# ── TASK I-B DECISION LOCK (frames) ─────────────────────────────────
# Once RETREAT or a pursuit state (CHASE/SLIDE/SHOOT) is chosen, it
# persists this many frames unless a HARD threshold overrides — the
# spec's "fire only when crossing thresholds" rule applied to the
# movement-state writer, killing frame-to-frame retreat↔close flapping.
_DECISION_LOCK_FRAMES: int = 5

# ── TASK IV PATCH 1 — STUCK FALLBACK THRESHOLDS (2026-08-14) ────────
# A defender is "stuck" only when it is NOT at its target (target_x offset
# > _STUCK_AT_TARGET_M) AND its TOTAL velocity is below
# _STUCK_VELOCITY_EPS (mirrors freeze_audit's is_stuck / soft-freeze
# semantics — a pure-lateral SLIDE/DRIFT mover with vx ≈ 0 but non-zero
# vy is moving, never stuck).
_STUCK_AT_TARGET_M: float = 0.2
_STUCK_VELOCITY_EPS: float = 0.01

_LOCKED_STATES = frozenset({
    "RETREAT",
    "CHASE",
    "SLIDE",
    "SHOOT",
})

# Roles that are never eligible for the stuck fallback: they play tight to
# the ruck (markers/DH), are TES-owned (tacklers), or hold a dedicated
# backfield anchor (fullback).
_STUCK_EXEMPT_ROLE_NAMES = frozenset({
    "MARKER_A",
    "MARKER_B",
    "DUMMY_HALF",
    "TACKLER_1",
    "TACKLER_2",
    "TACKLER_3",
    "KICK_CHASE",
    "DEF_CHASE",
})

# Roles that are NEVER eligible for the tackle-radius CHASE switch: they
# hold a dedicated movement owner (marker pair plays tight to the ruck,
# DH is PTB-owned, TES tacklers are TES-owned) or are not line bodies.
_TACKLE_RADIUS_EXEMPT_ROLE_NAMES = frozenset({
    "MARKER_A",
    "MARKER_B",
    "DUMMY_HALF",
    "TACKLER_1",
    "TACKLER_2",
    "TACKLER_3",
    "KICK_CHASE",
})


def _hard_threshold_violated(world, defender, bc, tuners) -> bool:
    """True when the BC crossing a hard threshold overrides any decision lock.

    A lock may only be broken by a genuine band-boundary crossing (BC past
    anchor + band edge, or BC retreating behind anchor - band edge) —
    mirroring the engagement-state hysteresis.  Sub-threshold lateral
    drift never releases a locked state.
    """
    if bc is None:
        return False
    from game.defence.defence_lvl_1.anchor_authority import (
        defensive_line_anchor_m,
    )
    anchor_x = defensive_line_anchor_m(world)
    band = float(getattr(tuners, "DEPTH_BAND_M", 5.0)) if tuners is not None else 5.0
    return bc.x > anchor_x + band or bc.x < anchor_x - band


def carrier_past_defensive_line(
    world, bc, extra_m: float = 1.0, anchor_x: float | None = None
) -> bool:
    """True once the ball carrier is clearly past the defensive anchor line.

    TASK VI CHASE OVERRIDE (2026-08-15): once ``bc.x`` exceeds
    ``anchor_x + extra_m`` the alignment phase is over — engaged
    PRIMARY/SECONDARY defenders ahead of the carrier must chase diagonally
    (CHASE, non-zero vx + vy) instead of completing a lateral SLIDE.  Uses
    the same lazy anchor_authority import as ``_hard_threshold_violated`` so
    the anchor stays single-sourced.
    """
    if bc is None:
        return False
    if anchor_x is not None:
        # ANCHOR OVERRIDE (2026-09-28).  The FULLBACK break test supplies the
        # anchor it wants compared against (``fullback_role.
        # break_test_line_anchor_x`` -- the live anchor floored by the anchor
        # latched when the chase was armed), so a FORWARD ruck relock cannot
        # retroactively un-break a live chase.  Every other caller leaves this
        # None and keeps the single-sourced live authority read below, so the
        # line-defender contract is byte-identical.
        try:
            return float(bc.x) > float(anchor_x) + extra_m
        except (TypeError, ValueError):
            return False
    from game.defence.defence_lvl_1.anchor_authority import (
        defensive_line_anchor_m,
    )
    anchor_x = defensive_line_anchor_m(world)
    return bc.x > anchor_x + extra_m


def _in_post_catch_chase_window(world) -> bool:
    """True while the post-catch pursuit window is active.

    POST-CATCH PURSUIT (2026-08-16): armed at 3.0s on kickoff catch in
    catch_system._perform_catch(), burned down by OpenPlayEngine each
    OPEN_PLAY frame.  While active, engaged PRIMARY/SECONDARY defenders
    chase the BC immediately (CHASE) instead of entering SLIDE — killing
    the post-catch stall where the line stops moving forward until
    bc.x passes anchor_x + 10.  Reads via getattr so harness worlds /
    verifiers without the field degrade to the normal SLIDE behaviour.
    """
    return getattr(world, "post_catch_chase_window_s", 0.0) > 0.0


def _tuner(tuners, name: str, default: float) -> float:
    """Read a tuner value safely, falling back to *default* if missing."""
    if tuners is None:
        return default
    value = getattr(tuners, name, None)
    if value is None:
        return default
    return float(value)


def _is_deep_passive(defender, bc, tuners) -> bool:
    """True when a PASSIVE defender is very deep behind the BC and should
    hold the line rather than drift laterally."""
    depth_gap = bc.x - defender.x
    return depth_gap > _tuner(tuners, "deep_passive_distance_m", _DEFAULT_DEEP_PASSIVE_DISTANCE_M)


def _is_engageable(depth_class: str) -> bool:
    """True for PRIMARY/SECONDARY depth classes (the CLOSE/SLIDE/SHOOT
    branch of the tree).  MARKER defenders never enter this branch."""
    return depth_class in ("PRIMARY", "SECONDARY")


def is_in_pursuit_commit_range(defender, bc, radius: float) -> bool:
    """True when *defender* is within *radius* metres (Euclidean) of *bc*.

    PURSUIT/COMMIT HARD OVERRIDE (2026-09-02): the MMDL gate and the
    target override both read this single proximity predicate so the
    12m pursuit radius is single-sourced.  Returns False when either
    entity is None or when *radius* is non-positive.
    """
    if bc is None or defender is None or radius <= 0.0:
        return False
    return math.hypot(defender.x - bc.x, defender.y - bc.y) < radius


def controller_owned_fullback_state(world, defender, fallback):
    """STAGE 2 — STATE OWNERSHIP: the controller's verdict, or *fallback*.

    While a BREAK EMERGENCY is live (the tagged fullback's break latch is
    armed) ``fullback_controller`` is the SOLE authority for the actor's
    movement state, so this shared policy layer must not decide one.  This
    helper is the ONE place the MMDL asks the controller, and it is TOTAL:

      * the controller owns the frame -> its commanded state is returned
        (``CHASE`` on every latch kind, resolved through the controller's own
        intent -> state law, so the two layers cannot disagree);
      * the controller does not own it (no latch, no tag, lever off, or the
        controller is unreachable) -> *fallback*, which is the state this
        branch would have returned before Stage 2.

    ``fullback_controller`` is imported lazily: it is a light leaf module and
    importing it at module scope here would put it on this module's import
    graph for every consumer, including the harnesses that stub the MMDL.
    """
    try:
        from game.defence.defence_lvl_1.fullback_controller import (
            emergency_fullback_state as _fb_owned_state,
        )
    except Exception:                                    # noqa: BLE001
        return fallback
    try:
        owned = _fb_owned_state(world, defender)
    except Exception:                                    # noqa: BLE001
        return fallback
    return owned if owned is not None else fallback


def _is_genuine_line_defender(defender) -> bool:
    """True when *defender* is a genuine line body eligible for the
    tackle-radius CHASE switch.

    TACKLE-RADIUS CHASE SWITCH (2026-09-05): the override applies ONLY to
    genuine line defenders — unfrozen, non-fullback, non-marker, non-DH,
    non-TES-tackler, non-kick-chase bodies.  Dedicated owners keep their
    contracts:
      - fullback keeps geometry / breakaway-chase logic,
      - MARKER_A/B play tight to the ruck (HOLD),
      - DUMMY_HALF is PTB-owned,
      - TACKLER_* are TES-owned,
      - KICK_CHASE has its own chase contract.
    """
    if defender is None:
        return False
    if bool(getattr(defender, "frozen", False)):
        return False
    if float(getattr(defender, "freeze_timer", 0.0) or 0.0) > 0.0:
        return False
    # FULLBACK IS ALLOWED TO CLOSE AND TACKLE like any other defender when it
    # has been promoted out of the deep-anchor role.  The dynamic fullback
    # remains a dedicated geometry actor only while it still carries the fullback
    # tag; once it is selected as a tackler we clear that flag and it re-enters
    # the normal line-body logic.
    # TACKLE-STATE GATE (2026-08-23): a radius-held tackler/marker is not
    # a genuine line body -- the gate's peel writer owns its movement.
    if bool(getattr(defender, "tackle_state_active", False)):
        return False
    role = getattr(defender, "defender_role", None)
    role_name = getattr(role, "name", None) or str(role) if role is not None else None
    if role_name in _TACKLE_RADIUS_EXEMPT_ROLE_NAMES:
        return False
    return True


def decide_movement_state(world, defender, bc, tuners=None) -> MovementState:
    """Return the best MovementState for *defender* this frame.

    Parameters
    ----------
    world : GameWorldState
        The current world state (reads ruck_x_m, tes_active).
    defender : Player
        The defender being decided.
    bc : Player or None
        Ball carrier (None during kicks/loose ball).
    tuners : DefenceTuners or None
        Tuner source for the MMDL thresholds.  All thresholds degrade to
        safe defaults when absent.

    Returns
    -------
    MovementState
    """
    # FULLBACK GEOMETRY OWNER (MMDL).  The CHASE-STATE DEMOTION mirror that
    # used to sit here -- clearing fullback_role_active whenever the actor was
    # in CHASE/SHOOT -- is DELETED (B2, 2026-09-16).  Movement state is never
    # a role-demotion mechanism.  The branch below is DRIFT-or-CHASE only and
    # never revokes the tag; the tag is handed back solely at
    # OpenPlayEngine._arm_tackler_role when the actor is actually armed as a
    # TES tackler, and tackle candidacy is gated by can_acquire_tackle.
    if getattr(defender, "fullback_role_active", False):
        # ── INTENT OWNERSHIP (Stage 1) ──
        # This policy layer does NOT name a fullback movement state.  It states
        # the INTENT and lets ``fullback_controller`` resolve it, so the MMDL's
        # verdict and the geometry writer's verdict can never disagree about
        # the NAME of what the last defender is doing.  (Making the controller
        # the SOLE authority on a latched break -- i.e. stopping this branch
        # from policy-deciding at all -- is Stage 2's State Ownership.)
        #
        # The resolved values are the same MovementState singletons the
        # returns below used to name literally, so the verdict is unchanged.
        from game.defence.defence_lvl_1.fullback_controller import (
            FullbackIntent,
            intent_movement_state,
        )
        _fb_sweep = intent_movement_state(FullbackIntent.SWEEP)
        _fb_chase = intent_movement_state(FullbackIntent.CHASE)
        if bc is None:
            return _fb_sweep
        # ── THE LATCH IS THE BREAK AUTHORITY (2026-09-29) ──
        # The emergency chase is GRANTED on the per-actor break latch
        # (``arm_fullback_break_latch``) and the geometry writer authors the
        # intercept point from it, so the state policy must read the SAME
        # authority.  When this test disagreed with the latch, the sweeper was
        # handed a CHASE target while its movement state stayed DRIFT -- and
        # DRIFT starves the depth axis by construction:
        # ``LATERAL_PRIORITY_BY_STATE[DRIFT] = 0.8`` gives ``dir_x = 0.2 * nx``
        # before normalisation and ``MAX_SPEED_BY_STATE[DRIFT] = 0.4`` caps the
        # approach, which is the "closes in Y but never in X" symptom.
        # CHASE also arms the intercept tracking in ``apply_fullback_locomotion``,
        # which steers the direct bearing on BOTH axes and skips the pre-tackle
        # lateral damping -- exactly the law the intercept-tracking block states.
        # The downstream ``carrier_past_defensive_line`` test is unchanged and
        # still decides the case where no latch is armed.
        #
        # ── STAGE 2 — STATE OWNERSHIP (2026-09-30) ──
        # A live latch IS a break emergency, and during one the FULLBACK
        # CONTROLLER is the sole authority for this actor's movement state.
        # This branch therefore does not decide: it asks the controller and
        # returns its verdict.  ``_fb_chase`` is the fallback for a frame the
        # controller does not own (lever off / controller unreachable), which
        # is what this branch decided before Stage 2 -- so the A/B rollback is
        # behaviour-identical.  Everything BELOW this gate is the shared
        # defensive state policy, and it now runs only once the emergency has
        # ended (the latch cleared at a tackle-cycle end, tag loss, or the
        # carrier dropping behind the latched line).
        try:
            from game.defence.defence_lvl_1.fullback_role import (
                is_fullback_break_latched as _fb_is_latched,
            )
            if _fb_is_latched(defender):
                return controller_owned_fullback_state(
                    world, defender, _fb_chase
                )
        except Exception:
            pass
        # THE BREAK TEST READS THE LATCHED ANCHOR (2026-09-28).  The MMDL
        # fullback branch and the geometry writer must agree on what a break
        # is, so both read the one single reader.  ``break_test_line_anchor_x``
        # is imported lazily: ``fullback_role`` imports THIS module lazily, so
        # a module-scope import here would close the cycle.
        try:
            from game.defence.defence_lvl_1.fullback_role import (
                break_test_line_anchor_x as _fb_break_anchor,
            )
            _test_anchor = _fb_break_anchor(world, defender)
        except Exception:
            _test_anchor = None
        if carrier_past_defensive_line(world, bc, anchor_x=_test_anchor):
            return _fb_chase
        dist = math.hypot(
            float(getattr(defender, "x", 0.0)) - float(bc.x),
            float(getattr(defender, "y", 0.0)) - float(bc.y),
        )
        if dist < _tuner(tuners, "tackle_radius_m", _DEFAULT_TACKLE_RADIUS_M):
            return _fb_chase
        return _fb_sweep

    # ── TACKLE-STATE GATE (2026-08-23) ──
    # A defender still inside the tackle-radius keeps its TACKLE-STATE:
    # the MMDL never chases/slides/shoots it.  Movement is owned by the
    # gate's peel writer until the radius exit releases it.
    if getattr(defender, "tackle_state_active", False):
        return MovementState.HOLD

    # ── STALE-SEED FORCE-HOLD (2026-09-09) ──
    # A line body whose spacing-Y or depth-role seed is stale must never be
    # routed into RETREAT/SLIDE/SHOOT/CHASE/DRIFT — its geometry is
    # undefined, so any pursuit state would jitter it frame-to-frame.  HOLD
    # it at the decision source so the MMDL resolves HOLD (the post-writer
    # apply_seed_hold in DefenceManager.update_open_play is the final word).
    from game.defence.defence_lvl_1.seed_hold import (
        is_seed_stale as _is_seed_stale,
    )
    if _is_seed_stale(defender, world):
        return MovementState.HOLD

    # ── 0. No ball carrier ──
    if bc is None:
        depth_class = getattr(defender, "depth_class", "PASSIVE")
        if depth_class == "PASSIVE":
            return MovementState.RETREAT
        return MovementState.HOLD

    # ── 1. TES guard: TES owns tackler movement ──
    if getattr(world, "tes_active", False) and getattr(defender, "is_tackler", False):
        return getattr(defender, "movement_state", MovementState.HOLD) or MovementState.HOLD

    # Marker guard: markers play tight to the ruck (never MMDL-chased).
    role = getattr(defender, "defender_role", None)
    if role is not None and getattr(role, "name", role) in ("MARKER_A", "MARKER_B"):
        return MovementState.HOLD

    # ── 1.5 TACKLE-RADIUS CHASE SWITCH (2026-09-05) ──
    # A genuine line defender that has breached the tackle radius
    # (dist(defender, bc) < tackle_radius_m) is committing to the tackle.
    # It MUST be CHASE (forward closing, non-smooth-damp) — never RETREAT
    # (backpedal into contact), DRIFT (wrong-side drift into contact), or
    # HOLD (early pre-tackle freeze).  This prevents TES from inheriting
    # a corrupted movement_state when the tackle triggers at this same
    # radius.  Fullback retains dedicated geometry/breakaway-chase logic
    # (handled above); markers/DH/TES tacklers keep dedicated owners
    # (marker guard above / TES guard above).
    #
    # PLACEMENT: after the dedicated-owner guards, BEFORE the convergence /
    # pursuit / retreat branches, so a tackle-committing line defender is
    # ALWAYS CHASE — even when the line-break chase override, convergence
    # dict ownership, or decision lock would otherwise route it elsewhere.
    if _is_genuine_line_defender(defender):
        _tackle_r = _tuner(tuners, "tackle_radius_m", _DEFAULT_TACKLE_RADIUS_M)
        if _tackle_r > 0.0:
            _radial_in = math.hypot(
                float(defender.x) - float(bc.x),
                float(defender.y) - float(bc.y),
            ) < _tackle_r
            if _radial_in:
                # ── TACKLE-GATING SLOT THRESHOLD (2026-09-07 rollout) ──
                # A BC inside the radial radius but OUTSIDE the defender's
                # slot-Y band (|bc.y - target_y| >= TACKLE_SLOT_THRESHOLD_M)
                # must NOT trigger the tackle — it is a radial proximity only,
                # not a slot commitment.  The CHASE switch therefore requires
                # radial-in AND slot-in.
                from game.defence.defence_lvl_1.slot_arbitration import (
                    is_slot_tackle_eligible as _slot_eligible,
                )
                # ── RADIAL+FACING TACKLE ENGAGE GUARD (2026-09-10) ──
                # The slot-Y gate vetoes EVERY engage whenever the carrier's
                # lane Y sits more than `tackle_slot_threshold_m` (0.3 m) off
                # the defender's live Y: the defender is radial-in, closing and
                # facing the carrier, yet it falls through to the convergence /
                # pursuit / retreat / DRIFT branches and no tackle ever forms
                # ("no tackles happen ever").  A genuine line defender that is
                # radial-in AND FACING the carrier has a live tackle attempt —
                # bypass the slot gate and force the closing state (stamping
                # target_source = "tackle_engage_guard") so TES can arm.
                from game.defence.defence_lvl_1.tackle_engage_guard import (
                    is_tackle_engage_guard as _is_engage_guard,
                    force_tackle_engage as _force_tackle_engage,
                )
                if _is_engage_guard(defender, bc, world, tuners):
                    _force_tackle_engage(defender, world=world)
                    return MovementState.CHASE
                if _slot_eligible(bc, defender, world):
                    return MovementState.CHASE

    # ── STAGE 3E CONVERGENCE OWNERSHIP GUARD (2026-08-25) ──
    # A defender holding an authoritative convergence entry
    # (``world.frame_convergence_targets[id(d)]`` — published by the
    # manager-owned ConvergenceManager each frame) is DICT-OWNED: it has
    # been committed to close on the ball carrier.  CHASE/SLIDE/DRIFT must
    # never override that intent — SLIDE's lateral-priority physics zeroes
    # vx, DRIFT/RETREAT/HOLD abandon the closing vector entirely — so this
    # branch returns the CONVERGENCE-ALIGNED pursuit state:
    #   SHOOT inside the collapse commit rail (forward-only finish),
    #   CHASE otherwise (diagonal pursuit toward the dict point).
    #
    # Precedence:
    #   - BELOW the fullback / tackle-state / TES guards so those dedicated
    #     movement owners always win (the convergence manager itself never
    #     publishes for frozen or fullback defenders; TES tacklers keep
    #     their TES-owned current state via the guard above).
    #   - GATED ON ``not tes_active`` mirroring the TASK VI carrier-past-
    #     line override: during a live TES frame the engagement system owns
    #     every defender's movement and no MMDL pursuit state may leak onto
    #     it (movement-reactivation ASSERT-A).  During TES-drive the caller
    #     projects OPEN_PLAY with ``tes_active=False``, so dict-owned line
    #     closers still get their convergence-aligned state there.
    if not getattr(world, "tes_active", False):
        _conv_map = getattr(world, "frame_convergence_targets", None) or {}
        if id(defender) in _conv_map:
            _dist = math.hypot(
                getattr(defender, "x", 0.0) - bc.x,
                getattr(defender, "y", 0.0) - bc.y,
            )
            if should_commit(_dist):
                return MovementState.SHOOT
            return MovementState.CHASE

    depth_class = getattr(defender, "depth_class", "PASSIVE")

    # ── PURSUIT/COMMIT HARD OVERRIDE (2026-09-02) ──
    # BC within pursuit_commit_radius_m (default 12m): the defender is
    # forced into pursuit/commit regardless of depth class, spacing,
    # role, retreat compliance, decision lock, or line-hold/DRIFT
    # fallback — until CONTACT fires.  SHOOT inside the 1.0 m collapse
    # commit rail (forward-only finish), CHASE otherwise (diagonal
    # pursuit).  Bypasses the oscillation lock so a locked SLIDE/RETREAT
    # can never hold the defender off the carrier when it is in strike
    # range.  Dedicated-owner guards (fullback / tackle-state / TES
    # tackler / marker) already returned above and keep their authority.
    _pursuit_radius = _tuner(
        tuners, "pursuit_commit_radius_m", _DEFAULT_PURSUIT_COMMIT_RADIUS_M
    )
    if is_in_pursuit_commit_range(defender, bc, _pursuit_radius):
        _pursuit_dist = math.hypot(
            getattr(defender, "x", 0.0) - bc.x,
            getattr(defender, "y", 0.0) - bc.y,
        )
        if should_commit(_pursuit_dist):
            return MovementState.SHOOT
        return MovementState.CHASE

    # ── 2. Retreat compliance (TASK I-B hysteresis band) ──
    ruck_x = getattr(world, "ruck_x_m", None)
    if ruck_x is not None:
        compliance_dist = _tuner(tuners, "retreat_compliance_distance_m", 10.0)

        # EXIT hysteresis: once RETREAT is held, the defender must clear
        # the compliance line by _RETREAT_HYSTERESIS_M before leaving.
        current_state = getattr(defender, "movement_state", None)
        current_name = getattr(current_state, "name", None) or str(current_state)
        if current_name == "RETREAT":
            if not (defender.x >= ruck_x + compliance_dist + _RETREAT_HYSTERESIS_M):
                return MovementState.RETREAT
        else:
            # ENTRY hysteresis: only below the band does RETREAT engage.
            if defender.x < ruck_x + compliance_dist - _RETREAT_HYSTERESIS_M and depth_class == "PASSIVE":
                return MovementState.RETREAT

    # ── TASK I-B DECISION LOCK (oscillation kill) ──
    # A previously locked state persists for _DECISION_LOCK_FRAMES frames
    # unless a hard anchor-band crossing overrides it.  The lock is stored
    # on the defender as `_mmdl_state_until_frame` (set by the MMDL caller
    # in DefensiveBehaviourUnified via resolve/apply; the decision layer
    # reads it here so the writer itself is stable).
    if _hard_threshold_violated(world, defender, bc, tuners) is False:
        current_state = getattr(defender, "movement_state", None)
        current_name = getattr(current_state, "name", None) or str(current_state)
        if current_name in _LOCKED_STATES:
            lock_until = getattr(defender, "_mmdl_state_until_frame", -1)
            frame = getattr(world, "frame_count", 0)
            if frame < lock_until:
                # ── TASK VI CARRIER-PAST-LINE CHASE OVERRIDE (2026-08-15) ──
                # A locked SLIDE must never stall the pursuit once the BC is
                # clearly past the anchor: engaged defenders switch to CHASE
                # (diagonal pursuit) immediately, bypassing the lock.  The
                # lock only persists sub-threshold oscillation, never a
                # broken line — this is the hard override from the task spec.
                # COLLAPSE-COMMIT EXCEPTION: the override must NOT beat the
                # 1.0 m commit rail — a defender inside the collapse radius
                # is in the collision-finish phase and keeps SHOOT (forward-
                # only) even when the BC is past the line.  Guards with the
                # same should_commit() the branch-4 tree uses.
                # TES-OWNER GATE (2026-08-15): during an active TES the
                # engagement system owns every defender's movement — the
                # stasis/retreat pin zeroes velocity and the TASK VI chase
                # override must never leak CHASE onto a TES frame.
                if (
                    (
                        carrier_past_defensive_line(world, bc)
                        or _in_post_catch_chase_window(world)
                    )
                    and not getattr(world, "tes_active", False)
                    and _is_engageable(
                        getattr(defender, "depth_class", "PASSIVE")
                    )
                ):
                    dist = math.hypot(
                        getattr(defender, "x", 0.0) - bc.x,
                        getattr(defender, "y", 0.0) - bc.y,
                    )
                    if not should_commit(dist):
                        return MovementState.CHASE
                return current_state

    # ── 3. Line hold / drift for PASSIVE defenders ──
    if not _is_engageable(depth_class):
        if _is_deep_passive(defender, bc, tuners):
            return MovementState.HOLD
        return MovementState.DRIFT

    # ── 4. PRIMARY/SECONDARY: commit windows ──
    dist = math.hypot(defender.x - bc.x, defender.y - bc.y)
    lateral = abs(defender.y - bc.y)

    lateral_threshold = _tuner(tuners, "lateral_slide_threshold_m", _DEFAULT_LATERAL_SLIDE_THRESHOLD_M)
    engage_distance = _tuner(tuners, "engage_distance_m", _DEFAULT_ENGAGE_DISTANCE_M)
    commit_distance = _tuner(tuners, "commit_distance_m", _DEFAULT_COMMIT_DISTANCE_M)

    # ── ITEM 7 (2026-08-15): MOVEMENT-STATE COMMIT (pre-tackle alignment).
    # Inside the 1.0m collapse radius the total distance to the BC is
    # smaller than any realistic lateral separation, so the defender is
    # forced into the COMMIT (SHOOT) state -- forward-only, lateral_priority
    # 0.0 -- BEFORE the SLIDE branch can engage.  This stops HOLD/DRIFT/SLIDE
    # from interfering with the collision finish when the tackler is already
    # on top of the carrier.
    if should_commit(dist):
        return MovementState.SHOOT

    # ── TASK VI CARRIER-PAST-LINE CHASE OVERRIDE (2026-08-15) ──
    # Once the BC is clearly past the anchor (bc.x > anchor_x + 1.0), engaged
    # PRIMARY/SECONDARY defenders chase diagonally (CHASE: lateral_priority
    # 0.3 -> non-zero vx + vy) instead of entering SLIDE (lateral_priority
    # 1.0 -> vx ~ 0, the lateral-only freeze).
    #
    # POST-CATCH PURSUIT (2026-08-16): the same override fires during the
    # post-catch window (post_catch_chase_window_s > 0) so the line keeps
    # chasing the BC immediately after a kickoff catch instead of waiting
    # for the BC to run past the anchor.
    #
    # SCOPE: this override targets the SLIDE branch ONLY.  The SHOOT commit
    # rail (collapse radius 4. pre-block) AND the short-distance commit
    # window (4b: dist < commit_distance AND lateral <= threshold) must keep
    # winning — a defender already on top of the carrier or in its commit
    # lane finishes with the forward-only SHOOT, never a diagonal CHASE.
    if (
        (
            carrier_past_defensive_line(world, bc)
            or _in_post_catch_chase_window(world)
        )
        and not getattr(world, "tes_active", False)
        and _is_engageable(depth_class)
        and not (dist < commit_distance and lateral <= lateral_threshold)
    ):
        return MovementState.CHASE

    # 4a. Close lateral, not yet committed: SLIDE to maintain angle.
    if lateral > lateral_threshold and dist < engage_distance:
        return MovementState.SLIDE

    # 4b. Short distance, commit window: SHOOT (jam forward).
    if dist < commit_distance and lateral <= lateral_threshold:
        return MovementState.SHOOT

    # 4c. Default: CHASE — angled pursuit.
    return MovementState.CHASE


def resolve_movement_state(defender, next_state: MovementState, fallback: MovementState) -> MovementState:
    """Validate a transition from the defender's current state.

    Returns *next_state* if the transition is legal, otherwise *fallback*.

    FULLBACK RETREAT BAN (2026-08-17): the fullback never HOLDs RETREAT —
    it is geometry-driven only (``decide_movement_state`` already returns
    DRIFT for it, but the MMDL blend/resolve path in
    DefensiveBehaviourUnified could route a locked RETREAT back through
    here).  When next_state is RETREAT and the defender is the active
    fullback actor, the RETREAT is swallowed to DRIFT so no pipeline path
    can land the last defender in RETREAT.
    """
    current = getattr(defender, "movement_state", MovementState.HOLD)
    if current is None:
        current = MovementState.HOLD
    if (
        getattr(defender, "fullback_role_active", False)
        and getattr(next_state, "name", None) == "RETREAT"
    ):
        # INTENT OWNERSHIP (Stage 1): the retreat ban resolves through the
        # controller too (SWEEP -> the same MovementState.DRIFT singleton), so
        # no path in this module names a fullback movement state of its own.
        from game.defence.defence_lvl_1.fullback_controller import (
            FullbackIntent,
            intent_movement_state,
        )
        return intent_movement_state(FullbackIntent.SWEEP)
    return next_state if is_valid_transition(current, next_state) else fallback


def resolve_pre_tackle_stuck(world, defender, state: MovementState) -> MovementState:
    """TASK IV PATCH 1 — pre-tackle STUCK_NOT_FROZEN fallback.

    Any defender in OPEN_PLAY with a valid target (target_x != x) and zero
    velocity who would otherwise HOLD/DRIFT in place is assigned a
    movement state so the state machine always produces motion:

        PASSIVE/PASSIVE-band  -> RETREAT   (backpedal to anchor/spacing)
        PRIMARY/SECONDARY     -> SLIDE     (lateral shuffle, keep the line)

    This is the LAST-RESORT fallback and runs AFTER the full decision tree
    (incl. the MMDL lerp blend + ``resolve_movement_state`` — the blend at
    t=0.5 filters HOLD->RETREAT back to HOLD, so the fallback must be
    applied by the WRITER after resolve).  It can never override:

      - frozen defenders              (their targets are freeze-owned)
      - the fullback actor            (geometry-driven only)
      - TES phases                    (TES owns tackler movement)
      - markers / DH / kick-chase     (tight-to-ruck roles, never stuck)
      - committed tacklers            (tackler-commit writer owns them)
      - a defender AT its target      (|target_x - x| <= _STUCK_AT_TARGET_M —
                                        the freeze audit's at_target grace)
      - a defender with non-zero TOTAL velocity (pure-lateral SLIDE/DRIFT
                                        movers have vx ~ 0 by design and are
                                        MOVING — cross-checking only vx would
                                        flag every line shuffle as stuck)
      - a state other than HOLD/DRIFT (RETREAT/SLIDE/CHASE/SHOOT already
                                        produce velocity by their physics)

    Returns *state* unchanged when none of the stuck conditions hold.
    """
    # Guards that make the fallback a no-op.
    if getattr(defender, "frozen", False):
        return state
    if getattr(defender, "fullback_role_active", False):
        return state
    # TACKLE-STATE GATE (2026-08-23): a radius-held tackler/marker is
    # never stuck -- the gate's peel writer owns its movement.
    if getattr(defender, "tackle_state_active", False):
        return state
    if getattr(world, "tes_active", False):
        return state
    if getattr(defender, "_tackle_commit_active", False):
        return state

    role = getattr(defender, "defender_role", None)
    if role is None:
        return state
    role_name = getattr(role, "name", None) or str(role)
    if role_name in _STUCK_EXEMPT_ROLE_NAMES:
        return state

    # Only HOLD/DRIFT can stall (all other states produce velocity).
    state_name = getattr(state, "name", None) or str(state)
    if state_name not in ("HOLD", "DRIFT"):
        return state

    # "Stuck" requires a live target the defender is NOT parked on.
    target_x = getattr(defender, "target_x", None)
    if target_x is None:
        return state
    if abs(float(target_x) - getattr(defender, "x", 0.0)) <= _STUCK_AT_TARGET_M:
        return state

    # Zero TOTAL velocity — a pure-lateral mover (vx ~ 0, vy != 0) is fine.
    if math.hypot(
        getattr(defender, "vx", 0.0),
        getattr(defender, "vy", 0.0),
    ) >= _STUCK_VELOCITY_EPS:
        return state

    # Assign the fallback movement state.
    depth_class = getattr(defender, "depth_class", "PASSIVE")
    chosen = (
        MovementState.RETREAT
        if depth_class == "PASSIVE"
        else MovementState.SLIDE
    )

    # Validate the transition from the defender's CURRENT state so the
    # state machine table is never violated (HOLD->RETREAT is legal, and
    # is_valid_transition(current, current) is always True).
    current = getattr(defender, "movement_state", MovementState.HOLD)
    if current is None:
        current = MovementState.HOLD
    if not is_valid_transition(current, chosen):
        return state
    return chosen
