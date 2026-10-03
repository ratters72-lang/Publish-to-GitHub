"""End-to-end LIVE-path probe for the radial+facing tackle engage guard.

Exercises the REAL chain the engine uses every frame:

    select_primary_candidate(world, defenders, bc)
        -> try_pre_tackle(world, primary, bc, tuners)
        -> run_machine -> should_engage

with a DEF_LINE_READY body that carries ``tackle_role = None``, sits
RADIAL-IN of the carrier, FACES it, and is SLOT-OUT by the 0.3 m lane
threshold -- the exact configuration that produced "no tackles happen ever".
"""

import math
import sys

sys.path.insert(0, ".")

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from types import SimpleNamespace

from player.player import DefenderRole
from player.movement_states import MovementState
from game.defence.defence_lvl_1.pretackle_classifier import (
    select_primary_candidate, is_engage_role,
)
from game.defence.defence_lvl_1.slot_arbitration import is_slot_tackle_eligible
from game.defence.defence_lvl_1.tackle_engage_guard import (
    is_tackle_engage_guard, TACKLE_ENGAGE_GUARD_SOURCE,
)
from game.defence.defence_lvl_1.defensive_behaviour_lvl1 import try_pre_tackle

out = []
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        out.append("  [PASS] %s" % name)
    else:
        FAIL += 1
        out.append("  [FAIL] %s  %s" % (name, detail))


class Body:
    def __init__(self, pid, x, y, role=DefenderRole.DEF_LINE_READY,
                 state=MovementState.CHASE, facing=0.0, vx=0.0, vy=0.0,
                 tackle_role=None):
        self.player_id = pid
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.facing = facing
        self.frozen = False
        self.freeze_timer = 0.0
        self.fullback_role_active = False
        self.tackle_state_active = False
        self.defender_role = role
        self.movement_state = state
        self.tackle_role = tackle_role
        self.target_source = None
        self.target_x = x
        self.target_y = y
        self.mass = 90.0
        self.tackle_skill = 0.8
        self._mmdl_moved = False


class T:
    def __init__(self):
        self.tackle_radius_m = 1.5
        self.tackle_engage_facing_max_deg = 60.0
        self.pretackle_machine_enabled = True
        self.machine_engage_distance_m = 2.0
        self.machine_head_on_max_deg = 30.0
        self.machine_side_on_max_deg = 60.0
        self.machine_max_approach_angle_deg = 120.0
        self.machine_planted_velocity_eps = 0.01
        self.machine_wrong_foot_facing_delta = 0.3
        self.machine_wrong_foot_base_chance = 0.4
        self.machine_wrong_foot_skill_scale = 0.15
        self.machine_base_random_fail = 0.05
        self.machine_bounce_back_push_m = 0.25
        self.machine_fall_over_stun_s = 0.5
        self.machine_stunned_upright_stun_s = 0.2
        self.machine_wrong_foot_recoil_m = 0.15
        self.retreat_compliance_distance_m = 10.0


tuners = T()
world = SimpleNamespace(
    ball_owner="BC", tes_active=False, open_play_engine=None,
    tackle_slot_threshold_m=0.3, frame_count=11, ruck_x_m=40.0,
    defence_tuners=tuners,
)

FACE_AT_BC = math.atan2(36.0 - 35.0, 60.0 - 61.0)   # atan2(1, -1)

out.append("[L1] the reported failure: tackle_role=None, radial-in, facing, SLOT-OUT")
bc = Body(99, 60.0, 36.0)
guard_body = Body(1, 61.0, 35.0, facing=FACE_AT_BC, tackle_role=None)
check("body carries tackle_role None (DEF_LINE_READY)", guard_body.tackle_role is None)
check("is_engage_role(body) is False (the old exclusion)", is_engage_role(guard_body) is False)
check("slot-OUT by the 0.3m lane threshold",
      is_slot_tackle_eligible(bc, guard_body, world) is False)
check("guard predicate True (radial-in + facing)",
      is_tackle_engage_guard(guard_body, bc, world, tuners) is True)
check("dist < tackle_radius (radial-in)",
      math.hypot(guard_body.x - bc.x, guard_body.y - bc.y) < 1.5)

out.append("\n[L2] select_primary_candidate admits the guarded body")
primary = select_primary_candidate(world, [guard_body], bc)
check("primary is the guarded body (not None)", primary is guard_body,
      "got %r" % (primary,))

out.append("\n[L3] try_pre_tackle FIRES (the tackle pipeline must fire)")
fired = try_pre_tackle(world, primary, bc, tuners)
check("try_pre_tackle returns True", fired is True)
check("TES engage role stamped on the body",
      guard_body.tackle_role in ("PRIMARY", "SECONDARY"),
      "got %r" % (guard_body.tackle_role,))
check("target_source stamped",
      guard_body.target_source == TACKLE_ENGAGE_GUARD_SOURCE,
      "got %r" % (guard_body.target_source,))
check("closing posture pinned to CHASE",
      guard_body.movement_state is MovementState.CHASE)

out.append("\n[L4] guarded body is preferred over a slot-eligible rival")
bc4 = Body(98, 60.0, 36.0)
guard4 = Body(11, 61.0, 35.0, facing=FACE_AT_BC, tackle_role=None)
rival = Body(12, 60.0, 36.2, facing=math.pi, tackle_role="PRIMARY")  # slot-in, dist 0.2
check("rival is slot-eligible",
      is_slot_tackle_eligible(bc4, rival, world) is True)
check("rival is an engage-role body", is_engage_role(rival) is True)
chosen = select_primary_candidate(world, [rival, guard4], bc4)
check("closest eligible still chosen when both qualify (rival, 0.2m)", chosen is rival,
      "got %r" % (chosen,))
chosen_far = select_primary_candidate(world, [Body(13, 70.0, 36.2, tackle_role="PRIMARY"), guard4], bc4)
check("guarded body wins when the rival is far (7m)", chosen_far is guard4,
      "got %r" % (chosen_far,))

out.append("\n[L5] negative controls still refuse candidacy")
far = Body(21, 70.0, 35.0, facing=FACE_AT_BC, tackle_role=None)
check("far line body NOT a candidate",
      select_primary_candidate(world, [far], bc) is None)
mark = Body(22, 61.0, 35.0, role=DefenderRole.MARKER_A, facing=FACE_AT_BC)
check("marker NOT a candidate",
      select_primary_candidate(world, [mark], bc) is None)
away = Body(23, 61.0, 35.0, facing=0.0, tackle_role=None)
check("not-facing line body NOT a candidate",
      select_primary_candidate(world, [away], bc) is None)
frozen = Body(24, 61.0, 35.0, facing=FACE_AT_BC, tackle_role=None)
frozen.frozen = True
check("frozen line body NOT a candidate",
      select_primary_candidate(world, [frozen], bc) is None)
w_tes = SimpleNamespace(ball_owner="BC", tes_active=True, open_play_engine=None,
                        tackle_slot_threshold_m=0.3, frame_count=11)
check("no forced candidacy during live TES",
      select_primary_candidate(w_tes, [Body(25, 61.0, 35.0, facing=FACE_AT_BC)],
                               bc) is None)
w_notbc = SimpleNamespace(ball_owner="9", tes_active=False, open_play_engine=None,
                          tackle_slot_threshold_m=0.3, frame_count=11)
check("no forced candidacy when BC does not own the ball",
      select_primary_candidate(w_notbc, [Body(26, 61.0, 35.0, facing=FACE_AT_BC)],
                               bc) is None)
third = Body(27, 61.0, 35.0, facing=FACE_AT_BC, tackle_role="THIRD")
check("THIRD stays joiner-only (no candidacy, not radial+facing forced)",
      select_primary_candidate(world, [third], bc) is None
      or is_tackle_engage_guard(third, bc, world, tuners) is False)

summary = "\nLIVE-PATH PROBE: %d passed, %d failed" % (PASS, FAIL)
text = "\n".join(out) + "\n" + summary
with open("tmp/probe_guard_live_out.txt", "w", encoding="utf-8") as f:
    f.write(text)
print(text)
sys.exit(1 if FAIL else 0)
