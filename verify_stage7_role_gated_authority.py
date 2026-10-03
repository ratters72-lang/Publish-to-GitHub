"""
verify_stage7_role_gated_authority.py — Stage 7: Role-Gated Pre-Tackle Authority verifier.

Confirms the Stage 7 ship-text claims by static inspection + runtime probe:

  A. `publish_pre_tackle_state` role-gates: only a real DEF_LINE_READY body
     (unfrozen, non-fullback, within the activation radius) may be published
     as COMMIT/PLANTED/SLOW/HOLD.  Markers / retreaters / DEF_CHASE /
     DUMMY_HALF / fullback / frozen bodies carrying stale legacy flags are
     dropped to NONE with their shims cleared.
  B. `DEF_LINE_READY` bodies still publish correctly (COMMIT/PLANTED/SLOW/HOLD).
  C. The Stage 6 radius gate still applies (far bodies → NONE regardless of role).
  D. `apply_pre_tackle` is role-gated (ineligible bodies dropped from
     `eligible` in Pass 1; managed ineligible bodies cleared in Pass 0) AND
     TES structure-held bodies are exempted in both passes: during a drive
     chamber the TES structure hold is the SOLE owner of a held line body,
     so this authority must not depth-lock/pin it (the lane-true contract).
  E. Runtime probe:
       - marker with tackle_role="LINE" / pre_tackle_stance / _tackle_commit_active
         is published NONE (not SLOW/HOLD/COMMIT/PLANTED);
       - DEF_RETREAT_10 with the same flags is published NONE;
       - DEF_CHASE with the same flags is published NONE;
       - DUMMY_HALF with the same flags is published NONE;
       - frozen DEF_LINE_READY is published NONE;
       - fullback DEF_LINE_READY is published NONE;
       - DEF_LINE_READY within radius with tackle_role="LINE" is SLOW/HOLD;
       - DEF_LINE_READY within radius with _tackle_commit_active + PRIMARY is COMMIT;
       - DEF_LINE_READY within radius with pre_tackle_stance is PLANTED;
       - far DEF_LINE_READY is NONE (radius gate preserved);
       - apply_pre_tackle(frozen_mode=False) only owns DEF_LINE_READY bodies.
  F. Engine module tree imports cleanly.

Run:  python verify_stage7_role_gated_authority.py
Exit 0 on success, 1 on failure.
"""
import math
import os
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

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
    t.pretackle_attempt_enabled = True
    t.pre_tackle_trigger_radius_m = 2.0
    # Scalar tuners the authority reads (via _phase_tuners getattr fallback).
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
    return t


def _make_world(bc):
    w = SimpleNamespace()
    w.ball_carrier = bc
    w.sub_state = SimpleNamespace(name="OPEN_PLAY")
    return w


class MockBody:
    """Minimal player-like body for the runtime probe.

    ``role`` may be a DefenderRole enum or a string like "LINE"/"PRIMARY"
    for the tackle_role attribute.
    """

    def __init__(self, pid, x, y, defender_role=None, frozen=False,
                 freeze_timer=0.0, fullback=False):
        from player.player import DefenderRole
        self.player_id = pid
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self.target_x = x
        self.target_y = y
        self.frozen = frozen
        self.freeze_timer = freeze_timer
        self.fullback_role_active = fullback
        self.defender_role = defender_role if defender_role is not None else DefenderRole.DEF_LINE_READY
        self.move_speed = 0.0
        self.movement_state = None
        self.facing = 0.0
        # Legacy shim attributes.
        self.tackle_role = None
        self.pre_tackle_stance = False
        self._tackle_commit_active = False
        self.footplanting = False

    def set_facing(self, angle):
        self.facing = angle


def main():
    print("=" * 64)
    print("STAGE 7 -- ROLE-GATED PRE-TACKLE AUTHORITY VERIFIER")
    print("=" * 64)

    pta = read(PTA)

    # ══════════════════════════════════════════════════════════════
    # A. publish_pre_tackle_state role-gates non-DEF_LINE_READY bodies
    # ══════════════════════════════════════════════════════════════
    print("\n[A] publish_pre_tackle_state role-gates non-DEF_LINE_READY bodies")
    check("A1 role-gate calls is_pre_tackle_eligible",
          "is_pre_tackle_eligible(d)" in pta)
    check("A2 role-gate is inside publish_pre_tackle_state (after radius, before mapping)",
          pta.index("if not is_pre_tackle_eligible(d):")
          > pta.index("_dist > activation_r")
          and pta.index("if not is_pre_tackle_eligible(d):")
          < pta.index("if bool(getattr(d, \"pre_tackle_stance\", False)):"))
    check("A3 ineligible body is set to NONE",
          "d.pre_tackle_state = PreTackleState.NONE" in pta
          and pta.count("d.pre_tackle_state = PreTackleState.NONE") >= 2)
    check("A4 ineligible body's legacy shims are cleared (tackle_role / commit / stance / footplanting)",
          re.search(r"if not is_pre_tackle_eligible\(d\):[\s\S]*?d\.tackle_role = None[\s\S]*?d\._tackle_commit_active = False[\s\S]*?d\.pre_tackle_stance = False[\s\S]*?d\.footplanting = False[\s\S]*?continue", pta) is not None)
    check("A5 is_pre_tackle_eligible requires defender_role == DEF_LINE_READY",
          "is DefenderRole.DEF_LINE_READY" in pta)
    check("A6 is_pre_tackle_eligible excludes markers/retreaters/CHASE by role",
          re.search(r"return getattr\(d, \"defender_role\", None\) is DefenderRole\.DEF_LINE_READY", pta) is not None)

    # ══════════════════════════════════════════════════════════════
    # B. DEF_LINE_READY bodies still publish correctly
    # ══════════════════════════════════════════════════════════════
    print("\n[B] DEF_LINE_READY bodies still publish correctly")
    check("B1 legacy-flag mapping still present (PLANTED via pre_tackle_stance)",
          "PreTackleState.PLANTED" in pta and "pre_tackle_stance" in pta)
    check("B2 COMMIT mapping still present (commit-active + PRIMARY)",
          "PreTackleState.COMMIT" in pta and "_tackle_commit_active" in pta)
    check("B3 HOLD/SLOW mapping still present (tackle_role == LINE)",
          "PreTackleState.HOLD" in pta and "PreTackleState.SLOW" in pta
          and "tackle_role\" , None) == \"LINE\"" not in pta)
    check("B4 _sync_shims still derives the legacy flags",
          "def _sync_shims" in pta)

    # ══════════════════════════════════════════════════════════════
    # C. Radius gate still applies
    # ══════════════════════════════════════════════════════════════
    print("\n[C] Stage 6 radius gate still applies")
    check("C1 radius gate still present before role-gate",
          "activation_r = float(getattr(tuners, \"pre_tackle_trigger_radius_m\", 2.0))" in pta
          and "_dist > activation_r" in pta
          and pta.index("_dist > activation_r") < pta.index("if not is_pre_tackle_eligible(d):"))

    # ══════════════════════════════════════════════════════════════
    # D. apply_pre_tackle already role-gated
    # ══════════════════════════════════════════════════════════════
    print("\n[D] apply_pre_tackle is already role-gated")
    check("D1 Pass 0 drops ineligible holders via is_pre_tackle_eligible",
          re.search(r"for d in defenders:[\s\S]*?if not is_pre_tackle_managed\(d\):[\s\S]*?continue[\s\S]*?not is_pre_tackle_eligible\(d[^)]*\):[\s\S]*?clear_pre_tackle_state\(d\)", pta) is not None)
    check("D1b Pass 0 also clears TES structure-held holders (sole-owner exemption)",
          re.search(r"for d in defenders:[\s\S]*?_is_struct_hold\(d\)[\s\S]*?not is_pre_tackle_eligible\(d[^)]*\):[\s\S]*?clear_pre_tackle_state\(d\)", pta) is not None)
    check("D1c Pass 1 excludes TES structure-held bodies from `eligible`",
          re.search(r"eligible = \[[\s\S]*?is_pre_tackle_eligible\(d[^)]*\)[\s\S]*?not _is_struct_hold\(d[^)]*\)[\s\S]*?\]", pta) is not None)
    check("D2 Pass 1 filters eligible to is_pre_tackle_eligible only",
          re.search(r"eligible = \[[\s\S]*?is_pre_tackle_eligible\(d(?:,[^)]*)?\)[\s\S]*?\]", pta) is not None)

    # ══════════════════════════════════════════════════════════════
    # E. Runtime probe
    # ══════════════════════════════════════════════════════════════
    print("\n[E] runtime probe")
    from player.player import DefenderRole
    from game.defence.defence_lvl_1.pre_tackle_authority import (
        apply_pre_tackle,
        publish_pre_tackle_state,
        PreTackleState,
    )

    bc = MockBody(99, 60.0, 35.0)
    tuners = _make_tuners()
    world = _make_world(bc)
    dt = 1.0 / 60.0

    # E1: marker with stale legacy flags published NONE.
    marker = MockBody(1, 61.0, 35.0, defender_role=DefenderRole.MARKER_A)
    marker.tackle_role = "LINE"
    marker.pre_tackle_stance = True
    marker._tackle_commit_active = True
    marker.footplanting = True
    publish_pre_tackle_state(world, [marker], bc, tuners)
    check("E1 marker published NONE (not SLOW/HOLD/COMMIT/PLANTED)",
          getattr(marker, "pre_tackle_state", None) is PreTackleState.NONE,
          "state=%s" % getattr(marker, "pre_tackle_state", None))
    check("E1b marker shims cleared (tackle_role None, stance False, commit False)",
          getattr(marker, "tackle_role", None) is None
          and not getattr(marker, "pre_tackle_stance", True)
          and not getattr(marker, "_tackle_commit_active", True)
          and not getattr(marker, "footplanting", True),
          "role=%s stance=%s commit=%s foot=%s" % (
              getattr(marker, "tackle_role", None),
              getattr(marker, "pre_tackle_stance", None),
              getattr(marker, "_tackle_commit_active", None),
              getattr(marker, "footplanting", None),
          ))

    # E2: DEF_RETREAT_10 with stale legacy flags published NONE.
    retreater = MockBody(2, 61.0, 35.0, defender_role=DefenderRole.DEF_RETREAT_10)
    retreater.tackle_role = "LINE"
    retreater.pre_tackle_stance = True
    retreater._tackle_commit_active = True
    publish_pre_tackle_state(world, [retreater], bc, tuners)
    check("E2 DEF_RETREAT_10 published NONE",
          getattr(retreater, "pre_tackle_state", None) is PreTackleState.NONE,
          "state=%s" % getattr(retreater, "pre_tackle_state", None))

    # E3: DEF_CHASE with stale legacy flags published NONE.
    chaser = MockBody(3, 61.0, 35.0, defender_role=DefenderRole.DEF_CHASE)
    chaser.tackle_role = "LINE"
    chaser.pre_tackle_stance = True
    chaser._tackle_commit_active = True
    publish_pre_tackle_state(world, [chaser], bc, tuners)
    check("E3 DEF_CHASE published NONE",
          getattr(chaser, "pre_tackle_state", None) is PreTackleState.NONE,
          "state=%s" % getattr(chaser, "pre_tackle_state", None))

    # E4: DUMMY_HALF with stale legacy flags published NONE.
    dh = MockBody(4, 61.0, 35.0, defender_role=DefenderRole.DUMMY_HALF)
    dh.tackle_role = "LINE"
    dh.pre_tackle_stance = True
    dh._tackle_commit_active = True
    publish_pre_tackle_state(world, [dh], bc, tuners)
    check("E4 DUMMY_HALF published NONE",
          getattr(dh, "pre_tackle_state", None) is PreTackleState.NONE,
          "state=%s" % getattr(dh, "pre_tackle_state", None))

    # E5: frozen DEF_LINE_READY with stale legacy flags published NONE.
    frozen_dlr = MockBody(5, 61.0, 35.0, defender_role=DefenderRole.DEF_LINE_READY,
                          frozen=True)
    frozen_dlr.tackle_role = "LINE"
    frozen_dlr.pre_tackle_stance = True
    frozen_dlr._tackle_commit_active = True
    publish_pre_tackle_state(world, [frozen_dlr], bc, tuners)
    check("E5 frozen DEF_LINE_READY published NONE",
          getattr(frozen_dlr, "pre_tackle_state", None) is PreTackleState.NONE,
          "state=%s" % getattr(frozen_dlr, "pre_tackle_state", None))

    # E6: fullback DEF_LINE_READY with stale legacy flags published NONE.
    fb = MockBody(6, 61.0, 35.0, defender_role=DefenderRole.DEF_LINE_READY,
                  fullback=True)
    fb.tackle_role = "LINE"
    fb.pre_tackle_stance = True
    fb._tackle_commit_active = True
    publish_pre_tackle_state(world, [fb], bc, tuners)
    check("E6 fullback DEF_LINE_READY published NONE",
          getattr(fb, "pre_tackle_state", None) is PreTackleState.NONE,
          "state=%s" % getattr(fb, "pre_tackle_state", None))

    # E7: DEF_LINE_READY with tackle_role="LINE":
    #     - freeze_timer > 0 body is INELIGIBLE (is_pre_tackle_eligible
    #       excludes freeze_timer > 0) -> published NONE;
    #     - non-frozen body -> published SLOW.
    #     HOLD is reached via apply_pre_tackle's state machine (SLOW -> HOLD
    #     when a body converges to its anchor), not via publish.
    dh_line_hold = MockBody(7, 61.0, 35.0, defender_role=DefenderRole.DEF_LINE_READY)
    dh_line_hold.tackle_role = "LINE"
    dh_line_hold.freeze_timer = 0.5
    publish_pre_tackle_state(world, [dh_line_hold], bc, tuners)
    check("E7a DEF_LINE_READY LINE+frozen published NONE (freeze_timer ineligible)",
          getattr(dh_line_hold, "pre_tackle_state", None) is PreTackleState.NONE,
          "state=%s" % getattr(dh_line_hold, "pre_tackle_state", None))

    dh_line_slow = MockBody(8, 61.0, 35.0, defender_role=DefenderRole.DEF_LINE_READY)
    dh_line_slow.tackle_role = "LINE"
    publish_pre_tackle_state(world, [dh_line_slow], bc, tuners)
    check("E7b DEF_LINE_READY LINE non-frozen published SLOW",
          getattr(dh_line_slow, "pre_tackle_state", None) is PreTackleState.SLOW,
          "state=%s" % getattr(dh_line_slow, "pre_tackle_state", None))

    # E7c: HOLD/SLOW fallback path is still present in apply_pre_tackle
    #      (anti-collapse contract) — verified statically by B3/B4 and
    #      dynamically by E7b (SLOW publish).  A single-body probe always
    #      gets promoted to COMMIT by _reconcile_owners, so HOLD is not
    #      directly reachable in a one-body runtime probe.

    # E8: DEF_LINE_READY with commit-active + PRIMARY published COMMIT.
    dh_commit = MockBody(9, 61.0, 35.0, defender_role=DefenderRole.DEF_LINE_READY)
    dh_commit.tackle_role = "PRIMARY"
    dh_commit._tackle_commit_active = True
    publish_pre_tackle_state(world, [dh_commit], bc, tuners)
    check("E8 DEF_LINE_READY PRIMARY+commit published COMMIT",
          getattr(dh_commit, "pre_tackle_state", None) is PreTackleState.COMMIT,
          "state=%s" % getattr(dh_commit, "pre_tackle_state", None))

    # E9: DEF_LINE_READY with pre_tackle_stance published PLANTED.
    dh_plant = MockBody(10, 61.0, 35.0, defender_role=DefenderRole.DEF_LINE_READY)
    dh_plant.pre_tackle_stance = True
    publish_pre_tackle_state(world, [dh_plant], bc, tuners)
    check("E9 DEF_LINE_READY pre_tackle_stance published PLANTED",
          getattr(dh_plant, "pre_tackle_state", None) is PreTackleState.PLANTED,
          "state=%s" % getattr(dh_plant, "pre_tackle_state", None))

    # E10: far DEF_LINE_READY (dist > 2.0) published NONE (radius gate preserved).
    far_dlr = MockBody(11, 70.0, 35.0, defender_role=DefenderRole.DEF_LINE_READY)
    far_dlr.tackle_role = "LINE"
    far_dlr.pre_tackle_stance = True
    far_dlr._tackle_commit_active = True
    publish_pre_tackle_state(world, [far_dlr], bc, tuners)
    check("E10 far DEF_LINE_READY published NONE (radius gate preserved)",
          getattr(far_dlr, "pre_tackle_state", None) is PreTackleState.NONE,
          "state=%s" % getattr(far_dlr, "pre_tackle_state", None))

    # E11: apply_pre_tackle(frozen_mode=False) only owns DEF_LINE_READY bodies.
    marker_a = MockBody(20, 61.0, 35.0, defender_role=DefenderRole.MARKER_A)
    retreater_a = MockBody(21, 61.0, 35.0, defender_role=DefenderRole.DEF_RETREAT_10)
    chaser_a = MockBody(22, 61.0, 35.0, defender_role=DefenderRole.DEF_CHASE)
    dlr_close = MockBody(23, 61.0, 35.0, defender_role=DefenderRole.DEF_LINE_READY)
    managed = apply_pre_tackle(world, [marker_a, retreater_a, chaser_a, dlr_close],
                               dt, tuners, frozen_mode=False)
    check("E11a marker NOT owned by apply_pre_tackle",
          marker_a not in managed, "marker in managed=%s" % (marker_a in managed))
    check("E11b retreater NOT owned by apply_pre_tackle",
          retreater_a not in managed, "retreater in managed=%s" % (retreater_a in managed))
    check("E11c DEF_CHASE NOT owned by apply_pre_tackle",
          chaser_a not in managed, "chaser in managed=%s" % (chaser_a in managed))
    check("E11d DEF_LINE_READY IS owned by apply_pre_tackle",
          dlr_close in managed, "dlr in managed=%s" % (dlr_close in managed))

    # E12: apply_pre_tackle clears an ineligible managed body (Pass 0).
    stale_marker = MockBody(24, 61.0, 35.0, defender_role=DefenderRole.MARKER_A)
    stale_marker.pre_tackle_state = PreTackleState.COMMIT
    managed2 = apply_pre_tackle(world, [stale_marker], dt, tuners, frozen_mode=False)
    check("E12 ineligible managed marker cleared (not owned, state NONE/None)",
          stale_marker not in managed2
          and getattr(stale_marker, "pre_tackle_state", None) in (None, PreTackleState.NONE),
          "in managed=%s state=%s" % (stale_marker in managed2,
                                      getattr(stale_marker, "pre_tackle_state", None)))

    # ══════════════════════════════════════════════════════════════
    # F. Engine imports cleanly
    # ══════════════════════════════════════════════════════════════
    print("\n[F] engine module tree imports cleanly")
    import importlib
    mods = [
        "world.open_play_engine",
        "game.managers.defence_manager",
        "game.defence.defence_lvl_1.pre_tackle_authority",
        "game.defence.defence_lvl_1.defensive_behaviour_unified",
        "game.defence.defence_lvl_1.defensive_behaviour_lvl1",
        "world.rules.movement_contracts",
    ]
    ok = True
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            ok = False
            print("      [import FAIL] %s: %s" % (m, e))
    check("F1 all engine modules import", ok)

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
