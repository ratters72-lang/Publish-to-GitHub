"""bc_steering_authority.py — BC steering stabilisation authority (Task XIII).

The ball carrier's run vector was the last discontinuous signal in the
attack chain.  Four discrete flip sources fed ``RunLogic.compute_run_vector``
every frame:

  1. DEFENDER-CONE FLIP — ``AttackGeometry.defender_cone`` selects the
     nearest defender with a strict ``<`` comparison, so near-equidistant
     defenders swapped "nearest" frame-to-frame and the avoidance angle
     jumped.
  2. LANE-SIGN FLIP — ``lane_geometry`` picks the drift direction from
     ``sign(bc.y - 35)``; a carrier straddling midfield flipped the drift
     vector instantly.
  3. GAP-PAIR CHURN — ``gap_geometry`` builds the gap from the first two
     defenders sorted by x; any order swap teleported the gap centre.
  4. ESCAPE-EXIT SNAP — the post-PTB escape blend (92% forced / 8%
     geometry) cut to pure geometry in ONE frame when the escape window
     expired.

This module is the SINGLE WRITER for stabilised steering inputs.  It wraps
the raw geometry dict produced by ``AttackController`` — it never replaces
the controller, never touches ``RunLogic``, and never writes physics:

  - CONE HYSTERESIS: the incumbent nearest-defender holder is kept while
    a challenger is not closer by ``cone_hysteresis_m``; the avoidance
    angle then tracks the HELD defender continuously.
  - LANE DEADBAND: the committed drift direction only flips when the BC
    is clearly past midfield by more than ``lane_deadband_m``.  The
    chosen vector is published as ``geom["lane"]["active_lane_vec"]``
    (consumed opt-in by ``run_logic``).
  - GAP COMMIT: the selected defender pair is HELD for
    ``gap_commit_window_s`` of wall-clock sim time; a sustained reorder
    (or member loss) triggers a deterministic refit ranked by
    ``(x, player_id)``.
  - ESCAPE-EXIT RAMP: after the escape window ends the forced-scoot
    weight tapers linearly to zero over ``escape_exit_ramp_s`` instead of
    snapping (Stage-3F wall-clock style).
  - AUDIT: CONE_HOLDER_CHANGE / LANE_DIR_CHANGE / GAP_REFIT /
    ESCAPE_EXIT_BEGIN / VECTOR_FLIP land in the Task XII dump chain
    (game.debug.attack_structure_audit).

Dark-safe gate:
  ``AttackStructureTuners.steering_enabled = False`` makes every entry
  point a no-op (geometry returned untouched, ramp weight 0.0), so the
  controller runs the legacy steering path byte-identically.

Cycle law:
  State is keyed by ``id(world)`` (same pattern as
  attack_structure_authority) and cleared by ``reset_bc_steering`` on
  kickoff.  No rng anywhere — identical input sequences produce
  identical output sequences.
"""

import math

from game.debug.attack_structure_audit import emit_event

# ── Per-cycle state, keyed by id(world) — same pattern as the Task XII ──
# attack-structure authority.
_STEER_STATE: dict = {}


def _state(world):
    key = id(world)
    return _STEER_STATE.setdefault(
        key,
        {
            "cone_holder_pid": None,     # incumbent nearest-defender id
            "lane_sign": None,           # +1 = right/down vec, -1 = left/up
            "gap_pair": None,            # (pid_a, pid_b) committed pair
            "gap_commit_left_s": 0.0,    # remaining commit-window time
            "esc_prev_active": False,    # escape window active last frame
            "esc_exit_t": 0.0,           # seconds since escape exit began
            "esc_exit_done": False,
            "last_vec": None,            # (rx, ry) unit vector audit memory
        },
    )


def reset_bc_steering(world) -> None:
    """Clear per-cycle BC-steering state (kickoff / ball-secured)."""
    _STEER_STATE.pop(id(world), None)


def _dist(bc, d) -> float:
    dx = d.x - bc.x
    dy = d.y - bc.y
    return math.hypot(dx, dy)


# ─────────────────────────────────────────────────────────────────────
# 1. GEOMETRY STABILISER — cone hysteresis + lane deadband + gap commit
# ─────────────────────────────────────────────────────────────────────

def stabilise_geometry(world, bc, geom, tuners, dt: float = 0.0) -> dict:
    """Return a stabilised copy of the raw steering geometry dict.

    Pure compute over the dict built by ``AttackController`` — never
    writes player fields, never touches physics.  Self-gated: with
    ``steering_enabled`` False the input dict is returned UNCHANGED.
    """
    if not getattr(tuners, "steering_enabled", False):
        return geom

    st = _state(world)
    defenders = list(getattr(world, "defending_team", []) or [])
    out = dict(geom)

    # ── 1a. Defender-cone hysteresis ────────────────────────────────
    cone = dict(out.get("def_cone") or {})
    holder_pid = st["cone_holder_pid"]
    holder = None
    if holder_pid is not None:
        holder = next(
            (d for d in defenders
             if getattr(d, "player_id", None) == holder_pid),
            None,
        )
    margin = float(getattr(tuners, "cone_hysteresis_m", 0.6))
    raw_near = cone.get("nearest_def")

    if holder is None:
        new_holder = raw_near          # first frame or holder left the field
    else:
        holder_dist = _dist(bc, holder)
        raw_dist = cone.get("nearest_def_dist")
        if raw_dist is None:
            raw_dist = _dist(bc, raw_near) if raw_near is not None else holder_dist
        # Incumbent keeps the slot unless the challenger is closer BY the
        # hysteresis margin (anti-flip band).
        new_holder = holder if raw_dist >= holder_dist - margin else raw_near

    if new_holder is not None:
        new_pid = getattr(new_holder, "player_id", None)
        if holder_pid is not None and new_pid != holder_pid:
            emit_event(world, "CONE_HOLDER_CHANGE", old=holder_pid, new=new_pid)
        st["cone_holder_pid"] = new_pid
        # Track the HELD defender continuously: distance/angle/classification
        # recomputed from the holder each frame (smooth, not a flip source).
        nd = _dist(bc, new_holder)
        if nd > 1e-6:
            ang = ((new_holder.x - bc.x) / nd, (new_holder.y - bc.y) / nd)
        else:
            ang = (0.0, 0.0)
        cone.update({
            "nearest_def": new_holder,
            "nearest_def_dist": nd,
            "nearest_def_angle": ang,
            "def_front": ang[0] > 0.3,
            "def_side": -0.3 <= ang[0] <= 0.3,
            "def_back": ang[0] < -0.3,
        })
    out["def_cone"] = cone

    # ── 1b. Lane-direction deadband ─────────────────────────────────
    lane = dict(out.get("lane") or {})
    offset_y = lane.get("offset_y", bc.y - 35.0)
    deadband = float(getattr(tuners, "lane_deadband_m", 1.5))
    raw_sign = 1 if offset_y < 0 else -1     # +1 = right/down, -1 = left/up
    prev_sign = st["lane_sign"]
    if abs(offset_y) <= deadband and prev_sign is not None:
        sign = prev_sign                     # hold through the deadband
    else:
        sign = raw_sign
        if prev_sign is not None and sign != prev_sign:
            emit_event(world, "LANE_DIR_CHANGE", old=prev_sign, new=sign)
        st["lane_sign"] = sign
    if sign == 1:
        lane["active_lane_vec"] = lane.get("right_lane_vec", (0.0, 1.0))
    else:
        lane["active_lane_vec"] = lane.get("left_lane_vec", (0.0, -1.0))
    out["lane"] = lane

    # ── 1c. Gap-pair commit window ──────────────────────────────────
    gap = dict(out.get("gap") or {})
    window = float(getattr(tuners, "gap_commit_window_s", 0.75))
    live_ids = {getattr(d, "player_id", None) for d in defenders}
    pair = st["gap_pair"]
    pair_valid = (
        pair is not None
        and len(defenders) >= 2
        and pair[0] in live_ids
        and pair[1] in live_ids
    )

    if pair_valid and st["gap_commit_left_s"] > 0.0:
        st["gap_commit_left_s"] -= float(dt)
    elif len(defenders) < 2:
        # Insufficient defenders for a gap pair: clear any held pair and
        # leave the raw gap dict untouched (attack_geometry's no-pair
        # fallback owns it).
        if pair is not None:
            emit_event(world, "GAP_REFIT", old=str(pair), new=None)
        pair = None
        st["gap_pair"] = None
        st["gap_commit_left_s"] = 0.0
        pair_valid = False
    elif not pair_valid or st["gap_commit_left_s"] <= 0.0:
        # Refit: deterministic rank by (x, player_id), first two.
        # Safe here because len(defenders) >= 2 (the branch above handled
        # the single/zero-defender case).
        ordered = sorted(
            defenders,
            key=lambda d: (getattr(d, "x", 0.0), getattr(d, "player_id", 0)),
        )
        new_pair = (
            getattr(ordered[0], "player_id", None),
            getattr(ordered[1], "player_id", None),
        )
        if pair is not None and new_pair != pair:
            emit_event(world, "GAP_REFIT", old=str(pair), new=str(new_pair))
        pair = new_pair
        st["gap_pair"] = pair
        st["gap_commit_left_s"] = window
        pair_valid = True

    if pair_valid:
        d1 = next((d for d in defenders
                   if getattr(d, "player_id", None) == pair[0]), None)
        d2 = next((d for d in defenders
                   if getattr(d, "player_id", None) == pair[1]), None)
        if d1 is not None and d2 is not None:
            # Same maths as AttackGeometry.gap_geometry — KEEP IN SYNC with
            # its WIDTH_SCALE / DEPTH_SCALE literals.
            gap_x = (d1.x + d2.x) * 0.5
            gap_y = (d1.y + d2.y) * 0.5
            dx = gap_x - bc.x
            dy = gap_y - bc.y
            dist = math.hypot(dx, dy)
            gap_angle = (dx / dist, dy / dist) if dist > 0 else (1.0, 0.0)
            wx = d2.x - d1.x
            wy = d2.y - d1.y
            gap_width = math.hypot(wx, wy)
            gap_depth = max(0.0, gap_x - bc.x)
            width_score = min(1.0, gap_width / 20.0)
            depth_score = min(1.0, gap_depth / 30.0)
            gap.update({
                "gap_angle": gap_angle,
                "gap_depth": gap_depth,
                "gap_width": gap_width,
                "gap_score": 0.5 * width_score + 0.5 * depth_score,
                "gap_center": (gap_x, gap_y),
            })
    out["gap"] = gap

    return out


# ─────────────────────────────────────────────────────────────────────
# 2. ESCAPE-EXIT RAMP — taper the forced scoot instead of snapping
# ─────────────────────────────────────────────────────────────────────

def note_escape_active(world) -> None:
    """Record that the escape window is ACTIVE this frame.

    Called by the controller inside its escape branch; arms the ramp so
    the FIRST inactive frame begins the taper from the full blend weight.
    """
    st = _state(world)
    st["esc_prev_active"] = True
    st["esc_exit_t"] = 0.0
    st["esc_exit_done"] = False


def escape_exit_blend(world, base_blend: float, dt: float, tuners) -> float:
    """Forced-vector weight for a frame OUTSIDE the escape window.

    Returns 0.0 when the steering gate is off (legacy path), before the
    ramp begins (handled by the controller's own escape branch), and
    after ``escape_exit_ramp_s`` of wall-clock sim time has elapsed.
    Between those it tapers linearly from ``base_blend`` to zero.
    """
    if not getattr(tuners, "steering_enabled", False):
        return 0.0
    st = _state(world)
    if st["esc_prev_active"]:
        # First frame after the escape window closed: begin the ramp.
        st["esc_prev_active"] = False
        st["esc_exit_t"] = 0.0
        st["esc_exit_done"] = False
        emit_event(world, "ESCAPE_EXIT_BEGIN", base_blend=float(base_blend))
    if st["esc_exit_done"]:
        return 0.0
    ramp = max(1e-6, float(getattr(tuners, "escape_exit_ramp_s", 0.30)))
    st["esc_exit_t"] += float(dt)
    if st["esc_exit_t"] >= ramp:
        st["esc_exit_done"] = True
        return 0.0
    frac = 1.0 - (st["esc_exit_t"] / ramp)
    return float(base_blend) * frac


# ─────────────────────────────────────────────────────────────────────
# 3. VECTOR-FLIP AUDIT — macro event on large run-vector rotations
# ─────────────────────────────────────────────────────────────────────

def record_run_vector(world, rx: float, ry: float, tuners) -> None:
    """Record the normalised run vector; emit VECTOR_FLIP when the
    direction rotates by more than ``vector_flip_threshold_deg`` between
    consecutive frames (audit-only — never modifies the vector)."""
    st = _state(world)
    cur = (float(rx), float(ry))
    prev = st["last_vec"]
    if prev is not None:
        mag = math.hypot(cur[0], cur[1]) * math.hypot(prev[0], prev[1])
        if mag > 1e-9:
            dot = max(-1.0, min(1.0, (prev[0] * cur[0] + prev[1] * cur[1]) / mag))
            deg = math.degrees(math.acos(dot))
            threshold = float(getattr(tuners, "vector_flip_threshold_deg", 60.0))
            if deg > threshold:
                emit_event(world, "VECTOR_FLIP",
                           from_deg=round(deg, 2),
                           frm=(round(prev[0], 3), round(prev[1], 3)),
                           to=(round(cur[0], 3), round(cur[1], 3)))
    st["last_vec"] = cur
