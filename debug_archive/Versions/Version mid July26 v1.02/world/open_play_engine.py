"""
world/open_play_engine.py -- Single-FSM OpenPlayEngine (rewrite).

Owns the open-play -> tackle -> PTB -> post-ruck -> open-play lifecycle via
a single internal phase machine mapped to GameSubState for downstream systems.

Key design rules:
  1. TES runs ONLY during TACKLE_INIT through TACKLE_DOMINANCE.
     At DOMINANCE->RELEASE, ALL TES geometry/containment/retreat stops.
  2. PTBManager is the SOLE authority for DH movement during PTB_PREP and PTB_EXEC.
     dh_controller.move_to_ruck() MUST NOT be called in those phases.
  3. DH gets defender_role = DefenderRole.DUMMY_HALF at RELEASE and keeps it
     through PTB.  DH is unfrozen throughout RELEASE -> PTB.
"""

import math
import random

from world.world_state import WorldState, GameWorldState, GameSubState
from player.player import Player, DefenderRole

from game.tes.tes import tes_start_tackle, _make_ctx
from game.tes.tes_context import TESContext, PlayerContext, TackleEngagement
from game.tes.tes_outcomes import TESOutcome
from game.tes.tes_geometry import can_join
from game.tes.tes_dominance import (
    update_waveform,
    compute_dominance,
    compute_drive_distance,
    compute_tackle_duration,
    compute_ptb_time,
)
from game.tes.tes_checks import (
    check_offload,
    check_strip,
    check_fumble,
    check_penalty,
    check_six_again,
    check_line_break,
)
from game.tes.ptb_manager import PTBManager
from game.attack.attack_lvl_4.dummy_half import DummyHalfController
from game.attack.attack_lvl_4.decision_engine import DecisionEngine
from game.attack.attack_lvl_1.attack_geometry import AttackGeometry
from game.attack.attack_lvl_1.attack_perception import AttackPerception
from game.attack.attack_lvl_1.attack_tuners import AttackTuners
from game.attack.attack_lvl_2.ball_carrier_intent import BallCarrierIntent
from game.attack.attack_lvl_3.run_logic import RunLogic
from game.attack.attack_lvl_4.attack_controller import AttackController
from game.defence.defence_lvl_1.defensive_behaviour_unified import DefensiveBehaviourUnified
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_geometry import DefensiveGeometry


# -- Constants -----------------------------------------------------------
TACKLE_RADIUS_M: float = 1.5
TACKLE_ANGLE_THRESHOLD_DEG: float = 60.0
POST_PTB_IMMUNITY_FRAMES: int = 8
RETREAT_DISTANCE_M: float = 10.0
RELEASE_PAUSE_S: float = 0.15        # brief pause in RELEASE before PTB prep
POST_PTB_DELAY_S: float = 0.2        # brief pause before POST_RUCK_OPEN_PLAY
RETREAT_DURATION_S: float = 1.0      # how long defenders retreat in POST_RUCK
POST_PTB_COOLDOWN_S: float = 0.5     # tackle immunity after PTB



def _retreat_target_x(ruck_x: float) -> float:
    """X-coordinate defenders retreat to after a ruck (behind the ruck)."""
    return ruck_x - RETREAT_DISTANCE_M


def _move_toward(p: Player, tx: float, ty: float, spd: float, dt: float) -> None:
    """Simple velocity-based movement toward (tx, ty)."""
    dx = tx - p.x
    dy = ty - p.y
    dist = math.hypot(dx, dy)
    if dist > 0.01:
        p.vx = (dx / dist) * spd
        p.vy = (dy / dist) * spd
        p.x += p.vx * dt
        p.y += p.vy * dt
    else:
        p.vx = 0.0
        p.vy = 0.0


# ═══════════════════════════════════════════════════════════════════════
# OpenPlayEngine
# ═══════════════════════════════════════════════════════════════════════

class OpenPlayEngine:
    """
    Single FSM for open-play -> tackle -> PTB -> post-ruck -> open-play.

    Internal phases (self._phase):
        OPEN_PLAY,
        TACKLE_INIT, TACKLE_CONTACT, TACKLE_DRIVE, TACKLE_DOMINANCE,
        RELEASE,
        PTB_PREP, PTB_EXEC, PTB,
        POST_RUCK_OPEN_PLAY

    Each maps to a GameSubState for downstream consumption (defence, markers, etc.).
    Defence runs continuously every frame regardless of phase.
    """

    def __init__(self, world: WorldState, seed=None):
        self.world = world
        self.rng = random.Random(seed)

        # -- Delegates --
        self.ptb_manager = PTBManager()
        self.dh_controller = DummyHalfController()

        # -- Internal phase machine: always OPEN_PLAY from frame 0 --
        self._phase = "OPEN_PLAY"
        self._phase_timer: float = 0.0

        # -- Tackle / TES state --
        self.ball_carrier: Player | None = None
        self.primary: Player | None = None       # primary tackler
        self.joiners: list[Player] = []           # secondary tacklers
        self.tes_ctx: TESContext | None = None
        self.tes_outcome: TESOutcome | None = None

        # -- Cooldown / immunity --
        self._cooldown_timer: float = 0.0
        self.frames_since_ptb: int = 0

        # -- Phase-4D-2: tackle approach micro-state flag --
        self.bc_in_approach = False

        # -- Phase 3: decision engine --
        self.decision_engine = DecisionEngine(world)

        # --- Phase 4 HS Attack Modules -------------------------------------------
        self.attack_geometry = AttackGeometry(self)
        self.attack_perception = AttackPerception(self.attack_geometry)
        self.ball_carrier_intent = BallCarrierIntent()
        self.run_logic = RunLogic()
        self.attack_controller = AttackController()

        # -- Phase-2 defence tuners --
        self.def_tuners = DefenceTuners()
        self.def_tuners.assert_radii()

        # -- Phase-2 defence (unified Lvl‑1 / STEP‑2 behaviour) --
        self.def_behaviour = DefensiveBehaviourUnified(self.def_tuners)

        # -- Phase-E STEP‑1 geometry layer --
        self.def_geometry = DefensiveGeometry(self.def_tuners)

        # -- DH reference (set during RELEASE, used through PTB) --
        self.dh: Player | None = None

    # -- Backward-compat property: run.py and debug scripts read open_play.tes_phase.value --
    @property
    def tes_phase(self):
        """Return a compat object with .value matching the current internal phase string."""
        class _CompatPhase:
            def __init__(self, phase_str):
                self.value = phase_str
        return _CompatPhase(self._phase)

    # ═══════════════════════════════════════════════════════════════════
    # Main entry -- called once per frame
    # ═══════════════════════════════════════════════════════════════════

    def update(self, dt: float) -> None:
        """Main update -- dispatch to current phase handler.

        Defence ALWAYS runs every frame, independent of phase or
        ball-carrier status.  Attack pipeline (decision engine, intent,
        run vector, apply_vector) only runs when a ball carrier exists
        and we're in OPEN_PLAY.  Tackle checks are also gated on
        ``self.world.ball_carrier is not None`` and ``in_kickoff``.
        """
        self.world.frame_count += 1
        self.world.dt = dt

        # ═══ Defence runs every frame, always ═══
        self._update_defence(dt)

        bc = self.world.ball_carrier
        if bc is not None:
            self.ball_carrier = bc
            self.frames_since_ptb += 1
        else:
            self.ball_carrier = None

        # Dispatch
        phase_map = {
            "OPEN_PLAY":             self._update_open_play,
            "TACKLE_INIT":           self._update_tackle_init,
            "TACKLE_CONTACT":        self._update_tackle_contact,
            "TACKLE_DRIVE":          self._update_tackle_drive,
            "TACKLE_DOMINANCE":      self._update_tackle_dominance,
            "RELEASE":               self._update_release,
            "PTB_PREP":              self._update_ptb_prep,
            "PTB_EXEC":              self._update_ptb_exec,
            "PTB":                   self._update_ptb,
            "POST_RUCK_OPEN_PLAY":   self._update_post_ruck_open_play,
        }
        handler = phase_map.get(self._phase)
        if handler:
            handler(dt)
        else:
            # Safety fallback -- reset to open play
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "FALLBACK")

    # ═══════════════════════════════════════════════════════════════════
    # Phase helpers
    # ═══════════════════════════════════════════════════════════════════

    def _set_phase(self, phase: str, sub_state: GameSubState, reason: str) -> None:
        """Transition internal phase and set world sub_state."""
        self._phase = phase
        self._phase_timer = 0.0
        self.world.set_sub_state(sub_state, reason)

    # -- Public entry point (called from catch_system after kickoff catch) --

    def start(self, player: Player) -> None:
        """Register ball carrier after kickoff catch.  No phase change --
        the engine is already in OPEN_PLAY since boot."""
        self.ball_carrier = player
        self.world.game_state = GameWorldState.OPEN_PLAY
        self.world.set_sub_state(GameSubState.OPEN_PLAY, "OpenPlayEngine.start()")
        print(f"[OPEN_PLAY] start() registered Player #{player.player_id}")

    # -- OPEN_PLAY ------------------------------------------------------

    def _update_open_play(self, dt: float) -> None:
        """Normal open play: BC runs, defence tracks, check for tackle initiation."""
        bc = self.ball_carrier
        if bc is None:
            return

        # Cooldown
        if self._cooldown_timer > 0.0:
            self._cooldown_timer -= dt

        # Phase 3: set BC intent
        self.decision_engine.update(dt)

        # Ensure BC has tuners
        if bc.tuners is None:
            bc.tuners = AttackTuners()

        # --- Phase 4: HS Attack Pipeline -----------------------------------------

        # 1. Perception (Level 1)
        per = self.attack_perception.compute(bc)

        # 2. Intent (Level 2)
        bc.intent = self.ball_carrier_intent.decide(bc, per)

        # 3. Behaviour (Level 3) — Phase-4C-3: blended geometry
        defenders = [d for d in self.world.defending_team
                     if d.defender_role != DefenderRole.DUMMY_HALF]
        geom = {
            "forward": self.attack_geometry.forward_corridor(bc),
            "sideline": self.attack_geometry.sideline_geometry(bc),
            "def_cone": self.attack_geometry.defender_cone(bc, defenders),
            "lane": self.attack_geometry.lane_geometry(bc),
            "gap": self.attack_geometry.gap_geometry(bc, defenders),
        }
        vx, vy = self.run_logic.compute_run_vector(bc, geom)

        # 4. Execution (Level 4)
        self.attack_controller.apply_vector(bc, vx, vy, dt)

        # [STEP-02A-01] Legacy tackle trigger disabled.
        # Lvl-1 chase is now the sole authority for initiating tackles.
        # if self._cooldown_timer <= 0.0 and self.frames_since_ptb >= POST_PTB_IMMUNITY_FRAMES:
        #     self._maybe_start_tackle(bc)

    def _update_bc_run(self, bc: Player, dt: float) -> None:
        """Ball carrier forward movement -- simple version."""
        bc.vx = 5.0
        bc.x += bc.vx * dt
        # Track ruck position at BC
        self.world.ruck_x_m = bc.x
        self.world.ruck_y_m = bc.y

    def _update_defence(self, dt: float) -> None:
        """
        Update defensive line positions using role-depth-based anchoring.

        Anchor is:
            ruck_x = world.ruck_x_m if set else ball.x
            base_line_x = ruck_x - def_line_depth_m  (behind the ruck)

        Each defender's target_x = base_line_x + ROLE_DEPTH_M[role].
        Depth classification (PRIMARY/SECONDARY/PASSIVE) decides who
        engages (step-up/chase) vs. retreats.
        """
        # ═══════════════════════════════════════════════════════════
        # BALL DEBUG: Print real vs eased ball position every frame
        # ═══════════════════════════════════════════════════════════
        bc = self.world.ball_carrier
        b = self.world.ball
        b_x = getattr(b, "true_x_m", getattr(b, "x", "???")) if b is not None else "N/A"
        bc_x = bc.x if bc is not None else "N/A"
        print(f"BALL DEBUG: world.ball.x={b_x}, ball_carrier.x={bc_x}")

        defenders = self.world.defending_team

        # ── Unfreeze defenders during normal open play ──
        # reset_for_kickoff() sets frozen=True on all players.
        # OpenPlayEngine only unfreezes DH/BC/tacklers in RELEASE.
        # Without this, defenders stay frozen forever and never move
        # despite having correct target_x set by the behaviour layer.
        for d in defenders:
            d.frozen = False

        # Compute the anchor: ruck position (or ball.x if no ruck yet)
        if self.world.ruck_x_m is not None:
            ruck_x = self.world.ruck_x_m
        else:
            ruck_x = getattr(self.world.ball, "true_x_m", 60.0)

        base_line_x = ruck_x - self.def_tuners.def_line_depth_m

        # STEP‑1 geometry — compute lane_y from DefensiveGeometry
        lane_y = self.def_geometry.compute_line_shape(self.world, defenders, base_line_x)

        # Unified defensive behaviour (dispatches Lvl‑1 / STEP‑2 based on sub-state
        # and per-defender depth classification)
        self.def_behaviour.apply(
            self.world,
            defenders,
            base_line_x,
            lane_y,
        )

        # --- APPLY MOVEMENT: convert target_x/y into velocity for all defenders ---
        for d in defenders:
            d.update(dt)

    # -- Tackle initiation ----------------------------------------------

    # [STEP-02A-01] _enter_tackle_approach disabled (was used only by _maybe_start_tackle).
    # def _enter_tackle_approach(self, bc: Player, d: Player) -> None:
    #     """Phase-4D-2: short pre-contact shaping before TES starts."""
    #     self.bc_in_approach = True
    #
    #     dx = d.x - bc.x
    #     dy = d.y - bc.y
    #     dist = math.hypot(dx, dy)
    #     if dist < 0.01:
    #         return
    #
    #     # Direction toward tackler
    #     nx = dx / dist
    #     ny = dy / dist
    #
    #     # Reduce speed slightly (approach deceleration)
    #     speed = math.hypot(bc.vx, bc.vy)
    #     speed *= 0.75  # soften approach
    #
    #     bc.vx = nx * speed
    #     bc.vy = ny * speed

    # [STEP-02A-01] _lock_bc_momentum_for_tackle disabled (was used only by _maybe_start_tackle).
    # def _lock_bc_momentum_for_tackle(self, bc: Player, d: Player) -> None:
    #     """Phase-4D-1: lock BC momentum toward primary tackler just before tackle start."""
    #     dx = d.x - bc.x
    #     dy = d.y - bc.y
    #     dist = math.hypot(dx, dy)
    #     if dist < 0.01:
    #         return
    #
    #     # Direction toward tackler
    #     nx = dx / dist
    #     ny = dy / dist
    #
    #     # Preserve current speed
    #     speed = math.hypot(bc.vx, bc.vy)
    #     if speed <= 0.01:
    #         return
    #
    #     # Redirect velocity toward tackler
    #     bc.vx = nx * speed
    #     bc.vy = ny * speed

    # [STEP-02A-01] _maybe_start_tackle disabled.
    # Lvl-1 chase now calls _start_tackle() directly after pre-tackle logic.
    # def _maybe_start_tackle(self, bc: Player) -> None:
    #     """Check proximity and angle to start a tackle."""
    #     if self._phase != "OPEN_PLAY":
    #         return
    #
    #     closest = None
    #     cd = 9999.0
    #     for d in self.world.defending_team:
    #         if d.defender_role == DefenderRole.DUMMY_HALF:
    #             continue
    #         dist = math.hypot(bc.x - d.x, bc.y - d.y)
    #         if dist < cd:
    #             cd = dist
    #             closest = d
    #
    #     if closest is None:
    #         return
    #     if cd > TACKLE_RADIUS_M:
    #         return
    #     if _approach_angle(bc, closest) > TACKLE_ANGLE_THRESHOLD_DEG:
    #         return
    #
    #     # Phase-4D-2: enter tackle approach for one frame
    #     self._enter_tackle_approach(bc, closest)
    #
    #     # Phase-4D-1: momentum lock
    #     self._lock_bc_momentum_for_tackle(bc, closest)
    #
    #     # Initiate tackle
    #     self.bc_in_approach = False
    #     self._start_tackle(bc, closest)

    def _start_tackle(self, bc: Player, d: Player) -> None:
        """Create TES context and enter TACKLE_INIT.

        Delegates all TES entry operations to tes_start_tackle() so
        that TES entry behaviour is centralised in the TES module.
        """
        # Phase-4D-1: entry smoothing — soften BC velocity on first contact frame
        bc.vx *= 0.6
        bc.vy *= 0.6

        self.primary = d
        self.joiners = []

        # Delegate all TES entry setup to the central function
        ctx = tes_start_tackle(bc, d, self.world)

        self.tes_ctx = ctx
        self.tes_outcome = TESOutcome()

        self._set_phase("TACKLE_INIT", GameSubState.TACKLE_INIT, "TACKLE_INIT")

    # -- TACKLE_INIT ----------------------------------------------------

    def _update_tackle_init(self, dt: float) -> None:
        """First contact frame -- defender closes to BC, then enter contact."""
        self._phase_timer += dt
        bc = self.ball_carrier
        d = self.primary
        if bc is None or d is None:
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "TACKLE_INIT_ABORT")
            return

        # Pull defender toward BC
        dx = bc.x - d.x
        dy = bc.y - d.y
        dist = math.hypot(dx, dy)
        if dist > 0.6:
            spd = 3.0
            d.vx = (dx / dist) * spd
            d.vy = (dy / dist) * spd
            d.x += d.vx * dt
            d.y += d.vy * dt

        # Transition after brief engagement window
        if self._phase_timer >= 0.10:
            self._set_phase("TACKLE_CONTACT", GameSubState.TACKLE_CONTACT, "TACKLE_INIT->TACKLE_CONTACT")

    # -- TACKLE_CONTACT -------------------------------------------------

    def _update_tackle_contact(self, dt: float) -> None:
        """Contact phase: update waveform, check for joiners."""
        self._phase_timer += dt
        bc = self.ball_carrier
        d = self.primary
        if bc is None or d is None:
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "TACKLE_CONTACT_ABORT")
            return

        if self.tes_ctx is not None:
            # Refresh context snapshots
            self.tes_ctx.attacker = _make_ctx(bc)
            self.tes_ctx.defender = _make_ctx(d)
            # Update waveform (TES runs here)
            update_waveform(self.tes_ctx, self.rng)
            # Check for additional tacklers joining
            self._maybe_add_joiners()

        if self._phase_timer >= 0.15:
            self._set_phase("TACKLE_DRIVE", GameSubState.TACKLE_DRIVE, "TACKLE_CONTACT->TACKLE_DRIVE")

    def _maybe_add_joiners(self) -> None:
        """Allow additional defenders to join the tackle (multi-tackler).

        STEP-03A-04: Checks ``joiner_gate_open`` before allowing any
        joiner registration.  The gate is opened by tes_start_tackle()
        after TES lock is established, ensuring secondary tacklers
        cannot register before the primary engagement is locked down.
        """
        if self.tes_ctx is None:
            return
        ctx = self.tes_ctx

        # Gate: secondary registration not allowed until TES lock completes
        if not ctx.joiner_gate_open:
            return

        engaged = ctx.engaged_tacklers
        if len(engaged) >= ctx.max_joiners:
            return

        bc = self.ball_carrier
        if bc is None:
            return
        bc_ctx = _make_ctx(bc)
        engaged_ctx = [ctx.engagement.tacklers[i] for i in engaged]

        for d in self.world.defending_team:
            if d is self.primary:
                continue
            if d.player_id in engaged:
                continue
            if d.defender_role == DefenderRole.DUMMY_HALF:
                continue

            cand = _make_ctx(d)
            if can_join(cand, bc_ctx, engaged_ctx):
                ctx.engagement.add(cand, self.world.sim_time_s if hasattr(self.world, "sim_time_s") else 0.0)
                self.joiners.append(d)
                engaged_ctx.append(cand)
                if len(ctx.engaged_tacklers) >= ctx.max_joiners:
                    break

    # -- TACKLE_DRIVE ---------------------------------------------------

    def _update_tackle_drive(self, dt: float) -> None:
        """Drive phase: compute dominance/distance/duration once, then apply."""
        bc = self.ball_carrier
        d = self.primary
        if bc is None or d is None or self.tes_ctx is None or self.tes_outcome is None:
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "TACKLE_DRIVE_ABORT")
            return

        # First frame of drive: compute all metrics
        if self._phase_timer == 0.0:
            compute_dominance(self.tes_ctx, self.tes_outcome, self.rng)
            compute_drive_distance(self.tes_ctx, self.tes_outcome, self.rng)
            compute_tackle_duration(self.tes_ctx, self.tes_outcome, self.rng)
            compute_ptb_time(self.tes_ctx, self.tes_outcome, self.rng)

        self._phase_timer += dt

        # Apply drive movement -- BC and tacklers move together
        dist = self.tes_outcome.drive_distance_m
        dur = max(0.1, self.tes_outcome.tackle_duration_s)
        spd = dist / dur if dur > 0 else 0.0

        bc.vx = spd
        bc.x += bc.vx * dt
        self.world.ruck_x_m = bc.x
        self.world.ruck_y_m = bc.y

        for tackler in [d] + self.joiners:
            tackler.vx = spd
            tackler.x += tackler.vx * dt

        # Transition when drive duration elapses
        if self._phase_timer >= dur:
            self._set_phase("TACKLE_DOMINANCE", GameSubState.TACKLE_DOMINANCE, "TACKLE_DRIVE->TACKLE_DOMINANCE")

    # -- TACKLE_DOMINANCE -----------------------------------------------

    def _update_tackle_dominance(self, dt: float) -> None:
        """Dominance phase: run outcome checks, then release."""
        self._phase_timer += dt

        if self.tes_ctx is not None and self.tes_outcome is not None:
            check_offload(self.tes_ctx, self.tes_outcome, self.rng)
            check_strip(self.tes_ctx, self.tes_outcome, self.rng)
            check_fumble(self.tes_ctx, self.tes_outcome, self.rng)
            check_penalty(self.tes_ctx, self.tes_outcome, self.rng)
            check_six_again(self.tes_ctx, self.tes_outcome, self.rng)
            check_line_break(self.tes_ctx, self.tes_outcome, self.rng)

        if self._phase_timer >= 0.25:
            # =========================================================
            # DOMINANCE -> RELEASE: TES shuts down, DH assigned
            # =========================================================
            self._prepare_release()
            self._set_phase("RELEASE", GameSubState.TACKLE_DOMINANCE, "TACKLE_DOMINANCE->RELEASE")

    def _prepare_release(self) -> None:
        """Set up ruck position and assign dummy half on DOMINANCE->RELEASE."""
        bc = self.ball_carrier
        if bc is None:
            return

        # Record ruck position at BC's location
        self.world.ruck_x_m = bc.x
        self.world.ruck_y_m = bc.y

        # Assign DH from attacking team (closest to ruck who is NOT the BC)
        self.dh = self._find_dh(self.world.attacking_team, bc.x, bc.y, bc)

        if self.dh is not None:
            self.dh.defender_role = DefenderRole.DUMMY_HALF
            self.dh.frozen = False
            self.dh.tackle_locked = False

    @staticmethod
    def _find_dh(team: list, ruck_x: float, ruck_y: float, bc: Player) -> Player | None:
        """Return the closest player to the ruck who is NOT the BC (Dummy Half)."""
        candidates = [p for p in team if p is not bc]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda p: (p.x - ruck_x) ** 2 + (p.y - ruck_y) ** 2,
        )

    # -- RELEASE --------------------------------------------------------

    def _update_release(self, dt: float) -> None:
        """
        Release phase: PTB wind-up timer.

        TES is fully shut down -- no containment, no retreat, no tackle freeze.
        DH is unfrozen with DefenderRole.DUMMY_HALF.
        """
        self._phase_timer += dt

        # Unfreeze BC and tacklers so they can move to PTB positions
        bc = self.ball_carrier
        if bc is not None:
            bc.frozen = False

        for tackler in [self.primary] + self.joiners:
            if tackler is not None:
                tackler.frozen = False
                tackler.is_tackler = False
                tackler.in_contact = False
                tackler.tackle_locked = False

        # Move tacklers back slightly (clear ruck area)
        for tackler in [self.primary] + self.joiners:
            if tackler is not None:
                tackler.x -= 1.0 * dt

        # Wait for PTB time (from tes_outcome) before transitioning
        ptb_t = max(1.0, self.tes_outcome.ptb_time_s if self.tes_outcome else 1.0)
        if self._phase_timer >= ptb_t:
            self._enter_ptb_prep()

    def _enter_ptb_prep(self) -> None:
        """RELEASE -> PTB_PREP: hand control of DH to PTBManager."""
        if self.dh is not None:
            # Ensure ruck pos is correct
            bc = self.ball_carrier
            if bc is not None:
                self.world.ruck_x_m = bc.x
                self.world.ruck_y_m = bc.y

            # First frame: tell PTBManager to start prep
            if self.ptb_manager.state == "idle":
                self.ptb_manager.start_prep(
                    self.dh,
                    self.world.ruck_x_m,
                    self.world.ruck_y_m,
                )

        self._set_phase("PTB_PREP", GameSubState.PTB, "RELEASE->PTB_PREP")

    # -- PTB_PREP -------------------------------------------------------

    def _update_ptb_prep(self, dt: float) -> None:
        """
        PTB_PREP: DH moves to behind-ball position via PTBManager.

        CRITICAL: No dh_controller.move_to_ruck() here.
        No geometry/containment/retreat touches DH.
        PTBManager is the sole authority for DH movement.
        """
        if self.dh is None:
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "PTB_PREP_NO_DH")
            return

        # PTBManager drives DH movement -- NO other system touches DH here
        ready = self.ptb_manager.update_prep(
            dt,
            self.dh,
            self.world.ruck_x_m,
            self.world.ruck_y_m,
        )

        # Ensure DH stays unfrozen
        self.dh.frozen = False

        if ready:
            # PTB_PREP -> PTB_EXEC
            self.ptb_manager.start_exec(
                self.dh,
                self.world.ruck_x_m,
                self.world.ruck_y_m,
            )
            self._set_phase("PTB_EXEC", GameSubState.PTB, "PTB_PREP->PTB_EXEC")

    # -- PTB_EXEC -------------------------------------------------------

    def _update_ptb_exec(self, dt: float) -> None:
        """
        PTB_EXEC: DH moves to scoop position via PTBManager.

        CRITICAL: No dh_controller.move_to_ruck() here.
        PTBManager is the sole authority for DH movement.
        """
        if self.dh is None:
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "PTB_EXEC_NO_DH")
            return

        # PTBManager drives DH movement
        ready = self.ptb_manager.update_exec(
            dt,
            self.dh,
            self.world.ruck_x_m,
            self.world.ruck_y_m,
        )

        # Ensure DH stays unfrozen
        self.dh.frozen = False

        if ready:
            # PTB_EXEC -> PTB: ball transfer
            bc = self.ball_carrier
            self.ptb_manager.complete(self.dh, bc)

            # Update world and internal references
            self.world.ball_carrier = self.dh
            self.ball_carrier = self.dh

            self._set_phase("PTB", GameSubState.PTB, "PTB_EXEC->PTB")

    # -- PTB ------------------------------------------------------------

    def _update_ptb(self, dt: float) -> None:
        """
        PTB: brief pause after ball transfer, then enter POST_RUCK.

        DH has the ball now (bc = dh).  No TES runs here.
        """
        self._phase_timer += dt

        # Keep DH unfrozen
        if self.dh is not None:
            self.dh.frozen = False

        # Brief pause to let the PTB "register"
        if self._phase_timer >= POST_PTB_DELAY_S:
            self._enter_post_ruck()

    def _enter_post_ruck(self) -> None:
        """PTB -> POST_RUCK_OPEN_PLAY: reset PTB subsystems."""
        self.ptb_manager.reset()
        self.dh_controller.clear()

        # Clear DH role -- returns to normal defensive responsibilities
        if self.dh is not None:
            self.dh.defender_role = None

        self._set_phase(
            "POST_RUCK_OPEN_PLAY",
            GameSubState.POST_RUCK_OPEN_PLAY,
            "PTB->POST_RUCK",
        )

    # -- POST_RUCK_OPEN_PLAY --------------------------------------------

    def _update_post_ruck_open_play(self, dt: float) -> None:
        """
        POST_RUCK_OPEN_PLAY: defenders retreat to reset line,
        then transition back to OPEN_PLAY.
        """
        self._phase_timer += dt
        rx = self.world.ruck_x_m

        # Defenders retreat to defensive line position
        for d in self.world.defending_team:
            if d.defender_role == DefenderRole.DUMMY_HALF:
                continue
            tx = _retreat_target_x(rx)
            _move_toward(d, tx, d.y, 6.0, dt)

        # After retreat duration, return to open play
        if self._phase_timer >= RETREAT_DURATION_S:
            self._reset_tackle_state()
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "POST_RUCK->OPEN_PLAY")

    def _reset_tackle_state(self) -> None:
        """Clean up tackle/PTB state for the next cycle."""
        self.primary = None
        self.joiners = []
        self.tes_ctx = None
        self.tes_outcome = None
        self.dh = None
        self.frames_since_ptb = 0
        self._cooldown_timer = POST_PTB_COOLDOWN_S
