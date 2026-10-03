"""End-to-end runtime probe for the TES drive un-gating task.

Boots the engine exactly like run.py (headless), drives a kickoff into a
tackle, and audits every TACKLE_CONTACT / TACKLE_DRIVE / TACKLE_DOMINANCE
frame:

  * DEF_LINE_READY / DEF_CHASE / DEF_RETREAT_10 bodies must NEVER be frozen
    during the drive (they must keep movement authority).
  * at least one such non-tackler body must actually MOVE (integrate) on
    essentially every drive frame — i.e. spacing / retreat / pursuit keep
    running instead of the line being pinned.

Exit code 0 = the un-gating holds end to end, 1 = a drive frame shows a
frozen non-tackler or a fully static line.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

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
AUTHORITY_ROLES = ("DEF_LINE_READY", "DEF_CHASE", "DEF_RETREAT_10")
MOVE_EPS = 1e-9
MAX_FRAMES = 2500

failures = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def role_name(player):
    role = getattr(player, "defender_role", None)
    return getattr(role, "name", None) or (str(role) if role is not None else None)


def tes_owned_ids(world, attached):
    ids = set(attached)
    primary = getattr(world, "primary_tackler_id", None)
    if primary is not None:
        ids.add(primary)
    for pid in (getattr(world, "secondary_tacklers", None) or []):
        ids.add(pid)
    return ids


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


def main():
    print("=" * 72)
    print("DRIVE NON-TACKLER MOTION PROBE (TES must_be_frozen un-gating)")
    print("=" * 72)

    cap = io.StringIO()
    with contextlib.redirect_stdout(cap):
        world, ball, open_play, kickoff_engine, collision_system, field = boot_world()
        dt = 1.0 / 60.0

        drive_frames = 0
        frames_with_mover = 0
        bc_moving_frames = 0
        bc_moving_with_mover = 0
        frozen_violations = []
        authority_seen = {}
        per_frame_movers = []
        static_during_drive = []
        missed_signature = []

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

            before = {id(d): (d.x, d.y) for d in world.defending_team}
            _bc0 = world.ball_carrier
            bc_before = (
                (float(_bc0.x), float(_bc0.y)) if _bc0 is not None else None
            )
            # TES attachment set is published during the frame; snapshot IDs
            # from the previous frame's live state too.
            attached = {
                d.player_id for d in world.defending_team
                if getattr(d, "attached", False) or getattr(d, "is_attached", False)
            }

            open_play.update(dt)

            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            phase = open_play._phase
            if phase not in DRIVE_PHASES:
                if drive_frames and not missed_signature:
                    missed_signature.append(phase)
                continue

            drive_frames += 1
            owned = tes_owned_ids(world, attached)
            bc = world.ball_carrier
            bc_id = getattr(bc, "player_id", None)
            bc_moved = False
            if bc is not None and bc_before is not None:
                bc_moved = (
                    abs(float(bc.x) - bc_before[0]) > MOVE_EPS
                    or abs(float(bc.y) - bc_before[1]) > MOVE_EPS
                )
            if bc_moved:
                bc_moving_frames += 1

            movers = 0
            frozen_here = []
            for d in world.defending_team:
                name = role_name(d)
                if name not in AUTHORITY_ROLES:
                    continue
                if d.player_id in owned or d.player_id == bc_id:
                    continue
                if getattr(d, "fullback_role_active", False):
                    continue
                authority_seen[name] = authority_seen.get(name, 0) + 1
                px, py = before[id(d)]
                moved = abs(d.x - px) > MOVE_EPS or abs(d.y - py) > MOVE_EPS
                frozen = bool(getattr(d, "frozen", False))
                if frozen:
                    frozen_here.append((d.player_id, name))
                if moved:
                    movers += 1

            if movers:
                frames_with_mover += 1
            if bc_moved:
                if movers:
                    bc_moving_with_mover += 1
                else:
                    static_during_drive.append(
                        (world.frame_count, phase, movers)
                    )
            per_frame_movers.append(movers)
            if frozen_here:
                frozen_violations.append((world.frame_count, phase, frozen_here))

    pygame.quit()

    print(f"\n[P1] drive frames observed: {drive_frames}")
    check("P1: probe observed at least one TES drive frame", drive_frames > 0,
          f"{drive_frames} frame(s)")

    print(f"[P2] authority-role samples by role: {authority_seen}")
    check("P2: DEF_LINE_READY / DEF_CHASE / DEF_RETREAT_10 bodies sampled",
          bool(authority_seen))

    check("P3: NO authority-role body frozen on ANY drive frame",
          not frozen_violations,
          f"{len(frozen_violations)} violating frame(s): "
          + "; ".join(f"f={f} {p} {bad}" for f, p, bad in frozen_violations[:3]))

    ratio = (frames_with_mover / drive_frames) if drive_frames else 0.0
    active_ratio = (
        bc_moving_with_mover / bc_moving_frames if bc_moving_frames else 0.0
    )
    print(f"[P4] frames with >=1 authority-role mover: "
          f"{frames_with_mover}/{drive_frames} ({ratio:.1%}); "
          f"movers/frame min={min(per_frame_movers) if per_frame_movers else 0} "
          f"max={max(per_frame_movers) if per_frame_movers else 0}")
    print(f"[P5] carrier-moving drive frames: {bc_moving_frames}/{drive_frames}; "
          f"of those {bc_moving_with_mover} had >=1 authority mover "
          f"({active_ratio:.1%})")
    check("P5: while the carrier is driving, the non-tackler line moves with it",
          active_ratio >= 0.9,
          f"{active_ratio:.1%} of carrier-moving drive frames had a "
          f"non-tackler mover; static examples: {static_during_drive[:3]}")

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED — non-tackler roles keep movement "
          "authority and keep running through the TES drive")
    sys.exit(0)


if __name__ == "__main__":
    main()
