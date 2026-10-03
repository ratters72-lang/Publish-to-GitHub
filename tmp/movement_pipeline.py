"""
tmp/movement_pipeline.py — MOVEMENT_PIPELINE definition.

This file defines the explicit ordering of movement subsystems
per phase. It is the canonical contract for which subsystem runs
when, imported by open_play_engine.py.
"""

# Stage names:
#   "handler"      — phase-specific handler (e.g. _update_tackle_drive)
#   "defence"      — _update_defence() geometry pipeline
#   "attack"       — _update_attack() BC + support runner movement
#   "role_pipeline" — apply_role_movement() + arrival_predictor()
#   "sdm"          — _apply_defence_sdm() deterministic retreat
#   "freeze"       — freeze-state compliance enforcement (zero vx, pin targets)
#   "markers"      — _move_markers_to_targets()
#   "ptb"          — PTBManager DH movement (PTB_PREP/EXEC only)
#
# CRITICAL: Changing the order here changes simulation behaviour.
# Every reordering MUST be validated against PHASE_RULES invariants.

MOVEMENT_PIPELINE: dict[str, list[str]] = {
    # OPEN_PLAY: defence (geometry) -> attack -> handler -> SDM -> freeze
    "OPEN_PLAY":             ["defence", "attack", "handler", "sdm", "freeze"],

    # TES phases: handler (TES drive) -> role_pipeline -> SDM -> freeze
    "TACKLE_INIT":           ["handler", "role_pipeline", "sdm", "freeze"],
    "TACKLE_CONTACT":        ["handler", "role_pipeline", "sdm", "freeze"],
    "TACKLE_DRIVE":          ["handler", "role_pipeline", "sdm", "freeze"],
    "TACKLE_DOMINANCE":      ["handler", "role_pipeline", "sdm", "freeze"],

    # RELEASE: handler (unfreeze markers, detach) -> role_pipeline -> markers -> SDM -> freeze
    "RELEASE":               ["handler", "role_pipeline", "markers", "sdm", "freeze"],

    # PTB phases: markers -> handler (PTBManager) -> role_pipeline -> SDM -> freeze
    "PTB_PREP":              ["markers", "handler", "role_pipeline", "sdm", "freeze"],
    "PTB_EXEC":              ["markers", "handler", "role_pipeline", "sdm", "freeze"],
    "PTB":                   ["markers", "handler", "role_pipeline", "sdm", "freeze"],

    # POST_RUCK_OPEN_PLAY: defence (retreat) -> attack -> handler -> SDM -> freeze
    "POST_RUCK_OPEN_PLAY":   ["defence", "attack", "handler", "sdm", "freeze"],
}
