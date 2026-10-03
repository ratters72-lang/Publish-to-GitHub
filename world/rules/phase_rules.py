"""
world/rules/phase_rules.py — Canonical phase-role-freeze table (AI contract).

This is the single source of truth for freeze/movement permissions.
It mirrors MOVEMENT_RULES in movement_contracts.py but in pure-dict
form with string keys for consumption by log-hunter scripts.

Usage:
    from world.rules.phase_rules import PHASE_RULES
    rules = PHASE_RULES.get(phase, {})
    must_frozen: list[str] = rules.get("must_be_frozen", [])
    allowed: list[str] = rules.get("allowed_to_move", [])
"""

# Phase-Role-Freeze table — authoritative contract for all subsystems
PHASE_RULES: dict[str, dict] = {
    # OPEN_PLAY / POST_RUCK_OPEN_PLAY
    # Every role is free to move. No frozen roles.
    "OPEN_PLAY": {
        "must_be_frozen": [],
        "allowed_to_move": [
            "DEF_LINE_READY", "DEF_CHASE", "ATT_CARRIER", "ATT_SUPPORT",
            "MARKER_A", "MARKER_B", "DUMMY_HALF", "DEF_RETREAT_10",
            "TACKLER_1", "TACKLER_2", "TACKLER_3", "NONE",
        ],
    },
    "POST_RUCK_OPEN_PLAY": {
        "must_be_frozen": [],
        "allowed_to_move": [
            "DEF_LINE_READY", "DEF_CHASE", "ATT_CARRIER", "ATT_SUPPORT",
            "MARKER_A", "MARKER_B", "DUMMY_HALF", "DEF_RETREAT_10",
            "TACKLER_1", "TACKLER_2", "TACKLER_3", "NONE",
        ],
    },

    # TES phases — only BC, tacklers, DH, and retreating defenders move
    "TACKLE_INIT": {
        "must_be_frozen": [
            "MARKER_A", "MARKER_B", "DEF_LINE_READY", "DEF_CHASE",
            "ATT_SUPPORT", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "TACKLER_1", "TACKLER_2", "TACKLER_3",
            "DUMMY_HALF", "DEF_RETREAT_10",
        ],
    },
    "TACKLE_CONTACT": {
        "must_be_frozen": [
            "MARKER_A", "MARKER_B", "DEF_LINE_READY", "DEF_CHASE",
            "ATT_SUPPORT", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "TACKLER_1", "TACKLER_2", "TACKLER_3",
            "DUMMY_HALF", "DEF_RETREAT_10",
        ],
    },
    "TACKLE_DRIVE": {
        "must_be_frozen": [
            "MARKER_A", "MARKER_B", "DEF_LINE_READY", "DEF_CHASE",
            "ATT_SUPPORT", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "TACKLER_1", "TACKLER_2", "TACKLER_3",
            "DUMMY_HALF", "DEF_RETREAT_10",
        ],
    },
    "TACKLE_DOMINANCE": {
        "must_be_frozen": [
            "MARKER_A", "MARKER_B", "DEF_LINE_READY", "DEF_CHASE",
            "ATT_SUPPORT", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "TACKLER_1", "TACKLER_2", "TACKLER_3",
            "DUMMY_HALF", "DEF_RETREAT_10",
        ],
    },

    # RELEASE — tacklers frozen, everyone else moves
    "RELEASE": {
        "must_be_frozen": [
            "TACKLER_1", "TACKLER_2", "TACKLER_3", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "DUMMY_HALF", "MARKER_A", "MARKER_B",
            "DEF_RETREAT_10", "DEF_LINE_READY", "DEF_CHASE", "ATT_SUPPORT",
        ],
    },

    # PTB phases — BC, DH, markers, and retreating defenders move
    "PTB_PREP": {
        "must_be_frozen": [
            "DEF_LINE_READY", "DEF_CHASE", "ATT_SUPPORT",
            "TACKLER_1", "TACKLER_2", "TACKLER_3", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "DUMMY_HALF", "MARKER_A", "MARKER_B",
            "DEF_RETREAT_10",
        ],
    },
    "PTB_EXEC": {
        "must_be_frozen": [
            "DEF_LINE_READY", "DEF_CHASE", "ATT_SUPPORT",
            "TACKLER_1", "TACKLER_2", "TACKLER_3", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "DUMMY_HALF", "MARKER_A", "MARKER_B",
            "DEF_RETREAT_10",
        ],
    },
    "PTB": {
        "must_be_frozen": [
            "DEF_LINE_READY", "DEF_CHASE", "ATT_SUPPORT",
            "TACKLER_1", "TACKLER_2", "TACKLER_3", "NONE",
        ],
        "allowed_to_move": [
            "ATT_CARRIER", "DUMMY_HALF", "MARKER_A", "MARKER_B",
            "DEF_RETREAT_10",
        ],
    },
}
