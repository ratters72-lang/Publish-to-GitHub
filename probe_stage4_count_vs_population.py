"""probe_stage4_count_vs_population.py — Stage 4
COUNT CONTRACT vs POPULATION REALITY.

QUESTION
--------
Stage 3 established "Width Share != Population Share".
Stage 3.5 observed, live and in-frame:

    n_open = 5      (the count authority's own published stamp)
    open-side line bodies = 6

This probe answers ONE question, per frame:

    Count Authority   vs   Actual Bodies Stamped To Side

and identifies the FIRST frame where they diverge, naming the player and
the writer responsible.

WHAT IS MEASURED
----------------
The count authority is a single writer with TWO publishers:

  * defensive_split_spacing.decide_side_counts()  -- computes (n_left,
    n_right) and publishes world._last_count_split / count_split_left /
    count_split_right.  Called through required_side_counts() by
    assign_split_sides().
  * assign_split_sides() has exactly two production call sites, BOTH of
    which reach decide_side_counts with their OWN defender list:
      1. defensive_depth_roles.assign_depth_roles()      (frame preamble)
      2. defensive_rebanding.reband_after_stabilisation() (post-quiet reband)
    world._last_count_split is therefore LAST-WRITER-WINS across two
    call sites that can hold different populations.

The population contract is the set of bodies whose ``depth_side`` is
stamped.  assign_split_sides() stamps exactly the list it is handed, and
that list is filtered by defensive_pipeline._line_defenders() (drops the
fullback actor and every ``tackle_state_active`` body) and then by
defensive_depth_roles.EXCLUDED_ROLES.

NOTHING CLEARS ``depth_side`` ON EXCLUSION, so a body dropped from the
count authority's population keeps its stamp on the Player object and is
still counted by any observer that reads ``depth_side``.

FIVE POPULATIONS ARE MEASURED PER FRAME
---------------------------------------
  P_AUTH_FULL  defenders - fullback - tackle_state - EXCLUDED_ROLES
               == exactly the list assign_depth_roles hands to
                  assign_split_sides -> this IS the count's population
  P_AUTH       defenders - fullback - tackle_state            (no role gate)
  P_LINE       the Stage 3.5 observer set
               (defender_role in DEF_LINE_READY/DEF_CHASE/DEF_RETREAT_10,
                - fullback - ball carrier)
  P_STAMP      every defender carrying depth_side in (-1, +1)
  P_CHAIN      keys(world.frame_spacing_targets) -> bodies

FOUR DIAGNOSTICS PER FRAME
--------------------------
  D1 TOTAL INTEGRITY   n_left + n_right  vs  len(P_AUTH_FULL) at the
                       moment decide_side_counts ran.  Isolates the
                       count_split_hysteresis lag (the adopted previous
                       counts are never re-checked against n_total).
  D2 STAMP INTEGRITY   bodies in P_STAMP that are NOT in P_AUTH_FULL:
                       the stranded / stale depth_side stamp.  Names the
                       body, its defender_role, its exclusion reason, and
                       the writer that last stamped its side.
  D3 OBSERVER DIVERGENCE  the published counts vs the per-side body count
                       of P_LINE and of P_STAMP.  This reproduces the
                       n_open = 5 / 6 bodies symptom and says which
                       population the authority does agree with.
  D4 PUBLISHER DIVERGENCE  which of the two assign_split_sides call sites
                       published last this frame, and whether their
                       n_total arguments agreed.

READ-ONLY
---------
No production file is modified.  No fix is proposed.  No tuner value is
changed.  Every wrapped symbol is restored in a ``finally`` block.

OUTPUTS
-------
  - stdout report
  - stage4_count_population.csv   per-frame: both counts + all 5 populations
  - stage4_divergence.csv         every divergence, with causal player + writer
  - stage4_engine.log             captured engine stdout

Run:
  python probe_stage4_count_vs_population.py
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
import game.defence.defence_lvl_1.defensive_rebanding as drb
import game.defence.defence_lvl_1.defensive_split_spacing as dss
import game.defence.defence_lvl_1.tackle_state_gate as tsg
import game.defence.defence_lvl_1.fullback_role as fbr

# ─────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────

MAX_FRAMES = 3600
FPS = 60.0
DT = 1.0 / FPS

# The Stage 3.5 observer set -- the bodies an observer of "the defensive
# line" counts.  Deliberately does NOT exclude tackle_state bodies.
LINE_ROLES = ("DEF_LINE_READY", "DEF_CHASE", "DEF_RETREAT_10")

LEFT = dss.LEFT
RIGHT = dss.RIGHT

# The count authority's role gate (defensive_depth_roles.EXCLUDED_ROLES),
# read as a frozenset of role NAMES so it compares against .name strings.
EXCLUDED_ROLE_NAMES = frozenset(
    getattr(r, "name", str(r)) for r in dr.EXCLUDED_ROLES
)

# The two side-stamping call sites (import-site identities).
CALLER_PREAMBLE = "assign_depth_roles"
CALLER_REBAND = "reband_after_stabilisation"
CALLER_OTHER = "other"
# ─────────────────────────────────────────────────────────────────────
# SMALL HELPERS
# ─────────────────────────────────────────────────────────────────────

def _cls(v):
    """JSON/CSV-safe scalar."""
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


def _def_role_name(d):
    """The ``defender_role`` as a plain string name ('-' when unset)."""
    role = getattr(d, "defender_role", None)
    if role is None:
        return "-"
    return getattr(role, "name", str(role))


def _phase_label(ope, world):
    label = getattr(world, "_ope_phase_label", None)
    if label:
        return str(label)
    return str(getattr(ope, "_phase", "") or "")


def _sub_label(world):
    sub = getattr(world, "sub_state", None)
    return str(getattr(sub, "value", sub))


def _tuners(ope):
    return getattr(ope, "def_tuners", None)


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────
# PHASE BUCKETS
# ─────────────────────────────────────────────────────────────────────
# The task explicitly excludes PTB-transition and role-movement questions.
# Phase is recorded for every divergence so the verdict can state WHICH
# phase each divergence lives in, without analysing the transition itself.

RETREAT_PHASES = ("RELEASE", "TACKLE_RELEASE", "PTB_READY")
PTB_PHASES = ("PTB_PREP", "PTB_EXEC", "PTB")
POST_RUCK_PHASES = ("POST_PTB_RESET", "POST_RUCK_OPEN_PLAY")
DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
RECOVERY_PHASES = RETREAT_PHASES + PTB_PHASES + POST_RUCK_PHASES


def _phase_bucket(phase):
    p = str(phase or "")
    if p in DRIVE_PHASES:
        return "TES_DRIVE"
    if p in RETREAT_PHASES:
        return "RETREAT"
    if p in PTB_PHASES:
        return "PTB"
    if p in POST_RUCK_PHASES:
        return "POST_RUCK"
    if p == "OPEN_PLAY":
        return "OPEN_PLAY"
    return "OTHER"


# ─────────────────────────────────────────────────────────────────────
# THE FIVE POPULATIONS
# ─────────────────────────────────────────────────────────────────────
# Every set is built from ``world.defending_team`` (the engine population
# the preamble itself is handed at open_play_engine.py:1851-1853).

def _defenders(world):
    return [d for d in (getattr(world, "defending_team", None) or [])
            if d is not None]


def _is_fullback(d):
    return bool(getattr(d, "fullback_role_active", False))


def _is_tackle_state(d):
    return bool(getattr(d, "tackle_state_active", False))


def _is_excluded_role(d):
    return _def_role_name(d) in EXCLUDED_ROLE_NAMES


def p_auth_full(world):
    """defenders - fullback - tackle_state - EXCLUDED_ROLES.

    This is EXACTLY the list ``assign_depth_roles`` hands to
    ``assign_split_sides`` and therefore exactly the population the count
    authority decides the split for.
    """
    return [
        d for d in _defenders(world)
        if not _is_fullback(d)
        and not _is_tackle_state(d)
        and not _is_excluded_role(d)
    ]


def p_auth(world):
    """defenders - fullback - tackle_state (no role gate)."""
    return [
        d for d in _defenders(world)
        if not _is_fullback(d) and not _is_tackle_state(d)
    ]


def p_line(world):
    """The Stage 3.5 observer set: line-role bodies only."""
    bc = getattr(world, "ball_carrier", None)
    return [
        d for d in _defenders(world)
        if _def_role_name(d) in LINE_ROLES
        and not _is_fullback(d)
        and d is not bc
    ]


def p_stamp(world):
    """Every defender carrying a live side stamp (depth_side in -1/+1)."""
    return [
        d for d in _defenders(world)
        if getattr(d, "depth_side", None) in (LEFT, RIGHT)
    ]


def p_chain(world):
    """Every defender covered by the published frame spacing dict."""
    dct = getattr(world, "frame_spacing_targets", None)
    if not isinstance(dct, dict):
        return []
    return [d for d in _defenders(world) if id(d) in dct]


def side_counts(bodies):
    """(n_left, n_right, n_unstamped) over a population."""
    nl = nr = nu = 0
    for d in bodies:
        s = getattr(d, "depth_side", None)
        if s == LEFT:
            nl += 1
        elif s == RIGHT:
            nr += 1
        else:
            nu += 1
    return nl, nr, nu


def population_pids(bodies):
    return [getattr(d, "player_id", None) for d in bodies]


# ─────────────────────────────────────────────────────────────────────
# WRITER ATTRIBUTION — wrapper machinery
# ─────────────────────────────────────────────────────────────────────

_installed = []          # [(module, attr, original)]

# Per-pid record of the LAST writer that stamped depth_side.
_side_writer = {}        # pid -> (frame, caller, old_side, new_side)
# Per-pid record of the LAST writer that touched a membership flag.
_flag_writer = {}        # pid -> {attr: (frame, writer)}
# Every decide_side_counts call, in frame order.
_count_calls = []        # [ {frame, phase, caller, n_total, n_left, n_right,
                         #    ruck_y, prev} ]
# Every assign_split_sides call, in frame order.
_split_calls = []        # [ {frame, phase, caller, n_pop, n_left, n_right,
                         #    stamp_changes} ]
# Every _line_defenders drop event.
_drop_events = []        # [ {frame, pid, role, reason} ]
# Population membership transitions (a body entering/leaving P_AUTH_FULL).
_pop_events = []         # [ {frame, phase, pid, kind, reason, role, side} ]

_frame_ctx = {"frame": 0, "phase": "", "sub": ""}
_WORLD = None
_OPE = None


def _refresh_ctx():
    if _WORLD is not None:
        _frame_ctx["frame"] = int(
            getattr(_WORLD, "frame_count", _frame_ctx["frame"]) or 0
        )
        _frame_ctx["sub"] = _sub_label(_WORLD)
    if _OPE is not None:
        _frame_ctx["phase"] = str(getattr(_OPE, "_phase", "") or "")


def _record_flag(pid, attr, writer):
    if pid is None:
        return
    rec = _flag_writer.setdefault(pid, {})
    rec[attr] = (_frame_ctx["frame"], writer)


def _wrap(module, attr, writer_name):
    """Wrap ``module.attr`` with a recorder; restore via ``unwrap_all``."""
    original = getattr(module, attr, None)
    if original is None or not callable(original):
        return
    if getattr(original, "_stage4_wrapped", False):
        return

    def wrapper(*args, **kwargs):
        _refresh_ctx()
        try:
            return original(*args, **kwargs)
        finally:
            pass

    wrapper._stage4_wrapped = True
    wrapper._stage4_original = original
    setattr(module, attr, wrapper)
    _installed.append((module, attr, original))


def unwrap_all():
    for module, attr, original in reversed(_installed):
        try:
            setattr(module, attr, original)
        except Exception:
            pass
    _installed.clear()
# ─────────────────────────────────────────────────────────────────────
# SPECIFIC RECORDERS
# ─────────────────────────────────────────────────────────────────────

def _wrap_decide_side_counts():
    """Record every count-authority solve: its n_total and its output.

    Patched on the SOURCE module: ``required_side_counts`` calls
    ``decide_side_counts`` as a module global, so this single patch catches
    every call from both ``assign_split_sides`` call sites.
    """
    original = getattr(dss, "decide_side_counts", None)
    if original is None or getattr(original, "_stage4_wrapped", False):
        return

    def wrapper(*args, **kwargs):
        _refresh_ctx()
        world = args[0] if len(args) > 0 else kwargs.get("world")
        n_total = args[2] if len(args) > 2 else kwargs.get("n_total")
        ruck_y = args[3] if len(args) > 3 else kwargs.get("ruck_y")
        prev = None
        if world is not None:
            prev = getattr(world, "_last_count_split", None)
        result = original(*args, **kwargs)
        n_left = n_right = None
        try:
            n_left, n_right = int(result[0]), int(result[1])
        except (TypeError, ValueError, IndexError):
            pass
        _count_calls.append({
            "frame": _frame_ctx["frame"],
            "phase": _frame_ctx["phase"],
            "n_total": int(n_total) if n_total is not None else None,
            "n_left": n_left,
            "n_right": n_right,
            "ruck_y": _cls(ruck_y),
            "prev": tuple(prev) if isinstance(prev, (tuple, list)) else None,
        })
        return result

    wrapper._stage4_wrapped = True
    dss.decide_side_counts = wrapper
    _installed.append((dss, "decide_side_counts", original))


def _wrap_assign_split_sides(module, caller):
    """Record one ``assign_split_sides`` call site and every side it stamps.

    The module is patched AT ITS IMPORT SITE (``dr.assign_split_sides`` /
    ``drb.assign_split_sides``) because both modules bind the symbol into
    their own namespace with ``from ... import assign_split_sides``.  The
    ``caller`` label identifies which publisher produced this count.
    """
    original = getattr(module, "assign_split_sides", None)
    if original is None or getattr(original, "_stage4_wrapped", False):
        return

    def wrapper(*args, **kwargs):
        _refresh_ctx()
        world = args[0] if len(args) > 0 else kwargs.get("world")
        defenders = args[1] if len(args) > 1 else kwargs.get("defenders")
        defenders = [d for d in (defenders or []) if d is not None]
        before = {id(d): getattr(d, "depth_side", None) for d in defenders}
        result = original(*args, **kwargs)
        changes = []
        for d in defenders:
            now = getattr(d, "depth_side", None)
            old = before.get(id(d))
            if old == now:
                continue
            pid = getattr(d, "player_id", None)
            changes.append((pid, old, now))
            if pid is not None:
                _side_writer[pid] = (
                    _frame_ctx["frame"], caller, old, now,
                )
        _split_calls.append({
            "frame": _frame_ctx["frame"],
            "phase": _frame_ctx["phase"],
            "caller": caller,
            "n_pop": len(defenders),
            "n_changes": len(changes),
            "changes": changes,
        })
        return result

    wrapper._stage4_wrapped = True
    setattr(module, "assign_split_sides", wrapper)
    _installed.append((module, "assign_split_sides", original))


def _wrap_line_defenders():
    """Record every STAMPED body the population filter drops this frame.

    ``defensive_pipeline._line_defenders`` is the filter that removes the
    fullback actor and every ``tackle_state_active`` body from the list the
    count authority sees.  A dropped body that is still carrying a
    ``depth_side`` stamp is exactly the "stranded stamp" of D2.
    """
    original = getattr(dp, "_line_defenders", None)
    if original is None or getattr(original, "_stage4_wrapped", False):
        return

    def wrapper(defenders):
        _refresh_ctx()
        out = original(defenders)
        keep = set(id(d) for d in out)
        for d in (defenders or []):
            if d is None or id(d) in keep:
                continue
            if getattr(d, "depth_side", None) not in (LEFT, RIGHT):
                continue
            if _is_fullback(d):
                reason = "fullback_role_active"
            elif _is_tackle_state(d):
                reason = "tackle_state_active"
            else:
                reason = "other"
            _drop_events.append({
                "frame": _frame_ctx["frame"],
                "pid": getattr(d, "player_id", None),
                "role": _def_role_name(d),
                "reason": reason,
                "side": getattr(d, "depth_side", None),
            })
        return out

    wrapper._stage4_wrapped = True
    dp._line_defenders = wrapper
    _installed.append((dp, "_line_defenders", original))


def _wrap_tackle_flags():
    """Record the writer that sets / clears ``tackle_state_active``."""
    for fn_name in ("stamp_tackle_state", "clear_tackle_state"):
        original = getattr(tsg, fn_name, None)
        if original is None or getattr(original, "_stage4_wrapped", False):
            continue

        def _make(orig, label):
            def wrapper(*args, **kwargs):
                _refresh_ctx()
                d = args[0] if len(args) > 0 else kwargs.get("d")
                result = orig(*args, **kwargs)
                _record_flag(
                    getattr(d, "player_id", None),
                    "tackle_state",
                    "tackle_state_gate." + label,
                )
                return result
            return wrapper

        w = _make(original, fn_name)
        w._stage4_wrapped = True
        setattr(tsg, fn_name, w)
        _installed.append((tsg, fn_name, original))


def _wrap_fullback():
    """Record ``fullback_role.assign_fullback_role`` as the flag writer."""
    original = getattr(fbr, "assign_fullback_role", None)
    if original is None or getattr(original, "_stage4_wrapped", False):
        return

    def wrapper(*args, **kwargs):
        _refresh_ctx()
        result = original(*args, **kwargs)
        if _WORLD is not None:
            for d in _defenders(_WORLD):
                if _is_fullback(d):
                    _record_flag(
                        getattr(d, "player_id", None),
                        "fullback",
                        "fullback_role.assign_fullback_role",
                    )
        return result

    wrapper._stage4_wrapped = True
    fbr.assign_fullback_role = wrapper
    _installed.append((fbr, "assign_fullback_role", original))


def install_wrappers(world, ope):
    global _WORLD, _OPE
    _WORLD = world
    _OPE = ope
    _wrap_decide_side_counts()
    _wrap_assign_split_sides(dr, CALLER_PREAMBLE)
    _wrap_assign_split_sides(drb, CALLER_REBAND)
    _wrap_line_defenders()
    _wrap_tackle_flags()
    _wrap_fullback()
# ─────────────────────────────────────────────────────────────────────
# BOOT + PER-FRAME RECORD
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


def _pop_key(d):
    pid = getattr(d, "player_id", None)
    return pid if pid is not None else id(d)


def _short_open(nl, nr, short_side):
    """Map (n_left, n_right) onto (n_short, n_open)."""
    if short_side == LEFT:
        return nl, nr
    if short_side == RIGHT:
        return nr, nl
    return None, None


def _last_count_call(frames_count, frame):
    """The LAST decide_side_counts call recorded this frame (or None)."""
    for call in reversed(_count_calls):
        if call["frame"] == frame:
            return call
    return None


def _split_calls_this_frame(frame):
    return [c for c in _split_calls if c["frame"] == frame]


def _resolve_short_side(world, tuners):
    if tuners is None:
        return None
    try:
        return dss.resolve_short_side(
            world, tuners, ruck_y=_f(getattr(world, "ruck_y_m", 0.0))
        )
    except Exception:
        return None


def frame_record(world, ope):
    """One per-frame snapshot: the count authority + five populations."""
    tuners = _tuners(ope)
    frame = int(getattr(world, "frame_count", 0) or 0)
    phase = _phase_label(ope, world)
    ruck_y = _f(getattr(world, "ruck_y_m", 0.0))

    split_prev = getattr(world, "_last_count_split", None)
    c_left = getattr(world, "count_split_left", None)
    c_right = getattr(world, "count_split_right", None)
    c_phase = getattr(world, "count_split_phase", None)

    call = _last_count_call(frame, frame)
    split_here = _split_calls_this_frame(frame)

    auth_full = p_auth_full(world)
    auth = p_auth(world)
    line = p_line(world)
    stamp = p_stamp(world)
    chain = p_chain(world)

    short_side = _resolve_short_side(world, tuners)

    af_l, af_r, af_u = side_counts(auth_full)
    au_l, au_r, au_u = side_counts(auth)
    ln_l, ln_r, ln_u = side_counts(line)
    st_l, st_r, st_u = side_counts(stamp)
    ch_l, ch_r, ch_u = side_counts(chain)

    n_short, n_open = _short_open(c_left, c_right, short_side)
    af_short, af_open = _short_open(af_l, af_r, short_side)
    au_short, au_open = _short_open(au_l, au_r, short_side)
    ln_short, ln_open = _short_open(ln_l, ln_r, short_side)
    st_short, st_open = _short_open(st_l, st_r, short_side)
    ch_short, ch_open = _short_open(ch_l, ch_r, short_side)

    auth_full_ids = set(id(d) for d in auth_full)
    auth_ids = set(id(d) for d in auth)
    stranded = [d for d in stamp if id(d) not in auth_full_ids]
    stranded_pids = [getattr(d, "player_id", None) for d in stranded]

    return {
        "frame": frame,
        "phase": phase,
        "bucket": _phase_bucket(phase),
        "sub": _sub_label(world),
        "ruck_y": ruck_y,
        "short_side": short_side,
        # ── COUNT AUTHORITY ──
        "count_left": c_left,
        "count_right": c_right,
        "count_phase": c_phase,
        "n_short": n_short,
        "n_open": n_open,
        "split_prev": tuple(split_prev) if isinstance(
            split_prev, (tuple, list)) else None,
        "call_n_total": call["n_total"] if call else None,
        "call_n_left": call["n_left"] if call else None,
        "call_n_right": call["n_right"] if call else None,
        "call_count": len([c for c in _count_calls if c["frame"] == frame]),
        "split_callers": [c["caller"] for c in split_here],
        "split_n_pops": [c["n_pop"] for c in split_here],
        # ── POPULATIONS (size + per-side) ──
        "auth_full_len": len(auth_full),
        "auth_full_ids": [getattr(d, "player_id", None) for d in auth_full],
        "auth_full_short": af_short,
        "auth_full_open": af_open,
        "auth_len": len(auth),
        "auth_ids": [getattr(d, "player_id", None) for d in auth],
        "auth_short": au_short,
        "auth_open": au_open,
        "line_len": len(line),
        "line_ids": [getattr(d, "player_id", None) for d in line],
        "line_short": ln_short,
        "line_open": ln_open,
        "stamp_len": len(stamp),
        "stamp_ids": [getattr(d, "player_id", None) for d in stamp],
        "stamp_short": st_short,
        "stamp_open": st_open,
        "chain_len": len(chain),
        "chain_ids": [getattr(d, "player_id", None) for d in chain],
        "chain_short": ch_short,
        "chain_open": ch_open,
        # ── D2 DATA ──
        "stranded_ids": stranded_pids,
        "stranded_detail": [
            {
                "pid": getattr(d, "player_id", None),
                "role": _def_role_name(d),
                "side": getattr(d, "depth_side", None),
                "tackle_state": _is_tackle_state(d),
                "fullback": _is_fullback(d),
                "excluded_role": _is_excluded_role(d),
            }
            for d in stranded
        ],
        # ── DIAGNOSTIC FLAGS ──
        "d1_total": (
            None if (call is None or call["n_total"] is None
                     or call["n_left"] is None or call["n_right"] is None)
            else (call["n_left"] + call["n_right"]) != call["n_total"]
        ),
        "d1_delta": (
            None if (call is None or call["n_total"] is None
                     or call["n_left"] is None or call["n_right"] is None)
            else call["n_total"] - (call["n_left"] + call["n_right"])
        ),
        "d2_stamp": bool(stranded_pids),
        "d2_count": len(stranded_pids),
        "d3_line": (
            None if (n_short is None or ln_short is None)
            else (n_short != ln_short or n_open != ln_open)
        ),
        "d3_stamp": (
            None if (n_short is None or st_short is None)
            else (n_short != st_short or n_open != st_open)
        ),
        "d4_publisher": (
            len(set(c["n_pop"] for c in split_here)) > 1
            if split_here else None
        ),
    }


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
    pop_state = {}
    wrapped_log = io.StringIO()
    try:
        with contextlib.redirect_stdout(wrapped_log):
            for _ in range(MAX_FRAMES):
                _refresh_ctx()
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
                _track_population(world, rec, pop_state)
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


def _membership_state(world):
    """pid/obj-key -> (fullback, tackle_state, excluded_role) membership."""
    out = {}
    for d in _defenders(world):
        out[_pop_key(d)] = (
            _is_fullback(d), _is_tackle_state(d), _is_excluded_role(d),
        )
    return out


def _track_population(world, rec, prev):
    """Emit one event per body entering/leaving the count's population."""
    cur = _membership_state(world)
    for key, flags in cur.items():
        old = prev.get(key)
        if old is None or old == flags:
            continue
        fb_o, ts_o, ex_o = old
        fb_n, ts_n, ex_n = flags
        reasons = []
        if fb_o != fb_n:
            reasons.append("fullback_role_active")
        if ts_o != ts_n:
            reasons.append("tackle_state_active")
        if ex_o != ex_n:
            reasons.append("defender_role(excluded)")
        in_before = (not fb_o) and (not ts_o) and (not ex_o)
        in_after = (not fb_n) and (not ts_n) and (not ex_n)
        if in_after and not in_before:
            kind = "ENTER"
        elif in_before and not in_after:
            kind = "EXIT"
        else:
            kind = "FLAG"
        body = next(
            (d for d in _defenders(world) if _pop_key(d) == key), None
        )
        _pop_events.append({
            "frame": rec["frame"],
            "phase": rec["phase"],
            "pid": getattr(body, "player_id", None) if body is not None else None,
            "kind": kind,
            "reason": "+".join(reasons) or "flag",
            "role": _def_role_name(body) if body is not None else "-",
            "side": getattr(body, "depth_side", None) if body is not None else None,
        })
    prev.clear()
    prev.update(cur)
# ─────────────────────────────────────────────────────────────────────
# ANALYSIS
# ─────────────────────────────────────────────────────────────────────

def _pop_event_at_or_before(frame):
    """The most recent population-membership event at or before *frame*."""
    best = None
    for ev in _pop_events:
        if ev["frame"] <= frame:
            if best is None or ev["frame"] > best["frame"]:
                best = ev
    return best


def _drop_event_at_or_before(frame, pid):
    """The most recent filter-drop event for *pid* at or before *frame*."""
    best = None
    for ev in _drop_events:
        if ev["pid"] == pid and ev["frame"] <= frame:
            if best is None or ev["frame"] > best["frame"]:
                best = ev
    return best


def _flag_writer_for(pid, attr):
    rec = _flag_writer.get(pid) or {}
    entry = rec.get(attr)
    if not entry:
        return None, None
    return entry[1], entry[0]


def _side_writer_for(pid):
    return _side_writer.get(pid)


def _causal_for_body(pid, frame):
    """(writer_label, reason_label) for a stranded / differing body."""
    out = []
    sw = _side_writer_for(pid)
    if sw is not None:
        out.append(f"depth_side stamped f={sw[0]} by {sw[1]} "
                   f"({_side_name(sw[2])}->{_side_name(sw[3])})")
    tw, tf = _flag_writer_for(pid, "tackle_state")
    if tw is not None:
        out.append(f"tackle_state_active set f={tf} by {tw}")
    fw, ff = _flag_writer_for(pid, "fullback")
    if fw is not None:
        out.append(f"fullback_role_active set f={ff} by {fw}")
    drop = _drop_event_at_or_before(frame, pid)
    if drop is not None:
        out.append(f"excluded from count population f={drop['frame']} "
                   f"({drop['reason']})")
    return "; ".join(out) if out else "(no attributed writer)"


def _d3_auth(rec):
    """Count contract vs the per-side counts of ITS OWN population."""
    n_short, n_open = rec.get("n_short"), rec.get("n_open")
    af_short, af_open = rec.get("auth_full_short"), rec.get("auth_full_open")
    if n_short is None or af_short is None:
        return None
    return (n_short != af_short) or (n_open != af_open)


def build_divergences(frames):
    """One row per (frame, diagnostic) divergence, with cause + writer."""
    rows = []
    for rec in frames:
        frame = rec["frame"]
        n_short, n_open = rec.get("n_short"), rec.get("n_open")

        # ── D1: the count's own total vs its own population ──
        if rec.get("d1_total"):
            pop = _pop_event_at_or_before(frame)
            causal_pid = pop["pid"] if pop else None
            writer = "(none)"
            reason = "(none)"
            if pop:
                if "tackle_state_active" in pop["reason"]:
                    w, f = _flag_writer_for(causal_pid, "tackle_state")
                    writer = w or "tackle_state_gate"
                    reason = f"body left/entered the count population via {pop['kind']} (tackle_state_active)"
                elif "fullback_role_active" in pop["reason"]:
                    w, f = _flag_writer_for(causal_pid, "fullback")
                    writer = w or "fullback_role"
                    reason = f"body left/entered the count population via {pop['kind']} (fullback_role_active)"
                else:
                    writer = "defender_role writer"
                    reason = f"body left/entered the count population via {pop['kind']} ({pop['reason']})"
            rows.append({
                "frame": frame,
                "phase": rec["phase"],
                "bucket": rec["bucket"],
                "diagnostic": "D1_COUNT_TOTAL_LAG",
                "n_short": n_short,
                "n_open": n_open,
                "actual_short": rec.get("auth_full_short"),
                "actual_open": rec.get("auth_full_open"),
                "actual_set": "P_AUTH_FULL (the count's own population)",
                "causal_player": causal_pid,
                "causal_role": pop["role"] if pop else "-",
                "causal_side": _side_name(pop["side"]) if pop else "-",
                "causal_reason": reason,
                "causal_writer": writer,
                "detail": (f"published total {rec.get('call_n_left')}+"
                           f"{rec.get('call_n_right')} != n_total "
                           f"{rec.get('call_n_total')} "
                           f"(delta {rec.get('d1_delta')})"),
            })

        # ── D2: stamped bodies outside the count population ──
        if rec.get("d2_stamp"):
            for det in rec.get("stranded_detail") or []:
                pid = det["pid"]
                excl = []
                if det["tackle_state"]:
                    excl.append("tackle_state_active")
                if det["fullback"]:
                    excl.append("fullback_role_active")
                if det["excluded_role"]:
                    excl.append("defender_role in EXCLUDED_ROLES")
                rows.append({
                    "frame": frame,
                    "phase": rec["phase"],
                    "bucket": rec["bucket"],
                    "diagnostic": "D2_STRANDED_STAMP",
                    "n_short": n_short,
                    "n_open": n_open,
                    "actual_short": rec.get("stamp_short"),
                    "actual_open": rec.get("stamp_open"),
                    "actual_set": "P_STAMP (every body carrying depth_side)",
                    "causal_player": pid,
                    "causal_role": det["role"],
                    "causal_side": _side_name(det["side"]),
                    "causal_reason": "excluded from the count's population by: "
                                     + (", ".join(excl) or "unknown filter"),
                    "causal_writer": _causal_for_body(pid, frame),
                    "detail": "body carries depth_side but is absent from "
                              "P_AUTH_FULL (the count authority's population)",
                })

        # ── D3: observer divergence (the Stage 3.5 symptom) ──
        if rec.get("d3_line"):
            rows.append({
                "frame": frame,
                "phase": rec["phase"],
                "bucket": rec["bucket"],
                "diagnostic": "D3_LINE_OBSERVER",
                "n_short": n_short,
                "n_open": n_open,
                "actual_short": rec.get("line_short"),
                "actual_open": rec.get("line_open"),
                "actual_set": "P_LINE (line-role bodies, includes tackle_state)",
                "causal_player": (rec.get("stranded_ids") or [None])[0],
                "causal_role": "-",
                "causal_side": "-",
                "causal_reason": "observer population is a superset of the "
                                 "count's population",
                "causal_writer": _causal_for_body(
                    (rec.get("stranded_ids") or [None])[0], frame),
                "detail": "count stamp vs line-role body count on each side",
            })

        # ── D3b: count contract vs the stamped population ──
        if rec.get("d3_stamp"):
            rows.append({
                "frame": frame,
                "phase": rec["phase"],
                "bucket": rec["bucket"],
                "diagnostic": "D3B_STAMP_OBSERVER",
                "n_short": n_short,
                "n_open": n_open,
                "actual_short": rec.get("stamp_short"),
                "actual_open": rec.get("stamp_open"),
                "actual_set": "P_STAMP (every body carrying depth_side)",
                "causal_player": (rec.get("stranded_ids") or [None])[0],
                "causal_role": "-",
                "causal_side": "-",
                "causal_reason": "stranded depth_side stamp",
                "causal_writer": _causal_for_body(
                    (rec.get("stranded_ids") or [None])[0], frame),
                "detail": "count stamp vs the full stamped population",
            })

        # ── D3c: count contract vs its OWN population's side counts ──
        if _d3_auth(rec):
            rows.append({
                "frame": frame,
                "phase": rec["phase"],
                "bucket": rec["bucket"],
                "diagnostic": "D3C_AUTH_SELF",
                "n_short": n_short,
                "n_open": n_open,
                "actual_short": rec.get("auth_full_short"),
                "actual_open": rec.get("auth_full_open"),
                "actual_set": "P_AUTH_FULL (the count's own population)",
                "causal_player": None,
                "causal_role": "-",
                "causal_side": "-",
                "causal_reason": "published counts disagree with the sides "
                                 "actually stamped on the count's population",
                "causal_writer": "-",
                "detail": "the count contract is inconsistent with its own "
                          "population's stamping",
            })

        # ── D4: two publishers with different populations this frame ──
        if rec.get("d4_publisher"):
            rows.append({
                "frame": frame,
                "phase": rec["phase"],
                "bucket": rec["bucket"],
                "diagnostic": "D4_PUBLISHER_SPLIT",
                "n_short": n_short,
                "n_open": n_open,
                "actual_short": None,
                "actual_open": None,
                "actual_set": "two publishers",
                "causal_player": None,
                "causal_role": "-",
                "causal_side": "-",
                "causal_reason": "two assign_split_sides calls in one frame "
                                 "with different populations",
                "causal_writer": "+".join(rec.get("split_callers") or []),
                "detail": f"callers={rec.get('split_callers')} "
                          f"n_pops={rec.get('split_n_pops')}",
            })
    return rows


def summarise(frames, divergences):
    """Counts + the first divergence per diagnostic."""
    stats = {
        "frames": len(frames),
        "frames_with_count": 0,
        "d1_n": 0, "d2_n": 0, "d3line_n": 0, "d3stamp_n": 0,
        "d3auth_n": 0, "d4_n": 0,
        "d1_first": None, "d2_first": None, "d3line_first": None,
        "d3stamp_first": None, "d3auth_first": None, "d4_first": None,
        "any_first": None,
        "buckets": {},
    }
    for rec in frames:
        if rec.get("count_left") is not None:
            stats["frames_with_count"] += 1
        b = rec["bucket"]
        stats["buckets"].setdefault(b, {"frames": 0, "d1": 0, "d2": 0,
                                        "d3line": 0, "d3stamp": 0,
                                        "d3auth": 0})
        stats["buckets"][b]["frames"] += 1
        if rec.get("d1_total"):
            stats["d1_n"] += 1
            stats["buckets"][b]["d1"] += 1
            if stats["d1_first"] is None:
                stats["d1_first"] = rec["frame"]
        if rec.get("d2_stamp"):
            stats["d2_n"] += 1
            stats["buckets"][b]["d2"] += 1
            if stats["d2_first"] is None:
                stats["d2_first"] = rec["frame"]
        if rec.get("d3_line"):
            stats["d3line_n"] += 1
            stats["buckets"][b]["d3line"] += 1
            if stats["d3line_first"] is None:
                stats["d3line_first"] = rec["frame"]
        if rec.get("d3_stamp"):
            stats["d3stamp_n"] += 1
            stats["buckets"][b]["d3stamp"] += 1
            if stats["d3stamp_first"] is None:
                stats["d3stamp_first"] = rec["frame"]
        if _d3_auth(rec):
            stats["d3auth_n"] += 1
            stats["buckets"][b]["d3auth"] += 1
            if stats["d3auth_first"] is None:
                stats["d3auth_first"] = rec["frame"]
        if rec.get("d4_publisher"):
            stats["d4_n"] += 1
            if stats["d4_first"] is None:
                stats["d4_first"] = rec["frame"]

    firsts = [f for f in (stats["d1_first"], stats["d2_first"],
                          stats["d3line_first"], stats["d3stamp_first"],
                          stats["d3auth_first"]) if f is not None]
    stats["any_first"] = min(firsts) if firsts else None
    return stats
# ─────────────────────────────────────────────────────────────────────
# VERDICT RUBRIC
# ─────────────────────────────────────────────────────────────────────

def verdict_of(stats):
    """The three-way constitutional verdict, decided by the numbers.

    The two contracts are tested separately:

      COUNT CONTRACT internally correct  <=>  the published total always
          equals the size of the population it was solved for (D1 == 0)
          AND the published per-side counts always equal the sides
          actually stamped on that population (D3C == 0).

      POPULATION CONTRACT correct  <=>  the stamped population is exactly
          the population the count was solved for (D2 == 0).  When D2 > 0
          the population carries bodies the count never counted.

    A divergence in D3 (line / stamp observers) with both contracts
    internally correct is the third case: two correct contracts describing
    different body sets.
    """
    d1 = stats["d1_n"]
    d2 = stats["d2_n"]
    d3a = stats["d3auth_n"]
    d3l = stats["d3line_n"]
    d3s = stats["d3stamp_n"]

    count_self_ok = (d1 == 0) and (d3a == 0)
    pop_equals_count_pop = (d2 == 0)

    if count_self_ok and pop_equals_count_pop:
        if (d3l or d3s):
            return (
                "BOTH CORRECT INDIVIDUALLY BUT OPERATING ON DIFFERENT "
                "BODY SETS",
                [
                    "the count contract is self-consistent (D1=0, D3C=0)",
                    "the stamped population contains no body outside the "
                    "count's population (D2=0)",
                    "yet the observed line population diverges (D3/D3B>0), "
                    "so the two contracts describe different body sets",
                ],
            )
        return ("NO DIVERGENCE OBSERVED",
                ["count contract and population agree on every frame"])

    if count_self_ok and not pop_equals_count_pop:
        return (
            "COUNT CONTRACT CORRECT / POPULATION CONTRACT INCORRECT",
            [
                "the count contract is self-consistent: its published total "
                "always equals its own population (D1=0) and its per-side "
                "counts always match that population's stamps (D3C=0)",
                "the population contract is not: it carries "
                f"{d2} frame(s) of side-stamped bodies absent from the "
                "count's population (D2>0)",
            ],
        )

    if (not count_self_ok) and pop_equals_count_pop:
        return (
            "POPULATION CONTRACT CORRECT / COUNT CONTRACT INCORRECT",
            [
                "the stamped population is exactly the count's population "
                "(D2=0)",
                f"the count contract is violated: D1={d1} frame(s) with a "
                f"published total that disagrees with the population it was "
                f"solved for, D3C={d3a} frame(s) of self-inconsistency",
            ],
        )

    return (
        "MIXED: BOTH CONTRACTS VIOLATED",
        [
            f"count contract: D1={d1} frames of published-total lag, "
            f"D3C={d3a} frames self-inconsistent",
            f"population contract: D2={d2} frames carry side-stamped bodies "
            "outside the count's population",
        ],
    )


def stranded_census(frames):
    """Per-pid tally of the stranded-stamp condition across the run."""
    census = {}
    for rec in frames:
        for det in rec.get("stranded_detail") or []:
            pid = det["pid"]
            row = census.setdefault(pid, {
                "pid": pid,
                "role": det["role"],
                "side": det["side"],
                "frames": 0,
                "first_frame": rec["frame"],
                "last_frame": rec["frame"],
                "tackle_state": det["tackle_state"],
                "fullback": det["fullback"],
                "excluded_role": det["excluded_role"],
            })
            row["frames"] += 1
            row["last_frame"] = rec["frame"]
            row["side"] = det["side"]
    return sorted(census.values(), key=lambda r: -r["frames"])


def find_record(frames, frame):
    for rec in frames:
        if rec["frame"] == frame:
            return rec
    return None


# ─────────────────────────────────────────────────────────────────────
# CSV OUTPUT
# ─────────────────────────────────────────────────────────────────────

POP_CSV = "stage4_count_population.csv"
DIV_CSV = "stage4_divergence.csv"
LOG_FILE = "stage4_engine.log"


def write_population_csv(path, frames):
    import csv
    cols = [
        "frame", "phase", "bucket", "sub", "ruck_y", "short_side",
        "count_left", "count_right", "count_phase", "n_short", "n_open",
        "call_n_total", "call_n_left", "call_n_right", "call_count",
        "split_callers", "split_n_pops",
        "auth_full_len", "auth_full_short", "auth_full_open",
        "auth_len", "auth_short", "auth_open",
        "line_len", "line_short", "line_open",
        "stamp_len", "stamp_short", "stamp_open",
        "chain_len", "chain_short", "chain_open",
        "d1_total", "d1_delta", "d2_stamp", "d2_count",
        "d3_line", "d3_stamp", "d4_publisher",
        "stranded_ids", "auth_full_ids", "line_ids", "stamp_ids",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols)
        wr.writeheader()
        for rec in frames:
            wr.writerow({
                "frame": rec["frame"],
                "phase": rec["phase"],
                "bucket": rec["bucket"],
                "sub": rec["sub"],
                "ruck_y": round(float(rec["ruck_y"]), 3),
                "short_side": _side_name(rec["short_side"]),
                "count_left": rec["count_left"],
                "count_right": rec["count_right"],
                "count_phase": rec["count_phase"],
                "n_short": rec["n_short"],
                "n_open": rec["n_open"],
                "call_n_total": rec["call_n_total"],
                "call_n_left": rec["call_n_left"],
                "call_n_right": rec["call_n_right"],
                "call_count": rec["call_count"],
                "split_callers": "|".join(str(c) for c in
                                          rec["split_callers"]),
                "split_n_pops": "|".join(str(c) for c in
                                         rec["split_n_pops"]),
                "auth_full_len": rec["auth_full_len"],
                "auth_full_short": rec["auth_full_short"],
                "auth_full_open": rec["auth_full_open"],
                "auth_len": rec["auth_len"],
                "auth_short": rec["auth_short"],
                "auth_open": rec["auth_open"],
                "line_len": rec["line_len"],
                "line_short": rec["line_short"],
                "line_open": rec["line_open"],
                "stamp_len": rec["stamp_len"],
                "stamp_short": rec["stamp_short"],
                "stamp_open": rec["stamp_open"],
                "chain_len": rec["chain_len"],
                "chain_short": rec["chain_short"],
                "chain_open": rec["chain_open"],
                "d1_total": rec["d1_total"],
                "d1_delta": rec["d1_delta"],
                "d2_stamp": rec["d2_stamp"],
                "d2_count": rec["d2_count"],
                "d3_line": rec["d3_line"],
                "d3_stamp": rec["d3_stamp"],
                "d4_publisher": rec["d4_publisher"],
                "stranded_ids": "|".join(str(p) for p in rec["stranded_ids"]),
                "auth_full_ids": "|".join(str(p) for p in rec["auth_full_ids"]),
                "line_ids": "|".join(str(p) for p in rec["line_ids"]),
                "stamp_ids": "|".join(str(p) for p in rec["stamp_ids"]),
            })


def write_divergence_csv(path, rows):
    import csv
    cols = [
        "frame", "phase", "bucket", "diagnostic",
        "n_short", "n_open", "actual_short", "actual_open", "actual_set",
        "causal_player", "causal_role", "causal_side",
        "causal_reason", "causal_writer", "detail",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: r.get(k) for k in cols})
# ─────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────

def _fmt(v):
    if v is None:
        return "-"
    try:
        return f"{float(v):.3f}"
    except (TypeError, ValueError):
        return str(v)


def _ids(v):
    if not v:
        return "-"
    return "|".join(str(x) for x in v)


def _first_rows(divergences, diagnostic):
    for r in divergences:
        if r["diagnostic"] == diagnostic:
            return r
    return None


def print_report(result, frames, divergences, stats, verdict, reasons,
                 census):
    world = result["world"]
    print("=" * 78)
    print("STAGE 4 — COUNT CONTRACT vs POPULATION REALITY")
    print("=" * 78)

    # ── 0. EXECUTION SUMMARY ──
    print("\n" + "-" * 78)
    print("0. EXECUTION SUMMARY")
    print("-" * 78)
    print(f"  OPEN_PLAY frames recorded      = {len(frames)}")
    print(f"  frames carrying a count stamp  = {stats['frames_with_count']}")
    print(f"  frame_count at end             = "
          f"{getattr(world, 'frame_count', None)}")
    print("  phase counts:")
    for k, v in sorted(result["phase_counts"].items(),
                       key=lambda kv: -kv[1]):
        print(f"    {k:<26} {v}")

    # ── 1. THE COUNT AUTHORITY AND ITS TWO PUBLISHERS ──
    print("\n" + "-" * 78)
    print("1. THE COUNT AUTHORITY AND ITS PUBLISHERS")
    print("-" * 78)
    print(f"  decide_side_counts calls       = {len(_count_calls)}")
    print(f"  assign_split_sides calls       = {len(_split_calls)}")
    by_caller = {}
    for c in _split_calls:
        by_caller[c["caller"]] = by_caller.get(c["caller"], 0) + 1
    for k, v in sorted(by_caller.items(), key=lambda kv: -kv[1]):
        print(f"    caller {k:<32} {v} call(s)")
    n_totals = {}
    for c in _count_calls:
        n_totals[c["n_total"]] = n_totals.get(c["n_total"], 0) + 1
    print("  n_total values handed to the count authority:")
    for k, v in sorted(n_totals.items(),
                       key=lambda kv: (-1 if kv[0] is None else kv[0])):
        print(f"    n_total={k:<6} {v} call(s)")
    pops = {}
    for c in _split_calls:
        pops[c["n_pop"]] = pops.get(c["n_pop"], 0) + 1
    print("  stamping-call population sizes (len of the list handed):")
    for k, v in sorted(pops.items(), key=lambda kv: -kv[1]):
        print(f"    n_pop={k:<6} {v} call(s)")
    print(f"  stamped-body filter drops      = {len(_drop_events)}")
    reasons = {}
    for e in _drop_events:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"    drop reason {k:<28} {v}")
    print(f"  population-membership events   = {len(_pop_events)}")
    kinds = {}
    for e in _pop_events:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"    {k:<8} {v}")

    # ── 2. DIAGNOSTIC TOTALS ──
    print("\n" + "-" * 78)
    print("2. DIAGNOSTIC TOTALS")
    print("-" * 78)
    print(f"  D1 COUNT TOTAL LAG       : {stats['d1_n']:>5} frame(s)"
          f"   first=f{stats['d1_first']}")
    print(f"  D2 STRANDED STAMP        : {stats['d2_n']:>5} frame(s)"
          f"   first=f{stats['d2_first']}")
    print(f"  D3  LINE OBSERVER        : {stats['d3line_n']:>5} frame(s)"
          f"   first=f{stats['d3line_first']}")
    print(f"  D3B STAMP OBSERVER       : {stats['d3stamp_n']:>5} frame(s)"
          f"   first=f{stats['d3stamp_first']}")
    print(f"  D3C AUTH SELF-CONSISTENT : {stats['d3auth_n']:>5} frame(s)"
          f"   first=f{stats['d3auth_first']}")
    print(f"  D4 PUBLISHER SPLIT       : {stats['d4_n']:>5} frame(s)"
          f"   first=f{stats['d4_first']}")
    print(f"\n  FIRST divergence of ANY kind : f{stats['any_first']}")
    print(f"\n  {'bucket':<12}{'frames':>8}{'D1':>7}{'D2':>7}"
          f"{'D3':>7}{'D3B':>7}{'D3C':>7}")
    for b, rec in sorted(stats["buckets"].items(),
                         key=lambda kv: -kv[1]["frames"]):
        print(f"  {b:<12}{rec['frames']:>8}{rec['d1']:>7}{rec['d2']:>7}"
              f"{rec['d3line']:>7}{rec['d3stamp']:>7}{rec['d3auth']:>7}")

    # ── 3. FIRST DIVERGENCE — FIVE POPULATIONS SIDE BY SIDE ──
    print("\n" + "-" * 78)
    print("3. FIRST DIVERGENCE — ALL POPULATIONS AT THAT FRAME")
    print("-" * 78)
    f0 = stats["any_first"]
    r0 = find_record(frames, f0) if f0 is not None else None
    if r0 is None:
        print("  (no divergence observed — count and population agree)")
    else:
        print(f"  frame {r0['frame']}   phase={r0['phase']}   "
              f"sub={r0['sub']}   bucket={r0['bucket']}")
        print(f"  short_side = {_side_name(r0['short_side'])}   "
              f"ruck_y = {_fmt(r0['ruck_y'])}")
        print(f"  count stamp (published)  : n_left={r0['count_left']} "
              f"n_right={r0['count_right']}  "
              f"n_short={r0['n_short']} n_open={r0['n_open']}")
        print(f"  count solved for n_total : {r0['call_n_total']} "
              f"-> ({r0['call_n_left']}, {r0['call_n_right']})")
        print(f"  publishers this frame    : {r0['split_callers']} "
              f"n_pops={r0['split_n_pops']}")
        print("")
        print(f"  {'population':<14}{'size':>6}{'short':>7}{'open':>7}"
              f"   ids")
        for label, ln, sh, op, ids in (
            ("P_AUTH_FULL", r0["auth_full_len"], r0["auth_full_short"],
             r0["auth_full_open"], r0["auth_full_ids"]),
            ("P_AUTH", r0["auth_len"], r0["auth_short"], r0["auth_open"],
             r0["auth_ids"]),
            ("P_LINE", r0["line_len"], r0["line_short"], r0["line_open"],
             r0["line_ids"]),
            ("P_STAMP", r0["stamp_len"], r0["stamp_short"],
             r0["stamp_open"], r0["stamp_ids"]),
            ("P_CHAIN", r0["chain_len"], r0["chain_short"],
             r0["chain_open"], r0["chain_ids"]),
        ):
            print(f"  {label:<14}{ln:>6}{str(sh):>7}{str(op):>7}   "
                  f"{_ids(ids)}")
        if r0["stranded_detail"]:
            print("\n  STRANDED (depth_side stamp outside the count's "
                  "population):")
            for det in r0["stranded_detail"]:
                print(f"    pid={det['pid']:<5} role={det['role']:<14} "
                      f"side={_side_name(det['side']):<5} "
                      f"tackle_state={det['tackle_state']} "
                      f"fullback={det['fullback']} "
                      f"excluded_role={det['excluded_role']}")

    # ── 4. FIRST DIVERGENCE PER DIAGNOSTIC ──
    print("\n" + "-" * 78)
    print("4. EVERY DIVERGENCE  (frame, phase, n_short, n_open, "
          "actual_short, actual_open, player, writer)")
    print("-" * 78)
    if not divergences:
        print("  (none)")
    else:
        print(f"  {'diag':<22}{'frame':>7}{'phase':<16}"
              f"{'ns/no':>9}{'act_s/o':>9}  player / writer")
        for r in divergences:
            ns = f"{r['n_short']}/{r['n_open']}"
            ac = f"{r['actual_short']}/{r['actual_open']}"
            print(f"  {r['diagnostic']:<22}{r['frame']:>7}"
                  f"{str(r['phase']):<16}{ns:>9}{ac:>9}  "
                  f"pid={r['causal_player']} "
                  f"role={r['causal_role']}")
            print(f"  {'':<22}{'':>7}{'':<16}{'':>9}{'':>9}  "
                  f"writer: {r['causal_writer']}")
            if r.get("detail"):
                print(f"  {'':<22}{'':>7}{'':<16}{'':>9}{'':>9}  "
                      f"{r['detail']}")
        print(f"\n  total divergence rows = {len(divergences)}")

    # ── 5. STRANDED-BODY CENSUS ──
    print("\n" + "-" * 78)
    print("5. STRANDED-BODY CENSUS  (bodies carrying depth_side but absent "
          "from the count's population)")
    print("-" * 78)
    if not census:
        print("  (no stranded body on any frame)")
    else:
        print(f"  {'pid':<6}{'role':<15}{'side':>6}{'frames':>8}"
              f"{'first':>8}{'last':>8}  flags")
        for row in census[:20]:
            flags = []
            if row["tackle_state"]:
                flags.append("tackle_state")
            if row["fullback"]:
                flags.append("fullback")
            if row["excluded_role"]:
                flags.append("excluded_role")
            print(f"  {str(row['pid']):<6}{str(row['role']):<15}"
                  f"{_side_name(row['side']):>6}{row['frames']:>8}"
                  f"{row['first_frame']:>8}{row['last_frame']:>8}  "
                  f"{'+'.join(flags) or '-'}")
        if len(census) > 20:
            print(f"  ... {len(census) - 20} more")

    # ── 6. VERDICT ──
    print("\n" + "=" * 78)
    print("6. CONSTITUTIONAL VERDICT")
    print("=" * 78)
    for line in reasons:
        print(f"  - {line}")
    print("")
    print(f"  VERDICT: {verdict}")
    print("")


def main():
    result = run_sequence(with_wrappers=True)
    frames = result["frames"]
    if not frames:
        print("RESULT: no OPEN_PLAY frames recorded — nothing to measure.")
        return 0

    divergences = build_divergences(frames)
    stats = summarise(frames, divergences)
    verdict, reasons = verdict_of(stats)
    census = stranded_census(frames)

    write_population_csv(POP_CSV, frames)
    write_divergence_csv(DIV_CSV, divergences)

    print_report(result, frames, divergences, stats, verdict, reasons,
                 census)

    with open(LOG_FILE, "w", encoding="utf-8") as fh:
        fh.write(result["engine_log"])

    print(f"wrote: {POP_CSV}, {DIV_CSV}, {LOG_FILE}")
    return 0


if __name__ == "__main__":
    _code = 0
    try:
        _code = main()
    finally:
        unwrap_all()
        pygame.quit()
    sys.exit(_code)
