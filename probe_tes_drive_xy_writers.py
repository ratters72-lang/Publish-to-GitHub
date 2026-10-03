"""probe_tes_drive_xy_writers.py — trace EVERY X/Y write on a drive-chamber
DEF_LINE_READY / DEF_CHASE body, end to end, and attribute it to an authority.

Boots the engine headless exactly like ``probe_drive_non_tackler_motion.py``
(dummy SDL, kickoff -> catch -> OPEN_PLAY -> tackle -> TES drive), installs and
arms ``game.debug.tes_xy_write_trace`` for the run, and then reports:

  * how many drive frames were observed;
  * every authority that wrote a line body's X/Y inside the drive chamber,
    split by axis (this is the answer to "which authority is still active");
  * the (authority, inner-site) pairs, so integrator write-forms
    (``_accelerate_toward_target`` / ``_clamp_position`` / depth-lock glide)
    are separated from target/authority writers;
  * a sample of raw write records.

Exit 0 = drive frames observed AND at least one write traced, 1 otherwise.
Engine stdout is captured to ``tmp_tes_xy_trace_engine.log`` so the trace
report is not drowned by the per-frame [RUCK-PIN] audit.
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

from game.debug import tes_xy_write_trace as trace

DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
MAX_FRAMES = 2500
ENGINE_LOG = "tmp_tes_xy_trace_engine.log"


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


def run_capture():
    """Boot + drive the engine with the trace armed. Returns (drive_frames,)."""
    buffer = io.StringIO()
    drive_frames = 0
    with contextlib.redirect_stdout(buffer):
        world, ball, open_play, kickoff_engine, collision_system, field = boot_world()
        trace.install()
        trace.set_fallback_world(world)
        trace.arm()
        dt = 1.0 / 60.0
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
            if open_play._phase in DRIVE_PHASES:
                drive_frames += 1
        trace.disarm()
        trace.uninstall()
    with open(ENGINE_LOG, "w", encoding="utf-8") as fh:
        fh.write(buffer.getvalue())
    return drive_frames


def _print_matrix(summary):
    print("\nAUTHORITY x AXIS (writes to DEF_LINE_READY / DEF_CHASE bodies in the drive):")
    if not summary["by_authority"]:
        print("  (none)")
        return
    scope = summary.get("by_scope", {})
    lines = summary.get("lines", {})
    ids = summary.get("ids", {})
    for auth in summary["authorities"]:
        slot = summary["by_authority"][auth]
        total = summary["by_authority_axis"][auth]
        sc = scope.get(auth, {})
        print(f"  {auth}  X={slot.get('X', 0)} Y={slot.get('Y', 0)} total={total}")
        print(f"      scope : genuine-line={sc.get('line', 0)} "
              f"tes-tackler={sc.get('tackler', 0)} "
              f"attached={sc.get('attached', 0)}")
        print(f"      ids   : {ids.get(auth, [])}")
        print(f"      site  : line(s) {lines.get(auth, [])}")


def _print_pairs(summary):
    print("\n(AUTHORITY, INNER SITE) PAIRS:")
    if not summary["pairs"]:
        print("  (none)")
        return
    for (auth, inner), n in sorted(summary["pairs"].items(), key=lambda kv: -kv[1]):
        print(f"  {n:<7} {auth}  <-  {inner}")


def _print_samples(limit=40):
    recs = trace.records()
    print(f"\nSAMPLE WRITE RECORDS (first {min(limit, len(recs))} of {len(recs)}):")
    for r in recs[:limit]:
        d = r["delta"]
        d_txt = "-" if d is None else f"{d:+.4f}"
        print(
            f"  [XY-WRITE] f={r['frame']} phase={r['phase']} id={r['player_id']} "
            f"role={r['role']} depth={r['depth_role']} axis={r['axis']} "
            f"old={r['old']} new={r['new']} d={d_txt} "
            f"src={r['authority']} inner={r['inner']} line={r['line']}"
        )


def main():
    print("=" * 78)
    print("TES DRIVE X/Y WRITE TRACE — which authority still moves the line?")
    print("=" * 78)

    drive_frames = run_capture()
    summary = trace.summarise()

    print(f"\ndrive frames observed : {drive_frames}")
    print(f"total writes recorded : {summary['total']}")
    print(f"by axis               : {summary['by_axis']}")
    print(f"by role               : {summary['by_role']}")
    print(f"by phase              : {summary['by_phase']}")

    _print_matrix(summary)
    _print_pairs(summary)
    _print_samples()

    print("\nVERDICT — authorities active inside the drive chamber:")
    if summary["authorities"]:
        for auth in summary["authorities"]:
            print(f"  - {auth}  ({summary['by_authority_axis'][auth]} writes)")
    else:
        print("  (no writes traced)")

    ok = drive_frames > 0 and summary["total"] > 0
    print("\n" + "=" * 78)
    if ok:
        print("RESULT: trace captured line-body X/Y writes during the drive.")
        return 0
    print("RESULT: FAILED — no drive frame or no write captured.")
    return 1


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
