import io

path = "game/defence/defence_lvl_1/defensive_behaviour_unified.py"
with io.open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

ranges = [
    (782, 794),
    (1000, 1048),
    (1150, 1180),
    (1305, 1330),
    (1380, 1400),
    (1445, 1462),
    (1515, 1555),
]

out = []
for start, end in ranges:
    out.append("=== %d-%d ===" % (start, end))
    for i in range(start - 1, end):
        out.append("%4d: %s" % (i + 1, lines[i].rstrip("\n")))

with io.open("tmp/dump_out.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("dumped")
