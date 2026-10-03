import py_compile

FILES = [
    "game/defence/defence_lvl_1/defensive_tuners.py",
    "game/defence/defence_lvl_1/defensive_line_governor.py",
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
    "game/defence/defence_lvl_1/defensive_role_movement.py",
    "game/kickoff_engine.py",
]
for path in FILES:
    py_compile.compile(path, doraise=True)
    print("COMPILED:", path)

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
t = DefenceTuners()
assert t.max_defensive_speed_mps == 5.0, t.max_defensive_speed_mps
assert t.post_catch_cap_mps == 5.0, t.post_catch_cap_mps
print("TUNERS OK: max_defensive_speed_mps=%.1f post_catch_cap_mps=%.1f" % (
    t.max_defensive_speed_mps, t.post_catch_cap_mps))

# Verify every movement writer derives from the unified ceiling.
import io, re

def assert_no_raw_ceiling(path, patterns):
    with io.open(path, "r", encoding="utf-8") as f:
        src = f.read()
    for pat in patterns:
        assert re.search(pat, src) is None, (
            "RAW CEILING STILL PRESENT in %s: %s" % (path, pat)
        )

# No subsystem may use hard-coded 5.5/9.2/6.5 top speeds for defenders.
assert_no_raw_ceiling(
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
    [r"max_speed_mps\", 9\.2\)", r"9\.2\) \* 0", r"getattr\(d, \"max_speed_mps\", 6\.5\)"],
)
# role_movement must use _capped_speed everywhere.
with io.open("game/defence/defence_lvl_1/defensive_role_movement.py", "r", encoding="utf-8") as f:
    rm = f.read()
assert "max_speed_mps\", 9.2" not in rm, "role_movement raw 9.2 ceiling"
assert "_capped_speed(" in rm, "role_movement missing _capped_speed"
print("ROLE MOVEMENT OK: capped speeds")

print("ALL CHECKS PASSED")
