"""game/debug/target_write_log.py — per-frame target-write tagging.

Every movement writer that assigns ``target_x`` / ``target_y`` (or,
optionally, ``x`` / ``y`` / ``vx`` / ``vy``) stamps the write with a writer
name so the jitter audit can attribute each consecutive-frame jump to the
exact subsystem that wrote the value.  All probes are NO-OPs unless the
world carries a ``_target_write_log`` list or ``RLM_JITTER_AUDIT=1``, so
production behaviour is byte-identical when the audit is absent.

Wiring:
  - Every target-write site calls ``stamp_target_write(world, player, writer)``.
  - ``OpenPlayEngine.update()`` tail calls ``record_final_targets`` once per
    frame AFTER all writers and the freeze-pin.

``diff_target_writes`` is the consecutive-frame differ: it walks the
per-frame FINAL records and, for every defender whose final ``target_y``
moved more than *threshold* between two adjacent frames, reports the delta
plus the writer tag that produced the new value.
"""
import os


# Blessed writer names — every target-write site uses one of these so the
# audit output is a small, readable set of subsystems.
TARGET_WRITERS = frozenset({
    "spacing",              # spacing-chain application (DBU / structured)
    "retreat_manager",      # apply_forced_retreat
    "tes_support_retreat",  # TES-support fold-back writer
    "ope_markers",          # OpenPlayEngine marker targets
    "attack",               # BC / DH / support-runners
    "fullback_geometry",    # apply_fullback_geometry
    "structured_close",     # DefensiveBehaviourUnified structured-close
    "mmdl",                 # Movement Mode Decision Layer
    "pursuit",              # in_pursuit_mode writers
    "sdm",                  # Structured Decision Module retreat
    "ruck_pin",             # ruck_adjacent_pin
    "freeze_pin",           # freeze-compliance pin
    "pretackle",            # pretackle_attempt writer (PTA_TARGET_SOURCE)
    "tes_bc_lock",          # BC X TES lock
    "tes_drive_shadow",     # TES-drive BC-shadow writer
})


def audit_on(world) -> bool:
    """True when the write log collector exists (world._target_write_log)
    or ``RLM_JITTER_AUDIT=1`` (default off)."""
    if world is not None and hasattr(world, "_target_write_log"):
        return True
    return os.environ.get("RLM_JITTER_AUDIT", "") == "1"


def _get_store(world):
    """Return world's target-write log, lazily installing it on
    ``RLM_JITTER_AUDIT=1`` so a driver can just set the env var."""
    if world is None:
        return None
    store = getattr(world, "_target_write_log", None)
    if store is None and os.environ.get("RLM_JITTER_AUDIT", "") == "1":
        store = []
        setattr(world, "_target_write_log", store)
    return store


def stamp_target_write(world, player, writer_name, frame=None,
                       *, include_pos=True, detail=None) -> None:
    """Tag a target write with *writer_name* (NO-OP unless audit_on).

    Sets the canonical blessed writer tag in ``player._target_writer`` (a
    NEW, non-behavioural field — the engine's existing ``target_source`` is
    deliberately NOT touched because it is read for behaviour, e.g.
    ``target_source == "tes_drive_shadow"``).  When the audit collector is
    present, ALSO appends a per-write record so consecutive-frame diffs can
    attribute a jump to the last writer.

    *detail* (optional) preserves a granular sub-tag (e.g. the old
    ``retreat_non_compliant`` values) in ``player._target_writer_detail``
    for extra diagnostics without polluting the blessed ``target_source``.

    ``frame`` defaults to ``world.frame_count`` when not supplied.
    """
    if player is None:
        return
    # Canonical blessed writer tag (behaviour-neutral — separate field).
    player._target_writer = writer_name
    if detail is not None:
        player._target_writer_detail = detail
    store = _get_store(world)
    if store is None:
        return
    if world is None:
        f = frame
    elif frame is None:
        f = getattr(world, "frame_count", None)
    else:
        f = frame
    rec = {
        "frame": f,
        "player_id": getattr(player, "player_id", None),
        "writer": writer_name,
        "target_x": getattr(player, "target_x", None),
        "target_y": getattr(player, "target_y", None),
    }
    if detail is not None:
        rec["detail"] = detail
    if include_pos:
        rec["x"] = float(getattr(player, "x", 0.0))
        rec["y"] = float(getattr(player, "y", 0.0))
        rec["vx"] = float(getattr(player, "vx", 0.0))
        rec["vy"] = float(getattr(player, "vy", 0.0))
    store.append(rec)


def record_final_targets(world, frame, players) -> None:
    """End-of-frame snapshot of every player's FINAL applied target + writer.

    Called at the tail of ``OpenPlayEngine.update()`` AFTER all writers and
    the freeze-pin.  Captures the value that actually got applied, so the
    diff sees real post-write state rather than the spacing producer's
    output.  Reads ``player._target_writer`` (the last ``stamp_target_write``
    that ran on that player) as the writer tag, falling back to the legacy
    ``target_source`` for writers not yet migrated.

    NO-OP unless ``audit_on(world)``.  Appends one ``final=True`` record per
    player to ``world._target_write_log``.
    """
    store = _get_store(world)
    if store is None:
        return
    for p in players:
        if p is None:
            continue
        store.append({
            "frame": frame,
            "player_id": getattr(p, "player_id", None),
            "writer": (getattr(p, "_target_writer", None)
                       or getattr(p, "target_source", None) or "unknown"),
            "detail": (getattr(p, "_target_writer_detail", None)
                       or getattr(p, "target_source_detail", None)),
            "target_x": float(getattr(p, "target_x", 0.0) or 0.0),
            "target_y": float(getattr(p, "target_y", 0.0) or 0.0),
            "x": float(getattr(p, "x", 0.0)),
            "y": float(getattr(p, "y", 0.0)),
            "vx": float(getattr(p, "vx", 0.0)),
            "vy": float(getattr(p, "vy", 0.0)),
            "final": True,
        })


def diff_target_writes(records, threshold=0.1):
    """Diff consecutive end-of-frame (``final=True``) records per player.

    For every player whose final ``target_y`` moved more than *threshold*
    between two adjacent frames, return a row carrying the writer tag of
    the NEWER record plus the deltas.

    Row keys: ``frame`` ("f0->f1"), ``player_id``, ``dty``, ``dtx``,
    ``writer``, ``detail``.
    """
    # Group final records by player; keep the LAST final record per frame
    # (a frame may have one final record per player, but ordering is in
    # write order so the last one for a given frame is authoritative).
    by_player = {}
    for r in records:
        if not r.get("final"):
            continue
        pid = r["player_id"]
        by_player.setdefault(pid, []).append(r)

    rows = []
    for pid, recs in by_player.items():
        per_frame = {}
        for r in recs:
            if r.get("frame") is None:
                continue
            per_frame[r["frame"]] = r
        ordered = [per_frame[f] for f in sorted(per_frame)]
        for i in range(1, len(ordered)):
            r0, r1 = ordered[i - 1], ordered[i]
            if r0.get("target_y") is None or r1.get("target_y") is None:
                continue
            dty = r1["target_y"] - r0["target_y"]
            if abs(dty) < threshold:
                continue
            dtx = None
            if (r0.get("target_x") is not None
                    and r1.get("target_x") is not None):
                dtx = r1["target_x"] - r0["target_x"]
            rows.append({
                "frame": f"{r0['frame']}->{r1['frame']}",
                "player_id": pid,
                "dty": dty,
                "dtx": dtx,
                "writer": r1.get("writer"),
                "detail": r1.get("detail"),
            })
    return rows


def _self_test() -> None:
    """Smoke test: two final records, writer changes, target_y jumps."""
    recs = [
        {"frame": 0, "player_id": 1, "writer": "spacing",
         "target_x": 80.0, "target_y": 35.0, "x": 40.0, "y": 30.0,
         "vx": 0.0, "vy": 0.0, "final": True},
        {"frame": 1, "player_id": 1, "writer": "retreat_manager",
         "target_x": 90.0, "target_y": 45.0, "x": 41.0, "y": 31.0,
         "vx": 0.0, "vy": 0.0, "final": True},
    ]
    rows = diff_target_writes(recs, threshold=0.1)
    assert rows, "expected a diff row"
    r = rows[0]
    assert r["player_id"] == 1
    assert abs(r["dty"] - 10.0) < 1e-9
    assert abs(r["dtx"] - 10.0) < 1e-9
    assert r["writer"] == "retreat_manager"
    print("TARGET_WRITE_LOG_SELF_TEST_OK")


if __name__ == "__main__":
    _self_test()
