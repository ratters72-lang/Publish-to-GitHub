"""probe_stage35_transition_attribution.py — Stage 3.5
Single-Defender Transition Attribution (retreat -> PTB).

PURPOSE
-------
Stage 3 established (project_info__63.md) that the Width Contract, the Count
Contract and the Population Contract are constitutionally incompatible: the
width share is a CONSTANT (0.65/1.65 = 0.3939) while the population share is a
ROOM-LINEAR ramp (0.10 -> 0.50), and the side-assignment boundary is offset
(W_open - W_short)/8 past the ruck into the open side.

The visible symptom is: "retreat formation looks wrong; one or more defenders
laterally shift during the retreat -> PTB transition; the line suddenly looks
correct."

This probe does NOT revisit Stage 3.  It answers ONE question:

    is the visible lateral "pull-over" primarily
        (A) the Width-Population incompatibility itself, becoming visible,
        (B) a transition OWNERSHIP bug (a writer re-tasking a body at the
            handover), or
        (C) both?

METHOD
------
1. Boot the real engine headless (same bootstrap as the other probes).
2. Wrap EVERY named writer at its IMPORT SITE (not in its source module) with
   a shadow-diff recorder: snapshot the tracked fields of every line body
   before the call, diff after, emit one event per real change.  Because the
   snapshots are taken immediately before each call, NESTED writers attribute
   to the INNERMOST writer that actually moved the value.
3. Run the sequence, recording a per-frame snapshot of every line body plus
   the count / width / lane state.
4. Detect the FIRST defender that "pulls across": the first recovery-window
   frame in which a line body either changes ``depth_side`` or moves
   laterally by >= PULL_THRESHOLD_M.  Ties break on the largest |dy|.
5. Emit the T-5 .. T+5 ledger and attribute every change of
   depth_side / depth_role / target_y to its first writer.
6. Compute the density impact (Width Share, Population Share, Density Share,
   Misalignment Index) at T-1 and T+1.

WHAT THIS PROBE DOES NOT DO
---------------------------
No production file is modified.  No fix is proposed.  ``0.65`` is not changed.
The count allocation is not changed.  Side assignment is not changed.  The
probe is read-only against the engine and restores every patched symbol in a
``finally`` block.

OUTPUTS
-------
  - stdout report
  - stage35_transition.csv         (T-5..T+5 ledger)
  - stage35_writer_events.csv      (every attributed writer event in the window)

Run:
  python probe_stage35_transition_attribution.py
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

import game.defence.defence_lvl_1.defensive_depth_roles as dr
import game.defence.defence_lvl_1.defensive_pipeline as dp
import game.defence.defence_lvl_1.defensive_split_spacing as dss
import game.defence.defence_lvl_1.defensive_width as dw
from game.defence.defence_lvl_1.defensive_tuners import post_tes_settle_active

# ─────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────

MAX_FRAMES = 3600
FPS = 60.0
DT = 1.0 / FPS

# Line bodies the depth-role / spacing chain owns.
LINE_ROLES = ("DEF_LINE_READY", "DEF_CHASE", "DEF_RETREAT_10")

# The recovery span: TES hand-over through the first OPEN_PLAY frame.  The
# "retreat -> PTB transition" lives inside this span.
RETREAT_PHASES = ("RELEASE", "TACKLE_RELEASE", "PTB_READY")
PTB_PHASES = ("PTB_PREP", "PTB_EXEC", "PTB")
POST_RUCK_PHASES = ("POST_PTB_RESET", "POST_RUCK_OPEN_PLAY")
RECOVERY_PHASES = RETREAT_PHASES + PTB_PHASES + POST_RUCK_PHASES

# A lateral move at or above this is a visible "pull across".
PULL_THRESHOLD_M = 0.5

# Fields the attribution shadow tracks.
TRACK_FIELDS = (
    "depth_side",
    "depth_role",
    "target_x",
    "target_y",
    "x",
    "y",
    "frame_spacing_y",
    "role_depth_target_y",
)
# Fields whose mutation is a genuine WRITER act (geometry ownership).
OWNERSHIP_FIELDS = ("depth_side", "depth_role", "target_x", "target_y")
# Fields that only move because the integrator converged toward a target.
INTEGRATION_FIELDS = ("x", "y")

LEFT = dss.LEFT
RIGHT = dss.RIGHT


def _cls(v):
    """JSON-safe scalar."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


def _side_name(side):
    if side == LEFT:
        return "LEFT"
    if side == RIGHT:
        return "RIGHT"
    return "-"


def _role_name(p):
    role = getattr(p, "defender_role", None)
    return getattr(role, "name", None) or (str(role) if role is not None else None)


def _phase_label(ope, world):
    label = getattr(world, "_ope_phase_label", None)
    if label:
        return str(label)
    return str(getattr(ope, "_phase", "") or "")


def _sub_label(world):
    sub = getattr(world, "sub_state", None)
    return str(getattr(sub, "value", sub))


# ─────────────────────────────────────────────────────────────────────
# WRITER ATTRIBUTION — shadow-diff recorder
# ─────────────────────────────────────────────────────────────────────
#
# One shared ledger.  ``_shadow`` holds the last observed value of every
# tracked field for every player id.  A wrapper snapshots live values into a
# LOCAL dict immediately before calling the real function, so a nested call
# sees the inner call's writes and the outer call reports "no change" for
# fields the inner already moved — which attributes a change to the
# INNERMOST writer that produced it.

_shadow = {}          # pid -> {field: value}
_events = []          # list of event dicts
_installed = []       # [(module, attr, original)]
_frame_ctx = {"frame": 0, "phase": "", "sub": ""}

# Live engine references, set by install_wrappers().  Used to stamp each
# event with the phase / sub-state / frame current when the writer RAN
# (not the value at the end of the frame).
_OPE = None
_WORLD = None


def _refresh_ctx():
    """Stamp the frame context from the live engine objects."""
    if _WORLD is not None:
        _frame_ctx["frame"] = int(
            getattr(_WORLD, "frame_count", _frame_ctx["frame"]) or 0
        )
        _frame_ctx["sub"] = _sub_label(_WORLD)
    if _OPE is not None:
        _frame_ctx["phase"] = str(getattr(_OPE, "_phase", "") or "")


def _snapshot(players):
    snap = {}
    for p in players:
        pid = getattr(p, "player_id", None)
        if pid is None:
            continue
        snap[pid] = {f: getattr(p, f, None) for f in TRACK_FIELDS}
    return snap


def _emit(frame, phase, sub, writer, pid, field, old, new, role, side):
    _events.append({
        "frame": int(frame),
        "phase": phase,
        "sub": sub,
        "writer": writer,
        "player_id": pid,
        "field": field,
        "old": _cls(old),
        "new": _cls(new),
        "depth_role": role,
        "depth_side": side,
    })


def _diff_against(before, players, writer):
    """Emit an event for every tracked field *writer* actually changed."""
    frame = _frame_ctx["frame"]
    phase = _frame_ctx["phase"]
    sub = _frame_ctx["sub"]
    for p in players:
        pid = getattr(p, "player_id", None)
        if pid is None:
            continue
        b = before.get(pid)
        if b is None:
            continue
        for f in TRACK_FIELDS:
            old = b.get(f)
            new = getattr(p, f, None)
            if old == new:
                continue
            _emit(frame, phase, sub, writer, pid, f, old, new,
                  getattr(p, "depth_role", None),
                  getattr(p, "depth_side", None))


def _wrap(module, attr, writer_name, players_getter):
    """Wrap ``module.attr`` with a shadow-diff recorder.

    ``players_getter()`` returns the list of players to watch.  The wrapper
    records ONLY real field changes and then calls the original.
    """
    original = getattr(module, attr, None)
    if original is None or not callable(original):
        return
    if getattr(original, "_stage35_wrapped", False):
        return

    def wrapper(*args, **kwargs):
        _refresh_ctx()
        players = players_getter()
        before = _snapshot(players)
        try:
            result = original(*args, **kwargs)
        finally:
            _diff_against(before, players, writer_name)
        return result

    wrapper._stage35_wrapped = True
    wrapper._stage35_original = original
    setattr(module, attr, wrapper)
    _installed.append((module, attr, original))


def unwrap_all():
    """Restore every patched symbol (finally-block safety)."""
    for module, attr, original in reversed(_installed):
        try:
            setattr(module, attr, original)
        except Exception:
            pass
    _installed.clear()
# ─────────────────────────────────────────────────────────────────────
# WORLD READERS
# ─────────────────────────────────────────────────────────────────────


def boot_world():
    """Boot the real engine headless (mirrors the other probes)."""
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


def line_bodies(world):
    """Line bodies the depth-role / spacing chain owns."""
    bc = getattr(world, "ball_carrier", None)
    out = []
    for d in (getattr(world, "defending_team", None) or []):
        if d is bc:
            continue
        if getattr(d, "fullback_role_active", False):
            continue
        if _role_name(d) in LINE_ROLES:
            out.append(d)
    return out


def all_defenders(world):
    """Every defending-team player (the watch set for the wrappers)."""
    return list(getattr(world, "defending_team", None) or [])


def _tuners(ope):
    return getattr(ope, "def_tuners", None)


def count_split(world):
    """(n_left, n_right, ruck_y) from the count authority's own stamp."""
    prev = getattr(world, "_last_count_split", None)
    if prev is None:
        return None, None, None
    try:
        n_left, n_right, ruck_y = prev
        return int(n_left), int(n_right), float(ruck_y)
    except (TypeError, ValueError):
        return None, None, None


def widths(world, tuners):
    """(left_w, right_w, short_side) from Contract W."""
    ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    try:
        left_w, right_w = dss.side_widths(world, tuners, ruck_y=ruck_y)
    except Exception:
        return None, None, None
    try:
        short_side = dss.resolve_short_side(world, tuners, ruck_y=ruck_y)
    except Exception:
        short_side = None
    return float(left_w), float(right_w), short_side


def governed_width(world, tuners):
    """The governed span G (3-point width interpolation by ball X)."""
    try:
        return float(dw.governed_width_m(world, tuners))
    except Exception:
        return None


def lane_index(world, d):
    """Index of *d*'s assigned lane within its side's published ladder."""
    ladders = getattr(world, "frame_side_lanes", None)
    if not isinstance(ladders, dict):
        return None
    side = getattr(d, "depth_side", None)
    if side not in (LEFT, RIGHT):
        return None
    lanes = ladders.get(int(side))
    if not lanes:
        return None
    ty = getattr(d, "target_y", None)
    if ty is None:
        return None
    best_i, best_dy = None, None
    for i, y in enumerate(lanes):
        dy = abs(float(y) - float(ty))
        if best_dy is None or dy < best_dy:
            best_i, best_dy = i, dy
    return best_i


def _settle(world):
    try:
        return bool(post_tes_settle_active(world))
    except Exception:
        return None


def _frames_fresh(world):
    return (
        getattr(world, "frame_geometry_frame", None)
        == getattr(world, "frame_count", None)
    )


def frame_record(world, ope):
    """One per-frame snapshot of the state + every line body."""
    tuners = _tuners(ope)
    ruck_x = float(getattr(world, "ruck_x_m", 0.0) or 0.0)
    ruck_y = float(getattr(world, "ruck_y_m", 0.0) or 0.0)
    n_left, n_right, split_ruck = count_split(world)
    if tuners is not None:
        left_w, right_w, short_side = widths(world, tuners)
        G = governed_width(world, tuners)
    else:
        left_w = right_w = short_side = G = None
    if short_side == LEFT:
        short_w, open_w = left_w, right_w
        n_short, n_open = n_left, n_right
    elif short_side == RIGHT:
        short_w, open_w = right_w, left_w
        n_short, n_open = n_right, n_left
    else:
        short_w = open_w = n_short = n_open = None

    bodies = []
    for d in line_bodies(world):
        bodies.append({
            "player_id": getattr(d, "player_id", None),
            "role": _role_name(d),
            "depth_role": getattr(d, "depth_role", None),
            "depth_side": getattr(d, "depth_side", None),
            "y": float(getattr(d, "y", 0.0) or 0.0),
            "x": float(getattr(d, "x", 0.0) or 0.0),
            "target_y": _cls(getattr(d, "target_y", None)),
            "target_x": _cls(getattr(d, "target_x", None)),
            "frame_spacing_y": _cls(getattr(d, "frame_spacing_y", None)),
            "role_depth_target_y": _cls(getattr(d, "role_depth_target_y", None)),
            "target_source": getattr(d, "target_source", None),
            "target_writer": getattr(d, "_target_writer", None),
            "frozen": bool(getattr(d, "frozen", False)),
            "depth_locked": bool(getattr(d, "depth_locked", False)),
            "arrival_mode": bool(getattr(d, "arrival_mode", False)),
            "movement_state": str(getattr(d, "movement_state", None)),
            "lane_index": lane_index(world, d),
        })

    return {
        "frame": int(getattr(world, "frame_count", 0)),
        "phase": _phase_label(ope, world),
        "sub": _sub_label(world),
        "ruck_x": ruck_x,
        "ruck_y": ruck_y,
        "split_ruck": split_ruck,
        "n_left": n_left,
        "n_right": n_right,
        "n_short": n_short,
        "n_open": n_open,
        "short_side": short_side,
        "left_w": left_w,
        "right_w": right_w,
        "short_w": short_w,
        "open_w": open_w,
        "governed_w": G,
        "settle": _settle(world),
        "geometry_fresh": _frames_fresh(world),
        "bc_x": _cls(getattr(getattr(world, "ball_carrier", None), "x", None)),
        "bc_y": _cls(getattr(getattr(world, "ball_carrier", None), "y", None)),
        "bodies": bodies,
    }


# ─────────────────────────────────────────────────────────────────────
# DENSITY / SHARE METRICS  (Stage 3 reconciliation table)
# ─────────────────────────────────────────────────────────────────────


def density_metrics(rec):
    """Width Share, Population Share, Density Share, Misalignment Index.

    Computed for the SHORT side and the OPEN side of one frame record.
    ``Misalignment Index`` = Population Share / Width Share (Stage 3's ``M``).
    """
    out = {
        "w_share_short": None, "w_share_open": None,
        "p_share_short": None, "p_share_open": None,
        "d_share_short": None, "d_share_open": None,
        "mis_short": None, "mis_open": None,
        "dens_short": None, "dens_open": None,
        "n_total": None, "span_total": None,
    }
    short_w, open_w = rec.get("short_w"), rec.get("open_w")
    n_short, n_open = rec.get("n_short"), rec.get("n_open")
    if short_w is None or open_w is None:
        return out
    if n_short is None or n_open is None:
        return out
    span = float(short_w) + float(open_w)
    n_tot = int(n_short) + int(n_open)
    if span <= 0.0 or n_tot <= 0:
        return out
    out["n_total"] = n_tot
    out["span_total"] = span
    out["w_share_short"] = float(short_w) / span
    out["w_share_open"] = float(open_w) / span
    out["p_share_short"] = float(n_short) / n_tot
    out["p_share_open"] = float(n_open) / n_tot
    ds = float(n_short) / float(short_w) if float(short_w) > 0 else 0.0
    do = float(n_open) / float(open_w) if float(open_w) > 0 else 0.0
    dtot = ds + do
    out["dens_short"] = ds
    out["dens_open"] = do
    if dtot > 0:
        out["d_share_short"] = ds / dtot
        out["d_share_open"] = do / dtot
    if out["w_share_short"] > 0:
        out["mis_short"] = out["p_share_short"] / out["w_share_short"]
    if out["w_share_open"] > 0:
        out["mis_open"] = out["p_share_open"] / out["w_share_open"]
    return out
# ─────────────────────────────────────────────────────────────────────
# WRAPPER INSTALLATION — patch at the IMPORT SITE, never the source module
# ─────────────────────────────────────────────────────────────────────
#
# ``assign_depth_roles`` binds ``assign_split_sides`` into ITS OWN namespace
# (``from ... import assign_split_sides``), so patching the source module
# would miss the call.  Same for ``compute_targets`` in the pipeline module,
# and for ``run_defensive_pipeline`` / ``enforce_ruck_adjacent_pin`` in the
# OpenPlayEngine namespace.

OPE_METHOD_WRITERS = (
    "_update_release",
    "_enter_ptb_prep",
    "_update_ptb_prep",
    "_update_ptb_exec",
    "_update_ptb",
    "_update_post_ruck_open_play",
    "_update_defence",
    "_apply_defence_sdm",
)


def install_wrappers(world, ope):
    """Wrap every named writer at its import site."""
    global _OPE, _WORLD
    _OPE = ope
    _WORLD = world

    def getter():
        return all_defenders(_WORLD)

    # ── Contract P / C — side ownership ──
    _wrap(dr, "assign_split_sides", "assign_split_sides", getter)
    _wrap(dr, "decide_side_counts", "decide_side_counts", getter)
    _wrap(dss, "assign_split_sides", "assign_split_sides(src)", getter)

    # ── depth-role ownership ──
    _wrap(dr, "_bootstrap_side_roles", "_bootstrap_side_roles", getter)
    _wrap(dr, "_post_ruck_recovery_seed", "_post_ruck_recovery_seed", getter)
    _wrap(dr, "_maintain_side", "_maintain_side", getter)
    _wrap(dr, "_post_ruck_marker_cleanup", "_post_ruck_marker_cleanup", getter)
    _wrap(dr, "assign_depth_roles", "assign_depth_roles", getter)

    # ── spacing (published Y) ──
    _wrap(dp, "compute_targets", "compute_targets", getter)
    _wrap(dp, "apply_frame_spacing", "apply_frame_spacing", getter)

    # ── the per-frame preamble and the LAST writer of the frame ──
    _wrap(ope_mod, "run_defensive_pipeline", "run_defensive_pipeline", getter)
    _wrap(ope_mod, "enforce_ruck_adjacent_pin", "enforce_ruck_adjacent_pin",
          getter)
    _wrap(ope_mod, "assign_depth_roles", "assign_depth_roles(ope)", getter)

    # ── OPE phase handlers (retreat / PTB transition writers) ──
    cls = type(ope)
    for name in OPE_METHOD_WRITERS:
        _wrap(cls, name, "OPE." + name, getter)


# ─────────────────────────────────────────────────────────────────────
# SEQUENCE RUNNER
# ─────────────────────────────────────────────────────────────────────


def run_sequence(with_wrappers=True):
    """Boot, run headless, record one frame record per OPEN_PLAY frame."""
    world, ball, ope, kickoff_engine, collision_system = boot_world()
    if with_wrappers:
        install_wrappers(world, ope)

    frames = []
    phase_counts = {}
    wrapped_log = io.StringIO()
    try:
        with contextlib.redirect_stdout(wrapped_log):
            for _ in range(MAX_FRAMES):
                _frame_ctx["frame"] = int(getattr(world, "frame_count", 0))
                _frame_ctx["phase"] = str(getattr(ope, "_phase", "") or "")
                _frame_ctx["sub"] = _sub_label(world)
                ball.update(DT)
                if world.phase == GamePhase.KICKOFF:
                    kickoff_engine.update(DT)
                    check_for_catch(world, ball, DT)
                    continue
                if world.phase == GamePhase.CATCH:
                    if world.ball_carrier is not None:
                        ope.start(world.ball_carrier)
                    world.phase = GamePhase.OPEN_PLAY
                    continue
                if world.phase != GamePhase.OPEN_PLAY:
                    continue
                ope.update(DT)
                if not getattr(world, "tes_active", False):
                    collision_system.resolve_all(world.players, world=world)
                rec = frame_record(world, ope)
                frames.append(rec)
                ph = rec["phase"]
                phase_counts[ph] = phase_counts.get(ph, 0) + 1
    finally:
        unwrap_all()

    return {
        "world": world,
        "ope": ope,
        "frames": frames,
        "phase_counts": phase_counts,
        "engine_log": wrapped_log.getvalue(),
    }


# ─────────────────────────────────────────────────────────────────────
# PULL DETECTION
# ─────────────────────────────────────────────────────────────────────


def detect_pull(frames):
    """Locate T = the first recovery-window frame with a lateral pull-over.

    A frame qualifies when a line body either changes ``depth_side`` or moves
    laterally by >= ``PULL_THRESHOLD_M`` since the previous recovery frame.
    The FIRST qualifying frame wins; within it the largest |dy| body is the
    offending defender.  Returns None when no recovery span was observed.
    """
    candidates = []
    prev = None
    for rec in frames:
        if prev is not None:
            if (prev["phase"] in RECOVERY_PHASES
                    and rec["phase"] in RECOVERY_PHASES):
                pmap = {b["player_id"]: b for b in prev["bodies"]}
                for b in rec["bodies"]:
                    pb = pmap.get(b["player_id"])
                    if pb is None:
                        continue
                    dy = float(b["y"]) - float(pb["y"])
                    side_old = pb["depth_side"]
                    side_new = b["depth_side"]
                    side_flip = (
                        side_old in (LEFT, RIGHT)
                        and side_new in (LEFT, RIGHT)
                        and side_old != side_new
                    )
                    role_flip = pb["depth_role"] != b["depth_role"]
                    if side_flip or abs(dy) >= PULL_THRESHOLD_M:
                        candidates.append({
                            "frame": rec["frame"],
                            "prev_frame": prev["frame"],
                            "player_id": b["player_id"],
                            "role": b["role"],
                            "depth_role_old": pb["depth_role"],
                            "depth_role_new": b["depth_role"],
                            "depth_side_old": side_old,
                            "depth_side_new": side_new,
                            "y_old": float(pb["y"]),
                            "y_new": float(b["y"]),
                            "dy": dy,
                            "side_flip": side_flip,
                            "role_flip": role_flip,
                            "phase": rec["phase"],
                            "sub": rec["sub"],
                            "n_short": rec["n_short"],
                            "n_open": rec["n_open"],
                            "prev_n_short": prev["n_short"],
                            "prev_n_open": prev["n_open"],
                        })
        prev = rec

    if not candidates:
        return None, None, []

    # FIRST pull-over (earliest qualifying frame).
    first_frame = min(c["frame"] for c in candidates)
    at_frame = [c for c in candidates if c["frame"] == first_frame]
    first = max(at_frame, key=lambda c: abs(c["dy"]))

    # LARGEST pull-over (the visibly offending body).  A side-LABEL flip
    # with no realised movement is not the on-field symptom; the realised
    # lateral step is.  Rank on |dy| so the deep-dive attributes the body
    # the user can actually see move.
    largest = max(candidates, key=lambda c: abs(c["dy"]))
    return largest, first, candidates


def phase_boundaries(frames):
    """Return [(frame, from_phase, to_phase)] for every phase change."""
    out = []
    prev = None
    for rec in frames:
        if prev is not None and rec["phase"] != prev["phase"]:
            out.append((rec["frame"], prev["phase"], rec["phase"]))
        prev = rec
    return out


def boundary_named(boundaries, lo_phases, hi_phases):
    """First boundary that leaves *lo_phases* for *hi_phases*."""
    for frame, a, b in boundaries:
        if a in lo_phases and b in hi_phases:
            return frame, a, b
    return None, None, None


def frames_in_window(frames, t_frame, half=5):
    """Return the T-half .. T+half slice (clamped to what was recorded)."""
    lo = t_frame - half
    hi = t_frame + half
    return [r for r in frames if lo <= r["frame"] <= hi]


def find_frame(frames, frame_no):
    for r in frames:
        if r["frame"] == frame_no:
            return r
    return None
# ─────────────────────────────────────────────────────────────────────
# ATTRIBUTION
# ─────────────────────────────────────────────────────────────────────

CONTRACT_WRITERS = frozenset({
    "assign_split_sides",
    "assign_split_sides(src)",
    "decide_side_counts",
})
ROLE_REBUILD_WRITERS = frozenset({
    "_bootstrap_side_roles",
    "_post_ruck_recovery_seed",
    "_maintain_side",
    "assign_depth_roles",
    "assign_depth_roles(ope)",
})
SPACING_WRITERS = frozenset({
    "compute_targets",
    "apply_frame_spacing",
})
PIN_WRITERS = frozenset({
    "enforce_ruck_adjacent_pin",
})
TRANSITION_WRITERS = frozenset({
    "OPE._update_release",
    "OPE._enter_ptb_prep",
    "OPE._update_ptb_prep",
    "OPE._update_ptb_exec",
    "OPE._update_ptb",
    "OPE._update_post_ruck_open_play",
    "OPE._update_defence",
    "OPE._apply_defence_sdm",
})

LATERAL_FIELDS = ("target_y", "y")
OWNERSHIP_TRACK = ("depth_side", "depth_role", "target_x", "target_y")


def _delta(e):
    old, new = e.get("old"), e.get("new")
    if old is None or new is None:
        return 0.0
    try:
        return abs(float(new) - float(old))
    except (TypeError, ValueError):
        return 0.0


def boundary_y_star(rec):
    """Contract P's indifference boundary: y* = ruck_y + (W_open-W_short)/8."""
    ruck_y = rec.get("ruck_y")
    short_w, open_w = rec.get("short_w"), rec.get("open_w")
    if ruck_y is None or short_w is None or open_w is None:
        return None
    return float(ruck_y) + (float(open_w) - float(short_w)) / 8.0


def attr_bucket(writer):
    if writer in CONTRACT_WRITERS:
        return "contract"
    if writer in ROLE_REBUILD_WRITERS:
        return "role_rebuild"
    if writer in PIN_WRITERS:
        return "ruck_pin"
    if writer in SPACING_WRITERS:
        return "spacing"
    if writer in TRANSITION_WRITERS:
        return "transition_writer"
    return "other"


def attribution(events, frames, t_frame, offender_pid, half=5):
    """Build the attribution payload for the T-half .. T+half window."""
    lo, hi = t_frame - half, t_frame + half
    win = [e for e in events if lo <= e["frame"] <= hi]
    off = [e for e in win if e["player_id"] == offender_pid]

    # Per-writer structural contribution (targets) and realised (positions).
    def _sum(evts, fields):
        return sum(_delta(e) for e in evts if e["field"] in fields)

    by_writer = {}
    for e in off:
        w = e["writer"]
        rec = by_writer.setdefault(w, {
            "writer": w, "bucket": attr_bucket(w),
            "d_target_y": 0.0, "d_y": 0.0, "d_target_x": 0.0, "d_x": 0.0,
            "side_changes": 0, "role_changes": 0,
            "fields": set(), "frames": [],
        })
        rec["fields"].add(e["field"])
        if e["frame"] not in rec["frames"]:
            rec["frames"].append(e["frame"])
        d = _delta(e)
        if e["field"] == "target_y":
            rec["d_target_y"] += d
        elif e["field"] == "y":
            rec["d_y"] += d
        elif e["field"] == "target_x":
            rec["d_target_x"] += d
        elif e["field"] == "x":
            rec["d_x"] += d
        elif e["field"] == "depth_side":
            rec["side_changes"] += 1
        elif e["field"] == "depth_role":
            rec["role_changes"] += 1

    for rec in by_writer.values():
        rec["fields"] = sorted(rec["fields"])
        rec["frames"] = sorted(rec["frames"])

    # Ownership events: side / role changes anywhere in the window.
    side_events = [e for e in win if e["field"] == "depth_side"]
    role_events = [e for e in win if e["field"] == "depth_role"]

    # Bucket totals for the offending defender.
    bucket_totals = {}
    for rec in by_writer.values():
        bucket_totals[rec["bucket"]] = (
            bucket_totals.get(rec["bucket"], 0.0)
            + rec["d_target_y"] + rec["d_target_x"]
        )

    return {
        "window_lo": lo,
        "window_hi": hi,
        "all_events": win,
        "offender_events": off,
        "by_writer": sorted(
            by_writer.values(),
            key=lambda r: -(r["d_target_y"] + r["d_y"]),
        ),
        "side_events": side_events,
        "role_events": role_events,
        "bucket_totals": bucket_totals,
    }


def classify(attr, offender, recs, counts_changed):
    """CONTRACT INCOMPATIBILITY / TRANSITION OWNERSHIP BUG / MIXED CAUSE.

    The verdict is decided by the DOMINANT contributor to the offending
    defender's lateral geometry inside the window:

      - depth_side reassigned by ``assign_split_sides`` (Contract P) or the
        count re-solved (Contract C)  -> the constitutional incompatibility
        becoming visible.
      - depth_role rebuilt by ``_bootstrap_side_roles`` / re-laned, with the
        side intact                     -> a transition ownership act (the
        role rebuild EXPOSES the incompatibility).
      - a transition writer moving ``target_y`` with role and side intact
                                       -> a transition ownership act.
    """
    buckets = attr["bucket_totals"]
    contract = buckets.get("contract", 0.0)
    role_rebuild = buckets.get("role_rebuild", 0.0)
    pin = buckets.get("ruck_pin", 0.0)
    spacing = buckets.get("spacing", 0.0)
    transition = buckets.get("transition_writer", 0.0)

    side_flipped = bool(attr["side_events"])
    role_changed = bool(attr["role_events"])

    total = contract + role_rebuild + pin + spacing + transition
    pct = {
        "contract": (contract / total * 100.0) if total > 0 else 0.0,
        "role_rebuild": (role_rebuild / total * 100.0) if total > 0 else 0.0,
        "ruck_pin": (pin / total * 100.0) if total > 0 else 0.0,
        "spacing": (spacing / total * 100.0) if total > 0 else 0.0,
        "transition_writer": (transition / total * 100.0) if total > 0 else 0.0,
    }

    if not attr["offender_events"]:
        return "NO WRITER OBSERVED", pct, {
            "side_flipped": side_flipped,
            "role_changed": role_changed,
            "counts_changed": counts_changed,
        }

    # ── OWNERSHIP-FIRST VERDICT (2026-09-13) ──
    # The ROOT CAUSE is the ownership field (depth_side / depth_role) that
    # changed; the movement writers only execute it.  A side flip by the
    # contract executor IS the incompatibility becoming visible, so metres
    # of unrelated retreat re-targeting must not outvote a single
    # reassignment act.
    side_by_contract = [e for e in attr["side_events"]
                        if e["writer"] in CONTRACT_WRITERS]
    side_by_other = [e for e in attr["side_events"]
                     if e["writer"] not in CONTRACT_WRITERS]
    role_by_rebuild = [e for e in attr["role_events"]
                       if e["writer"] in ROLE_REBUILD_WRITERS]

    if side_by_contract:
        root = "contract_c" if counts_changed else "contract_p"
        detail = (
            "Contract C - decide_side_counts re-solved the split on the "
            "transition frame, then assign_split_sides reassigned depth_side"
            if counts_changed else
            "Contract P - assign_split_sides band-centre boundary reassigned "
            "depth_side with the count unchanged"
        )
    elif side_by_other:
        root = "side_other"
        detail = ("depth_side changed by a non-contract writer: "
                  + str(side_by_other[0]["writer"]))
    elif role_by_rebuild or attr["role_events"]:
        root = "role_rebuild"
        detail = "depth-role re-lane at the handover"
    elif pin > 0.0:
        root = "ruck_pin"
        detail = ("ruck-adjacent pin owned the lateral target "
                  "(no ownership change)")
    elif transition > 0.0:
        root = "transition_writer"
        detail = ("retreat/PTB transition writer owned the lateral target "
                  "(no ownership change)")
    else:
        root = "none"
        detail = "no ownership change and no lateral writer observed"

    if root in ("contract_p", "contract_c"):
        verdict = ("MIXED CAUSE" if (role_by_rebuild or side_by_other)
                   else "CONTRACT INCOMPATIBILITY")
    elif root in ("role_rebuild", "side_other", "ruck_pin",
                  "transition_writer"):
        verdict = "TRANSITION OWNERSHIP BUG"
    else:
        verdict = "NO WRITER OBSERVED"

    return verdict, pct, {
        "side_flipped": side_flipped,
        "role_changed": role_changed,
        "counts_changed": counts_changed,
        "root": root,
        "detail": detail,
        "metres": {"contract": contract, "role_rebuild": role_rebuild,
                   "ruck_pin": pin, "spacing": spacing,
                   "transition_writer": transition},
    }


# ─────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────


def _fmt(v, nd=3):
    if v is None:
        return "-"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def write_ledger_csv(path, ledger, offender_pid):
    """T-5..T+5 per-frame ledger (one row per frame per offending body)."""
    import csv
    cols = [
        "frame", "phase", "sub", "depth_side", "depth_role",
        "target_y", "current_y", "dy_frame",
        "n_short", "n_open",
        "short_side_width", "open_side_width", "governed_w",
        "short_side_ids", "open_side_ids",
        "assigned_lane_index",
        "frame_spacing_y", "role_depth_target_y",
        "target_source", "target_writer",
        "frozen", "depth_locked", "settle", "geometry_fresh",
        "y_star",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols)
        wr.writeheader()
        prev_y = None
        for rec in ledger:
            body = None
            for b in rec["bodies"]:
                if b["player_id"] == offender_pid:
                    body = b
                    break
            if body is None:
                continue
            y = float(body["y"])
            dy = None if prev_y is None else y - prev_y
            prev_y = y
            short_ids = [
                b["player_id"] for b in rec["bodies"]
                if b["depth_side"] == rec["short_side"]
            ]
            open_ids = [
                b["player_id"] for b in rec["bodies"]
                if b["depth_side"] in (LEFT, RIGHT)
                and b["depth_side"] != rec["short_side"]
            ]
            wr.writerow({
                "frame": rec["frame"],
                "phase": rec["phase"],
                "sub": rec["sub"],
                "depth_side": _side_name(body["depth_side"]),
                "depth_role": body["depth_role"],
                "target_y": _fmt(body["target_y"]),
                "current_y": _fmt(y),
                "dy_frame": _fmt(dy),
                "n_short": rec["n_short"],
                "n_open": rec["n_open"],
                "short_side_width": _fmt(rec["short_w"]),
                "open_side_width": _fmt(rec["open_w"]),
                "governed_w": _fmt(rec["governed_w"]),
                "short_side_ids": "|".join(str(i) for i in short_ids),
                "open_side_ids": "|".join(str(i) for i in open_ids),
                "assigned_lane_index": body["lane_index"],
                "frame_spacing_y": _fmt(body["frame_spacing_y"]),
                "role_depth_target_y": _fmt(body["role_depth_target_y"]),
                "target_source": body["target_source"],
                "target_writer": body["target_writer"],
                "frozen": body["frozen"],
                "depth_locked": body["depth_locked"],
                "settle": rec["settle"],
                "geometry_fresh": rec["geometry_fresh"],
                "y_star": _fmt(boundary_y_star(rec)),
            })


def write_events_csv(path, events):
    import csv
    cols = ["frame", "phase", "sub", "writer", "bucket", "player_id",
            "field", "old", "new", "delta", "depth_role", "depth_side"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols)
        wr.writeheader()
        for e in events:
            wr.writerow({
                "frame": e["frame"],
                "phase": e["phase"],
                "sub": e["sub"],
                "writer": e["writer"],
                "bucket": attr_bucket(e["writer"]),
                "player_id": e["player_id"],
                "field": e["field"],
                "old": e["old"],
                "new": e["new"],
                "delta": round(_delta(e), 4),
                "depth_role": e["depth_role"],
                "depth_side": _side_name(e["depth_side"]),
            })


def print_report(result, offender, first_pull, candidates, attr, verdict,
                 pct, flags, events, glides, half=5):
    world = result["world"]
    frames = result["frames"]
    print("=" * 78)
    print("STAGE 3.5 — SINGLE-DEFENDER TRANSITION ATTRIBUTION (retreat -> PTB)")
    print("=" * 78)
    print(f"frames recorded          = {len(frames)}")
    print(f"frame_count at end       = {getattr(world, 'frame_count', None)}")
    print("phase counts:")
    for k, v in sorted(result["phase_counts"].items(), key=lambda kv: -kv[1]):
        print(f"    {k:<24} {v}")

    print_transition_map(events, frames, glides)
    print_glide_dive(frames, events, glides[0] if glides else None)

    if offender is None:
        print("\nRESULT: NO recovery-window lateral pull-over detected.")
        print("        (no RELEASE/PTB/POST_RUCK span with a >= "
              f"{PULL_THRESHOLD_M} m lateral step or a depth_side flip)")
        print("        -> no attribution possible this run.")
        return

    t_frame = offender["frame"]
    lo, hi = t_frame - half, t_frame + half
    bnd = phase_boundaries(frames)
    rel = boundary_named(bnd, RETREAT_PHASES, PTB_PHASES)
    rb = boundary_named(bnd, ("RELEASE",) + PTB_PHASES, POST_RUCK_PHASES)
    print(f"\ncandidate pull-overs      = {len(candidates)}")
    print(f"RELEASE->PTB boundary     = {rel[0]} ({rel[1]} -> {rel[2]})"
          if rel[0] else "RELEASE->PTB boundary     = (not observed)")
    print(f"PTB->POST_RUCK boundary   = {rb[0]} ({rb[1]} -> {rb[2]})"
          if rb[0] else "PTB->POST_RUCK boundary   = (not observed)")
    print("DEEP-DIVE TARGET          = the LARGEST realised pull-over")
    print(f"  T (largest pull frame)  = {t_frame}")
    print(f"  window                  = {lo} .. {hi}")
    print(f"  dy at T                 = {_fmt(offender['dy'])} m")
    if first_pull is not None and first_pull["frame"] != t_frame:
        print("FIRST qualifying pull-over (reference):")
        print(f"  frame={first_pull['frame']} pid={first_pull['player_id']} "
              f"dy={_fmt(first_pull['dy'])} "
              f"side={_side_name(first_pull['depth_side_old'])}->"
              f"{_side_name(first_pull['depth_side_new'])}")

    print("\n" + "-" * 78)
    print("1. WHO MOVED")
    print("-" * 78)
    print(f"  player_id        : {offender['player_id']}")
    print(f"  defender_role    : {offender['role']}")
    print(f"  depth_role       : {offender['depth_role_old']} -> "
          f"{offender['depth_role_new']}")
    print(f"  depth_side       : {_side_name(offender['depth_side_old'])} -> "
          f"{_side_name(offender['depth_side_new'])}")
    print(f"  current_y        : {_fmt(offender['y_old'])} -> "
          f"{_fmt(offender['y_new'])}  (dy = {_fmt(offender['dy'])})")
    print(f"  phase / sub      : {offender['phase']} / {offender['sub']}")
    print(f"  count split      : n_short {offender['prev_n_short']} -> "
          f"{offender['n_short']} | n_open {offender['prev_n_open']} -> "
          f"{offender['n_open']}")

    print("\n" + "-" * 78)
    print("2. WHICH AUTHORITY MOVED THEM  (first writer per field)")
    print("-" * 78)
    if not attr["offender_events"]:
        print("  (no writer events captured for this body in the window)")
    for rec in attr["by_writer"]:
        print(f"  {rec['writer']:<28} bucket={rec['bucket']:<17} "
              f"d_target_y={rec['d_target_y']:>7.3f}  d_y={rec['d_y']:>7.3f}  "
              f"d_target_x={rec['d_target_x']:>7.3f}  "
              f"side+{rec['side_changes']} role+{rec['role_changes']}  "
              f"frames={rec['frames']}")

    print("\n  ownership events in the window (all line bodies):")
    if attr["side_events"]:
        for e in attr["side_events"]:
            print(f"    f={e['frame']:<6} SIDE  pid={e['player_id']:<5} "
                  f"{_side_name(e['old'])} -> {_side_name(e['new'])}  "
                  f"by {e['writer']}  phase={e['phase']}")
    else:
        print("    (no depth_side change anywhere in the window)")
    if attr["role_events"]:
        shown = 0
        for e in attr["role_events"]:
            print(f"    f={e['frame']:<6} ROLE  pid={e['player_id']:<5} "
                  f"{e['old']} -> {e['new']}  by {e['writer']}  "
                  f"phase={e['phase']}")
            shown += 1
            if shown >= 24:
                print(f"    ... {len(attr['role_events']) - shown} more")
                break
    else:
        print("    (no depth_role change anywhere in the window)")

    print("\n" + "-" * 78)
    print("3. HOW FAR / WHEN")
    print("-" * 78)
    realised = sum(_delta(e) for e in attr["offender_events"]
                   if e["field"] == "y")
    structural = sum(_delta(e) for e in attr["offender_events"]
                     if e["field"] == "target_y")
    print(f"  realised lateral travel (sum |d y|)        : {realised:.3f} m")
    print(f"  structural lateral travel (sum |d target_y|): {structural:.3f} m")
    print(f"  movement span                              : frames {lo}..{hi}")
    wins = [e["frame"] for e in attr["offender_events"]]
    if wins:
        print(f"  first writer act                           : frame {min(wins)}")
        print(f"  last  writer act                           : frame {max(wins)}")

    print("\n" + "-" * 78)
    print("4. DENSITY IMPACT  (T-1 vs T+1)")
    print("-" * 78)
    r_before = find_frame(frames, offender.get("dens_before", t_frame - 1))
    r_after = find_frame(frames, offender.get("dens_after", t_frame + 1))
    m_before = density_metrics(r_before) if r_before else None
    m_after = density_metrics(r_after) if r_after else None
    if not m_before or not m_after:
        print("  (insufficient frames either side for a density comparison)")
    else:
        print(f"  {'metric':<24}{'T-1':>12}{'T+1':>12}{'delta':>12}")
        for key in ("w_share_short", "p_share_short", "d_share_short",
                    "mis_short", "w_share_open", "p_share_open",
                    "d_share_open", "mis_open"):
            a = m_before.get(key)
            b = m_after.get(key)
            d = None if (a is None or b is None) else (b - a)
            print(f"  {key:<24}{_fmt(a):>12}{_fmt(b):>12}{_fmt(d):>12}")
        ma, mb = m_before.get("mis_short"), m_after.get("mis_short")
        if ma is not None and mb is not None:
            dist_a, dist_b = abs(ma - 1.0), abs(mb - 1.0)
            improved = dist_b < dist_a
            print(f"\n  Misalignment |M-1| short side: {dist_a:.4f} -> "
                  f"{dist_b:.4f}")
            if improved:
                print(f"  Did this movement improve density balance? YES "
                      f"(by {dist_a - dist_b:.4f} in |M-1|)")
            else:
                print(f"  Did this movement improve density balance? NO "
                      f"(it worsened |M-1| by {dist_b - dist_a:.4f})")

    print("\n" + "-" * 78)
    print("5. CONTRACT-P BOUNDARY TEST  (y* = ruck_y + (W_open - W_short)/8)")
    print("-" * 78)
    r_t = find_frame(frames, t_frame)
    y_star = boundary_y_star(r_t) if r_t else None
    if y_star is not None and r_t is not None:
        ruck_y = r_t["ruck_y"]
        print(f"  ruck_y            = {_fmt(ruck_y)}")
        print(f"  W_short / W_open  = {_fmt(r_t['short_w'])} / "
              f"{_fmt(r_t['open_w'])}   (governed G={_fmt(r_t['governed_w'])})")
        print(f"  y*                = {_fmt(y_star)}  "
              f"(ruck_y + {_fmt(y_star - ruck_y)})")
        print(f"  offender y old/new= {_fmt(offender['y_old'])} / "
              f"{_fmt(offender['y_new'])}")
        crossed = (
            min(offender["y_old"], offender["y_new"]) < y_star
            <= max(offender["y_old"], offender["y_new"])
        )
        print(f"  offender crossed y*: {crossed}")
    else:
        print("  (boundary not computable this frame)")

    print("\n" + "=" * 78)
    print("6. SYMPTOM ATTRIBUTION VERDICT")
    print("=" * 78)
    print(f"  depth_side changed in window : {flags['side_flipped']}")
    print(f"  depth_role changed in window : {flags['role_changed']}")
    print(f"  count split changed          : {flags['counts_changed']}")
    print("\n  OWNERSHIP axis (ROOT CAUSE) - share of ownership acts:")
    for k in ("contract", "role_rebuild", "ruck_pin", "spacing",
              "transition_writer"):
        print(f"    {k:<20} {pct.get(k, 0.0):6.1f} %")
    metres = flags.get("metres") or {}
    mv = {k: v for k, v in metres.items() if k != "role_rebuild"}
    mv_total = sum(mv.values())
    print("\n  MOVEMENT axis (PROXIMATE MOVER) - metres of target_y written:")
    for k, v in mv.items():
        pc = (v / mv_total * 100.0) if mv_total > 0 else 0.0
        print(f"    {k:<20} {v:>8.3f} m  {pc:6.1f} %")
    print(f"\n  ROOT CAUSE : {flags.get('root')} - {flags.get('detail')}")
    print(f"\n  VERDICT: {verdict}")


def print_glide_dive(frames, events, glide):
    """Attribute the largest lateral GLIDE (the visible pull-across)."""
    if not glide:
        return
    pid = glide["player_id"]
    f0, f1 = glide["f0"], glide["f1"]
    win = [e for e in events
           if f0 <= e["frame"] <= f1 and e["player_id"] == pid]
    print("\n" + "-" * 78)
    print("0b. LARGEST LATERAL GLIDE - the visible pull-across")
    print("-" * 78)
    print(f"  player_id  : {pid}")
    print(f"  frames     : {f0} -> {f1}  ({f1 - f0} frames)")
    print(f"  dy         : {glide['dy']:.3f} m   "
          f"y {glide['y0']:.2f} -> {glide['y1']:.2f}")
    print(f"  side       : {_side_name(glide['side0'])} -> "
          f"{_side_name(glide['side1'])}")
    print(f"  role       : {glide['role0']} -> {glide['role1']}")
    print(f"  lane idx   : {glide['lane0']} -> {glide['lane1']}")
    print(f"  phase      : {glide['phase0']} -> {glide['phase1']}")

    agg = {}
    for e in win:
        w = e["writer"]
        rec = agg.setdefault(w, {"bucket": attr_bucket(w), "target_y": 0.0,
                                 "y": 0.0, "target_x": 0.0,
                                 "side": 0, "role": 0})
        d = _delta(e)
        if e["field"] == "target_y":
            rec["target_y"] += d
        elif e["field"] == "y":
            rec["y"] += d
        elif e["field"] == "target_x":
            rec["target_x"] += d
        elif e["field"] == "depth_side":
            rec["side"] += 1
        elif e["field"] == "depth_role":
            rec["role"] += 1

    print(f"\n  writers touching this body across the glide "
          f"({len(win)} acts):")
    for w, rec in sorted(agg.items(),
                         key=lambda kv: -(kv[1]["target_y"] + kv[1]["y"])):
        print(f"    {w:<28} bucket={rec['bucket']:<17} "
              f"d_target_y={rec['target_y']:>7.3f} d_y={rec['y']:>7.3f} "
              f"d_target_x={rec['target_x']:>7.3f} "
              f"side+{rec['side']} role+{rec['role']}")

    side_acts = [e for e in win if e["field"] == "depth_side"]
    role_acts = [e for e in win if e["field"] == "depth_role"]
    print("\n  side acts on this body : "
          + (", ".join(f"f={e['frame']} {_side_name(e['old'])}->"
                       f"{_side_name(e['new'])} by {e['writer']}"
                       for e in side_acts) if side_acts else "(none)"))
    print("  role acts on this body : "
          + (", ".join(f"f={e['frame']} {e['old']}->{e['new']} by "
                       f"{e['writer']}" for e in role_acts)
             if role_acts else "(none)"))


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────


def main():
    result = run_sequence(with_wrappers=True)
    frames = result["frames"]

    if not frames:
        print("RESULT: no OPEN_PLAY frames recorded — nothing to attribute.")
        return 0

    pull_offender, first_pull, candidates = detect_pull(frames)
    glides = detect_glides(frames)
    # PRIMARY SUBJECT = the LARGEST VISIBLE lateral glide.  A side-LABEL
    # flip with no realised movement is not the on-field symptom.
    offender = glide_as_offender(frames, glides[0] if glides else None)
    if offender is None:
        offender = pull_offender
    attr = None
    verdict = "NOT REACHED"
    pct = {}
    flags = {"side_flipped": False, "role_changed": False,
             "counts_changed": False}

    if offender is not None:
        t_frame = offender["frame"]
        # Did the count split change at T?
        r_t = find_frame(frames, t_frame)
        r_p = find_frame(frames, t_frame - 1)
        counts_changed = bool(
            r_t and r_p
            and (r_t["n_short"] != r_p["n_short"]
                 or r_t["n_open"] != r_p["n_open"])
        )
        half = int(offender.get("window_half", 5))
        attr = attribution(_events, frames, t_frame, offender["player_id"],
                           half=half)
        verdict, pct, flags = classify(attr, offender, frames, counts_changed)

        ledger = frames_in_window(frames, t_frame, half=half)
        write_ledger_csv("stage35_transition.csv", ledger,
                         offender["player_id"])
        write_events_csv("stage35_writer_events.csv", attr["all_events"])
    else:
        write_ledger_csv("stage35_transition.csv", [], None)
        write_events_csv("stage35_writer_events.csv", [])

    print_report(result, offender, first_pull, candidates, attr, verdict,
                 pct, flags, _events, glides,
                 half=int(offender.get("window_half", 5)) if offender else 5)

    with open("stage35_engine.log", "w", encoding="utf-8") as fh:
        fh.write(result["engine_log"])

    print("\nwrote: stage35_transition.csv, stage35_writer_events.csv, "
          "stage35_engine.log")
    return 0


# ─────────────────────────────────────────────────────────────────────
# TRANSITION MAP + GLIDE DETECTION
# ─────────────────────────────────────────────────────────────────────
#
# Per-frame realised |dy| is the WRONG detector for the on-field symptom:
# a reassignment changes the body's TARGET lane and the body then GLIDES
# there over many frames, so no single frame carries a large step.  These
# helpers measure the accumulated lateral travel over a short horizon (the
# visible "pull across"), and map every depth_side / depth_role change in
# the recovery span to its writer.


def glide_as_offender(frames, glide):
    """Build an offender-dict view of a detected glide (the visible pull)."""
    if not glide:
        return None
    f0, f1 = glide["f0"], glide["f1"]
    r0 = find_frame(frames, f0)
    r1 = find_frame(frames, f1)
    rp = find_frame(frames, f0 - 1)
    if r0 is None or r1 is None:
        return None
    b0 = next((b for b in r0["bodies"]
               if b["player_id"] == glide["player_id"]), None)
    b1 = next((b for b in r1["bodies"]
               if b["player_id"] == glide["player_id"]), None)
    if b0 is None or b1 is None:
        return None
    return {
        "frame": f0,
        "player_id": glide["player_id"],
        "role": b0["role"],
        "depth_role_old": b0["depth_role"],
        "depth_role_new": b1["depth_role"],
        "depth_side_old": b0["depth_side"],
        "depth_side_new": b1["depth_side"],
        "y_old": b0["y"],
        "y_new": b1["y"],
        "dy": glide["dy"],
        "phase": r0["phase"],
        "sub": r0["sub"],
        "n_short": r0["n_short"],
        "n_open": r0["n_open"],
        "prev_n_short": (rp or r0)["n_short"],
        "prev_n_open": (rp or r0)["n_open"],
        "window_half": 40,
        "dens_before": f0 - 1,
        "dens_after": f1 + 1,
        "glide_span": (f0, f1),
    }


def detect_glides(frames, horizon=30):
    """Top lateral travellers over a *horizon*-frame window."""
    index = {}
    for rec in frames:
        if rec["phase"] not in RECOVERY_PHASES:
            continue
        for b in rec["bodies"]:
            index.setdefault(b["player_id"], {})[rec["frame"]] = (b, rec)

    out = []
    for pid, by_frame in index.items():
        for f0 in sorted(by_frame):
            b0, r0 = by_frame[f0]
            best = None
            for h in range(1, horizon + 1):
                hit = by_frame.get(f0 + h)
                if hit is None:
                    continue
                b1, r1 = hit
                dy = float(b1["y"]) - float(b0["y"])
                if best is None or abs(dy) > abs(best["dy"]):
                    best = {
                        "player_id": pid,
                        "f0": f0,
                        "f1": f0 + h,
                        "y0": float(b0["y"]),
                        "y1": float(b1["y"]),
                        "dy": dy,
                        "phase0": r0["phase"],
                        "phase1": r1["phase"],
                        "side0": b0["depth_side"],
                        "side1": b1["depth_side"],
                        "role0": b0["depth_role"],
                        "role1": b1["depth_role"],
                        "lane0": b0["lane_index"],
                        "lane1": b1["lane_index"],
                    }
            if best is not None:
                out.append(best)

    out.sort(key=lambda d: -abs(d["dy"]))
    return out


def side_flip_map(events):
    """Every depth_side change in the run, in frame order (deduped)."""
    rows = []
    seen = set()
    for e in events:
        if e["field"] != "depth_side":
            continue
        key = (e["frame"], e["player_id"], e["writer"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(e)
    rows.sort(key=lambda e: (e["frame"], str(e["player_id"])))
    return rows


def role_change_map(events):
    """Every depth_role change in the run, in frame order (deduped)."""
    rows = []
    seen = set()
    for e in events:
        if e["field"] != "depth_role":
            continue
        key = (e["frame"], e["player_id"], e["writer"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(e)
    rows.sort(key=lambda e: (e["frame"], str(e["player_id"])))
    return rows


def print_transition_map(events, frames, glides):
    """Section 0: the whole-run transition map."""
    bnd = phase_boundaries(frames)
    rel = boundary_named(bnd, RETREAT_PHASES, PTB_PHASES)
    rb = boundary_named(bnd, ("RELEASE",) + PTB_PHASES, POST_RUCK_PHASES)
    print("\n" + "-" * 78)
    print("0. TRANSITION MAP  (whole run)")
    print("-" * 78)
    if rel[0]:
        print(f"  RELEASE -> PTB boundary    : frame {rel[0]}  "
              f"({rel[1]} -> {rel[2]})")
    else:
        print("  RELEASE -> PTB boundary    : (not observed)")
    if rb[0]:
        print(f"  PTB -> POST_RUCK boundary  : frame {rb[0]}  "
              f"({rb[1]} -> {rb[2]})")
    else:
        print("  PTB -> POST_RUCK boundary  : (not observed)")
    print("  all phase boundaries:")
    for f, a, b in bnd:
        print(f"    f={f:<6} {a} -> {b}")

    flips = side_flip_map(events)
    print(f"\n  depth_side changes in the whole run: {len(flips)}")
    for e in flips[:40]:
        print(f"    f={e['frame']:<6} pid={e['player_id']:<5} "
              f"{_side_name(e['old'])} -> {_side_name(e['new'])}  "
              f"by {e['writer']:<22} phase={e['phase']}")
    if len(flips) > 40:
        print(f"    ... {len(flips) - 40} more")

    roles = role_change_map(events)
    print(f"\n  depth_role changes in the whole run: {len(roles)}")
    by_writer = {}
    for e in roles:
        by_writer[e["writer"]] = by_writer.get(e["writer"], 0) + 1
    for w, n in sorted(by_writer.items(), key=lambda kv: -kv[1]):
        print(f"    {w:<28} {n}")

    print("\n  TOP LATERAL TRAVELLERS over <= 30 frames (visible pull):")
    if not glides:
        print("    (none in the recovery span)")
    for g in glides[:12]:
        print(f"    pid={g['player_id']:<5} f{g['f0']}->{g['f1']:<6} "
              f"dy={g['dy']:>7.3f} m   y {g['y0']:.2f}->{g['y1']:.2f}   "
              f"side {_side_name(g['side0'])}->{_side_name(g['side1'])}   "
              f"role {g['role0']}->{g['role1']}   "
              f"lane {g['lane0']}->{g['lane1']}   "
              f"{g['phase0']}->{g['phase1']}")


if __name__ == "__main__":
    code = 0
    try:
        code = main()
    finally:
        unwrap_all()
        pygame.quit()
    sys.exit(code)
# ─────────────────────────────────────────────────────────────────────
# TRANSITION MAP + GLIDE DETECTION (added after the first live run)
# ─────────────────────────────────────────────────────────────────────
#
# The first run showed that per-frame realised |dy| is the WRONG detector
# for the on-field symptom: a reassignment changes the body's TARGET lane
# and the body then GLIDES there over many frames, so no single frame
# carries a large step.  These helpers measure the accumulated lateral
# travel over a short horizon (the visible "pull across"), and map every
# depth_side / depth_role change in the recovery span to its writer.


def detect_glides(frames, horizon=30):
    """Top lateral travellers over a *horizon*-frame window.

    Returns the largest |y(f0+h) - y(f0)| for h <= horizon for each line
    body inside the recovery span, best-first.  This is the movement the
    user can actually see; a single-frame step is only its first increment.
    """
    index = {}
    for rec in frames:
        if rec["phase"] not in RECOVERY_PHASES:
            continue
        for b in rec["bodies"]:
            index.setdefault(b["player_id"], {})[rec["frame"]] = (b, rec)

    out = []
    for pid, by_frame in index.items():
        frames_sorted = sorted(by_frame)
        seen_starts = set()
        for f0 in frames_sorted:
            b0, r0 = by_frame[f0]
            best = None
            for h in range(1, horizon + 1):
                hit = by_frame.get(f0 + h)
                if hit is None:
                    continue
                b1, r1 = hit
                dy = float(b1["y"]) - float(b0["y"])
                if best is None or abs(dy) > abs(best["dy"]):
                    best = {
                        "player_id": pid,
                        "f0": f0,
                        "f1": f0 + h,
                        "y0": float(b0["y"]),
                        "y1": float(b1["y"]),
                        "dy": dy,
                        "phase0": r0["phase"],
                        "phase1": r1["phase"],
                        "side0": b0["depth_side"],
                        "side1": b1["depth_side"],
                        "role0": b0["depth_role"],
                        "role1": b1["depth_role"],
                        "lane0": b0["lane_index"],
                        "lane1": b1["lane_index"],
                    }
            if best is None:
                continue
            key = (pid, best["f1"])
            if key in seen_starts:
                continue
            seen_starts.add(key)
            out.append(best)

    out.sort(key=lambda d: -abs(d["dy"]))
    return out


def side_flip_map(events, frames):
    """Every depth_side change in the run, in frame order (deduped)."""
    rows = []
    seen = set()
    for e in events:
        if e["field"] != "depth_side":
            continue
        key = (e["frame"], e["player_id"], e["writer"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(e)
    rows.sort(key=lambda e: (e["frame"], str(e["player_id"])))
    return rows


def role_change_map(events, frames):
    """Every depth_role change in the run, in frame order (deduped)."""
    rows = []
    seen = set()
    for e in events:
        if e["field"] != "depth_role":
            continue
        key = (e["frame"], e["player_id"], e["writer"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(e)
    rows.sort(key=lambda e: (e["frame"], str(e["player_id"])))
    return rows


def print_transition_map(events, frames, glides):
    """Section 0: the whole-run transition map."""
    bnd = phase_boundaries(frames)
    rel = boundary_named(bnd, RETREAT_PHASES, PTB_PHASES)
    rb = boundary_named(bnd, ("RELEASE",) + PTB_PHASES, POST_RUCK_PHASES)
    print("\n" + "-" * 78)
    print("0. TRANSITION MAP  (whole run)")
    print("-" * 78)
    print(f"  RELEASE -> PTB boundary    : "
          + (f"frame {rel[0]}  ({rel[1]} -> {rel[2]})" if rel[0]
             else "(not observed)"))
    print(f"  PTB -> POST_RUCK boundary  : "
          + (f"frame {rb[0]}  ({rb[1]} -> {rb[2]})" if rb[0]
             else "(not observed)"))
    print("  all phase boundaries:")
    for f, a, b in bnd:
        print(f"    f={f:<6} {a} -> {b}")

    flips = side_flip_map(events, frames)
    print(f"\n  depth_side changes in the whole run: {len(flips)}")
    for e in flips[:40]:
        print(f"    f={e['frame']:<6} pid={e['player_id']:<5} "
              f"{_side_name(e['old'])} -> {_side_name(e['new'])}  "
              f"by {e['writer']:<22} phase={e['phase']}")
    if len(flips) > 40:
        print(f"    ... {len(flips) - 40} more")

    roles = role_change_map(events, frames)
    print(f"\n  depth_role changes in the whole run: {len(roles)}")
    by_writer = {}
    for e in roles:
        by_writer[e["writer"]] = by_writer.get(e["writer"], 0) + 1
    for w, n in sorted(by_writer.items(), key=lambda kv: -kv[1]):
        print(f"    {w:<28} {n}")

    print(f"\n  TOP LATERAL TRAVELLERS over <= 30 frames (visible pull):")
    if not glides:
        print("    (none >= 0.5 m in the recovery span)")
    for g in glides[:12]:
        print(f"    pid={g['player_id']:<5} f{g['f0']}->{g['f1']:<6} "
              f"dy={g['dy']:>7.3f} m   y {g['y0']:.2f}->{g['y1']:.2f}   "
              f"side {_side_name(g['side0'])}->{_side_name(g['side1'])}   "
              f"role {g['role0']}->{g['role1']}   "
              f"lane {g['lane0']}->{g['lane1']}   "
              f"{g['phase0']}->{g['phase1']}")
