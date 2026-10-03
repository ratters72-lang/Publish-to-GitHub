import inspect
import sys
sys.path.insert(0, ".")
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

out = []

import importlib
for m in (
    "game.defence.defence_lvl_1.tackle_engage_guard",
    "game.defence.defence_lvl_1.movement_mode_decision",
    "game.defence.defence_lvl_1.pre_tackle_authority",
    "game.defence.defence_lvl_1.pretackle_machine",
    "game.defence.defence_lvl_1.pretackle_classifier",
    "game.defence.defence_lvl_1.slot_arbitration",
    "world.open_play_engine",
    "game.managers.defence_manager",
):
    try:
        importlib.import_module(m)
        out.append("IMPORT OK   %s" % m)
    except Exception as e:
        out.append("IMPORT FAIL %s -> %r" % (m, e))

from types import SimpleNamespace
from player.player import DefenderRole
from player.movement_states import MovementState
from game.defence.defence_lvl_1.pretackle_classifier import is_engage_role
from game.defence.defence_lvl_1.slot_arbitration import is_slot_tackle_eligible
from game.defence.defence_lvl_1.tackle_engage_guard import (
    is_tackle_engage_guard, approach_angle_deg, force_tackle_engage,
    TACKLE_ENGAGE_GUARD_SOURCE,
)
from game.defence.defence_lvl_1.pretackle_machine import (
    run_machine, should_engage, PretackleMachineState,
)


class Body:
    def __init__(self, pid, x, y, role=DefenderRole.DEF_LINE_READY,
                 state=MovementState.CHASE, facing=0.0):
        self.player_id = pid
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self.facing = facing
        self.frozen = False
        self.freeze_timer = 0.0
        self.fullback_role_active = False
        self.tackle_state_active = False
        self.defender_role = role
        self.movement_state = state
        self.target_source = None

    def set_facing(self, a):
        self.facing = a


tuners = SimpleNamespace(tackle_radius_m=1.5, tackle_engage_facing_max_deg=60.0)
world = SimpleNamespace(tes_active=False, ball_owner="BC", frame_count=7)


def report(label, got, expect):
    out.append("%-46s %s   (expect %s)" % (label, got, expect))


out.append("\n=== PREDICATE UNIT CASES (bc at (60,35); radial 1.0m) ===")
bc = Body(99, 60.0, 35.0)
# facing pi points -X, straight at bc
d_face = Body(1, 61.0, 35.0, facing=3.141592653589793)
report("approach angle (facing pi)", "%.1f deg" % approach_angle_deg(d_face, bc), "0.0")
report("guard radial-in + facing", is_tackle_engage_guard(d_face, bc, world, tuners), True)
force_tackle_engage(d_face, world=world)
report("force -> target_source", d_face.target_source, TACKLE_ENGAGE_GUARD_SOURCE)
report("force -> movement_state", d_face.movement_state.name, "CHASE")
report("force -> tackle_role", getattr(d_face, "tackle_role", None), "PRIMARY")

d_away = Body(3, 61.0, 35.0, facing=0.0)
report("guard not-facing", is_tackle_engage_guard(d_away, bc, world, tuners), False)
d_far = Body(4, 70.0, 35.0, facing=3.141592653589793)
report("guard radial-out (9.0m)", is_tackle_engage_guard(d_far, bc, world, tuners), False)
d_mark = Body(5, 61.0, 35.0, role=DefenderRole.MARKER_A, facing=3.141592653589793)
report("guard marker", is_tackle_engage_guard(d_mark, bc, world, tuners), False)
d_fb = Body(6, 61.0, 35.0, facing=3.141592653589793)
d_fb.fullback_role_active = True
report("guard fullback", is_tackle_engage_guard(d_fb, bc, world, tuners), False)
d_fr = Body(7, 61.0, 35.0, facing=3.141592653589793)
d_fr.frozen = True
report("guard frozen", is_tackle_engage_guard(d_fr, bc, world, tuners), False)
w_tes = SimpleNamespace(tes_active=True, ball_owner="BC", frame_count=7)
report("guard during live TES", is_tackle_engage_guard(d_face, bc, w_tes, tuners), False)

out.append("\n=== END-TO-END: radial-in + facing, SLOT-OUT ===")
# dist = hypot(-1, 1) = 1.414 < 1.5 (radial-in)
# |bc.y - d.y| = 1.0 > 0.3 (slot-OUT)
bc_so = Body(98, 60.0, 36.0)
import math as _m
facing_at_bc = _m.atan2(36.0 - 35.0, 60.0 - 61.0)   # atan2(1, -1)
d5 = Body(5, 61.0, 35.0, facing=facing_at_bc)
report("dist to bc", "%.3f m" % _m.hypot(1.0, 1.0), "< 1.5 (radial-in)")
report("slot eligible", is_slot_tackle_eligible(bc_so, d5, world), "False (slot-OUT)")
report("guard predicate", is_tackle_engage_guard(d5, bc_so, world, tuners), True)
st = run_machine(world, d5, bc_so, tuners)
report("run_machine state", st.name, "CLOSING_HEAD_ON")
report("tackle_role after machine", getattr(d5, "tackle_role", None), "PRIMARY")
report("should_engage", should_engage(world, d5, bc_so, tuners, state=st), True)
report("state in ENGAGE_STATES", st in __import__(
    "game.defence.defence_lvl_1.pretackle_machine", fromlist=["ENGAGE_STATES"]
).ENGAGE_STATES, True)

out.append("\n=== ROLE PRESERVATION (existing SECONDARY) ===")
d6 = Body(6, 61.0, 35.0, facing=facing_at_bc)
d6.tackle_role = "SECONDARY"
st6 = run_machine(world, d6, bc_so, tuners)
report("state", st6.name, "CLOSING_HEAD_ON")
report("tackle_role", d6.tackle_role, "SECONDARY (not downgraded)")

out.append("\n=== NEGATIVE CONTROL: radial-in but NOT facing ===")
d7 = Body(7, 61.0, 35.0, facing=0.0)
st7 = run_machine(world, d7, bc_so, tuners)
report("run_machine state", st7.name, "RETREATING")
report("tackle_role", getattr(d7, "tackle_role", None), "None (no role stamp)")

out.append("\n=== NEGATIVE CONTROL: slot-IN unaffected path ===")
bc_in = Body(97, 60.0, 35.1)
d8 = Body(8, 61.0, 35.0, facing=facing_at_bc)
report("slot eligible (0.1m off)", is_slot_tackle_eligible(bc_in, d8, world), True)

with open("tmp/probe_guard_out.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("\n".join(out))
