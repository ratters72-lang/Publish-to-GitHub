"""
world/rules/movement_contracts.py — Hard-coded phase invariants for the OpenPlayEngine.

Provides:
  - MOVEMENT_RULES table: per-phase sets of which roles may be frozen or moving.
  - Role enum: unified role classification for both defenders and attackers.
  - get_role(player): maps DefenderRole / attack_role to Role.
  - apply_phase_invariants(world, phase_str): sets frozen flags BEFORE the phase handler.
  - audit_movement(world, phase_str): checks compliance AFTER the phase handler (log only).
  - enforce_bc_backward_invariant(bc, world): clamps BC velocity to avoid forward movement
    during RELEASE.
"""
print(__file__)

from enum import Enum, auto
from player.player import Player, DefenderRole

# ═══════════════════════════════════════════════════════════════════════
# LEGACY TACKLE-AUDIT DIAGNOSTICS (Block 1 cleanup)
# ═══════════════════════════════════════════════════════════════════════
# The old tackle/PTB/marker/ruck prints were built for legacy geometry and
# produced misleading readings for the new TACKLE SDEA.  Disabled by default.
# Flip to True ONLY for isolated diagnosis.
DEBUG_PHASE_INVARIANTS: bool = False

# ═══════════════════════════════════════════════════════════════════════
# ROLE ENUM (unified — covers both defender_role and attack_role)
# ═══════════════════════════════════════════════════════════════════════

class Role(Enum):
    MARKER_A = auto()
    MARKER_B = auto()
    DUMMY_HALF = auto()
    DEF_RETREAT_10 = auto()
    DEF_LINE_READY = auto()
    DEF_CHASE = auto()
    TACKLER_1 = auto()
    TACKLER_2 = auto()
    TACKLER_3 = auto()
    ATT_CARRIER = auto()
    ATT_SUPPORT = auto()
    NONE = auto()


ROLE_MAP = {
    DefenderRole.MARKER_A: Role.MARKER_A,
    DefenderRole.MARKER_B: Role.MARKER_B,
    DefenderRole.DUMMY_HALF: Role.DUMMY_HALF,
    DefenderRole.DEF_RETREAT_10: Role.DEF_RETREAT_10,
    DefenderRole.DEF_LINE_READY: Role.DEF_LINE_READY,
    DefenderRole.DEF_CHASE: Role.DEF_CHASE,
    DefenderRole.TACKLER_1: Role.TACKLER_1,
    DefenderRole.TACKLER_2: Role.TACKLER_2,
    DefenderRole.TACKLER_3: Role.TACKLER_3,
}


def get_role(player: Player) -> Role:
    """Map a Player to the unified Role enum.

    Checks ``defender_role`` first (for defending-team players),
    then ``attack_role`` (for attacking-team players).
    Returns Role.NONE if nothing matches.
    """
    dr = getattr(player, "defender_role", None)
    if dr in ROLE_MAP:
        return ROLE_MAP[dr]
    ar = getattr(player, "attack_role", "none")
    if ar == "ball_carrier":
        return Role.ATT_CARRIER
    if ar == "dummy_half":
        return Role.DUMMY_HALF
    if ar in ("support_runner", "first_receiver"):
        return Role.ATT_SUPPORT
    return Role.NONE


def _registered_tes_tackler_ids(world) -> set[int]:
    """Return the union of registered TES tackler ids (primary + joiners).

    The TES tackler registry (``world.tackler_ids`` + ``primary_tackler_id``
    + ``secondary_tacklers``) is the authoritative set of players engaged in
    the current tackle.  A registered tackler retains movement authority
    even when its ``defender_role`` tag is not yet TACKLER_1/2/3 (e.g. a
    joiner that still carries DEF_LINE_READY from the OPEN_PLAY promotion).
    """
    ids = set(getattr(world, "tackler_ids", None) or ())
    pid = getattr(world, "primary_tackler_id", None)
    if pid is not None:
        ids.add(pid)
    for sid in (getattr(world, "secondary_tacklers", None) or ()):
        ids.add(sid)
    return ids


def drive_depth_track_enabled(world) -> bool:
    """Return True when the TES drive depth-track behaviour is live.

    Canary gate (2026-09-06, DEFAULT now ON 2026-09-08-revert): when True,
    during the TES drive (CONTACT/DRIVE/DOMINANCE) the depth-locked line
    bodies (DEF_LINE_READY / DEF_CHASE / DEF_RETREAT_10) track the LIVE ball
    carrier in DEPTH (X = bc.x + WIDTH_OFFSETS[depth_role], refreshed each
    frame) while DEF_LINE_READY / DEF_CHASE hold their LATERAL (Y) at drive
    entry.  When False (canary off), the v2 hard-freeze is restored:
    DEF_LINE_READY / DEF_CHASE freeze and the line holds its position exactly
    as before — the DH + line jitter is instead suppressed by the TES drive
    authority (the depth-lock glide as the sole integrator).  Read from the
    live tuner (``DefenceTuners.tes_drive_depth_track_enabled``); stub worlds
    with no tuner default to True (the depth-track behaviour).
    """
    ope = getattr(world, "open_play_engine", None)
    tuners = getattr(ope, "def_tuners", None) if ope is not None else None
    if tuners is None:
        return True  # default: ON (depth-track) for stub worlds / verifier stubs
    return bool(getattr(tuners, "tes_drive_depth_track_enabled", True))


# ═══════════════════════════════════════════════════════════════════════
# MOVEMENT AUTHORITY PREDICATES (2026-08-31)
# ═══════════════════════════════════════════════════════════════════════
# The invariant to enforce: any defender whose movement authority is
# suppressed MUST be frozen; any defender who is unfrozen MUST have movement
# authority.  "No third state may exist."  These predicates define movement
# authority as (role in allowed_to_move) OR (an explicit freeze-authority
# exemption), mirroring the exemption logic in apply_phase_invariants exactly.

def has_movement_authority(world, phase_str: str, player: Player) -> bool:
    """Return True if *player* retains movement authority for *phase_str*.

    Movement authority is granted when either:
      - the player's unified Role is in ``MOVEMENT_RULES[phase].allowed_to_move``
        (the role-based contract), OR
      - the player falls under an explicit freeze-authority exemption:
          * the dynamic fullback — an independent backfield anchor that is
            NEVER frozen in any phase (fullback_role_active),
          * a registered TES tackler during TACKLE_INIT/CONTACT/DRIVE/DOMINANCE,
          * the ball carrier or the dummy half during the open-play + PTB
            belt-and-suspenders unfreeze block.

    This EXACTLY mirrors the exemption logic in ``apply_phase_invariants`` so
    the consolidation pass and the runtime enforcement use the same notion of
    authority as the sole freeze authority itself.
    """
    rules = MOVEMENT_RULES.get(phase_str)
    if rules is None:
        return False
    # FROZEN OVERRIDE (2026-09-10): the freeze authority's verdict outranks
    # the role table.  A body the sole freeze authority pinned MUST report no
    # movement authority, otherwise this predicate would contradict
    # apply_phase_invariants and the "frozen means frozen" invariant.
    if getattr(player, "frozen", False):
        return False
    if get_role(player) in rules["allowed_to_move"]:
        return True

    # -- TES DEPTH-LOCK EXEMPTION (2026-09-06) --
    # A depth-locked body (TES line-role defender) is authorised to move in
    # Y (lateral/lane) but is pinned in X (depth).  It is NOT fully frozen,
    # so it must retain movement authority or the no-third-state enforcement
    # in apply_phase_invariants would re-freeze it.
    if getattr(player, "depth_locked", False):
        return True

    # -- DYNAMIC FULLBACK EXEMPTION (never frozen in any phase) --
    if getattr(player, "fullback_role_active", False):
        return True

    # -- TES TACKLER EXEMPTION (registered tacklers keep movement authority) --
    if phase_str in (
        "TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE",
    ):
        if getattr(player, "player_id", None) in _registered_tes_tackler_ids(world):
            return True

    # -- BELT-AND-SUSPENDERS: BC / DH unfreeze in open-play + PTB phases --
    if phase_str in ("OPEN_PLAY", "POST_RUCK_OPEN_PLAY", "PTB", "PTB_PREP", "PTB_EXEC"):
        if player is getattr(world, "ball_carrier", None):
            return True
        if getattr(player, "attack_role", None) == "dummy_half":
            return True

    return False


def movement_authority_suppressed(world, phase_str: str, player: Player) -> bool:
    """Return True when *player*'s movement authority is suppressed.

    ``suppressed`` is the negation of ``has_movement_authority`` — the player
    is neither on the ``allowed_to_move`` contract nor covered by an exemption.
    """
    return not has_movement_authority(world, phase_str, player)


# ═══════════════════════════════════════════════════════════════════════
# MOVEMENT RULES TABLE  (AI contract — per-phase freeze/allow)
# ═══════════════════════════════════════════════════════════════════════

MOVEMENT_RULES: dict[str, dict] = {
    # ═══════════════════════════════════════════════════════════════════
    # OPEN_PLAY — all roles free to move, no frozen restrictions.
    # ═══════════════════════════════════════════════════════════════════
    "OPEN_PLAY": {
        "must_be_frozen": set(),
        "allowed_to_move": {
            Role.DEF_LINE_READY,
            Role.DEF_CHASE,
            Role.ATT_CARRIER,
            Role.ATT_SUPPORT,
            Role.MARKER_A,
            Role.MARKER_B,
            Role.DUMMY_HALF,
            Role.DEF_RETREAT_10,
            Role.TACKLER_1,
            Role.TACKLER_2,
            Role.TACKLER_3,
            Role.NONE,
        },
    },
    # ═══════════════════════════════════════════════════════════════════
    # TES phases — DEFENDERS NEVER FREEZE DURING THE DRIVE (2026-09-10).
    #
    # TACKLE_INIT keeps the pre-contact freeze: the line bodies
    # (DEF_LINE_READY / DEF_CHASE) are hard-frozen there and the retreat
    # role (DEF_RETREAT_10) is depth-locked.
    #
    # From TACKLE_CONTACT through TACKLE_DOMINANCE **no defensive role is
    # frozen and nothing is depth-locked**: DEF_LINE_READY / DEF_CHASE /
    # DEF_RETREAT_10 keep movement authority and keep playing their normal
    # depth-role geometry — depth (X) and lane (Y) tracked against the LIVE
    # ball carrier.  A defender outside the tackle radius therefore keeps
    # defending instead of being pinned, and the spacing / retreat /
    # pursuit writers are free to run for it.
    #
    # The TES chamber still owns the ball carrier, the registered tacklers
    # (TACKLER_1/2/3 + joiners) and the dummy half.
    #
    # MARKER_A/B do not exist during CONTACT/DRIVE/DOMINANCE — markers are
    # assigned at DOMINANCE -> RELEASE, so they are listed on the INIT
    # freeze only.  At RELEASE the freshly-assigned markers become the
    # movement authority alongside BC + DH; the ex-tacklers (TACKLER_1/2/3)
    # freeze as TES hands the ruck back to the phase handlers.
    #
    # NOTE: being in ``allowed_to_move`` is what keeps a role unfrozen —
    # ``apply_phase_invariants`` default-freezes any role that is in neither
    # ``must_be_frozen`` nor ``allowed_to_move`` (nor ``must_lock_depth``).
    # Removing a role from ``must_be_frozen`` alone would NOT unfreeze it.
    # ═══════════════════════════════════════════════════════════════════
    "TACKLE_INIT": {
        "must_be_frozen": {
            Role.MARKER_A,
            Role.MARKER_B,
            Role.ATT_SUPPORT,
            Role.NONE,
            Role.DEF_LINE_READY,
            Role.DEF_CHASE,
        },
        "must_lock_depth": {
            Role.DEF_RETREAT_10,
        },
        "allowed_to_move": {
            Role.ATT_CARRIER,
            Role.TACKLER_1,
            Role.TACKLER_2,
            Role.TACKLER_3,
            Role.DUMMY_HALF,
        },
    },
    "TACKLE_CONTACT": {
        "must_be_frozen": {
            Role.ATT_SUPPORT,
            Role.NONE,
        },
        "must_lock_depth": set(),
        "allowed_to_move": {
            Role.ATT_CARRIER,
            Role.TACKLER_1,
            Role.TACKLER_2,
            Role.TACKLER_3,
            Role.DUMMY_HALF,
            Role.DEF_LINE_READY,
            Role.DEF_CHASE,
            Role.DEF_RETREAT_10,
        },
    },
    "TACKLE_DRIVE": {
        "must_be_frozen": {
            Role.ATT_SUPPORT,
            Role.NONE,
        },
        "must_lock_depth": set(),
        "allowed_to_move": {
            Role.ATT_CARRIER,
            Role.TACKLER_1,
            Role.TACKLER_2,
            Role.TACKLER_3,
            Role.DUMMY_HALF,
            Role.DEF_LINE_READY,
            Role.DEF_CHASE,
            Role.DEF_RETREAT_10,
        },
    },
    "TACKLE_DOMINANCE": {
        "must_be_frozen": {
            Role.ATT_SUPPORT,
            Role.NONE,
        },
        "must_lock_depth": set(),
        "allowed_to_move": {
            Role.ATT_CARRIER,
            Role.TACKLER_1,
            Role.TACKLER_2,
            Role.TACKLER_3,
            Role.DUMMY_HALF,
            Role.DEF_LINE_READY,
            Role.DEF_CHASE,
            Role.DEF_RETREAT_10,
        },
    },
    # ═══════════════════════════════════════════════════════════════════
    # RELEASE — TES hands back to OPE and the TES isolation freeze is
    # dropped the moment RELEASE begins.  The retreat line (DEF_RETREAT_10)
    # unfreezes immediately and enters forced-retreat to ruck_x + 10.
    # Only the ball carrier, the dummy half, and the freshly-assigned
    # MARKER_A/B retain their own movement authority (markers must reach
    # the ruck_x+1 / ruck_x+2 targets).  Ex-tacklers (TACKLER_1/2/3),
    # DEF_LINE_READY / DEF_CHASE role tags, ATT_SUPPORT and NONE stay
    # hard-frozen; the fullback is handled by its own guard.
    # ═══════════════════════════════════════════════════════════════════
    "RELEASE": {
        "must_be_frozen": {
            Role.TACKLER_1,
            Role.TACKLER_2,
            Role.TACKLER_3,
            Role.DEF_LINE_READY,
            Role.DEF_CHASE,
            Role.ATT_SUPPORT,
            Role.NONE,
        },
        "allowed_to_move": {
            Role.ATT_CARRIER,
            Role.DUMMY_HALF,
            Role.MARKER_A,
            Role.MARKER_B,
            Role.DEF_RETREAT_10,
        },
    },
    "PTB_PREP": {
        "must_be_frozen": {
            Role.DEF_LINE_READY,
            Role.DEF_CHASE,
            Role.ATT_SUPPORT,
            Role.TACKLER_1,
            Role.TACKLER_2,
            Role.TACKLER_3,
            Role.NONE,
        },
        "allowed_to_move": {
            Role.ATT_CARRIER,
            Role.DUMMY_HALF,
            Role.MARKER_A,
            Role.MARKER_B,
            Role.DEF_RETREAT_10,
        },
    },
    "PTB_EXEC": {
        "must_be_frozen": {
            Role.DEF_LINE_READY,
            Role.DEF_CHASE,
            Role.ATT_SUPPORT,
            Role.TACKLER_1,
            Role.TACKLER_2,
            Role.TACKLER_3,
            Role.NONE,
        },
        "allowed_to_move": {
            Role.ATT_CARRIER,
            Role.DUMMY_HALF,
            Role.MARKER_A,
            Role.MARKER_B,
            Role.DEF_RETREAT_10,
        },
    },
    "PTB": {
        "must_be_frozen": {
            Role.DEF_LINE_READY,
            Role.DEF_CHASE,
            Role.ATT_SUPPORT,
            Role.TACKLER_1,
            Role.TACKLER_2,
            Role.TACKLER_3,
            Role.NONE,
        },
        "allowed_to_move": {
            Role.ATT_CARRIER,
            Role.DUMMY_HALF,
            Role.MARKER_A,
            Role.MARKER_B,
            Role.DEF_RETREAT_10,
        },
    },
    # ═══════════════════════════════════════════════════════════════════
    # POST_RUCK_OPEN_PLAY — all roles free to move.
    # Marker freeze timer (applied in phase handler) is business logic
    # on specific players, not a freeze regime for the whole phase.
    # ═══════════════════════════════════════════════════════════════════
    "POST_RUCK_OPEN_PLAY": {
        "must_be_frozen": set(),
        "allowed_to_move": {
            Role.DEF_LINE_READY,
            Role.DEF_CHASE,
            Role.DEF_RETREAT_10,
            Role.MARKER_A,
            Role.MARKER_B,
            Role.DUMMY_HALF,
            Role.ATT_CARRIER,
            Role.ATT_SUPPORT,
            Role.TACKLER_1,
            Role.TACKLER_2,
            Role.TACKLER_3,
            Role.NONE,
        },
    },
}


# ═══════════════════════════════════════════════════════════════════════
# HELPER: all players from world
# ═══════════════════════════════════════════════════════════════════════

def _all_players(world) -> list[Player]:
    """Return all players (attacking + defending + ball carrier)."""
    players: list[Player] = []
    if hasattr(world, "defending_team"):
        players.extend(world.defending_team)
    if hasattr(world, "attacking_team"):
        players.extend(world.attacking_team)
    bc = getattr(world, "ball_carrier", None)
    if bc is not None and bc not in players:
        players.append(bc)
    return players


# ═══════════════════════════════════════════════════════════════════════
# APPLY (BEFORE phase handler) — set frozen flags from contract
# ═══════════════════════════════════════════════════════════════════════

def apply_phase_invariants(world, phase_str: str) -> None:
    """Apply per-phase freeze invariants — THE SOLE FREEZE AUTHORITY.

    Called ONCE per frame BEFORE the phase handler runs.
    Sets ``frozen`` deterministically for every player based on their role
    and the current phase.  No other system may change ``frozen``.

    Roles in ``must_be_frozen`` get ``frozen = True``.
    Roles in ``allowed_to_move`` get ``frozen = False``.
    All other roles default to ``frozen = True``.
    """
    rules = MOVEMENT_RULES.get(phase_str)
    if rules is None:
        return

    if DEBUG_PHASE_INVARIANTS:
        print("APPLY_PHASE_INVARIANTS ACTIVE", phase_str)

    must_frozen: set[Role] = rules["must_be_frozen"]
    locked_depth: set[Role] = rules.get("must_lock_depth", set())
    allowed: set[Role] = rules["allowed_to_move"]

    for p in _all_players(world):
        role = get_role(p)

        if role in must_frozen:
            p.frozen = True
            p.depth_locked = False
        elif role in locked_depth:
            # ── TES DEPTH-LOCK (2026-09-06) ──
            # The line-role body is NOT fully frozen: it is constrained in
            # X (depth) while retaining a defined lateral behaviour.
            #
            # DRIVE-DEPTH TRACK (2026-09-06): during the TES drive
            # (CONTACT/DRIVE/DOMINANCE) depth_lock_x is refreshed each frame
            # to the LIVE BC-tracked value (bc.x + WIDTH_OFFSETS[depth_role])
            # by the drive handler (stamp_drive_depth_x), so the line moves
            # back with the driven carrier.  depth_lock_y is the drive-
            # lateral hold: DEF_LINE_READY / DEF_CHASE (the lock roles) pin
            # Y to the Y they had at drive entry (no lateral blow-out);
            # DEF_RETREAT_10 (the wall-reforming retreat role) leaves it
            # None so Y stays free to slide.  The dynamic fullback (which
            # carries DEF_LINE_READY) is exempt — it owns its own backfield
            # anchor and is never depth-locked.
            p.frozen = False
            if getattr(p, "fullback_role_active", False):
                p.depth_locked = False
                p.depth_lock_y = None
            else:
                # NOTE (2026-09-10): the drive-depth-track canary no longer
                # re-freezes anything.  DEF_LINE_READY / DEF_CHASE appear in
                # ``must_lock_depth`` only for TACKLE_INIT (pre-contact), and
                # must_lock_depth is EMPTY for CONTACT/DRIVE/DOMINANCE, so no
                # defender is pinned during the drive.
                if not getattr(p, "depth_locked", False):
                    p.depth_locked = True
                    p.depth_lock_x = float(p.x)
                if role is Role.DEF_RETREAT_10:
                    p.depth_lock_y = None
                elif getattr(p, "depth_lock_y", None) is None:
                    p.depth_lock_y = float(p.y)
        elif role in allowed:
            p.frozen = False
            # DRIVE MOVEMENT-AUTHORITY (2026-09-10): an allowed body is never
            # depth-pinned.  Clear the pin outright (not just the flag) so no
            # stale depth_lock_x / depth_lock_y survives for a later writer to
            # read on a body that is freely integrating both axes.
            p.depth_locked = False
            p.depth_lock_x = None
            p.depth_lock_y = None
        else:
            p.frozen = True
            p.depth_locked = False

        # ── TASK VI (2026-08-15): restore _mmdl_moved to FRAME SCOPE ──
        # ``_mmdl_moved`` is the MMDL's per-frame integration contract ("position
        # already integrated this frame — skip Player.update").  It was stamped
        # on the last pre-tackle OPEN_PLAY frame and survived the whole
        # TES/RELEASE/PTB freeze era.  On the next OPEN_PLAY entry frame,
        # reactivate_movement_after_thaw() short-circuits on the STALE flag
        # BEFORE its stuck predicate, so a just-thawed defender with vx=vy=0
        # and a live target is never re-integrated: the
        # verify_movement_reactivation ASSERT-A freeze (seed 3/7/10, pid 212
        # state=CHASE _mmdl_moved=True v=0).
        # This authority runs BEFORE every phase handler, so when ANY player
        # is pinned frozen here the flag is cleared — frozen players are never
        # integrated, so clearing is harmless — and the contract is frame-scoped
        # exactly as the MMDL requires.  In OPEN_PLAY (no freeze pin) the flag
        # set by the MMDL this frame survives, so the same-frame skip still works.
        if p.frozen:
            if getattr(p, "_mmdl_moved", False):
                p._mmdl_moved = False

    # -- TES TACKLER EXEMPTION (2026-08-29) --
    # During the TES engagement phases the registered tacklers (primary +
    # joiners) retain movement authority even when their defender_role tag is
    # not yet TACKLER_1/2/3 (a joiner may still carry DEF_LINE_READY from the
    # OPEN_PLAY promotion).  The sole freeze authority honours the tackler
    # registry so no registered tackler is left frozen by a stale role tag.
    # RELEASE is deliberately excluded: ex-tacklers freeze there (only
    # BC / DH / MARKER_A/B move), per the TES isolation contract.
    if phase_str in (
        "TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE",
    ):
        _tes_ids = _registered_tes_tackler_ids(world)
        if _tes_ids:
            for p in _all_players(world):
                if getattr(p, "player_id", None) in _tes_ids:
                    p.frozen = False
                    p.depth_locked = False

    # -- BELT-AND-SUSPENDERS: BC and DH must NEVER be frozen in open-play phases --
    # Even if role resolution somehow puts ATT_CARRIER or DUMMY_HALF in must_frozen,
    # override here so BC/DH can always move during open play and PTB phases.
    if phase_str in ("OPEN_PLAY", "POST_RUCK_OPEN_PLAY", "PTB", "PTB_PREP", "PTB_EXEC"):
        # Unfreeze BC
        bc = getattr(world, "ball_carrier", None)
        if bc is not None:
            bc.frozen = False
            bc.freeze_timer = 0.0
            if hasattr(bc, "tackle_locked"):
                bc.tackle_locked = False

        # Unfreeze DH (attack_role == "dummy_half")
        for p in _all_players(world):
            if getattr(p, "attack_role", None) == "dummy_half":
                p.frozen = False
                p.freeze_timer = 0.0
                if hasattr(p, "tackle_locked"):
                    p.tackle_locked = False

        # -- POST-PTB MARKER LOCK: freeze markers during lock window --
        # After PTB completes, markers are frozen for `marker_lock_duration_s`
        # seconds so the DH/BC can scoot laterally without being chased.
        lock_time = getattr(world, "marker_lock_duration_s", 0.75)
        freeze_timer = getattr(world, "marker_freeze_timer", lock_time)

        if freeze_timer < lock_time:
            # Freeze all markers
            for p in (getattr(world, "defending_team", []) or []):
                role = getattr(p, "defender_role", None)
                if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                    p.frozen = True
        else:
            # Unfreeze all markers and reset their timers
            for p in (getattr(world, "defending_team", []) or []):
                role = getattr(p, "defender_role", None)
                if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                    p.frozen = False
                    p.freeze_timer = 0.0

    # -- FULLBACK UNFREEZE (2026-08-13, made phase-unconditional 2026-09-02) --
    # The dynamic fullback is an independent backfield anchor -- it is NEVER
    # frozen by ANY phase freeze contract (DEF_LINE_READY is must_be_frozen in
    # RELEASE / PTB_PREP / PTB_EXEC / PTB).  This block runs in EVERY phase,
    # so even the RELEASE role pass (which freezes DEF_LINE_READY) cannot leave
    # the actor pinned.  has_movement_authority already grants the fullback an
    # unconditional exemption; this is the matching runtime unfreeze so the two
    # never disagree.
    # ── EMERGENCY-DEFENDER TES-OWNERSHIP EXEMPTION (B2b, 2026-09-16) ──
    # While the tagged fullback has been granted tackle authority it is a
    # REGISTERED TES TACKLER: TES owns its frozen state and its movement
    # locks for the whole chamber.  It already carries movement authority
    # through the registered-tackler branch of ``has_movement_authority``,
    # so this sweeper unfreeze is redundant for it -- and actively harmful,
    # because it would clear ``tackle_locked`` (a TES lock) mid-tackle and
    # hand the body back to the line pipeline's freeze contract.  Skip the
    # unfreeze for an authority-granted actor; the sweeper path resumes the
    # moment the authority is released.
    from game.defence.defence_lvl_1.fullback_role import (
        fullback_tackle_authority_active as _fb_authority_active,
    )
    for p in (getattr(world, "defending_team", []) or []):
        if getattr(p, "fullback_role_active", False):
            if _fb_authority_active(p):
                continue
            p.frozen = False
            p.freeze_timer = 0.0
            if hasattr(p, "tackle_locked"):
                p.tackle_locked = False

    # --- HARD OVERRIDE REMOVED (July 2026): PTB phases previously force-froze
    # DEF_RETREAT_10 regardless of MOVEMENT_RULES rules. This was a belt-and-suspenders
    # workaround that broke retreat behaviour — defenders stood still during PTB
    # instead of retreating to 10m. Now MOVEMENT_RULES is the sole freeze authority
    # and DEF_RETREAT_10 is in allowed_to_move for all phases except open-play
    # sub-phases where it should be frozen (none currently).
    # The P7 retreat target code in OPE phase handlers already sets targets each frame;
    # with frozen=False those targets now produce actual movement.
    #

    # ═══════════════════════════════════════════════════════════════════
    # MOVEMENT-AUTHORITY / FREEZE INVARIANT — "NO THIRD STATE" (2026-08-31)
    # ═══════════════════════════════════════════════════════════════════
    # Enforce: any player whose movement authority is suppressed MUST be
    # frozen.  The exempt unfreezes above (registered TES tacklers, BC, DH,
    # dynamic fullback, marker-unlock) grant authority; any player STILL
    # UNFROZEN after them but WITHOUT authority is a "third state" — force
    # it frozen so the invariant holds by construction, regardless of what
    # any other subsystem wrote to ``frozen``.  This is the actual
    # enforcement; the runtime audit (enforce_movement_authority_invariant)
    # surfaces any future re-wiring that breaks it.
    for _p in _all_players(world):
        if has_movement_authority(world, phase_str, _p):
            continue
        if not getattr(_p, "frozen", False):
            _p.frozen = True
            if getattr(_p, "_mmdl_moved", False):
                _p._mmdl_moved = False

    # ── FROZEN-VELOCITY GUARANTEE (2026-09-07) ──
    # The sole freeze authority must be fully authoritative: any player that
    # ends this call frozen may carry NO velocity, regardless of which
    # subsystem wrote vx/vy mid-frame.  Frame-order gap: the tackler-gate
    # release (update_tackle_state → _release_defender) fires AFTER the
    # freeze-compliance pass in OpenPlayEngine.update(), then the slot-
    # invariant repair re-runs run_defensive_pipeline and integrates the
    # freshly-released DEF_LINE_READY at line speed (vx ≈ -5.2).  The FINAL
    # apply_phase_invariants call re-froze the body but never zeroed the
    # velocity (it relied on Player.update(), which is not called after),
    # so audit_movement saw a frozen body moving → the teleport.  Zeroing
    # here guarantees a frozen body can never carry velocity into the audit
    # or the next frame.
    for _fp in _all_players(world):
        if getattr(_fp, "frozen", False):
            _fp.vx = 0.0
            _fp.vy = 0.0


# ═══════════════════════════════════════════════════════════════════════
# AUDIT (AFTER phase handler) — log violations, don't crash
# ═══════════════════════════════════════════════════════════════════════

def audit_movement(world, phase_str: str,
                   tes_attackers: set = None,
                   tes_defenders: set = None) -> list[str]:
    """Check movement compliance AFTER the phase handler runs.

    Returns a list of violation strings (empty if clean).
    Log-only — does not raise exceptions.
    """
    rules = MOVEMENT_RULES.get(phase_str)
    if rules is None:
        return []

    must_frozen: set[Role] = rules["must_be_frozen"]
    allowed: set[Role] = rules["allowed_to_move"]

    # TES TACKLER EXEMPTION (2026-08-29): registered tacklers are allowed to
    # move during the TES engagement phases even when their role tag is not
    # TACKLER_1/2/3.  Their movement authority is retained by the tackler
    # registry, so the audit must not flag them as MUST_BE_FROZEN_*.
    _tes_tackler_ids = set()
    if phase_str in (
        "TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE",
    ):
        _tes_tackler_ids = _registered_tes_tackler_ids(world)

    violations: list[str] = []
    for p in _all_players(world):
        # FULLBACK EXEMPTION (2026-08-13): the dynamic fullback is an
        # independent backfield anchor, NOT a line-role actor.  It is
        # intentionally unfrozen through RELEASE/PTB/POST_RUCK and moves
        # under its own geometry writer, so phase freeze contracts
        # (DEF_LINE_READY must_be_frozen in PTB phases) do not apply.
        if getattr(p, "fullback_role_active", False):
            continue
        if getattr(p, "player_id", None) in _tes_tackler_ids:
            continue  # registered tackler — movement authority retained
        role = get_role(p)
        vx = getattr(p, "vx", 0.0)
        vy = getattr(p, "vy", 0.0)

        # TES DEPTH-LOCK (2026-09-06): a depth-locked body is authorised to
        # move in Y (lateral/lane); only X (depth) velocity is forbidden.
        if getattr(p, "depth_locked", False):
            if abs(vx) > 1e-3:
                violations.append(
                    f"DEPTH_LOCK_MOVING id={p.player_id} "
                    f"role={role.name} vx={vx:.3f} phase={phase_str}"
                )
            continue

        # Must-be-frozen but has non-zero velocity
        if role in must_frozen and (abs(vx) > 1e-3 or abs(vy) > 1e-3):
            violations.append(
                f"MUST_BE_FROZEN_MOVING id={p.player_id} "
                f"role={role.name} vx={vx:.3f} vy={vy:.3f} phase={phase_str}"
            )

        # Not in allowed_to_move but has non-zero velocity
        if role not in allowed and (abs(vx) > 1e-3 or abs(vy) > 1e-3):
            violations.append(
                f"ILLEGAL_MOVE id={p.player_id} "
                f"role={role.name} vx={vx:.3f} vy={vy:.3f} phase={phase_str}"
            )

        # Must-be-frozen but frozen=False
        if role in must_frozen and not getattr(p, "frozen", False):
            violations.append(
                f"MUST_BE_FROZEN_NOT_FROZEN id={p.player_id} "
                f"role={role.name} frozen=False phase={phase_str}"
            )

    return violations


# ═══════════════════════════════════════════════════════════════════════
# FROZEN-VELOCITY PIN (2026-09-07) — belt-and-suspenders pre-audit zeroing
# ═══════════════════════════════════════════════════════════════════════
# apply_phase_invariants already zeroes vx/vy for every player that ends the
# call frozen (the FROZEN-VELOCITY GUARANTEE).  In OpenPlayEngine.update()
# that guarantee runs via the FINAL FREEZE AUTHORITY RUN, but that run is
# GATED to non-OPEN_PLAY phases.  In OPEN_PLAY a body can be frozen (e.g. the
# POST-PTB marker-lock window) and receive velocity after the early
# freeze-compliance loop, so it would still carry motion into audit_movement.
# This helper is the explicit second zeroing, called immediately before the
# audit: a frozen body can never carry velocity into the audit or the next
# frame, regardless of which writer set it mid-frame.  It is idempotent with
# the apply_phase_invariants zeroing (a frozen body is zeroed in both places).
def zero_frozen_velocity(world) -> None:
    """Zero ``vx``/``vy`` on every player that is frozen.

    Belt-and-suspenders guard run immediately before ``audit_movement`` so a
    frozen body can never carry velocity into the audit or into the next
    frame, even when the final ``apply_phase_invariants`` freezer authority
    run is gated away (OPEN_PLAY).  Frozen bodies are not integrated by
    ``Player.update()``, so zeroing here cannot double-integrate anything; it
    simply guarantees a frozen body is velocity-pinned by construction.
    """
    for _p in _all_players(world):
        if getattr(_p, "frozen", False):
            _p.vx = 0.0
            _p.vy = 0.0


# ═══════════════════════════════════════════════════════════════════════
# MOVEMENT-AUTHORITY INVARIANT ENFORCEMENT (2026-08-31)
# ═══════════════════════════════════════════════════════════════════════

def enforce_movement_authority_invariant(world, phase_str: str,
                                         raise_on_violation: bool = False) -> list[str]:
    """Enforce the movement-authority ↔ frozen invariant ("no third state").

    For every defending-team player, asserts:
        has_movement_authority(...) OR frozen   (i.e. NOT suppressed-and-unfrozen)

    Equivalently: any defender whose movement authority is suppressed MUST be
    frozen; any defender who is unfrozen MUST have movement authority.

    The dynamic fullback is exempt (it owns its own backfield anchor writer).

    Returns a list of violation strings (empty = clean).  When
    *raise_on_violation* is True and violations exist, the first violation is
    raised as an ``AssertionError`` so a regression is hard-failed instead of
    silently logged.

    This is the runtime complement to the consolidation pass inside
    ``apply_phase_invariants`` (which makes the invariant hold by
    construction).  It surfaces any subsystem that unfreezes a defender AFTER
    the sole freeze authority ran, so a future re-wiring cannot silently
    reintroduce the "suppressed but unfrozen" third state.
    """
    violations: list[str] = []
    for d in (getattr(world, "defending_team", []) or []):
        if getattr(d, "fullback_role_active", False):
            continue  # fullback is exemption — own backfield anchor writer
        if has_movement_authority(world, phase_str, d):
            continue
        if not getattr(d, "frozen", False):
            violations.append(
                f"MOVEMENT_AUTHORITY_INVARIANT id={getattr(d, 'player_id', '?')} "
                f"role={getattr(getattr(d, 'defender_role', None), 'name', None)} "
                f"phase={phase_str} frozen=False but authority suppressed"
            )
    if raise_on_violation and violations:
        raise AssertionError(
            "MOVEMENT_AUTHORITY_INVARIANT VIOLATION:\n  "
            + "\n  ".join(violations)
        )
    return violations


# ═══════════════════════════════════════════════════════════════════════
# BC BACKWARD INVARIANT (enforce in RELEASE)
# ═══════════════════════════════════════════════════════════════════════

def enforce_bc_backward_invariant(bc: Player, world) -> None:
    """Clamp BC velocity so it never moves forward (toward the defensive line).

    ``attacking_direction`` is -1 (left, toward own try line) or +1 (right).
    BC must satisfy: dot(v, attacking_direction_vector) <= 0
    i.e. bc.vx * attacking_direction <= 0.

    When violated, vx is zeroed so BC cannot gain ground toward the defence.
    Lateral movement (vy) is untouched.
    """
    ad = getattr(world, "attacking_direction", -1)
    if bc.vx * ad > 0:
        bc.vx = 0.0


# ═══════════════════════════════════════════════════════════════════════
# MOVEMENT REACTIVATION — force thawed players back into the pipeline
# ═══════════════════════════════════════════════════════════════════════

def reactivate_movement_after_thaw(players, dt: float) -> None:
    """Force just-thawed players back into the movement pipeline.

    Called only on OPEN_PLAY entry frames where the defence/attack
    pipelines already ran under the OLD phase and therefore did not
    integrate movement for players who were frozen the whole frame
    before (POST_RUCK_OPEN_PLAY -> OPEN_PLAY, BROKEN_TACKLE -> OPEN_PLAY).

    Those players have vx=vy=0 (left over from the freeze-compliance
    loop which zeroes every frozen player) and now hold a fresh target,
    but nothing integrated them THIS frame — so they would sit at vx=0
    for 1-2 frames (PTB delay, DH jitter, defensive-line jitter after
    the first tackle, BC step-forward lag, defender retreat lag).

    Rules:
      - Only unfrozen players are touched (frozen = still pinned).
      - Only players with vx==vy==0 AND a target away from their current
        position are integrated — exactly the "stuck" set.  Players
        already integrated this frame (BC / support runners, moved by
        _update_attack) have non-zero vx and are skipped, so they are
        never double-integrated.
      - PTB-era arrival_mode / reached_target leakage is cleared so the
        arrival predictor (snap-to-target at 0.1 m, ignores move_speed,
        zeroes vx inside stop radius) can never bleed into OPEN_PLAY.
      - p.update(dt) applies every cap internally (reaction governor,
        post-catch clamp, defensive ceiling, facing), so no writer can
        escape the limits.
    """
    for p in players:
        if getattr(p, "frozen", False):
            continue

        # Kill PTB-era arrival-mode leakage (set True by the PTB role
        # pipeline, never reset anywhere else).
        if getattr(p, "arrival_mode", False) or getattr(p, "reached_target", False):
            p.arrival_mode = False
            p.reached_target = False

        # TASK III (2026-08-14): a defender the MMDL already integrated
        # this frame (structured OPEN_PLAY step 10b sets _mmdl_moved AFTER
        # apply_movement_state() moved it) must NEVER be re-integrated
        # here.  _mmdl_moved is the contract "position already integrated
        # this frame; skip Player.update"; re-running Player.update would
        # double-integrate velocity (~2x speed) and re-stabilise the
        # target — the "stale target / double-write" frame-order mech.
        #
        # TASK VI (2026-08-15): the flag is only a valid same-frame guard
        # when the player actually MOVED.  During PTB/POST_RUCK the role-
        # movement writer stamps _mmdl_moved on markers (MARKER_A/B are in
        # allowed_to_move, so the freeze-authority clear in
        # apply_phase_invariants never fires for them).  A STALE
        # _mmdl_moved then survives to the OPEN_PLAY entry frame; the
        # player is unfrozen with vx=vy=0 and a live target — by
        # definition NOT integrated this frame (the verify_movement_
        # reactivation seed 3/7/10 fail: pid 212 state=CHASE
        # _mmdl_moved=True v=0 target=anchor dx=8.0).  Only skip when the
        # player has non-zero velocity, i.e. the MMDL genuinely moved it;
        # a v==0+_mmdl_moved player falls through to the stuck predicate
        # below and is re-integrated.
        if getattr(p, "_mmdl_moved", False) and (
                getattr(p, "vx", 0.0) != 0.0 or getattr(p, "vy", 0.0) != 0.0):
            continue

        # TASK III (2026-08-14): a committed PRIMARY/SECONDARY tackler is
        # owned by the tackler-commit writer and integrated by OPE's
        # _update_defence loop.  Re-integration here would shove it off
        # its collision target the same frame the pre-tackle trigger runs.
        if getattr(p, "_tackle_commit_active", False):
            continue

        # Stuck = zero velocity while a live target awaits movement.
        if (getattr(p, "vx", 0.0) == 0.0 and getattr(p, "vy", 0.0) == 0.0
                and (abs(p.target_x - p.x) > 0.01
                     or abs(p.target_y - p.y) > 0.01)):
            p.update(dt)


# ═══════════════════════════════════════════════════════════════════════
# STEP-3: log_phase_freeze — dump who is frozen per phase every N frames
# ═══════════════════════════════════════════════════════════════════════

def log_phase_freeze(world, frame: int, phase: str, interval: int = 30) -> None:
    """Log a compact freeze-state dump every `interval` frames.

    Output format:
        [FREEZE_AUDIT] frame=123 phase=RELEASE
          Frozen: [id=5 role=DEF_LINE_READY, ...]
          Moving: [id=3 role=ATT_CARRIER, ...]
          Stuck (vx=0 while target!=x): [id=7 role=DEF_RETREAT_10, ...]

    Designed to be parsed by the log-hunter script (Step 6).
    """
    if frame % interval != 0:
        return

    from world.rules.movement_contracts import MOVEMENT_RULES, Role, get_role
    rules = MOVEMENT_RULES.get(phase)
    if rules is None:
        return

    frozen_players = []
    moving_players = []
    stuck_players = []

    players = []
    if hasattr(world, 'defending_team'):
        players.extend(world.defending_team)
    if hasattr(world, 'attacking_team'):
        players.extend(world.attacking_team)
    bc = getattr(world, 'ball_carrier', None)
    if bc is not None and bc not in players:
        players.append(bc)

    for p in players:
        role = get_role(p)
        role_name = role.name if hasattr(role, 'name') else str(role)
        pid = getattr(p, 'player_id', id(p))
        entry = f"id={pid} role={role_name}"

        if p.frozen:
            frozen_players.append(entry)
        else:
            moving_players.append(entry)
            vx = getattr(p, 'vx', 0.0)
            tx = getattr(p, 'target_x', getattr(p, 'x', 0.0))
            px = getattr(p, 'x', 0.0)
            if abs(vx) < 0.001 and abs(tx - px) > 0.05:
                stuck_players.append(entry)

    print(f"[FREEZE_AUDIT] frame={frame} phase={phase}")
    if frozen_players:
        print(f"  Frozen: [{', '.join(frozen_players)}]")
    if moving_players:
        print(f"  Moving: [{', '.join(moving_players)}]")
    if stuck_players:
        print(f"  Stuck (vx=0 while target!=x): [{', '.join(stuck_players)}]")
