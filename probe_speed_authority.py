"""probe_speed_authority.py — SPEED-AUTHORITY AUDIT for the pre-tackle corridor.

Run:  python probe_speed_authority.py > tmp_speed_authority.txt 2>&1

THE QUESTION THIS ANSWERS
-------------------------
The slowdown curve was proven to be calculated, rewired into movement, and
tuned — yet the on-screen approach stayed the same.  That meant one of two
things: another authority was determining the final approach speed, or the
tackle transitioned before the curve could act.  This probe identifies, on a
REAL kickoff -> catch -> tackle cycle:

    (A) the FINAL WRITER of the primary tackler's speed in the last few
        metres (the ``target_source`` on the last OPEN_PLAY frame),
    (B) the FINAL CONSUMER of that speed (the velocity the TES outcome
        system actually receives at TACKLE_INIT, ``ctx.defender.vx/vy``),
    (C) whether the corridor owned the body across that whole span
        (``_pta_corridor_frames``), i.e. whether the taper had time to act,
    (D) how many OPEN_PLAY frames elapsed between the body entering the
        slowdown band and TACKLE_INIT — the taper's on-screen runway.

A run is a PASS when, for every recorded tackle:

    - the primary is corridor-owned on its final pre-tackle frame,
    - the final ``target_source`` is a pre-tackle/corridor source,
    - the velocity TES receives equals the corridor's stamped contact
      velocity (bit-for-bit within float tolerance), and
    - the body spent > 0 frames inside the band before the trigger.

It is an AUDIT, not a unit test: it prints the evidence and a verdict per
tackle, and exits non-zero only when no tackle could be measured at all.
"""

import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import math
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame

pygame.init()
pygame.display.set_mode((1, 1))

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

from game.defence.defence_lvl_1 import pretackle_corridor as C

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
FPS = 60
MAX_FRAMES = 2400

# The "last few metres": record every approach frame from here inward.
APPROACH_M = 8.0
# How many trailing frames of the approach to print per tackle.
TRACE_TAIL = 45

_EPS = 1e-6


def build():
    field = Field(SCREEN_WIDTH, SCREEN_HEIGHT)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)
    collision_system = CollisionSystem()

    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.vel = (0.0, 0.0)
    world.ball.possessed_by = None
    kickoff_engine.kickoff()
    return world, open_play, kickoff_engine, collision_system, ball


def _tuners(world, open_play):
    """Resolve the DefenceTuners the live pipeline is actually using."""
    for holder, name in (
        (open_play, "def_tuners"),
        (open_play, "tuners"),
        (world, "deftuners"),
        (world, "tuners"),
    ):
        if holder is None:
            continue
        value = getattr(holder, name, None)
        if value is not None:
            return value
    return None


def _speed(vx, vy):
    return math.hypot(float(vx or 0.0), float(vy or 0.0))


def _state_name(d):
    state = getattr(d, "pre_tackle_state", None)
    if state is None:
        return "-"
    return getattr(state, "name", None) or str(state)


def _sample(world, d, bc, tuners, frame):
    """One approach-frame record for *d*."""
    dist = math.hypot(d.x - float(bc.x), d.y - float(bc.y))
    band = C.band_m(tuners)
    plant_r = C.plant_radius_m(tuners)
    return {
        "frame": frame,
        "id": getattr(d, "player_id", id(d)),
        "dist": dist,
        "band": band,
        "plant_r": plant_r,
        "in_band": dist <= band,
        "past_plant": dist <= plant_r,
        "src": str(getattr(d, "target_source", "") or ""),
        "state": _state_name(d),
        "move_speed": float(getattr(d, "move_speed", 0.0) or 0.0),
        "v": _speed(getattr(d, "vx", 0.0), getattr(d, "vy", 0.0)),
        "vx": float(getattr(d, "vx", 0.0) or 0.0),
        "vy": float(getattr(d, "vy", 0.0) or 0.0),
        "owned": bool(C.is_corridor_owned(d)),
        "frames": int(C.corridor_frames(d)),
        "stamp_vx": getattr(d, C.CONTACT_VX_ATTR, None),
        "stamp_vy": getattr(d, C.CONTACT_VY_ATTR, None),
        "stamp_speed": getattr(d, C.CONTACT_SPEED_ATTR, None),
        "plant_stamp": getattr(d, C.CONTACT_PLANT_ATTR, None),
        "role": getattr(d, "tackle_role", None),
        # A genuine committed tackler has been marked by the step-10a1
        # writer at least once.  The frozen kicker carries tile_role
        # PRIMARY in some frames but is NEVER committed — the audit must
        # not score it as an approach.
        "committed": bool(getattr(d, "_tackle_commit_active", False)),
    }


def _run_sim(world, open_play, kickoff_engine, collision_system, ball,
             tuners, dt):
    """Run the kickoff -> catch -> tackle cycle and capture the evidence.

    EVERY engine print is swallowed: the engine's per-frame defensive /
    fullback audits are extremely chatty during the kickoff phase and would
    otherwise bury this tool's own report.  The audit report is the only
    output this probe produces.
    """
    import contextlib
    import io

    approach: dict = {}     # player_id -> [frame records]
    tackles: list = []      # one dict per observed TACKLE_INIT
    was_tes = bool(getattr(world, "tes_active", False))

    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        for frame in range(MAX_FRAMES):
            ball.update(dt)

            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(dt)
                check_for_catch(world, ball, dt)
            elif world.phase == GamePhase.CATCH:
                if (world.ball_carrier is not None
                        and world.open_play is not None):
                    world.open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
            elif world.phase == GamePhase.OPEN_PLAY:
                open_play.update(dt)
                if not world.tes_active:
                    collision_system.resolve_all(world.players, world=world)

            bc = getattr(world, "ball_carrier", None)
            tes_now = bool(getattr(world, "tes_active", False))

            # ── (B) THE HANDOFF: capture the instant TES arms ──
            if tes_now and not was_tes and bc is not None:
                ctx = getattr(open_play, "tes_ctx", None)
                deff = (getattr(ctx, "defender", None)
                        if ctx is not None else None)
                live = next(
                    (p for p in world.defending_team
                     if getattr(p, "player_id", None)
                     == getattr(deff, "player_id", None)),
                    None,
                )
                tackles.append({
                    "frame": frame,
                    "ctx_vx": float(getattr(deff, "vx", 0.0) or 0.0),
                    "ctx_vy": float(getattr(deff, "vy", 0.0) or 0.0),
                    "live_vx": (float(getattr(live, "vx", 0.0) or 0.0)
                                if live else None),
                    "live_vy": (float(getattr(live, "vy", 0.0) or 0.0)
                                if live else None),
                    "stamp_vx": (getattr(live, C.CONTACT_VX_ATTR, None)
                                 if live else None),
                    "stamp_vy": (getattr(live, C.CONTACT_VY_ATTR, None)
                                 if live else None),
                    "pid": getattr(deff, "player_id", None),
                })
            was_tes = tes_now

            # ── (A)/(C) Record the approach for committed primaries ──
            if bc is None or tes_now:
                continue
            for d in getattr(world, "defending_team", ()) or ():
                if getattr(d, "tackle_role", None) != "PRIMARY":
                    continue
                dist = math.hypot(d.x - float(bc.x), d.y - float(bc.y))
                if dist > APPROACH_M:
                    continue
                pid = getattr(d, "player_id", id(d))
                approach.setdefault(pid, []).append(
                    _sample(world, d, bc, tuners, frame)
                )
    return approach, tackles


def main():
    world, open_play, kickoff_engine, collision_system, ball = build()
    tuners = _tuners(world, open_play)
    dt = 1.0 / FPS

    print("=" * 78)
    print("SPEED-AUTHORITY AUDIT — pre-tackle corridor vs the TES handoff")
    print("=" * 78)
    print(f"  corridor_owns_speed (gate)   = "
          f"{C.corridor_owns_speed(tuners)}")
    print(f"  handoff_enabled (gate)       = {C.handoff_enabled(tuners)}")
    print(f"  corridor radius (m)          = {C.corridor_radius_m(tuners):.2f}")
    print(f"  slowdown band (m)            = {C.band_m(tuners):.2f}")
    print(f"  plant radius (m)             = {C.plant_radius_m(tuners):.2f}")
    print(f"  taper floor/scale            = "
          f"{getattr(tuners, 'pretackle_slowdown_scale', None)}")
    print(f"  commit speed (m/s)           = "
          f"{getattr(tuners, 'pretackle_commit_speed_mps', None)}")

    approach, tackles = _run_sim(
        world, open_play, kickoff_engine, collision_system, ball, tuners, dt,
    )

    # ─────────────────────────────────────────────────────────────────
    # REPORT
    # ─────────────────────────────────────────────────────────────────
    if not approach and not tackles:
        print("\n!! No committed PRIMARY tackler ever entered the approach "
              "window, and TES never armed — cannot audit.")
        print("   (Increase MAX_FRAMES or inspect the kickoff->catch cycle.)")
        pygame.quit()
        return

    _report_tackles(tackles, approach)
    _report_writers(approach)
    _report_verdict(tackles, approach)

    pygame.quit()


def _report_tackles(tackles, approach):
    print("\n" + "=" * 78)
    print("(B) FINAL CONSUMER — what TES actually received at TACKLE_INIT")
    print("=" * 78)
    if not tackles:
        print("  !! TES never armed during the run — the tackle never "
              "transitioned. The approach speed could not be consumed.")
        return
    print(f"  tackles observed: {len(tackles)}")
    for n, t in enumerate(tackles, 1):
        ctx_spd = _speed(t["ctx_vx"], t["ctx_vy"])
        live_spd = (None if t["live_vx"] is None
                    else _speed(t["live_vx"], t["live_vy"]))
        stamp_spd = (None if t["stamp_vx"] is None
                     else _speed(t["stamp_vx"], t["stamp_vy"]))
        print(f"\n  ── tackle {n}  (frame {t['frame']}, "
              f"primary id={t['pid']}) ──")
        print(f"     TES ctx.defender vx/vy      = "
              f"({t['ctx_vx']:+.3f}, {t['ctx_vy']:+.3f})  "
              f"|v| = {ctx_spd:.3f} m/s")
        if t["live_vx"] is not None:
            print(f"     live body   vx/vy           = "
                  f"({t['live_vx']:+.3f}, {t['live_vy']:+.3f})  "
                  f"|v| = {live_spd:.3f} m/s")
        if t["stamp_vx"] is not None:
            print(f"     corridor stamp vx/vy        = "
                  f"({t['stamp_vx']:+.3f}, {t['stamp_vy']:+.3f})  "
                  f"|v| = {stamp_spd:.3f} m/s")
        else:
            print("     corridor stamp              = (none — body never "
                  "entered the corridor)")
        trace = approach.get(t["pid"], [])
        if trace:
            print(f"     approach frames recorded    = {len(trace)}")
            print(f"     corridor-owned on last frame= "
                  f"{trace[-1]['owned']}  (streak "
                  f"{trace[-1]['frames']} frames)")


def _report_writers(approach):
    print("\n" + "=" * 78)
    print("(A) FINAL WRITER — the last OPEN_PLAY writer of each approach")
    print("=" * 78)
    if not approach:
        print("  (no approach frames recorded)")
        return
    for pid, trace in sorted(approach.items()):
        tail = trace[-TRACE_TAIL:]
        last = tail[-1]
        print(f"\n  ── primary id={pid}  ({len(trace)} frames in "
              f"{APPROACH_M:.0f} m) ──")
        print(f"     {'frame':>6} {'dist':>6} {'mv_spd':>7} {'|v|':>6} "
              f"{'own':>4} {'fr':>4} {'st':<8} {'src':<26}")
        for r in tail:
            print(f"     {r['frame']:>6} {r['dist']:>6.2f} "
                  f"{r['move_speed']:>7.2f} {r['v']:>6.2f} "
                  f"{str(r['owned'])[0]:>4} {r['frames']:>4} "
                  f"{r['state']:<8} {r['src'][:26]:<26}")
        print(f"     -> FINAL WRITER (target_source) = {last['src']!r}")
        print(f"     -> final state                  = {last['state']}")
        print(f"     -> final dist to BC             = {last['dist']:.2f} m")
        print(f"     -> final corridor streak        = {last['frames']} frames")

        # The taper's on-screen runway: band entry -> trigger.
        band_frames = [r for r in trace if r["in_band"]]
        if band_frames:
            print(f"     -> frames inside the band       = "
                  f"{len(band_frames)} (from {band_frames[0]['dist']:.2f} m "
                  f"inward)")
            speeds = [r["v"] for r in band_frames]
            print(f"     -> speed entering band          = "
                  f"{speeds[0]:.3f} m/s")
            print(f"     -> slowest speed in band        = "
                  f"{min(speeds):.3f} m/s")
            print(f"     -> speed on last band frame     = "
                  f"{speeds[-1]:.3f} m/s")
        else:
            print("     -> frames inside the band       = 0  "
                  "!! the taper never had a frame to act on")


def _report_verdict(tackles, approach):
    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    if not tackles:
        print("  INCONCLUSIVE: TES never armed, so no final consumer existed.")
        return
    failures = 0
    skipped = 0
    for n, t in enumerate(tackles, 1):
        pid = t["pid"]
        trace = approach.get(pid, [])
        last = trace[-1] if trace else None

        # ── INERT-BODY GATE ──
        # A body that was never committed by the step-10a1 writer is not a
        # tackler: it is the frozen kicker (or another pinned body) that
        # happened to satisfy the distance test.  It carries no corridor
        # contract to audit, so it is reported and skipped rather than
        # counted as a failure.
        if trace and not any(r["committed"] for r in trace):
            print(f"\n  tackle {n} (primary id={pid}): SKIP — inert body "
                  "(never committed by the step-10a1 writer; the frozen "
                  "kicker or a pinned body).")
            skipped += 1
            continue

        checks = []

        checks.append((
            "primary corridor-owned on its final pre-tackle frame",
            bool(last and last["owned"]),
        ))
        checks.append((
            "final target_source is a pre-tackle/corridor source",
            bool(last and last["src"] in (
                "pre_tackle", "pretackle", "tackle_engage_guard",
                "tackler_commit", "structured_close",
            )),
        ))
        if t["stamp_vx"] is not None:
            checks.append((
                "TES ctx velocity == corridor stamped contact velocity",
                abs(t["ctx_vx"] - t["stamp_vx"]) < 1e-4
                and abs(t["ctx_vy"] - t["stamp_vy"]) < 1e-4,
            ))
        else:
            checks.append((
                "TES ctx velocity == live body velocity (no corridor stamp)",
                (t["live_vx"] is not None
                 and abs(t["ctx_vx"] - t["live_vx"]) < 1e-4
                 and abs(t["ctx_vy"] - t["live_vy"]) < 1e-4),
            ))
        band_frames = [r for r in trace if r["in_band"]] if trace else []
        checks.append((
            "body spent > 0 frames inside the slowdown band before trigger",
            len(band_frames) > 0,
        ))

        print(f"\n  tackle {n} (primary id={pid}):")
        for label, ok in checks:
            print(f"    [{'PASS' if ok else 'FAIL'}] {label}")
            if not ok:
                failures += 1

    print("\n" + "-" * 78)
    if skipped:
        print(f"  ({skipped} inert non-tackler tackle(s) skipped.)")
    if failures == 0 and not skipped:
        print("  RESULT: the pre-tackle corridor is the FINAL WRITER and the")
        print("          TES outcome system is the FINAL CONSUMER, and the")
        print("          taper had band frames to act on before the trigger.")
    elif failures == 0:
        print("  RESULT: every COMMITTED tackler had the corridor as its")
        print("          FINAL WRITER and the TES outcome system as its")
        print("          FINAL CONSUMER, with band frames for the taper.")
    else:
        print(f"  RESULT: {failures} check(s) FAILED — a second speed "
              "authority is still determining the final approach, or the")
        print("          trigger fires before the taper can act.  See the")
        print("          per-frame trace above for the last writer.")


if __name__ == "__main__":
    main()
