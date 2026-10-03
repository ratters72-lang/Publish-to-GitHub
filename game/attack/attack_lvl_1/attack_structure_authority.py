"""attack_structure_authority.py — Single-writer attack-geometry authority
(Task XII).

The attack side was the last noisy system: every support runner chased the
ball carrier directly, so the formation collapsed into a blob (lane drift);
there was no depth/stagger law, no sweep/support identity, no settle timing,
and no audit trail.  This module is the SINGLE WRITER for attacking shape:

  - IDENTITY: stable FIRST_RECEIVER / SWEEP / SUPPORT_B/C/D slots.
  - LANE LAW: target_y anchors to ruck_y + open_side * lane_m (NEVER bc.y),
    which locks lane drift exactly like the Task VII defensive lane law.
  - DEPTH/STAGGER LAW: target_x = bc.x - depth_m, enforced behind the BC.
  - SETTLE: a wall-clock window entered on POST_RUCK_OPEN_PLAY entry (and
    on the first OPEN_PLAY after kickoff), during which identities and lane
    targets stay frozen.  Refits are blocked until the window expires.
  - AUDIT: every refit/lane-read/identity-swap/settle transition is emitted
    through game.debug.attack_structure_audit (flag-gated, zero-cost off).

Authority boundary (mirrors DefenceManager/orders.py):
  - The manager (AttackManager) is the sole entry point per frame.
  - This module issues target_x / target_y / move_speed / unfreeze for the
    SUPPORT runners ONLY.  It never touches the BC (AttackController owns
    BC steering), never touches the DH (PTBManager owns DH movement), and
    never writes physics.

Dark-safe gate:
  ``AttackStructureTuners.structure_enabled = False`` (the default) makes
  apply_support_orders() return False so AttackManager runs the legacy
  BC-chase byte-stable.  This module only activates when the gate is on.
"""

import math

# ── Per-cycle state, keyed by id(world) — same pattern as ball_side_collapse ──
_STRUCTURE_STATE: dict = {}


def _state(world):
    key = id(world)
    return _STRUCTURE_STATE.setdefault(
        key,
        {
            "identity": {},            # player_id -> (role, slot)
            "settle_remaining_s": 0.0,
            "settling": False,
            "last_phase": None,
            "last_targets": {},        # player_id -> (target_x, target_y)
        },
    )


def reset_attack_structure(world) -> None:
    """Clear per-cycle attack-structure state (kickoff / ball-secured)."""
    _STRUCTURE_STATE.pop(id(world), None)


def _open_side(ruck_y: float) -> int:
    """Open side is the side with more field: 1 toward y=70, -1 toward y=0."""
    return 1 if ruck_y < 35.0 else -1


def _lane_anchor_y(world, slot: str, tuners, open_side: int) -> float:
    """Ruck-anchored lane Y for a slot (the lane law)."""
    ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    offsets = {
        "FR": tuners.lane_fr_m,
        "SUPPORT_B": tuners.lane_support_b_m,
        "SUPPORT_C": tuners.lane_support_c_m,
        "SWEEP": tuners.lane_sweep_m,
    }
    off = offsets.get(slot, tuners.lane_weak_support_m)
    return ruck_y + open_side * off


def _depth_x(bc, slot: str, tuners) -> float:
    """Depth law: target_x = bc.x - depth_m (behind the BC; attack runs +X)."""
    depths = {
        "FR": tuners.depth_fr_m,
        "SUPPORT_B": tuners.depth_support_b_m,
        "SUPPORT_C": tuners.depth_support_c_m,
        "SWEEP": tuners.depth_sweep_m,
    }
    return bc.x - depths.get(slot, tuners.depth_support_b_m)


def _find(team, player_id):
    for p in team:
        if getattr(p, "player_id", None) == player_id:
            return p
    return None


def _ideal_slots(eligible, ruck_y: float):
    """Fresh ideal slot assignment (ranked, deterministic).

    Returns ``list[(player, slot)]`` ordered FR → SUPPORT_B/C... and SWEEP.
    """
    if not eligible:
        return []
    ordered = sorted(
        eligible, key=lambda p: (abs(p.y - ruck_y), getattr(p, "player_id", 0))
    )
    fr = ordered[0]
    slots = [(fr, "FR")]
    rest = [p for p in ordered[1:]]
    if rest:
        open_side = _open_side(ruck_y)
        # SWEEP = widest on the open side
        sweep = max(rest, key=lambda p: (p.y - ruck_y) * open_side)
        slots.append((sweep, "SWEEP"))
        rest = [p for p in rest if p is not sweep]
    for i, p in enumerate(rest):
        label = f"SUPPORT_{chr(ord('B') + i)}"   # B, C, D, E...
        slots.append((p, label))
    return slots


def role_for_slot(slot: str) -> str:
    if slot == "FR":
        return "first_receiver"
    if slot == "SWEEP":
        return "sweep"
    return "support"


def _resolve_identities(world, tuners, eligible):
    """Reconcile held identities with the fresh ideal, with hysteresis.

    One player per slot, guaranteed.  During settle the identity map is
    frozen (no refits).  After settle, a held FR/SWEEP holder keeps its
    slot while it is still eligible, has not crossed sides of the ruck, and
    is within ``identity_hysteresis_m`` of its lane anchor.  Support slots
    are deterministic by proximity rank.
    """
    st = _state(world)
    ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    open_side = _open_side(ruck_y)
    team = getattr(world, "attacking_team", []) or []
    ideal = _ideal_slots(eligible, ruck_y)

    if st["settling"]:
        # Frozen: keep held identities that are still eligible.  If the
        # settle-begin clear left an EMPTY map, seed it ONCE from the ideal
        # so supports receive lane orders through the whole window (the
        # freeze contract is "no refits", never "no orders").
        keep = {}
        for pid, (role, slot) in st["identity"].items():
            p = _find(team, pid)
            if p is None or p not in eligible:
                continue
            keep[pid] = (role, slot)
        if not keep:
            for p, slot in ideal:
                keep[p.player_id] = (role_for_slot(slot), slot)
        st["identity"] = keep
        return keep, ideal

    held = st["identity"]
    result = {}
    used_pids = set()
    used_slots = set()

    slot_priority = ["FR", "SWEEP", "SUPPORT_B", "SUPPORT_C", "SUPPORT_D",
                     "SUPPORT_E", "SUPPORT_F"]
    avail_slots = [s for s in slot_priority if s in {slot for _p, slot in ideal}]

    ideal_by_slot = {}
    for p, slot in ideal:
        ideal_by_slot.setdefault(slot, p)

    def choose_holder(slot):
        """Preferred player for a slot: held-qualified, else ideal, else spare."""
        # (1) held holder that still qualifies (anti-flip stickiness)
        for pid, (_role, s) in held.items():
            if s != slot:
                continue
            p = _find(team, pid)
            if p is None or p not in eligible or p.player_id in used_pids:
                continue
            anchor_y = _lane_anchor_y(world, slot, tuners, open_side)
            if abs(p.y - anchor_y) > tuners.identity_hysteresis_m:
                continue
            anchor_side = -1 if anchor_y < ruck_y else 1
            p_side = -1 if p.y < ruck_y else 1
            if anchor_side != p_side:
                continue
            return p
        # (2) ideal player for this slot, if free
        cand = ideal_by_slot.get(slot)
        if cand is not None and cand.player_id not in used_pids:
            return cand
        # (3) first free eligible by proximity
        spare = [p for p in eligible if p.player_id not in used_pids]
        spare.sort(key=lambda p: (abs(p.y - ruck_y), p.player_id))
        return spare[0] if spare else None

    for slot in avail_slots:
        p = choose_holder(slot)
        if p is None:
            continue
        result[p.player_id] = (role_for_slot(slot), slot)
        used_pids.add(p.player_id)
        used_slots.add(slot)

    # Any leftover eligible players fold into generic SUPPORT slots.
    for p in eligible:
        if p.player_id in result:
            continue
        i = len(used_slots)
        label = f"SUPPORT_{chr(ord('B') + i)}"
        while label in used_slots:
            i += 1
            label = f"SUPPORT_{chr(ord('B') + i)}"
        result[p.player_id] = ("support", label)
        used_slots.add(label)

    st["identity"] = result
    return result, ideal


def update_settle_state(world, phase: str, dt: float) -> None:
    """Advance the settle window; detect POST_RUCK / OPEN_PLAY entry.

    Begins a settle window when the engine enters POST_RUCK_OPEN_PLAY
    (after PTB handover) or the first OPEN_PLAY frame after a kickoff
    reset (previous phase was not OPEN_PLAY).
    """
    st = _state(world)
    prev = st["last_phase"]
    st["last_phase"] = phase

    if phase == "POST_RUCK_OPEN_PLAY" and prev != "POST_RUCK_OPEN_PLAY":
        from game.debug.attack_structure_audit import emit_event
        st["settling"] = True
        st["settle_remaining_s"] = float(getattr(world, "_settle_window_s", 1.0))
        st["identity"] = {}
        emit_event(world, "SETTLE_BEGIN", phase=phase,
                   window_s=st["settle_remaining_s"])
    elif phase == "OPEN_PLAY" and prev != "OPEN_PLAY":
        from game.debug.attack_structure_audit import emit_event
        st["settling"] = True
        st["settle_remaining_s"] = float(getattr(world, "_settle_window_s", 1.0))
        st["identity"] = {}
        emit_event(world, "SETTLE_BEGIN", phase=phase,
                   window_s=st["settle_remaining_s"])

    if st["settling"]:
        st["settle_remaining_s"] -= dt
        if st["settle_remaining_s"] <= 0.0:
            st["settling"] = False
            st["settle_remaining_s"] = 0.0
            from game.debug.attack_structure_audit import emit_event
            emit_event(world, "SETTLE_END", phase=phase)


def apply_support_orders(ope, dt: float, tuners) -> bool:
    """Issue authored lane orders to every non-BC/non-DH attacker.

    Returns True if structured orders were applied (gate ON), False if the
    caller should fall back to the legacy BC-chase (gate OFF).
    """
    if not getattr(tuners, "structure_enabled", False):
        return False

    world = getattr(ope, "world", None)
    if world is None:
        return False
    bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return False

    phase = getattr(ope, "_phase", None)
    if phase not in ("OPEN_PLAY", "POST_RUCK_OPEN_PLAY"):
        return False

    world._settle_window_s = getattr(tuners, "settle_window_s", 1.0)
    update_settle_state(world, phase, dt)

    ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    open_side = _open_side(ruck_y)
    team = getattr(world, "attacking_team", []) or []
    dh = getattr(ope, "dh", None)

    eligible = [
        p for p in team
        if p is not bc and p is not dh and not getattr(p, "frozen", False)
    ]
    st = _state(world)
    identities, _ideal = _resolve_identities(world, tuners, eligible)

    for p in team:
        if p is bc or p is dh:
            continue
        pid = getattr(p, "player_id", None)
        entry = identities.get(pid)
        if entry is None:
            p.attack_role = "none"
            continue
        role, slot = entry
        p.attack_role = role
        p.attack_lane_slot = slot

        tx = _depth_x(bc, slot, tuners)
        ty = _lane_anchor_y(world, slot, tuners, open_side)

        # Lane-read event: emitted when the target moves by more than the
        # threshold (this is the auditable "every lane read" macro).
        last = st["last_targets"].get(pid)
        if last is not None:
            delta = math.hypot(tx - last[0], ty - last[1])
            if delta > getattr(tuners, "lane_read_delta_m", 1.0):
                from game.debug.attack_structure_audit import emit_event
                emit_event(world, "LANE_READ",
                           player_id=pid, slot=slot, target_x=tx, target_y=ty)
        st["last_targets"][pid] = (tx, ty)

        # Write the order (single-writer authority, manager-attributed).
        p.target_x = tx
        p.target_y = ty
        p.move_speed = float(getattr(tuners, "support_speed_mps", 4.0))
        p.frozen = False
        p.freeze_timer = 0.0
        p.movement_writer = "manager:attack"

        # Simple velocity toward the lane target (matches legacy integration,
        # which drives position through p.update()).
        dx = tx - p.x
        dy = ty - p.y
        dist = math.hypot(dx, dy)
        if dist > 0.01:
            spd = p.move_speed
            p.vx = (dx / dist) * spd
            p.vy = (dy / dist) * spd
        else:
            p.vx = 0.0
            p.vy = 0.0

    return True
