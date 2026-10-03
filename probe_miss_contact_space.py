"""probe_miss_contact_space.py -- WHERE DOES A MISS ACTUALLY HAPPEN?

THE QUESTION
------------
When the pre-contact scalar resolves to ``MISS``, is the defender a GENUINE
NO-CONTACT failure (the bodies never reached collision space), or a TACKLE
FAILURE that occurred AFTER the bodies had already reached collision space?

THE PRE-CONTACT SCALAR (verbatim, world/open_play_engine.py::_check_pre_tackle)
-------------------------------------------------------------------------------
    dist        = hypot(bc - defender)            # measured, NOT used in score
    dot         = defender_heading . (defender->bc)
    angle_score = dot * 0.5 + 0.5                 # 0 (worst) .. 1 (head-on)
    rel_speed   = def_spd - bc_spd * 0.5
    speed_score = clamp(0..1, rel_speed / 10.0)
    tackle_score= angle_score * 0.6 + speed_score * 0.4     # THE FINAL SCORE

    MISS        <= tackle_score < 0.3
    ARM_GRAB    <= tackle_score < 0.6
    GLANCING    <= tackle_score < 0.75
    CONTACT     <= otherwise

``dist`` is measured but is NEVER used by the score (only a ``< 0.01`` m
degenerate branch returns CONTACT).  So the band says nothing about whether the
two bodies actually touched -- it grades APPROACH (angle + relative speed).

CONTACT SPACE
-------------
player/collision_body.py: CollisionBody.RADIUS_M = 0.55 m.  Two bodies are in
genuine collision space (surfaces touching / overlapping) when

    dist <= r_defender + r_carrier      (~1.10 m at the default radius)

The scalar is only ever reached inside ``TACKLE_RADIUS_M = 1.5 m`` (the
authority's conjunct 3), so the zone ``1.10 .. 1.50 m`` is "inside the tackle
radius but surfaces NOT touching" -- the only ambiguous band.

WHAT THIS PROBE DUMPS, PER MISS RESOLUTION (at resolution)
----------------------------------------------------------
    frame, bc_id, def_id, defender_role, tackle_role
    dist (separation), combined_radius, contact-space classification
    def_spd, bc_spd, closing_mps (TRUE closing), rel_speed (the score's term)
    dot, angle_score, speed_score, tackle_score (THE FINAL SCORE), band

CLASSIFICATION (at the instant of resolution)
---------------------------------------------
    IN_CONTACT_SPACE       dist <= combined_radius          (bodies touching)
    IN_RANGE_NOT_TOUCHED   combined_radius < dist <= 1.5 m
    OUT_OF_RANGE           dist > 1.5 m                    (genuine whiff)

A MISS that lands IN_CONTACT_SPACE is a tackle failure AFTER collision space
was reached.  One in IN_RANGE_NOT_TOUCHED is a near miss with no surface
contact.  OUT_OF_RANGE is a genuine no-contact whiff.

SECONDARY QUESTION
------------------
For every defender that resolved a MISS, did that defender EVER reach contact
space at any point in the rollout?  This separates "whiffed at range and never
got there" from "was in contact space at some moment".

METHOD / PURITY
---------------
Read-only.  The ONLY patch is a probe-local wrapper around the real
``OpenPlayEngine._check_pre_tackle`` which calls it UNCHANGED and records its
inputs plus the score RECOMPUTED from the same inputs.  The recomputed band is
asserted to agree with the band the real function returned, so a formula drift
cannot pass silently.  No gameplay file is modified.

Run:  python probe_miss_contact_space.py
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import math
import random
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

# The engine's per-frame physics chatter would drown the report.
ope_mod.DEBUG_SDEA_PHYSICS = False
ope_mod.DEBUG_OPE_TRACE = False

from world.field import Field
from world.world_state import WorldState, GamePhase
from world.open_play_engine import (
    OpenPlayEngine,
    TACKLE_RADIUS_M,
)
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch

# THE CONTACT_SPACE_MISS REACTION.  The authority imports
# ``apply_contact_space_miss`` INSIDE ``_apply_contact_space_miss``, so the
# module attribute is resolved at CALL time: wrapping it here intercepts every
# application without touching a single gameplay file.
import game.physics.contact_space_miss as csm_mod


# ── CONFIG ───────────────────────────────────────────────────────────
MAX_FRAMES = int(os.environ.get("RLM_MISS_CS_FRAMES", "1500"))
SEED = int(os.environ.get("RLM_MISS_CS_SEED", "20260827"))
SEEDS = [
    int(s) for s in
    os.environ.get("RLM_MISS_CS_SEEDS", str(SEED)).split(",")
    if s.strip()
]
DT = 1.0 / 60.0

# Seed of the rollout currently in flight (stamped on every record so the
# report can break the MISS census down per seed).
_CURRENT_SEED = SEED

# Collision-body radius fallback (player/collision_body.py::CollisionBody.RADIUS_M).
DEFAULT_BODY_RADIUS_M = 0.55

# The score's own band thresholds (mirrored so the recomputed band can be
# checked against the real function's return without importing a private name).
MISS_THRESHOLD = 0.3
ARM_GRAB_THRESHOLD = 0.6
GLANCING_THRESHOLD = 0.75

# Contact-space classification vocabulary.
CS_IN_CONTACT = "IN_CONTACT_SPACE"
CS_IN_RANGE = "IN_RANGE_NOT_TOUCHED"
CS_OUT_OF_RANGE = "OUT_OF_RANGE"

REPORT_PATH = os.environ.get(
    "RLM_MISS_CS_REPORT", "tmp_miss_contact_space_report.txt"
)


class Ledger:
    """Every _check_pre_tackle call, with inputs + score decomposition."""

    def __init__(self):
        self.calls = []            # every resolution
        self.band_mismatches = []  # recomputed band != returned band
        self.frames_stepped = 0
        self.csm_events = []       # every CONTACT_SPACE_MISS reaction applied


def _body_radius(p) -> float:
    """A body's collision radius (CollisionBody.radius, fallback 0.55 m)."""
    body = getattr(p, "body", None)
    try:
        r = float(getattr(body, "radius", DEFAULT_BODY_RADIUS_M))
    except (TypeError, ValueError):
        r = DEFAULT_BODY_RADIUS_M
    if not math.isfinite(r) or r <= 0.0:
        r = DEFAULT_BODY_RADIUS_M
    return r


def band_for(score: float) -> str:
    """The band the declared thresholds assign to *score*."""
    if score < MISS_THRESHOLD:
        return "MISS"
    if score < ARM_GRAB_THRESHOLD:
        return "ARM_GRAB"
    if score < GLANCING_THRESHOLD:
        return "GLANCING"
    return "CONTACT"


def classify_contact_space(dist: float, combined: float,
                           tackle_radius: float) -> str:
    """Contact-space class at the instant of resolution."""
    if dist <= combined:
        return CS_IN_CONTACT
    if dist <= tackle_radius:
        return CS_IN_RANGE
    return CS_OUT_OF_RANGE


def record(self, bc, defender, returned_band, ledger: Ledger) -> None:
    """Measure ONE _check_pre_tackle resolution.  Pure read of the bodies."""
    try:
        world = getattr(self, "world", None)
        frame = int(getattr(world, "frame_count", 0) or 0)

        dx = bc.x - defender.x
        dy = bc.y - defender.y
        dist = math.hypot(dx, dy)

        def_vx = float(getattr(defender, "vx", 0.0) or 0.0)
        def_vy = float(getattr(defender, "vy", 0.0) or 0.0)
        bc_vx = float(getattr(bc, "vx", 0.0) or 0.0)
        bc_vy = float(getattr(bc, "vy", 0.0) or 0.0)
        def_spd = math.hypot(def_vx, def_vy)
        bc_spd = math.hypot(bc_vx, bc_vy)

        # Defender heading (exactly the scalar's own convention).
        if def_spd > 0.1:
            def_dx = def_vx / def_spd
            def_dy = def_vy / def_spd
        else:
            facing = float(getattr(defender, "facing", 0.0) or 0.0)
            def_dx = math.cos(facing)
            def_dy = math.sin(facing)

        if dist > 1e-9:
            to_bc_dx = dx / dist
            to_bc_dy = dy / dist
        else:
            to_bc_dx, to_bc_dy = 1.0, 0.0

        dot = def_dx * to_bc_dx + def_dy * to_bc_dy

        # The score's own relative-speed term.
        rel_speed = def_spd - bc_spd * 0.5

        # TRUE closing speed (rate the separation is shrinking).
        closing = ((def_vx - bc_vx) * to_bc_dx
                   + (def_vy - bc_vy) * to_bc_dy)

        angle_score = dot * 0.5 + 0.5
        speed_score = max(0.0, min(1.0, rel_speed / 10.0))
        tackle_score = angle_score * 0.6 + speed_score * 0.4

        recomputed = band_for(tackle_score)
        if recomputed != returned_band:
            ledger.band_mismatches.append(
                (frame, returned_band, recomputed, round(tackle_score, 6))
            )

        combined = _body_radius(defender) + _body_radius(bc)
        cspace = classify_contact_space(dist, combined, TACKLE_RADIUS_M)

        role = getattr(defender, "defender_role", None)
        ledger.calls.append({
            "seed": _CURRENT_SEED,
            "frame": frame,
            "bc_id": int(getattr(bc, "player_id", -1) or -1),
            "def_id": int(getattr(defender, "player_id", -1) or -1),
            "defender_role": getattr(role, "name", None) or str(role),
            "tackle_role": getattr(defender, "tackle_role", None),
            "dist": dist,
            "combined_radius": combined,
            "contact_space": cspace,
            "def_spd": def_spd,
            "bc_spd": bc_spd,
            "closing_mps": closing,
            "rel_speed": rel_speed,
            "dot": dot,
            "angle_score": angle_score,
            "speed_score": speed_score,
            "tackle_score": tackle_score,
            "band": returned_band,
        })
    except Exception as exc:  # never let the instrument break a frame
        ledger.calls.append({"error": repr(exc)})


def install_wrapper(ledger: Ledger) -> None:
    """Wrap OpenPlayEngine._check_pre_tackle (call UNCHANGED, then record)."""
    real = OpenPlayEngine._check_pre_tackle

    def wrapper(self, bc, defender):
        result = real(self, bc, defender)
        record(self, bc, defender, result, ledger)
        return result

    OpenPlayEngine._check_pre_tackle = wrapper


def record_csm(world, bc, defender, result, ledger: Ledger) -> None:
    """Record ONE CONTACT_SPACE_MISS reaction (pure read of the result)."""
    if result is None:
        return
    try:
        frame = int(getattr(world, "frame_count", 0) or 0)
        ledger.csm_events.append({
            "seed": _CURRENT_SEED,
            "frame": frame,
            "bc_id": int(getattr(bc, "player_id", -1) or -1),
            "def_id": int(getattr(defender, "player_id", -1) or -1),
            "applied": bool(getattr(result, "applied", False)),
            "separation_m": float(getattr(result, "separation_m", math.nan)),
            "combined_radius_m": float(
                getattr(result, "combined_radius_m", math.nan)),
            "carrier_dx": float(getattr(result, "carrier_dx", 0.0)),
            "carrier_dy": float(getattr(result, "carrier_dy", 0.0)),
            "carrier_dvx": float(getattr(result, "carrier_dvx", 0.0)),
            "carrier_dvy": float(getattr(result, "carrier_dvy", 0.0)),
            "defender_dx": float(getattr(result, "defender_dx", 0.0)),
            "defender_dy": float(getattr(result, "defender_dy", 0.0)),
            "defender_dvx": float(getattr(result, "defender_dvx", 0.0)),
            "defender_dvy": float(getattr(result, "defender_dvy", 0.0)),
            "defender_stagger_s": float(
                getattr(result, "defender_stagger_s", 0.0)),
            "defender_role": str(getattr(
                getattr(defender, "defender_role", None), "name", "-")),
            "tackle_role": getattr(defender, "tackle_role", None),
        })
    except Exception as exc:  # never let the instrument break a frame
        ledger.csm_events.append({"error": repr(exc)})


def install_csm_wrapper(ledger: Ledger) -> None:
    """Wrap contact_space_miss.apply_contact_space_miss (the reaction).

    The authority resolves this attribute INSIDE its own helper, so wrapping
    the MODULE attribute intercepts every application it makes.  When
    ``RLM_CONTACT_SPACE_MISS=0`` disarms the path the wrapper is never called
    at all -- which is exactly the OFF-arm signal the census needs in order to
    prove it is measuring the NEW path rather than noise.
    """
    real = csm_mod.apply_contact_space_miss

    def wrapper(world, bc, defender, tuners=None):
        result = real(world, bc, defender, tuners)
        record_csm(world, bc, defender, result, ledger)
        return result

    csm_mod.apply_contact_space_miss = wrapper
    ledger.csm_wrapped = True


# ═════════════════════════════════════════════════════════════════════
# THE ROLLOUT — the real engine, booted exactly like the other probes
# ═════════════════════════════════════════════════════════════════════

def run_rollout(ledger, frames=MAX_FRAMES, seed=SEED, verbose=True):
    global _CURRENT_SEED
    _CURRENT_SEED = seed
    # Seed BEFORE any construction.  Constructors can draw from the global
    # `random` module; any draw taken before this line would make the rollout
    # depend on the process's start-up entropy (cross-process drift).
    random.seed(seed)
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

    for frame in range(1, frames + 1):
        world.frame_count = frame
        ball.update(DT)

        if world.phase == GamePhase.KICKOFF:
            kickoff_engine.update(DT)
            check_for_catch(world, ball, DT)
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                world.open_play.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        elif world.phase == GamePhase.OPEN_PLAY:
            open_play.update(DT)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

        ledger.frames_stepped += 1
        if verbose and frame % 400 == 0:
            print("      seed %d frame %d" % (seed, frame),
                  file=_REAL_STDOUT, flush=True)

    return {
        "ledger": ledger,
        "world": world,
        "open_play": open_play,
    }


# ═════════════════════════════════════════════════════════════════════
# ANALYSIS
# ═════════════════════════════════════════════════════════════════════

def _err_rows(calls):
    return [c for c in calls if "error" in c]


def _valid(calls):
    return [c for c in calls if "error" not in c]


def percentiles(values, ps=(0, 25, 50, 75, 90, 100)):
    """Percentile table (nearest-rank) for a list of floats."""
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    if n == 0:
        return {}
    out = {}
    for p in ps:
        if p <= 0:
            out[p] = vals[0]
        elif p >= 100:
            out[p] = vals[-1]
        else:
            idx = int(round((p / 100.0) * (n - 1)))
            out[p] = vals[idx]
    return out


def fmt_pct(table):
    return "  ".join("p%s=%.3f" % (k, v) for k, v in sorted(table.items()))


def summarise(res):
    led = res["ledger"]
    calls = led.calls
    err = _err_rows(calls)
    valid = _valid(calls)
    miss = [c for c in valid if c["band"] == "MISS"]

    # Every defender that ever resolved a MISS, and the closest it ever got
    # across the WHOLE rollout (of any band, at any resolution).
    min_dist_by_def = {}
    for c in valid:
        d = c["def_id"]
        min_dist_by_def[d] = min(min_dist_by_def.get(d, math.inf), c["dist"])

    miss_def_ids = sorted({c["def_id"] for c in miss})
    miss_defs_reached_contact = [
        d for d in miss_def_ids
        if min_dist_by_def.get(d, math.inf) <= 2.0 * DEFAULT_BODY_RADIUS_M
    ]

    by_space = {CS_IN_CONTACT: 0, CS_IN_RANGE: 0, CS_OUT_OF_RANGE: 0}
    for c in miss:
        by_space[c["contact_space"]] = by_space.get(c["contact_space"], 0) + 1

    miss_by_seed = {}
    for c in miss:
        key = c.get("seed")
        miss_by_seed[key] = miss_by_seed.get(key, 0) + 1

    miss_closing_pos = sum(1 for c in miss if c["closing_mps"] > 0.0)
    miss_closing_neg = sum(1 for c in miss if c["closing_mps"] < 0.0)

    # Count contiguous IN_CONTACT_SPACE runs (same seed + defender, adjacent
    # frames) so a single dive-through reads as ONE event, not N frames.
    contact_rows = sorted(
        [c for c in miss if c["contact_space"] == CS_IN_CONTACT],
        key=lambda c: (c.get("seed"), c["frame"]),
    )
    contact_episodes = 0
    prev_key = None
    prev_frame = None
    for c in contact_rows:
        key = (c.get("seed"), c["def_id"])
        adjacent = (prev_frame is not None and c["frame"] == prev_frame + 1)
        if key != prev_key or not adjacent:
            contact_episodes += 1
        prev_key = key
        prev_frame = c["frame"]

    band_counts = {}
    for c in valid:
        band_counts[c["band"]] = band_counts.get(c["band"], 0) + 1

    # ── THE CONTACT_SPACE_MISS CENSUS ─────────────────────────────────
    # Every reaction the authority ACTUALLY applied this sweep.  It is empty in
    # the OFF arm by construction, which is what makes it a MEASUREMENT of the
    # new path rather than a restatement of the code.
    csm_raw = list(getattr(led, "csm_events", []))
    csm_err = [e for e in csm_raw if "error" in e]
    csm = _valid(csm_raw)
    csm_applied = [e for e in csm if e["applied"]]
    csm_by_seed = {}
    for e in csm_applied:
        csm_by_seed[e["seed"]] = csm_by_seed.get(e["seed"], 0) + 1
    csm_sorted = sorted(csm_applied,
                        key=lambda e: (e["seed"], e["frame"], e["def_id"]))
    csm_episodes = 0
    csm_prev_key = None
    csm_prev_frame = None
    for e in csm_sorted:
        key = (e["seed"], e["def_id"])
        adjacent = (csm_prev_frame is not None
                    and e["frame"] == csm_prev_frame + 1)
        if key != csm_prev_key or not adjacent:
            csm_episodes += 1
        csm_prev_key = key
        csm_prev_frame = e["frame"]
    csm_stats = {
        "calls": len(csm_raw),
        "errors": csm_err,
        "applied": len(csm_applied),
        "episodes": csm_episodes,
        "by_seed": csm_by_seed,
        "rows": csm_applied,
        "defenders": sorted({e["def_id"] for e in csm_applied}),
        "roles": sorted({e["defender_role"] for e in csm_applied}),
        "separation_m": [e["separation_m"] for e in csm_applied],
        "carrier_dx": [e["carrier_dx"] for e in csm_applied],
        "carrier_dvx": [e["carrier_dvx"] for e in csm_applied],
        "defender_dx": [e["defender_dx"] for e in csm_applied],
        "stagger_s": [e["defender_stagger_s"] for e in csm_applied],
        "in_contact_before": sum(
            1 for e in csm_applied
            if e["separation_m"] <= e["combined_radius_m"]),
    }

    return {
        "frames": led.frames_stepped,
        "calls": len(calls),
        "errors": err,
        "valid": len(valid),
        "band_counts": band_counts,
        "csm": csm_stats,
        "csm_wrapped": bool(getattr(led, "csm_wrapped", False)),
        "miss": miss,
        "miss_count": len(miss),
        "by_space": by_space,
        "miss_by_seed": miss_by_seed,
        "miss_closing_pos": miss_closing_pos,
        "miss_closing_neg": miss_closing_neg,
        "contact_episodes": contact_episodes,
        "band_mismatches": led.band_mismatches,
        "miss_def_ids": miss_def_ids,
        "miss_defs_reached_contact": miss_defs_reached_contact,
        "min_dist_by_def": min_dist_by_def,
    }


_REAL_STDOUT = sys.stdout
_REPORT = None


class tee:
    def write(self, s):
        try:
            if _REPORT is not None:
                _REPORT.write(s)
        except Exception:
            pass
        return _REAL_STDOUT.write(s)

    def flush(self):
        try:
            if _REPORT is not None:
                _REPORT.flush()
        except Exception:
            pass
        return _REAL_STDOUT.flush()


def banner(title):
    print("")
    print("=" * 100)
    print(title)
    print("=" * 100)


def sub(title):
    print("")
    print("-" * 100)
    print(title)
    print("-" * 100)


def report(res):
    s = summarise(res)
    miss = s["miss"]

    banner("MISS CONTACT-SPACE PROBE — where does a MISS actually happen?")
    print("  seeds                      : %s" % res.get("seeds"))
    print("  CONTACT_SPACE_MISS arm     : %s (RLM_CONTACT_SPACE_MISS)"
          % res.get("arm", "?"))
    print("  frames stepped (sum)       : %d" % s["frames"])
    print("  _check_pre_tackle calls    : %d (valid %d, errors %d)"
          % (s["calls"], s["valid"], len(s["errors"])))
    print("  TACKLE_RADIUS_M            : %.2f m (the eligibility radius)"
          % TACKLE_RADIUS_M)
    print("  combined body radius       : %.2f m (2 x %.2f)"
          % (2 * DEFAULT_BODY_RADIUS_M, DEFAULT_BODY_RADIUS_M))
    print("  NOTE: the score itself NEVER reads separation — only angle and "
          "relative speed.")

    ref = 0
    sub("A. BAND COVERAGE (every resolution) and formula self-check")
    for b in ("MISS", "ARM_GRAB", "GLANCING", "CONTACT"):
        n = s["band_counts"].get(b, 0)
        ref += n
        print("  %-10s = %d" % (b, n))
    print("  total      = %d" % ref)
    if s["band_mismatches"]:
        print("  !! %d resolution(s) where the recomputed band disagreed with "
              "the band the real function returned:" % len(s["band_mismatches"]))
        for row in s["band_mismatches"][:20]:
            print("       frame=%s returned=%s recomputed=%s score=%.6f"
                  % row)
    else:
        print("  formula self-check: the recomputed band agreed with the "
              "returned band on EVERY resolution (no drift).")

    sub("B. THE ANSWER — MISS classified by contact space at resolution")
    n_miss = s["miss_count"]
    if n_miss == 0:
        print("  no MISS resolution in this rollout — nothing to classify.")
        return s
    for k in (CS_IN_CONTACT, CS_IN_RANGE, CS_OUT_OF_RANGE):
        n = s["by_space"].get(k, 0)
        print("  %-22s = %-6d (%.1f%% of MISS)"
              % (k, n, 100.0 * n / n_miss))
    print("  MISS per seed              : %s  (total %d)"
          % (s["miss_by_seed"], n_miss))
    print("  MISS closing (true)        : closing in=%d  separating=%d"
          % (s["miss_closing_pos"], s["miss_closing_neg"]))
    print("  IN_CONTACT_SPACE episodes  : %d (contiguous runs, per defender)"
          % s["contact_episodes"])
    frac_contact = s["by_space"].get(CS_IN_CONTACT, 0) / float(n_miss)
    frac_range = s["by_space"].get(CS_IN_RANGE, 0) / float(n_miss)
    print("")
    if frac_contact >= 0.5:
        print("  VERDICT: the MAJORITY (%.0f%%) of MISS resolutions occurred "
              "with the bodies IN CONTACT SPACE —" % (100.0 * frac_contact))
        print("           MISS is primarily a TACKLE FAILURE after collision "
              "space was reached, not a whiff.")
    elif frac_contact + frac_range >= 0.5:
        print("  VERDICT: few MISS (%.0f%%) are in contact space, but %.0f%% "
              "are INSIDE the tackle radius while NOT touching —"
              % (100.0 * frac_contact, 100.0 * frac_range))
        print("           MISS is largely a NEAR-CONTACT band (in range, no "
              "surface contact).")
    else:
        print("  VERDICT: the MAJORITY of MISS resolutions are OUTSIDE the "
              "tackle radius — genuine no-contact whiffs.")

    sub("C. MISS SEPARATION and SCORE distributions")
    dists = [c["dist"] for c in miss]
    scores = [c["tackle_score"] for c in miss]
    closing = [c["closing_mps"] for c in miss]
    rels = [c["rel_speed"] for c in miss]
    dots = [c["dot"] for c in miss]
    print("  dist (m)          : %s" % fmt_pct(percentiles(dists)))
    print("  tackle_score      : %s" % fmt_pct(percentiles(scores)))
    print("  closing_mps (true): %s" % fmt_pct(percentiles(closing)))
    print("  rel_speed (score) : %s" % fmt_pct(percentiles(rels)))
    print("  dot (heading)     : %s" % fmt_pct(percentiles(dots)))

    # How close to the MISS_THRESHOLD were the misses? (near-miss vs deep miss)
    near_threshold = [c for c in miss if c["tackle_score"] >= 0.2]
    print("  MISS with score >= 0.20 (near-threshold) : %d / %d"
          % (len(near_threshold), n_miss))
    print("  MISS with score <  0.10 (deep miss)      : %d / %d"
          % (len([c for c in miss if c["tackle_score"] < 0.10]), n_miss))

    sub("D. DID THE MISSING DEFENDERS EVER REACH CONTACT SPACE?")
    total_defs = len(s["miss_def_ids"])
    reached = len(s["miss_defs_reached_contact"])
    print("  distinct defenders that resolved >=1 MISS : %d" % total_defs)
    print("  of those, ever reached contact space (<= %.2f m) at ANY moment: "
          "%d" % (2 * DEFAULT_BODY_RADIUS_M, reached))
    if total_defs:
        print("  fraction                                   : %.1f%%"
              % (100.0 * reached / total_defs))

    sub("E. SAMPLE MISS RESOLUTIONS (first 25, in frame order)")
    print("  %-6s %-6s %-14s %-8s %-8s %-9s %-9s %-7s %-7s %s"
          % ("frame", "def", "role", "dist", "comb_r", "closing",
             "score", "dot", "band", "space"))
    for c in sorted(miss, key=lambda r: r["frame"])[:25]:
        print("  %-6d %-6d %-14s %-8.3f %-8.2f %-9.2f %-9.4f %-7.3f %-7s %s"
              % (c["frame"], c["def_id"], (c["defender_role"] or "-")[:14],
                 c["dist"], c["combined_radius"], c["closing_mps"],
                 c["tackle_score"], c["dot"], c["band"], c["contact_space"]))

    sub("F. CONTACT_SPACE_MISS REACTIONS APPLIED (live)")
    csm = s["csm"]
    print("  instrument                 : %s"
          % ("wrapped csm.apply_contact_space_miss"
             if s["csm_wrapped"] else "NOT WRAPPED"))
    print("  reaction calls seen        : %d (errors %d)"
          % (csm["calls"], len(csm["errors"])))
    print("  reactions APPLIED          : %d" % csm["applied"])
    print("  reaction EPISODES          : %d (contiguous runs, per defender)"
          % csm["episodes"])
    print("  applied per seed           : %s" % csm["by_seed"])
    if csm["applied"] == 0:
        print("  (no reaction fired — either the arm is OFF or no MISS landed "
              "inside contact space this sweep)")
    else:
        print("  defenders staggered        : %s" % (csm["defenders"],))
        print("  defender roles             : %s" % (csm["roles"],))
        print("  separation at reaction (m) : %s"
              % fmt_pct(percentiles(csm["separation_m"])))
        print("  carrier nudge |dx| (m)     : %s"
              % fmt_pct(percentiles([abs(v) for v in csm["carrier_dx"]])))
        print("  carrier Dvx (m/s)          : %s"
              % fmt_pct(percentiles(csm["carrier_dvx"])))
        print("  defender push |dx| (m)     : %s"
              % fmt_pct(percentiles([abs(v) for v in csm["defender_dx"]])))
        print("  defender stagger (s)       : %s"
              % fmt_pct(percentiles(csm["stagger_s"])))
        print("  fired while bodies touching: %d / %d (separation <= combined "
              "radius)" % (csm["in_contact_before"], csm["applied"]))
        print("")
        print("  %-6s %-6s %-14s %-9s %-9s %-9s %-9s %s"
              % ("frame", "def", "role", "sep_m", "comb_r",
                 "bc_dx", "bc_dvx", "stagger"))
        for e in csm["rows"][:25]:
            print("  %-6d %-6d %-14s %-9.3f %-9.3f %-9.3f %-9.3f %.2f"
                  % (e["frame"], e["def_id"], e["defender_role"][:14],
                     e["separation_m"], e["combined_radius_m"],
                     e["carrier_dx"], e["carrier_dvx"],
                     e["defender_stagger_s"]))
    if csm["errors"]:
        for e in csm["errors"][:10]:
            print("  !! %s" % e)

    if s["errors"]:
        sub("G. INSTRUMENT ERRORS (_check_pre_tackle census)")
        for e in s["errors"][:10]:
            print("  %s" % e)

    return s


def main():
    real = sys.stdout

    # ── THE ARM ──
    # `on` (default) leaves RLM_CONTACT_SPACE_MISS unset -> the authority's
    # gate arms the reaction.  `off` sets RLM_CONTACT_SPACE_MISS=0 so the
    # authority NEVER calls apply_contact_space_miss.  Section F must read 0
    # applied in the OFF arm; that is what proves the census is measuring the
    # new path and not incidental state.
    arm = os.environ.get("RLM_MISS_CS_ARM", "on").strip().lower()
    if arm not in ("on", "off"):
        raise SystemExit("RLM_MISS_CS_ARM must be 'on' or 'off', got %r"
                         % arm)
    if arm == "off":
        os.environ["RLM_CONTACT_SPACE_MISS"] = "0"
    else:
        os.environ.pop("RLM_CONTACT_SPACE_MISS", None)

    ledger = Ledger()
    install_wrapper(ledger)
    install_csm_wrapper(ledger)
    print("      arm=%s  csm_wrapped=%s"
          % (arm, getattr(ledger, "csm_wrapped", False)),
          file=real, flush=True)

    devnull = open(os.devnull, "w", encoding="utf-8")
    last_world = None
    seeds_done = []
    for idx, seed in enumerate(SEEDS, 1):
        sys.stdout = devnull
        try:
            out = run_rollout(ledger, seed=seed)
        finally:
            sys.stdout = real
        last_world = out["world"]
        seeds_done.append(seed)
        print("      seed %d complete (%d/%d) - cumulative calls=%d"
              % (seed, idx, len(SEEDS), len(ledger.calls)),
              file=real, flush=True)

        # Rewrite the pooled report after EVERY seed, so an interrupted sweep
        # still leaves a complete report for the seeds that have finished.
        pooled = {
            "ledger": ledger,
            "world": last_world,
            "seeds": list(seeds_done),
            "arm": arm,
        }
        rep = open(REPORT_PATH, "w", encoding="utf-8")
        sys.stdout = rep
        try:
            report(pooled)
        finally:
            sys.stdout = real
        rep.flush()
        rep.close()

    print("")
    print("  Report written to %s" % REPORT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
