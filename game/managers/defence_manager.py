"""defence_manager.py -- DefenceManager: sole authority for defensive orders.

One macro brain for the defending side. Consolidates every defensive
order writer that previously lived inline across OpenPlayEngine:

  1. run_frame_preamble()      -- un-gated per-frame geometry preamble
                                  (depth-roles -> spacing -> width).
                                  Compute-only; publishes frame dicts.
  2. update_open_play()        -- OPEN_PLAY dispatch: default roles,
                                  ceiling/smooth-time stamps, anchor
                                  publish, DefensiveBehaviourUnified,
                                  fullback geometry authority + publish,
                                  movement integration, tackle trigger.
  3. update_tes_drive()        -- TES-drive: non-tackler defenders are
                                  HARD-FROZEN (TES isolation contract).
                                  NO open-play defensive geometry runs
                                  during TACKLE_DRIVE / TACKLE_DOMINANCE.
  4. assign_markers_for_new_tackle() -- fresh marker assignment at
                                  DOMINANCE -> RELEASE.
  5. reassert_fullback()       -- deep backfield anchor re-assert used
                                  by every phase handler.
  6. update_tackle_gate()      -- tackle-state gate sweep (sole release
                                  authority) + release-frame slot repair.
  7. enforce_ruck_pin()        -- ruck-adjacent final-authority pin.
  8. Pending D-series state    -- deferred anchor/spacing/retreat-reset
                                  writes consumed on next OPEN_PLAY.

Authority boundary (ENGINE LAW):
  The manager issues movement/role orders ONLY. It NEVER owns TES
  attach/detach geometry, drive LERP, dominance pushback, ball-carrier/
  tackler movement during a tackle, PTBManager DH movement, phase
  transitions, freeze flags (apply_phase_invariants stays in OPE), or
  collision physics.

Zero-behaviour-change note (Stage 2):
  Every method below is a verbatim relocation of the corresponding
  inline OpenPlayEngine code. Behaviour is identical; ownership and
  audit attribution change.
"""

import math

from player.player import (
    DefenderRole,
    FIELD_MIN_X,
    FIELD_MAX_X,
    FIELD_MIN_Y,
    FIELD_MAX_Y,
)
from world.world_state import GameSubState, is_tackle_phase
from game.defence.defence_lvl_1.defensive_pipeline import (
    run_defensive_pipeline,
)
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _anchor_m,
    set_anchor,
)
from game.defence.defence_lvl_1.defensive_width import governed_width_m
from game.defence.defence_lvl_1.fullback_role import (
    apply_fullback_geometry,
    publish_fullback_state,
    clear_fullback_post_tackle_return,
    compute_fullback_ruck_anchor,
    fullback_post_tackle_return_live,
    is_fullback_post_tackle_return_active,
)
from game.debug.fullback_audit import audit_fullback_state
from game.defence.defence_lvl_1.tackle_state_gate import (
    update_tackle_state,
    stamp_tackle_state,
    is_tes_drive_hold,
)
from game.defence.defence_lvl_1.pre_tackle_authority import (
    apply_pretackle_attempts,
    reset_pretackle_attempts,
    clear_pretackle_state,
    is_pretackle_managed,
    assign_def_line_ready,
    PTA_TARGET_SOURCE,
    apply_pre_tackle,
    is_pre_tackle_eligible,
)
# ── CONTACT AUTHORITY (Stage 1B, 2026-09-19) ─────────────────────────
# THE declared owner of contest FORMATION.  The trigger loop that used to
# live at the end of update_open_play() has MOVED into this module; the
# manager is now a CONSUMER of its single published decision.  The five
# historical formation writers (intercept_tacklers, pre_tackle_authority,
# pretackle_corridor, pretackle_machine, and this manager's trigger) keep
# writing exactly what they wrote before — they are evidence providers now,
# not deciders.  `contact_authority` has no module-scope project imports, so
# this import cannot create a cycle.
from game.defence.defence_lvl_1.contact_authority import (
    ContactOutcome,
    CHANNEL_MANAGER_TRIGGER,
    evaluate_contact,
    enter_tes,
)
from game.defence.defence_lvl_1.ruck_adjacent_pin import (
    enforce_ruck_adjacent_pin,
)
from game.defence.defence_lvl_1.tes_drive_structure import (
    apply_tes_drive_structure,
    clear_tes_drive_structure,
    is_structure_hold_eligible,
    open_tes_drive_chamber,
)
from game.defence.defence_lvl_1.tes_depth_authority import (
    apply_tes_depth_authority,
)
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
    stamp_drive_depth_x,
)
from game.managers.retreat_manager import RetreatManager
from game.defence.defence_lvl_1.spacing_authority import SpacingAuthority
from game.managers.convergence_manager import ConvergenceManager
from game.defence.defence_lvl_1.retreat_obligation import (
    RETREAT_OBLIGATION_STRICT,
    sync_retreat_obligation,
    enforce_retreat_obligation,
    strip_tackle_authority,
    is_retreat_obligated,
    audit_retreat_obligation_compliance,
    format_obligation_report,
)

# ── THE GLIDE BUDGET (single source) ──
# glide_budget owns the ONE glide ceiling (GLIDE_SPEED_MPS) shared by the
# depth-lock glide, the tackle-begin glide, the target_x glide and the
# two-radius attach band — they previously each declared their own 6.0.
# The module imports only stdlib at module scope, so this reach cannot
# create an import cycle in any load order.
from game.defence.defence_lvl_1.glide_budget import GLIDE_SPEED_MPS

# ── DEPTH-LOCK GLIDE (2026-09-07) ──
# The post-writer depth-lock clamp wrote ``_dlp.x = depth_lock_x`` /
# ``_dlp.y = depth_lock_y`` directly every TES frame.  The X pin is
# refreshed to the LIVE BC-tracked depth (role_depth_target_x = bc.x +
# WIDTH_OFFSETS[depth_role]) so when the carrier drives forward the whole
# depth-locked line was teleported onto the new depth in ONE frame (the
# TES-drive mid-range teleport, contact + a moment later).  The clamp now
# GLIDES toward the pin at at most this speed * dt per frame, so the line
# converges instead of jumping.  Single-sourced from the shared glide
# ceiling so it can never drift from the other glide authorities.  Player
# imports this name directly, so the alias is kept.
DEPTH_LOCK_GLIDE_SPEED_MPS: float = GLIDE_SPEED_MPS

# ── PRE-TACKLE PILE GATE (2026-09-10) ──
# Fallback radius (m) when the tuner is unavailable: only bodies this close to
# the ball carrier may be owned by the pre-tackle authority during the drive.
# A line body further out keeps playing its depth role — the authority must
# never depth-lock a defender that is not at the tackle.
PTA_PILE_GATE_RADIUS_M: float = 2.0


def _trace_enabled() -> bool:
    """Lazy read of the engine trace flag (avoids import cycle)."""
    try:
        from world.open_play_engine import DEBUG_OPE_TRACE
        return DEBUG_OPE_TRACE
    except Exception:
        return False


def _refresh_depth_lock_x(p, world) -> None:
    """DRIVE-DEPTH TRACK (2026-09-06): refresh a DEF_RETREAT_10 body's X pin
    from the live BC-tracked value (role_depth_target_x = bc.x +
    WIDTH_OFFSETS[depth_role]) so Player.update's X clamp follows the
    driven carrier (the wall-reforming retreat role SLIDES with the carrier).

    DEPTH-PIN CONTRACT (2026-09-09): DEF_LINE_READY / DEF_CHASE (the line
    bodies) do NOT refresh here — they hold their STATIC drive-entry pin
    (depth_lock_x captured once in apply_phase_invariants / _lock_depth) so
    the line does NOT depth-move during TACKLE_DOMINANCE (the defensive-line
    jitter fix).  Only DEF_RETREAT_10 (not a line-shape body) keeps the
    live-BC depth track so the wall can reform.  Canary-gated: when
    disabled, the static v2 pin holds for all.
    """
    if not getattr(p, "depth_locked", False):
        return
    from player.player import DefenderRole
    if getattr(p, "defender_role", None) is not DefenderRole.DEF_RETREAT_10:
        # DEPTH-PIN: DEF_LINE_READY / DEF_CHASE keep their static drive-entry
        # pin — no live-BC refresh, so no depth movement during the drive.
        return
    try:
        from world.rules.movement_contracts import drive_depth_track_enabled
        if not drive_depth_track_enabled(world):
            return
    except Exception:
        return
    _rtx = getattr(p, "role_depth_target_x", None)
    if _rtx is None:
        return
    try:
        _rtxf = float(_rtx)
    except (TypeError, ValueError):
        return
    if not math.isfinite(_rtxf):
        return
    # ── THE DEPTH BOUNDARY INVARIANT (defence boundary, 2026-09-19) ──
    # The positional lock is refreshed from the SAME live-BC depth law as
    # the stamp (``bc.x + WIDTH_OFFSETS[depth_role]``), so an unclamped lock
    # glides a non-fullback depth-role body PAST the defending team's own
    # tryline (+X = 110) — the depth-role equivalent of the publish defect.
    # The lock is therefore clamped through the same single boundary law the
    # target uses, so targets and positional locks can never disagree about
    # the legal envelope.  The dynamic fullback is exempt (its backfield
    # anchor is deliberately allowed slightly in-goal) and a legal lock
    # passes through unchanged, so a compliant frame is byte-identical.
    # Imported FUNCTION-LOCALLY (this module already lazy-imports inside
    # helpers) to avoid a module-scope edge.
    _bounded = None
    try:
        from game.defence.defence_lvl_1.depth_boundary_guard import (
            clamp_depth_target_x as _clamp_depth_target_x,
        )
        _bounded = _clamp_depth_target_x(world, p, _rtxf)
    except Exception:
        _bounded = None
    if _bounded is not None and math.isfinite(float(_bounded)):
        _rtxf = float(_bounded)
    p.depth_lock_x = _rtxf


class DefenceManager:
    """Sole defensive-order authority (one loop per side per frame).

    Holds the tuners + unified behaviour references and the deferred
    D-series pending state. Everything engine-owned (phase, ball carrier,
    TES participants) is reached through the ``ope`` argument so the
    manager never duplicates engine state.
    """

    def __init__(self, tuners, def_behaviour):
        """
        Args:
            tuners: DefenceTuners instance (centralised parameters).
            def_behaviour: DefensiveBehaviourUnified instance (the
                authoritative defensive targeting pipeline delegate).
        """
        self.tuners = tuners
        self.def_behaviour = def_behaviour

        # ── STAGE 3B: RETREAT + SPACING AUTHORITY ──
        # The manager owns the RetreatManager — the SOLE forced-retreat
        # writer (spacing-authority Y, anchor-authority X, TES freeze
        # compliant).  Every retreat path in the engine routes here.
        self.retreat = RetreatManager(tuners)

        # ── STAGE 3C: SPACING AUTHORITY ──
        # The manager owns ONE SpacingAuthority — the SOLE place that
        # assigns sides, builds the split-spacing chain (incl. role=None
        # pre-ruck), runs SpacingGuard + WidthGuard, and PUBLISHES both
        # frame dicts every frame:
        #     world.frame_spacing_targets  (Y authority)
        #     world.frame_width_targets    (X authority)
        # Every downstream writer is a PURE CONSUMER of those dicts.
        self.spacing = SpacingAuthority(tuners)

        # ── STAGE 3D: CONVERGENCE AUTHORITY ──
        # The manager owns ONE ConvergenceManager — the SOLE BC-closing
        # compute+publish authority (structured-close / MMDL / tackler-commit
        # targets).  Publishes world.frame_convergence_targets each frame.
        # Pure compute, never writes target_x/target_y.
        self.convergence = ConvergenceManager(tuners)

        # -- Deferred D-series state (applied on next OPEN_PLAY frame) --
        # Written by PTB-era handlers, consumed once by update_open_play().
        self.pending_anchor_x: float | None = None
        self.pending_spacing_y: float | None = None
        self.pending_retreat_reset: bool = False

    # ------------------------------------------------------------------
    # PENDING STATE (deferred D-series writes)
    # ------------------------------------------------------------------

    def reset_pending_state(self) -> None:
        """Clear deferred writes (kickoff reset / fresh sequence)."""
        self.pending_anchor_x = None
        self.pending_spacing_y = None
        self.pending_retreat_reset = False

    # ------------------------------------------------------------------
    # 0b. FORCED RETREAT -- sole retreat authority (Stage 3B)
    # ------------------------------------------------------------------

    def apply_forced_retreat(self, world, defenders, governor=None,
                             tes_retreat_priority=False,
                             force_anchor_x=False):
        """Run the unified forced-retreat writer through the owned
        RetreatManager.

        Sole authority for defender positioning during retreat:
          X <- anchor authority, Y <- spacing authority,
          TES freeze contracts enforced (frozen players never move).
        When *tes_retreat_priority* is True (RELEASE / PTB with a live
        tackle-escape), the writer routes every non-compliant defender
        through the TES-support fold-back writer so TES retreat targets
        win over the generic spacing/lane write.
        When *force_anchor_x* is True, every non-compliant defender is
        pinned to the anchor compliance X (ruck_x + 10), skipping the
        width-lane override (DRIVE-RELEASE retreat mode).
        Returns ``(compliant, non_compliant)``.
        """
        result = self.retreat.apply_forced_retreat(
            world, defenders, governor=governor,
            tes_retreat_priority=tes_retreat_priority,
            force_anchor_x=force_anchor_x,
        )
        # ── THE 10 m CONTRACT, ENFORCED WHERE THE ROLE ACTUALLY LIVES ──
        # (2026-09-18) The engine CREATES the obligation in
        # ``_update_release`` -- every non-marker, non-DH defender becomes
        # ``DEF_RETREAT_10`` at DOMINANCE -> RELEASE -- and it then lives
        # through RELEASE / PTB_PREP / PTB_EXEC / PTB / POST_RUCK.
        #
        # The veto used to run in ONE place, ``update_open_play``, which
        # returns immediately unless ``ope._phase == "OPEN_PLAY"``.  Those
        # retreat eras are engine SUB-phases of ``world.phase ==
        # OPEN_PLAY``, so for the whole era the contract was simply not
        # applied: an obligated body kept the ``depth_class`` PRIMARY /
        # SECONDARY authority it held from the previous OPEN_PLAY frame
        # (measured: 1316 violations in one 20 s rollout, and a
        # post-writer revocation count of ZERO) and never took the
        # ``PASSIVE`` branch the retreat movement contract keys on, so it
        # never converged on the line and never discharged.
        #
        # This IS the retreat authority every one of those eras routes
        # through, so the contract is applied here -- one call, every era,
        # no phase list to keep in sync.
        #
        # TES-GUARDED: never applied mid-tackle.  Inside
        # CONTACT/DRIVE/DOMINANCE the ruck X is a drive-entry value and a
        # body upfield could satisfy the arrival test prematurely, so
        # completion stays strictly outside the tackle-escape window.
        if not getattr(world, "tes_active", False):
            self.enforce_retreat_obligation(world, world.defending_team)
        return result

    def enforce_retreat_obligation(self, world, defenders):
        """Apply the 10 m retreat contract for THIS frame.

        Revokes tackle authority from every obligated body and completes
        the obligation of any body that has reached the retreat line
        (the engine's own arrival test).  Returns ``(gated, completed)``.

        Delegates to the single definition in
        ``retreat_obligation.enforce_retreat_obligation`` so the veto, the
        revocation and the completion can never drift apart.
        """
        return enforce_retreat_obligation(world, defenders)

    # ------------------------------------------------------------------
    # 0c. CONVERGENCE -- sole BC-closing authority (Stage 3D)
    # ------------------------------------------------------------------

    def run_convergence(self, world, defenders, bc):
        """Run the BC-convergence compute+publish through the owned
        ConvergenceManager.

        Pure compute — publishes ``world.frame_convergence_targets``;
        never writes target_x/target_y.  Returns the published dict.
        """
        return self.convergence.apply(world, defenders, bc)

    # ------------------------------------------------------------------
    # 0. BAND ASSIGNMENT -- depth-role band ownership (Stage 3A)
    # ------------------------------------------------------------------

    def assign_bands(self, defenders, world):
        """Assign persistent depth-role bands (A/B/C/D/CENTRE/WINGER).

        SOLE band authority (2026-08-24): every engine-level band
        assignment routes through this method. The underlying structural
        assignment is ``defensive_depth_roles.assign_depth_roles``
        (idempotent, hysteresis-protected); the manager owns WHEN it runs
        and owns any future band-policy changes.

        Returns the assignment dict from the structural layer.
        """
        return assign_depth_roles(defenders, world)

    # ------------------------------------------------------------------
    # 1. FRAME PREAMBLE -- depth-roles -> spacing -> width (ungated)
    # ------------------------------------------------------------------

    def run_frame_preamble(self, world):
        """Run the un-gated per-frame defensive geometry preamble.

        Pure compute: assigns depth roles, computes split-spacing Y and
        width X through their authorities, publishes
        world.frame_spacing_targets / world.frame_width_targets.
        NEVER writes target_x/target_y.

        Returns ``(spacing_targets, width_targets)``.
        """
        # FULLBACK HARD-EXCLUDE (2026-08-27): the dynamic fullback actor is a
        # backfield anchor owned by apply_fullback_geometry.  Exclude it from
        # the preamble array BEFORE any depth-role/spacing/width writer runs,
        # so it can never be stamped with a line role or a spacing/width slot
        # during RELEASE/PTB eras.  (Downstream _line_defenders already filters
        # it, so this is belt-and-suspenders and behaviour-neutral.)
        line_pool = [
            p for p in world.defending_team
            if not getattr(p, "fullback_role_active", False)
        ]
        return self.spacing.run(
            world,
            line_pool,
            governor=self.def_behaviour.line_governor,
        )

    # ------------------------------------------------------------------
    # 2. OPEN_PLAY DISPATCH -- the authoritative defensive pipeline
    # ------------------------------------------------------------------

    def update_open_play(self, ope, dt: float) -> None:
        """OPEN_PLAY defensive order issuance (verbatim relocation of
        OpenPlayEngine._update_defence).

        HARD GATE: runs ONLY during OPEN_PLAY. Dormant during all TES
        phases, RELEASE, PTB, and POST_RUCK.
        """
        world = ope.world

        # ---- TARGET_X GLIDE CAP (TARGET_JIGGLE fix, 2026-09-07) ----
        # Lazy import to avoid any cycle; the helper clamps a parked line
        # body's target_x swing to TARGET_X_GLIDE_SPEED_MPS * dt / frame.
        from game.defence.defence_lvl_1.target_x_glide import (
            glide_cap_target_x as _glide_cap_x,
            is_target_x_glide_eligible as _glide_eligible_x,
        )
        # ---- STALE-SEED LOCKDOWN (2026-09-09) ----
        # The seed_hold helper freezes a line body whose spacing-Y or
        # depth-role seed is stale (target_y restored to the frame-start Y,
        # target_x clamped into the anchor band, MovementState forced to
        # HOLD) so no lateral writer can jitter an undefined-geometry body.
        from game.defence.defence_lvl_1.seed_hold import (
            apply_seed_hold as _apply_seed_hold,
            is_seed_stale as _is_seed_stale,
        )
        # ---- WRITER-CONFLICT PRIORITY BANDS (2026-09-09) ----
        # Lateral (Y, 0.3 m) + depth (X, 0.5 m) priority bands.  Every writer
        # stage registers a proposal; the STRONGEST movement signal survives
        # and any competing writer whose value lies inside a band of the
        # dominant is IGNORED, killing last-writer-wins target jitter.
        # See game/defence/defence_lvl_1/writer_priority_bands.py.
        from game.defence.defence_lvl_1.writer_priority_bands import (
            begin_frame as _wpb_begin,
            capture as _wpb_capture,
            resolve as _wpb_resolve,
            suppressed_rows as _wpb_suppressed,
        )

        # ---- HARD GATE: defence AI runs ONLY during OPEN_PLAY ----
        if ope._phase != "OPEN_PLAY":
            return
        if is_tackle_phase(world):
            return

        # ═══════════════════════════════════════════════════════════════
        # RETREAT-OBLIGATION AUTHORITY GATE (2026-09-17)
        # ═══════════════════════════════════════════════════════════════
        # THE 10 m RETREAT CONTRACT, RESTORED.  A non-marker defender
        # that has not completed its 10 m retreat may keep its geometry,
        # spacing and width writers (so line shape is byte-identical) but
        # may NOT acquire tackle authority.  Historically the obligation
        # was discharged only because the tackle-exit chain was long
        # enough (DRIVE_END -> PTB_ENTER 3.69 s); convergence-first
        # RELEASE cut it to 1.289 s and the obligation silently expired
        # in `_post_ruck_marker_cleanup`'s unconditional role sweep.
        #
        # FRAME CONTRACT: sync + strip run BEFORE every
        # authority-acquisition writer of this dispatch (lane selection,
        # pre-tackle ownership, the 12 m convergence/pursuit promotion,
        # the engage machine).  The drift audit runs AFTER them.
        #
        # No phase timer, gate or exit condition is touched anywhere, so
        # convergence-first RELEASE and the PTB timing budget are
        # preserved.
        _gated_now = sync_retreat_obligation(world, world.defending_team)
        # WIRING PROOF: the gate is only a real contract if bodies are
        # actually gated.  Counted so the compliance probe can assert the
        # enforcement is LIVE rather than nominal (the structure-hold
        # defect was a nominal writer that never ran in a live rollout).
        self._retreat_gated_total = (
            getattr(self, "_retreat_gated_total", 0) + len(_gated_now)
        )
        self._retreat_gated_last_frame = len(_gated_now)

        # ---- Promote the defensive line to DEF_LINE_READY ----
        # Promote EVERY eligible line body (None / DEF_RETREAT_10 /
        # DEF_CHASE) to DEF_LINE_READY so the role is set BEFORE the
        # tackle cycle enters TACKLE_INIT. Markers / DH / tacklers /
        # fullback / frozen bodies are never touched (idempotent).
        #
        # RETREAT-OBLIGATION STAMP: this promotion is the boundary at
        # which a still-retreating body used to silently regain its
        # status as a line defender.  The ROLE write stays exactly as it
        # was (so spacing / width / hinge geometry are unchanged — the
        # role is not retained, which matters because DEF_RETREAT_10 sits
        # in _HINGE_EXCLUDED_ROLES); the OBLIGATION is carried as an
        # explicit authority flag instead.
        # RETREAT-OBLIGATION STAMP MOVED (2026-09-18): the debt is now
        # incurred ONLY at the once-per-cycle TACKLE_INIT promotion in
        # OpenPlayEngine.  This loop runs EVERY frame and the post-ruck
        # sweep oscillates the role DEF_RETREAT_10 <-> DEF_LINE_READY, so
        # stamping here re-armed a discharged debt every frame against the
        # WIDTH LANE that had run ahead of the body (``ruck_x + lane
        # offset`` advances with the ruck) -- measured as gates of 30-59 s
        # whose real shortfall never exceeded 1.8 m.
        for p in world.defending_team:
            assign_def_line_ready(p)

        # ---- RETREAT CONTRACT: REVOKE + COMPLETE (engine truth) ----
        # The engine's own completion test lives in
        # ``_update_post_ruck_open_play`` -- ``if d.x >= target_x ->
        # DEF_LINE_READY`` -- but that phase is only 3 frames long
        # (POST_RUCK_DURATION_S = 0.05 s).  The retreat itself is driven
        # through RELEASE/PTB and continues into OPEN_PLAY, so the SAME
        # test is applied here, through the ONE shared entry point, or a
        # body that was still short when POST_RUCK ended would carry
        # DEF_RETREAT_10 forever (measured: 71% of OPEN_PLAY frames gated,
        # runs of 11.8 s, 1316 authority violations).  This also revokes
        # any authority the previous frame granted.
        self.enforce_retreat_obligation(world, world.defending_team)

        # ---- GLOBAL DEFENSIVE TOP-SPEED CEILING stamp ----
        # Stamp the single authoritative defensive top speed onto every
        # defender BEFORE any movement writer runs. Player.update()
        # enforces it as a hard ceiling, so no subsystem can move a
        # defender faster than the unified maximum.
        _def_ceiling = self.tuners.max_defensive_speed_mps
        for d in world.defending_team:
            d.max_defensive_speed_mps = _def_ceiling

        # ---- GEOMETRY SMOOTH-TIME STAMP ----
        # Stamp the tuner value onto every defender so tuners stays the
        # single source of truth for smooth-damp time. Never zeroed --
        # PTB-era suppression is handled by Player's own phase gate.
        _smooth_time = self.tuners.geometry_smooth_time_s
        for d in world.defending_team:
            d._geometry_smooth_time_s = _smooth_time

        bc = world.ball_carrier
        if bc is None:
            return

        defenders = [p for p in world.defending_team if not p.frozen]

        # ---- STALE-SEED PRE-FRAME POSITION SNAPSHOT (2026-09-09) ----
        # The post-writer seed_hold pass freezes a stale-seed line body's
        # lateral target back to its frame-START Y.  Capture every defender's
        # position at the very start of this frame so the freeze reference is
        # the frame-authoritative start position (mirrors the tackle-begin
        # glide snapshot).
        pre_pos = {p.player_id: (float(p.x), float(p.y)) for p in defenders}
        # ---- WRITER-CONFLICT BANDS: open the capture frame ----
        # Seeds the frame-start reference used as the "strongest movement
        # signal" tie-break, then clears this frame's proposal registry.
        _wpb_begin(world, defenders)

        # ---- GLOBAL DEFENDER REACTION GOVERNOR: advance clock ONCE/frame ----
        # Player.update() may run twice per OPEN_PLAY frame for SDM-retreat
        # defenders; the reaction window must be wall-clock time.
        for d in defenders:
            d.step_defence_reaction(dt)

        # ---- Anchor + governed width + even lane spread ----
        ruck_x = getattr(world, "ruck_x_m", bc.x if bc else 60.0)
        ruck_y = getattr(world, "ruck_y_m", bc.y if bc else 35.0)
        # TASK VI: anchor consumes the ANCHOR AUTHORITY (ruck_x + 10) --
        # never a bare field read or an inline derivation.
        anchor_x = _anchor_m(world)

        def_line_width = governed_width_m(world, self.tuners)
        n = len(defenders)
        lane_y = []
        half_width = def_line_width * 0.5
        for i in range(n):
            if n > 1:
                t = (i / (n - 1)) * 2.0 - 1.0  # -1..1
            else:
                t = 0.0
            lane_y.append(ruck_y + t * half_width)

        # ---- Deferred D-series pending state (from PTB transitions) ----
        if self.pending_anchor_x is not None:
            anchor_x = self.pending_anchor_x
            self.pending_anchor_x = None
        if self.pending_spacing_y is not None:
            ruck_y = self.pending_spacing_y
            self.pending_spacing_y = None
        if self.pending_retreat_reset:
            ope.defensive_line_manager.reset_retreat_state()
            self.pending_retreat_reset = False

        # ---- DEFENSIVE LINE ANCHOR RE-PUBLISH ----
        # Re-publish the authoritative local anchor EVERY OPEN_PLAY defence
        # frame so later readers never see a zeroed anchor after RELEASE.
        # TASK G ANCHOR AUTHORITY: goes through set_anchor so a lock is
        # never silently overwritten (identical value = no-op).
        set_anchor(world, anchor_x)

        # ── STAGE 3D: PUBLISH CONVERGENCE DICT before the DBU dispatch ──
        # The owned ConvergenceManager computes the single BC-closing target
        # set for PRIMARY/SECONDARY (tackle_role OR depth_class closers) and
        # publishes world.frame_convergence_targets.  Every downstream
        # convergence writer (tackler-commit / structured-close / MMDL) is a
        # PURE CONSUMER of this dict -- it never recomputes a BC target.
        self.run_convergence(world, defenders, bc)

        # ---- Dispatch to DefensiveBehaviourUnified ----
        # WRITER-CONFLICT BANDS: the behaviour layer is the frame's largest
        # multi-writer stage (structured open play / unified close / role
        # movement / retreat / MMDL / finalise).  Capture its NET per-defender
        # proposal, attributed by each defender's resulting ``target_source``.
        with _wpb_capture(world, defenders, "behaviour"):
            self.def_behaviour.apply(world, defenders, anchor_x, lane_y)

        # ── FX-2 (TA8-2026-08-25): POST-CLASSIFY CONVERGENCE RE-PUBLISH ──
        # The pre-dispatch publish above feeds MMDL decision inputs but is
        # built from LAST frame's PRIMARY/SECONDARY stamps (one-frame lag).
        # Re-publish now, AFTER classify_depth_all / select_lane_tacklers
        # ran inside apply(), so world.frame_convergence_targets carries
        # THIS frame's classifications for every downstream reader (commit
        # targets, audits, next-frame MMDL).  Dual-publish is intentional:
        # pre-apply = decision input, post-apply = authoritative truth.
        self.run_convergence(world, defenders, bc)

        # ---- MODULE 1: FULLBACK GEOMETRY AUTHORITY ----
        # Authoritative LAST target writer of the frame. Re-asserts the
        # fullback's deep backfield anchor AFTER every defensive line
        # writer. Self-gates on fullback_role_active.
        #
        # HIGH-VELOCITY RECOVERY ARMING (2026-09-21): this dispatch is the
        # OPEN_PLAY seam that owns live defensive geometry, so it is also the
        # seam that arms the fullback recovery locomotion.  Armed HERE (and
        # only here, plus the PTB-era re-assert) the lever is live exactly
        # while the defensive pipeline is: a frame that bails out above (no
        # ball carrier, wrong phase, tackle phase) never arms it, and
        # kickoff_engine.reset_for_kickoff() disarms it at the start of every
        # sequence.  The predicate itself (debt band, tackle authority, TES,
        # kickoff flight) lives in fullback_role so no caller can widen it.
        world.fullback_recover_allowed = True
        with _wpb_capture(world, defenders, "fullback_geometry"):
            for _d in world.defending_team:
                apply_fullback_geometry(world, _d)

        # ---- FULLBACK STATE PUBLISH ----
        # Expose per-frame fullback actor + anchor for debug overlays /
        # tooling after the defensive pipeline has run.
        _fb_actor = None
        _fb_tx = None
        _fb_ty = None
        _fb_obj = None
        for _d in world.defending_team:
            if getattr(_d, "fullback_role_active", False):
                _fb_actor = _d.player_id
                _fb_tx = getattr(_d, "target_x", None)
                _fb_ty = getattr(_d, "target_y", None)
                _fb_obj = _d
                break
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

        # ---- DEFENCE + FULLBACK AUDIT (self-limiting) ----
        audit_fullback_state(world)

        # ---- TARGET_X GLIDE CAP (TARGET_JIGGLE fix, 2026-09-07) ----
        # The TARGET_JIGGLE audit fires when a STATIONARY line body's
        # target_x ranges > 5.0 m across a 10-frame window — competing
        # movement/spacing writers re-pointing a parked defender's
        # target_x.  Glide-cap the target_x of parked line bodies
        # (RETREAT/DRIFT/HOLD/SLOW) so it converges over frames instead of
        # seesawing metres.  Exempt pursuit/commit/fullback/marker/TES-
        # authoritative bodies.  Runs AFTER every writer (apply + fullback
        # geometry), BEFORE p.update(dt) integration and the audit, so the
        # capped target is what the integrator + freeze_audit read.
        import os as _os
        _tx_glide_on = _os.environ.get("RLM_TARGET_X_TRACE", "") == "1"
        for _dg in defenders:
            if not _glide_eligible_x(_dg, world):
                continue
            _prev_tx = getattr(_dg, "_prev_target_x_glide", None)
            _raw_tx = float(getattr(_dg, "target_x", 0.0) or 0.0)
            # BOUNDED FROM FRAME ONE (2026-09-20): the tuner/world handles
            # let the glide seed its baseline from the body's own position
            # and cap on the first call, instead of landing the full
            # uncapped writer swing on the frame the baseline is missing.
            _glide_cap_x(_dg, dt, tuners=self.tuners, world=world)
            if _tx_glide_on:
                _capped_tx = float(getattr(_dg, "target_x", 0.0) or 0.0)
                print(
                    f"[TARGET_X] frame={getattr(world, 'frame_count', 0)} "
                    f"id={getattr(_dg, 'player_id', '?')} "
                    f"role={getattr(_dg, 'depth_role', None) or '-'} "
                    f"src={getattr(_dg, 'target_source', '')} "
                    f"raw_tx={_raw_tx:.2f} prev_tx={_prev_tx:.2f} "
                    f"capped_tx={_capped_tx:.2f}"
                )

        # ---- STALE-SEED LOCKDOWN (2026-09-09) ----
        # A line body whose spacing-Y or depth-role seed is stale must NOT
        # move on an undefined-geometry frame.  Freeze lateral writers
        # (target_y restored to the frame-start Y), clamp target_x drift
        # (into the anchor band), and force MovementState to HOLD.  Runs
        # AFTER every writer (apply + fullback geometry + target_x glide),
        # BEFORE the integration loop, so the frozen target/state is what
        # the integrator + freeze_audit read.
        with _wpb_capture(world, defenders, "seed_hold"):
            _seed_held = _apply_seed_hold(world, defenders, pre_pos=pre_pos)
        if _seed_held and _tx_glide_on:
            for _sh in _seed_held:
                print(
                    f"[SEED-HOLD] frame={getattr(world, 'frame_count', 0)} "
                    f"id={getattr(_sh, 'player_id', '?')} "
                    f"role={getattr(_sh, 'depth_role', None) or '-'} "
                    f"state=HOLD target_y={float(getattr(_sh, 'target_y', 0.0) or 0.0):.2f} "
                    f"target_x={float(getattr(_sh, 'target_x', 0.0) or 0.0):.2f}"
                )

        # ---- WRITER-CONFLICT PRIORITY BANDS: RESOLVE (final word) ----
        # Runs AFTER every writer (behaviour + fullback + glide + seed_hold)
        # and BEFORE integration.  Per defender, per axis: the dominant
        # (strongest) proposal wins; every other proposal within the band of
        # the dominant is ignored.  Gated by
        # tuners.writer_priority_bands_enabled (default True).
        _wpb_won = _wpb_resolve(world, defenders, self.tuners, ope=ope)
        if _wpb_won and _tx_glide_on:
            _rows = _wpb_suppressed(world)
            for _wr in _wpb_won:
                print(
                    f"[WPB] frame={getattr(world, 'frame_count', 0)} "
                    f"id={getattr(_wr, 'player_id', '?')} "
                    f"role={getattr(_wr, 'depth_role', None) or '-'} "
                    f"axis={getattr(_wr, '_band_axis', '-')} "
                    f"tx={float(getattr(_wr, 'target_x', 0.0)):.2f} "
                    f"ty={float(getattr(_wr, 'target_y', 0.0)):.2f} "
                    f"src={getattr(_wr, 'target_source', '')}"
                )
            if _rows:
                print(f"[WPB] suppressed_this_frame={len(_rows)}")

        # ── CATCH X-AUTHORITY (2026-09-10) ──
        # Sole X-writer during the CATCH (chase-to-catch) transition.  Armed
        # by catch_system._perform_catch(); runs as the LAST X write of the
        # dispatch (after every writer, after the writer-priority-band
        # resolution) so EXACTLY ONE writer owns target_x for genuine line
        # defenders while the lock is armed.  Y is never touched (spacing
        # authority keeps Y).  No-op when the lock is off (normal play).
        from game.defence.defence_lvl_1.catch_x_authority import (
            apply_catch_x_authority as _apply_catch_x,
        )
        _apply_catch_x(world, defenders, bc, self.tuners)

        # ---- LINE-BREAK STATIC TARGET GUARD (2026-09-10) ----
        # A defender whose target_x is pinned to a static value after the line
        # is broken produces no vx, never closes into TACKLE_RADIUS_M, and the
        # tackle pipeline stays unarmed (the pre-tackle classifier also requires
        # CHASE/SHOOT for is_closing_posture).  This is the FINAL X recovery
        # write of the dispatch: rewrite target_x = bc.x and force CHASE so the
        # pipeline can arm again.  Runs after catch-x (and after the writer-
        # priority-band resolve) and before movement integration, so nothing
        # else can re-pin the target afterward.
        from game.defence.defence_lvl_1.line_break_static_guard import (
            enforce_line_break_static_guard as _enforce_lb_static,
        )
        _enforce_lb_static(world, defenders, bc, self.tuners)

        # ═══════════════════════════════════════════════════════════════
        # OBLIGATED-BODY RETREAT AUTHORITY -- THE LAST TARGET WRITE
        # ═══════════════════════════════════════════════════════════════
        # An obligated body is, BY CONTRACT, retreating.  It must reach the
        # 10 m line, and no other writer may hand it a target short of that
        # line.  Without this, the contract cannot discharge, and that was
        # measured live (2026-09-18): the MMDL band writer stamped
        # ``band:mmdl_RETREAT`` with ``target_x`` exactly 10 m short of the
        # anchor (``ruck_x`` rather than ``ruck_x + 10``) and OUTRANKED the
        # retreat writer in the writer-priority bands, so an obligated body
        # held a 4 m gap that GREW to 13 m while the carrier ran:
        #
        #     f973   x=34.47  anchor=38.83  tx=38.83  retreat_non_compliant
        #     f1729  x=45.47  anchor=58.53  tx=48.53  band:mmdl_RETREAT
        #
        # The body converged toward the anchor only while the retreat
        # writer happened to win, then was re-targeted short of it, so the
        # obligation never completed for 10 bodies over the whole rollout.
        #
        # This is the SAME documented authority the RELEASE and POST_RUCK
        # completion loops already use -- ``apply_forced_retreat`` with
        # ``force_anchor_x=True``, which pins ``target_x`` to the anchor
        # compliance line and skips the width-lane override -- applied as
        # the dispatch's FINAL target write (after the line-break guard,
        # after the writer-priority-band resolve) and immediately before
        # integration, so nothing can re-pin the target afterwards.  The
        # caller-side enforcement (revoke + complete) then has a body that
        # actually converges on the line.
        #
        # It writes NOTHING for a compliant body and nothing for a body
        # that is not obligated, so line shape on every other frame is
        # byte-identical.
        _obligated_line = [
            p for p in defenders
            if is_retreat_obligated(p, world)
        ]
        # ── THE OBLIGATION IS A RUCK-ALIGNMENT CONTRACT (2026-09-28) ──
        # This is the dispatch's LAST X write, and it pins target_x to
        # ``ruck_x + 10`` -- a per-ruck CONSTANT.  That is correct while the
        # line is intact (the body owes alignment) and WRONG the moment the
        # carrier is past the line: the pin then holds a static target on a
        # body whose job has become to close, so it produces no forward
        # velocity and the tackle pipeline never arms.
        #
        # MEASURED (probe tmp_fb_line_writers, seed 20260827, ids 203/212,
        # frames 1690-2170): the obligated-retreat pin owned the final
        # target_x with ``static_after_break`` on the frames it ran, and
        # ``line_break_static_guard`` -- the dispatch's dedicated post-break
        # recovery write -- VETOES exactly these bodies
        # (``is_static_guard_recovery_eligible`` -> the retreat-obligation
        # veto), so with the line broken nothing else in the frame can
        # clear the static pin.  The fix is at the source: while the line is
        # broken, a genuine line break is the survival contract and the
        # alignment pin is suspended, so the ordinary closing writers
        # (bands / behaviour / pursuit) own the body.
        #
        # Reversible: ``world.retreat_pin_yields_to_break = False`` restores
        # the unconditional pin (pre-change engine) with no code revert.
        from game.defence.defence_lvl_1.movement_mode_decision import (
            carrier_past_defensive_line as _carrier_past_line,
        )
        _line_broken_for_pin = False
        try:
            _line_broken_for_pin = bool(_carrier_past_line(world, bc))
        except Exception:
            _line_broken_for_pin = False
        _pin_yields = bool(
            getattr(world, "retreat_pin_yields_to_break", True)
        )
        if _obligated_line and not (_pin_yields and _line_broken_for_pin):
            self.retreat.apply_forced_retreat(
                world, _obligated_line,
                tes_retreat_priority=False,
                force_anchor_x=True,
            )

        # ── POST-MISS PURSUIT AUTHORITY — THE DISPATCH'S FINAL TARGET WRITE ──
        # (2026-09-26) A defender the carrier beat OUTRIGHT owns its pursuit
        # for a bounded window after the miss (``post_miss_pursuit``).  This
        # is the LAST target write of the dispatch: it runs after every other
        # writer -- including the obligated-body retreat authority above --
        # and immediately before movement integration, so nothing can re-pin
        # the body afterwards and the chase is AUTHORED rather than withheld.
        #
        # WHY IT MUST BE HERE AND NOT EARLIER: the probe measured that a chase
        # point at this tier wins EVERY contest it enters (OVERRIDDEN_BY == 0
        # over 77,681 post-miss body-frames).  The defect was never the
        # arbitration order -- it was that for a beaten body NO chase point was
        # ever PROPOSED (NO_PURSUIT_INTENT), because the freeze pin, the
        # surviving commit latches and the retreat authority each declined it
        # in turn.  The entitlement removes those refusals; this write is what
        # guarantees a live pursuit command exists on every entitled frame.
        #
        # It writes nothing for a body that is not entitled, so every other
        # frame is byte-identical, and the whole path is reversible with
        # RLM_POST_MISS_PURSUIT=0.
        from game.defence.defence_lvl_1.post_miss_pursuit import (
            owned_bodies as _owned_pursuit_bodies,
            pursue_carrier as _pursue_carrier,
        )
        for _owned in _owned_pursuit_bodies(world, defenders):
            _pursue_carrier(world, _owned, bc)

        # ---- Apply movement ----
        # MMDL EXCEPTION: in structured OPEN_PLAY the Movement Mode Decision
        # Layer has ALREADY integrated position for defenders it moved this
        # frame (_mmdl_moved). Running Player.update() again would
        # DOUBLE-INTEGRATE their velocity (~2x speed). Skip those.
        for p in defenders:

            if p.frozen:
                continue
            if getattr(p, "_mmdl_moved", False):
                p._mmdl_moved = False
                continue
            p.update(dt)

        # ── RETREAT-OBLIGATION DRIFT AUDIT + POST-WRITER REVOCATION ──
        # Runs AFTER every authority-acquisition writer of this dispatch
        # (lane selection, pre-tackle ownership, the ranked depth
        # assignment, the line-break force-chase override, the
        # convergence/pursuit promotion, the target writers) and BEFORE
        # the tackle trigger, so the trigger loop's early `return` cannot
        # skip it.
        #
        # TWO STEPS, AND THE ORDER IS LOAD-BEARING:
        #
        #  1. AUDIT first.  This is the DRIFT DETECTOR: it names every
        #     obligated body that some writer granted authority to.  The
        #     defence has TWELVE authority writers, not the five the
        #     original design assumed, so this is how an un-vetoed writer
        #     announces itself instead of being silently blessed.
        #     ``RLM_OBLIGATION_STRICT=1`` promotes the finding to a hard
        #     failure (that is how the verifier PROVES the veto set is
        #     complete); the default is log-only.
        #
        #  2. REVOKE second.  The pre-writer veto can only cover writers
        #     that consult it; this revocation makes the guarantee
        #     INDEPENDENT of that enumeration, so "an obligated body
        #     cannot hold tackle authority" holds for every writer,
        #     present and future, and the trigger below always sees a
        #     clean state.  Revocation happens AFTER the audit so the
        #     audit still reports what was granted.
        _obligation_violations = audit_retreat_obligation_compliance(
            world, world.defending_team,
            raise_on_violation=RETREAT_OBLIGATION_STRICT,
        )
        if _obligation_violations:
            self._retreat_revoked_total = (
                getattr(self, "_retreat_revoked_total", 0)
                + len(_obligation_violations)
            )
            if RETREAT_OBLIGATION_STRICT:
                print(format_obligation_report(world, world.defending_team))
            for _ov in world.defending_team:
                if is_retreat_obligated(_ov, world):
                    strip_tackle_authority(world, _ov)

        # ── FIELD BOUND ON EVERY AUTHORED TARGET (2026-09-25) ──────────
        # THE reachability guarantee for the defensive frame: after every
        # OPEN_PLAY writer has run, bound each defender's target into the
        # playable field.  This pass runs AFTER the integration loop above,
        # so it cannot affect the frame's own motion -- it exists for the
        # bodies that integration SKIPS (``_mmdl_moved``), whose targets are
        # declared but never read by ``Player.update``: without it an MMDL
        # body could sit on a stale out-of-field target indefinitely.
        # ``Player.update`` itself bounds the target it is about to consume
        # (the ``TARGET FIELD BOUND`` block there), so between the two every
        # defender's target is in-field on every path.
        #
        # WHY HERE.  The individual authorities each own a target and each
        # exempts itself from the writer-priority bands ("their targets must
        # snap"), so bounding them one by one means chasing a dozen writers.
        # A FIELD BOUND is not an authority conflict, though: an out-of-field
        # target is UNREACHABLE for every authority equally, because
        # ``Player._clamp_position`` refuses to move the body past the
        # boundary.  Clamping one here can therefore only make a commanded
        # move more achievable, whichever writer demanded it.
        #
        # MEASURED (probe_open_play_stall_authority, seed 20260827): an
        # out-of-field target pins the body against the wall with ``vx=vy=0``
        # while its authority keeps commanding a forward approach -- the
        # reported "agents remain stationary in OPEN_PLAY with static
        # targets".  The remaining out-of-field authors after the
        # pre-tackle / commit / band clamps were struct_freeze_timer (1075
        # body-frames), tes_depth_authority (142), mmdl_CHASE (33),
        # tes_drive_hold (10), pre_tackle (9) and line_break_force (4).
        #
        # The clamp is the IDENTITY for an in-field target, so a compliant
        # frame stays byte-identical and no in-field behaviour moves.
        from game.defence.defence_lvl_1.target_field_bound import (
            clamp_target_to_field as _clamp_target_to_field,
        )
        for _d in (getattr(world, "defending_team", None) or ()):
            if _d is None:
                continue
            _tx = getattr(_d, "target_x", None)
            _ty = getattr(_d, "target_y", None)
            if _tx is None or _ty is None:
                continue
            _cx, _cy = _clamp_target_to_field(_tx, _ty)
            if _cx != _tx:
                _d.target_x = _cx
            if _cy != _ty:
                _d.target_y = _cy

            # ── POSITION BOUND (same pass) ────────────────────────────
            # The target bound above only helps a body that INTEGRATES.
            # MEASURED (verify_open_play_stall_authority, seed 20260827):
            # five defenders held ``src=struct_freeze_timer`` at
            # ``x = 120.13 / 120.15 / 120.18 / 120.52 / 120.53`` --
            # OUTSIDE the field -- with ``target_x = 120.00`` in-field,
            # ``speed = 0.0`` and ``move_speed = 7.89``, state CHASE, for
            # 2581 consecutive frames each.  A body resting outside the
            # field cannot reach any target inside it, which is the
            # reported "agents remain stationary in OPEN_PLAY with static
            # targets".
            #
            # These bodies never run ``Player.update`` on those frames
            # (the MMDL / structure-hold paths own their position), so
            # ``Player._clamp_position`` never bounds them, and the
            # collision pass cannot either (it is skipped while
            # ``world.tes_active``).  This IS the post-writer choke point
            # that sees every defender every OPEN_PLAY frame, so the
            # positional invariant is restored here.
            #
            # IDENTITY for an in-field body: no compliant frame moves.
            if _d.x < FIELD_MIN_X:
                _d.x = FIELD_MIN_X
            elif _d.x > FIELD_MAX_X:
                _d.x = FIELD_MAX_X
            if _d.y < FIELD_MIN_Y:
                _d.y = FIELD_MIN_Y
            elif _d.y > FIELD_MAX_Y:
                _d.y = FIELD_MAX_Y
            _bd = getattr(_d, "body", None)
            if _bd is not None:
                _bd.sync()

        # ═══════════════════════════════════════════════════════════════
        # CONTACT AUTHORITY — CONTEST FORMATION (Stage 1B, 2026-09-19)
        # ═══════════════════════════════════════════════════════════════
        # THIS DISPATCH IS the Open Play <-> Contact boundary.  As of Stage
        # 1B the FIRE decision is no longer taken here: the trigger loop
        # MOVED into `contact_authority.evaluate_contact`, the DECLARED
        # owner of contest formation, which publishes ONE authoritative
        # outcome per frame:
        #
        #       CONTACT_FAILED / CONTACT_FORMED
        #
        # The manager is now a PURE CONSUMER of that decision:
        #   - CONTACT_FORMED -> hand the declared winner to the existing,
        #     unchanged single TES entry point and end the dispatch (the
        #     removed loop's `return` did exactly this);
        #   - CONTACT_FAILED -> fall through; nothing follows this block, so
        #     the frame ends exactly as it did before.
        #
        # FRAME POSITION IS UNCHANGED: this call occupies the same place the
        # inline loop did — after every authority-acquisition writer (lane
        # candidacy, pre-tackle ownership, the corridor, the engage machine)
        # and after the drift audit.  The predicate, the contact radius, the
        # thresholds, the iteration order, the first-wins short-circuit and
        # the two failed-contact carrier effects are byte-identical to the
        # loop that was removed; that equivalence is machine-checked in
        # section [3] of verify_contact_authority_contract.py, which is why
        # this relocation ships WITHOUT a behavioural canary (a canary whose
        # "off" arm restored the old loop would recreate a second decider).
        #
        # The five historical formation writers remain in place and keep
        # writing what they always wrote; they are evidence providers to the
        # authority now, not deciders.
        _contact_decision = evaluate_contact(
            world, ope, defenders, bc, self.tuners,
        )
        if _contact_decision.outcome is ContactOutcome.CONTACT_FORMED:
            # THE HANDOVER IS OWNED BY THE AUTHORITY (Stage 1B, 2026-09-19):
            # ``enter_tes`` is the single TES-entry choke point.  The decision
            # is already published by ``evaluate_contact`` above, so this call
            # only performs the handover (already_decided=True) — byte-for-byte
            # the ``ope._start_tackle(bc, winner)`` that used to be here.
            # STAGE 2A: the population is handed over so the preservation
            # layer can hold the in-radius bodies the decider's short-circuit
            # never reached.  The handover itself is byte-for-byte the same
            # call (``already_decided=True`` records nothing new).
            enter_tes(world, ope, bc, _contact_decision.winner,
                      CHANNEL_MANAGER_TRIGGER, already_decided=True,
                      defenders=defenders)
            return

    # ------------------------------------------------------------------
    # 3. TES-DRIVE LINE BEHAVIOUR -- NON-TACKLERS ONLY
    # ------------------------------------------------------------------

    def update_tes_drive(self, ope, dt: float) -> None:
        """TES-drive/contact: non-tackler defenders fold back (TES-exclusive).

        TES ISOLATION CONTRACT (revised 2026-08-29): only the ball carrier,
        the dummy half, the registered tacklers, and the TES markers remain
        in TES isolation.  Every other defender (DEF_LINE_READY / DEF_CHASE /
        DEF_RETREAT_10 and any unisolated line body) folds back behind the
        ruck line while the tackle runs.

        STAGE 3H CONDUCTOR MODEL: TES EXCLUSIVELY owns target_x/target_y for
        non-tes-isolated defenders during CONTACT/DRIVE/DOMINANCE.  This
        method does NOT invoke ``def_behaviour.apply()`` — its TES-non-tackler
        branch writes ``apply_forced_retreat`` X then OVERWRITES it with
        ``role_depth_target_x`` (two writers, same frame, the TARGET_JIGGLE
        root cause).  Instead each non-tes-isolated defender receives ONE
        TES-owned fold-back write via the sole RetreatManager
        (``apply_tes_support_retreat``: X <- anchor authority, Y <- frame
        spacing, RETREAT, 0.5x ceiling) and is integrated once.  The
        tackler/BC physics and the RELEASE marker assignment stay owned by
        the phase handlers.
        """
        # ── CHAMBER BOUNDARY — OWNED BY THE ENGINE (2026-09-11) ──
        # ``OpenPlayEngine._start_tackle`` bumps ``world._tes_tackle_seq``
        # at the single entry point for every tackle, and the structure-hold
        # authority validates each latch against it.  The manager does NOT
        # open chambers: a manager-side boundary was tried twice and failed
        # both times — a "clear on phase exit" flag is never cleared on the
        # post-tackle paths that do not call this method, and a
        # ``is_tackle_phase`` rising-edge test never re-fires because that
        # predicate does not return False between tackles.  Either way the
        # first tackle's lanes survived into every later tackle (a single
        # chamber spanning ~1000 drive frames; bodies reading 5–23 m off
        # lane once a later tackle moved the line).
        if ope._phase not in ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE"):
            # INTRA-TACKLE FRAMES (TACKLE_INIT) ARE NOT A CHAMBER EXIT.
            # This method also runs under TACKLE_INIT (and any other TES
            # frame outside the three drive sub-states).  Ending the
            # chamber there wiped the lane latch on every alternating
            # frame, so the next CONTACT/DRIVE frame re-seeded every lane
            # from a line that had already moved — the measured per-frame
            # lane ratchet (10–18 m of lateral drift, nothing ever
            # freezing).  The latch is TACKLE-scoped: it survives until
            # the tackle is genuinely over.
            if is_tackle_phase(ope.world):
                return
            # Genuine chamber exit (RELEASE / PTB / POST_RUCK / OPEN_PLAY).
            # Clear any live pre-tackle attempt state so the NEXT drive
            # reseeds fresh anchors/states, and INSTANTLY UNFREEZE the whole
            # defensive line: drop every TES structure-hold stamp and
            # re-point DEF_RETREAT_10 depth targeting from the drive-ENTRY
            # ruck_x to the LIVE ruck_x (the post-tackle ruck written by
            # _prepare_release), so the wall reforms off the new ruck.  See
            # tes_drive_structure.py.
            if getattr(self, "_pta_was_active", False):
                self._pta_was_active = False
                reset_pretackle_attempts(ope.world)
            if getattr(self, "_tes_structure_was_active", False):
                self._tes_structure_was_active = False
            clear_tes_drive_structure(ope.world, self.tuners)
            return

        world = ope.world
        bc = world.ball_carrier
        if bc is None:
            return

        # TES participants (primary + joiners + attached) are owned by TES —
        # never run the defensive pipeline on them.  Markers / DH / fullback
        # are skipped by the role pool below as well.
        tes_ids = set()
        if ope.primary is not None:
            tes_ids.add(ope.primary.player_id)
        for j in ope.joiners:
            tes_ids.add(j.player_id)
        for _att_pid in getattr(ope, "_attached_player_slots", {}):
            tes_ids.add(_att_pid)

        TES_MOVER_ROLES = frozenset({
            DefenderRole.TACKLER_1,
            DefenderRole.TACKLER_2,
            DefenderRole.TACKLER_3,
            DefenderRole.MARKER_A,
            DefenderRole.MARKER_B,
        })

        # DRIVE-DEPTH TRACK (2026-09-06): the non-tackler pool is built from
        # UNFROZEN bodies ONLY.  The sole freeze authority (MOVEMENT_RULES)
        # DEPTH-LOCKS the line roles (DEF_LINE_READY / DEF_CHASE /
        # DEF_RETREAT_10) on entry to CONTACT / DRIVE / DOMINANCE — they are
        # NOT hard-frozen, so they ARE in this pool.  Their DEPTH (X) is
        # pinned by the depth-lock clamp, their LATERAL (Y) is held (lock
        # roles) or free (DEF_RETREAT_10).  The pre-tackle authority owns the
        # DEF_LINE_READY bodies; the retreat writer owns DEF_RETREAT_10.
        defenders = [
            p for p in world.defending_team
            if (
                not p.frozen
            )
            and p is not bc
            and p.player_id not in tes_ids
            and getattr(p, "attack_role", None) != "dummy_half"
            and getattr(p, "defender_role", None) not in TES_MOVER_ROLES
            and not getattr(p, "fullback_role_active", False)
            # ATTACH-FLAG EXCLUSION (2026-09-11): a body carrying the attach
            # flag is a TES joiner, NOT a line body.  The slot registry is
            # cleared at every TACKLE_INIT (``_start_tackle``), but a stale
            # per-player flag from a tackle that ended without
            # ``_detach_all_tacklers`` (broken tackle / turnover) could
            # survive; keying the exclusion on the flag itself makes the
            # line authority robust to any registry/flag divergence and
            # keeps it strictly on the line proper.
            and not getattr(p, "tes_attached", False)
        ]
        if not defenders:
            return

        # ── TACKLE-BEGIN GLIDE PRE-FRAME SNAPSHOT (2026-09-07) ──
        # Capture every non-tackler's position at the very START of this
        # frame so the tackle-begin glide authority can measure ALL movement
        # this frame (p.update, soft-follow, depth-lock clamp) and clamp any
        # teleport-scale step back to the legal glide ceiling.
        pre_pos = {p.player_id: (float(p.x), float(p.y)) for p in defenders}

        # ── NO DEPTH PIN AND NO FREEZE DURING THE DRIVE (2026-09-10) ──
        # DEF_LINE_READY / DEF_CHASE / DEF_RETREAT_10 carry movement authority
        # for CONTACT / DRIVE / DOMINANCE (MOVEMENT_RULES), so no drive body
        # may be depth-locked or frozen.  Clearing the lock means the live
        # depth-role geometry owns BOTH axes: X tracks the carrier
        # (bc.x + WIDTH_OFFSETS[depth_role]) and Y is the frame-spacing lane
        # RELATIVE TO THE LIVE BC.  A defender outside the tackle radius
        # therefore keeps playing its depth role instead of being pinned to
        # its drive-entry Y.
        for _dp in defenders:
            _dp.frozen = False
            _dp.depth_locked = False
            _dp.depth_lock_y = None
            _dp.depth_lock_x = None

        # ── UNIFIED PRE-TACKLE AUTHORITY (2026-09-04, Task XIV consolidation) ──
        # The unified pre-tackle authority OWNS eligible DEF_LINE_READY line
        # bodies during CONTACT/DRIVE/DOMINANCE (frozen-mode anchors +
        # deterministic COMMIT/PLANTED/SLOW/HOLD states).  Bodies it manages
        # are integrated directly (single-integrator via _mmdl_moved); every
        # other non-tes-isolated defender folds back through the sole
        # RetreatManager TES-support writer.
        #
        # This replaces BOTH the legacy live lane-tackler commit/stance
        # writers and the previous TES-exclusive retreat-only path, so there
        # is ONE pre-contact authority across OPEN_PLAY -> CONTACT -> DRIVE.
        #
        # ── STAGE 8 CORRECTION (2026-09-05): DO NOT FREEZE THE LINE ──
        # Only the two assigned TES markers (MARKER_A/B) are frozen here,
        # and only after they've been assigned (assign_markers_for_new_tackle
        # ran in the phase handler).  The defensive line is NOT frozen —
        # it keeps its retreat/tracking behaviour via apply_tes_support_retreat
        # below.  The pre-tackle authority pool is built WITHOUT the
        # allow_frozen_drive relaxation since no line body is drive-frozen.
        for p in world.defending_team:
            if p.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                p.frozen = True
        # ── PRE-TACKLE PILE GATE (2026-09-10) ──
        # Eligibility is ROLE-based (DEF_LINE_READY + unfrozen), so without a
        # distance gate a defender 15-20 m away that happens to be closing was
        # handed to the authority, which then depth-locked it (X pinned, Y
        # held) and stopped it defending.  Only bodies AT THE PILE may be
        # owned: everyone else keeps playing their depth role while the deep
        # geometry writes for them every frame.
        _pile_r = float(getattr(self.tuners, "pre_tackle_trigger_radius_m",
                                PTA_PILE_GATE_RADIUS_M))
        _pta_pool = []
        for _p in defenders:
            if not is_pre_tackle_eligible(_p):
                continue
            _d2pile = math.hypot(float(_p.x) - float(bc.x),
                                 float(_p.y) - float(bc.y))
            if _d2pile <= _pile_r:
                _pta_pool.append(_p)
            elif is_pretackle_managed(_p):
                # Drifted out of the pile: release the stale ownership so the
                # body's depth-role geometry owns it again this frame.
                clear_pretackle_state(_p)
        _managed = apply_pre_tackle(
            world, _pta_pool, dt, self.tuners, frozen_mode=True,
        )
        self._pta_was_active = bool(_managed)
        _managed_ids = {id(p) for p in _managed}

        # ── DEPTH PIN REMOVED FROM THE DRIVE (2026-09-10) ──
        # The drive no longer depth-locks any body, so there is no
        # depth_lock_x to refresh.  The spacing / retreat writers publish the
        # live depth-role target (X and Y relative to the carrier) every frame
        # and Player.update() integrates it.  Re-clear defensively in case a
        # writer above re-stamped a lock.
        for _dd in defenders:
            _dd.depth_locked = False
            _dd.depth_lock_y = None
            _dd.depth_lock_x = None

        # ── TES DRIVE STRUCTURE HOLD (2026-09-11) ──
        # Every non-tackler line body (DEF_LINE_READY / DEF_CHASE /
        # DEF_RETREAT_10) that is NOT owned by the pre-tackle pile gate is
        # held on its STATIC structure cell: it FREEZES when already at its
        # depth role, and JOGS toward that role when out of position.  The
        # cell X is the drive-ENTRY ruck + WIDTH_OFFSETS[depth_role] — never
        # the live ball carrier — so non-tacklers ignore BC movement entirely.
        # Only the bounded anticipation micro-adjustment may stretch the
        # locked spacing band.  Tacklers stay TES-owned (excluded from
        # ``defenders``); this is the ONLY line writer for the chamber.
        _structure_bodies = [
            p for p in defenders if id(p) not in _managed_ids
        ]
        _held = apply_tes_drive_structure(
            world, _structure_bodies, bc, dt, self.tuners,
        )
        self._tes_structure_was_active = bool(_held)

        # ═══════════════════════════════════════════════════════════════
        # TES DEPTH AUTHORITY (STAGE 2, 2026-09-18) — THE DISCHARGE WRITER
        # ═══════════════════════════════════════════════════════════════
        # The successor to the TES Depth Obligation.  A non-exempt line
        # body whose depth debt exceeds the obligation tolerance is
        # granted TEMPORARY locomotion authority: the depth-discharge
        # system owns its X closure toward the canonical role depth target
        # and commands a GAP-PROPORTIONAL convergence speed, so the debt
        # returns inside tolerance instead of being merely paced.
        #
        # WHY THIS IS NEEDED AT ALL: the structure hold above already
        # points every out-of-position body at the correct cell
        # (``bc.x + WIDTH_OFFSETS[depth_role]`` — the SAME canonical role
        # depth Stage 1 measures against), but it jogs there at the FLAT
        # ``tes_hold_jog_speed_mps``.  On a driven carrier the cell moves
        # faster than that flat jog, so the debt grows and never
        # discharges — exactly the persistence Stage 1 measured.  This
        # authority replaces the flat jog with the debt-scaled speed for
        # the obligated bodies ONLY.
        #
        # WRITE BOUNDARY: it is applied to the SAME pool the structure
        # hold owns (``_structure_bodies``), i.e. the non-tackler line
        # bodies that are NOT already owned by the pre-tackle pile
        # authority.  That is a WRITE boundary, not a redefinition of who
        # is obligated: Stage 1's exemption set is read verbatim.  A body
        # at the pile stays with ``pre_tackle_authority``, so two
        # authorities never fight for one body's X (the structure hold
        # already defers to it the same way).
        #
        # FRAME ORDER: it runs AFTER the structure hold (so its speed wins
        # for obligated bodies) and BEFORE the single ``p.update(dt)``
        # integration loop below (so the commanded target/speed is what
        # lands, exactly once — no double integration).  Both post-
        # integration guards (``enforce_tackle_begin_glide``,
        # ``enforce_attach_band``) EXEMPT structure-held bodies
        # (``_tes_hold_active``), so this faster closure is never clamped
        # back down to the glide ceiling.
        #
        # PURE PER-FRAME READ: no latch, no hysteresis, no cooldown, no
        # target store, no persistent attribute.  The authority is held for
        # exactly the frames ``is_tes_depth_obligated`` is True and is
        # released the moment the debt is back inside tolerance.  The
        # returned list is the wiring proof (an empty list across a whole
        # rollout would mean the authority is nominal, not live).
        self._tes_depth_authority_last = apply_tes_depth_authority(
            world, _structure_bodies, self.tuners,
        )
        self._tes_depth_authority_total = (
            getattr(self, "_tes_depth_authority_total", 0)
            + len(self._tes_depth_authority_last)
        )

        for p in defenders:
            # ── PLAYER.UPDATE IS THE SOLE INTEGRATOR ──
            # MANAGED (pre-tackle pile) bodies were written by
            # apply_pre_tackle; STRUCTURE bodies by apply_tes_drive_structure
            # above.  Both are integrated exactly once here — the target each
            # writer set is what lands this frame.
            if id(p) in _managed_ids:
                p.update(dt)
                continue
            if not is_structure_hold_eligible(p):
                # Rare non-line body (no structure ownership): legacy
                # TES-support fold-back so it still has a live target.
                self.retreat.apply_tes_support_retreat(
                    world, p, governor=self.def_behaviour.line_governor,
                    track_bc_x=True,
                )
            p.update(dt)

        # ── DEPTH-LOCK POST-WRITER CLAMP — REMOVED (2026-09-08) ──
        # The depth-lock post-writer clamp wrote line x/y directly
        # (_dlp.x = _dlx / _dlp.y = _dly), logged as [TES-POS-WRITE].  That
        # was the TES "pos-write" that rewrote line X/Y during DRIVE /
        # DOMINANCE and produced the DH + line jitter.  The glide is now the
        # SOLE integrator inside Player.update() (the non-TES depth-lock
        # glide), so this clamp is redundant and has been removed — the TES
        # drive authority NO LONGER writes line x/y.  DEF_RETREAT_10 keeps
        # Player.update() (Y-free lateral slide) and glides X via the same
        # Player.update depth-lock block.

        # ── TACKLE-BEGIN ROLE-DEPTH GLIDE (2026-09-07) ──
        # Single authoritative enforcement of the tackle-begin law: when
        # TACKLE_INIT fires ONLY explicitly committed tacklers may snap into
        # TES/tackle geometry.  Every other defender treats role depth as a
        # DESTINATION and glides toward it under its current movement mode,
        # with per-frame movement capped at TACKLE_BEGIN_GLIDE_SPEED_MPS * dt.
        # Runs AFTER every writer (pre-tackle authority + retreat shadow +
        # depth-lock clamp) and BEFORE the next frame's integration, so the
        # glided position is what lands.  Exempt (legally snap): committed
        # tacklers, TES-attached bodies, markers, DUMMY_HALF, fullback, frozen.
        # Gated behind tuners.tackle_begin_glide_enabled (feature flag).
        from game.defence.defence_lvl_1.role_depth_glide import (
            enforce_tackle_begin_glide as _enforce_tackle_begin_glide,
        )
        _enforce_tackle_begin_glide(
            world, defenders, dt, self.tuners, ope, pre_pos=pre_pos,
        )

        # ── TWO-RADIUS ATTACH BAND (2026-09-07) ──
        # Reserve + nudge defenders inside the assist band (R_tackle < dist
        # <= R_assist) toward the BC / their reserved slot so the defensive
        # line approaches the pile as a coherent line instead of fragmenting.
        # Runs AFTER the tackle-begin glide and BEFORE the next frame's
        # integration; gated behind tuners.attach_slot_reserve_enabled.
        from game.defence.defence_lvl_1.attach_band import (
            enforce_attach_band as _enforce_attach_band,
        )
        _enforce_attach_band(
            world, defenders, dt, self.tuners, ope,
            bc=getattr(world, "ball_carrier", None),
            pre_pos=pre_pos,
            glide_speed_mps=GLIDE_SPEED_MPS,
        )

    # ------------------------------------------------------------------
    # 4. MARKER ASSIGNMENT AT RELEASE
    # ------------------------------------------------------------------

    def assign_markers_for_new_tackle(self, ope, strict_tacklers_only: bool = False) -> None:
        """Assign fresh markers for this tackle cycle (verbatim relocation
        of OpenPlayEngine._assign_markers_for_new_tackle).

        Clears ALL existing MARKER_A/B roles first, then applies the
        3-case rugby-league rule:

          CASE 1 (2+ tacklers):  Marker A = tackler[0], Marker B = tackler[1]
          CASE 2 (1 tackler):    Marker A = tackler,   Marker B = closest
          CASE 3 (0 tacklers):   Marker A/B = two closest to ruck

        Old markers not selected become DEF_RETREAT_10 (unfrozen).
        Called ONCE from _prepare_release() at DOMINANCE -> RELEASE.

        MARKER LOCK (2026-09-04): when ``strict_tacklers_only`` is True
        (used at TACKLE_CONTACT), marker roles are assigned ONLY from the
        2+ confirmed TES tacklers (CASE 1).  If fewer than 2 tacklers are
        confirmed, no marker role is assigned here — the full 3-case rule
        runs at RELEASE instead.  This forbids promoting a non-tackler into
        MARKER_A/B early during CONTACT/DRIVE.
        """
        from world.open_play_engine import (
            MARKER_A_DEPTH_M,
            MARKER_B_DEPTH_M,
            MARKER_MOVE_SPEED_MPS,
        )
        world = ope.world
        defenders = world.defending_team
        # BC fallback (2026-09-01): marker assignment is re-timed to tackle
        # start (_start_tackle), which may run before world.ruck_x_m is
        # published (ruck_x_m is written at DRIVE-END / _prepare_release).
        # Fall back to the BC position so marker targets stay finite.
        ruck_x = world.ruck_x_m if world.ruck_x_m is not None else (
            getattr(getattr(ope, "ball_carrier", None), "x", 60.0)
        )
        ruck_y = world.ruck_y_m if world.ruck_y_m is not None else (
            getattr(getattr(ope, "ball_carrier", None), "y", 35.0)
        )

        # ---- Step 1: Strip ALL stale MARKER_A/B roles ----
        # REFORM-WINDOW DELAY (2026-09-03): the DEF_RETREAT_10 role flip is
        # deferred until after PTB so the pin can own these bodies as
        # DEF_LINE_READY line bodies through RELEASE -> PTB.  They convert to
        # DEF_RETREAT_10 at the post-PTB sweep (_clear_marker_roles).  Flag
        # the deferred conversion so the sweep knows which bodies to flip.
        for d in defenders:
            if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                d.defender_role = DefenderRole.DEF_LINE_READY
                d.freeze_timer = 0.0
                d._defer_retreat_10 = True

        # ---- Step 2: Identify tacklers (TACKLER_1/2/3 from TES) ----
        # Build from BOTH the TACKLER_* roles AND the TES tackler context
        # (ope.primary / ope.joiners / ctx.engagement.tacklers).  When marker
        # assignment is re-timed to TACKLE_CONTACT, the TACKLER_* roles may
        # have been consumed by the MARKER_* roles; the TES context is the
        # stable tackler identity and keeps the 3-case rule correct.
        tackler_ids = set()
        for d in defenders:
            if d.defender_role in (DefenderRole.TACKLER_1,
                                   DefenderRole.TACKLER_2,
                                   DefenderRole.TACKLER_3):
                tackler_ids.add(id(d))
        _primary = getattr(ope, "primary", None)
        if _primary is not None:
            tackler_ids.add(id(_primary))
        for _j in (getattr(ope, "joiners", None) or []):
            tackler_ids.add(id(_j))
        _ctx_tacklers = getattr(getattr(ope, "tes_ctx", None), "tacklers", None)
        for _t in (_ctx_tacklers or []):
            tackler_ids.add(id(_t))
        # ── FULLBACK EXCLUSION — ALL CASES (2026-09-30) ──
        # THE OWNERSHIP DEFECT THIS REMOVES.  The two declared
        # "FULLBACK EXCLUSION (2026-08-28)" guards below live ONLY in
        # CASE 2's ``others`` pool and CASE 3's fallback pool — i.e. they
        # only ever protected the PARTNER slot.  The primary slots were
        # never guarded: CASE 1 picks ``tacklers[0]`` / ``tacklers[1]`` and
        # CASE 2 picks ``tacklers[0]`` for Marker A, so a tagged fullback
        # that was the primary TES tackler (or a joiner) WAS stamped
        # MARKER_A/B — and the very next RELEASE-entry pass runs
        # ``fullback_role.reset_fullback_emergency_engagement``, which
        # forces ``defender_role = DEF_LINE_READY`` on that body and thereby
        # DELETES the marker stamp a frame later.  Markers are assigned once
        # per DOMINANCE->RELEASE boundary, so nothing refilled the pair: the
        # ruck-adjacent pin silently shipped a body short, and
        # ``reassert_fullback`` then re-anchored the actor onto its deep
        # sweeper anchor.  Net effect: the fullback that won the contest
        # never transitioned to marker — the reported symptom.
        #
        # The exclusion is therefore applied ONCE, to the TACKLER POOL, so
        # every case below is covered by construction: CASE 1 and CASE 2's
        # primary slot as well as their partner slots.  A tagged fullback is
        # a backfield anchor owned by ``apply_fullback_geometry`` and is
        # NEVER marker duty — which is also why CASE 2/CASE 3's own comments
        # give for excluding it in the first place (a MARKER_* role makes
        # ``fullback_role._is_available_simple`` reject the body, so the
        # tag migrates to a different defender mid-cycle).
        #
        # No cover is lost: with the fullback removed from the pool the
        # selection falls through to CASE 2 / CASE 3 exactly as it does when
        # the fullback was never a tackler, and the pair is filled from the
        # nearest non-fullback bodies.
        tacklers = [
            d for d in defenders
            if id(d) in tackler_ids
            and not getattr(d, "fullback_role_active", False)
        ]

        # ── MARKER LOCK (2026-09-04) ──
        # At TACKLE_CONTACT (strict_tacklers_only=True) marker roles may only
        # be assigned when 2+ confirmed TES tacklers exist (CASE 1).  If fewer
        # are confirmed, defer to RELEASE — no non-tackler is promoted into a
        # marker role early.  The full 3-case rule runs at RELEASE when all
        # tacklers (primary + joiners) are known.
        if strict_tacklers_only and len(tacklers) < 2:
            return

        m_a = None
        m_b = None

        # CASE 1: Two or more tacklers -> Marker A/B = first two
        if len(tacklers) >= 2:
            m_a = tacklers[0]
            m_b = tacklers[1]

        # CASE 2: One tackler -> Marker A = tackler, B = closest defender
        elif len(tacklers) == 1:
            m_a = tacklers[0]
            # FULLBACK EXCLUSION (2026-08-28): the dynamic fullback is a
            # backfield anchor owned by apply_fullback_geometry — it must
            # never be drafted into marker duty.  Selecting it as Marker B
            # would set defender_role = MARKER_B; on the next OPEN_PLAY the
            # fullback availability check (_is_available_simple) rejects a
            # MARKER_A/B role and a DIFFERENT defender is selected, stealing
            # the FB role mid-cycle.
            others = [
                d for d in defenders
                if d is not m_a
                and not getattr(d, "fullback_role_active", False)
            ]
            if others:
                m_b = min(others, key=lambda d: abs(d.x - ruck_x))

        # CASE 3: No tacklers -> Marker A/B = two closest to ruck
        else:
            # FULLBACK EXCLUSION (2026-08-28): same guard as CASE 2 — never
            # draft the deep fullback into the marker pair.
            sorted_by_ruck = sorted(
                [
                    d for d in defenders
                    if not getattr(d, "fullback_role_active", False)
                ],
                key=lambda d: (d.x - ruck_x) ** 2 + (d.y - ruck_y) ** 2,
            )
            if len(sorted_by_ruck) >= 1:
                m_a = sorted_by_ruck[0]
            if len(sorted_by_ruck) >= 2:
                m_b = sorted_by_ruck[1]

        # ---- Step 3: Clear stale TACKLER frozen state on non-selected ----
        # REFORM-WINDOW DELAY (2026-09-03): same deferred DEF_RETREAT_10 flip
        # as step 1 — keep as DEF_LINE_READY through RELEASE -> PTB so the pin
        # owns them; convert at the post-PTB sweep.
        for d in tacklers:
            if d is m_a or d is m_b:
                continue
            d.defender_role = DefenderRole.DEF_LINE_READY
            d.freeze_timer = 0.0
            d._defer_retreat_10 = True
            # TACKLE-STATE GATE: ex-tacklers hold TACKLE-STATE through
            # RELEASE/PTB while inside the tackle-radius; the gate
            # releases them on radius exit.
            stamp_tackle_state(d)

        # ---- FLIP MARKER (DOMINANCE->RELEASE) ----
        # Ex-tacklers becoming DEF_RETREAT_10 re-enter the line on the next
        # OPEN_PLAY frame. Single-use flip marker forces them into the A
        # band (innermost) on their current side. Idempotent.
        for d in tacklers:
            if d is m_a or d is m_b:
                continue
            if getattr(d, '_flipped_in_release', False):
                continue  # already stamped
            if getattr(d, 'fullback_role_active', False):
                continue  # never flag fullback
            d._flipped_in_release = True
            # Clear movement/target locks at flip time
            d.target_x = d.x
            d.target_y = d.y
            d.vx = 0.0
            d.vy = 0.0
            d._mmdl_state_until_frame = -1
            d._mmdl_moved = False
            if _trace_enabled():
                print(f'[RELEASE] flipped defender id={getattr(d, "player_id", "?")}'
                      f' y={d.y:.1f} -> _flipped_in_release=True')

        # ---- Step 4: Assign roles with temporary freeze ----
        if m_a is not None:
            m_a.defender_role = DefenderRole.MARKER_A
            m_a.freeze_timer = ope.def_tuners.MARKER_A_FREEZE_AFTER_PTB
            m_a.target_x = ruck_x + MARKER_A_DEPTH_M
            m_a.target_y = ruck_y
            m_a.move_speed = MARKER_MOVE_SPEED_MPS
            # TACKLE-STATE GATE: marker pair holds TACKLE-STATE through
            # PTB while inside the tackle-radius.
            stamp_tackle_state(m_a)
            # Clear deferred flip: this body is a live marker now, not a
            # post-PTB DEF_RETREAT_10 candidate.
            m_a._defer_retreat_10 = False

        if m_b is not None:
            m_b.defender_role = DefenderRole.MARKER_B
            m_b.freeze_timer = ope.def_tuners.MARKER_B_FREEZE_AFTER_PTB
            m_b.target_x = ruck_x + MARKER_B_DEPTH_M
            m_b.target_y = ruck_y
            m_b.move_speed = MARKER_MOVE_SPEED_MPS
            stamp_tackle_state(m_b)
            m_b._defer_retreat_10 = False

        # ---- Step 5: Sweep -- ensure non-marker defenders are unfrozen ----
        for d in defenders:
            if d.defender_role not in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                d.freeze_timer = 0.0

    # ------------------------------------------------------------------
    # 5. FULLBACK RE-ASSERT (any non-OPEN_PLAY phase)
    # ------------------------------------------------------------------

    def reassert_fullback(self, ope, dt: float) -> None:
        """Re-assert the deep backfield anchor for the fullback actor in
        ANY non-OPEN_PLAY phase (RELEASE / PTB_PREP / PTB_EXEC / PTB /
        POST_RUCK_OPEN_PLAY).

        The unified defensive pipeline only runs during OPEN_PLAY, so the
        FB geometry authority does NOT fire there. Without this re-assert
        the actor would be left on its last line target through the PTB era.

        Verbatim relocation of OpenPlayEngine._reassert_fullback().
        """
        actor = None
        for d in ope.world.defending_team:
            if getattr(d, "fullback_role_active", False):
                actor = d
                break
        if actor is None:
            publish_fullback_state(ope.world)
            return
        # ── POST-TACKLE RETURN EXEMPTION (handoff defect) ──
        # Between a completed tackle's DOMINANCE->RELEASE handover and the
        # PTB the tagged fullback OWNS its own return:
        # ``_prepare_release`` has already cleared its TES attachment and
        # contact flags, disarmed the depth lock, armed the ruck-relative
        # return target and integrated the body once.  Re-anchoring it here
        # (``post_tackle_ruck_anchor=True`` builds the anchor from the LIVE
        # ruck, which is the tackle point the ball carrier is standing on)
        # walked the body back beside the ball carrier on every RELEASE /
        # PTB_PREP / PTB_EXEC / PTB frame, so it never ran its commanded
        # return until the PTB released it -- the reported "the fullback
        # stays beside the ball carrier until PTB, though it moves slightly
        # while waiting".
        #
        # While the return window is armed this re-assert therefore YIELDS:
        # it publishes the state (the invariant audit keeps a stable FB id)
        # and touches neither the target nor the body, so the fullback runs
        # home under its own return contract.  The re-assert resumes the
        # moment the window closes (the sweeper / kickoff-flight contracts
        # are unchanged, because the flag is only ever armed by a completed
        # tackle's release handover).
        if is_fullback_post_tackle_return_active(actor):
            # THE WINDOW IS CONSUMED HERE TOO (2026-10-02).  This re-assert is
            # the ONLY integrator the tagged fullback has in the RELEASE / PTB
            # era, so it is also the only place the post-tackle return window
            # can be RELEASED there.  The window HOLDs while it still owns the
            # body, and is released on COMPLETION (the actor reached the point
            # it was sent to) or on a HIGHER-PRIORITY INTERRUPTION -- after
            # which this method falls through to the ordinary re-assert below
            # and the sweeper contract resumes from that frame.
            #
            # Before this release the flag was never cleared, so the window
            # outlived the tackle cycle and the body stayed ruck-anchored for
            # the rest of the match.
            _return_ax, _return_ay = compute_fullback_ruck_anchor(
                ope.world, _anchor_m(ope.world)
            )
            if fullback_post_tackle_return_live(
                ope.world, actor, _return_ax, _return_ay
            ):
                # YIELD THE ANCHOR, KEEP THE BODY MOVING: the return target is
                # owned by the release handoff, so do NOT re-write it from the
                # live ruck -- but the fullback must still RUN home, and this
                # re-assert is the only integrator it has in these phases.
                # RELEASE / PTB phase invariants can leave a stale freeze on
                # the tagged actor.  The return window is only live after
                # tackle ownership has ended, so restore its owned recovery
                # locomotion before integrating rather than allowing that
                # stale flag to strand it beside the ruck.
                from game.defence.defence_lvl_1.fullback_controller import (
                    FullbackIntent,
                    declare_fullback_recovery,
                    enter_fullback_intent,
                    SOURCE_RECOVERY,
                )
                ope.world.fullback_recover_allowed = True
                declare_fullback_recovery(
                    ope.world, actor, _return_ax, _return_ay,
                    source=SOURCE_RECOVERY,
                )
                enter_fullback_intent(
                    ope.world,
                    actor,
                    FullbackIntent.RECOVER,
                    source=SOURCE_RECOVERY,
                )
                actor.frozen = False
                actor.freeze_timer = 0.0
                publish_fullback_state(ope.world)
                actor.update(dt)
                return
            clear_fullback_post_tackle_return(
                actor, reason="completion_or_interruption"
            )
            # fall through to the ordinary sweepers re-assert below
        # TES ISOLATION CONTRACT (2026-08-29): during the TES phases and
        # RELEASE the fullback is a hard-frozen defender — it receives NO
        # retreat, spacing, or line-target update.  apply_phase_invariants()
        # pins it frozen; skip the target write entirely so no global
        # defensive geometry leaks onto the backfield anchor.  Still publish
        # the (frozen) state so the invariant audit keeps a stable FB id.
        if actor.frozen:
            publish_fullback_state(ope.world)
            return
        # HIGH-VELOCITY RECOVERY ARMING (2026-09-21): the PTB-era re-assert is
        # the second and last seam that arms the recovery locomotion.  A
        # tagged fullback whose anchor snapped away during RELEASE / PTB is
        # owed ground on a live play, so it may recover home here too.  The
        # same predicate gates it (debt band, no tackle authority, no TES, no
        # kickoff flight, and never during kickoff — reset disarms it).
        apply_fullback_geometry(
            ope.world, actor, post_tackle_ruck_anchor=True
        )
        # PUBLISH-REFRESH (2026-08-27): the open-play dispatch publishes the
        # fullback state; this PTB-era re-assert must too, otherwise a
        # re-assign during RELEASE/PTB leaves world.fullback_actor_id stale
        # and the invariant audit flags FB_ID_MISMATCH.
        publish_fullback_state(ope.world)
        if not actor.frozen:
            actor.update(dt)

    # ------------------------------------------------------------------
    # 6. TACKLE-STATE GATE SWEEP (sole release authority)
    # ------------------------------------------------------------------

    def update_tackle_gate(self, world, dt: float) -> list:
        """Advance the tackle-state gate sweep.

        Sole release authority: holders leave TACKLE-STATE ONLY by exiting
        the tackle-radius (+hysteresis). Returns the list of defenders
        released THIS frame so the caller can immediately re-run the
        frame preamble (slot-invariant repair at release).
        """
        return update_tackle_state(world, dt)

    # ------------------------------------------------------------------
    # 7. RUCK-ADJACENT FINAL-AUTHORITY PIN
    # ------------------------------------------------------------------

    def enforce_ruck_pin(self, world) -> None:
        """Ruck-adjacent final-authority pin (last writer of the frame).

        Pins each side's first defender to EXACTLY ruck_y minus/plus the
        pin offset. See game/defence/defence_lvl_1/ruck_adjacent_pin.py.
        """
        enforce_ruck_adjacent_pin(world)
