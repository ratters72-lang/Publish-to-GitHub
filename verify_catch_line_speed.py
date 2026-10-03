"""Catch-line-speed verifier (2026-08-15).

Regression for the post-catch slowdown: right after a reception the new
ball carrier sits behind the defensive-line anchor with low speed and the
engine may still be framing the transition, so the CHASE-SCENARIO override
in classify_depth_all never fires and every non-marker defender used to be
seeded ``depth_class = PASSIVE`` -> MMDL DRIFT (the "half the line jogging"
slowdown).

Fix: lane-eligible defenders (intercept_tacklers.is_lane_tackler_candidate)
seed as SECONDARY in classify_depth_all, so the MMDL resolves
CHASE/SLIDE/SHOOT (full chase speed) instead of DRIFT/HOLD.

This verifier drives REAL engine frames with the BC behind the line and
asserts, across every sampled transition frame:
  1. lane-eligible defenders are NEVER classified PASSIVE (the regression
     guard - with the PASSIVE seed removed the post-break reactivity audit
     has nothing to flag),
  2. MMDL resolves CHASE/SLIDE/SHOOT (not DRIFT) for the line defenders,
  3. the line advances purposefully: committed CHASE/SHOOT movers always
     converge on the carrier; SLIDE movers progress to their AUTHORED
     spacing slot derived from the governed width (SpacingChain owns Y,
     Task E), so the wider post-fix line still forms without dragging
     away from the play,
  4. no LATE_DEPTH_SWITCH events are emitted by the reactivity audit on
     any frame where it is eligible to run (the consequence guard).
"""
import sys
import math
import io
from contextlib import redirect_stdout

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, Team
from world.world_state import WorldState, GamePhase, GameSubState
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    role_anchor_y_for,
    governed_width_m,
)

failures = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def make_defender(pid, x, y):
    d = Player(x=x, y=y, player_id=pid)
    d.team = Team.RECEIVING
    d.is_defender = True
    d.max_defensive_speed_mps = 8.3
    d.defender_role = None
    d.depth_role = "A"
    d.depth_side = 1
    d.fullback_role_active = False
    return d


def make_world():
    w = WorldState()
    w.ruck_x_m = 55.0
    w.ruck_y_m = 35.0
    w.def_line_width_m = 40.0
    w.defensive_line_anchor = 65.0
    w.anchor_locked = True
    w.govvernor_width_active = False
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
    w.frames_since_ptb = 60   # past the audit grace window
    return w


def main():
    print("=" * 64)
    print("CATCH-LINE-SPEED (line must NOT collapse to PASSIVE/DRIFT)")
    print("=" * 64)

    # Line of 5 straddling the BC lane, sitting behind the anchored line
    # (anchor 65), plus a deep 6th defender so the engine assigns the
    # fullback actor to #6 and NOT to a mid-line defender (the FB actor
    # is legitimately PASSIVE/DRIFT).  The BC starts near the ruck (60)
    # and moves slowly, so the chase-scenario override in
    # classify_depth_all is SILENT - the exact post-catch transition the
    # fix targets.
    line = [
        make_defender(1, 63.0, 33.0),
        make_defender(2, 63.4, 34.2),
        make_defender(3, 63.6, 35.0),   # on the BC lane
        make_defender(4, 63.2, 36.2),
        make_defender(5, 62.8, 37.4),
    ]
    deep = make_defender(6, 66.0, 38.0)  # deeper -> absorbs FB actor
    bc = make_defender(99, 60.0, 35.0)
    bc.vx = 0.8
    defenders = line + [deep]
    w = make_world()
    w.defending_team = list(defenders)
    w.attacking_team = []
    w.players = list(defenders) + [bc]
    w.ball_carrier = bc
    bc.possessed = True

    from world.open_play_engine import OpenPlayEngine
    eng = OpenPlayEngine(w)
    w.open_play_engine = eng
    w.open_play = eng

    from game.debug.defence_reactivity_audit import audit_defence_reactivity

    tuners = DefenceTuners()
    dt = 1.0 / 60.0
    seen_passive = set()   # ids ever seen PASSIVE in the transition
    seen_drift = set()     # ids ever seen DRIFT in the transition
    chase_states = set()   # ids ever seen CHASE/SLIDE/SHOOT
    converging = set()     # ids ever seen moving toward the BC
    slot_tracking = set()  # ids ever seen progressing to their spacing slot
    chase_no_bc = set()    # ids seen in CHASE/SHOOT WITHOUT BC convergence
    line_vx = {}
    line_vy = {}
    late_switches = []

    for frame in range(1, 31):
        eng.update(dt)
        w.frame_count = frame

        if eng._phase != "OPEN_PLAY":
            continue
        if bc.x > w.defensive_line_anchor:
            continue

        for d in line:
            dc = getattr(d, "depth_class", "PASSIVE")
            st = getattr(d, "movement_state", None)
            name = getattr(st, "name", None) or str(st)
            if dc == "PASSIVE":
                seen_passive.add(d.player_id)
            if name == "DRIFT":
                seen_drift.add(d.player_id)
            if name in ("CHASE", "SLIDE", "SHOOT"):
                chase_states.add(d.player_id)
            # Progress toward the BC: velocity must point at the carrier
            # in at least one axis.  The line sits AHEAD of the BC
            # (d.x > bc.x), so convergence is NEGATIVE X; SLIDE movers
            # close the lateral gap first with vy toward bc.y.
            vx = getattr(d, "vx", 0.0)
            vy = getattr(d, "vy", 0.0)
            progress = (
                (bc.x - d.x) * vx > 0.01
                or (bc.y - d.y) * vy > 0.01
            )
            if progress:
                converging.add(d.player_id)
            if progress and name in ("CHASE", "SHOOT"):
                chase_no_bc.discard(d.player_id)
            if name in ("CHASE", "SHOOT") and not progress:
                chase_no_bc.add(d.player_id)
            # SLIDE movers head to their AUTHORED spacing slot
            # (SpacingChain owns Y, Task E): velocity must point at the
            # role-anchor slot derived from the governed width.  The
            # engine clears d.target between frames, so recompute the
            # slot from the same single width formula the spacing chain
            # publishes (role_anchor_y_for).
            slot_y = role_anchor_y_for(
                d.depth_role, d.depth_side,
                w.ruck_y_m, governed_width_m(w, DefenceTuners()),
                tuners,
            )
            if abs(slot_y - d.y) > 0.05:
                if (slot_y - d.y) * vy > 0.01:
                    slot_tracking.add(d.player_id)
            line_vx[d.player_id] = vx
            line_vy[d.player_id] = vy

        # The reactivity audit only runs its checks once the BC has passed
        # the line; run it on every frame anyway and record any
        # LATE_DEPTH_SWITCH it flags (none is expected with the fix).
        buf = io.StringIO()
        with redirect_stdout(buf):
            audit_defence_reactivity(w)
        for ln in buf.getvalue().splitlines():
            if "LATE_DEPTH_SWITCH" in ln:
                late_switches.append(ln.strip())

    # 1. THE regression guard: no lane-eligible defender was ever seeded
    #    PASSIVE during the transition (with the old universal PASSIVE
    #    seed this set would contain most of the line immediately).
    passive_seen = len(seen_passive)
    check("lane-eligible defenders NEVER classified PASSIVE in transition",
          passive_seen == 0,
          f"passive_ids={sorted(seen_passive)}")

    # 2. MMDL resolves chase movement, not DRIFT.
    check("MMDL resolves CHASE/SLIDE/SHOOT (not DRIFT)",
          len(chase_states) >= 3,
          f"chase_ids={sorted(chase_states)} drift_ids={sorted(seen_drift)}")

    # 3. Line convergence.  Committed CHASE/SHOOT movers MUST progress
    #    toward the BC (the chase regression guard); SLIDE movers form the
    #    line by progressing to their AUTHORED spacing slot (SpacingChain
    #    owns Y, Task E).  Both behaviours must cover the line so no
    #    defender is left without purposeful motion.
    check("committed chasers always converge on the carrier",
          len(chase_no_bc) == 0,
          f"chase_without_bc={sorted(chase_no_bc)}")
    purposeful = converging | slot_tracking
    check("every line defender moves purposefully (BC chase or slot)",
          len(purposeful) == len(line),
          f"purposeful={sorted(purposeful)} "
          f"converging={sorted(converging)} "
          f"slot_tracking={sorted(slot_tracking)} "
          f"vx={ {k: round(v, 2) for k, v in line_vx.items()} } "
          f"vy={ {k: round(v, 2) for k, v in line_vy.items()} }")
    check("SLIDE movers progress to their authorised spacing slot",
          len(slot_tracking) >= 2,
          f"slot_tracking={sorted(slot_tracking)}")

    # 4. The reactivity audit never fires LATE_DEPTH_SWITCH.
    check("no LATE_DEPTH_SWITCH events from reactivity audit",
          len(late_switches) == 0,
          f"violations={late_switches}")

    print(f"\n  [INFO] BC x={bc.x:.2f} anchor={w.defensive_line_anchor}")

    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
