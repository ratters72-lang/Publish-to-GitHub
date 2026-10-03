"""game/defence/defence_lvl_1/writer_priority_bands.py
Lateral + depth writer-conflict priority bands (writer-conflict jitter kill).

PROBLEM
-------
Every frame many independent subsystems write ``target_x`` / ``target_y`` for
the same defender (spacing, width, structured close, MMDL, retreat, ruck pin,
seed-hold, pre-tackle, fullback, marker, tackle-state...).  The write path is
plain last-writer-wins, so when two writers disagree by centimetres the
target seesaws frame-to-frame.  ``freeze_audit`` already *detects* this
(``writer_conflicts`` = >1 entry in ``player.movement_writer``) and
``movement_contract_audit`` labels it a frame-order conflict, but nothing
resolves it.

LAW (this module)
-----------------
Introduce LATERAL and DEPTH priority bands.  For each defender, each frame,
each axis independently:

  1. Every writer registers a PROPOSAL: (source, priority, value).
  2. The DOMINANT proposal for the axis is the STRONGEST movement signal:
     highest priority, ties broken by the largest displacement from the
     frame-start position (the writer demanding the most movement).
  3. Every other proposal whose value lies WITHIN the band of the dominant
     is IGNORED — it is indistinguishable from the dominant signal and only
     adds jitter:
         lateral (Y): |dy| < LATERAL_BAND_M (0.3 m) -> ignored
         depth   (X): |dx| < DEPTH_BAND_M   (0.5 m) -> ignored
  4. Only the surviving (dominant) signal is written.

Per-axis (not atomic) by design: this codebase enforces a hard
"spacing owns Y / width owns X" split (``SpacingGuard`` / ``WidthGuard``).
Arbitrating per axis preserves it — a depth writer can never discard a
legitimate lateral correction.

CONTRACTS
---------
  - Writes ONLY ``target_x`` / ``target_y`` (and its own bookkeeping stamps).
  - Never mutates roles, movement_state, spacing/width/anchor authority.
  - Exempt (never banded): frozen / freeze-timer bodies, committed tacklers,
    TES-attached bodies, pre-tackle COMMIT/PLANTED owners, tackle-state
    holders — their targets must snap.
  - Gated by ``tuners.writer_priority_bands_enabled`` (default True).
  - Determinism: identical proposals -> identical winner.
"""

from __future__ import annotations

from contextlib import contextmanager

# FIELD BOUND ON THE RESOLVED WINNER (2026-09-25)
# The band resolver is the LAST writer of `target_x` / `target_y` on every
# band-eligible body: it copies the winning proposal's value straight onto
# the player.  A proposal captured from an upstream writer can lie OUTSIDE
# the playable field (measured live: `mmdl_CHASE` authored
# `target_x == 120.0595` against a `FIELD_MAX_X` of 120.0), and
# `Player._clamp_position` then makes that target UNREACHABLE -- the body
# presses the boundary, the clamp cancels its displacement, and it sits
# still while an authority keeps commanding a forward approach.
#
# Clamping here bounds EVERY band winner on BOTH axes in one place, rather
# than at each of the dozens of upstream proposal writers.  It is the same
# reachability argument as `marker_authority` / `open_play_engine`, and the
# clamp is the identity for an in-field value, so no in-field frame moves.
# `target_field_bound` is a pure leaf (it imports only the field bounds), so
# this cannot create an import cycle.
from game.defence.defence_lvl_1.target_field_bound import (
    clamp_target_x,
    clamp_target_y,
)

# ── BANDS (metres) ───────────────────────────────────────────────────
# Lateral band (Y / width axis): writers within this of the dominant Y are
# ignored.  Depth band (X / downfield axis): same for X.
LATERAL_BAND_M: float = 0.3
DEPTH_BAND_M: float = 0.5

# ── WINNER-FLIP HYSTERESIS BANDS (metres) ───────────────────────────
# Once a source wins an axis for a defender, it RETAINS the axis while a
# challenger's value lies within this band of the incumbent's value AND the
# challenger does not outrank it by a full priority tier.  This kills the
# frame-to-frame band flip — two near-equivalent writers trading the axis
# winner each frame (the lateral/depth "band flip" jitter).  A genuinely
# different target (> flip band) or a strictly higher-priority writer hands
# the axis over immediately.
LATERAL_FLIP_BAND_M: float = 0.15
DEPTH_FLIP_BAND_M: float = 0.25

# ── AXES ─────────────────────────────────────────────────────────────
AXIS_X: str = "x"
AXIS_Y: str = "y"
_AXIS_ATTR = {AXIS_X: "target_x", AXIS_Y: "target_y"}
_AXIS_BAND = {AXIS_X: DEPTH_BAND_M, AXIS_Y: LATERAL_BAND_M}

# ── PRIORITY LADDER (higher = stronger movement signal) ──────────────
# Mirrors and extends slot_arbitration.PRIORITY_* so the codebase keeps ONE
# ladder.  Hard movement intent (commit / attach) outranks geometry.
PRIORITY_FALLBACK: int = 10
PRIORITY_SEED_HOLD: int = 800      # explicit stale-seed lockdown: wins
PRIORITY_RETREAT: int = 30
PRIORITY_ROLE_MOVEMENT: int = 40
PRIORITY_SPACING: int = 50         # Y authority
PRIORITY_WIDTH: int = 50           # X authority
PRIORITY_MMDL: int = 60
PRIORITY_PIN: int = 200            # ruck-adjacent final authority
PRIORITY_FULLBACK: int = 220
PRIORITY_MARKER: int = 300
PRIORITY_TACKLE_STATE: int = 900
PRIORITY_COMMIT: int = 1000        # tackler commit / pre-tackle / pursuit

# ``target_source`` prefix -> priority.  First match wins (longest/most
# specific prefixes are listed first).  Every defender's net per-stage
# ``target_source`` is mapped through this table, so a captured stage is
# attributed the strength of the writer that actually owned it.
_SOURCE_PRIORITY: tuple[tuple[str, int], ...] = (
    ("tackler_commit", PRIORITY_COMMIT),
    ("pursuit_commit_override", PRIORITY_COMMIT),
    ("pre_tackle", PRIORITY_COMMIT),
    ("pretackle", PRIORITY_COMMIT),
    ("pta_", PRIORITY_COMMIT),
    ("catch_x_authority", PRIORITY_COMMIT),
    ("line_break_static_guard", PRIORITY_COMMIT),
    # POST-MISS PURSUIT (2026-09-26): the beaten body's chase entitlement.
    # Registered at the SAME tier as the other live-pursuit recovery writers
    # (``line_break_static_guard`` / ``freeze_expiry_pursuit``): all three
    # author a point on the live carrier after a genuine line break, and the
    # probe measured that a chase point at this tier wins EVERY contest it
    # enters (``OVERRIDDEN_BY == 0`` over 77,681 post-miss body-frames).  No
    # priority tier is reordered by this entry; it only stops a NEW stamp
    # from being attributed the weakest fallback tier.
    ("post_miss_pursuit", PRIORITY_COMMIT),
    ("tackle_state", PRIORITY_TACKLE_STATE),

    ("drive_end_authoritative", PRIORITY_TACKLE_STATE),
    ("seed_hold", PRIORITY_SEED_HOLD),
    ("marker", PRIORITY_MARKER),
    ("fullback", PRIORITY_FULLBACK),
    ("ruck_pin", PRIORITY_PIN),
    ("mmdl_", PRIORITY_MMDL),
    ("spacing", PRIORITY_SPACING),
    ("width", PRIORITY_WIDTH),
    ("structured_close", PRIORITY_ROLE_MOVEMENT),
    ("structured_open_play", PRIORITY_ROLE_MOVEMENT),
    ("unified_close", PRIORITY_ROLE_MOVEMENT),
    ("decision_", PRIORITY_ROLE_MOVEMENT),
    ("role_movement", PRIORITY_ROLE_MOVEMENT),
    ("role_governs", PRIORITY_ROLE_MOVEMENT),
    ("retreat_", PRIORITY_RETREAT),
    ("pursuit", PRIORITY_ROLE_MOVEMENT),
    ("line_break", PRIORITY_ROLE_MOVEMENT),
    ("fallback", PRIORITY_FALLBACK),
)

_STATE_KEY = "_writer_priority_bands"


# ── FEATURE GATE + BANDS ─────────────────────────────────────────────

def writer_priority_bands_enabled(tuners, world=None) -> bool:
    """True when the band arbiter is armed.

    ``world.writer_priority_bands_enabled`` overrides the tuner (used by
    verifiers and canary runs).  Defaults to True so the jitter fix is live;
    set either to False for byte-identical legacy behaviour.
    """
    if world is not None:
        override = getattr(world, "writer_priority_bands_enabled", None)
        if override is not None:
            return bool(override)
    if tuners is None:
        return True
    return bool(getattr(tuners, "writer_priority_bands_enabled", True))


def lateral_band_m(world=None) -> float:
    """Lateral (Y) band in metres.  ``world.lateral_band_m`` overrides."""
    if world is None:
        return LATERAL_BAND_M
    return float(getattr(world, "lateral_band_m", LATERAL_BAND_M))


def depth_band_m(world=None) -> float:
    """Depth (X) band in metres.  ``world.depth_band_m`` overrides."""
    if world is None:
        return DEPTH_BAND_M
    return float(getattr(world, "depth_band_m", DEPTH_BAND_M))


def lateral_flip_band_m(world=None) -> float:
    """Lateral (Y) winner-flip hysteresis band in metres."""
    if world is None:
        return LATERAL_FLIP_BAND_M
    return float(getattr(world, "lateral_flip_band_m", LATERAL_FLIP_BAND_M))


def depth_flip_band_m(world=None) -> float:
    """Depth (X) winner-flip hysteresis band in metres."""
    if world is None:
        return DEPTH_FLIP_BAND_M
    return float(getattr(world, "depth_flip_band_m", DEPTH_FLIP_BAND_M))


def _flip_band_for(axis: str, world) -> float:
    """Winner-flip hysteresis band for *axis*."""
    if axis == AXIS_X:
        return depth_flip_band_m(world)
    return lateral_flip_band_m(world)


def priority_for_source(source) -> int:
    """Map a ``target_source`` stamp to its priority tier."""
    if not source:
        return PRIORITY_FALLBACK
    name = str(source).lower()
    for prefix, priority in _SOURCE_PRIORITY:
        if name.startswith(prefix) or prefix in name:
            return priority
    return PRIORITY_FALLBACK


# ── FRAME STATE ──────────────────────────────────────────────────────

def _state(world) -> dict:
    state = getattr(world, _STATE_KEY, None)
    if state is None:
        state = {}
        setattr(world, _STATE_KEY, state)
    return state


def begin_frame(world, defenders) -> None:
    """Open a capture frame: clear proposals, seed the frame-start reference.

    The frame-start position is the tie-break reference for "strongest
    movement signal" (largest displacement from where the body began).
    """
    state = _state(world)
    state["frame"] = getattr(world, "frame_count", None)
    state["proposals"] = {}      # id(d) -> {axis: {source: (priority, value)}}
    state["frame_start"] = {}    # id(d) -> (x, y)
    state["suppressed"] = []     # reporting rows
    state["winners"] = {}        # id(d) -> {axis: (priority, source, value)}
    # PERSISTED across frames: id(d) -> {axis: source} — the previous frame's
    # per-axis winner, used by the winner-flip hysteresis so a near-equivalent
    # challenger cannot steal an axis frame-to-frame (the band flip).
    state.setdefault("last_winners", {})
    for d in defenders:
        if d is None:
            continue
        state["frame_start"][id(d)] = (
            float(getattr(d, "x", 0.0) or 0.0),
            float(getattr(d, "y", 0.0) or 0.0),
        )


def note(world, d, axis: str, value, source, priority=None) -> None:
    """Register a proposal for *d* on *axis*.

    Later proposals from the SAME source replace the earlier value (the
    source's net write for the frame), keeping its priority.
    """
    if d is None or axis not in _AXIS_ATTR:
        return
    try:
        val = float(value)
    except (TypeError, ValueError):
        return
    if val != val:  # NaN guard
        return
    state = _state(world)
    proposals = state.get("proposals")
    if proposals is None:
        return
    per_axis = proposals.setdefault(id(d), {})
    by_source = per_axis.setdefault(axis, {})
    prio = (
        int(priority) if priority is not None
        else priority_for_source(source)
    )
    prev = by_source.get(str(source))
    if prev is None or prio >= prev[0]:
        by_source[str(source)] = (prio, val)


@contextmanager
def capture(world, defenders, source: str, priority=None):
    """Capture every net target change a writer stage makes.

    Wrap a writer stage: on exit, any defender whose ``target_x`` /
    ``target_y`` moved is registered as a proposal attributed to *source*
    (priority derived from ``d.target_source`` when *priority* is None).
    """
    before = {}
    for d in defenders:
        if d is None:
            continue
        before[id(d)] = (
            float(getattr(d, "target_x", 0.0) or 0.0),
            float(getattr(d, "target_y", 0.0) or 0.0),
        )
    try:
        yield
    finally:
        for d in defenders:
            snapshot = before.get(id(d))
            if snapshot is None:
                continue
            tx = float(getattr(d, "target_x", 0.0) or 0.0)
            ty = float(getattr(d, "target_y", 0.0) or 0.0)
            if tx == snapshot[0] and ty == snapshot[1]:
                continue
            src = str(getattr(d, "target_source", None) or source)
            prio = priority if priority is not None else None
            note(world, d, AXIS_X, tx, src, prio)
            note(world, d, AXIS_Y, ty, src, prio)
# ── ELIGIBILITY ──────────────────────────────────────────────────────

def is_band_eligible(d, ope=None) -> bool:
    """True when *d* participates in band arbitration.

    Exempt (must be allowed to snap): frozen / freeze-timer bodies, committed
    TES tacklers, TES-attached bodies, pre-tackle COMMIT/PLANTED owners,
    tackle-state holders, and a body its owner has told to HOLD at zero
    speed.  Their movement is owned by an explicit authority with a hard
    destination, not by competing geometry writers.
    """
    if d is None:
        return False
    if bool(getattr(d, "frozen", False)):
        return False
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        return False
    if bool(getattr(d, "tackle_state_active", False)):
        return False
    if bool(getattr(d, "_tackle_commit_active", False)):
        return False

    # ── ZERO-SPEED HOLD (2026-09-25) ─────────────────────────────────
    # A body whose owner declared HOLD **and** a zero speed budget cannot
    # move by construction, so ANY target this arbiter writes for it is
    # unsatisfiable: the body is left reporting a "static target" it can
    # never reach while `move_speed` stays 0.
    #
    # Measured live (probe_open_play_stall_authority, seed 20260827): 274 of
    # 279 stalled body-frames were this exact case — `movement_state HOLD`,
    # `move_speed == 0.0`, `target_source == "pre_tackle"`, `target_x == x`
    # (pinned) with `target_y` 0.15 m off the body.  `_freeze_stance` had
    # correctly pinned the target to the body's own position; the band
    # resolver then overwrote the lateral axis with a geometry lane the body
    # was forbidden to walk to.
    #
    # A zero-speed HOLD is therefore a MUST-SNAP case like the others above:
    # the owner's hold target (the body's position) has to survive the frame.
    _state = getattr(d, "movement_state", None)
    _state_name = getattr(_state, "name", None) or (
        str(_state) if _state is not None else None
    )
    if _state_name == "HOLD" and float(getattr(d, "move_speed", 0.0) or 0.0) <= 0.0:
        return False

    # Committed TES tacklers (registry + TACKLER_* roles).
    from game.defence.defence_lvl_1.role_depth_glide import (
        is_committed_tackler as _is_committed,
    )
    if _is_committed(d, ope):
        return False

    # Pre-tackle COMMIT / PLANTED owners chase a live destination.
    from game.defence.defence_lvl_1.pre_tackle_authority import (
        PreTackleState,
        is_pre_tackle_managed,
    )
    if is_pre_tackle_managed(d):
        state = getattr(d, "pre_tackle_state", None)
        if state in (PreTackleState.COMMIT, PreTackleState.PLANTED):
            return False
    return True


# ── DOMINANCE ────────────────────────────────────────────────────────

def _displacement(value: float, frame_start: float) -> float:
    return abs(float(value) - float(frame_start))


def _dominant(candidates, frame_start: float):
    """Pick the strongest movement signal from *candidates*.

    *candidates* is an iterable of ``(source, priority, value)``.  Ranking:
    highest priority first, then the largest displacement from the frame-start
    position (the writer demanding the most movement), then source name for a
    stable, deterministic tie-break.
    """
    best = None
    for source, priority, value in candidates:
        key = (
            int(priority),
            _displacement(value, frame_start),
            str(source),
        )
        if best is None or key > best[0]:
            best = (key, (source, int(priority), float(value)))
    return best[1] if best is not None else None


def _band_for(axis: str, world) -> float:
    if axis == AXIS_X:
        return depth_band_m(world)
    return lateral_band_m(world)


def _clamp_axis_value(axis: str, value: float) -> float:
    """Bound a resolved band winner to the playable field.

    The band resolver is the frame's FINAL target authority for every
    band-eligible body, so this is the single place a reachable winner can
    be guaranteed: an out-of-field target is unreachable by construction
    (``Player._clamp_position`` rejects it), and clamping one can only make
    the commanded move MORE achievable.  Identity for an in-field value.
    """
    if axis == AXIS_X:
        return clamp_target_x(value)
    return clamp_target_y(value)


# ── RESOLUTION ───────────────────────────────────────────────────────

def resolve(world, defenders, tuners=None, ope=None) -> list:
    """Arbitrate every captured proposal and write the surviving signal.

    Runs AFTER every writer and BEFORE physics integration.  For each
    eligible defender and each axis:

      - the dominant (highest-priority, largest-displacement) proposal wins;
      - every other proposal within the axis band of the dominant is
        IGNORED (recorded in ``world._writer_priority_bands["suppressed"]``);
      - the dominant value is written to ``target_x`` / ``target_y``.

    Returns the list of defenders whose target was (re)written by the band.
    """
    if not writer_priority_bands_enabled(tuners, world):
        return []
    state = _state(world)
    proposals = state.get("proposals") or {}
    if not proposals:
        return []
    frame_start = state.get("frame_start") or {}
    suppressed_rows = state.setdefault("suppressed", [])
    winners = state.setdefault("winners", {})
    resolved: list = []

    for d in defenders:
        if not is_band_eligible(d, ope):
            continue
        per_axis = proposals.get(id(d))
        if not per_axis:
            continue
        start_xy = frame_start.get(id(d))
        if start_xy is None:
            continue
        start = {AXIS_X: start_xy[0], AXIS_Y: start_xy[1]}
        applied: dict = {}
        for axis, by_source in per_axis.items():
            candidates = [
                (source, priority, value)
                for source, (priority, value) in by_source.items()
            ]
            if not candidates:
                continue
            winner = _dominant(candidates, start[axis])
            if winner is None:
                continue
            win_source, _win_priority, win_value = winner
            # ── WINNER-FLIP HYSTERESIS (2026-09-09) ──
            # Once a source wins an axis, it RETAINS it while a challenger's
            # value lies within the flip band of the incumbent.  This kills
            # the frame-to-frame band flip (two near-equivalent writers
            # trading the axis winner).  A genuinely different target
            # (> flip band) or a no-longer-proposing incumbent hands over.
            prev_winners = state.get("last_winners") or {}
            _inc_source = (prev_winners.get(id(d)) or {}).get(axis)
            if _inc_source and _inc_source != win_source:
                _inc = by_source.get(_inc_source)
                if _inc is not None:
                    _inc_val = float(_inc[1])
                    if abs(_inc_val - win_value) < _flip_band_for(axis, world):
                        winner = (_inc_source, int(_inc[0]), _inc_val)
                        win_source, _win_priority, win_value = winner
            band = _band_for(axis, world)
            for source, priority, value in candidates:
                if source == win_source:
                    continue
                if abs(float(value) - win_value) < band:
                    suppressed_rows.append({
                        "frame": state.get("frame"),
                        "player_id": getattr(d, "player_id", None),
                        "axis": axis,
                        "source": source,
                        "priority": priority,
                        "value": round(float(value), 3),
                        "dominant": win_source,
                        "dominant_value": round(win_value, 3),
                    })
            # BOUND THE WINNER (see the `target_field_bound` import note):
            # the value written below is the frame's final target, so it is
            # clamped here and the clamped value is what the bookkeeping
            # (`winners` / `last_winners`) records as well -- the resolved
            # value and the written value must never disagree for an audit.
            applied[axis] = (
                win_source,
                int(_win_priority),
                _clamp_axis_value(axis, float(win_value)),
            )

        if not applied:
            continue
        for axis, (_source, _priority, value) in applied.items():
            setattr(d, _AXIS_ATTR[axis], float(value))
        # The strongest surviving signal names the frame's writer.
        top_axis, top = max(
            applied.items(), key=lambda kv: (kv[1][1], str(kv[1][0])),
        )
        winners[id(d)] = {
            "x": applied.get(AXIS_X),
            "y": applied.get(AXIS_Y),
        }
        # Persist the per-axis winners so the NEXT frame's resolve can apply
        # the winner-flip hysteresis (anti-flip).
        state.setdefault("last_winners", {})[id(d)] = {
            ax: src for ax, (src, _prio, _val) in applied.items()
        }
        d.target_source = "band:%s" % top[0]
        d._band_axis = top_axis
        resolved.append(d)
    return resolved


def clear_band_state(world) -> None:
    """Drop all band bookkeeping (kickoff reset / fresh sequence)."""
    state = _state(world)
    state["proposals"] = {}
    state["frame_start"] = {}
    state["suppressed"] = []
    state["winners"] = {}
    state["last_winners"] = {}


def suppressed_rows(world) -> list:
    """Return the suppressed-writer rows recorded this frame."""
    return list(_state(world).get("suppressed") or [])


def winner_for(world, d) -> dict | None:
    """Return the per-axis winners written for *d* this frame."""
    return (_state(world).get("winners") or {}).get(id(d))
