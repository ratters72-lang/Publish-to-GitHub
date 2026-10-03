import os
import re
import sys

sys.path.insert(0, os.getcwd())

TUNERS = "game/defence/defence_lvl_1/defensive_tuners.py"

print("=== A. how the two knobs are DECLARED in the tuner source ===")
with open(TUNERS, encoding="utf-8") as fh:
    for i, line in enumerate(fh, 1):
        if "compression" in line:
            print("%5d | %s" % (i, line.rstrip()))

print()
print("=== B. RNG usage in the boot / attack path ===")
FILES = [
    "world/open_play_engine.py",
    "world/world_state.py",
    "world/catch_system.py",
    "game/kickoff_engine.py",
    "game/tes/tes.py",
    "game/tes/tes_drive_v1.py",
    "game/tes/tes_dominance.py",
    "game/tes/tes_drive_variation.py",
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
    "game/defence/defence_lvl_1/pre_tackle_authority.py",
    "game/physics/collision_system.py",
]
pat = re.compile(r"(random\.\w+|Random\(|np\.random|numpy\.random|"
                 r"\.seed\()")
for fp in FILES:
    if not os.path.isfile(fp):
        print("  (missing)", fp)
        continue
    with open(fp, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            if pat.search(line):
                print("%-58s %5d | %s" % (fp.replace("\\", "/"), i,
                                          line.strip()[:96]))

print()
print("=== C. is run_one reproducible across two calls? ===")
import io
import contextlib
import probe_goal_line_outcomes as p

def once():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return p.run_one("neutral", 0.95, 1.0, 11, 0.0)

a = once()
b = once()
keys = sorted(set(a) | set(b))
for k in keys:
    if a.get(k) != b.get(k):
        print("  DIFF %-18s a=%r  b=%r" % (k, a.get(k), b.get(k)))
print("  identical:", all(a.get(k) == b.get(k) for k in keys))
