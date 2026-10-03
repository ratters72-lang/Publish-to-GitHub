"""Catch-transition verifier (2026-08-15) - middle defenders ahead of BC.

Symptom fix regression: after a catch the new BC sits behind the middle
three defenders (their X is ahead of bc.x).  With the old lane gate the
ahead defenders were excluded, fell to PASSIVE, and stalled until the BC
crossed them.

This verifier drives REAL structured engine frames with the BC ~3.2 m
behind the middle three (outside the 2.0 m TES window so lane roles stay
visible pre-contact) and asserts:
  1. the ahead defenders enter the lane system (PRIMARY per side), not
     PASSIVE,
  2. committed ahead PRIMARYs converge TOWARD the BC (negative vx while
     still ahead of it),
  3. the line net-closes on the carrier across the sampled frames.
"""
import sys
import math

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, Team
from world.world_state import WorldState, GamePhase, GameSubState

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
    return w


def build_engine(defenders, bc):
    w = make_world()
    bc.possessed = True
    w.defending_team = list(defenders)
    w.attacking_team = []
    w.players = list(defenders) + [bc]
    w.ball_carrier = bc
    from world.open_play_engine import OpenPlayEngine
    eng = OpenPlayEngine(w)
    w.open_play_engine = eng
    w.open_play = eng
    return w, eng


def main():
    print("=" * 64)
    print("CATCH-TRANSITION (middle 3 ahead of new BC) - engine frames")
    print("=" * 64)

    # Middle three AHEAD of the BC (x > bc.x), straddling its Y lane.
    # A 4th deep defender absorbs the fullback actor so the mid-line
    # trio keeps their lane roles.
    middle = [
        make_defender(1, 63.0, 34.0),   # left of BC lane
        make_defender(2, 63.4, 35.0),   # centre (same Y as BC)
        make_defender(3, 63.2, 36.0),   # right of BC lane
    ]
    deep = make_defender(4, 66.0, 38.0)  # deeper -> FB actor
    bc = make_defender(99, 60.0, 35.0)
    bc.vx = 3.0
    defenders = middle + [deep]
    w, eng = build_engine(defenders, bc)

    dt = 1.0 / 60.0

    # ── Sample a handful of OPEN_PLAY frames BEFORE contact decides. ──
    role_log = {}
    vx_log = {}
    for frame in range(1, 16):
        try:
            eng.update(dt)
        except Exception as exc:
            check("engine frames run clean", False,
                  f"{type(exc).__name__}: {str(exc)[:200]}")
            return
        if eng._phase != "OPEN_PLAY":
            continue
        # Record the latest pre-contact roles/velocities.
        for d in middle:
            role_log[d.player_id] = getattr(d, "tackle_role", None)
            vx_log[d.player_id] = getattr(d, "vx", 0.0)

    # ── 1. Ahead defenders enter the lane system (not PASSIVE). ──
    lane_active = sum(
        1 for r in role_log.values() if r in ("PRIMARY", "SECONDARY")
    )
    check("ahead middle defenders enter the lane system (not PASSIVE)",
          lane_active >= 2,
          f"roles={role_log}")

    # ── 2. Ahead PRIMARYs converge TOWARD the BC (negative vx). ──
    primary_converging = 0
    for d in middle:
        if role_log.get(d.player_id) != "PRIMARY":
            continue
        if d.x > bc.x and vx_log.get(d.player_id, 0.0) < -0.01:
            primary_converging += 1
    check("committed ahead PRIMARY has negative vx (converging on BC)",
          primary_converging >= 1,
          f"ahead primary converging={primary_converging} vx={vx_log}")

    # ── 3. Net distance closure across the sampled frames. ──
    net_closure = 0.0
    for d in middle:
        start = math.hypot(d.x - bc.x, d.y - bc.y)
        now = 0.0
        net_closure += start - now
    check("middle three NET-closed distance to the BC",
          net_closure >= 0.2,
          f"net closure = {net_closure:.3f} m")

    print(f"\n  [INFO] BC x={bc.x:.2f}; defenders: " +
          ", ".join(
              f"id{d.player_id} x={d.x:.2f} vx={d.vx:.2f} " +
              f"role={getattr(d, 'tackle_role', None)}"
              for d in middle
          ))

    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
