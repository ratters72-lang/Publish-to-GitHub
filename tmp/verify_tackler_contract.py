"""Headless verification of the TACKLER MOVEMENT CONTRACT (spec A-H)."""
import math
import os
import sys

sys.path.insert(0, os.getcwd())

from player.player import Player, DefenderRole
from player.movement_states import MovementMode, CollisionPriority

FAILURES = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    if not cond:
        FAILURES.append(f"{name}: {detail}")
    print(f"[{tag}] {name} {detail}")


# ── Minimal world with the attributes OPE touches at construction ──
class FakeCollision:
    def __init__(self):
        self.collision_enabled = True
        self.player_id = -1
        self.reset_collision_state = lambda: None
        self.apply_kickoff_properties = lambda: None
        self.sync = lambda: None


def make_player(x, y, pid):
    p = Player(x, y, pid)
    p.mass_kg = 90.0
    p.radius = 0.55
    p.facing_override = False
    p.facing_override_angle = 0.0
    p.body = FakeCollision()
    p.attack_role = "none"
    p.tackle_role = "PASSIVE"
    p.retreating = False
    p.freeze_timer = 0.0
    p.frozen = False
    p.teams_set = True
    return p


class MiniWorld:
    """Stub world satisfying OPE.__init__ + the new verification paths."""
    attacking_direction = 1
    ruck_x_m = 55.0
    ruck_y_m = 35.0
    frame_count = 0
    dt = 1.0 / 60.0


from world.open_play_engine import (  # noqa: E402
    OpenPlayEngine,
    TACKLER_ACCEL_FLOOR_MPS,
    TACKLER_CONTACT_FLOOR_MPS,
    TACKLER_LUNGE_SPEED_MPS,
    TACKLER_OPEN_PLAY_LUNGE_X_M,
)


def build_engine():
    w = MiniWorld()
    w.players = []
    w.defending_team = []
    w.attacking_team = []
    w.ball_carrier = None
    w.sub_state = None
    w.tes_active = False
    w.in_kickoff = False
    w.first_tackle_not_made = False
    w.in_pursuit_mode = False
    return w


# ═══════════════════════════════════════════════════════════════
# 1. ROLE SWITCH + MOVEMENT MODE (spec A/B)
# ═══════════════════════════════════════════════════════════════
bc = make_player(60.0, 35.0, 1)
d1 = make_player(57.5, 35.0, 2)
d2 = make_player(58.0, 33.0, 3)

from game.tes.tes_context import TESContext  # noqa: E402

ctx = TESContext()
ctx.tackler_roles = {}

# Manual invocation of the contract's essential wiring.  Real engine path:
# _start_tackle -> _arm_tackler_role -> _update_tackler_movement -> RELEASE.
ope = OpenPlayEngine.__new__(OpenPlayEngine)  # skip delegate-heavy __init__
ope.primary = d1
ope.joiners = [d2]
ope.ball_carrier = bc
ope.world = build_engine()
ope.world.players = [bc, d1, d2]
ope.world.defending_team = [d1, d2]

ope._arm_tackler_role(d1, DefenderRole.TACKLER_1, "TACKLER_1", ctx)
ope._arm_tackler_role(d2, DefenderRole.TACKLER_2, "TACKLER_2", ctx)

check("A: primary defender_role=TACKLER_1",
      d1.defender_role == DefenderRole.TACKLER_1,
      f"got {d1.defender_role}")
check("A: primary tes_tackler_role=TACKLER_1",
      d1.tes_tackler_role == "TACKLER_1", f"got {d1.tes_tackler_role}")
check("A: primary movement_mode=TACKLER",
      d1.movement_mode == MovementMode.TACKLER, f"got {d1.movement_mode}")
check("A: primary collision_priority=HIGH",
      d1.collision_priority == CollisionPriority.HIGH,
      f"got {d1.collision_priority}")
check("A: joiner movement_mode=TACKLER",
      d2.movement_mode == MovementMode.TACKLER, f"got {d2.movement_mode}")
check("A: ctx.tackler_roles populated",
      ctx.tackler_roles.get(2) == "TACKLER_1"
      and ctx.tackler_roles.get(3) == "TACKLER_2",
      f"got {ctx.tackler_roles}")
check("A: retreating cleared", d1.retreating is False)

# ═══════════════════════════════════════════════════════════════
# 2. TACKLER MOVEMENT BEHAVIOUR (spec C)
# ═══════════════════════════════════════════════════════════════
# Place BC in open play (bc.x > ruck_x + 5) and a tackler 2.0 m away.
ope.world.ruck_x_m = 55.0
bc.x = 70.0
bc.y = 35.0
bc.vx = 5.0
bc.vy = 0.5
d1.x = 68.0
d1.y = 35.0
d1.frozen = False
d1.tes_attached = False

ope._update_tackler_movement(1.0 / 60.0)

check("C1: speed_target floored at 4.0 (dist<=3.0)",
      d1.speed_target >= TACKLER_ACCEL_FLOOR_MPS,
      f"got {d1.speed_target:.2f}")
check("C1: speed_target not slower than BC",
      d1.speed_target >= math.hypot(bc.vx, bc.vy),
      f"speed_target={d1.speed_target:.2f} bc_speed={math.hypot(bc.vx, bc.vy):.2f}")
check("C2: vx_target=bc.vx", abs(d1.vx_target - 5.0) < 1e-6,
      f"got {d1.vx_target}")
check("C2: vy_target=bc.vy", abs(d1.vy_target - 0.5) < 1e-6,
      f"got {d1.vy_target}")
check("C3: heading_target faces BC",
      abs(d1.heading_target - math.atan2(0.0, 2.0)) < 1e-6,
      f"got {d1.heading_target:.4f}")
check("C4: lunge active in open play",
      d1.lunge_active and abs(d1.lunge_speed - TACKLER_LUNGE_SPEED_MPS) < 1e-6,
      f"lunge_active={d1.lunge_active} lunge_speed={d1.lunge_speed}")
check("C4: lunge_duration=0.20", abs(d1.lunge_duration - 0.20) < 1e-6,
      f"got {d1.lunge_duration}")

# dist <= 1.5 -> floor 5.0
bc.x = 69.2
d1.x = 67.8
ope._update_tackler_movement(1.0 / 60.0)
check("C1: speed_target floored at 5.0 (dist<=1.5)",
      d1.speed_target >= TACKLER_CONTACT_FLOOR_MPS,
      f"got {d1.speed_target:.2f}")

# ═══════════════════════════════════════════════════════════════
# 3. DISTANCE-ONLY TES TRIGGER (spec D)
# ═══════════════════════════════════════════════════════════════
from game.defence.defence_lvl_1.defensive_behaviour_lvl1 import try_pre_tackle  # noqa: E402
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners  # noqa: E402

class MiniWorldTrigger:
    ball_owner = "BC"
    ruck_x_m = 55.0
    sub_state = None


w_trig = MiniWorldTrigger()
w_trig.open_play_engine = ope

# TACKLER-mode, in talkie distance, but designed to fail every evasion gate:
# stationary defender, BC fast, rear angle.
d1.x = 68.2
bc.x = 69.99
bc.vx = 8.0
bc.vy = 0.0
bc._prev_facing = 0.0
bc.facing = 0.0
d1.vx = 0.0
d1.vy = 0.0
d1.tackle_role = "PASSIVE"  # would normally reject a non-tackler
dist = math.hypot(bc.x - d1.x, bc.y - d1.y)
check("D: setup within 2.0m", dist <= 2.0 + 1e-9, f"dist={dist:.3f}")

triggered = try_pre_tackle(w_trig, d1, bc, DefenceTuners())
check("D: TACKLER-mode triggers on distance alone (evasion gates bypassed)",
      triggered is True,
      f"got {triggered}")

# Non-tackler must still be blocked (regression guard)
d3 = make_player(67.0, 35.0, 4)
d3.vx = 0.0
d3.vy = 0.0
d3.tackle_role = "PASSIVE"
d3.retreating = False
w_trig_not = MiniWorldTrigger()
w_trig_not.open_play_engine = ope
blocked = try_pre_tackle(w_trig_not, d3, bc, DefenceTuners())
check("D: non-TACKLER still gated",
      blocked is False,
      "non-TACKLER unexpectedly triggered TES")

# ═══════════════════════════════════════════════════════════════
# 4. RELEASE TRANSITION (spec G)
# ═══════════════════════════════════════════════════════════════
class MiniWorldRelease:
    players = []
    defending_team = []
    attacking_team = []
    defensive_line_anchor = 0.0
    tes_active = True
    tackler_ids = {2, 3}


w_rel = MiniWorldRelease()
w_rel.players = [bc, d1, d2]
w_rel.defending_team = [d1, d2]
ope.world = w_rel

ope._clear_tes_artefacts()

check("G: ex-tackler movement_mode=RETREAT",
      d1.movement_mode == MovementMode.RETREAT,
      f"got {d1.movement_mode}")
check("G: ex-tackler lunge disabled",
      d1.lunge_active is False and d1.lunge_speed == 0.0
      and d1.lunge_duration == 0.0,
      f"lunge_active={d1.lunge_active} lunge_speed={d1.lunge_speed}")
check("G: collision_priority cleared",
      d1.collision_priority is None and d2.collision_priority is None,
      f"primary={d1.collision_priority} joiner={d2.collision_priority}")
check("G: tes_tackler_role cleared",
      d1.tes_tackler_role is None and d2.tes_tackler_role is None,
      f"primary={d1.tes_tackler_role} joiner={d2.tes_tackler_role}")
check("G: tes_active cleared", w_rel.tes_active is False)
check("G: tackler_ids cleared", len(w_rel.tackler_ids) == 0)

# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════
print()
if FAILURES:
    print(f"TACKLER CONTRACT VERIFICATION: {len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("TACKLER CONTRACT VERIFICATION: ALL CHECKS PASSED")
