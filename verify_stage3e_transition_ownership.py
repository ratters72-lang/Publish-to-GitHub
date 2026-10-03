"""verify_stage3e_transition_ownership.py - STAGE 3E ownership proof.

Proves the movement-state decision layer honours convergence ownership:

  [1] SOURCE GUARDS - the MMDL decision layer carries the Stage 3E
      convergence-ownership guard (reads world.frame_convergence_targets),
      and the DBU writer exempts dict-owned closers from the LINE-gate
      DRIFT demotion and the lateral-priority blend.
  [2] DICT-OWNED DECISION - a defender present in the published
      frame-convergence dict is decided into a CONVERGENCE-ALIGNED state
      (SHOOT inside the collapse rail / CHASE otherwise).  SLIDE, DRIFT,
      RETREAT and HOLD are unreachable for it - CHASE/SLIDE/DRIFT can
      never override a dict-owned closer.
  [3] NON-DICT BEHAVIOUR PRESERVED - a defender NOT in the dict follows
      the legacy tree exactly as before (PASSIVE deep -> HOLD, PASSIVE ->
      DRIFT, engageable windows unchanged).
  [4] GATING PRECEDENCE - frozen / fullback actors are never dict-owned
      by construction; a TES tackler keeps its current state even when a
      stale dict entry exists (the guard is gated on ``not tes_active``).

Run: python verify_stage3e_transition_ownership.py
"""

import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def main():
    print("=" * 72)
    print("STAGE 3E TRANSITION OWNERSHIP - MOVEMENT STATE vs CONVERGENCE")
    print("=" * 72)

    # --------------------------------------------------------------
    print("")
    print("[1] Source guards")
    mmdl_src = open(
        "game/defence/defence_lvl_1/movement_mode_decision.py",
        encoding="utf-8", errors="replace").read()
    dbu_src = open(
        "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
        encoding="utf-8", errors="replace").read()

    check("decision layer reads the convergence dict",
          "frame_convergence_targets" in mmdl_src)
    check("decision-layer ownership guard gated on not-tes_active",
          'if not getattr(world, "tes_active", False):' in mmdl_src
          and "_conv_map" in mmdl_src)
    check("dict-owned branch returns SHOOT inside collapse rail",
          "return MovementState.SHOOT" in mmdl_src)
    check("dict-owned branch returns CHASE otherwise",
          "return MovementState.CHASE" in mmdl_src)

    check("DBU MMDL block fetches the ownership map once per frame",
          "_mmdl_conv_map = (" in dbu_src)
    check("DBU LINE-gate exempted for dict-owned closers",
          "and not _dict_owned" in dbu_src)
    check("DBU blend bypassed for dict-owned closers",
          "or _dict_owned" in dbu_src)

    # --------------------------------------------------------------
    print("")
    print("[2] Dict-owned decision behaviour")
    from game.managers.convergence_manager import ConvergenceManager
    from world.world_state import WorldState, GamePhase, GameSubState
    from player.player import Player, Team
    from player.movement_states import MovementState
    from game.defence.defence_lvl_1.movement_mode_decision import (
        decide_movement_state,
    )

    def make_defender(pid, x, y, role="PASSIVE", dclass="PASSIVE",
                      frozen=False, fb=False):
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
    world.ruck_x_m = 50.0
    world.defensive_line_anchor = 60.0
    world.in_pursuit_mode = False
    world.use_structured_open_play = True
    world.defence_enabled = True
    world.tes_active = False
    world.sub_state = GameSubState.OPEN_PLAY
    world.phase = GamePhase.OPEN_PLAY

    bc = make_defender(999, 80.0, 35.0)
    bc.possessed = True
    world.ball_carrier = bc

    closers = [
        # PRIMARY lane closer, far behind BC -> CHASE
        make_defender(100, 55.0, 35.0, role="PRIMARY", dclass="PRIMARY"),
        # SECONDARY depth-class closer (lane PASSIVE), far behind -> CHASE
        make_defender(101, 56.0, 38.0, role="PASSIVE", dclass="SECONDARY"),
        # PRIMARY already inside the collapse commit rail -> SHOOT
        make_defender(102, 79.6, 35.0, role="PRIMARY", dclass="PRIMARY"),
    ]
    bystander = make_defender(103, 68.0, 20.0)   # no roles -> NOT dict-owned

    line = closers + [bystander]
    cm = ConvergenceManager(None)
    cm.apply(world, line, bc)
    dct = getattr(world, "frame_convergence_targets", None) or {}
    check("closers published to the dict",
          all(id(d) in dct for d in closers))
    check("bystander excluded from the dict", id(bystander) not in dct)

    s0 = decide_movement_state(world, closers[0], bc, None)
    s1 = decide_movement_state(world, closers[1], bc, None)
    s2 = decide_movement_state(world, closers[2], bc, None)
    sb = decide_movement_state(world, bystander, bc, None)

    check("PRIMARY closer far behind -> CHASE (never SLIDE/DRIFT)",
          s0 == MovementState.CHASE,
          f"got {getattr(s0, 'name', s0)}")
    check("depth-class SECONDARY closer -> CHASE (never SLIDE/DRIFT)",
          s1 == MovementState.CHASE,
          f"got {getattr(s1, 'name', s1)}")
    check("closer inside collapse rail -> SHOOT",
          s2 == MovementState.SHOOT,
          f"got {getattr(s2, 'name', s2)}")

    forbidden = {"SLIDE", "DRIFT", "RETREAT", "HOLD"}
    got_names = {
        "s0": getattr(s0, "name", str(s0)),
        "s1": getattr(s1, "name", str(s1)),
        "s2": getattr(s2, "name", str(s2)),
    }
    check("no SLIDE/DRIFT/RETREAT/HOLD on any dict-owned closer",
          all(n not in forbidden for n in got_names.values()),
          str(got_names))

    # --------------------------------------------------------------
    print("")
    print("[3] Non-dict behaviour preserved")
    # Both fixtures sit PAST the retreat-compliance line (ruck_x=50 +
    # compliance 10 + hysteresis 0.5 => x >= 60.5 required to reach the
    # legacy branches) so mandatory retreat does not shadow the result.
    # Bystander has NO roles -> PASSIVE/PASSIVE, not dict-owned; with
    # tuners absent deep_passive defaults to 15 m: the bystander sits
    # 12 m behind the BC -> DRIFT (legacy branch 3).
    check("non-dict PASSIVE bystander keeps legacy DRIFT branch",
          sb == MovementState.DRIFT,
          f"got {getattr(sb, 'name', sb)}")

    # A deep PASSIVE beyond the hold threshold still holds (legacy):
    # 18 m behind the BC (> 15 m hold threshold) while compliant.
    deep = make_defender(104, 62.0, 35.0)
    sd = decide_movement_state(world, deep, bc, None)
    check("deep PASSIVE holder keeps legacy HOLD branch",
          sd == MovementState.HOLD,
          f"got {getattr(sd, 'name', sd)}")

    # --------------------------------------------------------------
    print("")
    print("[4] Gating precedence")
    # Frozen defenders are never published (Stage 3D C2); assert the
    # publish side again here so the decision-side gate cannot be blamed.
    frozen_d = make_defender(105, 63.0, 36.0, role="PRIMARY",
                             dclass="PRIMARY", frozen=True)
    cm.apply(world, [frozen_d] + line, bc)
    check("frozen defender never enters the dict",
          id(frozen_d) not in getattr(
              world, "frame_convergence_targets", {}))

    # Fullback actor is never published either.
    fb_actor = make_defender(106, 80.0, 35.0, role="PRIMARY",
                             dclass="PRIMARY", fb=True)
    cm.apply(world, [fb_actor] + line, bc)
    check("fullback actor never enters the dict",
          id(fb_actor) not in getattr(
              world, "frame_convergence_targets", {}))

    # TES tackler with a STALE dict entry keeps its current state: the
    # ownership guard must stay gated on ``not tes_active``.
    stale_world = WorldState()
    stale_world.ruck_established = True
    stale_world.ruck_x_m = 50.0
    stale_world.tes_active = True
    stale_world.sub_state = GameSubState.TACKLE_CONTACT
    tackler = make_defender(107, 63.5, 35.4, role="PRIMARY",
                            dclass="PRIMARY")
    tackler.is_tackler = True
    tackler.movement_state = MovementState.RETREAT
    stale_world.frame_convergence_targets = {id(tackler): (66.0, 35.0)}
    st = decide_movement_state(stale_world, tackler, bc, None)
    check("TES tackler keeps TES-owned state despite stale dict entry",
          st == MovementState.RETREAT,
          f"got {getattr(st, 'name', st)}")

    print("")
    print("=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED - movement states honour "
          "convergence ownership")
    sys.exit(0)


if __name__ == "__main__":
    main()
