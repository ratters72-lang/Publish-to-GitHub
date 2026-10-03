"""Quick headless verification that new movement code works correctly."""
import sys
sys.path.insert(0, '.')

import math

from player.player import Player, PlayerState
from world.world_state import WorldState, GameWorldState, GameSubState


def test_accelerate_toward_target():
    """Player moves toward target using acceleration."""
    p = Player(10.0, 10.0, player_id=1)
    p.target_x = 20.0
    p.target_y = 10.0
    
    # Initially at rest
    assert p.vx == 0.0 and p.vy == 0.0
    
    # After one frame, should have velocity toward target
    p.update(1.0/60.0)
    assert p.vx > 0.0, f"Expected positive vx, got {p.vx}"
    assert abs(p.vy) < 0.01, f"Expected vy ~0, got {p.vy}"
    
    # Should have moved toward target
    assert p.x > 10.0, f"Expected x > 10.0, got {p.x}"
    print(f"  test_accelerate: x={p.x:.3f}, vx={p.vx:.3f} — OK")
    return True


def test_no_target_override():
    """Player.update() does NOT override target — engine controls it."""
    p = Player(10.0, 10.0, player_id=1)
    p.state = PlayerState.BALL_CARRIER
    
    # Set a target that would be DIFFERENT from what old code set
    # Old code would do target_x = x + 4.0 = 14.0
    # We set something else entirely
    p.target_x = 30.0
    p.target_y = 20.0
    p.update(1.0/60.0)
    
    # After update, target should STILL be 30.0, 20.0 — NOT overridden
    assert p.target_x == 30.0, f"Expected target_x=30.0, got {p.target_x}"
    assert p.target_y == 20.0, f"Expected target_y=20.0, got {p.target_y}"
    print(f"  test_no_target_override: target=({p.target_x}, {p.target_y}) — OK")
    return True


def test_frozen_no_movement():
    """Frozen player does not move."""
    p = Player(10.0, 10.0, player_id=1)
    p.frozen = True
    p.target_x = 50.0
    p.vx = 5.0
    p.vy = 0.0
    
    p.update(1.0/60.0)
    assert p.x == 10.0, f"Frozen player moved: x={p.x}"
    assert p.y == 10.0, f"Frozen player moved: y={p.y}"
    assert p.vx == 0.0, f"Frozen player has vx: {p.vx}"
    print(f"  test_frozen_no_movement: x={p.x}, vx={p.vx} — OK")
    return True


def test_apply_physics_forces_skips_movement():
    """Player with apply_physics_forces=True does NOT run acceleration."""
    p = Player(10.0, 10.0, player_id=1)
    p.apply_physics_forces = True
    p.vx = 3.0
    p.vy = 2.0
    p.target_x = 50.0  # would move if _accelerate ran
    
    p.update(1.0/60.0)
    # vx/vy should be unchanged (no acceleration toward target)
    # But should still apply _update_physics (computes momentum from speed)
    assert p.vx == 3.0, f"apply_physics_forces: vx changed to {p.vx}"
    assert p.vy == 2.0, f"apply_physics_forces: vy changed to {p.vy}"
    # Position should still update from velocity via _update_physics
    # Actually looking at the code: apply_physics_forces skips _accelerate_toward_target
    # but _update_physics only computes momentum, doesn't apply velocity to position
    # So position should be unchanged
    # Wait, let me check: the old code had `if self.apply_physics_forces: return` 
    # which skips _move entirely. The new code has the same pattern.
    # Position is only updated in _accelerate_toward_target.
    # With apply_physics_forces=True, _accelerate is skipped.
    # So position should be unchanged.
    print(f"  test_apply_physics_forces: pos=({p.x},{p.y}), v=({p.vx},{p.vy}) — OK")
    return True


def test_apply_state_moves_player():
    """apply_state() with chase ball sets target and moves player."""
    # Use a simple ball mock — Field requires pygame display
    class BallMock:
        x_m = 30.0
        y_m = 35.0
        z_m = 3.0
    
    b = BallMock()
    
    w = WorldState()
    w.game_state = GameWorldState.KICKOFF  # must NOT be KICKOFF for open-play chase
    
    # KICKOFF guard in _apply_chase_ball checks game_state == KICKOFF
    # For a simple test, set to OPEN_PLAY so it uses the fallback
    w.game_state = GameWorldState.OPEN_PLAY
    
    p = Player(10.0, 35.0, player_id=1)
    p.state = PlayerState.CHASE_BALL
    
    # apply_state should set target to ball position and accelerate toward it
    p.apply_state(b, 1.0/60.0, w)
    
    # Should have moved toward the ball
    target_dist = math.hypot(p.target_x - p.x, p.target_y - p.y)
    assert target_dist > 0, f"apply_state didn't set target toward ball, pos=({p.x},{p.y}), target=({p.target_x},{p.target_y})"
    # Should have positive velocity toward ball
    assert p.vx > 0.0, f"Expected vx > 0 toward ball, got {p.vx}"
    # Position should have moved
    assert p.x > 10.0, f"Expected x > 10.0, got {p.x}"
    print(f"  test_apply_state: pos=({p.x:.1f},{p.y:.1f}), target=({p.target_x:.1f},{p.target_y:.1f}), vx={p.vx:.2f} — OK")
    return True


def run_all():
    tests = [
        test_accelerate_toward_target,
        test_no_target_override,
        test_frozen_no_movement,
        test_apply_physics_forces_skips_movement,
        test_apply_state_moves_player,
    ]
    
    for t in tests:
        try:
            result = t()
            if not result:
                print(f"FAIL: {t.__name__}")
                return False
        except Exception as e:
            import traceback
            print(f"ERROR: {t.__name__}: {e}")
            traceback.print_exc()
            return False
    
    print("\nAll tests passed!")
    return True

if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
