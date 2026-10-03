"""verify_tes_diagnostics.py — focused verifier for the TES Y-slot lock +
off-by-default TES telemetry (rollout 2026-09-07).

Reproduces the mechanism the fix adds:

  TES Y-SLOT LOCK (2026-09-07): during TACKLE_INIT / TACKLE_CONTACT the
  ruck-adjacent pin previously SKIPPED entirely.  The fix keeps the slot-1
  (A-pivot) and slot-2 Y TARGETS locked to their structural values while
  leaving position/velocity owned by TES stasis (target-only — no snap, no
  HOLD clear).  DRIVE/DOMINANCE were subsequently moved into the same
  TARGET-ONLY set too (``ruck_adjacent_pin._TES_TARGET_LOCK_PHASES``): the
  full position snap + band clamp during the drive re-enabled the
  outside-defender teleport, so the pin now only LOCKS targets across the
  whole TES window and POSITION ownership returns in the REFORM window
  (RELEASE / PTB_* / POST_RUCK_OPEN_PLAY), where convergence is capped by
  ``RUCK_PIN_GLIDE_SPEED_MPS`` (``verify_ruck_pin_glide.py``).

  TES TELEMETRY (off-by-default, redesign 2026-09-07): RLM_TES_DIAGNOSTICS=1
  (or ``world._tes_diagnostics``) arms a lean collector that records ONLY
  three streams — SAMPLED per-TES summary rows (one per phase boundary /
  every TES_SAMPLE_EVERY frames), COMPACT suppressed-tackle events, and a
  PER-SLOT override ring — with flush-time ID redaction and size rotation.
  Without the env var / collector the pin is byte-identical (no collector is
  created, no full-state dump is produced).

Run:  python verify_tes_diagnostics.py
Exit 0 on success; prints a PASS/FAIL line per assertion.
"""
import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from world.world_state import WorldState, GameSubState
from game.defence.defence_lvl_1.ruck_adjacent_pin import (
    enforce_ruck_adjacent_pin,
    ruck_adjacent_anchor,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.ruck_adjacent_pin import (
    RUCK_PIN_SLOT2_MIN_GAP_M,
    RUCK_PIN_SLOT2_MAX_GAP_M,
    RUCK_PIN_GLIDE_SPEED_MPS,
)


failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def make_defender(pid, x, y, depth_role=None, defender_role=None,
                  fullback=False, tackle_state=False, frozen=False):
    d = Player(x=x, y=y, player_id=pid)
    d.depth_role = depth_role
    d.depth_side = -1 if y < 32.86 else 1
    d.defender_role = defender_role
    d.fullback_role_active = fullback
    d.tackle_state_active = tackle_state
    d.frozen = frozen
    d.freeze_timer = 0.0
    d.target_x = x
    d.target_y = y
    d.vx = 0.0
    d.vy = 0.0
    return d


def make_world(ruck_y, defenders, frame_spacing_targets=None, phase_label=""):
    w = WorldState()
    w.ruck_y_m = ruck_y
    w.ruck_x_m = 50.0
    w.defending_team = defenders
    w.frame_spacing_targets = frame_spacing_targets or {}
    w._ope_phase_label = phase_label
    w.sub_state = GameSubState.OPEN_PLAY
    w.tackler_ids = set()
    w.frame_count = 0
    # Deterministic harness state: NO drive-end settle window is armed (so
    # the pin's target-only override is driven purely by the phase label),
    # and the pin reads its capped-convergence step from ``world.dt``.
    w.post_tes_settle_frames = 0
    w.post_tes_settle_enabled = False
    w.dt = 1.0 / 60.0
    return w


def main():
    print("=" * 64)
    print("TES Y-SLOT LOCK + TELEMETRY VERIFIER (2026-09-07)")
    print("=" * 64)

    ruck_y = 32.86
    anchor_left = ruck_adjacent_anchor(ruck_y, LEFT)   # 30.86
    band_lo = min(anchor_left + LEFT * RUCK_PIN_SLOT2_MIN_GAP_M,
                  anchor_left + LEFT * RUCK_PIN_SLOT2_MAX_GAP_M)  # 27.36
    band_hi = max(anchor_left + LEFT * RUCK_PIN_SLOT2_MIN_GAP_M,
                  anchor_left + LEFT * RUCK_PIN_SLOT2_MAX_GAP_M)  # 29.86

    # ── TEST 1: TES INIT target-only Y lock (no position snap) ──
    print("\n[1] TACKLE_INIT: slot-1 target locked, position NOT snapped")
    a_pivot = make_defender(1, x=50.0, y=27.0, depth_role="A")  # far from anchor
    a_pivot.target_y = 27.0
    b_body = make_defender(2, x=50.0, y=30.40, depth_role="B")  # collapsed slot-2
    b_body.target_y = 30.40
    w = make_world(ruck_y, [a_pivot, b_body],
                   frame_spacing_targets={id(a_pivot): 27.0, id(b_body): 30.40},
                   phase_label="TACKLE_INIT")
    enforce_ruck_adjacent_pin(w)
    check("1.1 A-pivot target_y locked to anchor (30.86)", abs(a_pivot.target_y - anchor_left) < 1e-6,
          "target_y=%.2f (expected %.2f)" % (a_pivot.target_y, anchor_left))
    check("1.2 A-pivot position NOT snapped (kept 27.0)", abs(a_pivot.y - 27.0) < 1e-6,
          "y=%.2f (expected 27.0 — TES stasis owns position)" % a_pivot.y)
    check("1.3 B slot-2 target clamped to band_hi (29.86)",
          abs(b_body.target_y - band_hi) < 1e-6,
          "target_y=%.2f (expected %.2f)" % (b_body.target_y, band_hi))
    check("1.4 B slot-2 position NOT mutated (kept 30.40)", abs(b_body.y - 30.40) < 1e-6,
          "y=%.2f (expected 30.40 — target-only)" % b_body.y)

    # ── TEST 2: TES DRIVE is TARGET-ONLY (position owned by TES) ──
    # TACKLE_DRIVE / TACKLE_DOMINANCE are in
    # ruck_adjacent_pin._TES_TARGET_LOCK_PHASES: across the drive window the
    # pin LOCKS the slot Y TARGETS to their structural values and NEVER
    # writes position — TES stasis / drive-depth-track owns position and
    # velocity.  (The full reform snap now lives in RELEASE/PTB — TEST 2b.)
    print("\n[2] TACKLE_DRIVE: target-only Y lock, position NOT mutated")
    a_pivot2 = make_defender(11, x=50.0, y=27.0, depth_role="A")
    a_pivot2.target_y = 27.0
    b_body2 = make_defender(12, x=50.0, y=30.40, depth_role="B")
    b_body2.target_y = 30.40
    w2 = make_world(ruck_y, [a_pivot2, b_body2],
                    frame_spacing_targets={id(a_pivot2): 27.0, id(b_body2): 30.40},
                    phase_label="TACKLE_DRIVE")
    enforce_ruck_adjacent_pin(w2)
    check("2.1 DRIVE A-pivot target_y pinned to anchor", abs(a_pivot2.target_y - anchor_left) < 1e-6,
          "target_y=%.2f (expected %.2f)" % (a_pivot2.target_y, anchor_left))
    check("2.2 DRIVE A-pivot position NOT mutated (TES owns position)",
          abs(a_pivot2.y - 27.0) < 1e-6,
          "y=%.2f (expected 27.0 — TARGET-ONLY lock)" % a_pivot2.y)
    check("2.3 DRIVE B slot-2 target clamped into band, position NOT mutated",
          abs(b_body2.y - 30.40) < 1e-6
          and band_lo - 1e-6 <= b_body2.target_y <= band_hi + 1e-6,
          "y=%.2f target_y=%.2f (expected y=30.40, target in [%.2f, %.2f])"
          % (b_body2.y, b_body2.target_y, band_lo, band_hi))

    # ── TEST 2b: the REFORM window still OWNS convergence ──
    # At RELEASE the pin takes position ownership again: a body within
    # RUCK_PIN_GLIDE_THRESHOLD_M of its lane snaps, a body outside the
    # band is pulled back by at most RUCK_PIN_GLIDE_SPEED_MPS * dt.
    print("\n[2b] RELEASE: reform-window pin owns convergence (capped)")
    a_pivot2b = make_defender(13, x=50.0, y=anchor_left - 0.2, depth_role="A")
    a_pivot2b.target_y = anchor_left - 0.2
    b_body2b = make_defender(14, x=50.0, y=band_hi + 0.2, depth_role="B")
    b_body2b.target_y = band_hi + 0.2
    w2b = make_world(ruck_y, [a_pivot2b, b_body2b],
                     frame_spacing_targets={}, phase_label="RELEASE")
    enforce_ruck_adjacent_pin(w2b)
    _max_step2b = RUCK_PIN_GLIDE_SPEED_MPS * w2b.dt
    check("2b.1 RELEASE A-pivot position SNAPPED to anchor (within threshold)",
          abs(a_pivot2b.y - anchor_left) < 1e-6,
          "y=%.2f (expected %.2f — reform snap)" % (a_pivot2b.y, anchor_left))
    check("2b.2 RELEASE slot-2 pulled back toward the band (capped, no teleport)",
          abs(b_body2b.y - band_hi) <= _max_step2b + 1e-6
          and abs(b_body2b.y - (band_hi + 0.2)) <= _max_step2b + 1e-6,
          "y=%.4f (band_hi=%.2f before=%.2f cap=%.4f)"
          % (b_body2b.y, band_hi, band_hi + 0.2, _max_step2b))

    # ── TEST 3: telemetry OFF by default (no collector created) ──
    print("\n[3] telemetry off-by-default: no collector without env")
    os.environ.pop("RLM_TES_DIAGNOSTICS", None)
    if hasattr(w, "_tes_diagnostics"):
        del w._tes_diagnostics
    enforce_ruck_adjacent_pin(w)
    check("3.1 world._tes_diagnostics NOT created without env",
          not hasattr(w, "_tes_diagnostics"))
    check("3.2 world._slot_override_rings NOT created without env",
          not hasattr(w, "_slot_override_rings"))

    # ── TEST 4: telemetry ON captures a SAMPLED summary (dict store) ──
    print("\n[4] RLM_TES_DIAGNOSTICS=1 captures sampled per-TES summary")
    os.environ["RLM_TES_DIAGNOSTICS"] = "1"
    a_pivot4 = make_defender(21, x=50.0, y=30.86, depth_role="A")
    a_pivot4.target_y = 30.86
    b_body4 = make_defender(22, x=50.0, y=30.0, depth_role="B")
    b_body4.target_y = 30.0
    marker4 = make_defender(23, x=50.0, y=32.86,
                            defender_role=DefenderRole.MARKER_A)
    w4 = make_world(ruck_y, [a_pivot4, b_body4, marker4],
                    frame_spacing_targets={id(a_pivot4): 30.86,
                                           id(b_body4): 30.0},
                    phase_label="TACKLE_INIT")
    enforce_ruck_adjacent_pin(w4)  # phase TACKLE_INIT -> TES
    store = getattr(w4, "_tes_diagnostics", None)
    check("4.1 collector is a dict with summary/suppressed/rings",
          isinstance(store, dict)
          and "summary" in store and "suppressed" in store and "rings" in store,
          "type=%s" % type(store).__name__)
    summaries = store.get("summary", []) if isinstance(store, dict) else []
    check("4.2 summary holds exactly one SAMPLED row",
          len(summaries) == 1, "n=%s" % len(summaries))
    if summaries:
        rec = summaries[0]
        check("4.3 summary phase is TACKLE_INIT", rec.get("phase") == "TACKLE_INIT",
              "phase=%s" % rec.get("phase"))
        check("4.4 summary L1 = A-pivot (id=21) on LEFT",
              rec.get("L1_id") == 21, "L1_id=%s" % rec.get("L1_id"))
        check("4.5 summary L1 role/lane (A -> 30.86)",
              rec.get("L1_role") == "A"
              and abs(float(rec.get("L1_target_y", -1.0)) - anchor_left) < 1e-6,
              "L1_role=%s L1_target_y=%s" % (rec.get("L1_role"),
                                             rec.get("L1_target_y")))
        check("4.6 summary slot-2 = B (id=22)",
              rec.get("L2_id") == 22, "L2_id=%s" % rec.get("L2_id"))
        # No full-state dump: the record must NOT carry lanes/markers/slots arrays.
        check("4.7 summary has NO per-entity full-state arrays",
              "lanes" not in rec and "markers" not in rec and "slots" not in rec)

    # ── TEST 5: flush_tes produces a parseable, redacted CSV ──
    print("\n[5] flush_tes writes a parseable CSV (summary/suppressed/override)")
    import tempfile
    from game.debug.tes_diagnostics import (
        flush_tes,
        record_suppressed_tackle,
        record_override,
        TES_OVERRIDE_RING_LEN,
    )
    # Add one suppressed event + one override row so all three kinds exist.
    bc = Player(x=60.0, y=35.5, player_id=99)
    bc.y = 35.5
    d_out = Player(x=60.5, y=35.1, player_id=4)
    d_out.defender_role = DefenderRole.DEF_LINE_READY
    d_out.target_y = 35.0
    record_suppressed_tackle(w4, 7, "OPEN_PLAY", d_out, bc)
    record_override(w4, "L1", 7, {
        "writer": 1, "priority": 200, "proposed_y": 35.0,
        "current_target_y": 30.0, "override_frames": 3,
    })
    tmp = tempfile.mktemp(suffix=".csv")
    path = flush_tes(w4, tmp)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    lines = content.splitlines()
    check("5.1 CSV has header row starting with frame,phase,ruck_x,ruck_y,kind",
          lines and lines[0].startswith("frame,phase,ruck_x,ruck_y,kind"),
          "header=%s" % (lines[0] if lines else "<empty>"))
    check("5.2 CSV column count matches header",
          len(lines[0].split(",")) ==
          len("frame,phase,ruck_x,ruck_y,kind,n_left,n_right,"
              "L1_id,L1_role,L1_y,L1_target_y,L2_id,L2_role,L2_y,L2_target_y,"
              "defender_role,bc_y,slot_target_y,radial_dist,slot_dist,"
              "tackle_role,depth_side,target_source,"
              "slot_key,writer,priority,proposed_y,current_target_y,"
              "override_frames".split(",")),
          "n=%d" % len(lines[0].split(",")))
    check("5.3 CSV contains summary/suppressed/override kinds",
          ",summary," in content and ",suppressed," in content and ",override," in content)
    # Redaction: the writer id 1 must be redacted to "#...", never a bare "1,".
    check("5.4 writer id redacted (no bare writer id in CSV)",
          "\n,1," not in content and "1,200" not in content)
    os.remove(path)
    os.environ.pop("RLM_TES_DIAGNOSTICS", None)

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
