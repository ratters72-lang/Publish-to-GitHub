"""verify_ball_physics.py -- the kicked-Steeden ball contract.

The ball was rewritten (2026-09-20) from a point mass with a single scalar
bounce into a modelled prolate spheroid: three independent attitude axes, a
kick-type-specific spin profile, speed-and-angle-dependent aerodynamic
wobble, asymmetric turf behaviour and an orientation-driven brown Steeden
silhouette.

That rewrite is only acceptable if it keeps the promises the rest of the
match depends on.  This verifier asserts exactly those promises, and the new
behaviour the rewrite was FOR:

  COMPATIBILITY (the ball must not break its callers)
    * the published landing point is a real prediction of the flight the
      ball actually flies (the kick-chase AI runs to it);
    * the public surface - position, velocity, `grounded`, `possessed_by`,
      `true_*`, `frozen`, `can_be_caught()`, `spawn_kickoff()`,
      `update(dt)`, `draw(screen)` - still exists and still behaves.

  DETERMINISM (the project is audit-heavy)
    * identical kicks replay bit-identically, and flying the ball never
      touches the global `random` stream.

  THE NEW PHYSICS
    * the four kick types genuinely differ in the air;
    * instability is born LATE and grows through the descent;
    * the wobble it produces stays small enough to be catchable;
    * a bounce does not mirror its launch (the spin makes it asymmetric);
    * landing on a point kills the bounce, landing broadside keeps it;
    * the rebound can both kick on and check back;
    * roll, pitch and yaw are genuinely independent;
    * the renderer produces a real oriented silhouette.

Run:  python verify_ball_physics.py
Exit 0 on success.
"""

import math
import os
import random
import sys

# The renderer builds pygame surfaces, so a display driver must exist even
# though this verifier never opens a window.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

sys.path.insert(0, ".")

import pygame

pygame.init()
pygame.display.set_mode((64, 64))

from ball.attitude import BallAttitude
from ball.ball import Ball
from ball.bounce import ROLL_RADIUS_M, resolve_bounce
from ball.kick_types import (
    BASELINE_BOUNCE,
    KICK_DEFAULT,
    KickType,
    profile_with,
    resolve_profile,
)
from ball.ball_renderer import build_shape, draw_ball, draw_shadow
from ball.ball_projection import Z_PX_PER_M

DT = 1.0 / 60.0

#: How far the published landing point may sit from where the ball actually
#: first touches the turf.  The predictor runs the same integrator, so this
#: is really a guard against the two code paths drifting apart again.
LANDING_TOLERANCE_M = 0.6

#: The wobble must keep the ball near its own line.  This is the cross-track
#: bound, and it is the one the catch system's 1.5 m horizontal radius cares
#: about - a ball that wanders a metre off its line is still caught by a
#: receiver who ran to the published landing point.
MAX_WOBBLE_ACROSS_M = 1.0

#: The wobble also nudges the downfield leg (it changes the flight slightly),
#: but it must not change it enough to move the landing point out of reach.
MAX_WOBBLE_ALONG_M = 1.5

FAILURES: list[str] = []


def check(condition: bool, message: str) -> bool:
    """Record and report one assertion."""
    if condition:
        print(f"  PASS: {message}")
        return True
    print(f"  FAIL: {message}")
    FAILURES.append(message)
    return False


class StubField:
    """The minimum Field surface the ball touches.

    Only ``metres_to_px`` and the metre dimensions are read, so the verifier
    can exercise the ball without standing up the whole world.
    """

    scale = 13.83
    length_m = 120.0
    width_m = 70.0

    def metres_to_px(self, x_m, y_m):
        return int(round(x_m * self.scale)), int(round(y_m * self.scale))


def new_ball() -> Ball:
    return Ball(StubField())


def fly(
    kick_type=KICK_DEFAULT,
    power: float = 26.0,
    angle_deg: float = 34.0,
    direction_deg: float = 180.0,
    max_steps: int = 900,
    stop_when_settled: bool = True,
):
    """Kick a ball and return a full read-out of what happened.

    Runs at the game's nominal 60 Hz so the numbers match live behaviour.
    """
    ball = new_ball()
    ball.kick(power, angle_deg, direction_deg, kick_type=kick_type)

    predicted = (ball.landing_x_m, ball.landing_y_m, ball.flight_time)

    peak_z = ball.z_m
    first_contact = None
    quarter_instability = None
    contact_instability = 0.0
    peak_instability = 0.0
    airborne_instability = 0.0

    # A quarter of the way through the predicted flight.  Instability is
    # sampled at that point and at the FIRST impact, so both readings come
    # from the primary flight - a reading taken after a bounce would be
    # measuring a different piece of geometry entirely.
    quarter_mark = predicted[2] * 0.25

    for step in range(max_steps):
        ball.update(DT)
        peak_z = max(peak_z, ball.z_m)

        if not ball.grounded:
            airborne_instability = ball.last_aero.instability
            peak_instability = max(peak_instability, airborne_instability)
            if quarter_instability is None and ball.time_airborne >= quarter_mark:
                quarter_instability = airborne_instability

        if first_contact is None and ball.z_m <= 0.0:
            first_contact = (ball.x_m, ball.y_m, ball.time_airborne)
            contact_instability = airborne_instability

        if stop_when_settled and ball.grounded and ball.speed_mps < 0.01 and step > 240:
            break

    return {
        "ball": ball,
        "predicted": predicted,
        "first_contact": first_contact,
        "peak_z": peak_z,
        "instability_quarter": quarter_instability or 0.0,
        "instability_contact": contact_instability,
        "instability_peak": peak_instability,
        "spin_at_kick": resolve_profile(kick_type).roll_rad_s(),
        "rest": (ball.x_m, ball.y_m),
    }


# =====================================================================
# 1. THE KICK TYPES GENUINELY DIFFER IN THE AIR
# =====================================================================
def test_kick_types_separate():
    print("\n[1] kick types are physically distinct in flight")

    spiral = fly(KickType.SPIRAL_PUNT)
    bomb = fly(KickType.BOMB)
    grubber = fly(KickType.GRUBBER)
    floater = fly(KickType.FLOATER)

    print(
        f"      flight time: spiral {spiral['ball'].flight_time:.2f}s  "
        f"bomb {bomb['ball'].flight_time:.2f}s  "
        f"grubber {grubber['ball'].flight_time:.2f}s  "
        f"floater {floater['ball'].flight_time:.2f}s"
    )
    print(
        f"      peak height: spiral {spiral['peak_z']:.1f}m  "
        f"bomb {bomb['peak_z']:.1f}m  "
        f"grubber {grubber['peak_z']:.1f}m  "
        f"floater {floater['peak_z']:.1f}m"
    )
    print(
        f"      launch spin: spiral {spiral['spin_at_kick']:.0f}  "
        f"bomb {bomb['spin_at_kick']:.0f}  "
        f"grubber {grubber['spin_at_kick']:.0f}  "
        f"floater {floater['spin_at_kick']:.0f} rad/s"
    )

    check(
        bomb["peak_z"] > spiral["peak_z"] * 1.3,
        "a bomb is a materially higher kick than a spiral punt",
    )
    check(
        grubber["peak_z"] < spiral["peak_z"] * 0.25,
        "a grubber stays low instead of flying",
    )
    check(
        floater["peak_z"] > spiral["peak_z"],
        "a floater hangs higher than a flat spiral",
    )
    check(
        spiral["ball"].flight_time < bomb["ball"].flight_time,
        "a spiral reaches the ground sooner than a bomb",
    )
    check(
        grubber["ball"].flight_time < 1.5,
        "a grubber is airborne for well under 1.5 s",
    )
    check(
        spiral["spin_at_kick"] > floater["spin_at_kick"] * 2.0,
        "a spiral carries far more axial spin than a floater",
    )


# =====================================================================
# 2. THE PUBLISHED LANDING POINT MATCHES THE FLIGHT THAT IS FLOWN
# =====================================================================
def test_landing_prediction_matches_flight():
    print("\n[2] published landing point predicts the real flight")

    for kick_type in (
        KickType.SPIRAL_PUNT,
        KickType.BOMB,
        KickType.GRUBBER,
        KickType.FLOATER,
    ):
        result = fly(kick_type)
        if result["first_contact"] is None:
            check(False, f"{kick_type.value}: never reached the ground")
            continue

        px, py, pt = result["predicted"]
        cx, cy, ct = result["first_contact"]
        miss = math.hypot(cx - px, cy - py)
        time_error = abs(ct - pt)

        print(
            f"      {kick_type.value:<12} predicted ({px:7.2f},{py:6.2f}) t={pt:4.2f}  "
            f"actual ({cx:7.2f},{cy:6.2f}) t={ct:4.2f}  miss={miss:5.2f} m"
        )

        check(
            miss < LANDING_TOLERANCE_M,
            f"{kick_type.value}: landing point within {LANDING_TOLERANCE_M} m "
            f"(missed by {miss:.2f} m)",
        )
        check(
            time_error < 0.15,
            f"{kick_type.value}: predicted flight time within 0.15 s "
            f"(off by {time_error:.2f} s)",
        )


# =====================================================================
# 3. INSTABILITY IS BORN LATE AND GROWS THROUGH THE DESCENT
# =====================================================================
def test_late_instability_grows():
    print("\n[3] instability grows through the descent, not at launch")

    spiral = fly(KickType.SPIRAL_PUNT)
    bomb = fly(KickType.BOMB)
    floater = fly(KickType.FLOATER)

    for name, result in (
        ("spiral", spiral),
        ("bomb", bomb),
        ("floater", floater),
    ):
        print(
            f"      {name:<8} instability: quarter-flight "
            f"{result['instability_quarter']:.3f}  ->  first impact "
            f"{result['instability_contact']:.3f}   "
            f"(peak over the flight {result['instability_peak']:.3f})"
        )

    check(
        bomb["instability_contact"] > bomb["instability_quarter"] + 0.10,
        "the bomb destabilises as the flight progresses",
    )
    check(
        floater["instability_contact"] > floater["instability_quarter"] + 0.10,
        "the floater destabilises as the flight progresses",
    )
    check(
        floater["instability_contact"] > 0.50,
        "the floater arrives at the ground genuinely unstable",
    )
    check(
        spiral["instability_contact"] < floater["instability_contact"],
        "a tight spiral stays more stable than a floater",
    )


# =====================================================================
# 4. THE WOBBLE IS REAL BUT BOUNDED
# =====================================================================
def test_wobble_bounded():
    print("\n[4] the wobble moves the ball, but not out of reach")

    for kick_type in (
        KickType.SPIRAL_PUNT,
        KickType.BOMB,
        KickType.FLOATER,
    ):
        # The twin is the identical kick with the wobble and the attitude
        # wander switched off, so the difference between the two runs IS the
        # wobble's total footprint on the flight.
        live = new_ball()
        live.kick(26.0, 34.0, 180.0, kick_type=kick_type)

        quiet = new_ball()
        quiet.profile = profile_with(kick_type, wobble_gain=0.0)
        quiet.kick(26.0, 34.0, 180.0)

        # The intended flight line, so the divergence can be split into
        # "across the line" (the visible wander, bounded by the catch radius)
        # and "along the line" (a change of pace or flight time).  Both
        # matter to a chasing receiver; only the cross-track part is what the
        # horizontal catch radius bounds.
        azimuth = math.atan2(quiet.vy, quiet.vx)
        across_x, across_y = -math.sin(azimuth), math.cos(azimuth)

        worst_across = 0.0
        worst_along = 0.0
        for _ in range(600):
            live.update(DT)
            quiet.update(DT)
            if live.grounded or quiet.grounded:
                break
            dx = live.x_m - quiet.x_m
            dy = live.y_m - quiet.y_m
            worst_across = max(
                worst_across, abs(dx * across_x + dy * across_y)
            )
            worst_along = max(worst_along, abs(dx * -across_y + dy * across_x))

        print(
            f"      {kick_type.value:<12} wobble = {worst_across:.3f} m across "
            f"its line, {worst_along:.3f} m along it"
        )
        check(
            worst_across > 0.005,
            f"{kick_type.value}: the wobble moves the ball across its line",
        )
        check(
            worst_across < MAX_WOBBLE_ACROSS_M,
            f"{kick_type.value}: stays within {MAX_WOBBLE_ACROSS_M} m of its "
            f"line ({worst_across:.3f} m)",
        )
        check(
            worst_along < MAX_WOBBLE_ALONG_M,
            f"{kick_type.value}: the downfield leg moves by less than "
            f"{MAX_WOBBLE_ALONG_M} m ({worst_along:.3f} m)",
        )

    # A spiral must be tighter than a floater - that is the whole point of
    # the spin profile.
    spiral = fly(KickType.SPIRAL_PUNT)
    floater = fly(KickType.FLOATER)
    check(
        spiral["instability_peak"] < floater["instability_peak"],
        "the spiral's worst instability stays below the floater's",
    )
# =====================================================================
# 5. A BOUNCE DOES NOT MIRROR ITS LAUNCH (the spin breaks the symmetry)
# =====================================================================
def test_bounce_is_asymmetric():
    print("\n[5] the bounce depends on the spin, not only on the velocity")

    profile = resolve_profile(KickType.SPIRAL_PUNT)

    def attitude(pitch_deg, yaw_deg, roll_rad_s):
        a = BallAttitude()
        a.pitch = math.radians(pitch_deg)
        a.yaw = math.radians(yaw_deg)
        a.roll_rate = roll_rad_s
        return a

    inbound = (-20.0, 0.0, -8.0)

    # Same velocity, same long axis, OPPOSITE axial spin.
    forward_spin = resolve_bounce(
        *inbound,
        attitude(0.0, 180.0, 44.0),
        profile,
        BASELINE_BOUNCE,
    )
    back_spin = resolve_bounce(
        *inbound,
        attitude(0.0, 180.0, -44.0),
        profile,
        BASELINE_BOUNCE,
    )

    print(
        f"      roll +44 rad/s -> rebound ({forward_spin.vx:6.2f}, "
        f"{forward_spin.vy:6.2f}, {forward_spin.vz:5.2f})  "
        f"squirt {forward_spin.squirt_mps:.2f}"
    )
    print(
        f"      roll -44 rad/s -> rebound ({back_spin.vx:6.2f}, "
        f"{back_spin.vy:6.2f}, {back_spin.vz:5.2f})  "
        f"squirt {back_spin.squirt_mps:.2f}"
    )

    spin_difference = math.hypot(
        forward_spin.vx - back_spin.vx,
        forward_spin.vy - back_spin.vy,
    )
    check(
        spin_difference > 0.05,
        "reversing the axial spin changes the rebound "
        f"(differed by {spin_difference:.3f} m/s)",
    )

    # A NON-spinning ball still gets tangential friction - it is SLIDING
    # across the turf - but that friction must act purely along the slip.
    # So a ball with no spin and no sidespin must be slowed, never squirted
    # sideways.  This is what shows the asymmetry above is the SPIN's doing
    # rather than a sign error in the reflection.
    dead = resolve_bounce(
        *inbound,
        attitude(0.0, 180.0, 0.0),
        profile,
        BASELINE_BOUNCE,
    )
    print(
        f"      no spin        -> squirt ({dead.squirt_x:6.2f}, {dead.squirt_y:6.2f})"
    )
    check(
        abs(dead.squirt_y) < 1e-9,
        "friction on a non-spinning ball has no sideways component",
    )
    check(
        dead.squirt_x * inbound[0] < 0.0,
        "friction on a non-spinning ball opposes its travel",
    )
    check(
        dead.vz > 0.0,
        "a bouncing ball leaves the ground",
    )

    # ROLLING WITHOUT SLIPPING: the tumble exactly cancels the ball's own
    # horizontal motion at the contact point, so the slip is zero and there
    # is no tangential impulse at all.  This is the single strongest check
    # that the friction is genuinely slip-driven rather than arbitrary.
    rolling = attitude(0.0, 0.0, 0.0)
    rolling.pitch_rate = inbound[0] / ROLL_RADIUS_M
    rolled = resolve_bounce(*inbound, rolling, profile, BASELINE_BOUNCE)

    print(
        f"      rolling clean  -> squirt ({rolled.squirt_x:6.2f}, "
        f"{rolled.squirt_y:6.2f})"
    )
    check(
        abs(rolled.squirt_mps) < 1e-6,
        "a ball rolling without slipping takes no tangential impulse",
    )


# =====================================================================
# 6. LANDING ON A POINT KILLS THE BOUNCE
# =====================================================================
def test_point_first_bounce_dies():
    print("\n[6] a point-first landing is dead, a broadside landing is lively")

    profile = resolve_profile(KickType.SPIRAL_PUNT)
    vx, vy, vz = -20.0, 0.0, -8.0

    # Point-first: the long axis lies ALONG the velocity.
    point = BallAttitude()
    point.yaw = math.pi
    point.pitch = -math.atan2(8.0, 20.0)

    # Broadside: the long axis lies ACROSS the velocity.
    side = BallAttitude()
    side.yaw = math.pi * 0.5
    side.pitch = 0.0

    point_end_on = point.end_on_fraction(vx, vy, vz)
    side_end_on = side.end_on_fraction(vx, vy, vz)

    point_bounce = resolve_bounce(vx, vy, vz, point, profile, BASELINE_BOUNCE)
    side_bounce = resolve_bounce(vx, vy, vz, side, profile, BASELINE_BOUNCE)

    print(
        f"      point-first: end_on={point_end_on:.2f}  "
        f"restitution={point_bounce.restitution:.3f}  vz_out={point_bounce.vz:.2f}"
    )
    print(
        f"      broadside  : end_on={side_end_on:.2f}  "
        f"restitution={side_bounce.restitution:.3f}  vz_out={side_bounce.vz:.2f}"
    )

    check(
        abs(point_end_on - 1.0) < 0.01,
        "a nose-along-the-velocity attitude reads as fully end-on",
    )
    check(
        side_end_on < 0.01,
        "a nose-across-the-velocity attitude reads as fully broadside",
    )
    check(
        side_bounce.restitution > point_bounce.restitution * 1.5,
        "the broadside landing is far livelier than the point-first landing",
    )
    check(
        point_bounce.vz < side_bounce.vz,
        "landing on a point keeps the ball low",
    )


# =====================================================================
# 7. THE REBOUND CAN BOTH KICK ON AND CHECK BACK
# =====================================================================
def test_kick_on_and_check_back_reachable():
    print("\n[7] the rebound can kick on AND check back")

    profile = resolve_profile(KickType.GRUBBER)

    kick_on_values: list[float] = []

    # Sweep the tumble phase (where the nose is pointing when it lands) and
    # the horizontal pace.  A rugby ball's rebound direction depends on both.
    for horizontal in (4.0, 10.0, 18.0):
        for pitch_deg in range(-90, 91, 15):
            attitude = BallAttitude()
            attitude.yaw = math.pi
            attitude.pitch = math.radians(pitch_deg)
            attitude.pitch_rate = 40.0

            result = resolve_bounce(
                -horizontal,
                0.0,
                -7.0,
                attitude,
                profile,
                BASELINE_BOUNCE,
            )
            kick_on_values.append(result.kick_on_mps)

    kicked_on = [v for v in kick_on_values if v > 0.05]
    checked_back = [v for v in kick_on_values if v < -0.05]

    print(
        f"      {len(kick_on_values)} bounces sampled: "
        f"{len(kicked_on)} kicked on, {len(checked_back)} checked back"
    )
    print(
        f"      kick_on range: {min(kick_on_values):+.2f} .. "
        f"{max(kick_on_values):+.2f} m/s"
    )

    check(
        len(kicked_on) > 0,
        "at least one attitude kicks the ball forward on landing",
    )
    check(
        len(checked_back) > 0,
        "at least one attitude checks the ball back on landing",
    )

    # The rebound direction must depend on WHERE THE NOSE IS POINTING, not
    # merely on the incoming velocity: two landings at the same speed with
    # the nose mirrored must not give the same rebound.
    nose_down = BallAttitude()
    nose_down.yaw = math.pi
    nose_down.pitch = math.radians(-30.0)
    nose_up = BallAttitude()
    nose_up.yaw = math.pi
    nose_up.pitch = math.radians(30.0)

    down = resolve_bounce(-14.0, 0.0, -7.0, nose_down, profile, BASELINE_BOUNCE)
    up = resolve_bounce(-14.0, 0.0, -7.0, nose_up, profile, BASELINE_BOUNCE)

    print(
        f"      nose -30 deg -> kick_on {down.kick_on_mps:+.2f} m/s   "
        f"nose +30 deg -> kick_on {up.kick_on_mps:+.2f} m/s"
    )
    check(
        abs(down.kick_on_mps - up.kick_on_mps) > 0.05,
        "the nose angle at contact changes whether the ball kicks on",
    )


# =====================================================================
# 8. DETERMINISM, AND THE GLOBAL RNG IS LEFT ALONE
# =====================================================================
def test_determinism_and_rng_untouched():
    print("\n[8] the ball is deterministic and never touches the global RNG")

    first = fly(KickType.FLOATER)
    second = fly(KickType.FLOATER)

    a = first["ball"]
    b = second["ball"]
    check(
        (a.x_m, a.y_m) == (b.x_m, b.y_m),
        "an identical kick replays to an identical resting position",
    )
    check(
        a.attitude.snapshot() == b.attitude.snapshot(),
        "an identical kick replays to an identical attitude",
    )

    # The control draw and the draw taken AFTER a full flight must agree, so
    # the ball cannot have consumed (or reseeded) the process RNG.
    random.seed(20260920)
    control = [random.random() for _ in range(6)]

    random.seed(20260920)
    fly(KickType.BOMB)
    after_flight = [random.random() for _ in range(6)]

    check(
        control == after_flight,
        "flying the ball leaves the global random stream untouched",
    )


# =====================================================================
# 9. ROLL, PITCH AND YAW ARE GENUINELY INDEPENDENT
# =====================================================================
def test_axes_are_independent():
    print("\n[9] roll, pitch and yaw are independent axes")

    base = BallAttitude()
    base.pitch = math.radians(20.0)
    base.yaw = math.radians(35.0)

    axis_before = base.long_axis()
    elevation_before = base.nose_elevation_deg()
    azimuth_before = math.atan2(axis_before[1], axis_before[0])

    # A SHORT window is used throughout.  Measuring the chord of a near-full
    # revolution is meaningless - the axis arrives back where it started -
    # so the assertions are made against each axis's OWN angle instead.
    window = 0.05
    steps = int(round(window / DT))

    # Roll must advance the roll angle and NOT move the long axis: that
    # independence is exactly what makes it the spiral rather than a tumble.
    rolling = BallAttitude()
    rolling.pitch = base.pitch
    rolling.yaw = base.yaw
    rolling.roll_rate = 44.0
    for _ in range(steps):
        rolling.step(DT)
    axis_after_roll = rolling.long_axis()
    roll_shift = math.hypot(
        axis_after_roll[0] - axis_before[0],
        axis_after_roll[1] - axis_before[1],
        axis_after_roll[2] - axis_before[2],
    )
    print(
        f"      after roll : roll={rolling.roll:.3f} rad "
        f"(expected {44.0 * window:.3f}), long axis moved {roll_shift:.6f}"
    )
    check(
        roll_shift < 1e-9,
        "axial roll does not change where the nose points",
    )
    check(
        abs(rolling.roll - 44.0 * window) < 1e-6,
        "axial roll advances the roll angle at its own rate",
    )

    # Pitch must tumble the nose end over end, and must not touch the yaw.
    tumbling = BallAttitude()
    tumbling.pitch = base.pitch
    tumbling.yaw = base.yaw
    tumbling.pitch_rate = 6.0
    for _ in range(steps):
        tumbling.step(DT)
    elevation_after = tumbling.nose_elevation_deg()
    print(
        f"      after pitch: nose elevation {elevation_before:.1f} -> "
        f"{elevation_after:.1f} deg, yaw moved "
        f"{abs(tumbling.yaw - base.yaw):.6f} rad"
    )
    check(
        abs(elevation_after - elevation_before) > 10.0,
        "pitch spin tumbles the nose end over end",
    )
    check(
        abs(tumbling.yaw - base.yaw) < 1e-9,
        "pitch spin does not precess the nose",
    )

    # Yaw must precess the azimuth while leaving the elevation alone.
    precessing = BallAttitude()
    precessing.pitch = base.pitch
    precessing.yaw = base.yaw
    precessing.yaw_rate = 6.0
    for _ in range(steps):
        precessing.step(DT)
    axis_after_yaw = precessing.long_axis()
    azimuth_after = math.atan2(axis_after_yaw[1], axis_after_yaw[0])
    print(
        f"      after yaw  : azimuth {math.degrees(azimuth_before):.1f} -> "
        f"{math.degrees(azimuth_after):.1f} deg, elevation moved "
        f"{abs(precessing.nose_elevation_deg() - elevation_before):.6f} deg"
    )
    check(
        abs(math.degrees(azimuth_after - azimuth_before)) > 10.0,
        "yaw spin precesses the nose around the vertical",
    )
    check(
        abs(precessing.nose_elevation_deg() - elevation_before) < 1e-6,
        "yaw spin does not change the nose elevation",
    )

    # The fractions must classify a pure spin correctly.
    spiral_only = BallAttitude()
    spiral_only.roll_rate = 30.0
    tumble_only = BallAttitude()
    tumble_only.pitch_rate = 30.0
    check(
        abs(spiral_only.spiral_fraction() - 1.0) < 1e-9,
        "a pure axial spin reads as a pure spiral",
    )
    check(
        abs(tumble_only.tumble_fraction() - 1.0) < 1e-9,
        "a pure pitch spin reads as a pure tumble",
    )


# =====================================================================
# 10. THE PUBLIC API THE REST OF THE MATCH USES IS STILL THERE
# =====================================================================
def test_public_api_preserved():
    print("\n[10] the public ball surface is preserved")

    ball = new_ball()

    for name in (
        "x_m", "y_m", "z_m", "vx", "vy", "vz",
        "gravity", "drag", "bounce_factor", "roll_friction",
        "grounded", "kicked", "frozen",
        "true_x_m", "true_y_m",
        "landing_x_m", "landing_y_m", "flight_time",
        "possessed_by", "catch_height_m", "rotation", "time_airborne",
    ):
        if not hasattr(ball, name):
            check(False, f"attribute {name!r} is missing from the ball")
            return
    check(True, "every attribute the match reads still exists")

    for name in (
        "kick", "update", "spawn_kickoff", "can_be_caught", "draw",
        "set_kick_type", "predict_landing",
    ):
        if not callable(getattr(ball, name, None)):
            check(False, f"method {name}() is missing from the ball")
            return
    check(True, "every method the match calls still exists")

    # Catch threshold behaviour, unchanged.
    ball.possessed_by = None
    ball.grounded = False
    ball.z_m = ball.catch_height_m + 0.5
    check(
        ball.can_be_caught() is False,
        "a ball above the catch height cannot be caught",
    )
    ball.z_m = ball.catch_height_m - 0.5
    check(
        ball.can_be_caught() is True,
        "a ball below the catch height can be caught",
    )
    ball.grounded = True
    check(
        ball.can_be_caught() is True,
        "a grounded ball can always be picked up",
    )

    # is_in_air and the predicted_landing_* aliases.
    ball.grounded = False
    ball.possessed_by = None
    check(ball.is_in_air is True, "is_in_air is true for a loose ball in flight")
    ball.possessed_by = object()
    check(ball.is_in_air is False, "is_in_air is false while the ball is carried")

    ball.possessed_by = None
    ball.landing_x_m = 41.5
    ball.landing_y_m = 22.25
    check(
        ball.predicted_landing_x == 41.5 and ball.predicted_landing_y == 22.25,
        "predicted_landing_x / _y still mirror landing_x_m / _y_m",
    )

    # spawn_kickoff resets position and state.
    ball.spawn_kickoff()
    check(
        (ball.x_m, ball.y_m, ball.z_m) == (60.0, 35.0, 1.0),
        "spawn_kickoff puts the ball back on the halfway line",
    )
    check(
        ball.grounded is False and ball.possessed_by is None,
        "spawn_kickoff clears grounded and possession",
    )
    check(
        ball.attitude.spin_magnitude() == 0.0,
        "spawn_kickoff clears the attitude spin",
    )

    # The possessed branch keeps the exact legacy easing.
    class StubCarrier:
        x = 70.0
        y = 20.0
        facing = 0.0

    carrier = StubCarrier()
    ball.possessed_by = carrier
    ball.x_m = 60.0
    ball.y_m = 35.0
    ball.z_m = 0.0
    ball.vx = 4.0
    ball.vy = 2.0
    ball.vz = -3.0
    ball.update(DT)

    check(
        (ball.true_x_m, ball.true_y_m) == (carrier.x, carrier.y),
        "a possessed ball snaps its true position to the carrier",
    )
    check(
        abs(ball.x_m - (60.0 + (70.0 - 60.0) * 0.35)) < 1e-9,
        "the visual ball eases toward the carrier at the legacy 0.35 blend",
    )
    check(
        abs(ball.z_m - (0.0 + (1.0 - 0.0) * 0.35)) < 1e-9,
        "the carried ball eases up to carrying height at the legacy 0.35 blend",
    )
    check(
        abs(ball.vx - 2.0) < 1e-9 and abs(ball.vy - 1.0) < 1e-9 and ball.vz == 0.0,
        "carrying halves the horizontal velocity and zeroes the vertical",
    )
    check(
        ball.grounded is False,
        "a carried ball is not grounded",
    )
    check(
        ball.rotation == math.degrees(ball.attitude.roll),
        "rotation is published from the ball's real roll",
    )

    # A settled ball must not produce NaN.
    ball.possessed_by = None
    ball.spawn_kickoff()
    ball.z_m = 0.0
    ball.grounded = True
    ball.vx = 0.0
    ball.vy = 0.0
    for _ in range(120):
        ball.update(DT)
    check(
        all(
            math.isfinite(v)
            for v in (ball.x_m, ball.y_m, ball.z_m, ball.vx, ball.vy, ball.vz)
        ),
        "a settled ball stays numerically sane",
    )

    # world_state writes these directly, so they must be plain writable state.
    ball.frozen = True
    check(ball.frozen is True, "frozen is a plain writable attribute")


# =====================================================================
# 11. THE RENDERER DRAWS A REAL, ORIENTED STEEDEN
# =====================================================================
def test_renderer_orientation():
    print("\n[11] the renderer produces an orientation-driven silhouette")

    field = StubField()
    ball = new_ball()
    ball.x_m = 60.0
    ball.y_m = 35.0
    ball.z_m = 2.0

    shapes = {}
    for name, pitch_deg, yaw_deg in (
        ("broadside", 0.0, 0.0),
        ("point-on", 90.0, 0.0),
        ("angled", 35.0, 60.0),
    ):
        ball.attitude = BallAttitude()
        ball.attitude.pitch = math.radians(pitch_deg)
        ball.attitude.yaw = math.radians(yaw_deg)

        shape = build_shape(ball, field)
        if shape is None:
            check(False, f"{name}: build_shape returned nothing")
            return
        if not (
            math.isfinite(shape.centre_x)
            and math.isfinite(shape.centre_y)
            and math.isfinite(shape.half_length_px)
            and math.isfinite(shape.half_width_px)
        ):
            check(False, f"{name}: the silhouette geometry is not finite")
            return
        shapes[name] = shape
        print(
            f"      {name:<10} centre=({shape.centre_x:6.1f},{shape.centre_y:6.1f})  "
            f"half_length={shape.half_length_px:5.1f}px  "
            f"half_width={shape.half_width_px:5.1f}px"
        )

    check(
        shapes["broadside"].half_length_px > shapes["point-on"].half_length_px,
        "a broadside ball is drawn longer than one end-on to the camera",
    )
    check(
        shapes["angled"].half_length_px
        != shapes["broadside"].half_length_px,
        "tilting the ball changes the silhouette",
    )

    # 2 m of height must lift the drawn ball by 2 * Z_PX_PER_M, because the
    # render loop's own lift uses that same constant.
    ball.attitude = BallAttitude()
    ball.z_m = 0.0
    on_ground = build_shape(ball, field)
    ball.z_m = 2.0
    lifted = build_shape(ball, field)
    lift_px = on_ground.centre_y - lifted.centre_y

    check(
        abs(lift_px - 2.0 * Z_PX_PER_M) < 0.51,
        f"the 2 m height lift matches Z_PX_PER_M "
        f"({lift_px:.1f}px vs {2.0 * Z_PX_PER_M:.1f}px)",
    )

    # Drawing must paint the ball and must not raise.
    canvas = pygame.Surface((400, 400))
    canvas.fill((0, 0, 0))
    ball.attitude = BallAttitude()
    ball.attitude.pitch = math.radians(20.0)
    ball.x_m = 10.0
    ball.y_m = 12.0
    ball.z_m = 3.0

    shape = build_shape(ball, field)
    draw_shadow(canvas, ball, field, shape)
    draw_ball(canvas, ball, field, ball.speed_mps)

    lit = 0
    for x in range(400):
        for y in range(400):
            if canvas.get_at((x, y))[:3] != (0, 0, 0):
                lit += 1
    check(lit > 100, f"drawing paints the ball on the surface ({lit} pixels)")

    # Colour check: the ball is leather, so the drawn pixels are brown -
    # red channel dominant, and definitely not the old flat white.
    reds = greens = blues = 0
    for x in range(400):
        for y in range(400):
            r, g, b = canvas.get_at((x, y))[:3]
            reds += r
            greens += g
            blues += b
    if lit:
        mean_r = reds / lit
        mean_g = greens / lit
        mean_b = blues / lit
        print(
            f"      mean drawn colour = ({mean_r:.0f}, {mean_g:.0f}, {mean_b:.0f})"
        )
        check(
            mean_r > mean_b,
            "the drawn ball is warm leather, not a blue/white ball",
        )
        check(
            mean_r >= mean_g,
            "the drawn ball reads as brown (red channel leads green)",
        )

    # A sprite sheet for visual inspection - the only way to see the seams
    # and the lace without opening the match.  It is drawn MAGNIFIED: at the
    # game's 13.83 px/m a real 0.28 m ball is only about 17 px long, which is
    # the right size to play with but far too small to judge the artwork at.
    CELL = 150
    MAGNIFY = 6.0

    class MagnifiedField(StubField):
        """The same field, drawn six times larger, for inspection only."""

        scale = StubField.scale * MAGNIFY

    big = MagnifiedField()
    sheet = pygame.Surface((7 * CELL, 4 * CELL))
    sheet.fill((18, 42, 18))
    for row in range(4):
        for col in range(7):
            phase = BallAttitude()
            phase.yaw = math.radians(col * 26.0)
            phase.pitch = math.radians(row * 30.0 - 45.0)
            phase.roll = math.radians(col * 45.0)
            show = new_ball()
            show.attitude = phase
            show.x_m = (col * CELL + CELL * 0.5) / big.scale
            show.y_m = (row * CELL + CELL * 0.5) / big.scale
            show.z_m = 0.0

            pygame.draw.rect(sheet, (24, 56, 24), (col * CELL, row * CELL, CELL, CELL))
            if build_shape(show, big) is None:
                continue
            draw_ball(sheet, show, big, 0.0)

    os.makedirs("tmp", exist_ok=True)
    pygame.image.save(sheet, os.path.join("tmp", "ball_sprite_sheet.png"))
    print(
        "      wrote tmp/ball_sprite_sheet.png for visual inspection "
        f"(magnified x{MAGNIFY})"
    )
    check(True, "the sprite sheet was written")


# =====================================================================
# MAIN
# =====================================================================
def main():
    print("=" * 72)
    print("verify_ball_physics -- kicked Steeden contract")
    print("=" * 72)

    test_kick_types_separate()
    test_landing_prediction_matches_flight()
    test_late_instability_grows()
    test_wobble_bounded()
    test_bounce_is_asymmetric()
    test_point_first_bounce_dies()
    test_kick_on_and_check_back_reachable()
    test_determinism_and_rng_untouched()
    test_axes_are_independent()
    test_public_api_preserved()
    test_renderer_orientation()

    print("\n" + "=" * 72)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} assertion(s)")
        for message in FAILURES:
            print(f"  - {message}")
        print("=" * 72)
        return 1

    print("ALL BALL PHYSICS CHECKS PASSED")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
