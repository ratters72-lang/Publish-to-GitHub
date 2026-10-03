"""Verify Phase 2A import chain — TES dominance + movement constants."""
from game.tes.tes_dominance import (
    update_waveform,
    compute_dominance,
    compute_drive_distance,
    compute_tackle_duration,
    compute_ptb_time,
    _compute_angle_delta,
)
from game.tes.tes_context import TESContext
from game.tes.tes_outcomes import TESOutcome
from player.movement_constants import (
    ANGLE_DELTA_MAX,
    VELOCITY_DELTA_MAX,
    EMA_SMOOTHING_FACTOR,
    MASTER_MOVEMENT_SCALER,
)

print("✓ All Phase 2A imports OK")
print(f"  ANGLE_DELTA_MAX      = {ANGLE_DELTA_MAX}")
print(f"  VELOCITY_DELTA_MAX   = {VELOCITY_DELTA_MAX}")
print(f"  EMA_SMOOTHING_FACTOR = {EMA_SMOOTHING_FACTOR}")
print(f"  MASTER_MOVEMENT_SCALER = {MASTER_MOVEMENT_SCALER}")
print(f"  angle_delta(10°)     = {_compute_angle_delta(10):.4f}")
print(f"  angle_delta(90°)     = {_compute_angle_delta(90):.4f}")
print()
print("All constants live in player/movement_constants.py")
print("TES consumes from player.movement_constants — no circular dependency.")
