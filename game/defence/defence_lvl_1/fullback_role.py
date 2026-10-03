"""
fullback_role.py — Dynamic fullback role for the defensive line.

PURPOSE
-------
Provides a single dedicated "last defender" that:

  - Sits 3–30 m behind the defensive line depending on field position
    (deep at kickoff, mid-depth at halfway, shallow near the goal line).
  - Shifts laterally 60% toward the ball (sweep / kick coverage geometry).
  - At KICKOFF (2026-08-11) the ACTOR is forced to jersey 1 (when jersey 1
    is not the kicker) at (formation_line_x, centre + side_sign * 3.0).
  - Reassigns automatically to the best available player whenever the
    current actor becomes unavailable (mirrors the dummy-half logic).
  - Integrates cleanly with MMDL + the target stabiliser WITHOUT
    interfering with TES / PTB or the engagement depth classification.

DESIGN PRINCIPLE
----------------
The fullback is a GEOMETRY-DRIVEN defender.  The OpenPlayEngine already
gives every non-frozen, non-CHASE/SHOOT defender target smoothing via
``player.target_stabiliser.should_stabilise_target()``.  By forcing the
fullback actor into MovementState.DRIFT at the MMDL layer (step 10b of
the structured OPEN_PLAY pipeline) and NOT marking it ``_mmdl_moved``,
the regular ``Player.update(dt)`` accelerator consumes the stabilised
anchor — smooth movement, no oscillation, ~0.4x line speed.  No new
stabiliser code is required (stabiliser gating is implicit).

Why a dedicated attribute and NOT a DefenderRole:
  - ``world.rules.movement_contracts.apply_phase_invariants()`` freezes
    any role absent from MOVEMENT_RULES.  A new DefenderRole.FULLBACK
    would map to Role.NONE and freeze the actor in every phase.
  - The fullback actor therefore keeps ``defender_role =
    DEF_LINE_READY`` (always a legal mover) and we record the role in
    the boolean ``fullback_role_active`` (plus ``role = "FULLBACK"``
    for debug overlays).

FIELD GEOMETRY
--------------
The engine field runs x ∈ [10, 110] (tryline → tryline) with the
defensive line at ``defensive_line_x = ruck_x + 10``.  Y spans
[0, 70] with centre 35.0 (NOT 40.0 — the spec's 40.0 predates the
authoritative 70 m field width in ``Player.FIELD_MIN_Y/MAX_Y``).

Depth formula (normal play):
    t      = clamp((ruck_x - LEFT_TRYLINE_X) / (HALFWAY_X - LEFT_TRYLINE_X), 0, 1)
    depth  = lerp(3.0, 30.0, t)
    anchor_x = defensive_line_x + depth

The fullback sits DEEP BEHIND the line toward +X.  The engine's fixed
directional rule is "defence always retreats RIGHT (+X)" — the defending
team's tryline is always at the +X end, so the last defender (fullback)
deploys further +X than the line (``defensive_line_x = ruck_x + 10``).
Using ``- depth`` here (or ``ruck_x - depth``) would place the fullback
on the ATTACK side of the ruck inside the PTB bubble, blocking the DH
scoop lane, the marker cone, and the retreat path (root cause #1/#9,
2026-08-08).

Lateral formula (normal play):
    anchor_y = FIELD_CENTRE_Y + (ball.y - FIELD_CENTRE_Y) * 0.6

Kickoff override (2026-08-11):
    anchor_x = formation_line_x        (halfway + 2.0 = 62 m)
    anchor_y = FIELD_CENTRE_Y + side_sign * 3.0

Designated-FB priority + central replacement band (2026-08-11):
  - Jersey 1 is the PERSISTENT fullback priority every frame — KICKOFF
    and OPEN_PLAY — gated on the KICKOFF_FB_OVERRIDE runtime flag.  The
    distance gate is waived for the priority so jersey 1 survives the
    kickoff-catch anchor snap (jersey 1 is still on the flat line when
    the post-catch anchor re-derives from the new ball carrier).
  - When jersey 1 is unavailable (tackler / marker / frozen / attached /
    arrival, or jersey 1 IS the kicker) the fullback hands to the DEEPEST
    defender (furthest +X) INSIDE the central corridor y ∈ [20, 50].
    Defenders near the sidelines never count as fullback candidates.
"""

import math
import os

from player.player import Player, DefenderRole

# ─────────────────────────────────────────────────────────────────────
# FIELD CONSTANTS (authoritative engine geometry)
# ─────────────────────────────────────────────────────────────────────
LEFT_TRYLINE_X: float = 10.0
HALFWAY_X: float = 60.0
FIELD_MAX_X: float = 112.0      # allow the FB to sit a little into-goal beyond the tryline
FIELD_CENTRE_Y: float = 35.0  # mid of Player.FIELD_MIN_Y..FIELD_MAX_Y (0..70)

# ─────────────────────────────────────────────────────────────────────
# FULLBACK TUNERS
# ─────────────────────────────────────────────────────────────────────
DEPTH_MIN_M: float = 10.0      # goal-line defence (shallow, ready for grubbers)
DEPTH_MAX_M: float = 20.0     # kickoff / own-half depth (sweep coverage)
LATERAL_TRACK: float = 0.6    # LOOSE-BALL sweep weight (no ball carrier)
MAX_ANCHOR_DISTANCE_M: float = 25.0  # actor too far from anchor -> reassign

# ─────────────────────────────────────────────────────────────────────
# OPEN-PLAY LATERAL LANE MIRRORING (2026-09-22)
# ─────────────────────────────────────────────────────────────────────
# THE DEFECT THIS REMOVES ("the fullback freezes on a rail"): with a live
# ball carrier the lateral anchor was `centre + (bc.y - centre) * 0.6` and
# nothing else, so the last defender was never ASKED to leave its channel.
# Measured on the live engine (probe_fullback_lateral_tracking.py, 3000
# frames): the anchor sat 5-8 m off the carrier's lane on every DRIFT /
# RECOVER frame, and the actor a further 1-2 m short of even that — the
# fullback never moved across the field with the play at all.
#
# THE RULE: in open play (a live ball carrier is the anchor reference) the
# DESTINATION mirrors the carrier's lane at full weight, so the lateral
# target is the lane the last defender must actually cover and the actor
# slides across the field with the play.  The 0.6 ``LATERAL_TRACK`` sweep
# weight is kept for the LOOSE-BALL case (no carrier) where a fractional
# shift off centre is the correct kick-coverage geometry.  Depth is
# untouched: the FB still sits 3-30 m behind the line.
#
# A-B / rollback lever: RLM_FB_LATERAL_OPEN_PLAY=0 restores the historical
# 0.6 rail in open play with no code revert (same pattern as the other
# runtime levers in this module).
FB_LATERAL_TRACK_OPEN_PLAY: float = 1.0
FB_LATERAL_OPEN_PLAY: bool = (
    os.environ.get("RLM_FB_LATERAL_OPEN_PLAY", "1").strip().lower()
    not in ("0", "false", "no", "off")
)

# ──────────────────────────────────────────────────────────────────────
# KICKOFF FLIGHT LANE TRACKING (2026-09-27)
# ──────────────────────────────────────────────────────────────────────
# THE DEFECT THIS REMOVES ("the fullback is frozen until the ball is
# caught").  The kickoff override pinned the airborne anchor to a
# CONSTANT -- (formation_line_x, centre + side_sign * KICKOFF_FB_LATERAL_
# OFFSET_M) -- and ``placement_writer`` hard-places the actor exactly ON
# it, so ``target == position`` for the whole ball flight: ``dist == 0.0``
# from frame 1, the arrival branch stopped the velocity every frame, and
# the authored DRIFT had no ground to close.  The actor could not move
# until ``world.in_kickoff`` dropped at the reception.
#
# An A/B against the shared arrival branch proved this: preserving the
# authored DRIFT label (2026-09-27) left position and velocity IDENTICAL,
# because the defect was never the state label -- it was the TARGET.  A
# zero-length command integrates to zero motion whatever state it
# carries.
#
# THE RULE: the airborne lateral anchor MIRRORS the live ball's lane at
# the open-play weight, so the last defender starts sliding across the
# field the moment the ball is kicked.  Depth is untouched: the anchor X
# stays the flat formation line while the ball is in the air, because the
# sweeper's job during a kick is to cover the LANDING LANE, not to leave
# the line.
#
# A-B / rollback lever: RLM_FB_FLIGHT_TRACK=0 restores the historical
# constant flight anchor byte-for-byte with no code revert (same pattern
# as the levers above).
FB_FLIGHT_TRACK: bool = (
    os.environ.get("RLM_FB_FLIGHT_TRACK", "1").strip().lower()
    not in ("0", "false", "no", "off")
)

# ──────────────────────────────────────────────────────────────────────
# KICKOFF FLIGHT DEPTH-ROLE TRACKING (2026-09-28)
# ──────────────────────────────────────────────────────────────────────
# THE DEFECT THIS REMOVES ("the fullback cannot use its depth until the
# ball is caught").  The kickoff override pinned the airborne anchor X to
# the flat formation line (62.0) for the WHOLE flight, so the actor's only
# live axis was Y.  ``placement_writer`` then hard-placed it exactly ON
# that line, so ``dist == 0.0`` from frame 1, the arrival branch stopped
# the velocity every frame, and the body was a statue in X for the entire
# ball flight.  The 2026-09-27 lateral fix corrected Y only, by
# construction ("depth is deliberately NOT tracked").
#
# THE RULE: once the ball is kicked the actor holds its NORMAL DEPTH ROLE
# on both axes -- the same ``depth_role_anchor_x`` law open play uses --
# referenced to the live ball instead of a ruck, so the sweeper covers the
# ground its depth role declares in X as well as Y.
#
# ANTI-RETREAT GUARD (the "can't go backwards" rule).  The depth role is
# ``(reference_x + 10) + depth(reference_x)`` with depth lerping 3..30 by
# field position, so at the strike the ball at x = 60 asks for
# ``70 + 30 = 100`` while the actor is placed at 62 -- a 38 m command
# BACKWARDS toward its own tryline.  That is never a real demand: the ball
# is simply too close to be a "deep enough" reference.  The guard is a
# one-directional clamp, ``anchor_x <= defender.x``, so the actor may not
# be sent behind its own position.  It is SELF-RELEASING: solving
# ``(b + 10) + (3 + 0.54(b - 10)) = 62`` gives ``b = 35.3`` m, so the clamp
# stops binding once the ball has travelled ~25 m and the depth role comes
# in FRONT of the actor -- exactly "once the ball goes deep enough they
# track as to their depth role".  No frame counter is needed; the geometry
# supplies the release.
#
# THE BREAK CLAUSE is satisfied by SCOPE, not by a second test: a line
# break requires a ball carrier, and ``world.in_kickoff`` is False by
# definition once there is one.  The guard lives entirely inside the
# kickoff branch, so it is released the instant the flight ends and can
# never obstruct the open-play breakaway chase.
#
# A-B / rollback lever: RLM_FB_FLIGHT_DEPTH_TRACK=0 restores the
# historical constant-X flight anchor byte-for-byte with no code revert,
# same pattern as every other runtime lever in this module.
FB_FLIGHT_DEPTH_TRACK: bool = (
    os.environ.get("RLM_FB_FLIGHT_DEPTH_TRACK", "1").strip().lower()
    not in ("0", "false", "no", "off")
)

# The depth the defensive line sits behind its reference (ruck / live
# ball).  MIRRORS ``KickoffEngine._refresh_kickoff_anchor``
# (``set_pre_ruck_anchor(w, self.ball.x_m + 10.0)``) so the airborne
# depth-role line and the engine's own line anchor are the same number.
KICKOFF_LINE_ANCHOR_DEPTH_M: float = 10.0

# The legal lane strip a kickoff tracking reference must sit inside before
# it may be mirrored.  Guards against an unset world field (kickoff_landing_y
# defaults to 0.0) being mistaken for a genuine touchline lane.
FB_FLIGHT_LANE_MIN_Y: float = 0.0
FB_FLIGHT_LANE_MAX_Y: float = 70.0

# ─────────────────────────────────────────────────────────────────────
# THE 4-ZONE LATERAL SWEEPER MATRIX (Stage 3.3, 2026-09-24)
# ─────────────────────────────────────────────────────────────────────
# THE GAP THIS CLOSES.  The lateral law above is ONE weight for the WHOLE
# backfield: ``FB_LATERAL_TRACK_OPEN_PLAY`` (1.0) in open play, the loose-ball
# ``LATERAL_TRACK`` (0.6) rail otherwise.  So the CRITICAL case -- the last
# defender holding the try line, where a sweep that falls short of the
# carrier's lane is a walk-in -- and the ordinary case -- the same defender
# covering a backfield with the play 60 m away -- are THE SAME NUMBER.  The
# law that must never be weakened and the law that should be free to be tuned
# cannot be stated separately, so neither can be enforced.
#
# THE MATRIX.  The field is split into FOUR X zones along the fullback's OWN
# anchor position (not the carrier's, not the ruck's: the zone is "where the
# last defender is being asked to stand"), and each zone carries its own
# declared lateral weight:
#
#     zone 0   LEFT_TRYLINE_X .. 35.5   THE CRITICAL LATERAL SWEEPER LAW
#     zone 1           35.5   .. 61.0   own half, outside the line
#     zone 2           61.0   .. 86.5   the far half
#     zone 3           86.5   .. FIELD_MAX_X   into-goal beyond the attack line
#
# ZONE 0 IS ENFORCED, NOT MERELY DECLARED.  With the ball that deep the last
# defender is the only body between the carrier and the line, so the zone's
# weight is floored at full sweep by ``fullback_lateral_zone_weight``: no
# declared value, no future tuner and no mis-declaration can leave the actor
# short of the carrier's lane in the try-line zone.  Zones 1-3 are DECLARED
# BUT FLUID -- they ship at the same full weight the single rail did, so the
# shipped geometry is unchanged, and they are the surfaces a user tuner
# dials toward the loose-ball rail without ever touching the sweeper law.
#
# THE SHIPPED DEFAULT IS THEREFORE BEHAVIOUR-NEUTRAL: every zone ships at
# 1.0, which is exactly the weight the single rail already applied in open
# play, so no frame's geometry moves until someone tunes a fluid zone.
FB_LATERAL_ZONE_COUNT: int = 4

# The zone boundaries, inclusive-low / exclusive-high, so a position EXACTLY
# on a boundary belongs to the zone above it and the intervals tile the field
# with no gap and no overlap.
FB_LATERAL_ZONE_EDGES_M: tuple = (
    LEFT_TRYLINE_X,   # 10.0 — the defending try line
    35.5,             # a quarter of the 102 m strip
    61.0,
    86.5,
    FIELD_MAX_X,      # 112.0 — the into-goal limit the anchor clamps to
)

# The DECLARED per-zone lateral weights.  Zone 0 is additionally FLOORED by
# the enforcement below; zones 1-3 are read exactly as declared.
FB_LATERAL_ZONE_WEIGHTS: tuple = (1.0, 1.0, 1.0, 1.0)

# The zone that carries the critical lateral sweeper law, and the floor the
# enforcement holds it at.  A tuner may raise zone 0 above full sweep if a
# future stage ever wants an over-sweep; it can never take it below.
FB_LATERAL_ZONE_TRYLINE: int = 0
FB_LATERAL_ZONE_FLOOR: float = 1.0

# Lateral ground (m) owed by the actor to its OWN anchor that arms the
# open-play recovery locomotion, so a cross-field slide runs on the
# cap-exempt accel-lerp path at the ordinary line pace instead of the
# 2.0 m/s sweeper cap — which is what actually pinned the actor on the
# rail once its destination started moving laterally.  Paired with
# FB_RECOVER_EXIT_DEBT_M as the release band.
FB_LATERAL_RECOVER_M: float = 2.0

# ─────────────────────────────────────────────────────────────────────
# 2D STEP-OUT / LATERAL-BREAK GATE (2026-09-22)
# ─────────────────────────────────────────────────────────────────────
# The breakaway chase override used to fire on `bc.x > line_anchor_x + 1.0`
# ONLY, so a carrier who broke the line LATERALLY (outside the sweeper's
# channel, still level with or behind the line in X) was never pursued: no
# CHASE state, no carrier-lane target, and no tackle candidacy
# (``line_break_active`` was the same X-only test).  The gate is now 2D —
# a DEPTH break (the historical X test) or a LATERAL break (a channel
# breach within the sweeper's 2D reach).
FB_CHASE_TRAIL_M: float = 1.0            # gap behind the projected carrier
FB_LATERAL_BREAK_M: float = 5.0          # channel breach = a lateral break
FB_LATERAL_BREAK_RADIUS_M: float = 25.0  # 2D reach in which it is contestable

# ── EMERGENCY-CHASE INTERCEPT TARGET (2026-09-28) ──
# THE DEFECT THIS REMOVES ("the fullback does not use the y axis, so it
# overshoots the carrier").  Every emergency-chase writer aimed the last
# defender at the carrier's LIVE position, offset by a fixed, X-ONLY trail
# (``bc.x - FB_CHASE_TRAIL_M``, ``bc.y``).  Both axes were therefore a
# MEASUREMENT of where the carrier was, never a PREDICTION of where it was
# going:
#
#   * the Y command was ``bc.y`` exactly -- a pure mirror of the live lane, so
#     the actor was asked to slide to the lane the carrier had already left.
#     No writer in the engine supplied a lateral projection: the only
#     prediction machinery in the codebase (``anticipation_layer``) is
#     disabled by default AND restricted to the line depth roles, so the
#     last defender is structurally excluded from it;
#   * the X command was a 1.0 m GOAL-SIDE offset, so the body closed on a
#     point the carrier had already vacated.
#
# Measured signature of the pair: the lateral error GREW while the X error
# closed, and the body crossed the carrier's lane.
#
# THE RULE: intercept the carrier's projected path, not its trail. Projecting
# only Y leaves a fullback arriving at the carrier's OLD lateral lane when it
# is moving diagonally; projecting both axes sends it toward the future XY
# point. The target is the predicted carrier position itself (no trail
# subtraction), and the field clamp allows the fullback to take an in-goal
# position to protect the tryline:
#
#     tau       = clamp(d / s_def * damping, 0, FB_CHASE_LEAD_MAX_S) * near_taper
#     (lead_x, lead_y) = clamp_length((bc.vx, bc.vy) * tau, FB_CHASE_LEAD_MAX_M)
#     target_x = bc.x + lead_x
#     target_y = bc.y + lead_y
#
# ``d`` is the 2D distance to the carrier and ``s_def`` the pursuer's governed
# ceiling.  The whole predicted displacement is capped so a jinking carrier
# cannot throw the command across the field.
#
# THE NEAR-RANGE TAPER is not a convenience.  The pre-tackle slot gate reads
# ``|bc.y - target_y| < tackle_slot_threshold_m`` (0.3 m) to decide whether a
# radial approach is a slot commitment, so a full-strength lateral projection
# inside the contact radius would hold ``target_y`` off the carrier's lane and
# SUPPRESS the arm.  The Y lead therefore decays to ZERO inside
# ``FB_CHASE_LEAD_NEAR_M`` and reaches full strength at
# ``FB_CHASE_LEAD_FULL_M``: far away the actor anticipates the lane, up close
# it takes the carrier's own lane.
#
# The near-range command is the live carrier position on EVERY heading,
#
#     |bc - carrier| <= FB_CHASE_LEAD_NEAR_M  ->  (bc.x, bc.y)
#
# so the contact-range contract -- the slot gate, the arm, the engagement -- is
# kept intact, and the projection only ever acts at the range where
# anticipation is free to help.
#
# ROLLBACK: ``RLM_FB_CHASE_INTERCEPT=0`` restores the historical X-only trail
# BYTE-FOR-BYTE -- ``(bc.x - FB_CHASE_TRAIL_M, bc.y)``, and
# ``max(anchor_x, bc.x - FB_CHASE_TRAIL_M)`` for a lateral break -- with no
# code revert (the same pattern as every other runtime lever in this module).
FB_CHASE_INTERCEPT_ENV_VAR: str = "RLM_FB_CHASE_INTERCEPT"
FB_CHASE_INTERCEPT: bool = (
    os.environ.get(FB_CHASE_INTERCEPT_ENV_VAR, "1").strip().lower()
    not in ("0", "false", "no", "off", "")
)

# Lead-horizon damping.  The raw ``d / s_def`` is the time to reach the
# carrier's CURRENT position with the carrier stationary; projecting over the
# whole of that horizon over-leads a manoeuvring carrier (the classic
# pure-pursuit instability), so it is damped toward the conservative end while
# still projecting.
FB_CHASE_LEAD_DAMPING: float = 0.85
FB_CHASE_LEAD_MAX_S: float = 1.5        # hard ceiling on the horizon (s)
FB_CHASE_LEAD_MAX_M: float = 12.0       # hard ceiling on the projection (m)
FB_CHASE_LEAD_NEAR_M: float = 3.0       # inside this the lead is suppressed
FB_CHASE_LEAD_FULL_M: float = 8.0       # beyond this the lead is at full horizon
FB_CHASE_LEAD_DEFAULT_SPEED_MPS: float = 8.3  # ceiling fallback (stub body)
FB_CHASE_TRAIL_MIN_SPEED_MPS: float = 0.05    # below this: no carrier heading

# The chase-target contracts, as reported by ``fullback_chase_mode``.
FB_CHASE_MODE_INTERCEPT: str = "intercept"
FB_CHASE_MODE_TRAIL: str = "trail"

# ── LINE-INTACT EXCLUSION (2026-09-27) ──
# THE OVER-COMMIT THIS REMOVES ("the fullback leaves its post for a tackle
# the line already has").  The lateral gate above admits the last defender
# on a CHANNEL BREACH alone -- more than ``FB_LATERAL_BREAK_M`` off the
# sweeper's lane and inside its 2D reach -- with no reference to the X
# STATE OF THE LINE.  A carrier merely swinging wide BEHIND an intact line
# therefore committed the sweeper to a contest that the line, standing
# between the carrier and the tryline, should own; the last defender left
# the backfield it alone covers to join a tackle it did not own.
#
# THE RULE: a LATERAL breach only ADMITS the defender once the carrier has
# drawn LEVEL WITH the line (within ``FB_LATERAL_BREAK_LEVEL_M`` BELOW the
# line anchor, so the band is level-with-the-line through the 1.0 m depth
# margin above it).  While the carrier is still clearly behind the line
# the line is INTACT and owns the play; the sweeper holds its sweeper
# contract.  The DEPTH branch is untouched -- once the carrier is past the
# line the sweeper still chases and still contests, exactly as before.
#
# A-B / rollback lever: RLM_FB_LATERAL_BREAK_LEVEL=0 restores the
# historical channel-only admission with no code revert.
FB_LATERAL_BREAK_LEVEL_M: float = 3.0
FB_LATERAL_BREAK_LEVEL_ENABLED: bool = (
    os.environ.get("RLM_FB_LATERAL_BREAK_LEVEL", "1").strip().lower()
    not in ("0", "false", "no", "off")
)

# Break classification returned by classify_fullback_break().
FB_BREAK_NONE: str = "NONE"
FB_BREAK_DEPTH: str = "DEPTH"
FB_BREAK_LATERAL: str = "LATERAL"

# ─────────────────────────────────────────────────────────────────────
# THE BREAK LATCH (2026-09-28)
# ─────────────────────────────────────────────────────────────────────
# THE DEFECT THIS REMOVES ("the anchor relock silently un-breaks a live
# chase").  ``classify_fullback_break()`` reads the defensive line anchor,
# and the anchor IS ``ruck_x_m + 10``.  During a tackle the engine
# re-locks ``ruck_x_m`` onto the CARRIER:
#
#   * CONTACT -> DRIVE entry  (``_update_tackle_contact`` tail: "anchor the
#     drive-time defensive line to the CURRENT tackle point, not the stale
#     pre-tackle ruck"), which also drops the anchor lock so the first DRIVE
#     frame re-locks ``ruck_x + 10``;
#   * the DRIVE-END lock (``_update_tackle_drive``) and the progressive-drag
#     ruck exit (``_exit_progressive_drag_to_ruck``);
#   * DOMINANCE -> RELEASE (``_prepare_release``).
#
# A FORWARD relock moves the line anchor GOAL-SIDE of a live carrier, so a
# carrier that was a genuine DEPTH break one frame earlier becomes "behind
# an intact line" the next: the DEPTH leg falls through, the LINE-INTACT
# LEVEL exclusion then returns FB_BREAK_NONE, and ``apply_fullback_geometry``
# writes the sweeper anchor + DRIFT under the SAME writer with NO engine
# event — the ``MODE_OVERWRITE`` handover the authority-timeline probe
# reports, with nothing clearing the emergency engagement.
#
# MEASURED (tmp_fb_authority_timeline.txt, seed 20260827, window 16):
#   f=382  mode=CHASE  brk=DEPTH  bc_x=26.52  line=18.25  ruck=8.25
#   f=383  mode=DRIFT  brk=NONE   bc_x=26.52  line=36.52  ruck=26.52
# The CARRIER never moved.  Only the anchor did (+18.27 m), and that alone
# withdrew the classification: depth margin +7.27 m -> -11.00 m, and the
# LEVEL exclusion engaged (bc.x 26.52 < line 36.52 - 3.0).
#
# THE RULE: once a genuine break has been classified for the fullback, a
# FORWARD move of the line anchor may not retroactively un-classify it.
# The break TEST therefore reads the MINIMUM anchor seen over the live
# chase (``break_test_line_anchor_x``), while the sweeper's own DEPTH
# geometry keeps reading the LIVE anchor — the depth role must follow the
# real ruck; only the break VERDICT is latched.
#
# The latch is TACKLE-CYCLE state.  It is released ONLY at an explicit
# cycle end (``clear_fullback_chase_state``, called from the RELEASE-entry
# teardown and the miss teardown) or when the actor stops being the
# fullback; and it SELF-RELEASES the moment the carrier genuinely drops
# behind the LATCHED line, so a carried pass back / a carrier running
# backwards still ends the chase.
#
# A-B / rollback lever: RLM_FB_BREAK_LATCH=0 restores the live-anchor
# classification byte-for-byte with no code revert (the same pattern as
# every other runtime lever in this module).
FB_BREAK_LATCH_ENV_VAR: str = "RLM_FB_BREAK_LATCH"
FB_BREAK_LATCH: bool = (
    os.environ.get(FB_BREAK_LATCH_ENV_VAR, "1").strip().lower()
    not in ("0", "false", "no", "off")
)

# Per-actor latch state.  The ANCHOR is the MINIMUM seen over the chase,
# so repeated forward relocks cannot creep it goal-side.
FB_BREAK_LATCH_ATTR: str = "_fb_break_latched"
FB_BREAK_LATCH_ANCHOR_ATTR: str = "_fb_break_latched_anchor_x"
FB_BREAK_LATCH_KIND_ATTR: str = "_fb_break_latched_kind"

# A forward move of the line anchor smaller than this is drift, not a
# relock, and must not stamp an anchor-relock handover.
FB_ANCHOR_RELOCK_EPS_M: float = 0.5

# The observable stamp written when an anchor relock (rather than an
# explicit exit) ends the chase — the audit's proof the transition was
# made explicit instead of silent.
FB_RELOCK_HANDOVER_ATTR: str = "_fb_relock_handover"

# ─────────────────────────────────────────────────────────────────────
# DEFENSIVE-LINE OCCUPANCY (2026-09-29)
# ─────────────────────────────────────────────────────────────────────
# THE DEFECT THIS REMOVES ("the fullback runs in on a kick return with no
# line break").  On a kickoff reception ``catch_system._perform_catch`` seeds
#
#     world.ruck_x_m = bc.x      ->  anchor = bc.x + 10, LOCKED
#
# so the "defensive line" is manufactured 10 m in front of the catch.  The
# kicking team has NOT formed a line — it is still goal-side of that anchor —
# and the carrier simply runs ~11 m, trips
#
#     bc.x > anchor + FB_LINE_BREAK_MARGIN_M
#
# and is classified FB_BREAK_DEPTH.  The fullback is then granted emergency
# tackle candidacy, goes CHASE at the governed ceiling and runs the carrier
# down.
#
# MEASURED (probe_fb_kick_return_admission.py, seed 20260827): the break first
# fires at f=309 with the carrier 11.03 m past the reception, and on that
# frame ALL TWELVE other defenders are still AHEAD of the anchor — not one
# body is behind it and not one is on it.  Six of eight swept seeds reproduce
# the admission, every one of them through the DEPTH leg; the LATERAL leg
# never fired in any seed.
#
# THE LAW: a line the defence has not reached cannot be broken.  A break
# verdict therefore requires the anchor to be a line the defence actually
# HOLDS — at least one other defender at or behind it.  Measured on the same
# rollout this separates the two cases exactly: the bogus break at f=309 has
# 0 defenders behind the anchor, while a genuine break at f=780 (the carrier
# has carried the line) has 8.
#
# Scope: the test reads ``world.defending_team``.  A world carrying no
# readable population (every verifier stub, the detached geometry harness)
# keeps the historical contract unchanged, so this can only act live.
#
# A-B / rollback lever: RLM_FB_LINE_OCCUPIED=0 restores the historical
# unoccupied-anchor classification with no code revert (the same pattern as
# every other runtime lever in this module).
FB_LINE_OCCUPIED_ENV_VAR: str = "RLM_FB_LINE_OCCUPIED"
FB_LINE_OCCUPIED_REQUIRED: bool = (
    os.environ.get(FB_LINE_OCCUPIED_ENV_VAR, "1").strip().lower()
    not in ("0", "false", "no", "off")
)

# How far behind the anchor a body may sit and still count as holding it.
# A line defender is pinned to the anchor by the retreat contract, so a body
# goal-side by more than this band is a beaten / retreating body rather than
# a line holder.
FB_LINE_OCCUPIED_BAND_M: float = 2.0

# ─────────────────────────────────────────────────────────────────────
# KICKOFF FB OVERRIDE (2026-08-11)
# ─────────────────────────────────────────────────────────────────────
# Runtime feature flag.  When True (default) jersey 1 is the PERSISTENT
# fullback — KICKOFF and OPEN_PLAY — at (formation_line_x, centre +
# side_sign * 3.0) whenever jersey 1 is eligible and is not the kicker.
# Set to False — or set the environment variable RLM_KICKOFF_FB_OVERRIDE=0
# — to fall back to the deterministic deepest-central selection without
# reverting any code (rollback path).
KICKOFF_FB_OVERRIDE: bool = (
    os.environ.get("RLM_KICKOFF_FB_OVERRIDE", "1").strip().lower()
    not in ("0", "false", "no", "off")
)

# ─────────────────────────────────────────────────────────────────────
# B2 FULLBACK TACKLE-CANDIDACY OVERRIDE (2026-09-16)
# ─────────────────────────────────────────────────────────────────────
# Runtime feature flag.  When True (default) a tackle-capable fullback
# (``can_acquire_tackle``) is admitted to the lane / pre-tackle SELECTION
# pools so the last defender can actually make a tackle on a line break,
# while its geometry authority, sweeper anchor and jersey-1 priority are
# preserved.  Set to False — or set the environment variable
# RLM_FULLBACK_TACKLE_CANDIDACY=0 — to restore the historical
# "fullback never enters tackle selection" behaviour with no code revert
# (documented rollback / A-B lever).
FULLBACK_TACKLE_CANDIDACY: bool = (
    os.environ.get("RLM_FULLBACK_TACKLE_CANDIDACY", "1").strip().lower()
    not in ("0", "false", "no", "off")
)

# ─────────────────────────────────────────────────────────────────────
# B2 FULLBACK ROLE-PERSISTENCE OVERRIDE (2026-09-16)
# ─────────────────────────────────────────────────────────────────────
# Runtime feature flag.  When True (default) ``movement_state`` NEVER
# demotes the fullback role (the 2026-09-15 CHASE-STATE DEMOTION stays
# deleted).  Set to False — or set RLM_FULLBACK_ROLE_PERSISTENCE=0 — to
# restore that demotion verbatim for A-B diagnosis / rollback.
FULLBACK_ROLE_PERSISTENCE: bool = (
    os.environ.get("RLM_FULLBACK_ROLE_PERSISTENCE", "1").strip().lower()
    not in ("0", "false", "no", "off")
)

# Designated kickoff fullback jersey.  The FB anchor sits ON the flat
# formation line during KICKOFF, so the normal distance gate must NOT run
# for this preference.  The jersey identity comes from KickoffEngine.create_teams()
# stamping ``player.jersey_number = i + 1`` (jersey 1 = first defender).
KICKOFF_FB_JERSEY: int = 1

# Designated kicker jersey (mirrors kickoff_formations.KICKER_JERSEY_NUMBER
# without a circular import).  When jersey 1 IS the kicker the FB override
# cannot apply — the kicker owns the centre slot — so the selection falls
# back to the deepest central defender.
KICKER_JERSEY_NUMBER: int = 13

# KICKOFF FB LATERAL OFFSET: the FB stands 3 m from field centre toward
# the kick side.
KICKOFF_FB_LATERAL_OFFSET_M: float = 3.0

# FULLBACK REPLACEMENT CENTRAL BAND (2026-08-11): the last-defender role
# is reserved for defenders within ±FULLBACK_CENTRAL_BAND_M of field
# centre (y ∈ [20, 50] on the 70 m field — the recommended 30 m central
# corridor).  Defenders hugging the sidelines (wingers on the touchline)
# never count as fullback candidates when the designated FB (jersey 1) is
# unavailable (tackler / marker / frozen).
FULLBACK_CENTRAL_BAND_M: float = 15.0

# Deterministic selection tie-break (2026-08-11): role priority for
# depth-role groups of EQUAL depth — WINGER outranks interior line roles;
# lower value = higher priority.  Only consulted after depth and lateral
# distance so normal play keeps the documented WINGER → CENTRE → nearest
# hierarchy intact.
_ROLE_PRIORITY = {
    "WINGER": 0,
    "A": 1,
    "B": 1,
    "C": 1,
    "D": 1,
    "CENTRE": 1,
}

# ─────────────────────────────────────────────────────────────────────
# FULLBACK ABSORB HYSTERESIS + APPROACH TUNERS (Task J, 2026-08-12)
# ─────────────────────────────────────────────────────────────────────
# Absorption into the defensive line requires BOTH |x - defensive_line_anchor|
# and |y - ball-carrier lane| proximity for ABSORB_FRAMES_MIN consecutive
# frames.  Once absorbed, the actor stays a line defender until proximity
# breaks (de-absorb hysteresis), so the jersey-1 priority cannot re-select
# and flap every frame.
ABSORB_FRAMES_MIN: int = 6
ABSORB_X_THRESHOLD_M: float = 1.5
ABSORB_Y_THRESHOLD_M: float = 1.0

# ── POST-CATCH ABSORPTION GRACE WINDOW (2026-08-28) ──
# Frame count the absorption dissolve is SUPPRESSED for after a kickoff
# reception.  The kickoff fullback is hard-placed ON the flat formation
# line (x = formation_line_x ≈ 62); after the catch the post-catch line
# anchor locks to bc.x + 10, which lands within ABSORB_X_THRESHOLD_M of the
# FB's live position when the BC is caught near midfield.  Left un-suppressed,
# update_fullback_absorb_state() sees the FB "collapsed into the line" and
# clears fullback_role_active within ABSORB_FRAMES_MIN — the live-kickoff tag
# loss.  This window gives the FB ~1.0 s to glide to the ball-derived deep
# anchor (bc.x + 10 + depth) and clear the X threshold (≈45 frames at the
# 2.0 m/s approach speed, so 60 frames is a safe ceiling).
FB_POST_CATCH_ABSORB_GRACE_FRAMES: int = 60

# Approach-speed cap: the fullback's scalar speed toward its anchor never
# exceeds FB_MAX_APPROACH_SPEED_MPS and decelerates linearly inside
# FB_DECEL_START_M (floor 10% so it always creeps home, never stalls).
FB_MAX_APPROACH_SPEED_MPS: float = 2.0
FB_DECEL_START_M: float = 6.0

# Arrival: the actor counts as "reached" its backfield slot when within
# this distance of the anchor (audit visibility vs still gliding).
FB_ARRIVED_PROXIMITY_M: float = 0.5

# ── STAGE 3E: FULLBACK ANCHOR TIMING HYSTERESIS (2026-08-25) ──
# Sub-threshold band below which a BETWEEN-FRAMES anchor drift keeps the
# previous committed anchor.  Kills centimetre-level jitter of the deep
# backfield target while the BC / ruck references move slowly.  Genuine
# snaps (kickoff-catch re-anchor, line jump) exceed the band and pass
# through unchanged.
FB_ANCHOR_HYSTERESIS_M: float = 0.15

# ─────────────────────────────────────────────────────────────────────
# STATIC TARGET-AFTER-LINE-BREAK PURSUIT CLEARANCE (frame-1814, 2026-09-23)
# ─────────────────────────────────────────────────────────────────────
# THE DEFECT THIS REMOVES ("the X-axis deadlock"): frame 1814 of the live
# rollout shows the tagged fullback held static at x = 63.80 while the
# attacking carrier breaks the line and streams 106.35 -> 111.49 over a
# 40-frame window.  No forward velocity is produced and the last defender
# never enters a pursuit phase.
#
# The sweeper anchor is ``defensive_line_anchor_m(world) + depth``.  The
# line anchor (``ruck_x + 10``) is LOCKED for the ruck and ``depth``
# saturates at DEPTH_MAX_M = 30 once the carrier passes halfway, so the
# anchor is a per-ruck CONSTANT — 63.80 with a locked anchor of 33.80.
# Two paths then leave that constant standing as the command:
#
#   1. The EMERGENCY-DEFENDER YIELD in ``apply_fullback_geometry`` returns
#      without assigning ``target_x`` at all, so the last-written anchor
#      survives every frame.  The dispatch's LAST X recovery write
#      (``line_break_static_guard``) exists precisely to clear a static
#      post-break target, but it hard-exempts the tagged fullback — so
#      nothing else in the frame ever recovers it.
#   2. The non-DEPTH (LATERAL) branch assigned ``target_x = anchor_x``
#      unconditionally.
#
# A static pin is a *measurement* of where the line used to be; it is never
# a command to hold station while the carrier runs clear.  On a genuine
# line break the target assignment loop therefore clears a static pin by
# assigning the projected interception point, exactly as the depth branch
# does.
#
# A pin is recognised by the same two signals ``line_break_static_guard``
# documents: the target sitting on the sweeper anchor, or on the body's own
# X.  A live chase target (already at the pursuit point) and an out-of-band
# value are NOT pins and stay untouched, so the emergency path still yields
# target ownership to the commit / corridor writers.
FB_STATIC_TARGET_ANCHOR_EPS_M: float = 0.1   # pinned to the sweeper anchor
FB_STATIC_TARGET_BODY_EPS_M: float = 0.05    # pinned to the body's own X

# Command envelope: ``apply_fullback_geometry`` clamps every command it
# writes to the legal field strip (LEFT_TRYLINE_X .. FIELD_MAX_X), so only a
# value inside that strip can be a fullback command.  A value outside it
# belongs to another writer (or is a sentinel) and the clearance must not
# claim it — that is what keeps the emergency path's yield intact for the
# commit / corridor owners.
FB_COMMAND_ENVELOPE_EPS_M: float = 0.001

# Pursuit-tracker snap tolerance: once the clearance owns the command it
# re-commands the projected interception point whenever the current target lags
# it by more than this, so the pursuit rides the carrier continuously
# instead of lurching in one-shot jumps.
FB_PURSUIT_SNAP_TOLERANCE_M: float = 0.1

# A-B / rollback lever: RLM_FB_STATIC_PURSUIT_CLEAR=0 restores the
# historical freeze (a static pin is left standing) with no code revert,
# same pattern as the other runtime levers in this module.
FB_STATIC_PURSUIT_CLEAR: bool = (
    os.environ.get("RLM_FB_STATIC_PURSUIT_CLEAR", "1").strip().lower()
    not in ("0", "false", "no", "off")
)

# ─────────────────────────────────────────────────────────────────────
# MISS-TEARDOWN ENGAGEMENT RELEASE (2026-09-26)
# ─────────────────────────────────────────────────────────────────────
# THE DEFECT THIS REMOVES ("the beaten fullback never resumes its chase").
# ``_arm_tackler_role`` sets ``_fb_emergency_engaged`` on the tagged fullback
# and stamps ``defender_role = TACKLER_1``.  The ONLY site that ever cleared
# that pair is ``reset_fullback_emergency_engagement``, called from the
# RELEASE-entry loop inside ``OpenPlayEngine._clear_tes_artefacts``.
#
# A BROKEN TACKLE NEVER ENTERS RELEASE.  ``_handle_broken_tackle`` ends with
# ``self._set_phase("OPEN_PLAY", ..., "BROKEN_TACKLE")`` and its two
# normalisation sweeps BOTH ``continue`` on a tagged fullback (the actor is
# deliberately excluded from the line-role and chase-reset writers).  So a
# fullback the tackle armed kept the engagement marker and the TACKLER role
# for the REST OF THE MATCH, and that leaked pair is what the geometry
# authority yields to:
#
#     fullback_tackle_authority_active(fb) is True   (the marker)
#     fullback_tackle_side_owner_live(fb)  is True   (the same marker)
#       -> establish_fullback_live_break_chase() declines
#       -> apply_fullback_geometry() falls to a bare ``return``
#       -> NO target, NO state and NO speed write
#
# and because ``fullback_approach_cap_applies`` is the exact complement of
# the authority flag, the governed ceiling is never restored either: the
# body coasts on whatever ``move_speed`` it last inherited.  Measured by
# ``tmp_fb_engagement_leak_probe.py``: both stamps survive the teardown, the
# live-break chase establishment declines on the leaked body, and clearing
# them restores the chase AND the 8.3 m/s governed ceiling.
#
# The engagement is TACKLE-CYCLE state, so the miss teardown -- which ends
# the cycle just as RELEASE does -- must release it.  Default ON; set
# ``RLM_FB_EMERGENCY_RELEASE_ON_MISS=0`` to restore the pre-change engine
# (the marker survives the teardown) with no code revert.
FB_EMERGENCY_RELEASE_ON_MISS_ENV_VAR: str = "RLM_FB_EMERGENCY_RELEASE_ON_MISS"

# ─────────────────────────────────────────────────────────────────────
# LIVE-BREAK CHASE SPEED (2026-09-26)
# ─────────────────────────────────────────────────────────────────────
# THE DEFECT THIS REMOVES ("the beaten fullback makes a delayed, throttled
# pursuit").  The breakaway CHASE override writes its speed as
#
#     fullback_approach_speed(base, dist) if cap_applies(d) else base
#
# where ``cap_applies`` is ``fullback_approach_cap_applies(d)`` -- the exact
# COMPLEMENT of ``fullback_tackle_authority_active(d)``.  But the CHASE
# branch is only reachable AT ALL when the authority is INACTIVE: an
# authority-active body is diverted into the emergency yield above and
# returns before the anchor branch is ever reached.  ``cap_applies`` is
# therefore ALWAYS True at this call site and the ``else: base`` arm is
# DEAD CODE -- the documented contract of the branch ("an authority-granted
# fullback chases at the unified defensive speed like any other defender")
# has never actually executed.
#
# The real consequence: every live-break chase -- and specifically the chase
# a beaten fullback must run once the miss teardown releases it -- is
# computed through ``fullback_approach_speed``, whose FIRST LINE is
# ``min(base, FB_MAX_APPROACH_SPEED_MPS)``.  The last defender pursued the
# breaking carrier at a hard 2.0 m/s, further tapered by the ARRIVAL ramp
# inside FB_DECEL_START_M.  Measured on the fixture at a 3.0 m error:
# 2.0 * (3.0 / 6.0) = 1.0 m/s, against an 8.3 m/s ceiling.
#
# ``FB_MAX_APPROACH_SPEED_MPS`` is the SWEEPER ANCHOR cap: it bounds the
# disengaged glide home to the backfield slot so the last defender never
# bursts while it is merely measuring its own depth.  It must not bound a
# chase onto a live carrier, which is a different contract -- the same
# reasoning that already exempts MovementState.RECOVER from it.
#
# The chase therefore takes its own speed contract: the unified defensive
# ceiling, with the SAME FB_DECEL_START_M arrival taper (so the body settles
# onto the pursuit point instead of overshooting the carrier) and the 2.0 m/s
# sweeper cap NOT applied.  Default ON; set ``RLM_FB_CHASE_SPEED=0`` to
# restore the historical ``cap_applies`` expression verbatim -- which, as
# derived above, is the 2.0-clamped taper in every reachable case -- with no
# code revert.
FB_CHASE_SPEED_ENV_VAR: str = "RLM_FB_CHASE_SPEED"

# Chase pace as a fraction of the unified defensive ceiling.  1.0 = the same
# governed speed as any other committed defender (the branch's documented
# contract).  A named constant so a future tuner can slow the chase without
# ever reintroducing the sweeper cap.
FB_CHASE_SPEED_SCALE: float = 1.0

# ─────────────────────────────────────────────────────────────────────
# FULLBACK RECOVERY LOCOMOTION (2026-09-21)
# ─────────────────────────────────────────────────────────────────────
# The fullback's backfield slot is DERIVED (depth lerps with field position
# and its lateral line tracks the ball), so a breakaway / ruck / turnover
# can leave the actor owing a large amount of ground behind its own anchor.
# The DRIFT contract then measures it home at the 2.0 m/s approach cap on
# the critically-damped integration path -- the "fullback structural
# immobility" symptom: the last defender is never actually out of position,
# it just cannot physically get back to the slot it owns.
#
# MovementState.RECOVER is the fast-path answer: the actor travels home on
# the acceleration-lerp path with the target stabiliser bypassed (see
# player/movement_states.py, player/target_stabiliser.py and
# Player._uses_smooth_damp), at the ALIGNED recovery pace documented under
# FB_RECOVER_SPEED_SCALE below.  It is armed
# ONLY when all three hold:
#   1. the owning engine has armed the lever (``fullback_recover_allowed``
#      on the world, stamped by DefenceManager.update_open_play),
#   2. the actor genuinely owes ground (debt > FB_RECOVER_DEBT_M),
#   3. the actor is NOT playing as a defender (tackle authority / TES /
#      kickoff flight all keep their own contracts).
# Arming is engine-owned on purpose: the geometry authority's historical
# contract is DRIFT, and stub / verifier worlds that never arm the lever
# keep that contract byte-for-byte.
FB_RECOVER_DEBT_M: float = 6.0        # owed-ground distance that ARMS RECOVER
FB_RECOVER_EXIT_DEBT_M: float = 2.0   # release band (hysteresis around the arm)

# RECOVER PACE ALIGNMENT (2026-09-22).  The first cut of the recovery
# locomotion ran the owed-ground travel at the FULL unified defensive ceiling
# (8.3 m/s) -- the pace of a committed tackler or a chasing carrier, roughly
# double the 4.75 m/s a normal line defender uses to move to its slot.  In
# play it read as "the fullback is far too fast".  RECOVER now travels at the
# SAME fraction of the ceiling that defensive_role_movement._capped_speed(0.5)
# gives DEF_LINE_READY, which is the ordinary slot-repositioning pace.  What
# RECOVER still keeps is the DAMPENER BYPASS (no stabiliser deadzone /
# hysteresis / alpha lag, no critically-damped glide) and the lifted 2.0 m/s
# sweeper cap -- not an elevated pace.  Kept equal to
# movement_states.MAX_SPEED_BY_STATE[RECOVER] on purpose.
FB_RECOVER_SPEED_SCALE: float = 0.5

# Roles / states that make a player UNAVAILABLE to be the fullback.
# Mirrors the dummy-half availability rules from the engine:
#   tackled, attached (TES), marker, dummy half, frozen, arrival mode,
#   already a TES tackler, or retreat-role (shared with closer line work).
UNAVAILABLE_DEFENDER_ROLES = frozenset({
    DefenderRole.MARKER_A,
    DefenderRole.MARKER_B,
    DefenderRole.DUMMY_HALF,
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
})

# Roles owned by ANOTHER authority, which the fullback emergency teardown
# must never overwrite (2026-09-30).
#
# ``reset_fullback_emergency_engagement`` runs in the SAME
# DOMINANCE -> RELEASE handover as marker assignment, so it may only hand
# back the roles its OWN contract armed the body into (the TACKLER_* roles
# ``_arm_tackler_role`` stamped).  It used to write ``DEF_LINE_READY``
# unconditionally, which DELETED a MARKER_A/B stamp the marker authority had
# issued a frame earlier; and because markers are assigned exactly once per
# tackle cycle, nothing ever re-filled the pair -- the ruck-adjacent pin
# silently shipped a body short and the actor was re-anchored onto its deep
# sweeper anchor.  The dummy half is excluded for the same reason: it is a
# separate role contract with its own owner.
#
# Declared as NAMES rather than DefenderRole members on purpose: the guard
# reads it through ``getattr(role, "name", role)``, so a body carrying the
# string form (audit stubs, the detached geometry harness) is matched too.
_ROLES_OWNED_BY_OTHER_AUTHORITIES: tuple = (
    "MARKER_A",
    "MARKER_B",
    "DUMMY_HALF",
)


# ─────────────────────────────────────────────────────────────────────
# FULLBACK EMERGENCY DEFENDER — THE CONTROLLED LINE-BREAK PATH (B2b)
# ─────────────────────────────────────────────────────────────────────
# DESIGN LAW (2026-09-16)
# ----------------------
# TACKLE ELIGIBILITY and LINE MEMBERSHIP are TWO SEPARATE CONCEPTS and
# must never be conflated:
#
#   1. LINE MEMBERSHIP is owned SOLELY by the ``fullback_role_active`` tag.
#      Every line-shape consumer reads that tag --
#      ``population_authority.is_line_body`` (line population),
#      ``defensive_depth_roles`` (depth roles),
#      ``publish_line_population`` (side / count ownership),
#      ``spacing_authority.is_spacing_eligible`` (spacing),
#      ``width_authority`` / ``formation_width_authority`` /
#      ``defensive_line_governor`` (width + span),
#      ``defensive_pipeline._line_defenders`` and
#      ``defence_manager.run_frame_preamble`` (formation assignment).
#      A tagged defender is therefore NEVER a line member -- INCLUDING
#      while ``defender_role == TACKLER_1/2/3`` during an emergency
#      tackle.  No consumer may be narrowed to read ``defender_role``
#      instead of the tag.
#
#   2. TACKLE ELIGIBILITY is owned by ``can_acquire_tackle`` PLUS the
#      genuine line-break gate below (``fullback_emergency_defender_active``).
#      It NEVER consults line membership.
#
# THE EMERGENCY PATH
# ------------------
# A ``fullback_role_active`` defender stays a pure deep sweeper in normal
# play.  When -- and ONLY when -- a GENUINE LINE BREAK exists
# (``bc.x > defensive_anchor_x + FB_LINE_BREAK_MARGIN_M``, the canonical
# ``movement_mode_decision.carrier_past_defensive_line`` predicate) the
# last defender is admitted to the SAME selection path as any other
# defender: lane-tackler candidacy, the pre-tackle COMMIT pool, the
# pre-tackle corridor, and finally TES arming.
#
# THE TAG IS NEVER LOST
# ---------------------
# Entering that path does NOT revoke ``fullback_role_active``.  Arming the
# actor as a TES tackler sets ``_fb_emergency_engaged`` and keeps the tag,
# so the actor still fails every line-membership test (it is never
# absorbed into the defensive line, never takes a depth role / spacing
# slot / width cell / lane index / formation slot) while behaving as a
# normal committed defender.  At the end of the tackle cycle the engine
# clears ``_fb_emergency_engaged`` and restores ``defender_role =
# DEF_LINE_READY`` -- the tag was never removed, so the actor resumes its
# sweeper anchor.

# A break is "genuine" only once the carrier is clearly past the anchor
# (the same 1.0 m margin ``carrier_past_defensive_line`` defaults to).
FB_LINE_BREAK_MARGIN_M: float = 1.0

# Per-actor marker: set the moment the emergency fullback is ACTUALLY
# armed as a TES tackler (``OpenPlayEngine._arm_tackler_role``), cleared
# at the end of the tackle cycle.  While set, the tag must survive every
# dissolve site (absorption, ``_clear_fullback_role_if_selected``, the
# single-FB clear loop) and the invariant audit admits TACKLER_* roles.
FB_EMERGENCY_ENGAGED_ATTR: str = "_fb_emergency_engaged"
FB_EMERGENCY_ENGAGE_AUTHORITY_ATTR: str = "_fb_emergency_engage_authority"

# ─────────────────────────────────────────────────────────────────────
# THE ADMISSION LEDGER (2026-09-30)
# ─────────────────────────────────────────────────────────────────────
# ``emergency_chase_admission_audit`` records WHY the emergency-chase gate
# refused: which conjunct failed, the defensive line's width, the carrier's
# depth gap to the line, and the nearest defender's distance to the carrier.
# Before this, a refusal was a bare ``False`` and every diagnosis had to be
# re-derived afterwards by a bespoke probe.
#
# IT NEVER DECIDES.  Every verdict below is taken by the predicate the call
# sits in, exactly as before; the ledger is handed the ANSWER and the label
# for it.  It writes no body field and no engine field, so a frame cannot
# change because the audit is armed.
#
# IT IS FREE WHEN OFF.  The only cost of a disarmed audit is one lazy import
# (a ``sys.modules`` hit after the first call) and one attribute read in
# ``admission_audit_armed``.  ``RLM_FB_ADMISSION_AUDIT=1`` or an installed
# ``world.emergency_chase_admission_audit`` ledger arms it.
#
# Deliberately FUNCTION-LOCAL: the audit module reaches back into this one for
# the break margin, the line anchor, the occupancy gate and the latch reader,
# so a module-scope import here would close the cycle.

def _admission_audit():
    """The emergency-chase admission ledger (imported lazily)."""
    from game.defence.defence_lvl_1 import emergency_chase_admission_audit
    return emergency_chase_admission_audit


def admission_audit_armed(world) -> bool:
    """True when the admission ledger is collecting for *world*.

    THE CHEAP GATE every logging site checks FIRST.  Read through the ledger's
    own predicate so "armed" has exactly one definition (the environment lever
    OR a ledger installed on the world).
    """
    return bool(_admission_audit().audit_armed(world))


def _log_admission_rejection(world, defender, bc, leg, verdict,
                             anchor_x=None) -> None:
    """File ONE refused admission / line-break evaluation (no-op when off).

    *leg* and *verdict* are the ledger's own vocabulary strings.  An unknown
    label is REPORTED and dropped rather than raised: a diagnostic must never
    be able to break a frame, and a silent drop would hide the typo -- so the
    warning is printed instead (it can only ever fire on a wiring bug, and
    only while the ledger is armed).
    """
    audit = _admission_audit()
    if not audit.audit_armed(world):
        return
    if not audit.is_known_verdict(verdict):
        print(f"[FB_ADMISSION] unknown verdict {verdict!r} ignored "
              f"(leg={leg})")
        return
    audit.record_rejection(
        world, defender, bc, leg, verdict, anchor_x=anchor_x,
    )


def _log_admission(world, defender, bc, leg, anchor_x=None) -> None:
    """File ONE ADMITTED verdict, naming the leg that admitted it.

    The ledger's denominator: without it a refusal count says nothing about how
    often the last defender is admitted at all.
    """
    audit = _admission_audit()
    if not audit.audit_armed(world):
        return
    audit.record_admission(world, defender, bc, leg, anchor_x=anchor_x)


def is_fullback_emergency_engaged(d) -> bool:
    """True while *d* is a tagged fullback inside an emergency tackle."""
    return bool(getattr(d, FB_EMERGENCY_ENGAGED_ATTR, False))


# ─────────────────────────────────────────────────────────────────────
# POST-TACKLE RETURN LIFECYCLE (2026-10-02)
# ─────────────────────────────────────────────────────────────────────
# THE DEFECT THIS REMOVES ("the flag never clears").  The post-tackle return
# window is ARMED at drive completion (``_prepare_release`` on
# DOMINANCE -> RELEASE and the RELEASE-entry teardown in
# ``_clear_tes_artefacts``).  Before this block the flag was NEVER released
# anywhere in the engine, so ONE completed fullback tackle left the body:
#
#   * permanently ruck-anchored -- ``compute_fullback_ruck_anchor`` stayed the
#     argument to ``Player.update`` on every subsequent frame, so the sweeper
#     never tracked the live ball carrier again;
#   * permanently exempt from the ``tes_active`` half of
#     ``fullback_recovery_active``; and
#   * permanently yielding in ``DefenceManager.reassert_fullback``, so the
#     RELEASE / PTB re-assert never ran again for that body.
#
# THE LIFECYCLE.  The return is a WINDOW, not a latch.  It ends at exactly the
# two doorways the spec names -- "until recovery completes or a higher-priority
# interruption takes ownership":
#
#   COMPLETION    the body reaches the RETURN TARGET it was commanded to
#                 (the ruck-relative depth anchor) within
#                 ``FB_POST_TACKLE_RETURN_RELEASE_M``.
#   INTERRUPTION  a higher-priority owner takes the body: a live line break
#                 (DEPTH or LATERAL), a fresh emergency engagement, TES
#                 attachment / contact, or a kickoff flight.
#
# CONSUMPTION-TIME, exactly like the break latch.  The predicate is consulted
# and the flag cleared by the two writers that own the body's frame in these
# eras -- ``apply_fullback_geometry`` (OPEN_PLAY) and
# ``DefenceManager.reassert_fullback`` (RELEASE / PTB) -- so no timer and no
# frame counter is needed.  A world that never arms the window keeps the
# historical contract byte-for-byte, and ``RLM_FB_POST_TACKLE_RETURN=0``
# restores the pre-change engine (the window is inert) with no code revert.
FB_POST_TACKLE_RETURN_ENV_VAR: str = "RLM_FB_POST_TACKLE_RETURN"
FB_POST_TACKLE_RETURN: bool = (
    os.environ.get(FB_POST_TACKLE_RETURN_ENV_VAR, "1").strip().lower()
    not in ("0", "false", "no", "off", "")
)

# The window flag, the assigned return target and the release reason.
FB_POST_TACKLE_RETURN_ATTR: str = "_fb_post_tackle_return_active"
FB_POST_TACKLE_RETURN_TARGET_ATTR: str = "_fb_post_tackle_return_target"
FB_POST_TACKLE_RETURN_SOURCE_ATTR: str = "_fb_post_tackle_return_source"

# Completion band: the body counts as "home" within this distance of the
# return target.  Deliberately the same order as FB_ARRIVED_PROXIMITY_M
# (0.5 m -- "reached its backfield slot") so the release and the audit's
# arrival reading agree about what "home" means.
FB_POST_TACKLE_RETURN_RELEASE_M: float = 0.5


def fullback_post_tackle_return_enabled() -> bool:
    """Whether the post-tackle return lifecycle is armed.  Default ON.

    Read through the module boolean so a verifier or a probe can A/B the
    lifecycle IN-PROCESS by rebinding ``fullback_role.FB_POST_TACKLE_RETURN``
    -- the same pattern every other runtime lever in this module uses.
    """
    return bool(FB_POST_TACKLE_RETURN)


def is_fullback_post_tackle_return_active(d) -> bool:
    """True while *d* carries an ARMED post-tackle return window (pure reader)."""
    if d is None:
        return False
    return bool(getattr(d, FB_POST_TACKLE_RETURN_ATTR, False))


def assigned_fullback_return_target(d):
    """The (x, y) return target recorded for *d*, or ``(None, None)``.

    Pure reader.  Diagnostic plus fallback: the LIVE completion test uses the
    point the geometry authority commands on the frame it runs (which is the
    same ruck-relative anchor), and this record is what an audit prints.
    """
    if d is None:
        return None, None
    value = getattr(d, FB_POST_TACKLE_RETURN_TARGET_ATTR, None)
    if not (isinstance(value, (tuple, list)) and len(value) == 2):
        return None, None
    try:
        tx = float(value[0])
        ty = float(value[1])
    except (TypeError, ValueError):
        return None, None
    if not (math.isfinite(tx) and math.isfinite(ty)):
        return None, None
    return tx, ty


def record_fullback_return_target(d, target_x, target_y) -> bool:
    """Record the ASSIGNED return target the first time the writer commands it.

    Idempotent per window: the recorded target is stable, so an audit can read
    the point the body was SENT to rather than a value that creeps with the
    live ruck.  Total: an unreadable point records nothing.
    """
    if d is None:
        return False
    if getattr(d, FB_POST_TACKLE_RETURN_TARGET_ATTR, None) is not None:
        return False
    try:
        tx = float(target_x)
        ty = float(target_y)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(tx) and math.isfinite(ty)):
        return False
    d.__dict__[FB_POST_TACKLE_RETURN_TARGET_ATTR] = (tx, ty)
    return True


def arm_fullback_post_tackle_return(d) -> bool:
    """Arm the post-tackle return window for an engaged fullback.

    Called at drive completion (DOMINANCE -> RELEASE and the RELEASE-entry
    teardown).  The ASSIGNED return target is recorded by the geometry
    authority on the first frame it commands the ruck-relative anchor
    (``record_fullback_return_target``), because at this moment the anchor has
    not been computed yet.

    Returns True when the window was (re)armed.
    """
    if d is None:
        return False
    # THE OFF SWITCH.  ``RLM_FB_POST_TACKLE_RETURN=0`` must restore the
    # pre-change engine exactly, which means the window is never armed in the
    # first place -- not merely left unconsulted by the readers.  Without this
    # check the gate is inert on the write side and a disabled build still
    # parks the released fullback on the ruck anchor.
    if not fullback_post_tackle_return_enabled():
        return False
    if not getattr(d, "fullback_role_active", False):
        return False
    if not is_fullback_emergency_engaged(d):
        return False
    d.__dict__[FB_POST_TACKLE_RETURN_ATTR] = True
    d.__dict__.pop(FB_POST_TACKLE_RETURN_TARGET_ATTR, None)
    d.__dict__[FB_POST_TACKLE_RETURN_SOURCE_ATTR] = "drive_completion"
    return True


def clear_fullback_post_tackle_return(d, reason: str = "") -> bool:
    """Release the post-tackle return window (idempotent, total).

    Returns True when an artefact was actually cleared.  *reason* is stamped
    for the audit; it never affects behaviour.
    """
    if d is None:
        return False
    cleared = False
    for attr in (
        FB_POST_TACKLE_RETURN_ATTR,
        FB_POST_TACKLE_RETURN_TARGET_ATTR,
    ):
        if hasattr(d, attr):
            try:
                delattr(d, attr)
            except AttributeError:
                pass
            cleared = True
    if reason:
        d.__dict__[FB_POST_TACKLE_RETURN_SOURCE_ATTR] = "released:" + str(reason)
    return cleared


def fullback_post_tackle_return_live(
    world, defender, anchor_x=None, anchor_y=None
) -> bool:
    """True while the post-tackle return window still owns *defender*.

    THE SINGLE LIFECYCLE PREDICATE.  True only when ALL hold:

      * the lever is armed and the body carries the tag,
      * the window is ARMED (``is_fullback_post_tackle_return_active``),
      * no higher-priority interruption owns the frame -- not a kickoff
        flight, not a fresh emergency engagement, not TES-attached / in
        contact, and not a live line break of EITHER kind (the SAME
        two-branch test the emergency-candidacy gate reads), and
      * the body has NOT yet reached the return target within
        ``FB_POST_TACKLE_RETURN_RELEASE_M``.  The point compared against is
        the one passed by the caller (the anchor the geometry authority is
        about to command); with no point supplied the recorded assigned
        target is used, and with neither the debt is unmeasurable and the
        window HOLDS (the conservative answer -- it is released by the
        writers, not by a missing measurement).

    Pure / total: never raises, never mutates.
    """
    if not fullback_post_tackle_return_enabled():
        return False
    if defender is None:
        return False
    if not getattr(defender, "fullback_role_active", False):
        return False
    if not is_fullback_post_tackle_return_active(defender):
        return False
    if getattr(world, "in_kickoff", False):
        return False
    if is_fullback_emergency_engaged(defender):
        return False
    if getattr(defender, "tes_attached", False):
        return False
    if getattr(defender, "in_contact", False):
        return False
    try:
        if (
            fullback_depth_break_active(world, defender)
            or fullback_lateral_break_active(world, defender)
        ):
            return False
    except Exception:                                    # noqa: BLE001
        pass
    if anchor_x is None or anchor_y is None:
        anchor_x, anchor_y = assigned_fullback_return_target(defender)
    if anchor_x is None or anchor_y is None:
        return True
    try:
        error = math.hypot(
            float(anchor_x) - float(getattr(defender, "x", 0.0) or 0.0),
            float(anchor_y) - float(getattr(defender, "y", 0.0) or 0.0),
        )
    except (TypeError, ValueError):
        return True
    if not math.isfinite(error):
        return True
    return error > FB_POST_TACKLE_RETURN_RELEASE_M


def fullback_post_tackle_return_release_reason(
    defender, anchor_x=None, anchor_y=None
) -> str:
    """WHY the window is closing: ``"completion"`` or ``"interruption"``.

    Diagnostic label only -- the caller has already taken the verdict.  Names
    the completion doorway when the body is inside the release band, the
    interruption doorway otherwise.
    """
    if anchor_x is None or anchor_y is None:
        anchor_x, anchor_y = assigned_fullback_return_target(defender)
    if anchor_x is None or anchor_y is None:
        return "interruption"
    try:
        error = math.hypot(
            float(anchor_x) - float(getattr(defender, "x", 0.0) or 0.0),
            float(anchor_y) - float(getattr(defender, "y", 0.0) or 0.0),
        )
    except (TypeError, ValueError):
        return "interruption"
    if math.isfinite(error) and error <= FB_POST_TACKLE_RETURN_RELEASE_M:
        return "completion"
    return "interruption"


def clear_fullback_post_tackle_return_for(world) -> list:
    """Release every tagged fullback's post-tackle return window.

    THE EXPLICIT SEQUENCE-END SWEEP (kickoff reset).  Iterates the WHOLE
    defending team -- the window is per-body, not per-slot -- and returns the
    bodies actually released (the wiring proof an audit can assert).
    """
    if world is None:
        return []
    released = []
    for d in (getattr(world, "defending_team", None) or ()):
        if d is None or not getattr(d, "fullback_role_active", False):
            continue
        if clear_fullback_post_tackle_return(d, reason="team_sweep"):
            released.append(d)
    return released




def set_fullback_emergency_engaged(d) -> None:
    """Mark a tagged fullback as engaged in the emergency tackle path.

    A FRESH ENGAGEMENT IS AN INTERRUPTION: the body is being handed to the
    tackle cycle, which owns it until the end-of-cycle reset, so any live
    post-tackle return window is released here rather than surviving across
    the new tackle and mis-anchoring the actor on the frame the cycle ends.
    """
    if d is None:
        return
    clear_fullback_post_tackle_return(d, reason="emergency_engagement")
    setattr(d, FB_EMERGENCY_ENGAGED_ATTR, True)


def clear_fullback_emergency_engaged(d) -> None:
    """Drop the emergency-engagement marker (end of tackle cycle / reset)."""
    if d is None:
        return
    clear_fullback_emergency_engage_authority(d)
    try:
        delattr(d, FB_EMERGENCY_ENGAGED_ATTR)
    except AttributeError:
        pass


def fullback_emergency_release_on_miss_enabled() -> bool:
    """Whether the miss teardown releases the emergency engagement.

    Default ON.  ``RLM_FB_EMERGENCY_RELEASE_ON_MISS=0`` restores the
    pre-change engine -- the marker and the TACKLER role survive a broken
    tackle and the geometry authority yields for the rest of the match --
    with no code revert (the same pattern as every other runtime lever in
    this module).

    Read at CONSUMPTION time (the miss teardown), not at import time, so a
    rollout can be A/B'd from one process.
    """
    return os.environ.get(
        FB_EMERGENCY_RELEASE_ON_MISS_ENV_VAR, "1"
    ).strip().lower() not in ("0", "false", "no", "off", "")


def release_fullback_emergency_on_miss(world) -> list:
    """Release the emergency engagement of every tagged fullback.

    Called by the miss teardown (``OpenPlayEngine._handle_broken_tackle``),
    which ends the tackle cycle exactly as RELEASE entry does -- but WITHOUT
    entering RELEASE, so ``_clear_tes_artefacts``'s release loop never runs.
    A tagged fullback the tackle armed therefore kept ``_fb_emergency_engaged``
    and ``defender_role = TACKLER_1`` indefinitely, which is what made the
    geometry authority yield forever and the live-break chase establishment
    decline (see the MISS-TEARDOWN ENGAGEMENT RELEASE note above).

    Iterates the WHOLE defending team, not just the bodies the teardown
    wrapped: the engagement is TACKLE-CYCLE state, so a tagged fullback that
    joined the pile as TACKLER_2/3 must be released by the same teardown that
    releases the primary.

    Returns the list of bodies actually reset (the wiring proof: an empty
    list across a rollout means no fullback was engaged, which is correct --
    an empty list on a frame whose teardown beat an ARMED fullback is the
    defect this exists to remove).
    """
    if not fullback_emergency_release_on_miss_enabled():
        return []
    if world is None:
        return []
    released = []
    for d in (getattr(world, "defending_team", None) or ()):
        if d is None or not getattr(d, "fullback_role_active", False):
            continue
        if reset_fullback_emergency_engagement(d, clear_latch=False):
            released.append(d)
    return released


def reset_fullback_emergency_engagement(d, clear_latch: bool = True) -> bool:
    """Hand an engaged emergency fullback back to its sweeper contract.

    ``clear_latch=False`` (2026-10-01) hands the body's ROLE back WITHOUT
    ending EMERGENCY_CHASE.  The MISS teardown uses it: a broken tackle is
    NOT a completed drive, so the chase must persist across the miss and the
    latch may only be dropped by the cycle-end owners.  Default True keeps
    every other caller (and the break-latch contract) byte-identical.

    Called at the end of the tackle cycle (RELEASE entry).  Drops the
    engagement marker and restores the TES-owned body stamps to the line
    default: ``defender_role = DEF_LINE_READY`` (the one role that is a
    legal mover in every phase), no TACKLE-STATE hold, no tackler flags and
    no commit flag.

    THE FULLBACK TAG IS NEVER TOUCHED.  ``fullback_role_active`` was held
    for the whole tackle -- that is the entire point of the emergency path
    -- so the actor simply resumes its deep sweeper anchor on the next
    ``apply_fullback_geometry`` pass.  It is never absorbed into the line.

    Returns True when a body was actually reset.
    """
    if d is None or not is_fullback_emergency_engaged(d):
        # THE BREAK LATCH (2026-09-28): the engagement release and the latch
        # release are the SAME end-of-cycle event, so a latched-but-never-
        # engaged body is released here too rather than returned past.
        # ``clear_fullback_chase_state`` (the engine sweep) covers the whole
        # tagged population; this keeps the two packaged together for every
        # caller that resets a single body.
        #
        # ``clear_latch=False`` (2026-10-01): the MISS teardown hands the
        # body's ROLE back WITHOUT ending the chase, so the latch survives the
        # broken tackle and the last defender keeps pursuing.
        if d is not None and clear_latch:
            clear_fullback_break_latch(d)
        return False
    clear_fullback_emergency_engaged(d)
    if clear_latch:
        clear_fullback_break_latch(d)
    # ── ROLE HAND-BACK: ONLY THE ROLE THIS CONTRACT OWNS (2026-09-30) ──
    # This teardown's role contract is "hand the body back from the TES
    # TACKLER role it was armed into".  It is called from RELEASE entry
    # (``_clear_tes_artefacts``) and from the broken-tackle teardown, and
    # the marker pair is assigned at the DOMINANCE -> RELEASE boundary —
    # i.e. the assignment and this reset are adjacent last-writer passes in
    # the same handover.  Unconditionally forcing DEF_LINE_READY therefore
    # DELETED a MARKER_A/B stamp that the marker authority had just issued,
    # and because markers are assigned exactly once per cycle nothing ever
    # re-filled the pair: the ruck-adjacent pin silently shipped a body
    # short and the actor was re-anchored to the sweeper instead.
    #
    # The marker authority now excludes a tagged fullback from the marker
    # pool outright (``DefenceManager.assign_markers_for_new_tackle``), so
    # this guard is the belt-and-braces half: it makes the ownership
    # boundary explicit at the ONE reset site rather than relying on the
    # other writer's exclusion to hold.  Only the roles this contract owns
    # are handed back; a marker / DH role is left to the authority that
    # issued it.
    _role_before = getattr(d, "defender_role", None)
    _role_before_name = getattr(_role_before, "name", None)
    if _role_before_name is None and isinstance(_role_before, str):
        _role_before_name = _role_before
    if _role_before_name not in _ROLES_OWNED_BY_OTHER_AUTHORITIES:
        d.defender_role = DefenderRole.DEF_LINE_READY
    d.tackle_state_active = False
    d.is_tackler = False
    d.tes_tackler_role = None
    d._tackle_commit_active = False
    d.pre_tackle_stance = False
    return True


def line_break_active(world, bc=None) -> bool:
    """True when a GENUINE LINE BREAK exists this frame.

    The single definition of the emergency trigger, delegated to the
    canonical ``movement_mode_decision.carrier_past_defensive_line``
    (``bc.x > defensive_line_anchor_m(world) + margin``) so the fullback
    emergency path and the TASK VI chase override can never disagree about
    what a broken line is.

    Lazily imported: ``movement_mode_decision`` does not import this module,
    and resolving at call time keeps module init free of cycles.

    Returns False for a stub world (no anchor authority) or when there is
    no ball carrier.
    """
    if world is None:
        return False
    if bc is None:
        bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return False
    try:
        from game.defence.defence_lvl_1.movement_mode_decision import (
            carrier_past_defensive_line,
        )
    except Exception:
        return False
    return bool(carrier_past_defensive_line(world, bc, FB_LINE_BREAK_MARGIN_M))


def line_anchor_x(world, default: float = 60.0) -> float:
    """The defensive line anchor X, read through the single authority.

    Lazily delegated to ``anchor_authority.defensive_line_anchor_m`` so this
    module keeps its no-imports-at-module-scope rule.  Falls back to
    *default* for a stub world (no published / locked anchor), which is the
    same defensive fallback ``apply_fullback_geometry`` uses.
    """
    try:
        from game.defence.defence_lvl_1.anchor_authority import (
            defensive_line_anchor_m as _anchor_m,
        )
        value = _anchor_m(world)
    except Exception:
        value = None
    if value is None or float(value) <= 0.0:
        return float(default)
    return float(value)


# ─────────────────────────────────────────────────────────────────────
# THE BREAK LATCH — IMPLEMENTATION (2026-09-28)
# ─────────────────────────────────────────────────────────────────────

def fullback_break_latch_enabled() -> bool:
    """Whether the break latch is armed (the shipped default).

    Read through the module boolean so a verifier or a probe can A/B the
    two contracts IN-PROCESS by rebinding ``fullback_role.FB_BREAK_LATCH``
    -- the same pattern ``FB_CHASE_INTERCEPT`` documents.
    """
    return bool(FB_BREAK_LATCH)


def is_fullback_break_latched(defender) -> bool:
    """True while *defender* carries a latched break (pure reader)."""
    if defender is None:
        return False
    return bool(getattr(defender, FB_BREAK_LATCH_ATTR, False))


def latched_fullback_break_anchor_x(defender):
    """The latched anchor X, or None when unreadable (pure reader)."""
    if defender is None:
        return None
    value = getattr(defender, FB_BREAK_LATCH_ANCHOR_ATTR, None)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def arm_fullback_break_latch(defender, break_kind, anchor_x) -> bool:
    """Latch a classified break so a FORWARD anchor relock cannot un-break it.

    The latched anchor is the MINIMUM of the incoming anchor and any anchor
    already latched, so a sequence of forward relocks inside one chase can
    never walk the test anchor goal-side of the carrier.  Idempotent and
    total: a malformed anchor leaves an existing latch untouched rather
    than clearing it.

    Returns True when the latch was (re)armed with a usable anchor.
    """
    if defender is None:
        return False
    if not fullback_break_latch_enabled():
        return False
    try:
        value = float(anchor_x)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(value):
        return False
    current = latched_fullback_break_anchor_x(defender)
    if current is not None and current <= value:
        # Keep the more conservative (smaller) anchor already latched.
        value = current
    setattr(defender, FB_BREAK_LATCH_ATTR, True)
    setattr(defender, FB_BREAK_LATCH_ANCHOR_ATTR, value)
    if break_kind is not None:
        setattr(defender, FB_BREAK_LATCH_KIND_ATTR, break_kind)
    return True


def clear_fullback_break_latch(defender) -> bool:
    """Drop *defender*'s break latch + the anchor-relock stamp.

    Returns True when any latch artefact was actually cleared.
    """
    if defender is None:
        return False
    cleared = False
    for attr in (
        FB_BREAK_LATCH_ATTR,
        FB_BREAK_LATCH_ANCHOR_ATTR,
        FB_BREAK_LATCH_KIND_ATTR,
        FB_RELOCK_HANDOVER_ATTR,
    ):
        if hasattr(defender, attr):
            try:
                delattr(defender, attr)
            except AttributeError:
                pass
            cleared = True
    return cleared


def clear_fullback_chase_state(world) -> list:
    """Release the break latch of every tagged fullback (explicit cycle end).

    THE EXPLICIT TACKLE-CYCLE END.  Called from the RELEASE-entry teardown
    (``OpenPlayEngine._clear_tes_artefacts``) and the miss teardown
    (``OpenPlayEngine._handle_broken_tackle``), so a latched chase can
    never survive the cycle it was armed in.  Iterates the WHOLE defending
    team (the tag is per-body, not per-slot) and returns the bodies whose
    latch was dropped -- the wiring proof an audit can assert.
    """
    if world is None:
        return []
    released = []
    for d in (getattr(world, "defending_team", None) or ()):
        if d is None or not getattr(d, "fullback_role_active", False):
            continue
        if clear_fullback_break_latch(d):
            released.append(d)
    return released


def break_test_line_anchor_x(world, defender, anchor_x=None) -> float:
    """THE BREAK TEST'S line anchor: live, floored by any latched anchor.

    THE SINGLE READER every ``classify_fullback_break`` call site consumes,
    so the latch can never be honoured by one caller and ignored by
    another.  Contract:

      * no latch / lever off: the LIVE anchor (byte-identical behaviour),
      * latched: ``min(live, latched)`` -- a FORWARD relock cannot move the
        test anchor goal-side of the carrier, while a genuine RETREAT of
        the real anchor still tightens the test.

    ``anchor_x`` lets a caller that already holds the live value (e.g.
    ``apply_fullback_geometry``) avoid a second authority read.  Pure and
    total: never raises, never mutates.
    """
    if not fullback_break_latch_enabled():
        if anchor_x is not None:
            try:
                return float(anchor_x)
            except (TypeError, ValueError):
                pass
        return line_anchor_x(world)
    live = None
    if anchor_x is not None:
        try:
            live = float(anchor_x)
        except (TypeError, ValueError):
            live = None
    if live is None:
        live = line_anchor_x(world)
    latched = latched_fullback_break_anchor_x(defender)
    if latched is None:
        return float(live)
    return float(min(live, latched))


def carrier_behind_latched_line(defender, bc) -> bool:
    """True when the carrier has genuinely dropped behind the LATCHED line.

    THE LATCH'S SELF-RELEASE.  Judged against the LATCHED anchor -- never
    the live one, which is the whole point -- so a forward relock cannot
    reach it, while a carried pass back / a carrier running backwards can.
    Pure and total.
    """
    latched = latched_fullback_break_anchor_x(defender)
    if latched is None or bc is None:
        return False
    try:
        bc_x = float(bc.x)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(bc_x):
        return False
    return bc_x < latched + FB_LINE_BREAK_MARGIN_M


# ─────────────────────────────────────────────────────────────────────
# THE LATCH-BACKED CHASE HOLD (2026-10-01)
# ─────────────────────────────────────────────────────────────────────
# THE DEFECT THIS REMOVES ("a live chase still falls through to the
# sweeper when the classifier reverses").  ``apply_fullback_geometry``'s
# breakaway branch is guarded by TWO conjuncts -- the pure classifier AND
# the defensive-line occupancy gate -- and returns to the sweeper branch
# whenever EITHER refuses.  The anchor-relock latch (2026-09-28) floors the
# ANCHOR the classifier reads, but it does NOT guarantee the VERDICT: a
# frame can still fail the guard even though the chase was armed on a
# genuine break.
#
# The four reversal classes, named so the release rule can be stated once:
#
#   N1  ``carrier_behind_latched_line``  -- the carrier has genuinely
#       dropped behind the LATCHED line (a carried pass back, a carrier
#       running backwards).  THE RELEASE: the hold ends and the writer
#       falls through to the sweeper contract, exactly as it always has.
#   N2  the OCCUPANCY gate refuses -- the anchor is not a line the defence
#       presently HOLDS (``fullback_defensive_line_occupied``).  The
#       classifier itself still returns DEPTH here.
#   N3  the LINE-INTACT LEVEL exclusion -- ``bc.x < test_anchor - 3.0``.
#       The carrier is BEHIND the test anchor, so N3 implies N1 by
#       construction and is released with it.
#   N4  the 2D REACH test -- a lateral channel breach outside the sweeper's
#       reach.  Only reachable with the carrier level with / behind the
#       line in X, so it too implies N1 and is released with it.
#
# THE LAW: while a latch is live, it is AUTHORITY for pursuit ownership.
# A forward anchor relock or an occupancy reversal (N2) may not withdraw a
# pursuit the latch already granted, so the hold keeps the pursuit contract
# on those frames.  The hold REUSES the existing pursuit command path
# byte-for-byte (``fullback_chase_point`` + the CHASE intent) and commands
# the LATCHED kind, so the chase point is the one the arming frame would
# have produced.  It releases ONLY through the existing lifecycle clears
# (``clear_fullback_chase_state`` at RELEASE entry / the miss teardown, tag
# loss, the engagement reset) or N1.  ``classify_fullback_break`` is NOT
# made sticky and is not touched -- the change is LOCAL to this writer's
# ownership decision.
#
# Gated on the SAME ``RLM_FB_BREAK_LATCH`` lever as the anchor floor, so the
# documented disarmed rollback stays write-free AND behaviour-identical: with
# no latch there is no hold.

def latched_fullback_break_kind(defender):
    """The break kind the latch was armed on (``None`` when none/unreadable).

    Pure reader.  Returns only the closed vocabulary (``DEPTH`` / ``LATERAL``)
    so a caller can feed it straight back into ``fullback_chase_point`` and
    reproduce the arming frame's pursuit point.
    """
    if defender is None:
        return None
    value = getattr(defender, FB_BREAK_LATCH_KIND_ATTR, None)
    if value in (FB_BREAK_DEPTH, FB_BREAK_LATERAL):
        return value
    return None


def fullback_chase_hold_live(defender, bc) -> bool:
    """True while a LIVE LATCH authorises the pursuit hold this frame.

    THE RELEASE RULE, in one place.  True only when ALL hold:

      - the ``RLM_FB_BREAK_LATCH`` lever is armed (the hold IS the latch's
        authority, so it lives under the same lever and the disarmed
        rollback stays write-free),
      - *defender* carries an armed latch (``is_fullback_break_latched``),
      - there is a live carrier (nothing to pursue otherwise),
      - and N1 does NOT hold -- the carrier has NOT dropped behind the
        LATCHED line (``carrier_behind_latched_line``).  N1 is the only
        dynamic release; every other release is an existing lifecycle clear
        that drops the latch itself (RELEASE entry / miss teardown / tag
        loss / the engagement reset).

    Pure / total: never raises, never mutates.
    """
    if not fullback_break_latch_enabled():
        return False
    if defender is None or bc is None:
        return False
    if not is_fullback_break_latched(defender):
        return False
    # N1 -- THE RELEASE.
    if carrier_behind_latched_line(defender, bc):
        return False
    return True


def fullback_chase_hold_kind(defender, bc):
    """The break kind a live hold should command, or ``None`` when no hold.

    Returns the LATCHED kind -- the verdict the chase was armed on -- so the
    hold reuses the existing pursuit command path byte-for-byte.  Falls back
    to ``FB_BREAK_DEPTH`` for a latch armed without a readable kind (never
    happens through the shipped arm sites, which always pass a kind).
    """
    if not fullback_chase_hold_live(defender, bc):
        return None
    return latched_fullback_break_kind(defender) or FB_BREAK_DEPTH


def release_fullback_chase_on_anchor_relock(world, defender) -> bool:
    """Make an anchor-relock chase collapse EXPLICIT instead of silent.

    THE AUDIT BACKSTOP.  Called by ``apply_fullback_geometry`` when the
    sweeper leaves CHASE on a frame whose line anchor moved FORWARD.

    The latch should already have prevented the collapse, so this is the
    belt-and-braces half: clearing the latch and the emergency engagement
    here means a collapse that the latch could NOT prevent is still an
    OBSERVED handover (``FB_RELOCK_HANDOVER_ATTR`` set + the engagement
    reset) instead of the ``MODE_OVERWRITE`` the authority probe reports
    for the silent case, where nothing clears the marker.

    DISARMED LEVER => NO WRITE AT ALL: ``RLM_FB_BREAK_LATCH=0`` must
    restore the historical engine byte-for-byte, so every write below is
    gated on the same lever.

    Returns True when the explicit handover was performed.
    """
    if not fullback_break_latch_enabled():
        return False
    if defender is None or not getattr(defender, "fullback_role_active", False):
        return False
    clear_fullback_break_latch(defender)
    reset_fullback_emergency_engagement(defender)
    setattr(defender, FB_RELOCK_HANDOVER_ATTR, True)
    return True


def classify_fullback_break(bc, defender, line_anchor_x, anchor_y) -> str:
    """Classify this frame's break as DEPTH / LATERAL / NONE (2D gate).

    THE SINGLE DEFINITION of what the sweeper must respond to, shared by the
    geometry writer's chase override AND the emergency tackle-candidacy gate
    (``fullback_emergency_defender_active``) so the two can never disagree.

      - ``FB_BREAK_DEPTH``: the carrier is past the defensive line
        (``bc.x > line_anchor + FB_LINE_BREAK_MARGIN_M``) — the historical
        X-only test, unchanged.
      - ``FB_BREAK_LATERAL``: the carrier has broken the sweeper's LATERAL
        channel (more than ``FB_LATERAL_BREAK_M`` off the CHANNEL Y --
        see the channel-reference note in the body: the sweeper's own
        lane while open-play mirroring is on, the anchor rail when it is
        off) AND is inside the sweeper's 2D reach (``hypot(bc - defender)
        <= FB_LATERAL_BREAK_RADIUS_M``).  The reach test is what keeps
        normal play untouched: a carrier sweeping wide on the far side of
        the field, well away from the last defender, is the LINE's problem
        and must not drag the sweeper off its anchor.
      - ``FB_BREAK_NONE``: neither.  The actor keeps its sweeper contract.

    Pure / side-effect free; returns the classification, never a bool, so
    the caller can pick per-branch target coordinates.
    """
    if bc is None or defender is None:
        return FB_BREAK_NONE
    if float(bc.x) > float(line_anchor_x) + FB_LINE_BREAK_MARGIN_M:
        return FB_BREAK_DEPTH
    # ── CHANNEL REFERENCE (2026-09-22) ──
    # WHICH Y IS "the sweeper's channel"?  NOT always ``anchor_y``.
    #
    # With open-play mirroring ON (``FB_LATERAL_OPEN_PLAY``) the lateral
    # anchor IS the carrier's own lane by construction, so
    # ``|bc.y - anchor_y|`` is identically 0 and an anchor-based test can
    # never fire -- the lateral branch would be dead code and the gate
    # would silently collapse back to the X-only depth test it exists to
    # supplement.  In that regime the sweeper's real channel is its OWN
    # LANE: the carrier has broken out of it once it is more than
    # ``FB_LATERAL_BREAK_M`` from the last defender's body.
    #
    # With mirroring OFF (the ``RLM_FB_LATERAL_OPEN_PLAY=0`` rollback, and
    # any future fractional-rail regime) ``anchor_y`` IS a genuine
    # destination rail distinct from the carrier's lane, so the historical
    # anchor-based test is the correct one and is kept verbatim.
    #
    # Both regimes then share the identical 2D reach guard, so a carrier
    # sweeping wide on the far side of the field is never dragged onto the
    # sweeper -- that is the LINE's problem.
    if FB_LATERAL_OPEN_PLAY:
        channel_y = float(getattr(defender, "y", anchor_y) or anchor_y)
    else:
        channel_y = float(anchor_y)
    # ── LINE-INTACT EXCLUSION (2026-09-27) ──
    # A lateral breach is only contestable once the carrier has drawn LEVEL
    # WITH the line.  Behind an intact line the line owns the play and the
    # sweeper holds its post; see FB_LATERAL_BREAK_LEVEL_M.
    if (
        FB_LATERAL_BREAK_LEVEL_ENABLED
        and float(bc.x) < float(line_anchor_x) - FB_LATERAL_BREAK_LEVEL_M
    ):
        return FB_BREAK_NONE
    if abs(float(bc.y) - channel_y) > FB_LATERAL_BREAK_M:
        reach = math.hypot(
            float(bc.x) - float(getattr(defender, "x", 0.0) or 0.0),
            float(bc.y) - float(getattr(defender, "y", 0.0) or 0.0),
        )
        if reach <= FB_LATERAL_BREAK_RADIUS_M:
            return FB_BREAK_LATERAL
    return FB_BREAK_NONE


def fullback_breakaway_live(world, defender=None, bc=None) -> bool:
    """True while the sweeper owns a breakaway chase of its own.

    THE SINGLE GATE for "the carrier is past the line and the last defender
    is contesting it".  Read by the OPEN_PLAY fullback anchor pin so the
    engine's static pin (``target_x = anchor_x``, ``MovementState.DRIFT``,
    ``target_source = "fullback"``) can never overrule the breakaway chase
    authored by ``apply_fullback_geometry`` (the trajectory-projected chase
    point, ``CHASE``, ``target_source = "fullback_geometry"``).

    MEASURED DEFECT this gate removes (probe ``tmp_fb_line_writers``, seed
    20260827, id 200, frames 1690-2170): with both writers live the
    sweeper's ``target_x`` alternated between the live chase point (~119.0)
    and the STATIC line anchor (110.00) -- a ~9 m flip -- and the body
    walked BACKWARDS off a stationary carrier for 29 frames at a time,
    twice in the window.  That is a static target after a genuine line
    break AND a metre-scale target swing in one sequence, and it is why the
    actor's facing pointed away from the carrier: a DRIFT velocity points
    at the anchor.

    Uses the SAME ``classify_fullback_break`` the geometry writer uses, so
    the pin and the chase can never disagree about what a break is.  With
    no defender in hand the canonical X-only ``line_break_active`` test is
    used, which is the same comparison at one remove.
    """
    if world is None:
        return False
    if bc is None:
        bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return False
    if defender is None:
        return bool(line_break_active(world, bc))
    anchor_x = line_anchor_x(world)
    try:
        _ax, anchor_y = compute_fullback_anchor(bc, anchor_x)
    except Exception:
        anchor_y = getattr(defender, "y", None) or 0.0
    try:
        # THE BREAK TEST READS THE LATCHED ANCHOR (2026-09-28).  The pin and
        # the geometry writer must agree on the verdict, so both read the one
        # single reader rather than the raw live anchor.
        _test_anchor = break_test_line_anchor_x(world, defender, anchor_x)
        kind = classify_fullback_break(bc, defender, _test_anchor, anchor_y)
    except Exception:
        return bool(line_break_active(world, bc))
    if kind == FB_BREAK_NONE:
        return False
    # THE OCCUPANCY GATE (2026-09-29).  Holding this pin to the same law as
    # the geometry writer is exactly what its docstring demands ("the pin and
    # the chase can never disagree about what a break is"): without it a
    # reception-seeded anchor no defender occupies reports a break HERE while
    # the gated writer denies it, and the actors' verdicts disagree about the
    # manufactured line.  See ``fullback_defensive_line_occupied``.
    return fullback_defensive_line_occupied(world, _test_anchor, defender)


def fullback_defensive_line_occupied(world, anchor_x, defender=None) -> bool:
    """True when *anchor_x* is a line the defence actually HOLDS.

    THE OCCUPANCY GATE.  A break verdict is only meaningful if the anchor is
    a line the defence occupies: a reception seeds ``ruck_x = bc.x``, so the
    anchor lands 10 m in front of the catch with the whole kicking team still
    goal-side of it, and the carrier then "breaks" it simply by running
    forward.  MEASURED: at the frame that bogus break fires, twelve of twelve
    defenders are ahead of the anchor and none is behind it; on a genuine
    break the carrier has carried the line and bodies sit behind it.

    Contract:
      - a body at or behind ``anchor_x + FB_LINE_OCCUPIED_BAND_M`` holds it,
      - a carrier that has cleared an actual line body also proves the line
        was occupied, even when the catch-seeded anchor sits behind it,
      - the tagged fullback itself is never counted (it is the last defender,
        not a line holder),
      - a world with NO readable defending population reports True, so every
        stub / detached harness keeps the historical contract byte-for-byte,
      - a malformed anchor reports True (never silences a real break).

    Pure / total: never raises, never mutates.
    """
    if not FB_LINE_OCCUPIED_REQUIRED:
        return True
    if world is None or anchor_x is None:
        return True
    try:
        anchor = float(anchor_x)
    except (TypeError, ValueError):
        return True
    if not math.isfinite(anchor):
        return True
    carrier = getattr(world, "ball_carrier", None)
    try:
        carrier_x = float(getattr(carrier, "x", None))
        attack_dir = float(getattr(world, "attacking_direction", 1) or 1)
    except (TypeError, ValueError):
        carrier_x = None
        attack_dir = 1.0
    if not math.isfinite(attack_dir) or attack_dir == 0.0:
        attack_dir = 1.0
    attack_dir = 1.0 if attack_dir > 0.0 else -1.0
    team = getattr(world, "defending_team", None)
    if not team:
        return True
    seen = False
    for body in team:
        if body is None or body is defender:
            continue
        try:
            body_x = float(getattr(body, "x", None))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(body_x):
            continue
        seen = True
        if body_x - anchor <= FB_LINE_OCCUPIED_BAND_M:
            return True
        if (
            carrier_x is not None
            and math.isfinite(carrier_x)
            and is_fullback_line_member(body)
            and (carrier_x - body_x) * attack_dir
            >= FB_LINE_BREAK_MARGIN_M
        ):
            return True
    if not seen:
        # No readable body: an inadmissible world keeps the old contract.
        return True
    return False


def _lateral_break_refusal_reason(bc, defender, test_anchor, anchor_y,
                                  kind) -> str:
    """WHICH lateral conjunct refused -- diagnostic label, never a verdict.

    Mirrors the ORDER of ``classify_fullback_break``'s own tests, so a refusal
    is named by the branch that produced it:

      * the carrier is PAST the line (``kind`` is DEPTH): the DEPTH branch
        classified, so the lateral leg is simply not the admitting leg
        ("depth_break_classified");
      * the LINE-INTACT LEVEL exclusion refused it
        ("line_intact_level_exclusion");
      * the carrier's lane is inside the breach band, so there is no lateral
        breach to contest ("lane_intact");
      * the breach is real but outside the sweeper's 2D reach
        ("out_of_reach").

    It reads the SAME constants the classifier reads, and the caller has
    ALREADY taken the boolean verdict from the classifier -- this only labels
    it.  The label is asserted against the classifier's own verdict by
    ``verify_fb_admission_audit_contract.py`` over a geometry sweep, so the two
    cannot drift apart silently.
    """
    if kind == FB_BREAK_DEPTH:
        return "depth_break_classified"
    if (
        FB_LATERAL_BREAK_LEVEL_ENABLED
        and float(bc.x) < float(test_anchor) - FB_LATERAL_BREAK_LEVEL_M
    ):
        return "line_intact_level_exclusion"
    # The channel reference: with open-play mirroring ON the sweeper's channel
    # is its OWN lane (the anchor mirrors the carrier by construction); with it
    # off, the anchor rail is a genuine distinct rail.  Same rule, same order,
    # as ``classify_fullback_break``.
    if FB_LATERAL_OPEN_PLAY:
        channel_y = float(getattr(defender, "y", anchor_y) or anchor_y)
    else:
        channel_y = float(anchor_y)
    if abs(float(bc.y) - channel_y) <= FB_LATERAL_BREAK_M:
        return "lane_intact"
    return "out_of_reach"


def fullback_lateral_break_active(world, defender, bc=None) -> bool:
    """True when the carrier has broken the sweeper's LATERAL channel.

    The lateral half of the emergency trigger: a carrier outside the
    sweeper's channel while still level with / behind the line in X is a
    genuine break the last defender must be allowed to contest (chase and
    tackle), even though ``line_break_active``'s canonical X test says the
    line has not been passed.  Reads the same ``classify_fullback_break``
    the geometry writer uses.
    """
    if world is None or defender is None:
        return False
    if bc is None:
        bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return False
    anchor_x = line_anchor_x(world)
    _ax, anchor_y = compute_fullback_anchor(bc, anchor_x)
    # THE BREAK TEST READS THE LATCHED ANCHOR (2026-09-28): one of the call
    # sites the break latch must be honoured by, through the single reader.
    _test_anchor = break_test_line_anchor_x(world, defender, anchor_x)
    kind = classify_fullback_break(bc, defender, _test_anchor, anchor_y)
    if kind != FB_BREAK_LATERAL:
        # ── THE LEDGER (2026-09-30) ──
        # A refusal here IS a rejected line-break evaluation, so it is filed
        # with the defensive line's width, the carrier's gap, the nearest
        # defender's distance and the conjunct that refused.  The BOOLEAN
        # verdict above is still taken solely from the classifier; the label is
        # derived from the same constants, in the same order, and is
        # cross-checked against the classifier by the contract verifier.
        _log_admission_rejection(
            world, defender, bc, "lateral_break",
            _lateral_break_refusal_reason(
                bc, defender, _test_anchor, anchor_y, kind,
            ),
            _test_anchor,
        )
        return False
    # THE OCCUPANCY GATE (2026-09-29): the anchor must be a line the defence
    # actually holds, or the "break" is a manufactured one.
    # ── THE LEDGER (2026-09-30) ──
    # Armed-gated so the DISARMED engine evaluates the occupancy gate exactly
    # once, exactly as before.
    if admission_audit_armed(world) and not fullback_defensive_line_occupied(
        world, _test_anchor, defender
    ):
        _log_admission_rejection(
            world, defender, bc, "lateral_break", "line_unoccupied",
            _test_anchor,
        )
    return fullback_defensive_line_occupied(world, _test_anchor, defender)


def fullback_depth_break_active(world, defender, bc=None) -> bool:
    """The DEPTH half of the emergency trigger, read through the latch.

    ``line_break_active`` is the canonical X test and is read by every
    consumer of "has the line been broken".  The FULLBACK's admission gate
    must read that same test through ``break_test_line_anchor_x``, so a
    FORWARD anchor relock cannot withdraw the last defender's tackle
    candidacy mid-chase -- the identical defect the CHASE/DRIFT writer had,
    and the reason a latched chase would otherwise be a chase the actor may
    not contest.  Every non-fullback caller keeps the live anchor.
    """
    if world is None or defender is None:
        return False
    if bc is None:
        bc = getattr(world, "ball_carrier", None)
    if bc is None:
        # ── THE LEDGER (2026-09-30) ──
        # There is no line-break evaluation without a carrier, so this refusal
        # IS one: filed with the anchor (none) and the nearest defender still
        # measured against the world.
        _log_admission_rejection(
            world, defender, bc, "depth_break", "no_carrier",
        )
        return False
    try:
        from game.defence.defence_lvl_1.movement_mode_decision import (
            carrier_past_defensive_line,
        )
    except Exception:
        return False
    try:
        _test_anchor = break_test_line_anchor_x(world, defender)
    except Exception:
        return False
    if not carrier_past_defensive_line(
        world, bc, FB_LINE_BREAK_MARGIN_M, anchor_x=_test_anchor
    ):
        # ── THE LEDGER (2026-09-30) ──
        # The canonical DEPTH test refused: the carrier has not cleared the
        # line by the break margin.  The row carries the signed gap and the
        # surplus over that margin, so "how short" is measured, not inferred.
        _log_admission_rejection(
            world, defender, bc, "depth_break", "below_depth_margin",
            _test_anchor,
        )
        return False
    # THE OCCUPANCY GATE (2026-09-29): being past an anchor the defence has
    # not reached is not a line break.  See
    # ``fullback_defensive_line_occupied``.
    # ── THE LEDGER (2026-09-30) ──
    # Armed-gated so the DISARMED engine evaluates the occupancy gate exactly
    # once, exactly as before.
    if admission_audit_armed(world) and not fullback_defensive_line_occupied(
        world, _test_anchor, defender
    ):
        _log_admission_rejection(
            world, defender, bc, "depth_break", "line_unoccupied",
            _test_anchor,
        )
    return fullback_defensive_line_occupied(world, _test_anchor, defender)


def fullback_target_distance(defender, target_x, target_y) -> float:
    """Euclidean (2D) distance from *defender* to a commanded target point.

    THE ERROR METRIC FOR THE FB SPEED HELPERS.  Passing this instead of the
    signed X error is what stops a body that has ARRIVED IN DEPTH from
    having its whole scalar speed throttled while metres of LATERAL ground
    are still owed: the taper then only engages when the actor is genuinely
    close to the point it was told to reach, on whichever axis.
    """
    return math.hypot(
        float(target_x) - float(getattr(defender, "x", 0.0) or 0.0),
        float(target_y) - float(getattr(defender, "y", 0.0) or 0.0),
    )


def fullback_chase_intercept_enabled() -> bool:
    """Whether the emergency chase PROJECTS the carrier (intercept) or trails it.

    Default ON.  ``RLM_FB_CHASE_INTERCEPT=0`` restores the historical X-only
    trail pursuit byte-for-byte with no code revert.

    Read through the module boolean on purpose, so a verifier or a probe can
    A/B the two contracts IN-PROCESS by rebinding
    ``fullback_role.FB_CHASE_INTERCEPT`` -- the same pattern
    ``FB_BEATEN_OWNERSHIP`` documents.
    """
    return bool(FB_CHASE_INTERCEPT)


def fullback_chase_mode() -> str:
    """The shipped chase-target contract: ``"intercept"`` or ``"trail"``."""
    return (
        FB_CHASE_MODE_INTERCEPT
        if fullback_chase_intercept_enabled()
        else FB_CHASE_MODE_TRAIL
    )


def _fullback_lead_horizon_s(defender, bc_x, bc_y) -> float:
    """The damped, near-range-tapered projection horizon (seconds).

    ``tau = (d / s_def) * FB_CHASE_LEAD_DAMPING``, tapered to zero inside
    ``FB_CHASE_LEAD_NEAR_M`` and to full strength at ``FB_CHASE_LEAD_FULL_M``,
    then clamped to ``FB_CHASE_LEAD_MAX_S``.

    Total: an unreadable distance or speed reports 0.0 -- the no-lead answer --
    rather than raising, so a stub body degrades to the current-lane mirror.
    """
    distance = fullback_target_distance(defender, bc_x, bc_y)
    speed = float(getattr(defender, "max_defensive_speed_mps", 0.0) or 0.0)
    if speed <= 0.0:
        speed = float(getattr(defender, "max_speed_mps", 0.0) or 0.0)
    if speed <= 0.0:
        speed = FB_CHASE_LEAD_DEFAULT_SPEED_MPS
    if speed <= 0.0:
        return 0.0
    tau = (distance / speed) * FB_CHASE_LEAD_DAMPING
    # NEAR-RANGE TAPER (see the block comment): suppressed inside the contact
    # radius so the pre-tackle slot gate still sees the carrier's own lane.
    if distance <= FB_CHASE_LEAD_NEAR_M:
        taper = 0.0
    elif distance >= FB_CHASE_LEAD_FULL_M:
        taper = 1.0
    else:
        span = FB_CHASE_LEAD_FULL_M - FB_CHASE_LEAD_NEAR_M
        taper = (distance - FB_CHASE_LEAD_NEAR_M) / span if span > 0.0 else 1.0
    tau *= max(0.0, min(1.0, taper))
    if not math.isfinite(tau) or tau < 0.0:
        return 0.0
    return min(FB_CHASE_LEAD_MAX_S, tau)


def fullback_chase_point(bc, defender, anchor_x, break_kind):
    """The fullback's SINGLE emergency-chase command point ``(x, y)``.

    THE ONE DEFINITION of where the last defender is sent once a break is
    live.  Read by all three chase writers -- ``apply_fullback_geometry``,
    ``establish_fullback_live_break_chase`` and
    ``clear_static_pursuit_target`` -- so the breakaway sweep and the mid-tackle
    handover can never disagree about the pursuit point.

    Contract:

      * ``FB_BREAK_DEPTH`` (the carrier is past the line): the carrier's
        predicted XY interception point, without a trailing offset. The X
        projection can carry the fullback in-goal to defend the tryline.
      * any other break kind (a LATERAL channel breach): the same trajectory
        anticipation, with X floored at the sweeper's own depth anchor so the
        actor protects the backfield ground it alone covers.
      * ``RLM_FB_CHASE_INTERCEPT=0``: the historical X-only trail,
        byte-for-byte, with no projection at all.

    Total: an unreadable carrier reports the sweeper anchor itself -- a static
    pin, which every caller already knows how to clear -- never a raise and
    never a NaN command.
    """
    if bc is None:
        return (
            float(anchor_x),
            float(getattr(defender, "y", 0.0) or 0.0),
        )
    try:
        bc_x = float(getattr(bc, "x", None))
        bc_y = float(getattr(bc, "y", None))
    except (TypeError, ValueError):
        return (
            float(anchor_x),
            float(getattr(defender, "y", 0.0) or 0.0),
        )
    if not (math.isfinite(bc_x) and math.isfinite(bc_y)):
        return (
            float(anchor_x),
            float(getattr(defender, "y", 0.0) or 0.0),
        )

    # ── ROLLBACK: the historical X-only trail, verbatim ──
    if not fullback_chase_intercept_enabled():
        if break_kind == FB_BREAK_DEPTH:
            return float(bc.x) - FB_CHASE_TRAIL_M, bc.y
        return max(anchor_x, float(bc.x) - FB_CHASE_TRAIL_M), bc.y

    # ── INTERCEPT: project the trajectory toward the tryline in X ──
    try:
        bc_vx = float(getattr(bc, "vx", 0.0) or 0.0)
    except (TypeError, ValueError):
        bc_vx = 0.0
    if not math.isfinite(bc_vx):
        bc_vx = 0.0
    try:
        bc_vy = float(getattr(bc, "vy", 0.0) or 0.0)
    except (TypeError, ValueError):
        bc_vy = 0.0
    if not math.isfinite(bc_vy):
        bc_vy = 0.0

    tau = _fullback_lead_horizon_s(defender, bc_x, bc_y)
    lead_x = bc_vx * tau
    lead_y = bc_vy * tau
    lead_distance = math.hypot(lead_x, lead_y)
    if not math.isfinite(lead_distance):
        velocity_scale = max(abs(bc_vx), abs(bc_vy))
        if velocity_scale > 0.0:
            unit_x = bc_vx / velocity_scale
            unit_y = bc_vy / velocity_scale
            unit_length = math.hypot(unit_x, unit_y)
            lead_x = unit_x / unit_length * FB_CHASE_LEAD_MAX_M
            lead_y = unit_y / unit_length * FB_CHASE_LEAD_MAX_M
    elif lead_distance > FB_CHASE_LEAD_MAX_M:
        scale = FB_CHASE_LEAD_MAX_M / lead_distance
        lead_x *= scale
        lead_y *= scale
    target_x = bc_x + lead_x
    target_y = bc_y + lead_y

    # LATERAL break: never abandon the sweeper's own depth anchor.
    if break_kind != FB_BREAK_DEPTH:
        target_x = max(target_x, float(anchor_x))
    return _clamp_anchor(target_x, target_y)


def is_fullback_line_member(d) -> bool:
    """LINE-MEMBERSHIP predicate: a tagged fullback is never a line member.

    Provided as the single readable expression of the membership half of
    the law so no consumer has to re-derive it from ``defender_role`` (a
    tagged actor carries TACKLER_1/2/3 during an emergency tackle and must
    still fail every line test).
    """
    if d is None:
        return False
    if getattr(d, "fullback_role_active", False):
        return False
    if getattr(d, "tackle_state_active", False):
        return False
    from game.defence.defence_lvl_1.population_authority import (
        LINE_EXCLUDED_ROLES,
    )
    return getattr(d, "defender_role", None) not in LINE_EXCLUDED_ROLES


def fullback_emergency_defender_active(world, d, bc=None) -> bool:
    """True when the tagged fullback may act as a tackle-capable defender.

    The CONTROLLED admission gate for the emergency path.  Returns True
    only when ALL hold:

      - the ``RLM_FULLBACK_TACKLE_CANDIDACY`` runtime lever is on,
      - *d* carries the fullback tag (``fullback_role_active``),
      - *d* carries tackle authority (``can_acquire_tackle``),
      - *d* is neither frozen nor freeze-timed,
      - a GENUINE LINE BREAK exists: the canonical DEPTH break
        (``line_break_active``) OR the 2D LATERAL break
        (``fullback_lateral_break_active`` -- the carrier outside the
        sweeper's channel and inside its 2D reach).  Both branches are read
        from the single ``classify_fullback_break`` the geometry writer's
        chase override uses, so CHASE and tackle candidacy can never
        disagree about what a break is.

    The standalone fullback authority refresh uses this as its genuine-break
    and basic tackle-capability gate. Fullbacks are excluded from lane and
    corridor ownership; this predicate does not grant either one.

    Reads NO line-membership field -- tackle eligibility and line
    membership stay fully separate.
    """
    # ── THE LEDGER (2026-09-30) ──
    # The IDENTITY half is instrumented here; the LINE-BREAK half is
    # instrumented at the two legs, so a break refusal is attributed to the leg
    # that refused and a body refused on identity is NOT reported as a rejected
    # line-break evaluation (no break arithmetic is performed for it).
    #
    # The lever-off refusal is filed only for a body that carries the tag, so
    # the ledger stays about the LAST DEFENDER rather than about every line body
    # the lane selector happens to offer the gate.
    if not FULLBACK_TACKLE_CANDIDACY:
        if getattr(d, "fullback_role_active", False):
            _log_admission_rejection(
                world, d, bc, "admission", "lever_off",
            )
        return False
    if d is None:
        return False
    if not getattr(d, "fullback_role_active", False):
        return False
    if not getattr(d, "can_acquire_tackle", False):
        _log_admission_rejection(
            world, d, bc, "admission", "no_tackle_authority",
        )
        return False
    if not fullback_retreat_10_compliant(world, d):
        _log_admission_rejection(
            world, d, bc, "admission", "retreat_10_pending",
        )
        return False
    if getattr(d, "frozen", False):
        _log_admission_rejection(world, d, bc, "admission", "frozen")
        return False
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        _log_admission_rejection(world, d, bc, "admission", "freeze_timer")
        return False
    if fullback_depth_break_active(world, d, bc):
        _log_admission(world, d, bc, "depth_break")
        return True
    # LATERAL BREAK (2026-09-22): the last defender is ALSO admitted when the
    # carrier has broken its LATERAL channel (outside the sweeper's channel,
    # inside its 2D reach) without having passed the line in X -- otherwise a
    # lateral line break could never be chased or tackled by the one defender
    # standing in front of it.
    if fullback_lateral_break_active(world, d, bc):
        _log_admission(world, d, bc, "lateral_break")
        return True
    return False


def fullback_retreat_10_compliant(world, defender) -> bool:
    """Whether a tagged fullback has reached the ruck's 10 m offside line.

    Fullbacks own their return geometry rather than the shared
    ``DEF_RETREAT_10`` writer, but they remain offside until they reach the
    same ``ruck_x + 10`` boundary.  If the ruck coordinate is unavailable,
    the published defensive-line anchor is the equivalent boundary.
    """
    if defender is None or not getattr(defender, "fullback_role_active", False):
        return True

    line_x = None
    try:
        ruck_x = float(getattr(world, "ruck_x_m", None))
        if math.isfinite(ruck_x):
            line_x = ruck_x + 10.0
    except (TypeError, ValueError):
        pass
    if line_x is None:
        try:
            published_line_x = float(
                getattr(world, "defensive_line_anchor", None)
            )
            if math.isfinite(published_line_x):
                line_x = published_line_x
        except (TypeError, ValueError):
            pass
    if line_x is None:
        return True

    try:
        defender_x = float(getattr(defender, "x", None))
    except (TypeError, ValueError):
        return False
    return math.isfinite(defender_x) and defender_x >= line_x - 1e-6


def clear_fullback_emergency_engage_authority(d) -> None:
    """Revoke standalone fullback engage candidacy without changing lane roles."""
    if d is not None:
        setattr(d, FB_EMERGENCY_ENGAGE_AUTHORITY_ATTR, False)


def refresh_fullback_emergency_engage_authority(world, d, bc=None) -> bool:
    """Publish standalone engage authority only during an eligible break chase.

    This authority is intentionally independent of ``tackle_role``: lane
    selection may rewrite that field every frame, while the fullback keeps
    chasing under its geometry controller until TES actually arms it.
    """
    if d is None:
        return False
    state = getattr(getattr(d, "movement_state", None), "name", None)
    if state is None:
        state = str(getattr(d, "movement_state", None))
    chasing = state in ("CHASE", "SHOOT")
    active = (
        chasing
        and not is_fullback_emergency_engaged(d)
        and fullback_emergency_defender_active(world, d, bc)
        and not fullback_beaten_ownership_loss_active(world, d, bc)
    )
    setattr(d, FB_EMERGENCY_ENGAGE_AUTHORITY_ATTR, active)
    return active


def fullback_emergency_engage_authority_active(world, d, bc=None) -> bool:
    """Validate the fullback-owned engage flag against the live break gates."""
    if d is None or not getattr(d, FB_EMERGENCY_ENGAGE_AUTHORITY_ATTR, False):
        return False
    state = getattr(getattr(d, "movement_state", None), "name", None)
    if state is None:
        state = str(getattr(d, "movement_state", None))
    return (
        state in ("CHASE", "SHOOT")
        and not is_fullback_emergency_engaged(d)
        and fullback_emergency_defender_active(world, d, bc)
        and not fullback_beaten_ownership_loss_active(world, d, bc)
    )


def fullback_tackle_authority_active(d) -> bool:
    """True when an actor has transferred to tackle/TES movement ownership.

    A tagged fullback's separate break-chase candidacy is not movement
    authority. Its geometry controller yields only after TES arms it.
    Non-fullbacks retain the existing role/commit/stance ownership rules.

    Every consumer that would otherwise impose the SWEEPER contract on the
    body (geometry authority, approach-speed cap, phase unfreeze) consults
    this, so the actor behaves like any other defender during a break.
    Line membership is NOT part of this test — the tag still excludes the
    body from every line-shape system.
    """
    if d is None:
        return False
    if getattr(d, "fullback_role_active", False):
        # A standalone candidate still belongs to the fullback controller.
        # Only actual TES arming transfers its movement authority.
        return is_fullback_emergency_engaged(d)
    if is_fullback_emergency_engaged(d):
        return True
    if getattr(d, "tackle_role", None) in ("PRIMARY", "SECONDARY"):
        return True
    if getattr(d, "_tackle_commit_active", False):
        return True
    return bool(getattr(d, "pre_tackle_stance", False))


def fullback_approach_cap_applies(d) -> bool:
    """True when the 2.0 m/s sweeper approach cap may be applied to *d*.

    The cap IS the sweeper contract: the deep anchor glide never bursts and
    never outruns the line.  It must NOT be applied once the actor has been
    granted tackle authority, otherwise the emergency defender physically
    cannot reach a breaking carrier — measured, the last defender was held
    at 2.0 m/s by this cap while every other defender ran at the 8.3 m/s
    unified ceiling, which is exactly the "fullback never makes the tackle
    on a break" symptom.  With the cap lifted the actor closes at the same
    governed speed as any other committed defender. Standalone break-chase
    candidacy also lifts the cap without transferring movement ownership from
    the fullback geometry controller.

    RECOVER EXEMPTION (2026-09-21): the same reasoning applies to the
    recovery locomotion.  A body that is owed six or more metres by its own
    anchor and has been told to recover must take the fast path home --
    pinning it at 2.0 m/s is the exact immobility the state exists to
    remove.  The exempt state is still bounded: the unified defensive
    ceiling times FB_RECOVER_SPEED_SCALE (see that constant and
    fullback_recovery_speed), with the arrival taper kept.
    """
    if (
        getattr(d, "fullback_role_active", False)
        and getattr(d, FB_EMERGENCY_ENGAGE_AUTHORITY_ATTR, False)
    ):
        return False
    if movement_state_name(d) == "RECOVER":
        return False
    return not fullback_tackle_authority_active(d)


def movement_state_name(d) -> str | None:
    """Enum NAME of *d*'s MovementState (None when unset/unreadable).

    Name-based on purpose: this module must not import player.movement_states
    at module scope (the fully-qualified import is deferred inside
    apply_fullback_geometry so a cycle is impossible), and every other
    consumer in the engine compares states by name for the same reason.
    """
    ms = getattr(d, "movement_state", None)
    if ms is None:
        return None
    return getattr(ms, "name", None) or str(ms)


def fullback_recovery_armed(world) -> bool:
    """True when the owning engine has armed high-velocity FB recovery.

    Engine-owned opt-in: ``DefenceManager.update_open_play`` stamps
    ``world.fullback_recover_allowed = True`` on every live OPEN_PLAY
    dispatch and kickoff reset clears it.  Stub / verifier worlds that never
    stamp it keep the historical DRIFT-only geometry contract, so the
    authority's behaviour is unchanged for every caller that has not opted
    in.  ``RLM_FULLBACK_RECOVER=0`` is the documented A-B / rollback lever.
    """
    if os.environ.get("RLM_FULLBACK_RECOVER", "").strip().lower() in (
        "0", "false", "no", "off",
    ):
        return False
    return bool(getattr(world, "fullback_recover_allowed", False))


def fullback_recovery_active(world, defender, anchor_x, anchor_y) -> bool:
    """True when the tagged fullback should travel home in RECOVER.

    The single predicate for the high-velocity recovery locomotion.  True
    only when ALL hold:

      - the engine lever is armed (``fullback_recovery_armed``),
      - *defender* carries the fullback tag,
      - the actor is NOT playing as a defender (no tackle authority, so a
        committing / committed / armed actor keeps its own contract),
      - the actor is not TES-owned (attached / in contact) and the ball is
        not in kickoff flight (the flight anchor has its own geometry), and
      - the actor owes ground to the anchor: the EUCLIDEAN debt exceeds
        FB_RECOVER_DEBT_M, OR the LATERAL debt (|dy|) exceeds
        FB_LATERAL_RECOVER_M.  FB_RECOVER_EXIT_DEBT_M is the euclidean
        release band so a body hovering at the arm threshold cannot flap in
        and out of RECOVER frame to frame.

    LATERAL ARM (2026-09-22): once the open-play anchor MIRRORS the carrier's
    lane, a cross-field slide is owed ground exactly like a depth recovery,
    and the 2.0 m/s sweeper cap is what turns that slide into a crawl.  The
    lateral half of the debt therefore arms the same cap-exempt locomotion,
    so the actor changes Y at the ordinary line pace instead of being
    pinned off its lane by the sweeper contract.

    Reads NO line-membership field, and never mutates anything.
    """
    if not fullback_recovery_armed(world):
        return False
    if defender is None or not getattr(defender, "fullback_role_active", False):
        return False
    if fullback_tackle_authority_active(defender):
        return False
    if (
        getattr(world, "tes_active", False)
        and not getattr(defender, "_fb_post_tackle_return_active", False)
    ):
        return False
    if getattr(world, "in_kickoff", False):
        return False
    if getattr(defender, "tes_attached", False) or getattr(defender, "in_contact", False):
        return False
    dy = float(anchor_y) - float(getattr(defender, "y", 0.0) or 0.0)
    debt = math.hypot(
        float(anchor_x) - float(getattr(defender, "x", 0.0) or 0.0),
        dy,
    )
    # HYSTERESIS: a body already recovering stays in RECOVER until it is
    # nearly home, so the arm threshold is crossed exactly once per run.
    if movement_state_name(defender) == "RECOVER":
        return debt > FB_RECOVER_EXIT_DEBT_M or abs(dy) > FB_LATERAL_RECOVER_M
    return debt > FB_RECOVER_DEBT_M or abs(dy) > FB_LATERAL_RECOVER_M


# ─────────────────────────────────────────────────────────────────────
# BEATEN-DEFENDER OWNERSHIP LOSS (2026-09-27)
# ─────────────────────────────────────────────────────────────────────
# THE DEFECT THIS REMOVES ("the beaten fullback keeps a lane it cannot
# intercept").  Lane ownership is granted on "a genuine line break exists"
# and released on "the break is gone".  Neither test says anything about
# the FULLBACK: the ownership predicate's break clause
# (``fullback_emergency_defender_active`` -> ``line_break_active`` ->
# ``carrier_past_defensive_line`` = ``bc.x > ruck_x + 10 + 1.0``) measures
# the CARRIER against the LOCKED LINE ANCHOR.  Being beaten changes neither
# of those two numbers, so the predicate stays TRUE after the carrier runs
# past the last defender, the body is re-admitted to lane selection and
# re-stamped PRIMARY every frame, and every designed clearer
# (``release_stale_fullback_lane_stamp``, the pools, the corridor) either
# defers to that same still-true predicate or exempts the tagged fullback.
#
# The one geometric bound in the lane system is ONE-SIDED:
#
#     intercept_tacklers.is_lane_tackler_candidate
#         return d.x <= bc_x + ahead_engage_m          # ahead_engage_m = 6.0
#
# Its own docstring states the intent -- "a defender further than
# ``ahead_engage_m`` past the carrier is genuinely defeated and does not own
# the lane" -- but the sign makes it fire only for a defender AHEAD of the
# carrier.  For an overtaken defender (``d.x < bc.x``, the chase-from-behind
# case a beaten fullback is always in) the expression is trivially TRUE.
# Measured shape (FULLBACK_TACKLE_LOSS_OWNERSHIP_REPORT.md sect.3):
# ``FB.x = 46.1``, ``BC.x = 55.4`` -- the last defender ~9.3 m BEHIND the
# carrier, still holding PRIMARY.
#
# THE MISSING PREDICATE is the negative-side twin of that band: the carrier
# has run past this defender by more than the SAME 6.0 m intercept band, so
# the body can no longer intercept and does not own a lane.  Ownership loss
# is NOT pursuit loss: with no lane stamp the sweeper contract is restored,
# ``apply_fullback_geometry`` stops yielding, and its breakaway CHASE
# branch owns the body (pursuit point AND governed speed).  The band is
# hysteresis-free, so the moment the chasing body closes back inside 6.0 m
# of the carrier it is re-admitted and can tackle again -- pursuit, then
# tackle, with no lock-out.

# The behind band, in metres: the exact mirror of the lane system's
# ``ahead_engage_m`` default (intercept_tacklers.is_lane_tackler_candidate),
# so "genuinely defeated" means the same distance in both directions.
FB_BEATEN_ENGAGE_M: float = 6.0

# A-B / rollback lever: RLM_FB_BEATEN_OWNERSHIP=0 disables ONLY this
# predicate (the lane stamp survives a beaten body exactly as it does
# today), with no code revert -- the same pattern as every other lever in
# this module.  Read at import into a module boolean so a verifier can also
# A/B it in-process by rebinding ``fullback_role.FB_BEATEN_OWNERSHIP``.
FB_BEATEN_OWNERSHIP_ENV_VAR: str = "RLM_FB_BEATEN_OWNERSHIP"
FB_BEATEN_OWNERSHIP: bool = (
    os.environ.get(FB_BEATEN_OWNERSHIP_ENV_VAR, "1").strip().lower()
    not in ("0", "false", "no", "off")
)

# The ownership stamps that make the geometry authority YIELD.  Withdrawn
# together when the ownership is lost, because ``fullback_tackle_authority_
# active`` reads all three and leaving any one behind would keep the yield --
# and therefore the stale command -- alive.
FB_OWNERSHIP_STAMP_ATTRS: tuple = (
    "tackle_role",
    "_tackle_commit_active",
    "pre_tackle_stance",
)


def fullback_beaten_ownership_enabled() -> bool:
    """Whether the beaten-defender ownership-loss law is armed.

    Default ON.  ``RLM_FB_BEATEN_OWNERSHIP=0`` restores the pre-change
    engine (a beaten fullback keeps whatever lane stamp it holds) with no
    code revert.
    """
    return bool(FB_BEATEN_OWNERSHIP)


def is_beaten_by_carrier(
    defender, bc, behind_engage_m: float = FB_BEATEN_ENGAGE_M
) -> bool:
    """True when the carrier has run PAST *defender* by more than the band.

    THE NEGATIVE-SIDE TWIN of ``intercept_tacklers.is_lane_tackler_candidate``
    ('a defender further than ``ahead_engage_m`` past the carrier is
    genuinely defeated').  Attack runs +X, so an overtaken defender has
    ``defender.x < bc.x``; the body is defeated once the carrier is more
    than ``behind_engage_m`` beyond it, because from that separation the
    defender can no longer intercept the carrier's path.

    Pure geometry and TOTAL: a None body, a None carrier or an unreadable
    coordinate reports False (never raises), so a stub world degrades to
    the historical behaviour.
    """
    if defender is None or bc is None:
        return False
    raw_defender_x = getattr(defender, "x", None)
    raw_carrier_x = getattr(bc, "x", None)
    if raw_defender_x is None or raw_carrier_x is None:
        return False
    try:
        defender_x = float(raw_defender_x)
        carrier_x = float(raw_carrier_x)
    except (TypeError, ValueError):
        return False
    # The BAND is this module's own constant, so a malformed argument falls
    # back to the documented value rather than blinding the law; only an
    # unreadable POSITION reports False.
    try:
        band = float(behind_engage_m)
    except (TypeError, ValueError):
        band = FB_BEATEN_ENGAGE_M
    if not (math.isfinite(defender_x) and math.isfinite(carrier_x)):
        return False
    if not math.isfinite(band) or band < 0.0:
        band = FB_BEATEN_ENGAGE_M
    return defender_x < carrier_x - band


def fullback_beaten_ownership_loss_active(world, defender, bc=None) -> bool:
    """The OWNERSHIP-LOSS predicate: a tagged FB carried past on a live break.

    True only when ALL hold:

      - the ``RLM_FB_BEATEN_OWNERSHIP`` lever is on,
      - *defender* carries the fullback tag (``fullback_role_active``),
      - the actor is NOT emergency-engaged -- an armed TES tackler's stamps
        belong to the tackle cycle, which owns them until the end-of-cycle
        reset (``reset_fullback_emergency_engagement``),
      - a genuine break is live by the SAME two-branch test the admission
        gate reads (``line_break_active`` OR ``fullback_lateral_break_active``
        -- both delegate to the single ``classify_fullback_break``), so this
        predicate can only ever withdraw an ownership that a break granted
        in the first place,
      - the carrier has run past the body by more than ``FB_BEATEN_ENGAGE_M``
        (``is_beaten_by_carrier``).

    It is consulted at the two sites that own lane ownership: the PRIMARY
    ADMISSION gates (``intercept_tacklers._is_eligible_lane_defender`` and
    ``defensive_behaviour_unified.select_tacklers``'s ``selectable`` pool)
    and the release (``release_stale_fullback_lane_stamp``).  Nothing
    downstream -- the DRIFT / anchor re-stamp, the emergency yield, the
    static-pin clearance -- has to change: with no ownership the geometry
    authority's breakaway CHASE branch owns the body again.

    Reads NO line-membership field and never mutates anything.
    """
    if not fullback_beaten_ownership_enabled():
        return False
    if defender is None:
        return False
    if not getattr(defender, "fullback_role_active", False):
        return False
    if is_fullback_emergency_engaged(defender):
        return False
    if bc is None:
        bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return False
    if not (
        fullback_depth_break_active(world, defender, bc)
        or fullback_lateral_break_active(world, defender, bc)
    ):
        return False
    return is_beaten_by_carrier(defender, bc)


def clear_beaten_fullback_ownership_stamps(defender) -> list:
    """Clear legacy selector/commit/stance stamps from a beaten fullback.

    These stamps are not authority signals for a tagged fullback anymore, but
    clearing the package prevents generic tackle writers and audits from
    retaining stale state after the carrier beats it.

    The emergency-engagement marker is deliberately NOT touched: it belongs
    to the tackle cycle, and this helper is only ever reached for a
    non-engaged body.

    Returns the list of attributes actually changed (the wiring proof).
    """
    cleared: list = []
    if defender is None:
        return cleared
    if getattr(defender, "tackle_role", None) is not None:
        defender.tackle_role = None
        cleared.append("tackle_role")
    if bool(getattr(defender, "_tackle_commit_active", False)):
        defender._tackle_commit_active = False
        cleared.append("_tackle_commit_active")
    if bool(getattr(defender, "pre_tackle_stance", False)):
        defender.pre_tackle_stance = False
        cleared.append("pre_tackle_stance")
    return cleared


def release_stale_fullback_lane_stamp(world, defender) -> bool:
    """Clear a STALE lane-tackler stamp from a tagged fullback.

    THE GAP THIS CLOSES (B2b, 2026-09-16)
    -------------------------------------
    Lane selection and the legacy banded selector now exclude tagged
    fullbacks. This cleanup remains necessary for stale PRIMARY/commit/stance
    stamps left by prior selector or pre-tackle paths: a decayed break or a
    beaten fullback must not carry those generic stamps into later frames.

    The separate fullback engage flag is not derived from ``tackle_role``.
    This cleanup is a compatibility/safety release for generic consumers;
    the geometry controller itself yields only after TES arms the actor.

    Contract:
      - only a ``fullback_role_active`` body is considered,
      - a body inside a live engagement (``_fb_emergency_engaged``) is left
        alone — the tackle cycle owns its stamps until the end-of-cycle
        reset,
      - the legacy stamp is dropped when emergency eligibility ends or the
        actor is genuinely beaten during a live break,
      - OR the actor has been OVERTAKEN by the carrier on a live break
        (``fullback_beaten_ownership_loss_active``): the standalone engage
        authority is revoked and stale stamps are cleared together.

    Returns True when a stale stamp was released.
    """
    if defender is None:
        return False
    if not getattr(defender, "fullback_role_active", False):
        return False
    if is_fullback_emergency_engaged(defender):
        return False
    if getattr(defender, "tackle_role", None) not in ("PRIMARY", "SECONDARY"):
        return False
    if (
        fullback_emergency_defender_active(world, defender)
        and not fullback_beaten_ownership_loss_active(world, defender)
    ):
        return False
    # ── BEATEN-OWNERSHIP WITHDRAWAL (2026-09-27) ──
    # The actor is no longer a valid standalone candidate. Clear stale
    # selector/pre-tackle stamps as one observable package.
    clear_beaten_fullback_ownership_stamps(defender)
    return True


def is_fullback_command_target(target_x) -> bool:
    """True when *target_x* is inside this module's own command envelope.

    ``apply_fullback_geometry`` clamps every command it writes to the legal
    field strip (``LEFT_TRYLINE_X`` .. ``FIELD_MAX_X``), so a value outside
    that strip was written by another subsystem (or is a sentinel) and is not
    a fullback command at all.  The pursuit clearance must never claim such a
    value — that is what keeps the emergency path's yield intact for the
    TES movement owners.
    """
    if not isinstance(target_x, (int, float)):
        return False
    return (
        LEFT_TRYLINE_X - FB_COMMAND_ENVELOPE_EPS_M
        <= float(target_x)
        <= FIELD_MAX_X + FB_COMMAND_ENVELOPE_EPS_M
    )


def is_static_pursuit_pin(defender, anchor_x) -> bool:
    """True when *defender*'s ``target_x`` is a STATIC pin, not a command.

    Two signals, mirroring ``line_break_static_guard``:

      - the target sits ON THE SWEEPER ANCHOR (``ruck_x + 10 + depth``) — a
        per-ruck constant that produces no forward closure on a moving
        carrier;
      - the target sits ON THE BODY'S OWN X — a suppressed target that
        produces no velocity this frame.

    A live chase target (already at the pursuit point) and any out-of-band
    value are NOT pins, which is what keeps the emergency path's yield
    intact for the commit / corridor owners.

    Pure / side-effect free.
    """
    tx = getattr(defender, "target_x", None)
    if not isinstance(tx, (int, float)):
        return False
    tx = float(tx)
    if (
        anchor_x is not None
        and abs(tx - float(anchor_x)) < FB_STATIC_TARGET_ANCHOR_EPS_M
    ):
        return True
    return (
        abs(tx - float(getattr(defender, "x", 0.0) or 0.0))
        < FB_STATIC_TARGET_BODY_EPS_M
    )


def clear_static_pursuit_target(
    defender, world, bc=None, anchor_x=None
) -> bool:
    """Clear a STATIC post-line-break target pin on a tagged fullback.

    THE FRAME-1814 RECOVERY.  Called from the target assignment loop at the
    point where the geometry writer would otherwise leave the fullback's
    command untouched (the EMERGENCY-DEFENDER YIELD).  Returns True only
    when ALL hold:

      - the ``RLM_FB_STATIC_PURSUIT_CLEAR`` lever is on,
      - *defender* carries the fullback tag,
      - a GENUINE LINE BREAK exists (the canonical predicate, so this can
        never disagree with CHASE / tackle candidacy),
      - the current ``target_x`` is a STATIC PIN (on the sweeper anchor or
        on the body's own X) — never a live chase target.

    On success the command becomes the shared trajectory-projected pursuit
    point.  The MOVEMENT STATE is deliberately NOT set here: the caller owns
    that, so this helper stays a pure target writer and can be reused.

    Reads no line-membership field and mutates only ``target_x`` /
    ``target_y``.
    """
    if not FB_STATIC_PURSUIT_CLEAR:
        return False
    if defender is None or not getattr(defender, "fullback_role_active", False):
        return False
    if bc is None:
        bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return False
    if not line_break_active(world, bc):
        return False
    if anchor_x is None:
        anchor_x = line_anchor_x(world)
    tx = getattr(defender, "target_x", None)
    # Only a value inside this module's own clamped command envelope can be a
    # fullback command; anything else belongs to another writer.
    if not is_fullback_command_target(tx):
        return False
    # The frozen value in the telemetry is the SWEEPER anchor (line anchor
    # + saturated depth), so judge the pin against that as well as the anchor.
    sweeper_anchor_x, _sweeper_anchor_y = compute_fullback_anchor(
        bc, float(anchor_x)
    )
    pinned = (
        is_static_pursuit_pin(defender, sweeper_anchor_x)
        or is_static_pursuit_pin(defender, float(anchor_x))
    )
    # SINGLE CHASE POINT: the same law the breakaway sweep and the mid-tackle
    # handover use, so the clearance tracks the INTERCEPT point rather than the
    # historical X-only trail.  The lateral component is no longer dropped --
    # the old form wrote ``bc.x - trail`` and left ``target_y`` at the raw
    # carrier lane, which is exactly the "no y-axis command" defect.
    pursuit_x, pursuit_y = fullback_chase_point(
        bc, defender, float(anchor_x), FB_BREAK_DEPTH,
    )
    # Not a static pin AND already at (or ahead of) the pursuit point: another
    # writer is driving the command -- leave it alone.
    if not pinned and float(tx) >= pursuit_x - FB_PURSUIT_SNAP_TOLERANCE_M:
        return False
    defender.target_x = pursuit_x
    defender.target_y = pursuit_y
    return True


# ─────────────────────────────────────────────────────────────────────
# ANCHOR GEOMETRY
# ─────────────────────────────────────────────────────────────────────

def compute_fullback_anchor(
    ball,
    defensive_line_x: float,
    in_kickoff: bool = False,
    formation_line_x: float | None = None,
    side_sign: int = 1,
    open_play: bool = True,
    flight_lane_y: float | None = None,
    flight_depth_ref: tuple | None = None,
    defender_x: float | None = None,
):
    """Return (anchor_x, anchor_y) for the fullback this frame.

    Args:
        ball: The ANCHOR REFERENCE.  In open play it is the LIVE BALL
            CARRIER (``world.ball_carrier``): the lateral anchor mirrors
            that lane so the last defender slides across the field with
            the play.  With no carrier it is the loose ball, which uses
            the fractional sweep weight instead.  During KICKOFF (ball in
            flight) the kickoff override pins anchor_y from ``side_sign``
            only.
        defensive_line_x: The defensive line X (usually ``ruck_x + 10``).
        in_kickoff: True during the KICKOFF phase (ball in flight / not
            yet caught) → the kickoff anchor (flat line + side offset).
        formation_line_x: The kickoff FLAT FORMATION LINE (halfway + 2).
            When provided it is the anchor X source; otherwise the
            caller's defensive_line_x is used (backward compatible with
            existing kickoff call sites that pass the line directly).
        flight_depth_ref: ``(reference_x, reference_y)`` of the live ball
            during a kickoff flight.  When supplied (and
            ``FB_FLIGHT_DEPTH_TRACK`` is on) the airborne anchor X becomes
            the NORMAL DEPTH-ROLE X referenced to that point -- the same
            law open play applies -- so the sweeper can use its depth
            while the ball is in the air.  ``None`` (pre-kick, stub world)
            keeps the historical constant.
        defender_x: The actor's live X, used as the AIRBORNE
            ANTI-RETREAT CLAMP: the anchor may not be commanded BEHIND the
            actor, because at the strike the ball is too close for the
            depth formula to be a real demand (it would ask for ~38 m
            backwards).  Self-releasing -- see FB_FLIGHT_DEPTH_TRACK.
        side_sign: +1/-1 — the kick side.  anchor_y =
            FIELD_CENTRE_Y + side_sign * KICKOFF_FB_LATERAL_OFFSET_M.
        open_play: True (default) when a LIVE BALL CARRIER is the anchor
            reference: the lateral destination MIRRORS the carrier's lane
            (``FB_LATERAL_TRACK_OPEN_PLAY``) so the FB actually moves off
            the Y rail.  False (loose ball / no carrier) keeps the
            fractional ``LATERAL_TRACK`` sweep toward the reference.

    Returns:
        (anchor_x, anchor_y) as floats, always inside the field bounds.
    """
    if in_kickoff:
        anchor_x = defensive_line_x
        if formation_line_x is not None:
            anchor_x = formation_line_x
        side = 1 if side_sign >= 0 else -1
        anchor_y = FIELD_CENTRE_Y + side * KICKOFF_FB_LATERAL_OFFSET_M
        # KICKOFF FLIGHT LANE TRACKING (2026-09-27): with a LIVE lane
        # reference the airborne anchor MIRRORS that lane at the same
        # open-play weight the sweeper uses with a ball carrier, so the last
        # defender starts sliding across the field the moment the ball is
        # kicked instead of standing still until the catch.  The reference
        # is the lane the ball is travelling through, supplied by
        # ``_kickoff_flight_anchor``; with no reference (or with
        # ``RLM_FB_FLIGHT_TRACK`` disarmed) the historical constant side
        # offset is returned byte-for-byte.
        #
        # Depth is deliberately NOT tracked: anchor_x stays the flat
        # formation line because the sweeper's job during a kick is to
        # cover the LANDING LANE, not to abandon the line.
        if flight_lane_y is not None and FB_FLIGHT_TRACK:
            anchor_y = FIELD_CENTRE_Y + (
                float(flight_lane_y) - FIELD_CENTRE_Y
            ) * _lateral_track_weight(True, anchor_x)

        # ── DEPTH-ROLE X (2026-09-28) ──
        # The airborne anchor X is the SAME depth-role law open play
        # applies, referenced to the live ball rather than a ruck, so the
        # last defender uses its depth from the moment the ball is struck.
        # The reference line is derived here (reference_x + 10) so this
        # function stays the single source and no caller has to thread a
        # second X through.  Without a usable reference (pre-kick, stub
        # world) the historical constant line is kept byte-for-byte.
        if flight_depth_ref is not None and FB_FLIGHT_DEPTH_TRACK:
            _ref_x = flight_depth_ref[0]
            if _ref_x is not None:
                anchor_x = depth_role_anchor_x(
                    float(_ref_x),
                    float(_ref_x) + KICKOFF_LINE_ANCHOR_DEPTH_M,
                )
                # ANTI-RETREAT CLAMP: never command the actor behind
                # itself.  Self-releasing once the depth role comes in
                # front of it (see FB_FLIGHT_DEPTH_TRACK).
                if defender_x is not None:
                    anchor_x = min(anchor_x, float(defender_x))
        return _clamp_anchor(anchor_x, anchor_y)

    # Depth scales with ruck_x / field position.
    ruck_x = getattr(ball, "x", defensive_line_x - 10.0)
    if ruck_x is None:
        ruck_x = defensive_line_x - 10.0
    # SINGLE-SOURCED DEPTH LAW (2026-09-28): the open-play anchor and the
    # kickoff-flight anchor take the identical ``depth_role_anchor_x``
    # formula, so "the depth role" is one definition, never two that can
    # drift apart.  No behaviour change here (same arithmetic).
    anchor_x = depth_role_anchor_x(ruck_x, defensive_line_x)

    # Lateral: mirror the reference lane in open play, sweep otherwise.
    anchor_y = FIELD_CENTRE_Y + (
        _reference_lane(ball, FIELD_CENTRE_Y) - FIELD_CENTRE_Y
    ) * _lateral_track_weight(open_play, anchor_x)

    return _clamp_anchor(anchor_x, anchor_y)


def depth_role_anchor_x(reference_x: float, defensive_line_x: float) -> float:
    """The DEPTH-ROLE anchor X for a ball/ruck reference at *reference_x*.

    THE SINGLE DEFINITION of the fullback depth law, shared by the
    open-play branch of ``compute_fullback_anchor`` and its kickoff-flight
    branch, so the sweeper holds the SAME depth contract in both regimes
    -- only the reference (ruck vs live ball) differs.  Depth lerps
    3..30 m behind the line by field position, exactly as the module
    docstring documents.

    Pure / total: an unreadable reference reports the shallow end via the
    clamp rather than raising.
    """
    t = (float(reference_x) - LEFT_TRYLINE_X) / (HALFWAY_X - LEFT_TRYLINE_X)
    t = max(0.0, min(1.0, t))
    depth = DEPTH_MIN_M + (DEPTH_MAX_M - DEPTH_MIN_M) * t
    return float(defensive_line_x) + depth


def compute_fullback_ruck_anchor(world, defensive_line_x: float):
    """Return the fullback's positional-depth anchor from the settled ruck.

    Used after a completed tackle while the PTB phases own the frame.  Unlike
    live open-play tracking, neither axis follows the carrier moving around
    the PTB: depth is derived from ruck X, and the normal 0.6 sweep is applied
    to ruck Y.
    """
    try:
        ruck_x = float(getattr(world, "ruck_x_m", None))
    except (TypeError, ValueError):
        ruck_x = float(defensive_line_x) - 10.0
    if not math.isfinite(ruck_x):
        ruck_x = float(defensive_line_x) - 10.0

    try:
        ruck_y = float(getattr(world, "ruck_y_m", None))
    except (TypeError, ValueError):
        ruck_y = FIELD_CENTRE_Y
    if not math.isfinite(ruck_y):
        ruck_y = FIELD_CENTRE_Y

    anchor_x = depth_role_anchor_x(ruck_x, defensive_line_x)
    anchor_y = FIELD_CENTRE_Y + (ruck_y - FIELD_CENTRE_Y) * LATERAL_TRACK
    return _clamp_anchor(anchor_x, anchor_y)


def _reference_lane(ball, default_y: float) -> float:
    """The reference lane Y for the lateral anchor (never None)."""
    lane_y = getattr(ball, "y", default_y)
    if lane_y is None:
        return float(default_y)
    return float(lane_y)


def fullback_lateral_zone(anchor_x) -> int:
    """The 4-zone matrix index (0..3) of a fullback anchor X.

    The zones tile the legal anchor strip with no gap and no overlap
    (``FB_LATERAL_ZONE_EDGES_M``), inclusive-low / exclusive-high, so a
    position EXACTLY on a boundary belongs to the zone above it.

    TOTAL, never raises: an unreadable or non-finite position reports the
    FARTHEST zone, which is the ordinary-backfield default, rather than
    inventing a try-line position the caller did not ask for.
    """
    try:
        x = float(anchor_x)
    except (TypeError, ValueError):
        return FB_LATERAL_ZONE_COUNT - 1
    if not math.isfinite(x):
        return FB_LATERAL_ZONE_COUNT - 1
    for index in range(FB_LATERAL_ZONE_COUNT):
        if x < FB_LATERAL_ZONE_EDGES_M[index + 1]:
            return index
    return FB_LATERAL_ZONE_COUNT - 1


def fullback_lateral_zone_weight(anchor_x) -> float:
    """The declared lateral weight for *anchor_x*'s zone, ENFORCEMENT APPLIED.

    THE CRITICAL LATERAL SWEEPER LAW lives here.  Zone 0 is the try-line
    zone: with the ball that deep the last defender is the only body
    between the carrier and the line, so its lateral destination must track
    the carrier's lane at FULL weight.  The zone's declared value is
    therefore FLOORED at ``FB_LATERAL_ZONE_FLOOR`` -- no declared weight, no
    future tuner and no mis-declaration can leave the actor short of the
    lane in the try-line zone, while a value ABOVE the floor is honoured so
    a future stage may over-sweep.

    Zones 1-3 are the FLUID half of the matrix: their declared weight is
    returned exactly, so a user tuner can dial them toward the loose-ball
    rail without ever touching the sweeper law.

    Pure / side-effect free.
    """
    zone = fullback_lateral_zone(anchor_x)
    declared = float(FB_LATERAL_ZONE_WEIGHTS[zone])
    if zone == FB_LATERAL_ZONE_TRYLINE:
        return max(declared, FB_LATERAL_ZONE_FLOOR)
    return declared


def _lateral_track_weight(open_play: bool, anchor_x=None) -> float:
    """Lateral anchor weight: the open-play zone weight, else the sweep.

    ``open_play`` selects the mirroring branch so the last defender's
    destination follows the ball carrier across the field.  When *anchor_x*
    is supplied the weight comes from the 4-ZONE LATERAL SWEEPER MATRIX
    (``fullback_lateral_zone_weight``), whose try-line zone ENFORCES the
    critical sweeper law; zones 1-3 return their declared weight, which
    ships at the same full weight the single rail applied, so the values
    are the SAME number today.

    Called WITHOUT *anchor_x* (the historical one-argument form) the matrix
    is not consulted and the shipped open-play weight is returned, so every
    pre-existing caller keeps its exact behaviour.

    The rollback lever ``RLM_FB_LATERAL_OPEN_PLAY=0`` restores the
    historical 0.6 rail for A-B diagnosis without a code revert, and it
    DISARMS THE MATRIX TOO: the rollback is an unconditional 0.6 no matter
    what any zone declares, so the A-B baseline can never be quietly
    overridden by a zone weight.
    """
    if open_play and FB_LATERAL_OPEN_PLAY:
        if anchor_x is None:
            return FB_LATERAL_TRACK_OPEN_PLAY
        return fullback_lateral_zone_weight(anchor_x)
    return LATERAL_TRACK


def _clamp_anchor(anchor_x: float, anchor_y: float):
    """Clamp the fullback anchor to the legal field strip (10..112 X).

    The fullback is allowed to sit just into-goal beyond the right tryline,
    so the upper X clamp is 112 rather than 110.  The rest of the geometry
    remains unchanged.
    """
    anchor_x = max(LEFT_TRYLINE_X, min(FIELD_MAX_X, anchor_x))
    anchor_y = max(0.0, min(70.0, anchor_y))
    return anchor_x, anchor_y


# ─────────────────────────────────────────────────────────────────────
# FULLBACK GEOMETRY AUTHORITY (MODULE 1, 2026-08-13)
# ─────────────────────────────────────────────────────────────────────
# Authoritative last-writer for the fullback's X/Y geometry.  Runs AFTER
# every defensive line writer (spacing, width, retreat, structured-close,
# depth roles, MMDL, anchor enforcement) so the fullback's deep backfield
# anchor can NEVER be overwritten by the line pipeline — the fullback
# cannot collapse into the defensive line.
#
# Design:
#   - Self-gates on ``fullback_role_active``: line defenders are
#     untouched (zero behavioural change for the line).
#   - X/Y come from the SAME canonical geometry source as the rest of
#     this module (``compute_fullback_anchor``) — the depth formula is
#     field-position lerped 3..30 m and lateral tracking is 60% toward
#     the ball carrier, exactly as documented in the module docstring.
#   - Forces ``MovementState.DRIFT`` so OPE's target stabiliser stays ON
#     for the actor (DRIFT is not in NON_STABILISED_MOVEMENT_STATES) and
#     movement speed stays at the drifty ~0.4x line pace — the actor
#     glides home, never bursts.
#   - NEVER sets ``_mmdl_moved``: OpenPlayEngine._update_defence() still
#     runs Player.update(dt) for the actor, integrating the (stabilised)
#     anchor exactly like today — only the target survives.
#   - Persistent re-assert: runs every OPEN_PLAY / PTB / tackle-phase
#     defence frame, so even a writer that re-classified the actor
#     (frozen / movement_state / target pin) is corrected next frame.

def _kickoff_ball_is_kicked(world) -> bool:
    """True once the kickoff ball has actually been struck.

    THE GATE FOR FLIGHT TRACKING.  ``Ball.kick()`` sets ``kicked = True``
    and ``Ball.spawn_kickoff()`` clears it, so this is the exact "the ball
    has been kicked" predicate: the sweeper starts tracking the moment the
    ball is kicked and NOT before, because the pre-kick formation slot is a
    PLACED position rather than a tracking target.

    A world with no readable ball reports False, which keeps every stub and
    verifier world on the historical constant anchor.
    """
    ball = getattr(world, "ball", None) if world is not None else None
    return bool(getattr(ball, "kicked", False))


def _finite_flight_lane(value):
    """Coerce *value* to a finite in-field lane Y, or None when unusable.

    The kickoff tracking reference must never be trusted blindly: an unset
    world field (``kickoff_landing_y`` defaults to 0.0) or a NaN must be
    REJECTED rather than mirrored, or the sweeper would be commanded onto a
    touchline that no kick is going to.
    """
    try:
        lane = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lane):
        return None
    # STRICT in-field test.  The bounds are the field strip, and the
    # comparison is deliberately strict at BOTH ends because a lane exactly
    # on 0.0 is the ``world.kickoff_landing_y`` UNSET DEFAULT, not a
    # destination: a ball never lands exactly on a touchline, but an
    # unset field is 0.0 on every world.  An inclusive test would mirror
    # the unset default and command the sweeper onto the sideline.
    if not (FB_FLIGHT_LANE_MIN_Y < lane < FB_FLIGHT_LANE_MAX_Y):
        return None
    return lane


def _kickoff_flight_lane_y(world):
    """The live lane the airborne fullback tracks: the ball's own Y.

    KICKOFF FLIGHT LANE TRACKING (2026-09-27).  The in-flight anchor used to
    be a CONSTANT, so the sweeper had nothing to close and stood still for
    the whole ball flight.  The tracking reference is the ball itself:

      1. the live in-flight ball (``Ball.y_m``) -- the lane the ball is
         actually travelling through, which moves from the first post-kick
         frame.  NOTE: ``Ball`` exposes ``x_m``/``y_m``, NOT the ``x``/``y``
         properties ``_reference_lane`` reads on a ball CARRIER, so the
         attribute is read explicitly;
      2. the published kickoff landing lane (``world.kickoff_landing_y``)
         when the ball is unreadable, so the defender still covers where the
         kick is going;
      3. ``None`` -- no usable reference (a stub world, or a pre-kick setup
         where the landing lane is still the 0.0 default), in which case
         ``compute_fullback_anchor`` keeps the historical constant side
         offset and the engine behaves exactly as before.

    Pure / total: never raises, never returns a non-finite value, and never
    returns a lane outside the field strip.
    """
    if not _kickoff_ball_is_kicked(world):
        return None
    ball = getattr(world, "ball", None)
    if ball is not None:
        lane = _finite_flight_lane(getattr(ball, "y_m", None))
        if lane is not None:
            return lane
    return _finite_flight_lane(getattr(world, "kickoff_landing_y", None))


def _kickoff_flight_depth_ref(world):
    """The live-ball depth reference for the airborne depth role.

    Returns ``(ball.x_m, ball.y_m)`` once the ball has actually been
    kicked, else ``None``.  Gated on the SAME ``_kickoff_ball_is_kicked``
    predicate as the lateral tracking, so pre-kick frames keep the PLACED
    formation slot (contract C1) and the placement stamp the kickoff
    engine consumes is unchanged.

    ``Ball`` exposes ``x_m``/``y_m``, NOT the ``x``/``y`` carrier
    attributes ``compute_fullback_anchor``'s open-play branch reads, so the
    coordinates are read explicitly here -- passing the raw ``Ball`` would
    silently fall back to defaults and the anchor would not move at all.

    Pure / total: an unreadable or non-finite coordinate reports ``None``,
    which keeps the historical constant.
    """
    if not _kickoff_ball_is_kicked(world):
        return None
    ball = getattr(world, "ball", None)
    if ball is None:
        return None
    try:
        ref_x = float(getattr(ball, "x_m", None))
        ref_y = float(getattr(ball, "y_m", None))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(ref_x) and math.isfinite(ref_y)):
        return None
    return ref_x, ref_y


def _kickoff_flight_anchor(world, defender):
    """The KICKOFF-flight anchor: depth role on X and Y, or the constant.

    Single-sourced through ``compute_fullback_anchor(in_kickoff=True)`` so
    the geometry authority and ``KickoffEngine._assign_kickoff_fullback``
    can never disagree about the in-flight anchor.  Reads the FLIGHT
    SOURCES published by ``KickoffEngine.reset_for_kickoff`` /
    ``apply_kicking_team_formation`` (``kickoff_engine_line_x`` +
    ``kickoff_side_sign``).

    DEPTH-ROLE X (2026-09-28): once the ball has been kicked the anchor X
    is the normal depth role, referenced to the live ball, clamped so it
    can never command the actor behind itself (see
    ``FB_FLIGHT_DEPTH_TRACK``).  Before the strike -- and for a stub world
    with no readable ball -- this returns the historical constant.
    """
    line_x = getattr(world, "kickoff_engine_line_x", None)
    side_sign = getattr(world, "kickoff_side_sign", 1)
    try:
        side_sign = int(side_sign)
    except (TypeError, ValueError):
        side_sign = 1
    if line_x is None:
        line_x = getattr(defender, "x", 62.0) or 62.0
    line_x = float(line_x)
    return compute_fullback_anchor(
        None,
        line_x,
        True,
        formation_line_x=line_x,
        side_sign=side_sign,
        flight_lane_y=_kickoff_flight_lane_y(world),
        flight_depth_ref=_kickoff_flight_depth_ref(world),
        defender_x=getattr(defender, "x", None),
    )


def fullback_tackle_side_owner_live(d) -> bool:
    """True when a tackle-side system is ACTUALLY driving *d* this frame.

    A tagged fullback remains owned by its geometry controller through the
    emergency candidate and classifier stages. Only TES arming or physical
    attachment transfers movement ownership; selector and pre-tackle stamps
    are not authority signals for the fullback.

    This predicate draws the distinction the geometry authority needs.  True
    only for the stamps that denote a system owning the body's position,
    movement state and speed ON THIS FRAME:

      - ``_fb_emergency_engaged``: armed as a TES tackler; TES owns position,
      - ``tes_attached`` / ``in_contact``: physically pinned to the carrier.

    Reads no line-membership field and never mutates anything.
    """
    if d is None:
        return False
    if getattr(d, "fullback_role_active", False):
        return bool(
            is_fullback_emergency_engaged(d)
            or getattr(d, "tes_attached", False)
            or getattr(d, "in_contact", False)
        )
    if getattr(d, "_tackle_commit_active", False):
        return True
    if is_fullback_emergency_engaged(d):
        return True
    if getattr(d, "pre_tackle_stance", False):
        return True
    if getattr(d, "tes_attached", False):
        return True
    return bool(getattr(d, "in_contact", False))


def establish_fullback_live_break_chase(world, defender) -> bool:
    """Complete a live-break CHASE handover on the geometry yield path.

    THE LOCOMOTION GAP THIS CLOSES (2026-09-26)
    -------------------------------------------
    The emergency-defender yield hands the body to the tackle-side writers,
    and ``clear_static_pursuit_target`` exists so that a yielding actor never
    holds a STATIC pin: it advances the command to the goal-side pursuit
    point.  That clearance is a TARGET writer only -- by design it never
    touches the movement state or the speed -- and it also declines whenever
    the held command is ALREADY a live chase point.

    The live telemetry of a beaten fullback is exactly that declined case.
    After a lost tackle the command is already a live trajectory chase point
    (so the clearance politely declines on every frame), the state is CHASE,
    and ``_target_writer`` is this module -- while the SPEED is never written
    by anyone, so the body coasts on the last sweeper value it inherited.
    Measured over 11 557 post-loss frames with a genuine DEPTH break live:
    ``move_speed`` mean 1.245 m/s against a carrier at 4.404 m/s, and the
    defender -> carrier gap GROWING 2.195 -> 6.400 m.

    A pursuit POINT without a pursuit SPEED is not ownership.  This helper
    completes the handover whenever the geometry authority is about to yield
    on a genuine break while no tackle-side system actually holds the body
    (``fullback_tackle_side_owner_live``): it asserts the full pursuit
    contract -- point, source, writer, state, freeze and the governed
    defensive ceiling -- so the last defender can run the break down.

    Contract:
      - honours the ``RLM_FB_STATIC_PURSUIT_CLEAR`` A/B lever exactly like
        the static-pin clearance it complements (lever off => the historical
        yield, byte-for-byte),
      - never touches a body a tackle-side system genuinely owns,
      - never touches a command belonging to another writer (outside this
        module's own clamped envelope),
      - only a tagged fullback is considered.

    Returns True when the pursuit contract was asserted.
    """
    if not FB_STATIC_PURSUIT_CLEAR:
        return False
    if defender is None or not getattr(defender, "fullback_role_active", False):
        return False
    if fullback_tackle_side_owner_live(defender):
        return False
    # Anything outside this module's own command envelope belongs to another
    # writer, which still owns it: the yield remains the correct answer.
    if not is_fullback_command_target(getattr(defender, "target_x", None)):
        return False
    bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return False
    # THE BREAK LATCH (2026-09-28): the admission must read the SAME two
    # branches the emergency-candidacy gate reads (``fullback_depth_break_
    # active`` / ``fullback_lateral_break_active``), both of which go through
    # ``break_test_line_anchor_x``.  Gating on the LIVE-anchor
    # ``line_break_active`` here left the establishment path declining on a
    # body the candidacy gate had just ADMITTED -- admitted to contest a
    # break this writer would then refuse to chase.  With no latch armed the
    # two tests are identical, so the historical behaviour is unchanged.
    if not (
        fullback_depth_break_active(world, defender, bc)
        or fullback_lateral_break_active(world, defender, bc)
    ):
        return False

    line_x = float(line_anchor_x(world))
    anchor_x, anchor_y = compute_fullback_anchor(bc, line_x)
    # THE BREAK TEST READS THE LATCHED ANCHOR (2026-09-28): this handover
    # completes on the SAME verdict the geometry writer's breakaway branch
    # uses, so it reads the same single reader -- and it ARMS the latch on
    # success, so a handover established here is protected against a forward
    # relock exactly like one established by the sweep.
    _break_test_anchor = break_test_line_anchor_x(world, defender, line_x)
    break_kind = classify_fullback_break(
        bc, defender, _break_test_anchor, anchor_y
    )
    if break_kind == FB_BREAK_NONE:
        return False
    arm_fullback_break_latch(defender, break_kind, _break_test_anchor)

    # Same 2D pursuit geometry as the breakaway-chase branch -- literally the
    # same law, ``fullback_chase_point``, so the handover and the sweep can
    # never disagree about the pursuit point.  A DEPTH break takes the
    # intercept point on the carrier's own path; a LATERAL break keeps the
    # sweeper's own depth floor and never sits behind the goal-side point.
    _chase_x, _chase_y = fullback_chase_point(
        bc, defender, anchor_x, break_kind
    )
    defender.target_x = _chase_x
    defender.target_y = _chase_y
    defender.target_source = "fullback_geometry"
    defender._target_writer = "fullback_geometry"
    defender.frozen = False

    # INTENT OWNERSHIP (Stage 1): the live-break handover states an INTENT,
    # not a bare movement state.  The controller is the only writer of a
    # tagged fullback's movement_state; it resolves CHASE -> the same
    # MovementState.CHASE singleton this line always wrote, and records why.
    from game.defence.defence_lvl_1.fullback_controller import (
        FullbackIntent,
        enter_fullback_intent,
        SOURCE_LIVE_BREAK_HANDOVER,
    )
    enter_fullback_intent(
        world,
        defender,
        FullbackIntent.CHASE,
        source=SOURCE_LIVE_BREAK_HANDOVER,
    )

    # SPEED AUTHORITY REMOVED (2026-09-27).  This used to write the governed
    # ceiling straight into ``move_speed``.  The pursuer is now a CHASE body
    # like any other, so the shared movement-state model derives its pace from
    # MAX_SPEED_BY_STATE[CHASE] x max_speed_mps and Player.update's post-facing
    # ``max_defensive_speed_mps`` clamp bounds it at the same governed ceiling.
    # What remains here is the DECISION half of the contract -- the pursuit
    # target, the source stamp and the CHASE state -- which is the fullback's
    # own and stays its own.
    return True


def apply_fullback_geometry(world, defender, kickoff_flight: bool = False,
                            post_tackle_ruck_anchor: bool = False):
    """Authoritative fullback geometry writer.

    Runs AFTER all defensive line writers and ensures the fullback sits
    at the correct depth (X) and lateral tracking (Y) every frame,
    regardless of line behaviour.  No-op unless *defender* is the active
    fullback actor.

    Reads:
        world.defensive_line_anchor   — the line anchor (ruck_x + 10)
        world.ball_carrier           — lateral tracking reference
        world.ruck_x_m               — anchor fallback when unpublished

    Writes (fullback actor only):
        target_x / target_y          — the authoritative deep anchor
        target_source                — "fullback_geometry"
        movement_state               — MovementState.DRIFT (or RECOVER when
                                       the engine has armed recovery and the
                                       actor owes ground; CHASE on a break)
        move_speed                   — fullback approach-speed contract, or
                                       the ALIGNED recovery pace
                                       (FB_RECOVER_SPEED_SCALE x the ceiling)
                                       under RECOVER, sweeper cap lifted; the
                                       governed ceiling only with granted
                                       tackle authority
        frozen                       — False (never frozen by line logic)

    KICKOFF FLIGHT (2026-09-21): with ``kickoff_flight=True`` the writer runs
    DURING the ball's airborne flight and owns the in-flight anchor through
    ``_kickoff_flight_anchor`` (the same (formation_line_x,
    centre + side_sign * 3) geometry ``assign_fullback_role`` stamps).  This
    is the explicit flight call made by ``KickoffEngine.update`` so the
    airborne FB is owned by the geometry authority, not by the kickoff
    engine's ad-hoc writer.  Callers that do NOT opt in keep the historical
    early return, so a normal-play dispatch can never ball-derive (and thus
    clobber) the kickoff anchor mid-flight.
    """
    if not getattr(defender, "fullback_role_active", False):
        return

    # ── POST-TACKLE RETURN INTERRUPTION (2026-10-02) ──
    # A higher-priority owner taking the body RELEASES any live post-tackle
    # return window BEFORE the early yields below can swallow the frame
    # (a fresh emergency engagement, TES attachment / contact, or the
    # kickoff flight).  Without this the window survived such an
    # interruption and mis-anchored the body on the frame the other owner
    # released it -- the same "never clears" defect, one doorway over.
    if is_fullback_post_tackle_return_active(defender) and (
        fullback_tackle_authority_active(defender)
        or getattr(defender, "tes_attached", False)
        or getattr(defender, "in_contact", False)
        or getattr(world, "in_kickoff", False)
    ):
        clear_fullback_post_tackle_return(defender, reason="interruption")

    # ── KICKOFF ANCHOR PRESERVATION (2026-08-27) ──
    # During the kickoff flight (world.in_kickoff True) the FB anchor is the
    # KICKOFF override (formation_line_x, centre + side_sign * 3.0) stamped by
    # assign_fullback_role(in_kickoff=True).  The defence dispatch still runs
    # apply_fullback_geometry every frame (ope._phase stays OPEN_PLAY), so a
    # normal-play recompute from the (absent) ball carrier would clobber the
    # kickoff anchor with a ball-derived value (the "anchor drifted to 38.25
    # from 65.0" kickoff-verifier leak).  Skip the geometry authority until
    # the ball is secured (in_kickoff False), preserving the kickoff anchor.
    #
    # EXPLICIT FLIGHT MODE (2026-09-21): KickoffEngine.update() calls this
    # writer with kickoff_flight=True on every IN_AIR / CHASE frame, so the
    # airborne fullback's target, source, state and speed are owned by the
    # SAME authority that owns them in every other phase.  The anchor is the
    # kickoff anchor (constant through the flight) and no ball-derived depth
    # is ever computed while in_kickoff is true.
    if getattr(world, "in_kickoff", False):
        if not kickoff_flight:
            return
        _flight_x, _flight_y = _kickoff_flight_anchor(world, defender)
        defender.target_x = _flight_x
        defender.target_y = _flight_y
        defender.target_source = "fullback_geometry"
        defender._target_writer = "fullback_geometry"
        defender.frozen = False
        # INTENT OWNERSHIP (Stage 1): the airborne contract is FLIGHT, and the
        # controller resolves it to the same MovementState.DRIFT singleton this
        # branch always wrote.
        from game.defence.defence_lvl_1.fullback_controller import (
            FullbackIntent,
            enter_fullback_intent,
            SOURCE_FLIGHT,
        )
        enter_fullback_intent(
            world,
            defender,
            FullbackIntent.FLIGHT,
            source=SOURCE_FLIGHT,
        )
        # SPEED AUTHORITY REMOVED (2026-09-27): this used to write
        # ``move_speed = fullback_approach_speed(base, dist)`` -- the FB-only
        # 2.0 m/s sweeper cap.  The airborne actor now integrates through the
        # SHARED movement-state model (apply_fullback_locomotion), which
        # derives its speed from MAX_SPEED_BY_STATE[DRIFT] x max_speed_mps
        # exactly as every other defender's is.  The STATE stamp above is the
        # whole of the writer's locomotion contract now: the fullback decides
        # (DRIFT here), the shared model moves it.
        return

    # ── TES OWNERSHIP SAFETY (2026-08-13) ──
    # Never unpin a TES-owned / attached actor.  The fullback is excluded
    # from tackler selection by construction, so these guards cover
    # stale-flag corner cases only (a re-classified actor mid-tackle).  TES
    # owns position while attached/in-contact; unpinning would tear the
    # pile geometry.
    if getattr(defender, "tes_attached", False):
        return
    if getattr(defender, "in_contact", False):
        return

    if (
        getattr(defender, "fullback_role_active", False)
        and not is_fullback_emergency_engaged(defender)
    ):
        # Pre-TES commit/stance stamps cannot own fullback locomotion.
        defender._tackle_commit_active = False
        defender.pre_tackle_stance = False

    # ── TACKLER-COMMIT WINS (2026-08-13) ──
    # A committed PRIMARY/SECONDARY tackler engages the BC this frame —
    # the tackle-commit writer owns its target.  Geometry authority yields
    # to the collision point so the tackler keeps closing on the carrier.
    if (
        not getattr(defender, "fullback_role_active", False)
        and getattr(defender, "_tackle_commit_active", False)
    ):
        return

    # ── STANDALONE FB ENGAGE (2026-10) ──
    # Break-chase candidacy and selector/pre-tackle stamps do not transfer
    # movement ownership. The geometry controller continues to own the
    # fullback until _arm_tackler_role marks an actual TES engagement.
    release_stale_fullback_lane_stamp(world, defender)
    if fullback_tackle_authority_active(defender):
        # ── LIVE-BREAK CHASE OWNERSHIP (2026-09-26) ──
        # The yield below exists so the commit / pursuit / corridor writers
        # can own the body while they are genuinely driving it.  It keys on
        # ``fullback_tackle_authority_active``, which is a STAMP test, and a
        # lane stamp OUTLIVES the tackle attempt that created it: the break
        # gate keeps it alive (``release_stale_fullback_lane_stamp`` only
        # drops it once the break decays), so a BEATEN fullback goes on
        # yielding to writers that have already released it.  A point without
        # a speed is not ownership -- measured, the beaten body then coasts
        # at 1.245 m/s mean against a carrier at 4.404 m/s while the gap
        # grows.  Establish the live-break CHASE contract (point AND
        # locomotion) before this path can exit, unless a tackle-side system
        # genuinely owns the body.
        if establish_fullback_live_break_chase(world, defender):
            return

        # ── STATIC PURSUIT CLEARANCE (frame-1814, 2026-09-23) ──
        # Yielding target ownership must not leave the fullback holding a
        # STATIC pin.  The commit / corridor writers do not always write —
        # the carrier can run clear of the corridor — and the dispatch's
        # line_break_static_guard hard-exempts the tagged fullback, so a
        # pinned target would stand for the whole break: the X-axis
        # deadlock (telemetry: fullback static at 63.80, carrier running
        # 106.35 -> 111.49).  Clear ONLY a genuine static pin; a live chase
        # target and any out-of-band value are still handed to the
        # tackle-side owners untouched.
        if clear_static_pursuit_target(defender, world):
            from game.defence.defence_lvl_1.fullback_controller import (
                FullbackIntent,
                enter_fullback_intent,
                SOURCE_PIN_CLEARED,
            )
            enter_fullback_intent(
                world,
                defender,
                FullbackIntent.CHASE,
                source=SOURCE_PIN_CLEARED,
            )
            defender.frozen = False
            defender.target_source = "fullback_geometry"
            defender._target_writer = "fullback_geometry"
        return

    # Defensive line anchor is authoritative for depth.  TASK VII
    # (2026-08-20) — AUTHORITY-ROUTED: the anchor X must be read through
    # anchor_authority.defensive_line_anchor_m (ruck_x + 10, locked-
    # published fallback), never the bare world field.  The authority
    # itself performs the unpublished-anchor fallback (published anchor
    # -> ruck_x + 10 -> DEFAULT_RUCK_X + ANCHOR_DEPTH_M), so the inline
    # fallback below is replaced wholesale by the single reader.
    from game.defence.defence_lvl_1.anchor_authority import (
        defensive_line_anchor_m as _anchor_m,
    )
    line_anchor_x = _anchor_m(world)
    if not line_anchor_x or line_anchor_x <= 0.0:
        # Unreachable in practice — the authority always returns > 0
        # (locked/published ruck_x + 10, else DEFAULT_RUCK_X +
        # ANCHOR_DEPTH_M = 70.0).  Defensive fallback kept so a stub
        # world can never feed a non-positive line anchor downstream.
        line_anchor_x = 60.0

    bc = getattr(world, "ball_carrier", None)

    # Canonical fullback anchor: depth lerps 3..30 m behind the line by
    # field position; lateral tracks the live carrier during open play.
    anchor_x, anchor_y = compute_fullback_anchor(bc, float(line_anchor_x))
    # ── POST-TACKLE RETURN LIFECYCLE GATE (2026-10-02) ──
    # THE WINDOW IS CONSUMED HERE.  An armed post-tackle return either OWNS
    # the anchor (the whole point of the mechanism) or is RELEASED by this
    # writer -- on COMPLETION (the body has reached the point it was sent to)
    # or on a HIGHER-PRIORITY INTERRUPTION -- and the normal ball-derived
    # geometry resumes from this frame.  Before this gate the flag was never
    # cleared anywhere, so the ruck-relative anchor stayed authoritative for
    # the REST OF THE MATCH and the sweeper never tracked the live carrier
    # again.
    #
    # The explicit ``post_tackle_ruck_anchor=True`` argument (the PTB-era
    # re-assert) is NOT gated: it is a caller's deliberate request for the
    # ruck anchor and keeps working exactly as before.  Only the FLAG-driven
    # case has a lifetime, because only the flag was ever armed.
    _return_use_ruck = bool(post_tackle_ruck_anchor)
    if is_fullback_post_tackle_return_active(defender):
        if fullback_post_tackle_return_live(
            world, defender, anchor_x, anchor_y
        ):
            _return_use_ruck = True
        else:
            # The live test uses the same point the writer is about to
            # command, so a completion is judged against the commanded anchor;
            # the label is diagnostic only.
            clear_fullback_post_tackle_return(
                defender,
                reason=fullback_post_tackle_return_release_reason(
                    defender, anchor_x, anchor_y
                ),
            )
    if _return_use_ruck:
        anchor_x, anchor_y = compute_fullback_ruck_anchor(
            world, float(line_anchor_x)
        )
        if is_fullback_post_tackle_return_active(defender):
            record_fullback_return_target(defender, anchor_x, anchor_y)




    # ── STAGE 3E: ANCHOR TIMING STABILITY (2026-08-25) ──
    # Per-frame memoisation + sub-threshold hysteresis.
    #
    # IDEMPOTENCE: the writer is invoked from MULTIPLE Module-1 call
    # sites inside one frame (OPEN_PLAY dispatch AND the TES-drive
    # re-assert).  Both must produce byte-identical targets for a given
    # frame so last-writer ordering can never inject a mid-frame anchor
    # hop (a second call recomputing from shifted inputs would).  A same-
    # frame repeat replays the memoised anchor exactly.
    #
    # HYSTERESIS: on a NEW frame, an anchor that drifted less than
    # FB_ANCHOR_HYSTERESIS_M per axis keeps the previous committed value,
    # so the deep backfield glide stays smooth while the BC / ruck
    # references creep.  Real snaps (kickoff-catch re-anchor) exceed the
    # band and pass through unchanged.
    _frame_now = getattr(world, "frame_count", None)
    _memo_frame = getattr(defender, "_fb_anchor_frame", None)
    _memo_x = getattr(defender, "_fb_anchor_hyst_x", None)
    if _memo_frame == _frame_now and isinstance(_memo_x, float):
        anchor_x = _memo_x
        anchor_y = getattr(
            defender, "_fb_anchor_hyst_y", anchor_y
        )
    else:
        _prev_ax = getattr(defender, "_fb_committed_anchor_x", None)
        _prev_ay = getattr(defender, "_fb_committed_anchor_y", None)
        if (
            isinstance(_prev_ax, float)
            and isinstance(_prev_ay, float)
            and abs(anchor_x - _prev_ax) < FB_ANCHOR_HYSTERESIS_M
            and abs(anchor_y - _prev_ay) < FB_ANCHOR_HYSTERESIS_M
        ):
            anchor_x = _prev_ax
            anchor_y = _prev_ay
    defender._fb_anchor_frame = _frame_now
    defender._fb_anchor_hyst_x = anchor_x
    defender._fb_anchor_hyst_y = anchor_y
    defender._fb_committed_anchor_x = anchor_x
    defender._fb_committed_anchor_y = anchor_y

    # -- ANCHOR-RELOCK DETECTION (2026-09-28) --
    # Capture the mode the actor HELD coming into this frame, and whether the
    # authoritative line anchor moved FORWARD across it.  The sweeper branch
    # below writes DRIFT unconditionally, so without these two readings a
    # chase that a relock withdrew is indistinguishable from an ordinary
    # sweeper frame -- which is exactly how the frame-383 MODE_OVERWRITE
    # handover stayed silent (no engine call, no engagement reset).
    _prev_mode_name = movement_state_name(defender)
    _prev_line_anchor = getattr(defender, "_fb_prev_line_anchor_x", None)
    try:
        _live_line_anchor = float(line_anchor_x)
    except (TypeError, ValueError):
        _live_line_anchor = None
    _anchor_relocked_forward = bool(
        isinstance(_prev_line_anchor, (int, float))
        and _live_line_anchor is not None
        and _live_line_anchor
        > float(_prev_line_anchor) + FB_ANCHOR_RELOCK_EPS_M
    )
    # Stored ONLY under the armed lever, so the DISARMED contract stays
    # write-free as well as behaviour-identical: with no stored value the
    # relock test above is False forever and the handover backstop below can
    # never fire, which is exactly the historical engine.
    if fullback_break_latch_enabled() and _live_line_anchor is not None:
        defender._fb_prev_line_anchor_x = _live_line_anchor

    defender.target_x = anchor_x
    defender.target_y = anchor_y
    defender.target_source = "fullback_geometry"
    defender._target_writer = "fullback_geometry"
    defender.frozen = False

    # DRIFT keeps the stabiliser engaged and the approach speed capped at
    # ~0.4x — the documented fullback movement contract.
    from player.movement_states import MovementState
    # INTENT OWNERSHIP (Stage 1): the sweeper contract is an INTENT.  The
    # controller resolves SWEEP -> the same MovementState.DRIFT singleton this
    # line always wrote, and records the anchor as the reason.
    from game.defence.defence_lvl_1.fullback_controller import (
        FullbackIntent,
        enter_fullback_intent,
        SOURCE_ANCHOR,
    )
    enter_fullback_intent(
        world,
        defender,
        FullbackIntent.SWEEP,
        source=SOURCE_ANCHOR,
    )

    # ── BREAKAWAY CHASE OVERRIDE (2026-09-03) ──
    # The FB "never detects the BC to chase/tackle" because it was hard-
    # locked to DRIFT in every phase.  When the ball carrier is clearly
    # past the defensive line (line break), the last defender must
    # transition to CHASE and pursue the carrier goal-side.  This is the
    # single movement-state overwrite that was leaving the backfield
    # anchor passive while the line bent around it.  DRIFT is the normal
    # anchor-glide state; CHASE is the breakaway-response state.  The FB
    # is still never drafted into a TACKLER_1/2/3 role or a
    # PRIMARY/SECONDARY/THIRD/EDGE tackle_role (the line-tackler guards
    # remain); this only changes the MovementState so the FB actually
    # detects and pursues the carrier.  CHASE is excluded from the target
    # stabiliser and from smooth-damp (Player._uses_smooth_damp returns
    # False for CHASE/SHOOT), so the pursuit runs on the fast accel-lerp
    # path toward the live BC point.
    bc = getattr(world, "ball_carrier", None)
    # -- THE BREAK TEST READS THE LATCHED ANCHOR (2026-09-28) --
    # A FORWARD relock of the line anchor (the tackle-cycle ruck writes:
    # CONTACT -> DRIVE entry, the DRIVE-END lock, _prepare_release) rewrites
    # ruck_x_m onto the CARRIER and therefore moves the line anchor GOAL-SIDE
    # of a live break.  That must not retroactively un-classify a break the
    # last defender is already chasing.  ``line_anchor_x`` above stays the
    # LIVE anchor for the sweeper DEPTH geometry -- the depth role must still
    # follow the real ruck -- and only the break VERDICT reads the floored
    # value.
    _break_test_anchor = break_test_line_anchor_x(
        world, defender, line_anchor_x
    )
    break_kind = classify_fullback_break(
        bc, defender, _break_test_anchor, anchor_y
    )
    # ── THE LINE MUST EXIST TO BE BROKEN (2026-09-29) ──
    # The chase is refused unless the anchor is a line the defence actually
    # holds.  MEASURED defect this closes: a reception seeds ruck_x = bc.x, so
    # the anchor is catch_x + 10 with the whole kicking team still goal-side of
    # it; the carrier runs ~11 m and trips the DEPTH leg on a line nobody
    # occupies, and the last defender runs the carrier down.  See
    # ``fullback_defensive_line_occupied``.
    if break_kind != FB_BREAK_NONE and fullback_defensive_line_occupied(
        world, _break_test_anchor, defender
    ):
        _chase_kind = break_kind
    else:
        # ── THE LATCH-BACKED CHASE HOLD (2026-10-01) ──
        # A LIVE LATCH IS AUTHORITY FOR PURSUIT OWNERSHIP.  The guard above
        # has two conjuncts -- the pure classifier AND the occupancy gate --
        # and EITHER can refuse on a frame whose chase the latch already
        # legitimately armed.  The anchor-relock latch floors the anchor the
        # classifier reads but does NOT guarantee the VERDICT, so without
        # this the same writer falls through to the sweeper branch
        # (CHASE -> SWEEP) with no engine event.  The hold keeps the pursuit
        # contract the latch granted, commanding the LATCHED kind so the
        # chase point is the one the arming frame produced -- the EXISTING
        # pursuit command path, byte-for-byte.  Reversal classes N2/N3/N4 are
        # covered; N1 (``carrier_behind_latched_line``) and the existing
        # lifecycle clears release it (see ``fullback_chase_hold_live``).
        _chase_kind = fullback_chase_hold_kind(defender, bc)
    if _chase_kind is not None:
        arm_fullback_break_latch(defender, _chase_kind, _break_test_anchor)
        # INTENT OWNERSHIP (Stage 1): the breakaway override states CHASE and
        # names WHICH break admitted it, so the audit can read the reason off
        # the controller instead of re-deriving it from geometry.
        from game.defence.defence_lvl_1.fullback_controller import (
            FullbackIntent,
            enter_fullback_intent,
            SOURCE_BREAK_DEPTH,
            SOURCE_BREAK_LATERAL,
        )
        enter_fullback_intent(
            world,
            defender,
            FullbackIntent.CHASE,
            source=(
                SOURCE_BREAK_DEPTH
                if _chase_kind == FB_BREAK_DEPTH
                else SOURCE_BREAK_LATERAL
            ),
        )
        defender.target_source = "fullback_geometry"
        defender._target_writer = "fullback_geometry"
        defender.frozen = False
        # The bounded trajectory projection gives a DEPTH break an
        # intercept bearing toward the carrier's future XY position. A
        # LATERAL break -- outside the sweeper's channel but not yet past
        # the line -- retains the sweeper-depth floor while stepping into
        # the carrier's projected lane.
        # ── THE SINGLE CHASE POINT (2026-09-28) ──
        # The command comes from ``fullback_chase_point`` alone, so the
        # breakaway sweep, the mid-tackle handover and the static-pin
        # clearance can never disagree about the pursuit point.  That law
        # projects the BC's trajectory in XY with no trail offset, and applies
        # the LATERAL depth floor ("never abandon the backfield depth"). With
        # ``RLM_FB_CHASE_INTERCEPT=0`` it reproduces the historical X-only
        # trail verbatim, so the rollback is exact.
        _chase_x, _chase_y = fullback_chase_point(
            bc, defender, anchor_x, _chase_kind
        )
        defender.target_x = _chase_x
        defender.target_y = _chase_y
        base_speed = getattr(defender, "max_defensive_speed_mps", 0.0) or 0.0
        if base_speed <= 0.0:
            base_speed = getattr(defender, "max_speed_mps", 8.3)
        # EMERGENCY-DEFENDER SPEED (B2b, 2026-09-16): the sweep cap applies
        # ONLY to the anchor contract.  An authority-granted fullback chases
        # at the unified defensive speed like any other defender, so the
        # last defender can actually run the carrier down.
        #
        # 2D SPEED ERROR (2026-09-22): the taper is keyed on the DISTANCE to
        # the commanded pursuit point, never on the X component alone.  An
        # actor that has arrived in DEPTH while still owing metres LATERALLY
        # must not be throttled to the 10% floor -- measured on the old
        # X-only form: 0.2 m/s commanded with 8 m of lane outstanding, i.e.
        # the sweeper frozen beside the carrier it is meant to intercept.
        # CHASE SPEED (2026-09-26): the chase is NOT the sweeper anchor
        # glide, so it does not take the sweeper's 2.0 m/s approach cap.
        # The historical expression keyed on
        # ``fullback_approach_cap_applies(defender)`` -- the exact complement
        # of the authority flag -- but this branch is only reachable while
        # the authority is INACTIVE, so the predicate was ALWAYS True here
        # and ``float(base_speed)`` was dead code: every live-break chase ran
        # through ``fullback_approach_speed``, i.e. hard-clamped to
        # ``min(base, 2.0)`` and then arrival-tapered.  The last defender
        # pursued the breaking carrier at <= 2.0 m/s (measured 1.0 m/s at a
        # 3.0 m error) instead of the governed ceiling.  ``fullback_chase_speed``
        # keeps the arrival taper and the un-clamped defensive ceiling, and
        # ``RLM_FB_CHASE_SPEED=0`` restores the historical expression verbatim.
        # SPEED AUTHORITY REMOVED (2026-09-27): the breakaway chase used to
        # pick between ``fullback_chase_speed`` (un-capped ceiling + taper)
        # and the historical ``fullback_approach_speed``-vs-raw-base
        # expression.  BOTH are gone: the chase is a CHASE movement state and
        # the shared model derives its pace from
        # MAX_SPEED_BY_STATE[CHASE] x max_speed_mps, bounded by the post-facing
        # ``max_defensive_speed_mps`` ceiling in Player.update() exactly as a
        # line defender's pursuit is.  That is the parity the last defender
        # was missing -- it used to run a ceiling of its own invention.
        return

    # ── HIGH-VELOCITY RECOVERY (2026-09-21) ──
    # The actor is not chasing and nobody else owns its movement, so it is
    # travelling home to the slot it owns.  When the engine has armed the
    # recovery lever and the body genuinely owes ground, the state becomes
    # MovementState.RECOVER: the drift/backpedal dampeners are bypassed
    # (stabiliser deadzone + hysteresis + alpha lag, the critically-damped
    # smooth-damp port, and the 2.0 m/s approach cap), so the last defender
    # physically RUNS BACK to its anchor instead of measuring the last
    # twenty metres out at a fraction of the commanded speed.
    #
    # Placed AFTER the breakaway override above (a chasing actor is already
    # in CHASE and never reaches this point) and BEFORE the approach-speed
    # computation below, because ``fullback_approach_cap_applies`` reads the
    # state to decide whether the 2.0 m/s sweeper cap is in force.
    # -- THE LATCH IS TACKLE-CYCLE STATE: NO DYNAMIC SELF-RELEASE (2026-10-01) --
    # This site used to clear the latch the moment the carrier dropped behind
    # the LATCHED line.  A carrier stepping back behind a defender line is
    # ORDINARY LINE-BREAK GEOMETRY -- a stepping / doubling-back carrier, a
    # second-phase carry -- and NOT a dead-ball condition, so it must not end
    # the chase.  EMERGENCY_CHASE now persists across a carrier step-back: the
    # latch is tackle-cycle state and only the cycle-end owners
    # (``clear_fullback_chase_state`` at RELEASE entry) may drop it.
    # ``carrier_behind_latched_line`` is kept as a readable predicate; it no
    # longer authorises a release.
    # THE AUDIT BACKSTOP: a chase that a FORWARD relock withdrew is stamped
    # as an EXPLICIT handover instead of vanishing.  With the latch armed
    # this path should be unreachable for the frame-383 shape; it exists so
    # no future writer can reintroduce a silent collapse, and so an
    # A/B with the lever disarmed still reports the transition.
    if _prev_mode_name == "CHASE" and _anchor_relocked_forward:
        release_fullback_chase_on_anchor_relock(world, defender)

    if fullback_recovery_active(world, defender, anchor_x, anchor_y):
        # INTENT OWNERSHIP (Stage 1): owed-ground travel is the RECOVER
        # intent; the controller resolves it to the same
        # MovementState.RECOVER singleton this branch always wrote.
        #
        # RECOVERY OWNERSHIP (Stage 3, 2026-09-30): the frame is ALSO declared
        # to the controller's recovery contract, so the last defender's
        # owed-ground travel is a NAMED fullback behaviour rather than a state
        # the shared policy merely happens to read.  The declaration is
        # decision-only -- the MOVEMENT STATE is still written by the ONE write
        # path below -- and the four SHARED recovery pathways (see the
        # controller's Stage-3 section) refuse this body through the same
        # contract, so misses, recoveries and re-engagement have ONE owner.
        from game.defence.defence_lvl_1.fullback_controller import (
            FullbackIntent,
            enter_fullback_intent,
            declare_fullback_recovery,
            SOURCE_RECOVERY,
        )
        declare_fullback_recovery(
            world, defender, anchor_x, anchor_y, source=SOURCE_RECOVERY
        )
        enter_fullback_intent(
            world,
            defender,
            FullbackIntent.RECOVER,
            source=SOURCE_RECOVERY,
        )

    # Approach-speed contract.  Base speed is the unified defensive ceiling
    # stamped by the engine (fallback: the player's physical max).
    #
    # THREE branches, one per ownership state:
    #   RECOVER -- the ALIGNED recovery pace (FB_RECOVER_SPEED_SCALE x the
    #              ceiling: the same fraction an ordinary line defender uses
    #              to re-take its slot), sweeper cap lifted, arrival taper
    #              kept.  Never the full ceiling -- see the
    #              FB_RECOVER_SPEED_SCALE comment above.
    #   DRIFT   -- the sweeper contract: 2.0 m/s cap, decel band, 10% floor.
    #   else    -- granted tackle authority.  Unreachable in practice (the
    #              authority yield above returns first); kept as a
    #              belt-and-braces path at the governed ceiling.
    base_speed = getattr(defender, "max_defensive_speed_mps", 0.0) or 0.0
    if base_speed <= 0.0:
        base_speed = getattr(defender, "max_speed_mps", 8.3)
    # 2D SPEED ERROR (2026-09-22): the taper is keyed on the DISTANCE to the
    # commanded anchor, never on the X component alone.  With the open-play
    # lane now mirrored, the anchor is regularly the LARGER error on Y, and
    # an X-only error would command the 10% floor (0.2 m/s) to a body whose
    # whole remaining job is to slide across the field.
    # SPEED AUTHORITY REMOVED (2026-09-27): the three-way branch below used to
    # hand the sweeper a bespoke pace per ownership state -- the 0.5x
    # ``fullback_recovery_speed`` for RECOVER, the 2.0 m/s
    # ``fullback_approach_speed`` sweeper cap for DRIFT, and the raw governed
    # ceiling otherwise.  All three are gone.  The actor's STATE (RECOVER /
    # DRIFT, decided above) is now the only speed input, and the shared model
    # turns it into a pace via MAX_SPEED_BY_STATE x max_speed_mps.  Note this
    # is why RECOVER's table row is pinned to the RETREAT-rate build-up: the
    # sweeper must not become a sprinter just because it is running home.
    _anchor_error = fullback_target_distance(defender, anchor_x, anchor_y)

    # ── POST-CATCH ABSORB GRACE COUNTDOWN (2026-08-28) ──
    # The kickoff FB is hard-placed ON the flat formation line; after the
    # catch the post-catch line anchor (bc.x + 10) can land within
    # ABSORB_X_THRESHOLD_M of the FB's live position, tripping the
    # absorption dissolve.  Decrement the world grace window one frame per
    # FB-authority pass so the absorption sites see it reach 0 before they
    # may dissolve the actor.  The window is armed at reception and cleared
    # to 0 by kickoff_engine.reset_for_kickoff() (fresh each kickoff).
    _grace = getattr(world, "fb_post_catch_absorb_grace_frames", 0)
    if _grace and _grace > 0:
        world.fb_post_catch_absorb_grace_frames = _grace - 1


# ─────────────────────────────────────────────────────────────────────
# FULLBACK STATE PUBLISH (2026-08-27)
# ─────────────────────────────────────────────────────────────────────
# Single source of truth for publishing the live fullback actor + anchor
# onto ``world`` so the debug overlays, tooling and the fullback-invariant
# audit all read the SAME actor the geometry author wrote.  The engine's
# OPEN_PLAY dispatch already publishes this; the post-catch reception
# re-assert and the PTB-era ``_reassert_fullback`` (which run OUTSIDE
# `_update_defence`) must ALSO refresh these fields, otherwise a re-assign
# during RELEASE/PTB leaves ``world.fullback_actor_id`` stale and the
# invariant audit flags FB_ID_MISMATCH.

def publish_fullback_state(world) -> None:
    """Refresh world.fullback_actor_id / anchor fields from the live FB."""
    _fb_actor = None
    _fb_tx = None
    _fb_ty = None
    _fb_obj = None
    # ── SINGLE-FB RUNTIME ASSERT / TELEMETRY (2026-08-27 hotfix) ──
    # The single-FB invariant is enforced by assign_fullback_role's clear
    # loop, but a subset re-assign (TES-drive / PTB-era) could transiently
    # stamp two actors if the clear list missed one.  Count and surface any
    # violation so the hotfix fails fast instead of silently letting the
    # invariant audit catch it later.  Non-fatal: stubs and synthetic
    # verifier worlds may legitimately have zero defenders.
    _fb_count = sum(
        1 for _d in getattr(world, "defending_team", []) or []
        if getattr(_d, "fullback_role_active", False)
    )
    if _fb_count > 1:
        print(
            f"[FB_TELEMETRY] MULTI_ACTIVE_FB count={_fb_count} "
            f"frame={getattr(world, 'frame_count', '?')}"
        )
    for _d in getattr(world, "defending_team", []) or []:
        if getattr(_d, "fullback_role_active", False):
            _fb_actor = getattr(_d, "player_id", None)
            _fb_tx = getattr(_d, "target_x", None)
            _fb_ty = getattr(_d, "target_y", None)
            _fb_obj = _d
            break
      # DEPTH-SIDE LOCK ASSERT (2026-08-27 hotfix): the FB never carries a
      # depth_role / depth_side pair — clearing it is the depth-side lock.
      # If a line writer re-stamped it, surface the leak immediately.
    if _fb_obj is not None:
        _leak = (
            getattr(_fb_obj, "depth_role", None) is not None
            or getattr(_fb_obj, "depth_side", None) is not None
        )
        if _leak:
            print(
                f"[FB_TELEMETRY] DEPTH_SIDE_LEAK fb_id={getattr(_fb_obj, 'player_id', '?')} "
                f"depth_role={getattr(_fb_obj, 'depth_role', None)} "
                f"depth_side={getattr(_fb_obj, 'depth_side', None)}"
            )
    world.fullback_actor_id = _fb_actor
    world.fullback_anchor_x = _fb_tx
    world.fullback_anchor_y = _fb_ty
    if _fb_obj is not None:
        world.fullback_role_active = _fb_obj.fullback_role_active
        world.fullback_absorb_allowed = _fb_obj.fullback_absorb_allowed
        world.fullback_frames_within_absorb_x = _fb_obj.frames_within_absorb_x
        world.fullback_frames_within_absorb_y = _fb_obj.frames_within_absorb_y
        world.fullback_arrived = _fb_obj.fullback_arrived
    else:
        world.fullback_role_active = False
        world.fullback_absorb_allowed = False
        world.fullback_frames_within_absorb_x = 0
        world.fullback_frames_within_absorb_y = 0
        world.fullback_arrived = False


# ─────────────────────────────────────────────────────────────────────
# FULLBACK ABSORB HYSTERESIS + APPROACH SPEED (Task J, 2026-08-12)
# ─────────────────────────────────────────────────────────────────────

def update_fullback_absorb_state(
    fb,
    anchor_x: float,
    lane_y: float,
) -> None:
    """Update the fullback's absorb/arrival counters for this frame.

    Pure, deterministic, side-effect-free EXCEPT for the four fields it
    owns on *fb*: ``frames_within_absorb_x``, ``frames_within_absorb_y``,
    ``fullback_absorb_allowed`` and ``fullback_arrived``.  Call it every
    OPEN_PLAY defence frame while the actor holds the fullback role (the
    pipeline calls it right after ``assign_fullback_role``, before the
    line-pool exclusion is consumed).

    Hysteresis:
      - frames_within_absorb_x/Y increment when the actor is within
        ABSORB_X_THRESHOLD_M / ABSORB_Y_THRESHOLD_M of the anchor X and
        the ball-carrier lane Y respectively.
      - EACH counter resets to 0 the moment ITS proximity breaks, so
        absorption requires BOTH dimensions to be simultaneously within
        threshold for ABSORB_FRAMES_MIN consecutive frames (the spec's
        "requires both X and Y proximity for ABSORB_FRAMES_MIN consecutive
        frames").
      - fullback_absorb_allowed = (both counters >= ABSORB_FRAMES_MIN).
        The pipeline dissolves the FB role into a deterministic line role
        when this turns True.  It stays False while the FB is deep
        (counters well below the frame minimum).
      - fullback_arrived = True when the actor is within
        FB_ARRIVED_PROXIMITY_M of the anchor (reached the backfield slot);
        used for audit visibility, never gates movement.

    Args:
        fb: The active fullback Player actor.
        anchor_x: The defensive-line anchor X (``world.defensive_line_anchor``).
        lane_y: The ball-carrier Y (the lateral reference for absorb Y).
    """
    x_error = abs(fb.x - anchor_x)
    if x_error <= ABSORB_X_THRESHOLD_M:
        fb.frames_within_absorb_x += 1
    else:
        fb.frames_within_absorb_x = 0

    y_error = abs(fb.y - lane_y)
    if y_error <= ABSORB_Y_THRESHOLD_M:
        fb.frames_within_absorb_y += 1
    else:
        fb.frames_within_absorb_y = 0

    fb.fullback_absorb_allowed = (
        fb.frames_within_absorb_x >= ABSORB_FRAMES_MIN
        and fb.frames_within_absorb_y >= ABSORB_FRAMES_MIN
    )
    fb.fullback_arrived = math.hypot(fb.x - anchor_x, fb.y - lane_y) <= FB_ARRIVED_PROXIMITY_M


def fullback_approach_speed(base_speed: float, distance_to_target: float) -> float:
    """Scale a base speed to the fullback approach-speed contract.

    Returns ``min(base_speed, FB_MAX_APPROACH_SPEED_MPS)`` and decelerates
    linearly once the remaining ERROR is inside ``FB_DECEL_START_M``
    (floor 10% so the actor always glides home rather than stalling).

    ``distance_to_target`` is the 2D DISTANCE to the commanded target (see
    ``fullback_target_distance``), never the signed X component alone: once
    the open-play destination mirrors the carrier's lane, the largest
    remaining error is regularly on Y, and an X-only error would hold a body
    that still owes metres of lateral ground at the 10% floor.  Only the
    magnitude is used.

    Use this at every call site that writes the fullback's ``move_speed``
    (structured OPEN_PLAY + kickoff), so the "approach speed never exceeds
    FB_MAX_APPROACH_SPEED_MPS" acceptance holds in every phase.
    """
    speed = min(max(0.0, base_speed), FB_MAX_APPROACH_SPEED_MPS)
    error = abs(distance_to_target)
    if error < FB_DECEL_START_M:
        scale = max(0.1, error / FB_DECEL_START_M)
        speed *= scale
    return speed


def fullback_recovery_speed(base_speed: float, distance_to_target: float) -> float:
    """Scale a base speed to the RECOVER pace contract (aligned, uncapped).

    Two differences from :func:`fullback_approach_speed`:

      - the base speed is scaled by ``FB_RECOVER_SPEED_SCALE`` (0.5), so the
        owed-ground run sits at the ordinary line-defender slot pace instead
        of the full defensive ceiling, and
      - the ``FB_MAX_APPROACH_SPEED_MPS`` (2.0 m/s) sweeper cap is NOT
        applied.  That cap is exactly the immobility this state exists to
        remove; a recovering body is bounded instead by the unified defensive
        ceiling (``max_defensive_speed_mps``, hard-clamped in
        ``Player.update``) times this scale.

    The same ``FB_DECEL_START_M`` linear taper (floor 10%) is kept so the body
    settles onto its anchor smoothly instead of overshooting it.

    ``distance_to_target`` is the 2D DISTANCE to the commanded target (see
    ``fullback_target_distance``); only magnitude is used.  It is the same
    metric as :func:`fullback_approach_speed` on purpose, so a recovery that
    is owed ground LATERALLY tapers exactly like one owed ground in depth.
    """
    speed = max(0.0, base_speed) * FB_RECOVER_SPEED_SCALE
    error = abs(distance_to_target)
    if error < FB_DECEL_START_M:
        speed *= max(0.1, error / FB_DECEL_START_M)
    return speed


def fullback_chase_speed_enabled() -> bool:
    """Whether the live-break chase takes its own (uncapped) speed contract.

    Default ON.  ``RLM_FB_CHASE_SPEED=0`` restores the historical
    ``fullback_approach_speed(...) if fullback_approach_cap_applies(d) else
    base`` expression verbatim, so the pre-change engine (the 2.0 m/s
    sweeper cap applied to every live-break chase) is recoverable with no
    code revert.

    Read at CONSUMPTION time, not at import time, so one process can A/B the
    two contracts.
    """
    return os.environ.get(
        FB_CHASE_SPEED_ENV_VAR, "1"
    ).strip().lower() not in ("0", "false", "no", "off", "")


def fullback_chase_speed(base_speed: float, distance_to_target: float) -> float:
    """Scale a base speed to the LIVE-BREAK CHASE contract (uncapped).

    Three deliberate differences from :func:`fullback_approach_speed`:

      - the ``FB_MAX_APPROACH_SPEED_MPS`` (2.0 m/s) SWEEPER cap is NOT
        applied.  That cap exists to stop the disengaged anchor glide from
        bursting while the body measures its own depth; a chase onto a live
        carrier is a different contract and must run at the governed
        defensive ceiling, exactly as every other committed defender does.
      - the pace is ``FB_CHASE_SPEED_SCALE`` (1.0) x the ceiling rather than
        the 0.5x slot-repositioning pace ``fullback_recovery_speed`` uses:
        this is a pursuit, not a walk back into shape.
      - the same ``FB_DECEL_START_M`` linear taper (floor 10%) is KEPT, so
        the body settles onto the projected interception point instead of
        overshooting the carrier it is trying to tackle.

    ``distance_to_target`` is the 2D DISTANCE to the commanded pursuit point
    (``fullback_target_distance``); only magnitude is used, so a lateral
    step-out tapers exactly like a depth chase.
    """
    speed = max(0.0, base_speed) * FB_CHASE_SPEED_SCALE
    error = abs(distance_to_target)
    if error < FB_DECEL_START_M:
        speed *= max(0.1, error / FB_DECEL_START_M)
    return speed


# ─────────────────────────────────────────────────────────────────────
# ACTOR SELECTION
# ─────────────────────────────────────────────────────────────────────

def _is_available(player, anchor_x: float, anchor_y: float) -> bool:
    """Return True if *player* can act as the fullback THIS frame.

    A player is unavailable when:
      - not a valid Player (defensive team member),
      - frozen (movement owned by the freeze authority),
      - tackled / attached / in contact (TES owns position),
      - a marker, dummy half, or registered TES tackler,
      - in arrival mode (PTB-era velocity-proportional path),
      - already the fullback-capable role but beyond MAX_ANCHOR_DISTANCE_M
        from the anchor (too far to be the "last defender").
    """
    if not isinstance(player, Player):
        return False
    # ── EMERGENCY-DEFENDER STICKINESS (B2b, 2026-09-16) ──
    # A tagged fullback that is engaged in the emergency tackle path is
    # ACTING AS A TACKLER (defender_role = TACKLER_1/2/3, is_tackler True)
    # -- but the tag was never revoked.  It must therefore still count as
    # available, otherwise the role is handed to another defender mid
    # tackle and the clear loop strips the tag from the engaged actor.
    # Reaching this branch implies the tag is already set (only engaged
    # fullbacks carry the marker), so it is safe to short-circuit.
    if is_fullback_emergency_engaged(player):
        return True
    if getattr(player, "frozen", False):
        return False
    if getattr(player, "tes_attached", False) or getattr(player, "in_contact", False):
        return False
    if getattr(player, "is_tackler", False):
        return False
    if getattr(player, "arrival_mode", False):
        return False

    role = getattr(player, "defender_role", None)
    if role in UNAVAILABLE_DEFENDER_ROLES:
        return False

    # Too far from the anchor means the defender cannot fulfil the
    # backfield coverage role this frame — reassign.
    dist = math.hypot(player.x - anchor_x, player.y - anchor_y)
    if dist > MAX_ANCHOR_DISTANCE_M:
        return False

    return True


def _is_available_simple(player) -> bool:
    """Designated-FB availability check (NO distance gate).

    The designated fullback (jersey 1) holds the backfield anchor even
    when it is far from the current ball-derived anchor (e.g. still on the
    kickoff flat line the frame after the catch snaps the anchor to
    bc.x + 10).  The 25 m distance gate is therefore WAIVED for the
    priority — it mirrors every OTHER availability rule (frozen /
    TES-in-contact / tackler / arrival / unavailable role) so a genuinely
    unavailable defender is never chosen as the designated FB.
    """
    if not isinstance(player, Player):
        return False
    # ── EMERGENCY-DEFENDER STICKINESS (B2b, 2026-09-16) ──
    # Same law as ``_is_available``: an engaged emergency fullback keeps the
    # tag through TES arming, so it must stay the designated fullback
    # (TACKLER_* / is_tackler would otherwise mark it unavailable).
    if is_fullback_emergency_engaged(player):
        return True
    if getattr(player, "frozen", False):
        return False
    if getattr(player, "tes_attached", False) or getattr(player, "in_contact", False):
        return False
    if getattr(player, "is_tackler", False):
        return False
    if getattr(player, "arrival_mode", False):
        return False
    role = getattr(player, "defender_role", None)
    if role in UNAVAILABLE_DEFENDER_ROLES:
        return False
    return True


def _choose_designated_fullback(players, kicker_id=None):
    """Return the designated fullback (jersey 1) when eligible, else None.

    PERSISTENT PRIORITY — runs every frame, KICKOFF and OPEN_PLAY.  When
    jersey 1 passes the simple (distance-gate-free) availability check it
    is ALWAYS the fullback, so the actor survives the kickoff-catch anchor
    snap and keeps the role instead of handing off to a line defender.
    Returns None when jersey 1 is unavailable (frozen / TES-in-contact /
    tackler / marker / arrival) OR when jersey 1 IS the kicker (the kicker
    owns the centre slot and cannot double as the FB) so the caller falls
    back to the deepest central defender.
    """
    for p in players:
        if getattr(p, "jersey_number", None) != KICKOFF_FB_JERSEY:
            continue
        if kicker_id is not None and p.player_id == kicker_id:
            return None
        # Belt-and-braces: an explicit is_kicker stamp always beats jersey
        # identity even if the kicker id was not threaded through.
        if getattr(p, "is_kicker", False):
            return None
        if _is_available_simple(p):
            return p
        return None
    return None


def _in_fallback_corridor(player) -> bool:
    """True if *player* sits inside the fullback central corridor.

    The last-defender role is reserved for defenders within
    FULLBACK_CENTRAL_BAND_M (±15 m) of field centre — y ∈ [20, 50] on the
    70 m field.  Defenders near the sidelines never count as fullback
    candidates when the designated FB is unavailable.
    """
    return abs(getattr(player, "y", FIELD_CENTRE_Y) - FIELD_CENTRE_Y) <= FULLBACK_CENTRAL_BAND_M


def _rank_players(players, anchor_x: float, anchor_y: float):
    """Return available players + their distance to the anchor.

    The ranking pipeline mirrors the DH selection order:
        1. deepest backfield-capable defender (largest X = furthest back)
        2. nearest winger (depth_role == "WINGER")
        3. nearest centre (depth_role in A/B/C/D/CENTRE)
        4. nearest line/half (fallback)

    FB REPLACEMENT BAND (2026-08-11): only defenders inside the central
    corridor (|y - FIELD_CENTRE_Y| <= FULLBACK_CENTRAL_BAND_M) are
    eligible fullback candidates.  Sideline defenders never take the
    last-defender role.
    """
    eligible = []
    for p in players:
        if not _is_available(p, anchor_x, anchor_y):
            continue
        if not _in_fallback_corridor(p):
            continue
        dist = math.hypot(p.x - anchor_x, p.y - anchor_y)
        role = getattr(p, "depth_role", None)
        eligible.append((p, dist, role))
    return eligible


def choose_best_available_player(
    players,
    anchor_x: float,
    anchor_y: float,
    formation_line_x: float | None = None,
    kick_lane_y: float | None = None,
):
    """Return the best available player to act as fullback, or None.

    Preference order (first match wins):
        1. The previous fullback actor (if still available) — stickiness
           prevents the actor from swapping every frame.
        2. Deepest defender (largest X) — natural last-defender (defence
           retreats toward +X; the last defender is the furthest +X).
        3. Nearest WINGER (depth_role == "WINGER").
        4. Nearest CENTRE / line defender (A/B/C/D/CENTRE).
        5. Nearest remaining eligible defender (half / fallback).

    CANDIDATE BAND (2026-08-11): every step above draws from the central
    corridor only (|y - FIELD_CENTRE_Y| <= FULLBACK_CENTRAL_BAND_M) — the
    corridor filter lives in ``_rank_players`` and the relaxed rescue pool,
    so sideline wingers never become the last defender.

    KICKOFF FALLBACK (2026-08-11): when ``formation_line_x`` is provided
    (the kickoff FB anchor source) the deepest-defender step becomes a
    fully DETERMINISTIC key over the whole eligible set — all candidates
    are ranked by
        (-depth_from_formation_line, -lateral_distance_from_kick_lane,
         role_priority, stable_id)
    and the distance gate is relative to the FORMATION LINE (never the
    ball), so a 13-man flat line resolves to a stable actor instead of a
    list-order tie-break.  Normal play (``formation_line_x=None``) keeps
    the legacy sticky/deepest hierarchy byte-for-byte.
    """
    prev_actor = None
    for p in players:
        if getattr(p, "fullback_role_active", False):
            prev_actor = p
            break

    eligible = _rank_players(players, anchor_x, anchor_y)

    # 1. Sticky previous actor (when still available).
    if prev_actor is not None:
        for p, dist, role in eligible:
            if p is prev_actor:
                return p

    if not eligible:
        # ── DISTANCE-GATE RESCUE (2026-08-08) ──
        # Every candidate failed at least one gate.  The most common failure
        # is the distance gate (anchor wrong-side / extreme post-catch).
        # Fall back to the deepest defender that passes all OTHER gates.
        # FB REPLACEMENT BAND (2026-08-11): the rescue pool is also
        # corridor-filtered — sideline defenders never take the last-
        # defender role.
        relaxed = []
        for p in players:
            if not isinstance(p, Player):
                continue
            if not _in_fallback_corridor(p):
                continue
            if getattr(p, "frozen", False):
                continue
            if getattr(p, "tes_attached", False) or getattr(p, "in_contact", False):
                continue
            if getattr(p, "is_tackler", False):
                continue
            if getattr(p, "arrival_mode", False):
                continue
            role = getattr(p, "defender_role", None)
            if role in UNAVAILABLE_DEFENDER_ROLES:
                continue
            relaxed.append(p)
        if relaxed:
            # Deepest defender = max x (furthest +X = natural last
            # defender; attack/retreat direction is +X).  KICKOFF
            # DETERMINISM (2026-08-11): tie-break an equal-depth group by
            # stable player_id so the fallback never depends on list order.
            relaxed_max_depth = max(p.x for p in relaxed)
            deepest_relaxed = [p for p in relaxed
                               if abs(p.x - relaxed_max_depth) < 1e-9]
            fallback = min(deepest_relaxed, key=lambda p: p.player_id)
            # Sticky: prefer the previous actor even under relaxation when
            # they remain otherwise-gate-clean.
            if prev_actor is not None and prev_actor in relaxed:
                fallback = prev_actor
            return fallback
        return None

    # ── KICKOFF DETERMINISTIC FALLBACK (2026-08-11) ──
    # The flat 13-man kickoff line is a 13-way X tie, so the legacy
    # "deepest" selection would silently mean "creation order".  When the
    # caller supplies the formation line (kickoff), rank the WHOLE eligible
    # set by:
    #   (-depth_from_formation_line, -lateral_from_kick_lane,
    #    role_priority, stable_id)
    # with the distance gate already enforced relative to the formation
    # line by the caller's anchor (formation_line_x).  This is fully
    # deterministic and never consults the input list order.
    if formation_line_x is not None:
        ref_y = kick_lane_y if kick_lane_y is not None else anchor_y
        best = None
        best_key = None
        for item in eligible:
            p = item[0]
            depth_from_line = p.x - formation_line_x
            lateral_dist = abs(p.y - ref_y)
            key = (
                -depth_from_line,
                -lateral_dist,
                _ROLE_PRIORITY.get(getattr(p, "depth_role", None), 100),
                p.player_id,
            )
            if best_key is None or key < best_key:
                best_key = key
                best = p
        return best

    # 2. Deepest defender (furthest +X = last defender).  KICKOFF
    # DETERMINISM (2026-08-11): this branch is now kickoff-dead because the
    # formation-line branch above returns first — but the deterministic
    # tie-break is kept so any non-kickoff flat-line caller (e.g. a
    # synthetic verifier line) never depends on list order either.
    max_depth = max(item[0].x for item in eligible)
    deepest_group = [item for item in eligible
                     if abs(item[0].x - max_depth) < 1e-9]
    deepest = min(deepest_group, key=lambda item: (
        item[1],
        _ROLE_PRIORITY.get(item[2], 100),
        item[0].player_id,
    ))
    if deepest[1] < MAX_ANCHOR_DISTANCE_M:
        return deepest[0]

    # 3. Nearest WINGER.
    wingers = [item for item in eligible if item[2] == "WINGER"]
    if wingers:
        return min(wingers, key=lambda item: item[1])[0]

    # 4. Nearest CENTRE / A / B / C / D.
    interior = [item for item in eligible if item[2] in ("A", "B", "C", "D", "CENTRE")]
    if interior:
        return min(interior, key=lambda item: item[1])[0]

    # 5. Nearest remaining eligible player (halves / fallback).
    return min(eligible, key=lambda item: item[1])[0]


# ─────────────────────────────────────────────────────────────────────
# ASSIGNMENT (called every frame)
# ─────────────────────────────────────────────────────────────────────

def assign_fullback_role(
    players,
    ball,
    defensive_line_x: float,
    in_kickoff: bool = False,
    formation_line_x: float | None = None,
    side_sign: int = 1,
    kicker_id: int | None = None,
    world=None,
    stamp_geometry: bool = False,
):
    """Assign the fullback role for this frame.

    Clears the previous actor's flag, selects the best available player,
    and writes the anchor target.  Returns ``(actor, (anchor_x, anchor_y))``
    or ``(None, (None, None))`` when no player is available.

    JERSEY-1 PRIORITY (2026-08-11):
      - Jersey 1 is the PERSISTENT fullback priority every frame — KICKOFF
        and OPEN_PLAY — gated on the KICKOFF_FB_OVERRIDE flag.  The simple
        (distance-gate-free) availability check waives the 25 m anchor gate
        so jersey 1 survives the kickoff-catch anchor snap and keeps the
        role instead of handing off to a line defender.
      - When the flag is off (rollback), jersey 1 = kicker, or jersey 1 is
        unavailable (tackler / marker / frozen / attached / arrival), the
        selection falls back to the DEEPEST CENTRAL defender: the furthest
        +X defender inside the y ∈ [20, 50] corridor.  Sideline defenders
        never take the last-defender role (``_in_fallback_corridor``).
      - When jersey 1 becomes available again (e.g. marker duty ends at
        POST_RUCK cleanup) it automatically re-takes the role next frame.

    The caller (KickoffEngine / DefensiveBehaviourUnified) must then:
      - exclude *actor* from the line-formation lists,
      - force depth_class = "PASSIVE",
      - skip line-break collapse / mandatory retreat / structured close
        writers for *actor*,
      - force MovementState.DRIFT at the MMDL layer so Player.update(dt)
        integrates the stabilised anchor (smooth, no oscillation).

    ROLE-PERSISTENCE LAW (B2, 2026-09-16):
      ``fullback_role_active`` is granted and revoked ONLY by this writer plus
      the two explicit dissolve sites -- ``open_play_engine._arm_tackler_role``
      (the actor is armed as a TES tackler) and the PTB-era absorption in
      ``DefensiveBehaviourUnified``.  ``movement_state`` NEVER revokes it: a
      fullback in CHASE/SHOOT pursuit remains the sweeper, and the breakaway
      chase override in ``apply_fullback_geometry`` is a movement-state change
      only.  Actual tackle participation is gated by ``can_acquire_tackle``
      (stamped True below), which admits the actor to the lane / pre-tackle
      selection pools WITHOUT changing its line membership (depth_role /
      depth_side / spacing / band stay excluded).
    """
    anchor_x, anchor_y = compute_fullback_anchor(
        ball,
        defensive_line_x,
        in_kickoff,
        formation_line_x=formation_line_x,
        side_sign=side_sign,
        # SINGLE AIRBORNE SOURCE (2026-09-27): the kickoff assignment and the
        # flight geometry writer must agree on the in-flight anchor, so the
        # assignment takes the SAME tracking lane the flight authority uses.
        # Without this the assignment -- which runs FIRST in the frame and
        # publishes ``world.fullback_anchor_*`` -- left the PUBLISHED anchor
        # on the constant while the flight writer moved only the actor, so a
        # reader (audit, telemetry, verifier) never saw the tracking target.
        # ``_kickoff_flight_lane_y`` returns None until the ball has actually
        # been kicked, so the pre-kick formation slot -- and therefore the
        # ``fb_placement_stamp`` the kickoff engine consumes -- is unchanged.
        flight_lane_y=(
            _kickoff_flight_lane_y(world) if in_kickoff else None
        ),
        # DEPTH-ROLE X (2026-09-28): the airborne anchor takes the same
        # live-ball depth reference the flight authority uses, so the
        # ASSIGNMENT and the GEOMETRY writer agree on one anchor (the
        # single-source clause C6).  ``None`` pre-kick keeps the constant.
        flight_depth_ref=(
            _kickoff_flight_depth_ref(world) if in_kickoff else None
        ),
        defender_x=None,
    )

    # DESIGNATED FB PRIORITY (2026-08-11): jersey 1 is the persistent
    # fullback preference EVERY frame — KICKOFF and OPEN_PLAY — gated on
    # the runtime KICKOFF_FB_OVERRIDE flag.  The simple (distance-gate-
    # free) check keeps jersey 1 as FB after the kickoff catch even when
    # the new ball-derived anchor is far from the formation line.  When
    # the flag is off (rollback), jersey 1 is unavailable (tackler /
    # marker / frozen / attached), or jersey 1 IS the kicker, selection
    # falls back to the deepest central defender.
    actor = None
    if KICKOFF_FB_OVERRIDE:
        actor = _choose_designated_fullback(players, kicker_id=kicker_id)
    # ── EXISTING-ACTOR STICKINESS (2026-08-27) ──
    # The TES-drive / PTB-era structured pipeline re-runs this writer with a
    # SUBSET of defenders (``line_defenders``) that EXCLUDES the current
    # fullback — the actor is removed from the line pool before any shaping
    # step.  Re-selecting from that subset would hand the role to a line /
    # winger defender and the FB tag would jump off the deep player ("FB tag
    # goes to a wide player").  When the designated pick found nothing in the
    # passed subset but a live fullback already exists in the whole defending
    # team and is still available, PRESERVE it instead of re-selecting.
    if actor is None and world is not None:
        _def_team = getattr(world, "defending_team", None) or ()
        for _d in _def_team:
            if getattr(_d, "fullback_role_active", False) and _is_available_simple(_d):
                actor = _d
                break
    if actor is None:
        actor = choose_best_available_player(
            players,
            anchor_x,
            anchor_y,
            formation_line_x=formation_line_x if in_kickoff else None,
            kick_lane_y=anchor_y if in_kickoff else None,
        )

    # ── CHASE-STATE DEMOTION DELETED (2026-09-21) ──
    # The 2026-09-15 block that CLEARED ``fullback_role_active`` whenever the
    # actor's movement_state was CHASE / SHOOT is gone for good, including
    # its ``RLM_FULLBACK_ROLE_PERSISTENCE=0`` rollback form.  It was the
    # breakaway half of the "fullback structural immobility" defect: the
    # breakaway chase override in ``apply_fullback_geometry`` stamps CHASE
    # the moment the carrier passes the anchor, and this writer -- the FIRST
    # writer of the next frame -- then traded the sweeper tag away, so the
    # chasing last defender was demoted into the defensive line array and
    # lost its lane-agnostic geometry exactly when it was needed.
    #
    # THE LAW: ``movement_state`` is NEVER a role-demotion mechanism.  The
    # tag is granted and revoked ONLY here plus the two explicit dissolve
    # sites (``OpenPlayEngine._arm_tackler_role`` when the actor is actually
    # armed as a TES tackler, and the PTB-era absorption in
    # ``DefensiveBehaviourUnified``).  The A-B lever survives below with a
    # breakaway-safe meaning: it may withdraw TACKLE CANDIDACY, never the
    # tag, so no setting of it can put the chasing fullback into the line.

    # CHASE-STATE DEMOTION REMOVED (2026-09-16) -- B2 sweeper-until-needed.
    # The 2026-09-15 block that cleared ``fullback_role_active`` whenever the
    # actor's ``movement_state`` was CHASE / SHOOT is deleted.  MOVEMENT STATE
    # IS NEVER A ROLE-DEMOTION MECHANISM: the designated fullback keeps
    # fullback_role_active through normal pursuit (the breakaway chase
    # override in ``apply_fullback_geometry`` sets CHASE and the actor is STILL
    # the sweeper), so it can no longer be silently absorbed into the defensive
    # line merely because it started chasing the carrier.
    #
    # TACKLE ELIGIBILITY IS OWNED BY can_acquire_tackle (stamped True below on
    # every successful assignment).  The lane selector already honours it
    # (intercept_tacklers._is_eligible_lane_defender) and the structured
    # OPEN_PLAY pipeline admits a tackle-capable fullback into its selection
    # pools.  The tag is handed back at exactly ONE place --
    # OpenPlayEngine._arm_tackler_role, the single tackler-arming point -- i.e.
    # the moment the actor is ACTUALLY armed as a TES tackler and TES takes
    # ownership of the body.  A TACKLER_* role on a still-tagged actor is
    # forbidden by the fullback invariant audit (FB_ROLE_CONTAMINATION).

    # ── SINGLE-FB INVARIANT (2026-08-27) ──
    # The clear loop must run over the WHOLE defending team, not just the
    # passed ``players`` list.  The structured OPEN_PLAY path and the
    # TACKLE_DRIVE / PTB-era path both invoke this writer; when one passes
    # a SUBSET of defenders (e.g. non-ruck, non-tackler during the drive),
    # a previously-assigned actor outside that subset would keep
    # ``fullback_role_active`` and a second defender would also be stamped
    # — two simultaneous fullbacks (the "FB tag lands on a wide/line
    # defender" symptom).  Clearing across ``world.defending_team``
    # guarantees exactly one active fullback regardless of how many times
    # or with what subset this writer is called in a frame.
    _clear_list = list(players)
    if world is not None:
        _def_team = getattr(world, "defending_team", None) or ()
        for _d in _def_team:
            if _d not in _clear_list:
                _clear_list.append(_d)
    for p in _clear_list:
        if p is actor:
            continue
        # ── EMERGENCY-DEFENDER TAG HOLD (B2b, 2026-09-16) ──
        # An engaged emergency fullback is a TES participant: TES owns its
        # body and the tag is the ONLY thing keeping it out of the line.
        # Stripping it here would let the very next line writer absorb the
        # actor mid-tackle.  The engagement marker is cleared by the engine
        # at the end of the tackle cycle, never by a re-assign.
        if is_fullback_emergency_engaged(p):
            continue
        if getattr(p, "fullback_role_active", False):
            p.fullback_role_active = False
            # THE BREAK LATCH IS TAGGED-LIFETIME STATE (2026-09-28): losing
            # the tag must drop the latch with it, so a latched anchor can
            # never survive onto a body that is no longer the fullback.
            clear_fullback_break_latch(p)
            if getattr(p, "role", None) == "FULLBACK":
                p.role = None
            # TACKLE-ELIGIBILITY CLEAR (2026-08-17): a de-assigned actor is
            # a line defender again — no stale can_acquire_tackle may leak
            # onto a depth-role / spacing / tackle pool member.
            p.can_acquire_tackle = False

    if actor is None:
        if world is not None:
            publish_fullback_state(world)
        return None, (None, None)

    actor.fullback_role_active = True
    actor.role = "FULLBACK"
    # ── EMERGENCY-DEFENDER BODY OWNERSHIP (B2b, 2026-09-16) ──
    # While the actor is engaged in an emergency tackle, TES owns its body:
    # ``defender_role`` is TACKLER_1/2/3, ``movement_mode`` is TACKLER and
    # the commit / corridor writers own target_x / target_y / target_source
    # / movement_state / move_speed.  Re-stamping DEF_LINE_READY or the
    # backfield anchor here would tear the tackle geometry apart and break
    # the TES movement contract.  The TAG is still asserted (it is the only
    # thing keeping the actor out of the line) and the line-identity fields
    # are still cleared; only the geometry / role stamps are skipped.
    _engaged = is_fullback_emergency_engaged(actor)
    if not _engaged:
        # DEFENDER-ROLE CONTRACT (2026-08-28): the fullback always keeps
        # defender_role = DEF_LINE_READY — the one role that is a legal mover
        # in every MOVEMENT_RULES phase.  Setting it here aligns the code with
        # the module docstring ("the fullback actor therefore keeps
        # defender_role = DEF_LINE_READY") and removes the subtle fragility
        # where the reception ENTRY CLEANUP (catch_system._perform_catch) had
        # left it None.  apply_phase_invariants' FULLBACK UNFREEZE block covers
        # the PTB phases regardless, but a None role relied on that override;
        # DEF_LINE_READY is correct by construction.
        actor.defender_role = DefenderRole.DEF_LINE_READY
    # TACKLE-ELIGIBILITY SIGNAL (2026-08-17): the fullback ALWAYS retains
    # tackle acquisition — open play and retreat phases alike.  Consumers
    # that gate tackle acquisition on movement_state must treat
    # ``can_acquire_tackle == True`` as an override of any RETREAT-state
    # block, so the last defender never becomes a "RETREAT ghost".  While
    # engaged this is the SAME authority that admitted the actor to the
    # emergency path, so it is re-asserted (not revoked).
    actor.can_acquire_tackle = True
    # ── CANDIDACY-ONLY A-B LEVER (2026-09-21) ──
    # RLM_FULLBACK_ROLE_PERSISTENCE=0 no longer restores any role demotion
    # (see the law above).  Its remaining, breakaway-safe meaning is the
    # historical SELECTION half of the old behaviour: a fullback that is
    # actively chasing (CHASE / SHOOT) is not offered as a tackle candidate.
    # The sweep tag -- and therefore the lane-agnostic sweeper geometry -- is
    # untouched by this lever.
    if not FULLBACK_ROLE_PERSISTENCE and movement_state_name(actor) in ("CHASE", "SHOOT"):
        actor.can_acquire_tackle = False
    # STALE ROLE-DEPTH HYGIENE (2026-08-11): the depth-role system
    # excludes the fullback actor, so any depth_role stamped by a previous
    # line assignment (D/C/etc.) is stale on the last defender.  Clear it
    # so no consumer (audit, render overlay, geometry) reads line identity
    # on the FB actor — the FB renders as FB, never a stale line role.
    actor.depth_role = None
    # DEPTH-SIDE LOCK (2026-08-27): the fullback is not part of the line
    # shape.  Clear depth_side too so no line writer / count-split leaves a
    # stale side identity on the last defender (depth_role is already
    # cleared; the pair must both stay None so the invariant audit sees no
    # FB_DEPTH_ROLE_LEAK and no line writer can re-tag the FB).  This is the
    # LINE-MEMBERSHIP half of the law and is asserted even while engaged.
    actor.depth_side = None
    actor.frame_side = None
    if not _engaged:
        # ── AIRBORNE ANTI-RETREAT CLAMP, AGAINST THE RESOLVED ACTOR ──
        # The anchor was computed before selection, so the clamp is
        # re-applied here against the body that actually carries the tag.
        # This is what makes the published ``world.fullback_anchor_x``
        # exactly the value the geometry writer will stamp, so no reader
        # (audit / telemetry / verifier) can see an unclamped command.
        if in_kickoff and FB_FLIGHT_DEPTH_TRACK:
            _depth_ref = _kickoff_flight_depth_ref(world)
            if _depth_ref is not None and _depth_ref[0] is not None:
                anchor_x = min(
                    depth_role_anchor_x(
                        float(_depth_ref[0]),
                        float(_depth_ref[0]) + KICKOFF_LINE_ANCHOR_DEPTH_M,
                    ),
                    float(getattr(actor, "x", anchor_x)),
                )
        actor.target_x = anchor_x
        actor.target_y = anchor_y
        actor._target_writer = "fullback_geometry"
    # ── ATOMIC GEOMETRY STAMP (2026-08-27 hotfix) ──
    # When the caller wants the reception frame to be authoritative, stamp
    # the fullback_geometry authority marker BEFORE the publish so the
    # published world.fullback_* fields never reflect a pre-stamp actor.
    # Without this, assign+stamp+publish was two separate publishes and a
    # reader (invariant audit / telemetry) could observe the transient
    # pre-stamp target_source.
    if stamp_geometry and not _engaged:
        actor.target_source = "fullback_geometry"
        # INTENT OWNERSHIP (Stage 1): role assignment stamps the sweeper
        # contract through the controller rather than writing DRIFT directly.
        from game.defence.defence_lvl_1.fullback_controller import (
            FullbackIntent,
            enter_fullback_intent,
            SOURCE_ROLE_ASSIGN,
        )
        enter_fullback_intent(
            world,
            actor,
            FullbackIntent.SWEEP,
            source=SOURCE_ROLE_ASSIGN,
        )
        actor.frozen = False
    if world is not None:
        publish_fullback_state(world)
    return actor, (anchor_x, anchor_y)
