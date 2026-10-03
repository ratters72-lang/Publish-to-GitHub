"""
verify_fullback_recover_contract.py — FULLBACK RECOVERY LOCOMOTION (2026-09-21).

Gates the three-part fix for the "fullback structural immobility" defect:

  A. KICKOFF FLIGHT AUTHORITY — ``apply_fullback_geometry`` gains an explicit
     ``kickoff_flight=True`` mode, and ``KickoffEngine.update`` calls it on
     every IN_AIR / CHASE frame, so the airborne fullback is owned by the SAME
     geometry writer that owns it in every other phase.

  B. HIGH-VELOCITY RECOVERY — ``MovementState.RECOVER`` is issued by that
     writer when the actor genuinely owes ground, bypassing the drift /
     backpedal dampeners (target stabiliser, smooth-damp) and the 2.0 m/s
     sweeper approach cap.

  C. ROLE PERSISTENCE — the CHASE/SHOOT movement-state DEMOTION that cleared
     ``fullback_role_active`` is deleted: ``movement_state`` is never a
     role-revocation mechanism.

Sections:
  [1] MovementState.RECOVER table + transition contract.
  [2] Dampener-bypass contract (stabiliser gate + Player fast paths).
  [3] Arming levers (engine stamp + documented env rollback).
  [4] fullback_recovery_active gate matrix (debt / authority / TES / kickoff).
  [5] Approach-cap exemption (RECOVER exempt, DRIFT capped).
  [6] Geometry writer: RECOVER issuance + governed speed; DRIFT when disarmed.
  [7] Kickoff flight mode: anchor source, state, cap, and the non-opted-in
      early return that keeps every other caller byte-identical.
  [8] Source wiring: both engine call sites, both arming seams, reset disarm,
      and the ABSENCE of the deleted demotion.
  [9] Role-persistence law: the tag survives CHASE, and the A-B lever may only
      withdraw tackle candidacy.
"""
import math
import os
import sys

from player.player import Player
from player.movement_states import (
    MovementState,
    ACCEL_BY_STATE,
    DECEL_BY_STATE,
    MAX_SPEED_BY_STATE,
    LATERAL_PRIORITY_BY_STATE,
    VALID_TRANSITIONS,
    is_valid_transition,
)
from player.target_stabiliser import NON_STABILISED_MOVEMENT_STATES
from game.defence.defence_lvl_1 import fullback_role as fb
from game.defence.defence_lvl_1.fullback_role import (
    apply_fullback_geometry,
    assign_fullback_role,
    compute_fullback_anchor,
    fullback_approach_cap_applies,
    fullback_recovery_active,
    fullback_recovery_armed,
    fullback_recovery_speed,
    FB_RECOVER_DEBT_M,
    FB_RECOVER_EXIT_DEBT_M,
    FB_RECOVER_SPEED_SCALE,
    FB_MAX_APPROACH_SPEED_MPS,
    FIELD_CENTRE_Y,
    KICKOFF_FB_LATERAL_OFFSET_M,
)

FAILURES = []


def check(label, condition):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        FAILURES.append(label)


class _Ball:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _World:
    """Minimal world stub carrying the fields the FB authority reads."""

    def __init__(self, anchor, ruck_x, bc, recover_allowed=True):
        self.defensive_line_anchor = anchor
        self.ruck_x_m = ruck_x
        self.ball_carrier = bc
        self.fullback_recover_allowed = recover_allowed


def make_fb(x, y, jersey=1, role_active=True):
    p = Player(x=x, y=y, player_id=200 + jersey)
    p.jersey_number = jersey
    p.max_speed_mps = 9.2
    p.max_defensive_speed_mps = 8.3
    p.fullback_role_active = role_active
    p.tes_attached = False
    p.in_contact = False
    return p


def make_line():
    players = []
    for i in range(5):
        p = Player(x=55.0 + i * 3.0, y=25.0 + i * 5.0, player_id=i + 1)
        p.jersey_number = i + 1
        p.max_speed_mps = 9.2
        p.max_defensive_speed_mps = 8.3
        players.append(p)
    return players


def section(n, title):
    print(f"[{n}] {title}")


def _src(rel_path):
    with open(rel_path, encoding="utf-8") as fh:
        return fh.read()
def sec_recover_state_table():
    section(1, "MovementState.RECOVER — table + transition contract")
    check("RECOVER exists on the enum", hasattr(MovementState, "RECOVER"))
    recover = MovementState.RECOVER
    check("RECOVER.name == 'RECOVER'", recover.name == "RECOVER")
    check("ACCEL_BY_STATE has RECOVER", recover in ACCEL_BY_STATE)
    check("DECEL_BY_STATE has RECOVER", recover in DECEL_BY_STATE)
    check("MAX_SPEED_BY_STATE has RECOVER", recover in MAX_SPEED_BY_STATE)
    check("LATERAL_PRIORITY_BY_STATE has RECOVER", recover in LATERAL_PRIORITY_BY_STATE)
    check("VALID_TRANSITIONS has a RECOVER row", recover in VALID_TRANSITIONS)
    # Self-transitions are handled BY ``is_valid_transition`` (no row lists
    # its own state), so the contract is asserted through the module's own
    # predicate rather than by inspecting the row.
    check(
        "RECOVER self-transition is legal (is_valid_transition)",
        is_valid_transition(recover, recover),
    )
    check(
        "HOLD -> RECOVER is legal (a parked body can start recovering)",
        is_valid_transition(MovementState.HOLD, recover),
    )
    check(
        "every transition out of RECOVER is a legal state",
        all(is_valid_transition(recover, nxt) for nxt in VALID_TRANSITIONS[recover]),
    )
    # RECOVER PACE ALIGNMENT (2026-09-22): the state is documented as the
    # ordinary line-defender slot pace -- the SAME fraction the role writer
    # gives DEF_LINE_READY (``_capped_speed(0.5)``) -- NOT the full defensive
    # ceiling.  The first cut used 1.0 (the pace of a committed tackler) and
    # read in play as "the fullback is far too fast".  These checks pin the
    # alignment so a future tuner cannot silently restore a sprint, and they
    # pin the table EQUAL to FB_RECOVER_SPEED_SCALE so the documented state
    # contract and the effective fullback pace knob can never drift apart.
    recover_scale = MAX_SPEED_BY_STATE[recover]
    chase_scale = MAX_SPEED_BY_STATE.get(MovementState.CHASE)
    slide_scale = MAX_SPEED_BY_STATE.get(MovementState.SLIDE)
    check(
        f"RECOVER max-speed scale {recover_scale} equals the aligned "
        f"FB_RECOVER_SPEED_SCALE ({FB_RECOVER_SPEED_SCALE})",
        abs(recover_scale - FB_RECOVER_SPEED_SCALE) < 1e-9,
    )
    check(
        f"RECOVER scale {recover_scale} is BELOW the CHASE scale {chase_scale} "
        "(a recovering body is not a pursuer)",
        chase_scale is not None and recover_scale < chase_scale,
    )
    check(
        f"RECOVER scale {recover_scale} matches the SLIDE slot scale "
        f"{slide_scale} (ordinary slot-repositioning pace)",
        slide_scale is not None and abs(recover_scale - slide_scale) < 1e-9,
    )
    check(
        f"RECOVER accel {ACCEL_BY_STATE[recover]} is an ordinary "
        f"defender rate, not the SHOOT burst {ACCEL_BY_STATE[MovementState.SHOOT]}",
        ACCEL_BY_STATE[recover] < ACCEL_BY_STATE[MovementState.SHOOT],
    )
    # Every OTHER state must be able to enter RECOVER, otherwise the state
    # could only ever be set by a writer that bypasses the transition table.
    missing = [
        s.name for s in MovementState
        if s is not recover and recover not in VALID_TRANSITIONS.get(s, ())
    ]
    check(f"every state can transition INTO RECOVER (missing={missing})", not missing)


def sec_dampener_bypass():
    section(2, "Dampener bypass — stabiliser gate + Player fast paths")
    check(
        "RECOVER is in NON_STABILISED_MOVEMENT_STATES",
        "RECOVER" in NON_STABILISED_MOVEMENT_STATES,
    )
    check(
        "DRIFT is still stabilised (unchanged contract)",
        "DRIFT" not in NON_STABILISED_MOVEMENT_STATES,
    )
    p = make_fb(60.0, 35.0)
    p.movement_state = MovementState.RECOVER
    check("Player._is_chase_state() True under RECOVER", p._is_chase_state())
    check("Player._uses_smooth_damp() False under RECOVER", p._uses_smooth_damp() is False)
    p.movement_state = MovementState.DRIFT
    check("DRIFT still uses smooth damp (unchanged)", p._uses_smooth_damp() is True)
    check("DRIFT is not a chase state (unchanged)", p._is_chase_state() is False)


def sec_arming_levers():
    section(3, "Arming levers — engine stamp + documented env rollback")
    armed_world = _World(70.0, 60.0, _Ball(62.0, 45.0), recover_allowed=True)
    disarmed_world = _World(70.0, 60.0, _Ball(62.0, 45.0), recover_allowed=False)
    check("armed when world.fullback_recover_allowed is True",
          fullback_recovery_armed(armed_world) is True)
    check("disarmed when the world flag is False",
          fullback_recovery_armed(disarmed_world) is False)
    check("disarmed for a world without the attribute",
          fullback_recovery_armed(object()) is False)

    saved = os.environ.get("RLM_FULLBACK_RECOVER")
    try:
        os.environ["RLM_FULLBACK_RECOVER"] = "0"
        check("RLM_FULLBACK_RECOVER=0 is an A-B rollback (lever forced off)",
              fullback_recovery_armed(armed_world) is False)
        os.environ["RLM_FULLBACK_RECOVER"] = "1"
        check("RLM_FULLBACK_RECOVER=1 leaves the armed world armed",
              fullback_recovery_armed(armed_world) is True)
    finally:
        if saved is None:
            os.environ.pop("RLM_FULLBACK_RECOVER", None)
        else:
            os.environ["RLM_FULLBACK_RECOVER"] = saved


def sec_recovery_gate_matrix():
    section(4, "fullback_recovery_active — gate matrix")
    bc = _Ball(62.0, 45.0)
    world = _World(70.0, 60.0, bc)
    anchor_x, anchor_y = compute_fullback_anchor(bc, 70.0)

    # Debt reference: the FB far from the (deep) anchor.
    far = make_fb(anchor_x - 20.0, anchor_y)
    near = make_fb(anchor_x, anchor_y)

    check(
        "armed + large debt -> RECOVER armed",
        fullback_recovery_active(world, far, anchor_x, anchor_y) is True,
    )
    check(
        "armed + zero debt -> no RECOVER",
        fullback_recovery_active(world, near, anchor_x, anchor_y) is False,
    )
    just_inside = make_fb(anchor_x - (FB_RECOVER_DEBT_M - 0.5), anchor_y)
    check(
        f"armed + debt just under {FB_RECOVER_DEBT_M} m -> no RECOVER",
        fullback_recovery_active(world, just_inside, anchor_x, anchor_y) is False,
    )
    just_outside = make_fb(anchor_x - (FB_RECOVER_DEBT_M + 0.5), anchor_y)
    check(
        f"armed + debt just over {FB_RECOVER_DEBT_M} m -> RECOVER",
        fullback_recovery_active(world, just_outside, anchor_x, anchor_y) is True,
    )

    # Hysteresis: a body already in RECOVER holds it inside the arm band.
    holding = make_fb(anchor_x - (FB_RECOVER_DEBT_M - 1.0), anchor_y)
    holding.movement_state = MovementState.RECOVER
    check(
        "hysteresis: an already-recovering body holds RECOVER below the arm band",
        fullback_recovery_active(world, holding, anchor_x, anchor_y) is True,
    )
    released = make_fb(anchor_x - (FB_RECOVER_EXIT_DEBT_M - 0.5), anchor_y)
    released.movement_state = MovementState.RECOVER
    check(
        f"hysteresis: released once inside the {FB_RECOVER_EXIT_DEBT_M} m exit band",
        fullback_recovery_active(world, released, anchor_x, anchor_y) is False,
    )

    # Untagged bodies never recover.
    untagged = make_fb(anchor_x - 20.0, anchor_y, role_active=False)
    check(
        "untagged defender never recovers",
        fullback_recovery_active(world, untagged, anchor_x, anchor_y) is False,
    )

    # Tackle authority wins: the actor is playing as a defender.
    committing = make_fb(anchor_x - 20.0, anchor_y)
    committing._tackle_commit_active = True
    check(
        "committing actor (tackle authority) never recovers",
        fullback_recovery_active(world, committing, anchor_x, anchor_y) is False,
    )
    lane_stamped = make_fb(anchor_x - 20.0, anchor_y)
    lane_stamped.tackle_role = "PRIMARY"
    check(
        "lane-stamped actor (PRIMARY) never recovers",
        fullback_recovery_active(world, lane_stamped, anchor_x, anchor_y) is False,
    )

    # TES-owned / in-contact bodies are never unpinned into RECOVER.
    attached = make_fb(anchor_x - 20.0, anchor_y)
    attached.tes_attached = True
    check(
        "TES-attached actor never recovers",
        fullback_recovery_active(world, attached, anchor_x, anchor_y) is False,
    )

    # TES phase and kickoff flight keep their own contracts.
    tes_world = _World(70.0, 60.0, bc)
    tes_world.tes_active = True
    check(
        "tes_active world never arms RECOVER",
        fullback_recovery_active(tes_world, make_fb(anchor_x - 20.0, anchor_y),
                                 anchor_x, anchor_y) is False,
    )
    kickoff_world = _World(70.0, 60.0, bc)
    kickoff_world.in_kickoff = True
    check(
        "kickoff flight never arms RECOVER",
        fullback_recovery_active(kickoff_world, make_fb(anchor_x - 20.0, anchor_y),
                                 anchor_x, anchor_y) is False,
    )

    # Disarmed lever: even the far, fully-eligible body stays DRIFT.
    disarmed = _World(70.0, 60.0, bc, recover_allowed=False)
    check(
        "disarmed lever -> no RECOVER regardless of debt",
        fullback_recovery_active(disarmed, make_fb(anchor_x - 20.0, anchor_y),
                                 anchor_x, anchor_y) is False,
    )


def sec_approach_cap():
    section(5, "Approach-speed cap exemption")
    p = make_fb(60.0, 35.0)
    p.movement_state = MovementState.DRIFT
    check("cap applies under DRIFT", fullback_approach_cap_applies(p) is True)
    p.movement_state = MovementState.RECOVER
    check("cap is EXEMPT under RECOVER", fullback_approach_cap_applies(p) is False)
    p.movement_state = MovementState.CHASE
    check("cap still applies under CHASE (unchanged)", fullback_approach_cap_applies(p) is True)
def _fresh_world(frame, recover_allowed=True, bc=None):
    w = _World(70.0, 60.0, bc if bc is not None else _Ball(62.0, 45.0),
               recover_allowed=recover_allowed)
    w.frame_count = frame
    w.tes_active = False
    w.in_kickoff = False
    return w


def sec_geometry_writer():
    section(6, "Geometry writer — RECOVER issuance + governed speed")
    bc = _Ball(62.0, 45.0)
    anchor_x, anchor_y = compute_fullback_anchor(bc, 70.0)

    # (a) Armed + owed ground -> RECOVER at the ALIGNED line-defender pace.
    # The actor sits ON THE CARRIER'S LANE.  Under open-play lateral
    # mirroring the FB's channel IS its own lane, so a body parked 20 m
    # across the field from the carrier is a genuine LATERAL BREAK (pinned
    # separately in (a2)) and the writer rightly hands it to CHASE instead
    # of RECOVER.
    fb_armed = make_fb(55.0, anchor_y)
    apply_fullback_geometry(_fresh_world(1), fb_armed)
    check(
        "armed writer issues MovementState.RECOVER for an owed-ground actor",
        fb_armed.movement_state == MovementState.RECOVER,
    )
    # PACE RE-BASED (2026-09-27).  The writer no longer writes ``move_speed``
    # at all -- it stamps the STATE, and the SHARED movement-state model
    # derives the pace (MAX_SPEED_BY_STATE[state] x max_speed_mps, then the
    # post-facing ``max_defensive_speed_mps`` clamp in Player.update).  The
    # invariant is unchanged -- RECOVER must run at the ALIGNED recovery pace,
    # never the full defensive ceiling and never the retired sweeper cap -- so
    # it is now read off the stamped state.
    # ``expected_pace`` stays the PURE-HELPER reference (the 8.3 m/s defensive
    # ceiling) because the helper checks below feed the helper 8.3 directly.
    # The ACTOR's own expectation is scaled by ITS max_speed_mps, because the
    # shared model derives the actor's pace from that field.
    expected_pace = 8.3 * FB_RECOVER_SPEED_SCALE
    actor_pace = fb_armed.max_speed_mps * FB_RECOVER_SPEED_SCALE
    _armed_pace = (
        fb_armed.max_speed_mps * MAX_SPEED_BY_STATE[fb_armed.movement_state]
    )
    check(
        f"RECOVER runs at the ALIGNED pace ({_armed_pace:.2f} m/s = "
        f"{FB_RECOVER_SPEED_SCALE} x the {fb_armed.max_speed_mps:.1f} m/s "
        f"actor ceiling), not the full "
        f"{fb_armed.max_defensive_speed_mps:.1f} m/s defensive ceiling and "
        f"not the {FB_MAX_APPROACH_SPEED_MPS} m/s sweeper cap",
        abs(_armed_pace - actor_pace) < 1e-9
        and _armed_pace < fb_armed.max_defensive_speed_mps
        and _armed_pace > FB_MAX_APPROACH_SPEED_MPS,
    )
    # NOTE: the helper is called with an error ABOVE FB_DECEL_START_M, so the
    # arrival taper does not apply and the raw scaled pace is visible.  At
    # zero error the 10% floor is the correct answer, not the full pace.
    check(
        "the recovery pace helper agrees with the writer far from the anchor "
        f"({fullback_recovery_speed(8.3, 20.0):.2f} m/s at 20 m error)",
        abs(fullback_recovery_speed(8.3, 20.0) - expected_pace) < 1e-9,
    )
    check(
        "the recovery pace helper floors at 10% of the scaled pace on arrival "
        f"({fullback_recovery_speed(8.3, 0.0):.2f} m/s at zero error)",
        abs(fullback_recovery_speed(8.3, 0.0) - expected_pace * 0.1) < 1e-9,
    )
    check(
        "the recovery pace helper keeps the arrival taper "
        f"({fullback_recovery_speed(8.3, 3.0):.2f} m/s inside 6 m)",
        fullback_recovery_speed(8.3, 3.0) < expected_pace,
    )
    check(
        f"the recovery pace helper never applies the "
        f"{FB_MAX_APPROACH_SPEED_MPS} m/s sweeper cap",
        fullback_recovery_speed(8.3, 100.0) > FB_MAX_APPROACH_SPEED_MPS,
    )
    check("writer still stamps target_source='fullback_geometry'",
          fb_armed.target_source == "fullback_geometry")
    check("writer never leaves the actor frozen", fb_armed.frozen is False)
    check(
        "writer still targets the canonical deep anchor",
        abs(fb_armed.target_x - anchor_x) < 1e-6
        and abs(fb_armed.target_y - anchor_y) < 1e-6,
    )

    # (a2) LATERAL BREAK WINS OVER RECOVER (2026-09-22).  The same owed-ground
    # body, but parked 20 m off the carrier's lane and still inside its 2D
    # reach, is a genuine lateral channel breach.  The break gate owns it, so
    # the writer hands the body to CHASE instead of RECOVER: RECOVER is a
    # RE-RAILING state and never grants tackle authority, whereas the breach
    # path is what arms the emergency defender.
    #
    # ── CHASE PACE, CORRECTED 2026-09-26 ──
    # This block used to assert that the UN-STAMPED lateral chaser "still
    # holds the sweeper pace" -- the 2.0 m/s FB_MAX_APPROACH_SPEED_MPS cap.
    # That was the SAME defect the DEPTH break had, one branch over: every
    # live-break chase was computed through ``fullback_approach_speed``,
    # whose FIRST LINE is ``min(base, 2.0)``, because the CHASE branch's
    # ``fullback_approach_cap_applies`` test is ALWAYS true at that call
    # site -- an authority-active body is diverted into the emergency yield
    # and returns before the anchor branch is ever reached, so the
    # ``else: base_speed`` arm was DEAD CODE.  The last defender could
    # therefore not contest a lateral break any faster than it glides home
    # to its own anchor.
    #
    # The chase now takes the uncapped ``fullback_chase_speed`` contract, so
    # an un-stamped and a lane-stamped chaser BOTH close at the governed
    # defensive pace; ``RLM_FB_CHASE_SPEED=0`` restores the historical
    # expression verbatim (and with it this assertion).  The lane-stamp
    # exemption below is a predicate test and is unaffected.
    fb_lateral = make_fb(55.0, 25.0)
    # 2026-09-27 (LINE-INTACT EXCLUSION): the lateral branch admits a carrier
    # only once it has DRAWN LEVEL with the line -- within
    # ``FB_LATERAL_BREAK_LEVEL_M`` (3.0 m) below the line anchor -- so this
    # fixture needs its OWN carrier.  The ``_fresh_world`` default,
    # (62.0, 45.0), sits 8 m BEHIND the 70.0 anchor and is therefore excluded
    # before the channel test is ever reached; reusing it here made the check
    # assert the RECOVER outcome it exists to rule out.
    #
    # The probe carrier is level with the line (68.0 against a 67.0 floor),
    # still short of the depth trigger (68.0 <= 71.0), 17 m off the
    # sweeper's own lane (|42 - 25| against the 5 m channel) and 21.4 m away
    # -- inside the 25 m 2D reach -- so it is a clean LATERAL breach on every
    # count that does not depend on the level gate.
    apply_fullback_geometry(
        _fresh_world(1, bc=_Ball(68.0, 42.0)), fb_lateral
    )
    check(
        "a lateral channel breach is CHASE, not RECOVER",
        fb_lateral.movement_state == MovementState.CHASE,
    )
    # 2026-09-27: pace is the shared table's, read off the stamped state.
    _lateral_pace = (
        fb_lateral.max_speed_mps
        * MAX_SPEED_BY_STATE[fb_lateral.movement_state]
    )
    check(
        "the un-stamped lateral chaser closes at the governed chase pace, "
        f"not the {FB_MAX_APPROACH_SPEED_MPS} m/s sweeper cap "
        f"({_lateral_pace:.2f} m/s implied by "
        f"{fb_lateral.movement_state.name})",
        _lateral_pace > FB_MAX_APPROACH_SPEED_MPS
        and _lateral_pace <= fb_lateral.max_defensive_speed_mps,
    )
    fb_lateral.tackle_role = "PRIMARY"
    check(
        "a lane-stamped lateral chaser is EXEMPT from the sweeper cap",
        fullback_approach_cap_applies(fb_lateral) is False,
    )

    # (b) Disarmed (the historical contract) -> DRIFT on the 2.0 m/s cap.
    fb_plain = make_fb(55.0, anchor_y)
    apply_fullback_geometry(_fresh_world(1, recover_allowed=False), fb_plain)
    check(
        "disarmed writer keeps the historical DRIFT contract",
        fb_plain.movement_state == MovementState.DRIFT,
    )
    # 2026-09-27: the disarmed sweeper glides on the shared DRIFT row -- below
    # the unified defensive ceiling -- rather than on a bespoke 2.0 m/s write.
    _plain_pace = (
        fb_plain.max_speed_mps * MAX_SPEED_BY_STATE[fb_plain.movement_state]
    )
    check(
        "disarmed writer leaves the sweeper on the shared DRIFT glide, below "
        f"the unified ceiling ({_plain_pace:.2f} < "
        f"{fb_plain.max_defensive_speed_mps:.1f} m/s)",
        _plain_pace < fb_plain.max_defensive_speed_mps,
    )

    # (c) Owed ground but the carrier is past the line -> CHASE still wins.
    fb_break = make_fb(80.0, 35.0)
    world_break = _fresh_world(1)
    world_break.ball_carrier = _Ball(95.0, 35.0)
    apply_fullback_geometry(world_break, fb_break)
    check(
        "breakaway override still wins over RECOVER (CHASE on a line break)",
        fb_break.movement_state == MovementState.CHASE,
    )

    # (d) Tackle authority yields to the collision writer, never RECOVER.
    fb_tackler = make_fb(55.0, 25.0)
    fb_tackler._tackle_commit_active = True
    fb_tackler.target_x = 111.0
    fb_tackler.target_y = 22.0
    fb_tackler.target_source = "tackler_commit"
    apply_fullback_geometry(_fresh_world(1), fb_tackler)
    check(
        "committed tackler is untouched by the recovery writer",
        fb_tackler.target_source == "tackler_commit"
        and abs(fb_tackler.target_x - 111.0) < 1e-9,
    )


def sec_kickoff_flight():
    section(7, "Kickoff flight mode")
    fb_flight = make_fb(55.0, 20.0)
    world = _fresh_world(1, bc=None)
    world.in_kickoff = True
    world.kickoff_engine_line_x = 62.0
    world.kickoff_side_sign = -1
    apply_fullback_geometry(world, fb_flight, kickoff_flight=True)
    expected_y = FIELD_CENTRE_Y - KICKOFF_FB_LATERAL_OFFSET_M
    check(
        "flight anchor X is the kickoff formation line (62.0)",
        abs(fb_flight.target_x - 62.0) < 1e-9,
    )
    check(
        f"flight anchor Y is centre + side_sign * 3.0 ({expected_y})",
        abs(fb_flight.target_y - expected_y) < 1e-9,
    )
    check("flight writer stamps target_source='fullback_geometry'",
          fb_flight.target_source == "fullback_geometry")
    check("flight writer keeps MovementState.DRIFT (no RECOVER in flight)",
          fb_flight.movement_state == MovementState.DRIFT)
    check("flight writer never freezes the actor", fb_flight.frozen is False)
    # 2026-09-27: same re-basing for the kickoff-flight branch.
    _flight_pace = (
        fb_flight.max_speed_mps * MAX_SPEED_BY_STATE[fb_flight.movement_state]
    )
    check(
        "flight writer leaves the sweeper on the shared DRIFT glide, below "
        f"the unified ceiling ({_flight_pace:.2f} < "
        f"{fb_flight.max_defensive_speed_mps:.1f} m/s)",
        _flight_pace < fb_flight.max_defensive_speed_mps,
    )

    # Non-opted-in callers keep the historical early return byte-for-byte.
    fb_plain = make_fb(55.0, 20.0)
    fb_plain.target_x = -5.0
    fb_plain.target_y = -5.0
    fb_plain.target_source = "sentinel"
    apply_fullback_geometry(world, fb_plain)
    check(
        "a normal-play dispatch during flight is still a no-op",
        fb_plain.target_x == -5.0
        and fb_plain.target_y == -5.0
        and fb_plain.target_source == "sentinel",
    )

    # Documented fallback: no published line -> the actor's own X.
    fb_noline = make_fb(57.5, 20.0)
    world_noline = _fresh_world(1, bc=None)
    world_noline.in_kickoff = True
    world_noline.kickoff_side_sign = 1
    apply_fullback_geometry(world_noline, fb_noline, kickoff_flight=True)
    check(
        "flight anchor falls back to the actor's own X when no line is published",
        abs(fb_noline.target_x - 57.5) < 1e-9,
    )
    check(
        "flight anchor Y follows the published side sign (+1)",
        abs(fb_noline.target_y - (FIELD_CENTRE_Y + KICKOFF_FB_LATERAL_OFFSET_M)) < 1e-9,
    )


def sec_source_wiring():
    section(8, "Source wiring — call sites, arming seams, deleted demotion")
    ke = _src("game/kickoff_engine.py")
    dm = _src("game/managers/defence_manager.py")
    fr = _src("game/defence/defence_lvl_1/fullback_role.py")

    check("kickoff_engine imports apply_fullback_geometry",
          "apply_fullback_geometry," in ke)
    check("kickoff_engine defines the flight authority method",
          "def _apply_flight_fullback_geometry" in ke)
    check(
        "kickoff_engine calls the flight authority on BOTH airborne phases",
        ke.count("self._apply_flight_fullback_geometry()") == 2,
    )
    check("kickoff_engine delegates in flight mode (kickoff_flight=True)",
          "kickoff_flight=True" in ke)
    in_air_idx = ke.index("elif self.phase == KOPhase.IN_AIR:")
    chase_idx = ke.index("elif self.phase == KOPhase.CHASE:")
    first_call = ke.index("self._apply_flight_fullback_geometry()")
    last_call = ke.rindex("self._apply_flight_fullback_geometry()")
    check("flight authority runs inside the IN_AIR branch",
          in_air_idx < first_call < chase_idx)
    check("flight authority runs inside the CHASE branch", last_call > chase_idx)

    check("kickoff reset DISARMS the recovery lever",
          "w.fullback_recover_allowed = False" in ke)
    check(
        "defence_manager arms the lever at BOTH live seams",
        dm.count("fullback_recover_allowed = True") == 2,
    )
    check("fullback_role defines the recovery predicate",
          "def fullback_recovery_active" in fr)
    check("fullback_role defines the arming lever reader",
          "def fullback_recovery_armed" in fr)
    check(
        "the CHASE/SHOOT tag demotion is DELETED "
        "(no 'actor.fullback_role_active = False')",
        "actor.fullback_role_active = False" not in fr,
    )
    check(
        "the geometry writer exposes the explicit flight mode",
        "def apply_fullback_geometry(world, defender, kickoff_flight" in fr,
    )


def sec_role_persistence():
    section(9, "Role-persistence law — the tag survives CHASE")
    players = make_line()
    bc = _Ball(62.0, 45.0)
    actor, _anchor = assign_fullback_role(players, bc, 70.0)
    check("a fullback actor is selected", actor is not None)
    check("the selected actor carries the tag", actor.fullback_role_active is True)

    actor.movement_state = MovementState.CHASE
    actor2, _anchor2 = assign_fullback_role(players, bc, 70.0)
    check(
        "re-running the writer on a CHASING actor KEEPS the tag",
        actor2 is not None and actor2.fullback_role_active is True,
    )
    check(
        "the chasing actor is still the same body (sticky designation)",
        actor2 is actor,
    )
    check(
        "the chasing actor holds no line role (depth_role stays None)",
        getattr(actor2, "depth_role", None) is None,
    )

    saved = fb.FULLBACK_ROLE_PERSISTENCE
    try:
        fb.FULLBACK_ROLE_PERSISTENCE = False
        actor3, _anchor3 = assign_fullback_role(players, bc, 70.0)
        check(
            "RLM_FULLBACK_ROLE_PERSISTENCE=0 does NOT demote the tag",
            actor3 is not None and actor3.fullback_role_active is True,
        )
        check(
            "the A-B lever may only WITHDRAW TACKLE CANDIDACY",
            getattr(actor3, "can_acquire_tackle", True) is False,
        )
    finally:
        fb.FULLBACK_ROLE_PERSISTENCE = saved


def main():
    print("FULLBACK RECOVERY LOCOMOTION CONTRACT (2026-09-21)")
    print("=" * 62)
    sec_recover_state_table()
    sec_dampener_bypass()
    sec_arming_levers()
    sec_recovery_gate_matrix()
    sec_approach_cap()
    sec_geometry_writer()
    sec_kickoff_flight()
    sec_source_wiring()
    sec_role_persistence()
    print("=" * 62)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS — RECOVER state, dampener bypass, arming law, flight")
    print("        authority and role persistence all hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
