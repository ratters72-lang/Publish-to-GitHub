"""world/rules/exit_timing_budget.py — Stage 4B EXIT-ERA TIMING BUDGET.

WHY THIS MODULE EXISTS (2026-09-19)
-----------------------------------
The TES → line exit boundary is gated by FIVE independent clocks, and until
now they had no single declared source.  Two of them were imported, two
lived in the engine as module constants, and three were FUNCTION-LOCAL
literals re-created on every call:

    RELEASE      floor  RELEASE_MIN_CONVERGENCE_S       (release_gate)
                 cap    RELEASE_MAX_DURATION_S          (release_gate)
                        consumed only INDIRECTLY via the derived frame
                        count RELEASE_WATCHDOG_FRAMES in open_play_engine
    PTB_PREP     cap    PTB_PREP_MAX_S                  (game.tes.ptb_manager)
    PTB_EXEC     cap    PTB_EXEC_MAX_S                  (game.tes.ptb_manager)
    PTB          cap    PTB_TIMEOUT_S = 2.0             *** function-local ***
                 duck   DUCKOUT_DISTANCE = 2.0          *** function-local ***
    POST_RUCK    cap    POST_RUCK_DURATION_S = 0.05     *** function-local ***

A budget audit cannot be written against a literal that only exists inside a
function body, so the exit era had no auditable envelope.  This module is
that envelope: the ONE declared statement of every exit-era bound, derived
from the owning authorities wherever they already exist (never duplicated),
plus the derived frame counts and the worst-case total.

IMPORT SAFETY
-------------
The two live authorities are imported, not copied, so drift is impossible:
``world.rules.release_gate`` (RELEASE bounds) and ``game.tes.ptb_manager``
(PTB_PREP / PTB_EXEC bounds).  Both are already imported by
``world/open_play_engine.py``, and neither imports this module, so this adds
no cycle.  ``world.rules.release_gate`` imports only ``player.player``.

CONTRACT
--------
This module is DECLARATIVE: it defines no behaviour and is consumed by the
exit-boundary audit only.  ``PTB_TIMEOUT_S`` / ``POST_RUCK_DURATION_S`` /
``PTB_DUCKOUT_DISTANCE_M`` here are the canonical values the engine's phase
handlers must use; the handlers previously carried private copies of the
same numbers.
"""

from __future__ import annotations

# ── LIVE AUTHORITIES (imported, never copied) ────────────────────────
from world.rules.release_gate import (
    RELEASE_MIN_CONVERGENCE_S,
    RELEASE_MAX_DURATION_S,
)
from game.tes.ptb_manager import (
    PTB_PREP_MAX_S,
    PTB_EXEC_MAX_S,
)

__all__ = [
    "TICK_HZ",
    "RELEASE_MIN_CONVERGENCE_S",
    "RELEASE_MAX_DURATION_S",
    "PTB_PREP_MAX_S",
    "PTB_EXEC_MAX_S",
    "PTB_TIMEOUT_S",
    "PTB_DUCKOUT_DISTANCE_M",
    "POST_RUCK_DURATION_S",
    "RELEASE_WATCHDOG_FRAMES",
    "PTB_PREP_WATCHDOG_FRAMES",
    "PTB_EXEC_WATCHDOG_FRAMES",
    "EXIT_BUDGET_ORDER",
    "EXIT_BUDGET_S",
    "total_exit_budget_s",
    "watchdog_frames",
    "declared_budget_s",
    "format_budget",
]

# The frame rate every derived frame count assumes.  The engine's watchdog
# counts are recomputed from these seconds at this rate.
TICK_HZ: int = 60

# ── PTB-ERA BOUNDS (were function-local literals) ─────────────────────
# PTB: ends on the DH duck-out (|dx| or |dy| > this from the ruck) OR the
# hard timeout, whichever comes first.  The handler re-created both numbers
# inside _update_ptb() every call.
PTB_TIMEOUT_S: float = 2.0
PTB_DUCKOUT_DISTANCE_M: float = 2.0

# POST_RUCK: the phase flips to OPEN_PLAY on all_compliant OR this cap.
POST_RUCK_DURATION_S: float = 0.05


# ── DERIVED FRAME COUNTS (@ TICK_HZ) ─────────────────────────────────
def watchdog_frames(seconds: float) -> int:
    """Return the frame count for *seconds* at TICK_HZ (min 1).

    One definition so the engine's watchdog constants are derivable from
    these seconds instead of being independently rounded.
    """
    return max(1, int(round(float(seconds) * TICK_HZ)))


# RELEASE's watchdog is the MAX-DURATION cap MINUS the convergence floor:
# the floor must elapse before any transition is legal, so the countdown
# only starts after it.
RELEASE_WATCHDOG_FRAMES: int = watchdog_frames(
    RELEASE_MAX_DURATION_S - RELEASE_MIN_CONVERGENCE_S
)
PTB_PREP_WATCHDOG_FRAMES: int = watchdog_frames(PTB_PREP_MAX_S)
PTB_EXEC_WATCHDOG_FRAMES: int = watchdog_frames(PTB_EXEC_MAX_S)

# ── THE ORDERED ENVELOPE ─────────────────────────────────────────────
# The exit era advances through these sub-phases in this order; the list is
# the audit's iteration order and the total's summation order.
EXIT_BUDGET_ORDER = (
    "RELEASE",
    "PTB_PREP",
    "PTB_EXEC",
    "PTB",
    "POST_RUCK_OPEN_PLAY",
)

# Worst-case wall-clock per sub-phase.  RELEASE already includes its floor
# (the cap is the total, not the watchdog, because the watchdog fires at
# cap - floor and the phase began at 0).
EXIT_BUDGET_S: dict = {
    "RELEASE": float(RELEASE_MAX_DURATION_S),
    "PTB_PREP": float(PTB_PREP_MAX_S),
    "PTB_EXEC": float(PTB_EXEC_MAX_S),
    "PTB": float(PTB_TIMEOUT_S),
    "POST_RUCK_OPEN_PLAY": float(POST_RUCK_DURATION_S),
}


def total_exit_budget_s() -> float:
    """Worst-case total wall clock for one TES → line exit era."""
    return float(sum(EXIT_BUDGET_S[name] for name in EXIT_BUDGET_ORDER))


def declared_budget_s(subphase: str) -> float | None:
    """Declared worst-case seconds for *subphase* (None when unknown)."""
    return EXIT_BUDGET_S.get(str(subphase).upper())


def format_budget() -> str:
    """One-line rendering of the whole envelope (logging only)."""
    parts = [
        "%s<=%.2f" % (name.lower(), EXIT_BUDGET_S[name])
        for name in EXIT_BUDGET_ORDER
    ]
    return "%s | total<=%.2fs watchdog_frames(release=%d prep=%d exec=%d)" % (
        " ".join(parts),
        total_exit_budget_s(),
        RELEASE_WATCHDOG_FRAMES,
        PTB_PREP_WATCHDOG_FRAMES,
        PTB_EXEC_WATCHDOG_FRAMES,
    )
