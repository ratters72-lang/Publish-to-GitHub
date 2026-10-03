"""probe_tes_depthlock_v2.py — runtime probe for the TES DRIVE MOVEMENT contract
(2026-09-10).

Boots a drive stub world and asserts, over N frames, that the drive phases do
NOT seal the defensive line:

  - DEF_LINE_READY / DEF_CHASE (the line players) are NEITHER frozen NOR
    depth-locked during CONTACT / DRIVE / DOMINANCE.  Both axes are live:
    DEPTH (X) tracks the carrier-relative depth target and the LATERAL (Y)
    lane moves with the live spacing target.

  - DEF_RETREAT_10 (the retreat role) is likewise free and keeps moving.

  - Leaving TES (RELEASE) re-freezes the line (it holds for the PTB) and
    leaves DEF_RETREAT_10 free.

The superseded contract (DEPTH-LOCK v2 / DRIVE-DEPTH TRACK) pinned X at drive
entry or hard-froze the line; this probe asserts the replacement contract.

Run:  python probe_tes_depthlock_v2.py     (exit 0 on success)
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from player.movement_states import MovementState
from world.world_state import WorldState
from world.rules.movement_contracts import apply_phase_invariants
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners


def _mk_world(defenders, bc, phase="TACKLE_DRIVE"):
    w = WorldState()
    w.frame_count = 1
    w.defending_team = defenders
    w.attacking_team = []
    w.ball_carrier = bc
    w.tackler_ids = set()
    w.primary_tackler_id = None
    w.secondary_tacklers = []
    w.ruck_x_m = 55.0
    w.ruck_y_m = 35.0
    w.sub_state = phase
    w._ope_phase_label = phase
    w.dt = 1.0 / 60.0
    w.open_play_engine = SimpleNamespace(def_tuners=DefenceTuners())
    w.frame_spacing_targets = {
        id(d): 35.0 + 5.0 * (i + 1) for i, d in enumerate(defenders)
    }
    w.frame_width_targets = {}
    return w


def main():
    print("=" * 64)
    print("TES DRIVE MOVEMENT PROBE (no freeze, no depth pin) 2026-09-10")
    print("=" * 64)

    failures = []

    def check(name, cond, detail=""):
        print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                               (" - " + detail) if detail and not cond else ""))
        if not cond:
            failures.append(name)

    dt = 1.0 / 60.0

    # ── Scenario A: DEF_LINE_READY keeps playing (both axes live) ──
    print("\n[A] DEF_LINE_READY: free in the drive, X tracks carrier, Y tracks lane")
    bc_a = Player(x=60.0, y=35.0, player_id=99)
    line = Player(x=50.0, y=30.0, player_id=2)
    line.defender_role = DefenderRole.DEF_LINE_READY
    line.depth_role = "WINGER"          # offset 10.0 -> bc.x + 10.0
    line.frozen = False
    line.max_defensive_speed_mps = 8.3
    line.move_speed = 4.0
    line.movement_state = MovementState.CHASE
    line._mmdl_moved = False
    line._sd_vx = 0.0
    line._sd_vy = 0.0
    line._geometry_smooth_time_s = 0.12
    line.vx = 0.0
    line.vy = 0.0
    w = _mk_world([line], bc_a)

    apply_phase_invariants(w, "TACKLE_DRIVE")
    check("A.1 DEF_LINE_READY frozen=False in the drive",
          line.frozen is False, "frozen=%s" % line.frozen)
    check("A.2 DEF_LINE_READY depth_locked=False in the drive",
          getattr(line, "depth_locked", False) is False,
          "depth_locked=%s" % getattr(line, "depth_locked", False))
    check("A.3 DEF_LINE_READY depth_lock_x is None (X is live)",
          getattr(line, "depth_lock_x", "SENTINEL") is None,
          "depth_lock_x=%s" % getattr(line, "depth_lock_x", None))
    check("A.4 DEF_LINE_READY depth_lock_y is None (Y is live)",
          getattr(line, "depth_lock_y", "SENTINEL") is None,
          "depth_lock_y=%s" % getattr(line, "depth_lock_y", None))

    x0 = line.x
    y0 = line.y
    xs = []
    ys = []
    for _ in range(30):
        bc_a.x += 0.10                     # carrier drives +X 0.10 m/frame
        line.role_depth_target_x = bc_a.x + 10.0
        line.target_x = line.role_depth_target_x
        line.target_y = 32.0               # live lane target
        line.update(dt)
        xs.append(line.x)
        ys.append(line.y)
    check("A.5 X MOVED toward the carrier-relative depth target",
          line.x > x0, "x0=%.3f x=%.3f" % (x0, line.x))
    check("A.6 X advanced across the window (live depth geometry)",
          (xs[-1] - xs[0]) > 1.0, "span=%.4f" % (xs[-1] - xs[0]))
    check("A.7 Y MOVED toward the live lane target",
          line.y > y0, "y0=%.3f y=%.3f" % (y0, line.y))
    check("A.8 no depth pin written during the drive",
          getattr(line, "depth_lock_x", None) is None,
          "depth_lock_x=%s" % getattr(line, "depth_lock_x", None))
    check("A.9 body never re-frozen mid-drive",
          line.frozen is False, "frozen=%s" % line.frozen)

    # ── Scenario B: DEF_RETREAT_10 free, tracks the carrier ──
    print("\n[B] DEF_RETREAT_10: free in the drive, tracks the carrier depth")
    bc_b = Player(x=60.0, y=35.0, player_id=99)
    retreat = Player(x=50.0, y=30.0, player_id=1)
    retreat.defender_role = DefenderRole.DEF_RETREAT_10
    retreat.depth_role = "D"              # offset 5.0 -> bc.x + 5.0
    retreat.frozen = False
    retreat.max_defensive_speed_mps = 8.3
    retreat.move_speed = 8.3 * 0.5
    retreat.movement_state = MovementState.RETREAT
    retreat._mmdl_moved = False
    retreat._sd_vx = 0.0
    retreat._sd_vy = 0.0
    retreat._geometry_smooth_time_s = 0.12
    retreat.vx = 0.0
    retreat.vy = 0.0
    wb = _mk_world([retreat], bc_b)

    apply_phase_invariants(wb, "TACKLE_DRIVE")
    check("B.1 DEF_RETREAT_10 frozen=False", retreat.frozen is False,
          "frozen=%s" % retreat.frozen)
    check("B.2 DEF_RETREAT_10 depth_locked=False",
          getattr(retreat, "depth_locked", False) is False,
          "depth_locked=%s" % getattr(retreat, "depth_locked", False))
    check("B.3 DEF_RETREAT_10 depth_lock_y is None (Y free)",
          getattr(retreat, "depth_lock_y", "SENTINEL") is None,
          "depth_lock_y=%s" % getattr(retreat, "depth_lock_y", None))

    x_lock0 = retreat.x
    y0b = retreat.y
    lane_y = float(wb.frame_spacing_targets[id(retreat)])
    for _ in range(10):
        bc_b.x += 0.10                     # BC drives +X
        retreat.role_depth_target_x = bc_b.x + 5.0
        retreat.target_x = retreat.role_depth_target_x
        retreat.target_y = lane_y
        retreat.update(dt)
        wb.frame_count += 1
    check("B.4 X tracked forward with the driven carrier",
          retreat.x > x_lock0 + 1e-3, "x=%.4f x0=%.4f" % (retreat.x, x_lock0))
    check("B.5 lateral Y slid toward its lane (moved)",
          retreat.y > y0b, "y=%.4f y0=%.4f" % (retreat.y, y0b))

    # ── Scenario C: RELEASE re-freezes the line, frees the retreat role ──
    print("\n[C] RELEASE re-freezes the line; DEF_RETREAT_10 stays free")
    apply_phase_invariants(wb, "RELEASE")
    check("C.1 DEF_RETREAT_10 unfrozen in RELEASE (wall reforms)",
          retreat.frozen is False, "frozen=%s" % retreat.frozen)
    apply_phase_invariants(w, "RELEASE")
    check("C.2 DEF_LINE_READY frozen in RELEASE (line holds)",
          line.frozen is True, "frozen=%s" % line.frozen)
    check("C.3 DEF_LINE_READY depth-lock cleared in RELEASE",
          getattr(line, "depth_locked", False) is False,
          "depth_locked=%s" % getattr(line, "depth_locked", False))

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL PROBES PASSED — the line and the retreat role keep "
          "moving through TACKLE_CONTACT / TACKLE_DRIVE / TACKLE_DOMINANCE "
          "(both axes live, no freeze, no depth pin); RELEASE re-freezes the "
          "line for the PTB")
    sys.exit(0)


if __name__ == "__main__":
    main()
