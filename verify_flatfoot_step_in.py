"""verify_flatfoot_step_in.py — Forced-flatfoot micro-scenario verifier (2026-09-02).

Micro-scenario: ONE defender on a LINE BREAK (BC past the anchor) with
PURSUIT DISABLED, forced into a flat-footed HOLD body hosting a static
target, and asserted to react via the flat-footed step-in block
(``_apply_flatfoot_step_in`` from defensive_behaviour_unified.py):

  A. plants        -> flatfoot_forced_step=True, footplanting=False,
                      pre_tackle_stance=False (FOOTWORK LOCK 2026-09-09:
                      the forced step-in is a CLOSING posture, not a false
                      planted stance)
  B. staggers      -> step_stagger>0, step_stagger_active=True
  C. switches to CHASE -> resolved / movement_state / last_decision == CHASE
  D. clears the reactivity audit -> audit_defence_reactivity emits
     NO BAD_DECISION_ON_LINE_BREAK and NO STATIC_TARGET_AFTER_LINE_BREAK
     for the defender.

Run:  python verify_flatfoot_step_in.py
Exit 0 on success; prints a PASS/FAIL line per assertion.
"""
import io
import sys
from contextlib import redirect_stdout

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from player.movement_states import MovementState
from world.world_state import WorldState, GamePhase, GameSubState
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified,
    _apply_flatfoot_step_in,
)
from game.debug.defence_reactivity_audit import audit_defence_reactivity


failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def make_world(bc_x=64.0, anchor_x=60.0):
    """Bare WorldState: OPEN_PLAY, ruck_x=50 -> anchor=ruck+10=60.

    bc.x = 64 is past anchor+3 (63) so the audit's BAD_DECISION_ON_LINE_BREAK
    check is genuinely in scope (it only fires when bc.x > anchor_x + 3.0).
    """
    w = WorldState()
    w.phase = GamePhase.OPEN_PLAY
    w.sub_state = GameSubState.OPEN_PLAY
    w.use_structured_open_play = True
    w.defence_enabled = True
    w.in_pursuit_mode = False
    w.first_tackle_not_made = False        # mode-invariant: pursuit off => first_not_made off
    w.tes_active = False
    w.defensive_line_anchor = anchor_x
    w.anchor_locked = True
    w.ruck_x_m = 50.0
    w.ruck_y_m = 35.0
    w.def_line_width_m = 40.0
    w.frames_since_ptb = 60                 # >= 30 -> audit post-ruck grace passed
    w.frame_count = 0
    w.dt = 1.0 / 60.0
    bc = Player(x=bc_x, y=35.0, player_id=999)
    w.ball_carrier = bc
    return w, bc


def make_flatfoot_defender(x=50.0, y=35.0, pid=1):
    """Forced flat-footed defender: HOLD body, static target, pursuit disabled.

    - target_x == x  -> zero velocity (_ff_static True)
    - movement_state HOLD -> flat-footed body
    - depth_class PASSIVE -> line holder
    - pursuit disabled: _pursuit_commit_active / _tackle_commit_active False
    """
    d = Player(x=x, y=y, player_id=pid)
    d.is_defender = True
    d.max_defensive_speed_mps = 8.3
    d.depth_class = "PASSIVE"
    d.defender_role = None
    d.fullback_role_active = False
    d.frozen = False
    d.freeze_timer = 0.0
    d.timing_locked = False
    d.reaction_timer = 0.0
    d.retreat_delay = 0.0
    d.lateral_hesitation = 0.0
    d.step_stagger = 0.0
    d.step_stagger_active = False
    d.footplanting = False
    d.pre_tackle_stance = False
    # PURSUIT DISABLED for this defender:
    d._pursuit_commit_active = False
    d._tackle_commit_active = False
    # Flat-footed body: HOLD + static target (target_x == x -> zero velocity)
    d.movement_state = MovementState.HOLD
    d.target_x = x
    d.target_y = y
    d.vx = 0.0
    d.vy = 0.0
    d.last_decision = "HOLD"     # stale step-8 arbiter decision
    return d


def make_relaxed_defender(x, y, pid, claimed=False, role=None):
    """Close-band CHASE body with a MOVED target (flat-footed trigger candidate).

    - movement_state = CHASE
    - target_x != x (moved -> _ff_static False, so the OLD gate would skip it)
    - claimed toggles _pursuit_commit_active (the relaxed 'unclaimed' gate)
    - role sets d.defender_role (e.g. DEF_LINE_READY / DEF_RETREAT_10)
    """
    d = Player(x=x, y=y, player_id=pid)
    d.is_defender = True
    d.max_defensive_speed_mps = 8.3
    d.depth_class = "SECONDARY"
    d.defender_role = role
    d.fullback_role_active = False
    d.frozen = False
    d.freeze_timer = 0.0
    d.timing_locked = False
    d.reaction_timer = 0.0
    d.retreat_delay = 0.0
    d.lateral_hesitation = 0.0
    d.step_stagger = 0.0
    d.step_stagger_active = False
    d.footplanting = False
    d.pre_tackle_stance = False
    d._pursuit_commit_active = claimed
    d._tackle_commit_active = False
    d.movement_state = MovementState.CHASE
    d.target_x = x + 2.0        # moved -> _ff_static False
    d.target_y = y
    d.vx = 0.0
    d.vy = 0.0
    d.last_decision = "CHASE"
    return d


def main():
    print("=" * 64)
    print("FORCED-FLATFOOT STEP-IN MICRO-SCENARIO (2026-09-02)")
    print("=" * 64)

    # ── 1. Set up the micro world: line break (BC past anchor) + one
    #       defender with pursuit disabled and a flat-footed HOLD body ──
    w, bc = make_world(bc_x=64.0, anchor_x=60.0)
    d = make_flatfoot_defender(x=50.0, y=35.0, pid=1)

    tuners = DefenceTuners()
    dbu = DefensiveBehaviourUnified(tuners)
    carrier_past = dbu._is_carrier_past_anchor(w, bc)
    check("scenario gate: BC past anchor (carrier_past)", carrier_past is True,
          "bc.x=%s anchor=%s" % (bc.x, w.defensive_line_anchor))

    # ── 2. Run the REAL flat-footed step-in block ──
    out_state = _apply_flatfoot_step_in(
        w, d, bc, d.movement_state, carrier_past, dbu._def_speed
    )
    # Mirror the MMDL writer's final assignment: d.movement_state = blended_state
    d.movement_state = out_state

    # ── A. Plants (FOOTWORK LOCK 2026-09-09: the forced step-in is a
    #    CLOSING posture, NOT a planted stance — the overloaded
    #    pre_tackle_stance / footplanting flags stay FALSE and the distinct
    #    flatfoot_forced_step marker is set so the machine reads a clean
    #    closing closer, never a false-planted FLATFOOT_FORCED) ──
    check("A1 flatfoot_forced_step set (plant)", d.flatfoot_forced_step is True)
    check("A2 footplanting NOT set (not a plant)", d.footplanting is False)
    check("A2b pre_tackle_stance NOT set (not a plant)",
          d.pre_tackle_stance is False)

    # ── B. Staggers ──
    check("B1 step_stagger seeded (stagger)", d.step_stagger > 0.0,
          "step_stagger=%.3f" % d.step_stagger)
    check("B2 step_stagger_active (stagger)", d.step_stagger_active is True)

    # ── C. Switches to CHASE ──
    check("C1 resolved state CHASE", out_state is MovementState.CHASE,
          "got %s" % (getattr(out_state, "name", out_state)))
    check("C2 movement_state CHASE", d.movement_state is MovementState.CHASE,
          "got %s" % (getattr(d.movement_state, "name", d.movement_state)))
    check("C3 last_decision CHASE (syncs audit)", d.last_decision == "CHASE",
          "got %s" % d.last_decision)
    check("C4 target stepped into contact (target_x moved)",
          abs(d.target_x - d.x) > 0.5,
          "target_x=%.2f x=%.2f" % (d.target_x, d.x))
    check("C5 target_source flat_footed_step_in",
          d.target_source == "flat_footed_step_in",
          "got %s" % d.target_source)

    # ── D. Clears the reactivity audit ──
    w.defending_team = [d]
    buf = io.StringIO()
    with redirect_stdout(buf):
        audit_defence_reactivity(w)
    output = buf.getvalue()
    check("D1 no BAD_DECISION_ON_LINE_BREAK",
          "BAD_DECISION_ON_LINE_BREAK" not in output,
          "audit output:\n%s" % output.strip())
    check("D2 no STATIC_TARGET_AFTER_LINE_BREAK",
          "STATIC_TARGET_AFTER_LINE_BREAK" not in output,
          "audit output:\n%s" % output.strip())

    # ── E. RELAXED 3-5m PROXIMITY TRIGGER (2026-09-02) ──
    # A HOLD/DRIFT (or flat-body) defender OUTSIDE the pursuit/commit claim
    # but within 3-5m of a past-anchor carrier ALWAYS steps in, even when
    # the target is no longer static (CHASE body, moved target).
    print("\n[E] Relaxed 3-5m post-anchor proximity trigger")

    # E1-E4: dist=4.0m, CHASE + moved target + UNCLAIMED -> FIRES
    w2, bc2 = make_world(bc_x=64.0, anchor_x=60.0)
    d2 = make_relaxed_defender(x=60.0, y=35.0, pid=2, claimed=False)
    carrier_past2 = dbu._is_carrier_past_anchor(w2, bc2)
    out2 = _apply_flatfoot_step_in(
        w2, d2, bc2, d2.movement_state, carrier_past2, dbu._def_speed
    )
    check("E1 relaxed 4m unclaimed CHASE+moved -> FIRES (flatfoot marker)",
          d2.flatfoot_forced_step is True and d2.footplanting is False)
    check("E2 relaxed 4m unclaimed CHASE+moved -> FIRES (stagger)",
          d2.step_stagger > 0.0 and d2.step_stagger_active is True)
    check("E3 relaxed 4m unclaimed CHASE+moved -> resolved CHASE",
          out2 is MovementState.CHASE,
          "got %s" % (getattr(out2, "name", out2)))
    check("E4 relaxed 4m unclaimed CHASE+moved -> last_decision CHASE",
          d2.last_decision == "CHASE")

    # E5: dist=6.0m (>5m band) + CHASE + moved + UNCLAIMED -> does NOT fire
    w3, bc3 = make_world(bc_x=64.0, anchor_x=60.0)
    d3 = make_relaxed_defender(x=58.0, y=35.0, pid=3, claimed=False)
    carrier_past3 = dbu._is_carrier_past_anchor(w3, bc3)
    _apply_flatfoot_step_in(
        w3, d3, bc3, d3.movement_state, carrier_past3, dbu._def_speed
    )
    check("E5 relaxed 6m outside band -> does NOT fire",
          d3.flatfoot_forced_step is False)

    # E6: dist=4.0m but CLAIMED (pursuit_commit_active) -> does NOT fire
    d4 = make_relaxed_defender(x=60.0, y=35.0, pid=4, claimed=True)
    _apply_flatfoot_step_in(
        w3, d4, bc3, d4.movement_state, carrier_past3, dbu._def_speed
    )
    check("E6 relaxed 4m but claimed -> does NOT fire",
          d4.flatfoot_forced_step is False)

    # ── F. ROLE-RELAXED TRIGGER for DEF_LINE_READY / DEF_RETREAT_10 ──
    # A spacing-locked DEF_LINE_READY/DEF_RETREAT_10 defender outside the
    # pursuit/commit claim, within <=5m of a past-anchor carrier, ALWAYS
    # steps in instead of spacing-locking (drops the 3m lower bound).
    print("\n[F] Role-relaxed trigger for DEF_LINE_READY / DEF_RETREAT_10")

    # F1/F2: DEF_LINE_READY, dist=4m, unclaimed, CHASE+moved -> FIRES
    w4, bc4 = make_world(bc_x=64.0, anchor_x=60.0)
    d5 = make_relaxed_defender(
        x=60.0, y=35.0, pid=5, claimed=False,
        role=DefenderRole.DEF_LINE_READY,
    )
    carrier_past4 = dbu._is_carrier_past_anchor(w4, bc4)
    out5 = _apply_flatfoot_step_in(
        w4, d5, bc4, d5.movement_state, carrier_past4, dbu._def_speed
    )
    check("F1 DEF_LINE_READY 4m unclaimed -> FIRES (flatfoot marker)",
          d5.flatfoot_forced_step is True and d5.footplanting is False)
    check("F2 DEF_LINE_READY 4m unclaimed -> FIRES (stagger + CHASE)",
          d5.step_stagger > 0.0 and d5.step_stagger_active is True
          and out5 is MovementState.CHASE)

    # F3: DEF_RETREAT_10, dist=2.5m (<3m old floor), unclaimed -> FIRES
    w5, bc5 = make_world(bc_x=64.0, anchor_x=60.0)
    d6 = make_relaxed_defender(
        x=61.5, y=35.0, pid=6, claimed=False,
        role=DefenderRole.DEF_RETREAT_10,
    )
    carrier_past5 = dbu._is_carrier_past_anchor(w5, bc5)
    out6 = _apply_flatfoot_step_in(
        w5, d6, bc5, d6.movement_state, carrier_past5, dbu._def_speed
    )
    check("F3 DEF_RETREAT_10 2.5m unclaimed (<3m) -> FIRES",
          d6.flatfoot_forced_step is True and d6.footplanting is False
          and out6 is MovementState.CHASE)

    # F4: DEF_LINE_READY, dist=6m (>5m), unclaimed -> does NOT fire
    w6, bc6 = make_world(bc_x=64.0, anchor_x=60.0)
    d7 = make_relaxed_defender(
        x=58.0, y=35.0, pid=7, claimed=False,
        role=DefenderRole.DEF_LINE_READY,
    )
    carrier_past6 = dbu._is_carrier_past_anchor(w6, bc6)
    _apply_flatfoot_step_in(
        w6, d7, bc6, d7.movement_state, carrier_past6, dbu._def_speed
    )
    check("F4 DEF_LINE_READY 6m (>5m) -> does NOT fire",
          d7.flatfoot_forced_step is False)

    # F5: DEF_RETREAT_10, dist=4m but CLAIMED -> does NOT fire
    d8 = make_relaxed_defender(
        x=60.0, y=35.0, pid=8, claimed=True,
        role=DefenderRole.DEF_RETREAT_10,
    )
    _apply_flatfoot_step_in(
        w6, d8, bc6, d8.movement_state, carrier_past6, dbu._def_speed
    )
    check("F5 DEF_RETREAT_10 4m but claimed -> does NOT fire",
          d8.flatfoot_forced_step is False)

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
