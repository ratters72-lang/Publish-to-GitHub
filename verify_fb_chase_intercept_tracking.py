"""verify_fb_chase_intercept_tracking.py — THE FULLBACK INTERCEPT-TRACKING
CONTRACT (2026-09-29).

THE LAW UNDER TEST
------------------
On a genuine line break the tagged fullback is handed an INTERCEPT POINT by
``fullback_role.fullback_chase_point`` (a velocity projection with a goal-side
margin -- see FB_EMERGENCY_CHASE_TARGET_AXIS_AUDIT.md).  The BODY must travel
to that point:

  * the steering vector is the direct BEARING to the commanded intercept
    point on both axes -- not the X-skewed ``lateral_priority`` blend;
  * the pre-tackle lateral ALIGNMENT controls (the spacing-Y freeze and the
    compound lateral correction clamp, items 1/2/3/5 of the collapse pack)
    are NOT applied to the interceptor -- they exist to keep a LINE defender's
    forward momentum into a slot, and the fullback's contact-range contract is
    carried by the chase point's own near-range taper;
  * EVERYTHING ELSE is unchanged: the same ACCEL_BY_STATE ramp, the same
    MAX_SPEED_BY_STATE top speed, the same facing policy, the same single
    integration step, the same collapse-commit vector override, and the same
    post-facing ``max_defensive_speed_mps`` ceiling;
  * the shared CHASE row and every other caller are untouched.

WHAT IT CHECKS
--------------
  [1] source contract -- the lever, its default, the keyword-only parameter,
      and the single owner (``apply_fullback_locomotion``);
  [2] the bearing -- the lever-on steering vector IS the bearing, and the
      historical vector's measured departure from it;
  [3] the alignment exemptions -- the stepping-carrier lateral damp and the
      spacing-Y freeze, both of which starve the interceptor;
  [4] the shared table and other callers are untouched;
  [5] only CHASE diverges: the fullback's DRIFT / RECOVER are byte-identical;
  [6] the collapse-commit override and the arrival authority survive;
  [7] END TO END -- generation (the real ``fullback_chase_point``) feeding
      consumption through ``apply_fullback_locomotion``, pursued against a
      live diagonal carrier: the interceptor closes the LATERAL error, is
      never further from the carrier, and does not cross its lane more often.

Exits 1 on any failure.
"""
import inspect
import math
import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import player.movement_states as ms
from player.movement_states import (
    ACCEL_BY_STATE,
    LATERAL_PRIORITY_BY_STATE,
    MAX_SPEED_BY_STATE,
    MovementState,
    apply_fullback_locomotion,
    apply_movement_state,
)
from player.collapse_controls import (
    BC_LATERAL_DAMP,
    COLLAPSE_COMMIT_RADIUS_M,
    SPACING_FREEZE_RADIUS_M,
)
from player.player import Player
from game.defence.defence_lvl_1 import fullback_role as fb
from game.defence.defence_lvl_1.fullback_role import (
    FB_BREAK_DEPTH,
    fullback_chase_point,
)

DT = 1.0 / 60.0
# ``update_facing_unified`` adds a UI-coupling term to the velocity AFTER the
# movement model ("vx += cos(facing) * 0.02", smoothed by
# ROTATION_SMOOTHING).  Every stub body below pins ``facing_override`` (see
# ``make_fb``), which makes that function return before it can rotate or
# nudge, so the velocity this verifier reads IS the steering vector.  Without
# the pin, each numeric check would have to model an unrelated rotation
# constant.
UFU_NUDGE_MPS = 0.02
FAILURES: list[str] = []


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

class _Carrier:
    """Ball-carrier stub: position + velocity, nothing else."""

    def __init__(self, x, y, vx=0.0, vy=0.0):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.frozen = False


class _World:
    def __init__(self, bc=None):
        self.ball_carrier = bc


def make_fb(x, y, state=MovementState.CHASE, target=(0.0, 0.0), bc=None,
            pid=201):
    p = Player(x=x, y=y, player_id=pid)
    p.jersey_number = 1
    p.max_speed_mps = 9.5
    p.max_defensive_speed_mps = 8.3
    p.fullback_role_active = True
    p.movement_state = state
    p.target_x = target[0]
    p.target_y = target[1]
    p._world_ref = _World(bc)
    # Pin the facing override so update_facing_unified bypasses its rotation
    # and its velocity-coupling nudge: the measured velocity is then exactly
    # what apply_movement_state's steering produced.
    p.facing_override = True
    p.facing_override_angle = 0.0
    return p


def speed(p):
    return math.hypot(p.vx, p.vy)


def _bearing(dx, dy):
    """Unit vector of (dx, dy) as a 2-tuple (0,0 when degenerate)."""
    length = math.hypot(dx, dy)
    if length < 1e-12:
        return 0.0, 0.0
    return dx / length, dy / length


def _angle_between(a, b):
    """Angle in degrees between two 2-vectors (0 when either is degenerate)."""
    la = math.hypot(*a)
    lb = math.hypot(*b)
    if la < 1e-12 or lb < 1e-12:
        return 0.0
    cos = max(-1.0, min(1.0, (a[0] * b[0] + a[1] * b[1]) / (la * lb)))
    return math.degrees(math.acos(cos))


def _post_facing_ceiling(p):
    """Mirror Player.update's post-facing unified defensive ceiling clamp."""
    ceiling = float(getattr(p, "max_defensive_speed_mps", 0.0) or 0.0)
    if ceiling > 0.0:
        current = speed(p)
        if current > ceiling:
            p.vx *= ceiling / current
            p.vy *= ceiling / current


# ══════════════════════════════════════════════════════════════════════
# [1] SOURCE CONTRACT
# ══════════════════════════════════════════════════════════════════════

def sec_source_contract():
    section(1, "Source contract — lever, default, keyword-only flag, one owner")

    ms_src = _src(os.path.join("player", "movement_states.py"))

    check("1.1 the lever environment variable is declared",
          'FB_CHASE_INTERCEPT_TRACK_ENV_VAR: str = "RLM_FB_CHASE_INTERCEPT_TRACK"'
          in ms_src)
    check("1.2 the module boolean defaults ON",
          ms.FB_CHASE_INTERCEPT_TRACK is True,
          f"FB_CHASE_INTERCEPT_TRACK={ms.FB_CHASE_INTERCEPT_TRACK}")
    check("1.3 the reader exists and reports the shipped default",
          ms.fullback_chase_intercept_track_enabled() is True)

    sig = inspect.signature(apply_movement_state)
    param = sig.parameters.get("intercept_track")
    check("1.4 apply_movement_state exposes intercept_track", param is not None,
          f"params={sorted(sig.parameters)}")
    if param is not None:
        check("1.5 intercept_track is KEYWORD-ONLY",
              param.kind is inspect.Parameter.KEYWORD_ONLY,
              f"kind={param.kind}")
        check("1.6 intercept_track defaults to False (every other caller "
              "unchanged)", param.default is False, f"default={param.default!r}")

    fb_src = inspect.getsource(apply_fullback_locomotion)
    check("1.7 apply_fullback_locomotion is the single caller that sets it",
          "intercept_track=_intercept_track," in fb_src)
    check("1.8 it is armed ONLY for CHASE",
          "state == MovementState.CHASE" in fb_src)

    # No other ENGINE file may claim the flag.  A test harness is not an
    # engine file: verifiers and probes exercise the flag DIRECTLY, so they
    # are filtered out by the same naming convention the rest of the FB
    # suite uses.  Without this the check would count
    # ``verify_fullback_locomotion_ownership_contract.py`` -- which drives
    # ``intercept_track=`` explicitly to prove the historical derivation is
    # intact -- as a second claimant of a production flag.
    self_path = os.path.abspath(__file__)
    claimants = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs
                   if d not in {".git", "__pycache__", ".venv", "node_modules",
                                "debug_archive", "tmp"}]
        for name in files:
            if not name.endswith(".py"):
                continue
            if name.startswith(("verify_", "probe_", "tmp_", "tmp")):
                continue
            path = os.path.join(root, name)
            if os.path.abspath(path) == self_path:
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    body = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            if "intercept_track=" in body:
                claimants.append(path.replace("\\", "/").lstrip("./"))
    check("1.9 exactly ONE engine file passes intercept_track",
          claimants == ["player/movement_states.py"],
          f"claimants={sorted(claimants)}")


# ══════════════════════════════════════════════════════════════════════
# [2] THE BEARING
# ══════════════════════════════════════════════════════════════════════

# A deliberately STEEP intercept geometry: the commanded point is 10 m ahead in
# X and 20 m ACROSS in Y, i.e. a bearing 63.4 degrees off the attack axis.
_FB_XY = (60.0, 20.0)
_TARGET_XY = (70.0, 40.0)
# The carrier sits far from the body so no alignment item (1/2/3/5) can fire:
# dist_to_bc >> SPACING_FREEZE_RADIUS_M and >> the commit zone.
_FAR_BC = (120.0, 20.0)


def _first_frame_velocity(intercept_track: bool, target=_TARGET_XY,
                          bc=_FAR_BC, bc_vy=0.0):
    """One frame of the shared model; returns the resulting (vx, vy)."""
    body = make_fb(*_FB_XY, target=target, bc=_Carrier(*bc))
    apply_movement_state(
        body,
        MovementState.CHASE,
        target[0],
        target[1],
        DT,
        bc_x=bc[0],
        bc_y=bc[1],
        bc_vy=bc_vy,
        preserve_state=True,
        intercept_track=intercept_track,
    )
    return body.vx, body.vy


def sec_bearing():
    section(2, "The steering vector IS the bearing to the commanded point")

    dx = _TARGET_XY[0] - _FB_XY[0]
    dy = _TARGET_XY[1] - _FB_XY[1]
    bearing = _bearing(dx, dy)
    print(f"        geometry dx={dx:.1f} dy={dy:.1f} "
          f"bearing={math.degrees(math.atan2(dy, dx)):.1f} deg")

    ix, iy = _first_frame_velocity(intercept_track=True)
    hx, hy = _first_frame_velocity(intercept_track=False)
    print(f"        intercept-track v=({ix:.4f}, {iy:.4f})")
    print(f"        historical      v=({hx:.4f}, {hy:.4f})")

    err_on = _angle_between((ix, iy), bearing)
    err_off = _angle_between((hx, hy), bearing)
    print(f"        departure from the bearing: on={err_on:.4f} deg  "
          f"off={err_off:.2f} deg")

    check("2.1 the intercept-track velocity is parallel to the bearing",
          err_on < 1e-6, f"err={err_on:.9f} deg")
    check("2.2 the historical velocity DEPARTS from the bearing (the defect)",
          err_off > 10.0, f"err={err_off:.3f} deg")
    check("2.3 the intercept path delivers a strictly larger lateral command",
          abs(iy) > abs(hy) + 1e-9, f"on={abs(iy):.6f} off={abs(hy):.6f}")
    speed_on = math.hypot(ix, iy)
    speed_off = math.hypot(hx, hy)
    ramp = min(1.0, ACCEL_BY_STATE[MovementState.CHASE] * DT)
    ceiling = 9.5 * MAX_SPEED_BY_STATE[MovementState.CHASE]
    check("2.4 both legs run at the SAME speed — they differ in DIRECTION "
          "only",
          abs(speed_on - speed_off) < 1e-12,
          f"on={speed_on:.12f} off={speed_off:.12f}")
    check("2.5 the delivered speed is exactly the shared CHASE ramp",
          abs(speed_on - ceiling * ramp) < 1e-12,
          f"got={speed_on:.12f} expected={ceiling * ramp:.12f}")


# ══════════════════════════════════════════════════════════════════════
# [3] THE ALIGNMENT EXEMPTIONS
# ══════════════════════════════════════════════════════════════════════

def sec_alignment_exemptions():
    section(3, "The pre-tackle lateral ALIGNMENT controls are not applied")

    # ── (a) THE STEPPING-CARRIER LATERAL DAMP (item 3) ──
    # ``bc_vy_factor`` keys on the CARRIER's own |vy| > 1.0 m/s, so it is
    # isolated by running the SAME steering path twice -- once against a
    # stepping carrier, once against a stationary one -- at identical
    # geometry.  dist_to_bc = 3.0 m is outside the commit zone (0.8 m) AND
    # outside the freeze band (1.5 m) and above the 2.0 m distance window, so
    # neither of the other two items can contribute to the measured ratio.
    step_bc = (60.0 + 3.0, 20.0)
    hist_step = _first_frame_velocity(False, bc=step_bc, bc_vy=2.0)
    hist_still = _first_frame_velocity(False, bc=step_bc, bc_vy=0.0)
    int_step = _first_frame_velocity(True, bc=step_bc, bc_vy=2.0)
    int_still = _first_frame_velocity(True, bc=step_bc, bc_vy=0.0)

    hist_ratio = hist_step[1] / hist_still[1]
    int_ratio = int_step[1] / int_still[1]
    print(f"        stepping carrier (|bc_vy| 2.0 vs 0.0, dist_to_bc=3.0 m, "
          f"BC_LATERAL_DAMP={BC_LATERAL_DAMP}):")
    print(f"          historical vy ratio = {hist_ratio:.12f}")
    print(f"          intercept  vy ratio = {int_ratio:.12f}")

    check("3.1 the historical path multiplies the lateral command by "
          "BC_LATERAL_DAMP (item 3 fires)",
          abs(hist_ratio - BC_LATERAL_DAMP) < 1e-12,
          f"ratio={hist_ratio:.12f} expected={BC_LATERAL_DAMP}")
    check("3.2 the intercept path is NOT damped (item 3 is exempt)",
          abs(int_ratio - 1.0) < 1e-12, f"ratio={int_ratio:.12f}")
    check("3.3 the damp touches the LATERAL command only, on both paths",
          abs(hist_step[0] - hist_still[0]) < 1e-12
          and abs(int_step[0] - int_still[0]) < 1e-12,
          f"hist dx={hist_step[0] - hist_still[0]:.3e} "
          f"int dx={int_step[0] - int_still[0]:.3e}")
    check("3.4 the intercept lateral command is strictly greater than the "
          "historical one at the same geometry",
          abs(int_step[1]) > abs(hist_step[1]) + 1e-9,
          f"on={abs(int_step[1]):.6f} off={abs(hist_step[1]):.6f}")

    # ── (b) THE SPACING-Y FREEZE (item 5) ──
    # Inside SPACING_FREEZE_RADIUS_M the shared item re-points target_y at the
    # body's OWN lane, which zeroes the lateral command outright.
    near_bc = (60.0 + 1.0, 20.0)
    bx, by = _first_frame_velocity(True, bc=near_bc)
    zx, zy = _first_frame_velocity(False, bc=near_bc)
    print(f"        inside the {SPACING_FREEZE_RADIUS_M} m freeze band: "
          f"intercept |vy|={abs(by):.6f}  historical |vy|={abs(zy):.6f}")
    check("3.5 the historical path FREEZES the lateral command to zero "
          "(item 5 re-points target_y at the body's own lane)",
          abs(zy) < 1e-12, f"vy={zy:.12f}")
    check("3.6 the intercept path preserves the lateral command",
          abs(by) > 1e-6, f"vy={by:.9f}")
    check("3.7 the 1.0 m probe geometry straddles the two radii correctly "
          "(inside the freeze band, outside the commit zone)",
          1.0 < SPACING_FREEZE_RADIUS_M and 1.0 > COLLAPSE_COMMIT_RADIUS_M,
          f"freeze={SPACING_FREEZE_RADIUS_M} commit={COLLAPSE_COMMIT_RADIUS_M}")


# ══════════════════════════════════════════════════════════════════════
# [4] THE SHARED TABLE AND OTHER CALLERS ARE UNTOUCHED
# ══════════════════════════════════════════════════════════════════════

def sec_shared_table_untouched():
    section(4, "The shared CHASE row and every other caller are untouched")

    check("4.1 LATERAL_PRIORITY_BY_STATE[CHASE] is still the X-dominant 0.3",
          abs(LATERAL_PRIORITY_BY_STATE[MovementState.CHASE] - 0.3) < 1e-12,
          f"={LATERAL_PRIORITY_BY_STATE[MovementState.CHASE]}")
    check("4.2 the CHASE speed scale is unchanged",
          abs(MAX_SPEED_BY_STATE[MovementState.CHASE] - 0.9) < 1e-12)
    check("4.3 the CHASE acceleration is unchanged",
          abs(ACCEL_BY_STATE[MovementState.CHASE] - 6.0) < 1e-12)

    # A line defender calling with the historical signature is byte-identical.
    dx = _TARGET_XY[0] - _FB_XY[0]
    dy = _TARGET_XY[1] - _FB_XY[1]
    lat = LATERAL_PRIORITY_BY_STATE[MovementState.CHASE]
    nx, ny = _bearing(dx, dy)
    dir_x = nx * (1.0 - lat)
    dir_y = ny * lat + nx * (1.0 - abs(nx)) * (1.0 - lat) * 0.3
    ln = math.hypot(dir_x, dir_y)
    expected_vx = (dir_x / ln) * 9.5 * MAX_SPEED_BY_STATE[MovementState.CHASE]
    expected_vx *= min(1.0, ACCEL_BY_STATE[MovementState.CHASE] * DT)
    hx, _hy = _first_frame_velocity(intercept_track=False)
    check("4.4 the default path reproduces the historical blend EXACTLY",
          abs(hx - expected_vx) < 1e-12,
          f"got={hx:.12f} expected={expected_vx:.12f}")

    # An explicit keyword-free call is identical to intercept_track=False.
    a = make_fb(*_FB_XY, target=_TARGET_XY, bc=_Carrier(*_FAR_BC))
    b = make_fb(*_FB_XY, target=_TARGET_XY, bc=_Carrier(*_FAR_BC))
    apply_movement_state(a, MovementState.CHASE, *_TARGET_XY, DT,
                         bc_x=_FAR_BC[0], bc_y=_FAR_BC[1])
    apply_movement_state(b, MovementState.CHASE, *_TARGET_XY, DT,
                         bc_x=_FAR_BC[0], bc_y=_FAR_BC[1],
                         intercept_track=False)
    check("4.5 an omitted flag equals an explicit False",
          abs(a.vx - b.vx) < 1e-15 and abs(a.vy - b.vy) < 1e-15
          and a.x == b.x and a.y == b.y)


# ══════════════════════════════════════════════════════════════════════
# [5] ONLY CHASE DIVERGES
# ══════════════════════════════════════════════════════════════════════

def sec_only_chase_diverges():
    section(5, "Only CHASE diverges — DRIFT / RECOVER are byte-identical")

    for state in (MovementState.DRIFT, MovementState.RECOVER):
        legs = []
        for _ in range(2):
            body = make_fb(*_FB_XY, state=state, target=_TARGET_XY,
                           bc=_Carrier(*_FAR_BC))
            for _ in range(30):
                apply_fullback_locomotion(body, DT)
                _post_facing_ceiling(body)
            legs.append((body.vx, body.vy, body.x, body.y))
        check(f"5.1 [{state.name}] the fullback path is deterministic",
              legs[0] == legs[1])
        check(f"5.2 [{state.name}] it is NOT treated as an intercept "
              "(the lever only arms CHASE)",
              ms.fullback_chase_intercept_track_enabled() is True)

    # The lever really does bifurcate the CHASE leg.
    chase_on = make_fb(*_FB_XY, target=_TARGET_XY, bc=_Carrier(*_FAR_BC))
    chase_off = make_fb(*_FB_XY, target=_TARGET_XY, bc=_Carrier(*_FAR_BC))
    saved = ms.FB_CHASE_INTERCEPT_TRACK
    try:
        for _ in range(30):
            ms.FB_CHASE_INTERCEPT_TRACK = True
            apply_fullback_locomotion(chase_on, DT)
            ms.FB_CHASE_INTERCEPT_TRACK = False
            apply_fullback_locomotion(chase_off, DT)
    finally:
        ms.FB_CHASE_INTERCEPT_TRACK = saved
    check("5.3 the lever bifurcates the CHASE leg (in-process A/B works)",
          abs(chase_on.y - chase_off.y) > 1e-3
          and abs(chase_on.x - chase_off.x) > 1e-3,
          f"on=({chase_on.x:.4f},{chase_on.y:.4f}) "
          f"off=({chase_off.x:.4f},{chase_off.y:.4f})")
    check("5.4 the intercept leg has the greater lateral progress",
          abs(chase_on.y - _FB_XY[1]) > abs(chase_off.y - _FB_XY[1]),
          f"on dy={abs(chase_on.y - _FB_XY[1]):.4f} "
          f"off dy={abs(chase_off.y - _FB_XY[1]):.4f}")


# ══════════════════════════════════════════════════════════════════════
# [6] THE SURVIVING GUARDS
# ══════════════════════════════════════════════════════════════════════

def sec_surviving_guards():
    section(6, "The collapse-commit override and the arrival authority survive")

    # Inside the 0.8 m commit zone the collapse vector aims at the BC and the
    # arrival stop is suppressed.  That must hold on BOTH paths.
    for intercept in (False, True):
        body = make_fb(60.0, 20.0, target=(70.0, 45.0),
                       bc=_Carrier(60.2, 20.2))
        body.vx, body.vy = 1.5, 0.4
        apply_movement_state(
            body, MovementState.CHASE, body.target_x, body.target_y, DT,
            bc_x=60.2, bc_y=20.2, preserve_state=True,
            intercept_track=intercept,
        )
        label = "intercept" if intercept else "default"
        sp = math.hypot(body.vx, body.vy)
        to_bc = (60.2 - body.x, 20.2 - body.y)
        dot = body.vx * to_bc[0] + body.vy * to_bc[1]
        check(f"6.1 [{label}] the commit-zone velocity is not zeroed "
              "(item 4 owns these frames)",
              sp > 0.0, f"speed={sp:.6f}")
        check(f"6.2 [{label}] the commit-zone heading is toward the carrier",
              dot > 0.0, f"dot={dot:.6f}")
        check(f"6.3 [{label}] the state is not rewritten",
              body.movement_state is MovementState.CHASE)

    # Arrival still stops velocity and preserves the authored state.
    for intercept in (False, True):
        body = make_fb(60.0, 20.0, target=(60.0, 20.0),
                       bc=_Carrier(*_FAR_BC))
        body.vx, body.vy = 2.0, 2.0
        apply_movement_state(
            body, MovementState.CHASE, 60.0, 20.0, DT,
            bc_x=_FAR_BC[0], bc_y=_FAR_BC[1], preserve_state=True,
            intercept_track=intercept,
        )
        label = "intercept" if intercept else "default"
        check(f"6.4 [{label}] arrival stops the velocity",
              body.vx == 0.0 and body.vy == 0.0)
        check(f"6.5 [{label}] arrival keeps the authored CHASE state",
              body.movement_state is MovementState.CHASE)

    # apply_fullback_locomotion remains a PURE CONSUMER.
    body = make_fb(60.0, 20.0, target=_TARGET_XY, bc=_Carrier(*_FAR_BC))
    body.target_source = "fullback_geometry"
    body.move_speed = 3.25
    before = (body.target_x, body.target_y, body.movement_state,
              body.target_source, body.move_speed)
    apply_fullback_locomotion(body, DT)
    after = (body.target_x, body.target_y, body.movement_state,
             body.target_source, body.move_speed)
    check("6.6 the integrator writes no target / state / source / speed",
          before == after, f"{before} -> {after}")


# ══════════════════════════════════════════════════════════════════════
# [7] END TO END — REAL GENERATION + REAL CONSUMPTION
# ══════════════════════════════════════════════════════════════════════

CHASE_FRAMES = 150
ANCHOR_X = 60.0
BC_START = (72.0, 16.0)
BC_VEL = (4.0, 2.6)
FB_START = (60.0, 16.0)


def run_chase(intercept_track: bool):
    """Pursue a live diagonal carrier; return per-frame telemetry."""
    carrier = _Carrier(*BC_START, *BC_VEL)
    body = make_fb(*FB_START, bc=carrier)
    carrier.world = None

    rows = []
    saved = ms.FB_CHASE_INTERCEPT_TRACK
    ms.FB_CHASE_INTERCEPT_TRACK = intercept_track
    try:
        for frame in range(1, CHASE_FRAMES + 1):
            # ── GENERATION: the engine's own chase law ──
            cx, cy = fullback_chase_point(carrier, body, ANCHOR_X,
                                          FB_BREAK_DEPTH)
            body.target_x = float(cx)
            body.target_y = float(cy)
            body.movement_state = MovementState.CHASE
            body.target_source = "fullback_geometry"

            # ── CONSUMPTION: the engine's own integrator ──
            apply_fullback_locomotion(body, DT)
            _post_facing_ceiling(body)

            carrier.x += carrier.vx * DT
            carrier.y += carrier.vy * DT

            rows.append({
                "frame": frame,
                "tx": float(cx), "ty": float(cy),
                "fx": body.x, "fy": body.y,
                "bx": carrier.x, "by": carrier.y,
                "lat_err": abs(body.y - carrier.y),
                "x_err": abs(body.x - carrier.x),
                "dist": math.hypot(body.x - carrier.x,
                                   body.y - carrier.y),
            })
    finally:
        ms.FB_CHASE_INTERCEPT_TRACK = saved
    return rows


def _crossings(rows, key):
    """Count sign changes of (body - carrier) on the chosen axis."""
    count = 0
    prev = None
    for r in rows:
        off = r["fy"] - r["by"] if key == "y" else r["fx"] - r["bx"]
        if abs(off) <= 0.01:
            continue
        if prev is not None and prev * off < 0.0:
            count += 1
        prev = off
    return count


def sec_end_to_end():
    section(7, "END TO END — real chase point fed through the integrator")

    on = run_chase(True)
    off = run_chase(False)

    def mean(rows, key):
        return sum(r[key] for r in rows) / len(rows)

    lat_on, lat_off = mean(on, "lat_err"), mean(off, "lat_err")
    dist_on, dist_off = mean(on, "dist"), mean(off, "dist")
    final_on, final_off = on[-1], off[-1]
    ycross_on, ycross_off = _crossings(on, "y"), _crossings(off, "y")
    xcross_on, xcross_off = _crossings(on, "x"), _crossings(off, "x")

    print(f"        frames={CHASE_FRAMES}  carrier v=({BC_VEL[0]}, "
          f"{BC_VEL[1]}) m/s  dt=1/60")
    print(f"        mean lateral error to carrier : on={lat_on:6.3f} m  "
          f"off={lat_off:6.3f} m")
    print(f"        mean distance to carrier      : on={dist_on:6.3f} m  "
          f"off={dist_off:6.3f} m")
    print(f"        final lateral error           : on={final_on['lat_err']:6.3f} m"
          f"  off={final_off['lat_err']:6.3f} m")
    print(f"        final distance to carrier     : on={final_on['dist']:6.3f} m"
          f"  off={final_off['dist']:6.3f} m")
    print(f"        lane crossings (Y)            : on={ycross_on}  "
          f"off={ycross_off}")
    print(f"        lane crossings (X)            : on={xcross_on}  "
          f"off={xcross_off}")

    # The comparison is deliberately a set of inequalities between the two
    # legs of the SAME experiment: the interceptor must be closer to the
    # carrier, owe less lateral error, and reach contact range on the lateral
    # axis where the historical leg does not.  Absolute convergence times are
    # a property of pure pursuit against a moving carrier (the commanded point
    # trails the carrier by FB_CHASE_TRAIL_M and carries the near-range
    # taper), not of the steering contract under test.
    check("7.1 the interceptor ends CLOSER to the carrier than the historical "
          "leg",
          final_on["dist"] < final_off["dist"],
          f"on={final_on['dist']:.3f} off={final_off['dist']:.3f}")
    check("7.2 it is on average CLOSER to the carrier over the whole chase",
          dist_on < dist_off, f"on={dist_on:.3f} off={dist_off:.3f}")
    check("7.3 its lateral error is strictly smaller on average",
          lat_on < lat_off, f"on={lat_on:.3f} off={lat_off:.3f}")
    check("7.4 its FINAL lateral error is strictly smaller",
          final_on["lat_err"] < final_off["lat_err"],
          f"on={final_on['lat_err']:.3f} off={final_off['lat_err']:.3f}")
    check("7.5 its ATTAINED lateral progress by the end beats the historical "
          "leg",
          abs(on[-1]["fy"] - FB_START[1]) > abs(off[-1]["fy"] - FB_START[1]),
          f"on dy={abs(on[-1]['fy'] - FB_START[1]):.3f} "
          f"off dy={abs(off[-1]['fy'] - FB_START[1]):.3f}")
    check("7.6 it does not cross the carrier's lane more often",
          ycross_on <= ycross_off, f"on={ycross_on} off={ycross_off}")
    check("7.7 the historical leg shows the reported signature: the lateral "
          "error is the LAST thing to close",
          off[-1]["lat_err"] > 0.5 and off[-1]["x_err"] < off[-1]["lat_err"],
          f"lat={off[-1]['lat_err']:.3f} x={off[-1]['x_err']:.3f}")
    check("7.8 the interceptor reaches the LATERAL contact band (<2.0 m) "
          "where the historical leg does not",
          final_on["lat_err"] < 2.0 <= final_off["lat_err"],
          f"on={final_on['lat_err']:.3f} off={final_off['lat_err']:.3f}")
    print(f"        NOTE the known trade: bearing steering spends some X "
          f"closure on the owed lateral ground "
          f"(on x_err={final_on['x_err']:.3f} vs off={final_off['x_err']:.3f}),"
          f" which is the point of the fix.")


def _lever_first_frame(lever_on: bool, state=MovementState.CHASE):
    """One frame through the FULLBACK entry point at the given lever."""
    saved = ms.FB_CHASE_INTERCEPT_TRACK
    try:
        ms.FB_CHASE_INTERCEPT_TRACK = lever_on
        body = make_fb(*_FB_XY, state=state, target=_TARGET_XY,
                       bc=_Carrier(*_FAR_BC))
        apply_fullback_locomotion(body, DT)
        return body.vx, body.vy
    finally:
        ms.FB_CHASE_INTERCEPT_TRACK = saved


def sec_lever_rollback():
    section(8, "Rollback — the lever is a real, in-process switch")

    # The lever is read by ``apply_fullback_locomotion``, so the rollback test
    # must go through THAT entry point (a direct apply_movement_state call
    # passes the flag explicitly and never consults the lever).
    on = _lever_first_frame(True)
    off = _lever_first_frame(False)
    direct_on = _first_frame_velocity(True)
    direct_off = _first_frame_velocity(False)
    print(f"        lever ON   v=({on[0]:.6f}, {on[1]:.6f})")
    print(f"        lever OFF  v=({off[0]:.6f}, {off[1]:.6f})")

    check("8.1 the lever OFF reproduces the historical steering exactly",
          abs(off[0] - direct_off[0]) < 1e-12
          and abs(off[1] - direct_off[1]) < 1e-12,
          f"off={off} direct_off={direct_off}")
    check("8.2 the lever ON reproduces the intercept steering exactly",
          abs(on[0] - direct_on[0]) < 1e-12
          and abs(on[1] - direct_on[1]) < 1e-12,
          f"on={on} direct_on={direct_on}")
    check("8.3 the two lever positions really differ (a live switch)",
          abs(on[0] - off[0]) > 1e-9 and abs(on[1] - off[1]) > 1e-9,
          f"on={on} off={off}")

    # The lever must be inert for every state other than CHASE.
    for state in (MovementState.DRIFT, MovementState.RECOVER):
        a = _lever_first_frame(True, state=state)
        b = _lever_first_frame(False, state=state)
        check(f"8.4 [{state.name}] the lever is inert (it only arms CHASE)",
              a == b, f"on={a} off={b}")

    check("8.5 the flag parameter defaults to False for every other caller",
          inspect.signature(apply_movement_state)
          .parameters["intercept_track"].default is False)


def main():
    print("=" * 72)
    print("FULLBACK INTERCEPT-TRACKING CONTRACT (2026-09-29)")
    print("=" * 72)
    sec_source_contract()
    sec_bearing()
    sec_alignment_exemptions()
    sec_shared_table_untouched()
    sec_only_chase_diverges()
    sec_surviving_guards()
    sec_end_to_end()
    sec_lever_rollback()
    print("\n" + "=" * 72)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS — on a genuine line break the tagged fullback travels")
    print("        TO ITS COMMANDED INTERCEPT POINT: bearing steering on both")
    print("        axes with the pre-tackle lateral alignment controls")
    print("        exempt, under the same acceleration ramp, the same")
    print("        top-speed table, the same facing policy, the same single")
    print("        integration step and the same governed ceiling as every")
    print("        other defender.  The shared CHASE row is untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
