import sys
sys.path.insert(0, '.')

from world.world_state import WorldState
from world.open_play_engine import OpenPlayEngine, ANCHOR_STEP_M

ws = WorldState()
ope = OpenPlayEngine(ws)
print('defensive_anchor_x:', ope.defensive_anchor_x)
print('Has _compute_defensive_anchor:', hasattr(ope, '_compute_defensive_anchor'))
print('Constant ANCHOR_STEP_M:', ANCHOR_STEP_M)
print('OK - all verified')
