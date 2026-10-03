"""locate_s3d3.py - find real step-10 close writes + mmdl_* source stamps."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

FILES = [
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
    "game/defence/defence_lvl_1/defensive_role_movement.py",
    "game/defence/defence_lvl_1/defensive_decisions.py",
    "world/open_play_engine.py",
]
for f in FILES:
    try:
        s = open(f, encoding="utf-8", errors="replace").read()
    except OSError:
        continue
    print("=" * 70)
    print(f)
    hits = {}
    for pat in ("mmdl_CHASE", "mmdl_SLIDE", "mmdl_RETREAT", "mmdl_DRIFT",
                'target_source = "structured_close"',
                '"structured_close"', "step 10", "STEP 10"):
        n = s.count(pat)
        if n:
            lines = [s[:m.start()].count(chr(10)) + 1
                     for m in re.finditer(re.escape(pat), s)]
            hits[pat] = (n, lines[:12])
    for k, v in hits.items():
        print(f"  {k}: count={v[0]} lines={v[1]}")

# Dump the actual structured_close write region in DBU
s = open(FILES[0], encoding="utf-8", errors="replace").read()
i = s.find('"structured_close"')
if i != -1:
    ln = s[:i].count(chr(10)) + 1
    print("\n=== DBU structured_close write context ===")
    print(s[i - 2200:i + 600])
