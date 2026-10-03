"""Dataclass for tackler/ball-carrier context fed into TES."""

from dataclasses import dataclass, field
from typing import Dict, Optional, Any, Set


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
