"""
probe_stage7_pre_ptb_lateral_adjustment.py — Stage 7
Pre-PTB lateral adjustment — capture + attribution (READ-ONLY).

SUBJECT
-------
The post-Stage-6 field assessment records a residual behaviour:

    "Very small pre-PTB lateral adjustment occasionally observed, most
     noticeable around the second defender from the ruck on the short side.
     Brief.  Low amplitude.  Not obviously width-related.  Likely associated
     with PTB-transition authority (slot ownership refresh, census rebuild,
     lane refit, anticipation reconnect, or similar transition logic)."

Stage 6 established a single formation-WIDTH authority and exonerated the
width family.  This probe does NOT revisit width.  It answers ONE question:

    Which TRANSITION-PATH authority produces the small pre-PTB lateral
    adjustment of the short-side second-from-ruck defender?

WHY "SECOND DEFENDER FROM THE RUCK ON THE SHORT SIDE" IS A NAMED OBJECT
-----------------------------------------------------------------------
``ruck_adjacent_pin.select_first_two_defenders`` returns ``(slot1, slot2)``
per side, persisted on ``world._ruck_pin_state`` as ``L``/``R`` (slot-1) and
``L2``/``R2`` (slot-2).  Slot-2 is BY DEFINITION the second defender from the
ruck.  During the post-TES settle window (RELEASE -> PTB_* -> POST_RUCK,
``defensive_tuners.post_tes_settle_active``) the pin is TARGET-ONLY (no
position snap) but STILL:
  * pins slot-1's ``target_y`` to ``ruck_y +/- 2.0`` (role-gated to a
    genuine ``depth_role == 'A'`` pivot), and
  * clamps slot-2's ``target_y`` into the side-sign band
    ``[anchor +/- RUCK_PIN_SLOT2_MIN_GAP_M, anchor +/- RUCK_PIN_SLOT2_MAX_GAP_M]``
    (``anchor = ruck_y +/- 2.0``).
So the pin is the prime suspect for a small, brief, non-width lateral nudge
of exactly this body.

CANDIDATE AUTHORITIES (each wrapped at its IMPORT SITE)
-------------------------------------------------------
  slot ownership refresh  ruck_adjacent_pin.enforce_ruck_adjacent_pin,
                          update_slot_target, lock_slot
  census rebuild          population_authority.publish_line_population,
                          trim_observer_dicts,
                          open_play_engine.repair_line_population_if_drifted,
                          open_play_engine.finalise_line_population
  lane refit              defensive_split_spacing.compute_targets,
                          defensive_pipeline.apply_frame_spacing,
                          defensive_pipeline.run_defensive_pipeline
  anticipation reconnect  anticipation_layer.apply_anticipation_authority,
                          apply_anticipation_pre_shift
  ownership               assign_split_sides / assign_depth_roles /
                          _bootstrap_side_roles / _maintain_side /
                          _post_ruck_recovery_seed
  transition writers      the OPE phase handlers
                          (_update_release ... _update_post_ruck_open_play)

METHOD
------
1. Boot the real engine headless (same bootstrap as the other probes).
2. Wrap every named authority at its IMPORT SITE with a shadow-diff
   recorder: snapshot the tracked fields of every defender immediately
   before the call, diff immediately after, emit one event per real change.
   A NESTED call sees the inner call's writes, so each change is attributed
   to the INNERMOST writer that produced it.
3. Record one per-frame snapshot of the count / width / settle state and
   every line body, with the short-side slot-1 and slot-2 identities.
4. DETECT the pre-PTB lateral adjustment: within the pre-PTB window
   (TACKLE_DOMINANCE tail + RELEASE + PTB_READY), find the largest
   DIRECTIONAL lateral glide of the short-side slot-2 body over a short
   horizon (>= SMALL_THRESHOLD_M).  Report amplitude, direction, frame.
5. Attribute every writer that touched the offending body's fields inside
   the window, ownership-first.
6. Run the probe N_RUNS times (seeded) because the engine is documented
   NON-DETERMINISTIC run-to-run (see defensive_tuners side-ownership note);
   a single run cannot support a verdict.

WHAT THIS PROBE DOES NOT DO
---------------------------
No production file is modified.  No fix is proposed.  ``0.65`` is not
changed.  The count allocation is not changed.  Side assignment is not
changed.  The probe is read-only against the engine and restores every
patched symbol in a ``finally`` block.  The record path reads ONLY published
state (``world.formation_width``, ``world._ruck_pin_state``) and the pure
``resolve_short_side``; it never calls a solver that writes world state.

OUTPUTS
-------
  - stdout report
  - stage7_ledger.csv         T-20..T+20 per-frame ledger (best run)
  - stage7_writer_events.csv  every attributed writer event in the window
  - stage7_engine.log         captured engine stdout (best run)
  - stage7_summary.txt        per-run summary (all N runs)

Run:
  python probe_stage7_pre_ptb_lateral_adjustment.py
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import json
import random
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
import game.defence.defence_lvl_1.population_authority as pa
import game.defence.defence_lvl_1.ruck_adjacent_pin as rap
import game.defence.defence_lvl_1.anticipation_layer as al
import game.defence.defence_lvl_1.formation_width_authority as fwa
from game.defence.defence_lvl_1.defensive_tuners import post_tes_settle_active

# ─────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────

MAX_FRAMES = 3600
FPS = 60.0
DT = 1.0 / FPS
N_RUNS = 5
SEED = 0
# The engine is non-deterministic run-to-run and can abort inside
# tes_drive_structure on a pre-existing span-contract fault; retry a run
# with a fresh seed up to this many times so the probe always completes.
RUN_ATTEMPTS = 3

# The PRE-PTB reform span: the drive-end hand-over and the RELEASE hold.
# The "retreat -> PTB" boundary lives at RELEASE -> PTB_PREP; the pre-PTB
# window is everything up to and including RELEASE.
PRE_PTB_PHASES = ("TACKLE_DOMINANCE", "RELEASE", "TACKLE_RELEASE", "PTB_READY")
# The POST-PTB span (so "anticipation reconnect" at the first OPEN_PLAY
# frame is provable either way; is_drive_phase is False in RELEASE and True
# again in OPEN_PLAY).
POST_PTB_PHASES = ("PTB_PREP", "PTB_EXEC", "PTB",
                   "POST_PTB_RESET", "POST_RUCK_OPEN_PLAY")

# The wrapper is only armed while a reform window is live (or was live
# within the last ACTIVATE_TAIL frames), so the probe cost stays bounded.
ACTIVATE_TAIL = 4

# A pre-PTB lateral glide at or above this (m) is the residual artefact.
SMALL_THRESHOLD_M = 0.08
# Horizon (frames) over which a DIRECTIONAL glide is measured (0.1 s @60fps).
GLIDE_HORIZON = 6

# Line bodies the depth-role / spacing chain owns.
LINE_ROLES = ("DEF_LINE_READY", "DEF_CHASE", "DEF_RETREAT_10")

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

LEFT = -1
RIGHT = +1
_SIDE_NAME = {LEFT: "LEFT", RIGHT: "RIGHT"}


def _cls(v):
    """JSON-safe scalar."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


def _side_name(side):
    return _SIDE_NAME.get(side, "-")


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
# One shared ledger.  A wrapper snapshots live values into a LOCAL dict
# immediately before calling the real function, so a nested call sees the
# inner call's writes and the outer call reports "no change" for fields the
# inner already moved — which attributes a change to the INNERMOST writer
# that produced it.
#
# The recorder is armed ONLY while a reform window is live (or was live
# within ACTIVATE_TAIL frames); outside it the wrapper is a pass-through.
# This keeps the probe cost bounded (OPEN_PLAY dominates the run).

_events = []          # list of event dicts
_installed = []       # [(module, attr, original)]
_wrapped_keys = set()  # {(id(module), attr)} already wrapped
_frame_ctx = {"frame": 0, "phase": "", "sub": ""}
_active = {"armed": False}

# Live engine references, set by install_wrappers().
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

    ``module`` may be a module or a CLASS (for the OPE phase handlers).
    ``players_getter()`` returns the list of players to watch.  Each
    ``(module, attr)`` pair is wrapped at most once; two modules that bind
    the SAME function object each get their own wrapper, so whichever
    namespace the engine calls is the one that records.
    """
    key = (id(module), attr)
    if key in _wrapped_keys:
        return
    original = getattr(module, attr, None)
    if original is None or not callable(original):
        return

    def wrapper(*args, **kwargs):
        if not _active["armed"]:
            return original(*args, **kwargs)
        _refresh_ctx()
        players = players_getter()
        before = _snapshot(players)
        try:
            result = original(*args, **kwargs)
        finally:
            _diff_against(before, players, writer_name)
        return result

    try:
        setattr(module, attr, wrapper)
    except (AttributeError, TypeError):
        return
    _installed.append((module, attr, original))
    _wrapped_keys.add(key)


def unwrap_all():
    """Restore every patched symbol (finally-block safety)."""
    for module, attr, original in reversed(_installed):
        try:
            setattr(module, attr, original)
        except Exception:
            pass
    _installed.clear()
    _wrapped_keys.clear()


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


def all_defenders(world):
    """Every defending-team player (the watch set for the wrappers)."""
    return list(getattr(world, "defending_team", None) or [])


def _tuners(ope):
    return getattr(ope, "def_tuners", None)


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


def _published_width(world):
    """The PRIMARY width publication for THIS frame, or None."""
    pub = getattr(world, "formation_width", None)
    if not isinstance(pub, dict):
        return None
    if pub.get("frame") != getattr(world, "frame_count", None):
        return None
    return pub


def short_side_of(world, ope):
    """Resolve the short side — published first, then the PURE resolver."""
    pub = _published_width(world)
    if isinstance(pub, dict) and pub.get("short_side") in (LEFT, RIGHT):
        return int(pub["short_side"])
    tuners = _tuners(ope)
    if tuners is None:
        return None
    try:
        return int(fwa.resolve_short_side(world, tuners))
    except Exception:
        return None


def _pin_slots(world):
    """The pin's persisted slot picks: {L,R,L2,R2} -> player_id or None."""
    state = getattr(world, "_ruck_pin_state", None)
    if not isinstance(state, dict):
        return {}
    return state


def _body_by_pid(world, pid):
    if pid is None:
        return None
    for d in (getattr(world, "defending_team", None) or []):
        if getattr(d, "player_id", None) == pid:
            return d
    return None


def _settle(world):
    try:
        return bool(post_tes_settle_active(world))
    except Exception:
        return None


def frame_record(world, ope):
    """One per-frame snapshot of the state + every line body.

    Reads ONLY published / persisted state and the pure ``resolve_short_side``;
    it never calls a solver that writes world state.
    """
    tuners = _tuners(ope)
    ruck_x = float(getattr(world, "ruck_x_m", 0.0) or 0.0)
    ruck_y = float(getattr(world, "ruck_y_m", 0.0) or 0.0)
    short_side = short_side_of(world, ope)
    pub = _published_width(world)
    if isinstance(pub, dict):
        left_w = float(pub.get("left_m")) if pub.get("left_m") is not None else None
        right_w = float(pub.get("right_m")) if pub.get("right_m") is not None else None
        governed = float(pub.get("governed_m")) if pub.get("governed_m") is not None else None
        commanded = pub.get("commanded_m")
        reducers = dict(pub.get("reducers") or {})
    else:
        left_w = right_w = governed = commanded = None
        reducers = {}

    if short_side == LEFT:
        short_w, open_w = left_w, right_w
    elif short_side == RIGHT:
        short_w, open_w = right_w, left_w
    else:
        short_w = open_w = None

    n_left = getattr(world, "published_count_left", None)
    n_right = getattr(world, "published_count_right", None)
    if short_side == LEFT:
        n_short, n_open = n_left, n_right
    elif short_side == RIGHT:
        n_short, n_open = n_right, n_left
    else:
        n_short = n_open = None

    slots = _pin_slots(world)
    if short_side == LEFT:
        slot1_pid, slot2_pid = slots.get("L"), slots.get("L2")
        open_slot1_pid, open_slot2_pid = slots.get("R"), slots.get("R2")
    elif short_side == RIGHT:
        slot1_pid, slot2_pid = slots.get("R"), slots.get("R2")
        open_slot1_pid, open_slot2_pid = slots.get("L"), slots.get("L2")
    else:
        slot1_pid = slot2_pid = open_slot1_pid = open_slot2_pid = None

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
            "movement_state": str(getattr(d, "movement_state", None)),
            "anticipation_authority": bool(getattr(d, "anticipation_authority", False)),
            "anticipation_target_y": _cls(getattr(d, "anticipation_target_y", None)),
        })

    return {
        "frame": int(getattr(world, "frame_count", 0)),
        "phase": _phase_label(ope, world),
        "sub": _sub_label(world),
        "ruck_x": ruck_x,
        "ruck_y": ruck_y,
        "short_side": short_side,
        "left_w": left_w,
        "right_w": right_w,
        "short_w": short_w,
        "open_w": open_w,
        "governed_w": governed,
        "commanded_w": _cls(commanded),
        "reducers": reducers,
        "n_left": n_left,
        "n_right": n_right,
        "n_short": n_short,
        "n_open": n_open,
        "settle": _settle(world),
        "slot1_pid": slot1_pid,
        "slot2_pid": slot2_pid,
        "open_slot1_pid": open_slot1_pid,
        "open_slot2_pid": open_slot2_pid,
        "bc_x": _cls(getattr(getattr(world, "ball_carrier", None), "x", None)),
        "bc_y": _cls(getattr(getattr(world, "ball_carrier", None), "y", None)),
        "bodies": bodies,
    }


def _record_body(rec, pid):
    if pid is None:
        return None
    for b in rec["bodies"]:
        if b["player_id"] == pid:
            return b
    return None
# ─────────────────────────────────────────────────────────────────────
# SEQUENCE RUNNER
# ─────────────────────────────────────────────────────────────────────

def _recently_reformed(frames, idx, tail=ACTIVATE_TAIL):
    """True when frame *idx* or one of the *tail* frames before it is a
    reform window (so the wrapper is armed for the drive-end hand-over)."""
    lo = max(0, idx - tail)
    window = ("TACKLE_DOMINANCE", "RELEASE", "TACKLE_RELEASE", "PTB_READY",
              "PTB_PREP", "PTB_EXEC", "PTB", "POST_PTB_RESET",
              "POST_RUCK_OPEN_PLAY")
    for j in range(lo, idx + 1):
        if frames[j]["phase"] in window:
            return True
    return False


def run_sequence(install=True):
    """Boot, run headless, record one frame record per OPEN_PLAY frame."""
    world, ball, ope, kickoff_engine, collision_system = boot_world()
    if install:
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
                # Arm the recorder for the next frame when a reform window
                # is live or was live within ACTIVATE_TAIL frames.
                idx = len(frames) - 1
                _active["armed"] = _recently_reformed(frames, idx)
    finally:
        unwrap_all()

    return {
        "world": world,
        "ope": ope,
        "frames": frames,
        "phase_counts": phase_counts,
        "engine_log": wrapped_log.getvalue(),
        "events": list(_events),
    }


# ─────────────────────────────────────────────────────────────────────
# ADJUSTMENT DETECTION
# ─────────────────────────────────────────────────────────────────────

def find_frame(frames, frame_no):
    for r in frames:
        if r["frame"] == frame_no:
            return r
    return None


def pre_ptb_windows(frames):
    """Contiguous frame-index runs whose phase is in PRE_PTB_PHASES."""
    windows = []
    start = None
    for i, rec in enumerate(frames):
        if rec["phase"] in PRE_PTB_PHASES:
            if start is None:
                start = i
        else:
            if start is not None:
                windows.append((start, i - 1))
                start = None
    if start is not None:
        windows.append((start, len(frames) - 1))
    return windows


def post_ptb_windows(frames):
    """Contiguous frame-index runs whose phase is in POST_PTB_PHASES."""
    windows = []
    start = None
    for i, rec in enumerate(frames):
        if rec["phase"] in POST_PTB_PHASES:
            if start is None:
                start = i
        else:
            if start is not None:
                windows.append((start, i - 1))
                start = None
    if start is not None:
        windows.append((start, len(frames) - 1))
    return windows


def _directional_glide(track, horizon=GLIDE_HORIZON):
    """Largest DIRECTIONAL lateral glide over *horizon* frames.

    ``track`` is [(frame_index, y), ...] in frame order.  Returns
    ``(amplitude, idx_end, direction, monotonic)`` for the window with the
    largest |net| glide whose per-frame deltas are all the same sign;
    ``(0.0, None, 0, False)`` when none qualifies.
    """
    best = (0.0, None, 0, False)
    if len(track) < 2:
        return best
    n = len(track)
    for end in range(horizon, n):
        seg = track[end - horizon:end + 1]
        ys = [y for (_i, y) in seg]
        net = ys[-1] - ys[0]
        deltas = [ys[k + 1] - ys[k] for k in range(len(ys) - 1)]
        signs = {0 if abs(d) < 1e-9 else (1 if d > 0 else -1) for d in deltas}
        monotonic = len(signs - {0}) <= 1
        if monotonic and abs(net) > best[0]:
            best = (abs(net), track[end][0], 1 if net > 0 else -1, True)
    return best


def detect_adjustment(frames, use_slot="slot2", which="pre"):
    """Find the largest directional glide of the target body.

    ``use_slot`` selects the short-side slot-2 (default, the field-note body)
    or slot-1 (reference).  ``which`` selects the PRE-PTB reform window
    (default) or the POST-PTB window.  Returns ``(best, candidates, windows)``.
    """
    windows = (pre_ptb_windows(frames) if which == "pre"
               else post_ptb_windows(frames))
    candidates = []
    for (a, b) in windows:
        # Track the chosen slot's body Y across the window.  The slot body
        # is the short-side slot for each frame; if it flips mid-window the
        # track is split at the flip (a flip is itself a finding).
        track = []
        cur_pid = None
        seg_start = a
        segs = []
        for i in range(a, b + 1):
            rec = frames[i]
            pid = rec["slot2_pid"] if use_slot == "slot2" else rec["slot1_pid"]
            body = _record_body(rec, pid)
            if pid != cur_pid:
                if cur_pid is not None and track:
                    segs.append((cur_pid, list(track)))
                cur_pid = pid
                track = []
                seg_start = i
            if body is not None:
                track.append((i, body["y"]))
        if cur_pid is not None and track:
            segs.append((cur_pid, list(track)))

        for pid, tr in segs:
            amp, end_idx, direction, mono = _directional_glide(tr)
            if end_idx is None or amp < SMALL_THRESHOLD_M:
                continue
            end_rec = frames[end_idx]
            start_rec = _frame_at_or_before(frames, tr, end_idx - GLIDE_HORIZON)
            candidates.append({
                "pid": pid,
                "window": (a, b),
                "seg_start": tr[0][0],
                "t_frame": end_rec["frame"],
                "amp": amp,
                "direction": direction,
                "monotonic": mono,
                "y_start": float(tr[0][1]),
                "y_end": float(tr[-1][1]),
                "phase_at_t": end_rec["phase"],
                "short_side": end_rec["short_side"],
                "slot1_pid": end_rec["slot1_pid"],
                "slot2_pid": end_rec["slot2_pid"],
                "settle_at_t": end_rec["settle"],
                "n_short": end_rec["n_short"],
                "n_open": end_rec["n_open"],
                "open_slot2_pid": end_rec["open_slot2_pid"],
            })
            _ = start_rec

    if not candidates:
        return None, [], windows
    best = max(candidates, key=lambda c: c["amp"])
    return best, candidates, windows


def _frame_at_or_before(frames, track, idx):
    """Return the frame record nearest *idx* within the track's frame ids."""
    ids = [i for (i, _y) in track]
    pick = None
    for i in ids:
        if i <= idx:
            pick = i
    if pick is None:
        pick = ids[0]
    return frames[pick]


def slot2_identity_flips(frames):
    """Frames where the short-side slot-2 identity changed."""
    flips = []
    prev = None
    for rec in frames:
        if rec["phase"] not in PRE_PTB_PHASES:
            prev = None
            continue
        pid = rec["slot2_pid"]
        if prev is not None and pid is not None and prev is not None and pid != prev:
            flips.append({"frame": rec["frame"], "phase": rec["phase"],
                          "from": prev, "to": pid})
        prev = pid
    return flips
# ─────────────────────────────────────────────────────────────────────
# ATTRIBUTION
# ─────────────────────────────────────────────────────────────────────

CONTRACT_WRITERS = frozenset({"assign_split_sides", "decide_side_counts"})
ROLE_REBUILD_WRITERS = frozenset({
    "assign_depth_roles", "_bootstrap_side_roles", "_maintain_side",
    "_post_ruck_recovery_seed",
})
RUCK_PIN_WRITERS = frozenset({"enforce_ruck_adjacent_pin", "update_slot_target"})
POPULATION_WRITERS = frozenset({
    "publish_line_population", "trim_observer_dicts",
    "repair_line_population_if_drifted", "finalise_line_population",
})
LANE_REFIT_WRITERS = frozenset({
    "compute_targets", "apply_frame_spacing", "run_defensive_pipeline",
})
ANTICIPATION_WRITERS = frozenset({
    "apply_anticipation_authority", "apply_anticipation_pre_shift",
})


def attr_bucket(writer):
    if writer in CONTRACT_WRITERS:
        return "contract"
    if writer in ROLE_REBUILD_WRITERS:
        return "role_rebuild"
    if writer in RUCK_PIN_WRITERS:
        return "ruck_pin"
    if writer in POPULATION_WRITERS:
        return "population"
    if writer in LANE_REFIT_WRITERS:
        return "lane_refit"
    if writer in ANTICIPATION_WRITERS:
        return "anticipation"
    if str(writer).startswith("OPE."):
        return "transition_writer"
    return "other"


def _delta(e):
    old, new = e.get("old"), e.get("new")
    if old is None or new is None:
        return 0.0
    try:
        return abs(float(new) - float(old))
    except (TypeError, ValueError):
        return 0.0


def attribution(events, t_frame, pid, half=20):
    """Build the attribution payload for the T-half .. T+half window."""
    lo, hi = t_frame - half, t_frame + half
    win = [e for e in events if lo <= e["frame"] <= hi]
    off = [e for e in win if e["player_id"] == pid]

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

    bucket_totals = {}
    bucket_y = {}
    for rec in by_writer.values():
        b = rec["bucket"]
        bucket_totals[b] = bucket_totals.get(b, 0.0) + rec["d_target_y"] + rec["d_target_x"]
        bucket_y[b] = bucket_y.get(b, 0.0) + rec["d_y"]

    side_events = [e for e in win if e["field"] == "depth_side"]
    role_events = [e for e in win if e["field"] == "depth_role"]

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
        "bucket_y": bucket_y,
    }


def classify(attr, counts_changed):
    """Ownership-first verdict for the pre-PTB lateral adjustment.

    Root-cause precedence (mirrors the Stage 3.5 attribution):
      depth_side by contract  -> contract (width/count incompatibility)
      depth_side by other     -> ownership (non-contract side writer)
      depth_role change       -> role-rebuild re-lane
      ruck_pin writes         -> slot-ownership refresh (the pin)
      population writes       -> census rebuild
      lane refit writes       -> lane refit
      anticipation writes     -> anticipation reconnect
      transition writes       -> transition writer (retreat/PTB)
    """
    b = attr["bucket_totals"]
    contract = b.get("contract", 0.0)
    role_rebuild = b.get("role_rebuild", 0.0)
    pin = b.get("ruck_pin", 0.0)
    pop = b.get("population", 0.0)
    refit = b.get("lane_refit", 0.0)
    antic = b.get("anticipation", 0.0)
    trans = b.get("transition_writer", 0.0)
    total = contract + role_rebuild + pin + pop + refit + antic + trans
    pct = {
        k: (v / total * 100.0) if total > 0 else 0.0
        for k, v in (("contract", contract), ("role_rebuild", role_rebuild),
                     ("ruck_pin", pin), ("population", pop),
                     ("lane_refit", refit), ("anticipation", antic),
                     ("transition_writer", trans))
    }

    side_by_contract = [e for e in attr["side_events"]
                        if e["writer"] in CONTRACT_WRITERS]
    side_by_other = [e for e in attr["side_events"]
                     if e["writer"] not in CONTRACT_WRITERS]
    role_events = attr["role_events"]

    if side_by_contract:
        root = "contract_c" if counts_changed else "contract_p"
        verdict = "CONTRACT INCOMPATIBILITY"
        detail = ("depth_side reassigned by assign_split_sides "
                  f"(counts_changed={counts_changed})")
    elif side_by_other:
        root = "side_other"
        verdict = "OWNERSHIP BUG"
        detail = "depth_side changed by a non-contract writer: " + str(
            side_by_other[0]["writer"])
    elif role_events:
        root = "role_rebuild"
        verdict = "TRANSITION OWNERSHIP (role re-lane)"
        detail = ("depth_role changed in-window: "
                  + ", ".join(sorted({str(e["writer"]) for e in role_events})))
    elif (pin > 0.0 or trans > 0.0):
        # No ownership change.  Decide by the REALISED lateral contribution
        # (d_y), NOT the target-write churn — the pin/retreat dual-write
        # inflates the churn on both sides without moving the body.  The pin
        # is TARGET-ONLY during the settle window (d_y == 0); the retreat /
        # PTB transition writer integrates the position.
        pin_y = attr["bucket_y"].get("ruck_pin", 0.0)
        trans_y = attr["bucket_y"].get("transition_writer", 0.0)
        if trans_y > 0.0 and trans_y >= pin_y:
            root = "transition_writer"
            verdict = "PTB-TRANSITION PATH (retreat/spacing convergence)"
            detail = ("no ownership change; the retreat/PTB transition writer "
                      f"integrated the body's position (realised dy="
                      f"{trans_y:.2f} m) while the ruck pin held a "
                      "TARGET-ONLY value (its slot-2 clamp) the body never "
                      "occupied")
        elif pin_y > 0.0:
            root = "ruck_pin"
            verdict = "PTB-TRANSITION AUTHORITY (ruck-adjacent pin)"
            detail = ("the ruck pin moved the body's position "
                      "(no ownership change)")
        else:
            root = "ruck_pin_target_only"
            verdict = "TARGET/LANE CONTRADICTION (ruck pin, target-only)"
            detail = ("the ruck pin wrote a target the body never occupied "
                      "(target-only during the settle window)")
    elif pop > 0.0 and pop >= refit and pop >= antic and pop >= trans:
        root = "population"
        verdict = "PTB-TRANSITION AUTHORITY (census rebuild)"
        detail = "population-authority publication owned the lateral target"
    elif refit > 0.0 and refit >= antic and refit >= trans:
        root = "lane_refit"
        verdict = "PTB-TRANSITION AUTHORITY (lane refit)"
        detail = "spacing / pipeline lane recompute owned the lateral target"
    elif antic > 0.0 and antic >= trans:
        root = "anticipation"
        verdict = "PTB-TRANSITION AUTHORITY (anticipation reconnect)"
        detail = "anticipation authority owned the lateral target"
    elif trans > 0.0:
        root = "transition_writer"
        verdict = "PTB-TRANSITION AUTHORITY (transition writer)"
        detail = "a retreat/PTB transition writer owned the lateral target"
    else:
        root = "none"
        verdict = "NO WRITER OBSERVED"
        detail = "no ownership change and no lateral writer observed in-window"

    return verdict, root, detail, pct, {
        "side_flipped": bool(attr["side_events"]),
        "role_changed": bool(role_events),
        "counts_changed": bool(counts_changed),
        "metres": {"contract": contract, "role_rebuild": role_rebuild,
                   "ruck_pin": pin, "population": pop,
                   "lane_refit": refit, "anticipation": antic,
                   "transition_writer": trans},
    }


def counts_changed_in_window(events, t_frame, half=20):
    """True when a depth_side change occurred in-window (count re-solve)."""
    lo, hi = t_frame - half, t_frame + half
    return any(e["field"] == "depth_side" and lo <= e["frame"] <= hi
               for e in events)


# ─────────────────────────────────────────────────────────────────────
# CSV WRITERS
# ─────────────────────────────────────────────────────────────────────

def _fmt(v, nd=3):
    if v is None:
        return "-"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def write_ledger_csv(path, frames, t_frame, pid, half=20):
    import csv
    cols = [
        "frame", "phase", "sub", "depth_side", "depth_role",
        "target_y", "current_y", "dy_frame", "frame_spacing_y",
        "role_depth_target_y", "target_source", "target_writer",
        "frozen", "movement_state", "anticipation_authority",
        "anticipation_target_y",
        "n_short", "n_open", "short_side", "short_w", "open_w", "governed_w",
        "settle", "slot1_pid", "slot2_pid", "is_slot2",
    ]
    lo, hi = t_frame - half, t_frame + half
    prev_y = None
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols)
        wr.writeheader()
        for rec in frames:
            if rec["frame"] < lo or rec["frame"] > hi:
                continue
            body = _record_body(rec, pid)
            if body is None:
                prev_y = None
                continue
            y = float(body["y"])
            dy = None if prev_y is None else y - prev_y
            prev_y = y
            wr.writerow({
                "frame": rec["frame"],
                "phase": rec["phase"],
                "sub": rec["sub"],
                "depth_side": _side_name(body["depth_side"]),
                "depth_role": body["depth_role"],
                "target_y": _fmt(body["target_y"]),
                "current_y": _fmt(y),
                "dy_frame": _fmt(dy),
                "frame_spacing_y": _fmt(body["frame_spacing_y"]),
                "role_depth_target_y": _fmt(body["role_depth_target_y"]),
                "target_source": body["target_source"],
                "target_writer": body["target_writer"],
                "frozen": body["frozen"],
                "movement_state": body["movement_state"],
                "anticipation_authority": body["anticipation_authority"],
                "anticipation_target_y": _fmt(body["anticipation_target_y"]),
                "n_short": rec["n_short"],
                "n_open": rec["n_open"],
                "short_side": _side_name(rec["short_side"]),
                "short_w": _fmt(rec["short_w"]),
                "open_w": _fmt(rec["open_w"]),
                "governed_w": _fmt(rec["governed_w"]),
                "settle": rec["settle"],
                "slot1_pid": rec["slot1_pid"],
                "slot2_pid": rec["slot2_pid"],
                "is_slot2": (body["player_id"] == rec["slot2_pid"]),
            })


def write_events_csv(path, attr):
    import csv
    cols = ["frame", "phase", "sub", "writer", "bucket", "player_id",
            "field", "old", "new", "delta", "depth_role", "depth_side"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols)
        wr.writeheader()
        for e in attr["all_events"]:
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


# ─────────────────────────────────────────────────────────────────────
# WRAPPER INSTALLATION — patch at the IMPORT SITE, never the source module
# ─────────────────────────────────────────────────────────────────────

OPE_METHOD_WRITERS = (
    "_update_release",
    "_enter_ptb_prep",
    "_update_ptb_prep",
    "_update_ptb_exec",
    "_update_ptb",
    "_update_post_ruck_open_play",
)


def install_wrappers(world, ope):
    """Wrap every named authority at its import site."""
    global _OPE, _WORLD
    _OPE = ope
    _WORLD = world

    def getter():
        return all_defenders(_WORLD)

    # ── Contract P / C — side + count ownership ──
    _wrap(dr, "assign_split_sides", "assign_split_sides", getter)
    _wrap(dr, "decide_side_counts", "decide_side_counts", getter)
    _wrap(dss, "assign_split_sides", "assign_split_sides", getter)
    _wrap(dss, "decide_side_counts", "decide_side_counts", getter)

    # ── depth-role ownership ──
    _wrap(dr, "assign_depth_roles", "assign_depth_roles", getter)
    _wrap(dr, "_bootstrap_side_roles", "_bootstrap_side_roles", getter)
    _wrap(dr, "_maintain_side", "_maintain_side", getter)
    _wrap(dr, "_post_ruck_recovery_seed", "_post_ruck_recovery_seed", getter)

    # ── spacing / lane refit ──
    _wrap(dss, "compute_targets", "compute_targets", getter)
    _wrap(dp, "apply_frame_spacing", "apply_frame_spacing", getter)
    _wrap(dp, "run_defensive_pipeline", "run_defensive_pipeline", getter)

    # ── population authority (census rebuild) ──
    _wrap(pa, "publish_line_population", "publish_line_population", getter)
    _wrap(pa, "trim_observer_dicts", "trim_observer_dicts", getter)

    # ── ruck-adjacent pin (slot ownership refresh) ──
    _wrap(rap, "enforce_ruck_adjacent_pin", "enforce_ruck_adjacent_pin", getter)
    _wrap(rap, "update_slot_target", "update_slot_target", getter)

    # ── anticipation (reconnect) ──
    _wrap(al, "apply_anticipation_authority", "apply_anticipation_authority", getter)
    _wrap(al, "apply_anticipation_pre_shift", "apply_anticipation_pre_shift", getter)

    # ── the OPE module namespace (functions the engine binds at import) ──
    _wrap(ope_mod, "enforce_ruck_adjacent_pin", "enforce_ruck_adjacent_pin", getter)
    _wrap(ope_mod, "run_defensive_pipeline", "run_defensive_pipeline", getter)
    _wrap(ope_mod, "compute_targets", "compute_targets", getter)
    _wrap(ope_mod, "repair_line_population_if_drifted",
          "repair_line_population_if_drifted", getter)
    _wrap(ope_mod, "finalise_line_population", "finalise_line_population", getter)
    _wrap(ope_mod, "assign_depth_roles", "assign_depth_roles", getter)

    # ── OPE phase handlers (retreat / PTB transition writers) ──
    cls = type(ope)
    for name in OPE_METHOD_WRITERS:
        _wrap(cls, name, "OPE." + name, getter)
# ─────────────────────────────────────────────────────────────────────
# REFERENCE SCAN (threshold-independent) + REPORTING
# ─────────────────────────────────────────────────────────────────────

def scan_pre_slot2(frames, horizon=GLIDE_HORIZON):
    """Largest directional pre-PTB glide of short-side slot-2, ANY size.

    Returns ``(amp, t_frame, pid)`` for the largest directional glide found
    across the pre-PTB windows, regardless of ``SMALL_THRESHOLD_M`` — the
    "did anything move at all, and by how much" reference for a run whose
    best candidate falls below the artefact threshold.
    """
    best_amp, best_t, best_pid = 0.0, None, None
    for (a, b) in pre_ptb_windows(frames):
        track, cur_pid, segs = [], None, []
        for i in range(a, b + 1):
            rec = frames[i]
            pid = rec["slot2_pid"]
            body = _record_body(rec, pid)
            if pid != cur_pid:
                if cur_pid is not None and track:
                    segs.append((cur_pid, list(track)))
                cur_pid = pid
                track = []
            if body is not None:
                track.append((i, body["y"]))
        if cur_pid is not None and track:
            segs.append((cur_pid, list(track)))
        for pid, tr in segs:
            amp, end_idx, _dir, mono = _directional_glide(tr, horizon)
            if end_idx is not None and mono and amp > best_amp:
                best_amp, best_t, best_pid = amp, frames[end_idx]["frame"], pid
    return best_amp, best_t, best_pid


def target_lane_disagreement(frames, t_frame, pid, half=20):
    """The pin's ``target_y`` vs the body's frame-spacing lane, over a window.

    Returns ``(max_gap, mean_gap, y_pp)``: the largest and mean absolute
    ``|target_y - frame_spacing_y|`` for the offending body, and the
    peak-to-peak amplitude of its realised ``y`` (the settle sawtooth).
    A non-trivial gap is the target/lane contradiction; the body can only
    occupy ONE value, so the pin's target and the spacing lane disagree.
    """
    gaps = []
    ys = []
    lo, hi = t_frame - half, t_frame + half
    for rec in frames:
        if rec["frame"] < lo or rec["frame"] > hi:
            continue
        b = _record_body(rec, pid)
        if b is None:
            continue
        t_y = b.get("target_y")
        s_y = b.get("frame_spacing_y")
        if t_y is not None and s_y is not None:
            gaps.append(abs(float(t_y) - float(s_y)))
        ys.append(float(b["y"]))
    if not gaps:
        return None, None, None
    y_pp = (max(ys) - min(ys)) if ys else None
    return max(gaps), sum(gaps) / len(gaps), y_pp


def _error_run(run_index, exc):
    """A run stub when the engine aborted on a PRE-EXISTING fault."""
    return {
        "run": run_index, "error": f"{type(exc).__name__}: {exc}",
        "frames": [], "phase_counts": {}, "events": [],
        "best2": None, "cands2": [], "pre_wins": [],
        "best1": None, "cands1": [],
        "best2post": None, "cands2post": [], "post_wins": [],
        "flips": [], "ref_amp": 0.0, "ref_t": None, "ref_pid": None,
        "engine_log": "",
    }


def _run_once(run_index, attempt=0):
    """Run one seeded sequence; return an error stub on an engine abort.

    The engine is documented NON-DETERMINISTIC run-to-run and can abort
    inside ``tes_drive_structure`` on a width/span contract violation
    (``SpanContractError: span contract must be positive (got 0.0)``) that
    is PRE-EXISTING (see the Stage 5 report) and unrelated to this read-only
    probe.  Each run gets its own seed so a retry explores a fresh world.
    """
    random.seed(SEED + run_index * 131 + attempt * 17)
    _events.clear()
    try:
        res = run_sequence(install=True)
    except Exception as exc:  # pre-existing engine fault — do not mask silently
        return _error_run(run_index, exc)
    frames = res["frames"]
    best2, cands2, pre_wins = detect_adjustment(frames, "slot2", "pre")
    best1, cands1, _ = detect_adjustment(frames, "slot1", "pre")
    best2post, cands2post, post_wins = detect_adjustment(frames, "slot2", "post")
    flips = slot2_identity_flips(frames)
    ref_amp, ref_t, ref_pid = scan_pre_slot2(frames)
    res.update({
        "run": run_index,
        "best2": best2, "cands2": cands2, "pre_wins": pre_wins,
        "best1": best1, "cands1": cands1,
        "best2post": best2post, "cands2post": cands2post,
        "post_wins": post_wins,
        "flips": flips,
        "ref_amp": ref_amp, "ref_t": ref_t, "ref_pid": ref_pid,
    })
    return res


def print_run_header(res):
    frames = res["frames"]
    print("-" * 78)
    if res.get("error"):
        print(f"RUN {res['run'] + 1}/{N_RUNS}   ENGINE ABORT (pre-existing "
              f"non-deterministic fault): {res['error']}")
        return
    print(f"RUN {res['run'] + 1}/{N_RUNS}   frames={len(frames)}   "
          f"pre-ptb windows={len(res['pre_wins'])}   "
          f"post-ptb windows={len(res['post_wins'])}")
    b2 = res["best2"]
    if b2 is None:
        print(f"  pre-PTB short-side slot-2 glide >= {SMALL_THRESHOLD_M} m : "
              f"NONE")
        print(f"  reference (any size): {res['ref_amp']:.3f} m "
              f"pid={res['ref_pid']} at frame {res['ref_t']}")
    else:
        print(f"  pre-PTB short-side slot-2 glide : {b2['amp']:.3f} m  "
              f"pid={b2['pid']}  frame={b2['t_frame']}  "
              f"phase={b2['phase_at_t']}  "
              f"dir={'+' if b2['direction'] > 0 else '-'}  "
              f"settle={b2['settle_at_t']}")
    b1 = res["best1"]
    if b1 is not None:
        print(f"  pre-PTB short-side slot-1 glide : {b1['amp']:.3f} m  "
              f"pid={b1['pid']}  frame={b1['t_frame']}")
    b2p = res["best2post"]
    if b2p is not None:
        print(f"  post-PTB short-side slot-2 glide: {b2p['amp']:.3f} m  "
              f"pid={b2p['pid']}  frame={b2p['t_frame']}  "
              f"phase={b2p['phase_at_t']}")
    print(f"  short-side slot-2 identity flips: {len(res['flips'])}")


def print_ledger(frames, t_frame, pid, half=12):
    lo, hi = t_frame - half, t_frame + half
    print(f"    {'frame':>6} {'phase':<18} {'tgt_y':>9} {'y':>9} "
          f"{'dy':>8} {'spc_y':>9} {'role':>4} {'settle':>7} {'is2':>4}")
    prev_y = None
    for rec in frames:
        if rec["frame"] < lo or rec["frame"] > hi:
            continue
        body = _record_body(rec, pid)
        if body is None:
            prev_y = None
            continue
        y = float(body["y"])
        dy = None if prev_y is None else y - prev_y
        prev_y = y
        print(f"    {rec['frame']:>6} {rec['phase']:<18} "
              f"{_fmt(body['target_y']):>9} {_fmt(y):>9} "
              f"{_fmt(dy):>8} {_fmt(body['frame_spacing_y']):>9} "
              f"{str(body['depth_role'] or '-'):>4} "
              f"{str(rec['settle']):>7} "
              f"{str(body['player_id'] == rec['slot2_pid']):>4}")


def print_attribution(attr, verdict, root, detail, pct, flags):
    print("  attributed writers on the offending body (T-20 .. T+20):")
    if not attr["by_writer"]:
        print("    (no writer events captured for this body in the window)")
    for rec in attr["by_writer"]:
        print(f"    {rec['writer']:<28} bucket={rec['bucket']:<17} "
              f"d_tgt_y={rec['d_target_y']:>7.3f}  d_y={rec['d_y']:>7.3f}  "
              f"side+{rec['side_changes']} role+{rec['role_changes']}  "
              f"frames={rec['frames']}")
    print("  ownership events in the window (all line bodies):")
    if attr["side_events"]:
        for e in attr["side_events"][:20]:
            print(f"    f={e['frame']:<6} SIDE pid={e['player_id']:<5} "
                  f"{_side_name(e['old'])} -> {_side_name(e['new'])}  "
                  f"by {e['writer']}  phase={e['phase']}")
    else:
        print("    (no depth_side change anywhere in the window)")
    if attr["role_events"]:
        for e in attr["role_events"][:20]:
            print(f"    f={e['frame']:<6} ROLE pid={e['player_id']:<5} "
                  f"{e['old']} -> {e['new']}  by {e['writer']}  "
                  f"phase={e['phase']}")
    else:
        print("    (no depth_role change anywhere in the window)")
    print("  bucket shares (structural metres):")
    for k in ("contract", "role_rebuild", "ruck_pin", "population",
              "lane_refit", "anticipation", "transition_writer"):
        print(f"    {k:<18} {attr['bucket_totals'].get(k, 0.0):>8.3f} m  "
              f"({pct.get(k, 0.0):.1f}%)")
    print(f"  VERDICT: {verdict}")
    print(f"  root    : {root}")
    print(f"  detail  : {detail}")
    print(f"  flags   : side_flipped={flags['side_flipped']} "
          f"role_changed={flags['role_changed']} "
          f"counts_changed={flags['counts_changed']}")


def print_detailed_report(res):
    frames = res["frames"]
    print("=" * 78)
    print("STAGE 7 — PRE-PTB LATERAL ADJUSTMENT (capture + attribution)")
    print("=" * 78)
    print("phase counts (this run):")
    for k, v in sorted(res["phase_counts"].items(), key=lambda kv: -kv[1]):
        print(f"    {k:<24} {v}")

    b2 = res["best2"]
    if b2 is None:
        print("\nRESULT: no pre-PTB directional glide of the short-side "
              f"slot-2 body reached {SMALL_THRESHOLD_M} m this run.")
        print(f"        largest reference glide (any size) = "
              f"{res['ref_amp']:.3f} m (pid={res['ref_pid']}, "
              f"frame={res['ref_t']}).")
        print("        -> no attribution possible for a sub-threshold run.")
        return None

    t_frame, pid = b2["t_frame"], b2["pid"]
    full = find_frame(frames, t_frame)
    print("\n" + "-" * 78)
    print("1. WHO MOVED (short-side SECOND defender from the ruck = slot-2)")
    print("-" * 78)
    print(f"  player_id        : {pid}")
    print(f"  short_side       : {_side_name(b2['short_side'])}")
    print(f"  slot1_pid        : {b2['slot1_pid']}")
    print(f"  slot2_pid        : {b2['slot2_pid']}")
    print(f"  open_side slot2  : {b2['open_slot2_pid']}")
    print(f"  T (glide end)    : {t_frame}   phase={b2['phase_at_t']}")
    print(f"  amplitude        : {b2['amp']:.3f} m  "
          f"(dir={'+' if b2['direction'] > 0 else '-'})")
    print(f"  pre-PTB window   : frames {b2['window'][0]}..{b2['window'][1]}")
    print(f"  settle at T      : {b2['settle_at_t']}")
    print(f"  count at T       : n_short={b2['n_short']} n_open={b2['n_open']}")
    if full is not None:
        print(f"  ruck_y at T      : {_fmt(full['ruck_y'])}   "
              f"short_w={_fmt(full['short_w'])} open_w={_fmt(full['open_w'])} "
              f"governed={_fmt(full['governed_w'])}")
    print(f"  candidates >= {SMALL_THRESHOLD_M} m: {len(res['cands2'])}")

    print("\n" + "-" * 78)
    print("2. FRAME LEDGER (T-12 .. T+12)")
    print("-" * 78)
    print_ledger(frames, t_frame, pid)

    counts_changed = counts_changed_in_window(res["events"], t_frame)
    attr = attribution(res["events"], t_frame, pid)
    verdict, root, detail, pct, flags = classify(attr, counts_changed)

    print("\n" + "-" * 78)
    print("3. ATTRIBUTION")
    print("-" * 78)
    print_attribution(attr, verdict, root, detail, pct, flags)

    print("\n" + "-" * 78)
    print("4. POST-PTB CHECK (anticipation reconnect at OPEN_PLAY)")
    print("-" * 78)
    b2p = res["best2post"]
    if b2p is None:
        print(f"  no post-PTB directional glide of short-side slot-2 >= "
              f"{SMALL_THRESHOLD_M} m")
        print("  -> anticipation reconnect does NOT produce a qualifying "
              "post-PTB nudge on this body")
    else:
        print(f"  post-PTB glide   : {b2p['amp']:.3f} m  pid={b2p['pid']}  "
              f"frame={b2p['t_frame']}  phase={b2p['phase_at_t']}")

    print("\n" + "-" * 78)
    print("5. WIDTH-INDEPENDENCE CHECK")
    print("-" * 78)
    if full is not None:
        print(f"  short_w={_fmt(full['short_w'])} open_w={_fmt(full['open_w'])} "
              f"governed={_fmt(full['governed_w'])}")
    print(f"  depth_side changed in window : {flags['side_flipped']}")
    print(f"  depth_role changed in window : {flags['role_changed']}")
    print(f"  count split changed          : {flags['counts_changed']}")

    print("\n" + "-" * 78)
    print("6. TARGET vs LANE (the pin's target_y vs the spacing lane)")
    print("-" * 78)
    gap_max, gap_mean, y_pp = target_lane_disagreement(frames, t_frame, pid)
    print(f"  max |target_y - frame_spacing_y| : {_fmt(gap_max)} m")
    print(f"  mean                             : {_fmt(gap_mean)} m")
    print(f"  realised y peak-to-peak          : {_fmt(y_pp)} m")
    return {
        "t_frame": t_frame, "pid": pid, "amp": b2["amp"],
        "verdict": verdict, "root": root, "detail": detail,
        "attr": attr, "flags": flags,
    }


def write_summary(path, runs):
    lines = []
    lines.append("STAGE 7 — PRE-PTB LATERAL ADJUSTMENT — PER-RUN SUMMARY")
    lines.append("=" * 78)
    lines.append(f"N_RUNS={N_RUNS}  MAX_FRAMES={MAX_FRAMES}  "
                 f"threshold={SMALL_THRESHOLD_M} m  horizon={GLIDE_HORIZON}")
    lines.append("")
    hdr = (f"{'run':>3} {'frames':>7} {'preWin':>6} {'postWin':>7} "
           f"{'slot2_amp':>9} {'slot2_t':>8} {'slot1_amp':>9} "
           f"{'post_amp':>8} {'ref_amp':>8} {'flips':>5}")
    lines.append(hdr)
    for r in runs:
        b2, b1, b2p = r["best2"], r["best1"], r["best2post"]
        lines.append(
            f"{r['run'] + 1:>3} {len(r['frames']):>7} "
            f"{len(r['pre_wins']):>6} {len(r['post_wins']):>7} "
            f"{(b2['amp'] if b2 else 0.0):>9.3f} "
            f"{(str(b2['t_frame']) if b2 else '-'):>8} "
            f"{(b1['amp'] if b1 else 0.0):>9.3f} "
            f"{(b2p['amp'] if b2p else 0.0):>8.3f} "
            f"{r['ref_amp']:>8.3f} {len(r['flips']):>5}"
        )
    detected = sum(1 for r in runs if r["best2"] is not None)
    lines.append("")
    lines.append(f"runs with a qualifying pre-PTB slot-2 glide : "
                 f"{detected}/{N_RUNS}")
    amps = [r["best2"]["amp"] for r in runs if r["best2"] is not None]
    if amps:
        lines.append(f"pre-PTB slot-2 glide amplitude : "
                     f"min={min(amps):.3f} max={max(amps):.3f} "
                     f"mean={sum(amps) / len(amps):.3f} m")
    refs = [r["ref_amp"] for r in runs]
    if refs:
        lines.append(f"reference (any-size) glide    : "
                     f"min={min(refs):.3f} max={max(refs):.3f} m")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return "\n".join(lines)


def main():
    print("Stage 7 pre-PTB lateral adjustment probe — running "
          f"{N_RUNS} seeded runs ...")
    runs = []
    for i in range(N_RUNS):
        res = None
        for attempt in range(RUN_ATTEMPTS):
            res = _run_once(i, attempt)
            if not res.get("error"):
                break
            print(f"[retry] run {i + 1} aborted on attempt {attempt + 1}: "
                  f"{res['error']}")
        runs.append(res)
        print_run_header(res)

    # Choose the run with the largest qualifying pre-PTB slot-2 glide for
    # the CSV + deep-dive; fall back to the run with the largest reference.
    qualifying = [r for r in runs if r["best2"] is not None]
    if qualifying:
        chosen = max(qualifying, key=lambda r: r["best2"]["amp"])
    else:
        chosen = max(runs, key=lambda r: r["ref_amp"])

    deep = print_detailed_report(chosen)

    if deep is not None:
        write_ledger_csv("stage7_ledger.csv", chosen["frames"],
                         deep["t_frame"], deep["pid"])
        write_events_csv("stage7_writer_events.csv", deep["attr"])
        try:
            with open("stage7_engine.log", "w", encoding="utf-8") as fh:
                fh.write(chosen["engine_log"])
        except Exception:
            pass

    summary = write_summary("stage7_summary.txt", runs)
    print("\n" + "=" * 78)
    print("CROSS-RUN SUMMARY")
    print("=" * 78)
    print(summary)
    print("\nartefacts: stage7_ledger.csv, stage7_writer_events.csv, "
          "stage7_summary.txt, stage7_engine.log")


if __name__ == "__main__":
    main()
