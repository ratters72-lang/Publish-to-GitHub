"""
verify_stage6_tackle_radius_gating.py — Stage 6: Tackle-Radius Pre-Tackle Activation Gate verifier.

Confirms the Stage 6 ship-text claims by static inspection + runtime probe:

  A. `publish_pre_tackle_state` only publishes a non-NONE canonical state
     for a body within the pre-tackle activation radius (default 2.0 m);
     far bodies get `pre_tackle_state = NONE` so the MMDL keeps owning them.
  B. `apply_pre_tackle` (frozen_mode=False, OPEN_PLAY) only owns bodies that
     have actually breached the activation radius; far bodies fall through
     to the normal retreat/spacing pipeline.  Managed bodies that exit the
     radius are cleared in Pass 0.
  C. DRIVE-PHASE RADIUS GATE (revised 2026-09-10): `frozen_mode=True`
     (TES CONTACT / DRIVE / DOMINANCE) applies the SAME activation-radius
     gate.  The old carve-out owned EVERY eligible line body for the whole
     chamber, so a defender 15-20 m away was depth-locked and stopped
     defending; far bodies are released and keep playing their depth role.
  D. Runtime probe:
       - far body (dist > 2.0) is NOT managed by apply_pre_tackle(frozen_mode=False);
       - close body (dist <= 2.0) IS managed (COMMIT/SLOW/HOLD);
       - far body with PRIMARY/commit-active legacy shims is published NONE
         (not COMMIT) by publish_pre_tackle_state;
       - close body with the same shims is published COMMIT;
       - frozen_mode=True owns ONLY the close body (far body released);
       - a previously-managed body that moved far away is cleared.
  E. Engine module tree imports cleanly.

Run:  python verify_stage6_tackle_radius_gating.py
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
DBU = "game/defence/defence_lvl_1/defensive_behaviour_unified.py"
DEFMAN = "game/managers/defence_manager.py"

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
        self.defender_role = DefenderRole.DEF_LINE_READY
        self.move_speed = 0.0
        self.movement_state = None
        self.facing = 0.0

    def set_facing(self, angle):
        self.facing = angle


def main():
    print("=" * 64)
    print("STAGE 6 -- TACKLE-RADIUS PRE-TACKLE ACTIVATION GATE VERIFIER")
    print("=" * 64)

    pta = read(PTA)
    dbu = read(DBU)
    defman = read(DEFMAN)

    # ══════════════════════════════════════════════════════════════
    # A. publish_pre_tackle_state gates on the activation radius
    # ══════════════════════════════════════════════════════════════
    print("\n[A] publish_pre_tackle_state gates far bodies to NONE")
    check("A1 publish reads activation_r from pre_tackle_trigger_radius_m",
          "getattr(tuners, \"pre_tackle_trigger_radius_m\", 2.0)" in pta)
    check("A2 publish computes dist(d, bc) and drops > activation_r to NONE",
          "activation_r" in pta
          and "d.pre_tackle_state = PreTackleState.NONE" in pta
          and "_dist > activation_r" in pta)
    check("A3 the NONE drop is BEFORE the legacy-flag mapping",
          pta.index("_dist > activation_r") < pta.index("if bool(getattr(d, \"pre_tackle_stance\", False)):"))
    check("A4 publish returns the managed list (in-radius only)",
          "return managed" in pta)

    # ══════════════════════════════════════════════════════════════
    # B. apply_pre_tackle gates OPEN_PLAY on the activation radius
    # ══════════════════════════════════════════════════════════════
    print("\n[B] apply_pre_tackle (frozen_mode=False) gates on tackle-radius breach")
    check("B1 apply_pre_tackle computes activation_r",
          "activation_r = float(getattr(tuners, \"pre_tackle_trigger_radius_m\", 2.0))" in pta)
    check("B2 Pass 0 clears radius-exited managed bodies (all modes)",
          "_dist > activation_r" in pta
          and "clear_pre_tackle_state(d)" in pta)
    check("B3 Pass 1 filters eligible to in-radius bodies (all modes)",
          re.search(r"eligible = \[\s*\n\s*d for d in eligible\s*\n\s*if math\.hypot", pta) is not None)
    check("B4 early return when no in-radius bodies remain",
          "if not eligible:\n        return managed" in pta)
    check("B5 the eligible filter is NOT wrapped in `if not frozen_mode:` (the drive is gated too)",
          re.search(r"if not frozen_mode:\s*\n\s*eligible = \[", pta) is None)

    # ══════════════════════════════════════════════════════════════
    # C. TES drive path unchanged (frozen_mode=True owns all eligible)
    # ══════════════════════════════════════════════════════════════
    print("\n[C] the drive path applies the same activation-radius gate")
    check("C1 defence_manager.update_tes_drive still calls apply_pre_tackle(frozen_mode=True)",
          "apply_pre_tackle(" in defman and "frozen_mode=True" in defman)
    check("C2 the drive path radius-gates its pool (pile gate, never activation_r) and the "
          "authority gate is not mode-specific",
          "update_tes_drive" in defman and "_pile_r" in defman
          and "if not frozen_mode:\n        eligible = [" not in pta
          and "if not frozen_mode:\n            _dist" not in pta)

    # ══════════════════════════════════════════════════════════════
    # D. Runtime probe
    # ══════════════════════════════════════════════════════════════
    print("\n[D] runtime probe")
    from game.defence.defence_lvl_1.pre_tackle_authority import (
        apply_pre_tackle,
        publish_pre_tackle_state,
        PreTackleState,
    )

    bc = MockBody(99, 60.0, 35.0)
    far = MockBody(1, 70.0, 35.0)     # dist 10.0 > 2.0
    close = MockBody(2, 60.5, 35.0)   # dist 0.5 <= 2.0

    tuners = _make_tuners()
    world = _make_world(bc)
    dt = 1.0 / 60.0

    # D1: apply_pre_tackle(frozen_mode=False) does NOT own the far body.
    managed = apply_pre_tackle(world, [far, close], dt, tuners, frozen_mode=False)
    far_managed = far in managed
    close_managed = close in managed
    check("D1 far body NOT owned by OPEN_PLAY pre-tackle authority",
          not far_managed, "far in managed=%s" % far_managed)
    check("D2 close body IS owned (COMMIT/SLOW/HOLD)",
          close_managed, "close in managed=%s" % close_managed)
    check("D3 far body carries no non-NONE pre_tackle_state after apply",
          getattr(far, "pre_tackle_state", None) in (None, PreTackleState.NONE),
          "far state=%s" % getattr(far, "pre_tackle_state", None))
    check("D4 close body carries a non-NONE pre_tackle_state",
          getattr(close, "pre_tackle_state", None) not in (None, PreTackleState.NONE),
          "close state=%s" % getattr(close, "pre_tackle_state", None))

    # D5/D6: publish_pre_tackle_state gates far bodies even with PRIMARY/commit shims.
    far2 = MockBody(3, 71.0, 35.0)  # dist 11.0 > 2.0
    close2 = MockBody(4, 61.0, 35.0)  # dist 1.0 <= 2.0
    for d in (far2, close2):
        d.tackle_role = "PRIMARY"
        d._tackle_commit_active = True
    pub_managed = publish_pre_tackle_state(world, [far2, close2], bc, tuners)
    check("D5 far2 published NONE (not COMMIT) despite PRIMARY/commit shims",
          getattr(far2, "pre_tackle_state", None) is PreTackleState.NONE,
          "far2 state=%s" % getattr(far2, "pre_tackle_state", None))
    check("D6 close2 published COMMIT (within radius + PRIMARY/commit shims)",
          getattr(close2, "pre_tackle_state", None) is PreTackleState.COMMIT,
          "close2 state=%s" % getattr(close2, "pre_tackle_state", None))
    check("D7 publish only returns in-radius managed bodies",
          close2 in pub_managed and far2 not in pub_managed,
          "pub_managed contains close2=%s far2=%s" % (close2 in pub_managed, far2 in pub_managed))

    # D8: frozen_mode=True owns ONLY the close body — the drive is gated too,
    # so a far body is released and keeps playing its depth-role geometry.
    far3 = MockBody(5, 72.0, 35.0)  # dist 12.0 > 2.0
    close3 = MockBody(6, 61.0, 35.0)  # dist 1.0 <= 2.0
    world3 = _make_world(bc)
    managed3 = apply_pre_tackle(world3, [far3, close3], dt, tuners, frozen_mode=True)
    check("D8 frozen_mode=True owns ONLY the close body (far released)",
          far3 not in managed3 and close3 in managed3,
          "far3=%s close3=%s" % (far3 in managed3, close3 in managed3))

    # D9: previously-managed body moved far away is cleared by apply (Pass 0).
    newly_far = MockBody(7, 60.5, 35.0)  # starts close
    newly_far.pre_tackle_state = PreTackleState.COMMIT
    # Move it far away (the world simulation would do this between frames).
    newly_far.x = 80.0
    newly_far.y = 35.0  # dist 20.0 > 2.0
    managed4 = apply_pre_tackle(world, [newly_far], dt, tuners, frozen_mode=False)
    check("D9 radius-exited managed body is cleared (not re-owned)",
          newly_far not in managed4
          and getattr(newly_far, "pre_tackle_state", None) in (None, PreTackleState.NONE),
          "state=%s" % getattr(newly_far, "pre_tackle_state", None))

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
    check("E1 all engine modules import", ok)

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
