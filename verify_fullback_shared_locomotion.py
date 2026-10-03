"""verify_fullback_shared_locomotion.py — THE FULLBACK LOCOMOTION PARITY
CONTRACT (2026-09-27).

THE LAW UNDER TEST
------------------
The tagged fullback DECIDES differently but MOVES under exactly the same
physics as every other defender.  Concretely, four parity bullets:

  * acceleration  -- ACCEL_BY_STATE[state]
  * top speed     -- MAX_SPEED_BY_STATE[state] x max_speed_mps
  * turning       -- LATERAL_PRIORITY_BY_STATE + FACE_BC_BY_STATE
  * integration   -- apply_movement_state (the state machine's own velocity
                     model AND position step)

WHY THIS VERIFIER EXISTS
------------------------
The fullback is deliberately excluded from the MMDL loop (it has its own
DRIFT/CHASE policy and its own target generation -- see
verify_fullback_lateral_priority_exclusion.py and
verify_mmdl_fullback_exclusion.py).  That exclusion used to leave it with NO
consumer of the shared movement model at all, so it ran on the legacy
0.70-per-frame accel-lerp with a bespoke ``move_speed`` written by four
separate sites and an FB-ONLY post-integration speed cap in
``Player.update()``.  Four locomotion laws nobody else obeyed.

This verifier pins the replacement: the actor is routed through
``apply_fullback_locomotion()`` (Player.update) into the SAME
``apply_movement_state`` the line bodies use.

WHAT IT REPLACES
----------------
The bespoke-helper assertions in verify_fullback_lateral_priority_exclusion.py
[5]-[8], verify_fullback_absorb.py [E], verify_fb_determinism.py and
verify_fullback_recover_contract.py pinned the OLD contract (the 2.0 m/s
sweeper cap, the 0.5x/1.0x bespoke scales, the X-keyed/2D taper band).  The
live-engine half of those pins is superseded by sections [3]-[5] and [7]
below; sections [1]-[3] of that file (the MMDL-loop exclusion) still hold and
are untouched.
"""
import inspect
import math
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import player.movement_states as ms
from player.movement_states import (
    MovementState,
    ACCEL_BY_STATE,
    MAX_SPEED_BY_STATE,
    LATERAL_PRIORITY_BY_STATE,
    FACE_BC_BY_STATE,
    apply_fullback_locomotion,
)
from player.player import Player
from game.defence.defence_lvl_1.fullback_controller import (
    FullbackIntent,
    enter_fullback_intent,
)

DT = 1.0 / 60.0
FAILURES = []

# ``apply_movement_state`` ends by calling ``update_facing_unified`` for every
# state whose ``face_bc`` is False (CHASE / SHOOT / RECOVER).  That call nudges
# VELOCITY toward facing -- ``vx += cos(facing) * 0.02`` -- which lands AFTER
# the state's own speed clamp and AFTER the position step.  It is the SAME
# nudge every line defender receives, so it is part of the law under test and
# the checks below allow for exactly that much.  States with ``face_bc`` True
# (HOLD / RETREAT / SLIDE / DRIFT) go through ``set_facing`` instead and are
# nudge-free.  In the live engine the following post-facing unified ceiling
# clamp absorbs the nudge, which is why `_apply_post_facing_ceiling` mirrors it.
UFU_NUDGE_MPS = 0.02


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        FAILURES.append(label)


def section(n, title):
    print(f"\n[{n}] {title}")


def _src(rel_path):
    with open(rel_path, encoding="utf-8") as fh:
        return fh.read()


# ══════════════════════════════════════════════════════════════════════
# STUBS
# ══════════════════════════════════════════════════════════════════════

class _BC:
    """Ball-carrier stub (x/y are properties on the real Player)."""

    def __init__(self, x, y, vy=0.0):
        self.x = x
        self.y = y
        self.vy = vy


class _World:
    def __init__(self, bc=None):
        self.ball_carrier = bc


def make_fb(x, y, state, target_x, target_y, bc=None, pid=201):
    p = Player(x=x, y=y, player_id=pid)
    p.jersey_number = 1
    p.max_speed_mps = 9.5
    p.max_defensive_speed_mps = 8.3
    p.fullback_role_active = True
    p.movement_state = state
    p.target_x = target_x
    p.target_y = target_y
    p._world_ref = _World(bc)
    return p


def speed(p):
    return math.hypot(p.vx, p.vy)


def _apply_post_facing_ceiling(p):
    """Mirror Player.update's post-facing unified defensive ceiling.

    The FB's shared-model integration runs INSIDE ``Player.update``, which
    then applies the same hard ``max_defensive_speed_mps`` clamp it applies
    to every other defender -- and that clamp is what absorbs the +0.02
    facing nudge on the CHASE gait.  These standalone checks call the shared
    model directly, so they apply the clamp themselves to stay faithful to
    the real pipeline instead of measuring an idealised version of it.
    """
    ceiling = float(getattr(p, "max_defensive_speed_mps", 0.0) or 0.0)
    if ceiling > 0.0:
        current = speed(p)
        if current > ceiling:
            p.vx *= ceiling / current
            p.vy *= ceiling / current


# ══════════════════════════════════════════════════════════════════════
# [1] THE ACTOR IS ROUTED INTO THE SHARED INTEGRATOR
# ══════════════════════════════════════════════════════════════════════

def sec_routing():
    section(1, "apply_fullback_locomotion routes the actor into "
               "apply_movement_state")
    check(
        "apply_fullback_locomotion is exported by player/movement_states.py",
        callable(apply_fullback_locomotion),
    )
    bc = _BC(70.0, 35.0)
    fb = make_fb(60.0, 35.0, MovementState.DRIFT, 70.0, 35.0, bc=bc)

    calls = []
    original = ms.apply_movement_state

    def _spy(player, state, tx, ty, dt, **kw):
        calls.append((player, state, tx, ty, dt))
        return original(player, state, tx, ty, dt, **kw)

    ms.apply_movement_state = _spy
    try:
        ran = apply_fullback_locomotion(fb, DT)
    finally:
        ms.apply_movement_state = original

    check("it reports that the shared model ran", ran is True)
    check("apply_movement_state was called exactly once", len(calls) == 1,
          f"calls={len(calls)}")
    if calls:
        who, state, tx, ty, dt = calls[0]
        check("it was handed the fullback itself", who is fb)
        check("it was handed the actor's OWN decided state",
              state is MovementState.DRIFT, f"state={state}")
        check("it was handed the actor's OWN target (never recomputed)",
              abs(tx - 70.0) < 1e-9 and abs(ty - 35.0) < 1e-9,
              f"target=({tx},{ty})")
        check("it was handed the live dt", abs(dt - DT) < 1e-12)


# ══════════════════════════════════════════════════════════════════════
# [2] NO USABLE STATE -> THE LEGACY FALLBACK, NEVER A CRASH
# ══════════════════════════════════════════════════════════════════════

def sec_no_state_falls_back():
    section(2, "No usable MovementState -> returns False (caller falls back)")

    fresh = make_fb(60.0, 35.0, None, 70.0, 35.0)
    before = (fresh.x, fresh.y, fresh.vx, fresh.vy)
    ran = apply_fullback_locomotion(fresh, DT)
    check("a state-less body reports False", ran is False)
    check("a state-less body is left completely untouched",
          (fresh.x, fresh.y, fresh.vx, fresh.vy) == before)

    class _Alien:
        name = "NOT_A_MOVEMENT_STATE"

    alien = make_fb(60.0, 35.0, _Alien(), 70.0, 35.0)
    check("a non-MovementState enum reports False",
          apply_fullback_locomotion(alien, DT) is False)

    for st in (MovementState.HOLD, MovementState.RETREAT, MovementState.SLIDE,
               MovementState.SHOOT, MovementState.CHASE, MovementState.DRIFT,
               MovementState.RECOVER):
        body = make_fb(60.0, 35.0, st, 70.0, 35.0)
        check(f"state {st.name} is covered by the shared tables "
              "(no KeyError)",
              apply_fullback_locomotion(body, DT) is True)


# ══════════════════════════════════════════════════════════════════════
# [3] SHARED TOP SPEED — THE SAME TABLE AS EVERY DEFENDER
# ══════════════════════════════════════════════════════════════════════

def sec_top_speed():
    section(3, "Top speed is MAX_SPEED_BY_STATE x max_speed_mps")

    for state in (MovementState.DRIFT, MovementState.CHASE,
                  MovementState.RECOVER):
        ceiling = 9.5 * MAX_SPEED_BY_STATE[state]
        body = make_fb(60.0, 35.0, state, 200.0, 35.0,
                       bc=_BC(200.0, 35.0))
        for _ in range(240):
            apply_fullback_locomotion(body, DT)
            _apply_post_facing_ceiling(body)
        print(f"        {state.name:8s} reached {speed(body):7.3f} m/s  "
              f"(table ceiling {ceiling:.3f})")
        check(
            f"{state.name}: never exceeds the shared table ceiling "
            "(+ the shared facing nudge)",
            speed(body) <= ceiling + UFU_NUDGE_MPS + 1e-6,
            f"speed={speed(body):.6f} ceiling={ceiling:.6f}",
        )
        if state is not MovementState.HOLD:
            check(
                f"{state.name}: actually accelerates (table is live)",
                speed(body) > 0.0,
            )

    # THE HEADLINE ACCEPTANCE TEST: the old FB-only 2.0 m/s sweeper cap is
    # gone.  A chasing fullback must be able to reach the governed pace.
    chaser = make_fb(60.0, 35.0, MovementState.CHASE, 200.0, 35.0,
                     bc=_BC(200.0, 35.0))
    for _ in range(240):
        apply_fullback_locomotion(chaser, DT)
    print(f"        CHASE after 4 s: {speed(chaser):.3f} m/s "
          "(old FB-only cap was 2.0)")
    check(
        "CHASE breaks the legacy 2.0 m/s fullback-only sweeper cap",
        speed(chaser) > 2.0 + 1e-3,
        f"speed={speed(chaser):.4f}",
    )
    check(
        "CHASE is bounded by 0.9 x max_speed_mps (the shared CHASE scale)",
        speed(chaser)
        <= 9.5 * MAX_SPEED_BY_STATE[MovementState.CHASE] + UFU_NUDGE_MPS + 1e-6,
    )

    # And the governed defensive ceiling still bounds it where it is stamped.
    governed = make_fb(60.0, 35.0, MovementState.CHASE, 200.0, 35.0,
                       bc=_BC(200.0, 35.0))
    governed.max_defensive_speed_mps = 8.3
    for _ in range(240):
        apply_fullback_locomotion(governed, DT)
        # mirror the post-facing unified clamp Player.update applies
        _apply_post_facing_ceiling(governed)
    check(
        "with the unified ceiling stamped, CHASE settles at 8.3 m/s",
        abs(speed(governed) - 8.3) < 0.05,
        f"speed={speed(governed):.4f}",
    )


# ══════════════════════════════════════════════════════════════════════
# [4] SHARED ACCELERATION — THE SAME RAMP AS EVERY DEFENDER
# ══════════════════════════════════════════════════════════════════════

def sec_acceleration():
    section(4, "Acceleration is ACCEL_BY_STATE (the shared ramp)")

    # One frame of the shared model must land EXACTLY on the table's ramp:
    #   v = 0 + (desired - 0) * min(1, accel * dt)
    for state in (MovementState.DRIFT, MovementState.CHASE,
                  MovementState.RECOVER):
        body = make_fb(60.0, 35.0, state, 200.0, 35.0,
                       bc=_BC(200.0, 35.0))
        apply_fullback_locomotion(body, DT)
        accel = ACCEL_BY_STATE[state] * DT
        print(f"        {state.name:8s} accel={ACCEL_BY_STATE[state]:5.2f} "
              f"m/s^2 -> frame-1 speed {speed(body):.4f} m/s")
        check(
            f"{state.name}: the frame-1 speed is the shared ramp "
            "(not the legacy 0.70 lerp)",
            speed(body) > 0.0
            and speed(body)
            <= 9.5 * MAX_SPEED_BY_STATE[state] * min(1.0, accel)
            + UFU_NUDGE_MPS + 1e-9,
        )

    # RECOVER must build up at the ORDINARY rate, not an explosive one --
    # the pace knob is the shared table now, not FB_RECOVER_SPEED_SCALE.
    rec_over = ACCEL_BY_STATE[MovementState.RECOVER]
    check(
        "RECOVER accelerates at the RETREAT-rate build-up (<= CHASE)",
        rec_over <= ACCEL_BY_STATE[MovementState.CHASE],
        f"RECOVER={rec_over} CHASE={ACCEL_BY_STATE[MovementState.CHASE]}",
    )
    check(
        "RECOVER is not an explosive SHOOT-rate burst",
        rec_over < ACCEL_BY_STATE[MovementState.SHOOT],
    )


# ══════════════════════════════════════════════════════════════════════
# [5] SINGLE INTEGRATION — NO DOUBLE STEP (the "zoom" mechanism)
# ══════════════════════════════════════════════════════════════════════

def sec_single_integration():
    section(5, "Position is integrated exactly ONCE per call")
    body = make_fb(60.0, 35.0, MovementState.CHASE, 200.0, 35.0,
                   bc=_BC(200.0, 35.0))
    x0, y0 = body.x, body.y
    apply_fullback_locomotion(body, DT)
    dx, dy = body.x - x0, body.y - y0
    ex, ey = body.vx * DT, body.vy * DT
    print(f"        moved ({dx:.6f}, {dy:.6f})  velocity*dt "
          f"({ex:.6f}, {ey:.6f})")
    # The position step consumes the PRE-nudge velocity, after which
    # update_facing_unified may add at most UFU_NUDGE_MPS per axis -- so v*dt
    # may over-report by that constant and by nothing more.  A DOUBLE
    # integration would over-report by a FACTOR, which this bound still
    # catches (and the second check states outright).
    check(
        "the displacement is ONE v*dt step (no double integration)",
        abs(dx - ex) <= UFU_NUDGE_MPS * DT + 1e-12
        and abs(dy - ey) <= UFU_NUDGE_MPS * DT + 1e-12,
        f"dx={dx:.12f} ex={ex:.12f} dy={dy:.12f} ey={ey:.12f}",
    )
    check(
        "the displacement is not a double step (well inside 2 x v*dt)",
        abs(dx) < 2.0 * abs(ex) + 1e-12,
        f"dx={dx:.12f} 2*v*dt={2.0 * abs(ex):.12f}",
    )


# ══════════════════════════════════════════════════════════════════════
# [6] SHARED TURNING — THE SHARED LATERAL / FACING POLICIES APPLY
# ══════════════════════════════════════════════════════════════════════

def sec_turning():
    section(6, "Turning uses the shared lateral-priority + facing policy")

    check(
        "DRIFT is lateral-DOMINANT on the shared table (the sweeper's gait)",
        LATERAL_PRIORITY_BY_STATE[MovementState.DRIFT] > 0.5,
    )
    check(
        "CHASE is X-dominant on the shared table (diagonal to the carrier)",
        LATERAL_PRIORITY_BY_STATE[MovementState.CHASE] < 0.5,
    )
    check(
        "DRIFT faces the carrier (sweeper keeps eyes on the play)",
        FACE_BC_BY_STATE[MovementState.DRIFT] is True,
    )
    check(
        "CHASE faces its direction of travel on the shared table",
        FACE_BC_BY_STATE[MovementState.CHASE] is False,
    )

    # DRIFT with a live BC must resolve the shared facing policy onto the
    # carrier.  ``apply_movement_state`` expresses that through ``set_facing``
    # -- the documented facing-override API -- which STORES the angle for
    # ``update_facing_unified`` to apply rather than writing ``.facing``
    # itself, so the override pair is what must be asserted here.
    bc = _BC(70.0, 45.0)
    body = make_fb(60.0, 35.0, MovementState.DRIFT, 70.0, 35.0, bc=bc)
    apply_fullback_locomotion(body, DT)
    expected = math.atan2(bc.y - body.y, bc.x - body.x)
    check(
        "DRIFT engages the shared facing override onto the carrier",
        body.facing_override is True,
        f"facing_override={body.facing_override}",
    )
    check(
        "the override angle is the bearing to the carrier",
        abs((body.facing_override_angle - expected + math.pi)
            % (2 * math.pi) - math.pi) < 1e-6,
        f"angle={body.facing_override_angle:.6f} expected={expected:.6f}",
    )

    body = make_fb(
        60.0, 35.0, MovementState.DRIFT, 70.0, 35.0,
        bc=_BC(80.0, 35.0),
    )
    body.facing = math.pi
    body.set_facing(math.pi)
    enter_fullback_intent(
        body._world_ref, body, FullbackIntent.CHASE
    )
    facing_before = body.facing
    apply_fullback_locomotion(body, DT)
    facing_after_error = abs(
        (body.facing - math.atan2(body.vy, body.vx) + math.pi)
        % (2 * math.pi) - math.pi
    )
    check(
        "entering CHASE clears DRIFT's facing lock",
        body.facing_override is False,
    )
    check(
        "CHASE velocity-facing rotates toward the movement direction",
        facing_after_error < abs(
            (facing_before - math.atan2(body.vy, body.vx) + math.pi)
            % (2 * math.pi) - math.pi
        ),
        f"facing={body.facing:.4f}, velocity angle="
        f"{math.atan2(body.vy, body.vx):.4f}",
    )


# ══════════════════════════════════════════════════════════════════════
# [7] THE BESPOKE SPEED AUTHORITY IS GONE FROM THE LIVE ENGINE
# ══════════════════════════════════════════════════════════════════════

def sec_no_bespoke_authority():
    section(7, "The FB-only speed authority is gone from the live engine")

    player_py = _src("player/player.py")
    check(
        "Player.update routes the tagged fullback through "
        "apply_fullback_locomotion",
        "apply_fullback_locomotion" in player_py,
    )
    check(
        "Player.update no longer imports the FB-only approach cap",
        "fullback_approach_cap_applies" not in player_py,
    )
    check(
        "Player.update no longer clamps the FB to "
        "FB_MAX_APPROACH_SPEED_MPS",
        "FB_MAX_APPROACH_SPEED_MPS" not in player_py,
    )
    check(
        "the FB-only post-integration cap block is marked deleted",
        "FULLBACK APPROACH-SPEED HARD CAP: DELETED" in player_py,
    )

    kickoff = _src("game/kickoff_engine.py")
    check(
        "kickoff_engine no longer writes the FB move_speed",
        "fullback_approach_speed" not in kickoff
        and "fullback_target_distance" not in kickoff,
    )
    # STAGE 1 INTENT OWNERSHIP (2026-09-30): the stamp is no longer a bare
    # assignment -- it is routed through ``fullback_controller``'s ONE write
    # path, which is exactly what that stage shipped.  The FACT this check
    # protects is unchanged and still asserted here: the kickoff FLIGHT
    # movement state is still DECIDED in kickoff_engine (this stage moved
    # only the PACE to the shared model); it now names its INTENT and lets
    # the controller resolve it.
    check(
        "kickoff_engine still decides the FB movement state (through the "
        "controller's single write path)",
        "enter_fullback_intent(" in kickoff
        and "FullbackIntent" in kickoff,
    )
    check(
        "kickoff_engine no longer assigns the FB movement state directly",
        "movement_state = MovementState.DRIFT" not in kickoff,
    )

    dbu = _src("game/defence/defence_lvl_1/defensive_behaviour_unified.py")
    check(
        "defensive_behaviour_unified no longer writes the FB move_speed",
        "fb_actor.move_speed" not in dbu,
    )

    fb_role = _src("game/defence/defence_lvl_1/fullback_role.py")
    check(
        "fullback_role.apply_fullback_geometry no longer writes the FB "
        "move_speed",
        "defender.move_speed" not in fb_role,
    )
    # STAGE 1 INTENT OWNERSHIP (2026-09-30): same law as the kickoff site
    # above.  The geometry authority still DECIDES the actor's movement
    # state -- but it now states an INTENT and resolves it through the
    # controller, so the bare assignment is gone BY DESIGN.
    check(
        "fullback_role still decides the actor's movement STATE (through "
        "the controller's single write path)",
        "enter_fullback_intent(" in fb_role,
    )
    check(
        "fullback_role no longer assigns the actor's movement state directly",
        "defender.movement_state = " not in fb_role,
    )


# ══════════════════════════════════════════════════════════════════════
# [8] END-TO-END THROUGH Player.update()
# ══════════════════════════════════════════════════════════════════════

def sec_player_update_end_to_end():
    section(8, "Player.update() — the FB moves under the shared model")

    # (a) A tagged fullback WITH a state is integrated inside update().
    body = make_fb(60.0, 35.0, MovementState.DRIFT, 70.0, 35.0,
                   bc=_BC(70.0, 35.0))
    moved = []
    original = ms.apply_movement_state

    def _spy(player, state, tx, ty, dt, **kw):
        moved.append(player)
        return original(player, state, tx, ty, dt, **kw)

    ms.apply_movement_state = _spy
    try:
        body.update(DT)
    finally:
        ms.apply_movement_state = original
    check(
        "Player.update() sends the tagged fullback into the shared model",
        body in moved,
        f"integrated={len(moved)}",
    )
    check(
        "and the actor actually moved",
        abs(body.x - 60.0) > 1e-9,
    )

    # (b) A tagged fullback with NO state still integrates (legacy fallback)
    #     rather than freezing or raising.
    fallback = make_fb(60.0, 35.0, None, 70.0, 35.0)
    try:
        fallback.update(DT)
        ok, err = True, ""
    except Exception as exc:                      # noqa: BLE001
        ok, err = False, f"{type(exc).__name__}: {exc}"
    check("Player.update() does not raise for a state-less fullback",
          ok, err)
    if ok:
        check(
            "the state-less fullback still integrates on the legacy path",
            abs(fallback.x - 60.0) > 1e-9,
            f"x={fallback.x}",
        )


def main():
    print("FULLBACK SHARED LOCOMOTION — PARITY CONTRACT (2026-09-27)")
    print("=" * 68)
    sec_routing()
    sec_no_state_falls_back()
    sec_top_speed()
    sec_acceleration()
    sec_single_integration()
    sec_turning()
    sec_no_bespoke_authority()
    sec_player_update_end_to_end()
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS — the tagged fullback DECIDES differently but MOVES")
    print("        under the same acceleration table, the same top-speed")
    print("        table, the same turning policy and the same integration")
    print("        path as every other defender.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
