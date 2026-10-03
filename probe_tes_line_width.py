"""probe_tes_line_width.py — attribute the outward (Y) line fan during TES.

Boots the engine headless exactly like ``probe_drive_non_tackler_motion.py``
(kickoff -> catch -> OPEN_PLAY -> tackle -> TES drive) and measures, for the
defensive LINE bodies only (DEF_LINE_READY / DEF_CHASE / DEF_RETREAT_10, not a
tackler, not the fullback, not the carrier):

  * per side, the maximum ``|y - ruck_y|`` — the line's lateral half-width —
    captured at three moments:
        1. LAST OPEN_PLAY frame before the tackle (the pre-drive reference);
        2. every TES drive frame (CONTACT / DRIVE / DOMINANCE);
        3. the first OPEN_PLAY frame AFTER the drive (the PTB-reform width);

  * on the WIDEST drive frame, per body: depth_role, defender_role, depth_side,
    y, |y - ruck_y|, target_y, target_source, frame_spacing_y,
    role_depth_target_y, movement_state, and the TES structure-hold stamps
    (``_tes_hold_active`` / ``_tes_hold_y``) — so the fan can be attributed to
    the authority that set the target (``tes_drive_hold`` vs a retreat/spacing
    fallback), and to whether the hold's cell Y was seeded empty (falling back
    to live ``y``).

Exit 0 = the drive half-width exceeded the last-OPEN_PLAY half-width by more
than a tolerance (the fan reproduced, attribution printed).  Exit 1 = no fan
observed (nothing to fix) or no drive frame found.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False

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

DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
LINE_ROLES = ("DEF_LINE_READY", "DEF_CHASE", "DEF_RETREAT_10")
TACKLER_ROLES = ("TACKLER_1", "TACKLER_2", "TACKLER_3")
FAN_TOLERANCE_M = 0.75
MAX_FRAMES = 3000
ENGINE_LOG = "tmp_tes_line_width_engine.log"


def _role_name(player):
    role = getattr(player, "defender_role", None)
    return getattr(role, "name", None) or (str(role) if role is not None else None)


def _mstate(player):
    ms = getattr(player, "movement_state", None)
    return getattr(ms, "name", None) or (str(ms) if ms is not None else None)


def boot_world():
    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()
    return world, ball, open_play, kickoff_engine, collision_system, field


def line_bodies(world):
    """The defensive LINE bodies under test (no tacklers / FB / carrier)."""
    bc = world.ball_carrier
    out = []
    for d in world.defending_team:
        name = _role_name(d)
        if name not in LINE_ROLES:
            continue
        if getattr(d, "fullback_role_active", False):
            continue
        if d is bc:
            continue
        out.append(d)
    return out


def side_half_widths(world, ruck_y):
    """Return (left_max, right_max) |y - ruck_y| for the line bodies."""
    left = 0.0
    right = 0.0
    for d in line_bodies(world):
        delta = float(d.y) - float(ruck_y)
        if delta < 0.0:
            left = max(left, abs(delta))
        else:
            right = max(right, abs(delta))
    return left, right


def line_span(world):
    """Total lateral WIDTH of the line bodies: (ymin, ymax, span).

    This is THE "line width": the ruck-relative per-side deviation above is
    misleading when the ruck is off-centre or drifting (a correctly spaced
    wide line around a ruck near one touchline shows a huge ruck-relative
    "half width").  The span is directly comparable to def_line_width_m.
    """
    ys = [float(d.y) for d in line_bodies(world)]
    if not ys:
        return (0.0, 0.0, 0.0)
    lo, hi = min(ys), max(ys)
    return (lo, hi, hi - lo)


def snapshot_body(d, ruck_y):
    return {
        "pid": getattr(d, "player_id", "?"),
        "def_role": _role_name(d),
        "depth_role": getattr(d, "depth_role", None),
        "side": getattr(d, "depth_side", None),
        "y": float(getattr(d, "y", 0.0)),
        "dev": abs(float(getattr(d, "y", 0.0)) - float(ruck_y)),
        "target_y": getattr(d, "target_y", None),
        "src": getattr(d, "target_source", None),
        "frame_ty": getattr(d, "frame_spacing_y", None),
        "role_ty": getattr(d, "role_depth_target_y", None),
        "mstate": _mstate(d),
        "hold": bool(getattr(d, "_tes_hold_active", False)),
        "hold_y": getattr(d, "_tes_hold_y", None),
        "frozen": bool(getattr(d, "frozen", False)),
    }


def main():
    print("=" * 78)
    print("TES LINE WIDTH PROBE — does the line fan OUTWARD (Y) during the drive?")
    print("=" * 78)

    buf = io.StringIO()
    result = {}
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system, field = \
            boot_world()
        dt = 1.0 / 60.0

        last_open_play = None            # (ruck_y, left, right)
        drive_max = None                 # (frame, ruck_y, left, right, bodies)
        first_post_open_play = None
        drove = False
        drive_frames = 0
        series = []                      # per-drive-frame width samples

        for _ in range(MAX_FRAMES):
            world.frame_count += 1
            ball.update(dt)

            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(dt)
                check_for_catch(world, ball, dt)
                continue
            if world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if world.phase != GamePhase.OPEN_PLAY:
                continue

            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            phase = open_play._phase
            ruck_y = float(getattr(world, "ruck_y_m", 35.0) or 35.0)

            if phase not in DRIVE_PHASES:
                _sp = line_span(world)
                if not drove:
                    last_open_play = (
                        ruck_y, *side_half_widths(world, ruck_y), _sp[2]
                    )
                elif first_post_open_play is None:
                    first_post_open_play = (
                        ruck_y, *side_half_widths(world, ruck_y), _sp[2]
                    )
                continue

            # ── inside the drive chamber ──
            drove = True
            drive_frames += 1
            left, right = side_half_widths(world, ruck_y)
            bodies = [snapshot_body(d, ruck_y) for d in line_bodies(world)]
            governed = float(getattr(world, "def_line_width_m", 40.0) or 40.0)
            _line = line_bodies(world)
            n_left = sum(1 for d in _line if float(d.y) < ruck_y)
            n_right = len(_line) - n_left
            span = line_span(world)[2]
            series.append(
                (world.frame_count, phase, ruck_y, left, right,
                 governed, n_left, n_right, span)
            )
            if drive_max is None or span > drive_max[5]:
                drive_max = (
                    world.frame_count, ruck_y, left, right, bodies, span
                )

        result = {
            "last_open_play": last_open_play,
            "drive_max": drive_max,
            "first_post_open_play": first_post_open_play,
            "drive_frames": drive_frames,
        }

    with open(ENGINE_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())

    drive_frames = result["drive_frames"]
    print(f"\ndrive frames observed : {drive_frames}")
    print(f"last OPEN_PLAY ref    : {result['last_open_play']}")
    print(f"first post-drive OP   : {result['first_post_open_play']}")

    if drive_frames == 0 or result["drive_max"] is None:
        print("\nRESULT: FAILED — no TES drive frame observed.")
        return 1

    f, ruck_y, left, right, bodies, drive_span_abs = result["drive_max"]
    print(f"\nWIDEST drive frame #{f}: ruck_y={ruck_y:.2f} "
          f"left={left:.2f} right={right:.2f}")

    if series:
        spans = [s[8] for s in series]
        print(f"\nGOVERNED line width (world.def_line_width_m) = "
              f"{series[0][5]:.2f}")
        print(f"drive TOTAL line span: first={spans[0]:.2f} "
              f"min={min(spans):.2f} max={max(spans):.2f} last={spans[-1]:.2f}")
        print("\nfirst 6 drive frames (f, phase, ruck_y, L, R, gov, nL, nR):")
        for s in series[:6]:
            print(f"  {s[0]} {s[1]:<18} ruck_y={s[2]:.2f} L={s[3]:.2f} "
                  f"R={s[4]:.2f} gov={s[5]:.2f} nL={s[6]} nR={s[7]}")
        print("last 6 drive frames:")
        for s in series[-6:]:
            print(f"  {s[0]} {s[1]:<18} ruck_y={s[2]:.2f} L={s[3]:.2f} "
                  f"R={s[4]:.2f} gov={s[5]:.2f} nL={s[6]} nR={s[7]}")

    print("\n-- line bodies on the widest drive frame --")
    hdr = (f"  {'pid':<5}{'def_role':<16}{'depth':<7}{'side':<5}"
           f"{'y':>8}{'dev':>7}{'target_y':>10}{'src':<18}"
           f"{'frame_ty':>10}{'hold':<6}{'hold_y':>9}{'mstate':<9}")
    print(hdr)
    for b in sorted(bodies, key=lambda x: -x["dev"]):
        tgt = "-" if b["target_y"] is None else f"{float(b['target_y']):.2f}"
        fty = "-" if b["frame_ty"] is None else f"{float(b['frame_ty']):.2f}"
        hy = "-" if b["hold_y"] is None else f"{float(b['hold_y']):.2f}"
        print(
            f"  {str(b['pid']):<5}{str(b['def_role']):<16}"
            f"{str(b['depth_role']):<7}{str(b['side']):<5}"
            f"{b['y']:>8.2f}{b['dev']:>7.2f}{tgt:>10}"
            f"{str(b['src']):<18}{fty:>10}"
            f"{('Y' if b['hold'] else '-'):<6}{hy:>9}"
            f"{str(b['mstate']):<9}"
        )

    # ── verdict ──
    ref = result["last_open_play"]
    post = result["first_post_open_play"]
    drive_span = drive_span_abs
    ref_span = ref[3] if ref else None
    post_span = post[3] if post else None
    print(f"\ndrive line span   : {drive_span:.2f}")
    print(f"pre-drive (OP)    : "
          f"{('None' if ref_span is None else f'{ref_span:.2f}')}")
    print(f"post-drive (OP)   : "
          f"{('None' if post_span is None else f'{post_span:.2f}')}")

    fanned = ref_span is not None and (drive_span - ref_span) > FAN_TOLERANCE_M
    wider_than_ptb = post_span is not None and (drive_span - post_span) > FAN_TOLERANCE_M

    print("\nVERDICT:")
    print(f"  drive wider than pre-drive by > {FAN_TOLERANCE_M} m : {fanned}")
    print(f"  drive wider than post-drive (PTB) by > {FAN_TOLERANCE_M} m : "
          f"{wider_than_ptb}")

    print("\n" + "=" * 78)
    if fanned or wider_than_ptb:
        print("RESULT: OUTWARD Y FAN REPRODUCED — attribution above.")
        return 0
    print("RESULT: no outward fan observed within tolerance.")
    return 1


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
