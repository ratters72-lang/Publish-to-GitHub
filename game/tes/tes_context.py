"""Dataclass for tackler/ball-carrier context fed into TES."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set, Tuple


# ------------------------------------------------------------------
# ATTACH () — Phase 1/3 attach geometry
# ------------------------------------------------------------------
# Radius (metres) within which a valid tackler becomes "attached" to
# the ball carrier during TACKLE_CONTACT.
ATTACH_RADIUS: float = 0.7

# Fixed attach slot offsets relative to the BC's FACING direction
# (rotated per-frame so "left"/"right"/"back" track BC orientation).
# Magnitudes are stored here; the engine rotates them by bc.facing.
ATTACH_SLOT_LEFT_OFFSET_M: float = 0.6   # slot 0: left of BC
ATTACH_SLOT_RIGHT_OFFSET_M: float = 0.6  # slot 1: right of BC
ATTACH_SLOT_BACK_OFFSET_M: float = 0.8   # slot 2: back of BC

# ------------------------------------------------------------------
# BOOST TACKLE MOMENTUM (2026-08-05) — DEFENDER RESISTANCE REDUCTION
# ------------------------------------------------------------------
# Applied once at TES snapshot time (game/tes/tes.py::_make_ctx) to every
# DEFENDER PlayerContext so all downstream consumers (drive-force
# resistance, dominance, drive_strength, drive_distance, break-tackle
# resistance) see lower defender suppression.  The attacker snapshot is
# never scaled.
DEF_STRENGTH_MOMENTUM_MULT: float = 0.85   # direct drive-force resistance
DEF_AGGRESSION_MOMENTUM_MULT: float = 0.85
DEF_TACKLE_SKILL_MOMENTUM_MULT: float = 0.85
DEF_MASS_MOMENTUM_MULT: float = 0.95       # tackler momentum / mass inputs


@dataclass
class PlayerContext:
    """Per-player data snapshot used during a tackle engagement."""
    player_id: int = -1
    x_m: float = 0.0
    y_m: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    mass: float = 90.0       # kg
    stamina: float = 1.0     # 0..1
    momentum: float = 0.0    # computed from mass × speed
    aggression: float = 0.5  # 0..1 (player tendency)
    tackle_skill: float = 0.5  # 0..1
    offload_skill: float = 0.5  # 0..1
    strength: float = 0.5   # 0..1
    facing: float = 0.0     # facing direction in radians
    rot: float = 0.0        # rotation (used in tackle_score waveform)
    state: str = "DEFENDER_RUNNING"  # movement state: DEFENDER_RUNNING, DEFENDER_CHASING, etc.

    @property
    def speed(self) -> float:
        """Horizontal speed (magnitude of velocity)."""
        import math
        return math.hypot(self.vx, self.vy)


@dataclass
class TackleEngagement:
    """Tracks all defenders involved in a single tackle."""

    # Primary defender (the one who initiated)
    primary_id: int = -1

    # All tacklers keyed by player_id
    tacklers: Dict[int, PlayerContext] = field(default_factory=dict)

    # Wall-clock join times (seconds since tackle initiation)
    join_times: Dict[int, float] = field(default_factory=dict)

    # How many defenders are currently in the tackle
    num_tacklers: int = 0

    # Sim time when the primary tackle was initiated
    started_at: float = 0.0

    @property
    def primary(self) -> PlayerContext | None:
        return self.tacklers.get(self.primary_id)

    @property
    def engaged_ids(self) -> Set[int]:
        """Set of player IDs currently in this engagement."""
        return set(self.tacklers.keys())

    def add(self, ctx: PlayerContext, sim_time: float) -> None:
        """Add a defender to the engagement."""
        self.tacklers[ctx.player_id] = ctx
        if ctx.player_id not in self.join_times:
            self.join_times[ctx.player_id] = sim_time - self.started_at
        self.num_tacklers = len(self.tacklers)
        if self.primary_id < 0:
            self.primary_id = ctx.player_id

    def has(self, player_id: int) -> bool:
        return player_id in self.tacklers

    def combined_tackle_skill(self) -> float:
        """Average tackle skill across all defenders."""
        if not self.tacklers:
            return 0.0
        return sum(p.tackle_skill for p in self.tacklers.values()) / len(self.tacklers)

    def combined_strength(self) -> float:
        """Average strength across all defenders."""
        if not self.tacklers:
            return 0.0
        return sum(p.strength for p in self.tacklers.values()) / len(self.tacklers)

    def combined_aggression(self) -> float:
        """Average aggression across all defenders."""
        if not self.tacklers:
            return 0.0
        return sum(p.aggression for p in self.tacklers.values()) / len(self.tacklers)

    def total_momentum(self) -> float:
        """Sum of momentum of all defenders."""
        return sum(p.momentum for p in self.tacklers.values())

    def total_mass(self) -> float:
        """Sum of mass of all defenders."""
        return sum(p.mass for p in self.tacklers.values())

    def aggregate_defender(self) -> PlayerContext:
        """Build a synthetic PlayerContext representing the combined defender."""
        if not self.tacklers:
            return PlayerContext()
        return PlayerContext(
            player_id=-2,  # synthetic aggregate
            x_m=self.primary.x_m if self.primary else 0.0,
            y_m=self.primary.y_m if self.primary else 0.0,
            mass=sum(p.mass for p in self.tacklers.values()),
            stamina=sum(p.stamina for p in self.tacklers.values()) / len(self.tacklers),
            momentum=self.total_momentum(),
            aggression=self.combined_aggression(),
            tackle_skill=self.combined_tackle_skill(),
            offload_skill=0.0,
            strength=self.combined_strength(),
        )


@dataclass
class TESContext:
    """Full context for a single tackle engagement."""
    attacker: PlayerContext = field(default_factory=PlayerContext)
    defender: PlayerContext = field(default_factory=PlayerContext)
    engagement: TackleEngagement = field(default_factory=TackleEngagement)

    # ------------------------------------------------------------------
    # PHASE 3 — SELECTED TACKLERS (up to 3)
    # ------------------------------------------------------------------
    # The tacklers explicitly selected for this TES engagement (primary
    # first, then secondaries in join order).  Populated by
    # OpenPlayEngine._start_tackle().  Engaged-but-not-selected defenders
    # exist in `engagement` but never appear here.
    tacklers: List[PlayerContext] = field(default_factory=list)

    # player_id -> "TACKLER_1"/"TACKLER_2"/"TACKLER_3" for each defender
    # selected from the combined marker + defensive-line candidate pool by
    # OpenPlayEngine._start_tackle().  Populated at tackler selection and
    # cleared at RELEASE (via _detach_all_tacklers).  Mirrors the per-player
    # `tes_tackler_role` attribute for fast context-level lookups.
    tackler_roles: Dict[int, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # PHASE 1 — ATTACH STATE (contact-phase only)
    # ------------------------------------------------------------------
    # attached:       player_id -> attach slot index (0 = left, 1 = right,
    #                            2 = back) by order of arrival.
    # attach_offsets: player_id -> fixed (dx, dy) offset relative to the
    #                 BC's FACING direction.  The engine rotates this by
    #                 bc.facing each frame so "left/right/back" tracks the
    #                 BC's actual orientation during the drive.
    # attached_ids:   fast set of attached player ids.
    # attach_enabled: True only during TACKLE_CONTACT (Phase 1 rule 1).
    attached: Dict[int, int] = field(default_factory=dict)
    attach_offsets: Dict[int, Tuple[float, float]] = field(default_factory=dict)
    attached_ids: Set[int] = field(default_factory=set)
    attach_enabled: bool = False

    # ------------------------------------------------------------------
    # PHASE 2 — DRIVE STRENGTH / DOMINANCE INPUTS
    # ------------------------------------------------------------------
    # Aggregated over ALL attached tacklers (spec 2.2 / 3.4).  Computed
    # once at DRIVE entry by OpenPlayEngine._update_tackle_drive().
    total_tackler_mass: float = 0.0
    total_tackler_momentum: float = 0.0

    # Dominance classification cached at DRIVE entry:
    # one of "WEAK", "NEUTRAL", "DOMINANT" (from TESOutcome.tackle_result).
    dominance_result: Optional[str] = None

    # drive_strength: multiplier computed from total_tackler_mass,
    # total_tackler_momentum, dominance_result and number of attached
    # tacklers.  Drives the per-frame BC forward movement in TACKLE_DRIVE.
    drive_strength: float = 1.0

    # ------------------------------------------------------------------
    # PHASE 2 — DRIVE STEP STATE (replaces hardcoded LERP-only movement)
    # ------------------------------------------------------------------
    # drive_step_x: forward metres advanced per second along team_attack_dir
    #               (= |drive_target_x - drive_start_x| / duration * drive_strength).
    #               Sign is applied as team_attack_dir (or reversed when the
    #               defending team dominates, see engine).
    drive_step_mps: float = 0.0

    # positional at contact
    contact_x_m: float = 0.0
    contact_y_m: float = 0.0

    # approach angle (degrees)
    approach_angle_deg: float = 0.0

    # tackle count for the set (1..6)
    tackle_number: int = 1

    # distance from own tryline
    dist_from_tryline_m: float = 60.0

    # fatigue multiplier (applied to both players)
    fatigue: float = 1.0

    # randomness modifier 0..1
    randomness: float = 0.7

    # initial impact score (computed once at CONTACT_MADE)
    initial_impact_score: float = 0.0

    # PTB ready flag — set during RELEASE phase
    ptb_ready: bool = False

    # Release phase timer (accumulated seconds in RELEASE phase)
    release_timer: float = 0.0

    # tuners — attached by the tackle pipeline creator so TES can read
    # friction/drag values for BC favouritism during contact physics
    tuners: Optional[Any] = None

    # Multi-tackler join limits
    max_joiners: int = 3  # maximum total tacklers allowed (primary + secondaries)

    # ------------------------------------------------------------------
    # Phase 2B — Stability & Collapse
    # ------------------------------------------------------------------
    # Tracks the attacker's structural stability during the tackle.
    # Updated every frame in update_waveform() based on computed deltas
    # (angle, vel, stab, twist, wrap).  When stability drops below
    # COLLAPSE_THRESHOLD, collapsed is set True.  Stability feeds into
    # compute_dominance() as a tackle_score multiplier so an unstable
    # attacker gives the defender a dominance advantage.
    stability: float = 1.0
    collapsed: bool = False

    # ------------------------------------------------------------------
    # STEP-03A-04 — TES internal phase machine
    # ------------------------------------------------------------------
    # Set by tes_start_tackle() during TES state initialisation (step 6).
    # Used internally by TES to track lifecycle (CONTACT, DRIVE, DOMINANCE,
    # RELEASE).  Mirrors the OPE phase but at TES granularity.
    phase: Optional[Any] = None  # TESPhase enum value or None before init

    # ------------------------------------------------------------------
    # STEP-03A-04 — Secondary tackler registration gate
    # ------------------------------------------------------------------
    # Set True by tes_start_tackle() after TES lock is established.
    # _maybe_add_joiners() in OpenPlayEngine checks this before allowing
    # additional defenders to register.
    joiner_gate_open: bool = False

    # ------------------------------------------------------------------
    # STEP-03A-01 — TES entry snapshot fields
    # ------------------------------------------------------------------
    # Set by tes_start_tackle() on CONTACT_START entry.  Immutable after
    # initialisation — corresponds to positions at the instant the tackle
    # was triggered.
    entry_time: float = 0.0           # world.sim_time_s at the moment of contact
    bc_x0: float = 0.0                # BC x at entry (metres)
    bc_y0: float = 0.0                # BC y at entry (metres)
    def_x0: float = 0.0               # Primary defender x at entry (metres)
    def_y0: float = 0.0               # Primary defender y at entry (metres)

    # ------------------------------------------------------------------
    # Direction the attacking team runs (+1 = right, -1 = left)
    # Set during TES entry from world.attacking_direction.
    # Used by compute_drive_distance() so drive goes in the correct
    # direction regardless of which way the teams are oriented.
    # ------------------------------------------------------------------
    team_attack_dir: int = 1

    # ------------------------------------------------------------------
    # DRIVE SNAPSHOT FIELDS (set once at DRIVE phase entry)
    # ------------------------------------------------------------------
    # Snapshot of BC.x at the moment DRIVE begins.  Immutable during drive.
    drive_start_x: float = 0.0
    # Computed target X = drive_start_x + outcome.drive_distance_m.  Immutable during drive.
    drive_target_x: float = 0.0
    # Accumulated seconds in DRIVE phase.  Used by the lerp interpolation.
    drive_elapsed_s: Optional[float] = None  # None before first drive frame

    # ------------------------------------------------------------------
    # DRIVE + DOMINANCE VARIATION FIELDS (tes_drive_variation module)
    # ------------------------------------------------------------------
    # Accumulated deterministic forward metres produced by the per-frame
    # drive step (drive_step_mps * dt), attacker boost, and geometric
    # factor.  Reset to 0.0 at DRIVE entry; BC.x = drive_start_x +
    # drive_det_x + drive_variation_x.  Without this accumulator the
    # dt-scaled step was multiplied by elapsed seconds and never reached
    # metre-scale movement.
    drive_det_x: float = 0.0
    # Persistent drive momentum (m/s) that decays by DRIVE_DECAY each
    # DRIVE frame.  Accumulates the per-frame drive force so "extra
    # metres" build up then settle -- never a one-frame snap.
    drive_momentum: float = 0.0
    # Accumulated extra forward metres (signed along team_attack_dir)
    # produced by drive momentum variation during TACKLE_DRIVE.  Separate
    # from drive_target_x so the deterministic LERP target and the
    # snapshot-based break-tackle gates are never perturbed.
    drive_variation_x: float = 0.0
    # Accumulated seconds spent in TACKLE_DOMINANCE.  Drives the
    # dominance pushback loop and the DOMINANCE -> RELEASE gate.
    dominance_elapsed_s: float = 0.0
    # How long TACKLE_DOMINANCE holds before RELEASE.  Small extra
    # duration (over the legacy 0.25 s) makes the dominance pushback
    # visible without stalling the tackle.
    dominance_hold_s: float = 0.25

    # ------------------------------------------------------------------
    # NRL DRIVE GEOMETRY INPUTS (computed once at DRIVE entry)
    # ------------------------------------------------------------------
    # Geometric correction inputs consumed by
    # tes_drive_variation.compute_geometric_factor().  Populated by
    # OpenPlayEngine._update_tackle_drive() on the first DRIVE frame.
    #
    # contact_angle:     defender approach angle in degrees, 0 = perfect
    #                    front-on tackle (mirrors approach_angle_deg).
    # bc_lean_forward:   0..1 — fraction of BC velocity along the attack
    #                    direction (1 = fully leaning into the drive).
    # tackler_alignment: 0..1 — angular scatter of engaged tacklers around
    #                    the primary (0 = perfectly aligned, 1 = opposing
    #                    angles).  0.0 for a single tackler.
    contact_angle: float = 0.0
    bc_lean_forward: float = 1.0
    tackler_alignment: float = 0.0

    # ------------------------------------------------------------------
    # STEP 12z — DOMINANT-FROM-BEHIND GATE (2026-08-06)
    # ------------------------------------------------------------------
    # Set once at TES entry by tes_start_tackle() (game/tes/tes.py), from
    # the live pre-freeze positions and world.attacking_direction.  A
    # defender may only earn a DOMINANT result (defender-wins drive,
    # backward pushback) when defender_can_dominate is True — i.e. they
    # hit the BC from in front or level, not chasing from behind.
    # compute_dominance() caps DOMINANT -> NEUTRAL when this is False.
    #
    # can_dominate_in_front:  True if the defender is at/ahead of the BC
    #       along the attack axis (within DOMINANT_BEHIND_TOLERANCE_M).
    # can_dominate_angle_deg: |angle| of vector defender->BC relative to
    #       the attack axis (0 = straight ahead) at TES entry.  Diagnostic.
    defender_can_dominate: bool = True  # default permissive (no cap)
    can_dominate_in_front: bool = True
    can_dominate_angle_deg: float = 0.0

    # ------------------------------------------------------------------
    # STEP 12a — MOMENTUM GATE (2026-08-06) — pre-contact BC momentum discount
    # ------------------------------------------------------------------
    # Captured once at TES entry from the BC's REAL pre-contact velocity
    # (before the drive writers override it).  1.0 = full sprint, 0.35 =
    # flat-footed.  Applies as a scalar multiplier on drive_step_mps and
    # compute_drive_distance so a flat-footed BC does not drive 3 m.
    bc_momentum_factor: float = 1.0
    # ------------------------------------------------------------------
    # FORWARD-DRIVE REBALANCE (2026-08-06) — sideways fold + backward cap
    # ------------------------------------------------------------------
    # Per-tackle accumulation of ACTUAL backward metres applied to the BC
    # during TACKLE_DRIVE + TACKLE_DOMINANCE (post-rebalance X steps where
    # the step moves opposite to team_attack_dir).  Once this exceeds
    # NET_BACKWARD_CAP_M (2.0 m) the engine damps further backward push by
    # NET_BACKWARD_DAMP (0.3) so a tackle can never shove the BC more than
    # ~2 m backward.  Reset to 0.0 at DRIVE entry; forward metres never
    # count.  PURE engine-owned state — tes_drive_variation never reads it.
    net_backward_m: float = 0.0
    # Side-fold direction for the lateral (Y) part of the dominance
    # pushback / attacker-win fold: +1 folds "up", -1 folds "down".
    # Computed once at DRIVE entry from the defender->BC approach side
    # (bc_y0 - def_y0 sign); random ±1 only when level (< 0.3 m apart).
    # Keeps tackle movement reading as intentional geometry, not noise.
    side_fold_sign: float = 1.0
    # Flat-footed diagnostic record (pre-contact state) for
    # investigate_tes_forward_momentum().
    bc_precontact_vx: float = 0.0
    bc_precontact_accel: float = 0.0
    bc_precontact_step: str = ""
    bc_flatfooted: bool = False
    # Per-frame momentum/velocity log for investigate_tes_forward_momentum().
    # List of dicts, one per TES frame; CSV-dumped at RELEASE entry.
    frame_log: List[dict] = field(default_factory=list)

    # ------------------------------------------------------------------
    # THE MULTI-BODY WRAP MODEL (2026-09-23)
    # ------------------------------------------------------------------
    # THE declaration's collaborative pressure, derived ONCE at TES entry
    # (game/tes/tes.py::tes_start_tackle) from the complete multi-body
    # ContestRecord the boundary carries: the winning defender PLUS every
    # in-radius body the authority never evaluated, summed into one
    # collaborative wrap force.
    #
    # EVERY consumer of this block is a NO-OP while ``wrap_model`` is None —
    # a contest whose record could not be resolved, the engine's historical
    # bare-defender dispatch, and the ``RLM_TES_MULTI_BODY=0`` rollback arm all
    # land here.  The model only ever ADDS a term; its absence removes exactly
    # that term, so a single-body tackle runs the physics it ran before this
    # stage, bit for bit.
    #
    # These are MIRRORS of the model's own readers, cached on the context so
    # per-frame physics never has to walk the record's row list.  The MODEL is
    # the source of truth; these are the working set, and the contract test
    # asserts they agree with it.
    wrap_model: Optional[Any] = None
    wrap_force_n: float = 0.0
    wrap_momentum_kgmps: float = 0.0
    wrap_assist_ids: Tuple[int, ...] = ()
    wrap_contribution_count: int = 0
    multibody_duration_scale: float = 1.0
    tackle_break_window: bool = False
    offload_window_open: bool = False
    # The RESOLVED COLLISION STATE token (dynamic results pipeline, step 1):
    # exactly ``tes_multi_body.resolve_collision_state(model)`` for the model
    # attached above -- one of BUMP_OFF / DOMINANT_SMASH / PROGRESSIVE_DRAG.
    # MIRRORED from the model rather than recomputed, and the default is the
    # leaf's own undecided token, which the contract gate asserts is
    # ``tes_multi_body.COLLISION_STATE_PROGRESSIVE_DRAG`` so the two can never
    # drift.  No live physics reads it yet; it is the result step 2 consumes.
    wrap_collision_state: str = "PROGRESSIVE_DRAG"

    # ------------------------------------------------------------------
    # Convenience properties for multi-tackler join logic
    # ------------------------------------------------------------------

    @property
    def engaged_tacklers(self) -> Set[int]:
        """Set of player IDs currently engaged in this tackle."""
        return self.engagement.engaged_ids

    @property
    def engaged_count(self) -> int:
        """Number of tacklers currently engaged."""
        return self.engagement.num_tacklers
