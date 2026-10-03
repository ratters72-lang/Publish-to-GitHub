# game/tes/tes_state.py
"""Pure enums for TES — no FSM transitions, no events."""

from enum import Enum


class TESPhase(Enum):
    """Lifecycle phases of a tackle engagement (spec-correct chain)."""
    IDLE = "idle"
    ENGAGE = "engage"
    CONTACT_START = "contact_start"
    CONTACT = "contact"
    DRIVE = "drive"
    DOMINANCE = "dominance"
    RELEASE = "release"
    PTB_PREP = "ptb_prep"
    PTB_EXEC = "ptb_exec"
    DH_PLAY = "dh_play"
    RELEASED = "released"


class TackleResult(Enum):
    """Outcome classification of a completed tackle."""
    DOMINANT = "dominant"
    NEUTRAL = "neutral"
    WEAK = "weak"
    STRIP = "strip"
    FUMBLE = "fumble"
    PENALTY = "penalty"
    SIX_AGAIN = "six_again"
    LINE_BREAK = "line_break"
    BREAKING = "breaking"
    SLIPPING = "slipping"
    BROKEN = "broken"
