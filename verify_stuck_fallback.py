"""
verify_stuck_fallback.py — TASK IV PATCH 1 verifier (2026-08-14).

Unit-test ``resolve_pre_tackle_stuck`` from
game/defence/defence_lvl_1/movement_mode_decision.py.

The fallback is the PRE-TACKLE STUCK_NOT_FROZEN fix: any defender in
OPEN_PLAY with a live target it is NOT parked on, zero TOTAL velocity,
and a resolved HOLD/DRIFT state is assigned RETREAT (PASSIVE) or SLIDE
(engaged) so the state machine always produces motion.

Cases verified:
  A. PASSIVE + HOLD + target ahead + zero velocity  -> RETREAT
  B. PRIMARY + DRIFT + target ahead + zero velocity -> SLIDE
  C. Parked (target_x == x)                         -> unchanged (HOLD)
  D. Pure-lateral mover (vx=0, vy!=0)               -> unchanged (not stuck)
  E. Frozen defender                                -> unchanged
  F. Fullback actor                                 -> unchanged
  G. Marker_A / DH / KICK_CHASE / DEF_CHASE         -> unchanged
  H. Committed tackler (_tackle_commit_active)      -> unchanged
  I. CHASE / RETREAT / SLIDE resolved state         -> unchanged
  J. world.tes_active = True                        -> unchanged
  K. defender_role is None                          -> unchanged
  L. Non-PASSIVE depth -> SLIDE fallback is legal for a PRIMARY
"""

import sys

from player.player import Player, DefenderRole
from player.movement_states import MovementState
from game.defence.defence_lvl_1.movement_mode_decision import (
    resolve_pre_tackle_stuck,
)


class _World:
    """Minimal world stub for the fallback (reads tes_active only)."""
    def __init__(self, tes_active: bool = False):
        self.tes_active = tes_active
        self.frame_count = 0


def _player(
    pid: int,
    x: float,
    target_x: float,
    depth_class: str = "PASSIVE",
    vx: float = 0.0,
    vy: float = 0.0,
    role=DefenderRole.DEF_LINE_READY,
    frozen: bool = False,
    fullback: bool = False,
    committed: bool = False,
    movement_state=MovementState.HOLD,
):
    p = Player(x, 35.0, pid)
    p.target_x = target_x
    p.target_y = 35.0
    p.depth_class = depth_class
    p.vx = vx
    p.vy = vy
    p.defender_role = role
    p.frozen = frozen
    p.fullback_role_active = fullback
    p._tackle_commit_active = committed
    p.movement_state = movement_state
    return p


FAILURES: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        FAILURES.append(f"FAIL {name}: {detail}")


def state_name(state) -> str:
    return getattr(state, "name", None) or str(state)


def main() -> int:
    world = _World(tes_active=False)

    # A. PASSIVE + HOLD -> RETREAT
    p = _player(1, x=50.0, target_x=60.0, depth_class="PASSIVE")
    out = resolve_pre_tackle_stuck(world, p, MovementState.HOLD)
    check("A PASSIVE->RETREAT", out is MovementState.RETREAT,
          f"got {state_name(out)}")

    # B. PRIMARY + DRIFT -> SLIDE
    p = _player(2, x=50.0, target_x=55.0, depth_class="PRIMARY")
    out = resolve_pre_tackle_stuck(world, p, MovementState.DRIFT)
    check("B PRIMARY->SLIDE", out is MovementState.SLIDE,
          f"got {state_name(out)}")

    # C. Parked at target -> unchanged
    p = _player(3, x=60.0, target_x=60.0, depth_class="PASSIVE")
    out = resolve_pre_tackle_stuck(world, p, MovementState.HOLD)
    check("C parked unchanged", out is MovementState.HOLD,
          f"got {state_name(out)}")

    # D. Pure-lateral mover (vy != 0) -> not stuck
    p = _player(4, x=50.0, target_x=60.0, depth_class="PASSIVE", vy=0.8)
    out = resolve_pre_tackle_stuck(world, p, MovementState.HOLD)
    check("D lateral mover unchanged", out is MovementState.HOLD,
          f"got {state_name(out)}")

    # E. Frozen -> unchanged (HOLD stays)
    p = _player(5, x=50.0, target_x=60.0, depth_class="PASSIVE", frozen=True)
    out = resolve_pre_tackle_stuck(world, p, MovementState.HOLD)
    check("E frozen unchanged", out is MovementState.HOLD,
          f"got {state_name(out)}")

    # F. Fullback actor -> unchanged (DRIFT stays)
    p = _player(6, x=50.0, target_x=60.0, depth_class="PASSIVE",
                fullback=True)
    out = resolve_pre_tackle_stuck(world, p, MovementState.DRIFT)
    check("F fullback unchanged", out is MovementState.DRIFT,
          f"got {state_name(out)}")

    # G. Exempt roles unchanged
    for role in (
        DefenderRole.MARKER_A,
        DefenderRole.MARKER_B,
        DefenderRole.DUMMY_HALF,
        DefenderRole.KICK_CHASE,
        DefenderRole.DEF_CHASE,
    ):
        p = _player(7, x=50.0, target_x=60.0, depth_class="PASSIVE", role=role)
        out = resolve_pre_tackle_stuck(world, p, MovementState.HOLD)
        check(f"G role {role.name} unchanged", out is MovementState.HOLD,
              f"got {state_name(out)} for {role.name}")

    # H. Committed tackler -> unchanged
    p = _player(8, x=50.0, target_x=60.0, depth_class="PRIMARY",
                committed=True)
    out = resolve_pre_tackle_stuck(world, p, MovementState.DRIFT)
    check("H committed unchanged", out is MovementState.DRIFT,
          f"got {state_name(out)}")

    # I. Non-HOLD/DRIFT resolved states unchanged
    for state in (MovementState.CHASE, MovementState.RETREAT,
                  MovementState.SLIDE, MovementState.SHOOT):
        p = _player(9, x=50.0, target_x=60.0, depth_class="PASSIVE")
        out = resolve_pre_tackle_stuck(world, p, state)
        check(f"I {state_name(state)} unchanged", out is state,
              f"got {state_name(out)} for {state_name(state)}")

    # J. TES active -> unchanged
    tes_world = _World(tes_active=True)
    p = _player(10, x=50.0, target_x=60.0, depth_class="PASSIVE")
    out = resolve_pre_tackle_stuck(tes_world, p, MovementState.HOLD)
    check("J TES unchanged", out is MovementState.HOLD,
          f"got {state_name(out)}")

    # K. No defender_role -> unchanged
    p = _player(11, x=50.0, target_x=60.0, depth_class="PASSIVE", role=None)
    out = resolve_pre_tackle_stuck(world, p, MovementState.HOLD)
    check("K no role unchanged", out is MovementState.HOLD,
          f"got {state_name(out)}")

    # L. SECONDARY + HOLD -> SLIDE fallback is legal
    p = _player(12, x=50.0, target_x=55.0, depth_class="SECONDARY")
    out = resolve_pre_tackle_stuck(world, p, MovementState.HOLD)
    check("L SECONDARY->SLIDE", out is MovementState.SLIDE,
          f"got {state_name(out)}")

    if FAILURES:
        print(f"\nSTUCK FALLBACK VERIFIER: {len(FAILURES)} FAILURES")
        for f in FAILURES:
            print(f"  {f}")
        return 1

    print("\nSTUCK FALLBACK VERIFIER: ALL 12 CASE GROUPS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
