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
  3. update_tes_drive()        -- TES-drive line behaviour for
                                  NON-TACKLERS ONLY. TES retains FULL
                                  ownership of drive geometry and
                                  ball-carrier/tackler movement.
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

from player.player import DefenderRole
from world.world_state import GameSubState, is_tackle_phase
from game.defence.defence_lvl_1.defensive_pipeline import (
    run_defensive_pipeline,
)
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _anchor_m,
    set_anchor,
)
from game.defence.defence_lvl_1.defensive_width import governed_width_m
from game.defence.defence_lvl_1.fullback_role import apply_fullback_geometry
from game.debug.fullback_audit import audit_fullback_state
from game.defence.defence_lvl_1.tackle_state_gate import (
    update_tackle_state,
    stamp_tackle_state,
)
from game.defence.defence_lvl_1.pretackle_attempt import (
    apply_pretackle_attempts,
    reset_pretackle_attempts,
    clear_pretackle_state,
    is_pretackle_managed,
    PTA_TARGET_SOURCE,
)
from game.defence.defence_lvl_1.ruck_adjacent_pin import (
    enforce_ruck_adjacent_pin,
)
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
)
from game.managers.retreat_manager import RetreatManager
from game.defence.defence_lvl_1.spacing_authority import SpacingAuthority
from game.managers.convergence_manager import ConvergenceManager


def _trace_enabled() -> bool:
    """Lazy read of the engine trace flag (avoids import cycle)."""
    try:
        from world.open_play_engine import DEBUG_OPE_TRACE
        return DEBUG_OPE_TRACE
    except Exception:
        return False


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

    def apply_forced_retreat(self, world, defenders, governor=None):
        """Run the unified forced-retreat writer through the owned
        RetreatManager.

        Sole authority for defender positioning during retreat:
          X <- anchor authority, Y <- spacing authority,
          TES freeze contracts enforced (frozen players never move).
        Returns ``(compliant, non_compliant)``.
        """
        return self.retreat.apply_forced_retreat(
            world, defenders, governor=governor
        )

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
        return self.spacing.run(
            world,
            world.defending_team,
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

        # ---- HARD GATE: defence AI runs ONLY during OPEN_PLAY ----
        if ope._phase != "OPEN_PLAY":
            return
        if is_tackle_phase(world):
            return

        # ---- Assign default roles to any defender missing one ----
        for p in world.defending_team:
            if p.defender_role is None:
                p.defender_role = DefenderRole.DEF_LINE_READY

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

        # ---- Tackle trigger with pre-tackle check ----
        # Only PRIMARY/SECONDARY (lane-selected convergers) may start a
        # tackle through physical contact; random spacing defenders can
        # never steal the tackle.
        from world.open_play_engine import TACKLE_RADIUS_M
        for p in defenders:
            if p is bc:
                continue
            # FULLBACK EXCLUSION: the dynamic fullback is backfield
            # coverage, NOT a tackler.
            if getattr(p, "fullback_role_active", False):
                continue
            tackle_role = getattr(p, "tackle_role", None)
            if tackle_role not in ("PRIMARY", "SECONDARY"):
                continue
            dist = math.hypot(bc.x - p.x, bc.y - p.y)

            if dist < TACKLE_RADIUS_M:
                pre_result = ope._check_pre_tackle(bc, p)
                if pre_result == "MISS":
                    if _trace_enabled():
                        print(f"[PRE_TACKLE] MISS - defender {p.player_id} whiffs")
                    continue
                elif pre_result == "ARM_GRAB":
                    if _trace_enabled():
                        print(f"[PRE_TACKLE] ARM_GRAB - defender {p.player_id} grabs, BC slowed")
                    bc.vx *= 0.85
                    bc.vy *= 0.85
                    continue
                elif pre_result == "GLANCING":
                    if _trace_enabled():
                        print(f"[PRE_TACKLE] GLANCING - defender {p.player_id} hits at angle, BC displaced")
                    # Lateral displacement -- push BC sideways, no TES entry
                    lateral_dir = 1.0 if bc.y > p.y else -1.0
                    bc.y += 0.5 * lateral_dir
                    bc.vy = lateral_dir * 2.0
                    continue
                else:
                    if _trace_enabled():
                        print(f"[TACKLE] defender {p.player_id} hit BC at dist={dist:.2f}")
                    ope._start_tackle(bc, p)
                    return

    # ------------------------------------------------------------------
    # 3. TES-DRIVE LINE BEHAVIOUR -- NON-TACKLERS ONLY
    # ------------------------------------------------------------------

    def update_tes_drive(self, ope, dt: float) -> None:
        """TES-drive line behaviour for NON-TACKLERS ONLY.

        TES retains FULL ownership of drive geometry and ball-carrier/
        tackler movement. Non-tackler line defenders keep running the
        open-play defensive AI (spacing, lanes, role depths, closing,
        decision layer, MMDL) exactly as they do in OPEN_PLAY, plus the
        BC-shadow role-depth re-anchor and soft-follow so the line tracks
        the driven carrier.

        Verbatim relocation of
        OpenPlayEngine._update_defence_during_tes_drive().

        TASK XIV (dark-safe): when ``pretackle_attempt_enabled`` is True,
        non-tackler DEF_LINE_READY bodies are instead owned by the
        pre-tackle attempt authority (stable tackle-phase anchors +
        freeze/slow contracts + deterministic attempt states).  The
        authority is invoked AFTER the projected pipeline, and the legacy
        BC-SHADOW re-anchor is skipped for the bodies it manages.
        """
        if ope._phase not in ("TACKLE_DRIVE", "TACKLE_DOMINANCE"):
            # TASK XIV: drive ended — clear any live pre-tackle attempt
            # state so the NEXT drive reseeds fresh anchors/states
            # (DOMINANCE -> RELEASE exit law).
            if getattr(self, "_pta_was_active", False):
                self._pta_was_active = False
                reset_pretackle_attempts(ope.world)
            return
        self._pta_was_active = bool(
            getattr(self.tuners, "pretackle_attempt_enabled", False)
        )
        bc = ope.ball_carrier
        if bc is None:
            return
        world = ope.world

        # TES participants (primary + joiners + attach-slot registry +
        # canonical TESContext registries) are owned by TES -- never run
        # the open-play pipeline on them.
        tes_ids = set()
        if ope.primary is not None:
            tes_ids.add(ope.primary.player_id)
        for j in ope.joiners:
            tes_ids.add(j.player_id)
        for _att_pid in getattr(ope, "_attached_player_slots", {}):
            tes_ids.add(_att_pid)
        _ctx_attached = getattr(ope.tes_ctx, "attached", None)
        if _ctx_attached:
            for _cid in _ctx_attached:
                tes_ids.add(_cid)
        _ctx_attached_ids = getattr(ope.tes_ctx, "attached_ids", None)
        if _ctx_attached_ids:
            for _cid in _ctx_attached_ids:
                tes_ids.add(_cid)
        defenders = [
            p for p in world.defending_team
            if not p.frozen and p.player_id not in tes_ids
        ]
        if not defenders:
            return

        # Pre-frame position snapshot: the open-play pipeline below
        # integrates these defenders toward the STALE ruck-anchored targets
        # BEFORE the shadow re-anchor re-points them at the live BC.
        # Shadowed defenders get their pre-frame position restored in the
        # tail loop so the TES-drive soft-follow is the SINGLE integrator.
        pre_pos = {d.player_id: (d.x, d.y) for d in defenders}

        # ---- GLOBAL DEFENSIVE TOP-SPEED CEILING stamp (mirrors open play) ----
        _def_ceiling = self.tuners.max_defensive_speed_mps
        for d in defenders:
            d.max_defensive_speed_mps = _def_ceiling

        ruck_x = getattr(world, "ruck_x_m", bc.x)
        ruck_y = getattr(world, "ruck_y_m", bc.y)
        anchor_x = _anchor_m(world)

        # Even Y-spread across the GOVERNED width centred on ruck_y.
        def_line_width = governed_width_m(world, self.tuners)
        n = len(defenders)
        lane_y = []
        half_width = def_line_width * 0.5
        for i in range(n):
            if n > 1:
                t = (i / (n - 1)) * 2.0 - 1.0
            else:
                t = 0.0
            lane_y.append(ruck_y + t * half_width)

        # ---- Temporary OPEN_PLAY projection, then restore in finally ----
        prev_sub_state = world.sub_state
        prev_tes_active = world.tes_active
        prev_suppress = getattr(ope, "_suppress_pre_tackle", False)
        world.sub_state = GameSubState.OPEN_PLAY
        world.tes_active = False
        ope._suppress_pre_tackle = True
        try:
            self.def_behaviour.apply(world, defenders, anchor_x, lane_y)
        finally:
            world.sub_state = prev_sub_state
            world.tes_active = prev_tes_active
            ope._suppress_pre_tackle = prev_suppress

        # ---- MODULE 1: FULLBACK GEOMETRY AUTHORITY (same last-writer) ----
        for _d in world.defending_team:
            apply_fullback_geometry(world, _d)

        # ---- MODULE 1b: PRE-TACKLE ATTEMPT AUTHORITY (TASK XIV, gated) ----
        # Dark-safe gate: when ON, non-tackler DEF_LINE_READY bodies are
        # owned by the pre-tackle attempt authority instead of the legacy
        # BC-shadow follower.  The authority writes targets/velocities and
        # INTEGRATES its bodies directly (single-integrator law via
        # _mmdl_moved), so the legacy shadow + soft-follow tail must skip
        # them.  OFF (default) -> this returns [] and the legacy path runs
        # byte-identical.
        _pta_handled = []
        if bool(getattr(self.tuners, "pretackle_attempt_enabled", False)):
            _pta_handled = apply_pretackle_attempts(
                world, defenders, dt, self.tuners
            )

        # ---- MODULE 2: BC-SHADOW ROLE-DEPTH RE-ANCHOR ----
        # During TACKLE_DRIVE / TACKLE_DOMINANCE the ball carrier is driven
        # forward while the pipeline above anchored every line defender to
        # the STALE pre-drive ruck_x. This writer re-anchors EVERY eligible
        # depth-role holder to the LIVE BC:
        #     target_x = bc.x + WIDTH_OFFSETS[depth_role]
        # No Y write, no anchor/authority write.
        #
        # ── STAGE 3D/3E PRECEDENCE LAW (2026-08-25) ──
        # The convergence dict is STILL published each drive frame (it feeds
        # the MMDL decision layer -> dict-owned closers decide CHASE/SHOOT),
        # but during a drive the shadow writer is the LAST target authority
        # for non-tackler line defenders: convergence ownership governs the
        # MOVEMENT STATE, the shadow governs the TARGET GEOMETRY (both aim
        # at the live BC, so the two contracts agree geometrically).  The
        # former ``id(_s) in _conv_map`` skip left dict-owned closers on
        # stale mmdl_* targets (target_source != 'tes_drive_shadow'), which
        # broke the shadow contract (ASSERT-C/D) and dropped them out of the
        # soft-follow integrator.
        self.run_convergence(world, defenders, bc)
        _drive_offsets = getattr(self.tuners, "WIDTH_OFFSETS", None) or {}
        for _s in defenders:
            if _s.player_id in tes_ids:
                continue
            if getattr(_s, "fullback_role_active", False):
                continue
            if getattr(_s, "_tackle_commit_active", False):
                continue
            # TASK XIV: the pre-tackle attempt authority already owns this
            # body (stable anchor / freeze / slow contract).  Never shadow
            # it back onto the live carrier.
            if is_pretackle_managed(_s):
                continue
            _role = getattr(_s, "depth_role", None)
            if _role is None or _role not in _drive_offsets:
                continue
            _prev_tx = getattr(_s, "_tes_shadow_prev_tx", None)
            _s.target_x = bc.x + _drive_offsets[_role]
            # STAGE 3E feed-forward source: the RATE at which this
            # defender's own shadow anchor is advancing.  TES drive physics
            # moves the ball carrier by writing position directly, so bc.vx
            # is not a reliable line-speed signal; the target delta across
            # frames is exact by construction.
            if _prev_tx is None:
                _s._tes_shadow_ffx = 0.0
            else:
                _s._tes_shadow_ffx = max(
                    -_def_ceiling,
                    min(_def_ceiling, (_s.target_x - _prev_tx) / dt),
                )
            _s._tes_shadow_prev_tx = _s.target_x
            _s.target_source = "tes_drive_shadow"

        # ---- POST-WRITER TES-ESCALATION GUARD ----
        # A defender could be shadowed, then become a TES participant
        # mid-frame (joiner attach / commit escalation during DRIVE).
        # Membership is re-read LIVE at tail time; restore pre-frame
        # position so TES motion is the only integrator.
        from world.open_play_engine import TES_DRIVE_FOLLOW_K
        _live_tes_ids = set()
        if ope.primary is not None:
            _live_tes_ids.add(ope.primary.player_id)
        for _lj in ope.joiners:
            _live_tes_ids.add(_lj.player_id)
        for _att_pid in getattr(ope, "_attached_player_slots", {}):
            _live_tes_ids.add(_att_pid)
        _ctx_attached = getattr(ope.tes_ctx, "attached", None)
        if _ctx_attached:
            for _cid in _ctx_attached:
                _live_tes_ids.add(_cid)
        _ctx_attached_ids = getattr(ope.tes_ctx, "attached_ids", None)
        if _ctx_attached_ids:
            for _cid in _ctx_attached_ids:
                _live_tes_ids.add(_cid)
        for p in defenders:
            if p.player_id not in _live_tes_ids:
                continue
            _src = getattr(p, "target_source", None)
            if _src not in ("tes_drive_shadow", PTA_TARGET_SOURCE):
                continue
            # TASK XIV: a pre-tackle attempt body that became a TES
            # participant mid-frame hands back full ownership to TES.
            if _src == PTA_TARGET_SOURCE:
                clear_pretackle_state(p)
            p.target_source = None
            if p.player_id in pre_pos:
                p.x, p.y = pre_pos[p.player_id]
            p._mmdl_moved = False

        # ---- Movement integration tail ----
        _drive_ceiling = getattr(
            self.tuners, "max_defensive_speed_mps", 8.3
        )
        # ── STAGE 3E: TES-DRIVE FOLLOW TIMING CONSTANTS (2026-08-25) ──
        # Deadband: sub-threshold target error rests the follow, killing the
        # micro-jitter when a shadowed defender's target sits on it — BUT the
        # rest only applies to a NEAR-STATIC carrier.  A pure proportional
        # follow tracking a driving BC has an equilibrium lag of
        # bc_speed / K (~0.8 m under a fast drive); resting inside the band
        # against a moving carrier turns that lag into a limit-cycle drift.
        # Smoothing: EMA alpha on the follow velocity so a defender toggling
        # TES eligibility (joiner attach / escalated commit mid-drive) cannot
        # inject a one-frame velocity discontinuity into the soft-follow.
        # Feed-forward: the live BC velocity is added to the follow command
        # so the line tracks the driven carrier at near-zero steady-state
        # gap instead of lagging by bc_speed / K.
        _DRIVE_FOLLOW_DEADBAND_M = 0.05
        _DRIVE_FOLLOW_SMOOTH = 0.6
        _DRIVE_STATIC_CARRIER_MPS = 0.8
        for p in defenders:
            if p.frozen:
                p.vx = 0.0
                p.vy = 0.0
                p.target_x = p.x
                p.target_y = p.y
                continue
            if p.player_id in tes_ids:
                # TES participants keep their attach/commit integration --
                # never soft-follow them off the pile.
                p.update(dt)
                continue
            if getattr(p, "target_source", None) != "tes_drive_shadow":
                # Not shadowed (fullback / markers / DH / no depth role):
                # keep the open-play pipeline's own integration.
                if getattr(p, "_mmdl_moved", False):
                    p._mmdl_moved = False
                else:
                    p.update(dt)
                continue
            if p.player_id in pre_pos:
                _px, _py = pre_pos[p.player_id]
                p.x = _px
                p.y = _py
            if getattr(p, "_mmdl_moved", False):
                p._mmdl_moved = False
            # TES-DRIVE SOFT FOLLOW: non-tackler line defenders glide toward
            # their role-depth targets with a proportional follow so the
            # line VISIBLY tracks the carrier while the drive is in progress.
            # STAGE 3E deadband: a target landing within 5 cm per axis rests
            # the follow instead of injecting a velocity spike.
            _err_x = p.target_x - p.x
            _err_y = p.target_y - p.y
            _ffx = getattr(p, "_tes_shadow_ffx", 0.0)
            _in_band = (abs(_err_x) < _DRIVE_FOLLOW_DEADBAND_M
                        and abs(_err_y) < _DRIVE_FOLLOW_DEADBAND_M)
            if _in_band and abs(_ffx) < _DRIVE_STATIC_CARRIER_MPS:
                # Static-line rest: the target sits on the defender and the
                # shadow anchor is not advancing — rest the follow (kills
                # micro-jitter, no limit-cycle drift).
                p.vx = 0.0
                p.vy = 0.0
                continue
            vx_f = _err_x * TES_DRIVE_FOLLOW_K
            vy_f = _err_y * TES_DRIVE_FOLLOW_K
            # Anchor-motion feed-forward: cancel the equilibrium lag so the
            # line tracks the advancing depth anchor at ~zero steady-state
            # gap regardless of how TES physics moves the carrier.
            vx_f += _ffx
            spd_f = math.hypot(vx_f, vy_f)
            spd_cap = min(
                getattr(p, "max_speed_mps", _drive_ceiling),
                _drive_ceiling,
            )
            if spd_f > spd_cap:
                vx_f = vx_f / spd_f * spd_cap
                vy_f = vy_f / spd_f * spd_cap
            # STAGE 3E EMA smoothing: a defender toggling eligibility
            # (joiner attach / commit escalation) cannot inject a one-frame
            # velocity discontinuity into the soft-follow.
            _prev_vx = getattr(p, "_tes_follow_vx", 0.0)
            _prev_vy = getattr(p, "_tes_follow_vy", 0.0)
            p.vx = _prev_vx + (vx_f - _prev_vx) * _DRIVE_FOLLOW_SMOOTH
            p.vy = _prev_vy + (vy_f - _prev_vy) * _DRIVE_FOLLOW_SMOOTH
            p._tes_follow_vx = p.vx
            p._tes_follow_vy = p.vy
            p.x += p.vx * dt
            p.y += p.vy * dt

    # ------------------------------------------------------------------
    # 4. MARKER ASSIGNMENT AT RELEASE
    # ------------------------------------------------------------------

    def assign_markers_for_new_tackle(self, ope) -> None:
        """Assign fresh markers for this tackle cycle (verbatim relocation
        of OpenPlayEngine._assign_markers_for_new_tackle).

        Clears ALL existing MARKER_A/B roles first, then applies the
        3-case rugby-league rule:

          CASE 1 (2+ tacklers):  Marker A = tackler[0], Marker B = tackler[1]
          CASE 2 (1 tackler):    Marker A = tackler,   Marker B = closest
          CASE 3 (0 tacklers):   Marker A/B = two closest to ruck

        Old markers not selected become DEF_RETREAT_10 (unfrozen).
        Called ONCE from _prepare_release() at DOMINANCE -> RELEASE.
        """
        from world.open_play_engine import (
            MARKER_A_DEPTH_M,
            MARKER_B_DEPTH_M,
            MARKER_MOVE_SPEED_MPS,
        )
        world = ope.world
        defenders = world.defending_team
        ruck_x = world.ruck_x_m
        ruck_y = world.ruck_y_m

        # ---- Step 1: Strip ALL stale MARKER_A/B roles ----
        for d in defenders:
            if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                d.defender_role = DefenderRole.DEF_RETREAT_10
                d.freeze_timer = 0.0

        # ---- Step 2: Identify tacklers (TACKLER_1/2/3 from TES) ----
        tacklers = [d for d in defenders
                    if d.defender_role in (DefenderRole.TACKLER_1,
                                           DefenderRole.TACKLER_2,
                                           DefenderRole.TACKLER_3)]

        m_a = None
        m_b = None

        # CASE 1: Two or more tacklers -> Marker A/B = first two
        if len(tacklers) >= 2:
            m_a = tacklers[0]
            m_b = tacklers[1]

        # CASE 2: One tackler -> Marker A = tackler, B = closest defender
        elif len(tacklers) == 1:
            m_a = tacklers[0]
            others = [d for d in defenders if d is not m_a]
            if others:
                m_b = min(others, key=lambda d: abs(d.x - ruck_x))

        # CASE 3: No tacklers -> Marker A/B = two closest to ruck
        else:
            sorted_by_ruck = sorted(defenders,
                                    key=lambda d: (d.x - ruck_x) ** 2 + (d.y - ruck_y) ** 2)
            if len(sorted_by_ruck) >= 1:
                m_a = sorted_by_ruck[0]
            if len(sorted_by_ruck) >= 2:
                m_b = sorted_by_ruck[1]

        # ---- Step 3: Clear stale TACKLER frozen state on non-selected ----
        for d in tacklers:
            if d is m_a or d is m_b:
                continue
            d.defender_role = DefenderRole.DEF_RETREAT_10
            d.freeze_timer = 0.0
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

        if m_b is not None:
            m_b.defender_role = DefenderRole.MARKER_B
            m_b.freeze_timer = ope.def_tuners.MARKER_B_FREEZE_AFTER_PTB
            m_b.target_x = ruck_x + MARKER_B_DEPTH_M
            m_b.target_y = ruck_y
            m_b.move_speed = MARKER_MOVE_SPEED_MPS
            stamp_tackle_state(m_b)

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
            return
        apply_fullback_geometry(ope.world, actor)
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
