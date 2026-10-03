"""game/defence/defence_lvl_1/slot_arbitration.py — slot target_y write
arbitration: soft-lock clamp + override hysteresis + exclusive ownership.

Rollout (2026-09-07): the ruck-adjacent pin is the LAST Y writer of the
frame, but during the TES window multiple subsystems still propose slot
targets.  This module centralises the slot target_y write so a locked slot
cannot be drifted by a low-priority writer, and a large override only lands
after sustained frames (hysteresis).

CONCEPTS
--------
SOFT LOCK
    When a slot's target_y is locked (``slot_lock_active``), a writer whose
    priority is BELOW the lock's priority (``slot_lock_priority``) can only
    move ``target_y`` within ``slot_locked_y +/- SOFT_LOCK_RADIUS_M``.
    Higher-priority writers may override, but only after the override is
    sustained (see hysteresis).

OVERRIDE HYSTERESIS
    A large override (``|proposed_y - slot_locked_y| > SOFT_LOCK_RADIUS_M``)
    is only committed once the SAME writer proposes ~the same value for
    ``OVERRIDE_SUSTAIN_FRAMES`` consecutive frames.  Until then the target
    is clamped to the soft-lock band.  This prevents a single-frame writer
    glitch from teleporting a locked slot.

OWNERSHIP
    ``claim_slot_exclusive`` / ``reset_slot_claims`` implement a per-frame
    exclusive-ownership registry ({slot_key: writer_id}).  A second claim on
    the same slot returns False, which the CI ownership-conflict assert uses
    to fail when two writers claim the same slot during TES.

All arbitration state lives on the holder Player (matching the existing
``target_y`` / ``_target_writer`` pattern) plus a frame-scoped
``world._slot_claims`` / ``world._slot_claim_conflicts`` dict/set.
"""

# ── TUNERS / CONSTANTS ────────────────────────────────────────────────
# Defaults; callers may override via world attributes (e.g. a playtest
# build sets world.slot_soft_lock_radius_m).  Tuned starting values match
# the ship note (SOFT_LOCK_RADIUS = 0.2 m, TACKLE_SLOT_THRESHOLD = 0.3 m).
SOFT_LOCK_RADIUS_M: float = 0.2
TACKLE_SLOT_THRESHOLD_M: float = 0.3
OVERRIDE_SUSTAIN_FRAMES: int = 3

# Writer priorities (higher = more authority).  The ruck pin is the final
# authority; spacing / retreat are lower.
PRIORITY_LOW: int = 10
PRIORITY_RETREAT: int = 30
PRIORITY_SPACING: int = 50
PRIORITY_PIN: int = 100

# Two proposals within this distance count as "the same override" so the
# hysteresis accumulator does not reset on sub-cm jitter.
_OVERRIDE_EPS_M: float = 0.05


# ── TUNER ACCESSORS ──────────────────────────────────────────────────

def soft_lock_radius_m(world) -> float:
    """Soft-lock radius (m).  ``world.slot_soft_lock_radius_m`` overrides."""
    return float(getattr(world, "slot_soft_lock_radius_m", SOFT_LOCK_RADIUS_M))


def override_sustain_frames(world) -> int:
    """Frames a large override must be sustained.  World overrideable."""
    return int(getattr(world, "slot_override_sustain_frames", OVERRIDE_SUSTAIN_FRAMES))


def tackle_slot_threshold_m(world) -> float:
    """Tackle slot-Y threshold (m).  World overrideable."""
    return float(getattr(world, "tackle_slot_threshold_m", TACKLE_SLOT_THRESHOLD_M))


# ── SOFT-LOCK STATE ──────────────────────────────────────────────────

def lock_slot(world, holder, locked_y, writer_id, priority) -> None:
    """Engage the soft lock on *holder* (slot) at *locked_y*.

    Resets any pending override so a (re)lock starts from zero.
    """
    holder.slot_locked_y = float(locked_y)
    holder.slot_lock_active = True
    holder.slot_owner_writer = writer_id
    holder.slot_lock_priority = int(priority)
    _reset_override(holder)


def unlock_slot(world, holder) -> None:
    """Release the soft lock on *holder*."""
    holder.slot_lock_active = False
    _reset_override(holder)


def _reset_override(holder) -> None:
    holder.slot_override_frames = 0
    holder.slot_override_writer = None
    holder.slot_override_proposed = None


def _log_override(world, record, holder, writer_id, priority, proposed, outcome) -> None:
    """Append a compact override log row to *record* (if provided).

    Matches the playtest "watch override logs (writer id, priority,
    proposed_y, current target_y, override_frames)" item.  The row is frame-
    stamped from ``world.frame_count`` so the per-slot ring is self-describing.
    """
    if record is None:
        return
    record.append({
        "frame": int(getattr(world, "frame_count", 0)),
        "writer": writer_id,
        "priority": int(priority),
        "proposed_y": float(proposed),
        "current_target_y": float(getattr(holder, "target_y", 0.0)),
        "override_frames": int(getattr(holder, "slot_override_frames", 0)),
        "outcome": outcome,
    })


def update_slot_target(world, holder, proposed_y, writer_id, priority,
                       *, record=None) -> float:
    """Write *holder*'s ``target_y`` under the arbitration rules.

    Returns the value actually written (may be clamped).

    Rules:
      - No soft lock -> unrestricted write (records ``_target_writer``).
      - Lock active, proposal inside ``locked +/- radius`` -> write as-is.
      - Lock active, proposal outside radius, priority < lock priority
        -> ALWAYS clamped to the band (low-priority writer).
      - Lock active, proposal outside radius, priority >= lock priority
        -> override hysteresis: clamped until the same writer sustains the
        proposal for ``OVERRIDE_SUSTAIN_FRAMES``, then committed.
    """
    proposed = float(proposed_y)
    priority = int(priority)

    if not getattr(holder, "slot_lock_active", False):
        holder.target_y = proposed
        holder._target_writer = writer_id
        return proposed

    locked = float(getattr(holder, "slot_locked_y", proposed))
    lock_prio = int(getattr(holder, "slot_lock_priority", 0))
    radius = soft_lock_radius_m(world)
    lo = locked - radius
    hi = locked + radius

    # Within the soft-lock band -> the write is valid as-is.
    if lo <= proposed <= hi:
        _reset_override(holder)
        holder.target_y = proposed
        holder._target_writer = writer_id
        _log_override(world, record, holder, writer_id, priority, proposed, "within")
        return proposed

    # Outside the band -> override attempt.
    if priority < lock_prio:
        clamped = min(max(proposed, lo), hi)
        holder.target_y = clamped
        holder._target_writer = writer_id
        _log_override(world, record, holder, writer_id, priority, proposed, "clamp-low-prio")
        return clamped

    # High-priority writer: override hysteresis.
    sustain = override_sustain_frames(world)
    if (
        getattr(holder, "slot_override_writer", None) == writer_id
        and getattr(holder, "slot_override_proposed", None) is not None
        and abs(proposed - holder.slot_override_proposed) <= _OVERRIDE_EPS_M
    ):
        holder.slot_override_frames = (
            int(getattr(holder, "slot_override_frames", 0)) + 1
        )
    else:
        holder.slot_override_frames = 1
        holder.slot_override_writer = writer_id
        holder.slot_override_proposed = proposed

    if int(getattr(holder, "slot_override_frames", 0)) >= sustain:
        holder.target_y = proposed
        holder._target_writer = writer_id
        _log_override(world, record, holder, writer_id, priority, proposed, "override-commit")
        return proposed

    clamped = min(max(proposed, lo), hi)
    holder.target_y = clamped
    holder._target_writer = writer_id
    _log_override(world, record, holder, writer_id, priority, proposed, "override-pending")
    return clamped


# ── EXCLUSIVE OWNERSHIP (CI conflict assert) ─────────────────────────

def claim_slot_exclusive(world, frame, key, writer_id) -> bool:
    """Claim exclusive ownership of slot *key* for *writer_id* on *frame*.

    Uses ``world._slot_claims`` ({key: (frame, writer_id)}).  Returns False
    when another writer already claimed the SAME slot on the SAME frame
    (recording the conflict in ``world._slot_claim_conflicts`` so
    ``slot_ownership_conflicts`` can surface it).  The caller must abort the
    write on False.

    The registry is frame-stamped, so no per-frame reset is required: a claim
    only conflicts when it arrives on the same frame from a different writer.
    """
    claims = getattr(world, "_slot_claims", None)
    if claims is None:
        claims = {}
        setattr(world, "_slot_claims", claims)
    prev_frame, prev_writer = claims.get(key, (None, None))
    if prev_frame == frame and prev_writer is not None and prev_writer != writer_id:
        conflicts = getattr(world, "_slot_claim_conflicts", None)
        if conflicts is None:
            conflicts = set()
            setattr(world, "_slot_claim_conflicts", conflicts)
        conflicts.add(key)
        return False
    claims[key] = (frame, writer_id)
    return True


def reset_slot_claims(world) -> None:
    """Clear the frame-scoped ownership registry + conflict set.

    Provided for a frame preamble that wants an explicit per-frame reset
    (the frame-stamped claims already self-expire, so this is optional).
    """
    setattr(world, "_slot_claims", {})
    setattr(world, "_slot_claim_conflicts", set())


def slot_ownership_conflicts(world) -> list:
    """Return slot keys claimed by >1 writer this frame (empty = clean).

    This is the CI assert hook: ``if slot_ownership_conflicts(world):
    fail``.  A live run that leaves a conflict means two writers raced for
    the same slot during TES.
    """
    conflicts = getattr(world, "_slot_claim_conflicts", None)
    return sorted(conflicts) if conflicts else []


# ── TACKLE-GATING SLOT THRESHOLD ─────────────────────────────────────

def is_slot_tackle_eligible(bc, defender, world) -> bool:
    """True when the BC is INSIDE the slot-Y threshold of *defender*.

    The tackle gate (Stage 6 radial radius + rollout slot threshold) is:
        radial-in AND slot-in  -> tackle eligible
        radial-in but slot-OUT -> NOT eligible (suppressed tackle)

    The slot reference is the defender's LIVE lateral lane (``y``), NOT its
    ``target_y``.  ``target_y`` is a destination that is rewritten by the
    anticipation lean (the predicted ``bc.y + vy*lead``) and by the
    pre-tackle COMMIT anti-stack point (``bc.y +/- commit_lateral_offset``),
    so it is not the lane the defender actually occupies.  Using it made the
    gate fail for every committed body (COMMIT-and-slot-IN = 0): the armed
    body's target was 1.0 m off ``bc.y`` while its live lane was aligned.
    The slot gate measures where the defender genuinely is.
    """
    if bc is None or defender is None:
        return False
    s = tackle_slot_threshold_m(world)
    bc_y = float(getattr(bc, "y", 0.0))
    ref_y = float(getattr(defender, "y", 0.0))
    return abs(bc_y - ref_y) < s
