"""
verify_open_play_tackle_radius_chase.py — OPEN_PLAY Tackle-Radius CHASE Switch verifier.

Confirms the ship-text fix by static inspection + runtime probe:

  A. `movement_mode_decision.decide_movement_state` contains the
     tackle-radius CHASE switch: a genuine line defender with
     dist(defender, bc) < tackle_radius_m returns CHASE (after the
     dedicated-owner guards, before the convergence/pursuit/retreat
     branches).  `_is_genuine_line_defender` scopes the override to
     genuine line bodies only (unfrozen, non-fullback, non-marker,
     non-DH, non-TES-tackler, non-kick-chase, non-tackle-state).
  B. `defensive_behaviour_unified` imports `_is_genuine_line_defender`
     and includes `_tackle_radius_chase` in the `chase_override` so the
     LINE GATE does not demote a within-radius defender to DRIFT, and
     the direct-resolve path forces CHASE for it.
  C. `pre_tackle_authority.apply_pre_tackle` promotes a managed
     SLOW/HOLD body within the tackle radius to COMMIT (which produces
     CHASE movement_state) so the pre-tackle authority never leaves a
     managed body in DRIFT/freeze while inside contact range.
  D. Runtime probe:
       - genuine line defender within tackle radius -> CHASE;
       - fullback within tackle radius -> DRIFT (dedicated logic kept);
       - marker within tackle radius -> HOLD (dedicated owner kept);
       - genuine line defender outside radius (pursuit off) -> NOT
         forced to CHASE by the switch (RETREAT from retreat compliance);
       - managed SLOW body within radius -> promoted to COMMIT by
         apply_pre_tackle.
  E. Engine module tree imports cleanly.

Run:  python verify_open_play_tackle_radius_chase.py
Exit 0 on success, 1 on failure.
"""
import math
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

MMDL = "game/defence/defence_lvl_1/movement_mode_decision.py"
DBU = "game/defence/defence_lvl_1/defensive_behaviour_unified.py"
PTA = "game/defence/defence_lvl_1/pre_tackle_authority.py"

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _make_tuners():
    t = SimpleNamespace()
    t.tackle_radius_m = 1.5
    t.pursuit_commit_radius_m = 12.0
    t.retreat_compliance_distance_m = 10.0
    t.deep_passive_distance_m = 15.0
    t.lateral_slide_threshold_m = 3.0
    t.engage_distance_m = 20.0
    t.commit_distance_m = 8.0
    t.pre_tackle_trigger_radius_m = 2.0
    t.pretackle_slow_speed_mps = 2.5
    t.pretackle_freeze_radius_m = 0.15
    t.pretackle_follow_smooth = 0.6
    t.pretackle_commit_speed_mps = 4.0
    t.pretackle_plant_radius_m = 0.8
    t.pretackle_slowdown_band_m = 3.0
    t.pretackle_slowdown_scale = 0.45
    t.pretackle_commit_depth_offset_m = 1.0
    t.pretackle_commit_lateral_offset_m = 1.0
    t.pretackle_phase_tuners = {}
    t.pretackle_attempt_enabled = True
    return t


def _make_world(bc):
    w = SimpleNamespace()
    w.ball_carrier = bc
    w.sub_state = SimpleNamespace(name="OPEN_PLAY")
    w.ruck_x_m = 60.0
    w.ruck_y_m = 35.0
    w.defensive_line_anchor = 70.0  # ruck_x + 10
    w.tes_active = False
    w.frame_count = 0
    w.post_catch_chase_window_s = 0.0
    w.frame_convergence_targets = {}
    # ── STALE-SEED HOLD (2026-09-09) init ──
    # `decide_movement_state` force-holds a line body whose spacing-Y /
    # depth-role seed is stale (`is_seed_stale`).  MockBody predates this
    # gate, so give the harness a fresh frame-geometry dict + ready stamp
    # (per body registered below) so the D-tests reach the tackle-radius
    # CHASE switch instead of being held at the seed gate.
    w.ruck_established = True
    w.frame_geometry_ready = True
    w.frame_geometry_frame = 0
    w.frame_spacing_targets = {}
    return w


def _seed_register(world, d):
    """Register *d* in the harness's fresh frame-spacing dict."""
    world.frame_spacing_targets[id(d)] = float(d.y)
    return world


class MockBody:
    """Minimal player-like body for the runtime probe."""

    def __init__(self, pid, x, y):
        from player.player import DefenderRole
        self.player_id = pid
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self.target_x = x
        self.target_y = y
        self.frozen = False
        self.freeze_timer = 0.0
        self.fullback_role_active = False
        self.tackle_state_active = False
        self.defender_role = DefenderRole.DEF_LINE_READY
        self.depth_class = "PASSIVE"
        self.move_speed = 0.0
        self.movement_state = None
        self.facing = 0.0
        self.is_tackler = False
        self._tackle_commit_active = False
        # ── STALE-SEED HOLD (2026-09-09) init ──
        # `decide_movement_state` force-holds a line body whose spacing-Y /
        # depth-role seed is stale (`is_seed_stale`).  Give the harness a
        # healthy line-body seed so the D-tests reach the tackle-radius
        # CHASE switch instead of being held at the seed gate.
        self.depth_role = "D"
        self.depth_side = 1
        self._depth_seeded = True
        self.role_depth_target_x = x
        self.role_depth_target_y = y
        self.frame_spacing_y = y

    def set_facing(self, angle):
        self.facing = angle


def main():
    print("=" * 64)
    print("OPEN_PLAY TACKLE-RADIUS CHASE SWITCH VERIFIER")
    print("=" * 64)

    mmdl = read(MMDL)
    dbu = read(DBU)
    pta = read(PTA)

    # ══════════════════════════════════════════════════════════════
    # A. MMDL tackle-radius CHASE switch
    # ══════════════════════════════════════════════════════════════
    print("\n[A] decide_movement_state has the tackle-radius CHASE switch")
    check("A1 _is_genuine_line_defender helper exists",
          "_is_genuine_line_defender" in mmdl)
    check("A2 tackle_radius_m is read via _tuner with 1.5 default",
          re.search(r"_tackle_r = _tuner\(tuners, \"tackle_radius_m\", (_DEFAULT_TACKLE_RADIUS_M|1\.5)\)", mmdl) is not None)
    check("A3 tackle-radius switch returns CHASE on radius breach",
          re.search(r"if _is_genuine_line_defender\(defender\):\s*\n\s*_tackle_r[\s\S]*?return MovementState\.CHASE", mmdl) is not None)
    check("A4 switch is AFTER the marker guard (dedicated owners kept)",
          mmdl.index("if role is not None and getattr(role, \"name\", role) in (\"MARKER_A\", \"MARKER_B\"):") < mmdl.index("if _is_genuine_line_defender(defender):"))
    check("A5 switch is BEFORE the convergence guard",
          mmdl.index("if _is_genuine_line_defender(defender):") < mmdl.index("frame_convergence_targets"))
    check("A6 _is_genuine_line_defender excludes fullback/marker/DH/tackler/kick-chase",
          "fullback_role_active" in mmdl.split("_is_genuine_line_defender")[1]
          and "_TACKLE_RADIUS_EXEMPT_ROLE_NAMES" in mmdl)
    check("A7 _is_genuine_line_defender excludes tackle-state holders",
          "tackle_state_active" in mmdl.split("_is_genuine_line_defender")[1])

    # ══════════════════════════════════════════════════════════════
    # B. defensive_behaviour_unified honours the switch
    # ══════════════════════════════════════════════════════════════
    print("\n[B] defensive_behaviour_unified honours the CHASE switch")
    check("B1 imports _is_genuine_line_defender",
          "_is_genuine_line_defender" in dbu.split("from game.defence.defence_lvl_1.movement_mode_decision import")[1].split(")")[0])
    check("B2 _tackle_radius_chase is computed per-defender",
          "_tackle_radius_chase = False" in dbu)
    check("B3 _tackle_radius_chase uses _is_genuine_line_defender",
          "_is_genuine_line_defender(d)" in dbu)
    check("B4 _tackle_radius_chase reads tackle_radius_m from tuners",
          "getattr(self.tuners, \"tackle_radius_m\", 1.5)" in dbu)
    check("B5 _tackle_radius_chase is folded into chase_override",
          re.search(r"chase_override = \(\s*carrier_past\s*or _in_post_catch_chase_window\(world\)\s*or _tackle_radius_chase", dbu) is not None)

    # ══════════════════════════════════════════════════════════════
    # C. pre_tackle_authority promotes within-radius SLOW/HOLD to COMMIT
    # ══════════════════════════════════════════════════════════════
    print("\n[C] apply_pre_tackle promotes within-radius SLOW/HOLD to COMMIT")
    check("C1 tackle-radius promotion block exists in apply_pre_tackle",
          re.search(r"TACKLE-RADIUS CHASE OVERRIDE[\s\S]*?PreTackleState\.SLOW, PreTackleState\.HOLD", pta) is not None)
    check("C2 promotion reads tackle_radius_m with 1.5 default",
          'getattr(tuners, "tackle_radius_m", 1.5)' in pta)
    check("C3 promotion syncs COMMIT shims",
          "_sync_shims(d, PreTackleState.COMMIT)" in pta)

    # ══════════════════════════════════════════════════════════════
    # D. Runtime probe
    # ══════════════════════════════════════════════════════════════
    print("\n[D] runtime probe")
    from game.defence.defence_lvl_1.movement_mode_decision import (
        decide_movement_state,
    )
    from game.defence.defence_lvl_1.pre_tackle_authority import (
        apply_pre_tackle,
        PreTackleState,
    )
    from player.movement_states import MovementState

    bc = MockBody(99, 60.0, 35.0)
    tuners = _make_tuners()
    world = _make_world(bc)

    # D1: genuine line defender within tackle radius -> CHASE.
    d1 = MockBody(1, 61.0, 35.0)  # dist 1.0 < 1.5
    _seed_register(world, d1)
    s1 = decide_movement_state(world, d1, bc, tuners)
    check("D1 genuine line defender within radius -> CHASE",
          s1 is MovementState.CHASE,
          "state=%s" % getattr(s1, "name", s1))

    # D2: fullback within radius -> DRIFT (dedicated logic kept).
    d2 = MockBody(2, 61.0, 35.0)
    d2.fullback_role_active = True
    _seed_register(world, d2)
    s2 = decide_movement_state(world, d2, bc, tuners)
    check("D2 fullback within radius -> DRIFT (not CHASE)",
          s2 is MovementState.DRIFT,
          "state=%s" % getattr(s2, "name", s2))

    # D3: marker within radius -> HOLD (dedicated owner kept).
    from player.player import DefenderRole
    d3 = MockBody(3, 61.0, 35.0)
    d3.defender_role = DefenderRole.MARKER_A
    _seed_register(world, d3)
    s3 = decide_movement_state(world, d3, bc, tuners)
    check("D3 marker within radius -> HOLD (not CHASE)",
          s3 is MovementState.HOLD,
          "state=%s" % getattr(s3, "name", s3))

    # D4: genuine line defender OUTSIDE radius, pursuit off -> NOT forced CHASE.
    tuners_far = _make_tuners()
    tuners_far.pursuit_commit_radius_m = 0.0  # disable pursuit override
    d4 = MockBody(4, 63.0, 35.0)  # dist 3.0 > 1.5
    d4.depth_class = "PASSIVE"
    _seed_register(world, d4)
    s4 = decide_movement_state(world, d4, bc, tuners_far)
    check("D4 line defender outside radius (pursuit off) -> NOT CHASE from switch",
          s4 is not MovementState.CHASE,
          "state=%s" % getattr(s4, "name", s4))

    # D5: managed SLOW body within radius -> promoted to COMMIT by apply_pre_tackle.
    d5 = MockBody(5, 61.0, 35.0)  # dist 1.0 < 1.5
    d5.pre_tackle_state = PreTackleState.SLOW
    d5._pta_anchor_x = 65.0
    d5._pta_anchor_y = 35.0
    d5._pta_commit_x = 61.0
    d5._pta_commit_y = 35.0
    world5 = _make_world(bc)
    managed5 = apply_pre_tackle(world5, [d5], 1.0 / 60.0, tuners, frozen_mode=False)
    check("D5 managed SLOW body within radius -> COMMIT",
          getattr(d5, "pre_tackle_state", None) is PreTackleState.COMMIT,
          "state=%s" % getattr(d5, "pre_tackle_state", None))
    check("D6 promoted body is in the managed list",
          d5 in managed5,
          "in managed=%s" % (d5 in managed5))

    # ══════════════════════════════════════════════════════════════
    # E. Engine imports cleanly
    # ══════════════════════════════════════════════════════════════
    print("\n[E] engine module tree imports cleanly")
    import importlib
    mods = [
        "world.open_play_engine",
        "game.managers.defence_manager",
        "game.defence.defence_lvl_1.pre_tackle_authority",
        "game.defence.defence_lvl_1.defensive_behaviour_unified",
        "game.defence.defence_lvl_1.movement_mode_decision",
        "world.rules.movement_contracts",
    ]
    ok = True
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            ok = False
            print("      [import FAIL] %s: %s" % (m, e))
    check("E1 all engine modules import", ok)

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
