"""contest_preservation_audit.py — THE CONTEST RECORD AUDIT SINK (Stage 2A).

An OPTIONAL, read-only CSV sink for the Stage 2A preservation layer.  It
answers, for every declaration, "what was the complete formation state, and
what did the authority do with every body in it" — on disk, in a form another
tool can read.

TWO ROW KINDS
-------------
  ``row_kind="record"``  one row per declaration: the decision (frame, seq,
                         outcome, reason), the admission (channel +
                         admission predicate + site + decider), the winner,
                         the geometry, and the carrier snapshot.
  ``row_kind="body"``    one row per body in the formation state, with the
                         preserved stamps and the body's DISPOSITION — what
                         the authority did with it.  In Stage 1B an in-radius
                         body the decider never reached appeared NOWHERE; here
                         each one is a row, with ``disposition =
                         in_radius_not_evaluated``.

ROWS ARE COLUMN-KEYED.  Every row is built as a dict keyed by the declared
column name and written in declared order through ``csv.DictWriter``, so a row
can never drift out of alignment with the header and a new column cannot
silently land in the wrong field.  (Stage 1B shipped that exact defect once and
its verifier caught it; this sink is written the corrected way from the start.)

THE SINK IS OFF BY DEFAULT AND WRITES A FILE ONLY.
``RLM_CONTEST_PRESERVATION_AUDIT=1`` arms it.  It never decides anything, never
touches a body, and nothing in the engine depends on it — deleting the file
changes nothing.
"""

from __future__ import annotations

import csv
import os

from game.defence.defence_lvl_1.contest_record import ContestRecord

__all__ = [
    "AUDIT_ENV_VAR",
    "AUDIT_CSV_NAME",
    "AUDIT_COLUMNS",
    "audit_enabled",
    "audit_sink_path",
    "record_contest_row",
]

# Off by default: preservation always runs, the AUDIT is opt-in.
AUDIT_ENV_VAR: str = "RLM_CONTEST_PRESERVATION_AUDIT"
AUDIT_CSV_NAME: str = "contest_preservation_audit.csv"

# The declared column set, in write order.
AUDIT_COLUMNS: tuple = (
    "row_kind",
    "record_index", "frame", "seq", "phase", "outcome", "reason",
    "channel", "admission_predicate", "admission_site", "decider",
    "winner_id",
    "radius_m", "candidate_count", "role_holder_count",
    "in_radius_count", "evaluated_count", "population_source",
    "arm_grab_damping", "glancing_displacement",
    "bc_x", "bc_y", "bc_vx", "bc_vy",
    "body_id", "defender_role", "tackle_role", "depth_class", "dist",
    "in_radius", "disposition", "failed_at", "predicate_verdict",
    "pre_tackle_state", "corridor_owned", "corridor_frames", "slot_eligible",
    "engage_guard", "engage_guard_stamped", "engage_state_last",
    "retreat_obligated", "is_winner", "never_evaluated",
    # Stage 3.1 - the skill-trait placeholders.  The body pair is named for
    # its fields; the carrier pair carries the carrier's own ``bc_`` prefix,
    # like every other carrier column.  They are appended LAST so no existing
    # column moves, and they are declared here because this sink's law is that
    # its columns cover EVERY field of the record and of a body row.
    "tackle_skill_trait", "core_strength_trait",
    "bc_fend_trait", "bc_tackle_break_trait",
)


def audit_enabled() -> bool:
    """True when the CSV audit sink is armed.  OFF by default.

    This gate can never change a decision or a record: it only decides whether
    the record is ALSO written to disk.
    """
    return os.environ.get(AUDIT_ENV_VAR, "") == "1"


def audit_sink_path() -> str:
    """Path of the contest-preservation audit CSV (see ``audit_enabled``)."""
    return AUDIT_CSV_NAME


def _blank_row() -> dict:
    """A row with every declared column present and empty."""
    return {name: "" for name in AUDIT_COLUMNS}


def _decision_fields(row: dict, record: ContestRecord) -> None:
    """Fill the decision/admission/geometry columns shared by both row kinds."""
    row.update({
        "record_index": record.record_index,
        "frame": record.frame,
        "seq": record.seq,
        "phase": record.phase,
        "outcome": record.outcome,
        "reason": record.reason,
        "channel": record.channel,
        "admission_predicate": record.admission_predicate,
        "admission_site": record.admission_site,
        "decider": record.decider,
        "winner_id": ("" if record.winner_id is None else record.winner_id),
        "radius_m": round(record.radius_m, 6),
        "candidate_count": record.candidate_count,
        "role_holder_count": record.role_holder_count,
        "in_radius_count": record.in_radius_count,
        "evaluated_count": record.evaluated_count,
        "population_source": record.population_source,
        "arm_grab_damping": int(bool(record.arm_grab_damping)),
        "glancing_displacement": (
            "" if record.glancing_displacement is None
            else record.glancing_displacement
        ),
        "bc_x": round(record.carrier.x, 4),
        "bc_y": round(record.carrier.y, 4),
        "bc_vx": round(record.carrier.vx, 4),
        "bc_vy": round(record.carrier.vy, 4),
        "bc_fend_trait": round(record.carrier.fend_trait, 6),
        "bc_tackle_break_trait": round(
            record.carrier.tackle_break_trait, 6
        ),
    })


def record_contest_row(record: ContestRecord) -> None:
    """Append *record* (one record row + one row per body) to the CSV.

    Read-only with respect to the engine: it writes the audit file only, and
    the caller invokes it only when ``audit_enabled()`` is true.
    """
    path = audit_sink_path()
    needs_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(AUDIT_COLUMNS))
        if needs_header:
            writer.writeheader()

        header = _blank_row()
        header["row_kind"] = "record"
        _decision_fields(header, record)
        writer.writerow(header)

        for row in record.candidates:
            body = _blank_row()
            body["row_kind"] = "body"
            _decision_fields(body, record)
            body.update({
                "body_id": row.player_id,
                "defender_role": row.defender_role,
                "tackle_role": row.tackle_role or "",
                "depth_class": row.depth_class or "",
                "dist": round(row.dist, 4),
                "in_radius": int(bool(row.in_radius)),
                "disposition": row.disposition.value,
                "failed_at": row.failed_at or "",
                "predicate_verdict": row.predicate_verdict or "",
                "pre_tackle_state": row.pre_tackle_state or "",
                "corridor_owned": int(bool(row.corridor_owned)),
                "corridor_frames": row.corridor_frames,
                "slot_eligible": ("" if row.slot_eligible is None
                                  else int(bool(row.slot_eligible))),
                "engage_guard": int(bool(row.engage_guard)),
                "engage_guard_stamped": int(bool(row.engage_guard_stamped)),
                "engage_state_last": row.engage_state_last or "",
                "retreat_obligated": int(bool(row.retreat_obligated)),
                "is_winner": int(bool(row.is_winner)),
                "never_evaluated": int(bool(row.never_evaluated)),
                "tackle_skill_trait": round(row.tackle_skill_trait, 6),
                "core_strength_trait": round(row.core_strength_trait, 6),
            })
            writer.writerow(body)
