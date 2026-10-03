"""verify_fullback_lateral_priority_exclusion.py — the 0.25 lateral-priority
hypothesis, machine-checked, plus the CONTRACT THAT REPLACED IT.

THE CLAIM UNDER TEST (audit, 2026-09-22): the fullback's Y-axis delta is
flattened inside ``fullback_role.apply_fullback_geometry()`` by the
``LATERAL_PRIORITY_BY_STATE`` entry ``RECOVER = 0.25`` (or by some other
lateral-priority weighting in the sweeper's lateral tracking velocity).

THE VERDICT ON THAT HYPOTHESIS (see FULLBACK_LATERAL_TRACKING_AUDIT.md):

  A. The constant exists and is 0.25, but the table is consumed ONLY by
     ``apply_movement_state`` / ``lerp_movement_state`` / ``get_state_params``
     inside ``player/movement_states.py``, and its SINGLE live engine call
     site iterates a list that EXCLUDES tagged fullbacks by construction.
  B. ``fullback_role.py`` never references the table and
     ``apply_fullback_geometry`` never calls ``apply_movement_state``.
  C. The fullback is excluded from every retreat path, so the OTHER
     lateral-priority table (retreat gait 0.25 / 0.10) is unreachable too.
  H. LIVE: across a seeded engine rollout, the tagged fullback's identity is
     never once handed to ``apply_movement_state`` (the spy is non-vacuous:
     it sees the line bodies every frame).

  The 0.25 table was therefore never the cause.  Sections [1]-[4] and [9]
  still pin that negative result unchanged.

THE DEFECT THE AUDIT *DID* FIND — AND THE CURE PINNED HERE (2026-09-22):

  PACING SUPERSEDED (2026-09-27).  The 2D-distance TAPER described in D/E
  below was the cure for the sweeper's PACE at the time.  That whole bespoke
  speed contract is now RETIRED: the fullback integrates through the SHARED
  movement-state model (player/movement_states.apply_fullback_locomotion), so
  its pace is MAX_SPEED_BY_STATE[state] x max_speed_mps -- the same table
  every line defender's pace comes from, bounded by the same post-facing
  ``max_defensive_speed_mps`` clamp in Player.update.  Sections [5] and [7]
  below therefore pin the helpers, the target error and the STATE as RETAINED
  code and assert the LIVE pace from the shared table; the superseding
  contract is verify_fullback_shared_locomotion.py.  The MMDL-exclusion
  result in sections [1]-[4] and [9]-[10] is unaffected and still holds.

  D. The two speed helpers the sweeper uses took an X-ONLY error.  They now
     take a 2D distance: ``fullback_approach_speed`` and
     ``fullback_recovery_speed`` are ``(base_speed, distance_to_target)``,
     and every call site — the geometry writer, the DBU dispatcher and the
     kickoff chase — feeds them
     ``fullback_target_distance(actor, target_x, target_y)``.  Section [5]
     pins the signature and the metric.
  E. Under the old X-only taper an actor parked at its anchor in X but 8 m
     away in Y was commanded at the 10% floor (0.2 m/s), IDENTICAL to the
     command it got with zero metres of lateral ground owed.  Section [7] now
     pins the INVERSION: the 8 m body is commanded above the floor, and 8 m
     and 0 m of lateral debt receive DIFFERENT commands.
  F. The tracking channel was ``LATERAL_TRACK = 0.6``, a DESTINATION clamp:
     a carrier on the touchline yielded an anchor 14 m from its lane, a gap
     no speed or state could close.  In OPEN PLAY the anchor now mirrors the
     carrier's own lane at FULL weight (``FB_LATERAL_TRACK_OPEN_PLAY = 1.0``).
     Section [6] pins both regimes: the 0.6 rail is the LOOSE-BALL contract
     (``open_play=False``), while the default writer destination is the
     carrier's lane.
  G. The break/step-out gate was X-only.  It is now gated on 2D distance —
     a depth break OR a lateral channel breach, both additionally inside
     ``FB_LATERAL_BREAK_RADIUS_M``.  Section [8] pins that a carrier level
     with the line but outside the sweeper's channel opens the gate inside
     the 2D reach, and does NOT open it beyond the reach.

Sections [1]-[9] are static/stub checks (fast, deterministic).  Section [10]
is the live end-to-end spy, booted the same way
``verify_fullback_recover_live.py`` boots the engine.
"""
import io
import inspect
import math
import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from game.defence.defence_lvl_1 import fullback_role as fb
from game.defence.defence_lvl_1.fullback_role import (
    apply_fullback_geometry,
    compute_fullback_anchor,
    fullback_target_distance,
    FB_DECEL_START_M,
    FB_LATERAL_BREAK_RADIUS_M,
    FB_LATERAL_TRACK_OPEN_PLAY,
    FB_MAX_APPROACH_SPEED_MPS,
    FIELD_CENTRE_Y,
    LATERAL_TRACK,
)
from game.defence.defence_lvl_1.movement_mode_decision import (
    decide_movement_state,
)
from player.player import Player
from player.movement_states import (
    MovementState,
    LATERAL_PRIORITY_BY_STATE,
    MAX_SPEED_BY_STATE,
)

FAILURES = []


def check(label, condition):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        FAILURES.append(label)


def section(n, title):
    print(f"\n[{n}] {title}")


def _src(rel_path):
    with open(rel_path, encoding="utf-8") as fh:
        return fh.read()


# ══════════════════════════════════════════════════════════════════════
# STUB WORLD / ACTORS (mirror verify_fullback_recover_contract.py)
# ══════════════════════════════════════════════════════════════════════

class _Ball:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _World:
    """Minimal world stub carrying the fields the FB authority reads."""

    def __init__(self, anchor=70.0, ruck_x=60.0, bc=None,
                 recover_allowed=False, frame=1):
        self.defensive_line_anchor = anchor
        self.ruck_x_m = ruck_x
        self.ball_carrier = bc
        self.fullback_recover_allowed = recover_allowed
        self.frame_count = frame
        self.tes_active = False
        self.in_kickoff = False


def make_fb(x, y, player_id=201):
    p = Player(x=x, y=y, player_id=player_id)
    p.jersey_number = 1
    p.max_speed_mps = 9.2
    p.max_defensive_speed_mps = 8.3
    p.fullback_role_active = True
    p.tes_attached = False
    p.in_contact = False
    return p


# ══════════════════════════════════════════════════════════════════════
# [1] THE 0.25 PREMISE
# ══════════════════════════════════════════════════════════════════════

def sec_premise():
    section(1, "The 0.25 constant — exists, and is RECOVER's entry")
    table = {s.name: v for s, v in LATERAL_PRIORITY_BY_STATE.items()}
    print(f"        LATERAL_PRIORITY_BY_STATE = {table}")
    check("RECOVER exists on the enum", hasattr(MovementState, "RECOVER"))
    check(
        "LATERAL_PRIORITY_BY_STATE[RECOVER] == 0.25 (the audited constant)",
        abs(LATERAL_PRIORITY_BY_STATE[MovementState.RECOVER] - 0.25) < 1e-9,
    )
    check(
        "the fullback's NORMAL state DRIFT is lateral-DOMINANT (0.8), "
        "not X-dominant",
        LATERAL_PRIORITY_BY_STATE[MovementState.DRIFT] > 0.5,
    )
    check(
        "0.25 belongs to RECOVER only — no other state carries it",
        [s.name for s, v in LATERAL_PRIORITY_BY_STATE.items()
         if abs(v - 0.25) < 1e-9] == ["RECOVER"],
    )


# ══════════════════════════════════════════════════════════════════════
# [2] THE FULLBACK MODULE NEVER TOUCHES THE TABLE OR THE MMDL INTEGRATOR
# ══════════════════════════════════════════════════════════════════════

def sec_fullback_module_is_clean():
    section(2, "fullback_role.py — no lateral-priority read, no MMDL integrate")
    fr = _src("game/defence/defence_lvl_1/fullback_role.py")
    check(
        "fullback_role.py does not reference LATERAL_PRIORITY_BY_STATE",
        "LATERAL_PRIORITY_BY_STATE" not in fr,
    )
    check(
        "fullback_role.py does not reference any 'lateral_priority'",
        "lateral_priority" not in fr,
    )
    check(
        "fullback_role.py never calls apply_movement_state",
        "apply_movement_state" not in fr,
    )
    check(
        "apply_fullback_geometry never writes vx/vy (no per-axis velocity)",
        "defender.vx" not in fr and "defender.vy" not in fr,
    )
    # The writer's own documented integrator contract.
    check(
        "the module documents that _mmdl_moved is never set "
        "(so Player.update integrates the actor)",
        "NEVER sets ``_mmdl_moved``" in fr,
    )
    check(
        "the module names Player.update(dt) as the live integrator",
        "Player.update(dt)" in fr,
    )


# ══════════════════════════════════════════════════════════════════════
# [3] THE ONLY LIVE CALLER ITERATES A FULLBACK-FREE LIST
# ══════════════════════════════════════════════════════════════════════

def sec_only_live_caller():
    section(3, "The single live apply_movement_state() call site")
    dbu = _src("game/defence/defence_lvl_1/defensive_behaviour_unified.py")
    check(
        "DBU imports apply_movement_state by name (module-scope binding)",
        "    apply_movement_state," in dbu,
    )
    # The RAW text count is contaminated by the block's explanatory comments
    # ("apply_movement_state() ..."), so count CALL SITES in executable code
    # only: drop whole-line comments, then count.
    _code = "\n".join(
        ln for ln in dbu.splitlines() if not ln.lstrip().startswith("#")
    )
    check(
        "DBU contains exactly ONE apply_movement_state(...) call in code",
        _code.count("apply_movement_state(") == 1,
    )
    call_idx = _code.index("apply_movement_state(")
    loop_idx = _code.rindex("for d in sorted_defs:", 0, call_idx)
    check(
        "that call is inside the 'for d in sorted_defs' loop",
        loop_idx < call_idx,
    )
    check(
        "sorted_defs is built from line_defenders",
        "sorted_defs = list(line_defenders)" in dbu,
    )
    check(
        "line_defenders excludes every fullback_role_active body",
        'if not getattr(d, "fullback_role_active", False)' in dbu,
    )
    check(
        "DBU states the exclusion outright",
        "the actor is not in sorted_defs" in dbu,
    )
    check(
        "the MMDL loop body carries an explicit fullback guard comment",
        "Fullback / marker / DH / TES tacklers keep their" in dbu,
    )


# ══════════════════════════════════════════════════════════════════════
# [4] NO RETREAT GAIT (THE OTHER 0.25 / 0.10) CAN REACH THE FULLBACK
# ══════════════════════════════════════════════════════════════════════

def sec_retreat_exclusions():
    section(4, "Retreat-gait lateral priorities (0.25 / 0.10) are unreachable")
    check(
        "the retreat gait table carries the OTHER 0.25 (RETREAT_GLIDE)",
        "0.25" in _src("game/defence/defence_lvl_1/retreat_locomotion.py"),
    )
    rm = _src("game/managers/retreat_manager.py")
    check(
        "retreat_manager excludes the fullback (role list build)",
        'and not getattr(d, "fullback_role_active", False)' in rm,
    )
    check(
        "retreat_manager documents the exclusion",
        "the last man never enters RETREAT" in rm,
    )
    ro = _src("game/defence/defence_lvl_1/retreat_obligation.py")
    check(
        "retreat_obligation exempts the fullback",
        'bool(getattr(d, "fullback_role_active", False))' in ro,
    )
    dbu = _src("game/defence/defence_lvl_1/defensive_behaviour_unified.py")
    check(
        "the DBU retreat writer skips the fullback",
        "the fullback never enters\n                # RETREAT" in dbu
        or "the fullback never enters" in dbu,
    )
    mc = _src("world/rules/movement_contracts.py")
    check(
        "movement_contracts exempts the fullback from retreat pins",
        "FULLBACK EXEMPTION" in mc,
    )
    # Behavioural: a real Player with the tag never carries a gait profile
    # after the writers documented above; assert the field is absent by
    # default and only ever set by the retreat authority.
    p = make_fb(70.0, 35.0)
    check(
        "a fresh fullback actor carries no retreat_locomotion profile",
        getattr(p, "retreat_locomotion", None) is None,
    )


# ══════════════════════════════════════════════════════════════════════
# [5] THE SPEED HELPERS CONSUME A 2D DISTANCE (CURED 2026-09-22)
# ══════════════════════════════════════════════════════════════════════

def sec_helper_signatures():
    section(5, "Speed helpers — 2D distance signature (lateral-aware)")
    for name in ("fullback_approach_speed", "fullback_recovery_speed"):
        fn = getattr(fb, name)
        params = list(inspect.signature(fn).parameters)
        print(f"        {name}{tuple(params)}")
        check(
            f"{name} takes (base_speed, distance_to_target) and nothing else",
            params == ["base_speed", "distance_to_target"],
        )
        check(
            f"{name} no longer advertises an X-only error axis",
            "dx" not in " ".join(params) and "dx_to_anchor" not in params,
        )
    # The metric the call sites feed them is 2D, and it grows with the Y error
    # exactly as it grows with the X error -- which is the whole cure.
    on_lane = make_fb(60.0, 35.0)
    dx_only = fullback_target_distance(on_lane, 70.0, 35.0)
    with_lateral = fullback_target_distance(on_lane, 70.0, 45.0)
    print(f"        distance (dx=10, dy= 0) = {dx_only:.3f} m")
    print(f"        distance (dx=10, dy=10) = {with_lateral:.3f} m")
    check(
        "fullback_target_distance is pure X when the target is on the lane",
        abs(dx_only - 10.0) < 1e-9,
    )
    check(
        "fullback_target_distance is the 2D hypot once Y is owed",
        abs(with_lateral - math.hypot(10.0, 10.0)) < 1e-9,
    )
    check(
        "the taper therefore SEES lateral ground: 8 m of Y is not free",
        fb.fullback_approach_speed(8.3, 8.0)
        > fb.fullback_approach_speed(8.3, 0.0),
    )
    check(
        "FB_DECEL_START_M is the taper band (6.0 m)",
        abs(FB_DECEL_START_M - 6.0) < 1e-9,
    )
    check(
        "FB_MAX_APPROACH_SPEED_MPS is the scaler cap (2.0 m/s)",
        abs(FB_MAX_APPROACH_SPEED_MPS - 2.0) < 1e-9,
    )


# ══════════════════════════════════════════════════════════════════════
# [6] THE TRACKING CHANNEL — LOOSE-BALL RAIL vs OPEN-PLAY MIRROR
# ══════════════════════════════════════════════════════════════════════

def sec_channel_clamp():
    section(6, "The tracking channel — loose-ball 0.6 rail vs open-play mirror")
    check(
        f"LATERAL_TRACK is {LATERAL_TRACK} (the LOOSE-BALL rail weight)",
        abs(LATERAL_TRACK - 0.6) < 1e-9,
    )
    check(
        "open play mirrors the carrier lane at FULL weight "
        f"(FB_LATERAL_TRACK_OPEN_PLAY = {FB_LATERAL_TRACK_OPEN_PLAY})",
        abs(FB_LATERAL_TRACK_OPEN_PLAY - 1.0) < 1e-9,
    )

    # ── LOOSE BALL (open_play=False): the historical 0.6 destination clamp.
    ax_left, ay_left = compute_fullback_anchor(
        _Ball(60.0, 0.0), 70.0, open_play=False
    )
    ax_right, ay_right = compute_fullback_anchor(
        _Ball(60.0, 70.0), 70.0, open_play=False
    )
    ax_ctr, ay_ctr = compute_fullback_anchor(
        _Ball(60.0, FIELD_CENTRE_Y), 70.0, open_play=False
    )
    print(f"        loose ball bc.y= 0 -> anchor_y={ay_left:.2f} "
          f"residual={0.0 - ay_left:+.2f} m")
    print(f"        loose ball bc.y=35 -> anchor_y={ay_ctr:.2f}")
    print(f"        loose ball bc.y=70 -> anchor_y={ay_right:.2f} "
          f"residual={70.0 - ay_right:+.2f} m")
    check("loose ball, centred carrier -> anchor on field centre",
          abs(ay_ctr - 35.0) < 1e-9)
    check("loose ball, carrier on y=0 -> anchor 14.0 (the 0.6 rail)",
          abs(ay_left - 14.0) < 1e-9)
    check("loose ball, carrier on y=70 -> anchor 56.0 (the 0.6 rail)",
          abs(ay_right - 56.0) < 1e-9)
    check(
        "the loose-ball corridor is exactly y in [14, 56]",
        abs(ay_left - (FIELD_CENTRE_Y - FIELD_CENTRE_Y * LATERAL_TRACK)) < 1e-9,
    )

    # ── OPEN PLAY (the default): the anchor IS the carrier's own lane.
    ax_m, ay_m0 = compute_fullback_anchor(_Ball(60.0, 0.0), 70.0)
    check("open play, carrier on y=0 -> anchor_y == 0.0 (its own lane)",
          abs(ay_m0 - 0.0) < 1e-9)
    _, ay_m70 = compute_fullback_anchor(_Ball(60.0, 70.0), 70.0)
    check("open play, carrier on y=70 -> anchor_y == 70.0 (its own lane)",
          abs(ay_m70 - 70.0) < 1e-9)

    # And through the WRITER (not just the anchor helper).  The actor sits on
    # the carrier's X but 35 m off its lane, i.e. far outside the 2D break
    # reach, so the gate stays shut and the DRIFT/RECOVER destination is
    # written -- and that destination is now the carrier's lane itself.
    bc = _Ball(62.0, 0.0)
    world = _World(anchor=70.0, bc=bc, frame=11)
    actor = make_fb(ax_ctr, 35.0)
    apply_fullback_geometry(world, actor)
    print(f"        writer target_y={actor.target_y:.2f} "
          f"(carrier lane {bc.y:.2f}, loose-ball rail {ay_left:.2f})")
    check(
        "apply_fullback_geometry writes the CARRIER'S LANE as target_y",
        abs(actor.target_y - bc.y) < 1e-9,
    )
    check(
        "the writer no longer clamps the lane to the 0.6 rail",
        abs(actor.target_y - ay_left) > 13.0,
    )


# ══════════════════════════════════════════════════════════════════════
# [7] THE TAPER IS 2D — LATERAL CLOSURE IS CONSULTED (CURED 2026-09-22)
# ══════════════════════════════════════════════════════════════════════

def sec_x_keyed_starvation():
    section(7, "The 2D taper — lateral closure is consulted")
    bc = _Ball(62.0, 40.0)
    anchor_x, anchor_y = compute_fullback_anchor(bc, 70.0)

    # (a) Parked exactly on the anchor in X, 8 m away in Y.  Under the OLD
    # X-only taper this body got the 10% floor -- identical to a body owing
    # nothing at all.  The distance is now 2D, so 8 m of owed lateral ground
    # is 8 m of owed ground and the actor is commanded ABOVE the floor.
    lat_owed = 8.0
    parked = make_fb(anchor_x, anchor_y + lat_owed, player_id=301)
    apply_fullback_geometry(_World(anchor=70.0, bc=bc, frame=21), parked)
    dx_parked = abs(parked.target_x - parked.x)
    dy_parked = abs(parked.target_y - parked.y)
    _parked_pace = parked.max_speed_mps * MAX_SPEED_BY_STATE.get(
        parked.movement_state, 0.0
    )
    print(f"        parked: |dx|={dx_parked:.2f}  |dy|={dy_parked:.2f}  "
          f"state={parked.movement_state.name}  "
          f"implied pace={_parked_pace:.3f} m/s")
    check(
        "the actor is genuinely at its depth (|dx| == 0) with 8 m of lateral debt",
        dx_parked < 1e-9 and abs(dy_parked - lat_owed) < 1e-6,
    )
    check(
        "the 8 m body's pace is the SHARED table's (and clear of the retired "
        "0.2 m/s floor)",
        parked.movement_state in MAX_SPEED_BY_STATE
        and _parked_pace > FB_MAX_APPROACH_SPEED_MPS * 0.1 + 1e-9,
    )
    check(
        "the 2D taper agrees: 8 m of error outranks 3 m, which is inside "
        "the 6 m band",
        fb.fullback_approach_speed(8.3, lat_owed)
        > fb.fullback_approach_speed(8.3, 3.0),
    )

    # (b) An actor ON the anchor in BOTH axes gets a DIFFERENT command: the
    # zero-distance body sits at the 10% floor.
    centred = make_fb(anchor_x, anchor_y, player_id=302)
    apply_fullback_geometry(_World(anchor=70.0, bc=bc, frame=22), centred)
    _centred_pace = centred.max_speed_mps * MAX_SPEED_BY_STATE.get(
        centred.movement_state, 0.0
    )
    print(f"        centred: |dx|=0.00  |dy|={abs(centred.target_y - centred.y):.2f}  "
          f"state={centred.movement_state.name}  "
          f"implied pace={_centred_pace:.3f} m/s")
    # NEW-CONTRACT FORM (2026-09-27): the writer no longer expresses the Y
    # error as a ``move_speed`` command -- it expresses it as TARGET error and
    # STATE, and the shared table prices the state.  The "the writer is not
    # blind to the Y error" invariant is therefore asserted exactly that way:
    # the parked body is handed 8 m of live lateral target error and the
    # centred body 0 m, and BOTH receive a state the shared table can price.
    check(
        "8 m of lateral debt and 0 m of lateral debt produce DIFFERENT target "
        "errors (the writer is not blind to the Y error)",
        abs(dy_parked - lat_owed) < 1e-6
        and abs(centred.target_y - centred.y) < 1e-6,
    )
    check(
        "both bodies carry a movement state the shared table prices "
        "(the pace is no longer the writer's at all)",
        parked.movement_state in MAX_SPEED_BY_STATE
        and centred.movement_state in MAX_SPEED_BY_STATE,
    )

    # (c) The taper follows the 2D distance: the same X error with different
    # lateral errors produces different speeds -- impossible under the old
    # X-only signature, which had no Y term to vary.
    speed_zero_y = fb.fullback_approach_speed(8.3, math.hypot(0.0, 0.0))
    speed_big_y = fb.fullback_approach_speed(8.3, math.hypot(0.0, 12.0))
    print(f"        |dx|=0 |dy|= 0 -> {speed_zero_y:.3f} m/s   "
          f"|dx|=0 |dy|=12 -> {speed_big_y:.3f} m/s")
    check(
        "the same X error with 12 m of lateral error outruns the 0 m case",
        speed_big_y > speed_zero_y + 1e-9,
    )
    check(
        "the taper floor is reached at ZERO 2D distance (0.2 m/s, i.e. 10%)",
        abs(fb.fullback_approach_speed(8.3, 0.0) - 0.2) < 1e-9,
    )
    check(
        "inside the 6 m band the taper is strictly below the 2.0 m/s cap",
        fb.fullback_approach_speed(8.3, 3.0) < FB_MAX_APPROACH_SPEED_MPS,
    )
    check(
        "outside the band the cap is the only limit",
        abs(fb.fullback_approach_speed(8.3, 50.0) - FB_MAX_APPROACH_SPEED_MPS) < 1e-9,
    )


# ══════════════════════════════════════════════════════════════════════
# [8] THE BREAK / STEP-OUT GATE IS 2D (CURED 2026-09-22)
# ══════════════════════════════════════════════════════════════════════

def sec_step_out_gate():
    section(8, "The break/step-out gate — depth OR a lateral channel breach")
    anchor_x = 70.0
    check(
        f"the 2D reach guard is the documented {FB_LATERAL_BREAK_RADIUS_M} m",
        abs(FB_LATERAL_BREAK_RADIUS_M - 25.0) < 1e-9,
    )

    # (a) Carrier level with the line in X but far OUTSIDE the sweeper's
    # channel AND outside the 2D reach: the reach guard keeps the gate shut,
    # so the sweeper holds its rail and is NOT dragged across the field.
    bc = _Ball(anchor_x, 70.0)
    world = _World(anchor=anchor_x, bc=bc, frame=31)
    actor = make_fb(60.0, 35.0, player_id=401)
    apply_fullback_geometry(world, actor)
    print(f"        out of reach: state={actor.movement_state.name} "
          f"target_y={actor.target_y:.2f} (carrier lane {bc.y:.2f}, "
          f"reach={math.hypot(anchor_x - 60.0, 70.0 - 35.0):.1f} m)")
    check(
        "a wide carrier OUT OF 2D REACH does not open the gate",
        actor.movement_state != MovementState.CHASE,
    )
    check(
        "the destination is still the carrier's lane (open-play mirroring)",
        abs(actor.target_y - bc.y) < 1e-9,
    )

    # (b) Same lateral picture, now INSIDE the reach: this is a genuine
    # lateral channel breach, so the gate opens even though the carrier has
    # not crossed the anchor line in X.
    bc_in = _Ball(anchor_x, 45.0)
    world_in = _World(anchor=anchor_x, bc=bc_in, frame=33)
    actor_in = make_fb(60.0, 35.0, player_id=403)
    apply_fullback_geometry(world_in, actor_in)
    print(f"        in reach    : state={actor_in.movement_state.name} "
          f"target_y={actor_in.target_y:.2f} (carrier lane {bc_in.y:.2f}, "
          f"reach={math.hypot(anchor_x - 60.0, 45.0 - 35.0):.1f} m)")
    check(
        "a lateral channel breach INSIDE 2D reach is a break -> CHASE",
        actor_in.movement_state == MovementState.CHASE,
    )

    # (c) The DEPTH branch still wins on its own.
    bc2 = _Ball(anchor_x + 1.01, 70.0)
    world2 = _World(anchor=anchor_x, bc=bc2, frame=32)
    actor2 = make_fb(60.0, 35.0, player_id=402)
    apply_fullback_geometry(world2, actor2)
    print(f"        past the gate: state={actor2.movement_state.name} "
          f"target_y={actor2.target_y:.2f} (carrier lane {bc2.y:.2f})")
    check(
        "carrier 1.01 m past the anchor line -> CHASE",
        actor2.movement_state == MovementState.CHASE,
    )
    check(
        "target_y is the carrier's own lane",
        abs(actor2.target_y - bc2.y) < 1e-9,
    )


def sec_mmdl_state():
    section(9, "MMDL mirror — the fullback's decided state is DRIFT/CHASE only")
    # Decide for a tagged fullback in both regimes and confirm the state
    # never enters a lateral-priority regime of its own choosing.
    class _MW:
        def __init__(self, ruck_x, tes=False):
            self.ruck_x_m = ruck_x
            self.tes_active = tes

    class _MF:
        def __init__(self):
            self.x = 60.0
            self.y = 35.0
            self.depth_class = "PASSIVE"
            self.player_id = 1
            self.defender_role = None
            self.movement_state = None
            self.is_tackler = False
            self.fullback_role_active = True

    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
    tuners = DefenceTuners()
    no_break = decide_movement_state(
        _MW(100.0), _MF(), _Ball(105.0, 35.0), tuners)
    brk = decide_movement_state(
        _MW(30.0), _MF(), _Ball(55.0, 35.0), tuners)
    print(f"        no break -> {no_break.name}   break -> {brk.name}")
    check("no-break fullback resolves DRIFT", no_break == MovementState.DRIFT)
    check("break fullback resolves CHASE", brk == MovementState.CHASE)
    check(
        "the fullback is never routed into a RETREAT/SHOOT state of its own",
        no_break not in (MovementState.RETREAT, MovementState.SHOOT)
        and brk not in (MovementState.RETREAT, MovementState.SHOOT),
    )


# ══════════════════════════════════════════════════════════════════════
# [10] LIVE SPY — THE TAGGED ACTOR IS NEVER HANDED TO apply_movement_state
#
# The engine's per-frame debug chatter is enormous, so the rollout itself runs
# inside the quiet window.  The TELEMETRY it returns is reported AFTER the
# window closes, so the evidence is visible in the verifier's own output.
# ══════════════════════════════════════════════════════════════════════

_REAL_STDOUT = sys.stdout


def _quiet():
    sys.stdout = io.TextIOWrapper(
        open(os.devnull, "wb"), encoding="utf-8", errors="replace"
    )


def _loud():
    sys.stdout = _REAL_STDOUT


def run_live_spy():
    """Seeded rollout with apply_movement_state spied; returns telemetry."""
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    import pygame

    pygame.display.init()
    pygame.display.set_mode((1, 1), pygame.NOFRAME)

    import world.open_play_engine as ope_mod
    ope_mod.DEBUG_SDEA_PHYSICS = False
    ope_mod.DEBUG_OPE_TRACE = False

    from world.field import Field
    from world.world_state import WorldState, GamePhase
    from world.open_play_engine import OpenPlayEngine
    from kick_off.kickoff_formations import (
        apply_receiving_kickoff_formation,
        apply_kicking_team_formation,
    )
    from game.kickoff_engine import KickoffEngine
    from ball.ball import Ball
    from game.physics.collision_system import CollisionSystem
    from world.catch_system import check_for_catch
    import game.defence.defence_lvl_1.defensive_behaviour_unified as dbu

    import random
    random.seed(20260827)

    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)

    telemetry = {
        "frames": 0,
        "fb_tagged_frames": 0,
        "mmdl_calls": 0,
        "framed_mmdl_calls": 0,
        "violation_frames": 0,
        "violation_bodies": set(),
        "sampled_bodies": set(),
    }

    original = dbu.apply_movement_state
    handed_this_frame: set[int] = set()

    def _spy(player, *args, **kwargs):
        telemetry["mmdl_calls"] += 1
        handed_this_frame.add(id(player))
        return original(player, *args, **kwargs)

    dbu.apply_movement_state = _spy
    dt = 1.0 / 60.0
    try:
        kickoff_engine.reset_for_kickoff()
        apply_receiving_kickoff_formation(world, field)
        apply_kicking_team_formation(world, field)
        world.ball_owner = "NONE"
        world.ball_carrier = None
        world.ball.possessed_by = None
        kickoff_engine.kickoff()

        max_frames = int(os.environ.get("RLM_FB_LATERAL_FRAMES", "2500"))
        for frame in range(1, max_frames + 1):
            handed_this_frame.clear()
            telemetry["frames"] = frame
            world.frame_count = frame
            ball.update(dt)
            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(dt)
                check_for_catch(world, ball, dt)
            elif world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    world.open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
            elif world.phase == GamePhase.OPEN_PLAY:
                open_play.update(dt)
                if not world.tes_active:
                    collision_system.resolve_all(world.players, world=world)
            else:
                continue

            if handed_this_frame:
                telemetry["framed_mmdl_calls"] += 1
                telemetry["sampled_bodies"] |= handed_this_frame

            tagged = [p for p in world.defending_team
                      if getattr(p, "fullback_role_active", False)]
            if not tagged:
                continue
            telemetry["fb_tagged_frames"] += 1
            tagged_ids = {id(p) for p in tagged}
            overlap = tagged_ids & handed_this_frame
            if overlap:
                telemetry["violation_frames"] += 1
                telemetry["violation_bodies"] |= overlap
    finally:
        dbu.apply_movement_state = original
        pygame.quit()

    return telemetry


def report_live_spy(telemetry):
    section(10, "LIVE — the tagged fullback never reaches the MMDL LOOP")
    print(f"        rollout frames                  : {telemetry['frames']}")
    print(f"        frames with a tagged fullback   : {telemetry['fb_tagged_frames']}")
    print(f"        frames where the MMDL integrated: {telemetry['framed_mmdl_calls']}")
    print(f"        total apply_movement_state calls: {telemetry['mmdl_calls']}")
    print(f"        distinct MMDL-integrated bodies : {len(telemetry['sampled_bodies'])}")
    print(f"        frames with a tagged FB handed  : {telemetry['violation_frames']}")

    check(
        "the rollout reached frames with a tagged fullback",
        telemetry["fb_tagged_frames"] > 0,
    )
    check(
        "the spy is NON-VACUOUS (line bodies are handed to the MMDL live)",
        telemetry["mmdl_calls"] > 0 and len(telemetry["sampled_bodies"]) > 0,
    )
    # NOTE (2026-09-27).  This spy patches the DBU BINDING of
    # apply_movement_state -- i.e. the MMDL LOOP -- and that loop STILL
    # excludes every tagged fullback.  The fullback is NOT excluded from the
    # shared movement model ITSELF: it reaches it through its own entry point
    # (player/movement_states.apply_fullback_locomotion, called from
    # Player.update).  Both halves of the law are therefore live at once:
    # excluded from the loop, integrated by the same physics.  The superseding
    # verifier is verify_fullback_shared_locomotion.py.
    check(
        "the tagged fullback is NEVER handed to the MMDL loop itself "
        "(0 violating frames)",
        telemetry["violation_frames"] == 0,
    )


def main():
    print("FULLBACK LATERAL-PRIORITY EXCLUSION (audit 2026-09-22)")
    print("=" * 68)
    sec_premise()
    sec_fullback_module_is_clean()
    sec_only_live_caller()
    sec_retreat_exclusions()
    sec_helper_signatures()
    sec_channel_clamp()
    sec_x_keyed_starvation()
    sec_step_out_gate()
    sec_mmdl_state()
    _quiet()
    try:
        telemetry = run_live_spy()
    finally:
        _loud()
    report_live_spy(telemetry)
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS — the 0.25 lateral-priority constraint is NOT on the")
    print("        fullback's MMDL-LOOP code path (sections [1]-[4], [9], [10]);")
    print("        the Y-axis limiters the audit DID find — the 0.6 destination")
    print("        clamp, the Y-aware target error and the 2D step-out gate —")
    print("        are pinned by sections [5]-[8].")
    print("        PACING (2026-09-27): the sweeper's PACE is no longer written")
    print("        by any fullback code at all — it is MAX_SPEED_BY_STATE x")
    print("        max_speed_mps from the SHARED movement-state table, pinned")
    print("        by verify_fullback_shared_locomotion.py.")
    print("        (see FULLBACK_LATERAL_TRACKING_AUDIT.md and")
    print("        FULLBACK_LATERAL_OPEN_PLAY_REPORT.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
