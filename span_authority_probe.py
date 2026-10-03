"""
span_authority_probe.py — COMMAND-vs-REALISED SPAN MEASUREMENT ENGINE (STAGE 1).

WHAT THIS IS
------------
The Stage-1 measurement half of the span-authority programme.  It answers one
question, for a representative grid of (field position, ruck position,
defender count, phase):

    does the COMMANDED width become the REALISED winger-to-winger span?

It does NOT assert.  ``verify_span_authority.py`` is the test that consumes
this module.  Nothing here writes production state: every case builds its own
stub world and its own line, and every measurement is taken from the REAL
shipped functions (``interpolated_defensive_width``, ``governed_width_m``,
``side_widths``, ``side_lanes``, ``compute_targets``,
``establish_slot_ownership``).

THE AUTHORITY CHAIN MEASURED (five levels, same case)
-----------------------------------------------------
    1. COMMANDED   interpolated_defensive_width(tuners, bc.x)
    2. GOVERNED    governed_width_m(world, tuners)      [PHYSICAL FIELD CAP
                                                         ONLY — no width rails
                                                         since 2026-09-14,
                                                         Option B]
    3. PER-SIDE    side_widths(world, tuners, ruck_y=...)
    4. LANE GEOM   outermost lane per side from side_lanes(..., side_width=W)
    5. REALISED    span of the line's published Y targets (compute_targets) —
                   and, for the TES drive chamber, the census ladder from
                   ``establish_slot_ownership``.

DIVERGENCE DECOMPOSITION
------------------------
``realised - commanded`` is decomposed into a TELESCOPING chain of named
deltas so every metre is attributed to a stage:

    commanded
      -> governed                      delta: CLAMP_MAX (physical field cap;
                                       CLAMP_MIN is RETIRED and never fires)
      -> split span                    delta: SPLIT_FACTOR
      -> room-capped span              delta: ROOM_CAP
      -> lane span (outer lanes)       delta: LANE_STEP_FLOOR | SIDELINE_LANE_CLAMP
      -> realised span                 delta: GOVERNOR_BAND_FIT | BAND_PULL
    (TES only)
      -> flat-gap census span          delta: TES_WIDTH_FREE_LADDER

Plus two latent defects the audit flagged, reported as named sources when they
fire: RUCK_Y_ARG_IGNORED (``side_widths`` discards its ``ruck_y=`` argument for
the room cap and re-reads ``world.ruck_y_m``) and
GOVERNED_ACCESSOR_DIVERGENCE (the two ``governed_width_m`` accessors disagree
whenever the governor is inactive).

PREDICTED vs OBSERVED
---------------------
The decomposition is computed TWICE: once from the shipped functions
(observed) and once from an explicit re-statement of the documented arithmetic
(predicted — the ``model_*`` functions below).
``verify_span_authority.py`` fails when the two disagree beyond
``ATTRIB_TOL_M``, i.e. when the code has moved and the documented model has
not.  That is what makes this the canonical authority test: a behavioural
change that alters span without updating the model is caught.

READ-ONLY.  No file under ``game/`` or ``world/`` is modified.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

from player.player import Player, DefenderRole
from world.world_state import GameSubState

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    interpolated_defensive_width,
    governed_width_m,
    clamp_to_sideline,
    LEFT_TRYLINE_X,
    HALFWAY_X,
    RIGHT_TRYLINE_X,
    ATTACKING_TRYLINE_X,
    DEFENDING_10M_X,
    DEFENDING_TRYLINE_X,
    attacking_tryline_anchor_width_m,
    halfway_anchor_width_m,
    defending_10m_anchor_width_m,
    defending_tryline_anchor_width_m,
)
from game.defence.defence_lvl_1.defensive_line_governor import (
    DefensiveLineGovernor,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets,
    side_widths,
    side_lanes,
    lane_gap_m,
    ruck_adjacent_anchor,
    resolve_short_side,
    RUCK_ADJACENT_ANCHOR_M,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1 import tes_drive_structure as tds

# ---------------------------------------------------------------------
# EXPLICIT TOLERANCES (the "explicitly defined tolerance" of the brief)
# ---------------------------------------------------------------------
# SPAN_AUTHORITY_TOL_M — the contract: the realised span must equal the
#   commanded width within this unless a PHYSICAL reducer (field room)
#   binds.  Tight, because the relationship is an exact geometric identity
#   when no reducer binds.
SPAN_AUTHORITY_TOL_M: float = 0.10
# ATTRIB_TOL_M — observed vs predicted decomposition.  Both are computed
#   from the same floating-point chain, so this is float noise only.
ATTRIB_TOL_M: float = 0.02
# ROOM_BIND_TOL_M — equality tolerance when the room cap is the binding
#   authority (the span is then a function of ruck Y, not of the command).
ROOM_BIND_TOL_M: float = 0.05

# ---------------------------------------------------------------------
# NAMED DIVERGENCE SOURCES
# ---------------------------------------------------------------------
CLAMP_MIN = "CLAMP_MIN"                          # RETIRED width floor (never fires)
CLAMP_MAX = "CLAMP_MAX"                          # commanded > max/field cap
SPLIT_FACTOR = "SPLIT_FACTOR"                    # tuner_short_side_factor x governed
ROOM_CAP = "ROOM_CAP"                            # short-side room collapse
LANE_STEP_FLOOR = "LANE_STEP_FLOOR"              # min_defender_gap_m floors the step
SIDELINE_LANE_CLAMP = "SIDELINE_LANE_CLAMP"      # outermost lane clamped in
TES_WIDTH_FREE_LADDER = "TES_WIDTH_FREE_LADDER"  # census omits side_width
GOVERNOR_BAND_FIT = "GOVERNOR_BAND_FIT"          # _fit_lanes_to_band
BAND_PULL = "BAND_PULL"                          # engagement band / anticipation
RUCK_Y_ARG_IGNORED = "RUCK_Y_ARG_IGNORED"        # side_widths re-reads world.ruck_y_m
GOVERNED_ACCESSOR_DIVERGENCE = "GOVERNED_ACCESSOR_DIVERGENCE"
NO_BALL_CARRIER = "NO_BALL_CARRIER"              # command undefined
UNATTRIBUTED = "UNATTRIBUTED"                    # observed != predicted

ALL_SOURCES = (
    CLAMP_MIN, CLAMP_MAX, SPLIT_FACTOR, ROOM_CAP, LANE_STEP_FLOOR,
    SIDELINE_LANE_CLAMP, TES_WIDTH_FREE_LADDER, GOVERNOR_BAND_FIT,
    BAND_PULL, RUCK_Y_ARG_IGNORED, GOVERNED_ACCESSOR_DIVERGENCE,
    NO_BALL_CARRIER, UNATTRIBUTED,
)

# ---------------------------------------------------------------------
# CASE GRID
# ---------------------------------------------------------------------
BC_X_GRID = (10.0, 30.0, 50.0, 60.0, 70.0, 90.0, 110.0)
RUCK_Y_GRID = (10.0, 20.0, 27.5, 35.0, 42.5, 50.0, 60.0)
SPLIT_GRID = ((3, 3), (4, 4), (5, 5), (6, 5), (2, 4))
LANE_PHASES = ("OPEN_PLAY", "PTB", "POST_RUCK_OPEN_PLAY")
TES_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
TUNER_SWEEP = (25.0, 40.0, 55.0, 70.0, 88.0)

RUCK_X = 60.0
_SIDE_ROLES = ("A", "B", "C", "D", "CENTRE", "WINGER")
_TES_ROLES = (
    DefenderRole.DEF_LINE_READY,
    DefenderRole.DEF_CHASE,
    DefenderRole.DEF_RETREAT_10,
)
# =====================================================================
# DOCUMENTED MODEL (predicted) — an explicit re-statement of the shipped
# arithmetic, written from the DOCUMENTATION/comments rather than copied
# from the implementation, so a code/doc divergence becomes detectable.
# =====================================================================

def model_governed_width(commanded: float, tuners: DefenceTuners) -> float:
    """Predicted governed width: the PHYSICAL FIELD CAP and nothing else.

    WIDTH RAILS REMOVED (2026-09-14, Option B): the former minimum-FLOOR and
    maximum-CEILING width tuners are GONE, so ``governed_width_m`` is the
    IDENTITY below the field cap.  The only surviving bound is
    ``field_width_m - 2 * sideline_margin_m`` — geometry, not a tuning rail.
    """
    margin = float(getattr(tuners, "sideline_margin_m", 3.0))
    field_cap = float(getattr(tuners, "field_width_m", 70.0)) - 2.0 * margin
    return max(0.0, min(float(commanded), field_cap))


def model_split_span(governed: float, short_side: int,
                     tuners: DefenceTuners) -> tuple:
    """Predicted ``(split_short, split_open)`` — governed x split factor.

    This is the width BEFORE the min/max clamp and BEFORE the room cap.
    """
    # GOVERNED WIDTH = AUTHORITATIVE TOTAL SPAN (2026-09-14): the short/open
    # SPLIT FACTOR is no longer a span reducer, so BOTH sides carry the
    # governed width exactly.  The factors survive on the tuners for
    # compatibility but no longer scale a side, which is why the observed
    # SPLIT_FACTOR attribution is now identically 0.0.
    del short_side  # no side is biassed: both spans are the governed width
    return float(governed), float(governed)


def model_room_cap_m(tuners: DefenceTuners, ruck_y: float) -> tuple:
    """Predicted ``(room_left, room_right)`` HALF-spans at *ruck_y*, metres.

    The margin-only usable distance from the scope ruck to each touchline —
    the ABSOLUTE SIDELINE SAFETY bound and the ONLY reducer left in the
    chain.  ``short_side_buffer_m`` is no longer part of it: the winger may
    stand up to the touchline margin because the governed width is the
    authoritative span and no tactical buffer may narrow it.
    """
    margin = float(getattr(tuners, "sideline_margin_m", 3.0))
    field_max = float(getattr(tuners, "field_width_m", 70.0)) - margin
    return (max(0.0, float(ruck_y) - margin),
            max(0.0, field_max - float(ruck_y)))


def model_distribute_span(left_width: float, right_width: float,
                          room_left: float, room_right: float) -> tuple:
    """Predicted ``(left_span, right_span)`` after the room distribution.

    Mirrors ``formation_width_authority.distribute_total_span``: the TARGET
    total span is the mean of the two per-side requests (the governed width),
    split evenly and then balanced toward whichever side still has
    margin-only room.  Only when BOTH halves are exhausted does the published
    total fall below the request — that residual is the absolute sideline
    safety clamp, the ONE permitted reduction of the realised span.
    """
    total = 0.5 * (float(left_width) + float(right_width))
    room_l = max(0.0, float(room_left))
    room_r = max(0.0, float(room_right))
    half = total * 0.5
    half_l = min(half, room_l)
    half_r = min(half, room_r)
    shortfall = total - (half_l + half_r)
    if shortfall > 1e-12:
        if (room_l - half_l) >= (room_r - half_r):
            half_l = min(room_l, half_l + shortfall)
            half_r = min(room_r, total - half_l)
        else:
            half_r = min(room_r, half_r + shortfall)
            half_l = min(room_l, total - half_r)
    return half_l * 2.0, half_r * 2.0


def model_clamp_side(width: float, tuners: DefenceTuners) -> float:
    """Predicted per-side min/max clamp ONLY (no room cap, no sideline).

    NO-OP since 2026-09-14 (Option B): the per-side minimum-floor /
    maximum-ceiling tuner clamp was REMOVED with the tuners themselves.  The
    per-side span IS the governed width — already field-capped upstream — so
    this step is the IDENTITY.  Retained as a NAMED step so any future
    re-introduction of a per-side rail shows up as a non-zero CLAMP delta on
    the very next measurement.
    """
    _ = tuners
    return float(width)


def model_lane_outer(side_width, n_side, ruck_y: float, side: int,
                     tuners: DefenceTuners):
    """Predicted outermost lane Y for one side (mirrors ``side_lanes``).

    ``side_width=None`` reproduces the no-width fallback ladder that the TES
    census takes.  Returns None for an empty side.
    """
    if n_side is None or int(n_side) <= 0:
        return None
    n_side = int(n_side)
    a_y = ruck_adjacent_anchor(ruck_y, side)
    if n_side == 1:
        return clamp_to_sideline(a_y, tuners)
    if side_width is None:
        gap = float(getattr(tuners, "defender_gap_m", 4.0))
    else:
        gap = lane_gap_m(side_width, n_side, tuners)
    raw_outer = float(ruck_y) + int(side) * (
        RUCK_ADJACENT_ANCHOR_M + (n_side - 1) * float(gap)
    )
    return clamp_to_sideline(raw_outer, tuners)


def model_lane_span(left_width, right_width, n_left: int, n_right: int,
                    ruck_y: float, tuners: DefenceTuners) -> float:
    """Predicted winger-to-winger span from the predicted outermost lanes."""
    outer_l = model_lane_outer(left_width, n_left, ruck_y, LEFT, tuners)
    outer_r = model_lane_outer(right_width, n_right, ruck_y, RIGHT, tuners)
    if outer_l is None or outer_r is None:
        return 0.0
    return float(outer_r) - float(outer_l)


def model_tes_ladder_span(n_left: int, n_right: int, ruck_y: float,
                          tuners: DefenceTuners, left_width=None,
                          right_width=None) -> float:
    """Predicted TES census span.

    STAGE 6 (2026-09-13): the TES drive-chamber census ladder now consumes
    the SAME published per-side width the lane path does, so it is no longer
    width-free.  Passing the widths models the CURRENT law; the ``None``
    form reproduces the retired width-free flat-gap ladder so the old
    behaviour stays expressible for comparison.
    """
    if left_width is None or right_width is None:
        return model_lane_span(None, None, n_left, n_right, ruck_y, tuners)
    return model_lane_span(left_width, right_width, n_left, n_right, ruck_y, tuners)


# =====================================================================
# STUB WORLD + LINE BUILDERS
# =====================================================================

class SpanProbeWorld:
    """Minimal world stub carrying only what the span chain reads."""

    def __init__(self, ruck_y: float, phase="OPEN_PLAY", commanded: float = 55.0):
        self.ruck_x_m = RUCK_X
        self.ruck_y_m = float(ruck_y)
        self.def_line_width_m = float(commanded)
        self.governor_line_width_m = float(commanded)
        self.governor_width_active = True
        self.frame_count = 1
        self.dt = 1.0 / 60.0
        self.ball_carrier = None
        self.defending_team = []
        self.attacking_team = []
        self.players = []
        self.in_pursuit_mode = False
        self.sub_state = phase
        self.phase = None
        self._ope_phase_label = phase
        self.defensive_line_anchor = RUCK_X + 10.0
        self.anchor_locked = False
        self.ruck_established = True
        self._last_count_split = None
        self.frame_geometry_ready = False
        self.frame_geometry_frame = None
        self.frame_spacing_targets = None
        self.frame_width_targets = None
        self.frame_side_frame = None
        # Fields the guarded run_defensive_pipeline cross-check touches.
        self.role_depth_state = {}
        self.depth_role_state = {}
        self.open_play_engine = None
        self.ctx = None
        self.ball = None
        self.first_receiver = None
        self.role_governs = False
        self.defence_ran_this_frame = True
        self.first_tackle_not_made = False
        self.use_structured_open_play = True
        self.defence_enabled = True
        self.tes_active = False
        self.frames_since_ptb = 100


class StubCarrier:
    """Ball carrier stub — position only; the probe never moves it."""

    def __init__(self, x: float, y: float):
        self.x = float(x)
        self.y = float(y)
        self.vx = 0.0
        self.vy = 0.0
        self.in_contact = False


def make_line(n_left: int, n_right: int, ruck_y: float,
              *, base_pid: int = 100):
    """Build a line of real Player objects with sides pre-stamped.

    ``depth_side`` is stamped explicitly so the count partition is exact and
    independent of the physical Y spread (the spacing chain partitions by
    ``depth_side_of``, not by live position).
    """
    line = []
    pid = base_pid
    for i in range(n_left):
        pid += 1
        d = Player(x=RUCK_X, y=ruck_y - 3.0 * (i + 1), player_id=pid)
        d.depth_role = _SIDE_ROLES[min(i, len(_SIDE_ROLES) - 1)]
        d.depth_side = LEFT
        d.is_defender = True
        d.fullback_role_active = False
        d.tackle_state_active = False
        line.append(d)
    for i in range(n_right):
        pid += 1
        d = Player(x=RUCK_X, y=ruck_y + 3.0 * (i + 1), player_id=pid)
        d.depth_role = _SIDE_ROLES[min(i, len(_SIDE_ROLES) - 1)]
        d.depth_side = RIGHT
        d.is_defender = True
        d.fullback_role_active = False
        d.tackle_state_active = False
        line.append(d)
    return line


def make_tes_line(n_left: int, n_right: int, ruck_y: float,
                  *, base_pid: int = 200):
    """Build a line of structure-hold-eligible bodies for the TES census."""
    line = []
    pid = base_pid
    for i in range(n_left):
        pid += 1
        d = Player(x=RUCK_X, y=ruck_y - 3.0 * (i + 1), player_id=pid)
        d.defender_role = _TES_ROLES[i % len(_TES_ROLES)]
        d.fullback_role_active = False
        line.append(d)
    for i in range(n_right):
        pid += 1
        d = Player(x=RUCK_X, y=ruck_y + 3.0 * (i + 1), player_id=pid)
        d.defender_role = _TES_ROLES[i % len(_TES_ROLES)]
        d.fullback_role_active = False
        line.append(d)
    return line
def _side_role_slots(n_side: int):
    """Return the per-side role tuple for *n_side* bodies (WINGER outermost)."""
    return tuple(_SIDE_ROLES[i % len(_SIDE_ROLES)] for i in range(n_side))


def _outer_lane(n_side: int, ruck_y: float, side: int, tuners,
                side_width) -> float | None:
    """Outermost lane Y from the REAL ``side_lanes`` (None for an empty side)."""
    if n_side is None or int(n_side) <= 0:
        return None
    # SPAN CONTRACT (Stage 2, 2026-09-13): ``side_lanes`` takes the per-side
    # width as the MANDATORY ``span=`` keyword; the retired ``side_width=``
    # name was removed when the ladder became span-derived.
    lanes = side_lanes(int(n_side), ruck_y, side, tuners, span=side_width)
    return float(lanes[-1]) if lanes else None


def _span_of(targets: dict) -> float:
    """Span of a ``{id(d): y}`` target dict (0.0 when it has < 2 entries)."""
    ys = list(targets.values())
    if len(ys) < 2:
        return 0.0
    return float(max(ys)) - float(min(ys))


def model_interpolated_width(tuners: DefenceTuners, bc_x: float) -> float:
    """Predicted commanded width — the FOUR-ANCHOR compression model.

    (2026-10-03) The width is interpolated between four field-position
    anchors:

        attacking tryline (10)  -> attacking tryline width
        halfway           (60)  -> halfway width
        defending 10 m    (100) -> defending-tryline REGION x compression
        defending tryline (110) -> defending-tryline REGION x compression

    The two goal-line anchors are COMPRESSIONS of the ONE defending-tryline
    width region, so the defending 10 m line has no width tuner of its own.
    """
    attack = attacking_tryline_anchor_width_m(tuners)
    mid = halfway_anchor_width_m(tuners)
    ten_m = defending_10m_anchor_width_m(tuners)
    tryline = defending_tryline_anchor_width_m(tuners)
    x = min(max(float(bc_x), ATTACKING_TRYLINE_X), DEFENDING_TRYLINE_X)
    if x <= HALFWAY_X:
        t = (x - ATTACKING_TRYLINE_X) / (HALFWAY_X - ATTACKING_TRYLINE_X)
        return attack * (1.0 - t) + mid * t
    if x <= DEFENDING_10M_X:
        t = (x - HALFWAY_X) / (DEFENDING_10M_X - HALFWAY_X)
        return mid * (1.0 - t) + ten_m * t
    t = (x - DEFENDING_10M_X) / (DEFENDING_TRYLINE_X - DEFENDING_10M_X)
    return ten_m * (1.0 - t) + tryline * t


def _model_band_fit_outer(outer, a_y, ruck_y: float, half_width: float):
    """Predicted outermost lane after ``_fit_lanes_to_band``."""
    if outer is None:
        return None
    direction = 1 if float(outer) >= float(a_y) else -1
    bound = float(ruck_y) + float(direction) * float(half_width)
    if direction * (bound - float(a_y)) <= 0.0:
        return float(outer)
    if direction * (float(outer) - bound) <= 1e-9:
        return float(outer)
    return float(bound)


def _model_clamp_outer(outer, ruck_y: float, half_left: float,
                       half_right: float, tuners):
    """Predicted outermost lane after ``governor.apply_width_clamp``.

    GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14): the clamp
    band is the PUBLISHED per-side span about the ruck —
    ``[ruck_y - half_left, ruck_y + half_right]`` — so it is asymmetric
    whenever the line has slid toward the roomier touchline.  The symmetric
    ``+/- half_width`` form re-declared the governed width as a per-side
    value and re-narrowed the far side of an off-centre ruck.
    """
    if outer is None:
        return None
    margin = float(getattr(tuners, "sideline_margin_m", 3.0))
    field_max = float(getattr(tuners, "field_width_m", 70.0)) - margin
    lo = max(float(ruck_y) - float(half_left), margin)
    hi = min(float(ruck_y) + float(half_right), field_max)
    return max(lo, min(hi, float(outer)))


def _add_source(breakdown: dict, name: str, delta: float) -> None:
    """Accumulate one named delta, skipping (and never storing) a no-op."""
    if abs(float(delta)) < 1e-9:
        return
    breakdown[name] = breakdown.get(name, 0.0) + float(delta)


@dataclass
class SpanMeasurement:
    """One measured case: command -> governed -> per-side -> lane -> realised."""

    label: str
    path: str                       # "lane" | "tes"
    phase: str
    bc_x: float
    ruck_y: float
    n_left: int
    n_right: int
    commanded_m: float
    governed_m: float
    governed_alt_m: float
    side_left_m: float
    side_right_m: float
    lane_span_m: float
    realised_span_m: float
    predicted_span_m: float
    divergence_m: float             # realised - commanded
    unexplained_m: float            # realised - predicted
    room_capped: bool
    governor_active: bool
    carrier_present: bool
    breakdown: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    @property
    def divergence_ratio(self) -> float:
        if abs(self.commanded_m) < 1e-9:
            return 0.0
        return self.realised_span_m / self.commanded_m


def to_json(m: SpanMeasurement) -> dict:
    """JSON-friendly view of a measurement."""
    d = asdict(m)
    d["divergence_ratio"] = round(m.divergence_ratio, 6)
    return d


def _room_cap_binds(clamped_l: float, clamped_r: float, cap_l: float,
                    cap_r: float) -> bool:
    """True when the sideline room bound lowered the TOTAL realised span.

    Compares the REQUESTED total span (the mean of the two pre-distribution
    per-side requests) against the PUBLISHED total (the mean of the two
    distributed side spans).  Comparing the per-side values would flag the
    harmless re-balancing of a line that is merely sliding toward the roomier
    touchline, which is not a reduction of the span at all.
    """
    requested = 0.5 * (float(clamped_l) + float(clamped_r))
    published = 0.5 * (float(cap_l) + float(cap_r))
    return published < requested - 1e-6


def measure_lane_case(
    phase: str,
    bc_x: float,
    ruck_y: float,
    n_left: int,
    n_right: int,
    tuners: DefenceTuners,
    *,
    label: str = "",
    with_governor: bool = True,
    with_carrier: bool = False,
) -> SpanMeasurement:
    """Measure one lane-path case (OPEN_PLAY / PTB / POST_RUCK_OPEN_PLAY)."""
    commanded = interpolated_defensive_width(tuners, bc_x)
    world = SpanProbeWorld(ruck_y, phase, commanded)
    if with_carrier:
        world.ball_carrier = StubCarrier(bc_x, ruck_y)
    line = make_line(n_left, n_right, ruck_y)
    world.defending_team = list(line)
    gov = DefensiveLineGovernor(tuners, None) if with_governor else None

    targets = compute_targets(world, line, tuners, governor=gov)
    realised = _span_of(targets)

    governed = governed_width_m(world, tuners)
    governed_alt = DefensiveLineGovernor(tuners, None).governed_width_m(world)
    left_w, right_w = side_widths(world, tuners, ruck_y=ruck_y)
    short_side = resolve_short_side(world, tuners, ruck_y=ruck_y)

    outer_l = _outer_lane(n_left, ruck_y, LEFT, tuners, left_w)
    outer_r = _outer_lane(n_right, ruck_y, RIGHT, tuners, right_w)
    lane_span = (0.0 if outer_l is None or outer_r is None
                 else float(outer_r) - float(outer_l))

    # ---- predicted chain (documented arithmetic) ----
    gov_pred = model_governed_width(model_interpolated_width(tuners, bc_x), tuners)
    chain = _attribute_width_chain(
        commanded, governed, short_side, tuners,
        float(getattr(world, "ruck_y_m", ruck_y)), ruck_y, n_left, n_right)
    cap_l = chain["_cap_left"]
    cap_r = chain["_cap_right"]
    outer_l_pred = chain["_outer_left"]
    outer_r_pred = chain["_outer_right"]
    if with_governor:
        # The clamp band is the PUBLISHED per-side span (GOVERNED WIDTH IS
        # THE AUTHORITATIVE TOTAL SPAN, 2026-09-14): each side is fitted and
        # clamped against its OWN half-span, which is what the shipped
        # ``_fit_lanes_to_band`` / ``apply_width_clamp`` now read.
        half_l = float(cap_l) * 0.5
        half_r = float(cap_r) * 0.5
        outer_l_pred = _model_clamp_outer(
            _model_band_fit_outer(
                outer_l_pred, ruck_adjacent_anchor(ruck_y, LEFT),
                ruck_y, half_l),
            ruck_y, half_l, half_r, tuners)
        outer_r_pred = _model_clamp_outer(
            _model_band_fit_outer(
                outer_r_pred, ruck_adjacent_anchor(ruck_y, RIGHT),
                ruck_y, half_r),
            ruck_y, half_l, half_r, tuners)
    predicted = (0.0 if outer_l_pred is None or outer_r_pred is None
                 else float(outer_r_pred) - float(outer_l_pred))

    breakdown = {k: v for k, v in chain.items() if not k.startswith("_")}
    cap_span = (float(cap_l) + float(cap_r)) * 0.5
    lane_delta = lane_span - cap_span
    if abs(lane_delta) > 1e-9:
        direction = LANE_STEP_FLOOR if _floor_binds(
            cap_l, cap_r, n_left, n_right, tuners) else SIDELINE_LANE_CLAMP
        _add_source(breakdown, direction, lane_delta)
    final_delta = realised - lane_span
    if abs(final_delta) > 1e-9:
        _add_source(breakdown, GOVERNOR_BAND_FIT if with_governor else BAND_PULL,
                    final_delta)

    notes = []
    if not with_carrier and with_governor:
        notes.append("isolated: no ball carrier, so no engagement-band pull")
    return SpanMeasurement(
        label=label or f"{phase} bc.x={bc_x} ruck_y={ruck_y} {n_left}-{n_right}",
        path="lane",
        phase=phase,
        bc_x=float(bc_x),
        ruck_y=float(ruck_y),
        n_left=int(n_left),
        n_right=int(n_right),
        commanded_m=float(commanded),
        governed_m=float(governed),
        governed_alt_m=float(governed_alt),
        side_left_m=float(left_w),
        side_right_m=float(right_w),
        lane_span_m=float(lane_span),
        realised_span_m=float(realised),
        predicted_span_m=float(predicted),
        divergence_m=float(realised - commanded),
        unexplained_m=float(realised - predicted),
        room_capped=bool(_room_cap_binds(
            chain["_clamped_left"], chain["_clamped_right"], cap_l, cap_r)),
        governor_active=bool(with_governor),
        carrier_present=bool(with_carrier),
        breakdown={k: round(v, 6) for k, v in breakdown.items()},
        notes=notes,
    )


def _floor_binds(cap_l: float, cap_r: float, n_left: int, n_right: int,
                 tuners: DefenceTuners) -> bool:
    """True when ``min_defender_gap_m`` floors either side's derived step."""
    # GOVERNED WIDTH = AUTHORITATIVE TOTAL SPAN (2026-09-14): the
    # ``min_defender_gap_m`` FLOOR is REMOVED from ``lane_gap_m``, so no
    # ladder is floored any more and no lane reducer other than the absolute
    # sideline clamp can fire.  The predicate is retained so the measurement
    # keeps one call site, and it now truthfully reports False.
    _ = (cap_l, cap_r, n_left, n_right, tuners)
    return False
def _attribute_width_chain(commanded: float, governed: float, short_side: int,
                           tuners: DefenceTuners, cap_ruck_y: float,
                           ruck_y: float, n_left: int, n_right: int) -> dict:
    """Named deltas for the chain ``commanded -> ... -> lane span``.

    Returns ``{source: metres}`` plus the predicted per-side widths under the
    keys ``"_cap_left"`` / ``"_cap_right"`` / ``"_lane_span"``.
    """
    split_short, split_open = model_split_span(governed, short_side, tuners)
    room_l, room_r = model_room_cap_m(tuners, cap_ruck_y)
    if int(short_side) == LEFT:
        split_l, split_r = split_short, split_open
    else:
        split_l, split_r = split_open, split_short
    clamped_l = model_clamp_side(split_l, tuners)
    clamped_r = model_clamp_side(split_r, tuners)
    # GOVERNED WIDTH = AUTHORITATIVE TOTAL SPAN (2026-09-14): the governed
    # width is the TOTAL span, so the only remaining reducer is the ABSOLUTE
    # SIDELINE SAFETY bound.  ``model_distribute_span`` restates the shipped
    # distribution of that total across the two ruck-anchored halves, so
    # ROOM_CAP fires only when the two halves genuinely exhaust the field.
    cap_l, cap_r = model_distribute_span(clamped_l, clamped_r, room_l, room_r)

    out: dict = {}
    _add_source(out, CLAMP_MIN, governed - commanded if commanded < governed else 0.0)
    _add_source(out, CLAMP_MAX, governed - commanded if commanded > governed else 0.0)
    split_span = (float(split_l) + float(split_r)) * 0.5
    clamped_span = (float(clamped_l) + float(clamped_r)) * 0.5
    cap_span = (float(cap_l) + float(cap_r)) * 0.5
    _add_source(out, SPLIT_FACTOR, split_span - governed)
    _add_source(out, CLAMP_MIN if clamped_span > split_span else CLAMP_MAX,
                clamped_span - split_span)
    _add_source(out, ROOM_CAP, cap_span - clamped_span)
    out["_split_left"] = float(split_l)
    out["_split_right"] = float(split_r)
    out["_clamped_left"] = float(clamped_l)
    out["_clamped_right"] = float(clamped_r)
    out["_cap_left"] = float(cap_l)
    out["_cap_right"] = float(cap_r)
    out["_outer_left"] = model_lane_outer(cap_l, n_left, ruck_y, LEFT, tuners)
    out["_outer_right"] = model_lane_outer(cap_r, n_right, ruck_y, RIGHT, tuners)
    out["_lane_span"] = model_lane_span(cap_l, cap_r, n_left, n_right,
                                        ruck_y, tuners)
    return out


def _ladder_sides(world):
    """Return ``(left_ys, right_ys)`` from the published TES slot ladder."""
    ladder = tds.slot_ladder_for(world)
    if not isinstance(ladder, dict):
        return [], []
    sides = ladder.get("sides") or {}
    left = [float(e["y"]) for e in (sides.get(int(LEFT)) or [])]
    right = [float(e["y"]) for e in (sides.get(int(RIGHT)) or [])]
    return left, right


def measure_tes_case(phase: str, bc_x: float, ruck_y: float, n_left: int,
                     n_right: int, tuners: DefenceTuners, *,
                     label: str = "") -> SpanMeasurement:
    """Measure one TES drive-chamber case via the slot-ownership census."""
    commanded = interpolated_defensive_width(tuners, bc_x)
    world = SpanProbeWorld(ruck_y, phase, commanded)
    setattr(world, tds.CHAMBER_ID_ATTR, 7)
    line = make_tes_line(n_left, n_right, ruck_y)
    world.defending_team = list(line)
    bc = StubCarrier(bc_x, ruck_y)
    tds.establish_slot_ownership(world, line, bc, tuners)
    left_ys, right_ys = _ladder_sides(world)
    tes_span = (float(max(right_ys)) - float(min(left_ys))
                if left_ys and right_ys else 0.0)

    governed = governed_width_m(world, tuners)
    governed_alt = DefensiveLineGovernor(tuners, None).governed_width_m(world)
    left_w, right_w = side_widths(world, tuners, ruck_y=ruck_y)
    short_side = resolve_short_side(world, tuners, ruck_y=ruck_y)
    chain = _attribute_width_chain(commanded, governed, short_side, tuners,
                                   float(getattr(world, "ruck_y_m", ruck_y)),
                                   ruck_y, n_left, n_right)
    governed_lane_span = chain["_lane_span"]
    # STAGE 6 (2026-09-13): the census ladder consumes the published per-side
    # width, so the PREDICTED span is the width-derived span.  Modelling the
    # retired width-free ladder here would flag the restored law as a defect.
    predicted = model_tes_ladder_span(
        n_left, n_right, ruck_y, tuners,
        left_width=left_w, right_width=right_w,
    )

    breakdown = {k: v for k, v in chain.items() if not k.startswith("_")}
    _add_source(breakdown, TES_WIDTH_FREE_LADDER, tes_span - governed_lane_span)

    notes = ["TES census ladder consumes the published side width (Stage 6)"]
    return SpanMeasurement(
        label=label or f"{phase} bc.x={bc_x} ruck_y={ruck_y} {n_left}-{n_right}",
        path="tes",
        phase=phase,
        bc_x=float(bc_x),
        ruck_y=float(ruck_y),
        n_left=int(n_left),
        n_right=int(n_right),
        commanded_m=float(commanded),
        governed_m=float(governed),
        governed_alt_m=float(governed_alt),
        side_left_m=float(left_w),
        side_right_m=float(right_w),
        lane_span_m=float(governed_lane_span),
        realised_span_m=float(tes_span),
        predicted_span_m=float(predicted),
        divergence_m=float(tes_span - commanded),
        unexplained_m=float(tes_span - predicted),
        room_capped=bool(_room_cap_binds(
            chain["_clamped_left"], chain["_clamped_right"],
            chain["_cap_left"], chain["_cap_right"])),
        governor_active=True,
        carrier_present=True,
        breakdown={k: round(v, 6) for k, v in breakdown.items()},
        notes=notes,
    )


# =====================================================================
# LATENT-DEFECT PROBES (named sources reported when they fire)
# =====================================================================

def probe_ruck_y_arg_ignored(tuners: DefenceTuners) -> dict:
    """Detect G3: ``side_widths`` caps the room from ``world.ruck_y_m``."""
    world = SpanProbeWorld(30.0, "OPEN_PLAY", 55.0)
    world.ruck_y_m = 30.0
    observed = side_widths(world, tuners, ruck_y=20.0)
    short_side = resolve_short_side(world, tuners, ruck_y=20.0)
    split_short, split_open = model_split_span(55.0, short_side, tuners)
    room_l_world, room_r_world = model_room_cap_m(tuners, 30.0)
    room_l_arg, room_r_arg = model_room_cap_m(tuners, 20.0)
    if int(short_side) == LEFT:
        split_l, split_r = split_short, split_open
    else:
        split_l, split_r = split_open, split_short
    want_world = model_distribute_span(split_l, split_r,
                                       room_l_world, room_r_world)
    want_arg = model_distribute_span(split_l, split_r,
                                     room_l_arg, room_r_arg)
    follows_world = _close_pair(observed, want_world)
    follows_arg = _close_pair(observed, want_arg)
    return {
        "source": RUCK_Y_ARG_IGNORED,
        "arg_ruck_y": 20.0,
        "world_ruck_y_m": 30.0,
        "observed": [round(v, 4) for v in observed],
        "expected_if_arg_honoured": [round(v, 4) for v in want_arg],
        "expected_if_world_used": [round(v, 4) for v in want_world],
        "fires": bool(follows_world and not follows_arg),
    }


def _close_pair(a, b, tol: float = 1e-6) -> bool:
    """True when two 2-tuples agree within *tol*."""
    return (abs(float(a[0]) - float(b[0])) <= tol
            and abs(float(a[1]) - float(b[1])) <= tol)


def probe_accessor_divergence(tuners: DefenceTuners) -> dict:
    """Detect G9: the two ``governed_width_m`` accessors disagree."""
    world = SpanProbeWorld(35.0, "OPEN_PLAY", 20.0)
    world.governor_width_active = False
    world.def_line_width_m = 20.0
    a = governed_width_m(world, tuners)
    b = DefensiveLineGovernor(tuners, None).governed_width_m(world)
    return {
        "source": GOVERNED_ACCESSOR_DIVERGENCE,
        "raw_width_m": 20.0,
        "governor_inactive": True,
        "defensive_width_accessor": round(float(a), 6),
        "governor_accessor": round(float(b), 6),
        "divergence_m": round(float(b) - float(a), 6),
        "fires": abs(float(b) - float(a)) > 1e-6,
    }


def probe_publish_conformance(tuners: DefenceTuners) -> list:
    """Confirm the governor publishes exactly the interpolated command."""
    rows = []
    for bc_x in BC_X_GRID:
        world = SpanProbeWorld(35.0, "OPEN_PLAY", 0.0)
        world.ball_carrier = StubCarrier(bc_x, 35.0)
        gov = DefensiveLineGovernor(tuners, None)
        gov.compute_line_state(world, [])
        want = interpolated_defensive_width(tuners, bc_x)
        rows.append({
            "bc_x": float(bc_x),
            "published_m": round(float(world.def_line_width_m), 6),
            "interpolated_m": round(float(want), 6),
            "conformant": abs(float(world.def_line_width_m) - float(want)) < 1e-9,
        })
    return rows


def probe_no_ball_carrier(tuners: DefenceTuners) -> dict:
    """Record the no-BC case, where the command is not tuner-determined."""
    world = SpanProbeWorld(35.0, "OPEN_PLAY", 40.0)
    world.ball_carrier = None
    world.governor_width_active = False
    world.def_line_width_m = 40.0          # the cold-world default
    governed = governed_width_m(world, tuners)
    line = make_line(4, 4, 35.0)
    span = _span_of(compute_targets(world, line, tuners, governor=None))
    return {
        "source": NO_BALL_CARRIER,
        "raw_def_line_width_m": 40.0,
        "governed_m": round(float(governed), 6),
        "realised_span_m": round(float(span), 6),
        "fires": True,
    }


# =====================================================================
# GRIDS
# =====================================================================

def run_lane_grid() -> list:
    """The lane-path grid: phase x field position x ruck Y x count split."""
    out = []
    for phase in LANE_PHASES:
        for bc_x in BC_X_GRID:
            for ruck_y in RUCK_Y_GRID:
                for n_left, n_right in SPLIT_GRID:
                    out.append(measure_lane_case(
                        phase, bc_x, ruck_y, n_left, n_right, DefenceTuners()))
    return out


def run_tes_grid() -> list:
    """The TES drive-chamber grid (same axes, census ladder path)."""
    out = []
    for phase in TES_PHASES:
        for bc_x in BC_X_GRID:
            for ruck_y in RUCK_Y_GRID:
                for n_left, n_right in SPLIT_GRID:
                    out.append(measure_tes_case(
                        phase, bc_x, ruck_y, n_left, n_right, DefenceTuners()))
    return out


def _swept_tuners(halfway: float) -> DefenceTuners:
    """Tuners with the 3-point curve scaled so ``halfway`` is the mid point."""
    t = DefenceTuners()
    t.width_halfway_m = float(halfway)
    t.width_left_tryline_m = float(halfway) * (50.0 / 55.0)
    t.width_right_tryline_m = float(halfway) * (60.0 / 55.0)
    return t


def run_tuner_sweep() -> list:
    """Sweep ``width_halfway_m`` at a centred ruck to test responsiveness."""
    out = []
    for halfway in TUNER_SWEEP:
        t = _swept_tuners(halfway)
        out.append(measure_lane_case(
            "OPEN_PLAY", HALFWAY_X, 35.0, 4, 4, t,
            label=f"tuner_sweep halfway={halfway:.1f}"))
    return out


def run_governor_ab() -> list:
    """Same case with and without the governor band clamp."""
    out = []
    for with_gov in (True, False):
        out.append(measure_lane_case(
            "OPEN_PLAY", HALFWAY_X, 35.0, 4, 4, DefenceTuners(),
            label=f"governor_ab with_governor={with_gov}",
            with_governor=with_gov))
    return out


def run_carrier_ab() -> list:
    """Same case with and without a live ball carrier (band pull on/off)."""
    out = []
    for with_bc in (False, True):
        out.append(measure_lane_case(
            "OPEN_PLAY", HALFWAY_X, 35.0, 4, 4, DefenceTuners(),
            label=f"carrier_ab with_carrier={with_bc}",
            with_governor=True, with_carrier=with_bc))
    return out


def run_governed_rung_probe(tuners: DefenceTuners | None = None) -> list:
    """The headline rung: how the contract behaves across command values.

    At a centred ruck with a symmetric 4-4 line, for each commanded width in
    the sweep: the command, the governed width, the per-side widths and the
    realised span.  This is the table a reader looks at first.
    """
    tuners = tuners or DefenceTuners()
    rows = []
    for halfway in TUNER_SWEEP:
        t = _swept_tuners(halfway)
        m = measure_lane_case("OPEN_PLAY", HALFWAY_X, 35.0, 4, 4, t,
                              label=f"rung halfway={halfway:.1f}")
        rows.append(m)
    return rows


def run_all() -> dict:
    """Run every probe; return the full evidence set."""
    tuners = DefenceTuners()
    return {
        "tolerances": {
            "span_authority_tol_m": SPAN_AUTHORITY_TOL_M,
            "attrib_tol_m": ATTRIB_TOL_M,
            "room_bind_tol_m": ROOM_BIND_TOL_M,
        },
        "lane_grid": run_lane_grid(),
        "tes_grid": run_tes_grid(),
        "tuner_sweep": run_tuner_sweep(),
        "governed_rung": run_governed_rung_probe(tuners),
        "governor_ab": run_governor_ab(),
        "carrier_ab": run_carrier_ab(),
        "publish_conformance": probe_publish_conformance(tuners),
        "ruck_y_arg": probe_ruck_y_arg_ignored(tuners),
        "accessor_divergence": probe_accessor_divergence(tuners),
        "no_ball_carrier": probe_no_ball_carrier(tuners),
    }
