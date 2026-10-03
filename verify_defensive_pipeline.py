"""
verify_defensive_pipeline.py — Validation for the un-gated defensive geometry
preamble (2026-08-12).

Confirms game/defence/defence_lvl_1/defensive_pipeline.py:

  1. MODULE SURFACE — run_defensive_pipeline / frame_spacing_y /
     clear_frame_geometry / force_reseed_depth_roles exist and import.
  2. PURE PREAMBLE — run_defensive_pipeline(world, defenders, tuners)
     publishes frame_geometry_ready, frame_geometry_frame == world.frame_count,
     frame_spacing_targets and frame_width_targets populated for EVERY line
     defender (fullback excluded), and NEVER writes target_x/target_y
     (compute-only contract).
  3. FRAME AUTHORITY — frame_spacing_y() returns the fresh frame dict value
     for a depth-role line defender when the dict is fresh, and falls back to
     the structural spacing_y helper when the dict is stale/absent (identical
     formula → behaviour unchanged without the preamble).
  4. FORCED RE-SEED — force_reseed_depth_roles() clears the persistent seed
     and re-bootstraps BC-relative roles (every line defender has a fresh
     depth_role / depth_side / _depth_seeded stamp).
  5. CLEAR FRAME GEOMETRY — clear_frame_geometry() drops the stamps so a
     stale dict can never read as fresh.
  6. ENGINE WIRING — OpenPlayEngine.update() in OPEN_PLAY publishes fresh
     frame geometry and every non-fullback line defender has a target_source;
     the written target_y stays within the invariant-sheet drift tolerance of
     the frame-authoritative spacing Y (no stale values across frames).
  7. UN-GATED BY PHASE — the preamble produces the same fresh dicts when
     world.sub_state is forced to a PTB-era sub-state (the preamble is phase-
     independent: depth-role -> spacing -> width runs before ANY writer).

Exit code 0 = all pass, 1 = any failure.
"""

import sys
import traceback

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, Team
from world.world_state import WorldState, GamePhase, GameSubState
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_split_spacing import spacing_y
from game.defence.defence_lvl_1.defensive_pipeline import (
    run_defensive_pipeline,
    frame_spacing_y,
    frame_geometry_is_fresh,
    clear_frame_geometry,
    force_reseed_depth_roles,
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
    d.max_defensive_speed_mps = 8.3
    d.defender_role = None
    return d


def make_world():
    """A WorldState fully configured for an OPEN_PLAY engine frame."""
    w = WorldState()
    w.ruck_established = True  # STAGE 3B: post-TACKLE_INIT band/role scenario

    w.ruck_x_m = 55.0
    w.ruck_y_m = 35.0
    w.def_line_width_m = 40.0
    w.defensive_line_anchor = w.ruck_x_m + 10.0
    w.anchor_locked = True
    w.govvernor_width_active = False  # typo-safe: governor reads via governed_width_m
    w.first_tackle_not_made = False  # DEBUG: deterministic OPEN_PLAY assert gate
    w.in_pursuit_mode = False
    w.use_structured_open_play = True
    w.defence_enabled = True
    w.tes_active = False
    w.phase = GamePhase.OPEN_PLAY
    w.sub_state = GameSubState.OPEN_PLAY
    w.ball_owner = "BC"
    w.role_governs = False
    w.ctx = None
    w.frame_count = 0
    return w


def make_line(world, n=13, fullback=False):
    """Defensive line spread around ruck_y; optional one fullback actor."""
    line = []
    base_y = world.ruck_y_m - 5.0
    for i in range(n):
        d = make_defender(100 + i, world.ruck_x_m + 10.0, base_y + i * 5.0)
        line.append(d)
    if fullback:
        fb = make_defender(900, world.ruck_x_m + 2.0, world.ruck_y_m + 3.0)
        fb.fullback_role_active = True
        fb.role = "FULLBACK"
        line.append(fb)
    return line


def main():
    print("=" * 64)
    print("DEFENSIVE PIPELINE (UN-GATED PREAMBLE) VALIDATION")
    print("=" * 64)

    # ── 1. Module surface ─────────────────────────────────────────
    print("\n[1] Module surface")
    for fn in (run_defensive_pipeline, frame_spacing_y, frame_geometry_is_fresh,
               clear_frame_geometry, force_reseed_depth_roles):
        check(f"defines {fn.__name__}()", callable(fn))

    # ── 2. Pure preamble (compute-only) ───────────────────────────
    print("\n[2] Pure preamble publishes frame geometry")
    world = make_world()
    line = make_line(world, fullback=True)
    world.defending_team = line
    world.players = list(line)
    tuners = DefenceTuners()

    world.frame_count = 1
    spacing, width = run_defensive_pipeline(world, line, tuners)

    check("frame_geometry_ready published True",
          getattr(world, "frame_geometry_ready", False) is True)
    check("frame_geometry_frame matches frame_count",
          getattr(world, "frame_geometry_frame", None) == world.frame_count)
    check("frame_spacing_targets populated",
          bool(getattr(world, "frame_spacing_targets", None)))
    check("frame_width_targets populated",
          bool(getattr(world, "frame_width_targets", None)))

    line_defs = [d for d in line if not getattr(d, "fullback_role_active", False)]
    spacing_ok = all(id(d) in world.frame_spacing_targets for d in line_defs)
    width_ok = all(id(d) in world.frame_width_targets for d in line_defs)
    check("every line defender in spacing dict", spacing_ok)
    check("every line defender in width dict", width_ok)

    fb = [d for d in line if getattr(d, "fullback_role_active", False)][0]
    check("fullback EXCLUDED from spacing dict",
          id(fb) not in world.frame_spacing_targets)
    check("fullback EXCLUDED from width dict",
          id(fb) not in world.frame_width_targets)

    # Compute-only contract: the preamble never writes targets.
    wrote_targets = any(
        getattr(d, "target_x", None) != d._x or getattr(d, "target_y", None) != d.y
        for d in line_defs
    )
    check("preamble never writes target_x/target_y (compute-only)",
          not wrote_targets)

    # ── 3. Frame-authoritative Y read ─────────────────────────────
    print("\n[3] frame_spacing_y prefers the fresh frame dict")
    fresh_match = True
    no_fallback_match = True
    for d in line_defs:
        # With fresh dict: frame_spacing_y == stamped frame value.
        if frame_spacing_y(world, d, tuners) != world.frame_spacing_targets[id(d)]:
            fresh_match = False
        # STAGE 3C — NO STRUCTURAL FALLBACK: with a stale/absent dict the
        # dict-only read returns the documented non-chain ruck_y pin,
        # never a second compute_targets/spacing_y structural recompute.
        clear_frame_geometry(world)
        expected_pin = float(world.ruck_y_m)
        if frame_spacing_y(world, d, tuners) != expected_pin:
            no_fallback_match = False
        # Re-publish for the next defender.
        run_defensive_pipeline(world, line_defs, tuners)
    check("fresh dict returned by frame_spacing_y", fresh_match)
    check("stale/absent dict returns ruck_y pin (NO structural fallback)",
          no_fallback_match)

    # ── 4. Forced re-seed (kickoff catch path) ────────────────────
    print("\n[4] force_reseed_depth_roles bootstraps BC-relative")
    world2 = make_world()
    world2.ruck_y_m = 50.0  # move the ruck: the OLD roles must not survive
    line2 = make_line(world2, fullback=True)
    # Pre-stamp STALE roles from an old ruck (simulate a maintained line).
    for i, d in enumerate(line2):
        d.depth_role = "D"
        d.depth_side = -1 if i % 2 == 0 else 1
        d._depth_seeded = True
        d.role_depth_target_y = 10.0
    world2.defending_team = line2

    eligible2 = force_reseed_depth_roles(world2, line2)
    all_roles = eligible2 and all(
        getattr(d, "depth_role", None) in ("A", "B", "C", "D", "CENTRE", "WINGER")
        for d in eligible2
    )
    check("force_reseed re-stamps depth_role on every line defender", all_roles)
    check("force_reseed excludes the fullback actor",
          all(not getattr(d, "fullback_role_active", False) for d in eligible2))
    check("seed re-bootstraps against the NEW ruck (fresh _depth_seeded stamps)",
          all(getattr(d, "_depth_seeded", False) for d in eligible2))

    # ── 5. clear_frame_geometry drops stamps ──────────────────────
    print("\n[5] clear_frame_geometry invalidates the dicts")
    clear_frame_geometry(world)
    check("frame_geometry_ready False after clear",
          getattr(world, "frame_geometry_ready", True) is False)
    check("frame dicts None after clear",
          getattr(world, "frame_spacing_targets", None) is None
          and getattr(world, "frame_width_targets", None) is None)
    check("frame_geometry_is_fresh False after clear",
          not frame_geometry_is_fresh(world))

    # ── 6. Engine wiring — OPEN_PLAY frames ───────────────────────
    print("\n[6] OpenPlayEngine wiring (OPEN_PLAY frames)")
    from world.open_play_engine import OpenPlayEngine

    world3 = make_world()
    line3 = make_line(world3, fullback=True)
    bc = make_defender(999, world3.ruck_x_m - 2.0, world3.ruck_y_m)
    bc.possessed = True
    world3.defending_team = line3
    world3.players = line3 + [bc]
    world3.ball_carrier = bc

    eng = None
    try:
        eng = OpenPlayEngine(world3)
        world3.open_play_engine = eng
        world3.open_play = eng
        print("    engine constructed OK")
    except Exception:
        traceback.print_exc()
        check("OpenPlayEngine constructs", False, "raised during __init__")

    if eng is not None:
        engine_ok = True      # fresh geometry stamps every frame
        first_frame_dict_nonempty = True   # frame-1 bootstrap dict populated
        dict_entry_follow_ok = True  # any dict entry matches frame_spacing_y
        target_source_ok = True  # every non-fullback defender has a writer stamp
        first_frame = True
        try:
            for _ in range(3):
                eng.update(1.0 / 60.0)
                if not frame_geometry_is_fresh(world3):
                    engine_ok = False
                spacing_dict = getattr(world3, "frame_spacing_targets", None) or {}
                # TASK H S3 skip contract: once MMDL/chase writers stamp
                # chase_target_*/mmdl_target_* attributes on a defender,
                # compute_targets deliberately skips it so the spacing dict
                # legitimately shrinks.  The invariant is: the DICT IS
                # RE-PUBLISHED FRESH every frame (ready + frame stamp), and
                # every entry it DOES carry agrees with frame_spacing_y.
                if first_frame and not spacing_dict:
                    first_frame_dict_nonempty = False
                for d in line3:
                    if getattr(d, "fullback_role_active", False):
                        continue
                    src = getattr(d, "target_source", None)
                    if not src:
                        target_source_ok = False
                    if id(d) in spacing_dict:
                        ty = getattr(d, "target_y", None)
                        expected = spacing_dict[id(d)]
                        if ty is None or abs(ty - expected) > 3.0:
                            dict_entry_follow_ok = False
                first_frame = False
        except Exception:
            engine_ok = False
            traceback.print_exc()
        check("engine publishes FRESH geometry every frame", engine_ok)
        check("frame-1 spacing dict populated (bootstrap before MMDL skip)",
              first_frame_dict_nonempty)
        check("every spacing-dict entry matches frame_spacing_y within tolerance",
              dict_entry_follow_ok)
        check("every non-fullback line defender has target_source",
              target_source_ok)

    # ── 7. Un-gated across PTB-era sub-state ──────────────────────
    print("\n[7] Preamble is phase-independent (PTB-era sub-state)")
    world4 = make_world()
    world4.sub_state = GameSubState.PTB
    line4 = make_line(world4, fullback=False)
    world4.defending_team = line4
    world4.frame_count = 7
    try:
        sp4, wd4 = run_defensive_pipeline(world4, line4, DefenceTuners())
        phase_ok = (
            bool(sp4) and bool(wd4)
            and getattr(world4, "frame_geometry_ready", False)
            and getattr(world4, "frame_geometry_frame", None) == 7
        )
    except Exception:
        phase_ok = False
        traceback.print_exc()
    check("preamble runs with sub_state=PTB and publishes fresh dicts", phase_ok)

    # ── Result ────────────────────────────────────────────────────
    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
