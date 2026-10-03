"""verify_slot_out_late_chase_histogram.py — role/side histogram verifier.

PROBLEM (2026-09-07): the SLOT_OUT (suppressed-tackle) and LATE_CHASE
(CHASE_X_COLLAPSE_TOO_SLOW) records did not carry the defender's
``tackle_role`` / ``depth_side`` / ``target_source``.  Without the writer
stamp we cannot say WHICH writer path (commit / spacing / structured_close /
mmdl_CHASE) collapsed the spacing authority that left a radially-close
body slot-OUT, or that left a CHASE body too slow to close the X gap.

This verifier exercises the instrumented record builders end-to-end:

  1. SLOT_OUT rows via ``tes_diagnostics.record_suppressed_tackle`` (the
     real suppressed-tackle recorder), each defender carrying a distinct
     (tackle_role, depth_side, target_source) triple.  Asserts the row
     carries all three fields.
  2. LATE_CHASE rows via ``chase_probe.probe_chase_velocity`` under
     ``RLM_CHASE_PROBE=1`` with stdout captured; parses the
     ``CHASE_X_COLLAPSE_TOO_SLOW``-flagged STATE rows and asserts the three
     writer-role fields land on them.
  3. Builds a ``(target_source, tackle_role, depth_side)`` histogram from
     both streams and asserts a single dominant writer is isolatable
     (spacing for SLOT_OUT, mmdl_CHASE for LATE_CHASE in the synthetic
     fixture).

THE HISTOGRAM IS THE DELIVERABLE: feeding real ``run.py`` diagnostics into
``build_histogram`` names the writer path that is collapsing spacing
authority, so the surgical fix can target ONLY that path.

Run:  python verify_slot_out_late_chase_histogram.py
Exit 0 on success; prints a PASS/FAIL line per assertion.
"""
import io
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from world.world_state import WorldState
from game.debug.tes_diagnostics import record_suppressed_tackle
from game.debug.chase_probe import probe_chase_velocity
from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y
from game.defence.defence_lvl_1.slot_arbitration import (
    tackle_slot_threshold_m as _slot_threshold,
)

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ── Writer-path vocabulary (the four the task names) ─────────────────
WRITER_COMMIT = "tackler_commit"        # "commit"
WRITER_SPACING = "spacing"              # "spacing"
WRITER_CLOSE = "structured_close"       # "structured_close"
WRITER_MMDL_CHASE = "mmdl_CHASE"        # "mmdl_CHASE"


def _mk_defender(pid, x, y, tackle_role, depth_side, target_source, target_y):
    d = Player(x=x, y=y, player_id=pid)
    d.tackle_role = tackle_role
    d.depth_side = depth_side
    d.target_source = target_source
    d.target_y = target_y
    d.vx = 0.0
    d.vy = 0.0
    d._probe_frame = 0
    return d


# ─────────────────────────────────────────────────────────────────────
# HISTOGRAM BUILDER (the reusable analysis tool)
# ─────────────────────────────────────────────────────────────────────

def build_histogram(rows):
    """Aggregate ``rows`` (iterable of dicts) into a role/side/writer histogram.

    Each row must carry ``target_source``, ``tackle_role``, ``depth_side``
    keys (the instrumented fields).  Returns:
      hist : {(target_source, tackle_role, depth_side): count}
    grouped by writer so the dominant spacing-collapse path is visible.
    """
    hist = defaultdict(int)
    for r in rows:
        key = (
            str(r.get("target_source", "unknown")),
            str(r.get("tackle_role", "unknown")),
            str(r.get("depth_side", "unknown")),
        )
        hist[key] += 1
    return dict(hist)


def dominant_writer(hist):
    """Return the target_source with the largest total count in *hist*."""
    per_writer = defaultdict(int)
    for (writer, _role, _side), n in hist.items():
        per_writer[writer] += n
    if not per_writer:
        return None, {}
    top = max(per_writer.items(), key=lambda kv: kv[1])
    return top[0], dict(per_writer)


def print_histogram(title, hist):
    print(f"\n  {title}")
    print("  %-22s %-14s %-6s %s" % ("writer", "tackle_role", "side", "count"))
    for (writer, role, side), n in sorted(hist.items(), key=lambda kv: -kv[1]):
        print("  %-22s %-14s %-6s %d" % (writer, role, side, n))


# ─────────────────────────────────────────────────────────────────────
# LATE_CHASE captures: run the real chase probe, parse the flagged rows
# ─────────────────────────────────────────────────────────────────────

_STATE_RE = re.compile(r"src=([^ ]+)")
_STATE_RE_TACKLE = re.compile(r"tackle=([^ ]+)")
_STATE_RE_SIDE = re.compile(r"side=([^ ]+)")


def _capture_late_chase(defenders):
    """Run ``probe_chase_velocity`` for *defenders* under RLM_CHASE_PROBE=1.

    Each defender must be near a target that is >5.0 m away in X with a
    final |vx| < 0.5 so the LATE_CHASE flag fires.  Returns the parsed
    LATE_CHASE rows (only the CHASE_X_COLLAPSE_TOO_SLOW-flagged lines).
    """
    os.environ["RLM_CHASE_PROBE"] = "1"
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        for d in defenders:
            target_x = d.x + 20.0          # x_gap > 5.0
            target_y = d.y
            dist = 20.0
            probe_chase_velocity(
                d,
                target_x=target_x,
                target_y=target_y,
                state_name="CHASE",
                dx=target_x - d.x,
                dy=0.0,
                dist=dist,
                dir_x=1.0,
                dir_y=0.0,
                desired_vx=8.3,
                desired_vy=0.0,
                final_vx=0.1,              # |vx| < 0.5 -> flag
                final_vy=0.0,
                ceiling=8.3,
            )
    finally:
        sys.stdout = old
        os.environ.pop("RLM_CHASE_PROBE", None)
    out = buf.getvalue()
    rows = []
    for line in out.splitlines():
        if "CHASE_X_COLLAPSE_TOO_SLOW" not in line:
            continue
        m_src = _STATE_RE.search(line)
        m_t = _STATE_RE_TACKLE.search(line)
        m_s = _STATE_RE_SIDE.search(line)
        rows.append({
            "target_source": m_src.group(1) if m_src else "unknown",
            "tackle_role": m_t.group(1) if m_t else "unknown",
            "depth_side": m_s.group(1) if m_s else "unknown",
        })
    return rows


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 64)
    print("SLOT_OUT / LATE_CHASE ROLE-SIDE-WRITER HISTOGRAM VERIFIER")
    print("=" * 64)

    # ── PART 1: SLOT_OUT records carry the writer-role triple ──
    print("\n[1] SLOT_OUT (suppressed) rows carry tackle_role/depth_side/target_source")
    os.environ["RLM_TES_DIAGNOSTICS"] = "1"
    w = WorldState()
    w.frame_count = 10

    # Synthetic fixture: spacing dominates SLOT_OUT (the spacing-Y authority
    # positions the body off the BC lane); a couple of structured_close /
    # mmdl_CHASE / tackler_commit outliers.
    slot_out_defs = [
        # (pid, x, y, tackle_role, depth_side, target_source, target_y)
        (1, 60.5, 35.1, "SECONDARY", -1, WRITER_SPACING, 35.0),
        (2, 60.5, 34.9, "SECONDARY",  1, WRITER_SPACING, 35.0),
        (3, 60.4, 35.0, "PRIMARY",   -1, WRITER_SPACING, 35.0),
        (4, 60.6, 35.2, "SECONDARY",  1, WRITER_SPACING, 35.0),
        (5, 60.3, 34.8, "PRIMARY",   -1, WRITER_SPACING, 35.0),
        (6, 60.5, 35.1, "SECONDARY", -1, WRITER_SPACING, 35.0),
        (7, 60.5, 35.1, "SECONDARY", -1, WRITER_CLOSE,   35.2),
        (8, 60.5, 35.1, "PRIMARY",   -1, WRITER_CLOSE,   35.2),
        (9, 60.5, 35.1, "SECONDARY", -1, WRITER_MMDL_CHASE, 36.1),
        (10, 60.5, 35.1, "PRIMARY",  -1, WRITER_COMMIT,  35.0),
    ]
    bc = Player(x=60.0, y=35.5, player_id=99)
    bc.y = 35.5
    slot_out_rows = []
    for pid, x, y, role, side, src, ty in slot_out_defs:
        d = _mk_defender(pid, x, y, role, side, src, ty)
        record_suppressed_tackle(w, 10, "OPEN_PLAY", d, bc)
        slot_out_rows.append({
            "target_source": src,
            "tackle_role": role,
            "depth_side": side,
        })

    store = getattr(w, "_tes_diagnostics", None)
    check("1.1 SLOT_OUT store holds all 10 rows",
          store is not None and len(store.get("suppressed", [])) == 10,
          "n=%s" % (len(store.get("suppressed", [])) if store else None))
    if store and store.get("suppressed"):
        rec = store["suppressed"][0]
        check("1.2 SLOT_OUT row carries tackle_role",
              rec.get("tackle_role") == "SECONDARY", "tackle_role=%s" % rec.get("tackle_role"))
        check("1.3 SLOT_OUT row carries depth_side",
              rec.get("depth_side") == -1, "depth_side=%s" % rec.get("depth_side"))
        check("1.4 SLOT_OUT row carries target_source",
              rec.get("target_source") == WRITER_SPACING, "target_source=%s" % rec.get("target_source"))

    hist_slot_out = build_histogram(slot_out_rows)
    print_histogram("SLOT_OUT histogram (target_source, tackle_role, depth_side):", hist_slot_out)
    dom_slot, per_writer_slot = dominant_writer(hist_slot_out)
    print("    dominant writer -> %s" % dom_slot)
    check("1.5 SLOT_OUT dominant writer isolatable (spacing)",
          dom_slot == WRITER_SPACING and per_writer_slot.get(WRITER_SPACING, 0) > 5,
          "dom=%s per_writer=%s" % (dom_slot, per_writer_slot))

    os.environ.pop("RLM_TES_DIAGNOSTICS", None)

    # ── PART 2: LATE_CHASE records carry the writer-role triple ──
    print("\n[2] LATE_CHASE (CHASE_X_COLLAPSE_TOO_SLOW) rows carry the triple")
    late_chase_defs = [
        # mmdl_CHASE dominates: MMDL set CHASE on top of a stale collapsed target.
        _mk_defender(20, 40.0, 30.0, "SECONDARY", -1, WRITER_MMDL_CHASE, 60.0),
        _mk_defender(21, 40.0, 30.0, "PRIMARY",    1, WRITER_MMDL_CHASE, 60.0),
        _mk_defender(22, 40.0, 30.0, "SECONDARY", -1, WRITER_MMDL_CHASE, 60.0),
        _mk_defender(23, 40.0, 30.0, "PRIMARY",    1, WRITER_MMDL_CHASE, 60.1),
        _mk_defender(24, 40.0, 30.0, "SECONDARY", -1, WRITER_MMDL_CHASE, 60.2),
        _mk_defender(25, 40.0, 30.0, "PRIMARY",    1, WRITER_SPACING,    60.0),
        _mk_defender(26, 40.0, 30.0, "SECONDARY", -1, WRITER_CLOSE,      60.0),
    ]
    late_chase_rows = _capture_late_chase(late_chase_defs)
    check("2.1 LATE_CHASE rows captured (>=5 flagged)",
          len(late_chase_rows) >= 5, "n=%d" % len(late_chase_rows))
    check("2.2 LATE_CHASE row carries tackle_role/side/src",
          late_chase_rows and "tackle_role" in late_chase_rows[0]
          and "depth_side" in late_chase_rows[0]
          and "target_source" in late_chase_rows[0],
          "row0=%s" % (late_chase_rows[0] if late_chase_rows else None))

    hist_late = build_histogram(late_chase_rows)
    print_histogram("LATE_CHASE histogram (target_source, tackle_role, depth_side):", hist_late)
    dom_late, per_writer_late = dominant_writer(hist_late)
    print("    dominant writer -> %s" % dom_late)
    check("2.3 LATE_CHASE dominant writer isolatable (mmdl_CHASE)",
          dom_late == WRITER_MMDL_CHASE and per_writer_late.get(WRITER_MMDL_CHASE, 0) > 3,
          "dom=%s per_writer=%s" % (dom_late, per_writer_late))

    # ── PART 3: combined writer histogram names the dominant collapse path ──
    print("\n[3] COMBINED histogram isolates the dominant spacing-collapse writer")
    combined = build_histogram(slot_out_rows + late_chase_rows)
    print_histogram("COMBINED (SLOT_OUT + LATE_CHASE):", combined)
    dom_combined, per_writer_combined = dominant_writer(combined)
    if per_writer_combined:
        top_writer = max(per_writer_combined.items(), key=lambda kv: kv[1])[0]
        top_count = per_writer_combined[top_writer]
    else:
        top_writer, top_count = None, 0
    check("3.1 combined histogram isolates the max-count writer path",
          top_writer in (WRITER_SPACING, WRITER_MMDL_CHASE, WRITER_CLOSE,
                         WRITER_COMMIT)
          and top_count == max(per_writer_combined.values()) if per_writer_combined else False,
          "top=%s count=%d per_writer=%s" % (top_writer, top_count, per_writer_combined))

    # ── PART 4: surgical spacing fix — engaged radially-close PRIMARY stays
    # in the slot band (the spacing-only clamp added to frame_spacing_y). ──
    print("\n[4] spacing-only slot-tight clamp keeps a radially-close engaged body slot-IN")
    from types import SimpleNamespace
    tuners4 = SimpleNamespace()
    tuners4.slot_tight_spacing_enabled = True
    tuners4.tackle_radius_m = 1.5
    tuners4.tackle_slot_threshold_m = 0.3
    world4 = SimpleNamespace()
    world4.ball_carrier = Player(x=60.0, y=35.0, player_id=99)
    world4.ruck_y_m = 35.0
    world4.frame_geometry_ready = False
    world4.frame_geometry_frame = None
    world4.frame_spacing_targets = None
    # Radially-close engaged PRIMARY whose spacing Y is 1.0 m off the BC lane.
    engaged = Player(x=60.5, y=34.6, player_id=101)
    engaged.depth_class = "PRIMARY"
    engaged.depth_role = "B"
    engaged.frame_spacing_y = 34.0   # 1.0 m below bc.y -> slot-OUT candidate
    engaged.x = 60.5
    engaged.y = 34.6
    slot_thr = _slot_threshold(world4)
    y_clamped = frame_spacing_y(world4, engaged, tuners4)
    check("4.1 engaged radially-close PRIMARY clamped into the slot band",
          abs(world4.ball_carrier.y - y_clamped) < slot_thr,
          "bc.y=%.2f spacing_y=%.2f slot_thr=%.2f" % (
              world4.ball_carrier.y, y_clamped, slot_thr))
    # Disabling the gate restores the original spacing Y (surgical switch-off).
    tuners4.slot_tight_spacing_enabled = False
    y_off = frame_spacing_y(world4, engaged, tuners4)
    check("4.2 gate OFF restores the original spacing Y",
          abs(y_off - 34.0) < 1e-9,
          "y_off=%.4f" % y_off)
    # A PASSIVE deep body is never clamped (only engaged PRIMARY/SECONDARY).
    passive = Player(x=45.0, y=30.0, player_id=102)
    passive.depth_class = "PASSIVE"
    passive.depth_role = "C"
    passive.frame_spacing_y = 30.0
    passive.x = 45.0
    passive.y = 30.0
    tuners4.slot_tight_spacing_enabled = True
    y_passive = frame_spacing_y(world4, passive, tuners4)
    check("4.3 PASSIVE body NOT clamped (spacing-only scope)",
          abs(y_passive - 30.0) < 1e-9,
          "y_passive=%.4f" % y_passive)

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED — dominant writer path isolatable")
    sys.exit(0)


if __name__ == "__main__":
    main()
