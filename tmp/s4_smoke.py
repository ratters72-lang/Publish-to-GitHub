import sys, os
sys.path.insert(0, ".")
import player.movement_states as ms
import game.defence.defence_lvl_1.fullback_controller as fbc
import game.defence.defence_lvl_1.fullback_locomotion as fbl

print("imports OK")
print("lever", fbc.FB_LOCOMOTION_CONTROLLER_ENV_VAR, fbc.FB_LOCOMOTION_CONTROLLER)
print("profiles", sorted(i.name for i in fbl.FB_LOCOMOTION_PROFILES))
for intent in (fbc.FullbackIntent.CHASE, fbc.FullbackIntent.RECOVER):
    print(intent.name, fbl.profile_for_intent(intent),
          "shared_eq", fbl.locomotion_profile_is_shared_equivalent(intent))
for intent in (fbc.FullbackIntent.SWEEP, fbc.FullbackIntent.FLIGHT,
               fbc.FullbackIntent.UNOWNED):
    print(intent.name, fbl.profile_for_intent(intent))

class W:
    def __init__(self, latch=False, recover=False):
        self.frame_count = 1
        self.ball_carrier = None
        self.fullback_recover_allowed = recover
        self._l = latch
class B:
    def __init__(self, w):
        self.world = w
        self.x = 60.0; self.y = 35.0
        self.target_x = 60.0; self.target_y = 35.0
        self.fullback_role_active = True
        self.movement_state = ms.MovementState.CHASE
        self.max_speed_mps = 9.5
        self.max_defensive_speed_mps = 8.3
        self.vx = 0.0; self.vy = 0.0

w = W()
b = B(w)
b.__dict__["_fb_break_latched"] = True
print("owned(broken)", fbc.locomotion_contract_owned(w, b),
      "intent", fbc.locomotion_intent(w, b).name)
print("profile", fbc.fullback_locomotion_profile(w, b) is not None)
print("run", ms.apply_fullback_locomotion(b, 1.0/60.0), b.vx, b.vy)

b2 = B(W())
print("owned(clean)", fbc.locomotion_contract_owned(b2.world, b2),
      "profile", fbc.fullback_locomotion_profile(b2.world, b2))
b2.movement_state = ms.MovementState.DRIFT
print("run(clean)", ms.apply_fullback_locomotion(b2, 1.0/60.0), b2.vx, b2.vy)
