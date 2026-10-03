import io, sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

TARGETS = [
    ("game/defence/defence_lvl_1/defensive_behaviour_lvl1.py", [(250, 300), (500, 640)]),
    ("world/open_play_engine.py", [(2620, 2760), (3290, 3345), (3450, 3620), (3640, 3700)]),
]

out = io.StringIO()
for path, ranges in TARGETS:
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    for lo, hi in ranges:
        out.write("\n===== %s lines %d-%d (of %d) =====\n" % (path, lo, hi, len(lines)))
        for i in range(lo - 1, min(hi, len(lines))):
            out.write("%5d| %s" % (i + 1, lines[i]))
with open("tmp/dump_regions2.txt", "w", encoding="utf-8") as f:
    f.write(out.getvalue())
print("DONE", len(out.getvalue()))
