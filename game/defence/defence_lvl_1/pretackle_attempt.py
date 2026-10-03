"""
pretackle_attempt.py — PRE-TACKLE ATTEMPT AUTHORITY (Task XIV, 2026-08-26).

PURPOSE
-------
During ``TACKLE_DRIVE`` / ``TACKLE_DOMINANCE`` the DEF_LINE_READY bodies of
the defensive line must STOP chasing the ball carrier.  The legacy path
(``DefenceManager.update_tes_drive`` Module 2 — "BC-SHADOW ROLE-DEPTH
RE-ANCHOR") re-points every non-tackler line body at the LIVE carrier:

    target_x = bc.x + WIDTH_OFFSETS[depth_role]
    target_source = "tes_drive_shadow"

followed by a soft-follow integrator (deadband + EMA + feed-forward).
Because the whole line tracks a moving / driven / clamped carrier, that
legacy contract produces three visible failure modes:

  - PROXIMITY-BUCKET COLLAPSE — every shadow target is bc.x-derived, so as
    the drive advances the whole line converges into one moving bucket;
  - VX-ZERO JIGGLE — the deadband rest zeroes vx while the feed-forward /
    EMA pair toggles it against a moving anchor (limit-cycle drift);
  - B-ON-A STACKING — two line bodies chase the same ``bc.x + offset``
    point and stack on top of each other.

WHAT THIS MODULE DOES (gated, dark-safe)
----------------------------------------
When ``DefenceTuners.pretackle_attempt_enabled`` is True, this authority
OWNS the non-tackler DEF_LINE_READY bodies for the whole drive:

  1. STABLE TACKLE-PHASE ANCHORS — at DRIVE entry each managed body's
     anchor is FROZEN once from its pre-drive open-play geometry
     (``target_x/target_y`` after the projected pipeline ran: role depth +
     spacing).  Anchors are NEVER recomputed and NEVER read live ``bc.x``,
     so the line is a coherent band that does not track the carrier.
  2. DETERMINISTIC ATTEMPT STATES — each managed body carries exactly one
     state, assigned once per drive:
         COMMIT   — the single designated attempt body PER SIDE of the BC
                    (smallest |x − commit_x| at seed; incumbent keeps it);
                    closes toward a FROZEN commit point (BC position at
                    drive entry plus a fixed forward offset), then plants.
         PLANTED  — arrived within plant radius: one-way freeze + square-up.
         SLOW     — converge to its frozen anchor at capped slow speed.
         HOLD     — arrived within freeze radius: one-way freeze.
  3. FREEZE/SLOW CONTRACTS — freeze is ONE-WAY inside a drive (a planted /
     holding body never re-arms until the drive-end reset), which kills the
     VX-zero jiggle by construction; the slow contract caps speed well
     below the unified ceiling with proportional approach + EMA smoothing.
  4. ANTI-COLLAPSE / ANTI-STACK — anchors are keyed by frozen role geometry
     so two bodies never converge onto one point; commit ownership is
     exclusive per side, so no second body ever chases the first's point.

CONTRACTS & ISOLATION
---------------------
  - Writes ONLY ``target_x/target_y/vx/vy/_mmdl_moved/movement_state`` (+
    facing square-up on plant) for bodies it manages, stamped
    ``target_source = "pretackle_attempt"``.  It never touches TES attach /
    detach geometry, drive LERP, dominance pushback, ball-carrier movement,
    collision physics, or any phase transition — TES retains full tackle
    ownership (engine-law boundary preserved).
  - NEVER mutates ``defender_role`` or ``depth_role`` — roles stay stable
    through the contact sequence.
  - Determinism: identical inputs -> identical anchors, states, owners,
    velocities, positions.  All selection uses min() with player_id
    tiebreaks over caller-provided list order; all targets are frozen
    values captured at seed time.
  - Reset law: state lives only inside a single drive.  Cleared when the
    phase leaves DRIVE/DOMINANCE (DefenceManager exit hook), at kickoff
    (OpenPlayEngine.reset_for_kickoff), and defensively whenever a managed
    body stops being eligible (became a TES participant / lost its role).

This module imports nothing from the engine or other defence modules
(import-cycle law); consumers import the predicates from here.
"""

from __future__ import annotations

import math

from player.movement_states import MovementState

# ── TARGET-SOURCE STAMP ──────────────────────────────────────────────
PTA_TARGET_SOURCE: str = "pretackle_attempt"

# ── ATTEMPT STATES ───────────────────────────────────────────────────
PTA_COMMIT: str = "COMMIT"
PTA_PLANTED: str = "PLANTED"
PTA_SLOW: str = "SLOW"
PTA_HOLD: str = "HOLD"

# ── PER-DEFENDER ATTRIBUTE NAMES ─────────────────────────────────────
STATE_ATTR: str = "_pretackle_attempt_state"
ANCHOR_X_ATTR: str = "_pta_anchor_x"
ANCHOR_Y_ATTR: str = "_pta_anchor_y"
COMMIT_X_ATTR: str = "_pta_commit_x"
COMMIT_Y_ATTR: str = "_pta_commit_y"
OWNER_SIDE_ATTR: str = "_pta_owner_side"

# World key holding {side: player_id} for commit-owner incumbency.
OWNERS_KEY: str = "pretackle_attempt_owners"

# Env gate for the per-frame audit trace (default off — quiet logs).
_TRACE_ENV: str = "RLM_PTA_TRACE"


def _trace_on() -> bool:
    import os
    return os.environ.get(_TRACE_ENV, "") not in ("", "0")


# ─────────────────────────────────────────────────────────────────────
# GATE + ELIGIBILITY PREDICATES
# ─────────────────────────────────────────────────────────────────────

def pretackle_attempt_enabled(tuners) -> bool:
    """True when the Task XIV dark-safe master gate is ON."""
    if tuners is None:
        return False
    return bool(getattr(tuners, "pretackle_attempt_enabled", False))


def is_pretackle_eligible(d) -> bool:
    """True when *d* may be owned by the pre-tackle attempt authority.

    Scope is STRICTLY non-tackler line bodies:
      - defender_role == DEF_LINE_READY (the task's exact scope), and
      - unfrozen, and
      - not the dynamic fullback actor (geometry-driven only).

    Markers / DH / tacklers are excluded by the role check itself; the
    caller additionally excludes TES participants before invoking apply().
    """
    if d is None:
        return False
    if bool(getattr(d, "frozen", False)):
        return False
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        return False
    if bool(getattr(d, "fullback_role_active", False)):
        return False
    from player.player import DefenderRole
    return getattr(d, "defender_role", None) is DefenderRole.DEF_LINE_READY


def assign_def_line_ready(d) -> None:
    """Promote *d* to ``DEF_LINE_READY`` if it is an eligible line body.

    The defensive line must be ``DEF_LINE_READY`` BEFORE a tackle cycle
    enters ``TACKLE_INIT`` — otherwise a body carrying a stale
    ``DEF_RETREAT_10`` / ``DEF_CHASE`` / ``None`` role reaches the drive
    without the role, ``is_pretackle_eligible`` returns False, and the
    pretackle-attempt authority never owns it.

    Idempotent.  Promotes ``None`` / ``DEF_CHASE`` to ``DEF_LINE_READY``.
    DELIBERATELY DOES NOT PROMOTE ``DEF_RETREAT_10`` (2026-09-18): that role
    IS the engine's retreat-completion signal (``_update_post_ruck_open_play``
    flips it to ``DEF_LINE_READY`` once ``d.x >= target_x``), and this
    function runs on EVERY defender EVERY frame BEFORE the authority
    writers, so promoting it here destroyed the signal before any retreat
    veto could observe it.  NEVER touches ``MARKER_A`` / ``MARKER_B`` /
    ``DUMMY_HALF`` / ``TACKLER_*`` (TES-owned), the dynamic fullback
    actor, or a frozen body.  The only write is the role stamp; it is a
    pure role-promotion, not a movement writer.
    """
    if d is None:
        return
    if bool(getattr(d, "fullback_role_active", False)):
        return
    if bool(getattr(d, "frozen", False)):
        return
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        return
    from player.player import DefenderRole
    role = getattr(d, "defender_role", None)
    if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B,
                DefenderRole.DUMMY_HALF, DefenderRole.TACKLER_1,
                DefenderRole.TACKLER_2, DefenderRole.TACKLER_3):
        return
    # THE RETREAT ROLE SURVIVES (2026-09-18).  ``DEF_RETREAT_10`` is the
    # engine's own "10 m retreat not yet complete" state; the sole writer of
    # its completion is the engine's arrival test (``d.x >= target_x``).
    if role is DefenderRole.DEF_RETREAT_10:
        return
    if role is not DefenderRole.DEF_LINE_READY:
        d.defender_role = DefenderRole.DEF_LINE_READY


def is_pretackle_managed(d) -> bool:
    """True when *d* carries a live pre-tackle attempt state."""
    return getattr(d, STATE_ATTR, None) is not None


def clear_pretackle_state(d) -> None:
    """Drop every attempt stamp from *d* (drive exit / eligibility loss)."""
    if d is None:
        return
    for attr in (STATE_ATTR, ANCHOR_X_ATTR, ANCHOR_Y_ATTR,
                 COMMIT_X_ATTR, COMMIT_Y_ATTR, OWNER_SIDE_ATTR):
        try:
            delattr(d, attr)
        except AttributeError:
            pass


def reset_pretackle_attempts(world) -> None:
    """Clear ALL attempt state (kickoff reset / fresh sequence).

    Sweeps the surviving world players so no stale flag can leak into a
    new drive or a new game (mirrors tackle_state_gate.reset pattern).
    """
    seen = set()
    for p in (getattr(world, "players", None) or []):
        pid = id(p)
        if pid in seen:
            continue
        seen.add(pid)
        clear_pretackle_state(p)
    owners = getattr(world, OWNERS_KEY, None)
    if owners is not None:
        owners.clear()


# ─────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────

def _finite(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(float(v))


def _freeze_stance(d, face_x=None, face_y=None) -> None:
    """One-way freeze: zero velocity, pinned targets, MMDL-skip stamp.

    A frozen body stays frozen every frame until the drive-end reset —
    there is NO re-arm path inside a drive, which kills VX-zero jiggle
    by construction.  Optional square-up faces (face_x, face_y).
    """
    d.vx = 0.0
    d.vy = 0.0
    d.target_x = d.x
    d.target_y = d.y
    d.move_speed = 0.0
    d._mmdl_moved = True
    d.target_source = PTA_TARGET_SOURCE
    d.movement_state = MovementState.HOLD
    if face_x is not None and face_y is not None:
        dx = float(face_x) - float(d.x)
        dy = float(face_y) - float(d.y)
        if math.hypot(dx, dy) > 1e-6:
            d.set_facing(math.atan2(dy, dx))


def _slow_step(d, tx: float, ty: float, dt: float, cap_mps: float,
               smooth: float) -> None:
    """Slow-contract step toward (tx, ty): proportional + cap + EMA.

    Direct integration (the authority is the SINGLE integrator for its
    bodies); ``_mmdl_moved`` is stamped so OPE's movement loop skips the
    player this frame.
    """
    dx = tx - float(d.x)
    dy = ty - float(d.y)
    dist = max(1e-6, math.hypot(dx, dy))
    nx, ny = dx / dist, dy / dist
    # Proportional approach clamped to the slow cap: large errors run at
    # the cap, small errors taper proportionally (no overshoot).
    speed = min(cap_mps, dist * 3.0)
    vx_c = nx * speed
    vy_c = ny * speed
    prev_vx = float(getattr(d, "_pta_vx", 0.0))
    prev_vy = float(getattr(d, "_pta_vy", 0.0))
    d.vx = prev_vx + (vx_c - prev_vx) * smooth
    d.vy = prev_vy + (vy_c - prev_vy) * smooth
    d._pta_vx = d.vx
    d._pta_vy = d.vy
    d.x += d.vx * dt
    d.y += d.vy * dt
    d.target_x = tx
    d.target_y = ty
    d.move_speed = math.hypot(d.vx, d.vy)
    d._mmdl_moved = True
    d.target_source = PTA_TARGET_SOURCE


# ─────────────────────────────────────────────────────────────────────
# SEEDING (once per drive) + OWNER RECONCILIATION
# ─────────────────────────────────────────────────────────────────────

def _seed_body(world, d, bc, tuners) -> None:
    """Freeze the stable tackle-phase anchors for one body (once/drive).

    Anchor  = the body's PRE-DRIVE open-play geometry target (role depth +
              spacing) — read from target_x/target_y AFTER the projected
              pipeline ran, falling back to live position when invalid.
    Commit  = the FROZEN BC-forward contact point at drive entry:
              (bc.x + commit_depth_offset, bc.y).  Attack runs +X, so the
              commit point sits slightly ahead of where the carrier was
              when the drive began — a fixed coordinate, never live bc.x.
    """
    tx = getattr(d, "target_x", None)
    ty = getattr(d, "target_y", None)
    if _finite(tx):
        ax = float(tx)
    else:
        ax = float(d.x)
    if _finite(ty):
        ay = float(ty)
    else:
        ay = float(d.y)

    offset = float(getattr(tuners, "pretackle_commit_depth_offset_m", 1.0))
    lateral = float(getattr(tuners, "pretackle_commit_lateral_offset_m", 1.0))
    # Side-offset the commit point laterally so the two per-side COMMIT
    # bodies (left / right of the BC) never converge onto ONE point — the
    # anti-stack guarantee.  Side law matches _reconcile_owners exactly.
    side = -1 if float(d.y) < float(bc.y) else 1
    cx = float(bc.x) + offset
    cy = float(bc.y) + lateral * side

    setattr(d, ANCHOR_X_ATTR, ax)
    setattr(d, ANCHOR_Y_ATTR, ay)
    setattr(d, COMMIT_X_ATTR, cx)
    setattr(d, COMMIT_Y_ATTR, cy)


def _reconcile_owners(world, managed, bc, tuners) -> dict:
    """Ensure <=1 COMMIT owner per side of the BC (deterministic).

    Side law matches the lane-tackler convention: LEFT = d.y < bc.y,
    RIGHT = d.y >= bc.y.  Owner selection: smallest |x - commit_x| with
    an explicit player_id tiebreak (never list-order dependent).
    Incumbent law: a live eligible owner KEEPS its side for the whole
    drive (hysteresis by construction — selection happens once); a new
    owner is chosen ONLY when the side has none (first frame or the
    previous owner left eligibility).  Mid-drive newcomers can only be
    seeded SLOW/HOLD — they never steal an existing owner.
    """
    owners = getattr(world, OWNERS_KEY, None)
    if owners is None:
        owners = {}
        setattr(world, OWNERS_KEY, owners)

    by_side = {-1: [], 1: []}
    for d in managed:
        bcy = float(bc.y)
        side = -1 if float(d.y) < bcy else 1
        by_side[side].append(d)

    result = {}
    for side in (-1, 1):
        pool = by_side[side]
        if not pool:
            continue

        def _rank(dd):
            cx = float(getattr(dd, COMMIT_X_ATTR))
            return (abs(float(dd.x) - cx), int(getattr(dd, "player_id", 0)))

        current = None
        for dd in pool:
            if getattr(dd, STATE_ATTR, None) == PTA_COMMIT:
                current = dd
                break
            if getattr(dd, STATE_ATTR, None) == PTA_PLANTED:
                current = dd
                break

        if current is None:
            current = min(pool, key=_rank)
            setattr(current, STATE_ATTR, PTA_COMMIT)
            setattr(current, OWNER_SIDE_ATTR, side)

        # Everyone else on this side starts as SLOW (converge to anchor),
        # unless already terminal-frozen (PLANTED cannot happen without
        # having been COMMIT first).
        for dd in pool:
            st = getattr(dd, STATE_ATTR, None)
            if dd is current:
                continue
            if st is None:
                setattr(dd, STATE_ATTR, PTA_SLOW)

        result[side] = current
    return result


# ─────────────────────────────────────────────────────────────────────
# PER-FRAME APPLICATION (sole writer for managed bodies during a drive)
# ─────────────────────────────────────────────────────────────────────

def apply_pretackle_attempts(world, defenders, dt: float, tuners) -> list:
    """Own the DEF_LINE_READY bodies for this drive frame.

    Called from DefenceManager.update_tes_drive() AFTER the projected
    open-play pipeline + fullback geometry, INSTEAD OF the legacy
    tes_drive_shadow re-anchor for the bodies it returns.

    Per frame:
      - drop managed bodies that stopped being eligible (became a TES
        participant / lost DEF_LINE_READY) — their state clears so the
        legacy systems own them again immediately;
      - seed unmanaged eligible bodies ONCE (frozen anchors + commit);
      - reconcile per-side COMMIT owners deterministically;
      - advance every managed body through its attempt-state machine and
        integrate it DIRECTLY (single-integrator law via _mmdl_moved).

    Returns the list of bodies managed THIS frame.
    """
    if not pretackle_attempt_enabled(tuners):
        return []
    bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return []

    managed: list = []

    # ── Pass 0: drop ineligible holders (eligibility-loss reset law) ──
    for d in defenders:
        if is_pretackle_managed(d) and not is_pretackle_eligible(d):
            clear_pretackle_state(d)

    # ── Pass 1: collect eligible bodies ──
    eligible = [d for d in defenders if is_pretackle_eligible(d)]
    if not eligible:
        return managed

    # ── Pass 2: seed new bodies (frozen anchors, once per drive) ──
    for d in eligible:
        if not is_pretackle_managed(d):
            _seed_body(world, d, bc, tuners)

    # ── Pass 3: reconcile COMMIT owners (<=1 per side, deterministic) ──
    owners = _reconcile_owners(world, eligible, bc, tuners)

    # ── Tuner reads ──
    hold_cap = float(getattr(tuners, "pretackle_slow_speed_mps", 2.5))
    freeze_r = float(getattr(tuners, "pretackle_freeze_radius_m", 0.15))
    smooth = float(getattr(tuners, "pretackle_follow_smooth", 0.6))
    commit_cap = float(getattr(tuners, "pretackle_commit_speed_mps", 4.0))
    plant_r = float(getattr(tuners, "pretackle_plant_radius_m", 0.8))
    slowdown_band = float(getattr(tuners, "pretackle_slowdown_band_m", 3.0))
    slowdown_scale = float(getattr(tuners, "pretackle_slowdown_scale", 0.45))
    band_width = max(0.01, slowdown_band - plant_r)

    frame = int(getattr(world, "frame_count", 0))

    # ── Pass 4: advance the state machine + integrate ──
    for d in eligible:
        st = getattr(d, STATE_ATTR, None)
        ax = float(getattr(d, ANCHOR_X_ATTR))
        ay = float(getattr(d, ANCHOR_Y_ATTR))

        if st in (PTA_COMMIT, PTA_PLANTED):
            cx = float(getattr(d, COMMIT_X_ATTR))
            cy = float(getattr(d, COMMIT_Y_ATTR))
            dist = math.hypot(cx - float(d.x), cy - float(d.y))

            if st == PTA_PLANTED:
                # One-way freeze: planted bodies NEVER re-arm mid-drive.
                _freeze_stance(d, face_x=cx, face_y=cy)
                managed.append(d)
                if _trace_on():
                    print(f"[PTA] f={frame} id={getattr(d, 'player_id', '?')} "
                          f"PLANTED hold @({float(d.x):.2f},{float(d.y):.2f})")
                continue

            if dist < plant_r:
                # Arrived at the frozen commit point: PLANT.
                setattr(d, STATE_ATTR, PTA_PLANTED)
                _freeze_stance(d, face_x=cx, face_y=cy)
                managed.append(d)
                if _trace_on():
                    print(f"[PTA] f={frame} id={getattr(d, 'player_id', '?')} "
                          f"{PTA_COMMIT}->{PTA_PLANTED} d={dist:.2f}")
                continue

            # Closing on the FROZEN commit point (never live bc.x).
            # Slowdown band tapers the final metres into the bite.
            if dist < slowdown_band:
                t = (dist - plant_r) / band_width
                t = max(0.0, min(1.0, t))
                cap = commit_cap * (slowdown_scale + (1.0 - slowdown_scale) * t)
            else:
                cap = commit_cap
            _slow_step(d, cx, cy, dt, cap, smooth)
            d.movement_state = MovementState.CHASE
            managed.append(d)
            if _trace_on():
                print(f"[PTA] f={frame} id={getattr(d, 'player_id', '?')} "
                      f"COMMIT closing d={dist:.2f} cap={cap:.2f}")
            continue

        # ── HOLD / SLOW : the frozen-anchor contract ──
        err = math.hypot(ax - float(d.x), ay - float(d.y))

        if st == PTA_HOLD:
            # One-way freeze: holding bodies NEVER re-arm mid-drive.
            _freeze_stance(d)
            managed.append(d)
            continue

        if err < freeze_r:
            # Arrived at the stable anchor: freeze (one-way).
            setattr(d, STATE_ATTR, PTA_HOLD)
            _freeze_stance(d)
            managed.append(d)
            if _trace_on():
                print(f"[PTA] f={frame} id={getattr(d, 'player_id', '?')} "
                      f"SLOW->{PTA_HOLD} err={err:.3f}")
            continue

        _slow_step(d, ax, ay, dt, hold_cap, smooth)
        d.movement_state = MovementState.DRIFT
        managed.append(d)
        if _trace_on():
            print(f"[PTA] f={frame} id={getattr(d, 'player_id', '?')} "
                  f"{PTA_SLOW} err={err:.2f}")

    return managed
