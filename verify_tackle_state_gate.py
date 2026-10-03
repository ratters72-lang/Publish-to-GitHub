"""TACKLE-STATE GATE verifier (2026-08-23).

SPEC: "A defender stays in TACKLE-STATE for the entire TES/PTB while
inside tackle-radius, and only when they exit that radius do they
re-enter spacing and receive a depth role."

Gate geometry: distance from the RUCK/TACKLE POINT, radius = 3.5 m
(RUCK_BUBBLE_RADIUS_M; covers both markers at ruck_x+1/+2).

Regression target: the OLD behaviour released tacklers/markers purely on
phase timers -- the 0.3 s marker-freeze expiry converted a marker still
standing at ruck_x+1/+2 (well INSIDE any reasonable tackle-radius) to
DEF_LINE_READY, and the next frame's preamble handed it a depth role +
spacing target while it was still at the ruck.

PART 1 (deterministic predicates): a flagged holder inside the radius is
  invisible to every line system -- no depth role, absent from the
  spacing/width frame dicts, engagement-exempt, never a ruck-pin slot,
  never a lane-tackler candidate, MMDL HOLD.
PART 2 (engine wiring): across real engine frames the holder keeps its
  role and stays excluded while inside the radius.
PART 3 (timer-expiry NON-release + radius RELEASE): marker_freeze_timer
  expiring alone must NOT release the holder; moving it past
  radius + hysteresis MUST release it (flag cleared, DEF_LINE_READY,
  depth role + spacing entry on the following frame).
PART 4 (peel): an expired-freeze holder inside the bubble is moved
  OUTWARD each OPEN_PLAY frame so the exit actually happens.
"""
import sys
import math

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, Team
from player.movement_states import MovementState
from world.world_state import WorldState, GamePhase, GameSubState

from game.defence.defence_lvl_1.tackle_state_gate import (
    stamp_tackle_state,
    is_tackle_state_flagged,
    is_in_tackle_state,
    distance_to_tackle_point,
    update_tackle_state,
    reset_tackle_state,
    TACKLE_STATE_RADIUS_M,
)
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles
from game.defence.defence_lvl_1.defensive_engagement_state import (
    _is_engagement_exempt,
)
from game.defence.defence_lvl_1.ruck_adjacent_pin import _pin_eligible
from game.defence.defence_lvl_1.intercept_tacklers import (
    is_lane_tackler_candidate,
)
from game.defence.defence_lvl_1.movement_mode_decision import (
    decide_movement_state,
)

failures = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def make_defender(pid, x, y):
    d = Player(x=x, y=y, player_id=pid)
    d.team = Team.RECEIVING
    d.is_defender = True
    d.max_defensive_speed_mps = 8.3
    d.defender_role = None
    d.depth_role = None
    d.depth_side = None
    return d


def make_bc(pid=99, x=80.0, y=35.0):
    bc = make_defender(pid, x, y)
    bc.possessed = True
    return bc


def make_world():
    w = WorldState()
    w.ruck_established = True  # STAGE 3B: post-TACKLE_INIT band/role scenario

    w.ruck_x_m = 55.0
    w.ruck_y_m = 35.0
    w.def_line_width_m = 40.0
    w.first_tackle_not_made = False
    w.in_pursuit_mode = False
    w.use_structured_open_play = True
    w.defence_enabled = True
    w.tes_active = False
    w.phase = GamePhase.OPEN_PLAY
    w.sub_state = GameSubState.OPEN_PLAY
    w.ball_owner = "BC"
    w.role_governs = False
    w.ctx = None
    w.frame_count = 0
    w.sim_time_s = 0.0
    w.dt = 1.0 / 60.0
    return w


def build_engine(defenders, bc):
    w = make_world()
    w.defending_team = list(defenders)
    w.attacking_team = []
    w.players = list(defenders) + [bc]
    w.ball_carrier = bc
    from world.open_play_engine import OpenPlayEngine
    eng = OpenPlayEngine(w)
    w.open_play_engine = eng
    w.open_play = eng
    return w, eng


# ─────────────────────────────────────────────────────────────────────
# PART 1 — deterministic exclusion predicates
# ─────────────────────────────────────────────────────────────────────

def part1_predicates():
    print("[PART 1] Flagged holder inside the radius is invisible to the line")
    w = make_world()
    holder = make_defender(1, 56.0, 35.0)   # ruck_x + 1 -> inside 3.5 m
    line_d = make_defender(2, 66.0, 30.0)   # normal line defender
    stamp_tackle_state(holder)
    bc = make_bc()

    check("P1.0: holder reads as IN tackle-state",
          is_in_tackle_state(holder, w),
          f"dist={distance_to_tackle_point(holder, w):.2f} "
          f"radius={TACKLE_STATE_RADIUS_M}")

    # Depth roles: holder gets NONE, line defender gets one.
    result = assign_depth_roles([holder, line_d], w)
    check("P1.1: holder receives NO depth role",
          getattr(holder, "depth_role", None) is None,
          f"depth_role={getattr(holder, 'depth_role', None)}")
    check("P1.2: line defender DOES receive a depth role",
          getattr(line_d, "depth_role", None) in ("A", "B", "C", "D", "CENTRE", "WINGER"),
          f"depth_role={getattr(line_d, 'depth_role', None)}")

    # Spacing/width frame dicts exclude the holder.
    from game.defence.defence_lvl_1.defensive_pipeline import run_defensive_pipeline
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
    tuners = DefenceTuners()
    spacing, width = run_defensive_pipeline(w, [holder, line_d], tuners)
    check("P1.3: holder ABSENT from frame_spacing_targets",
          id(holder) not in spacing,
          f"keys={len(spacing)}")
    check("P1.4: holder ABSENT from frame_width_targets",
          id(holder) not in width)
    check("P1.5: line defender PRESENT in frame_spacing_targets",
          id(line_d) in spacing)

    # Engagement exemption.
    check("P1.6: holder is engagement-exempt", _is_engagement_exempt(holder))

    # Ruck-pin eligibility.
    check("P1.7: holder is NEVER a ruck-pin slot", not _pin_eligible(holder, w))

    # Lane-tackler candidacy.
    check("P1.8: holder is NEVER a lane-tackler candidate",
          not is_lane_tackler_candidate(holder, bc.x))

    # MMDL decision.
    state = decide_movement_state(w, holder, bc, tuners)
    check("P1.9: MMDL holds the holder (never chases/slides/shoots)",
          state == MovementState.HOLD,
          f"state={state}")


# ─────────────────────────────────────────────────────────────────────
# PART 2 — engine wiring: holder stays held across real frames
# ─────────────────────────────────────────────────────────────────────

def part2_engine_hold():
    print("[PART 2] Engine frames: holder keeps role + exclusion inside radius")
    holder = make_defender(1, 56.0, 35.0)
    holder.defender_role = __import__(
        "player.player", fromlist=["DefenderRole"]
    ).DefenderRole.MARKER_A
    line_a = make_defender(2, 66.0, 30.0)
    line_b = make_defender(3, 67.0, 40.0)
    stamp_tackle_state(holder)
    bc = make_bc(x=80.0, y=35.0)
    bc.vx = 5.0
    w, eng = build_engine([holder, line_a, line_b], bc)

    dt = 1.0 / 60.0
    err = None
    for _ in range(10):
        try:
            eng.update(dt)
        except Exception as exc:
            err = exc
            break

    if err is not None:
        check("P2.0: engine frames run clean", False,
              f"{type(err).__name__}: {str(err)[:200]}")
        return
    check("P2.0: engine frames run clean", True)

    check("P2.1: holder STILL flagged after 10 engine frames",
          is_tackle_state_flagged(holder))
    role_name = getattr(getattr(holder, "defender_role", None), "name", None)
    check("P2.2: holder KEEPS its MARKER_A role (no timer conversion)",
          role_name == "MARKER_A",
          f"role={role_name}")
    dct = getattr(w, "frame_spacing_targets", None) or {}
    check("P2.3: holder ABSENT from live frame_spacing_targets",
          id(holder) not in dct)
    check("P2.4: holder received NO depth role",
          getattr(holder, "depth_role", None) is None,
          f"depth_role={getattr(holder, 'depth_role', None)}")


# ─────────────────────────────────────────────────────────────────────
# PART 3 — freeze-expiry NON-release + radius-exit RELEASE
# ─────────────────────────────────────────────────────────────────────

def part3_release():
    print("[PART 3] Freeze expiry alone releases NOTHING; radius exit does")
    holder = make_defender(1, 56.0, 35.0)
    holder.defender_role = __import__(
        "player.player", fromlist=["DefenderRole"]
    ).DefenderRole.MARKER_B
    line_a = make_defender(2, 66.0, 30.0)
    line_b = make_defender(3, 67.0, 40.0)
    stamp_tackle_state(holder)
    bc = make_bc(x=80.0, y=35.0)
    bc.vx = 5.0
    w, eng = build_engine([holder, line_a, line_b], bc)

    dt = 1.0 / 60.0
    # Simulate an ALREADY-EXPIRED marker freeze (the old bug's trigger).
    holder.marker_freeze_timer = 0.0
    for _ in range(6):
        try:
            eng.update(dt)
        except Exception as exc:
            check("P3.0: engine frames run clean", False,
                  f"{type(exc).__name__}: {str(exc)[:200]}")
            return
    role_name = getattr(getattr(holder, "defender_role", None), "name", None)
    check("P3.1: EXPIRED freeze alone does NOT convert the holder",
          is_tackle_state_flagged(holder) and role_name == "MARKER_B",
          f"flagged={is_tackle_state_flagged(holder)} role={role_name}")

    # ── Radius exit ──
    holder.x = w.ruck_x_m + 4.5      # > radius(3.5) + hysteresis(0.25)
    released = update_tackle_state(w, dt)
    check("P3.2: crossing radius+hysteresis RELEASES the holder",
          holder in released and not is_tackle_state_flagged(holder),
          f"released={[getattr(d, 'player_id', '?') for d in released]}")
    role_name = getattr(getattr(holder, "defender_role", None), "name", None)
    check("P3.3: released holder converts to DEF_LINE_READY",
          role_name == "DEF_LINE_READY",
          f"role={role_name}")

    # Next engine frame: depth role + spacing entry.
    try:
        eng.update(dt)
    except Exception as exc:
        check("P3.4: post-release engine frame runs clean", False,
              f"{type(exc).__name__}: {str(exc)[:200]}")
        return
    check("P3.4: post-release engine frame runs clean", True)
    check("P3.5: released holder RECEIVES a depth role",
          getattr(holder, "depth_role", None) in ("A", "B", "C", "D", "CENTRE", "WINGER"),
          f"depth_role={getattr(holder, 'depth_role', None)}")
    dct = getattr(w, "frame_spacing_targets", None) or {}
    check("P3.6: released holder PRESENT in frame_spacing_targets",
          id(holder) in dct)


# ─────────────────────────────────────────────────────────────────────
# PART 4 — peel writer moves expired holders outward
# ─────────────────────────────────────────────────────────────────────

def part4_peel():
    print("[PART 4] Expired-freeze holder PEELS outward in OPEN_PLAY")
    holder = make_defender(1, 56.0, 35.0)   # ruck_x + 1
    line_a = make_defender(2, 66.0, 30.0)
    line_b = make_defender(3, 67.0, 40.0)
    stamp_tackle_state(holder)
    bc = make_bc(x=80.0, y=35.0)
    bc.vx = 5.0
    w, eng = build_engine([holder, line_a, line_b], bc)

    dt = 1.0 / 60.0
    holder.marker_freeze_timer = 0.0
    d_before = distance_to_tackle_point(holder, w)
    try:
        eng.update(dt)
    except Exception as exc:
        check("P4.0: engine frame runs clean", False,
              f"{type(exc).__name__}: {str(exc)[:200]}")
        return
    d_after = distance_to_tackle_point(holder, w)
    check("P4.1: holder MOVED OUTWARD (peel active)",
          d_after > d_before + 0.02,
          f"dist {d_before:.3f} -> {d_after:.3f}")
    check("P4.2: holder still flagged (not yet past exit bound)",
          is_tackle_state_flagged(holder))


def main():
    print("=" * 64)
    print("TACKLE-STATE GATE (radius hold/release, spec 2026-08-23)")
    print("=" * 64)

    part1_predicates()
    part2_engine_hold()
    part3_release()
    part4_peel()

    print("=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
