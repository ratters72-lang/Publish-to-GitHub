"""
game/debug/movement_contract_audit.py — Movement Contract Stability Audit (TASK III).

Per-frame audit that verifies no MC/TW outside the defensive pipeline
overwrites the pipeline's stamped defensive geometry:

    tackle_role  (PRIMARY/SECONDARY/PASSIVE/...)
    movement_state (CHASE/DRIFT/RETREAT/COMMIT — and every MovementState)
    target_source  (mmdl_CHASE / tackler_commit / structured_* / ...)
    target_x / target_y

DESIGN
------
Mirrors the existing diagnostic pattern (defender_state_audit /
freeze_audit): no-op unless explicitly enabled (``DEBUG_MOVEMENT_CONTRACT_AUDIT``),
self-limiting, and never raises — it only records and prints.

Wiring contract (OpenPlayEngine.update() and _update_defence()):

    # 1. AFTER the defensive pipeline has stamped every defender
    #    (end of _update_defence, after the MMDL integration loop):
    from game.debug.movement_contract_audit import snapshot_defensive_state
    snapshot_defensive_state(world)

    # 2. AFTER each LATER writer segment has run, CHECK against the
    #    snapshot so the corrupting writer is identifiable:
    from game.debug.movement_contract_audit import check_for_overwrites
    check_for_overwrites(world, phase=self._phase, writer_label="role_movement")

    # 3. Every N frames dump the per-writer / per-phase summary:
    from game.debug.movement_contract_audit import print_movement_contract_report
    print_movement_contract_report(world)

A "defensive" stamp is any target_source that belongs to the OPEN_PLAY
defensive pipeline: ``structured_*``, ``mmdl_*``, ``tackler_commit``,
``pursuit``, ``decision_*``, ``retreat_*``, ``line_break_force``.
A later writer changing any of the five stamped fields (tackle_role /
movement_state / target_source / target_x / target_y) is an OVERWRITE
unless it is the freeze authority pin (frozen players) — that pin is
reported under the ``freeze_pin`` label but marked LEGAL.

NOT a state machine — this module owns no player fields.  It keeps its
aggregates in module state so the engine is untouched when disabled.
"""

# ── MODULE GATE (no-op unless True) ─────────────────────────────────
DEBUG_MOVEMENT_CONTRACT_AUDIT: bool = False

# Self-limiting: capture at most this many frames total, then silent.
AUDIT_MAX_FRAMES: int = 300
# Dump the summary every N frames (and at engine report time).
REPORT_INTERVAL_FRAMES: int = 60

# Defensive-pipeline target_source prefixes.  A stamp is "defensive" if
# it starts with any of these.
_DEFENSIVE_SOURCE_PREFIXES = (
    "structured_",
    "mmdl_",
    "tackler_commit",
    "pursuit",
    "decision_",
    "retreat_",
    "line_break_force",
)

# ── MODULE AGGREGATES ───────────────────────────────────────────────
_frames_audited: int = 0
_snapshots: dict = {}          # frame -> {id(player): (role, state, src, tx, ty)}
_writer_counts: dict = {}      # writer_label -> {overwrites, stale_reads, frame_orders}
_phase_counts: dict = {}       # phase -> {overwrites_by_writer, ...}
_corruption_rows: list = []    # per-player per-frame corruption rows (bounded)


def _is_enabled() -> bool:
    return DEBUG_MOVEMENT_CONTRACT_AUDIT


def _state_name(state) -> str:
    return getattr(state, "name", None) or str(state) or "None"


def _is_defensive_source(src) -> bool:
    if not src:
        return False
    return isinstance(src, str) and src.startswith(_DEFENSIVE_SOURCE_PREFIXES)


def _stamp_of(d) -> tuple:
    """Return the five stamped fields of *d* as a comparable tuple."""
    return (
        str(getattr(d, "tackle_role", "PASSIVE")),
        _state_name(getattr(d, "movement_state", None)),
        str(getattr(d, "target_source", None)),
        float(getattr(d, "target_x", float("nan"))),
        float(getattr(d, "target_y", float("nan"))),
    )


# ─────────────────────────────────────────────────────────────────────
# PUBLIC API — snapshot + check
# ─────────────────────────────────────────────────────────────────────

def snapshot_defensive_state(world) -> None:
    """Record the defensive-pipeline stamps for the current frame.

    Call at the END of OpenPlayEngine._update_defence() (after the MMDL
    integration loop, before the pre-tackle trigger) when the pipeline
    has made its final write for the frame.

    Only defenders carrying a defensive target_source are snapshotted —
    a defender the pipeline did NOT own this frame cannot be "overwritten"
    by a later writer.
    """
    if not _is_enabled():
        return
    global _frames_audited
    frame = getattr(world, "frame_count", 0)
    if _frames_audited >= AUDIT_MAX_FRAMES:
        return
    _frames_audited += 1

    stamp_map = {}
    for d in (getattr(world, "defending_team", []) or []):
        if d is None:
            continue
        src = getattr(d, "target_source", None)
        if not _is_defensive_source(src):
            continue
        stamp_map[id(d)] = (d, _stamp_of(d))
    _snapshots[frame] = stamp_map

    # Housekeeping: drop dicts from old frames so memory stays bounded.
    max_frames = 30
    if len(_snapshots) > max_frames:
        oldest = sorted(_snapshots)[: len(_snapshots) - max_frames]
        for k in oldest:
            _snapshots.pop(k, None)


def check_for_overwrites(world, phase: str, writer_label: str) -> None:
    """Compare the CURRENT defender state against the frame snapshot.

    NOTE ON COVERAGE (TASK III, 2026-08-14): the frame snapshot only
    exists for OPEN_PLAY frames (the pipeline is dormant in every TES /
    RELEASE / PTB phase).  A check called from a non-OPEN_PLAY path
    (e.g. the RELEASE/PTB role-movement block) early-returns BEFORE this
    writer is registered, so its row is absent from the per-writer table.
    That is correct: a writer that ran with no pipeline stamp in the
    frame cannot be "overwriting" one.

    Call AFTER every writer segment that runs after the defensive
    pipeline (role movement, SDM, TACKLE_DRIVE extra integration,
    freeze pin).  ``writer_label`` identifies the segment so the
    per-writer corruption table can attribute each overwrite.

    Overwrite detection (TASK_III §2.1/§2.5–§2.8):
      - tackle_role changed away from the stamped value while the
        snapshot said PRIMARY/SECONDARY  -> PRIM/SEC suppression
      - movement_state changed away while snapshot said CHASE
        -> movement_state suppression
      - target_source changed away from a defensive source
        -> target_source suppression / frame-order conflict
      - target_x / target_y drifted > 3.0 m from the stamped value
        -> geometry overwrite
    """
    if not _is_enabled():
        return
    frame = getattr(world, "frame_count", 0)
    stamp_map = _snapshots.get(frame)
    if not stamp_map:
        return

    wc = _writer_counts.setdefault(
        writer_label, {"overwrites": 0, "stale_reads": 0, "frame_orders": 0}
    )
    pc = _phase_counts.setdefault(phase, {})

    for pid, (d, before) in stamp_map.items():
        after = _stamp_of(d)
        if after == before:
            continue

        role_before, state_before, src_before, tx_before, ty_before = before
        role_after, state_after, src_after, tx_after, ty_after = after

        flags = []

        # PRIM/SEC suppression (TASK_III §2.5).
        if role_before in ("PRIMARY", "SECONDARY") and role_after != role_before:
            flags.append("tackle_role_overwrite")

        # movement_state suppression (TASK_III §2.6).
        if state_before in ("CHASE", "SHOOT", "RETREAT") and state_after != state_before:
            flags.append("movement_state_overwrite")

        # target_source suppression / frame-order conflict (§2.7/§2.8).
        if src_before != src_after:
            if _is_defensive_source(src_before) and not _is_defensive_source(src_after):
                flags.append("target_source_overwrite")
            elif _is_defensive_source(src_before) and _is_defensive_source(src_after):
                flags.append("target_source_changed_defensive")
            else:
                flags.append("target_source_overwrite")

        # Geometry overwrite (> 3.0 m drift on a stamped axis).
        tx_drift = abs(tx_after - tx_before)
        ty_drift = abs(ty_after - ty_before)
        if tx_drift > 3.0 or ty_drift > 3.0:
            flags.append("target_geometry_overwrite")

        # Stale-read detection: a later writer that did NOT change the
        # source but rewrote nothing is fine; a writer that consumed the
        # OLD value would keep the same source but move the target.
        if src_after == src_before and (tx_drift != 0.0 or ty_drift != 0.0):
            flags.append("stale_geometry_read")

        if not flags:
            continue

        # Freeze pin is the legal exception: frozen players get pinned to
        # x/y by the freeze authority.  Report but do not count as an
        # overwrite violation.
        if writer_label == "freeze_pin" and getattr(d, "frozen", False):
            continue

        wc["overwrites"] += 1
        if "stale_geometry_read" in flags:
            wc["stale_reads"] += 1
        if "target_source_overwrite" in flags or "frame_order" in flags:
            wc["frame_orders"] += 1

        per_writer = pc.setdefault(
            writer_label, {"overwrites": 0, "stale_reads": 0, "frame_orders": 0}
        )
        per_writer["overwrites"] += 1
        if "stale_geometry_read" in flags:
            per_writer["stale_reads"] += 1
        if "target_source_overwrite" in flags:
            per_writer["frame_orders"] += 1

        # Bounded per-player corruption table.
        if len(_corruption_rows) < 2000:
            _corruption_rows.append({
                "frame": frame,
                "player_id": getattr(d, "player_id", pid),
                "writer": writer_label,
                "phase": phase,
                "role_def": role_before,
                "role_after": role_after,
                "state_def": state_before,
                "state_after": state_after,
                "src_def": src_before,
                "src_after": src_after,
                "ty_def": round(ty_before, 2),
                "ty_after": round(ty_after, 2),
                "flags": flags,
            })


# ─────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────

def print_movement_contract_report(world, frame: int | None = None) -> None:
    """Print the per-writer / per-phase / global corruption summary.

    Called every REPORT_INTERVAL_FRAMES and at report time from the
    engine.  No-op unless enabled.
    """
    if not _is_enabled():
        return

    print("\n=== MOVEMENT CONTRACT AUDIT REPORT ===")
    print(f"frames_audited={_frames_audited}")

    # Per-writer table.
    print("\n--- PER-WRITER CORRUPTION TABLE ---")
    print(f"{'writer':<26}{'overwrites':>10}{'stale_reads':>12}{'frame_orders':>14}")
    for label, counts in sorted(_writer_counts.items()):
        print(f"{label:<26}{counts['overwrites']:>10}"
              f"{counts['stale_reads']:>12}{counts['frame_orders']:>14}")

    # Per-phase summary grouped by the TASK_III phase buckets.
    print("\n--- PER-PHASE CORRUPTION SUMMARY ---")
    buckets = {
        "OPEN_PLAY": ("OPEN_PLAY", "POST_RUCK_OPEN_PLAY"),
        "RETREAT": ("RELEASE", "PTB_PREP", "PTB_EXEC", "PTB"),
        "CHASE": ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
                  "TACKLE_DOMINANCE", "POST_RUCK_OPEN_PLAY"),
    }
    for bucket, phases in buckets.items():
        total = 0
        details = []
        for phase in phases:
            pc = _phase_counts.get(phase, {})
            for label, counts in pc.items():
                total += counts["overwrites"]
                details.append(f"{phase}:{label}={counts['overwrites']}")
        print(f"  {bucket}: total_overwrites={total}"
              + (f"  ({', '.join(details)})" if details else ""))

    # Global summary.
    total_overwrites = sum(c["overwrites"] for c in _writer_counts.values())
    total_stale = sum(c["stale_reads"] for c in _writer_counts.values())
    total_orders = sum(c["frame_orders"] for c in _writer_counts.values())
    print("\n--- GLOBAL CORRUPTION SUMMARY ---")
    print(f"  total_overwrites={total_overwrites}")
    print(f"  total_stale_reads={total_stale}")
    print(f"  total_frame_order_conflicts={total_orders}")
    print("=== END MOVEMENT CONTRACT AUDIT REPORT ===\n")


def reset_movement_contract_audit() -> None:
    """Clear all audit state (call on kickoff reset / new capture run)."""
    global _frames_audited
    _frames_audited = 0
    _snapshots.clear()
    _writer_counts.clear()
    _phase_counts.clear()
    _corruption_rows.clear()
