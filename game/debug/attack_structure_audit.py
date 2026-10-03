"""game/debug/attack_structure_audit.py — Structured ATTACK-side dump chain
(Task XII).

Gives the same macro-transition visibility the defence side has had since
the SDEA audits: every refit, lane read, identity swap, settle window and
BC steering decision becomes an auditable record instead of invisible
noise downstream consumers must compensate for.

TWO RECORD KINDS
----------------
1. MACRO EVENT LOG (append-only):
   SETTLE_BEGIN / SETTLE_END / LANE_READ / IDENTITY_SWAP / REFIT /
   ESCAPE_WINDOW_ENTER / ESCAPE_WINDOW_EXIT / BC_VECTOR_SOURCE /
   PHASE_ENTER.  One line each, in order — replayable from the log alone.

2. FRAME SNAPSHOT (CSV): one row per audited open-play frame with phase,
   sub-state, settle flag, and per-attacker identity/target truth.

COST MODEL
----------
Events are ALWAYS appended to an in-memory bounded ring (cheap, capped)
so verifiers and post-mortems can read them without enabling file dumps.
File I/O happens ONLY when ``DEBUG_ATTACK_STRUCTURE_AUDIT`` is truthy
(env var "1"/"true"/"yes", case-insensitive).  Disabled => zero file cost.

WIRING
------
- attack_structure_authority emits SETTLE_BEGIN/SETTLE_END/LANE_READ.
- game/managers/attack_manager records BC decision provenance
  (ESCAPE_WINDOW_* / BC_VECTOR_SOURCE) around AttackController calls.
- OpenPlayEngine.reset_for_kickoff / on_ball_secured reset the chain via
  reset_attack_structure(world) (which resets this audit too).
"""

import os

# ── Gate ─────────────────────────────────────────────────────────────
DEBUG_ATTACK_STRUCTURE_AUDIT = os.environ.get(
    "DEBUG_ATTACK_STRUCTURE_AUDIT", ""
).strip().lower() in ("1", "true", "yes")

_EVENT_RING_CAP = 4096

# ── In-memory ring (always available to verifiers) ───────────────────
_EVENTS: list = []


def get_events() -> list:
    """Copy of the recorded macro-event ring (oldest first)."""
    return list(_EVENTS)


def reset_events() -> None:
    """Clear the in-memory event ring (per-cycle reset)."""
    _EVENTS.clear()


def format_event(entry: dict) -> str:
    """One canonical text line for an event entry (deterministic order)."""
    fields = entry.get("fields") or {}
    parts = [f"{k}={v}" for k, v in sorted(fields.items())]
    return (
        f"[frame={entry.get('frame')}] "
        f"[t={entry.get('sim_time')}] "
        f"[phase={entry.get('phase')}] "
        f"{entry.get('kind')} " + " ".join(parts)
    ).rstrip()


def emit_event(world, kind: str, **fields) -> None:
    """Record one macro transition into the attack dump chain."""
    frame = getattr(world, "frame_count", None)
    sim_time = getattr(world, "sim_time_s", None)
    sub_state = getattr(world, "sub_state", None)
    phase_name = getattr(sub_state, "name", None) or str(sub_state)

    entry = {
        "frame": frame,
        "sim_time": sim_time,
        "phase": phase_name,
        "kind": kind,
        "fields": fields,
    }
    _EVENTS.append(entry)
    if len(_EVENTS) > _EVENT_RING_CAP:
        del _EVENTS[: len(_EVENTS) - _EVENT_RING_CAP]

    if DEBUG_ATTACK_STRUCTURE_AUDIT:
        print("ATTACK-STRUCTURE:", format_event(entry))


def record_bc_decision(world, source: str, escape_window: bool) -> None:
    """Audit the BC steering provenance for this frame (no behaviour)."""
    prev = getattr(record_bc_decision, "_last_source", None)
    record_bc_decision._last_source = source  # noqa: attr on function
    if escape_window:
        if not getattr(record_bc_decision, "_escape_active", False):
            record_bc_decision._escape_active = True  # noqa
            emit_event(world, "ESCAPE_WINDOW_ENTER", source=source)
    else:
        if getattr(record_bc_decision, "_escape_active", False):
            record_bc_decision._escape_active = False  # noqa
            emit_event(world, "ESCAPE_WINDOW_EXIT", source=source)
    if source != prev:
        emit_event(world, "BC_VECTOR_SOURCE", mode=source)


def record_frame_snapshot(world, attackers) -> None:
    """Append one frame row of attacker identity/target truth (CSV-style).

    Always appended to the in-memory ring tail? No — snapshots are verbose;
    they are materialised ONLY when the debug flag is on.  Verifiers that
    need frame truth read live player fields directly.
    """
    if not DEBUG_ATTACK_STRUCTURE_AUDIT:
        return
    bc = getattr(world, "ball_carrier", None)
    ruck_y = getattr(world, "ruck_y_m", None)
    header = (
        f"FRAME frame={getattr(world, 'frame_count', None)} "
        f"t={getattr(world, 'sim_time_s', None)} "
        f"bc=({'%.2f' % bc.x},{'%.2f' % bc.y})" if bc is not None else "FRAME bc=None"
    )
    lines = [header + f" ruck_y={ruck_y if ruck_y is None else '%.2f' % ruck_y}"]
    for p in attackers:
        lines.append(
            f"  id={getattr(p, 'player_id', '?')} "
            f"role={getattr(p, 'attack_role', None)} "
            f"slot={getattr(p, 'attack_lane_slot', None)} "
            f"x={p.x:.2f} y={p.y:.2f} "
            f"tx={p.target_x if p.target_x is None else '%.2f' % p.target_x} "
            f"ty={p.target_y if p.target_y is None else '%.2f' % p.target_y}"
        )
    print("ATTACK-FRAME-SNAPSHOT")
    for ln in lines:
        print(ln)
