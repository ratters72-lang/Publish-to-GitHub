"""game/debug/tes_xy_write_trace.py — X/Y WRITE TRACE for TES-drive line bodies.

PURPOSE
-------
Diagnostic (NOT behaviour).  Records EVERY write to a ``Player``'s ``x`` / ``y``
position while ALL of these hold:

  * the trace is INSTALLED and ARMED, and
  * the owner carries ``defender_role in {DEF_LINE_READY, DEF_CHASE}``, and
  * the live phase is a TES drive chamber sub-state
    (``TACKLE_CONTACT`` / ``TACKLE_DRIVE`` / ``TACKLE_DOMINANCE``).

Each record is ATTRIBUTED to the authority that made the write: the first
stack frame outside ``player/player.py`` and this module, keyed by
``(basename, function)``.  That is how the tracer answers "which authority is
still active inside the drive chamber".

HOW IT IS INSTALLED
-------------------
``install()`` wraps ``Player.__setattr__`` ONCE.  In CPython every attribute
assignment (``p.x = v`` and ``p.y = v``) dispatches through
``type(p).__setattr__``, and the default ``object.__setattr__`` is what then
routes ``x`` to its property setter.  Wrapping ``__setattr__`` therefore
intercepts BOTH axes at a single point, including augmented writes
(``p.x += …``) and the integrator's ``self.x += vx * dt``.

``uninstall()`` restores the original ``__setattr__``.  When not installed
(the production default) there is ZERO overhead — nothing is patched.

Gates:
    module DEBUG_TES_XY_TRACE (default True)
    env    RLM_TES_XY_TRACE=0 -> disabled
    install() -> patched (recording off)
    arm()     -> recording on; disarm() -> recording off (still patched)
"""

from __future__ import annotations

import os
import posixpath
import sys

from player.player import Player

# ── Gates ────────────────────────────────────────────────────────────
DEBUG_TES_XY_TRACE: bool = (
    os.environ.get("RLM_TES_XY_TRACE", "1") not in ("", "0")
)

# ── Scope ────────────────────────────────────────────────────────────
TRACED_ATTRS: frozenset[str] = frozenset({"x", "y"})
DRIVE_ROLES: frozenset[str] = frozenset({"DEF_LINE_READY", "DEF_CHASE"})
DRIVE_PHASES: frozenset[str] = frozenset({
    "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE",
})

# Frame-walk budget (guards against runaway stacks).
_MAX_FRAMES: int = 16

# Record cap (guards memory on a long capture run).
MAX_RECORDS: int = 200_000

# ── Module state ─────────────────────────────────────────────────────
_installed: bool = False
_recording: bool = False
_orig_setattr = None
_records: list[dict] = []

# World fallback: a probe may run bodies whose ``_world_ref`` was never
# stamped (stub worlds, partially-booted actors).  The caller can point the
# tracer at the live world so phase scoping still works.
_fallback_world = None

_PLAYER_CORE_SUFFIX: str = "player/player.py"


def set_fallback_world(world) -> None:
    """Point the tracer at *world* for bodies without ``_world_ref``."""
    global _fallback_world
    _fallback_world = world


def _owner_world(player):
    """The world that owns *player* (its ref, else the fallback)."""
    return getattr(player, "_world_ref", None) or _fallback_world


# ─────────────────────────────────────────────────────────────────────
# FRAME ATTRIBUTION
# ─────────────────────────────────────────────────────────────────────

def _norm(path: str) -> str:
    return str(path).replace("\\", "/")


def _is_this_module(path: str) -> bool:
    return path.endswith("debug/tes_xy_write_trace.py")


def _is_player_core(path: str) -> bool:
    return path.endswith(_PLAYER_CORE_SUFFIX)


def _attribute() -> tuple[str, str, int]:
    """Return (inner_site, outer_authority, outer_line) for this write.

    ``inner_site``  — the first frame outside this module (e.g.
                      ``role_depth_glide.py:enforce_tackle_begin_glide``);
                      may still be ``player/player.py`` (the integrator).
    ``outer_authority`` — the first frame outside this module AND outside
                      ``player/player.py``: the authority that owns the
                      movement (e.g. ``defence_manager.py:update_tes_drive``).
    """
    inner = "?"
    outer = "?"
    outer_line = 0
    try:
        f = sys._getframe(2)
    except ValueError:
        return inner, outer, outer_line
    depth = 0
    while f is not None and depth < _MAX_FRAMES:
        path = _norm(f.f_code.co_filename)
        base = posixpath.basename(path)
        name = f.f_code.co_name
        if not _is_this_module(path):
            if inner == "?":
                inner = f"{base}:{name}"
            if not _is_player_core(path):
                outer = f"{base}:{name}"
                outer_line = int(f.f_lineno)
                break
        f = f.f_back
        depth += 1
    return inner, outer, outer_line


# ─────────────────────────────────────────────────────────────────────
# PHASE / SCOPE RESOLUTION
# ─────────────────────────────────────────────────────────────────────

def _phase_label(world) -> str:
    """Best-available upper-case phase label for *world* (or "")."""
    if world is None:
        return ""
    ope = getattr(world, "open_play_engine", None)
    ph = getattr(ope, "_phase", None) if ope is not None else None
    if ph:
        return str(ph).upper()
    lab = getattr(world, "_ope_phase_label", None)
    if lab:
        return str(lab).upper()
    sub = getattr(world, "sub_state", None)
    if sub is not None:
        val = getattr(sub, "value", sub)
        return str(val).upper()
    return ""


def _role_name(player) -> str:
    role = getattr(player, "defender_role", None)
    return getattr(role, "name", None) or (str(role) if role is not None else "")


def _tes_tackler_ids(world) -> set:
    """The registered TES tackler ids (primary + joiners + secondary)."""
    if world is None:
        return set()
    ids = set(getattr(world, "tackler_ids", None) or ())
    prim = getattr(world, "primary_tackler_id", None)
    if prim is not None:
        ids.add(prim)
    for sid in (getattr(world, "secondary_tacklers", None) or ()):
        ids.add(sid)
    return ids


def _in_scope(player) -> tuple[bool, str, object]:
    """True when *player* is a drive body the trace should record."""
    if _role_name(player) not in DRIVE_ROLES:
        return False, "", None
    world = _owner_world(player)
    phase = _phase_label(world)
    if phase not in DRIVE_PHASES:
        return False, phase, world
    if world is not None and player is getattr(world, "ball_carrier", None):
        return False, phase, world
    return True, phase, world


# ─────────────────────────────────────────────────────────────────────
# THE SETTER WRAPPER
# ─────────────────────────────────────────────────────────────────────

def _record_write(player, attr: str, value) -> None:
    in_scope, phase, world = _in_scope(player)
    if not in_scope:
        return
    try:
        old = getattr(player, attr)
        old_f = float(old)
    except (TypeError, ValueError, AttributeError):
        old_f = None
    try:
        new_f = float(value)
    except (TypeError, ValueError):
        return
    inner, outer, line = _attribute()
    pid = getattr(player, "player_id", None)
    is_tackler = pid is not None and pid in _tes_tackler_ids(world)
    if len(_records) < MAX_RECORDS:
        _records.append({
            "frame": int(getattr(world, "frame_count", 0) or 0),
            "phase": phase,
            "player_id": pid,
            "role": _role_name(player),
            "depth_role": getattr(player, "depth_role", None) or "-",
            "axis": attr.upper(),
            "old": old_f,
            "new": new_f,
            "delta": (None if old_f is None else new_f - old_f),
            "inner": inner,
            "authority": outer,
            "line": line,
            # ── attribution disambiguators (read from the live world) ──
            # A body may still carry defender_role == DEF_LINE_READY while
            # being a registered TES tackler / attached joiner; these two
            # flags separate a genuine line body from a tackle participant.
            "tes_tackler": bool(is_tackler),
            "attached": bool(getattr(player, "tes_attached", False)),
        })


def _traced_setattr(self, name, value):
    if name in TRACED_ATTRS and _recording:
        try:
            _record_write(self, name, value)
        except Exception:
            pass
    return _orig_setattr(self, name, value)


# ─────────────────────────────────────────────────────────────────────
# INSTALL / ARM / RESET
# ─────────────────────────────────────────────────────────────────────

def install() -> bool:
    """Patch ``Player.__setattr__`` once.  Returns True when patched."""
    global _installed, _orig_setattr
    if not DEBUG_TES_XY_TRACE:
        return False
    if _installed:
        return True
    _orig_setattr = Player.__setattr__
    Player.__setattr__ = _traced_setattr
    _installed = True
    return True


def uninstall() -> None:
    """Restore the original ``Player.__setattr__`` (recording force-off)."""
    global _installed, _recording
    if not _installed:
        return
    Player.__setattr__ = _orig_setattr
    _installed = False
    _recording = False


def arm() -> None:
    """Begin recording (auto-installs if needed)."""
    global _recording
    install()
    _recording = True


def disarm() -> None:
    """Stop recording (stays patched)."""
    global _recording
    _recording = False


def is_installed() -> bool:
    return _installed


def is_recording() -> bool:
    return _recording


def reset() -> None:
    """Drop every captured record."""
    _records.clear()


# ─────────────────────────────────────────────────────────────────────
# ACCESSORS
# ─────────────────────────────────────────────────────────────────────

def records() -> list[dict]:
    """The captured records (live list; copy if you will mutate)."""
    return _records


def record_count() -> int:
    return len(_records)


def summarise() -> dict:
    """Aggregate the captured records for reporting.

    Returns a dict with:
      ``total``            — number of writes,
      ``by_axis``          — {X: n, Y: n},
      ``by_authority``     — {authority: {X: n, Y: n}},
      ``by_authority_axis``— {authority: n} over BOTH axes,
      ``by_role``          — {role: n},
      ``by_phase``         — {phase: n},
      ``pairs``            — {(authority, inner): n},
      ``authorities``      — sorted distinct authorities.
    """
    by_axis: dict[str, int] = {}
    by_authority: dict[str, dict[str, int]] = {}
    by_authority_axis: dict[str, int] = {}
    by_role: dict[str, int] = {}
    by_phase: dict[str, int] = {}
    pairs: dict[tuple[str, str], int] = {}
    # {authority: {"line": n, "tackler": n, "attached": n}}
    by_scope: dict[str, dict[str, int]] = {}
    lines: dict[str, set] = {}
    ids: dict[str, set] = {}

    for r in _records:
        ax = r["axis"]
        auth = r["authority"]
        by_axis[ax] = by_axis.get(ax, 0) + 1
        slot = by_authority.setdefault(auth, {})
        slot[ax] = slot.get(ax, 0) + 1
        by_authority_axis[auth] = by_authority_axis.get(auth, 0) + 1
        by_role[r["role"]] = by_role.get(r["role"], 0) + 1
        by_phase[r["phase"]] = by_phase.get(r["phase"], 0) + 1
        key = (auth, r["inner"])
        pairs[key] = pairs.get(key, 0) + 1
        sc = by_scope.setdefault(auth, {"line": 0, "tackler": 0, "attached": 0})
        if r.get("tes_tackler"):
            sc["tackler"] += 1
        else:
            sc["line"] += 1
        if r.get("attached"):
            sc["attached"] += 1
        lines.setdefault(auth, set()).add(r.get("line", 0))
        if r.get("player_id") is not None:
            ids.setdefault(auth, set()).add(r["player_id"])

    return {
        "total": len(_records),
        "by_axis": by_axis,
        "by_authority": by_authority,
        "by_authority_axis": by_authority_axis,
        "by_role": by_role,
        "by_phase": by_phase,
        "pairs": pairs,
        "by_scope": by_scope,
        "lines": {k: sorted(v) for k, v in lines.items()},
        "ids": {k: sorted(v) for k, v in ids.items()},
        "authorities": sorted(by_authority_axis.keys(),
                              key=lambda k: -by_authority_axis[k]),
    }
