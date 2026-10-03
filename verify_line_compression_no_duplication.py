"""verify_line_compression_no_duplication.py — SYMPTOM VERIFIER (2 of 3).

The investigation names three field symptoms.  The rebuild-pool collapse
(symptom 1 — a growing subset of defenders retreating but never
participating in later lines) is proven by
``verify_line_participation_completeness.py``.  This module measures the
other two against the SAME rebuild population:

  S2  FAR-EDGE WINGER COMPRESSION toward adjacent defenders — the outermost
      body on a side receives a lane closer to its neighbour than the
      nominal count-lane step allows.
  S3  SHORT-SIDE OVERLAP / DUPLICATION near touchline tackles — two
      same-side line bodies are assigned the SAME lane.

MEASURED ON TARGETS, NOT LIVE POSITIONS
---------------------------------------
A body mid-glide transiently sits anywhere between its previous and next
lane, so live ``d.y`` is NOT the geometry law and two gliding bodies can
momentarily align without any ownership fault.  The law is what the chain
PUBLISHED for this frame: ``world.frame_spacing_targets``.  Every clause
below reads that dict, which is the single frame-authoritative Y source
(``compute_targets`` -> ``SpacingGuard`` -> ``apply_anticipation_pre_shift``),
so the bound is the engine's own geometry rather than a new invention.

WHY A DEPLETED POOL PRODUCES THEM
---------------------------------
``run_defensive_pipeline`` -> ``compute_targets`` lays the per-side chain
out for the CURRENT eligible count.  When ``_line_defenders`` silently
drops bodies, the count no longer matches the bodies standing on the line:
the survivors' lanes resolve against a smaller population, so the outer
body is pulled in toward its neighbour (S2) and two bodies can resolve onto
one lane (S3).

CLAUSES
-------
  C0  TOTAL PINNING — every pool body is assigned a lane by the fresh frame
      dict (no body left out of the reconstruction).
  C1  NO DUPLICATION — no two same-side lanes are within ``DUPLICATE_EPS_M``.
  C2  NO OVER-COMPRESSION — on any side with >= 2 lanes, the minimum gap is
      at least ``COMPRESSION_FRACTION`` of the nominal count-lane step.
  C3  NO ACCUMULATION — the per-chamber minimum-gap ratio must not degrade
      monotonically across successive chambers (the accumulating form).

CONTROL
-------
``RLM_DISABLE_LANE_GUARD=1`` re-arms the PRE-FIX LANE GEOMETRY this module
actually measures, so the clauses can be shown to FAIL on the defect:

  1. ``_fit_lanes_to_band`` reverts to the OLD per-lane min/max clamp,
     which merges every overrunning lane onto the band edge — two or more
     bodies handed an IDENTICAL lane (symptom S3).
  2. ``_band_spacing_pull`` ignores its lane-step bound, so the fixed-metre
     band offset eats a large share of a narrowed lane and closes the
     ruck-side pair (symptom S2).

SCOPE NOTE: an earlier draft used ``RLM_DISABLE_RECONCILE`` here.  That
flag re-arms the stale-hold LIFECYCLE (symptom 1, measured by
``verify_line_participation_completeness.py``); it does not touch the lane
geometry, so it cannot make THIS module's clauses bite — it passed in both
modes and proved nothing.  A verifier that passes in both modes is void.

Exit 0 = intact.  Exit 1 = violation (listed).  Exit 2 = harness failure.
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

from game.defence.defence_lvl_1.defensive_pipeline import _line_defenders
from game.defence.defence_lvl_1.defensive_split_spacing import (
    LEFT,
    RIGHT,
    _SPACING_EXCLUDED_ROLES,
    _SPACING_ROLES,
    _fit_lanes_to_band,
    side_lanes,
)
import game.defence.defence_lvl_1.defensive_split_spacing as dss
from game.defence.defence_lvl_1 import tes_drive_structure as tds

MAX_FRAMES = 4000
DUPLICATE_EPS_M = 0.30
COMPRESSION_FRACTION = 0.50
OUT_LOG = "tmp_verify_compression.log"

failures = []


def _install_control_if_requested():
    """CONTROL: re-arm the PRE-FIX LANE GEOMETRY this module measures.

    ``RLM_DISABLE_LANE_GUARD=1`` restores the two behaviours that produced
    the field symptoms:

      1. ``_fit_lanes_to_band`` -> the OLD per-lane min/max clamp, which
         merges every overrunning lane onto the band edge: two or more
         bodies are handed an IDENTICAL lane (symptom S3).
      2. ``_band_spacing_pull`` -> its lane-step bound removed, so the
         fixed-metre band offset eats a large share of a narrowed lane and
         closes the ruck-side pair (symptom S2).

    Both are patched on the MODULE, where ``compute_targets`` resolves
    them, so the engine is re-armed without editing production code.
    """
    if os.environ.get("RLM_DISABLE_LANE_GUARD") != "1":
        return False

    def _legacy_per_lane_clamp(lanes, ruck_y, half_width):
        """PRE-FIX: clamp each lane independently into the band."""
        lo = float(ruck_y) - float(half_width)
        hi = float(ruck_y) + float(half_width)
        return [min(max(float(v), lo), hi) for v in lanes]

    _orig_pull = dss._band_spacing_pull

    def _legacy_unbounded_pull(world, tuners, d, y, max_abs_m=None):
        """PRE-FIX: ignore the lane-step bound entirely."""
        return _orig_pull(world, tuners, d, y, None)

    dss._fit_lanes_to_band = _legacy_per_lane_clamp
    dss._band_spacing_pull = _legacy_unbounded_pull
    print("  [CONTROL] lane guard disabled - pre-fix lane geometry reproduced")
    return True


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


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


def nominal_step(n_side, ruck_y, side, tuners, world=None, governor=None):
    """First count-lane gap the shipped model publishes for n_side.

    The published geometry is the governed-band FITTED ladder, so the
    nominal step must be read AFTER the same unit fit ``compute_targets``
    applies.  A RAW ladder step denominator reported a legitimately
    narrowed line as compression: an 11-lane 4.0 m ladder fitted to a
    2.478 m step makes a 1.187 m gap 0.48 of the PUBLISHED step but only
    0.30 of the raw one.
    """
    if n_side < 2:
        return None
    # PREFER THE PUBLISHED LADDER (2026-09-12).  ``compute_targets`` now
    # publishes the per-side ladder it ACTUALLY used (after the
    # governed-band unit fit) as ``world.frame_side_lanes``.  Reading that
    # is the only way to measure against the SAME law the chain applied —
    # a local re-derivation that misses the governor flags computes the RAW
    # 4.0 m step and reports a legitimately narrowed line as compression.
    # Fall back to the local derivation only when the publication is absent
    # or describes a different side population (verifier stubs).
    lanes = None
    published = getattr(world, "frame_side_lanes", None)
    if isinstance(published, dict):
        _cand = published.get(int(side))
        if _cand is None:
            _cand = published.get(side)
        if isinstance(_cand, (list, tuple)) and len(_cand) == n_side:
            lanes = [float(v) for v in _cand]
    if lanes is None:
        lanes = side_lanes(n_side, ruck_y, side, tuners)
    if not lanes or len(lanes) < 2:
        return None
    if governor is not None and world is not None:
        try:
            if (bool(getattr(tuners, "governor_width_active", False))
                    and bool(getattr(world, "governor_width_active", False))):
                lanes = _fit_lanes_to_band(
                    lanes, ruck_y,
                    float(governor.governed_width_m(world)) * 0.5,
                )
        except Exception:
            pass
    return abs(float(lanes[1]) - float(lanes[0]))


def collect():
    samples = []
    chambers = []
    unpinned_frames = []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        keep = list(world.defending_team)
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
            if phase not in ("OPEN_PLAY", "POST_RUCK_OPEN_PLAY"):
                continue
            ope = getattr(world, "open_play_engine", None)
            tuners = getattr(ope, "def_tuners", None)
            if tuners is None:
                continue
            # ATTRIBUTION CONTROL (2026-09-12): the Stage-9 anticipation
            # pre-shift is the ONE post-chain Y decoration that runs in
            # OPEN_PLAY (the ruck pin refuses Y there by contract).  Its
            # separation guard is a FIXED 0.5 m, not a fraction of the
            # published lane step, so it can legally converge a neighbour
            # pair below the lane law.  Disabling it must make C2.1 pass;
            # if it does not, the writer is elsewhere and this
            # attribution is wrong.
            if os.environ.get("RLM_DISABLE_ANTICIPATION") == "1":
                tuners.anticipation_enabled = False
            # A stale/absent dict is not this module's subject — the frame
            # spacing authority is verified elsewhere.  Measure only frames
            # where the chain actually published for THIS frame.
            fresh = bool(getattr(world, "frame_geometry_ready", False)) and (
                getattr(world, "frame_geometry_frame", None)
                == getattr(world, "frame_count", None)
            )
            spacing = getattr(world, "frame_spacing_targets", None) or {}
            if not fresh or not spacing:
                continue

            ruck_y = float(getattr(world, "ruck_y_m", 35.0))
            pool = _line_defenders(list(keep))
            cid = getattr(world, tds.CHAMBER_ID_ATTR, None)

            # CHAIN SCOPE — MIRRORS compute_targets EXACTLY.
            # compute_targets builds its chain pool by EXCLUSION:
            # fullback out, _SPACING_EXCLUDED_ROLES out, and a
            # tackle-state body out only when it carries no chain role.
            # A body with NO depth_role stamped is STILL a chain body
            # (its slot is resolved positionally), so requiring a
            # depth_role here would under-count the side and mis-derive
            # the nominal step.  Markers / DH / tacklers are the ones
            # deliberately pinned tight to the ruck by the documented
            # NON-CHAIN pin, and several share ``ruck_y`` by contract —
            # including them would report that contract as a defect.
            chain = []
            for d in pool:
                _role_name = getattr(
                    getattr(d, "defender_role", None), "name",
                    getattr(d, "defender_role", None),
                )
                if _role_name in _SPACING_EXCLUDED_ROLES:
                    continue
                if (getattr(d, "tackle_state_active", False)
                        and getattr(d, "depth_role", None)
                        not in _SPACING_ROLES):
                    continue
                chain.append(d)

            # C0 signal: chain bodies the chain published NO lane for.
            pinned = []
            unpinned = 0
            for d in chain:
                ty = spacing.get(id(d))
                if ty is None:
                    unpinned += 1
                    continue
                pinned.append((float(ty), d))
            unpinned_frames.append(unpinned)

            for side in (LEFT, RIGHT):
                pairs = sorted(
                    ((y, d) for y, d in pinned
                     if (y < ruck_y) == (int(side) < 0)),
                    key=lambda p: p[0],
                )
                ys = [y for y, _ in pairs]
                n_side = len(ys)
                if n_side < 2:
                    continue
                gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
                min_gap = min(gaps) if gaps else None
                step = nominal_step(
                    n_side, ruck_y, side, tuners,
                    world=world, governor=getattr(ope, "governor", None),
                )
                ratio = (min_gap / step) if (step and min_gap is not None) else None
                # Per-body detail so a violation can be attributed to a
                # specific clamp instead of guessed at.
                detail = [
                    (getattr(d, "player_id", None),
                     getattr(d, "depth_role", None),
                     round(float(y), 3))
                    for y, d in pairs
                ]
                samples.append({
                    "frame": int(world.frame_count),
                    "cid": cid,
                    "phase": phase,
                    "side": int(side),
                    "n": n_side,
                    "min_gap": min_gap,
                    "step": step,
                    "ratio": ratio,
                    "detail": detail,
                    "ruck_y": ruck_y,
                    "ladder": [round(v, 3) for v in
                               side_lanes(n_side, ruck_y, side, tuners)],
                })

            if cid is not None and cid != prev_cid:
                prev_cid = cid
                chambers.append({"cid": cid,
                                 "frame": int(world.frame_count),
                                 "pool": len(pool)})
    with open(OUT_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    return samples, chambers, unpinned_frames


def main():
    print("=" * 78)
    print("LINE COMPRESSION / DUPLICATION VERIFIER (symptoms 2 and 3)")
    print("=" * 78)
    _install_control_if_requested()
    samples, chambers, unpinned_frames = collect()
    if not samples:
        print("\nRESULT: FAILED - no fresh side-frames with >= 2 lanes.")
        return 2

    print("\n  side-frames measured : %d" % len(samples))
    print("  chambers observed    : %d" % len(chambers))

    # ── C0: total pinning ──
    print("\n[C0] EVERY POOL BODY IS ASSIGNED A LANE")
    worst_unpinned = max(unpinned_frames) if unpinned_frames else 0
    check("C0.1 no pool body is left out of the fresh frame dict",
          worst_unpinned == 0,
          "worst frame left %d pool bodies unpinned" % worst_unpinned)

    # ── C1: duplication ──
    print("\n[C1] NO SAME-SIDE LANE DUPLICATION")
    dupes = [s for s in samples
             if s["min_gap"] is not None and s["min_gap"] < DUPLICATE_EPS_M]
    check("C1.1 no two same-side lanes are within %.2f m" % DUPLICATE_EPS_M,
          not dupes,
          "%d side-frames with a shared lane, e.g. %s" % (
              len(dupes), [(s["frame"], s["side"], round(s["min_gap"], 3))
                           for s in dupes[:5]]))
    for s in dupes[:3]:
        print("      DIAG frame=%d side=%d ruck_y=%.2f n=%d step=%s"
              % (s["frame"], s["side"], s["ruck_y"], s["n"],
                 round(s["step"], 3) if s["step"] is not None else "n/a"))
        print("           ladder = %s" % s["ladder"])
        for pid, role, ty in s["detail"]:
            print("           id=%-5s role=%-8s target=%.3f" % (pid, role, ty))

    # ── C2: over-compression ──
    print("\n[C2] NO OVER-COMPRESSION vs the NOMINAL count-lane step")
    compress = [s for s in samples
                if s["ratio"] is not None and s["ratio"] < COMPRESSION_FRACTION]
    check("C2.1 min side lane gap >= %.2f x the nominal lane step"
          % COMPRESSION_FRACTION, not compress,
          "%d side-frames compressed below the bound, e.g. %s" % (
              len(compress), [(s["frame"], s.get("phase"), s["side"],
                               round(s["ratio"], 2))
                              for s in compress[:5]]))
    if compress:
        # PHASE ATTRIBUTION (2026-09-12): the compression authority is
        # phase-scoped.  In OPEN_PLAY the split-spacing chain is the SOLE
        # Y authority; in the post-ruck line-reforming window the
        # ruck-adjacent pin additionally owns the innermost pair.  Which
        # phase the compressed frames fall in therefore decides whether a
        # compression is a lane defect or the pin's documented ruck
        # compression.  Reported so the clause is never read phase-blind.
        _by_phase = {}
        for s in compress:
            key = s.get("phase")
            _by_phase[key] = _by_phase.get(key, 0) + 1
        print("      compressed-frame phase histogram: %s"
              % sorted(_by_phase.items(), key=lambda kv: -kv[1]))
    ratios = [s["ratio"] for s in samples if s["ratio"] is not None]
    if ratios:
        rs = sorted(ratios)
        print("      ratio min/p50/p90 = %.2f / %.2f / %.2f" % (
            rs[0], rs[len(rs) // 2], rs[int(len(rs) * 0.9)]))
    # Worst-compression attribution: print the per-body lanes against the
    # published ladder so the body that collapsed can be identified (band
    # pull / clamp / glide) instead of guessed at.
    for s in sorted(compress, key=lambda s: s["ratio"])[:3]:
        print("      DIAG frame=%d side=%d ruck_y=%.2f n=%d step=%s "
              "ratio=%.2f"
              % (s["frame"], s["side"], s["ruck_y"], s["n"],
                 round(s["step"], 3) if s["step"] is not None else "n/a",
                 s["ratio"]))
        print("           ladder = %s" % s["ladder"])
        for pid, role, ty in s["detail"]:
            print("           id=%-5s role=%-8s target=%.3f" % (pid, role, ty))

    # ── C3: no accumulation ──
    print("\n[C3] NO ACCUMULATION ACROSS CHAMBERS")
    by_chamber = {}
    for s in samples:
        if s["cid"] is None or s["ratio"] is None:
            continue
        by_chamber.setdefault(s["cid"], []).append(s["ratio"])
    seq = []
    for c in chambers:
        rs = by_chamber.get(c["cid"])
        if rs:
            seq.append(round(min(rs), 2))
    check("C3.1 the harness reached >= 3 chambers", len(seq) >= 3,
          "chambers=%d" % len(seq))
    if len(seq) >= 3:
        monotone = all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))
        check("C3.2 the min-lane ratio does not degrade every chamber",
              not (monotone and seq[0] > seq[-1]),
              "per-chamber min ratio: %s" % seq)
        print("      per-chamber min lane ratio: %s" % seq)
    if chambers:
        print("      per-chamber pool size     : %s"
              % [c["pool"] for c in chambers])

    print("\n" + "=" * 78)
    if failures:
        print("RESULT: %d VIOLATION(S) - line lanes are compressed / "
              "duplicated:" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("RESULT: LINE GEOMETRY INTACT - every pool body is assigned a lane, "
          "no same-side duplication, no over-compression against the nominal "
          "count-lane step, and no accumulation across chambers.")
    return 0


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
