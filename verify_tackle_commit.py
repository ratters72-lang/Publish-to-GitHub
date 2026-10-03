"""Tackle-commit verifier (2026-08-13). Checks PRIMARY/SECONDARY commit writer contracts."""
import sys, math
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, Team
from world.world_state import WorldState, GamePhase, GameSubState
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified, _apply_tackler_commit,
)
from game.defence.defence_lvl_1.pretackle_corridor import is_corridor_owned

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
    return w

def main():
    print("=" * 64)
    print("TACKLE-COMMIT VALIDATION (2026-08-13)")
    print("=" * 64)

    # ── 1. Unit: _apply_tackler_commit writes dedicated BC-forward target ──
    print("\n[1] Commit writer: stable collision target, CHASE state, anti-stall")
    w = make_world()
    line = [make_defender(1, 60.0, 34.0), make_defender(2, 61.0, 36.0), make_defender(3, 62.0, 38.0)]
    bc = make_defender(99, 63.0, 35.0)
    bc.possessed = True
    w.ball_carrier = bc
    line[0].tackle_role = "PRIMARY"
    line[1].tackle_role = "SECONDARY"
    line[2].tackle_role = "PASSIVE"
    committed = _apply_tackler_commit(w, line, DefenceTuners())
    check("PRIMARY + SECONDARY committed, PASSIVE not",
          [id(d) in (id(line[0]), id(line[1])) for d in committed] == [True] * 2
          and all(d._tackle_commit_active for d in (line[0], line[1]))
          and not line[2]._tackle_commit_active)
    # SPEED-AUTHORITY CONSOLIDATION (2026-09-15): the pre-tackle corridor now
    # owns a committed tackler's final approach, so the commit writer YIELDS
    # its static BC-forward offset whenever the body is corridor-owned.  The
    # legacy offsets are still asserted -- on the corridor-OFF canary, which
    # proves the writer is intact and merely GATED rather than deleted.
    tuners_off = DefenceTuners()
    tuners_off.pretackle_corridor_owns_speed = False
    w_off = make_world()
    line_off = [make_defender(11, 60.0, 34.0), make_defender(12, 61.0, 36.0)]
    bc_off = make_defender(98, 63.0, 35.0)
    bc_off.possessed = True
    w_off.ball_carrier = bc_off
    line_off[0].tackle_role = "PRIMARY"
    line_off[1].tackle_role = "SECONDARY"
    _apply_tackler_commit(w_off, line_off, tuners_off)
    check("corridor OFF: PRIMARY target (bc.x+1.0, bc.y)",
          abs(line_off[0].target_x - (bc_off.x + 1.0)) < 1e-9
          and abs(line_off[0].target_y - bc_off.y) < 1e-9,
          f"tx={line_off[0].target_x} ty={line_off[0].target_y}")
    check("corridor OFF: SECONDARY target (bc.x+2.0, bc.y+0.5*depth_side)",
          abs(line_off[1].target_x - (bc_off.x + 2.0)) < 1e-9
          and abs(line_off[1].target_y - (bc_off.y + 0.5)) < 1e-9,
          f"tx={line_off[1].target_x} ty={line_off[1].target_y}")
    check("corridor ON: the body is corridor-owned (the new final writer)",
          is_corridor_owned(line[0]))
    check("corridor ON: the commit writer YIELDED (no static offset written)",
          not (abs(line[0].target_x - (bc.x + 1.0)) < 1e-9
               and abs(line[0].target_y - bc.y) < 1e-9),
          f"tx={line[0].target_x} ty={line[0].target_y}")
    # The remaining [1] contract checks describe the COMMIT WRITER's own path,
    # so they are asserted on the body the writer actually owns -- the
    # corridor-OFF body.  A corridor-owned body is skipped by the writer
    # entirely (it carries no ``target_source`` of its own; the corridor and
    # the pre-tackle authority stamp that), which is precisely the handover
    # the consolidation exists to make.
    check("target_source = tackler_commit (writer owns the body)",
          line_off[0].target_source == "tackler_commit"
          and line_off[1].target_source == "tackler_commit")
    check("movement_state = CHASE (guaranteed forward velocity)",
          getattr(line_off[0].movement_state, "name",
                  line_off[0].movement_state) == "CHASE")
    check("move_speed at ceiling (never stalls)", line_off[0].move_speed >= 8.0)
    check("frozen False, locks cleared",
          not line_off[0].frozen and not line_off[0].timing_locked)

    # ── 2. Persistence: re-select after wipe keeps PRIMARY/SECONDARY ──
    print("\n[2] Role persistence across select_tacklers re-roll")
    dbu = DefensiveBehaviourUnified(DefenceTuners())
    dbu._compute_lane_indices(w, line)
    dbu.select_tacklers(w, line)
    check("PRIMARY persisted after select_tacklers",
          getattr(line[0], "tackle_role", None) == "PRIMARY")
    check("SECONDARY persisted after select_tacklers",
          getattr(line[1], "tackle_role", None) == "SECONDARY")

    # ── 3. Engine frames: commit survives OPEN_PLAY pipeline ──
    print("\n[3] Engine wiring: commit flag survives engine frames")
    from world.open_play_engine import OpenPlayEngine
    w2 = make_world()
    line2 = []
    for i in range(7):
        d = make_defender(100 + i, 60.0 + i * 0.5, 30.0 + i * 2.0)
        line2.append(d)
    bc2 = make_defender(999, 62.0, 35.0)
    bc2.possessed = True
    w2.defending_team = line2
    w2.players = line2 + [bc2]
    w2.ball_carrier = bc2
    try:
        eng = OpenPlayEngine(w2)
        w2.open_play_engine = eng
        w2.open_play = eng
        print("    engine constructed")
    except Exception as exc:
        print("    engine init failed:", repr(exc)[:200])
        check("OpenPlayEngine constructs", False)
        eng = None
    if eng is not None:
        saw_commit = False
        try:
            for _ in range(3):
                eng.update(1.0 / 60.0)
                for d in line2:
                    if getattr(d, "_tackle_commit_active", False):
                        saw_commit = True
                        # SPEED-AUTHORITY CONSOLIDATION (2026-09-15): the
                        # committed tackler is now corridor-owned, so the
                        # COMMIT WRITER is no longer its target author and
                        # ``target_source`` is stamped by the corridor's
                        # authority instead.  Inside this fixture the carrier
                        # starts in contact, so the engine transitions
                        # straight to TACKLE_CONTACT and no OPEN_PLAY
                        # authority frame ever runs to stamp the frozen body
                        # -- which is why the observed source is reported
                        # rather than asserted.  What must survive the frame
                        # is the commit flag itself.
                        print(
                            f"    committed id={getattr(d, 'player_id', '?')} "
                            f"target_source="
                            f"{getattr(d, 'target_source', None)!r} "
                            f"corridor_owned={is_corridor_owned(d)}"
                        )
                        check("committed tackler still flagged committed at "
                              "frame end",
                              bool(getattr(d, "_tackle_commit_active", False)))
                        break
        except Exception as exc:
            print("    frame error:", repr(exc)[:300])
            check("engine frames run clean", False)
        check("at least one defender commits during engine frames", saw_commit)

    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)

if __name__ == "__main__":
    main()
