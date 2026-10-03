"""
verify_catch_x_authority_lock.py — CATCH X-authority lock verifier.

Confirms the tackler-freeze fix by static inspection + runtime probe:

  A. `_perform_catch()` in world/catch_system.py arms `catch_x_authority_lock`
     (via `arm_catch_x_authority`), and the OPEN PLAY ENTRY CLEANUP is the only
     X write for the non-BC players on that frame.
  B. `DefenceManager.update_open_play` invokes `apply_catch_x_authority` as the
     LAST X write (after the writer-priority-band resolution, before movement
     integration), so exactly ONE writer owns target_x while the lock is armed.
  C. `world_state.py` declares `catch_x_authority_lock` (default False), and
     `open_play_engine.py` clears it on kickoff reset / when the post-catch
     pursuit window expires.
  D. Runtime probe:
       - lock OFF  -> `apply_catch_x_authority` is a pure no-op;
       - lock ON   -> a genuine line defender's target_x becomes BC-relative
                      (bc.x + WIDTH_OFFSETS[depth_role]) and is stamped
                      target_source = "catch_x_authority";
       - lock ON   -> fullback / marker / DH / TES tackler / frozen bodies are
                      never touched (dedicated owners remain);
       - lock ON   -> target_y is never written (Y stays with spacing);
       - arm/clear cycle: `arm_catch_x_authority` sets the flag, `clear_catch_x_authority`
                      clears it.
  E. Engine module tree imports cleanly.

Run:  python verify_catch_x_authority_lock.py
Exit 0 on success, 1 on failure.
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

CATCH_SYS = "world/catch_system.py"
OPE = "world/open_play_engine.py"
WS = "world/world_state.py"
DM = "game/managers/defence_manager.py"

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
        self.depth_role = "D"
        self.depth_side = 1
        self._depth_seeded = True
        self.role_depth_target_x = x
        self.role_depth_target_y = y
        self.frame_spacing_y = y
        self.is_tackler = False
        self._tackle_commit_active = False
        self.target_source = None


def _make_tuners():
    t = SimpleNamespace()
    t.WIDTH_OFFSETS = {
        "A": -1.0, "B": -2.0, "C": -3.0, "D": -4.0,
        "CENTRE": -5.0, "WINGER": -6.0,
    }
    return t


def main():
    print("=" * 64)
    print("CATCH X-AUTHORITY LOCK VERIFIER")
    print("=" * 64)

    catch_sys = read(CATCH_SYS)
    ope = read(OPE)
    ws = read(WS)
    dm = read(DM)

    # ══════════════════════════════════════════════════════════════
    # A. _perform_catch arms the lock
    # ══════════════════════════════════════════════════════════════
    print("\n[A] _perform_catch arms the CATCH X-authority lock")
    check("A1 catch_system imports arm_catch_x_authority",
          "arm_catch_x_authority as _arm_catch_x" in catch_sys)
    check("A2 _perform_catch calls _arm_catch_x(world)",
          "_arm_catch_x(world)" in catch_sys)
    check("A3 arm happens before the OPEN PLAY ENTRY CLEANUP loop",
          catch_sys.index("_arm_catch_x(world)") < catch_sys.index("OPEN PLAY ENTRY CLEANUP"))

    # ══════════════════════════════════════════════════════════════
    # B. DefenceManager.update_open_play invokes the sole X writer LAST
    # ══════════════════════════════════════════════════════════════
    print("\n[B] update_open_play invokes apply_catch_x_authority LAST")
    check("B1 defence_manager imports apply_catch_x_authority",
          "apply_catch_x_authority as _apply_catch_x" in dm)
    check("B2 update_open_play calls _apply_catch_x(world, defenders, bc, self.tuners)",
          "_apply_catch_x(world, defenders, bc, self.tuners)" in dm)
    check("B3 catch authority runs AFTER the writer-priority-band resolve",
          dm.index("_apply_catch_x(world, defenders, bc, self.tuners)")
          > dm.index("_wpb_resolve(world, defenders, self.tuners, ope=ope)"))
    check("B4 catch authority runs BEFORE the movement integration loop",
          dm.index("_apply_catch_x(world, defenders, bc, self.tuners)")
          < dm.index("# ---- Apply movement ----"))

    # ══════════════════════════════════════════════════════════════
    # C. world flag + OPE clear
    # ══════════════════════════════════════════════════════════════
    print("\n[C] world flag declared + cleared by OPE")
    check("C1 world_state declares catch_x_authority_lock default False",
          "catch_x_authority_lock: bool = False" in ws)
    check("C2 OPE.update clears the lock when post_catch window expires",
          "_cxa_clear(self.world)" in ope and "post_catch_chase_window_s" in ope)
    check("C3 OPE.reset_for_kickoff clears the lock",
          "_cxa_clear(w)" in ope)

    # ══════════════════════════════════════════════════════════════
    # D. Runtime probe
    # ══════════════════════════════════════════════════════════════
    print("\n[D] runtime probe")
    from game.defence.defence_lvl_1.catch_x_authority import (
        apply_catch_x_authority,
        arm_catch_x_authority,
        clear_catch_x_authority,
        catch_x_authority_locked,
        is_catch_x_authority_eligible,
        CATCH_X_AUTHORITY_SOURCE,
    )
    from player.player import DefenderRole

    tuners = _make_tuners()
    bc = MockBody(99, 70.0, 35.0)

    def _new_world():
        w = SimpleNamespace()
        w.ball_carrier = bc
        w.catch_x_authority_lock = False
        w.defensive_line_anchor = 80.0
        w.post_catch_chase_window_s = 0.0
        return w

    # D1: lock OFF -> no-op
    w_off = _new_world()
    d_off = MockBody(1, 66.0, 35.0)
    d_off.target_x = 999.0
    wrote_off = apply_catch_x_authority(w_off, [d_off], bc, tuners)
    check("D1 lock OFF -> no-op (no writes, nothing returned)",
          wrote_off == [] and d_off.target_x == 999.0,
          "wrote=%s target_x=%s" % (wrote_off, d_off.target_x))

    # D2: lock ON -> genuine line defender gets BC-relative X
    w_on = _new_world()
    w_on.catch_x_authority_lock = True
    d_on = MockBody(2, 66.0, 35.0)
    d_on.depth_role = "D"
    d_on.target_x = 999.0
    d_on.target_source = "band:width"
    wrote_on = apply_catch_x_authority(w_on, [d_on], bc, tuners)
    check("D2 lock ON -> genuine line defender written",
          d_on in wrote_on and d_on.target_x == bc.x + tuners.WIDTH_OFFSETS["D"],
          "target_x=%s expected=%s" % (d_on.target_x, bc.x + tuners.WIDTH_OFFSETS["D"]))
    check("D3 target_source stamped catch_x_authority",
          d_on.target_source == CATCH_X_AUTHORITY_SOURCE,
          "target_source=%s" % d_on.target_source)

    # D4: lock ON -> fullback never touched
    d_fb = MockBody(3, 66.0, 35.0)
    d_fb.fullback_role_active = True
    d_fb.target_x = 999.0
    apply_catch_x_authority(w_on, [d_fb], bc, tuners)
    check("D4 fullback never touched", d_fb.target_x == 999.0,
          "target_x=%s" % d_fb.target_x)

    # D5: lock ON -> marker never touched
    d_marker = MockBody(4, 66.0, 35.0)
    d_marker.defender_role = DefenderRole.MARKER_A
    d_marker.target_x = 999.0
    apply_catch_x_authority(w_on, [d_marker], bc, tuners)
    check("D5 marker never touched", d_marker.target_x == 999.0,
          "target_x=%s" % d_marker.target_x)

    # D6: lock ON -> frozen body never touched
    d_frozen = MockBody(5, 66.0, 35.0)
    d_frozen.frozen = True
    d_frozen.target_x = 999.0
    apply_catch_x_authority(w_on, [d_frozen], bc, tuners)
    check("D6 frozen body never touched", d_frozen.target_x == 999.0,
          "target_x=%s" % d_frozen.target_x)

    # D7: Y never touched
    d_y = MockBody(6, 66.0, 35.0)
    d_y.target_y = 123.0
    apply_catch_x_authority(w_on, [d_y], bc, tuners)
    check("D7 target_y never written", d_y.target_y == 123.0,
          "target_y=%s" % d_y.target_y)

    # D8: arm/clear cycle
    w_cyc = _new_world()
    check("D8 initially unlocked",
          catch_x_authority_locked(w_cyc) is False)
    arm_catch_x_authority(w_cyc)
    check("D9 arm_catch_x_authority sets the flag",
          catch_x_authority_locked(w_cyc) is True)
    clear_catch_x_authority(w_cyc)
    check("D10 clear_catch_x_authority clears the flag",
          catch_x_authority_locked(w_cyc) is False)

    # D11: eligibility predicate
    d_elig = MockBody(7, 66.0, 35.0)
    check("D11 genuine line defender eligible",
          is_catch_x_authority_eligible(d_elig, w_on) is True)
    d_elig.fullback_role_active = True
    check("D12 fullback ineligible",
          is_catch_x_authority_eligible(d_elig, w_on) is False)

    # ══════════════════════════════════════════════════════════════
    # E. Engine imports cleanly
    # ══════════════════════════════════════════════════════════════
    print("\n[E] engine module tree imports cleanly")
    import importlib
    mods = [
        "world.catch_system",
        "world.open_play_engine",
        "world.world_state",
        "game.managers.defence_manager",
        "game.defence.defence_lvl_1.catch_x_authority",
        "game.defence.defence_lvl_1.writer_priority_bands",
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
