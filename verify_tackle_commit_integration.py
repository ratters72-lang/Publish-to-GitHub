"""Tackle-commit INTEGRATION verifier (2026-08-13).

Regression for the "tacklers never commit" bug in structured OPEN_PLAY:

  DefensiveBehaviourUnified._apply_structured_open_play step 10b (MMDL)
  commit-skip branch set `d._mmdl_moved = True` on committed
  PRIMARY/SECONDARY tacklers WITHOUT calling apply_movement_state().
  `_mmdl_moved` promises the caller already integrated position; the
  engine's _update_defence() movement loop then SKIPS the committed
  defender's Player.update(dt) entirely — so the tackler froze ~3 m from
  the BC at its (bc.x+1.0 / bc.x+2.0) commit target, never closed the
  2.0 m try_pre_tackle window, and TES never started.

  verify_tackle_commit.py only checked flags/targets — this verifier
  drives the REAL structured engine headless and checks that committed
  tacklers actually MOVE and CLOSE on the BC.

PART 1 (deterministic): a line defender pre-armed PRIMARY at ~2.6 m from
  the BC must ADVANCE X during the next engine frame.  Two defenders are
  used so the tracked one is never absorbed by the fullback role (a lone
  defender IS the fullback, whose writer overrides the commit target —
  harness artifact, not the fix).

PART 2 (natural flow): a compliant line + running BC must produce a
  commit, net closure >= 0.3 m, and a tackle engagement (2.0 m window
  reached OR TES phase entered).  _update_defence must run on every frame
  that ENDS in OPEN_PLAY (the flag is intentionally unset on the frame
  that transitions to TACKLE_INIT — engine contract).
"""
import sys
import math

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, Team
from world.world_state import WorldState, GamePhase, GameSubState

failures = []
warnings = []


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


def part1_deterministic():
    print("\n[PART 1] Pre-armed PRIMARY must advance X under the commit writer")
    # Two defenders: the deeper one (id=2) becomes the fullback; id=1
    # stays on the line and keeps its pre-armed PRIMARY commit role.
    d = make_defender(1, 66.0, 35.0)
    d.vx = 2.0
    d.tackle_role = "PRIMARY"
    d._tackle_commit_active = True
    d2 = make_defender(2, 68.0, 38.0)   # deeper -> fullback actor
    bc = make_defender(99, 68.6, 35.0)
    bc.vx = 2.0
    w, eng = build_engine([d, d2], bc)

    x_before = d.x
    x2_before = d2.x
    try:
        eng.update(1.0 / 60.0)
    except Exception as exc:
        check("PART 1: engine frame runs clean", False,
              f"{type(exc).__name__}: {str(exc)[:200]}")
        return

    x_after = d.x
    dx = x_after - x_before
    check("PART 1: committed line defender ADVANCES X during the frame",
          dx >= 0.02,
          f"x {x_before:.3f} -> {x_after:.3f} (moved {dx:.3f} m)")
    check("PART 1: still committed after the frame",
          getattr(d, "_tackle_commit_active", False)
          and getattr(d, "tackle_role", None) == "PRIMARY",
          f"commit_active={getattr(d, '_tackle_commit_active', False)} "
          f"tackle_role={getattr(d, 'tackle_role', None)}")
    # Fullback actor is NOT a commit target (excluded from line pool).
    if d2.x != x2_before:
        print(f"    [INFO] fullback actor id=2 x {x2_before:.3f} -> {d2.x:.3f}")


def part2_natural_flow():
    print("\n[PART 2] Natural structured OPEN_PLAY flow: commit -> close -> tackle")
    line = [make_defender(1, 66.0, 34.0),
            make_defender(2, 67.0, 36.0),
            make_defender(3, 68.0, 38.0)]
    bc = make_defender(99, 69.5, 35.0)
    bc.vx = 7.0
    bc.vy = 0.0
    w, eng = build_engine(line, bc)

    dt = 1.0 / 60.0
    MAX_FRAMES = 360
    last_exc = None

    # ---- Single pass until TES engages or cap ----
    defence_missed_frames = 0
    open_play_frames = 0
    commit_info = {}   # player_id -> {"frame", "dist_start", "x_start"}

    def freeze_commit(d, frame):
        if d.player_id in commit_info:
            return
        if (getattr(d, "tackle_role", "PASSIVE") in ("PRIMARY", "SECONDARY")
                and getattr(d, "_tackle_commit_active", False)):
            commit_info[d.player_id] = {
                "frame": frame,
                "dist": math.hypot(bc.x - d.x, bc.y - d.y),
                "x": d.x,
            }

    for frame in range(1, MAX_FRAMES + 1):
        try:
            eng.update(dt)
        except Exception as exc:
            last_exc = exc
            print("    frame error:", type(exc).__name__, str(exc)[:250])
            break
        if eng._phase == "OPEN_PLAY":
            open_play_frames += 1
            if not w.defence_ran_this_frame:
                defence_missed_frames += 1
        else:
            # TES engaged (or fallback) — record final captures, stop.
            for d in line:
                freeze_commit(d, frame)
            break
        for d in line:
            freeze_commit(d, frame)

    check("A: _update_defence ran every frame while phase stayed OPEN_PLAY",
          defence_missed_frames == 0,
          f"open_play_frames={open_play_frames} missed={defence_missed_frames}")
    check("A2: enough OPEN_PLAY frames observed", open_play_frames >= 5)

    if not commit_info:
        check("B: at least one defender commits", False,
              "no PRIMARY/SECONDARY commit before TES/error")
        return

    # Closest-start commit is the primary threat.
    pid = min(commit_info, key=lambda k: commit_info[k]["dist"])
    ci = commit_info[pid]
    cp = next(d for d in line if d.player_id == pid)
    dist_end = math.hypot(bc.x - cp.x, bc.y - cp.y)
    closed_m = ci["dist"] - dist_end
    check("B: defender commits (PRIMARY/SECONDARY + _tackle_commit_active)",
          True,
          f"id={pid} role={cp.tackle_role} frame={ci['frame']} "
          f"start_dist={ci['dist']:.2f}")

    check("C: committed defender closes distance to BC (net ≥ 0.3 m)",
          closed_m >= 0.3,
          f"dist {ci['dist']:.2f} -> {dist_end:.2f} (closed {closed_m:.2f} m)")

    reached_tes = eng._phase != "OPEN_PLAY"
    reached_window = dist_end <= 2.0
    check("D: tackle engages (2.0 m try_pre_tackle window reached OR TES)",
          reached_window or reached_tes,
          f"phase={eng._phase} end_dist={dist_end:.2f} window={reached_window} tes={reached_tes}")
    check("E: TES phase entered — full pipeline live",
          reached_tes,
          f"phase={eng._phase}")
    print(f"    [INFO] line {ci['x']:.3f} -> {cp.x:.3f} "
          f"(X moved {cp.x - ci['x']:.3f} m); BC x={bc.x:.2f}")

    if last_exc is not None:
        print(f"  [INFO] last engine exception: {last_exc}")


def main():
    print("=" * 64)
    print("TACKLE-COMMIT INTEGRATION (structured OPEN_PLAY engine frames)")
    print("=" * 64)

    part1_deterministic()
    part2_natural_flow()

    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    if warnings:
        print("WARNINGS:", warnings)
    sys.exit(0)


if __name__ == "__main__":
    main()
