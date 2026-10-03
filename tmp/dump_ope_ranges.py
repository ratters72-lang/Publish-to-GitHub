PATH = "world/open_play_engine.py"
OUT = "tmp/ope_ranges.txt"

RANGES = [
    (1345, 1560),
    (1560, 1930),
    (2415, 2445),
    (2665, 2720),
]

with open(PATH, "r", encoding="utf-8") as f:
    lines = f.readlines()

buf = []
for lo, hi in RANGES:
    buf.append("=" * 70)
    buf.append(f"RANGE {lo}-{hi}")
    buf.append("=" * 70)
    for j in range(lo - 1, min(hi, len(lines))):
        txt = lines[j].rstrip()
        txt = txt.encode("ascii", "replace").decode("ascii")
        buf.append(f"{j+1}: {txt}")
    buf.append("")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(buf) + "\n")

print("wrote", OUT, len(buf), "lines")
