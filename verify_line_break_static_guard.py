"""
verify_line_break_static_guard.py — LINE-BREAK STATIC TARGET GUARD verifier.

Confirms the "if a defender's target_x ever becomes static after a line break,
immediately overwrite it with the live bc.x and force movement_state = CHASE so
the tackle pipeline can arm again" fix by static inspection + runtime probe:

  A. ``line_break_static_guard.py`` defines ``enforce_line_break_static_guard``
     and gates it on the single-sourced line-break predicate
     (``carrier_past_defensive_line``), and writes ``target_x = float(bc.x)``
     with ``movement_state = MovementState.CHASE``.
  B. ``DefenceManager.update_open_play`` wires the guard as the FINAL X write of
     the dispatch: it runs AFTER ``_apply_catch_x(...)`` and BEFORE the
     ``# ---- Apply movement ----`` integration loop.
  C. ``writer_priority_bands._SOURCE_PRIORITY`` registers
     ``("line_break_static_guard", PRIORITY_COMMIT)``.
  D. Runtime probe (mock world + defender stubs): a defender pinned to the
     static anchor after a line break is re-armed (target_x = bc.x, CHASE, locks
     cleared); a non-line-break frame is a no-op; markers / fullback / committed
     tacklers / pursuit-commit bodies / already-BC-targeting bodies are never
     touched; the pure ``is_target_x_static`` predicate recognises the
     anchor-pin, suppressed, temporal and already-closing cases.
  E. Engine module tree imports cleanly.

Run:  python verify_line_break_static_guard.py
Exit 0 on success, 1 on failure.
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

GUARD = "game/defence/defence_lvl_1/line_break_static_guard.py"
DM = "game/managers/defence_manager.py"
WPB = "game/defence/defence_lvl_1/writer_priority_bands.py"

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class MockBody:
    """Minimal player-like body for the runtime probe.

    Pre-tackle state is left absent (None) so ``is_pre_tackle_managed`` returns
    False (mirrors verify_target_x_glide._Fake's "pre-tackle absent" contract).
    """

    def __init__(self, pid, x, target_x, y=35.0, role=None):
        from player.player import DefenderRole
        self.player_id = pid
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self.target_x = target_x
        self.target_y = y
        self.frozen = False
        self.freeze_timer = 0.0
        self.fullback_role_active = False
        self.tackle_state_active = False
        self._tackle_commit_active = False
        self._pursuit_commit_active = False
        self.defender_role = role if role is not None else DefenderRole.DEF_LINE_READY
        self.movement_state = None
        self.move_speed = 0.0
        self.target_source = None
        self.timing_locked = False
        self.reaction_timer = 0.0
        self.retreat_delay = 0.0
        self.lateral_hesitation = 0.0
        self.step_stagger = 0.0
        self.step_stagger_active = False
        self.max_speed_mps = 8.5
        self.pre_tackle_state = None


def _new_world(ruck_x=50.0, bc_x=63.0):
    """SimpleNamespace world; anchor = ruck_x + 10, line broken iff bc.x > anchor + 1."""
    w = SimpleNamespace()
    w.ruck_x_m = ruck_x
    w.anchor_locked = False
    w.defensive_line_anchor = None
    w.ball_carrier = SimpleNamespace(x=bc_x, y=35.0)
    return w


def main():
    print("=" * 64)
    print("LINE-BREAK STATIC TARGET GUARD VERIFIER")
    print("=" * 64)

    guard = read(GUARD)
    dm = read(DM)
    wpb = read(WPB)

    # ══════════════════════════════════════════════════════════════
    # A. Guard module structure
    # ══════════════════════════════════════════════════════════════
    print("\n[A] guard module structure")
    check("A1 `enforce_line_break_static_guard` exists",
          "def enforce_line_break_static_guard(world, defenders, bc=None, tuners=None) -> list:" in guard)
    check("A2 gates on `carrier_past_defensive_line`",
          "carrier_past_defensive_line" in guard)
    check("A3 overwrites `target_x = float(bc.x)`",
          "d.target_x = float(bc.x)" in guard)
    check("A4 forces `movement_state = MovementState.CHASE`",
          "d.movement_state = MovementState.CHASE" in guard)
    check("A5 uses the static-anchor signal `_LB_STATIC_ANCHOR_EPS_M`",
          "_LB_STATIC_ANCHOR_EPS_M" in guard)
    check("A6 clears timing/retreat locks (timing_locked = False)",
          "d.timing_locked = False" in guard)
    check("A7 stamps `target_source` = LINE_BREAK_STATIC_GUARD_SOURCE",
          'd.target_source = LINE_BREAK_STATIC_GUARD_SOURCE' in guard)

    # ══════════════════════════════════════════════════════════════
    # B. DefenceManager wiring
    # ══════════════════════════════════════════════════════════════
    print("\n[B] DefenceManager.update_open_play wiring")
    check("B1 lazily imports `enforce_line_break_static_guard as _enforce_lb_static`",
          "enforce_line_break_static_guard as _enforce_lb_static" in dm)
    check("B2 calls `_enforce_lb_static(world, defenders, bc, self.tuners)`",
          "_enforce_lb_static(world, defenders, bc, self.tuners)" in dm)
    idx_catch = dm.find("_apply_catch_x(world, defenders, bc, self.tuners)")
    idx_guard = dm.find("_enforce_lb_static(world, defenders, bc, self.tuners)")
    idx_apply = dm.find("# ---- Apply movement ----")
    check("B3 guard runs AFTER catch-X",
          idx_catch != -1 and idx_guard != -1 and idx_guard > idx_catch,
          "catch=%s guard=%s" % (idx_catch, idx_guard))
    check("B4 guard runs BEFORE the integration loop",
          idx_guard != -1 and idx_apply != -1 and idx_guard < idx_apply,
          "guard=%s apply=%s" % (idx_guard, idx_apply))

    # ══════════════════════════════════════════════════════════════
    # C. Writer-priority-band registration
    # ══════════════════════════════════════════════════════════════
    print("\n[C] writer-priority-band registration")
    check("C1 `line_break_static_guard` registered at PRIORITY_COMMIT",
          '("line_break_static_guard", PRIORITY_COMMIT)' in wpb)

    # ══════════════════════════════════════════════════════════════
    # D. Runtime probe
    # ══════════════════════════════════════════════════════════════
    print("\n[D] runtime probe")
    from game.defence.defence_lvl_1.line_break_static_guard import (
        enforce_line_break_static_guard,
        is_target_x_static,
        advance_static_window,
        LINE_BREAK_STATIC_GUARD_SOURCE,
    )
    from player.movement_states import MovementState
    from player.player import DefenderRole

    class _Tuners:
        max_defensive_speed_mps = 8.3

    tuners = _Tuners()

    # D1: line broken + defender pinned to the static anchor -> re-armed.
    w1 = _new_world(ruck_x=50.0, bc_x=63.0)  # anchor=60, bc.x=63 > 61
    d1 = MockBody(1, x=50.0, target_x=60.0)  # static at anchor
    d1.movement_state = MovementState.HOLD
    d1.timing_locked = True
    d1.retreat_delay = 1.0
    r1 = enforce_line_break_static_guard(w1, [d1], bc=w1.ball_carrier, tuners=tuners)
    check("D1 re-arms a static-at-anchor defender", len(r1) == 1, "n=%s" % len(r1))
    check("D2 target_x overwritten with live bc.x",
          abs(d1.target_x - 63.0) < 1e-6, "tx=%s" % d1.target_x)
    check("D3 movement_state forced to CHASE",
          d1.movement_state == MovementState.CHASE, "state=%s" % d1.movement_state)
    check("D4 timing/retreat locks cleared",
          not d1.timing_locked and d1.retreat_delay == 0.0,
          "locked=%s delay=%s" % (d1.timing_locked, d1.retreat_delay))
    check("D5 target_source stamped",
          d1.target_source == LINE_BREAK_STATIC_GUARD_SOURCE,
          "src=%s" % d1.target_source)
    check("D6 frozen cleared and speed restored",
          not d1.frozen and d1.move_speed > 0.0,
          "frozen=%s speed=%s" % (d1.frozen, d1.move_speed))

    # D7: line NOT broken (bc.x < anchor + 1) -> no-op.
    w2 = _new_world(ruck_x=50.0, bc_x=55.0)  # anchor=60, bc.x=55 < 61
    d2 = MockBody(2, x=50.0, target_x=60.0)
    r2 = enforce_line_break_static_guard(w2, [d2], bc=w2.ball_carrier, tuners=tuners)
    check("D7 non-line-break frame is a no-op",
          len(r2) == 0 and abs(d2.target_x - 60.0) < 1e-6, "n=%s tx=%s" % (len(r2), d2.target_x))

    # D8: marker never touched.
    w3 = _new_world(ruck_x=50.0, bc_x=63.0)
    d3 = MockBody(3, x=50.0, target_x=60.0, role=DefenderRole.MARKER_A)
    r3 = enforce_line_break_static_guard(w3, [d3], bc=w3.ball_carrier, tuners=tuners)
    check("D8 marker never touched",
          len(r3) == 0 and abs(d3.target_x - 60.0) < 1e-6, "n=%s" % len(r3))

    # D9: fullback never touched.
    w4 = _new_world(ruck_x=50.0, bc_x=63.0)
    d4 = MockBody(4, x=50.0, target_x=60.0)
    d4.fullback_role_active = True
    r4 = enforce_line_break_static_guard(w4, [d4], bc=w4.ball_carrier, tuners=tuners)
    check("D9 fullback never touched", len(r4) == 0, "n=%s" % len(r4))

    # D10: committed tackler never touched.
    w5 = _new_world(ruck_x=50.0, bc_x=63.0)
    d5 = MockBody(5, x=50.0, target_x=60.0)
    d5._tackle_commit_active = True
    r5 = enforce_line_break_static_guard(w5, [d5], bc=w5.ball_carrier, tuners=tuners)
    check("D10 committed tackler never touched", len(r5) == 0, "n=%s" % len(r5))

    # D11: pursuit-commit body never touched.
    w6 = _new_world(ruck_x=50.0, bc_x=63.0)
    d6 = MockBody(6, x=50.0, target_x=60.0)
    d6._pursuit_commit_active = True
    r6 = enforce_line_break_static_guard(w6, [d6], bc=w6.ball_carrier, tuners=tuners)
    check("D11 pursuit-commit body never touched", len(r6) == 0, "n=%s" % len(r6))

    # D12: already targeting the live BC -> not touched.
    w7 = _new_world(ruck_x=50.0, bc_x=63.0)
    d7 = MockBody(7, x=50.0, target_x=63.0)
    r7 = enforce_line_break_static_guard(w7, [d7], bc=w7.ball_carrier, tuners=tuners)
    check("D12 already-BC-targeting body never touched",
          len(r7) == 0 and abs(d7.target_x - 63.0) < 1e-6, "n=%s" % len(r7))

    # D13: pure predicate — anchor-pin signal.
    d13 = MockBody(13, x=50.0, target_x=60.0)
    check("D13 is_target_x_static True for anchor-pin",
          is_target_x_static(d13, w1, w1.ball_carrier, anchor_x=60.0))

    # D14: pure predicate — suppressed signal (target == self.x).
    d14 = MockBody(14, x=50.0, target_x=50.0)
    check("D14 is_target_x_static True for suppressed",
          is_target_x_static(d14, w1, w1.ball_carrier, anchor_x=60.0))

    # D15: pure predicate — already closing on BC -> not static.
    d15 = MockBody(15, x=50.0, target_x=63.0)
    check("D15 is_target_x_static False for already-closing",
          not is_target_x_static(d15, w1, w1.ball_carrier, anchor_x=60.0))

    # D16: pure predicate — temporal flat window.
    d16 = MockBody(16, x=50.0, target_x=52.0)
    for _ in range(6):
        advance_static_window(d16)
    check("D16 is_target_x_static True for flat temporal window",
          is_target_x_static(d16, w1, w1.ball_carrier, anchor_x=60.0))

    # ══════════════════════════════════════════════════════════════
    # E. Engine imports cleanly
    # ══════════════════════════════════════════════════════════════
    print("\n[E] engine module tree imports cleanly")
    import importlib
    mods = [
        "game.defence.defence_lvl_1.line_break_static_guard",
        "game.defence.defence_lvl_1.movement_mode_decision",
        "game.defence.defence_lvl_1.writer_priority_bands",
        "game.managers.defence_manager",
    ]
    ok = True
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            ok = False
            print("      [import FAIL] %s: %s" % (m, e))
    check("E1 engine modules import", ok)

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
