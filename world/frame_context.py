"""
world/frame_context.py — FrameContext builder.

Builds a read-only FRAME_CONTEXT dict (as specified by GAME_CONTEXT_SPEC)
once per frame, projecting the live engine state into a clean nested dict
that any subsystem can read.

Rule: NO mutations to world or engine.  Pure projection.
"""

import math

from world.world_state import GameSubState
from world.rules.movement_contracts import (
    MOVEMENT_RULES,
    Role,
    get_role,
    Role as EngineRoleEnum,
)
from player.player import Player, DefenderRole


# ═══════════════════════════════════════════════════════════════════════
# 1. PHASE — authoritative phase state for this frame
# ═══════════════════════════════════════════════════════════════════════

# Maps engine-internal phase strings to GAME_CONTEXT phase names.
_PHASE_NAME_MAP: dict[str, str] = {
    "OPEN_PLAY":             "OPEN_PLAY",
    "TACKLE_INIT":           "TES",
    "TACKLE_CONTACT":        "TES",
    "TACKLE_DRIVE":          "TES",
    "TACKLE_DOMINANCE":      "TES",
    "RELEASE":               "RELEASE",
    "PTB_PREP":              "PTB_PREP",
    "PTB_EXEC":              "PTB_EXEC",
    "PTB":                   "PTB_EXEC",  # PTB substate treated as exec
    "POST_RUCK_OPEN_PLAY":   "POST_RUCK",
}

_TES_PHASES = frozenset({"TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE"})


def _build_phase(engine) -> dict:
    """Build phase context from OpenPlayEngine state."""
    raw_phase = engine._phase  # e.g. "TACKLE_INIT", "RELEASE", etc.
    name = _PHASE_NAME_MAP.get(raw_phase, "OPEN_PLAY")
    sub_state = engine.world.sub_state
    timer_s = engine._phase_timer
    return {
        "name": name,
        "sub_state": sub_state,
        "timer_s": timer_s,
        "is_open_play": name == "OPEN_PLAY",
        "is_tes": raw_phase in _TES_PHASES,
        "is_release": name == "RELEASE",
        "is_ptb_prep": name == "PTB_PREP",
        "is_ptb_exec": name == "PTB_EXEC",
        "is_ptb": name in ("PTB_PREP", "PTB_EXEC"),
        "is_post_ruck": name == "POST_RUCK",
    }


# ═══════════════════════════════════════════════════════════════════════
# 2. RUCK — anchor + actors around the ball
# ═══════════════════════════════════════════════════════════════════════

def _build_ruck(world, engine) -> dict:
    """Build ruck context from world state and engine tackler tracking."""
    bc = world.ball_carrier if hasattr(world, "ball_carrier") else None
    anchor_x = getattr(world, "ruck_x_m", None)
    anchor_y = getattr(world, "ruck_y_m", None)

    # If ruck_x_m is None, fall back to ptb_anchor if available, then BC position
    if anchor_x is None:
        anchor_x = getattr(world, "ptb_anchor_x", None)
    if anchor_x is None and bc is not None:
        anchor_x = bc.x
    if anchor_x is None:
        anchor_x = 60.0

    if anchor_y is None:
        anchor_y = getattr(world, "ptb_anchor_y", None)
    if anchor_y is None and bc is not None:
        anchor_y = bc.y
    if anchor_y is None:
        anchor_y = 35.0

    # Determine if a ruck exists: not in OPEN_PLAY
    exists = engine._phase != "OPEN_PLAY"

    # Collect tackler IDs from players with TACKLER_x roles
    tackler_ids: list[int] = []
    tacklers_frozen: dict[int, bool] = {}
    for p in world.players:
        role = getattr(p, "defender_role", None)
        if role in (DefenderRole.TACKLER_1, DefenderRole.TACKLER_2, DefenderRole.TACKLER_3):
            tackler_ids.append(p.player_id)
            tacklers_frozen[p.player_id] = p.frozen

    # Check if any markers are present
    markers_present = any(
        getattr(p, "defender_role", None) in (DefenderRole.MARKER_A, DefenderRole.MARKER_B)
        for p in world.players
    )

    return {
        "exists": exists,
        "anchor_x": anchor_x,
        "anchor_y": anchor_y,
        "ball_carrier_id": bc.player_id if bc is not None else None,
        "ball_carrier_frozen": bc.frozen if bc is not None else False,
        "tackler_ids": tackler_ids,
        "tacklers_frozen": tacklers_frozen,
        "markers_present": markers_present,
    }


# ═══════════════════════════════════════════════════════════════════════
# 3. RUCK_POSITION_INDEX — spacing meaning (0 off, 1 off, 2 off, …)
# ═══════════════════════════════════════════════════════════════════════

def _build_ruck_position(world, ruck_ctx: dict) -> dict:
    """
    Build ruck_position index for all defenders.

    Markers (MARKER_A/B) are always index 0 if present.
    Remaining defenders sorted by distance from (anchor_x, anchor_y).
    Non-defenders (attackers, BC, DH) are excluded.
    """
    anchor_x = ruck_ctx["anchor_x"]
    anchor_y = ruck_ctx["anchor_y"]
    bc_id = ruck_ctx["ball_carrier_id"]
    bc = world.ball_carrier if hasattr(world, "ball_carrier") else None

    player_index: dict[int, int] = {}
    markers: list[int] = []
    a_defenders: list[int] = []
    b_defenders: list[int] = []
    c_defenders: list[int] = []
    edges: list[int] = []

    # Separate markers from other defenders
    marker_players: list[Player] = []
    other_defenders: list[Player] = []

    for p in world.players:
        role = getattr(p, "defender_role", None)
        # Only include defenders and the BC if in a tackle phase
        if role is None and p is not bc:
            continue
        if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
            marker_players.append(p)
        elif role is not None or p is bc:
            other_defenders.append(p)

    # Sort non-marker defenders by squared distance from ruck anchor
    other_defenders.sort(
        key=lambda p: (p.x - anchor_x) ** 2 + (p.y - anchor_y) ** 2
    )

    # Assign indices: markers first
    idx = 0
    for p in marker_players:
        player_index[p.player_id] = idx
        markers.append(p.player_id)
        idx += 1

    # Now assign remaining defenders (skip marker roles already assigned)
    for p in other_defenders:
        if p.player_id in player_index:
            continue  # already assigned as marker
        role = getattr(p, "defender_role", None)
        # BC in non-TES phases gets index as nearest defender
        player_index[p.player_id] = idx
        if idx == 0 and not markers:
            # No markers exist — index 0 is first defender off ruck
            markers.append(p.player_id)
        elif idx == 1:
            a_defenders.append(p.player_id)
        elif idx == 2:
            b_defenders.append(p.player_id)
        elif idx == 3:
            c_defenders.append(p.player_id)
        else:
            edges.append(p.player_id)
        idx += 1

    # If no markers were found but someone got index 0, move them to markers
    if not markers:
        # Find whoever got index 0
        first = next(
            (pid for pid, i in player_index.items() if i == 0),
            None
        )
        if first is not None:
            markers.append(first)
            # Remove them from wherever they ended up
            if first in a_defenders:
                a_defenders.remove(first)
            elif first in b_defenders:
                b_defenders.remove(first)
            elif first in c_defenders:
                c_defenders.remove(first)
            elif first in edges:
                edges.remove(first)

    return {
        "player_index": player_index,
        "markers": markers,
        "a_defenders": a_defenders,
        "b_defenders": b_defenders,
        "c_defenders": c_defenders,
        "edges": edges,
    }


# ═══════════════════════════════════════════════════════════════════════
# 4. ENGINE_ROLE — movement/freeze authority (MOVEMENT_RULES)
# ═══════════════════════════════════════════════════════════════════════

_ENGINE_ROLE_NAME_MAP: dict[Role, str] = {
    Role.MARKER_A:       "MARKER_A",
    Role.MARKER_B:       "MARKER_B",
    Role.DUMMY_HALF:     "DUMMY_HALF",
    Role.DEF_RETREAT_10: "DEF_RETREAT_10",
    Role.DEF_LINE_READY: "DEF_LINE_READY",
    Role.DEF_CHASE:      "DEF_CHASE",
    Role.TACKLER_1:      "TACKLER_1",
    Role.TACKLER_2:      "TACKLER_2",
    Role.TACKLER_3:      "TACKLER_3",
    Role.ATT_CARRIER:    "BALL_CARRIER",
    Role.ATT_SUPPORT:    "ATT_SUPPORT",
    Role.NONE:           "NONE",
}

_VALID_ROLES: list[str] = [
    "BALL_CARRIER", "DUMMY_HALF",
    "TACKLER_1", "TACKLER_2", "TACKLER_3",
    "DEF_RETREAT_10",
    "MARKER_A", "MARKER_B",
    "DEF_LINE_READY", "DEF_CHASE",
    "ATT_SUPPORT", "NONE",
]


def _build_engine_role(world) -> dict:
    """Build engine_role context using get_role() from movement_contracts."""
    player_role: dict[int, str] = {}
    for p in world.players:
        role_enum = get_role(p)
        name = _ENGINE_ROLE_NAME_MAP.get(role_enum, "NONE")
        player_role[p.player_id] = name
    return {
        "player_role": player_role,
        "valid_roles": _VALID_ROLES,
    }


# ═══════════════════════════════════════════════════════════════════════
# 5. DEFENSIVE_ROLE — geometry/depth/spacing (DefenceTuners)
# ═══════════════════════════════════════════════════════════════════════

def _build_defensive_role(world, engine) -> dict:
    """
    Build defensive_role context for defenders (markers + line defenders).

    Only players with a defender_role (i.e. defending team) are included.
    Attackers, BC, DH, support runners are excluded.
    """
    tuners = engine.def_tuners

    player_role: dict[int, str] = {}
    for p in world.players:
        role = getattr(p, "defender_role", None)
        if role is None:
            continue  # skip attackers, BC, support

        # Map DefenderRole to geometry depth class
        if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
            mapped = "A"  # markers are closest
        elif role == DefenderRole.DUMMY_HALF:
            mapped = "A"  # DH is also at marker depth
        elif role == DefenderRole.TACKLER_1:
            mapped = "A"
        elif role in (DefenderRole.TACKLER_2, DefenderRole.TACKLER_3):
            mapped = "B"
        elif role == DefenderRole.DEF_RETREAT_10:
            # Retreating defenders — use depth_class if available
            depth = getattr(p, "depth_class", None)
            if depth in ("A", "B", "C", "D"):
                mapped = depth
            else:
                mapped = "C"
        elif role in (DefenderRole.DEF_LINE_READY, DefenderRole.DEF_CHASE):
            depth = getattr(p, "depth_class", None)
            if depth in ("A", "B", "C", "D"):
                mapped = depth
            else:
                # Fall back to positional role classification
                pos_role = getattr(p, "positional_role", None)
                if pos_role in ("CENTRE", "WINGER"):
                    mapped = pos_role
                else:
                    mapped = "CENTRE"  # sensible default
        else:
            continue  # unknown defender role — skip

        player_role[p.player_id] = mapped

    return {
        "player_role": player_role,
        "ROLE_DEPTH_M": dict(tuners.ROLE_DEPTH_M),
        "width_curve_factor": dict(tuners.width_curve_factor),
    }


# ═══════════════════════════════════════════════════════════════════════
# 6. MOVEMENT — who may move this frame
# ═══════════════════════════════════════════════════════════════════════

def _build_movement(world, engine, phase_name: str, engine_role_ctx: dict) -> dict:
    """
    Build movement context from MOVEMENT_RULES table.

    Reads the allowed/must_be_frozen sets for the current phase,
    then computes per-player bool dicts.
    """
    raw_phase = engine._phase
    rules = MOVEMENT_RULES.get(raw_phase)

    if rules is None:
        # Fallback: no rules — everything allowed
        allowed_roles = set()
        must_frozen = set()
    else:
        allowed_roles = rules.get("allowed_to_move", set())
        must_frozen = rules.get("must_be_frozen", set())

    # Map engine role names to Role enum values
    allowed_engine_roles: set[str] = set()
    must_be_frozen_engine_roles: set[str] = set()
    for r in allowed_roles:
        name = _ENGINE_ROLE_NAME_MAP.get(r)
        if name:
            allowed_engine_roles.add(name)
    for r in must_frozen:
        name = _ENGINE_ROLE_NAME_MAP.get(r)
        if name:
            must_be_frozen_engine_roles.add(name)

    player_role = engine_role_ctx["player_role"]
    allowed_to_move: dict[int, bool] = {}
    must_be_frozen_map: dict[int, bool] = {}

    for pid, role_name in player_role.items():
        allowed_to_move[pid] = role_name in allowed_engine_roles
        must_be_frozen_map[pid] = role_name in must_be_frozen_engine_roles

    # Belt-and-suspenders: BC and DH are never in must_be_frozen during
    # open-play or PTB phases (mirrors apply_phase_invariants logic).
    if engine._phase in ("OPEN_PLAY", "POST_RUCK_OPEN_PLAY", "PTB_PREP", "PTB_EXEC", "PTB"):
        bc = world.ball_carrier if hasattr(world, "ball_carrier") else None
        if bc is not None and bc.player_id in must_be_frozen_map:
            must_be_frozen_map[bc.player_id] = False
            allowed_to_move[bc.player_id] = True
        for p in world.players:
            if getattr(p, "attack_role", None) == "dummy_half":
                pid = p.player_id
                if pid in must_be_frozen_map:
                    must_be_frozen_map[pid] = False
                    allowed_to_move[pid] = True

    # ── NOTE (July 2026): The old hard override freezing DEF_RETREAT_10
    # during PTB phases has been REMOVED.  MOVEMENT_RULES is the sole
    # freeze authority — DEF_RETREAT_10 is in "allowed_to_move" for all
    # PTB phases (see movement_contracts.py).  The old override was a
    # belt-and-suspenders workaround that broke retreat behaviour:
    # defenders stood still during PTB instead of retreating to 10m.
    # The P7 retreat target code in OPE phase handlers (now replaced by
    # the SDM module) sets targets each frame; with frozen=False those
    # targets now produce actual retreat movement.

    return {
        "allowed_engine_roles": allowed_engine_roles,
        "must_be_frozen_engine_roles": must_be_frozen_engine_roles,
        "allowed_to_move": allowed_to_move,
        "must_be_frozen": must_be_frozen_map,
    }


# ═══════════════════════════════════════════════════════════════════════
# 7. FREEZE — actual frozen/suppressed state
# ═══════════════════════════════════════════════════════════════════════

def _build_freeze(world, engine, movement_ctx: dict, is_tes: bool, phase_name: str) -> dict:
    """
    Build freeze context from actual player frozen flags and movement suppression.

    movement_suppressed: True if vx == 0 AND target != position (soft freeze).

    global_freeze_like: True in TES phases (all non-ruck actors frozen)
    or when PTB_EXEC and all non-essential roles are frozen.
    """
    player_frozen: dict[int, bool] = {}
    movement_suppressed: dict[int, bool] = {}

    for p in world.players:
        pid = p.player_id
        player_frozen[pid] = p.frozen
        # Movement suppression: vx == 0 but target_x != x (offset > 0.05m)
        if abs(p.vx) < 0.001 and abs(p.target_x - p.x) > 0.05:
            movement_suppressed[pid] = True
        else:
            movement_suppressed[pid] = False

    # global_freeze_like heuristic
    global_freeze_like = False
    if is_tes:
        # In TES phases, all non-ruck actors are frozen by design
        global_freeze_like = True
    elif phase_name == "PTB_EXEC":
        # In PTB_EXEC, check if >80% of non-BC/DH/marker roles are frozen
        must_frozen = movement_ctx.get("must_be_frozen", {})
        if must_frozen:
            frozen_count = sum(1 for v in must_frozen.values() if v)
            total = len(must_frozen)
            if total > 0 and frozen_count / total >= 0.8:
                global_freeze_like = True

    return {
        "player_frozen": player_frozen,
        "movement_suppressed": movement_suppressed,
        "global_freeze_like": global_freeze_like,
    }


# ═══════════════════════════════════════════════════════════════════════
# 8. PLAYERS — unified per-player view (for SDM agent_input)
# ═══════════════════════════════════════════════════════════════════════

_DEFENDER_ROLE_STR_MAP: dict[DefenderRole, str] = {
    DefenderRole.MARKER_A: "MARKER_A",
    DefenderRole.MARKER_B: "MARKER_B",
    DefenderRole.LINE: "LINE",
    DefenderRole.KICK_CHASE: "KICK_CHASE",
    DefenderRole.DUMMY_HALF: "DUMMY_HALF",
    DefenderRole.TACKLER_1: "TACKLER_1",
    DefenderRole.TACKLER_2: "TACKLER_2",
    DefenderRole.TACKLER_3: "TACKLER_3",
    DefenderRole.DEF_RETREAT_10: "DEF_RETREAT_10",
    DefenderRole.DEF_LINE_READY: "DEF_LINE_READY",
    DefenderRole.DEF_CHASE: "DEF_CHASE",
}


def _build_players(world, engine_role_ctx, def_role_ctx, movement_ctx, freeze_ctx) -> dict:
    """Build a unified **players** dict keyed by player_id.

    Each entry contains position, roles, freeze state, and movement
    permissions — the subset needed by the SDM's ``agent_input``.
    """
    players: dict[int, dict] = {}
    engine_role_map = engine_role_ctx["player_role"]
    def_role_map = def_role_ctx["player_role"]
    allowed_move = movement_ctx["allowed_to_move"]
    must_frozen = movement_ctx["must_be_frozen"]
    player_frozen = freeze_ctx["player_frozen"]

    for p in world.players:
        pid = p.player_id
        dr = getattr(p, "defender_role", None)
        dr_str = _DEFENDER_ROLE_STR_MAP.get(dr, None)
        players[pid] = {
            "x": p.x,
            "y": p.y,
            "frozen": player_frozen.get(pid, False),
            "allowed_to_move": allowed_move.get(pid, False),
            "must_be_frozen": must_frozen.get(pid, False),
            "defender_role": dr_str,
            "engine_role": engine_role_map.get(pid, "NONE"),
        }
    return players


# ═══════════════════════════════════════════════════════════════════════
# BUILD — top-level entry point
# ═══════════════════════════════════════════════════════════════════════

def build_frame_context(world, engine) -> dict:
    """
    Build the complete FRAME_CONTEXT dict for the current frame.

    Must be called AFTER apply_phase_invariants() so frozen flags
    reflect the authoritative freeze state.

    Args:
        world: WorldState instance with all player/ball state.
        engine: OpenPlayEngine instance with phase/timer/def_tuners.

    Returns:
        dict structured as per FRAME_CONTEXT spec:
            frame -> {phase, ruck, ruck_position, engine_role,
                      defensive_role, movement, freeze, players}
    """
    phase_ctx = _build_phase(engine)
    ruck_ctx = _build_ruck(world, engine)
    ruck_pos_ctx = _build_ruck_position(world, ruck_ctx)
    engine_role_ctx = _build_engine_role(world)
    def_role_ctx = _build_defensive_role(world, engine)
    movement_ctx = _build_movement(world, engine, phase_ctx["name"], engine_role_ctx)
    freeze_ctx = _build_freeze(world, engine, movement_ctx, phase_ctx["is_tes"], phase_ctx["name"])
    players_ctx = _build_players(world, engine_role_ctx, def_role_ctx, movement_ctx, freeze_ctx)

    return {
        "frame": {
            "phase": phase_ctx,
            "ruck": ruck_ctx,
            "ruck_position": ruck_pos_ctx,
            "engine_role": engine_role_ctx,
            "defensive_role": def_role_ctx,
            "movement": movement_ctx,
            "freeze": freeze_ctx,
            "players": players_ctx,
        }
    }
