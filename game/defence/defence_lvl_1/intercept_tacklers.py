"""
intercept_tacklers.py — Per-side lane tackler selection & pre-tackle stance.

INTERCEPT BEHAVIOUR SPEC (2026-08-15)
=====================================
Goal: defenders must reliably converge on the ball carrier (BC), not just
hold spacing or chase vaguely.

  1. LANE-TACKLER SELECTION (per frame)
     Per side of the BC, select ONE defender as the primary converger
     ("lane tackler"):
       - LEFT  side: defenders with d.y <  bc.y, pick the one with the
                     smallest |d.x - bc.x|.
       - RIGHT side: defenders with d.y >= bc.y, pick the one with the
                     smallest |d.x - bc.x|.
     Stamped tackle_role = "PRIMARY".
     All other defenders on that side are tackle_role = "LINE" — they
     maintain spacing/shape and do NOT own the intercept.

  2. PRIMARY behaviour (intercept logic)
     BC-driven movement (implemented by the tackler-commit writer in
     defensive_behaviour_unified._apply_tackler_commit):
       - X aims slightly ahead of the BC along the defender->BC line.
       - Y aims directly at BC's Y (tight band, not spacing/lane Y).
       - Lateral movement strongly clamped so the tackler never drifts
         past / orbits the BC.

  3. CLOSE-RANGE STANCE (pre-contact, THIS module)
     For PRIMARY lane tacklers within the stance radius (e.g. < 0.8 m):
       - stop moving (no forward or lateral drift),
       - square up: facing locked directly toward the BC,
       - hold this planted stance until TES decides the outcome.

  4. TACKLE AUTHORITY (TES gating)
     Only PRIMARY (and any persisted/TES-armed SECONDARY) may start the
     tackle sequence.  LINE/PASSIVE defenders can never trigger TES even
     when close.  Enforced at the try_pre_tackle gate and the OPE contact
     loop.

  5. RETREAT-OBLIGATION VETO (2026-09-17)
     A non-marker defender that has not completed its 10 m retreat may
     not own a lane.  Lane ownership IS the tackle-authority grant
     (PRIMARY is what the engage machine and the OPE contact loop
     require), so a body still short of the anchor line is excluded from
     candidacy here.  The veto is the single shared predicate
     ``retreat_obligation.is_retreat_obligated``, never a local
     re-derivation.
"""

import math

from player.player import Player, DefenderRole
from player.movement_states import MovementState
from game.defence.defence_lvl_1.retreat_obligation import (
    is_retreat_obligated,
)


# ── EXCLUDED ROLES ──────────────────────────────────────────────────
# Markers, the dummy half, and the dynamic fullback are never lane
# tacklers: they play tight to the ruck / hold the backfield anchor.
_EXCLUDED_DEFENDER_ROLES = frozenset({
    DefenderRole.MARKER_A,
    DefenderRole.MARKER_B,
    DefenderRole.DUMMY_HALF,
    DefenderRole.KICK_CHASE,
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
})


# ── SUPPRESSION GATE ───────────────────────────────────────────────
def _lane_tacklers_suppressed(world) -> bool:
    """True when lane selection / stance must not run.

    Suppressed while TES is active or while the OPE is projecting
    OPEN_PLAY semantics during a TES drive (``_suppress_pre_tackle``):
    in those frames the TES owns who engages and the non-ruck defenders
    only run spacing/shape.
    """
    if getattr(world, "tes_active", False):
        return True
    ope = getattr(world, "open_play_engine", None)
    if ope is not None and getattr(ope, "_suppress_pre_tackle", False):
        return True
    return False


def _is_eligible_lane_defender(d, world=None, bc=None) -> bool:
    """Role/state eligibility for lane-tackler selection (no X bound)."""
    if d is None or not isinstance(d, Player):
        return False
    if getattr(d, "frozen", False) or getattr(d, "freeze_timer", 0.0) > 0.0:
        return False
    role = getattr(d, "defender_role", None)
    if role in _EXCLUDED_DEFENDER_ROLES:
        return False
    if getattr(d, "fullback_role_active", False):
        # Emergency fullbacks use a separate engage authority and never own lanes.
        return False
    if getattr(d, "tackle_state_active", False):
        return False
    if is_retreat_obligated(d, world):
        return False
    return True


def is_lane_tackler_candidate(d, bc_x: float, ahead_engage_m: float = 6.0,
                              world=None, bc=None) -> bool:
    """Return True if *d* may be selected as a lane tackler.

    Candidates are non-marker, non-DH, non-tackler, unfrozen defenders.
    Convergence is allowed from a small band AHEAD of the carrier as well
    as from behind: when the line straddles the BC (catch transition,
    straight line run), the central defenders are in front of the
    carrier's X and must still own the lane — otherwise they fall to
    PASSIVE/line-hold and stall until the BC crosses them (the "middle
    defenders stop after catch" bug).  A defender further than
    ``ahead_engage_m`` past the carrier is genuinely beaten and does not
    own the lane.

    Tagged fullbacks use their separate emergency-engage authority and
    cannot be lane candidates.
    """
    if not _is_eligible_lane_defender(d, world, bc):
        return False
    # Behind the carrier: always a candidate.  Ahead: only within the
    # bounded ahead-engagement band (spec §1 has no X-direction gating).
    return d.x <= bc_x + ahead_engage_m


def select_lane_tacklers(world, defenders, bc) -> list:
    """Assign per-side PRIMARY/LINE tackle roles around the BC.  (Spec §1)

    Per side of the BC:
      - LEFT  side: defenders with d.y <  bc.y
      - RIGHT side: defenders with d.y >= bc.y
    the candidate with the smallest |d.x - bc.x| becomes ``PRIMARY``;
    every other candidate on that side becomes ``LINE``.

    Defenders excluded from the lane system (markers / DH / tacklers /
    fullback / frozen) keep their existing tackle_role unchanged — they
    are not part of the intercept ownership.

    INCUMBENT HYSTERESIS: if a side already has a PRIMARY who is still a
    candidate and remains within ``LANE_SWITCH_HYSTERESIS_M`` of the new
    minimum-X candidate, the incumbent keeps the role.  This prevents a
    frame-to-frame PRIMARY flip when two defenders are level on X (the
    same hysteresis pattern as the lane-index / depth-role systems).

    Returns the list of PRIMARY lane tacklers (0, 1, or 2 — at most one
    per side).
    """
    primaries: list = []
    if bc is None or _lane_tacklers_suppressed(world):
        return primaries
    if not defenders:
        return primaries

    bcy = getattr(bc, "y", 0.0)
    bcx = getattr(bc, "x", 0.0)

    # ── LANE SIDE HYSTERESIS (2026-09-15) ──
    # The side split is `d.y` vs `bc.y`.  A defender whose Y sits within
    # ``lane_side_hysteresis_m`` of the carrier's lane is effectively ON the
    # lane, and a centimetre of BC Y drift flips its side — which flips the
    # per-side PRIMARY owner AND re-roles the whole side (LINE -> PASSIVE)
    # frame-to-frame.  Hold the side the defender held on the previous frame
    # unless it has clearly (beyond the hysteresis) crossed the carrier's
    # lane.  This mirrors the persistent-side law the depth-role system
    # already applies (defensive_depth_roles.SIDE_SWAP_HYSTERESIS_M) instead
    # of re-deriving the side from live position every frame.
    side_hyst = float(getattr(world, "lane_side_hysteresis_m", 0.35))

    def _resolve_side(d) -> int:
        y = getattr(d, "y", 0.0)
        prev = getattr(d, "_lane_side", None)
        if prev == -1 and y < bcy + side_hyst:
            return -1
        if prev == 1 and y >= bcy - side_hyst:
            return 1
        return -1 if y < bcy else 1

    side_of: dict = {}
    for d in defenders:
        _s = _resolve_side(d)
        side_of[id(d)] = _s
        d._lane_side = _s

    # Snapshot previous PRIMARY per side so hysteresis can keep an
    # incumbent whose X is still within the switch band.
    prev_primary_by_side = {}
    for d in defenders:
        if getattr(d, "tackle_role", None) != "PRIMARY":
            continue
        prev_primary_by_side[side_of[id(d)]] = d

    hysteresis = float(getattr(world, "lane_tackler_switch_hysteresis_m", 0.5))

    for side in (-1, 1):
        # Side pools: LEFT = left of the carrier's lane, RIGHT = the rest,
        # each resolved WITH HYSTERESIS (see _resolve_side above).
        side_defs = [
            d for d in defenders
            if side_of[id(d)] == side
        ]
        # Normal candidate pool (respects the ahead_engage_m bound).
        candidates = [
            d for d in side_defs
            if is_lane_tackler_candidate(d, bcx, world=world, bc=bc)
        ]
        if not candidates:
            # ── KICKOFF-CATCH / ALL-AHEAD FALLBACK (2026-08-30) ──
            # After a short kickoff catch the whole defensive line is
            # legitimately AHEAD of the BC by more than ahead_engage_m
            # (the kick lands at the opposite tryline; the kicking-team
            # chasers start ~30-40 m upfield and only close ~20 m during
            # the ~2.8 s flight).  Those ahead defenders ARE the natural
            # first tacklers, but the 6 m ahead-band — meant to reject a
            # genuinely-beaten defender mid-run — would starve the
            # tackle-authority entirely in this configuration (no PRIMARY
            # stamped -> tackle_role stays PASSIVE everywhere -> the
            # tackle-initiation precheck never runs -> the BC is a free
            # runner).  Fall back to the closest-X eligible defender on
            # the side so a PRIMARY is ALWAYS assigned and the precheck
            # runs.  Uses _is_eligible_lane_defender (role/state gate
            # only) so a marker/DH/fullback/tackle-state holder can never
            # be drafted into PRIMARY.
            eligible = [
                d for d in side_defs
                if _is_eligible_lane_defender(d, world, bc)
            ]
            if not eligible:
                continue
            primary = min(eligible, key=lambda d: abs(d.x - bcx))
            primary.tackle_role = "PRIMARY"
            primaries.append(primary)
            for d in eligible:
                if d is not primary:
                    d.tackle_role = "LINE"
            continue

        # Smallest |d.x - bc.x| owns the lane.
        primary = min(candidates, key=lambda d: abs(d.x - bcx))

        # Incumbent hysteresis: a previous PRIMARY on this side who is
        # still a candidate and within the switch band keeps the role.
        incumbent = prev_primary_by_side.get(side)
        if incumbent is not None and incumbent in candidates and incumbent is not primary:
            if abs(incumbent.x - bcx) <= abs(primary.x - bcx) + hysteresis:
                primary = incumbent

        # Stamp PRIMARY on the selected lane tackler; everything else on
        # this side is LINE.
        primary.tackle_role = "PRIMARY"
        primaries.append(primary)
        for d in candidates:
            if d is not primary:
                d.tackle_role = "LINE"

    return primaries


def is_primary_lane_tackler(d) -> bool:
    """True when *d* is a PRIMARY lane tackler (owns convergence + TES)."""
    return getattr(d, "tackle_role", None) == "PRIMARY"


def is_line_defender(d) -> bool:
    """True when *d* is a LINE spacing defender (holds shape, no intercept)."""
    return getattr(d, "tackle_role", None) == "LINE"


def apply_pre_tackle_stance(world, defenders, bc) -> None:
    """Plant PRIMARY lane tacklers inside the close-range stance radius.

    Spec §3 — for PRIMARY tacklers within ``pre_tackle_stance_radius_m``
    (default 0.8 m) of the BC:
      - stop moving: vx = vy = 0, targets pinned to current position,
      - movement_state = HOLD so MMDL / engine integration never drifts,
      - facing locked directly toward the BC (square up),
      - ``_mmdl_moved = True`` so the OPEN_PLAY movement loop does not
        double-integrate (the stance holder is deliberately stationary
        until TES decides the outcome),
      - ``footplanting = True`` + ``pre_tackle_stance = True`` so
        ``try_pre_tackle`` treats them as a planted, committed tackler.

    Clears the stance when the carrier exits the radius: velocity/targets
    are handed back to the commit writer and the defender resumes closing.
    """
    if bc is None or _lane_tacklers_suppressed(world):
        return
    radius = float(getattr(world, "pre_tackle_stance_radius_m", 0.8))
    # ── PRE-TACKLE SLOWDOWN BAND (2026-08-16) ──
    # PRIMARY lane tacklers decelerate inside this Euclidean band of the
    # BC so the approach tapers into the bite instead of charging at line
    # speed then snapping to a dead stop at the 0.8 m stance radius
    # (the overshoot + "no slowdown" complaint).  Published from
    # DefenceTuners by DefensiveBehaviourUnified.select_lane_tacklers();
    # getattr defaults keep a detatched caller safe.
    band = float(getattr(world, "pre_tackle_slowdown_band_m", 3.0))
    band_scale = float(getattr(world, "pre_tackle_slowdown_scale", 0.45))
    # ── COMMIT-SPEED BUDGET (2026-09-15) ──
    # ``pretackle_commit_speed_mps`` is the documented CLOSING speed for the
    # per-side COMMIT attempt body, and the band/scale above are the taper
    # OF THAT SPEED (defensive_tuners: "the commit speed tapers toward the
    # plant", "floor multiplier inside the band").  This writer is the LAST
    # ``move_speed`` authority of the OPEN_PLAY frame, so it must apply an
    # ABSOLUTE budget derived from that tuner -- NOT a multiplier on whatever
    # the upstream writer left behind.  The upstream writer is the
    # tackler-commit / pursuit writer, which stamps the unified PURSUIT
    # ceiling (``max_defensive_speed_mps`` = 8.3), so the old multiplier form
    # silently ran the taper on the wrong base and its floor became
    # 0.32 * 8.3 = 2.66 m/s instead of 0.32 * 4.0 = 1.28 m/s -- the observed
    # "approach still driven at ~8 m/s while the cap sits at 1.28 m/s".
    # Published by DefensiveBehaviourUnified.select_lane_tacklers alongside
    # the band/scale; the default matches the tuner default so a detached
    # caller (verifier stub) keeps the documented behaviour.
    commit_speed = float(getattr(
        world, "pre_tackle_slowdown_commit_speed_mps", 4.0,
    ))
    band_width = max(0.01, band - radius)
    bcy = getattr(bc, "y", 0.0)
    bcx = getattr(bc, "x", 0.0)
    # ── ANTICIPATION SLOWDOWN ANCHOR (2026-09-08) ──
    # The slowdown band is measured against the modernised anchor point
    # (predicted threat point / role-depth cell / live BC) so the defender
    # decelerates as it reaches the point the line leaned toward, instead
    # of a noise-driven raw-BC read.  The PLANT radius stays on live-BC
    # distance (a defender plants only when it actually reaches the
    # carrier); only the taper uses the anchor.
    from game.defence.defence_lvl_1.anticipation_layer import (
        anticipation_slowdown_point as _aspoint,
    )
    # ── SPEED LOCK (2026-09-09) ──
    # The slowdown anchor (bc_predicted default) is bc.vy-derived and jinks
    # frame-to-frame, so anchor_dist oscillates and the move_speed taper
    # wobbles.  EMA-smooth the anchor per defender so the taper distance is
    # stable through the lean:  anchor_dist uses the smoothed anchor, while
    # the PLANT radius stays live-BC (a defender plants only when it actually
    # reaches the carrier).
    _anchor_smooth = 0.15  # EMA alpha (low = heavy smoothing)

    # ── CORRIDOR YIELD (2026-09-15) ──
    # A corridor-owned body is written by pre_tackle_authority ALONE: the
    # corridor integrates its position and owns target_x/target_y,
    # move_speed, movement_state and the plant verdict.  This writer is the
    # LAST move_speed authority of the OPEN_PLAY frame, so leaving it active
    # on a corridor body would overwrite the governed taper exactly where it
    # matters (the slowdown band down to contact) -- the "tuning blind"
    # defect the corridor consolidation removes.  Bodies the corridor does
    # NOT own keep the legacy stance verbatim, so nothing outside the
    # corridor changes.
    from game.defence.defence_lvl_1.pretackle_corridor import (
        is_corridor_owned as _is_corridor_owned,
    )

    for d in defenders:
        if not isinstance(d, Player):
            continue
        if not is_primary_lane_tackler(d):
            continue
        if getattr(d, "frozen", False):
            continue
        if _is_corridor_owned(d):
            continue

        dist = math.hypot(d.x - bcx, d.y - bcy)
        # Anchor-aware slowdown distance (per-defender for role_depth_x).
        _anchor_x, _anchor_y = _aspoint(world, bc, tuners=None, d=d)
        # ── LIVE-BC ANCHOR: NO SMOOTHING (2026-09-15) ──
        # The EMA below exists to suppress the jink wobble of a PROJECTED
        # anchor (bc_predicted = bc + bc.v * lead, which swings with the
        # carrier's instantaneous velocity).  When the anchor resolver is
        # gated off (anticipation_enabled False) or configured "bc_live" it
        # returns (bc.x, bc.y) -- already smooth -- and the EMA becomes pure
        # LAG: at the observed ~0.27 m/frame closing rate the 0.15-alpha EMA
        # trails the carrier by ~0.75 m, so anchor_dist read ~0.75 m LONGER
        # than the true distance and the whole deceleration curve was shifted
        # inward -- the defender covered the outer 0.75 m of the band at full
        # speed.  Detect the live-BC anchor exactly and bypass the EMA, so
        # anchor_dist is the true live distance and the taper spans the
        # configured band.
        _anchor_is_live_bc = (
            abs(float(_anchor_x) - float(bcx)) <= 1e-6
            and abs(float(_anchor_y) - float(bcy)) <= 1e-6
        )
        _prev_ax = getattr(d, "_anticipation_anchor_smooth_x", None)
        _prev_ay = getattr(d, "_anticipation_anchor_smooth_y", None)
        if _anchor_is_live_bc:
            _sm_ax, _sm_ay = float(_anchor_x), float(_anchor_y)
        elif _prev_ax is None or _prev_ay is None:
            _sm_ax, _sm_ay = float(_anchor_x), float(_anchor_y)
        else:
            _sm_ax = (
                float(_prev_ax)
                + (float(_anchor_x) - float(_prev_ax)) * _anchor_smooth
            )
            _sm_ay = (
                float(_prev_ay)
                + (float(_anchor_y) - float(_prev_ay)) * _anchor_smooth
            )
        d._anticipation_anchor_smooth_x = _sm_ax
        d._anticipation_anchor_smooth_y = _sm_ay
        anchor_dist = math.hypot(d.x - _sm_ax, d.y - _sm_ay)

        if dist < radius:
            # ── ENTER / HOLD STANCE ──
            d.pre_tackle_stance = True
            d.footplanting = True
            d.vx = 0.0
            d.vy = 0.0
            d.target_x = d.x
            d.target_y = d.y
            d.move_speed = 0.0
            d.movement_state = MovementState.HOLD
            # Square up: lock facing directly toward the BC.
            d.set_facing(math.atan2(bcy - d.y, bcx - d.x))
            # The pipeline may still call Player.update(dt) for this
            # defender; the HOLD path snaps velocity to zero and the
            # pinned target keeps the position fixed.  Mark as
            # integrated so OPE's movement loop skips (no slip).
            d._mmdl_moved = True
        else:
            # ── EXIT STANCE ──
            if getattr(d, "pre_tackle_stance", False):
                d.pre_tackle_stance = False
                d.footplanting = False
                # Facing override is owned by the square-up; hand back to
                # the normal facing pipeline on exit.
                d.facing_override = False
                d._mmdl_moved = False

            # ── PRE-TACKLE SLOWDOWN BAND (else branch = not planted) ──
            # Within `band` metres of the ANCHOR (BC-live / BC-predicted /
            # role-depth cell) the committed tackler's speed tapers
            # linearly from full commit speed (at the band edge) down to
            # `band_scale * speed` at the stance radius.  The commit
            # writer above wrote full CHASE speed; this scaling runs
            # AFTER it, so it wins without touching the tackle_target
            # (the defender still converges on the collision point).
            # The modernised anchor (default bc_predicted) decelerates
            # the defender as it reaches the point the line leaned
            # toward — the "get to BC fast, then decelerate for tackle"
            # behaviour — instead of a raw-BC read that fires on noise.
            # ── ABSOLUTE COMMIT-SPEED BUDGET (2026-09-15) ──
            # The committed tackler's CLOSING speed is the tuner
            # ``pretackle_commit_speed_mps`` (4.0 m/s), tapering linearly to
            # ``band_scale`` of it (1.28 m/s) at the stance radius.  Applied
            # as an absolute budget with min() so it can only ever REDUCE
            # speed (a HOLD / pinned body stays at 0.0), and so the upstream
            # writer's value -- the unified PURSUIT ceiling, 8.3 m/s -- can no
            # longer define the taper's base and floor.
            if commit_speed > 0.0:
                if anchor_dist < band:
                    t = (anchor_dist - radius) / band_width
                    t = max(0.0, min(1.0, t))
                    budget = commit_speed * (
                        band_scale + (1.0 - band_scale) * t
                    )
                else:
                    budget = commit_speed
                d.move_speed = min(d.move_speed, budget)
