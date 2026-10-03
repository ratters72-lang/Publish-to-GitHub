"""verify_tes_unified_drive_pipeline.py — contract verifier for the claim:

    "TES runs a unified defensive-line pipeline for the line proper during
     DRIVE, with movement owned solely by ``update_tes_drive``."

METHOD
------
Live, write-level.  Boots the engine headless (kickoff -> catch -> OPEN_PLAY
-> tackle -> TES drive), installs the ``game.debug.tes_xy_write_trace``
diagnostic for the run, then partitions every captured X/Y write into four
mutually-exclusive scopes read from the live world at write time:

  LINE PROPER : a genuine line body -- NOT a registered TES tackler, NOT a
                TES-attached joiner, and NOT the dynamic fullback actor.
  FULLBACK    : the body carrying ``fullback_role_active`` this drive
                (a backfield anchor, deliberately excluded from the line).
  TACKLER     : a registered TES tackler (trace flag ``tes_tackler``).
  ATTACHED    : a TES-attached joiner (trace flag ``attached``).

The claim is then asserted as a SET EQUALITY: the authorities observed on
LINE-PROPER records must be exactly
``{defence_manager.py:update_tes_drive}``, reached through the ``Player.update``
integrator (targets, never a direct ``d.x =``).  The fullback, TES tackler and
attached scopes must be owned by their own, different writers -- they are TES
participants / backfield anchors, not the defensive line.

A second axis checks the LATENT writers a static read suggests could touch the
line during a drive (tackle-begin glide, two-radius attach band, ruck-adjacent
pin, pre-tackle authority, tackle-state peel) recorded ZERO writes of any
scope.

Runs as a standalone script (exit 0 = all checks pass, 1 = a check failed).
Deterministic on the assertion axis: the drive frame count varies run-to-run
(kickoff/chase physics is not deterministic) but the AUTHORITY SET does not.
"""

from __future__ import annotations

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

# ── CONTRACT CONSTANTS ───────────────────────────────────────────────
DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
MAX_FRAMES = 2500
ENGINE_LOG = "tmp_tes_unified_drive_engine.log"

# The one authority the claim says owns line-proper movement.
AUTH_DEF_MANAGER = "defence_manager.py:update_tes_drive"
# The writers the other (non line-proper) scopes are allowed to use.
AUTH_OPE_UPDATE = "open_play_engine.py:update"
AUTH_TES_DRIVE = "open_play_engine.py:_update_tackle_drive"
AUTH_ATTACHED = "open_play_engine.py:_follow_attached_tacklers"

# The TES participant writers: they own a registered tackler / attached joiner
# (a body that merely still carries the DEF_LINE_READY tag), NOT the line.
AUTH_TES_WRITERS = frozenset({AUTH_TES_DRIVE, AUTH_ATTACHED})

# The complete set of writers the drive chamber may legitimately use.
KNOWN_WRITERS = frozenset({
    AUTH_DEF_MANAGER, AUTH_OPE_UPDATE, AUTH_TES_DRIVE, AUTH_ATTACHED,
})

# The integrator sites a target-writer lands through (targets, not raw x/y).
INTEGRATOR_SITES = frozenset({
    "player.py:_accelerate_toward_target",
    "player.py:_clamp_position",
})

# Writers a static read suggests *could* touch the line in the chamber; the
# trace must show ZERO of any scope from each.
LATENT_WRITERS = (
    "role_depth_glide.py:enforce_tackle_begin_glide",
    "attach_band.py:enforce_attach_band",
    "ruck_adjacent_pin.py:enforce_ruck_adjacent_pin",
    "pre_tackle_authority.py:apply_pre_tackle",
    "tackle_state_gate.py:_peel_holder",
)

_PASS = 0
_FAIL = 0


def _check(cond, label):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}")
    return bool(cond)


# ─────────────────────────────────────────────────────────────────────
# HEADLESS DRIVE CAPTURE
# ─────────────────────────────────────────────────────────────────────

def _boot_world():
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
    return world, ball, open_play, kickoff_engine, collision_system


def _scan_fullback_ids(world, sink: set) -> None:
    """Accumulate every body that is (or was) the dynamic fullback."""
    for p in (getattr(world, "defending_team", None) or []):
        if getattr(p, "fullback_role_active", False):
            pid = getattr(p, "player_id", None)
            if pid is not None:
                sink.add(pid)
    fb_actor = getattr(world, "fullback_actor_id", None)
    if fb_actor is not None:
        sink.add(fb_actor)


def run_capture():
    """Boot + run the engine with the trace armed.

    Returns ``(drive_frames, fullback_ids)``.  Every frame the engine is in a
    TES drive sub-state the live fullback actor ids are collected so the
    line-proper partition can exclude them exactly as the engine does.
    """
    buffer = io.StringIO()
    drive_frames = 0
    fullback_ids: set = set()
    with contextlib.redirect_stdout(buffer):
        (world, ball, open_play, kickoff_engine,
         collision_system) = _boot_world()
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
                _scan_fullback_ids(world, fullback_ids)
        trace.disarm()
        trace.uninstall()
    with open(ENGINE_LOG, "w", encoding="utf-8") as fh:
        fh.write(buffer.getvalue())
    return drive_frames, fullback_ids


# ─────────────────────────────────────────────────────────────────────
# PARTITION
# ─────────────────────────────────────────────────────────────────────

def partition(records, fullback_ids):
    """Split records into the four mutually-exclusive scopes.

    LINE PROPER is everything that is NOT the fullback, NOT a registered TES
    tackler and NOT an attached joiner -- exactly the engine's own line pool
    (``update_tes_drive`` excludes tes_ids / fullback / TES mover roles).
    """
    line, fullback, tackler, attached = [], [], [], []
    for r in records:
        if r.get("player_id") in fullback_ids:
            fullback.append(r)
        elif r.get("tes_tackler"):
            tackler.append(r)
        elif r.get("attached"):
            attached.append(r)
        else:
            line.append(r)
    return {
        "line": line,
        "fullback": fullback,
        "tackler": tackler,
        "attached": attached,
    }


def _authorities(records) -> set:
    return {r["authority"] for r in records}


def _inner_sites(records) -> set:
    return {r["inner"] for r in records}


def _ids(records) -> set:
    return {r["player_id"] for r in records}


def _roles(records) -> set:
    return {r["role"] for r in records}


# ─────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────

def _print_scope(scopes):
    print("\nAUTHORITY BY SCOPE (X/Y writes on DEF_LINE_READY / DEF_CHASE bodies):")
    for name in ("line", "fullback", "tackler", "attached"):
        recs = scopes[name]
        print(f"  {name:<9} writes={len(recs):<6} "
              f"ids={sorted(i for i in _ids(recs) if i is not None)}")
        if not recs:
            continue
        for auth in sorted(_authorities(recs)):
            n = sum(1 for r in recs if r["authority"] == auth)
            print(f"        {auth}  ({n})")


# ─────────────────────────────────────────────────────────────────────
# CHECKS
# ─────────────────────────────────────────────────────────────────────

def check_capture(drive_frames, records, scopes):
    print("[1] capture")
    _check(drive_frames > 0, f"1.1 a drive was observed ({drive_frames} frames)")
    _check(len(records) > 0, f"1.2 writes recorded ({len(records)})")
    _check(len(scopes["line"]) > 0,
           f"1.3 line-proper writes exist ({len(scopes['line'])})")


def check_line_authority(scopes):
    print("[2] LINE PROPER — movement owned solely by update_tes_drive")
    line = scopes["line"]
    auths = _authorities(line)
    _check(
        auths == {AUTH_DEF_MANAGER},
        f"2.1 line-proper authority set == {{{AUTH_DEF_MANAGER}}} "
        f"(got {sorted(auths)})",
    )
    # Explicit negative guards, so a regression names the offending writer.
    for banned in (AUTH_OPE_UPDATE, AUTH_TES_DRIVE, AUTH_ATTACHED):
        _check(
            all(r["authority"] != banned for r in line),
            f"2.2 no line-proper write from {banned}",
        )
    _check(
        len(_ids(line)) >= 2,
        f"2.3 the line is plural, not one body ({sorted(_ids(line))})",
    )
    _check(
        _roles(line) <= {"DEF_LINE_READY", "DEF_CHASE"},
        f"2.4 line-proper roles are line roles ({sorted(_roles(line))})",
    )


def check_integrator(scopes):
    print("[3] LINE PROPER — writes are targets via the Player.update integrator")
    line = scopes["line"]
    sites = _inner_sites(line)
    _check(
        sites <= INTEGRATOR_SITES,
        f"3.1 inner sites ⊆ integrator ({sorted(sites)})",
    )
    _check(
        "player.py:_clamp_position" in sites,
        "3.2 integrator clamp present",
    )


def check_other_scopes(scopes):
    print("[4] other scopes are owned by their own writers, not the line authority")
    fb = scopes["fullback"]
    tk = scopes["tackler"]
    at = scopes["attached"]

    _check(len(fb) > 0, f"4.1 the fullback moved this drive ({len(fb)})")
    _check(
        _authorities(fb) <= {AUTH_OPE_UPDATE},
        f"4.2 fullback authority ⊆ {{{AUTH_OPE_UPDATE}}} "
        f"(got {sorted(_authorities(fb))})",
    )
    _check(
        all(r["authority"] != AUTH_DEF_MANAGER for r in fb),
        "4.3 update_tes_drive never writes the fullback",
    )

    if tk:
        _check(
            _authorities(tk) <= AUTH_TES_WRITERS,
            f"4.4 TES tackler authority ⊆ TES participant writers "
            f"{sorted(AUTH_TES_WRITERS)} (got {sorted(_authorities(tk))})",
        )
        _check(
            all(r["authority"] != AUTH_DEF_MANAGER for r in tk),
            "4.5 update_tes_drive never writes a registered TES tackler",
        )
    else:
        _check(True, "4.4 no TES tackler scope this run (informational)")

    if at:
        _check(
            _authorities(at) <= AUTH_TES_WRITERS,
            f"4.6 attached authority ⊆ TES participant writers "
            f"{sorted(AUTH_TES_WRITERS)} (got {sorted(_authorities(at))})",
        )
        _check(
            all(r["authority"] != AUTH_DEF_MANAGER for r in at),
            "4.7 update_tes_drive never writes an attached joiner",
        )
    else:
        _check(True, "4.6 no attached scope this run (informational)")


def check_partition(records, scopes, fullback_ids):
    print("[5] partition integrity")
    total = sum(len(scopes[k]) for k in ("line", "fullback", "tackler", "attached"))
    _check(total == len(records),
           f"5.1 scopes partition every record ({total}/{len(records)})")
    # Line-proper ids and fullback ids must be disjoint by construction.
    _check(
        _ids(scopes["line"]).isdisjoint(fullback_ids),
        "5.2 line-proper ids exclude the fullback ids",
    )


def check_latent(records):
    print("[6] latent line writers recorded ZERO writes of any scope")
    for name in LATENT_WRITERS:
        n = sum(1 for r in records if r["authority"] == name)
        _check(n == 0, f"6 latent writer {name} wrote nothing ({n})")


def check_authority_universe(records):
    """No authority OUTSIDE the four known writers touched the chamber."""
    print("[7] the chamber uses only the four known writers")
    observed = {r["authority"] for r in records}
    _check(
        observed <= KNOWN_WRITERS,
        f"7.1 no unknown writer touched the chamber (observed {sorted(observed)})",
    )


# ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 78)
    print("VERIFY — TES UNIFIED DRIVE PIPELINE (line proper owned by update_tes_drive)")
    print("=" * 78)

    drive_frames, fullback_ids = run_capture()
    records = list(trace.records())
    scopes = partition(records, fullback_ids)

    print(f"\ndrive frames observed : {drive_frames}")
    print(f"total writes recorded : {len(records)}")
    print(f"fullback ids seen     : {sorted(fullback_ids)}")
    _print_scope(scopes)

    check_capture(drive_frames, records, scopes)
    check_line_authority(scopes)
    check_integrator(scopes)
    check_other_scopes(scopes)
    check_partition(records, scopes, fullback_ids)
    check_latent(records)
    check_authority_universe(records)

    print("\n" + "=" * 78)
    print(f"{_PASS} passed, {_FAIL} failed")
    if _FAIL == 0:
        print("RESULT: the defensive line proper is moved solely by "
              "defence_manager.update_tes_drive during DRIVE.")
        return 0
    print("RESULT: FAILED — the line proper is not under a single authority.")
    return 1


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        trace.disarm()
        trace.uninstall()
        pygame.quit()
    sys.exit(code)
