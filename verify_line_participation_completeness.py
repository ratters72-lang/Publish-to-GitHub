"""verify_line_participation_completeness.py — LINE PARTICIPATION VERIFIER.

CONTRACT UNDER TEST
-------------------
The defensive-line REBUILD (``run_defensive_pipeline`` ->
``_line_defenders`` -> ``compute_targets`` -> ``world.frame_spacing_targets``)
must consume the COMPLETE eligible defender population, ownership must not
silently exclude a body from later rebuilds, and the excluded set must not
GROW across successive tackles.

CLAUSES
-------
  P0  RECONCILIATION AUTHORITY EXISTS — the TES structure hold is a
      CHAMBER-SCOPED state, so an authority must exist that answers "is
      this hold live?" and drops a hold that outlives its chamber.  Without
      it the hold is a permanent exemption from the tackle-state release
      (the participation collapse).  Static: the module must expose
      ``hold_is_live`` and ``reconcile_stale_holds``, and the sole release
      authority (``tackle_state_gate.update_tackle_state``) must consult
      them rather than the bare hold flag.
  P1  NO HOLD OUTLIVES ITS CHAMBER — no defender may carry a live TES
      structure hold while the engine is outside the drive chamber.
  P2  RELEASE IS REACHABLE — a body flagged ``tackle_state_active`` with
      distance to the ruck greater than the release bound must be released
      within a bounded number of frames; the flag may not persist.
  P3  REBUILD POPULATION IS COMPLETE — no eligible (non-fullback) defender
      may be absent from ``world.frame_spacing_targets`` during OPEN_PLAY.
  P4  NO MONOTONE COLLAPSE — the rebuild pool size must not shrink
      monotonically across successive chambers (the accumulation symptom).

Exit 0 = contract intact.  Exit 1 = violation (listed).  Exit 2 = harness
could not reach the required phases.
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

from game.defence.defence_lvl_1.defensive_pipeline import (
    _line_defenders,
    frame_geometry_is_fresh,
)
from game.defence.defence_lvl_1.tackle_state_gate import (
    TACKLE_STATE_EXIT_HYSTERESIS_M,
    tackle_state_radius_m,
    distance_to_tackle_point,
)
from game.defence.defence_lvl_1 import tes_drive_structure as tds

MAX_FRAMES = 4000
RELEASE_GRACE_FRAMES = 60
OUT_LOG = "tmp_verify_line_participation.log"

# Mirror of ``defensive_split_spacing._has_chase_targets`` (the S3 chase
# skip).  Re-stated locally rather than imported so this verifier carries no
# dependency on a private helper of the module it measures.
_CHASE_TARGET_FIELDS = ("chase_target_x", "chase_target_y")


def _has_chase_targets(d) -> bool:
    """True when *d* carries a chase-owned target field (chain skip)."""
    return any(hasattr(d, field) for field in _CHASE_TARGET_FIELDS)


failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def check_p0():
    print("\n[P0] RECONCILIATION AUTHORITY EXISTS")
    tds_path = os.path.join("game", "defence", "defence_lvl_1",
                            "tes_drive_structure.py")
    gate_path = os.path.join("game", "defence", "defence_lvl_1",
                             "tackle_state_gate.py")
    with open(tds_path, "r", encoding="utf-8", errors="replace") as fh:
        tds_src = fh.read()
    with open(gate_path, "r", encoding="utf-8", errors="replace") as fh:
        gate_src = fh.read()

    check("P0.1 tes_drive_structure exposes hold_is_live()",
          "def hold_is_live(" in tds_src,
          "a chamber-scoped hold liveness predicate is required")
    check("P0.2 tes_drive_structure exposes reconcile_stale_holds()",
          "def reconcile_stale_holds(" in tds_src,
          "an authority must drop holds that outlive their chamber")
    check("P0.3 release authority consults hold liveness",
          ("hold_is_live" in gate_src) or ("reconcile_stale_holds" in gate_src),
          "tackle_state_gate must not skip a body on the bare hold flag")


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
    return world, ball, open_play, kickoff_engine, collision_system


def collect():
    frames = []
    chambers = []
    keep = []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        keep.extend(world.defending_team)
        dt = 1.0 / 60.0
        prev_cid = None
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
            in_chamber = tds.is_tes_drive_substate(world)
            radius = tackle_state_radius_m(world)
            bound = radius + TACKLE_STATE_EXIT_HYSTERESIS_M
            team = [p for p in world.defending_team if p is not None]
            pool = _line_defenders(list(team))
            spacing = getattr(world, "frame_spacing_targets", None) or {}
            _ready = bool(getattr(world, "frame_geometry_ready", False))
            _pub = getattr(world, "frame_geometry_frame", None)
            _chase_excluded = sum(
                1 for p in team if _has_chase_targets(p)
            )

            cid = getattr(world, tds.CHAMBER_ID_ATTR, None)
            if in_chamber and cid != prev_cid:
                prev_cid = cid
                chambers.append({"cid": cid, "frame": int(world.frame_count),
                                 "pool": len(pool)})

            rows = []
            for p in team:
                rows.append({
                    "pid": getattr(p, "player_id", None),
                    "hold": bool(getattr(p, tds.HOLD_ACTIVE_ATTR, False)),
                    "flag": bool(getattr(p, "tackle_state_active", False)),
                    "fb": bool(getattr(p, "fullback_role_active", False)),
                    "in_spacing": id(p) in spacing,
                    "dist": distance_to_tackle_point(p, world),
                    "bound": bound,
                })
            frames.append({
                "frame": int(world.frame_count),
                "phase": phase,
                "in_chamber": in_chamber,
                "fresh": frame_geometry_is_fresh(world),
                "spacing_size": len(spacing),
                "ready": _ready,
                "pub_frame": _pub,
                "chase_excluded": _chase_excluded,
                "rows": rows,
            })
    with open(OUT_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    return frames, chambers


def check_runtime(frames, chambers):
    print("\n[P1] NO HOLD OUTLIVES ITS CHAMBER")
    outside = []
    for f in frames:
        if f["in_chamber"]:
            continue
        for r in f["rows"]:
            if r["hold"]:
                outside.append((f["frame"], f["phase"], r["pid"]))
    check("P1.1 no live structure hold outside the drive chamber",
          not outside,
          "%d body-frames hold outside the chamber, e.g. %s" % (
              len(outside), outside[:5]))

    print("\n[P2] RELEASE IS REACHABLE (release owed => released)")
    streaks = {}
    worst = {}
    for f in frames:
        for r in f["rows"]:
            pid = r["pid"]
            # A structure hold excuses release ONLY while it is LIVE.  A hold
            # carried OUTSIDE the drive chamber is exactly the stale stamp
            # this verifier exists to catch, so it must NOT excuse the
            # release test — otherwise the clause is vacuous and the
            # permanent-exemption collapse passes undetected.
            on_live_hold = bool(r["hold"]) and bool(f["in_chamber"])
            owed = (r["flag"] and not on_live_hold
                    and r["dist"] is not None and r["dist"] > r["bound"])
            if owed:
                streaks[pid] = streaks.get(pid, 0) + 1
                worst[pid] = max(worst.get(pid, 0), streaks[pid])
            else:
                if streaks.get(pid):
                    worst[pid] = max(worst.get(pid, 0), streaks[pid])
                streaks[pid] = 0
    stuck = {p: n for p, n in worst.items() if n > RELEASE_GRACE_FRAMES}
    check("P2.1 every release-owed flag clears within %d frames"
          % RELEASE_GRACE_FRAMES, not stuck,
          "bodies release-owed beyond the grace: %s" % stuck)

    print("\n[P3] REBUILD POPULATION COMPLETE DURING OPEN_PLAY")
    # FRESHNESS SCOPE (2026-09-12).  Absence may be measured ONLY against a
    # frame dict the chain published FOR THIS FRAME.  During a TES drive
    # chamber ``run_defensive_pipeline`` deliberately republishes nothing
    # (the locked lane owns the line), and the idle no-tackle gate suppresses
    # the whole pipeline — in both cases the dict legitimately describes an
    # EARLIER population.  A body released since then is absent from that
    # stale dict without any rebuild fault, so counting stale frames here
    # reported the documented non-republication contract as a defect.
    missing = {}
    stale_frames = 0
    first_ctx = None
    empty_dict_frames = 0
    for f in frames:
        if f["phase"] not in ("OPEN_PLAY", "POST_RUCK_OPEN_PLAY"):
            continue
        if f["spacing_size"] == 0:
            empty_dict_frames += 1
        if not f.get("fresh"):
            stale_frames += 1
            continue
        for r in f["rows"]:
            if r["fb"] or r["in_spacing"]:
                continue
            # ``_line_defenders`` excludes exactly TWO things: the dynamic
            # fullback (filtered above) and a ``tackle_state_active`` holder
            # — the documented TACKLE-STATE GATE (holders inside the radius
            # are invisible to the line systems by contract).  That absence
            # is LEGITIMATE.  What must never happen is an eligible body
            # absent for ANY OTHER reason: a silent hole in the rebuild pool.
            # Whether the flag itself may persist is P2's clause, asserted
            # separately; here the absence only has to be EXPLAINED by it.
            if r["flag"]:
                continue
            missing[r["pid"]] = missing.get(r["pid"], 0) + 1
            if first_ctx is None:
                first_ctx = (f["frame"], f["phase"], f["spacing_size"],
                             f["ready"], f["pub_frame"],
                             f["chase_excluded"], len(f["rows"]))
    check("P3.1 no UNEXPLAINED absence from the published spacing dict",
          not missing,
          "%d bodies absent while NOT tackle-state holders, worst: %s" % (
              len(missing), sorted(missing.items(), key=lambda kv: -kv[1])[:8]))
    print("      frames measured: fresh=%d  stale(skipped)=%d  "
          "empty-dict(OPEN_PLAY)=%d"
          % (len([f for f in frames if f.get("fresh")]), stale_frames,
             empty_dict_frames))
    if first_ctx is not None:
        print("      FIRST ABSENCE ctx (frame, phase, dict_size, ready, "
              "pub_frame, chase_excluded, team) = %s" % (first_ctx,))
    # Dict-size histogram so an under-populated publication (a short dict
    # rather than a missing body) is visible instead of inferred.
    _sizes = {}
    for f in frames:
        if f["phase"] not in ("OPEN_PLAY", "POST_RUCK_OPEN_PLAY"):
            continue
        _sizes[f["spacing_size"]] = _sizes.get(f["spacing_size"], 0) + 1
    print("      OPEN_PLAY dict-size histogram: %s"
          % sorted(_sizes.items()))

    print("\n[P4] NO MONOTONE COLLAPSE ACROSS CHAMBERS")
    pools = [c["pool"] for c in chambers]
    check("P4.1 the harness reached >= 3 chambers", len(pools) >= 3,
          "chambers=%d" % len(pools))
    if len(pools) >= 3:
        monotone = all(pools[i] >= pools[i + 1] for i in range(len(pools) - 1))
        check("P4.2 the rebuild pool does not shrink every chamber",
              not (monotone and pools[0] > pools[-1]),
              "pool sizes across chambers: %s" % pools)

    print("\n  chambers: %d  frames observed: %d" % (len(chambers), len(frames)))
    if pools:
        print("  rebuild pool size per chamber: %s" % pools)


def main():
    print("=" * 78)
    print("LINE PARTICIPATION COMPLETENESS VERIFIER")
    print("=" * 78)
    check_p0()
    frames, chambers = collect()
    if not frames:
        print("\nRESULT: FAILED - no frames observed.")
        return 2
    if not chambers:
        print("\nRESULT: FAILED - no drive chamber reached.")
        return 2
    check_runtime(frames, chambers)

    print("\n" + "=" * 78)
    if failures:
        print("RESULT: %d VIOLATION(S) - the defensive-line rebuild does NOT "
              "consume the complete eligible population:" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("RESULT: LINE PARTICIPATION INTACT - the rebuild consumes the "
          "complete eligible population, ownership does not exclude a body "
          "from later rebuilds, and no exclusion set grows across tackles.")
    return 0


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
