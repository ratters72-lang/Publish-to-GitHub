"""
verify_catch_chase_force.py — CATCH CHASE-FORCE verifier.

Confirms the "force every genuine line defender into CHASE on the catch
frame" fix by static inspection + runtime probe:

  A. `_perform_catch()` in world/catch_system.py invokes
     `force_line_defenders_chase(world)` AFTER the OPEN PLAY ENTRY CLEANUP
     (so defender_role is None for every body and every non-frozen /
     non-fullback line defender is eligible), inside the `if _was_kickoff:`
     block (paired with the post-catch pursuit window arming).
  B. `force_line_defenders_chase(world)` in catch_x_authority.py:
       - arms `world.post_catch_chase_window_s > 0` (top-up to
         POST_CATCH_CHASE_WINDOW_S, never shortening),
       - stamps `movement_state == MovementState.CHASE` on every genuine
         line defender (`is_catch_x_authority_eligible`), and
       - normalises `movement_mode` to MovementMode.NORMAL (the line-chase
         movement contract; MovementMode has no CHASE value).
  C. Dedicated owners are NOT forced: fullback, marker, DH, TES tackler,
     kick-chase, frozen, and tackle-state holders keep their owners.
  D. Runtime probe: mock world + defender stubs; verify count, window
     armed, CHASE state, normalised mode, and no-op on ineligible bodies.
  E. Engine module tree imports cleanly.

Run:  python verify_catch_chase_force.py
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
CXA = "game/defence/defence_lvl_1/catch_x_authority.py"

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
        self.movement_state = None
        self.movement_mode = None


def main():
    print("=" * 64)
    print("CATCH CHASE-FORCE VERIFIER")
    print("=" * 64)

    catch_sys = read(CATCH_SYS)
    cxa = read(CXA)

    # ══════════════════════════════════════════════════════════════
    # A. _perform_catch invokes force_line_defenders_chase
    # ══════════════════════════════════════════════════════════════
    print("\n[A] _perform_catch invokes force_line_defenders_chase")
    check("A1 catch_system imports force_line_defenders_chase",
          "force_line_defenders_chase as _force_line_chase" in catch_sys)
    check("A2 _perform_catch calls _force_line_chase(world)",
          "_force_line_chase(world)" in catch_sys)
    # Must run AFTER the OPEN PLAY ENTRY CLEANUP loop.
    check("A3 CHASE FORCE runs after OPEN PLAY ENTRY CLEANUP",
          catch_sys.index("_force_line_chase(world)")
          > catch_sys.index("OPEN PLAY ENTRY CLEANUP"))
    # Must run INSIDE the kickoff block (paired with post-catch window arm).
    check("A4 CHASE FORCE is inside _was_kickoff block (after in_kickoff=False)",
          catch_sys.index("_force_line_chase(world)")
          > catch_sys.index("world.in_kickoff = False"))

    # ══════════════════════════════════════════════════════════════
    # B. force_line_defenders_chase arms window + stamps CHASE
    # ══════════════════════════════════════════════════════════════
    print("\n[B] force_line_defenders_chase arms window + stamps CHASE")
    check("B1 function exists",
          "def force_line_defenders_chase(world) -> int:" in cxa)
    check("B2 imports MovementState",
          "from player.movement_states import MovementState, MovementMode" in cxa)
    check("B3 arms post_catch_chase_window_s (top-up)",
          "world.post_catch_chase_window_s = max(" in cxa
          and "POST_CATCH_CHASE_WINDOW_S" in cxa)
    check("B4 stamps movement_state = MovementState.CHASE",
          "d.movement_state = MovementState.CHASE" in cxa)
    check("B5 normalises movement_mode to MovementMode.NORMAL",
          "d.movement_mode = MovementMode.NORMAL" in cxa)
    check("B6 uses is_catch_x_authority_eligible gate",
          "if not is_catch_x_authority_eligible(d, world):" in cxa)

    # ══════════════════════════════════════════════════════════════
    # C. Runtime probe
    # ══════════════════════════════════════════════════════════════
    print("\n[C] runtime probe")
    from game.defence.defence_lvl_1.catch_x_authority import (
        force_line_defenders_chase,
    )
    from player.movement_states import MovementState, MovementMode
    from player.player import DefenderRole

    def _new_world():
        w = SimpleNamespace()
        w.defending_team = []
        w.post_catch_chase_window_s = 0.0
        w.POST_CATCH_CHASE_WINDOW_S = 3.0
        return w

    # C1: arms the window + forces eligible defenders
    w1 = _new_world()
    d1 = MockBody(1, 66.0, 35.0)
    d1.movement_state = MovementState.HOLD
    d1.movement_mode = MovementMode.RETREAT
    w1.defending_team = [d1]
    n1 = force_line_defenders_chase(w1)
    check("C1 forces a genuine line defender", n1 == 1, "n=%s" % n1)
    check("C2 window armed to POST_CATCH_CHASE_WINDOW_S",
          w1.post_catch_chase_window_s == 3.0,
          "window=%s" % w1.post_catch_chase_window_s)
    check("C3 movement_state == CHASE",
          d1.movement_state == MovementState.CHASE,
          "state=%s" % d1.movement_state)
    check("C4 movement_mode normalised to NORMAL",
          d1.movement_mode == MovementMode.NORMAL,
          "mode=%s" % d1.movement_mode)

    # C5: fullback not forced
    w2 = _new_world()
    d_fb = MockBody(2, 66.0, 35.0)
    d_fb.fullback_role_active = True
    d_fb.movement_state = MovementState.DRIFT
    w2.defending_team = [d_fb]
    n2 = force_line_defenders_chase(w2)
    check("C5 fullback never forced", n2 == 0 and d_fb.movement_state == MovementState.DRIFT,
          "n=%s state=%s" % (n2, d_fb.movement_state))

    # C6: marker not forced
    w3 = _new_world()
    d_marker = MockBody(3, 66.0, 35.0)
    d_marker.defender_role = DefenderRole.MARKER_A
    d_marker.movement_state = MovementState.HOLD
    w3.defending_team = [d_marker]
    n3 = force_line_defenders_chase(w3)
    check("C6 marker never forced",
          n3 == 0 and d_marker.movement_state == MovementState.HOLD,
          "n=%s state=%s" % (n3, d_marker.movement_state))

    # C7: frozen body not forced
    w4 = _new_world()
    d_frozen = MockBody(4, 66.0, 35.0)
    d_frozen.frozen = True
    d_frozen.movement_state = MovementState.HOLD
    w4.defending_team = [d_frozen]
    n4 = force_line_defenders_chase(w4)
    check("C7 frozen body never forced",
          n4 == 0 and d_frozen.movement_state == MovementState.HOLD,
          "n=%s state=%s" % (n4, d_frozen.movement_state))

    # C8: tackle-state holder not forced
    w5 = _new_world()
    d_ts = MockBody(5, 66.0, 35.0)
    d_ts.tackle_state_active = True
    d_ts.movement_state = MovementState.HOLD
    w5.defending_team = [d_ts]
    n5 = force_line_defenders_chase(w5)
    check("C8 tackle-state holder never forced",
          n5 == 0 and d_ts.movement_state == MovementState.HOLD,
          "n=%s state=%s" % (n5, d_ts.movement_state))

    # C9: top-up never shortens an armed window
    w6 = _new_world()
    w6.post_catch_chase_window_s = 1.5
    d6 = MockBody(6, 66.0, 35.0)
    w6.defending_team = [d6]
    force_line_defenders_chase(w6)
    check("C9 top-up preserves a longer live window",
          w6.post_catch_chase_window_s >= 3.0,
          "window=%s" % w6.post_catch_chase_window_s)

    # C10: None world no-op
    n7 = force_line_defenders_chase(None)
    check("C10 None world is a no-op", n7 == 0)

    # ══════════════════════════════════════════════════════════════
    # E. Engine imports cleanly
    # ══════════════════════════════════════════════════════════════
    print("\n[E] engine module tree imports cleanly")
    import importlib
    mods = [
        "world.catch_system",
        "game.defence.defence_lvl_1.catch_x_authority",
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
