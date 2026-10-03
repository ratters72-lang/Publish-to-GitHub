import math

__all__ = ["DefensiveBehaviourLvl1"]

from world.world_state import GameSubState, is_tackle_phase, is_in_ruck_bubble
from player.player import DefenderRole
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _anchor_m,
)
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles
# ── CONTACT AUTHORITY — THE SINGLE TES-ENTRY CHOKE POINT (Stage 1B) ──
# ``apply_chase`` can admit a contest, but it does not own the handover to
# TES: every engine TES entry goes through ``contact_authority.enter_tes``.
from game.defence.defence_lvl_1.contact_authority import (
    enter_tes as _enter_tes,
    CHANNEL_LVL1_CHASE_ENGAGE as _CH_LVL1,
)


class DefensiveBehaviourLvl1:

    def __init__(self, tuners=None, geometry=None):
        self.tuners = tuners
        self.def_geometry = geometry

    # ═══════════════════════════════════════════════════════════════
    # D3 — Split methods (called from DefensiveBehaviourUnified)
    # ═══════════════════════════════════════════════════════════════

    def apply_contact(self, world, defenders):
        """
        TACKLE_CONTACT: freeze only TACKLER_1/2/3 and MARKER_A/B at current position.
        All other defenders (including DEF_RETREAT_10) continue retreating toward
        ruck_x + retreat_compliance_distance_m.

        Per SDEA 5.3: Only TACKLER_1/2/3 and MARKER_A/B may freeze.
        All other defenders must remain unfrozen and retreat RIGHT (+X) continuously.
        """
        ALLOWED_FREEZE_ROLES = frozenset({
            DefenderRole.TACKLER_1,
            DefenderRole.TACKLER_2,
            DefenderRole.TACKLER_3,
            DefenderRole.MARKER_A,
            DefenderRole.MARKER_B,
        })
        ruck_x = getattr(world, "ruck_x_m", 60.0)
        retreat_target_x = _anchor_m(world)

        for i, d in enumerate(defenders):
            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if d.defender_role in ALLOWED_FREEZE_ROLES:
                if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                    # MARKER AUTHORITY LOCK (2026-09-04): a marker freeze is a
                    # VELOCITY-ONLY hold — target stays the ruck-pin geometry
                    # authority, never d.x/d.y.
                    from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
                    d.target_x, d.target_y = _mt(world, d)
                else:
                    d.target_x = d.x
                    d.target_y = d.y
                d.frozen = True
            else:
                d.frozen = False
                d.target_x = retreat_target_x
                # SPACING Y (Task F, 2026-08-10): retreat keeps spacing Y,
                # never the defender's current Y (lane/fallback-free).
                from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
                d.target_y = _frame_spacing_y(world, d, self.tuners)

    def apply_markers(self, world, defenders):
        """TACKLE_DOMINANCE non-marker retreat/spacing maintenance only.

        MARKER-AUTHORITY SINGLE-SOURCE (2026-09-04): marker ROLE assignment
        (``defender_role = MARKER_A/B``) is owned SOLELY by
        ``DefenceManager.assign_markers_for_new_tackle``, invoked at
        TACKLE_CONTACT (strict) and RELEASE/PTB.  This lvl1 defensive-
        behaviour method is therefore a pure non-marker fold-back: it NEVER
        writes ``defender_role = MARKER_A/B`` and NEVER clears a MARKER role.

        It only drives retreat + spacing for non-TES, non-marker line
        defenders during the DOMINANCE / TES-DRIVE window so the line keeps
        retreating while the tackle resolves.  Markers (assigned only by
        DefenceManager) and TES tacklers are left untouched.
        """
        # P12: Fold back every non-tes-isolated, non-marker defender.
        # Markers (MARKER_A/B) are NOT touched here — they are owned by
        # DefenceManager and the OPE phase handlers.  TES tackler roles,
        # DUMMY_HALF, KICK_CHASE, and the fullback are also skipped.
        ALLOWED_FREEZE_ROLES = frozenset({
            DefenderRole.TACKLER_1,
            DefenderRole.TACKLER_2,
            DefenderRole.TACKLER_3,
            DefenderRole.MARKER_A,
            DefenderRole.MARKER_B,
            DefenderRole.DUMMY_HALF,
        })
        retreat_target_x = _anchor_m(world)
        for d in defenders:
            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if d.defender_role in ALLOWED_FREEZE_ROLES:
                continue
            if getattr(d, "fullback_role_active", False):
                continue
            d.frozen = False
            d.target_x = retreat_target_x
            # SPACING Y (Task F, 2026-08-10): retreat keeps spacing Y.
            from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
            d.target_y = _frame_spacing_y(world, d, self.tuners)

    def apply_step_up(self, world, defenders, line_x, lane_y):
        """
        STEP UP: non-frozen, non-marker defenders → target_x = line_x,
        target_y = geometry lane with 80/20 blend to prevent jitter.
        """
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES active guard: no step-up during tackle phase
        # ═══════════════════════════════════════════════════════════════
        if is_tackle_phase(world):
            return
        # ── STEP‑03A‑06C: subsystem flag guard ──
        if not world.defensive_line_update_enabled:
            return

        # INPUT ORDER (2026-08-09): compute_line_shape returns role-driven
        # Y targets in INPUT order — no Y-sort, no lane-index ordering.
        sorted_defs = list(defenders)
        # Compute geometry lane targets for this line
        lane_targets = self.def_geometry.compute_line_shape(world, sorted_defs, line_x)

        for i, d in enumerate(sorted_defs):
            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if getattr(d, "freeze_timer", 0.0) > 0:
                if self._is_marker(d):
                    # MARKER AUTHORITY LOCK (2026-09-04): marker freeze is a
                    # VELOCITY-ONLY hold — target stays the ruck-pin geometry
                    # authority, never d.x/d.y.
                    from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
                    d.target_x, d.target_y = _mt(world, d)
                    d.frozen = True
                else:
                    d.target_x = d.x
                    d.target_y = d.y
                continue
            if self._is_marker(d):
                # MARKER AUTHORITY LOCK (2026-09-04): marker target = the
                # single X-depth ruck pin (ruck_x + depth, ruck_y).
                from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
                d.target_x, d.target_y = _mt(world, d)
                d.frozen = True
                continue
            d.target_x = line_x
            # SPACING Y (Task F, 2026-08-10): step-up drives X only; Y is
            # spacing_targets[player].y (no lane blend / current-Y blend).
            from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
            d.target_y = _frame_spacing_y(world, d, self.tuners)

            # FACING OVERRIDE (MANDATORY): face BC while stepping up
            bc = world.ball_carrier
            if bc is not None:
                angle_to_bc = math.atan2(bc.y - d.y, bc.x - d.x)
                d.set_facing(angle_to_bc)

    def apply_chase(self, world, defenders, line_x, lane_y):
        """
        CHASE: engagement steering toward ball carrier with geometry blend.

        Defenders compute angle to BC, blend BC y with lane y (70/30),
        apply role-based steering factor, and set target with engagement
        offset to create a curved approach.

        Geometry lane targets are blended 40/60 with steering to preserve
        spacing without fighting the chase direction.
        """
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES active guard: no chase during tackle phase
        # ═══════════════════════════════════════════════════════════════
        if is_tackle_phase(world):
            return
        # ── STEP‑03A‑06C: subsystem flag guard ──
        if not world.defensive_chase_enabled:
            return

        # Use ball carrier's TRUE position (not eased ball.x_m) — critical for chase steering
        if world.ball_carrier is not None:
            bc_x = world.ball_carrier.x
            bc_y = world.ball_carrier.y
        else:
            ball = world.ball
            bc_x = getattr(ball, "true_x_m", getattr(ball, "x", 60.0))
            bc_y = getattr(ball, "true_y_m", getattr(ball, "y", 35.0))

        # INPUT ORDER (2026-08-09): geometry lane targets are returned in
        # input order — no Y-sort, no lane-index ordering.
        sorted_defs = list(defenders)
        # Compute geometry lane targets for spacing preservation
        lane_targets = self.def_geometry.compute_line_shape(world, sorted_defs, line_x)

        for i, d in enumerate(sorted_defs):
            # ───────────────────────────────────────────────
            # STEP‑02A‑06 — Missed tackle recovery
            # ───────────────────────────────────────────────
            if getattr(d, "recovery_timer", 0.0) > 0.0:
                d.recovery_timer -= world.dt

                # ─────────────────────────────────────────────
                # STEP‑02A‑08 — Stumble movement
                # ─────────────────────────────────────────────
                # Push defender backward along facing direction
                stumble_speed = 1.0  # m/s, tune later
                d.x -= math.cos(d.facing) * stumble_speed * world.dt
                d.y -= math.sin(d.facing) * stumble_speed * world.dt

                d.target_x = d.x
                d.target_y = d.y
                continue

            # ───────────────────────────────────────────────
            # STEP‑02A‑11 — Auto‑rotate toward BC after recovery
            # ───────────────────────────────────────────────
            if getattr(d, "recovery_timer", 0.0) <= 0.0 and d.state == "MISSED_TACKLE":
                dx = bc_x - d.x
                dy = bc_y - d.y
                d.set_facing(math.atan2(dy, dx))
                d.state = "CHASE"  # re‑engage

            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if getattr(d, "freeze_timer", 0.0) > 0:
                if self._is_marker(d):
                    # MARKER AUTHORITY LOCK (2026-09-04): marker freeze is a
                    # VELOCITY-ONLY hold — target stays the ruck-pin geometry
                    # authority, never d.x/d.y.
                    from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
                    d.target_x, d.target_y = _mt(world, d)
                    d.frozen = True
                else:
                    d.target_x = d.x
                    d.target_y = d.y
                continue
            if self._is_marker(d):
                # MARKER AUTHORITY LOCK (2026-09-04): marker target = the
                # single X-depth ruck pin (ruck_x + depth, ruck_y).
                from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
                d.target_x, d.target_y = _mt(world, d)
                d.frozen = True
                continue

            # 1. Compute lateral steering toward BC
            dx = bc_x - d.x
            dy = bc_y - d.y

            # 2. Compute angle toward BC
            angle = math.atan2(dy, dx)

            # ───────────────────────────────────────────────
            # STEP‑02A‑02 — Pre‑tackle attempt hook
            # ───────────────────────────────────────────────

            dist = math.hypot(dx, dy)

            tackle_radius = self.tuners.tackle_radius_m
            dive_radius   = self.tuners.dive_radius_m

            # Normal tackle window
            if dist <= tackle_radius:
                d._pre_tackle_window = "NORMAL"

            # Diving tackle window
            elif dist <= dive_radius:
                d._pre_tackle_window = "DIVE"

            else:
                d._pre_tackle_window = None

            # ───────────────────────────────────────────────
            # STEP‑02A‑04 — Trigger TES on successful pre‑tackle
            # ───────────────────────────────────────────────

            if d._pre_tackle_window is not None:
                if try_pre_tackle(world, d, world.ball_carrier, self.tuners):
                    # Successful tackle — start TES
                    ope = world.open_play_engine
                    # STAGE 2A: hand the examined population to the
                    # preservation layer with the admission, so the record
                    # holds WHO was in radius and WHAT each body's declared
                    # stamps were at this moment -- not just the winner.  A
                    # pure data argument: the admission above and the
                    # handover below are unchanged.
                    _enter_tes(world, ope, world.ball_carrier, d, _CH_LVL1,
                               defenders=defenders)
                    return  # stop chase for this defender

            # 3. Compute steering strength based on depth role
            # "D" is the universal bulk/fallback depth role (ruck-driven
            # role system, 2026-08-08).
            role = getattr(d, "depth_role", "D")
            role_factor = self.tuners.ROLE_STEER_FACTOR.get(role, 1.0)

            # 4-6. SPACING Y (Task F, 2026-08-10): the chase writer drives X
            # only (BC-relative engagement offset).  Y MUST be
            # spacing_targets[player].y — no bc_y/lane_y blend, no chase_y,
            # no geometry-lane blend, no engagement-angle Y component.
            d.target_x = bc_x - (self.tuners.engagement_offset_m * math.cos(angle))
            from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
            d.target_y = _frame_spacing_y(world, d, self.tuners)

            # 7. Mark defender as "engaging"
            d.is_engaging = True

    # ═══════════════════════════════════════════════════════════════
    # Original apply() — kept for backward compatibility
    # ═══════════════════════════════════════════════════════════════

    def apply(self, world, defenders, line_x, lane_y):
        # ═══════════════════════════════════════════════════════════════
        # TES GUARD — do NOT run during TES phases (TACKLE_INIT through
        # TACKLE_DOMINANCE). TES handler in OpenPlayEngine owns all player
        # positioning during these phases. This guard prevents overwriting
        # the TES handler's correct retreat/anchor targets.
        # ═══════════════════════════════════════════════════════════════
        if getattr(world, 'tes_locked', False) or is_tackle_phase(world):
            return

        # ═══════════════════════════════════════════════════════════════
        # PHASE GUARD — defence only runs during OPEN_PLAY phase.
        # ═══════════════════════════════════════════════════════════════
        from world.world_state import GamePhase
        if world.phase != GamePhase.OPEN_PLAY:
            return
        bc = world.ball_carrier
        sub = world.sub_state
        # ── Removed legacy TACKLE freeze block — handled by OPE TES gate.
        # ── Removed legacy old dispatch for TACKLE_CONTACT/TACKLE_DOMINANCE
        #    — handled by is_tackle_phase() return above.
        # ── Removed legacy ruck_bubble freeze — OPE TES gate handles it.
        # ── Removed legacy marker assignment here — done in _prepare_release().

        sub = world.sub_state

        non_chase_defs = [d for d in defenders if d.defender_role != DefenderRole.KICK_CHASE]
        if not non_chase_defs:
            return

        if sub == GameSubState.TACKLE_CONTACT:
            for d in non_chase_defs:
                d.frozen = True
                d.target_x = d.x
                d.target_y = d.y
            return

        if sub == GameSubState.TACKLE_DOMINANCE:
            # MARKER-AUTHORITY SINGLE-SOURCE (2026-09-04): marker role
            # assignment (defender_role = MARKER_A/B) is owned SOLELY by
            # DefenceManager.assign_markers_for_new_tackle.  This legacy
            # apply() branch must NEVER write a marker role — it only folds
            # non-marker line defenders back to the retreat anchor.
            from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
            ALLOWED_FREEZE_ROLES = frozenset({
                DefenderRole.TACKLER_1,
                DefenderRole.TACKLER_2,
                DefenderRole.TACKLER_3,
                DefenderRole.MARKER_A,
                DefenderRole.MARKER_B,
                DefenderRole.DUMMY_HALF,
            })
            retreat_target_x = _anchor_m(world)
            for d in non_chase_defs:
                if d.defender_role in ALLOWED_FREEZE_ROLES:
                    continue
                if getattr(d, "fullback_role_active", False):
                    continue
                d.frozen = False
                d.target_x = retreat_target_x
                d.target_y = _frame_spacing_y(world, d, self.tuners)
            return

        # INPUT ORDER (2026-08-09): no Y-sort, no lane-index ordering.
        sorted_defs = list(non_chase_defs)

        for i, d in enumerate(sorted_defs):
            if getattr(d, "freeze_timer", 0.0) > 0:
                if self._is_marker(d):
                    # MARKER AUTHORITY LOCK (2026-09-04): marker freeze is a
                    # VELOCITY-ONLY hold — target stays the ruck-pin geometry
                    # authority, never d.x/d.y.
                    from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
                    d.target_x, d.target_y = _mt(world, d)
                    d.frozen = True
                else:
                    d.target_x = d.x
                    d.target_y = d.y
                continue

            if self._is_marker(d):
                # MARKER AUTHORITY LOCK (2026-09-04): marker target = the
                # single X-depth ruck pin (ruck_x + depth, ruck_y).
                from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
                d.target_x, d.target_y = _mt(world, d)
                d.frozen = True
                continue

            if sub in (GameSubState.RUCK_FORMING, GameSubState.PTB, GameSubState.POST_RUCK_OPEN_PLAY):
                self._apply_retreat(world, d, lane_y, i)
                continue

            if sub == GameSubState.OPEN_PLAY:
                self._apply_open_play(d, world, line_x, lane_y, i)
                continue

            d.target_x = line_x
            # SPACING Y (Task F, 2026-08-10): apply() fallback keeps spacing Y.
            from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
            d.target_y = _frame_spacing_y(world, d, self.tuners)

    @staticmethod
    def _is_marker(d):
        return getattr(d, "defender_role", None) in (
            DefenderRole.MARKER_A,
            DefenderRole.MARKER_B,
        )

    def _apply_retreat(self, world, d, lane_y, idx):
        """Legacy retreat: push defender RIGHT (+X) behind the ruck.

        FIXED direction: defence always retreats RIGHT (+X), away from the
        ruck (toward the attacking team's tryline at x=110).

        STEP‑03A‑08: Use world.defensive_line_anchor instead of ruck_x + 10.

        FACING OVERRIDE (MANDATORY): defender faces BC while retreating.
        """
        if self._is_marker(d):
            return
        # TASK VII (2026-08-20) — AUTHORITY-ROUTED: the retreat X must be
        # read through anchor_authority.defensive_line_anchor_m (ruck_x +
        # 10, locked-published fallback), never the bare world field.  The
        # bare read bypassed the authority fallback stamping and was one
        # edit away from drifting from the frame lock.
        retreat_x = _anchor_m(world)
        d.target_x = retreat_x
        # SPACING Y (Task F, 2026-08-10): retreat keeps spacing Y.
        from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
        d.target_y = _frame_spacing_y(world, d, self.tuners)

        # Facing override: face BC while retreating
        bc = world.ball_carrier
        if bc is not None:
            angle_to_bc = math.atan2(bc.y - d.y, bc.x - d.x)
            d.set_facing(angle_to_bc)

    @staticmethod
    def _get_ball_x(world):
        """Extract ball X position, preferring ball carrier's true position."""
        if world.ball_carrier is not None:
            return world.ball_carrier.x
        ball = getattr(world, "ball", None)
        if ball is not None:
            if getattr(ball, "possessed_by", None) is not None:
                return ball.possessed_by.x
            return getattr(ball, "true_x_m", getattr(ball, "x", 60.0))
        return getattr(world, "ball_x_m", 60.0)

    @staticmethod
    def _get_ball_y(world):
        """Extract ball Y position, preferring ball carrier's true position."""
        if world.ball_carrier is not None:
            return world.ball_carrier.y
        ball = getattr(world, "ball", None)
        if ball is not None:
            if getattr(ball, "possessed_by", None) is not None:
                return ball.possessed_by.y
            return getattr(ball, "true_y_m", getattr(ball, "y", 35.0))
        return getattr(world, "ball_y_m", 35.0)

    def _apply_open_play(self, d, world, line_x, lane_y, idx):
        ball_x = self._get_ball_x(world)
        ball_y = self._get_ball_y(world)

        step_up = self.tuners.defender_activation_radius_m * 0.15
        chase = self.tuners.defender_activation_radius_m * 0.30

        if ball_x < line_x + step_up:
            d.target_x = line_x
        elif ball_x < line_x + chase:
            d.target_x = line_x - 1.0
        elif ball_x > line_x + chase:
            d.target_x = ball_x
        else:
            d.target_x = line_x

        # SPACING Y (Task F, 2026-08-10): legacy open-play writer drives X
        # only; Y is always spacing_targets[player].y (no ball_y, no lane_y).
        from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
        d.target_y = _frame_spacing_y(world, d, self.tuners)


# ═══════════════════════════════════════════════════════════════════
# Step‑03A‑06 — Pre‑tackle trigger (realistic contact conditions)
# ═══════════════════════════════════════════════════════════════════

def forward_projection_hits_bc(defender, bc):
    """Return True if defender's forward-facing ray intersects BC's bounding circle.

    Casts a ray of length 2.0m from defender along defender.facing.
    Uses the CollisionBody radius for BC (default 0.55m).

    This is a line-segment-vs-circle test.
    """
    ray_length = 2.0
    defender_radius = getattr(defender, 'radius', 0.55)
    bc_radius = getattr(bc, 'radius', 0.55)
    combined_radius = bc_radius  # treat defender as point for ray

    # Ray endpoint
    ex = defender.x + math.cos(defender.facing) * ray_length
    ey = defender.y + math.sin(defender.facing) * ray_length

    # Vector from ray start to BC centre
    dx = bc.x - defender.x
    dy = bc.y - defender.y

    # Project BC centre onto ray direction (clamped 0..1)
    ray_dx = ex - defender.x
    ray_dy = ey - defender.y
    ray_len_sq = ray_dx * ray_dx + ray_dy * ray_dy
    if ray_len_sq < 0.0001:
        return False

    t = (dx * ray_dx + dy * ray_dy) / ray_len_sq
    t = max(0.0, min(1.0, t))

    # Closest point on ray to BC centre
    closest_x = defender.x + t * ray_dx
    closest_y = defender.y + t * ray_dy

    # Distance from closest point to BC centre
    dist_to_center = math.hypot(bc.x - closest_x, bc.y - closest_y)

    return dist_to_center < combined_radius


def try_pre_tackle(world, defender, bc, tuners):
    """Return True if tackle should start (TES entry).

    TACKLE AUTHORITY STACK — Step‑02A‑03 (2026-07-21).
    This is the SOLE gate for TES entry.  When it returns False the
    BC breaks the tackle — no TES starts, BC continues running.

    As of the 12-state pretackle machine (2026-09-09) the gate body is a
    delegate to the machine's ``run_machine`` + ``should_engage`` + 
    ``apply_outcome``.  The machine runs AFTER anticipation and jitter
    removal (the Stage-9 lean and the finalise/target-stabiliser pass),
    reads a single MovementState-driven posture, resolves it into one of
    12 states, maps that state onto one of 5 outcomes (bounce-back /
    fall-over / stunned-upright / wrong-foot / clean-engage), and feeds
    TES ONLY when contact geometry, closing posture, planted predicate and
    wrong-foot delta all resolve into a valid engage window.  The legacy
    ``pretackle_classifier.classify_pretackle`` is retained verbatim so
    ``verify_pretackle_classifier`` keeps passing and the two views can be
    diffed.
    """
    from game.defence.defence_lvl_1.pretackle_machine import (
        run_machine,
        should_engage,
        apply_outcome,
        ENGAGE_STATES,
    )
    state = run_machine(world, defender, bc, tuners)
    if should_engage(world, defender, bc, tuners, state=state):
        return True
    # ── NO CLEAN-ENGAGE MINTED WITHOUT TES (2026-10-01) ──
    # ``apply_outcome`` maps the three ENGAGE states onto CLEAN_ENGAGE, which
    # ``pretackle_machine`` documents as "the ONLY outcome that feeds TES".
    # We only reach this line because the engage window did NOT open
    # (should_engage was False -- e.g. the defender never closed to
    # ENGAGE_DISTANCE_M).  Feeding an engage state to apply_outcome here would
    # MINT a CLEAN_ENGAGE that never fed TES: the "receives CLEAN_ENGAGE but
    # never converts" artefact, where every probe reading apply_outcome saw a
    # clean engage on a frame in which no tackle started.
    #
    # apply_outcome is a documented no-op for CLEAN_ENGAGE, so skipping it is
    # byte-identical physics: nothing is applied, we simply do not LABEL a
    # non-contact as a clean engage.  Genuine non-engage states (BOUNCE_BACK /
    # FALL_OVER / STUNNED_UPRIGHT / WRONG_FOOT) still resolve exactly as before.
    if state not in ENGAGE_STATES:
        apply_outcome(world, defender, bc, tuners, state=state)
    return False
