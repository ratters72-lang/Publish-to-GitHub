"""
defensive_decisions.py  Decision Layer for defender behaviour.

Provides SHOOT/HOLD/SLIDE/COMPRESS/DRIFT arbitration for each defender
each frame.  The decision arbiter selects the highest-priority action
based on context (distance to BC, depth_class, timing flags, etc.).

Each decision produces a (target_x, target_y, speed, movement_state, facing) 
tuple consumed by the movement system.

Decision priority (highest first):
  1. SHOOT    BC within tackle range, defender commits
  2. CHASE    BC moving away laterally, defender follows
  3. COMPRESS  BC approaching the line, defender tightens Y
  4. SLIDE    BC in front of defender, lateral coverage needed
  5. DRIFT    BC deep, defender maintains depth while shifting Y
  6. RETREAT  BC behind defender, backpedal to anchor
  7. HOLD     No threat, maintain position
"""

import math
from enum import Enum, auto
from player.movement_states import MovementState


class Decision(Enum):
    """Defender behaviour decisions."""
    SHOOT = auto()     # Explosive tackle commit
    CHASE = auto()     # Fast pursuit of BC
    COMPRESS = auto()  # Tighten Y toward FR lane
    SLIDE = auto()     # Lateral coverage, maintain X
    DRIFT = auto()     # Slow lateral, maintain depth
    RETREAT = auto()   # Backpedal to anchor
    HOLD = auto()      # Maintain position


#  Mapping from Decision to MovementState 

DECISION_TO_MOVEMENT = {
    Decision.SHOOT: MovementState.SHOOT,
    Decision.CHASE: MovementState.CHASE,
    Decision.COMPRESS: MovementState.DRIFT,
    Decision.SLIDE: MovementState.SLIDE,
    Decision.DRIFT: MovementState.DRIFT,
    Decision.RETREAT: MovementState.RETREAT,
    Decision.HOLD: MovementState.HOLD,
}


#  Arbitration thresholds 

SHOOT_DISTANCE_M = 3.0        # Max distance for SHOOT decision
SHOOT_ANGLE_DEG = 45.0        # Max angle (from defender->BC line) for SHOOT
SHOOT_MIN_SPEED_MPS = 2.0     # Minimum defender speed to commit to SHOOT

CHASE_LATERAL_THRESHOLD_M = 5.0   # BC lateral movement to trigger CHASE
CHASE_DEPTH_THRESHOLD_M = 8.0     # BC depth movement to trigger CHASE

COMPRESS_DISTANCE_M = 12.0        # BC within this X distance triggers COMPRESS
SLIDE_DISTANCE_M = 15.0           # BC within this Y distance triggers SLIDE
DRIFT_DISTANCE_M = 25.0           # BC beyond this triggers DRIFT

RETREAT_COMPLIANCE_M = 10.0       # Must be this far behind ruck to exit RETREAT


def compute_intercept_target(defender, bc, def_speed=None,
                             t_min=0.05, t_max=1.5):
    """
    Aim defender at the point the BC will occupy when the defender gets there.

    Solves |r0 + v_bc*t| = v_def*t for t (quadratic), where
    r0 = vector defender->BC and v_bc = BC velocity.  This incorporates
    BOTH speeds, so a fast BC produces a longer lead and a slow BC a
    shorter one.  Falls back to a distance-scaled lead when the BC is
    outrunning the defender (no positive root).
    Returns (target_x, target_y).
    """
    if bc is None:
        return defender.x, defender.y

    if def_speed is None or def_speed <= 0:
        def_speed = (getattr(defender, "move_speed", 0.0)
                     or getattr(defender, "max_speed_mps", 8.0))

    r0 = (bc.x - defender.x, bc.y - defender.y)
    v = (getattr(bc, "vx", 0.0), getattr(bc, "vy", 0.0))
    dist = math.hypot(*r0)
    bc_speed_sq = v[0] ** 2 + v[1] ** 2

    t = None
    if dist >= 0.01 and bc_speed_sq >= 0.01:
        a = bc_speed_sq - def_speed ** 2
        bq = 2.0 * (r0[0] * v[0] + r0[1] * v[1])
        c = dist ** 2
        if abs(a) < 1e-6:
            if bq < 0:
                t = -c / bq
        else:
            disc = bq * bq - 4.0 * a * c
            if disc >= 0:
                root = math.sqrt(disc)
                pos = [x for x in ((-bq - root) / (2 * a),
                                   (-bq + root) / (2 * a)) if x > 0]
                if pos:
                    t = min(pos)

    if t is None:  # BC outrunning defender -> distance-scaled lead
        t = min(t_max, max(t_min, dist / max(def_speed, 0.01)))

    t = max(t_min, min(t_max, t))
    return bc.x + v[0] * t, bc.y + v[1] * t


def _dist_to_bc(defender, bc) -> float:
    """Euclidean distance from defender to BC."""
    if bc is None:
        return 9999.0
    return math.hypot(defender.x - bc.x, defender.y - bc.y)


def _angle_to_bc(defender, bc) -> float:
    """Angle (degrees) between defender's facing and direction to BC."""
    if bc is None:
        return 180.0
    dx = bc.x - defender.x
    dy = bc.y - defender.y
    dist = math.hypot(dx, dy)
    if dist < 0.01:
        return 0.0
    # Angle from facing direction to BC
    to_bc = math.atan2(dy, dx)
    diff = defender.facing - to_bc
    return abs(math.degrees(diff))


def _lateral_distance(defender, bc) -> float:
    """Absolute Y distance between defender and BC."""
    if bc is None:
        return 0.0
    return abs(defender.y - bc.y)


def _depth_distance(defender, bc) -> float:
    """X distance (defender behind BC = positive)."""
    if bc is None:
        return 0.0
    return bc.x - defender.x


# 
# DECISION ARBITER  single entry point
# 

def decide(
    defender,
    bc,
    ruck_x: float,
    anchor_x: float,
    depth_class: str = "PASSIVE",
    is_marker: bool = False,
    timing_locked: bool = False,
    freeze_timer: float = 0.0,
    retreat_compliant: bool = False,
) -> Decision:
    """Return the highest-priority Decision for *defender* this frame.

    Parameters
    ----------
    defender : Player
        The defender making the decision.
    bc : Player or None
        The ball carrier (None during kicks/loose ball).
    ruck_x : float
        Current ruck X position.
    anchor_x : float
        Current defensive line anchor X position.
    depth_class : str
        Depth classification ("PRIMARY", "SECONDARY", "PASSIVE", "MARKER").
    is_marker : bool
        True if the defender is a marker (MARKER_A/B).
    timing_locked : bool
        True if the timing system has locked this defender.
    freeze_timer : float
        Remaining freeze time (s).  >0 means defender is frozen.
    retreat_compliant : bool
        True if defender is past the 10m retreat compliance line.

    Returns
    -------
    Decision
    """
    #  Gate: frozen or timing-locked defenders always HOLD 
    if freeze_timer > 0.0 or timing_locked:
        return Decision.HOLD

    #  Gate: markers always HOLD (they stay at ruck) 
    if is_marker:
        return Decision.HOLD

    #  No BC  HOLD 
    if bc is None:
        return Decision.HOLD

    #  LINE BREAK GATE: BC past anchor + 5m  non-marker defenders always CHASE 
    # When the ball carrier has advanced well past the defensive line anchor,
    # ALL non-marker defenders must pursue regardless of their depth_class.
    # Without this gate, PASSIVE compliant defenders fall through to HOLD/DRIFT
    # (static anchor_x) and never receive velocity-producing targets.
    if anchor_x is not None and bc.x > anchor_x + 5.0 and not is_marker and freeze_timer <= 0 and not timing_locked:
        return Decision.CHASE

    dist = _dist_to_bc(defender, bc)
    angle = _angle_to_bc(defender, bc)
    lat_dist = _lateral_distance(defender, bc)
    depth = _depth_distance(defender, bc)

    #  1. SHOOT (highest priority) 
    # BC is close, angle is good, defender is moving fast enough,
    # and defender is not a passive deep defender
    if (dist <= SHOOT_DISTANCE_M
            and angle <= SHOOT_ANGLE_DEG
            and depth_class in ("PRIMARY", "SECONDARY")):
        # Check defender speed
        speed = math.hypot(defender.vx, defender.vy)
        if speed >= SHOOT_MIN_SPEED_MPS or depth < 2.0:
            return Decision.SHOOT

    #  2. CHASE 
    # BC is moving laterally or forward within chase range
    if (depth_class in ("PRIMARY", "SECONDARY")
            and dist <= CHASE_DEPTH_THRESHOLD_M * 2):
        if lat_dist <= CHASE_LATERAL_THRESHOLD_M * 1.5:
            return Decision.CHASE

    #  3. COMPRESS 
    # BC is approaching the line within compress distance
    if (depth <= COMPRESS_DISTANCE_M
            and depth > 0
            and depth_class != "PASSIVE"):
        return Decision.COMPRESS

    #  4. SLIDE 
    # BC is within lateral slide distance, defender needs to cover
    if lat_dist <= SLIDE_DISTANCE_M and depth > 2.0:
        return Decision.SLIDE

    #  5. RETREAT 
    # Defender is not yet compliant (within 10m of ruck) or BC is
    # behind the defender
    if not retreat_compliant or depth < -2.0:
        return Decision.RETREAT

    #  6. DRIFT 
    # BC is far, defender maintains depth while slowly adjusting Y
    if dist > DRIFT_DISTANCE_M:
        return Decision.DRIFT

    #  7. HOLD (lowest priority) 
    return Decision.HOLD


def get_targets_for_decision(
    decision: Decision,
    defender,
    bc,
    anchor_x: float,
    ruck_x: float,
    target_x_structured: float,
    target_y_structured: float,
) -> tuple[float, float, float]:
    """
    Given a Decision, return (target_x, target_y, speed_scale).

    This converts the abstract decision into concrete movement targets.
    The structured defense (governor/spacing) provides baseline targets;
    the decision may override them.

    Parameters
    ----------
    decision : Decision
        The selected decision.
    defender : Player
        The defender.
    bc : Player or None
        The ball carrier.
    anchor_x : float
        Defensive line anchor.
    ruck_x : float
        Current ruck X.
    target_x_structured : float
        Baseline target X from structured defence (governor/spacing).
    target_y_structured : float
        Baseline target Y from structured defence.

    Returns
    -------
    (target_x, target_y, speed_scale)
        speed_scale is a multiplier on max_speed_mps.
    """
    if decision == Decision.SHOOT:
        # Shoot directly at BC
        if bc is not None:
            return (bc.x + 1.0, bc.y, 1.0)
        return (target_x_structured, target_y_structured, 1.0)

    if decision == Decision.CHASE:
        # Chase toward BC's anticipated intercept point.  The
        # time-to-intercept solve incorporates both BC speed and
        # defender speed, so the defender curves ahead onto the BC's
        # trajectory instead of converging on its current position.
        if bc is not None:
            tx, ty = compute_intercept_target(defender, bc)
            return (tx, ty, 0.9)
        return (target_x_structured, target_y_structured, 0.9)

    if decision == Decision.COMPRESS:
        # Tighten Y toward BC, maintain depth at structured X
        # AI_ENGINE_FIX: Reduced compression from 0.4→0.2 so the
        # defensive line width is not aggressively collapsed
        # toward the BC's Y position.
        if bc is not None:
            compressed_y = defender.y + (bc.y - defender.y) * 0.2
            return (target_x_structured, compressed_y, 0.7)
        return (target_x_structured, target_y_structured, 0.7)

    if decision == Decision.SLIDE:
        # Lateral only  maintain X, slide Y toward structured Y
        return (defender.x, target_y_structured, 0.6)

    if decision == Decision.DRIFT:
        # Slow adjustment toward structured targets
        return (target_x_structured, target_y_structured, 0.4)

    if decision == Decision.RETREAT:
        # Backpedal to anchor at structured Y.
        # TASK I-B (2026-08-11): the RETREAT writer is a PURE CONSUMER of the
        # structured line target (spacing Y + width/anchor X).  It must NEVER
        # pull the defender FORWARD toward the anchor when the structured
        # target is already deeper (e.g. a deep PASSIVE role target, or a
        # retreat line past the anchor) — that produced the retreat↔close
        # oscillation and the "rejected due to anchor distance" behaviour.
        # The structured X target is kept when it is at/behind the anchor;
        # only a genuinely OVER-compliant forward target is clamped back to
        # the anchor line.
        return (max(target_x_structured, anchor_x), target_y_structured, 0.7)

    # HOLD  maintain current position
    return (defender.x, defender.y, 0.0)


def select_movement_state(decision: Decision) -> MovementState:
    """Map Decision to MovementState."""
    return DECISION_TO_MOVEMENT.get(decision, MovementState.HOLD)
