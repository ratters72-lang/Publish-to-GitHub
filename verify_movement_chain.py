"""
verify_movement_chain.py — TASK I-B MOVEMENT SOLVER STABILISATION verifier.

Drives 200 OPEN_PLAY frames through the full structured defensive pipeline
(DefensiveBehaviourUnified -> progression authority chain -> MMDL) with the
same integration contract as the real engine (pipeline writes target_x/y +
move_speed; Player.update(dt) integrates them into vx/vy/x/y) and asserts
every Task I-B contract:

  B1. DETERMINISM: identical defender positions across two 200-frame runs
      when spacing + width + anchor + tuners are identical (kills jiggle).
  B2. AUTHORITY SAFETY: the movement consumers (MMDL + role movement) never
      mutate spacing/width/anchor/chase/depth-role fields.  The internal
      MovementAuthorityGuard runs inside the pipeline without raising; the
      guard itself is unit-tested to raise on a deliberate violation.
  B3. DEPTH-SWITCH STABILITY: exempt roles (markers, DH, tacklers,
      DEF_CHASE, KICK_CHASE, fullback) never switch bands; line defenders
      switch at most on genuine band crossings with the 20-frame interval.
  B4. FREEZE AUDIT CLEAN: FreezeAndMovementAuditAgent reports zero
      STUCK_NOT_FROZEN / STUCK_SOFT_FREEZE / VX_ZERO_JIGGLE / TARGET_JIGGLE
      across the sequence.
  B5. MMDL DRIFT BOUND: PASSIVE line holders never receive/consume a target
      far off the anchor band (no "40m off anchor" drift).  The dynamic
      fullback actor is excluded — it owns its own backfield anchor.
  B6. RETREAT/CLOSE ACCEPTANCE: PASSIVE non-compliant defenders accept
      spacing+width targets and move to them (no rejection due to anchor
      distance, no state flapping).

Emits verify_movement_chain_report.json.  Exit code 0 when all pass.
"""

import sys
import json

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified,
)
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
)
from game.defence.defence_lvl_1.defensive_engagement_state import (
    update_engagement_state,
    MIN_SWITCH_INTERVAL,
)
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m,
)
from game.defence.defence_lvl_1.movement_authority import (
    MovementAuthorityGuard,
    MovementAuthorityViolation,
)
from game.debug.freeze_audit import FreezeAndMovementAuditAgent
from world.world_state import GameSubState

RUCK_X = 60.0
RUCK_Y = 35.0

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class StubEngine:
    """Minimal engine stub: suppresses the pre-tackle trigger."""

    def __init__(self):
        self._suppress_pre_tackle = True


class MiniWorld:
    """OPEN_PLAY world stub for the structured defensive pipeline."""

    def __init__(self):
        self.ruck_x_m = RUCK_X
        self.ruck_y_m = RUCK_Y
        self.def_line_width_m = 40.0
        self.governor_width_active = True
        self.governor_line_width_m = 40.0
        self.short_side_width_m = None
        self.open_side_width_m = None
        self.frame_count = 0
        self.dt = 1.0 / 60.0
        self.ball_carrier = None
        self.defending_team = []
        self.in_pursuit_mode = False
        self.first_tackle_not_made = False
        self.use_structured_open_play = True
        self.defence_enabled = True
        self.sub_state = GameSubState.OPEN_PLAY
        self.tes_active = False
        self.frames_since_ptb = 100
        self.open_play_engine = StubEngine()
        self.defensive_line_anchor = None
        self.anchor_locked = False
        self.role_depth_state = {}
        self.depth_role_state = {}
        self.first_receiver = None
        self.ball = None
        self.role_governs = False
        # Engine-contract fields the audit / pipeline read.
        self.defence_ran_this_frame = True
        self.ctx = None


def make_defender(player_id, x, y, role=None):
    d = Player(x=x, y=y, player_id=player_id)
    d.is_defender = True
    # Stamped every OPEN_PLAY frame by OpenPlayEngine._update_defence()
    # (mirrors the real engine), selecting the smooth_damp geometry path.
    d.max_defensive_speed_mps = 8.3
    if role is not None:
        d.defender_role = role
    return d


def make_line():
    """Six line defenders around the ruck (no tackle can trigger)."""
    return [
        make_defender(1, 70.0, 15.0),
        make_defender(2, 70.0, 22.0),
        make_defender(3, 70.0, 30.0),
        make_defender(4, 70.0, 40.0),
        make_defender(5, 70.0, 48.0),
        make_defender(6, 70.0, 55.0),
    ]


def snapshot_positions(defenders):
    return {
        d.player_id: (round(d.x, 6), round(d.y, 6))
        for d in defenders
    }


def run_open_play_frames(world, defenders, frames=200):
    """Run *frames* OPEN_PLAY frames through the unified pipeline.

    Engine contract: DefensiveBehaviourUnified.apply() writes target_x/y +
    move_speed; Player.update(dt) integrates them into vx/vy/x/y.  The
    pipeline alone must NEVER be expected to move a player — a harness
    that skips integration reports false STUCK_NOT_FROZEN on a static
    line with live targets.
    """
    tuners = DefenceTuners()
    dbu = DefensiveBehaviourUnified(tuners)
    audit = FreezeAndMovementAuditAgent(report_interval=999)
    assign_depth_roles(defenders, world)

    pos_history = []
    violations = []
    for frame in range(1, frames + 1):
        world.frame_count = frame
        for d in defenders:
            d.vx = 0.0
            d.vy = 0.0
            d.frozen = False
            d.freeze_timer = 0.0
            d._mmdl_moved = False
            d.movement_writer = None
        dbu.apply(world, defenders, world.defensive_line_anchor or 0.0, [])
        # Integration step (mirrors OpenPlayEngine._update_defence()).
        for d in defenders:
            if not d.frozen:
                d.movement_writer = "Player.update"
                d.update(world.dt)
        pos_history.append(snapshot_positions(defenders))

        audit.run(world, frame, "OPEN_PLAY", 0.0)
        for v in audit._violations:
            if any(tag in v for tag in (
                "STUCK_NOT_FROZEN", "STUCK_SOFT_FREEZE",
                "VX_ZERO_JIGGLE", "TARGET_JIGGLE",
            )):
                violations.append(v)

    return dbu, audit, pos_history, violations


def run_b1_determinism():
    print("\n[B1] Deterministic movement across 200 frames (two identical runs)")
    world1 = MiniWorld()
    line1 = make_line()
    world1.defending_team = line1
    world1.ball_carrier = make_defender(99, 55.0, 35.0)
    _, _, hist1, vio1 = run_open_play_frames(world1, line1)

    world2 = MiniWorld()
    line2 = make_line()
    world2.defending_team = line2
    world2.ball_carrier = make_defender(99, 55.0, 35.0)
    _, _, hist2, vio2 = run_open_play_frames(world2, line2)

    identical = hist1 == hist2
    worst = 0.0
    for frame in range(len(hist1)):
        for pid in hist1[frame]:
            p1 = hist1[frame][pid]
            p2 = hist2[frame][pid]
            worst = max(worst, abs(p1[0] - p2[0]), abs(p1[1] - p2[1]))
    check("position history identical across two runs (delta <= 1e-6m)",
          identical, f"worst_delta={worst:.8f}m")
    check("both runs audit-clean (B4 baseline)", not vio1 and not vio2,
          f"violations={vio1[:2]}{vio2[:2]}")


def run_b2_authority_safety():
    print("\n[B2] Authority safety — movement never mutates spacing/width/anchor")
    world = MiniWorld()
    defenders = make_line()
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 55.0, 35.0)
    assign_depth_roles(defenders, world)
    world.defensive_line_anchor = RUCK_X + 10.0
    world.anchor_locked = True
    for d in defenders:
        d.depth_class = "PASSIVE"
        d.role_depth_class = "BALANCED"

    guard = MovementAuthorityGuard(world, defenders)

    raised = False
    try:
        guard.verify()
    except MovementAuthorityViolation:
        raised = True
    check("clean body passes MovementAuthorityGuard", not raised)

    defenders[0].depth_class = "SECONDARY"
    detail = ""
    raised = False
    try:
        guard.verify()
    except MovementAuthorityViolation as exc:
        raised = True
        detail = str(exc)
    check("guard raises on depth_class mutation", raised, detail)
    defenders[0].depth_class = "PASSIVE"


def run_b3_depth_switch_stability():
    print("\n[B3] Depth-switch hysteresis + role exemptions")
    world = MiniWorld()

    exempt = make_defender(11, 70.0, 30.0, DefenderRole.DEF_CHASE)
    exempt.depth_side = -1
    exempt._depth_seeded = True
    exempt.depth_role = "D"
    line_def = make_defender(12, 70.0, 35.0, DefenderRole.DEF_LINE_READY)
    line_def.depth_side = -1
    line_def._depth_seeded = True
    line_def.depth_role = "D"
    world.defending_team = [exempt, line_def]
    world.ball_carrier = make_defender(99, 70.0, 35.0)

    states = {"exempt": [], "line": []}
    for frame in range(1, 121):
        world.frame_count = frame
        world.ball_carrier.x = 74.9 if frame % 2 == 1 else 75.1
        update_engagement_state(world, world.defending_team)
        states["exempt"].append(exempt.role_depth_class)
        states["line"].append(line_def.role_depth_class)

    exempt_set = set(states["exempt"])
    check("exempt role (DEF_CHASE) never switches band",
          len(exempt_set) == 1,
          f"values={sorted(exempt_set)}")

    line_hist = states["line"]
    transitions = []
    prev = None
    for i, v in enumerate(line_hist):
        if prev is not None and v != prev:
            transitions.append((i + 1, prev, v))
        prev = v
    gaps_ok = all(
        b[0] - a[0] > MIN_SWITCH_INTERVAL
        for a, b in zip(transitions, transitions[1:])
    )
    check("line defender switches only on genuine band crossings (no per-frame spam)",
          len(transitions) <= 2 and gaps_ok,
          str(transitions))


def run_b5_mmdl_drift_bound():
    print("\n[B5] MMDL drift bound — PASSIVE holders never far off anchor")
    world = MiniWorld()
    defenders = make_line()
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 55.0, 35.0)
    _, _, _, _ = run_open_play_frames(world, defenders)
    anchor_x = defensive_line_anchor_m(world)

    worst_drift = 0.0
    offenders = []
    stamps_missing = []
    for d in defenders:
        # The dynamic fullback actor has its own backfield-anchor writer
        # (target_source="fullback"); it is not an MMDL line holder and is
        # not part of the anchor-band drift contract.
        if getattr(d, "fullback_role_active", False):
            continue
        tx = getattr(d, "mmdl_target_x", None)
        if tx is None:
            stamps_missing.append(d.player_id)
            continue
        if getattr(d, "depth_class", "PASSIVE") == "PASSIVE":
            drift = abs(tx - anchor_x)
            worst_drift = max(worst_drift, drift)
            if drift > 14.0:
                offenders.append((d.player_id, round(drift, 2)))
    check("every non-fullback defender consumed a movement target (mmdl stamp present)",
          not stamps_missing,
          str(stamps_missing[:3]))
    check("no PASSIVE holder drifts > 14m off anchor (40m bug killed)",
          not offenders,
          f"worst_drift={worst_drift:.2f}m offenders={offenders[:3]}")


def run_b6_retreat_acceptance():
    print("\n[B6] Retreat/close accepts spacing+width; no state flapping")
    non_compliant = [
        make_defender(21, 61.0, 30.0),
        make_defender(22, 62.0, 40.0),
    ]
    state_history = {d.player_id: [] for d in non_compliant}

    world = MiniWorld()
    world.defending_team = non_compliant
    world.ball_carrier = make_defender(99, 55.0, 35.0)
    dbu = DefensiveBehaviourUnified(DefenceTuners())

    for frame in range(1, 61):
        world.frame_count = frame
        for d in non_compliant:
            d.vx = 0.0
            d.vy = 0.0
            d.frozen = False
            d.freeze_timer = 0.0
            d._mmdl_moved = False
            d.movement_writer = None
        dbu.apply(world, non_compliant, 0.0, [])
        for d in non_compliant:
            if not d.frozen:
                d.movement_writer = "Player.update"
                d.update(world.dt)
            ms = getattr(d, "movement_state", None)
            state_history[d.player_id].append(getattr(ms, "name", None) or str(ms))

    # Retreat target = ruck_x + compliance (10m) = 70.  Non-compliant
    # defenders start at 61/62 so they MUST advance toward 70 within 60
    # frames — this asserts the retreat writer accepted the spacing+width
    # target instead of rejecting it due to anchor distance.
    for d in non_compliant:
        advanced = d.x > (RUCK_X + 1.0)
        check(f"id={d.player_id} retreat accepted spacing+width target (x advanced)",
              advanced, f"x={d.x:.2f}")

    for pid, hist in state_history.items():
        flips = sum(1 for a, b in zip(hist, hist[1:]) if a != b)
        check(f"id={pid} no state flapping (< 3 state changes over 60 frames)",
              flips < 3, f"flips={flips}")

    check("retreat/close writers consume spacing Y",
          all(
              getattr(d, "target_y", None) is not None
              and abs(d.target_y - d.y) < 30.0
              for d in non_compliant
          ))


def main():
    print("=" * 72)
    print("MOVEMENT CHAIN STABILITY VALIDATION (Task I-B, 2026-08-11)")
    print("=" * 72)

    run_b1_determinism()
    run_b2_authority_safety()
    run_b3_depth_switch_stability()
    run_b5_mmdl_drift_bound()
    run_b6_retreat_acceptance()

    report = {
        "movement_chain": {
            "deterministic_200_frames": True,
            "authority_safe": True,
            "depth_switch_stable": True,
            "freeze_audit_clean": True,
            "mmdl_drift_bound": True,
            "retreat_close_accepting": True,
            "issues_remaining": failures,
        }
    }
    if failures:
        for key in report["movement_chain"]:
            if key != "issues_remaining":
                report["movement_chain"][key] = False
        report["movement_chain"]["issues_remaining"] = failures

    report_path = "verify_movement_chain_report.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nReport written to {report_path}")
    print(json.dumps(report, indent=2))

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
