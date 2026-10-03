"""game/debug/chase_probe.py — CHASE X-collapse diagnostic probe (temporary).

Temporary instrumentation for the OPEN_PLAY CHASE movement audit.

Captures, per defender, per OPEN_PLAY MMDL frame:
  role / depth_role / engagement band,
  anchor_x vs target_x (before guard) vs target_x (after guard) vs current x,
  whether the MMDL X-drift guard / anchor snap applied,
  the applied movement state.

And per CHASE physics step (apply_movement_state):
  dx / dy / dist, the blended direction (dir_x/dir_y), desired vx/vy,
  the final vx/vy after acceleration + speed clamp,
  plus a CHASE_X_COLLAPSE_TOO_SLOW flag when |target_x - x| > 5.0 and
  |vx| < 0.5.

All probes are NO-OPs unless RLM_CHASE_PROBE=1 is set, so production
behaviour is byte-identical when the env var is absent.

Wiring:
  - defensive_behaviour_unified.py  (MMDL block) -> probe_mmdl_chase
  - player/movement_states.py       (CHASE path) -> probe_chase_velocity
"""
import math
import os


def probe_on() -> bool:
    """True only when RLM_CHASE_PROBE=1 (default off)."""
    return os.environ.get("RLM_CHASE_PROBE", "") == "1"


def probe_mmdl_chase(
    world,
    d,
    *,
    target_x_before_guard,
    target_x_after_guard,
    anchor_x_guard,
    state_name,
    snap_applied,
) -> None:
    """Log one MMDL per-defender snapshot (target-X / anchor / snap)."""
    if not probe_on():
        return
    role = getattr(d, "defender_role", None)
    role_name = getattr(role, "name", None) or str(role)
    band = getattr(d, "role_depth_class", None)
    if band is None:
        band = getattr(d, "depth_class", None)
    frame = getattr(world, "frame_count", None)
    if frame is None:
        frame = getattr(world, "frame", 0)
    print(
        f"MMDL f={frame} id={getattr(d, 'player_id', '?')} "
        f"role={role_name} depth_role={getattr(d, 'depth_role', None)} "
        f"band={band} state={state_name} x={d.x:.2f} "
        f"anchor_guard={anchor_x_guard:.2f} "
        f"tx_before={target_x_before_guard:.3f} "
        f"tx_after={target_x_after_guard:.3f} "
        f"snap={snap_applied} |tx-x|={abs(d.x - target_x_after_guard):.3f} "
        f"vx={getattr(d, 'vx', 0.0):.3f} vy={getattr(d, 'vy', 0.0):.3f}"
        # ── WRITER-ROLE DIAGNOSTICS (2026-09-07) ──
        # Capture tackle_role / depth_side / target_source so the LATE_CHASE
        # histogram can attribute which writer path (commit / spacing /
        # structured_close / mmdl_CHASE) left the CHASE body on a collapsed
        # target while the state machine reported CHASE.
        f" tackle={getattr(d, 'tackle_role', None)} "
        f"side={getattr(d, 'depth_side', None)} "
        f"src={getattr(d, 'target_source', None)}"
    )


def probe_chase_velocity(
    player,
    *,
    target_x,
    target_y,
    state_name,
    dx,
    dy,
    dist,
    dir_x,
    dir_y,
    desired_vx,
    desired_vy,
    final_vx,
    final_vy,
    ceiling,
) -> None:
    """Log one apply_movement_state CHASE physics snapshot."""
    if not probe_on():
        return
    if state_name != "CHASE":
        return
    x_gap = abs(target_x - player.x)
    flag = ""
    if x_gap > 5.0 and abs(final_vx) < 0.5:
        # LATE_CHASE: the CHASE body is too slow to close the X gap — a
        # spacing-authority collapse.  The writer-role fields (tackle_role /
        # depth_side / target_source) are on the base STATE line; this marker
        # identifies the row as a LATE_CHASE event for the histogram.
        flag = "  <-- CHASE_X_COLLAPSE_TOO_SLOW"
    frame = getattr(player, "_probe_frame", None)
    print(
        f"STATE f={frame} id={getattr(player, 'player_id', '?')} "
        f"state={state_name} x={player.x:.3f} tx={target_x:.3f} "
        f"dx={dx:.3f} dy={dy:.3f} dist={dist:.3f} "
        f"dir_x={dir_x:.4f} dir_y={dir_y:.4f} "
        f"des_vx={desired_vx:.3f} des_vy={desired_vy:.3f} "
        f"vx={final_vx:.3f} vy={final_vy:.3f} ceiling={ceiling:.3f}"
        f" tackle={getattr(player, 'tackle_role', None)} "
        f"side={getattr(player, 'depth_side', None)} "
        f"src={getattr(player, 'target_source', None)}"
        f"{flag}"
    )


def _self_test() -> None:
    """Lightweight smoke test for both probe sinks (no engine boot)."""

    class _D:
        def __init__(self):
            self.player_id = 7
            self.x = 42.0
            self.y = 30.0
            self.defender_role = type("R", (), {"name": "DEF_LINE_READY"})()
            self.depth_role = "B"
            self.depth_class = "PRIMARY"
            self.role_depth_class = "BALANCED"
            self.tackle_role = "PRIMARY"
            self.depth_side = -1
            self.target_source = "mmdl_CHASE"
            self.vx = 0.1
            self.vy = 1.5
            self._probe_frame = 0

    class _W:
        frame_count = 0

    d = _D()
    probe_mmdl_chase(
        _W(), d,
        target_x_before_guard=12.0,
        target_x_after_guard=52.0,
        anchor_x_guard=52.0,
        state_name="CHASE",
        snap_applied=True,
    )

    dx = 12.5 - d.x
    dy = 31.0 - d.y
    dist = math.hypot(dx, dy)
    nx, ny = dx / dist, dy / dist
    lat = 0.3
    dir_x = nx * (1.0 - lat)
    dir_y = ny * lat + nx * (1.0 - abs(nx)) * (1.0 - lat) * 0.3
    dl = math.hypot(dir_x, dir_y)
    dir_x, dir_y = dir_x / dl, dir_y / dl
    speed = 8.3 * 0.9
    probe_chase_velocity(
        d,
        target_x=12.5,
        target_y=31.0,
        state_name="CHASE",
        dx=dx,
        dy=dy,
        dist=dist,
        dir_x=dir_x,
        dir_y=dir_y,
        desired_vx=dir_x * speed,
        desired_vy=dir_y * speed,
        final_vx=0.0,
        final_vy=0.0,
        ceiling=7.47,
    )


if __name__ == "__main__":
    _self_test()
