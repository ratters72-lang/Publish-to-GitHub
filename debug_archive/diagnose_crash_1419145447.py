"""Direct TES diagnostic to capture exact crash exception."""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from game.core.state_machine import StateMachine, Transition
from game.tes.tes_state import TESPhase, TESEvent, get_tes_transitions
from game.tes.tes_context import TESContext, PlayerContext, TackleEngagement
from game.tes.tes import TES, DRIVE_PHASE_STR

tes = TES()
print("States in FSM:", sorted(tes._fsm._states))
print("Transitions:")
for k, v in tes._fsm._transitions.items():
    print(f"  ({k[0]}, {k[1]}) -> {v.target}")
print()

# Build realistic context — attacker and defender within CONTACT_RADIUS (0.8m)
# so _update_engage triggers contact immediately
bc = PlayerContext(player_id=1, x_m=50, y_m=35, vx=2.0, vy=0.0)
df = PlayerContext(player_id=2, x_m=50.5, y_m=35, vx=3.0, vy=0.0)  # 0.5m apart
eng = TackleEngagement()
ctx = TESContext(attacker=bc, defender=df, engagement=eng)

try:
    tes.initiate_tackle(ctx)
    print("After init — state:", tes._fsm.state, "phase:", tes.phase)
    
    for i in range(500):
        try:
            tes.update(0.016)
        except Exception as e:
            print(f"\n!!! CRASH at frame {i} !!!")
            print(f"Exception: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        
        state = tes._fsm.state
        if state == "engage":
            if i < 5 or i % 20 == 0:
                dist = math.hypot(bc.x_m - df.x_m, bc.y_m - df.y_m)
                print(f"  [{i}] ENGAGE dist={dist:.3f}")
        elif state == "contact":
            print(f"  [{i}] CONTACT!  physics_timer={tes._physics_timer:.3f}")
        elif state == DRIVE_PHASE_STR:
            print(f"  [{i}] DRIVE!    drive_timer={tes._drive_timer:.3f}")
        elif state == "dominance":
            print(f"  [{i}] DOMINANCE physics_timer={tes._physics_timer:.3f}")
        elif state == "dominance_settling":
            print(f"  [{i}] DOMINANCE_SETTLING")
        elif state == "ground":
            print(f"  [{i}] GROUND")
        elif state == "tackle_complete":
            print(f"  [{i}] TACKLE_COMPLETE!")
            break
    
    print("\nFinal state:", tes._fsm.state)
    print("Final phase:", tes.phase)
    print("Outcome:", tes.outcome)

except Exception as e:
    print(f"\n!!! EXCEPTION !!!")
    print(f"Exception: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
