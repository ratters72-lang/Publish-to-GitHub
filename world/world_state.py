"""
Top-level world state for the rugby league simulation.
This file unifies:
- GameWorldState (global phase machine — DEPRECATED, use GamePhase)
- GameSubState (debug/logging sub-states)
- WorldState (full world snapshot)
"""

from dataclasses import dataclass, field
from enum import Enum

from .ruck_state import _RuckStateBackwardsCompat as _RuckStateCompat

# ═══════════════════════════════════════════════════════════════════════
# LEGACY TACKLE-AUDIT DIAGNOSTICS (Block 1 cleanup)
# ═══════════════════════════════════════════════════════════════════════
# The old tackle/PTB/marker/ruck prints were built for legacy geometry and
# produced misleading readings for the new TACKLE SDEA.  Disabled by default.
# Flip to True ONLY for isolated diagnosis.
DEBUG_WORLD_TRACE: bool = False

# ═══════════════════════════════════════════════════════════════
# UNIFIED DEFENCE TARGET — single universal targeting function
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# SINGLE SETTER FOR BC OWNERSHIP
# ═══════════════════════════════════════════════════════════════


def set_ball_carrier(world, player):
    """Set BC ownership — the ONLY way to establish ball_carrier.

    This is the single source of truth for establishing BC ownership.
    It ensures `ball_owner` stays in sync with `ball_carrier` and
    synchronises the ball's true position to the carrier.

    All legacy code that directly writes ``world.ball_carrier = player``
    should be migrated to ``set_ball_carrier(world, player)`` over time.
    """
    world.ball_owner = "BC"

    # Only apply BC speed reduction when BC is changing
    if world.ball_carrier is not player:
        # Restore the outgoing carrier's full speed so the 0.95 cut never
        # ratchets a player down across multiple possessions (carry -> pass
        # away -> receive back -> carry again).  This keeps the reduction
        # strictly once-per-possession with no permanent degradation.
        prev = world.ball_carrier
        if prev is not None:
            base = getattr(prev, "_bc_base_max_speed_mps", None)
            if base is not None:
                prev.max_speed_mps = base

        world.ball_carrier = player

        # BC speed reduction (non-compounding): always derived from the
        # player's own base speed, never from an already-reduced value, so
        # repeated possessions can never stack the 0.95 multiplier.
        if hasattr(player, "max_speed_mps"):
            base = getattr(player, "_bc_base_max_speed_mps", None)
            if base is None:
                base = player.max_speed_mps
                player._bc_base_max_speed_mps = base
            player.max_speed_mps = base * 0.95
    else:
        # Already BC — do not reapply reduction
        world.ball_carrier = player

    if world.ball is not None:
        world.ball.true_x_m = player.x
        world.ball.true_y_m = player.y
        world.ball.possessed_by = player

    # ── RUCK-ORIGIN SYNC (2026-08-29) ──
    # Establish the ruck origin from the NEW BC immediately on possession
    # establishment (catch / DH pickup), so retreat and kickoff-reception
    # geometry never read a stale or None world.ruck_x_m/ruck_y_m on the
    # same frame they first consume it.  WORLD.RUCK_X_M IS LEFT None AFTER
    # kickoff reset; without this sync, the first OPEN_PLAY frame's retreat
    # and kickoff-reception readers fall back to a stale/default ruck and
    # mid-line actors converge to outdated positions before correcting.
    #
    # GUARD: only fires while NO ruck is established (post-kickoff).  After
    # the first tackle world.ruck_x_m is set and stable, so a later pass
    # reception to a new BC never relocates the ruck — the ruck origin only
    # moves on genuine possession-establishment with no prior ruck.
    #
    # POST-CATCH RUCK-PIVOT ARM (2026-08-30): the kickoff reception is the
    # first legitimate ruck pivot of the set — the caught BC IS the anchor.
    # Arming ``ruck_established`` here (instead of leaving it False until
    # _start_tackle) lets assign_depth_roles bootstrap the full
    # A/B/C/D/CENTRE/WINGER ladder anchored to the caught BC's position on
    # the first OPEN_PLAY frame, so the post-catch line forms a real depth
    # ladder instead of degenerating to a flat "D"-cluster (the "defenders
    # break into 3 clusters / don't converge / don't go into role depth /
    # don't tackle" bug).  It only fires when ruck_x_m was None (fresh
    # post-kickoff possession), so a later pass reception with an existing
    # ruck is never affected.
    if getattr(world, "ruck_x_m", None) is None:
        world.ruck_x_m = float(player.x)
        world.ruck_y_m = float(player.y)
        if hasattr(world, "ruck_established"):
            world.ruck_established = True


def get_defence_target(world, defender_index: int = 0) -> tuple[float, float]:
    """
    Returns (target_x, target_y) for a defender given their index in the
    defensive line ordering.

    AI_ENGINE_FIX: This is the SINGLE source of truth for ALL defensive
    positioning.  Every module MUST call this function — no module may
    compute its own targets from bc.x, bc.y, fr.x, fr.y, or ball_x.

    Index mapping:
        0 — marker 1 (tight to ruck, behind Y)
        1 — marker 2 (tight to ruck, further behind Y)
        2 — dummy half (at the ruck)
        3+ — defensive line (lane-based Y, anchored at defensive_line_anchor)

    Defensive targets NEVER use ball_carrier.x, ball_carrier.y, or
    first_receiver.x/y for positioning.  Markers and DH use ruck_x_m/ruck_y_m.
    The defensive line uses defensive_line_anchor for X and lane-based Y.

    Returns (60.0, 35.0) as safe fallback if world state is unavailable.
    """
    anchor_x = getattr(world, "defensive_line_anchor", None)
    if anchor_x is None:
        anchor_x = 60.0

    ruck_x = getattr(world, "ruck_x_m", None)
    if ruck_x is None:
        ruck_x = 60.0

    ruck_y = getattr(world, "ruck_y_m", None)
    if ruck_y is None:
        ruck_y = 35.0

    # ═══════════════════════════════════════════════════════════════════
    # DEFENSIVE LINE WIDTH — AUTHORITATIVE SPACING
    #
    # All line defenders (indices 3+) have target_y distributed across
    # the full def_line_width_m span, centred on ruck_y_m.
    #
    # For N line defenders, the Y spread is:
    #     target_y = ruck_y + t * half_width
    # where t is in [-1, 1] evenly over the count.
    #
    # This replaces the old hardcoded lane_width=10.0 / base_y=5.0.
    # ═══════════════════════════════════════════════════════════════════
    def_line_width = getattr(world, "def_line_width_m", 40.0)

    # ── Markers (indices 0 and 1) ──
    if defender_index == 0:
        return (ruck_x, ruck_y - 0.5)
    if defender_index == 1:
        return (ruck_x, ruck_y - 1.0)

    # ── Dummy Half (index 2) ──
    if defender_index == 2:
        return (ruck_x, ruck_y)

    # ── All other defenders = defensive line (indices 3+) ──
    target_x = anchor_x

    # AI_ENGINE_FIX: Distribute across def_line_width_m using a fixed
    # assumed number of line defender slots (7 = indices 3-9).  Each
    # call with a single index cannot know the actual total count, so
    # we use a default slot count and spread evenly from -half_width
    # to +half_width around ruck_y_m.
    #
    # lane_offset = 0 → slot index 0   → ruck_y - half_width
    # lane_offset = 6 → slot index 6   → ruck_y + half_width
    # Intermediate slots are linearly interpolated.
    lane_offset = defender_index - 3  # 0, 1, 2, 3, ...
    DEFAULT_NUM_LANES = 7  # standard 7-line-defender formation
    slot_count = max(1, DEFAULT_NUM_LANES - 1)  # 6 intervals between 7 slots
    half_width = def_line_width * 0.5
    step = def_line_width / slot_count
    target_y = ruck_y - half_width + min(lane_offset, slot_count) * step

    return (target_x, target_y)


# ═══════════════════════════════════════════════════════════════
# TRACE HELPER — KICKOFF FLAG OVERWRITE DIAGNOSTIC
# ═══════════════════════════════════════════════════════════════
# Called at critical transition points during kickoff → open_play
# to identify exactly where in_pursuit_mode and first_tackle_not_made
# are being overwritten.

def trace_kickoff_flags(world, label):
    print(
        f"[TRACE {label}] "
        f"pursuit={world.in_pursuit_mode} "
        f"first_not_made={world.first_tackle_not_made} "
        f"phase={world.phase} sub_state={world.sub_state}"
    )


# ---------------------------------------------------------
# GLOBAL PHASE MACHINE
# ---------------------------------------------------------

class GamePhase(Enum):
    """Top-level game phase — single source of truth for what's happening.
    
    KICKOFF: ball is being kicked or in flight after kickoff. Only kickoff
             engine, ball physics, and catch system run.
    CATCH:   ball is being caught (1-2 frame transition). Only catch system
             runs; OPE, defence, attack, TES are inactive.
    OPEN_PLAY: normal play — OPE runs, including all sub-states (tackle,
               PTB, reset). Defence, attack, TES, DH all run.
    """
    KICKOFF = "KICKOFF"
    CATCH = "CATCH"
    OPEN_PLAY = "OPEN_PLAY"


class GameWorldState(Enum):
    """[DEPRECATED] Use GamePhase instead.
    
    Kept for backwards compatibility.  Was: KICKOFF, OPEN_PLAY, RESTART.
    This enum was always set to OPEN_PLAY from frame 0 and never changed,
    making it effectively dead code.
    
    New code should use world.phase (GamePhase enum) which accurately
    tracks the real game state: KICKOFF → CATCH → OPEN_PLAY.
    """
    KICKOFF = "KICKOFF"
    OPEN_PLAY = "OPEN_PLAY"
    RESTART = "RESTART"


class GameSubState(Enum):
    """High-level game sub-state during open play — drives defence movement policy.
    
    KICKOFF_START: kickoff reset applied, waiting for kick. anchor/ruck_x are None.
    KICKOFF_IN_AIR: ball is in the air after a kickoff, not yet catchable.
    OPEN_PLAY: normal play, defence can move, ruck follows BC.
    OPEN_PLAY_STARTUP: brief settling period after kickoff catch (0.5s).
                       No TES, spacing, DH, PTB, or tackle checks run.
    TACKLE_INIT: one-frame state when a tackle is initiated (freeze BC, snap defender, start TES).
    TACKLE_CONTACT: TES running (contact phase). Defence briefly blocked.
    TACKLE_DRIVE: TES drive phase.
    TACKLE_DOMINANCE: TES dominance phase — outcome computation, markers assigned.
    TACKLE_RELEASE: TES completed — BC unfrozen, tacklers clearing, DH assigned.
                    TES shuts down. OPE takes full control. Ruck bubble still active.
    PTB_READY: DH is next ball owner, moving into PTB position. No ball possession yet.
               Markers remain frozen at ruck. Ruck bubble still active.
    PTB: play-the-ball, DH pickup, markers hold.
    POST_PTB_RESET: ball out, defence retreats + attack line rebuilds + ruck bubble dissolves.
    RESET_LINE: explicit, slower rebuild (scrum, penalty, kickoff, 7-tackle set).
    """
    KICKOFF_START = "kickoff_start"
    KICKOFF_IN_AIR = "kickoff_in_air"
    OPEN_PLAY = "open_play"
    TACKLE_INIT = "tackle_init"
    TACKLE_CONTACT = "tackle_contact"
    TACKLE_DRIVE = "tackle_drive"
    TACKLE_DOMINANCE = "tackle_dominance"
    TACKLE_RELEASE = "tackle_release"
    PTB_READY = "ptb_ready"
    PTB = "ptb"
    POST_RUCK_OPEN_PLAY = "POST_RUCK_OPEN_PLAY"
    POST_PTB_RESET = "post_ptb_reset"
    RESET_LINE = "reset_line"


# ══════════════════════════════════════════════════════════════
# STEP‑03A‑06C — TES tackle-phase predicates
# ══════════════════════════════════════════════════════════════

TES_TACKLE_PHASES = frozenset({
    GameSubState.TACKLE_INIT,
    GameSubState.TACKLE_CONTACT,
    GameSubState.TACKLE_DRIVE,
    GameSubState.TACKLE_DOMINANCE,
    # NOTE: TACKLE_RELEASE is deliberately excluded.
    # TES hands control back to OPE during RELEASE — the OPE's
    # _update_release() handler manages BC unlock, tackler peeling,
    # and DH assignment.  Including RELEASE in TES_TACKLE_PHASES
    # would set tes_locked=True, which causes OPE's tes_locked
    # early-return path to skip _update_release() entirely because
    # the tes_handlers dict (which only has INIT/CONTACT/DRIVE/DOMINANCE)
    # has no RELEASE handler.  The RELEASE -> PTB_READY transition
    # would never fire, and the game would hang.
})


def is_tackle_phase(world) -> bool:
    """Return True if TES tackle engagement is active.

    During these sub-states, only TES should move players inside the
    ruck bubble.  Other systems must freeze or avoid players within
    ~3.5m of the ball carrier.

    NOTE: TACKLE_RELEASE is NOT a tackle phase — TES has disengaged.
    The ruck bubble still exists but is managed by defensive behaviour,
    not by TES ownership.
    """
    return world.sub_state in (
        GameSubState.TACKLE_INIT,
        GameSubState.TACKLE_CONTACT,
        GameSubState.TACKLE_DRIVE,
        GameSubState.TACKLE_DOMINANCE,
    )


def is_tes_locked_substate(world) -> bool:
    """Return True if this sub-state requires tes_locked = True.

    TES owns the ruck throughout:
        TACKLE_INIT → TACKLE_CONTACT → TACKLE_DRIVE → TACKLE_DOMINANCE
    During these phases ALL non‑TES subsystems are frozen.

    TACKLE_RELEASE is NOT included — it would deadlock the OPE's
    phase dispatch.  During RELEASE, tes_locked=False, so OPE's
    normal phase_map runs _update_release(), which manages the
    tackle->PTB handover.  See TES_TACKLE_PHASES comment above.
    """
    return world.sub_state in TES_TACKLE_PHASES


def is_in_ruck_bubble(player, bc, radius: float = 3.5) -> bool:
    """Return True if *player* is inside the ruck bubble around *bc*.

    Uses squared-distance comparison to avoid sqrt.  The default
    radius of 3.5 metres defines the ruck zone where players are
    close enough to interfere with the tackle engagement.
    """
    if player is None or bc is None:
        return False
    dx = player.x - bc.x
    dy = player.y - bc.y
    return (dx * dx + dy * dy) < (radius * radius)


# ---------------------------------------------------------
# FULL WORLD SNAPSHOT
# ---------------------------------------------------------

@dataclass
class WorldState:
    """
    Holds the complete state of the simulated world each frame.
    This is the authoritative world snapshot used by:
    - kickoff controller
    - catch system
    - open play engine
    - rendering
    """

    # All players (single source of truth for update loops)
    players: list = field(default_factory=list)

    # Ball state
    ball_x_m: float = 60.0
    ball_y_m: float = 35.0
    ball_z_m: float = 0.0
    ball_vx: float = 0.0
    ball_vy: float = 0.0
    ball_vz: float = 0.0
    ball_live: bool = False          # ball is catchable / playable
    ball_in_play: bool = False       # ball is legally in play (not dead)

    # Ruck / tackle state
    ruck_y_m: float = 35.0
    tackle_count: int = 0

    # ═══════════════════════════════════════════════════════════════
    # DEFENSIVE LINE WIDTH — SINGLE SOURCE OF TRUTH (GOVERNOR-OWNED)
    #
    # def_line_width_m defines the total span of the defensive line
    # (in metres) centred on ruck_y_m.  DefensiveLineGovernor is the
    # SOLE writer of this field during OPEN_PLAY: each frame it
    # publishes the compression-ramp width (base → min) clamped by the
    # per-frame delta limit.  Every downstream width consumer
    # (geometry, spacing, structured close, pursuit, catch, scramble)
    # reads it through defensive_width.governed_width_m(), which
    # overrides it with the governor's live value plus the
    # [min_def_width_m, max_def_width_m] and sideline-margin clamps.
    # No other module may write this field.
    # ═══════════════════════════════════════════════════════════════
    def_line_width_m: float = 40.0

    # ── Optional short-side / open-side width overrides ──
    # When set (non-None), these override def_line_width_m on the
    # respective side of the ruck, allowing asymmetrical alignment.
    short_side_width_m: float | None = None
    open_side_width_m: float | None = None

    # ── Fullback default anchor (Task E, spec step 4) ──
    # When FULLBACK_EXCLUDED is active, the split-spacing module pins
    # the fullback actor to this anchor instead of giving it a chain
    # slot.  The dynamic fullback role (fullback_role.py) normally owns
    # this actor's real target; these defaults cover the exclusion
    # contract so spacing_targets fully specifies the backfield actor.
    fullback_default_x: float = 60.0
    fullback_default_y: float = 35.0

    # ── Fullback audit publish (Task J, 2026-08-12) ──
    # Per-frame absorption/arrival state published by OpenPlayEngine and
    # KickoffEngine for debug overlays + kickoff audit logs:
    #   fullback_role_active            — the actor is the independent FB
    #   fullback_absorb_allowed         — both X/Y counters >= ABSORB_FRAMES_MIN
    #   fullback_frames_within_absorb_x — consecutive frames within X proximity
    #   fullback_frames_within_absorb_y — consecutive frames within Y proximity
    #   fullback_arrived                — actor reached its backfield slot
    fullback_role_active: bool = False
    fullback_absorb_allowed: bool = False
    fullback_frames_within_absorb_x: int = 0
    fullback_frames_within_absorb_y: int = 0
    fullback_arrived: bool = False

    # ── POST-CATCH FULLBACK ABSORPTION GRACE WINDOW (2026-08-28) ──
    # The kickoff fullback is hard-placed ON the flat formation line at
# reception (x = formation_line_x ≈ 62).  After the catch the post-
    # catch defensive line anchor is locked to *bc.x + 10*; when the BC is
    # caught near midfield the line anchor lands within ABSORB_X_THRESHOLD_M
    # of the FB's live position, so update_fullback_absorb_state() sees the
    # actor "collapsed into the line" and dissolves fullback_role_active —
    # the live-kickoff tag loss.  This frame-count grace window is armed at
    # reception and decremented each OPEN_PLAY FB frame; while > 0 the
    # absorption dissolve is suppressed, giving the FB time to glide to the
    # ball-derived deep anchor (bc.x + 10 + depth) and clear the threshold.
    # Cleared (0) by kickoff_engine.reset_for_kickoff() so each kickoff
    # re-arms fresh.
    fb_post_catch_absorb_grace_frames: int = 0

    # ═══════════════════════════════════════════════════════════════════
    # GOVERNED WIDTH ACTIVATION (Task D, 2026-08-09)
    #
    # DefensiveLineGovernor is the SOLE writer of def_line_width_m each
    # OPEN_PLAY frame (compression ramp + per-frame delta clamp).  While
    # it publishes, governor_width_active is True and
    # governor_line_width_m holds the raw governed width before the
    # defensive_width.governed_width_m() clamp.  The governor clears
    # both flags on disabled / pursuit / no-BC zero-state paths so no
    # reader ever sees a stale activation.
    # ═══════════════════════════════════════════════════════════════
    governor_width_active: bool = False
    governor_line_width_m: float | None = None

    # ── Legacy ruck_state field (kept for backwards compat) ──
    ruck_state: _RuckStateCompat = _RuckStateCompat.NONE

    # Old ruck_timer (kept for backwards compat)
    ruck_timer: float = 0.0

    # Team orientation
    defending_left: bool = True
    attacking_direction: int = 1     # +1 = attacking right, -1 = attacking left

    # Global phase — SINGLE SOURCE OF TRUTH
    # Set to KICKOFF at world creation; transitions:
    #   kickoff → check_for_catch() → CATCH (1-2 frames) → open_play.start() → OPEN_PLAY
    # Use GamePhase enum for reading, GameWorldState kept for backwards compat.
    phase: GamePhase = GamePhase.KICKOFF

    # [DEPRECATED] Legacy phase — always OPEN_PLAY from frame 0, never changed.
    # New code should read/write world.phase instead.
    game_state: GameWorldState = GameWorldState.OPEN_PLAY

    # Open-play sub-state (drives defence movement policy)
    sub_state: GameSubState = GameSubState.KICKOFF_IN_AIR

    # Optional: store references to ball + players
    ball: object = None

    # Kickoff-phase teams (named by role — zero ambiguity)
    kicking_team: list = field(default_factory=list)
    receiving_team: list = field(default_factory=list)

    # ═══════════════════════════════════════════════════════════════
    # KICKOFF FORMATION / FULLBACK PLACEMENT (2026-08-11)
    #
    # Sole writers are apply_kicking_team_formation() (kicker/side_sign)
    # and the kickoff engine's FB selection + placement writer.  These
    # fields let the FB assignment, the placement writer, the audit, and
    # the verifiers agree on one deterministic geometry per sequence.
    #   - kickoff_kicker_id: player_id of the designated kicker
    #     (jersey 13 by default).
    #   - kickoff_side_sign: +1 = kick lands toward +Y (bottom sideline),
    #     -1 = toward -Y (top sideline).  Default +1 when the landing
    #     target sits exactly on centre.
    #   - fb_placement_stamp: dict {player_id, x, y} emitted IMMEDIATELY
    #     after FB selection so the placement writer can hard-place the
    #     actor BEFORE any render/movement consumer reads positions.
    #     Cleared by the placement writer once placed.
    #   - fb_placement_applied: True after the placement writer consumed
    #     the stamp for this sequence (one-shot ordering barrier).
    # ═══════════════════════════════════════════════════════════════
    kickoff_kicker_id: int | None = None
    kickoff_side_sign: int = 1
    fb_placement_stamp: dict | None = None
    fb_placement_applied: bool = False

    # The flat kicking-formation line X (halfway + 5.0).  Published by
    # KickoffEngine.reset_for_kickoff() so the audit / tooling / verifiers
    # can read the exact formation anchor without reaching into the engine.
    kickoff_engine_line_x: float | None = None

    # Open-play teams (set from receiving/kicking after a legal kickoff)
    attacking_team: list = field(default_factory=list)
    defending_team: list = field(default_factory=list)

    # ═══════════════════════════════════════════════════════════════
    # BALL OWNERSHIP — SINGLE SOURCE OF TRUTH
    #
    # ball_owner values:
    #   "NONE"  → pre-kickoff, reset, or error state
    #   "BC"    → ball attached to a player (ball_carrier is valid)
    #   "KICK"  → ball in flight from a kick (no BC, kick chase active)
    #   "LOOSE" → ball on ground / contestable (pickups, knock-ons)
    #
    # When ball_owner == "BC":
    #   - ball_carrier MUST be not None
    #   - ball.true_x/y MUST equal ball_carrier.x/y (within epsilon)
    # When ball_owner != "BC":
    #   - ball_carrier MUST be None
    #
    # ball_carrier is kept in sync with ball_owner at all ownership
    # change points (catch, pickup, kick, pass release, knock-on).
    # ═══════════════════════════════════════════════════════════════
    ball_owner: str = "NONE"

    # Ball carrier (the player currently in possession).
    # Derived from ball_owner — when ball_owner == "BC" this is valid,
    # otherwise it is None.
    ball_carrier: object = None

    # Predicted kickoff landing point (set by run.py after ball.kick())
    kickoff_landing_x: float = 0.0
    kickoff_landing_y: float = 0.0

    # Designated primary catcher for this kickoff (set by run.py after ball.kick())
    primary_catcher: object = None

    # Kickoff run-back controller (created on catch, used briefly after kickoff)
    kickoff_runback: object = None

    # Kickoff kick-chase controller (manages kicking-team chase lanes)
    kickoff_kickchase: object = None

    # Reference to the open-play engine (set by run.py)
    open_play: object = None

    # Reference to the open-play engine under the name defensive behaviour expects
    open_play_engine: object = None

    # Reference to the kickoff controller (set by run.py)
    kickoff_controller: object = None

    # One-frame catch immunity flag
    just_caught_this_frame: bool = False

    # ── CATCH X-AUTHORITY LOCK (2026-09-10) ──
    # Sole X-authority at the CATCH (chase-to-catch) transition.  Armed by
    # catch_system._perform_catch() on reception; cleared by OpenPlayEngine
    # once the post-catch pursuit window expires, and by kickoff reset.
    # While True, the catch_x_authority module is the ONLY writer of
    # target_x for genuine line defenders (BC-relative chase X), so no
    # competing non-TES X writer can fight over target_x and freeze the
    # chasers/tacklers.  Y is never touched (spacing authority keeps Y).
    catch_x_authority_lock: bool = False

    # ── POST-CATCH PURSUIT WINDOW (2026-08-16) ──

    # Seconds remaining in which defenders keep chasing the BC immediately
    # after a kickoff catch, instead of falling into MMDL SLIDE/DRIFT (the
    # post-catch stall: engaged defenders stop moving forward on X until
    # bc.x passes anchor_x + 10).  Armed at 3.0s in catch_system.
    # _perform_catch(), decremented every OPEN_PLAY frame by
    # OpenPlayEngine, and read by the MMDL + depth-classification layers.
    post_catch_chase_window_s: float = 0.0
    POST_CATCH_CHASE_WINDOW_S: float = 3.0

    # Post-catch contact immunity counter
    disable_contact_frames: int = 0

    # ═══════════════════════════════════════════════════════════════
    # KICKOFF ISOLATION (Phase 5) — single source of truth
    # ═══════════════════════════════════════════════════════════════
    in_kickoff: bool = False

    # KICKOFF COLLISION GUARD (Rules 1 & 15)
    just_left_kickoff_timer: float = 0.0
    KICKOFF_COLLISION_DELAY_S: float = 0.2

    # Frame counter
    frame_count: int = 0

    # ═══════════════════════════════════════════════════════════════
    # ROLE GOVERNANCE FLAG — True during KICKOFF / KICK_RETURN /
    # BALL_CAUGHT transition.  When True, role spacing becomes the
    # ONLY active defensive system; all timing, hesitation,
    # compression, retreat, and freeze logic is bypassed.
    # Once OPEN_PLAY activates, this returns False and all systems
    # re-enable normally.
    # ═══════════════════════════════════════════════════════════════
    # Set each frame by OpenPlayEngine.update() after tes_lock flags
    # have been computed, before defensive behaviour dispatch.
    role_governs: bool = False

    # ═══════════════════════════════════════════════════════════════
    # SIMULATION TIME — monotonic seconds since epoch
    # ═══════════════════════════════════════════════════════════════
    # Incremented each frame by OpenPlayEngine.update() via dt.
    # Used by TES entry to stamp entry_time.
    sim_time_s: float = 0.0

    # ═══════════════════════════════════════════════════════════════
    # TES ACTIVE FLAG — True while a tackle engagement is in progress
    # ═══════════════════════════════════════════════════════════════
    # Set True by tes_start_tackle() during TES entry.
    # Set False by _prepare_release() when TES shuts down.
    # While True, non‑TES systems (collision, free movement) must
    # avoid BC and the primary tackler.
    tes_active: bool = False

    # ═══════════════════════════════════════════════════════════════
    # TACKLE OWNERSHIP
    # ═══════════════════════════════════════════════════════════════
    primary_tackler_id: int | None = None
    secondary_tacklers: list[int] = field(default_factory=list)

    # ── TES TACKLER REGISTRY (2026-08-03) ──
    # Set of player_ids engaged in the current TES tackle (primary + all
    # joiners).  Populated by tes_start_tackle() and the joiner paths in
    # OpenPlayEngine / DefensiveBehaviourUnified.  Cleared everywhere
    # `tes_active` is set False (release, broken tackle, kickoff reset).
    # Defensive behaviour uses this to distinguish TES tacklers (who keep
    # their TES roles) from non-tacklers (who must NOT run chase/structured-
    # close writers during TES — they retreat, fold, or hold instead).
    tackler_ids: set[int] = field(default_factory=set)

    # First receiver (set during POST_PTB_RESET)
    first_receiver: object = None

    # Attacking line (managed by OpenPlayEngine)
    attacking_line_enabled: bool = False

    # ═══════════════════════════════════════════════════════════════
    # STEP‑03A‑06C — TES LOCK & SUBSYSTEM FLAGS
    # ═══════════════════════════════════════════════════════════════
    # tes_locked is the single source of truth.  All subsystem flags
    # are derived from it via update_tes_lock_flags().  No other
    # code may set these subsystem flags directly.
    tes_locked: bool = False

    # ── Defensive subsystem enable flags ──
    defensive_chase_enabled: bool = True
    defensive_retreat_enabled: bool = True
    defensive_line_update_enabled: bool = True
    marker_logic_enabled: bool = True
    defensive_dh_enabled: bool = True

    # ── Attacking subsystem enable flags ──
    attacking_dh_enabled: bool = True
    attacking_line_enabled_override: bool = True   # Override for world.attacking_line_enabled
    support_runners_enabled: bool = True
    first_receiver_enabled: bool = True
    lane_update_enabled: bool = True
    target_update_enabled: bool = True

    # ═══════════════════════════════════════════════════════════════
    # STEP‑03A‑09B — Defence enable flag (single source of truth)
    # ═══════════════════════════════════════════════════════════════
    # Set True/False by OpenPlayEngine in update() each frame based
    # on substate + tes_locked.  All defensive behaviour methods
    # check this flag and return early if False.
    defence_enabled: bool = True

    # ═══════════════════════════════════════════════════════════════
    # STEP‑03A‑08 — Defensive line anchor (single source of truth)
    # ═══════════════════════════════════════════════════════════════
    # Computed each frame in OPEN_PLAY and POST_PTB_RESET.
    # Used by all defensive behaviour methods instead of hardcoded
    # bc.x + 10.0 / ruck_x + 10.0.
    defensive_line_anchor: float = 0.0

    # ═══════════════════════════════════════════════════════════════
    # GLOBAL DEFENSIVE ENGINE CONTRACT — LAW 1
    #
    # current_target_writer identifies which subsystem wrote defensive
    # targets this frame.  Must be "pursuit" or "structured" by end of
    # frame when defence is enabled.  Reset at start of each frame.
    # ═══════════════════════════════════════════════════════════════
    current_target_writer: str = ""

    # ═══════════════════════════════════════════════════════════════
    # BIG BLOCK DEFENCE — First tackle not yet made flag
    # ═══════════════════════════════════════════════════════════════
    # True from kickoff until the first complete tackle cycle (POST_PTB_RESET).
    # While True, Defence Rules 0, 5, 6, 7 suppress:
    #   - retreat (defenders stay in pursuit lanes)
    #   - compression (no Y-compression toward FR lane)
    #   - step-up (no defensive line step-up)
    #   - line formation (no line anchor-based positioning)
    # Set True at world creation.  Set False on first POST_PTB_RESET entry.
    first_tackle_not_made: bool = True

    # ═══════════════════════════════════════════════════════════════
    # RUCK-PIVOT TIMING FLAG (Stage 3B)
    #
    # Band formation (A/B/C/D/CENTRE/WINGER depth-role seeding) occurs
    # once a ruck pivot is armed.  It is armed at the kickoff reception
    # (set_ball_carrier's ruck-origin sync, 2026-08-30) so the post-catch
    # line bootstraps a full A/B/C/D ladder anchored to the caught BC,
    # and re-armed at TES TACKLE_INIT entry (OpenPlayEngine._start_tackle)
    # for each subsequent tackle.  Reset to False by
    # KickoffEngine.reset_for_kickoff().  Verification stubs that omit
    # the field default to True via getattr so direct verifier calls are
    # unaffected.
    # ═══════════════════════════════════════════════════════════════
    ruck_established: bool = False

    # ═══════════════════════════════════════════════════════════════
    # STEP 3 — PTB TRANSITION ANCHOR (single source of truth)
    # ═══════════════════════════════════════════════════════════════
    # Set by set_tackle_contact_anchor() at DOMINANCE->RELEASE boundary.
    # Used by apply_ptb_transition() to snap bc, dh, and ball to a
    # single physical PTB spot during PTB_PREP -> PTB_EXEC -> PTB.
    # Eliminates the stale-anchor teleport/snap-back bug.
    ptb_anchor_x: float | None = None
    ptb_anchor_y: float | None = None

    # Last known contact position (overwrites stale PRE-TACKLE snapshots).
    # Set at the same time as ptb_anchor.
    last_contact_x: float | None = None
    last_contact_y: float | None = None

    # ═══════════════════════════════════════════════════════════════
    # PURSUIT MODE FLAG — SINGLE BOOLEAN GATE
    # ═══════════════════════════════════════════════════════════════
    # Set True ONLY by chase event methods (on_kickoff, on_downfield_kick,
    # on_bomb, on_grubber, on_intercept, on_loose_ball) in OpenPlayEngine.
    # Set False by on_tackle_complete, on_ball_secured, on_ptb_begin.
    #
    # NO LONGER coupled to first_tackle_not_made — the one-shot flag
    # that tracks "has any tackle completed in this set" still exists
    # for its own purposes but does not drive in_pursuit_mode.
    #
    # When True, ALL defensive geometry, line formation, retreat,
    # compression, anchor, ruck_x, governor, and spacing are hard-disabled.
    # Defenders use pure BC-relative pursuit offsets only.
    #
    # This is the authoritative gate that MUST be checked by every
    # subsystem that could produce a defensive target Y.  If any code
    # reads or writes a Y value that is not BC-relative while this flag
    # is True, that is a bug.
    in_pursuit_mode: bool = False

    # ═══════════════════════════════════════════════════════════════
    # STRUCTURED OPEN PLAY FLAG
    # ═══════════════════════════════════════════════════════════════
    # When True (default), OPEN_PLAY sub-state uses the full structured
    # defensive pipeline (governor, geometry, role spacing, decision layer).
    # When False, OPEN_PLAY uses the legacy scramble-mode behaviour
    # (_apply_open_play_unified) which bypasses geometry and governor.
    #
    # Set once at world creation; intended as a developer toggle for
    # debugging/regression testing.  Does not change during a session.
    use_structured_open_play: bool = True

    # ═══════════════════════════════════════════════════════════════
    # POST-PTB SCOOT SYSTEM — TUNABLE MARKER LOCK + ESCAPE ANGLE
    # ═══════════════════════════════════════════════════════════════
    # marker_lock_duration_s: seconds markers A/B are frozen after PTB.
    #   REDUCED 1.0 -> 0.3 (2026-08-16): the 1s lock made the DH/BC scoot
    #   feel laggy and markers visibly glue to the ruck.  0.3s still keeps
    #   markers from collapsing the ruck on the escape step while letting
    #   the peel begin almost immediately (DH-readiness/peel-timing tests
    #   confirmed safe).
    # escape_angle_deg: minimum lateral escape angle for DH/BC (degrees)
    # escape_window_frames: number of frames the escape window lasts (~0.33s @ 60fps)
    # marker_freeze_timer: accumulated time since PTB completion (capped at lock duration)
    # frames_since_ptb: frame counter since last PTB completed
    # seconds_since_ptb: dt-accumulated seconds since last PTB completed.
    #   STAGE 3F: frame-count gates (frames_since_ptb < 30 / < escape_window_frames)
    #   made the post-ruck quiet window and the BC escape window vary with tick
    #   rate, so spacing/pacing inherited nondeterministic frame drift.  This is
    #   the time-based source of truth; frames_since_ptb is kept as a back-compat
    #   frame counter.  Fallback readers use frames_since_ptb / 60.0 when this
    #   field is absent (legacy test fixtures).
    marker_lock_duration_s: float = 0.3
    escape_angle_deg: float = 30
    escape_window_frames: int = 20
    marker_freeze_timer: float = 0.0
    frames_since_ptb: int = 0
    seconds_since_ptb: float = 999.0

    # ═══════════════════════════════════════════════════════════════
    # DEBUG METHODS
    # ═══════════════════════════════════════════════════════════════

    def set_sub_state(self, new_state, caller):
        """Set sub_state with debug trace."""
        if DEBUG_WORLD_TRACE:
            print(f"[SUBSTATE] {caller} → {new_state}")
        self.sub_state = new_state

    # ═══════════════════════════════════════════════════════════════
    # STEP‑03A‑06C — TES LOCK FLAG UPDATE
    # ═══════════════════════════════════════════════════════════════

    def update_tes_lock_flags(self) -> None:
        """Set ALL subsystem enable flags from tes_locked.

        Called once per frame at the top of OpenPlayEngine.update()
        AFTER tes_locked has been set from GameSubState.

        This is the SOLE place where subsystem flags are set.
        No other code may write directly to these flags.
        """
        if self.tes_locked:
            # ── Defensive subsystems — HARD DISABLE ──
            self.defensive_chase_enabled = False
            self.defensive_retreat_enabled = False
            self.defensive_line_update_enabled = False
            self.marker_logic_enabled = False
            self.defensive_dh_enabled = False

            # ── Attacking subsystems — HARD DISABLE ──
            self.attacking_dh_enabled = False
            self.attacking_line_enabled_override = False
            self.support_runners_enabled = False
            self.first_receiver_enabled = False
            self.lane_update_enabled = False
            self.target_update_enabled = False
        else:
            # ── All subsystems RE-ENABLED ──
            self.defensive_chase_enabled = True
            self.defensive_retreat_enabled = True
            self.defensive_line_update_enabled = True
            self.marker_logic_enabled = True
            self.defensive_dh_enabled = True

            self.attacking_dh_enabled = True
            self.attacking_line_enabled_override = True
            self.support_runners_enabled = True
            self.first_receiver_enabled = True
            self.lane_update_enabled = True
            self.target_update_enabled = True

    def freeze_bc(self, caller):
        """Freeze ball carrier with debug trace."""
        if DEBUG_WORLD_TRACE:
            print(f"[FREEZE BC] {caller}")
        if self.ball_carrier is not None:
            self.ball_carrier.frozen = True

    def freeze_ball(self, caller):
        """Freeze ball with debug trace."""
        if DEBUG_WORLD_TRACE:
            print(f"[FREEZE BALL] {caller}")
        if self.ball is not None:
            self.ball.frozen = True

    def set_ruck(self, x, y, caller):
        """Set ruck position with debug trace."""
        if DEBUG_WORLD_TRACE:
            print(f"[RUCK SET] {caller} → ({x:.2f},{y:.2f})")
        self.ruck_x_m = x
        self.ruck_y_m = y
