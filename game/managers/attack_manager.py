"""attack_manager.py -- AttackManager: sole authority for attacking orders.

One macro brain for the attacking side. Owns:

  1. Ball-carrier steering order  -- delegated to AttackController
     (geometry -> run vector -> target projection), applied at the
     exact frame position the inline writer previously occupied.
  2. Support-runner orders        -- TASK XII: the legacy naive BC-chase is
     now OPTIONALLY replaced by authored lanes via
     attack_structure_authority (lane law / depth / stagger / sweep/support
     identity / settle), gated by AttackStructureTuners.structure_enabled.
     Gate OFF keeps the legacy chase byte-stable.

Authority boundary:
  The manager issues movement/role orders ONLY. It never owns TES
  attach geometry, drive LERP, dominance pushback, ball-carrier/tackler
  movement during a tackle, PTBManager DH movement, phase transitions,
  freeze flags, or collision physics.

Zero-behaviour-change note (Stage 2):
  update() is a verbatim relocation of OpenPlayEngine._update_attack().
  Behaviour is identical; ownership and audit attribution change.
  TASK XII: with structure_enabled=False the support block is byte-identical
  to the pre-task legacy loop; with structure_enabled=True the authored
  lane authority issues the orders instead.
"""

import math

from world.world_state import GameSubState, is_tackle_phase
from game.attack.attack_lvl_1.attack_tuners import AttackStructureTuners
from game.attack.attack_lvl_1.attack_structure_authority import (
    apply_support_orders,
)


def _trace_enabled() -> bool:
    """Lazy read of the engine trace flag (avoids import cycle)."""
    try:
        from world.open_play_engine import DEBUG_OPE_TRACE
        return DEBUG_OPE_TRACE
    except Exception:
        return False


class AttackManager:
    """Sole attacking-order authority (one loop per frame)."""

    def __init__(self):
        # TASK XII: structured-lane tuners (dark-safe default OFF).
        self.structure_tuners = AttackStructureTuners()

    # ------------------------------------------------------------------
    # MAIN ENTRY -- called once per OPEN_PLAY / POST_RUCK_OPEN_PLAY frame
    # ------------------------------------------------------------------

    def update(self, ope, dt: float) -> None:
        """Give every attacker movement orders each open-play frame.

        Verbatim relocation of OpenPlayEngine._update_attack():

          - Overrides any stale TES pinning on attackers.
          - BC steering via ope.attack_controller (geometry-driven).
          - BC physics step resolves target_x/target_y into movement.
          - Support runners get structured lane orders (Task XII) when the
            gate is on, else the legacy full X+Y chase vectors toward the
            BC so they converge with defenders' tackle corridors.

        Args:
            ope: the OpenPlayEngine instance (phase FSM + delegates).
            dt:  frame delta seconds.
        """
        # HARD GATE: attack runs ONLY during OPEN_PLAY / POST_RUCK_OPEN_PLAY.
        # This catches all call paths including hidden/implicit call sites
        # that bypass the gate in OPE.update().
        if ope._phase not in ("OPEN_PLAY", "POST_RUCK_OPEN_PLAY"):
            return
        # DEFENCE DISPATCH CONTRACT -- no attack during TES tackle.
        if is_tackle_phase(ope.world):
            return

        bc = ope.world.ball_carrier
        if bc is None:
            # No BC registered yet (kickoff/early frames): pin movement
            # targets to current positions so no attacker is flagged as
            # STUCK_NOT_FROZEN with stale/None/invalid targets.
            for p in ope.world.attacking_team:
                tx = getattr(p, "target_x", None)
                ty = getattr(p, "target_y", None)
                invalid = (
                    tx is None
                    or ty is None
                    or not math.isfinite(float(tx))
                    or not math.isfinite(float(ty))
                )
                if invalid:
                    p.target_x = p.x
                    p.target_y = p.y
                    p.vx = 0.0
                    p.vy = 0.0
            return

        # ---- BC steering via AttackController ----
        ope.attack_controller.apply_vector(bc, ope.world, dt)

        # Record BC steering provenance (Task XII audit; no behaviour).
        try:
            from game.debug.attack_structure_audit import record_bc_decision
            bc_source = getattr(bc, "_vector_source", "geometry")
            record_bc_decision(ope.world, bc_source, bc_source == "escape")
        except Exception:
            pass

        # ---- CRITICAL: BC physics step resolves target_x/target_y into movement ----
        bc.update(dt)

        # ── STAGE 2: ANTICIPATION RECONNECT (dark-safe) ──
        # When AttackStructureTuners.anticipation_enabled is on, run the team
        # intent + support-runner intent each OPEN_PLAY frame and stamp the
        # state onto world/players.  The BC intent is also stamped (the
        # controller already did it, but re-stamp so the manager is the single
        # frame authority).  Outputs feed the Stage 7 decision engine; the
        # target writes themselves are unchanged (geometry stays stable).
        _anticip_on = bool(
            getattr(self.structure_tuners, "anticipation_enabled", False)
        )
        if _anticip_on:
            _world = ope.world
            _per = None
            if getattr(ope, "attack_perception", None) is not None:
                _per = ope.attack_perception.compute(bc)
                bc._perception = _per
            if getattr(ope, "ball_carrier_intent", None) is not None and _per is not None:
                bc._anticipation_intent = ope.ball_carrier_intent.decide(
                    bc, _per, tuners=self.structure_tuners
                )
            # Team intent — lazily wire the producer if OPE lacks it.
            if getattr(ope, "attack_intent", None) is None:
                from game.attack.attack_lvl_2.attack_intent import AttackIntent as _AI
                ope.attack_intent = _AI()
            _world._attack_intent = ope.attack_intent.decide(_world)
            # Support-runner intent.
            if getattr(ope, "support_runner_intent", None) is None:
                from game.attack.attack_lvl_2.support_runner_intent import SupportRunnerIntent as _SI
                ope.support_runner_intent = _SI()
            _per_empty = _per if _per is not None else {}
            for _p in _world.attacking_team:
                if _p is bc:
                    continue
                _p._anticipation_intent = ope.support_runner_intent.decide(
                    _p, bc, _per_empty
                )

        # ---- Support runners ----
        structured = False
        try:
            structured = apply_support_orders(ope, dt, self.structure_tuners)
        except Exception:
            structured = False

        if structured:
            # Authored lanes: targets/velocity already set.  Integrate the
            # same physics step the legacy loop used, for every non-BC.
            for p in ope.world.attacking_team:
                if p is bc:
                    continue
                p.update(dt)
                if _trace_enabled():
                    print(f"ATTACK UPDATE id={p.player_id} x={p.x:.2f} "
                          f"y={p.y:.2f} vx={p.vx:.2f} vy={p.vy:.2f} "
                          f"role={getattr(p, 'attack_role', None)}")
        else:
            # LEGACY (byte-stable when gate is OFF): support runners move
            # toward the BC on both axes.
            for p in ope.world.attacking_team:
                if p is bc:
                    continue
                # Support runners: move toward BC on both axes
                dx = bc.x - p.x
                dy = bc.y - p.y
                dist = math.hypot(dx, dy)
                if dist > 0.01:
                    speed = 4.0
                    p.vx = (dx / dist) * speed
                    p.vy = (dy / dist) * speed
                    p.target_x = p.x + (dx / dist) * 5.0
                    p.target_y = p.y + (dy / dist) * 5.0
                else:
                    p.vx = 0.0
                    p.vy = 0.0
                    p.target_x = p.x
                    p.target_y = p.y
                p.frozen = False
                p.freeze_timer = 0.0
                # Integrate movement through the existing physics step so the
                # attacker's position advances toward its target. Without this,
                # support runners were assigned targets + velocity but never moved.
                p.update(dt)
                if _trace_enabled():
                    print(f"ATTACK UPDATE id={p.player_id} x={p.x:.2f} "
                          f"y={p.y:.2f} vx={p.vx:.2f} vy={p.vy:.2f}")
