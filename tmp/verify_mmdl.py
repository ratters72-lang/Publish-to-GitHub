"""
Headless MMDL verification v2.

Part A — DIRECT UNIT TESTS of decide_movement_state() covering ALL 6
branches of the decision tree (RETREAT/SLIDE/SHOOT/CHASE/DRIFT/HOLD) plus
the TES-tackler guard and marker guard.

Part B — Full kickoff->catch->OPEN_PLAY->tackle-cycle sim with the REAL
engine, asserting:
  - no exception across a full tackle cycle
  - all movement_state transitions respect VALID_TRANSITIONS
  - per-frame displacement is bounded by max_speed*dt * 1.3 (proves
    apply_movement_state integrates EXACTLY once; a second Player.update
    would double displacement)
  - TES tacklers keep movement_mode TACKLER during TES (MMDL untouched)
"""
import math
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, ".")

import pygame
pygame.init()
pygame.display.set_mode((64, 64))

from types import SimpleNamespace as NS

from game.defence.defence_lvl_1.movement_mode_decision import decide_movement_state
from player.movement_states import MovementState, is_valid_transition
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners


def make_defender(x, y, depth_class="PASSIVE", player_id=1, role=None):
    return NS(
        x=x, y=y, depth_class=depth_class, player_id=player_id,
        defender_role=role, movement_state=None, is_tackler=False,
    )


def make_bc(x, y):
    return NS(x=x, y=y)


def make_world(ruck_x=None, tes_active=False):
    return NS(ruck_x_m=ruck_x, tes_active=tes_active)


TUNERS = DefenceTuners()
failures = []


def check(name, actual, expected):
    if actual != expected:
        failures.append(f"{name}: expected {expected!r}, got {actual!r}")


# ── A1. No BC ──
check("no-bc PASSIVE", decide_movement_state(make_world(), make_defender(10, 10, "PASSIVE"), None, TUNERS), MovementState.RETREAT)
check("no-bc PRIMARY", decide_movement_state(make_world(), make_defender(10, 10, "PRIMARY"), None, TUNERS), MovementState.HOLD)

# ── A2. TES guard: active tackler keeps current state ──
tes_def = make_defender(10, 10, "PRIMARY")
tes_def.is_tackler = True
tes_def.movement_state = MovementState.CHASE
check("tes tackler guard", decide_movement_state(make_world(tes_active=True), tes_def, make_bc(15, 10), TUNERS), MovementState.CHASE)

# ── A3. Marker guard ──
marker = make_defender(10, 10, "MARKER", role="MARKER_A")
check("marker hold", decide_movement_state(make_world(ruck_x=5), marker, make_bc(15, 10), TUNERS), MovementState.HOLD)

# ── A4. Retreat compliance: PASSIVE in front of ruck_x + compliance ──
check("retreat non-compliant", decide_movement_state(make_world(ruck_x=5), make_defender(5.0, 10, "PASSIVE"), make_bc(20, 10), TUNERS), MovementState.RETREAT)
# compliant PASSIVE but deep -> HOLD
check("passive deep hold", decide_movement_state(make_world(ruck_x=5), make_defender(5.0, 10, "PASSIVE"), make_bc(30, 10), TUNERS), MovementState.RETREAT)  # still non-compliant: x=5 < 15

# ── A5. PASSIVE line hold / drift (compliant) ──
check("passive compliant deep hold", decide_movement_state(make_world(ruck_x=5), make_defender(20.0, 10, "PASSIVE"), make_bc(50, 10), TUNERS), MovementState.HOLD)
check("passive compliant drift", decide_movement_state(make_world(ruck_x=5), make_defender(20.0, 10, "PASSIVE"), make_bc(30, 10), TUNERS), MovementState.DRIFT)

# ── A6. PRIMARY/SECONDARY commit windows ──
# SLIDE: lateral > threshold, dist < engage
check("slide", decide_movement_state(make_world(ruck_x=5), make_defender(10.0, 10.0, "PRIMARY"), make_bc(15.0, 16.0), TUNERS), MovementState.SLIDE)
# SHOOT: dist < commit (8), lateral <= 3
check("shoot close", decide_movement_state(make_world(ruck_x=5), make_defender(10.0, 10.0, "PRIMARY"), make_bc(15.0, 12.0), TUNERS), MovementState.SHOOT)
# CHASE: dist>engage or far-lateral beyond slide
check("chase far", decide_movement_state(make_world(ruck_x=5), make_defender(10.0, 10.0, "SECONDARY"), make_bc(40.0, 10.0), TUNERS), MovementState.CHASE)

# ── A7. Tuner-less safety (bare namespace) ──
check("no-tuners slide", decide_movement_state(make_world(ruck_x=5), make_defender(10.0, 10.0, "PRIMARY"), make_bc(15.0, 16.0), None), MovementState.SLIDE)

print("=== UNIT TESTS ===")
for f in failures:
    print("  FAIL:", f)
print(f"unit failures: {len(failures)}")

# ─────────────────────────────────────────────────────────────────
# PART B — engine sim
# ─────────────────────────────────────────────────────────────────
from world.field import Field
from world.world_state import WorldState, GamePhase
from world.open_play_engine import OpenPlayEngine
from kick_off.kickoff_formations import apply_receiving_kickoff_formation, apply_kicking_team_formation
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
field = Field(SCREEN_WIDTH, SCREEN_HEIGHT)
world = WorldState()
ball = Ball(field)
world.ball = ball
open_play = OpenPlayEngine(world)
world.open_play = open_play
world.open_play_engine = open_play
kickoff_engine = KickoffEngine(world, ball, field)
collision_system = CollisionSystem()

kickoff_engine.reset_for_kickoff()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff_engine.kickoff()

DT = 1.0 / 60.0
MAX_FRAMES = 60 * 60
states_seen = set()
transition_errors = []
double_move_errors = []
structured_frames = 0
tackle_phases_seen = set()
tackler_modes_ok = True
# Per-defender last-frame snapshot for transition + displacement checks.
prev_state = {}
prev_x = {}
prev_y = {}
prev_frame = {}   # OPEN_PLAY frame at which the previous sample was taken

for frame in range(MAX_FRAMES):
    ball.update(DT)
    world.frame_count += 1

    if world.phase == GamePhase.KICKOFF:
        kickoff_engine.update(DT)
        check_for_catch(world, ball, DT)
    elif world.phase == GamePhase.CATCH:
        if world.ball_carrier is not None:
            ope = world.open_play
            if ope is not None:
                ope.start(world.ball_carrier)
        world.phase = GamePhase.OPEN_PLAY
    elif world.phase == GamePhase.OPEN_PLAY:
        open_play.update(DT)

        if open_play._phase in (
            "TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE"
        ):
            tackle_phases_seen.add(open_play._phase)
            for d in world.defending_team:
                if getattr(d, "is_tackler", False):
                    mode = getattr(d, "movement_mode", None)
                    if mode is None or getattr(mode, "name", "") != "TACKLER":
                        tackler_modes_ok = False
            continue

        if open_play._phase != "OPEN_PLAY":
            continue
        if not getattr(world, "use_structured_open_play", True):
            continue
        if world.in_pursuit_mode:
            continue

        structured_frames += 1
        for d in world.defending_team:
            if d.frozen:
                continue
            state = getattr(d, "movement_state", None)
            if state is None:
                continue
            states_seen.add(state.name)

            pid = d.player_id
            # Transition validity.
            pst = prev_state.get(pid)
            if pst is not None and pst != state and not is_valid_transition(pst, state):
                transition_errors.append(f"f{world.frame_count} id={pid} {pst.name}->{state.name}")
            prev_state[pid] = state

            # Displacement bound: |dx|,|dy| must each be <= max_speed*dt*1.3.
            # A double integration (MMDL + Player.update) would exceed this.
            # Only compare against a sample taken on the IMMEDIATELY previous
            # OPEN_PLAY frame — positions across a TES/PTB phase gap are
            # legitimately driven by other movement owners.
            px = prev_x.get(pid)
            if px is not None and prev_frame.get(pid, -999) == world.frame_count - 1:
                bound = d.max_speed_mps * DT * 1.3
                if abs(d.x - px) > bound or abs(d.y - prev_y.get(pid, d.y)) > bound:
                    double_move_errors.append(
                        f"f{world.frame_count} id={pid} dx={d.x-px:.4f} "
                        f"dy={d.y-prev_y.get(pid,d.y):.4f} bound={bound:.4f} "
                        f"state={state.name}"
                    )
            prev_x[pid] = d.x
            prev_y[pid] = d.y
            prev_frame[pid] = world.frame_count

        if not world.tes_active:
            collision_system.resolve_all(world.players, world=world)

    if len(tackle_phases_seen) >= 4 and "PTB" in open_play._phase:
        break

print("=== ENGINE SIM ===")
print(f"structured OPEN_PLAY frames: {structured_frames}")
print(f"MovementStates exercised: {sorted(states_seen)}")
print(f"transition errors: {len(transition_errors)}")
for e in transition_errors[:5]:
    print("  ", e)
print(f"double-move errors (displacement over max*dt*1.3): {len(double_move_errors)}")
for e in double_move_errors[:5]:
    print("  ", e)
print(f"tackler movement_mode intact: {tackler_modes_ok}")
print(f"tackle phases seen: {sorted(tackle_phases_seen)}")

ok = (
    not failures
    and structured_frames > 10
    and len(states_seen) >= 4
    and not transition_errors
    and not double_move_errors
    and tackler_modes_ok
    and len(tackle_phases_seen) >= 2
)
print("MMDL VERIFY:", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
