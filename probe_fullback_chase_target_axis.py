"""probe_fullback_chase_target_axis.py — EMERGENCY-CHASE TARGET-AXIS LEDGER.

THE QUESTION
------------
The tagged fullback's emergency-chase command: is it the carrier's CURRENT
POSITION, an X-ONLY TRAIL behind it, an X-ONLY LEAD ahead of it, or a TWO-AXIS
INTERCEPT projection — and how often does the command, or the body that
integrates it, cross AHEAD of the carrier's lane while the line break is being
recovered?

WHAT IT MEASURES (per line-break-recovery window, per tagged fullback)
---------------------------------------------------------------------
  * TARGET KIND per frame — ``classify_target_axis`` over the frame's final
    command vs the live carrier (CURRENT / TRAIL_X / LEAD_X / INTERCEPT_XY),
    with the target-ahead share split into 0-1 / 1-2 / >2 m bands.
  * Y-AXIS UTILISATION — mean ``|target_y - bc.y|``, the share of frames whose
    Y command is a pure live-lane MIRROR, and the best-fit lead horizon
    ``tau*`` that explains the Y command from ``bc.y + bc.vy*tau``.  This is
    the direct test of "the target does not use the y axis".
  * CROSSINGS — the count of X lane crossings (body passing the carrier in the
    attack axis) and Y lane crossings (body passing through the carrier's
    lateral lane), with the peak ahead / lateral margins.
  * ATTRIBUTION — which ``target_source`` owned each frame.
  * A-B — the same rollout with ``RLM_FB_CHASE_INTERCEPT`` ON (the shipped
    intercept law) and OFF (the historical X-only trail), same seeds and
    frames, so the change is measured rather than asserted.

METHOD
------
Read-only.  The probe drives the REAL engine (same boot sequence as
``probe_fullback_emergency_lifetime.py``) and samples the tagged fullback
AFTER the frame has completed, so every number is the frame's FINAL applied
state.  No engine callable is wrapped or mutated; the only engine state the
probe touches is the module-level lever it is explicitly A/B-ing.

Run:    python probe_fullback_chase_target_axis.py
Report: tmp_fb_chase_target_axis.txt
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import random
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

ope_mod.DEBUG_SDEA_PHYSICS = False
ope_mod.DEBUG_OPE_TRACE = False

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

import game.defence.defence_lvl_1.fullback_role as fbr
from game.debug.fb_chase_target_axis import (
    AxisSummary,
    TARGET_CURRENT_POSITION,
    TARGET_INTERCEPT_XY,
    TARGET_LEAD_X,
    TARGET_TRAIL_X,
    TARGET_UNKNOWN,
    best_fit_lead_horizon,
    classify_target_axis,
    crossing_stats,
    y_axis_utilisation,
)

# ── ROLLOUT CONFIG ───────────────────────────────────────────────────
DT = 1.0 / 60.0
MAX_FRAMES = int(os.environ.get("RLM_FBCTA_FRAMES", "6000"))
SEEDS = [
    int(part)
    for part in os.environ.get(
        "RLM_FBCTA_SEEDS", "20260827,20260830"
    ).split(",")
    if part.strip()
]

# A recovery window must be at least this long to be counted: a one-frame
# blip is a measurement artefact, not a chase.
MIN_WINDOW_FRAMES = int(os.environ.get("RLM_FBCTA_MIN_WINDOW", "5"))

# The ahead-margin bands reported for the COMMAND (metres).
AHEAD_BANDS_M = (1.0, 2.0)

# A command further than this from the carrier is kept as NAMED evidence.
HEAVY_COMMAND_M = 6.0
HEAVY_KEEP = 3

REPORT_PATH = os.environ.get(
    "RLM_FBCTA_REPORT", "tmp_fb_chase_target_axis.txt"
)

TARGET_KINDS = (
    TARGET_CURRENT_POSITION,
    TARGET_TRAIL_X,
    TARGET_LEAD_X,
    TARGET_INTERCEPT_XY,
    TARGET_UNKNOWN,
)


def pid_of(player):
    """Stable id of *player* (None when absent)."""
    if player is None:
        return None
    return getattr(player, "player_id", id(player))


def tagged_fullback(world):
    """The single ``fullback_role_active`` body on the defending team."""
    for d in (getattr(world, "defending_team", None) or ()):
        if d is not None and getattr(d, "fullback_role_active", False):
            return d
    return None


def is_recovery_frame(world, fb, bc) -> bool:
    """True when a line-break recovery is live for the tagged fullback.

    The SAME two-branch test the engine uses (``line_break_active`` OR the 2D
    ``classify_fullback_break``), read through the fullback module's public
    helpers so the probe can never disagree with the authority about what a
    break is.
    """
    if world is None or fb is None or bc is None:
        return False
    try:
        if fbr.line_break_active(world, bc):
            return True
    except Exception:
        return False
    try:
        anchor_x = fbr.line_anchor_x(world)
        _ax, anchor_y = fbr.compute_fullback_anchor(bc, anchor_x)
        return (
            fbr.classify_fullback_break(bc, fb, anchor_x, anchor_y)
            != fbr.FB_BREAK_NONE
        )
    except Exception:
        return False


def _chase_reference(bc, fb):
    """``(anchor_x, break_kind, chase_x, chase_y)`` for this frame.

    THE DISCRIMINATOR.  ``fullback_chase_point`` is the chase law's own answer
    for this frame; comparing the frame's FINAL ``target_x`` / ``target_y``
    against it says whether the chase point actually OWNED the frame or
    whether another writer (the pre-tackle corridor, the writer-priority band
    resolve) or the SWEEPER ANCHOR did.  Without this split the "command ahead
    of carrier" metric conflates the two: the sweeper anchor sits
    ``ruck_x + 10 + depth`` -- up to 40 m beyond the line -- which is legitimately
    far ahead of a carrier the play has driven backwards.
    """
    try:
        anchor_x = fbr.line_anchor_x(_WORLD_HOLDER[0])
    except Exception:
        anchor_x = 0.0
    bc_x = float(getattr(bc, "x", 0.0) or 0.0)
    bc_y = float(getattr(bc, "y", 0.0) or 0.0)
    anchor_y = bc_y
    try:
        _ax, anchor_y = fbr.compute_fullback_anchor(bc, anchor_x)
    except Exception:
        pass
    try:
        break_kind = fbr.classify_fullback_break(bc, fb, anchor_x, anchor_y)
    except Exception:
        break_kind = fbr.FB_BREAK_NONE
    try:
        chase_x, chase_y = fbr.fullback_chase_point(
            bc, fb, anchor_x, break_kind
        )
    except Exception:
        chase_x, chase_y = bc_x, bc_y
    # FIELD-BOUND the reference exactly as the APPLIED command is bound.  The
    # intercept path clamps internally (its last statement is
    # ``_clamp_anchor``); the legacy trail path does NOT -- its clamp lands
    # downstream.  Without this, a carrier in-goal (bc.x ~ 120) gives the
    # intercept arm a reference of 112 that matches the applied command while
    # the trail arm's reference of 119 does not, and the ownership test would
    # credit one arm with frames the other cannot match.  ``_clamp_anchor`` IS
    # the module's own bound, so the two arms are compared like for like.
    try:
        chase_x, chase_y = fbr._clamp_anchor(chase_x, chase_y)
    except Exception:
        pass
    return anchor_x, break_kind, float(chase_x), float(chase_y)


# The world of the rollout in progress.  ``_chase_reference`` needs the line
# anchor authority, which reads the world; the probe holds it here rather than
# threading it through every sampling call.
_WORLD_HOLDER: list = [None]


def sample_frame(world, fb, bc) -> dict:
    """Read one frame's chase-target state (pure: no writes)."""
    _anchor_x, break_kind, chase_x, chase_y = _chase_reference(bc, fb)
    return {
        "break_kind": break_kind,
        "chase_x": chase_x,
        "chase_y": chase_y,
        "frame": getattr(world, "frame_count", None),
        "pid": pid_of(fb),
        "fb_x": float(getattr(fb, "x", 0.0) or 0.0),
        "fb_y": float(getattr(fb, "y", 0.0) or 0.0),
        "fb_vx": float(getattr(fb, "vx", 0.0) or 0.0),
        "fb_vy": float(getattr(fb, "vy", 0.0) or 0.0),
        "bc_x": float(getattr(bc, "x", 0.0) or 0.0),
        "bc_y": float(getattr(bc, "y", 0.0) or 0.0),
        "bc_vx": float(getattr(bc, "vx", 0.0) or 0.0),
        "bc_vy": float(getattr(bc, "vy", 0.0) or 0.0),
        "target_x": getattr(fb, "target_x", None),
        "target_y": getattr(fb, "target_y", None),
        "target_source": getattr(fb, "target_source", None) or "(none)",
        "writer": getattr(fb, "_target_writer", None) or "(none)",
        "state": getattr(
            getattr(fb, "movement_state", None), "name", None
        ) or str(getattr(fb, "movement_state", None)),
        "move_speed": float(getattr(fb, "move_speed", 0.0) or 0.0),
        "fullback_role": bool(getattr(fb, "fullback_role_active", False)),
        "emergency": fbr.is_fullback_emergency_engaged(fb),
        "tackle_role": getattr(fb, "tackle_role", None),
        "frozen": bool(getattr(fb, "frozen", False)),
        "freeze_timer": float(getattr(fb, "freeze_timer", 0.0) or 0.0),
    }


def run_rollout(frames=MAX_FRAMES, seed=20260827, mode="intercept"):
    """Boot the real engine, kick off, step it, return the target ledger.

    *mode* is applied by rebinding the module lever, so both legs are driven
    by the SAME engine code path with no fork.
    """
    fbr.FB_CHASE_INTERCEPT = mode == "intercept"

    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball

    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play

    kickoff_engine = KickoffEngine(world, ball, field)

    random.seed(seed)
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    windows = []
    open_window = None
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
                players = getattr(world, "players", None) or []
                if players:
                    collision_system.resolve_all(players, world=world)

        # SAMPLE AFTER the engine has finished the frame.
        _WORLD_HOLDER[0] = world
        fb = tagged_fullback(world)
        bc = getattr(world, "ball_carrier", None)
        live = is_recovery_frame(world, fb, bc)

        if live:
            if open_window is None or open_window["pid"] != pid_of(fb):
                # A new recovery run (or the tag moved to another body).
                open_window = {
                    "pid": pid_of(fb),
                    "frame": frame,
                    "end": frame,
                    "samples": [],
                }
                windows.append(open_window)
            open_window["end"] = frame
            open_window["samples"].append(sample_frame(world, fb, bc))
        else:
            open_window = None

    return {
        "mode": mode,
        "seed": seed,
        "frames": frames,
        "windows": [
            w for w in windows if len(w["samples"]) >= MIN_WINDOW_FRAMES
        ],
    }


def target_kind_of(sample) -> str:
    """``classify_target_axis`` for one sampled frame (None-safe)."""
    tx = sample.get("target_x")
    ty = sample.get("target_y")
    if tx is None or ty is None:
        return TARGET_UNKNOWN
    return classify_target_axis(tx, ty, sample["bc_x"], sample["bc_y"])


def target_ahead_margin(sample):
    """Signed attack-axis margin of the COMMAND (positive = ahead)."""
    tx = sample.get("target_x")
    if tx is None:
        return None
    return float(tx) - sample["bc_x"]


def _hypot(dx, dy) -> float:
    """Local 2D distance (keeps the module import list minimal)."""
    return (float(dx) ** 2 + float(dy) ** 2) ** 0.5


def summarise(rollout) -> dict:
    """Aggregate one rollout's windows through the pure classifier."""
    summary = AxisSummary()
    kind_counts = {kind: 0 for kind in TARGET_KINDS}
    ahead_band_counts = {band: 0 for band in AHEAD_BANDS_M}
    source_counts = {}
    # CHASE-POINT OWNERSHIP SPLIT: "chase" = the frame's final command IS the
    # chase law's own answer; "other" = the sweeper anchor, the pre-tackle
    # corridor or the band resolve wrote it.  Conflating them inflates the
    # ahead-of-carrier share with the anchor, which is legitimately far ahead
    # of a carrier the play has driven back.
    chase_split = {
        "chase_frames": 0,
        "chase_ahead_frames": 0,
        "chase_worst_ahead_m": 0.0,
        "other_frames": 0,
        "other_ahead_frames": 0,
        "other_worst_ahead_m": 0.0,
        # Command-to-carrier DISTANCE per ownership bucket.  The chase law
        # bounds the point to ``FB_CHASE_TRAIL_M + FB_CHASE_LEAD_MAX_M`` of the
        # carrier, so a large bucket mean would mean the frame's command was
        # NOT the chase point after all -- the check that keeps the ownership
        # verdict honest.
        "chase_distance_sum": 0.0,
        "chase_distance_max": 0.0,
        "other_distance_sum": 0.0,
        "other_distance_max": 0.0,
        # LATERAL command restricted to the frames the CHASE POINT owned.  The
        # global Y figure is diluted by sweeper-anchor frames, so this is the
        # number that actually tests "the chase does not use the y axis".
        "chase_y_sum": 0.0,
        "chase_y_mirror_frames": 0,
        "other_y_sum": 0.0,
        "other_y_mirror_frames": 0,
    }
    # Frames whose command sits far from the carrier: kept as evidence so the
    # report can NAME them instead of asserting what they must be.
    heavy = []

    for window in rollout["windows"]:
        samples = window["samples"]

        # ── PER-FRAME ──
        for sample in samples:
            summary.frames += 1
            kind_counts[target_kind_of(sample)] += 1

            margin = target_ahead_margin(sample)
            if margin is not None and margin > 0.0:
                summary.target_ahead_frames += 1
                summary.worst_ahead_margin_m = max(
                    summary.worst_ahead_margin_m, margin
                )
                for band in AHEAD_BANDS_M:
                    if margin <= band:
                        ahead_band_counts[band] += 1
                        break

            # ── WHICH WRITER OWNED THE COMMAND? ──
            _tx = sample.get("target_x")
            _ty = sample.get("target_y")
            owned = (
                _tx is not None
                and _ty is not None
                and abs(float(_tx) - sample["chase_x"]) <= 0.05
                and abs(float(_ty) - sample["chase_y"]) <= 0.05
            )
            bucket = "chase" if owned else "other"
            chase_split[bucket + "_frames"] += 1
            if margin is not None and margin > 0.0:
                chase_split[bucket + "_ahead_frames"] += 1
                chase_split[bucket + "_worst_ahead_m"] = max(
                    chase_split[bucket + "_worst_ahead_m"], margin
                )

            if _ty is not None:
                _utilisation = y_axis_utilisation(_ty, sample["bc_y"])
                chase_split[bucket + "_y_sum"] += _utilisation
                if _utilisation <= 0.05:
                    chase_split[bucket + "_y_mirror_frames"] += 1

            if _tx is not None and _ty is not None:
                command_distance = _hypot(
                    float(_tx) - sample["bc_x"], float(_ty) - sample["bc_y"]
                )
                chase_split[bucket + "_distance_sum"] += command_distance
                chase_split[bucket + "_distance_max"] = max(
                    chase_split[bucket + "_distance_max"], command_distance
                )
                if (
                    command_distance > HEAVY_COMMAND_M
                    and len(heavy) < HEAVY_KEEP
                ):
                    heavy.append(
                        {
                            "frame": sample["frame"],
                            "distance_m": command_distance,
                            "owner": bucket,
                            "break_kind": sample["break_kind"],
                            "target_x": float(_tx),
                            "target_y": float(_ty),
                            "chase_x": sample["chase_x"],
                            "chase_y": sample["chase_y"],
                            "bc_x": sample["bc_x"],
                            "bc_y": sample["bc_y"],
                            "fb_x": sample["fb_x"],
                            "fb_y": sample["fb_y"],
                            "target_source": sample["target_source"],
                            "state": sample["state"],
                        }
                    )

            target_y = sample.get("target_y")
            if target_y is not None:
                utilisation = y_axis_utilisation(target_y, sample["bc_y"])
                summary.y_axis_utilisation_sum += utilisation
                if utilisation <= 0.05:
                    summary.y_axis_mirror_frames += 1
                tau, _residual, _mirror = best_fit_lead_horizon(
                    target_y, sample["bc_y"], sample["bc_vy"]
                )
                summary.best_fit_tau_sum += tau
                if sample.get("target_x") is not None:
                    summary.absolute_target_error_sum += _hypot(
                        sample["target_x"] - sample["bc_x"],
                        target_y - sample["bc_y"],
                    )

            summary.worst_lateral_margin_m = max(
                summary.worst_lateral_margin_m,
                abs(sample["fb_y"] - sample["bc_y"]),
            )
            source = sample.get("target_source") or "(none)"
            source_counts[source] = source_counts.get(source, 0) + 1

        # ── PER-WINDOW CROSSINGS (body vs carrier) ──
        stats = crossing_stats(samples)
        summary.crossing_x_body_events += stats.x_lane_crossings
        summary.crossing_y_lane_events += stats.y_lane_crossings

    return {
        "summary": summary,
        "kind_counts": kind_counts,
        "ahead_band_counts": ahead_band_counts,
        "source_counts": source_counts,
        "chase_split": chase_split,
        "heavy": heavy,
        "windows": len(rollout["windows"]),
    }


def pct(part, whole) -> float:
    """Percentage (0.0 when the population is empty)."""
    return (100.0 * part / whole) if whole else 0.0


def render_leg(report, rollout, result) -> None:
    """Write one rollout leg's measured section into *report*."""
    summary = result["summary"]
    frames = summary.frames
    report.append("")
    report.append("=" * 78)
    report.append(
        "LEG  mode=%-9s seed=%d  frames=%d  windows=%d"
        % (rollout["mode"], rollout["seed"], rollout["frames"],
           result["windows"])
    )
    report.append("=" * 78)
    if frames == 0:
        report.append("  NO line-break-recovery frames observed.")
        return

    report.append("")
    report.append("-- COMMAND KIND (final target vs live carrier) --")
    for kind in TARGET_KINDS:
        count = result["kind_counts"][kind]
        report.append(
            "  %-18s = %7d  (%.1f%%)" % (kind, count, pct(count, frames))
        )

    report.append("")
    report.append("-- COMMAND AHEAD OF CARRIER (attack axis) --")
    report.append(
        "  target_x > bc.x         = %7d  (%.1f%%)"
        % (summary.target_ahead_frames,
           pct(summary.target_ahead_frames, frames))
    )
    for band in AHEAD_BANDS_M:
        report.append(
            "      of which <= %.1f m   = %7d"
            % (band, result["ahead_band_counts"][band])
        )
    report.append(
        "  worst command margin    = %7.2f m" % summary.worst_ahead_margin_m
    )

    split = result["chase_split"]
    report.append("")
    report.append("-- COMMAND OWNERSHIP (is the frame's target the chase point?) --")
    report.append(
        "  chase point owns frame  = %7d  (%.1f%%)  ahead %d  worst %5.2f m"
        % (split["chase_frames"], pct(split["chase_frames"], frames),
           split["chase_ahead_frames"], split["chase_worst_ahead_m"])
    )
    report.append(
        "  other writer / anchor   = %7d  (%.1f%%)  ahead %d  worst %5.2f m"
        % (split["other_frames"], pct(split["other_frames"], frames),
           split["other_ahead_frames"], split["other_worst_ahead_m"])
    )
    report.append(
        "  chase mean/max distance = %7.3f / %7.2f m"
        % (
            split["chase_distance_sum"] / split["chase_frames"]
            if split["chase_frames"] else 0.0,
            split["chase_distance_max"],
        )
    )
    report.append(
        "  chase-owned |target_y-dy| = %7.3f m  mirror %d (%.1f%%)"
        % (
            split["chase_y_sum"] / split["chase_frames"]
            if split["chase_frames"] else 0.0,
            split["chase_y_mirror_frames"],
            pct(split["chase_y_mirror_frames"], split["chase_frames"]),
        )
    )
    report.append(
        "  other mean/max distance = %7.3f / %7.2f m"
        % (
            split["other_distance_sum"] / split["other_frames"]
            if split["other_frames"] else 0.0,
            split["other_distance_max"],
        )
    )
    for record in result["heavy"]:
        report.append(
            "    HEAVY frame=%s owner=%s kind=%s src=%s state=%s"
            % (record["frame"], record["owner"], record["break_kind"],
               record["target_source"], record["state"])
        )
        report.append(
            "      d=%.2f target=(%.2f,%.2f) chase=(%.2f,%.2f) "
            "bc=(%.2f,%.2f) fb=(%.2f,%.2f)"
            % (record["distance_m"], record["target_x"], record["target_y"],
               record["chase_x"], record["chase_y"], record["bc_x"],
               record["bc_y"], record["fb_x"], record["fb_y"])
        )

    report.append("")
    report.append("-- Y-AXIS UTILISATION --")
    report.append(
        "  mean |target_y - bc.y|  = %7.3f m"
        % summary.mean_y_axis_utilisation_m
    )
    report.append(
        "  live-lane MIRROR frames = %7d  (%.1f%%)"
        % (summary.y_axis_mirror_frames,
           pct(summary.y_axis_mirror_frames, frames))
    )
    report.append(
        "  mean best-fit lead t*   = %7.3f s" % summary.mean_best_fit_lead_s
    )
    report.append(
        "  mean |target - carrier| = %7.3f m"
        % summary.mean_absolute_target_error_m
    )

    report.append("")
    report.append("-- BODY vs CARRIER CROSSINGS --")
    report.append(
        "  X lane crossings (body) = %7d over %d windows"
        % (summary.crossing_x_body_events, result["windows"])
    )
    report.append(
        "  Y lane crossings (body) = %7d over %d windows"
        % (summary.crossing_y_lane_events, result["windows"])
    )
    report.append(
        "  worst lateral margin    = %7.2f m"
        % summary.worst_lateral_margin_m
    )

    report.append("")
    report.append("-- ATTRIBUTION (target_source, frames) --")
    for source, count in sorted(
        result["source_counts"].items(), key=lambda kv: -kv[1]
    )[:12]:
        report.append(
            "  %-24s = %7d  (%.1f%%)" % (source, count, pct(count, frames))
        )
    report.append("")


def render_ab(report, results) -> None:
    """Write the pooled intercept-vs-trail headline."""
    report.append("")
    report.append("=" * 78)
    report.append("A/B HEADLINE  (intercept vs trail, pooled over seeds)")
    report.append("=" * 78)
    for mode in ("intercept", "trail"):
        frames = 0
        ahead = 0
        mirror = 0
        y_sum = 0.0
        x_cross = 0
        y_cross = 0
        worst_ahead = 0.0
        worst_lateral = 0.0
        windows = 0
        chase_frames = 0
        chase_ahead = 0
        other_frames = 0
        other_ahead = 0
        chase_y_sum = 0.0
        chase_y_mirror = 0
        for (leg_mode, _seed), (_rollout, result) in results.items():
            if leg_mode != mode:
                continue
            summary = result["summary"]
            frames += summary.frames
            windows += result["windows"]
            ahead += summary.target_ahead_frames
            mirror += summary.y_axis_mirror_frames
            y_sum += summary.y_axis_utilisation_sum
            x_cross += summary.crossing_x_body_events
            y_cross += summary.crossing_y_lane_events
            worst_ahead = max(worst_ahead, summary.worst_ahead_margin_m)
            worst_lateral = max(
                worst_lateral, summary.worst_lateral_margin_m
            )
            split = result["chase_split"]
            chase_frames += split["chase_frames"]
            chase_ahead += split["chase_ahead_frames"]
            other_frames += split["other_frames"]
            other_ahead += split["other_ahead_frames"]
            chase_y_sum += split["chase_y_sum"]
            chase_y_mirror += split["chase_y_mirror_frames"]
        report.append("")
        report.append("  mode = %s" % mode)
        report.append("    windows                     = %d" % windows)
        report.append("    frames                      = %d" % frames)
        report.append(
            "    command ahead of carrier    = %d (%.1f%%)"
            % (ahead, pct(ahead, frames))
        )
        report.append(
            "    live-lane Y mirror frames   = %d (%.1f%%)"
            % (mirror, pct(mirror, frames))
        )
        report.append(
            "    mean |target_y - bc.y|      = %.3f m"
            % (y_sum / frames if frames else 0.0)
        )
        report.append("    body X lane crossings       = %d" % x_cross)
        report.append("    body Y lane crossings       = %d" % y_cross)
        report.append("    worst command ahead margin  = %.2f m" % worst_ahead)
        report.append("    worst lateral margin        = %.2f m" % worst_lateral)
        report.append(
            "    chase point owns frame      = %d (%.1f%%), of which ahead %d"
            % (chase_frames, pct(chase_frames, frames), chase_ahead)
        )
        report.append(
            "    chase-owned |dy| mean/mirror = %.3f m / %d (%.1f%%)"
            % (
                chase_y_sum / chase_frames if chase_frames else 0.0,
                chase_y_mirror,
                pct(chase_y_mirror, chase_frames),
            )
        )
        report.append(
            "    anchor / other owns frame   = %d (%.1f%%), of which ahead %d"
            % (other_frames, pct(other_frames, frames), other_ahead)
        )


def main() -> int:
    """Run both legs over every seed and write the ledger."""
    report = [
        "EMERGENCY-CHASE TARGET-AXIS LEDGER",
        "(fullback_role chase point vs live carrier)",
        "frames=%d  seeds=%s  min-window=%d  dt=1/60"
        % (MAX_FRAMES, SEEDS, MIN_WINDOW_FRAMES),
    ]

    results = {}
    for seed in SEEDS:
        for mode in ("intercept", "trail"):
            print(
                "  ... rollout mode=%s seed=%d" % (mode, seed), flush=True
            )
            rollout = run_rollout(frames=MAX_FRAMES, seed=seed, mode=mode)
            results[(mode, seed)] = (rollout, summarise(rollout))

    for seed in SEEDS:
        for mode in ("intercept", "trail"):
            rollout, result = results[(mode, seed)]
            render_leg(report, rollout, result)

    render_ab(report, results)

    text = "\n".join(report)
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
    print(text)
    print("\nReport written to %s" % REPORT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
