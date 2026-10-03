"""
verify_anticipation_connected.py - Verify Stage 2 anticipation reconnection.

Stage 2 (2026-09-04): "reconnect anticipation" — AttackPerception /
BallCarrierIntent / SupportRunnerIntent / AttackIntent were instantiated
on OpenPlayEngine but never called.  They are now invoked each OPEN_PLAY
frame when `AttackStructureTuners.anticipation_enabled` is True (dark-safe
OFF by default).

Asserts:
  1. With anticipation_enabled=True, the BC carries `_perception` (a dict
     from AttackPerception.compute) and `_anticipation_intent`
     (RUN/PASS/KICK/SCOOT).
  2. world._attack_intent is set (SET_DEFENCE/ESCAPE/MOMENTUM/ATTACK).
  3. Every non-BC attacker carries `_anticipation_intent`
     (FLAT/SWEEP/SUPPORT/RUN).
  4. With anticipation_enabled=False (default), NONE of those stamps are
     written — the geometry path stays byte-stable (no dead-object calls).

Exit 0 = PASS, exit 1 = FAIL.
"""

import sys
import types

from player.player import Player
from world.world_state import GameSubState, GamePhase
from game.attack.attack_lvl_1.attack_tuners import AttackStructureTuners
from game.attack.attack_lvl_1.attack_geometry import AttackGeometry
from game.attack.attack_lvl_1.attack_perception import AttackPerception
from game.attack.attack_lvl_3.run_logic import RunLogic
from game.attack.attack_lvl_4.attack_controller import AttackController
from game.attack.attack_lvl_2.ball_carrier_intent import BallCarrierIntent
from game.attack.attack_lvl_2.attack_intent import AttackIntent
from game.attack.attack_lvl_2.support_runner_intent import SupportRunnerIntent
from game.managers.attack_manager import AttackManager

failures = []


def make_player(x, y, pid, world):
    p = Player(x, y, pid)
    p._world_ref = world
    p.vx = 1.0
    p.vy = 0.0
    p.max_speed_mps = 9.2
    p.move_speed = 9.2
    p.frozen = False
    p.freeze_timer = 0.0
    p.target_x = x
    p.target_y = y
    p.attack_role = "none"
    return p


def build_harness(anticipation_enabled):
    """Build a minimal OPE-like harness wired like the live engine."""
    # World + team
    world = types.SimpleNamespace(
        ball_carrier=None,
        attacking_team=[],
        defending_team=[],
        sub_state=GameSubState.OPEN_PLAY,
        phase=GamePhase.OPEN_PLAY,
        frame_count=0,
        ruck_x_m=60.0,
        ruck_y_m=35.0,
        seconds_since_ptb=999.0,
        frames_since_ptb=999,
        escape_window_frames=20,
        escape_angle_deg=45,
        tes_active=False,
        _settle_window_s=1.0,
    )
    bc = make_player(60.0, 35.0, 1, world)
    sr1 = make_player(55.0, 30.0, 2, world)
    sr2 = make_player(55.0, 40.0, 3, world)
    d1 = make_player(65.0, 32.0, 11, world)
    d2 = make_player(66.0, 38.0, 12, world)
    world.ball_carrier = bc
    world.attacking_team = [bc, sr1, sr2]
    world.defending_team = [d1, d2]

    # OPE-like namespace
    ope = types.SimpleNamespace(
        _phase="OPEN_PLAY",
        world=world,
        dh=None,
        tes_ctx=None,
    )

    # Anticipation tuners (master gate; steering OFF so the verifier stays
    # focused on the anticipation path, not the BC steering stabiliser).
    tuners = AttackStructureTuners()
    tuners.anticipation_enabled = anticipation_enabled
    tuners.steering_enabled = False

    # Wire the anticipation subsystem exactly as OpenPlayEngine.__init__ does.
    ope.attack_geometry = AttackGeometry(ope)
    ope.attack_perception = AttackPerception(ope.attack_geometry)
    ope.ball_carrier_intent = BallCarrierIntent()
    ope.run_logic = RunLogic()
    ope.attack_controller = AttackController(
        ope.attack_geometry, ope.attack_perception, ope.run_logic,
        steer_tuners=tuners,
    )
    ope.attack_intent = AttackIntent()
    ope.support_runner_intent = SupportRunnerIntent()

    # AttackManager shares the same tuners so anticipation_enabled is seen.
    manager = AttackManager()
    manager.structure_tuners = tuners

    return manager, ope, world, bc, sr1, sr2


def test_anticipation_on():
    manager, ope, world, bc, sr1, sr2 = build_harness(True)
    manager.update(ope, dt=0.016)

    # 1. BC perception + intent.
    if not hasattr(bc, "_perception") or bc._perception is None:
        failures.append("anticipation ON: bc._perception is missing (AttackPerception not invoked)")
    else:
        if "nearest_defender" not in bc._perception:
            failures.append("anticipation ON: bc._perception lacks 'nearest_defender'")
    intent = getattr(bc, "_anticipation_intent", None)
    if intent not in ("RUN", "PASS", "KICK", "SCOOT"):
        failures.append(f"anticipation ON: bc._anticipation_intent={intent!r} (expected RUN/PASS/KICK/SCOOT)")

    # 2. Team intent.
    team_intent = getattr(world, "_attack_intent", None)
    if team_intent not in ("SET_DEFENCE", "ESCAPE", "MOMENTUM", "ATTACK"):
        failures.append(f"anticipation ON: world._attack_intent={team_intent!r} (expected a team posture)")

    # 3. Support-runner intent.
    for sr in (sr1, sr2):
        sr_intent = getattr(sr, "_anticipation_intent", None)
        if sr_intent not in ("FLAT", "SWEEP", "SUPPORT", "RUN"):
            failures.append(f"anticipation ON: sr#{sr.player_id}._anticipation_intent={sr_intent!r}")


def test_anticipation_off_byte_stable():
    manager, ope, world, bc, sr1, sr2 = build_harness(False)
    manager.update(ope, dt=0.016)

    # Default (off) must NOT stamp any anticipation state — geometry path
    # stays byte-stable (the perception/intent objects are never invoked).
    if hasattr(bc, "_perception") or hasattr(bc, "_anticipation_intent"):
        failures.append(
            "anticipation OFF: BC was stamped with anticipation state "
            "(default must be byte-stable)"
        )
    if hasattr(world, "_attack_intent"):
        failures.append(
            "anticipation OFF: world._attack_intent was written (default must be byte-stable)"
        )
    if hasattr(sr1, "_anticipation_intent") or hasattr(sr2, "_anticipation_intent"):
        failures.append(
            "anticipation OFF: support runner was stamped with anticipation "
            "state (default must be byte-stable)"
        )


def main():
    test_anticipation_on()
    test_anticipation_off_byte_stable()

    if failures:
        print("VERIFY_ANTICIPATION_CONNECTED: FAIL")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("VERIFY_ANTICIPATION_CONNECTED: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
