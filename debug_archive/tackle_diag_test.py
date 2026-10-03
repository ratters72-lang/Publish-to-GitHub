"""Test script to exercise _apply_structured_open_play() and capture TACKLE_DIAG output."""
import sys
import os
import io

# Set UTF-8 for stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Suppress prints we don't need
import builtins
_real_print = builtins.print

def filtered_print(*args, **kwargs):
    msg = ' '.join(str(a) for a in args)
    if 'TACKLE_DIAG' in msg:
        _real_print(*args, **kwargs)

builtins.print = filtered_print

from world.world_state import GameWorldState, GameSubState, GamePhase
from game.defence.defence_lvl_1.defensive_behaviour_unified import DefensiveBehaviourUnified
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

# Create a minimal mock world state
class MockPlayer:
    def __init__(self, pid, x, y):
        self.player_id = pid
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self.facing = 0.0
        self.frozen = False
        self.freeze_timer = 0.0
        self.depth_class = "PASSIVE"
        self.timing_locked = False
        self.tackle_role = None
        self.defender_role = None
        self.lane_index = 0
        self.max_speed_mps = 8.0
        self.move_speed = 0.0
        self.reaction_timer = 0.0
        self.retreat_delay = 0.0
        self.lateral_hesitation = 0.0
        self.step_stagger = 0.0
        self.movement_state = "idle"
        self.target_x = x
        self.target_y = y
        self.target_source = None
        self._prev_target_y_smooth = y
        self.footplanting = False
        self.is_tackler = False
        self.in_contact = False
        self.tes_locked = False
        self.radius = 0.55
        self.engaged = False
        self.possessed = False
    
    def distance_to(self, other):
        import math
        return math.hypot(self.x - other.x, self.y - other.y)
    
    def set_facing(self, angle):
        self.facing = angle
    
    def clear_facing_override(self):
        pass

class MockWorld:
    pass

def run_diag():
    tuners = DefenceTuners()
    tuners.assert_radii()
    
    behaviour = DefensiveBehaviourUnified(tuners)
    
    # Simulate BC at x=50, y=35
    bc = MockPlayer(99, 50.0, 35.0)
    
    # Create 6 defenders spread along the line
    defenders = []
    for i in range(6):
        x = 55.0 + i * 1.0  # 55..60 (5m behind BC)
        y = 20.0 + i * 5.0  # 20..45 spread
        d = MockPlayer(100 + i, x, y)
        defenders.append(d)
    
    # Set up a mock world
    world = MockWorld()
    world.ball_carrier = bc
    world.defending_team = defenders
    world.attacking_team = [bc]
    world.players = defenders + [bc]
    world.phase = GamePhase.OPEN_PLAY
    world.sub_state = GameSubState.OPEN_PLAY
    world.in_pursuit_mode = False
    world.first_tackle_not_made = False
    world.defence_enabled = True
    world.use_structured_open_play = True
    world.current_target_writer = None
    world.in_kickoff = False
    world.defensive_line_anchor = 65.0
    world.ruck_x_m = 50.0
    world.ruck_y_m = 35.0
    world.dt = 1.0/60.0
    world.role_governs = False
    world.tes_locked = False
    world.ball_owner = "BC"
    world.ball = None
    world.frame_count = 0
    world.sim_time_s = 0.0
    world.open_play_engine = None  # Won't be reached if try_pre_tackle fails
    
    # Run the structured open play
    print("[TEST] Calling _apply_structured_open_play()...")
    print(f"[TEST] BC at ({bc.x:.1f}, {bc.y:.1f})")
    for d in defenders:
        import math
        dist = math.hypot(d.x - bc.x, d.y - bc.y)
        print(f"[TEST] Defender #{d.player_id} at ({d.x:.1f}, {d.y:.1f}) dist_to_bc={dist:.2f}")
    
    # Restore real print for the method under test
    builtins.print = _real_print
    
    behaviour._apply_structured_open_play(world, defenders)
    
    print("\n[TEST] DONE")

if __name__ == "__main__":
    run_diag()
