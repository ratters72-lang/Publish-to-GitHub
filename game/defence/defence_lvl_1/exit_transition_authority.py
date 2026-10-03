"""
exit_transition_authority.py — Stage 4B EXIT-BOUNDARY OWNERSHIP AUTHORITY.

WHAT THIS MODULE IS FOR
-----------------------
The TES → line transition is not one boundary, it is FIVE, and each has a
different declared owner (see EXIT_SUBPHASES).  This module is the single
declared *ownership map* for that window plus the read-only predicates an
audit needs to test the live frame against it.

It is PURE: it never writes a position, a target, a velocity, a freeze flag
or a world field.  Like ``world/rules/release_gate.py`` it only decides
whether what already happened was declared.  Every caller is an audit
(probe / verifier); no production path consumes it.

WHY A DEDICATED MAP (the two-channel problem)
---------------------------------------------
The engine carries TWO INDEPENDENT writer-attribution channels, and they do
NOT describe the same set of writers:

  ``player._target_writer``  set by ``game/debug/target_write_log.py``
      ``stamp_target_write()``.  A deliberately NON-behavioural field from
      the blessed ``TARGET_WRITERS`` set (which INCLUDES "ruck_pin" and
      "ope_markers").  Read by ``record_final_targets`` / the jitter audit.

  ``player.target_source``   set directly by writers, and explicitly NOT
      touched by ``stamp_target_write`` because it is read for BEHAVIOUR
      (e.g. ``target_source == "tes_drive_shadow"``).  Audited by
      ``phase_authority_guard.report_phase_authority``.

The last positional writer of an exit-era frame is ``ruck_adjacent_pin``.
It stamps ``slot._target_writer = "ruck_pin"`` (and calls
``slot_arbitration.update_slot_target``, which sets the same field) — so it
is INVISIBLE to ``phase_authority_guard``, whose legal-source tables do not
list "ruck_pin" at all (nor "ope_markers").  A writer that stamps only one
channel, or neither, is exactly where a hidden competing writer hides.

``ownership_channels(world, d)`` therefore reports BOTH channels verbatim,
and ``audit_exit_frame`` flags ``UNATTRIBUTED`` — a body whose value moved
and whose two channels agree on nothing declared.

DECLARED-VS-OBSERVED, NOT A TOOL FOR CONTROL
-------------------------------------------
An ``UNATTRIBUTED`` or ``UNDECLARED`` result is NOT automatically a defect:
several exit-era writers are known to write targets with NO stamp at all
(the RELEASE BC recovery stand-up write, the POST_RUCK per-defender retreat
write, and ``_assign_defensive_line_targets``).  Those are recorded as
findings, not failures, and the contract verifier decides which of them the
project wants to close.
"""

from __future__ import annotations

from player.player import DefenderRole

__all__ = [
    "EXIT_SUBPHASES",
    "EXIT_BODY_CLASSES",
    "UNATTRIBUTED",
    "EXIT_OWNERSHIP",
    "ATTRIBUTION_CHANNELS",
    "body_class",
    "declared_writers",
    "ownership_channels",
    "is_declared",
    "audit_exit_frame",
    "format_exit_report",
]

# ─────────────────────────────────────────────────────────────────────
# THE FIVE EXIT SUB-PHASES (+ the first true open-play frame)
# ─────────────────────────────────────────────────────────────────────
# "OPEN_PLAY_ENTRY" is not an engine phase label: it is the FIRST frame whose
# handler is _update_open_play after a POST_RUCK → OPEN_PLAY transition, i.e.
# the frame on which the line is handed back to the normal structure.  The
# probe detects it from the phase label changing into OPEN_PLAY.
EXIT_SUBPHASES = (
    "RELEASE",
    "PTB_PREP",
    "PTB_EXEC",
    "PTB",
    "POST_RUCK_OPEN_PLAY",
    "OPEN_PLAY_ENTRY",
)

# Body classes the exit boundary distinguishes.  Order matters: the first
# matching class wins (see ``body_class``), because an actor can satisfy
# several tests at once (e.g. a kick-chase body is also a line body).
EXIT_BODY_CLASSES = (
    "BALL_CARRIER",
    "FULLBACK",
    "MARKER",
    "TACKLER",
    "TACKLE_STATE_HOLDER",
    "DUMMY_HALF",
    "DEF_RETREAT_10",
    "CHASE",
    "LINE",
    "OTHER",
)

# Sentinel for "the value moved but NEITHER attribution channel names a
# writer".  Deliberately distinct from "the declared writer set is empty".
UNATTRIBUTED = "*UNATTRIBUTED*"

# The two attribution channels this module reads.  Kept as data (not inline
# getattr calls) so the probe and the verifier cannot drift apart.
ATTRIBUTION_CHANNELS = ("_target_writer", "target_source")

# ─────────────────────────────────────────────────────────────────────
# THE DECLARED MAP
# ─────────────────────────────────────────────────────────────────────
# EXIT_OWNERSHIP[subphase][body_class] = frozenset of BLESSED writer tags
# (from game/debug/target_write_log.TARGET_WRITERS) that may legitimately
# hold target ownership for that (subphase, body_class) pair on that
# sub-phase's frame.
#
# An empty frozenset means "no writer is declared — if this body's value
# moved, the write is unattributed by construction".  UNATTRIBUTED as a
# member means "an unstamped direct write is the EXPECTED, KNOWN form here"
# (recorded as a finding, not a violation).
#
# Tags are read from EITHER channel; ``ownership_channels`` exposes which.
_EXIT_OWNERSHIP_RAW: dict = {
    # ── RELEASE: _update_release's own movers ──
    # Markers: _move_markers_to_targets → stamp_target_write("ope_markers")
    # Retreat line: DefenceManager.apply_forced_retreat → "retreat_manager"
    # Fullback: _reassert_fullback → "fullback_geometry"
    # DH: the RELEASE P7 approach writer → "dh_preposition"
    # BC recovery: a DIRECT target write in _update_release with NO stamp.
    # Line: the ruck pin is the last writer → "ruck_pin" (_target_writer only)
    "RELEASE": {
        "BALL_CARRIER": frozenset({UNATTRIBUTED}),
        "FULLBACK": frozenset({"fullback_geometry", "fullback"}),
        "MARKER": frozenset({"ope_markers"}),
        "TACKLER": frozenset({"freeze_pin"}),
        "TACKLE_STATE_HOLDER": frozenset({"freeze_pin"}),
        "DUMMY_HALF": frozenset({"dh_preposition", "attack"}),
        "DEF_RETREAT_10": frozenset({"retreat_manager", "tes_support_retreat"}),
        "CHASE": frozenset({"freeze_pin"}),
        "LINE": frozenset({"ruck_pin", "retreat_manager"}),
        "OTHER": frozenset({"freeze_pin"}),
    },
    # ── PTB_PREP / PTB_EXEC: PTBManager owns the DH ──
    # The DH targets are written by PTBManager directly; no stamp call is
    # made on that path, so the DH is UNATTRIBUTED by design here.
    "PTB_PREP": {
        "BALL_CARRIER": frozenset({UNATTRIBUTED}),
        "FULLBACK": frozenset({"fullback_geometry", "fullback"}),
        "MARKER": frozenset({"ope_markers"}),
        "TACKLER": frozenset({"freeze_pin"}),
        "TACKLE_STATE_HOLDER": frozenset({"freeze_pin"}),
        "DUMMY_HALF": frozenset({UNATTRIBUTED, "dh_preposition"}),
        "DEF_RETREAT_10": frozenset({"retreat_manager", "tes_support_retreat"}),
        "CHASE": frozenset({"freeze_pin"}),
        "LINE": frozenset({"ruck_pin", "retreat_manager"}),
        "OTHER": frozenset({"freeze_pin"}),
    },
    "PTB_EXEC": {
        "BALL_CARRIER": frozenset({UNATTRIBUTED}),
        "FULLBACK": frozenset({"fullback_geometry", "fullback"}),
        "MARKER": frozenset({"ope_markers"}),
        "TACKLER": frozenset({"freeze_pin"}),
        "TACKLE_STATE_HOLDER": frozenset({"freeze_pin"}),
        "DUMMY_HALF": frozenset({UNATTRIBUTED, "dh_preposition"}),
        "DEF_RETREAT_10": frozenset({"retreat_manager", "tes_support_retreat"}),
        "CHASE": frozenset({"freeze_pin"}),
        "LINE": frozenset({"ruck_pin", "retreat_manager"}),
        "OTHER": frozenset({"freeze_pin"}),
    },
    # ── PTB: the DH is now the ball carrier under the attack controller ──
    "PTB": {
        "BALL_CARRIER": frozenset({"attack"}),
        "FULLBACK": frozenset({"fullback_geometry", "fullback"}),
        "MARKER": frozenset({"ope_markers"}),
        "TACKLER": frozenset({"freeze_pin"}),
        "TACKLE_STATE_HOLDER": frozenset({"freeze_pin"}),
        "DUMMY_HALF": frozenset({"attack"}),
        "DEF_RETREAT_10": frozenset({"retreat_manager", "tes_support_retreat"}),
        "CHASE": frozenset({"freeze_pin"}),
        "LINE": frozenset({"ruck_pin", "retreat_manager"}),
        "OTHER": frozenset({"freeze_pin"}),
    },
    # ── POST_RUCK: the per-defender retreat write is DIRECT (no stamp);
    #    _post_ruck_marker_cleanup → _assign_defensive_line_targets is also
    #    a direct write.  Both are UNATTRIBUTED by construction.
    "POST_RUCK_OPEN_PLAY": {
        "BALL_CARRIER": frozenset({"attack"}),
        "FULLBACK": frozenset({"fullback_geometry", "fullback"}),
        "MARKER": frozenset({"ope_markers", "ruck_pin"}),
        "TACKLER": frozenset({"freeze_pin"}),
        "TACKLE_STATE_HOLDER": frozenset({"freeze_pin"}),
        "DUMMY_HALF": frozenset({"attack"}),
        "DEF_RETREAT_10": frozenset({UNATTRIBUTED, "retreat_manager"}),
        "CHASE": frozenset({UNATTRIBUTED, "retreat_manager"}),
        "LINE": frozenset({UNATTRIBUTED, "ruck_pin", "retreat_manager"}),
        "OTHER": frozenset({UNATTRIBUTED, "freeze_pin"}),
    },
    # ── OPEN_PLAY_ENTRY: the line is handed back to the normal structure.
    #    The preamble publishes geometry but never writes targets; the
    #    structured OPEN_PLAY pipeline owns the line targets from here.
    "OPEN_PLAY_ENTRY": {
        "BALL_CARRIER": frozenset({"attack"}),
        "FULLBACK": frozenset({"fullback_geometry", "fullback"}),
        "MARKER": frozenset({"ope_markers"}),
        "TACKLER": frozenset({"freeze_pin"}),
        "TACKLE_STATE_HOLDER": frozenset({"freeze_pin"}),
        "DUMMY_HALF": frozenset({"attack", "dh_preposition"}),
        "DEF_RETREAT_10": frozenset({"retreat_manager", "tes_support_retreat"}),
        "CHASE": frozenset({"mmdl", "pursuit", "structured_close"}),
        "LINE": frozenset({
            "spacing", "structured_close", "retreat_manager",
            "mmdl", "pretackle", "tackler_commit", "ruck_pin",
        }),
        "OTHER": frozenset({"freeze_pin", "spacing"}),
    },
}

# Freeze the map so a consumer cannot mutate the declared contract.
EXIT_OWNERSHIP: dict = {
    sub: {cls: frozenset(tags) for cls, tags in classes.items()}
    for sub, classes in _EXIT_OWNERSHIP_RAW.items()
}

# ── PHASE-UNIVERSAL WRITERS (measured, not assumed) ──
# MEASURED (probe_stage4b_exit_ownership, 38 cycles): the single most common
# FIRST writer on an exit-era body is ``freeze_pin`` — the engine's
# freeze-compliance pass (movement_contracts + the update() tail) runs on
# EVERY frame in EVERY phase, so it may legitimately own any body in any
# exit sub-phase.  Declaring it per-body-class in the literal above would
# repeat the same fact ~60 times, so it is unioned in ONCE here.  The first
# draft of this map omitted it and the audit reported 21,222 undeclared
# writers — the map was wrong, the engine was not.
_PHASE_UNIVERSAL_WRITERS = frozenset({"freeze_pin"})
for _classes_by_sub in EXIT_OWNERSHIP.values():
    for _cls, _tags in _classes_by_sub.items():
        _classes_by_sub[_cls] = _tags | _PHASE_UNIVERSAL_WRITERS

_ROLE_NAMES = {
    DefenderRole.MARKER_A: "MARKER",
    DefenderRole.MARKER_B: "MARKER",
    DefenderRole.TACKLER_1: "TACKLER",
    DefenderRole.TACKLER_2: "TACKLER",
    DefenderRole.TACKLER_3: "TACKLER",
    DefenderRole.DUMMY_HALF: "DUMMY_HALF",
    DefenderRole.DEF_RETREAT_10: "DEF_RETREAT_10",
    DefenderRole.DEF_CHASE: "CHASE",
    DefenderRole.KICK_CHASE: "CHASE",
}


# ─────────────────────────────────────────────────────────────────────
# CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────

def _role_name(d) -> str | None:
    role = getattr(d, "defender_role", None)
    if role is None:
        return None
    return getattr(role, "name", None) or str(role)


def body_class(d, world) -> str:
    """Return the exit-boundary body class for *d*.

    First match wins, in EXIT_BODY_CLASSES order.  Never raises; an
    unrecognised body is "OTHER".
    """
    if d is None:
        return "OTHER"
    if d is getattr(world, "ball_carrier", None):
        return "BALL_CARRIER"
    if getattr(d, "fullback_role_active", False):
        return "FULLBACK"
    role = getattr(d, "defender_role", None)
    mapped = _ROLE_NAMES.get(role)
    if mapped is not None:
        return mapped
    name = _role_name(d)
    if name in ("MARKER_A", "MARKER_B"):
        return "MARKER"
    if name in ("TACKLER_1", "TACKLER_2", "TACKLER_3"):
        return "TACKLER"
    if bool(getattr(d, "tackle_state_active", False)):
        return "TACKLE_STATE_HOLDER"
    if name == "DUMMY_HALF":
        return "DUMMY_HALF"
    if name == "DEF_RETREAT_10":
        return "DEF_RETREAT_10"
    if name in ("DEF_CHASE", "KICK_CHASE"):
        return "CHASE"
    # A body is on the LINE when the single population predicate says so.
    # Function-local import: exit_transition_authority must stay free of a
    # module-scope dependency on population_authority.
    try:
        from game.defence.defence_lvl_1.population_authority import (
            is_line_body as _is_line_body,
        )
        if _is_line_body(d):
            return "LINE"
    except Exception:
        pass
    return "OTHER"


# ─────────────────────────────────────────────────────────────────────
# THE DECLARED WRITER READ
# ─────────────────────────────────────────────────────────────────────

def declared_writers(subphase: str, cls: str) -> frozenset:
    """Return the declared blessed writer tags for (subphase, cls)."""
    classes = EXIT_OWNERSHIP.get(str(subphase).upper())
    if not classes:
        return frozenset()
    return classes.get(cls, frozenset())


def ownership_channels(world, d) -> dict:
    """Return BOTH attribution channels for *d*, verbatim, plus a verdict.

    Returns::

        {"target_writer": str|None, "target_source": str|None,
         "primary": str|None, "unattributed": bool}

    ``primary`` is the first non-empty channel value in
    ATTRIBUTION_CHANNELS order — the value the engine's own audits would
    use.  ``unattributed`` is True when neither channel names a writer, in
    which case ``primary`` is None and the caller should treat the write as
    UNATTRIBUTED (the value may still have changed; nothing tagged it).
    """
    tw = getattr(d, "_target_writer", None) or None
    ts = getattr(d, "target_source", None) or None
    primary = tw or ts or None
    return {
        "target_writer": tw,
        "target_source": ts,
        "primary": primary,
        "unattributed": primary is None,
        "channels_disagree": bool(tw and ts and tw != ts),
    }


def is_declared(subphase: str, cls: str, tag: str | None) -> bool:
    """True when *tag* is a declared writer for (subphase, cls).

    A None tag is declared only when the declared set contains
    UNATTRIBUTED (i.e. an unstamped direct write is the expected form).
    """
    declared = declared_writers(subphase, cls)
    if not declared:
        return False
    if tag is None:
        return UNATTRIBUTED in declared
    return tag in declared


# ─────────────────────────────────────────────────────────────────────
# THE AUDIT
# ─────────────────────────────────────────────────────────────────────

def audit_exit_frame(world, subphase: str, defenders=None) -> list[str]:
    """Return one row per (defender, undeclared writer) at this boundary.

    Read-only.  ``subphase`` must be one of EXIT_SUBPHASES (an unknown label
    returns an empty list rather than guessing).  Rows are strings so the
    caller can print or assert on them directly.

    Row kinds:
      EXIT_UNDECLARED_WRITER  — the body's primary channel names a writer
                                that is not declared for (subphase, class).
      EXIT_UNATTRIBUTED       — neither channel names a writer, and the
                                declared set does NOT expect that form.
      EXIT_CHANNEL_DISAGREE   — both channels are set and differ, so the
                                phase audit and the jitter audit attribute
                                this body's last write to different writers.
    """
    sub = str(subphase).upper()
    if sub not in EXIT_SUBPHASES:
        return []
    if defenders is None:
        defenders = getattr(world, "defending_team", None) or []
    rows: list[str] = []
    for d in defenders:
        if d is None:
            continue
        cls = body_class(d, world)
        chans = ownership_channels(world, d)
        pid = getattr(d, "player_id", "?")
        role = _role_name(d)
        if chans["channels_disagree"]:
            rows.append(
                "EXIT_CHANNEL_DISAGREE sub=%s id=%s role=%s class=%s "
                "target_writer=%r target_source=%r"
                % (sub, pid, role, cls,
                   chans["target_writer"], chans["target_source"])
            )
        tag = chans["primary"]
        if tag is None:
            if not is_declared(sub, cls, None):
                rows.append(
                    "EXIT_UNATTRIBUTED sub=%s id=%s role=%s class=%s "
                    "declared=%s"
                    % (sub, pid, role, cls,
                       sorted(declared_writers(sub, cls)))
                )
            continue
        if not is_declared(sub, cls, tag):
            rows.append(
                "EXIT_UNDECLARED_WRITER sub=%s id=%s role=%s class=%s "
                "writer=%r declared=%s"
                % (sub, pid, role, cls, tag,
                   sorted(declared_writers(sub, cls)))
            )
    return rows


def format_exit_report(world, subphase: str, defenders=None) -> str:
    """One-line summary of the audit for logging (read-only)."""
    rows = audit_exit_frame(world, subphase, defenders)
    if not rows:
        return "exit_ownership sub=%s OK" % str(subphase).upper()
    return "exit_ownership sub=%s rows=%d" % (str(subphase).upper(), len(rows))
