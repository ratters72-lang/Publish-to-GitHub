"""
game/defence/defence_lvl_1/fullback_locomotion.py — DEDICATED FULLBACK
LOCOMOTION (Stage 4 of the Fullback Controller).

THE LAW
-------
A tagged fullback on a LIVE BREAK or an OWED-GROUND RECOVERY steers under a
FULLBACK locomotion contract, resolved from its INTENT — not under the shared
defender tables (``ACCEL_BY_STATE`` / ``MAX_SPEED_BY_STATE`` /
``LATERAL_PRIORITY_BY_STATE`` / ``FACE_BC_BY_STATE``) and not under the
line-defence approach priorities.  When neither door is open the shared
tables keep the body, so the sweeper glide and the kickoff flight are
byte-identical.

WHY A SEPARATE MODULE
---------------------
``fullback_controller`` owns the VOCABULARY and the DECISION ("is this frame
the fullback's to steer?"); this leaf owns the ARITHMETIC ("what are the
pursuit / recovery numbers?").  Splitting them is what lets the numbers be
declared, audited and A/B'd on their own, and what lets the controller stay a
pure decision module with no locomotion constants in it.

INDEPENDENCE, AND WHY THE PROFILES ARE SNAPSHOTS
------------------------------------------------
The shared numeric parameters are captured ONCE, at import. The steering
switches deliberately differ where a fullback must travel to its own point.
That has two consequences:

  * TODAY THEY ARE THE SAME NUMBERS the shared row for that movement state
    carries. The fullback-only ``intercept_steer`` and
    ``alignment_damping`` switches provide direct two-axis pursuit and
    recovery to the owned target.

  * THEY DO NOT MOVE WHEN THE SHARED TABLE MOVES.  A later tuner that
    re-tunes ``LATERAL_PRIORITY_BY_STATE`` for the LINE (a SLIDE tweak, a
    DRIFT tweak) cannot silently re-steer the last defender's pursuit or its
    recovery.  That is the independence the stage asserts, and it is
    falsifiable: mutate the shared row and the fullback's profile is
    unchanged.

THE INTENTS
-----------
    CHASE    emergency PURSUIT and INTERCEPTION.  Direct bearing to the
             commanded point (bounded BC trajectory projection on both axes,
             with no trailing offset and an in-goal X destination; see
             ``fullback_role.fullback_chase_point``), so no line-defender
             lateral skew is spent on it, and the pre-tackle lateral
             alignment controls are not applied.
    RECOVER  owed-ground RECOVERY STEERING.  Travel-home pace and direct
             bearing to the owned X/Y anchor, without pre-tackle alignment
             clamps redirecting the return toward the ball carrier.

``profile_for_intent`` returns ``None`` for every other intent — ``SWEEP``,
``FLIGHT`` and ``UNOWNED`` are answered with the shared tables, which is what
keeps the sweeper and the airborne anchor on the contract they already had.
"""

from __future__ import annotations

from typing import Optional

from player.movement_states import (
    MovementState,
    ACCEL_BY_STATE,
    DECEL_BY_STATE,
    MAX_SPEED_BY_STATE,
    LATERAL_PRIORITY_BY_STATE,
    FACE_BC_BY_STATE,
)

from game.defence.defence_lvl_1.fullback_controller import FullbackIntent


# ═══════════════════════════════════════════════════════════════════════
# THE PROFILE FIELDS
# ═══════════════════════════════════════════════════════════════════════
# The complete vocabulary of a locomotion profile.  ``apply_movement_state``
# reads exactly these five, plus the two fullback-only steering switches this
# module adds on top:
#
#   accel             m/s^2 ramp (the shared attribute, resolved here)
#   decel             m/s^2 ramp for the HOLD branch (carried for totality)
#   max_speed_scale   fraction of max_speed_mps (the top-speed table)
#   lateral_priority  0 = bearing-only, 1 = lateral-only (the steering skew)
#   face_bc           True = face the carrier, False = face the direction
#   intercept_steer   True = steer at the COMMANDED POINT on both axes and
#                     skip the pre-tackle lateral alignment controls
#   alignment_damping False = the pre-tackle lateral alignment controls
#                     (items 1+2+3+5 of player/collapse_controls.py) must not
#                     damp this body's lateral command
LOCOMOTION_FIELDS: tuple = (
    "accel",
    "decel",
    "max_speed_scale",
    "lateral_priority",
    "face_bc",
    "intercept_steer",
    "alignment_damping",
)

# The intent -> movement state the profile is the locomotion half of.  Read
# for the neutrality reader below and for the report; the DRIVER never consults
# it (the controller resolves the intent, the movement layer applies the
# profile), so this is a documentation/audit edge and not a second decision.
_PROFILE_STATE: dict = {
    FullbackIntent.CHASE: MovementState.CHASE,
    FullbackIntent.RECOVER: MovementState.RECOVER,
}


def _shared(field: str, state: MovementState, default: float):
    """One shared-table reading, total.

    The tables are complete for every ``MovementState`` (the movement module
    declares them unconditionally), so the ``default`` arm exists only so a
    stub or a partially-loaded table can never raise at import.
    """
    table = {
        "accel": ACCEL_BY_STATE,
        "decel": DECEL_BY_STATE,
        "max_speed_scale": MAX_SPEED_BY_STATE,
        "lateral_priority": LATERAL_PRIORITY_BY_STATE,
        "face_bc": FACE_BC_BY_STATE,
    }.get(field)
    if table is None:
        return default
    value = table.get(state, default)
    return default if value is None else value


# ═══════════════════════════════════════════════════════════════════════
# THE PROFILES
# ═══════════════════════════════════════════════════════════════════════
# captured at import (see INDEPENDENCE above).  ``intercept_steer`` and
# ``alignment_damping`` are the two fullback-only switches. Both CHASE and
# RECOVER travel to an owned point on both axes; pre-tackle alignment clamps
# are for line defenders approaching the carrier, not the fullback returning
# to its positional anchor.
FB_LOCOMOTION_PROFILES: dict = {
    FullbackIntent.CHASE: {
        "intent": FullbackIntent.CHASE,
        "state": MovementState.CHASE,
        "accel": _shared("accel", MovementState.CHASE, 6.0),
        "decel": _shared("decel", MovementState.CHASE, 8.0),
        "max_speed_scale": _shared(
            "max_speed_scale", MovementState.CHASE, 0.9
        ),
        "lateral_priority": _shared(
            "lateral_priority", MovementState.CHASE, 0.3
        ),
        "face_bc": bool(_shared("face_bc", MovementState.CHASE, False)),
        # THE COMMANDED POINT IS AN INTERCEPTION.  ``fullback_role.
        # fullback_chase_point`` targets the carrier's bounded projected XY
        # position directly, with no trail and an in-goal X destination; the
        # lead tapers at contact range. The fullback travels directly to it.
        "intercept_steer": True,
        "alignment_damping": False,
    },
    FullbackIntent.RECOVER: {
        "intent": FullbackIntent.RECOVER,
        "state": MovementState.RECOVER,
        "accel": _shared("accel", MovementState.RECOVER, 3.0),
        "decel": _shared("decel", MovementState.RECOVER, 3.0),
        "max_speed_scale": _shared(
            "max_speed_scale", MovementState.RECOVER, 0.5
        ),
        "lateral_priority": _shared(
            "lateral_priority", MovementState.RECOVER, 0.25
        ),
        "face_bc": bool(_shared("face_bc", MovementState.RECOVER, False)),
        "intercept_steer": True,
        "alignment_damping": False,
    },
}


def profile_for_intent(intent) -> Optional[dict]:
    """The locomotion profile for *intent*, or ``None`` for the shared tables.

    ``None`` is the answer for ``SWEEP`` (the sweeper anchor glide),
    ``FLIGHT`` (the airborne anchor) and ``UNOWNED`` (a yielded body), and it
    means "the shared movement tables keep this body" — so those frames are
    byte-identical to the pre-stage engine.

    Total: an unhashable / unrecognised intent reports ``None`` rather than
    raising, which degrades to the shared tables (the safe answer).
    """
    try:
        return FB_LOCOMOTION_PROFILES.get(intent)
    except TypeError:
        return None


def locomotion_profile_is_shared_equivalent(intent) -> bool:
    """True when *intent*'s profile carries the shared row's own numbers.

    The neutrality reader: a profile whose five shared fields equal the
    shared movement table's row for the same state adds NOTHING but the
    fullback-only steering switches, which is what makes the shipped default
    behaviour-neutral.  False for an intent with no profile.

    Pure and total.  Kept here (not in the controller) because it is a
    statement about the ARITHMETIC, not about the decision.
    """
    profile = profile_for_intent(intent)
    if profile is None:
        return False
    state = profile.get("state")
    for field in ("accel", "decel", "max_speed_scale",
                  "lateral_priority", "face_bc"):
        if profile.get(field) != _shared(field, state, profile.get(field)):
            return False
    return True


__all__ = [
    "FB_LOCOMOTION_PROFILES",
    "LOCOMOTION_FIELDS",
    "profile_for_intent",
    "locomotion_profile_is_shared_equivalent",
]
