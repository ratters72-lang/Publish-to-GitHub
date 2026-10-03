"""role_depth_glide.py — TACKLE-BEGIN ROLE-DEPTH GLIDE AUTHORITY (2026-09-07).

PURPOSE
-------
Single authoritative enforcement of the tackle-begin law:

  When TACKLE_INIT fires, ONLY explicitly committed tacklers may snap into
  TES/tackle geometry.  EVERY other defender treats role depth as a
  DESTINATION and glides toward it under its current movement mode
  (backpedal / DRIFT / SLIDE / RETREAT), with per-frame movement capped at
  ``TACKLE_BEGIN_GLIDE_SPEED_MPS * dt``.

This replaces the ad-hoc, site-local glide caps (DEPTH_LOCK_GLIDE /
TES_ATTACH_GLIDE / target_x_glide) with ONE authority that carries an
explicit committed-tackler exemption and a single, verifiable per-frame
movement ceiling.  The authority runs AFTER every target writer and
BEFORE physics integration at the tackle-begin boundary, so the glided
position is what lands.

ANTICIPATION + PRE-TACKLE ENGAGEMENT STAY SEPARATE
--------------------------------------------------
The existing ``anticipation_layer.py`` and the unified pre-tackle station
(``pre_tackle_authority.py``) are additive systems: they nominate
candidates and bias spacing BEFORE contact.  They never overwrite
movement during the tackle-begin window.  This module is movement-only
and never touches those nomination/spacing outputs.

CONTRACTS
---------
  - Classification-only: never mutates defender_role / depth_role.
  - Writes ONLY ``x`` / ``y`` / ``vx`` / ``vy`` (and the private glide
    baseline) for bodies it manages, and only to CLAMP a teleport.
  - Exempt (legally snap): committed tacklers, TES-attached bodies,
    markers, DUMMY_HALF, the dynamic fullback, and frozen bodies.
  - Gated behind ``tuners.tackle_begin_glide_enabled`` (feature flag).
  - Determinism: identical inputs -> identical clamps.
"""

from __future__ import annotations

import math

# ── THE GLIDE BUDGET (single source) ─────────────────────────────────
# glide_budget owns the ONE glide ceiling and the bounded-step primitives.
# It imports only stdlib at module scope, so this reach cannot create a
# cycle in any load order.
from game.defence.defence_lvl_1.glide_budget import (
    GLIDE_SPEED_MPS,
    bounded_step,
    cap_body_velocity,
    glide_smooth_enabled,
    remaining as _glide_remaining,
)

# ── PER-FRAME MOVEMENT CEILING ───────────────────────────────────────
# SINGLE-SOURCED from glide_budget.GLIDE_SPEED_MPS — this module no longer
# declares its own 6.0, so the five glide authorities can never drift apart.
# A per-frame step anywhere above this during the tackle-begin window is a
# teleport by construction.
TACKLE_BEGIN_GLIDE_SPEED_MPS: float = GLIDE_SPEED_MPS

# Feature flag attribute on DefenceTuners.
ENABLED_ATTR: str = "tackle_begin_glide_enabled"

# Trace env gate.
_TRACE_ENV: str = "RLM_TACKLE_BEGIN_GLIDE_TRACE"

# Width-consistency trace env gate (per-frame Y vs TES slot band).
_WIDTH_TRACE_ENV: str = "RLM_TACKLE_BEGIN_WIDTH_TRACE"

# Two-radius tuner attribute names resolved from DefenceTuners.
ASSIST_RADIUS_ATTR: str = "tackle_assist_radius_m"
SNAP_RADIUS_ATTR: str = "tackle_snap_radius_m"

# Window phases the authority is live in.
_WINDOW_PHASES = frozenset({
    "TACKLE_INIT",
    "TACKLE_CONTACT",
    "TACKLE_DRIVE",
    "TACKLE_DOMINANCE",
})


# ─────────────────────────────────────────────────────────────────────
# FEATURE FLAG
# ─────────────────────────────────────────────────────────────────────

def tackle_begin_glide_enabled(tuners) -> bool:
    """True when the tackle-begin glide authority is armed.

    Feature gate: reads ``tuners.tackle_begin_glide_enabled`` (default
    True).  When off the authority is a strict no-op — the engine behaves
    exactly as it did before this module.
    """
    if tuners is None:
        return False
    return bool(getattr(tuners, ENABLED_ATTR, True))


# ─────────────────────────────────────────────────────────────────────
# COMMITTED-TACKLER EXEMPTION
# ─────────────────────────────────────────────────────────────────────

def is_committed_tackler(d, ope) -> bool:
    """True when *d* is an explicitly committed tackler (legally snaps).

    The exemption set is the TES participant registry plus the explicit
    TACKLER_* roles:
      - ``ope.primary``
      - ``ope.joiners``
      - ``ope._attached_player_slots`` (attach-slot registry)
      - ``ope.tes_ctx.attached`` / ``attached_ids``
      - ``defender_role`` in {TACKLER_1, TACKLER_2, TACKLER_3}
    """
    if d is None:
        return False
    pid = getattr(d, "player_id", None)
    if ope is not None:
        if getattr(ope, "primary", None) is d:
            return True
        for _j in (getattr(ope, "joiners", None) or ()):
            if _j is d:
                return True
        if pid is not None and pid in (getattr(ope, "_attached_player_slots", None) or {}):
            return True
        _ctx = getattr(ope, "tes_ctx", None)
        if _ctx is not None:
            if pid is not None:
                if pid in (getattr(_ctx, "attached_ids", None) or ()):
                    return True
            _attached = getattr(_ctx, "attached", None)
            if _attached and pid is not None and pid in _attached:
                return True
    from player.player import DefenderRole
    return getattr(d, "defender_role", None) in (
        DefenderRole.TACKLER_1,
        DefenderRole.TACKLER_2,
        DefenderRole.TACKLER_3,
    )


# ─────────────────────────────────────────────────────────────────────
# ELIGIBILITY
# ─────────────────────────────────────────────────────────────────────

def _phase_name(world, ope) -> str:
    """Resolve the canonical tackle-begin phase name."""
    if ope is not None:
        ph = getattr(ope, "_phase", None)
        if ph is not None:
            return str(ph).upper()
    sub = getattr(world, "sub_state", None)
    if sub is not None:
        n = getattr(sub, "name", None) or str(sub)
        return n.upper()
    return ""


def in_tackle_begin_window(world, ope) -> bool:
    """True when the world is inside the tackle-begin window."""
    return _phase_name(world, ope) in _WINDOW_PHASES


def is_tackle_begin_glide_eligible(d, world, ope) -> bool:
    """True when *d* is a NON-committed defender that must glide.

    Exempt (returns False): committed tacklers, TES-attached, markers,
    DUMMY_HALF, dynamic fullback, and frozen bodies.  Non-exempt bodies
    inside the tackle-begin window are eligible.
    """
    if d is None:
        return False
    if bool(getattr(d, "frozen", False)):
        return False
    # DEPTH-LOCK EXEMPTION (2026-09-08): a depth-locked line body is
    # integrated by Player.update's depth-lock glide (the non-TES
    # integrator).  The TES tackle-begin glide must not write its x/y —
    # that was the TES "pos-write" that rewrote line X/Y during DRIVE /
    # DOMINANCE.
    # TES STRUCTURE-HOLD EXEMPTION (2026-09-11): a body held on its TES
    # drive structure cell is owned by tes_drive_structure.py.  The glide
    # must not write its x/y or it would drag the held body off the locked
    # cell (the line would stop ignoring BC movement).
    if bool(getattr(d, "_tes_hold_active", False)):
        return False
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        return False
    if bool(getattr(d, "fullback_role_active", False)):
        return False
    if is_committed_tackler(d, ope):
        return False
    from player.player import DefenderRole
    role = getattr(d, "defender_role", None)
    if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B,
                DefenderRole.DUMMY_HALF):
        return False
    if not in_tackle_begin_window(world, ope):
        return False
    return True


# ─────────────────────────────────────────────────────────────────────
# GLIDE BASELINE PRIMITIVES
# ─────────────────────────────────────────────────────────────────────

def _base_attr_x() -> str:
    return "_tackle_begin_glide_base_x"


def _base_attr_y() -> str:
    return "_tackle_begin_glide_base_y"


def clear_role_depth_glide(d) -> None:
    """Drop the glide baseline (window exit / eligibility loss)."""
    if d is None:
        return
    for attr in (_base_attr_x(), _base_attr_y()):
        try:
            delattr(d, attr)
        except AttributeError:
            pass


def _trace_on() -> bool:
    import os
    return os.environ.get(_TRACE_ENV, "") == "1"


def _width_trace_on() -> bool:
    import os
    return os.environ.get(_WIDTH_TRACE_ENV, "") == "1"


# ─────────────────────────────────────────────────────────────────────
# TWO-RADIUS RESOLUTION + TES BAND HELPER
# ─────────────────────────────────────────────────────────────────────

def assist_radius_m(tuners) -> float:
    """R_assist (metres) — the reserve + inward-nudge band.

    A defender inside this radius of the ball carrier RESERVES an attach
    slot and is smoothly nudged inward; it is NOT yet committed (no snap).
    Reads ``tuners.tackle_assist_radius_m`` (default 5.0).
    """
    if tuners is None:
        return 5.0
    return float(getattr(tuners, ASSIST_RADIUS_ATTR, 5.0))


def snap_radius_m(tuners) -> float:
    """R_tackle (metres) — the sole radius that permits a snap/attach.

    Only a defender inside this radius may snap into the TES/tackle
    geometry.  Reads ``tuners.tackle_snap_radius_m`` (default 2.0).
    """
    if tuners is None:
        return 2.0
    return float(getattr(tuners, SNAP_RADIUS_ATTR, 2.0))


def tes_slot_band(d, world) -> tuple[float, float] | None:
    """The TES slot-Y band for *d*: (slot_y - threshold, slot_y + threshold).

    The slot-Y reference is ``target_y`` (fall back to live ``y``); the
    threshold is ``tackle_slot_threshold_m`` from slot_arbitration (world
    overrideable).  Returns None when no reference/threshold is available,
    in which case no Y clamp is applied.
    """
    if world is None:
        return None
    try:
        from game.defence.defence_lvl_1.slot_arbitration import (
            tackle_slot_threshold_m as _threshold,
        )
        s = float(_threshold(world))
    except Exception:
        s = 0.3
    if s is None or s <= 0.0:
        return None
    ref_y = float(getattr(d, "target_y", getattr(d, "y", 0.0)))
    return (ref_y - s, ref_y + s)


# ─────────────────────────────────────────────────────────────────────
# PER-FRAME ENFORCEMENT (sole authority entry point)
# ─────────────────────────────────────────────────────────────────────

def enforce_tackle_begin_glide(world, defenders, dt, tuners, ope,
                               pre_pos=None) -> list:
    """Clamp every eligible non-committed defender to the glide ceiling.

    Runs AFTER all target writers and BEFORE physics integration at the
    tackle-begin boundary.  For each eligible body, if its movement this
    frame from the reference position (``pre_pos`` snapshot, or the
    previous frame's glide baseline) exceeds
    ``TACKLE_BEGIN_GLIDE_SPEED_MPS * dt``, it is clamped back to that cap
    along the direction of travel — preserving the movement mode's
    velocity direction (never zeroed into a teleport).

    ``pre_pos`` is a ``{player_id: (x, y)}`` snapshot taken at the very
    START of the frame by the caller, so movement from any writer this
    frame (p.update, depth-lock clamp, soft-follow) is measured correctly.

    Returns the list of defenders clamped this frame.
    """
    if not tackle_begin_glide_enabled(tuners):
        return []
    if dt is None or dt <= 0.0:
        dt = float(getattr(world, "dt", 1.0 / 60.0) or 1.0 / 60.0)
    max_step = TACKLE_BEGIN_GLIDE_SPEED_MPS * float(dt)
    clamped: list = []
    frame = getattr(world, "frame_count", 0)
    trace = _trace_on()
    # Two-radius resolution (R_assist reserve/nudge band + R_tackle snap
    # radius).  A defender between R_tackle and R_assist is "reserved" and
    # its Y is clamped inside the TES slot band so open-play width writes
    # cannot shove it out of the band.
    r_assist = assist_radius_m(tuners)
    r_tackle = snap_radius_m(tuners)
    width_trace = _width_trace_on()
    bc = getattr(world, "ball_carrier", None)
    # ── BOUNDED-GLIDE GATE (2026-09-20) ──
    # Armed by default; ``RLM_GLIDE_SMOOTH=0`` or
    # ``tuners.glide_smooth_enabled = False`` restores the pre-fix
    # byte-identical path (amplifying vscale + unbounded band snap).
    _smooth = glide_smooth_enabled(tuners, world)

    for d in defenders:
        if not is_tackle_begin_glide_eligible(d, world, ope):
            clear_role_depth_glide(d)
            continue
        pid = getattr(d, "player_id", None)
        # Reference position: caller's pre-frame snapshot, else the last
        # frame's recorded baseline.
        if pre_pos is not None and pid is not None and pid in pre_pos:
            ref_x, ref_y = pre_pos[pid]
        else:
            ref_x = float(getattr(d, _base_attr_x(), d.x))
            ref_y = float(getattr(d, _base_attr_y(), d.y))
        dx = float(d.x) - ref_x
        dy = float(d.y) - ref_y
        dist = math.hypot(dx, dy)
        if dist > max_step:
            scale = max_step / dist
            d.x = ref_x + dx * scale
            d.y = ref_y + dy * scale
            if _smooth:
                # ── BOUNDED THROTTLE (never amplify) ──
                # A glide may only REMOVE excess speed.  The legacy
                # ``vscale = CEILING / spd`` MULTIPLIED a slow body's
                # velocity (1.0 m/s -> 6.0 m/s), producing a two-sided
                # velocity spike on the clamp frame and on its release.
                # cap_body_velocity is a no-op for a body already at or
                # below the ceiling, so the movement mode (backpedal /
                # drift / slide / retreat) is genuinely preserved instead
                # of being overwritten by the clamp.
                cap_body_velocity(d, TACKLE_BEGIN_GLIDE_SPEED_MPS)
            else:
                # LEGACY PATH — byte-identical when the gauge is off.
                spd = math.hypot(getattr(d, "vx", 0.0), getattr(d, "vy", 0.0))
                if spd > 0.0:
                    vscale = TACKLE_BEGIN_GLIDE_SPEED_MPS / spd
                    d.vx = float(getattr(d, "vx", 0.0)) * vscale
                    d.vy = float(getattr(d, "vy", 0.0)) * vscale
            clamped.append(d)
            if trace:
                role = getattr(d, "depth_role", None) or "-"
                mn = getattr(d, "movement_state", None)
                mname = getattr(mn, "name", mn) if mn is not None else "-"
                print(
                    f"[TACKLE-BEGIN-GLIDE] frame={frame} "
                    f"id={getattr(d, 'player_id', '?')} role={role} "
                    f"mode={mname} dist={dist:.2f} capped={max_step:.2f}"
                )
        # ── TES-BAND Y CLAMP (width-consistency) ──
        # Clamp the defender's Y into its slot band so open-play width
        # writers cannot shove it out of the TES band.
        #
        # BOUNDED (2026-09-20): this clamp used to write ``d.y = band_lo`` /
        # ``band_hi`` UNCONDITIONALLY, and it ran AFTER the vector clamp had
        # already spent the frame's movement budget — so a body metres
        # outside its band was teleported onto the band edge in one frame,
        # and the ceiling this module claims to enforce was exceeded by the
        # very next statement.  The correction is now taken from whatever
        # budget the vector clamp left unspent, so it CONVERGES over frames
        # (a 2 m band error closes in ~0.33 s at the ceiling) instead of
        # jumping, and the TOTAL displacement of both glide writes stays
        # inside the one per-frame ceiling.
        band = tes_slot_band(d, world)
        if band is not None:
            band_lo, band_hi = band
            _y_now = float(d.y)
            _y_target = _y_now
            if _y_now < band_lo:
                _y_target = band_lo
            elif _y_now > band_hi:
                _y_target = band_hi
            if _y_target != _y_now:
                if _smooth:
                    _budget = _glide_remaining(
                        (ref_x, ref_y), float(d.x), _y_now, dt,
                        TACKLE_BEGIN_GLIDE_SPEED_MPS,
                    )
                    d.y = bounded_step(_y_now, _y_target, _budget)
                else:
                    d.y = _y_target
        if width_trace:
            role = getattr(d, "depth_role", None) or "-"
            band_lo, band_hi = band if band is not None else (0.0, 0.0)
            _bc_dist = float("nan")
            if bc is not None:
                _bc_dist = math.hypot(
                    float(getattr(bc, "x", 0.0)) - float(d.x),
                    float(getattr(bc, "y", 0.0)) - float(d.y),
                )
            print(
                f"[TACKLE-BEGIN-WIDTH] frame={frame} "
                f"id={getattr(d, 'player_id', '?')} role={role} "
                f"y={float(d.y):.2f} mv_dist={dist:.2f} "
                f"bc_dist={_bc_dist:.2f} r_assist={r_assist:.2f} "
                f"r_tackle={r_tackle:.2f} band=[{band_lo:.2f},{band_hi:.2f}]"
            )
        # Refresh baseline to the (possibly clamped) current position so
        # the fallback reference tracks the last legal position.
        setattr(d, _base_attr_x(), float(d.x))
        setattr(d, _base_attr_y(), float(d.y))
    return clamped
