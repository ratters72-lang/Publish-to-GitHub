"""Task XIV — compile + import smoke for touched modules."""
import py_compile
import sys

sys.path.insert(0, ".")

FILES = [
    "game/defence/defence_lvl_1/pretackle_attempt.py",
    "game/defence/defence_lvl_1/defensive_tuners.py",
    "game/managers/defence_manager.py",
    "world/open_play_engine.py",
]

ok = True
for f in FILES:
    try:
        py_compile.compile(f, doraise=True)
        print(f"COMPILE OK  {f}")
    except Exception as exc:
        ok = False
        print(f"COMPILE FAIL {f}: {type(exc).__name__}: {exc}")

# Import smoke: authority module + tuners + engine chain.
try:
    from game.defence.defence_lvl_1.pretackle_attempt import (
        apply_pretackle_attempts,
        reset_pretackle_attempts,
        clear_pretackle_state,
        is_pretackle_managed,
        is_pretackle_eligible,
        pretackle_attempt_enabled,
        PTA_TARGET_SOURCE,
        PTA_COMMIT,
        PTA_PLANTED,
        PTA_SLOW,
        PTA_HOLD,
    )
    print("IMPORT OK   pretackle_attempt")
except Exception as exc:
    ok = False
    print(f"IMPORT FAIL pretackle_attempt: {type(exc).__name__}: {exc}")

try:
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
    t = DefenceTuners()
    assert t.pretackle_attempt_enabled is False, "gate must default OFF"
    assert isinstance(t.pretackle_slow_speed_mps, float)
    assert isinstance(t.pretackle_freeze_radius_m, float)
    assert isinstance(t.pretackle_follow_smooth, float)
    assert isinstance(t.pretackle_commit_speed_mps, float)
    assert isinstance(t.pretackle_plant_radius_m, float)
    assert isinstance(t.pretackle_slowdown_band_m, float)
    assert isinstance(t.pretackle_slowdown_scale, float)
    assert isinstance(t.pretackle_commit_depth_offset_m, float)
    print("IMPORT OK   DefenceTuners (gate defaults OFF)")
except AssertionError as exc:
    ok = False
    print(f"TUNERS FAIL: {exc}")
except Exception as exc:
    ok = False
    print(f"IMPORT FAIL DefenceTuners: {type(exc).__name__}: {exc}")

try:
    from world.open_play_engine import OpenPlayEngine
    print("IMPORT OK   OpenPlayEngine")
except Exception as exc:
    ok = False
    print(f"IMPORT FAIL OpenPlayEngine: {type(exc).__name__}: {exc}")

print("RESULT:", "ALL CHECKS PASSED" if ok else "FAILURES PRESENT")
sys.exit(0 if ok else 1)
