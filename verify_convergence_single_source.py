"""verify_convergence_single_source.py - STAGE 3D single-source proof."""

import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from game.managers.convergence_manager import ConvergenceManager, PRIMARY_X_OFFSET_M

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def main():
    print("=" * 72)
    print("CONVERGENCE SINGLE SOURCE (STAGE 3D) - BC-CLOSING AUTHORITY")
    print("=" * 72)

    print("\n[1] Module surface")
    cm = ConvergenceManager(None)
    check("ConvergenceManager constructible", isinstance(cm, ConvergenceManager))
    for fn in ("apply", "read_target", "is_committed"):
        check(f"defines {fn}()", callable(getattr(cm, fn, None)))

    print("\n[2] Publish gating (tackle_role OR depth_class; freeze/FB excluded)")
    from world.world_state import WorldState, GamePhase, GameSubState
    from player.player import Player, Team

    def make_defender(pid, x, y, role="PASSIVE", dclass="PASSIVE", frozen=False, fb=False):
        d = Player(x=x, y=y, player_id=pid)
        d.team = Team.RECEIVING
        d.is_defender = True
        d.defender_role = None
        d.tackle_role = role
        d.depth_class = dclass
        d.frozen = frozen
        d.fullback_role_active = fb
        d.freeze_timer = 0.0
        return d

    world = WorldState()
    world.ruck_established = True
    world.ruck_y_m = 35.0
    world.defensive_line_anchor = 70.0
    world.in_pursuit_mode = False
    world.use_structured_open_play = True
    world.defence_enabled = True
    world.tes_active = False
    world.sub_state = GameSubState.OPEN_PLAY
    world.phase = GamePhase.OPEN_PLAY

    bc = make_defender(999, 65.0, 35.0)
    bc.possessed = True
    world.ball_carrier = bc

    line = [
        make_defender(100, 60.0, 33.0, role="PRIMARY", dclass="PRIMARY"),
        make_defender(101, 60.0, 35.0, role="PASSIVE", dclass="SECONDARY"),
        make_defender(102, 60.0, 37.0, role="PASSIVE", dclass="PASSIVE"),
        make_defender(103, 60.0, 35.0, frozen=True, dclass="PRIMARY"),
        make_defender(104, 60.0, 38.0, fb=True, dclass="SECONDARY"),
    ]
    world.defending_team = line

    cm.apply(world, line, bc)
    dct = getattr(world, "frame_convergence_targets", None) or {}
    check("frame_convergence_targets published", isinstance(dct, dict))

    d0, d1, d2, d3, d4 = line
    check("PRIMARY (tackle_role) in dict", id(d0) in dct)
    check("SECONDARY (depth_class) in dict", id(d1) in dct)
    check("PASSIVE/PASSIVE excluded", id(d2) not in dct)
    check("frozen PRIMARY excluded", id(d3) not in dct)
    check("fullback excluded", id(d4) not in dct)

    print("\n[3] PRIMARY entry value (behind -> bc.x + offset)")
    tx, ty = dct[id(d0)]
    check("PRIMARY tx == bc.x + PRIMARY_X_OFFSET_M (d0.x=60 <= bc.x=65)",
          abs(tx - (bc.x + PRIMARY_X_OFFSET_M)) < 1e-9, f"tx={tx:.2f}")
    check("PRIMARY ty == bc.y", abs(ty - bc.y) < 1e-9, f"ty={ty:.2f}")

    print("\n[4] Source guards - shadow + tackler-commit consume the dict")
    dm_src = open("game/managers/defence_manager.py", encoding="utf-8", errors="replace").read()
    check("shadow publishes the convergence dict",
          "self.run_convergence(world, defenders, bc)" in dm_src)
    # STAGE 3E PRECEDENCE LAW (2026-08-25): during TACKLE_DRIVE/DOMINANCE the
    # convergence dict governs the MOVEMENT STATE (MMDL decides CHASE/SHOOT for
    # dict-owned closers), while the shadow writer is the LAST target authority
    # for non-tackler line defenders and re-anchors EVERY eligible depth-role
    # holder to the live BC.  The old ``id(_s) in _conv_map`` skip left
    # dict-owned closers on stale mmdl_* targets, breaking the shadow contract;
    # it was removed on purpose.  Assert the new law instead.
    check("old conv-skip removed from shadow writer",
          "if id(_s) in _conv_map:" not in dm_src)
    check("shadow still excludes tackler-committed defenders",
          'getattr(_s, "_tackle_commit_active", False)' in dm_src)

    dbu_src = open("game/defence/defence_lvl_1/defensive_behaviour_unified.py",
                   encoding="utf-8", errors="replace").read()
    check("tackler-commit reads the dict",
          "conv_map = getattr(world, \"frame_convergence_targets\", None) or {}" in dbu_src)
    check("tackler-commit prefers the dict entry", "_cpt = conv_map.get(id(d))" in dbu_src)
    check("step-10 structured_close consumes the dict",
          "_conv_map.get(id(d))" in dbu_src)
    check("step-7a engaged non-lane chase consumes the dict",
          dbu_src.count("_conv_map.get(id(d))") >= 2)

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED - convergence is single-source")
    sys.exit(0)


if __name__ == "__main__":
    main()
