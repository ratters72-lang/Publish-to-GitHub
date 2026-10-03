"""attach_band.py — TWO-RADIUS ATTACH-BAND AUTHORITY (2026-09-07).

PURPOSE
-------
Targeted fix for the defensive-line blowout: defenders now use TWO radii
around the ball carrier during the TES window:

  R_assist (default 5.0 m): a defender inside this radius RESERVES an
      attach slot and is smoothly nudged inward toward its slot.  It is
      NOT yet committed — no snap.

  R_tackle (default 2.0 m): the ONLY radius that permits a defender to
      SNAP / ATTACH into the TES/tackle geometry.

This keeps the defensive line from fragmenting: the reserve-and-nudge band
means defenders approach the pile as a coherent line instead of individual
defenders teleporting onto slots while width writers shove the rest of the
line wide.

The authority is:
  - Deterministic (identical inputs -> identical reservations/nudges).
  - Gated behind ``tuners.attach_slot_reserve_enabled`` (default True;
    False = no reservation, no nudge — byte-identical legacy attach).
  - Classification-only: never mutates defender_role / depth_role.
  - Writes ONLY ``x`` / ``y`` / ``vx`` / ``vy`` for nudge bodies and the
    private slot-reservation fields.

SLOT RESERVATIONS
-----------------
A frame-scoped registry (``world._attach_slot_reservations``) maps a slot
key -> (frame, writer_id).  ``reserve_attach_slot`` returns False when the
slot is already claimed this frame by a different writer, so two defenders
can never race one slot.  ``attach_slot_conflicts`` is the CI assert hook.

JOINER GRACE
------------
``stamp_joiner_grace`` / ``decay_joiner_grace`` implement a grace window
(0.05-0.2 s) granted to a freshly-joined tackler so it cannot be raced by
another joiner for the same slot during the window.

DEFENDER CONTACT DAMP
---------------------
``apply_defender_contact_damp`` damps the defender velocity on first TES
contact entry, neutralising the TES contact-spread spike that previously
threw the defensive line wide.
"""

from __future__ import annotations

import math

# ── THE GLIDE BUDGET (single source) ─────────────────────────────────
# glide_budget owns the ONE glide ceiling and the bounded-step primitives.
# It imports only stdlib at module scope, so this reach cannot create a
# cycle in any load order.
from game.defence.defence_lvl_1.glide_budget import (
    bounded_step,
    cap_body_velocity,
    glide_smooth_enabled,
    remaining as _glide_remaining,
)

# ── FEATURE GATE ─────────────────────────────────────────────────────
RESERVE_ENABLED_ATTR: str = "attach_slot_reserve_enabled"
SNAP_RADIUS_ATTR: str = "tackle_snap_radius_m"
ASSIST_RADIUS_ATTR: str = "tackle_assist_radius_m"
JOINER_GRACE_ATTR: str = "joiner_grace_s"
CONTACT_DAMP_ATTR: str = "defender_contact_damp"

# Frame-scoped reservation registry key on world.
_RESERVATIONS_KEY: str = "_attach_slot_reservations"
_CONFLICTS_KEY: str = "_attach_slot_reservation_conflicts"

# Per-defender private fields.
_RESERVED_SLOT_ATTR: str = "_reserved_attach_slot"
_JOINER_GRACE_ATTR: str = "_joiner_grace_remaining_s"
_CONTACT_DAMMED_ATTR: str = "_contact_damned_this_tackle"


# ─────────────────────────────────────────────────────────────────────
# RADIUS RESOLUTION
# ─────────────────────────────────────────────────────────────────────

def assist_radius_m(tuners) -> float:
    """R_assist (m) — reserve + inward-nudge band (default 5.0)."""
    if tuners is None:
        return 5.0
    return float(getattr(tuners, ASSIST_RADIUS_ATTR, 5.0))


def snap_radius_m(tuners) -> float:
    """R_tackle (m) — the sole snap/attach radius (default 2.0)."""
    if tuners is None:
        return 2.0
    return float(getattr(tuners, SNAP_RADIUS_ATTR, 2.0))


# ─────────────────────────────────────────────────────────────────────
# FEATURE GATE
# ─────────────────────────────────────────────────────────────────────

def attach_band_enabled(tuners) -> bool:
    """True when the two-radius attach-band authority is armed.

    Feature gate: reads ``tuners.attach_slot_reserve_enabled`` (default
    True).  When off the authority is a strict no-op — no reservation, no
    nudge, byte-identical legacy attach.
    """
    if tuners is None:
        return False
    return bool(getattr(tuners, RESERVE_ENABLED_ATTR, True))


# ─────────────────────────────────────────────────────────────────────
# SLOT RESERVATION REGISTRY (frame-scoped)
# ─────────────────────────────────────────────────────────────────────

def reserve_attach_slot(world, frame, slot, writer_id) -> bool:
    """Reserve attach *slot* for *writer_id* on *frame*.

    Uses ``world._attach_slot_reservations`` ({slot: (frame, writer_id)}).
    Returns False when another writer already reserved the SAME slot on the
    SAME frame (recording the conflict so ``attach_slot_conflicts`` can
    surface it).  The caller must abort the reservation on False.
    """
    reg = getattr(world, _RESERVATIONS_KEY, None)
    if reg is None:
        reg = {}
        setattr(world, _RESERVATIONS_KEY, reg)
    prev_frame, prev_writer = reg.get(slot, (None, None))
    if prev_frame == frame and prev_writer is not None and prev_writer != writer_id:
        conflicts = getattr(world, _CONFLICTS_KEY, None)
        if conflicts is None:
            conflicts = set()
            setattr(world, _CONFLICTS_KEY, conflicts)
        conflicts.add(slot)
        return False
    reg[slot] = (frame, writer_id)
    return True


def release_attach_slot(world, slot) -> None:
    """Release a slot reservation (best-effort; registry self-expires)."""
    reg = getattr(world, _RESERVATIONS_KEY, None)
    if reg is not None:
        reg.pop(slot, None)


def attach_slot_conflicts(world) -> list:
    """Return slot keys claimed by >1 writer this frame (empty = clean).

    This is the CI assert hook: ``if attach_slot_conflicts(world): fail``.
    """
    conflicts = getattr(world, _CONFLICTS_KEY, None)
    return sorted(conflicts) if conflicts else []


# ─────────────────────────────────────────────────────────────────────
# PER-DEFENDER RESERVATION + GRACE HELPERS
# ─────────────────────────────────────────────────────────────────────

def set_reserved_slot(d, slot) -> None:
    """Stamp the reserved attach slot on *d*."""
    if d is not None:
        setattr(d, _RESERVED_SLOT_ATTR, int(slot))


def get_reserved_slot(d):
    """Return the reserved attach slot for *d*, or None."""
    if d is None:
        return None
    return getattr(d, _RESERVED_SLOT_ATTR, None)


def clear_reserved_slot(d) -> None:
    """Drop the reserved-slot stamp."""
    if d is None:
        return
    try:
        delattr(d, _RESERVED_SLOT_ATTR)
    except AttributeError:
        pass


def stamp_joiner_grace(d, tuners) -> None:
    """Grant the joiner grace window (0.05-0.2 s) to *d*."""
    if d is None:
        return
    grace = float(getattr(tuners, JOINER_GRACE_ATTR, 0.1))
    setattr(d, _JOINER_GRACE_ATTR, max(0.0, grace))


def decay_joiner_grace(d, dt) -> None:
    """Decrement *d*'s joiner grace by *dt* (floors at 0)."""
    if d is None:
        return
    cur = float(getattr(d, _JOINER_GRACE_ATTR, 0.0) or 0.0)
    if cur > 0.0:
        setattr(d, _JOINER_GRACE_ATTR, max(0.0, cur - float(dt)))


def joiner_grace_remaining(d) -> float:
    """Return *d*'s remaining joiner grace (seconds)."""
    if d is None:
        return 0.0
    return float(getattr(d, _JOINER_GRACE_ATTR, 0.0) or 0.0)


# ─────────────────────────────────────────────────────────────────────
# DEFENDER CONTACT DAMP
# ─────────────────────────────────────────────────────────────────────

def apply_defender_contact_damp(d, tuners) -> None:
    """Damp *d*'s velocity on first TES contact entry.

    Neutralises the TES contact-spread spike that threw the defensive line
    wide at the moment of contact.  Only fires once per tackle cycle.
    """
    if d is None:
        return
    if bool(getattr(d, _CONTACT_DAMMED_ATTR, False)):
        return
    damp = float(getattr(tuners, CONTACT_DAMP_ATTR, 0.1))
    if damp > 0.0:
        d.vx = float(getattr(d, "vx", 0.0)) * damp
        d.vy = float(getattr(d, "vy", 0.0)) * damp
    setattr(d, _CONTACT_DAMMED_ATTR, True)


def clear_contact_damp(d) -> None:
    """Drop the per-cycle contact-damp latch (tackle exit / reset)."""
    if d is None:
        return
    try:
        delattr(d, _CONTACT_DAMMED_ATTR)
    except AttributeError:
        pass


# ─────────────────────────────────────────────────────────────────────
# ELIGIBILITY
# ─────────────────────────────────────────────────────────────────────

def is_attach_band_eligible(d, world, ope) -> bool:
    """True when *d* is a non-committed, unfrozen line body in the window.

    Mirrors role_depth_glide.is_tackle_begin_glide_eligible: committed
    tacklers, TES-attached, markers, DUMMY_HALF, fullback and frozen bodies
    are excluded; the body must be inside the tackle-begin window.
    """
    if d is None:
        return False
    if bool(getattr(d, "frozen", False)):
        return False
    # DEPTH-LOCK EXEMPTION (2026-09-08): a depth-locked line body is
    # integrated by Player.update's depth-lock glide (the non-TES
    # integrator).  The TES attach band must not write its x/y — that was
    # the TES "pos-write" that rewrote line X/Y during DRIVE / DOMINANCE.
    if bool(getattr(d, "depth_locked", False)):
        return False
    # TES STRUCTURE-HOLD EXEMPTION (2026-09-11): a body held on its TES
    # drive structure cell is owned by tes_drive_structure.py.  The attach
    # band must not write its x/y — the inward nudge is BC-relative and
    # would drag the held body toward the carrier (breaking "non-tacklers
    # ignore BC movement").
    if bool(getattr(d, "_tes_hold_active", False)):
        return False
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        return False
    if bool(getattr(d, "fullback_role_active", False)):
        return False
    # Committed tacklers are exempt (they snap via TES, not this band).
    from game.defence.defence_lvl_1.role_depth_glide import (
        is_committed_tackler as _is_committed,
        in_tackle_begin_window as _in_window,
    )
    if _is_committed(d, ope):
        return False
    from player.player import DefenderRole
    role = getattr(d, "defender_role", None)
    if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B,
                DefenderRole.DUMMY_HALF):
        return False
    if not _in_window(world, ope):
        return False
    return True


# ─────────────────────────────────────────────────────────────────────
# PER-FRAME ENFORCEMENT (sole two-radius band entry point)
# ─────────────────────────────────────────────────────────────────────

def enforce_attach_band(world, defenders, dt, tuners, ope, bc=None,
                        pre_pos=None, glide_speed_mps: float = 6.0) -> list:
    """Reserve + nudge defenders inside the assist band (R_tackle..R_assist).

    Runs AFTER the tackle-begin glide and BEFORE physics integration at the
    tackle-begin boundary.  For each eligible, non-committed defender:

      - If ``dist <= R_tackle``: snap-eligible — no nudge (TES attach owns
        it once it crosses the snap radius; the body is left to glide).
      - If ``R_tackle < dist <= R_assist``: reserve an attach slot (gated
        by ``attach_slot_reserve_enabled``) and nudge the body toward the
        reserved slot at at most ``glide_speed_mps * dt`` per frame,
        preserving the movement-mode velocity direction.  Its Y is also
        clamped inside the TES slot band so width writes cannot shove it
        out of the band.

    ``pre_pos`` is a ``{player_id: (x, y)}`` frame-start snapshot (the same
    one the tackle-begin glide uses) for measuring the nudge against.

    Returns the list of defenders nudged/reserved this frame.
    """
    if not attach_band_enabled(tuners):
        return []
    if dt is None or dt <= 0.0:
        dt = float(getattr(world, "dt", 1.0 / 60.0) or 1.0 / 60.0)
    r_assist = assist_radius_m(tuners)
    r_tackle = snap_radius_m(tuners)
    max_step = float(glide_speed_mps) * float(dt)
    frame = getattr(world, "frame_count", 0)
    managed: list = []
    # Frame-scoped reservation registry is lazily created by reserve_attach_slot.
    if bc is None:
        bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return managed
    # ── BOUNDED-GLIDE GATE (2026-09-20) ──
    # Armed by default; ``RLM_GLIDE_SMOOTH=0`` or
    # ``tuners.glide_smooth_enabled = False`` restores the pre-fix
    # byte-identical path (amplifying vscale + unbounded band snap).
    _smooth = glide_smooth_enabled(tuners, world)

    for d in defenders:
        if not is_attach_band_eligible(d, world, ope):
            clear_reserved_slot(d)
            decay_joiner_grace(d, dt)
            continue
        pid = getattr(d, "player_id", None)
        _dist = math.hypot(float(bc.x) - float(d.x),
                           float(bc.y) - float(d.y))
        if _dist <= r_tackle:
            # Snap-eligible: the body is about to attach via TES.  Clear
            # any stale reservation so it does not hold a dead slot.
            clear_reserved_slot(d)
            continue
        if _dist > r_assist:
            # Outside the assist band — no reserve, no nudge.
            clear_reserved_slot(d)
            continue
        # ── RESERVE the nearest free slot ──
        # Determine the reserved slot: reuse an existing reservation for
        # this defender, else claim the lowest free slot.
        slot = get_reserved_slot(d)
        if slot is None or slot >= 3:
            # Slots 0/1/2; pick the lowest not already reserved/attached.
            _reserved = set()
            _reg = getattr(world, _RESERVATIONS_KEY, None)
            if _reg is not None:
                _reserved = set(_reg.keys())
            _attached = set()
            _ope_slots = getattr(ope, "_attached_player_slots", None)
            if _ope_slots:
                _attached = set(_ope_slots.values())
            _claimed = _reserved | _attached
            for _s in range(3):
                if _s not in _claimed:
                    slot = _s
                    break
            else:
                slot = None
        if slot is None:
            # No free slot — leave the body to glide without a reservation.
            clear_reserved_slot(d)
            continue
        if reserve_attach_slot(world, frame, slot, int(pid or 0)):
            set_reserved_slot(d, slot)
        else:
            # Slot conflict this frame — another defender claimed it.  The
            # body keeps gliding via the tackle-begin glide (no nudge here).
            clear_reserved_slot(d)
            continue
        # ── NUDGE inward toward the reserved slot ──
        # Reference position: frame-start snapshot, else the defender's
        # current position (no nudge if we just re-stamped the reservation).
        # The reference is what the per-frame glide budget is measured from.
        if pre_pos is not None and pid is not None and pid in pre_pos:
            ref_x, ref_y = pre_pos[pid]
        else:
            ref_x, ref_y = float(d.x), float(d.y)
        # ── Y-BAND TARGET, RESOLVED BEFORE THE NUDGE (2026-09-20) ──
        # The band correction is the WIDTH / ROLE-DEPTH CONSISTENCY write and
        # the nudge is the secondary "approach the pile coherently" write,
        # yet the nudge ran first and consumed the whole frame budget every
        # frame — so the band correction was permanently STARVED and a body
        # parked outside its slot could never be brought back into it
        # (measured before this fix: y never left 35.0 across 400 frames
        # while the nudge walked the body 40 m past the carrier).  The band's
        # requirement is therefore resolved here and RESERVED out of the
        # budget the nudge may spend, so the two writes SHARE the one ceiling
        # instead of the first one monopolising it.  It is still APPLIED
        # below, after the nudge, so the legacy write order is untouched.
        band = _slot_band(d, world)
        _band_target = None
        if band is not None:
            _lo, _hi = band
            _y_band_now = float(d.y)
            if _y_band_now < _lo:
                _band_target = _lo
            elif _y_band_now > _hi:
                _band_target = _hi
        # The inward nudge target is the BC slot direction (approaching the
        # pile), so pull toward the BC.
        _tx = float(bc.x)
        _ty = float(bc.y)
        _tdx = _tx - float(d.x)
        _tdy = _ty - float(d.y)
        _tdist = math.hypot(_tdx, _tdy)
        if _tdist > 0.01:
            _nx = _tdx / _tdist
            _ny = _tdy / _tdist
            if _smooth:
                # ── ROLE-DEPTH AUTHORITY RESPECTED (2026-09-20) ──
                # This body is OUTSIDE the contest by construction (it is
                # not inside R_tackle): its role depth is owned by whichever
                # authority commands ``move_speed``.  The reserve band
                # exists only to keep the line approaching the pile
                # coherently, so the nudge may never move the body faster
                # than that authority's own commanded speed, and may never
                # spend movement budget the frame has already used.  The
                # legacy nudge ignored both — it applied a flat 6 m/s pull
                # ON TOP of whatever the body had already done, which is the
                # "corrects too aggressively / visible jump" report.
                # ── NO COMMANDED SPEED => NO AUTHORITY TO APPROACH THE PILE ──
                # A body whose own movement authority explicitly commanded
                # ZERO speed (unowned, or deliberately holding its cell) must
                # NOT be dragged toward the carrier: that was the reserve band
                # re-targeting a non-contest defender at a flat 6 m/s AGAINST
                # the explicit instruction of the authority that owns it — the
                # "corrects too aggressively" half of the report.  The nudge
                # is withheld entirely in that case (_speed == 0 leaves
                # _nudge == 0 below).  The legacy arm keeps the flat 6 m/s so
                # the A/B stays exact.
                #
                # ABSENT IS NOT ZERO.  A body that carries NO ``move_speed``
                # attribute at all has simply not been stamped by a movement
                # authority yet (an unstamped stub, or a body on its first
                # frame) — that is "no information", NOT "commanded to stop",
                # so the legacy ceiling applies.  Only an attribute that is
                # PRESENT and non-positive withholds the nudge.  In the live
                # engine ``Player.move_speed`` is always present and stamped
                # before this pass runs, so production always takes the capped
                # branch and never silently loses the nudge.
                _speed = float(glide_speed_mps)
                _own_raw = getattr(d, "move_speed", None)
                if _own_raw is not None:
                    _own = None
                    try:
                        _own = float(_own_raw)
                    except (TypeError, ValueError):
                        _own = None
                    if _own is not None:
                        _speed = (
                            0.0 if _own <= 0.0
                            else min(float(glide_speed_mps), _own)
                        )
                _budget = _glide_remaining(
                    (ref_x, ref_y), float(d.x), float(d.y), dt,
                    float(glide_speed_mps),
                )
                if _band_target is not None:
                    # Reserve the band's own (bounded) correction first.
                    _reserved = min(abs(_band_target - float(d.y)), _budget)
                    _budget = max(0.0, _budget - _reserved)
                _nudge = min(min(_speed * float(dt), _tdist), _budget)
            else:
                # LEGACY PATH — byte-identical when the gauge is off.
                _nudge = min(max_step, _tdist)
            if _nudge > 0.0:
                d.x += _nx * _nudge
                d.y += _ny * _nudge
            if _smooth:
                # NEVER AMPLIFY: a body travelling below the ceiling keeps
                # its speed.  The legacy path multiplied it by up to 6x
                # (``vscale = CEILING / spd``), a two-sided velocity spike
                # on the nudge frame and on its release.
                cap_body_velocity(d, float(glide_speed_mps))
            else:
                # LEGACY PATH — the amplifying velocity rescale, byte-for-
                # byte as it was before the gauge existed.
                _spd = math.hypot(
                    getattr(d, "vx", 0.0), getattr(d, "vy", 0.0),
                )
                if _spd > 0.0:
                    _vscale = float(glide_speed_mps) / _spd
                    d.vx = float(getattr(d, "vx", 0.0)) * _vscale
                    d.vy = float(getattr(d, "vy", 0.0)) * _vscale
        # ── Y-BAND CLAMP (applied after the nudge: legacy write order) ──
        if not _smooth:
            # LEGACY: re-derive from the POST-nudge y, exactly as before, so
            # the gate-off arm stays byte-identical to the pre-fix code.
            _band_target = None
            if band is not None:
                _lo, _hi = band
                _y_band_now = float(d.y)
                if _y_band_now < _lo:
                    _band_target = _lo
                elif _y_band_now > _hi:
                    _band_target = _hi
        if _band_target is not None:
            if _smooth:
                # BOUNDED: the band correction converges from whatever budget
                # is left this frame instead of snapping metres onto the band
                # edge in one write.  It was reserved above, so this step can
                # always make progress toward the band.
                _y_budget = _glide_remaining(
                    (ref_x, ref_y), float(d.x), float(d.y), dt,
                    float(glide_speed_mps),
                )
                d.y = bounded_step(float(d.y), _band_target, _y_budget)
            else:
                d.y = _band_target
        managed.append(d)
        decay_joiner_grace(d, dt)
    return managed


def _slot_band(d, world) -> tuple[float, float] | None:
    """The TES slot-Y band for *d*: (slot_y - threshold, slot_y + threshold)."""
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
