"""
game/debug/def_line_ready_audit.py — Per-frame DEF_LINE_READY audit dump.

PURPOSE
-------
Diagnostic (NOT behaviour) that, during TACKLE_DRIVE / TACKLE_DOMINANCE,
emits ONE line per defender every frame (self-limited), showing the exact
state of the DEF_LINE_READY pre-tackle-attempt contract:

    [DRIVE-AUDIT] f=123 phase=TACKLE_DRIVE id=201 role=DEF_LINE_READY
      depth=B elig=True pta=SLOW follow=True frozen=False dx=0.42
      src=pretackle_attempt

Fields (all read from the REAL engine state at the end of the frame):
  - elig    : is_pretackle_eligible(d)  — DEF_LINE_READY, unfrozen, not FB
  - pta     : is_pretackle_managed(d) state (COMMIT/PLANTED/SLOW/HOLD) or "-"
  - follow  : the body is driven by a follow integrator this frame
              (target_source in ("tes_drive_shadow", "pretackle_attempt")
              and not frozen) — the same semantic as the literal "FOLLOW"
              status ruck_adjacent_pin emits during DRIVE/DOMINANCE
  - frozen  : bool(d.frozen)
  - dx      : X-axis error to the current target (abs(target_x - x)) — the
              gap the follow must close
  - src     : target_source

No behaviour.  Purely observational — reads fields, never writes them.

SELF-LIMITING: prints at most AUDIT_MAX_DRIVE_FRAMES frames of
DRIVE/DOMINANCE state, then goes silent.  Call rearm_def_line_ready_audit()
to re-arm for another capture run.  Gate:
    module DEBUG_DEF_LINE_READY_AUDIT (default True)
    env  RLM_DEF_LINE_READY_AUDIT=0 -> disabled
"""

import os

from game.defence.defence_lvl_1.pretackle_attempt import (
    is_pretackle_eligible,
    is_pretackle_managed,
    STATE_ATTR,
    PTA_TARGET_SOURCE,
)

# ── Audit gate ───────────────────────────────────────────────────────
DEBUG_DEF_LINE_READY_AUDIT: bool = (
    os.environ.get("RLM_DEF_LINE_READY_AUDIT", "1") not in ("", "0")
)
# Print budget: per-frame dump during this many TACKLE_DRIVE/DOMINANCE
# frames, then silent (mirrors defender_state_audit).
AUDIT_MAX_DRIVE_FRAMES: int = 10

# The one legacy source + the PTA source are the two follow/integrator
# owners during a drive.
_FOLLOW_SOURCES = frozenset({"tes_drive_shadow", PTA_TARGET_SOURCE})

_printed_frames: int = 0


def rearm_def_line_ready_audit() -> None:
    """Reset the per-drive print budget for another capture run."""
    global _printed_frames
    _printed_frames = 0


def _follow_active(d) -> bool:
    """True when the body is currently driven by a follow integrator."""
    if bool(getattr(d, "frozen", False)):
        return False
    return getattr(d, "target_source", None) in _FOLLOW_SOURCES


def _fmt(value, nd=2) -> str:
    try:
        return f"{float(value):.{nd}f}"
    except (TypeError, ValueError):
        return str(value)


def dump_def_line_ready_audit(world, defenders, bc, phase) -> None:
    """Emit one per-defender DEF_LINE_READY audit line for this frame.

    Args:
        world: engine world object (or stub).
        defenders: list of defending-team actors (the TES-drive non-tackler
            set the manager passed in).
        bc: ball-carrier actor (may be None).
        phase: the live phase label (e.g. "TACKLE_DRIVE", "TACKLE_DOMINANCE").

    No-op when the gate is off or the print budget is exhausted.  Reads
    only; never writes a field.
    """
    global _printed_frames

    if not DEBUG_DEF_LINE_READY_AUDIT:
        return
    if _printed_frames >= AUDIT_MAX_DRIVE_FRAMES:
        return

    _printed_frames += 1
    frame = getattr(world, "frame_count", 0)

    print(f"== [DRIVE-AUDIT] f={frame} phase={phase} "
          f"bc_x={_fmt(getattr(bc, 'x', 0.0))} bc_y={_fmt(getattr(bc, 'y', 0.0))} ==")

    for d in defenders:
        pid = getattr(d, "player_id", "?")
        role = getattr(d, "defender_role", None)
        role_name = getattr(role, "name", None) or getattr(role, "value", None) or str(role)
        depth_role = getattr(d, "depth_role", None) or "-"
        elig = is_pretackle_eligible(d)
        pta_state = getattr(d, STATE_ATTR, None) if is_pretackle_managed(d) else "-"
        follow = _follow_active(d)
        frozen = bool(getattr(d, "frozen", False))
        tx = getattr(d, "target_x", None)
        x = getattr(d, "x", 0.0)
        dx = abs(float(tx) - float(x)) if tx is not None else 0.0
        src = getattr(d, "target_source", None) or "-"

        print(
            f"[DRIVE-AUDIT] f={frame} phase={phase} id={pid} "
            f"role={role_name} depth={depth_role} "
            f"elig={elig} pta={pta_state} follow={follow} "
            f"frozen={frozen} dx={dx:.2f} src={src}"
        )

    print("== [DRIVE-AUDIT] END ==")
