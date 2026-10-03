"""
verify_preamble_reset_audit.py — Validation for the kickoff frame-stamp reset
and the extended invariant-audit checks (2026-08-12).

Confirms the two deliverables of the defensive-geometry pipeline handoff:

  1. RESET WIRING — OpenPlayEngine.reset_for_kickoff() now calls
     clear_frame_geometry(world), so a frame-geometry dict published in a
     previous kickoff sequence can NEVER read as "fresh" against the new
     frame_count=0 cycle.  (This is the "geometry frame-stamp reset for
     invariant audit; no behavioural effect" wiring.)

  2. AUDIT EXTENSION — game/debug/defender_state_audit.py now reports:
       stale_geometry          — published frame dict is NOT fresh for the
                                 current frame (a writer consumed geometry
                                 without this frame's preamble).
       stale_target_x          — target_x drifts > 3.0 m from the FRESH
                                 frame width dict (width-X writer bug).
       residual_frame_geometry — KICKOFF: frame_geometry_ready is still
                                 True during kickoff flight (prior
                                 sequence's dict not cleared on reset).

Each check runs the REAL audit module (capturing its stdout) against a
minimal WorldState + Player stub, so the checks prove the engine and
audit behaviour together — not a mocked copy.

Exit code 0 = all pass, 1 = any failure.
"""

import io
import sys
from contextlib import redirect_stdout

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, Team
from world.world_state import WorldState, GamePhase, GameSubState
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_pipeline import (
    run_defensive_pipeline,
    frame_geometry_is_fresh,
    clear_frame_geometry,
)
from game.debug.defender_state_audit import (
    investigate_defender_state,
    rearm_defender_state_audit,
)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def make_defender(player_id, x, y):
    d = Player(x=x, y=y, player_id=player_id)
    d.team = Team.RECEIVING
    d.is_defender = True
    d.defender_role = None
    d.target_source = None
    return d


def make_world(phase=GamePhase.OPEN_PLAY, sub_state=GameSubState.OPEN_PLAY):
    """Minimal world stub carrying every field the audit reads."""
    w = WorldState()
    w.ruck_established = True  # STAGE 3B: post-TACKLE_INIT band/role scenario

    w.frame_count = 0
    w.ruck_x_m = 55.0
    w.ruck_y_m = 35.0
    w.def_line_width_m = 40.0
    w.defensive_line_anchor = w.ruck_x_m + 10.0
    w.anchor_locked = True
    w.governor_width_active = False
    w.use_structured_open_play = True
    w.phase = phase
    w.sub_state = sub_state
    w.ball_carrier = None
    w.first_tackle_not_made = False
    w.in_pursuit_mode = False
    w.defence_enabled = True
    w.tes_active = False
    return w


def make_line(world, n=5):
    line = []
    base_y = world.ruck_y_m - 5.0
    for i in range(n):
        d = make_defender(200 + i, world.ruck_x_m + 8.0, base_y + i * 2.5)
        line.append(d)
    return line


def run_audit(world):
    """Run the REAL defender-state audit and return its stdout text."""
    rearm_defender_state_audit()
    buf = io.StringIO()
    with redirect_stdout(buf):
        investigate_defender_state(
            world,
            getattr(world, "defending_team", None) or [],
            getattr(world, "ball_carrier", None),
            getattr(world, "phase", None),
            getattr(world, "sub_state", None),
        )
    return buf.getvalue()


def main():
    print("=" * 64)
    print("PREAMBLE RESET + INVARIANT AUDIT VALIDATION")
    print("=" * 64)

    # ── 1. Engine reset wiring: clear_frame_geometry on kickoff reset ──
    print("\n[1] OpenPlayEngine.reset_for_kickoff() clears frame stamps")
    from world.open_play_engine import OpenPlayEngine

    world = make_world()
    line = make_line(world)
    world.defending_team = line
    world.players = list(line)

    # Simulate a prior sequence: the preamble published fresh dicts.
    world.frame_count = 1
    run_defensive_pipeline(world, line, DefenceTuners())
    check("prior sequence published FRESH geometry",
          frame_geometry_is_fresh(world))
    check("prior sequence left dicts populated",
          bool(getattr(world, "frame_spacing_targets", None))
          and bool(getattr(world, "frame_width_targets", None)))

    # SPACE/R restart path: OPE.reset_for_kickoff() must drop the stamps.
    eng = OpenPlayEngine(world)
    eng.reset_for_kickoff()
    check("frame_geometry_is_fresh False after reset",
          not frame_geometry_is_fresh(world))
    check("dicts are None after reset (no prior-sequence leak)",
          getattr(world, "frame_spacing_targets", None) is None
          and getattr(world, "frame_width_targets", None) is None)

    # ── 2. stale_geometry (dict exists but is NOT fresh) ─────────────
    print("\n[2] Audit detects STALE GEOMETRY (dict stamped last frame)")
    world2 = make_world()
    line2 = make_line(world2)
    world2.defending_team = line2
    world2.players = list(line2)
    world2.frame_count = 5
    run_defensive_pipeline(world2, line2, DefenceTuners())
    for d in line2:
        d.target_source = "test_writer"
    world2.frame_count = 6  # preamble did NOT run for this frame
    out2 = run_audit(world2)
    check("STALE GEOMETRY reported for a stale-dict frame",
          "STALE GEOMETRY" in out2)
    clear_frame_geometry(world2)
    check("no STALE GEOMETRY when the dict is cleared (grace path)",
          "STALE GEOMETRY" not in run_audit(world2))

    # ── 3. stale_target_x (width-X drift vs the fresh frame width dict) ──
    print("\n[3] Audit detects STALE TARGET_X (width-X drift)")
    world3 = make_world()
    line3 = make_line(world3)
    world3.defending_team = line3
    world3.players = list(line3)
    world3.frame_count = 10
    _, width3 = run_defensive_pipeline(world3, line3, DefenceTuners())
    for d in line3:
        d.target_source = "structured_open_play"  # the ONE width-bound writer
        # Compliant X for every defender EXCEPT the first.
        expected_x = width3.get(id(d), d.x)
        d.target_x = expected_x + (5.0 if d.player_id % 2 == 0 else 0.0)
        d.target_y = 35.0  # irrelevant to the X check
    out3 = run_audit(world3)
    check("STALE TARGET_X reported when target_x drifts off the width dict",
          "STALE TARGET_X" in out3)

    # ── 4. residual_frame_geometry (KICKOFF with a leftover stamp) ───
    print("\n[4] Audit detects RESIDUAL FRAME GEOMETRY during KICKOFF")
    world4 = make_world(phase=GamePhase.KICKOFF, sub_state=GameSubState.OPEN_PLAY)
    line4 = make_line(world4, n=2)
    world4.defending_team = line4
    world4.players = list(line4)
    world4.frame_count = 0
    # Simulate the reset bug: a previous sequence left ready=True.
    world4.frame_geometry_ready = True
    world4.frame_geometry_frame = 0
    world4.frame_spacing_targets = {}
    world4.frame_width_targets = {}
    for d in line4:
        d.target_x = d.x
        d.target_y = d.y
        d.target_source = "kmc_chase"
    out4 = run_audit(world4)
    check("RESIDUAL FRAME GEOMETRY reported during kickoff with leftover stamp",
          "RESIDUAL FRAME GEOMETRY" in out4)
    clear_frame_geometry(world4)
    check("no RESIDUAL flag after clear_frame_geometry (fix applied)",
          "RESIDUAL FRAME GEOMETRY" not in run_audit(world4))

    # ── 5. Negative: fully compliant OPEN_PLAY frame fires none ──────
    print("\n[5] Negative — compliant OPEN_PLAY frame fires no new flags")
    world5 = make_world()
    line5 = make_line(world5)
    world5.defending_team = line5
    world5.players = list(line5)
    world5.frame_count = 20
    spacing5, width5 = run_defensive_pipeline(world5, line5, DefenceTuners())
    for d in line5:
        d.target_source = "test_writer"
        d.target_x = width5.get(id(d), d.x)
        d.target_y = spacing5.get(id(d), d.y)
    out5 = run_audit(world5)
    check("no STALE GEOMETRY on a compliant fresh frame",
          "STALE GEOMETRY" not in out5)
    check("no STALE TARGET_X on a compliant fresh frame",
          "STALE TARGET_X" not in out5)
    check("no RESIDUAL FRAME GEOMETRY on a non-kickoff fresh frame",
          "RESIDUAL FRAME GEOMETRY" not in out5)

    # ── Result ────────────────────────────────────────────────────
    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
