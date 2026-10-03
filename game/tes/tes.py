"""
game/tes/tes.py — Simplified TES physics functions.

The old TES class with its own FSM has been replaced by inline phase handling
in OpenPlayEngine. Only pure physics helper functions remain here.

This file now also provides the central TES entry function:

    tes_start_tackle(bc, defender, world) -> TESContext

All modules MUST use this function as the single entry point for TES.
No other code path may perform TES entry steps directly.
"""

import math

from .tes_context import (
    TESContext,
    PlayerContext,
    TackleEngagement,
    DEF_STRENGTH_MOMENTUM_MULT,
    DEF_AGGRESSION_MOMENTUM_MULT,
    DEF_TACKLE_SKILL_MOMENTUM_MULT,
    DEF_MASS_MOMENTUM_MULT,
)
from .tes_state import TESPhase
from player.player import DefenderRole


# ═══════════════════════════════════════════════════════════════════════
# LEGACY TACKLE-AUDIT DIAGNOSTICS (Block 1 cleanup)
# ═══════════════════════════════════════════════════════════════════════
# The per-tackle prints below were built for the old tackle geometry and
# produced misleading BC/ruck/drive readings.  They are DISABLED by default.
# Flip to True ONLY for isolated TES-entry diagnosis.
DEBUG_TES_ENTRY: bool = False

# ═══════════════════════════════════════════════════════════════════════
# DOMINANT-FROM-BEHIND GATE (2026-08-06)
# ═══════════════════════════════════════════════════════════════════════
# A defender may only earn a DOMINANT tackle (defender-wins drive, backward
# pushback) when they hit the BC from in front or level -- never while
# chasing from behind or coming from a pure side angle.  These thresholds
# are consumed by can_be_dominant() at TES entry.
#
#   DOMINANT_BEHIND_TOLERANCE_M: defender may be up to this far BEHIND the
#       BC (along the attack axis) and still count as "in front or level".
#   DOMINANT_ANGLE_LIMIT_DEG: max |angle| of the vector defender->BC relative
#       to the attack axis (0 = straight ahead).  Side/back angles beyond
#       this are not eligible for DOMINANT.
DOMINANT_BEHIND_TOLERANCE_M: float = 0.5
DOMINANT_ANGLE_LIMIT_DEG: float = 80.0


# ------------------------------------------------------------------
# DOMINANT-FROM-BEHIND GATE (2026-08-06) — pure geometric check
# ------------------------------------------------------------------

def can_be_dominant(defender, bc, team_attack_dir=1):
    """Dominant tackle only allowed if defender is in front or level,
    not chasing from behind.

    Pure geometric rule evaluated once at TES entry.  The pitch is
    two-directional, so all geometry is measured along the team attack
    axis (team_attack_dir = +1 attacks toward +X, -1 toward -X):

        in_front : defender is at or ahead of the BC along the attack
                   axis (small tolerance for "level").
        angle    : |angle| of the vector defender->BC relative to the
                   attack axis (0 = straight ahead).  Side/back angles
                   beyond DOMINANT_ANGLE_LIMIT_DEG are not eligible
                   for DOMINANT.

    Returns True only when the defender hits from in front/level AND
    the approach angle is within the limit.
    """
    # Defender must be at or ahead of BC along the attack axis to be
    # eligible.  Small tolerance means "level" still qualifies.
    along = (bc.x - defender.x) * team_attack_dir
    in_front = along >= -DOMINANT_BEHIND_TOLERANCE_M

    # Angle check (defender not coming from pure side/back).
    dx_att = (bc.x - defender.x) * team_attack_dir
    dy = bc.y - defender.y
    angle = math.atan2(dy, dx_att)  # 0 = straight ahead along attack axis
    good_angle = abs(angle) < math.radians(DOMINANT_ANGLE_LIMIT_DEG)

    return in_front and good_angle


# ------------------------------------------------------------------
# Public entry — the ONLY way to start a TES tackle engagement
# ------------------------------------------------------------------

def tes_start_tackle(bc, defender, world):
    """
    Initialise a TES tackle engagement.

    This is the single entry point for all TES operations.  The entry
    sequence is carefully ordered to ensure correct pipeline behaviour::

       1.  Establish primary tackler
       2a. Movement freeze
       2b. Physics freeze
       3.  Snapshot positions (bc_x0, bc_y0, def_x0, def_y0)
       4.  Snapshot velocities and facing/rotation
       5.  Create TESContext, set phase = CONTACT_START
       6.  Store entry_time = world.sim_time_s
       7.  Set tes_locked on BC and defender
       8.  Set in_contact flags and is_tackler
       9.  Set animation state to "TES_CONTACT"
      10.  Set joiner_gate_open = False (joiners NOT allowed during entry)
      11.  Set world.tes_active = True (halts non‑TES systems)
      12.  Return TESContext for sustain processing

    Parameters
    ----------
    bc : Player
        The ball carrier (attacker).
    defender : Player
        The primary tackler (defender).
    world : WorldState
        The current world snapshot (used for sim_time, tackle_count, etc.).

    Returns
    -------
    TESContext
        Fully initialised TES context at CONTACT_START phase.
    """
    # ═══ DIAGNOSTIC: BC position before any TES modifications (disabled, Block 1) ═══
    if DEBUG_TES_ENTRY:
        print(f"[BC_POS_BEFORE_TES] bc.x={bc.x:.3f} bc.y={bc.y:.3f}", flush=True)

    # ═══ DIAGNOSTIC: which defender actually triggered TES ═══
    if DEBUG_TES_ENTRY:
        print(f"[TES_START] bc={bc.player_id} primary={defender.player_id}", flush=True)

    # ----------------------------------------------------------------
    # STEP 1 — Establish primary tackler
    # ----------------------------------------------------------------
    eng = TackleEngagement()
    eng.started_at = world.sim_time_s

    # ── CONTACT-CONTRACT HANDOFF (2026-09-15) ──
    # SPEED-AUTHORITY CONSOLIDATION.  The primary tackler's TES velocity is
    # the PRE-TACKLE CORRIDOR's governed approach velocity -- the value the
    # corridor integrated and stamped on its final frame -- NOT whatever any
    # other writer left on ``defender.vx / vy`` between the corridor and this
    # call.  ``contact_velocity`` returns the live velocity when the body
    # carries no corridor stamp (or ``pretackle_handoff_enabled`` is off), so
    # a body that never entered the corridor is byte-identical.
    #
    # The latch is released here: the corridor's authority ENDS at
    # TACKLE_INIT, so the body must stop claiming corridor ownership for the
    # TES phases that follow (TES owns tackler movement from here).
    from game.defence.defence_lvl_1.pretackle_corridor import (
        clear_corridor as _clear_corridor,
        contact_velocity as _corridor_contact_velocity,
    )

    def _corridor_tuners():
        _ope = getattr(world, "open_play_engine", None)
        _t = getattr(_ope, "tuners", None)
        if _t is None:
            _t = getattr(world, "tuners", None)
        return _t

    deff = _make_ctx(
        defender,
        velocity=_corridor_contact_velocity(defender, _corridor_tuners()),
    )
    _clear_corridor(defender)
    att = _make_ctx(bc)

    eng.add(deff, eng.started_at)

    # ----------------------------------------------------------------
    # STEP 2a — Movement freeze  (velocity and target-seeking stop)
    # ----------------------------------------------------------------
    bc.frozen = True
    defender.frozen = True

    # ═══ ROLE ASSIGNMENT — align with movement_contracts.py ═══
    # BC must be ATT_CARRIER during TES so it resolves to Role.ATT_CARRIER
    # (allowed to move).  Primary tackler must be TACKLER_1 (allowed to move).
    # Non-BC attackers are suppressed separately in OPE._start_tackle().
    bc.attack_role = "ball_carrier"
    defender.defender_role = DefenderRole.TACKLER_1

    # ----------------------------------------------------------------
    # STEP 2b — Physics freeze  (no displacement, no forces)
    # ----------------------------------------------------------------
    # Same `frozen` flag handles both movement and physics freeze.
    # A future refactor may split into independent flags.

    # ----------------------------------------------------------------
    # STEP 3 — Snapshot positions
    # ----------------------------------------------------------------
    bc_x0 = bc.x
    bc_y0 = bc.y
    def_x0 = defender.x
    def_y0 = defender.y

    # ═══ DIAGNOSTIC: verify snapshot matches BC_POS_BEFORE_TES ═══
    if DEBUG_TES_ENTRY:
        print(f"[SNAPSHOT] drive_start_x={bc_x0:.3f}  (vs pre-TES bc.x={bc.x:.3f})")

    # ----------------------------------------------------------------
    # STEP 4 — Snapshot velocities and facing/rotation
    #           (already captured in _make_ctx above via PlayerContext)
    # ----------------------------------------------------------------

    # ----------------------------------------------------------------
    # STEP 5 — Create TESContext and set phase = CONTACT_START
    # ----------------------------------------------------------------
    ctx = TESContext()
    ctx.attacker = att
    ctx.defender = deff
    ctx.engagement = eng
    ctx.phase = TESPhase.CONTACT_START
    ctx.stability = 1.0
    ctx.collapsed = False
    ctx.release_timer = 0.0
    ctx.ptb_ready = False

    # ----------------------------------------------------------------
    # STEP 6 — Store entry_time
    # ----------------------------------------------------------------
    ctx.entry_time = world.sim_time_s

    # ----------------------------------------------------------------
    # STEP 7 — TES lock  (marks players as owned by TES system)
    # ----------------------------------------------------------------
    bc.tes_locked = True
    defender.tes_locked = True

    # ----------------------------------------------------------------
    # STEP 8 — Contact flags
    # ----------------------------------------------------------------
    bc.in_contact = True
    defender.in_contact = True
    defender.is_tackler = True

    # ----------------------------------------------------------------
    # STEP 9 — Animation state to TES_CONTACT
    # ----------------------------------------------------------------
    bc.anim_state = "TES_CONTACT"
    defender.anim_state = "TES_CONTACT"

    # ----------------------------------------------------------------
    # STEP 10 — Joiner gate CLOSED
    # ----------------------------------------------------------------
    # Joiners are NOT allowed during the initial CONTACT_START frame.
    # The gate will be opened by the CONTACT_START → CONTACT transition
    # in OpenPlayEngine so _maybe_add_joiners() can register secondaries
    # only after the primary engagement is fully locked down.
    ctx.joiner_gate_open = False

    # ----------------------------------------------------------------
    # STEP 11 — Populate remaining TESContext fields
    # ----------------------------------------------------------------
    ctx.contact_x_m = bc_x0
    ctx.contact_y_m = bc_y0
    ctx.bc_x0 = bc_x0
    ctx.bc_y0 = bc_y0
    ctx.def_x0 = def_x0
    ctx.def_y0 = def_y0
    ctx.drive_start_x = bc_x0
    ctx.approach_angle_deg = _approach_angle(bc, defender)
    ctx.tackle_number = max(1, getattr(world, "tackle_count", 0) + 1)
    ctx.dist_from_tryline_m = 100.0 - bc.x
    ctx.initial_impact_score = 1.5
    ctx.randomness = 0.7
    ctx.fatigue = 1.0
    ctx.max_joiners = 3

    # ----------------------------------------------------------------
    # STEP 12 — Record team attack direction for drive distance
    # ----------------------------------------------------------------
    ctx.team_attack_dir = world.attacking_direction

    # ----------------------------------------------------------------
    # STEP 12z — DOMINANT-FROM-BEHIND GATE (2026-08-06): snapshot the
    # geometric eligibility for a defender to earn a DOMINANT result.
    # Evaluated from positions BEFORE entry freezes the pipeline, so
    # the live attacker/tackler X/Y are the last reliable readings.
    # Downstream (compute_dominance) caps DOMINANT -> NEUTRAL when this
    # is False, killing "dominant from behind" backward drives.
    # ----------------------------------------------------------------
    ctx.defender_can_dominate = can_be_dominant(defender, bc, ctx.team_attack_dir)
    ctx.can_dominate_in_front = (
        (bc.x - defender.x) * ctx.team_attack_dir >= -DOMINANT_BEHIND_TOLERANCE_M
    )
    ctx.can_dominate_angle_deg = abs(
        math.degrees(math.atan2(
            bc.y - defender.y,
            (bc.x - defender.x) * ctx.team_attack_dir,
        ))
    )

    # ----------------------------------------------------------------
    # STEP 12a — MOMENTUM GATE (2026-08-06): snapshot pre-contact BC
    # momentum BEFORE tes_start_tackle freezes the movement pipeline.
    # The BC's real velocity here is the last reliable pre-contact
    # reading; every downstream drive/distance writer derives from this.
    # ----------------------------------------------------------------
    from .tes_drive_variation import compute_bc_momentum_factor
    ctx.bc_momentum_factor = compute_bc_momentum_factor(bc)
    # Flat-footed diagnostic record (advice for later tuning only —
    # the smooth momentum_factor drives behaviour, not this flag).
    bc_spd_pre = math.hypot(getattr(bc, "vx", 0.0), getattr(bc, "vy", 0.0))
    step_phase = getattr(bc, "step_phase", None)
    step_str = step_phase.name if hasattr(step_phase, "name") else str(step_phase)
    ctx.bc_precontact_vx = getattr(bc, "vx", 0.0)
    ctx.bc_precontact_accel = getattr(bc, "accel", getattr(bc, "ax", 0.0))
    ctx.bc_precontact_step = step_str
    ctx.bc_flatfooted = (
        getattr(bc, "vx", 0.0) < 0.8
        and getattr(bc, "accel", getattr(bc, "ax", 0.0)) < 0.5
        and step_str in ("PLANT", "RECOVER")
    )

    # ----------------------------------------------------------------
    # STEP 13 — Halt non‑TES systems
    # ----------------------------------------------------------------
    world.tes_active = True

    # ----------------------------------------------------------------
    # STEP 13a — Register tackler ownership
    # ----------------------------------------------------------------
    # Track the primary (and later joiners) in world.tackler_ids so
    # defensive behaviour can distinguish TES tacklers from non-tacklers
    # during the engagement.  Joiners append their ids in the joiner
    # registration paths (OpenPlayEngine._start_tackle / _maybe_add_joiners,
    # DefensiveBehaviourUnified pre-tackle joiners).
    if not hasattr(world, "tackler_ids"):
        world.tackler_ids = set()
    world.tackler_ids = set()
    world.tackler_ids.add(defender.player_id)

    # ----------------------------------------------------------------
    # STEP 14 — STAGE 2 HANDOVER: TES TAKES OWNERSHIP OF THE CONTEST
    # ----------------------------------------------------------------
    # Contact Authority FORMED this contest and latched the persistent
    # TackleState at its declaration; TES now becomes the sole authority for
    # EVOLVING it from those captured initial conditions.  It does NOT create
    # the contest: ``mark_tes_ownership`` is a NO-OP when no contest was
    # formed, which is what keeps the engine's own historical dispatch (the
    # unreachable ``_disabled_*`` copy, which reaches TES without going
    # through Contact Authority) behaving exactly as it did before.
    #
    # ``deff`` carries the GOVERNED contact velocity — the pre-tackle
    # corridor's stamped approach speed handed over a few statements above —
    # so the velocity TES actually drives from is recorded ON the contest
    # rather than being lost at the boundary.
    from .tackle_state import mark_tes_ownership as _mark_tes_ownership
    _mark_tes_ownership(world, bc, defender, (deff.vx, deff.vy))

    # ----------------------------------------------------------------
    # STEP 15 — MULTI-BODY WRAP MODEL (2026-09-23): TES CONSUMES THE
    #           COMPLETE DECLARATION IT WAS STARTED ON
    # ----------------------------------------------------------------
    # The declaration Contact Authority assembled carries the winner AND every
    # in-radius body the authority never evaluated.  That complete record is
    # resolved HERE and nowhere else, and ONE frozen model is derived from it
    # and attached to this context — so the collaborative pressure a tackle was
    # started with can never change under it mid-phase.
    #
    # MEMBERSHIP IS THE RECORD'S ANSWER: which body wrapped the carrier and
    # which body held a role outside the radius are facts the declaration
    # already carries, and nothing here re-measures a radius to re-derive them.
    # The bodies are read LIVE, because an impact vector is a measurement.
    #
    # This changes NOTHING unless a record naming THIS defender resolves and at
    # least one body contributes: every consumer below is a no-op on an absent
    # model, so a frame that declared no contest, a record that cannot be
    # matched, and the ``RLM_TES_MULTI_BODY=0`` rollback arm all leave the
    # physics exactly as it was before this stage.
    _attach_wrap_model(ctx, bc, defender, world)

    # ----------------------------------------------------------------
    # STEP 13b — Return TESContext
    # ----------------------------------------------------------------
    return ctx


# ------------------------------------------------------------------
# MULTI-BODY HELPERS (2026-09-23)
# ------------------------------------------------------------------
# The resolution + derivation half of the multi-body wrap model.  Every one of
# these is a pure read of the declaration and of the live bodies it NAMES;
# none of them decides anything, and all of them are safe to call on a world
# that declared no contest at all.

def _resolve_body(world, player_id: int):
    """The live body carrying *player_id* on *world*, or None.

    A pure read of the world's declared player list, mirroring
    ``tes_drive_v1._resolve_body`` so every consumer of a contest names the
    same bodies.
    """
    if world is None or player_id is None:
        return None
    try:
        wanted = int(player_id)
    except (TypeError, ValueError):
        return None
    for body in (getattr(world, "players", None) or ()):
        try:
            if int(getattr(body, "player_id", -1) or -1) == wanted:
                return body
        except (TypeError, ValueError):
            continue
    return None


def _record_rows(record) -> tuple:
    """The record's candidate rows, as a tuple (never raises)."""
    if record is None:
        return ()
    try:
        return tuple(getattr(record, "candidates", None) or ())
    except TypeError:
        return ()


def _bodies_for_record(world, record, bc, defender) -> dict:
    """``{player_id: body}`` for every body the record NAMES.

    The carrier and the primary defender are seeded from the live objects TES
    was handed, so the declared PAIR always resolves even when the world's
    player list cannot answer for it; every other row the record names goes
    through the world's own list.
    """
    bodies = {}
    for body in (bc, defender):
        if body is None:
            continue
        pid = int(getattr(body, "player_id", -1) or -1)
        if pid >= 0:
            bodies[pid] = body
    for row in _record_rows(record):
        pid = int(getattr(row, "player_id", -1) or -1)
        if pid in bodies:
            continue
        live = _resolve_body(world, pid)
        if live is not None:
            bodies[pid] = live
    return bodies


def _declaration_record(world, defender):
    """THE record of the declaration this contest was started on (or None).

    The declaration's identity is ``(frame, seq)`` — the frame the world
    reports and the sequence the authority published — so the lookup is exact.
    The record currently published on the world is the declared fallback.

    EITHER result must NAME THIS DEFENDER as its winner.  Without that check a
    record left over from an earlier contest could be consumed as this one's,
    and the engine's historical bare-defender dispatch — which reaches TES
    without any declaration, and is exactly the path that must stay
    bit-identical — would be handed somebody else's evidence.  When neither
    lookup resolves, TES has no declaration to consume, and consumes none.
    """
    from game.defence.defence_lvl_1 import contact_authority as _ca
    from game.defence.defence_lvl_1 import contest_preservation as _cp

    defender_id = int(getattr(defender, "player_id", -1) or -1)
    frame = int(getattr(world, "frame_count", 0) or 0)
    seq = int(getattr(world, _ca.CONTACT_AUTHORITY_SEQ_ATTR, 0) or 0)

    for record in (_cp.record_for(world, frame, seq),
                   _cp.last_record(world)):
        if record is None:
            continue
        winner_id = getattr(record, "winner_id", None)
        if winner_id is None:
            continue
        try:
            if int(winner_id) != defender_id:
                continue
        except (TypeError, ValueError):
            continue
        return record
    return None


def _attach_wrap_model(ctx, bc, defender, world) -> None:
    """Derive and attach THIS contest's collaborative wrap model to *ctx*.

    A NO-OP unless a complete record naming this defender resolves and at least
    one body contributes, so nothing here can perturb a frame that declared no
    contest.  The model is derived ONCE, here, from the declaration plus the
    live bodies it names; the TES loop then reads only the context and never
    the record again.
    """
    from . import tes_multi_body as _multi_body

    if not _multi_body.multi_body_enabled():
        return

    record = _declaration_record(world, defender)
    if record is None:
        return

    model = _multi_body.derive_wrap_model(
        record,
        _bodies_for_record(world, record, bc, defender),
        carrier=bc,
        attack_dir=int(getattr(ctx, "team_attack_dir", 1) or 1),
    )
    if model is None:
        return

    ctx.wrap_model = model
    ctx.wrap_force_n = float(model.wrap_force_n)
    ctx.wrap_momentum_kgmps = float(model.wrap_momentum_kgmps)
    ctx.wrap_assist_ids = tuple(model.assist_ids)
    ctx.wrap_contribution_count = len(model.contributions)
    ctx.multibody_duration_scale = (
        _multi_body.collaborative_duration_scale(model)
    )
    ctx.tackle_break_window = bool(model.break_window)
    ctx.offload_window_open = bool(model.offload_window)
    # THE RESOLVED COLLISION STATE (dynamic results pipeline, step 1).  The
    # token is DERIVED from the model by the discriminator -- never recomputed
    # here -- so the context and the model cannot disagree about which side of
    # the collision won.  Nothing in this frame's physics reads it: it is the
    # result the pipeline's next step consumes, attached at the same single
    # place (and from the same model) as every other mirror above.
    ctx.wrap_collision_state = _multi_body.resolve_collision_state(model)


# ------------------------------------------------------------------
# Internal helpers (moved from OPE to live with the TES domain)
# ------------------------------------------------------------------

def _make_ctx(p: "Player", velocity=None) -> PlayerContext:
    """Snapshot a Player into a PlayerContext for TES.

    BOOST TACKLE MOMENTUM (2026-08-05): DEFENDER RESISTANCE REDUCTION.
    When *p* is a defender (is_defender or has a DefenderRole), its
    strength / aggression / tackle_skill are scaled by 0.85 and its mass
    by 0.95 so every downstream consumer (drive-force resistance,
    dominance, drive_strength, drive_distance, break-tackle resistance)
    sees lower defender suppression.  The attacker (BC) is NEVER scaled.
    """
    c = PlayerContext()
    c.player_id = p.player_id
    c.x_m = p.x
    c.y_m = p.y
    if velocity is not None:
        # CORRIDOR HANDOFF (2026-09-15): the caller supplied the governed
        # contact velocity -- the pre-tackle corridor's stamped approach
        # velocity.  Applied to the primary tackler only; the attacker and
        # every unstamped body keep their live velocity.
        c.vx = float(velocity[0])
        c.vy = float(velocity[1])
    else:
        c.vx = p.vx
        c.vy = p.vy

    is_defender = bool(getattr(p, "is_defender", False)) or (
        getattr(p, "defender_role", None) is not None
    )

    c.mass = getattr(p, "mass_kg", 90.0)
    c.stamina = getattr(p, "stamina", 1.0)
    c.aggression = getattr(p, "aggression", 0.5)
    c.tackle_skill = getattr(p, "tackle_skill", 0.5)
    c.offload_skill = getattr(p, "offload_skill", 0.5)
    c.strength = getattr(p, "strength", 0.5)
    if is_defender:
        c.strength *= DEF_STRENGTH_MOMENTUM_MULT
        c.aggression *= DEF_AGGRESSION_MOMENTUM_MULT
        c.tackle_skill *= DEF_TACKLE_SKILL_MOMENTUM_MULT
        c.mass *= DEF_MASS_MOMENTUM_MULT
    c.facing = p.facing
    c.rot = getattr(p, "rot", 0.0)
    # state: enum or string -> always stored as string
    if p.state is None:
        c.state = "IDLE"
    elif isinstance(p.state, str):
        c.state = p.state
    else:
        c.state = p.state.name
    c.momentum = c.mass * math.hypot(c.vx, c.vy)
    return c


def _approach_angle(bc: "Player", d: "Player") -> float:
    """Angle (degrees) between defender's movement direction and BC."""
    dx = bc.x - d.x
    dy = bc.y - d.y
    dist = math.hypot(dx, dy)
    if dist < 0.01:
        return 0.0

    spd = math.hypot(d.vx, d.vy)
    if spd < 0.01:
        to_bc = math.atan2(dy, dx)
        diff = d.facing - to_bc
        return abs(math.degrees(diff))

    dxn = d.vx / spd
    dyn = d.vy / spd
    tx = dx / dist
    ty = dy / dist
    dot = max(-1.0, min(1.0, dxn * tx + dyn * ty))
    return math.degrees(math.acos(dot))
