"""
player/movement_constants.py — Single Source of Truth for Movement Physics Tuning

Phase 2A repair: These constants were previously embedded inside TES (tes_dominance.py),
creating a circular dependency where TES both defined and consumed movement physics.
They are relocated here so that:
  - Movement physics lives in the movement module
  - TES is a consumer (imports from here)
  - Any system (defence spacing, AI, collision) can import the same values

Recommended values (Phase 2A final):
    ANGLE_DELTA_MAX      = 0.018   — stops twitchy turning, preserves low-speed responsiveness
    VELOCITY_DELTA_MAX   = 0.095   — removes jitter/stutter, acceleration feels heavy
    EMA_SMOOTHING_FACTOR = 0.22    — kills input noise, preserves responsiveness
    MASTER_MOVEMENT_SCALER = 0.90  — grounded feel, reduces floatiness
"""

# ── Angle change cap per frame ──────────────────────────────────────
# Prevents 90° snaps at high speed. Gives TES clean facing data.
ANGLE_DELTA_MAX: float = 0.018

# ── Velocity change cap per frame ───────────────────────────────────
# Prevents velocity spikes that destabilise TES stability scoring.
# Matches engine speed scale (max ~7.5 m/s).
VELOCITY_DELTA_MAX: float = 0.095

# ── Exponential Moving Average smoothing factor ─────────────────────
# Applied to tackle_score in TES waveform update.
#   score = raw * factor + prev * (1 - factor)
# Strong enough to kill input noise, weak enough to preserve responsiveness.
EMA_SMOOTHING_FACTOR: float = 0.22

# ── Global movement scaler (master weight) ──────────────────────────
# Applied to the combined delta in TES waveform update.
# Makes movement feel grounded and heavy; prevents over-responsiveness
# that breaks TES dominance scoring.
MASTER_MOVEMENT_SCALER: float = 0.90

# ── Collapse threshold ────────────────────────────────────────────
# When ctx.stability drops below this value, ctx.collapsed is set True.
# Collapse indicates the attacker has lost structural control (feet, balance,
# forward drive), which feeds into dominance computation as a defender advantage.
COLLAPSE_THRESHOLD: float = 0.3
