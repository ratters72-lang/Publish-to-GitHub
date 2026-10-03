"""
verify_retreat_fullback_contracts.py — RETREAT collapse + fullback non-tackle fix.

Proves the AI-agent fix block contracts against the REAL codebase (the
task's ``apply_structured_retreat.py`` / ``movement_state.py`` /
``defensive_line_fullback.py`` map onto defensive_behaviour_unified.py /
movement_mode_decision.py / fullback_role.py):

  1. WIDTH-ELIGIBLE ROLE GATE (contract 1 + 3):
     ``is_width_eligible`` admits exactly the lane-holding depth roles
     (A/B/C/D/CENTRE/WINGER), keeps the structural exemptions (fullback /
     PRIMARY / SECONDARY / markers / frozen), and stays None-safe for
     unseeded bootstrap defenders.  Width X authority never leaks onto
     non-lane actors; RETREAT stays an X-only movement layer for lane
     holders (no Y write in the structured mandatory-retreat writer).

  2. SPACING AUTHORITY FOR RETREAT Y (contract 3):
     The unified / PTB-mode forced-retreat writer's ONLY target_y source
     is the spacing authority (``retreat_spacing_targets`` split chain or
     ``_frame_spacing_y``).  The legacy ``d.y`` current-position fallback
     is gone — RETREAT never carries a non-spacing Y.  The frozen-defender
     pin (``target_y = d.y`` under freeze_timer) is exempt — position is
     owned by the freeze authority, not retreat movement.

  3. FULLBACK NEVER RETREATS (contract 2):
     ``decide_movement_state`` returns DRIFT for the fullback under
     hostile retreat inputs and ``resolve_movement_state`` swallows any
     RETREAT to DRIFT, so no pipeline path (blend / resolve / lock) can
     land the last defender in RETREAT.

  4. FULLBACK TACKLE-ELIGIBILITY SIGNAL (contract "fullback cannot
     tackle"):
     ``assign_fullback_role`` stamps ``can_acquire_tackle = True`` on the
     actor and clears it on de-assignment / kickoff reset, so a tackler
     gate can prove the fullback is never blocked by a RETREAT-state flag.

  5. PIPELINE INTEGRITY (Governor → Spacing → Width →
     Retreat/Close → Finalise → Anchor → MMDL):
     the guards changed no pipeline ordering in defensive_behaviour_unified.py.

Emits verify_retreat_fullback_report.json; exits 0 when all checks pass.
"""

import json
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from player.movement_states import MovementState
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.width_authority import is_width_eligible
from game.defence.defence_lvl_1.movement_mode_decision import (
    decide_movement_state,
    resolve_movement_state,
)
from game.defence.defence_lvl_1.fullback_role import assign_fullback_role

UNIFIED_SRC_PATH = "game/defence/defence_lvl_1/defensive_behaviour_unified.py"

# STAGE 3B: the unified / PTB-mode forced-retreat writer moved into the
# DefenceManager-owned RetreatManager (sole retreat authority).
RMT_SRC_PATH = "game/managers/retreat_manager.py"

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def lane_defender(role, side=1, player_id=1, depth_class="PASSIVE"):
    d = Player(x=70.0, y=35.0 + side * 10.0, player_id=player_id)
    d.depth_role = role
    d.depth_side = side
    d.depth_class = depth_class
    return d


def make_bc(x=55.0, y=35.0):
    return NS(x=x, y=y)


def make_world(ruck_x=30.0, tes_active=False):
    return NS(ruck_x_m=ruck_x, tes_active=tes_active)


# ─────────────────────────────────────────────────────────────────────
# 1. WIDTH-ELIGIBLE ROLE GATE (contracts 1 + 3)
# ─────────────────────────────────────────────────────────────────────
def run_section1():
    print("\n[1] Width-eligible role gate — lane holders only")
    tuners = DefenceTuners()
    for role in ("A", "B", "C", "D", "CENTRE", "WINGER"):
        d = lane_defender(role, player_id=hash(role) % 900 + 1)
        check(f"{role} PASSIVE lane holder width-eligible",
              is_width_eligible(d), f"role={role}")

    # Structural exemptions still hold even with a lane depth_role.
    fb = lane_defender("D", player_id=1)
    fb.fullback_role_active = True
    check("fullback exempt despite lane role", not is_width_eligible(fb))

    primary = lane_defender("A", player_id=2, depth_class="PRIMARY")
    check("PRIMARY closer exempt despite lane role", not is_width_eligible(primary))
    secondary = lane_defender("B", player_id=3, depth_class="SECONDARY")
    check("SECONDARY closer exempt despite lane role", not is_width_eligible(secondary))

    # A non-lane depth role is NOT width-eligible (new role-inclusion gate).
    rogue = lane_defender("D", player_id=4)
    rogue.depth_role = "NOT_A_LANE"
    check("non-lane depth role not width-eligible", not is_width_eligible(rogue))

    # Unseeded bootstrap (depth_role None) stays eligible (recompute "D" default).
    unseeded = Player(x=70.0, y=35.0, player_id=5)
    unseeded.depth_class = "PASSIVE"
    check("unseeded bootstrap defender still eligible", is_width_eligible(unseeded))

    # Width recompute refuses to emit a target for a non-lane role (I5 guard).
    from game.defence.defence_lvl_1.defensive_width import recompute_width_x
    lane_holder = lane_defender("C", player_id=6)
    w = NS(ruck_x_m=60.0, defensive_line_anchor=70.0, anchor_locked=True,
           governor_width_active=False, governor_line_width_m=40.0,
           def_line_width_m=40.0)
    targets = recompute_width_x(w, [rogue, lane_holder], tuners,
                                spacing_targets={})
    check("width recompute refuses non-lane role target",
          id(rogue) not in targets and id(lane_holder) in targets,
          f"targets={sorted(targets)}")


# ─────────────────────────────────────────────────────────────────────
# 2. RETREAT NEVER WRITES Y — structured + unified writers (contract 3)
# ─────────────────────────────────────────────────────────────────────
def run_section2():
    print("\n[2] RETREAT Y authority — spacing consumer, never current-Y")
    src = open(UNIFIED_SRC_PATH, "r", encoding="utf-8", errors="replace").read()

    # 2a. STRUCTURED mandatory-retreat writer (step 9): X-only.
    start_i = src.find("# 9. MANDATORY RETREAT")
    end_i = src.find("# 10. STRUCTURED OPEN PLAY: SAFE CLOSING")
    step9 = src[start_i:end_i]
    check("structured mandatory-retreat block located", start_i != -1 and end_i > start_i)
    check("structured retreat never writes target_y (X-only)",
          "target_y" not in step9,
          "target_y appears inside the step-9 block — RETREAT overwrites Y")
    check("structured retreat loop guards fullback_role_active",
          "fullback_role_active" in step9)
    check("structured retreat stamps MovementState.RETREAT",
          "MovementState.RETREAT" in step9)

    # 2b. UNIFIED / PTB forced-retreat writer (STAGE 3B: owned by the
    # RetreatManager): Y from spacing authority only.
    rmt_src = open(RMT_SRC_PATH, "r", encoding="utf-8", errors="replace").read()
    start_i = rmt_src.find("retreat_spacing_targets = {}")
    end_i = rmt_src.find("Now process only compliant")
    retreat_block = rmt_src[start_i:end_i]
    check("unified forced-retreat block located",
          start_i != -1 and end_i != -1 and end_i > start_i)
    check("unified retreat loop guards fullback_role_active",
          "fullback_role_active" in retreat_block)

    # RETREAT Y-AUTHORITY WRITES.  The only allowed ``target_y =`` lines on
    # the retreat path:
    #   - ``d.target_y = d.y`` — the FROZEN-DEFENDER PIN (freeze_timer hold:
    #     position owned by the freeze authority, not retreat movement).
    #   - ``d.target_y = ty``  — the spacing-authority assignment, where
    #     ``ty`` is defined above from retreat_spacing_targets with a
    #     ``_frame_spacing_y`` fallback.
    # Any other target_y write in the block is a RETREAT Y-authority
    # violation.
    y_lines = [
        line.strip() for line in retreat_block.splitlines()
        if "target_y" in line and "=" in line
        and not line.strip().startswith("#")
    ]

    def _is_freeze_pin(y_line):
        # The freeze pin sits inside the freeze_timer guard, immediately
        # followed by `d.frozen = True`.  Verify by context: it pins to the
        # defender's CURRENT position (d.y), never a spacing value.
        return y_line == "d.target_y = d.y"

    def _is_spacing_assignment(y_line):
        # ``d.target_y = ty`` — confirm the block defines ``ty`` from the
        # spacing authority (retreat_spacing_targets / _frame_spacing_y).
        idx = retreat_block.find("ty = retreat_spacing_targets")
        has_chain = "retreat_spacing_targets.get(id(d))" in retreat_block
        has_fallback = ("_frame_spacing_y(" in retreat_block
                        and "retreat_spacing_targets.get(id(d))" in retreat_block)
        if y_line == "d.target_y = ty":
            return (idx != -1) and (has_chain or has_fallback)
        return False

    bad_y_writes = [
        y_line for y_line in y_lines
        if not (_is_freeze_pin(y_line) or _is_spacing_assignment(y_line))
    ]
    check(
        "every unified-retreat target_y write is freeze-pin or spacing-authority",
        not bad_y_writes,
        f"bad_y_writes={bad_y_writes}",
    )
    check("legacy current-Y fallback removed from unified retreat",
          "retreat_spacing_targets.get(id(d), d.y)" not in retreat_block,
          "found retreat_spacing_targets.get(id(d), d.y) — current-Y fallback")
    check("spacing-authority ty definition present",
          "ty = retreat_spacing_targets" in retreat_block
          and "_frame_spacing_y" in retreat_block,
          "ty must be built from the spacing chain + frame spacing fallback")


# ─────────────────────────────────────────────────────────────────────
# 3. FULLBACK NEVER RETREATS (contract 2)
# ─────────────────────────────────────────────────────────────────────
def seed_line_body(d):
    """Give *d* a healthy lane/depth seed so ``seed_hold`` does not force-hold it.

    ``is_seed_stale`` treats a body with no lane ``depth_role`` / ``depth_side``
    / ``_depth_seeded`` / ``role_depth_target_*`` as stale and routes it to
    HOLD.  A control defender that is held proves nothing about RETREAT, so it
    must be seeded first.
    """
    d.depth_role = "A"
    d.depth_side = 1
    d._depth_seeded = True
    d.role_depth_target_x = float(d.x)
    d.role_depth_target_y = float(d.y)
    d.frozen = False
    d.freeze_timer = 0.0
    d.tackle_state_active = False
    return d


def seal_line_seed(world, d):
    """Stamp the SPACING half of the seed: fresh geometry + this body's Y.

    ``is_seed_stale`` additionally requires ``frame_geometry_ready`` with
    ``frame_geometry_frame == frame_count``, a non-empty
    ``frame_spacing_targets`` dict containing ``id(d)``, and a non-None
    ``d.frame_spacing_y``.
    """
    world.frame_count = getattr(world, "frame_count", 0)
    world.frame_geometry_ready = True
    world.frame_geometry_frame = world.frame_count
    world.frame_spacing_targets = {id(d): float(getattr(d, "y", 35.0))}
    d.frame_spacing_y = float(getattr(d, "y", 35.0))
    return world


def run_section3():
    print("\n[3] Fullback never enters RETREAT (decision + resolve)")

    tuners = DefenceTuners()

    # NO-BREAK regime: ruck_x 100 -> line anchor 110; the carrier at 105 is
    # behind it.  The fullback sits NON-COMPLIANT (x=100 < 110) and far from
    # the carrier in Y, so neither a line break nor tackle-radius proximity
    # applies -- the only correct state is DRIFT.
    world = make_world(ruck_x=100.0)
    bc = make_bc(105.0, 35.0)

    fb = Player(x=100.0, y=20.0, player_id=1)   # non-compliant (100 < 110)
    fb.depth_class = "PASSIVE"
    fb.fullback_role_active = True
    check("fullback stays DRIFT on non-compliant retreat band",
          decide_movement_state(world, fb, bc, tuners) == MovementState.DRIFT)

    # BREAK regime: the SAME actor must now close on the carrier.  The law is
    # "the fullback behaves like any other defender during a break", so CHASE
    # is required here -- and RETREAT is still forbidden.
    world_break = make_world(ruck_x=30.0)
    bc_break = make_bc(55.0, 35.0)
    fb_break = Player(x=35.0, y=35.0, player_id=1)
    fb_break.depth_class = "PASSIVE"
    fb_break.fullback_role_active = True
    check("fullback CHASEs (never RETREATs) once the line is broken",
          decide_movement_state(world_break, fb_break, bc_break, tuners)
          == MovementState.CHASE)

    # CONTROL: an identical non-fullback body must still RETREAT.  Seeded, or
    # the seed-stale lockdown would hold it and prove nothing.
    ctrl = Player(x=100.0, y=20.0, player_id=2)  # identical, not fullback
    ctrl.depth_class = "PASSIVE"
    seed_line_body(ctrl)
    seal_line_seed(world, ctrl)
    check("control non-fullback still RETREATs",
          decide_movement_state(world, ctrl, bc, tuners) == MovementState.RETREAT)

    # resolve_movement_state swallow: even if a locked RETREAT is requested,
    # the fullback resolves to DRIFT.
    fb_res = NS(fullback_role_active=True, movement_state=MovementState.HOLD)
    check("resolve swallows RETREAT to DRIFT for fullback",
          resolve_movement_state(fb_res, MovementState.RETREAT, MovementState.CHASE)
          == MovementState.DRIFT)
    ctrl_res = NS(fullback_role_active=False, movement_state=MovementState.HOLD)
    check("resolve keeps RETREAT for a normal defender",
          resolve_movement_state(ctrl_res, MovementState.RETREAT, MovementState.CHASE)
          == MovementState.RETREAT)

    # resolve_pre_tackle_stuck already exempts the fullback (no-op).
    from game.defence.defence_lvl_1.movement_mode_decision import (
        resolve_pre_tackle_stuck,
    )
    fb_stuck = Player(x=35.0, y=35.0, player_id=1)
    fb_stuck.fullback_role_active = True
    fb_stuck.depth_class = "PASSIVE"
    fb_stuck.target_x = 50.0
    fb_stuck.vx = 0.0
    fb_stuck.vy = 0.0
    check("stuck fallback never assigns RETREAT to fullback",
          resolve_pre_tackle_stuck(world, fb_stuck, MovementState.HOLD)
          == MovementState.HOLD)
    # (``world`` here is the NO-BREAK world from this section; the fullback is
    # exempt from the seed-stale lockdown, so no sealing is required.)


# ─────────────────────────────────────────────────────────────────────
# 4. FULLBACK TACKLE-ELIGIBILITY SIGNAL (contract "fullback can tackle")
# ─────────────────────────────────────────────────────────────────────
def run_section4():
    print("\n[4] Fullback retains tackle acquisition (can_acquire_tackle)")

    fresh = Player(x=70.0, y=35.0, player_id=1)
    check("Player defaults can_acquire_tackle False", fresh.can_acquire_tackle is False)

    p1 = Player(x=70.0, y=35.0, player_id=101)
    p2 = Player(x=80.0, y=35.0, player_id=102)
    p2.frozen = True                     # unavailable -> p1 becomes FB
    ball = NS(x=60.0, y=35.0)

    actor, _ = assign_fullback_role([p1, p2], ball, 60.0)
    check("p1 assigned fullback (p2 frozen)", actor is p1)
    check("p1 stamped can_acquire_tackle on assignment",
          p1.fullback_role_active and p1.can_acquire_tackle is True)

    p2.frozen = False
    p1.frozen = True                     # now p2 deeper -> new FB
    actor2, _ = assign_fullback_role([p1, p2], ball, 60.0)
    check("p2 takes over fullback role", actor2 is p2 and p2.can_acquire_tackle is True)
    check("de-assigned p1 tackle signal cleared",
          not p1.fullback_role_active and p1.can_acquire_tackle is False)

    p1.can_acquire_tackle = True         # simulate a labelled actor
    p1.reset_for_kickoff()
    check("kickoff reset clears can_acquire_tackle",
          p1.can_acquire_tackle is False)


# ─────────────────────────────────────────────────────────────────────
# 5. PIPELINE INTEGRITY — no reordering introduced (contract 3)
# ─────────────────────────────────────────────────────────────────────
def run_section5():
    print("\n[5] Pipeline order preserved (Governor → Spacing → Width → Retreat → Close → Finalise → Anchor → MMDL)")
    src = open(UNIFIED_SRC_PATH, "r", encoding="utf-8", errors="replace").read()
    gov_i = src.find("GOVERNOR - shared line state")
    spacing_i = src.find("SpacingGuard(world, sorted_defs).verify")
    width_i = src.find("WidthGuard(")
    recompute_i = src.find("recompute_width_x(")
    # ANCHOR THE STEP BLOCKS ON THEIR NUMBERED MARKERS (2026-09-18).
    # The bare phrases also appear in the module's pipeline-mapping comments
    # near the top of the file ("# MANDATORY RETREAT, the MMDL step-2 retreat
    # branch", line 169), so a bare find() located the COMMENT, not the CODE,
    # and made the ordering assertion unsatisfiable in every revision.  The
    # numbered markers are the same locators section 2 already uses, and they
    # are unique to the code blocks.
    retreat_i = src.find("# 9. MANDATORY RETREAT")
    close_i = src.find("# 10. STRUCTURED OPEN PLAY: SAFE CLOSING")
    finalise_i = src.find("POST-CATCH DEFENSIVE LINE CLAMP — FINALISE PASS")
    anchor_i = src.find("enforce_retreat_to_anchor(world, sorted_defs)")
    mmdl_i = src.find("MOVEMENT MODE DECISION LAYER (MMDL)")
    order_ok = (
        gov_i != -1 and spacing_i != -1 and width_i != -1
        and recompute_i != -1 and retreat_i != -1 and close_i != -1
        and finalise_i != -1 and anchor_i != -1 and mmdl_i != -1
        and gov_i < spacing_i < recompute_i < width_i
        and recompute_i < retreat_i < close_i < finalise_i < anchor_i < mmdl_i
    )
    check(
        "chain order governor -> spacing -> width -> retreat -> close -> "
        "finalise -> anchor -> MMDL",
        order_ok,
        f"order=[gov:{gov_i}, spacing:{spacing_i}, width:{width_i}, "
        f"recompute:{recompute_i}, retreat:{retreat_i}, close:{close_i}, "
        f"finalise:{finalise_i}, anchor:{anchor_i}, mmdl:{mmdl_i}]",
    )


def main():
    print("=" * 72)
    print("RETREAT COLLAPSE + FULLBACK NON-TACKLE CONTRACT VERIFICATION")
    print("=" * 72)

    run_section1()
    run_section2()
    run_section3()
    run_section4()
    run_section5()

    report = {
        "retreat_fullback_contracts": {
            "width_eligible_role_gate": failures == [],
            "issues_remaining": failures,
        }
    }

    report_path = "verify_retreat_fullback_report.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nReport written to {report_path}")

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
