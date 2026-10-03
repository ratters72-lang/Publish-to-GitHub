"""verify_fullback_never_tackler_or_frozen.py — Focused verifier.

Confirms the two fullback corrections:

  1. FULLBACK NEVER FREEZES (apply_phase_invariants)
     The dynamic fullback (``fullback_role_active=True``, always
     ``defender_role = DEF_LINE_READY``) must be unfrozen in EVERY phase —
     including RELEASE and the PTB family — even though DEF_LINE_READY is in
     ``must_be_frozen`` for those phases.  The FULLBACK UNFREEZE block in
     movement_contracts.py is now phase-unconditional.

  2. FULLBACK NEVER A TACKLER (select_tacklers)
     The fullback must never be drafted into tackle_role
     PRIMARY/SECONDARY/THIRD/EDGE, even in pursuit mode where
     ``select_tacklers(world, defenders)`` runs on the FULL defenders list
     (the fullback is excluded from ``line_defenders`` upstream in the
     structured / open-play paths, but NOT in pursuit mode).  It must also
     never be marked ``_tackle_commit_active``.

Run:  python verify_fullback_never_tackler_or_frozen.py
Exit 0 on success; prints a PASS/FAIL line per assertion.
"""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from world.world_state import WorldState, GameSubState
from world.rules.movement_contracts import apply_phase_invariants
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified,
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners


failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def make_defender(pid, x, y, fullback=False, defender_role=None, depth_role=None,
                  speed=8.0, vx=1.0, vy=0.0):
    d = Player(x=x, y=y, player_id=pid)
    d.fullback_role_active = fullback
    d.defender_role = defender_role
    d.depth_role = depth_role
    d.frozen = False
    d.freeze_timer = 0.0
    d.target_x = x
    d.target_y = y
    d.vx = vx
    d.vy = vy
    d.max_speed_mps = speed
    d.max_defensive_speed_mps = 8.3
    d.tackle_role = "PASSIVE"
    d._tackle_commit_active = False
    d.lane_index = 0
    d._prev_lane_index = 0
    d.timing_locked = False
    d.reaction_timer = 0.0
    d.retreat_delay = 0.0
    d.lateral_hesitation = 0.0
    d.step_stagger = 0.0
    d.step_stagger_active = False
    d.is_tackler = False
    return d


def make_world(defending_team, attacking_team=None, ball_carrier=None,
               ruck_x=50.0, ruck_y=35.0):
    w = WorldState()
    w.defending_team = defending_team
    w.attacking_team = attacking_team or []
    w.ball_carrier = ball_carrier
    w.ruck_x_m = ruck_x
    w.ruck_y_m = ruck_y
    w.sub_state = GameSubState.OPEN_PLAY
    w.tackler_ids = set()
    w.primary_tackler_id = None
    w.secondary_tacklers = []
    w.frame_count = 0
    w.dt = 1.0 / 60.0
    w.in_pursuit_mode = False
    w.first_tackle_not_made = False
    w.defence_enabled = True
    w.use_structured_open_play = True
    w._prev_frame_open_play = None
    w.defensive_line_anchor = ruck_x + 10.0
    w.defensive_line_update_enabled = True
    w.defensive_chase_enabled = True
    w.ball_owner = "BC"
    w.ball_x_m = ruck_x
    w.ball_y_m = ruck_y
    w.marker_lock_duration_s = 0.75
    w.marker_freeze_timer = 0.75
    return w


def fullback_tuners():
    """Real DefenceTuners, padded with any missing tackle attributes."""
    t = DefenceTuners()
    for name, val in [
        ("tackle_primary_distance_m", 3.0),
        ("tackle_primary_angle_deg", 45.0),
        ("tackle_primary_min_speed_mps", 1.0),
        ("tackle_secondary_distance_m", 5.0),
        ("tackle_secondary_angle_deg", 60.0),
        ("tackle_secondary_min_speed_mps", 1.0),
        ("tackle_third_distance_m", 7.0),
        ("tackle_third_angle_deg", 75.0),
    ]:
        if not hasattr(t, name):
            setattr(t, name, val)
    return t


def main():
    print("=" * 64)
    print("FULLBACK NEVER-TACKLER / NEVER-FROZEN VERIFIER (2026-09-02)")
    print("=" * 64)

    # ══════════════════════════════════════════════════════════════
    # TEST 1 — apply_phase_invariants never freezes the fullback
    # ══════════════════════════════════════════════════════════════
    print("\n[1] apply_phase_invariants: fullback unfrozen in every phase")
    phases = [
        "OPEN_PLAY", "TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
        "TACKLE_DOMINANCE", "RELEASE", "PTB_PREP", "PTB_EXEC", "PTB",
        "POST_RUCK_OPEN_PLAY",
    ]
    for phase in phases:
        fb = make_defender(1, x=60.0, y=35.0, fullback=True,
                           defender_role=DefenderRole.DEF_LINE_READY)
        world = make_world([fb])
        apply_phase_invariants(world, phase)
        check("[%s] fullback frozen=False" % phase,
              not getattr(fb, "frozen", True),
              "frozen=%s" % getattr(fb, "frozen", None))

    # ══════════════════════════════════════════════════════════════
    # TEST 2 — a non-fullback line defender still freezes in RELEASE/PTB
    #         (the unfreeze must NOT leak to ordinary DEF_LINE_READY bodies)
    # ══════════════════════════════════════════════════════════════
    print("\n[2] ordinary DEF_LINE_READY still freezes in RELEASE/PTB")
    line_def = make_defender(2, x=60.0, y=35.0, fullback=False,
                             defender_role=DefenderRole.DEF_LINE_READY)
    world = make_world([line_def])
    apply_phase_invariants(world, "RELEASE")
    check("2.1 RELEASE non-fullback DEF_LINE_READY frozen=True",
          getattr(line_def, "frozen", False),
          "frozen=%s" % getattr(line_def, "frozen", None))

    world2 = make_world([line_def])
    apply_phase_invariants(world2, "PTB")
    check("2.2 PTB non-fullback DEF_LINE_READY frozen=True",
          getattr(line_def, "frozen", False),
          "frozen=%s" % getattr(line_def, "frozen", None))

    # ══════════════════════════════════════════════════════════════
    # TEST 3 — select_tacklers never drafts the fullback (pursuit path)
    # ══════════════════════════════════════════════════════════════
    print("\n[3] select_tacklers: fullback never PRIMARY/SECONDARY/THIRD/EDGE")
    tuners = fullback_tuners()
    dbu = DefensiveBehaviourUnified(tuners)

    # 3a: fullback is the PHYSICALLY CLOSEST body but must NOT be drafted.
    bc = make_defender(99, x=60.0, y=35.0)
    bc.max_speed_mps = 9.0
    bc.mass = 90.0
    fb = make_defender(101, x=59.0, y=35.0, fullback=True,
                       defender_role=DefenderRole.DEF_LINE_READY, vx=5.0)  # dist 1.0
    # A-band defender; give it high speed + an angle well under 45° so it
    # genuinely lands in the PRIMARY window (dist 2.5 < 3.0, speed 10 > 1,
    # angle ~0° < 45°).
    line_a = make_defender(102, x=57.5, y=35.0, depth_role="A", vx=10.0)   # dist 2.5
    world3 = make_world([fb, line_a], ball_carrier=bc)
    dbu.select_tacklers(world3, [fb, line_a])
    check("3a.1 fullback.tackle_role NOT PRIMARY/SECONDARY/THIRD/EDGE",
          getattr(fb, "tackle_role", "PASSIVE") not in
          ("PRIMARY", "SECONDARY", "THIRD", "EDGE"),
          "tackle_role=%s" % getattr(fb, "tackle_role", None))
    check("3a.2 A-band defender still gets PRIMARY (normal selection intact)",
          getattr(line_a, "tackle_role", None) == "PRIMARY",
          "tackle_role=%s" % getattr(line_a, "tackle_role", None))

    # 3b: fullback + no A-band — old code would draft the fullback into
    #     the primary-window fallback; new code must leave it PASSIVE.
    fb_b = make_defender(111, x=59.0, y=35.0, fullback=True,
                         defender_role=DefenderRole.DEF_LINE_READY)  # dist 1.0
    other = make_defender(112, x=52.0, y=35.0, depth_role="D")      # dist 8.0
    world3b = make_world([fb_b, other], ball_carrier=bc)
    dbu.select_tacklers(world3b, [fb_b, other])
    check("3b.1 fullback (no A-band) NOT PRIMARY/SECONDARY/THIRD/EDGE",
          getattr(fb_b, "tackle_role", "PASSIVE") not in
          ("PRIMARY", "SECONDARY", "THIRD", "EDGE"),
          "tackle_role=%s" % getattr(fb_b, "tackle_role", None))

    # 3c: fullback must never get _tackle_commit_active after select+commit.
    fb_c = make_defender(121, x=59.0, y=35.0, fullback=True,
                         defender_role=DefenderRole.DEF_LINE_READY)
    line_c = make_defender(122, x=52.0, y=35.0, depth_role="D")
    world3c = make_world([fb_c, line_c], ball_carrier=bc)
    dbu.select_tacklers(world3c, [fb_c, line_c])
    # Normalise non-P/S to PASSIVE (mirrors the pursuit path) so only a
    # surviving PRIMARY/SECONDARY would be committed.
    for d in (fb_c, line_c):
        tr = getattr(d, "tackle_role", "PASSIVE")
        if tr not in ("PRIMARY", "SECONDARY"):
            d.tackle_role = "PASSIVE"
    from game.defence.defence_lvl_1.defensive_behaviour_unified import (
        _apply_tackler_commit,
    )
    _apply_tackler_commit(world3c, [fb_c, line_c], tuners)
    check("3c.1 fullback._tackle_commit_active=False",
          not getattr(fb_c, "_tackle_commit_active", False),
          "_tackle_commit_active=%s" % getattr(fb_c, "_tackle_commit_active", None))
    check("3c.2 fullback.is_tackler=False",
          not getattr(fb_c, "is_tackler", False),
          "is_tackler=%s" % getattr(fb_c, "is_tackler", None))

    # ══════════════════════════════════════════════════════════════
    # TEST 4 — fullback excluded from lane-tackler selection too
    # ══════════════════════════════════════════════════════════════
    print("\n[4] select_lane_tacklers: fullback never a PRIMARY lane tackler")
    from game.defence.defence_lvl_1.intercept_tacklers import select_lane_tacklers
    fb4 = make_defender(131, x=59.0, y=35.0, fullback=True,
                        defender_role=DefenderRole.DEF_LINE_READY)
    other4 = make_defender(132, x=58.0, y=35.0, depth_role="B")
    world4 = make_world([fb4, other4], ball_carrier=bc)
    world4.pre_tackle_stance_radius_m = 0.8
    world4.lane_tackler_switch_hysteresis_m = 0.5
    primaries = select_lane_tacklers(world4, [fb4, other4], bc)
    check("4.1 fullback.tackle_role NOT PRIMARY",
          getattr(fb4, "tackle_role", None) != "PRIMARY",
          "tackle_role=%s" % getattr(fb4, "tackle_role", None))

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
