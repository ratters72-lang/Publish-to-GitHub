"""
game/debug/debug_utils.py — Clean debug signals.

The five signals used to isolate kickoff-teleport:

  1. BC position each frame           → dbg_bc_track()
  2. BC writes (frame + phase + sub)  → BC_X_WRITE in player.x setter
  3. Ruck writes                      → RUCK_X_WRITE in world.ruck_x_m setter
  4. TES entry                        → dbg_tes_start()
  5. TES snapshot                     → drive_start_x snapshot in tes.py

BLOCK 1 (legacy tackle-audit cleanup): the tackle/ruck/drive prints below
were written for the old tackle geometry and produced misleading readings.
They are DISABLED by default.  Flip any flag to True ONLY for isolated
diagnosis.
"""


# ═══════════════════════════════════════════════════════════════════════
# LEGACY TACKLE-AUDIT DIAGNOSTIC FLAGS (Block 1 cleanup)
# ═══════════════════════════════════════════════════════════════════════
DEBUG_BC_TRACK: bool = False
DEBUG_TES_START: bool = False
DEBUG_RUCK_WRITE: bool = False


def dbg_bc_track(world):
    """Print BC position + ruck_x for the current frame (disabled by default)."""
    if not DEBUG_BC_TRACK:
        return
    bc = world.ball_carrier
    if bc:
        print(
            f"[BC_TRACK] frame={world.frame} "
            f"bc.x={bc.x:.3f} ruck_x={world.ruck_x_m:.3f} "
            f"phase={world.phase} sub={world.sub_state}",
            flush=True,
        )


def dbg_tes_start(bc, primary, ctx):
    """Print TES entry diagnostics (disabled by default)."""
    if not DEBUG_TES_START:
        return
    print(
        f"[TES_START] bc={bc.player_id} primary={primary.player_id}",
        flush=True,
    )
    print(
        f"[BC_POS_BEFORE_TES] x={bc.x:.3f} y={bc.y:.3f}",
        flush=True,
    )
    print(
        f"[SNAPSHOT] drive_start_x={ctx.drive_start_x:.3f}",
        flush=True,
    )


def dbg_ruck_write(old, new):
    """Print ruck_x_m write (disabled by default)."""
    if not DEBUG_RUCK_WRITE:
        return
    print(
        f"[RUCK_X_WRITE] old={old:.3f} new={new:.3f}",
        flush=True,
    )
