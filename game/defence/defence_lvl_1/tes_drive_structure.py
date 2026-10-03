"""tes_drive_structure.py — TES DRIVE STRUCTURE-HOLD AUTHORITY (2026-09-11).

SPEC
----
During the TES drive chamber (``TACKLE_CONTACT`` / ``TACKLE_DRIVE`` /
``TACKLE_DOMINANCE``) the defensive line HOLDS ITS STRUCTURE:

  - defenders already at their correct depth FREEZE;
  - out-of-position defenders keep JOGGING toward their depth role;
  - ONLY tacklers inherit ball-carrier movement;
  - every other defender IGNORES BC movement entirely;
  - spacing bands stay LOCKED with only a small bounded stretch;
  - the anticipation micro-adjustment stays live on top of the band;
  - at RELEASE the whole line unfreezes instantly and the DEF_RETREAT_10
    depth target switches from the drive-ENTRY ruck_x to the LIVE ruck_x.

MODEL
-----
Each eligible non-tackler line body owns a structure cell whose two axes
have DIFFERENT sources:

    cell_x = bc.x + WIDTH_OFFSETS[depth_role]   -- LIVE, every frame
    cell_y = the body's lane from the LAST OPEN_PLAY frame (locked)

``cell_x`` re-reads the LIVE ball carrier every frame, so the line holds
its DEPTH ROLE RELATIVE TO THE CARRIER while the carrier is driven.  It is
the same law as ``defensive_depth_roles._depth_role_target_x``.

``cell_y`` is LANE-TRUE: it is captured once, at drive entry, from the
lane the defender **actually holds** on that frame (its achieved line Y),
so an already-lane-true body FREEZES immediately and the line cannot
drift.  It is deliberately NOT the re-derived count-lane chain value
(``d.frame_spacing_y``): that value is restamped every OPEN_PLAY frame
against the CURRENT ruck and the CURRENT per-side count, which by the last
pre-drive frame is anchored to a different ``ruck_y`` and compressed onto
the side-width cap (two bodies to one lane).  Seeding from it put every
held body 1–15 m off its own lane — nothing froze and the whole line was
dragged onto the wrong lanes across the chamber (the drift + pinch).  The
Y cell receives only the bounded anticipation micro-adjustment
(<= ``tes_band_stretch_max_m``) plus the line-width clamp, which together
auto-correct a body that is genuinely out of lane IN to the band edge.

Per frame the body is either:

  - FROZEN : ``|pos - cell| <= tes_hold_freeze_tolerance_m`` -> HOLD,
             zero velocity, target pinned to the current position; or
  - JOGGING: farther out -> DRIFT toward the cell at
             ``tes_hold_jog_speed_mps`` (a destination, never a snap).

CONTRACTS
---------
  - Never mutates ``defender_role`` / ``depth_role``.
  - Never sets the ``frozen`` flag (the sole freeze authority owns it);
    "freeze" here is a movement HOLD (``move_speed = 0``).
  - Writes only ``target_x/target_y/move_speed/movement_state`` and the
    private hold stamps.
  - Deterministic: identical inputs -> identical cells and targets.
  - Bodies carrying a live structure hold are exempt from the TES
    tackle-begin glide and the two-radius attach band so no BC-relative
    nudge can pull them (see ``is_tes_structure_hold``).
"""

from __future__ import annotations

import math

from player.movement_states import MovementState

# ── TARGET-SOURCE STAMP ──────────────────────────────────────────────
TES_DRIVE_HOLD_SOURCE: str = "tes_drive_hold"

# ── PER-DEFENDER PRIVATE ATTRS ───────────────────────────────────────
HOLD_ACTIVE_ATTR: str = "_tes_hold_active"
HOLD_X_ATTR: str = "_tes_hold_x"
HOLD_Y_ATTR: str = "_tes_hold_y"
HOLD_FROZEN_ATTR: str = "_tes_hold_frozen"
# IMMUTABLE LANE SEED (2026-09-11): the body's TRUE lateral lane,
# captured once at drive entry and never overwritten.  The lateral
# authority mutates only the EFFECTIVE cell (HOLD_Y_ATTR); an in-lane
# body therefore always targets its exact lane — in-lane spacing is
# never disrupted by a moving/re-clamping line-width band.
LANE_Y_ATTR: str = "_tes_lane_y"

# ── WORLD ATTR — drive-entry ruck X (captured once per drive) ────────
ENTRY_RUCK_X_ATTR: str = "_tes_drive_entry_ruck_x"
# STABLE LATERAL BAND REFERENCE (2026-09-11): the lateral band's centre
# Y, latched once per drive.  The engine re-pins ``world.ruck_y_m = bc.y``
# at the CONTACT -> DRIVE transition, so a band centred on the live ruck
# re-centred the held line every frame (the in-lane-spacing disruption).
BAND_REF_Y_ATTR: str = "_tes_lateral_band_ref_y"

# ── CHAMBER IDENTITY (2026-09-11) ────────────────────────────────────
# The TACKLE-scoped id of the drive chamber whose lanes are latched.
# The chamber is opened ONCE per tackle by the defence manager
# (:func:`open_tes_drive_chamber`) and is the ONLY valid reference for a
# lane latch: a latch carrying a different id belongs to a DEAD tackle
# and is re-seeded from the position the body actually holds.
#
# WHY NOT A FRAME STAMP: the manager is invoked on intra-tackle frames
# that are outside the three drive sub-states (``update_tes_drive`` runs
# under TACKLE_INIT as well), so frame adjacency reports a "gap" INSIDE a
# live tackle.  Measured live, that re-seeded every lane on every other
# frame — the hold chased a moving line and 10–18 m of lateral drift with
# zero freezing bodies was the result.  A tackle-scoped id is gap-proof.
# ENGINE-STAMPED, per-tackle.  ``OpenPlayEngine._start_tackle`` bumps this
# on EVERY new tackle, so it is the authoritative chamber identity — a
# page the defence manager cannot forget to turn (the manager is not
# invoked at all on some post-tackle paths, so a manager-maintained flag
# leaked one tackle's lanes into the next one: measured live, a single
# chamber id spanned 1077 drive frames / several tackles).
CHAMBER_ID_ATTR: str = "_tes_tackle_seq"
# Per-body stamp: the tackle seq this body's lane latch belongs to.
BODY_CHAMBER_ATTR: str = "_tes_hold_chamber"
# World-side latch stamp: the tackle seq the drive-entry ruck X and the
# lateral band reference belong to (dropped when the seq advances).
# Kept as its own name so a caller that bumps CHAMBER_ID_ATTR by hand
# still invalidates the latches consistently.
CHAMBER_SEQ_ATTR: str = "_tes_chamber_seq"
# Tackle seq the WORLD-side latches (entry ruck / band reference) carry.
LATCH_SEQ_ATTR: str = "_tes_latch_seq"

# ── SLOT OWNERSHIP (2026-09-12) ──────────────────────────────────────
# RECOVERY OWNERSHIP: the drive hold's lateral cell is the body's
# ASSIGNED SLOT, not its achieved Y.  The slot table is produced by a
# ONE-PASS CENSUS on the chamber-entry frame and is then IMMUTABLE for
# the chamber:
#
#   * a defender already correctly slotted RETAINS its slot (freezes on
#     it) and the line holds shape;
#   * a displaced defender RECOVERS to its OWN latched slot and nothing
#     else;
#   * a slot whose owner leaves the line stays VACANT — it keeps its
#     owner identity and is NEVER reassigned;
#   * there is NO chamber-wide rebalancing and NO dynamic spacing
#     redistribution (the ladder is latched, never re-solved).
#
# The census function (``establish_slot_ownership``) is the SOLE writer
# of every attribute below.  Nothing after the census may create,
# modify, transfer, or infer ownership (see ``verify_tes_slot_recovery_ownership.py``).
SLOT_LADDER_ATTR: str = "_tes_slot_ladder"
# Chamber seq the slot table belongs to (== CHAMBER_ID_ATTR at census).
SLOT_SEQ_ATTR: str = "_tes_slot_seq"
# Per-body slot ownership stamps (write-once, at the census).
SLOT_SIDE_ATTR: str = "_tes_slot_side"
SLOT_INDEX_ATTR: str = "_tes_slot_index"
SLOT_Y_ATTR: str = "_tes_slot_y"
# FREEZE-AT-SLOT marker: True when this body's held cell is its OWN slot
# (the recovery-ownership path), False when it falls back to the legacy
# lane-true hold (no slot owned — e.g. a body that joined mid-chamber).
SLOT_HELD_ATTR: str = "_tes_slot_held"

# Default phase set for the LATERAL (Y) authority — the TES drive
# chamber.  Read via ``tuners.tes_lateral_authority_phases``.
_DEFAULT_LATERAL_AUTHORITY_PHASES = frozenset({
    "tackle_contact", "tackle_drive", "tackle_dominance",
})

# Drive-chamber sub-states (lower-cased sub_state values).
_TES_DRIVE_SUBSTATES = frozenset({
    "tackle_contact", "tackle_drive", "tackle_dominance",
})

# Line roles that hold structure (tacklers / markers / DH are excluded by
# the caller and by the role check).
_STRUCTURE_ROLE_NAMES = frozenset({
    "DEF_LINE_READY", "DEF_CHASE", "DEF_RETREAT_10",
})


# ─────────────────────────────────────────────────────────────────────
# SMALL HELPERS
# ─────────────────────────────────────────────────────────────────────

def _finite(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(float(v))


def drive_structure_enabled(tuners) -> bool:
    """True when the TES drive structure-hold authority is armed."""
    if tuners is None:
        return False
    return bool(getattr(tuners, "tes_drive_structure_enabled", True))


def _role_name(d) -> str:
    role = getattr(d, "defender_role", None)
    return getattr(role, "name", None) or str(role)


def _substate_name(world) -> str:
    sub = getattr(world, "sub_state", None)
    if sub is None:
        return str(getattr(world, "_ope_phase_label", "") or "").lower()
    return str(getattr(sub, "value", sub)).lower()


def is_tes_drive_substate(world) -> bool:
    """True during the TES drive chamber (CONTACT / DRIVE / DOMINANCE)."""
    return _substate_name(world) in _TES_DRIVE_SUBSTATES


def is_lateral_authority_phase(world, tuners) -> bool:
    """True when the PHASE-GATED LATERAL (Y) authority may act this frame.

    The DEPTH (X) hold and the LATERAL (Y) authority are SEPARATE
    authorities.  Depth tracking (``bc.x + WIDTH_OFFSETS[role]``) is
    always live during the drive chamber; the lateral authority — the
    line-width clamp and the anticipation micro-adjustment that write the
    held cell's Y — is gated to the phase keys in
    ``tuners.tes_lateral_authority_phases``.

    A phase outside the set leaves the held cell on the body's TRUE
    drive-entry lane (``LANE_Y_ATTR``), so no phase profile can leak a
    lateral nudge into a phase that must hold its exact lane.  The
    DEFAULT set is the three TES drive sub-states, so default behaviour
    is unchanged; a narrower set (or ``frozenset()``) scopes/disables the
    lateral authority.
    """
    phases = getattr(tuners, "tes_lateral_authority_phases", None)
    if phases is None:
        phases = _DEFAULT_LATERAL_AUTHORITY_PHASES
    allowed = {str(p).lower() for p in phases}
    if not allowed:
        return False
    return _substate_name(world) in allowed


def is_tes_structure_hold(d) -> bool:
    """True when *d* CARRIES a TES structure hold stamp (raw flag read).

    Consumed by the tackle-begin glide and the two-radius attach band so
    neither BC-relative nudge can pull a structure-held body off its cell.

    NOTE: this is the STAMP, not liveness.  A hold is only VALID inside
    the live drive chamber (``hold_is_live``); the stamp can outlive the
    chamber and must then be dropped by ``reconcile_stale_holds``.  Use
    this predicate only where a stale stamp is already impossible, or
    where a one-frame over-exemption is harmless; otherwise use
    :func:`hold_is_live`.
    """
    return bool(getattr(d, HOLD_ACTIVE_ATTR, False))


def hold_is_live(world, d) -> bool:
    """True when *d*'s structure hold belongs to the LIVE drive chamber.

    A structure hold is a CHAMBER-SCOPED state, so the stamp alone is not
    enough to act on.  The hold is live only when ALL THREE hold:

      1. the body carries the stamp (``HOLD_ACTIVE_ATTR``);
      2. the body's chamber stamp equals the live chamber seq
         (``BODY_CHAMBER_ATTR == CHAMBER_ID_ATTR``) — a stamp from a DEAD
         tackle is not live; and
      3. the engine is currently inside the drive substate.

    WHY THIS PREDICATE EXISTS (2026-09-12)
    --------------------------------------
    ``clear_tes_drive_structure`` — the nominal chamber-exit writer — is
    NOT reliably invoked.  The defence manager reaches it only from the
    non-drive branch of ``update_tes_drive`` AFTER a ``is_tackle_phase``
    early return, and that predicate stays True across RELEASE/PTB, so in
    a live 8-tackle run the function was called ZERO times while
    ``apply_tes_drive_structure`` ran 1,455 times.  Every hold therefore
    outlived its chamber: measured, 12 bodies held across 2,209 frames of
    RELEASE/OPEN_PLAY/PTB/PTB_EXEC, and every body held from the first
    chamber to the end of the run.

    ``_validate_chamber`` encodes the same chamber test but is only
    reachable from inside ``apply_tes_drive_structure``, which itself
    early-returns when NOT in a drive substate — so it can never drop the
    hold that outlived the chamber.  The chamber boundary must be checked
    from an always-invoked frame hook instead; that is
    :func:`reconcile_stale_holds`.
    """
    if d is None:
        return False
    if not bool(getattr(d, HOLD_ACTIVE_ATTR, False)):
        return False
    if not is_tes_drive_substate(world):
        return False
    cid = getattr(world, CHAMBER_ID_ATTR, None)
    if cid is None:
        return False
    return getattr(d, BODY_CHAMBER_ATTR, None) == cid


def reconcile_stale_holds(world) -> int:
    """Drop every structure hold that is NOT live; return how many dropped.

    THE CHAMBER-EXIT RECONCILIATION.  Called once per frame from
    ``tackle_state_gate.update_tackle_state`` (the documented once-per-
    ``OpenPlayEngine.update()`` hook) so the chamber boundary is enforced
    by the FRAME LOOP rather than by a manager branch that may never run.

    Without it, a hold stamped in tackle N survives into RELEASE / PTB /
    OPEN_PLAY and the NEXT tackle, where ``tackle_state_gate`` skipped the
    body BEFORE its radius release test (the bare ``is_tes_structure_hold``
    exemption).  ``tackle_state_active`` could then never clear on that
    body, and ``defensive_pipeline._line_defenders`` — which DROPS
    ``tackle_state_active`` bodies — excluded it from every subsequent
    line rebuild.  That is the accumulating non-participation: each tackle
    froze a few more defenders out of the rebuild population for good.
    Measured live, the rebuild pool collapsed 12 -> 10 -> 9 -> 8 -> 7 -> 5
    -> 3 -> 2 across eight successive chambers while the slot table still
    owned 11 slots (ownership without participation).

    Dropping the stale stamp is sufficient and safe: it removes the gate
    exemption so the body's TACKLE-STATE flag is released by the ordinary
    radius test on the same frame, and it re-arms the tackle-begin glide /
    two-radius attach band for that body.  It never touches a LIVE hold, so
    in-chamber behaviour (including the slot recovery hold) is unchanged.
    """
    if world is None:
        return 0
    dropped = 0
    for d in (getattr(world, "defending_team", None) or []):
        if not bool(getattr(d, HOLD_ACTIVE_ATTR, False)):
            continue
        if hold_is_live(world, d):
            continue
        clear_body_hold(d)
        dropped += 1
    return dropped


def _role_offset(tuners, d) -> float:
    offsets = getattr(tuners, "WIDTH_OFFSETS", None) or {}
    role = getattr(d, "depth_role", None)
    if role in offsets:
        return float(offsets[role])
    return float(offsets.get("D", 5.0))


def _cell_x(bc, d, tuners) -> float:
    """The BC-RELATIVE depth-role X for *d*: ``bc.x + WIDTH_OFFSETS[role]``.

    This is the single drive-depth law (identical to
    ``defensive_depth_roles._depth_role_target_x``).  It reads the LIVE
    carrier every frame, so the held line keeps its depth role relative to
    the drive instead of sitting on a stale drive-entry ruck X.
    """
    if bc is None:
        return float(getattr(d, "x", 0.0))
    return float(bc.x) + _role_offset(tuners, d)


def is_structure_hold_eligible(d) -> bool:
    """True when *d* is a line body owned by the structure-hold authority.

    Markers / DUMMY_HALF / TACKLER_* / the dynamic fullback are excluded.
    """
    if d is None:
        return False
    if bool(getattr(d, "fullback_role_active", False)):
        return False
    return _role_name(d) in _STRUCTURE_ROLE_NAMES


# ─────────────────────────────────────────────────────────────────────
# DRIVE-ENTRY CELL STATE
# ─────────────────────────────────────────────────────────────────────

def resolve_entry_ruck_x(world, bc, tuners) -> float:
    """Return (capturing once per drive) the drive-entry ruck X.

    The value is the ruck X at the first drive frame (``world.ruck_x_m``),
    falling back to the ball-carrier X when no ruck is published.  It is
    held constant for the whole chamber so non-tacklers never track the
    live carrier; :func:`clear_tes_drive_structure` drops it at RELEASE.
    """
    cached = getattr(world, ENTRY_RUCK_X_ATTR, None)
    if _finite(cached):
        return float(cached)
    ruck_x = getattr(world, "ruck_x_m", None)
    if not _finite(ruck_x):
        ruck_x = float(getattr(bc, "x", 60.0)) if bc is not None else 60.0
    value = float(ruck_x)
    setattr(world, ENTRY_RUCK_X_ATTR, value)
    return value


def _lane_source_y(world, d, tuners=None) -> float:
    """The body's TRUE lane Y at drive entry — LANE-TRUE seed.

    The structure hold locks the lane the defender **actually holds** on
    the drive-entry frame, not the re-derived count-lane chain value.

    WHY NOT ``d.frame_spacing_y`` (the pre-2026-09-11 seed): the chain
    value is restamped by ``apply_frame_spacing`` on every OPEN_PLAY
    frame against the CURRENT ruck and the CURRENT per-side count, and
    (because the count is inflated while the line is mid-tackle, and the
    carrier has moved laterally so the chain re-anchors to a different
    ``ruck_y``) it does not describe the line the defenders are actually
    holding.  Measured live on the first drive frame, every held body's
    seeded lane sat 1–15 m off its own Y and two bodies shared one lane
    (bunched on the side-width cap), so nothing was ever in-lane (nothing
    froze) and the whole line was dragged onto the wrong, compressed lanes
    across the chamber — the lateral drift + pinch the lane-true contract
    removes.

    Precedence:

      1. ``d.frame_spacing_y`` — LEGACY seed, only when
         ``tuners.tes_lane_true_seed_enabled`` is False;
      2. the achieved lane — the body's live Y on the drive-entry frame
         (default, lane-true);
      3. ``role_depth_target_y`` then the live Y — bootstrap fallbacks for
         a body with no Y yet (verifier stubs).

    In every branch the mid-drive ``world.frame_spacing_targets`` dict is
    deliberately NOT consulted: it is recomputed from a drive-depleted
    defender count and would re-open the held band outward.
    """
    if tuners is not None and not bool(
        getattr(tuners, "tes_lane_true_seed_enabled", True)
    ):
        # LEGACY seed (byte-identical to the pre-2026-09-11 behaviour).
        fy = getattr(d, "frame_spacing_y", None)
        if _finite(fy):
            return float(fy)
        ry = getattr(d, "role_depth_target_y", None)
        if _finite(ry):
            return float(ry)
        return float(getattr(d, "y", 0.0))
    # LANE-TRUE seed: the lane the defender actually holds this frame.
    y = getattr(d, "y", None)
    if _finite(y):
        return float(y)
    ry = getattr(d, "role_depth_target_y", None)
    if _finite(ry):
        return float(ry)
    return float(getattr(d, "y", 0.0))


def sync_tes_drive_chamber(world, tuners=None) -> int:
    """Drop every latch left over from a PREVIOUS tackle (idempotent).

    The chamber identity is the engine-stamped tackle sequence
    (:data:`CHAMBER_ID_ATTR`, bumped by ``_start_tackle``).  This is
    called every drive frame by :func:`apply_tes_drive_structure` and is a
    no-op unless the seq has advanced since the latches were stamped, so:

      - lanes re-seed LANE-TRUE exactly ONCE per tackle (from the
        position each body actually holds in the new chamber), and
      - the drive-entry ruck and lateral band reference are re-captured
        for the new chamber instead of describing the previous one.

    Doing this off the ENGINE-stamped seq — rather than off a flag the
    defence manager clears on a post-tackle path it may never be invoked
    on — is what makes the boundary exact.  Returns the number of bodies
    invalidated (0 when nothing was stale).

    Gated by ``tuners.tes_hold_reseed_on_chamber_gap_enabled`` (default
    True); disabled, latches survive across tackles (legacy behaviour —
    the lanes then describe the previous tackle).
    """
    if world is None:
        return 0
    if tuners is not None and not bool(getattr(
        tuners, "tes_hold_reseed_on_chamber_gap_enabled", True,
    )):
        return 0
    seq = getattr(world, CHAMBER_ID_ATTR, None)
    if seq is None:
        return 0
    if getattr(world, LATCH_SEQ_ATTR, None) == seq:
        return 0
    cleared = _drop_all_holds(world)
    setattr(world, LATCH_SEQ_ATTR, seq)
    try:
        delattr(world, ENTRY_RUCK_X_ATTR)
    except AttributeError:
        pass
    try:
        delattr(world, BAND_REF_Y_ATTR)
    except AttributeError:
        pass
    # Drop the previous chamber's SLOT TABLE so the new chamber re-censuses.
    # The census is keyed to the chamber seq, so a stale table would
    # otherwise block it.  Per-body SLOT_* stamps are left in place —
    # ``owned_slot_y`` keys on the seq, so they are inert until the new
    # census re-stamps them (ownership is never inferred from a stale stamp).
    try:
        delattr(world, SLOT_LADDER_ATTR)
    except AttributeError:
        pass
    return cleared


def open_tes_drive_chamber(world, tuners=None) -> int:
    """OPEN a new chamber: bump the tackle seq, then re-seed every latch.

    Called by the defence manager on the RISING EDGE of the tackle phase
    (``not is_tackle_phase -> is_tackle_phase``), which is the first frame
    of every tackle — an edge the manager can see regardless of which
    post-tackle path it is (or is not) invoked on.  Bumping the seq makes
    every existing latch stale, so :func:`apply_tes_drive_structure`
    re-seeds each cell LANE-TRUE from the position the body actually
    holds in the new chamber.

    Returns the number of bodies invalidated.  Idempotent with respect to
    :func:`sync_tes_drive_chamber`: whichever runs first, the other sees
    the seq already stamped and does nothing.
    """
    if world is None:
        return 0
    if tuners is not None and not bool(getattr(
        tuners, "tes_hold_reseed_on_chamber_gap_enabled", True,
    )):
        return 0
    seq = int(getattr(world, CHAMBER_SEQ_ATTR, 0) or 0) + 1
    setattr(world, CHAMBER_SEQ_ATTR, seq)
    setattr(world, CHAMBER_ID_ATTR, seq)
    return sync_tes_drive_chamber(world, tuners)


def _drop_all_holds(world) -> int:
    """Clear every body's structure-hold latch; return how many dropped."""
    cleared = 0
    for d in (getattr(world, "defending_team", None) or []):
        if bool(getattr(d, HOLD_ACTIVE_ATTR, False)):
            clear_body_hold(d)
            cleared += 1
    return cleared


def _validate_chamber(world, d) -> None:
    """Drop *d*'s latch when it belongs to a previous chamber.

    The latch is valid only while its stamped chamber id matches the live
    one.  A mismatch (the manager opened a new chamber, or the latch
    predates the chamber system) re-seeds the cell from *d*'s current
    position — the LANE-TRUE contract, per chamber, with no frame bookkeeping.
    """
    cid = getattr(world, CHAMBER_ID_ATTR, None)
    if cid is None:
        return
    if getattr(d, BODY_CHAMBER_ATTR, None) != cid:
        if bool(getattr(d, HOLD_ACTIVE_ATTR, False)):
            clear_body_hold(d)


def _seed_hold(world, d, bc, tuners) -> None:
    """Capture the static structure cell for *d* (once per drive)."""
    if bool(getattr(d, HOLD_ACTIVE_ATTR, False)):
        return
    # DEPTH (X) is BC-relative and recomputed every frame; stamped here
    # only for inspection/audit.
    setattr(d, HOLD_X_ATTR, _cell_x(bc, d, tuners))
    # RECOVERY OWNERSHIP (2026-09-12): when the body OWNS a slot in this
    # chamber, the hold's lateral cell is that slot — NOT its achieved Y.
    # Ownership is READ here, never created: a body with no slot stamp for
    # the live chamber falls back to the legacy lane-true seed (held, but
    # not owned).  The owned slot is the ONLY recovery target.
    slot_y = owned_slot_y(world, d)
    if slot_y is not None:
        lane_y = slot_y
        setattr(d, SLOT_HELD_ATTR, True)
    else:
        lane_y = _lane_source_y(world, d, tuners)
        setattr(d, SLOT_HELD_ATTR, False)
    # IMMUTABLE LANE SEED (2026-09-11): the body's TRUE lane, captured
    # once and never overwritten.  The lateral authority (phase-gated
    # clamp + anticipation stretch) mutates ONLY the EFFECTIVE cell
    # (HOLD_Y_ATTR), so an in-lane body always targets its exact lane and
    # in-lane spacing is never disrupted by a moving/re-clamping band.
    setattr(d, LANE_Y_ATTR, lane_y)
    setattr(d, HOLD_Y_ATTR, lane_y)
    setattr(d, HOLD_ACTIVE_ATTR, True)
    setattr(d, HOLD_FROZEN_ATTR, False)
    _cid = getattr(world, CHAMBER_ID_ATTR, None)
    if _cid is not None:
        setattr(d, BODY_CHAMBER_ATTR, _cid)


# ─────────────────────────────────────────────────────────────────────
# ANTICIPATION MICRO-ADJUSTMENT (bounded band stretch)
# ─────────────────────────────────────────────────────────────────────

def _anticipation_stretch(world, d, bc, tuners, base_y: float) -> float:
    """Bounded lateral micro-adjustment toward the predicted threat lane.

    Reuses the Stage 9 anticipation geometry (band limit + role gain) but
    clamps the result to ``tes_band_stretch_max_m`` so the locked band can
    only STRETCH a small amount, never re-open.  Returns 0.0 when the layer
    is disabled / out of phase / out of band.
    """
    if bc is None:
        return 0.0
    if not bool(getattr(tuners, "anticipation_enabled", True)):
        return 0.0
    from game.defence.defence_lvl_1.defensive_width import is_drive_phase
    if not is_drive_phase(world):
        return 0.0
    from game.defence.defence_lvl_1.anticipation_layer import threat_lane_y
    threat = threat_lane_y(world, bc, tuners)
    lat = float(threat) - float(base_y)
    if abs(lat) <= 1e-9:
        return 0.0
    band = float(getattr(tuners, "anticipation_band_m", 8.0) or 0.0)
    if band > 0.0 and abs(lat) >= band:
        return 0.0
    proximity = 1.0 if band <= 0.0 else max(0.0, 1.0 - abs(lat) / band)
    max_gain = float(getattr(tuners, "anticipation_max_gain_m", 1.5) or 0.0)
    gain_table = getattr(tuners, "anticipation_role_gain", None) or {}
    role = getattr(d, "depth_role", None) or "D"
    gain = float(gain_table.get(role, 0.5))
    raw = (1.0 if lat > 0.0 else -1.0) * max_gain * gain * proximity
    cap_raw = getattr(tuners, "tes_band_stretch_max_m", None)
    cap = 0.4 if cap_raw is None else float(cap_raw)
    if cap <= 0.0:
        # A zero (or negative) cap LOCKS the band: no stretch at all.
        return 0.0
    if abs(raw) > cap:
        raw = math.copysign(cap, raw)
    return raw


# ─────────────────────────────────────────────────────────────────────
# LINE-WIDTH BAND (the held line may never exceed the defensive width)
# ─────────────────────────────────────────────────────────────────────

def resolve_band_ref_y(world, bc, tuners) -> float:
    """Return the lateral band reference Y, LATCHED once per drive.

    STABLE BAND REFERENCE (2026-09-11): the defensive-width band the held
    line is clamped into must NOT follow the live ruck Y.  The engine
    re-pins ``world.ruck_y_m = bc.y`` at the CONTACT -> DRIVE transition
    (see ``open_play_engine._update_tackle_contact``), so a band centred
    on the live ruck re-centres the held line every frame and squeezes it
    inward — the reported in-lane-spacing disruption.  The reference is
    therefore resolved ONCE at drive entry and held for the whole chamber;
    :func:`clear_tes_drive_structure` drops it at RELEASE.

    When ``tuners.tes_lateral_band_ref_latched`` is False the live
    ``ruck_y_m`` is read each frame (byte-identical to the pre-2026-09-11
    clamp).
    """
    if not bool(getattr(tuners, "tes_lateral_band_ref_latched", True)):
        ruck_y = getattr(world, "ruck_y_m", None)
        if not _finite(ruck_y):
            ruck_y = float(getattr(bc, "y", 35.0)) if bc is not None else 35.0
        return float(ruck_y)
    cached = getattr(world, BAND_REF_Y_ATTR, None)
    if _finite(cached):
        return float(cached)
    ruck_y = getattr(world, "ruck_y_m", None)
    if not _finite(ruck_y):
        ruck_y = float(getattr(bc, "y", 35.0)) if bc is not None else 35.0
    value = float(ruck_y)
    setattr(world, BAND_REF_Y_ATTR, value)
    return value


def _line_width_band(world, bc, tuners):
    """Return (lo, hi) lateral Y bounds = band_ref_y ± governed half-width.

    The held line must RESPECT the defensive line width
    (``governed_width_m(world, tuners)``), so the lateral cell is clamped
    into ``band_ref_y ± half`` (plus ``tes_hold_line_width_tol_m``).
    ``band_ref_y`` is the STABLE drive-entry reference
    (:func:`resolve_band_ref_y`), not the live ruck Y, so the band does
    not re-centre mid-drive.  Returns None when the clamp is disabled
    (legacy raw-lane hold).
    """
    if not bool(getattr(tuners, "tes_hold_clamp_line_width_enabled", True)):
        return None
    band_ref = resolve_band_ref_y(world, bc, tuners)
    from game.defence.defence_lvl_1.defensive_width import governed_width_m
    half = float(governed_width_m(world, tuners)) * 0.5
    tol = float(getattr(tuners, "tes_hold_line_width_tol_m", 0.5) or 0.0)
    band = half + abs(tol)
    return (float(band_ref) - band, float(band_ref) + band)


def width_band_for(world, bc=None, tuners=None):
    """PUBLIC view of the governed-width lateral band for the drive chamber.

    Returns ``(lo, hi)`` — the Y range the held line may occupy — or None
    when the clamp is disabled.  Exposed so verifiers can distinguish the
    two legitimate outcomes of the lane-true contract:

      * a body whose held lane is INSIDE the band holds that exact lane
        (the "in-lane defenders hold perfectly" half), and
      * a body whose held lane lies OUTSIDE the governed width cannot hold
        it — it is auto-corrected IN to the band edge (the "out-of-lane
        defenders auto-correct cleanly" half), monotonically and without
        overshoot.
    """
    if bc is None:
        bc = getattr(world, "ball_carrier", None)
    if tuners is None:
        try:
            from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
            tuners = DefenceTuners()
        except Exception:
            return None
    return _line_width_band(world, bc, tuners)


# ─────────────────────────────────────────────────────────────────────
# SLOT OWNERSHIP — ONE-PASS CHAMBER-ENTRY CENSUS (2026-09-12)
# ─────────────────────────────────────────────────────────────────────
# The census is the SOLE source of slot ownership.  It runs exactly once,
# on the first drive frame of the chamber, and writes the whole ownership
# table in a single pass:
#
#   * the per-side slot ladder is the shipped count-lane model
#     (``side_lanes``) laid out for the side's census population at the
#     LATCHED entry ruck Y;
#   * every eligible line body is matched to a ladder entry ONE-TO-ONE,
#     ORDER-PRESERVING (outside -> inside), so a body already correctly
#     slotted owns exactly the lane it is standing on;
#   * the ladder records ``owner_id`` per entry, write-once.
#
# AFTER the census nothing may create, modify, transfer, or infer
# ownership: readers may only read ``SLOT_*`` / ``SLOT_LADDER_ATTR``.
# ``present`` is the ONLY mutable field (it tracks whether the census
# owner is still on the line) — ``owner_id`` / ``y`` / ``index`` never
# change for the life of the chamber.

def slot_ownership_enabled(tuners) -> bool:
    """True when the census-owned slot table is the lateral authority."""
    if tuners is None:
        return False
    return bool(getattr(tuners, "tes_slot_ownership_enabled", True))


def _census_side_bodies(defenders, ruck_y: float, side: int) -> list:
    """Eligible line bodies physically on *side* of *ruck_y* (census pool).

    The census reads LIVE position ONCE, at the entry frame, because that
    is the input that defines the slot (order-preserving match).  It is
    never consulted for ownership again after the census.
    """
    out = []
    for d in (defenders or []):
        if not is_structure_hold_eligible(d):
            continue
        y = getattr(d, "y", None)
        if not _finite(y):
            continue
        phys = -1 if float(y) < float(ruck_y) else 1
        if phys == int(side):
            out.append(d)
    return out


def _census_side_ladder(world, defenders, ruck_y: float, side: int,
                        tuners) -> list:
    """Build one side's ladder entries (innermost A -> outermost WINGER).

    SPAN CONTRACT (Stage 2, 2026-09-13): the census ladder is derived from
    the PUBLISHED span contract - the same governed 3-point width chain the
    spacing pipeline consumes - NOT from a width-free count ladder.

    DEFECT THIS FIXES: this call previously passed no ``side_width``, so
    ``side_lanes`` fell back to the flat ``tuners.defender_gap_m`` (4.0 m)
    step.  The realised TES span was therefore INVARIANT to the commanded
    width - a count-only function.  Stage 1 named this source
    ``TES_WIDTH_FREE_LADDER``; it was the dominant divergence in 426 of 735
    TES cases and the whole of the worst case (-47.00 m).  Widthless lane
    generation is no longer supported: ``side_lanes`` takes a mandatory span
    and raises ``SpanContractError`` when handed none.
    """
    from game.defence.defence_lvl_1.defensive_split_spacing import side_lanes
    from game.defence.defence_lvl_1.span_contract import (
        publish_span_contract,
    )
    group = _census_side_bodies(defenders, ruck_y, side)
    n_side = len(group)
    if n_side <= 0:
        return []
    span = publish_span_contract(world, tuners, ruck_y=ruck_y)
    lanes = side_lanes(n_side, ruck_y, side, tuners, span=span)
    ordered = sorted(
        group,
        key=lambda d: (
            -abs(float(getattr(d, "y", 0.0)) - float(ruck_y)),
            getattr(d, "player_id", 0),
        ),
    )
    entries = []
    for j in range(n_side):
        slot_y = float(lanes[j])
        body = ordered[n_side - 1 - j]
        entries.append({
            "index": int(j),
            "y": slot_y,
            "owner_id": getattr(body, "player_id", None),
            "present": True,
            "_owner": body,
        })
    return entries


def establish_slot_ownership(world, defenders, bc, tuners) -> int:
    """ONE-PASS, WRITE-ONCE chamber-entry slot census.

    Returns the number of owned slots established (0 when disabled, out of
    the drive chamber, or when this chamber's table already exists).  This
    is the only function in the engine that writes ``SLOT_*``.
    """
    if world is None or not slot_ownership_enabled(tuners):
        return 0
    seq = getattr(world, CHAMBER_ID_ATTR, None)
    if seq is None:
        return 0
    if (getattr(world, SLOT_SEQ_ATTR, None) == seq
            and isinstance(getattr(world, SLOT_LADDER_ATTR, None), dict)):
        return 0
    ruck_y = resolve_band_ref_y(world, bc, tuners)
    from game.defence.defence_lvl_1.defensive_split_spacing import LEFT, RIGHT
    sides = {}
    owned = 0
    for side in (LEFT, RIGHT):
        entries = _census_side_ladder(world, defenders, ruck_y, side, tuners)
        for e in entries:
            body = e.pop("_owner")
            setattr(body, SLOT_SEQ_ATTR, seq)
            setattr(body, SLOT_SIDE_ATTR, int(side))
            setattr(body, SLOT_INDEX_ATTR, int(e["index"]))
            setattr(body, SLOT_Y_ATTR, float(e["y"]))
            owned += 1
        sides[int(side)] = entries
    setattr(world, SLOT_LADDER_ATTR, {
        "seq": seq,
        "ruck_y": float(ruck_y),
        "sides": sides,
    })
    setattr(world, SLOT_SEQ_ATTR, seq)
    return owned


def refresh_slot_presence(world, defenders) -> None:
    """Update ONLY the ``present`` field of every ladder entry.

    ``present`` is False while the census owner is absent from the line.
    This is a presence read, not an ownership change: ``owner_id`` / ``y``
    / ``index`` are never touched and a vacant slot is never reassigned.
    """
    ladder = getattr(world, SLOT_LADDER_ATTR, None)
    if not isinstance(ladder, dict):
        return
    live = {getattr(d, "player_id", None) for d in (defenders or [])}
    for entries in (ladder.get("sides") or {}).values():
        for e in entries:
            e["present"] = e.get("owner_id") in live


def owned_slot_y(world, d):
    """Return *d*'s OWN latched slot Y, or None when it owns no slot.

    Read-only.  Ownership is never inferred from live position here — a
    body with no ``SLOT_SEQ_ATTR`` for the live chamber owns nothing and
    receives the legacy lane-true hold instead.
    """
    seq = getattr(world, CHAMBER_ID_ATTR, None)
    if seq is None:
        return None
    if getattr(d, SLOT_SEQ_ATTR, None) != seq:
        return None
    y = getattr(d, SLOT_Y_ATTR, None)
    if not _finite(y):
        return None
    return float(y)


def slot_ladder_for(world):
    """PUBLIC read-only view of the chamber slot table (verifier hook)."""
    ladder = getattr(world, SLOT_LADDER_ATTR, None)
    return ladder if isinstance(ladder, dict) else None


# ─────────────────────────────────────────────────────────────────────
# PER-FRAME APPLICATION
# ─────────────────────────────────────────────────────────────────────

def apply_tes_drive_structure(world, defenders, bc, dt, tuners) -> list:
    """Hold every eligible non-tackler line body on its structure cell.

    Returns the list of bodies held this frame.  No-op (returns ``[]``)
    when the authority is disabled, there is no carrier, or the engine is
    not inside the TES drive chamber.
    """
    if not drive_structure_enabled(tuners):
        return []
    if bc is None:
        return []
    if not is_tes_drive_substate(world):
        return []

    # CHAMBER BOUNDARY: drop any latch carried over from the previous
    # tackle (engine-stamped seq).  Idempotent inside a live chamber.
    sync_tes_drive_chamber(world, tuners)

    # ── SLOT OWNERSHIP CENSUS (2026-09-12) ──
    # ONE-PASS, WRITE-ONCE, on the chamber-entry frame.  Idempotent for the
    # rest of the chamber (the table is keyed to the chamber seq), so this
    # is the only place ownership is ever established.
    # ``refresh_slot_presence`` then updates ONLY the ``present`` flag —
    # ownership is never transferred and never re-derived from position.
    establish_slot_ownership(world, defenders, bc, tuners)
    refresh_slot_presence(world, defenders)

    tolerance = float(getattr(tuners, "tes_hold_freeze_tolerance_m", 0.5) or 0.0)
    jog_speed = float(getattr(tuners, "tes_hold_jog_speed_mps", 2.5) or 0.0)
    slot_tolerance = float(
        getattr(tuners, "tes_slot_recovery_tolerance_m", tolerance) or 0.0
    )
    slot_speed = float(
        getattr(tuners, "tes_slot_recovery_speed_mps", jog_speed) or 0.0
    )
    # PHASE-GATED LATERAL AUTHORITY: resolved ONCE per frame (not per body).
    lateral_active = is_lateral_authority_phase(world, tuners)

    held: list = []
    for d in defenders:
        if not is_structure_hold_eligible(d):
            continue
        # A slot-owned body RETAINS/RECOVERS on the slot budget; an unowned
        # body keeps the legacy lane-true hold budget.
        if bool(getattr(d, SLOT_HELD_ATTR, False)):
            body_tolerance = slot_tolerance
            body_jog = slot_speed
        else:
            body_tolerance = tolerance
            body_jog = jog_speed
        # CHAMBER VALIDATION: a latch stamped by a previous chamber belongs
        # to a DEAD tackle (the manager opens a new id per tackle) — drop it
        # so the cell re-seeds LANE-TRUE from the position held NOW.
        _validate_chamber(world, d)
        _seed_hold(world, d, bc, tuners)
        # DEPTH (X) is BC-relative and LIVE this frame; LATERAL (Y) is the
        # lane locked at drive entry.
        hold_x = _cell_x(bc, d, tuners)
        setattr(d, HOLD_X_ATTR, hold_x)
        # ── LATERAL (Y) AUTHORITY — PHASE-GATED, STABLE BAND ──
        # The body's TRUE lane is the immutable drive-entry seed
        # (LANE_Y_ATTR) and is NEVER overwritten.  The effective cell
        # starts from that lane (+ the bounded anticipation
        # micro-adjustment) and the line-width clamp only ever adjusts the
        # EFFECTIVE cell: a body whose lane is inside the band keeps its
        # exact lane (in-lane spacing preserved), while a body outside the
        # band is auto-corrected IN to the band edge (it no longer freezes
        # and jogs in).  The band is centred on the STABLE drive-entry
        # reference (``resolve_band_ref_y``), so it does not re-centre on
        # the live carrier mid-drive.  When the phase gate is closed the
        # effective cell is the true lane exactly (no lateral nudge at all).
        lane_y = float(getattr(d, LANE_Y_ATTR, getattr(d, HOLD_Y_ATTR)))
        if lateral_active:
            # WIDTH BAND (2026-09-11): the held line may never exceed the
            # governed defensive width.  The band is centred on the STABLE
            # drive-entry reference, so it never re-centres mid-drive; the
            # clamp only moves a body whose OWN lane lies outside the
            # governed width (it is corrected IN to the band edge), and
            # leaves every in-band body on its exact lane (the clamp is a
            # no-op for them).  The TRUE lane (LANE_Y_ATTR) is never
            # touched, so the lane latch stays byte-stable for the chamber.
            hold_y = lane_y + _anticipation_stretch(
                world, d, bc, tuners, lane_y,
            )
            _band = _line_width_band(world, bc, tuners)
            if _band is not None:
                _lo, _hi = _band
                if hold_y < _lo:
                    hold_y = _lo
                elif hold_y > _hi:
                    hold_y = _hi
        else:
            hold_y = lane_y
        # Stamp the EFFECTIVE cell so the hold stamp, the audit probes and
        # the freeze test all read ONE authoritative Y.  The TRUE lane
        # (LANE_Y_ATTR) is left intact for the next frame's re-evaluation.
        setattr(d, HOLD_Y_ATTR, hold_y)

        # The cell owns both axes: DEPTH (X) BC-relative and live, LATERAL
        # (Y) locked at drive entry.  No depth lock is used.
        d.depth_locked = False
        d.depth_lock_x = None
        d.depth_lock_y = None
        d.target_source = TES_DRIVE_HOLD_SOURCE

        err = math.hypot(float(d.x) - hold_x, float(d.y) - hold_y)
        if err <= body_tolerance:
            # IN POSITION -> freeze (movement hold: zero velocity, pin target).
            d.vx = 0.0
            d.vy = 0.0
            d.target_x = float(d.x)
            d.target_y = float(d.y)
            d.move_speed = 0.0
            d.movement_state = MovementState.HOLD
            setattr(d, HOLD_FROZEN_ATTR, True)
        else:
            # OUT OF POSITION -> jog toward the depth role (a destination).
            d.target_x = hold_x
            d.target_y = hold_y
            ceiling = float(getattr(d, "max_speed_mps", body_jog) or body_jog)
            d.move_speed = min(body_jog, ceiling) if body_jog > 0.0 else 0.0
            d.movement_state = MovementState.DRIFT
            setattr(d, HOLD_FROZEN_ATTR, False)
        held.append(d)
    return held


# ─────────────────────────────────────────────────────────────────────
# RELEASE — instant unfreeze + live-ruck depth retarget
# ─────────────────────────────────────────────────────────────────────

def clear_body_hold(d) -> None:
    """Drop every structure-hold stamp from *d* (RELEASE / ineligible)."""
    if d is None:
        return
    for attr in (HOLD_ACTIVE_ATTR, HOLD_X_ATTR, HOLD_Y_ATTR, HOLD_FROZEN_ATTR,
                 LANE_Y_ATTR, BODY_CHAMBER_ATTR, SLOT_HELD_ATTR):
        try:
            delattr(d, attr)
        except AttributeError:
            pass


def retarget_retreat_10_live_ruck(world, tuners) -> None:
    """Switch DEF_RETREAT_10 depth targeting to the LIVE ruck X (RELEASE).

    At RELEASE the ruck is rewritten (``_prepare_release``) — the wall
    reforms off the NEW ruck, so each DEF_RETREAT_10 body's depth stamp is
    re-pointed to ``live_ruck_x + WIDTH_OFFSETS[depth_role]``.  Every other
    line role is re-stamped by the depth-role authority on the next frame.
    """
    live_x = getattr(world, "ruck_x_m", None)
    if not _finite(live_x):
        return
    for d in (getattr(world, "defending_team", None) or []):
        if _role_name(d) != "DEF_RETREAT_10":
            continue
        d.role_depth_target_x = float(live_x) + _role_offset(tuners, d)


def clear_tes_drive_structure(world, tuners=None) -> int:
    """Instantly unfreeze the whole line and drop the drive-entry ruck.

    Called on the frame the engine leaves the drive chamber.  Every held
    body loses its structure stamps; when at least one body was genuinely
    held (the DOMINANCE -> RELEASE transition), the DEF_RETREAT_10 depth
    target is re-pointed to the LIVE ruck so the wall reforms off the new
    ruck.  The drive-entry ruck capture is cleared so the next drive
    reseeds from the new ruck.  Returns the number of bodies released.
    """
    if world is None:
        return 0
    cleared = 0
    for d in (getattr(world, "defending_team", None) or []):
        if bool(getattr(d, HOLD_ACTIVE_ATTR, False)):
            clear_body_hold(d)
            cleared += 1
    if cleared > 0:
        retarget_retreat_10_live_ruck(world, tuners)
    try:
        delattr(world, ENTRY_RUCK_X_ATTR)
    except AttributeError:
        pass
    # Drop the STABLE LATERAL BAND REFERENCE so the next drive reseeds it
    # from the new (post-tackle) ruck Y.
    try:
        delattr(world, BAND_REF_Y_ATTR)
    except AttributeError:
        pass
    return cleared
