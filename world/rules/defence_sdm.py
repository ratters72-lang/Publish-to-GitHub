"""
world/rules/defence_sdm.py — Deterministic Structured Decision Module.

Single-function agent that reads ``agent_input`` (a pure-data dict projected
from the live engine state) and returns per-player decisions.

DESIGN
------
- ``decide_for_player()`` is a PURE FUNCTION — no state, no side effects.
  Given the same agent_input + player_id, it always returns the same result.
- It handles ALL phases (TES / RELEASE / PTB / OPEN_PLAY).
  During OPEN_PLAY defenders get role-appropriate depth targets relative to the BC
  (e.g. ``DEF_RETREAT_10 → bc_x + 10.0``).  The geometry pipeline handles Y-spread.
- The returned (target_x, target_y) is applied by the caller (OpenPlayEngine)
  using direct assignment and Player.update(dt).

CONTRACT
--------
``agent_input`` must have the following shape (projected from FrameContext):

    {
        "phase": str,                 # e.g. "OPEN_PLAY", "TACKLE_INIT", ...
        "is_tes": bool,
        "is_release": bool,
        "is_ptb": bool,               # PTB_PREP or PTB_EXEC
        "is_open_play": bool,         # true during OPEN_PLAY
        "ruck_x": float,              # ruck anchor X (metres)
        "bc_x": float | None,         # ball carrier X (metres), or None
        "players": {
            pid: {
                "x": float,
                "y": float,
                "frozen": bool,
                "allowed_to_move": bool,
                "must_be_frozen": bool,
                "defender_role": str | None,  # e.g. "DEF_RETREAT_10"
                "engine_role": str,            # e.g. "BALL_CARRIER", "DUMMY_HALF"
            }
        }
    }

The agent_input dict MUST be read-only for this module.  No mutations.
"""

# ── Constants ──────────────────────────────────────────────────────────
RETREAT_DISTANCE_M: float = 10.0  # 10-metre retreat compliance distance


def decide_for_player(agent_input: dict, pid: int) -> dict:
    """Return a decision dict for player *pid* given the current *agent_input*.

    Returns
    -------
    dict with keys:
        "action"      — "hold" | "retreat_10" | "geometry"
        "target_x"    — X coordinate to move toward (m)
        "target_y"    — Y coordinate to move toward (m)
        "reason"      — human-readable explanation
    """
    phase: str = agent_input["phase"]
    is_tes: bool = agent_input["is_tes"]
    is_release: bool = agent_input["is_release"]
    is_ptb: bool = agent_input["is_ptb"]
    ruck_x: float = agent_input["ruck_x"]

    players: dict = agent_input["players"]
    p: dict = players.get(pid)
    if p is None:
        # Unknown player — safe fallback
        return {
            "action": "hold",
            "target_x": 60.0,
            "target_y": 35.0,
            "reason": "player_not_found",
        }

    # ── Hard gates (priority 1) ──────────────────────────────────
    if p["frozen"] or p["must_be_frozen"] or not p["allowed_to_move"]:
        return {
            "action": "hold",
            "target_x": p["x"],
            "target_y": p["y"],
            "reason": "frozen_or_not_allowed",
        }

    engine_role: str = p.get("engine_role", "NONE")
    def_role: str | None = p.get("defender_role", None)

    # ── TES phases ───────────────────────────────────────────────
    # Only DEF_RETREAT_10 defenders retreat; all other roles hold
    # (BC, tacklers, DH, markers are handled by the TES phase logic).
    # CRITICAL: use bc_x (updated each frame by drive LERP) as retreat
    # reference — ruck_x is stale until RELEASE entry.
    if is_tes:
        if def_role == "DEF_RETREAT_10":
            ref_x = agent_input.get("bc_x") or ruck_x
            tx = ref_x + RETREAT_DISTANCE_M
            return {
                "action": "retreat_10",
                "target_x": tx,
                "target_y": p["y"],
                "reason": "tes_retreat_10",
            }
        return {
            "action": "hold",
            "target_x": p["x"],
            "target_y": p["y"],
            "reason": "tes_non_retreat_role",
        }

    # ── RELEASE phase ────────────────────────────────────────────
    if is_release:
        if def_role == "DEF_RETREAT_10":
            tx = ruck_x + RETREAT_DISTANCE_M
            return {
                "action": "retreat_10",
                "target_x": tx,
                "target_y": p["y"],
                "reason": "release_retreat_10",
            }
        return {
            "action": "hold",
            "target_x": p["x"],
            "target_y": p["y"],
            "reason": "release_non_retreat_role",
        }

    # ── PTB phases ───────────────────────────────────────────────
    if is_ptb:
        if def_role == "DEF_RETREAT_10":
            tx = ruck_x + RETREAT_DISTANCE_M
            return {
                "action": "retreat_10",
                "target_x": tx,
                "target_y": p["y"],
                "reason": "ptb_retreat_10",
            }
        return {
            "action": "hold",
            "target_x": p["x"],
            "target_y": p["y"],
            "reason": "ptb_non_retreat_role",
        }

    # ── OPEN_PLAY / POST_RUCK — role-appropriate depth behind BC ──
    if agent_input.get("is_open_play", False) or phase == "POST_RUCK":
        bc_x = agent_input.get("bc_x")
        if bc_x is None:
            return {
                "action": "hold",
                "target_x": p["x"],
                "target_y": p["y"],
                "reason": "open_play_no_bc",
            }

        # SPEC 4.2 (2026-08-04): the OPEN_PLAY retreat writer derives its
        # X target from the defensive line governor's shared line depth
        # (line_depth_m — the anchor), NEVER from a hardcoded bc_x + 10.0.
        # The structured pipeline owns OPEN_PLAY in the default config, so
        # this fallback only runs in legacy scramble mode; using the anchor
        # keeps any SDM-driven retreat line-shaped rather than BC-relative.
        line_depth = agent_input.get("line_depth_m", ruck_x + RETREAT_DISTANCE_M)
        line_x = line_depth if line_depth else ruck_x + RETREAT_DISTANCE_M

        if def_role == "DEF_RETREAT_10":
            return {
                "action": "retreat_10",
                "target_x": line_x,
                "target_y": p["y"],
                "reason": "open_play_retreat_10",
            }
        elif def_role == "DEF_LINE_READY":
            return {
                "action": "retreat_10",
                "target_x": line_x,
                "target_y": p["y"],
                "reason": "open_play_line_ready",
            }
        elif def_role == "DEF_CHASE":
            tx = bc_x + 8.0
            return {
                "action": "retreat_10",
                "target_x": tx,
                "target_y": p["y"],
                "reason": "open_play_chase",
            }
        elif def_role == "DUMMY_HALF":
            tx = bc_x + 1.0
            return {
                "action": "retreat_10",
                "target_x": tx,
                "target_y": p["y"],
                "reason": "open_play_dh",
            }
        elif def_role == "MARKER_A":
            tx = bc_x + 1.0
            return {
                "action": "retreat_10",
                "target_x": tx,
                "target_y": p["y"],
                "reason": "open_play_marker_a",
            }
        elif def_role == "MARKER_B":
            tx = bc_x + 2.0
            return {
                "action": "retreat_10",
                "target_x": tx,
                "target_y": p["y"],
                "reason": "open_play_marker_b",
            }
        # All other roles (TACKLER_*, NONE, etc.) — hold at current position
        return {
            "action": "hold",
            "target_x": p["x"],
            "target_y": p["y"],
            "reason": "open_play_hold",
        }

    # ── Fallback — unknown phase ─────────────────────────────────
    return {
        "action": "hold",
        "target_x": p["x"],
        "target_y": p["y"],
        "reason": "unknown_phase",
    }


def extract_agent_input(ctx: dict) -> dict:
    """Extract ``agent_input`` dict from the full FrameContext dict.

    ``ctx`` must be the full output of ``build_frame_context()``.

    This is a stateless projection — no mutations.
    """
    frame = ctx["frame"]
    phase_name = frame["phase"]["name"]
    ruck_x = frame["ruck"]["anchor_x"]

    # Build players view from the separate per-player fields in frame_context.
    # The frame context stores per-player data in individual top-level dicts
    # keyed by player_id.  We merge them into a single per-player block.
    engine_role_map: dict = frame["engine_role"]["player_role"]
    def_role_map: dict = frame["defensive_role"]["player_role"]
    allowed_to_move_map: dict = frame["movement"]["allowed_to_move"]
    must_be_frozen_map: dict = frame["movement"]["must_be_frozen"]
    player_frozen: dict = frame["freeze"]["player_frozen"]

    players: dict = {}
    # Get all unique player IDs from all the maps
    all_pids: set = set(engine_role_map.keys())
    all_pids.update(def_role_map.keys())
    all_pids.update(allowed_to_move_map.keys())
    all_pids.update(must_be_frozen_map.keys())
    all_pids.update(player_frozen.keys())

    # We also need x, y — these come from the game world but the frame_context
    # doesn't have a "positions" map yet.  Store player positions directly
    # if available, otherwise use default.
    # The caller should populate frame["players"] with position data before
    # calling extract_agent_input(), or we accept that positions are baked
    # into the agent_input directly by the OPE._apply_defence_sdm() method.

    # NOTE: player positions (x, y) cannot be extracted from the current
    # frame_context schema.  The OPE._apply_defence_sdm() method must add
    # position data to the agent_input before calling decide_for_player().
    # For now return partial data — the OPE wiring will be the real caller.

    return {
        "phase": phase_name,
        "is_tes": frame["phase"]["is_tes"],
        "is_release": frame["phase"]["is_release"],
        "is_ptb": phase_name in ("PTB_PREP", "PTB_EXEC"),
        "is_open_play": frame["phase"]["is_open_play"],
        "ruck_x": ruck_x,
        "players": players,  # caller must fill positions
    }
