"""game/debug/tes_diagnostics.py — lean, off-by-default TES telemetry.

Rollout (2026-09-07) redesign: the previous diagnostics recorded a FULL
per-frame snapshot (all lanes/markers/slots arrays).  That is a per-frame
full-state dump, a verbose per-entity trace, and continuous replay
recording — all three are DISABLED in this version.  Instead, under
RLM_TES_DIAGNOSTICS=1 (or ``world._tes_diagnostics``), only THREE lean
streams are kept:

  1. SAMPLED PER-TES SUMMARY ROWS — one compact row per TES phase boundary
     (or every ``TES_SAMPLE_EVERY`` frames), NOT per frame.  Fields are the
     shell summary only: frame, phase, ruck_x/y, per-side slot1/slot2
     id/role/y/target_y, and the side counts.
  2. COMPACT SUPPRESSED-TACKLE EVENTS — one row when tackle gating
     suppresses a radially-close tackle (BC inside the radial radius but
     outside the slot-Y band).  Captures defender_role, bc.y, slot_target_y,
     radial_dist, slot_dist — the "record replays when tackles are
     suppressed despite radial proximity" playtest item.
  3. PER-SLOT OVERRIDE RING BUFFER — a bounded ring (maxlen
     ``TES_OVERRIDE_RING_LEN``) of override writes per slot, fed by
     ``slot_arbitration.update_slot_target``.

Output hygiene: ``flush_tes`` rotates the CSV by size (keep
``TES_KEEP_GENS`` generations) and REDACTS player / writer ids (stable per-id
hash prefix) so raw internal ids never ship.

All probes are NO-OPs unless the world carries a ``_tes_diagnostics`` dict or
RLM_TES_DIAGNOSTICS=1 is set — production behaviour is byte-identical when
the diagnostic is absent.
"""

import hashlib
import os

# SAMPLED summary cadence (frames).  A row is emitted when the phase changes
# OR when ``frame % TES_SAMPLE_EVERY == 0`` — never once per frame.
TES_SAMPLE_EVERY: int = 10

# Per-slot override ring capacity.
TES_OVERRIDE_RING_LEN: int = 32

# Flush rotation: keep at most this many generations (path, .1, .2, ...).
TES_KEEP_GENS: int = 3

# Flush rotation: rotate once the primary file exceeds this many bytes.
TES_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB

# Phases captured by the TES diagnostic.
_TES_PHASES = frozenset({
    "TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE",
})


def diagnostics_on(world) -> bool:
    """True when the TES telemetry exists (world._tes_diagnostics) or
    RLM_TES_DIAGNOSTICS=1 (default off)."""
    if world is not None and hasattr(world, "_tes_diagnostics"):
        return True
    return os.environ.get("RLM_TES_DIAGNOSTICS", "") == "1"


def _get_store(world):
    """Return the world's telemetry store (dict), lazily installed on
    RLM_TES_DIAGNOSTICS=1.  Returns None when the platform is disabled or
    when the store holds a legacy list shape."""
    if world is None:
        return None
    store = getattr(world, "_tes_diagnostics", None)
    if store is None and os.environ.get("RLM_TES_DIAGNOSTICS", "") == "1":
        store = {"summary": [], "suppressed": [], "rings": {}}
        setattr(world, "_tes_diagnostics", store)
    if not isinstance(store, dict):
        return None
    return store


def _is_tes_phase(phase: str) -> bool:
    return str(phase).upper() in _TES_PHASES


def _redact(v) -> str:
    """Return a redacted alias for an integer id (stable per-id hash prefix).

    Raw player / writer ids are never written to telemetry.  Non-numeric
    values (roles, kind, phase) pass through unchanged.
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        digest = hashlib.sha1(str(v).encode("utf-8")).hexdigest()[:8]
        return "#" + digest
    return str(v)


def _fmt(v) -> str:
    """Format a scalar for CSV (blank for None)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _role_name(o) -> str:
    role = getattr(o, "defender_role", None)
    name = getattr(role, "name", None)
    if name:
        return name
    return getattr(o, "depth_role", None) or (str(role) if role else "")


def _summary_row(frame, phase, ruck_x, ruck_y, picks, team) -> dict:
    """Build ONE sampled summary row from the pin's slot picks (compact)."""
    from game.defence.defence_lvl_1.defensive_split_spacing import LEFT, RIGHT

    n_left = sum(1 for d in team if d is not None
                 and float(getattr(d, "y", ruck_y)) < ruck_y)
    n_right = sum(1 for d in team if d is not None
                  and float(getattr(d, "y", ruck_y)) > ruck_y)

    row = {
        "frame": int(frame),
        "phase": str(phase),
        "ruck_x": ruck_x,
        "ruck_y": ruck_y,
        "kind": "summary",
        "n_left": n_left,
        "n_right": n_right,
    }
    picks = picks if isinstance(picks, dict) else {}
    for side, tag in ((LEFT, "L"), (RIGHT, "R")):
        slot1, slot2 = picks.get(side, (None, None))
        row.update({
            f"{tag}1_id": getattr(slot1, "player_id", None),
            f"{tag}1_role": getattr(slot1, "depth_role", None),
            f"{tag}1_y": float(getattr(slot1, "y", 0.0)) if slot1 is not None else None,
            f"{tag}1_target_y": float(getattr(slot1, "target_y", 0.0)) if slot1 is not None else None,
            f"{tag}2_id": getattr(slot2, "player_id", None),
            f"{tag}2_role": getattr(slot2, "depth_role", None),
            f"{tag}2_y": float(getattr(slot2, "y", 0.0)) if slot2 is not None else None,
            f"{tag}2_target_y": float(getattr(slot2, "target_y", 0.0)) if slot2 is not None else None,
        })
    return row


def record_tes(world, frame, phase, picks=None, spacing_dct=None) -> None:
    """Record ONE SAMPLED per-TES summary row (NO-OP unless diagnostics_on).

    ``picks`` is the ``{LEFT: (slot1, slot2), RIGHT: (slot1, slot2)}`` dict
    from ``ruck_adjacent_pin.select_first_two_defenders``; ``spacing_dct``
    is consumed read-only here (retained for signature stability).  Emits a
    row only on a phase boundary or every ``TES_SAMPLE_EVERY`` frames — never
    per frame.
    """
    if not diagnostics_on(world):
        return
    if not _is_tes_phase(phase):
        return
    store = _get_store(world)
    if store is None:
        return
    last = store["summary"][-1] if store["summary"] else None
    if last is not None:
        if last["phase"] == str(phase) and int(frame) % TES_SAMPLE_EVERY != 0:
            return
    ruck_x = float(getattr(world, "ruck_x_m", 60.0))
    ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    team = getattr(world, "defending_team", None) or []
    store["summary"].append(
        _summary_row(frame, phase, ruck_x, ruck_y, picks, team)
    )


def record_suppressed_tackle(world, frame, phase, defender, bc) -> None:
    """Record a compact suppressed-tackle event (NO-OP unless diagnostics_on).

    Fired when tackle gating suppresses a radially-close tackle (BC inside the
    radial radius but outside the slot-Y band).  ``defender`` must carry the
    role + slot target; ``bc`` supplies the live carrier Y.
    """
    if not diagnostics_on(world):
        return
    store = _get_store(world)
    if store is None:
        return
    if defender is None or bc is None:
        return
    import math
    bc_y = float(getattr(bc, "y", 0.0))
    slot_target_y = float(getattr(defender, "target_y", getattr(defender, "y", 0.0)))
    d_y = float(getattr(defender, "y", 0.0))
    d_x = float(getattr(defender, "x", 0.0))
    bc_x = float(getattr(bc, "x", 0.0))
    radial_dist = math.hypot(d_x - bc_x, d_y - bc_y)
    slot_dist = abs(bc_y - slot_target_y)
    store["suppressed"].append({
        "frame": int(frame),
        "phase": str(phase),
        "kind": "suppressed",
        "defender_role": _role_name(defender),
        "bc_y": bc_y,
        "slot_target_y": slot_target_y,
        "radial_dist": radial_dist,
        "slot_dist": slot_dist,
        # ── WRITER-ROLE DIAGNOSTICS (2026-09-07) ──
        # Capture the defender's tackle_role / depth_side / target_source so
        # the SLOT_OUT (suppressed) histogram can attribute which writer path
        # (commit / spacing / structured_close / mmdl_CHASE) collapsed the
        # spacing authority that pushed this body slot-OUT.
        "tackle_role": getattr(defender, "tackle_role", None),
        "depth_side": getattr(defender, "depth_side", None),
        "target_source": getattr(defender, "target_source", None),
    })


# ── OVERRIDE RING (fed by slot_arbitration.update_slot_target) ────────

def slot_override_ring(world, slot_key) -> list | None:
    """Return the per-slot override ring buffer (a bounded list) for *slot_key*,
    or None when diagnostics is OFF (so callers pass ``record=None`` and the
    arbitration writes are un-logged — production byte-identical).

    Lazily creates ``world._slot_override_rings[slot_key]`` as a list capped
    at ``TES_OVERRIDE_RING_LEN``.
    """
    if not diagnostics_on(world):
        return None
    rings = getattr(world, "_slot_override_rings", None)
    if rings is None:
        rings = {}
        setattr(world, "_slot_override_rings", rings)
    ring = rings.get(slot_key)
    if ring is None:
        ring = []
        rings[slot_key] = ring
    return ring


def record_override(world, slot_key, frame, entry) -> None:
    """Append one override-write entry to the per-slot ring (bounded)."""
    ring = slot_override_ring(world, slot_key)
    if ring is None:
        return
    ring.append({"frame": int(frame), "kind": "override", "slot_key": slot_key, **entry})
    if len(ring) > TES_OVERRIDE_RING_LEN:
        del ring[: len(ring) - TES_OVERRIDE_RING_LEN]


# ── FLUSH (rotation + redaction) ─────────────────────────────────────

# Ordered CSV fields (26).  Every row emits each field (blank when N/A).
_HEADER = (
    "frame,phase,ruck_x,ruck_y,kind,n_left,n_right,"
    "L1_id,L1_role,L1_y,L1_target_y,L2_id,L2_role,L2_y,L2_target_y,"
    "defender_role,bc_y,slot_target_y,radial_dist,slot_dist,"
    "tackle_role,depth_side,target_source,"
    "slot_key,writer,priority,proposed_y,current_target_y,override_frames"
)
_FIELDS = _HEADER.split(",")


def _rotate(path: str, max_bytes: int, keep_gens: int) -> str:
    """Rotate *path* by size before writing a new generation.

    If the existing *path* exceeds *max_bytes*, shift .1 -> .2 -> .3 and
    move the current file to .1, returning the primary path to write to.
    Returns *path* unchanged when no rotation is needed (fresh/small file).
    """
    if not os.path.exists(path):
        return path
    if os.path.getsize(path) <= max_bytes:
        return path
    for gen in range(keep_gens - 1, 0, -1):
        src = f"{path}.{gen}"
        dst = f"{path}.{gen + 1}"
        if os.path.exists(src):
            os.replace(src, dst)
    os.replace(path, f"{path}.1")
    return path


def _row_fields(rec: dict, redact_keys=()) -> list:
    """Build the ordered 26-field list for *rec*, redacting *redact_keys*."""
    out = []
    for f in _FIELDS:
        if f == "kind":
            out.append(str(rec.get("kind", "")))
        elif f in redact_keys:
            out.append(_redact(rec.get(f)))
        else:
            out.append(_fmt(rec.get(f)))
    return out


def flush_tes(world, path="tes_diagnostics.csv") -> str:
    """Write the three lean telemetry streams to *path* with rotation +
    redaction.  Returns the path written."""
    store = getattr(world, "_tes_diagnostics", None)
    if not isinstance(store, dict):
        store = {"summary": [], "suppressed": [], "rings": {}}
    path = _rotate(path, TES_MAX_BYTES, TES_KEEP_GENS)

    lines = [_HEADER]
    redact = ("L1_id", "L2_id", "writer")
    for rec in store.get("summary", []):
        lines.append(",".join(_row_fields(rec, redact)))
    for rec in store.get("suppressed", []):
        lines.append(",".join(_row_fields(rec, redact)))
    rings = getattr(world, "_slot_override_rings", None) or {}
    for slot_key, ring in rings.items():
        for rec in ring:
            r = dict(rec)
            r.setdefault("slot_key", slot_key)
            lines.append(",".join(_row_fields(r, redact)))

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _self_test() -> None:
    """Smoke test: arm the env collector, record one sampled summary + one
    suppressed event + one override ring entry, flush and verify the lean
    CSV (parseable, expected columns, redacted ids, bounded ring)."""
    import os as _os
    import tempfile
    _os.environ["RLM_TES_DIAGNOSTICS"] = "1"

    from player.player import Player, DefenderRole
    from world.world_state import WorldState, GameSubState

    w = WorldState()
    w.ruck_x_m = 50.0
    w.ruck_y_m = 32.86
    w.sub_state = GameSubState.TACKLE_DRIVE
    w._ope_phase_label = "TACKLE_DRIVE"
    w.frame_count = 7

    d1 = Player(x=50.0, y=30.86, player_id=1)
    d1.depth_role = "A"
    d1.defender_role = None
    d1.target_y = 30.86
    d2 = Player(x=50.0, y=30.0, player_id=2)
    d2.depth_role = "B"
    d2.defender_role = None
    d2.target_y = 30.0
    m = Player(x=50.0, y=32.86, player_id=3)
    m.defender_role = DefenderRole.MARKER_A
    w.defending_team = [d1, d2, m]

    from game.defence.defence_lvl_1.defensive_split_spacing import LEFT, RIGHT
    picks = {LEFT: (d1, d2), RIGHT: (None, None)}
    record_tes(w, 7, "TACKLE_DRIVE", picks, {})

    # Suppressed-tackle event: BC radially close, slot-OUT.
    bc = Player(x=60.0, y=35.5, player_id=99)
    bc.y = 35.5
    d_out = Player(x=60.5, y=35.1, player_id=4)
    d_out.defender_role = DefenderRole.DEF_LINE_READY
    d_out.target_y = 35.0
    d_out.tackle_role = "SECONDARY"
    d_out.depth_side = -1
    d_out.target_source = "spacing"
    record_suppressed_tackle(w, 7, "TACKLE_DRIVE", d_out, bc)

    # Override ring entry.
    record_override(w, "L1", 7, {
        "writer": 1, "priority": 200, "proposed_y": 35.0,
        "current_target_y": 30.0, "override_frames": 3,
    })

    store = getattr(w, "_tes_diagnostics", None)
    assert store and store["summary"] and len(store["summary"]) == 1, store
    assert store["suppressed"] and len(store["suppressed"]) == 1, store
    # Writer-role diagnostics must land on the suppressed (SLOT_OUT) row.
    assert store["suppressed"][0]["tackle_role"] == "SECONDARY", store["suppressed"][0]
    assert store["suppressed"][0]["depth_side"] == -1, store["suppressed"][0]
    assert store["suppressed"][0]["target_source"] == "spacing", store["suppressed"][0]
    ring = getattr(w, "_slot_override_rings", {}).get("L1")
    assert ring and len(ring) == 1, ring
    # Ring is bounded.
    for i in range(40):
        record_override(w, "L1", 100 + i, {
            "writer": 1, "priority": 200, "proposed_y": float(i),
            "current_target_y": 30.0, "override_frames": 1,
        })
    assert len(ring) == TES_OVERRIDE_RING_LEN, len(ring)

    tmp = tempfile.mktemp(suffix=".csv")
    path = flush_tes(w, tmp)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    lines = content.splitlines()
    assert lines and lines[0].startswith("frame,phase,ruck_x,ruck_y,kind"), content
    assert len(lines[0].split(",")) == len(_FIELDS), lines[0]
    assert ",summary," in content and ",suppressed," in content and ",override," in content, content
    # Redaction: raw id 1 must NOT appear as a bare id ("1," starting a field).
    # The writer id 1 is redacted to "#..." so no bare ",1," should appear.
    assert "1,200" not in content, content
    _os.remove(path)
    _os.environ.pop("RLM_TES_DIAGNOSTICS", None)
    print("TES_DIAGNOSTICS_SELF_TEST_OK")


if __name__ == "__main__":
    import os as _os
    import sys as _sys
    # Direct execution: insert the project root (two levels up) so the smoke
    # test and any direct driver can import the engine packages regardless of
    # the caller's cwd.  Normal package import is side-effect-free.
    _root = _os.path.abspath(
        _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..")
    )
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    _self_test()
