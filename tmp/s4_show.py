import sys

PATH = "game/defence/defence_lvl_1/fullback_controller.py"
lo = int(sys.argv[1])
hi = int(sys.argv[2])
out = sys.argv[3]
lines = open(PATH, encoding="utf-8").read().splitlines()
with open(out, "w", encoding="utf-8") as fh:
    for i in range(lo - 1, min(hi, len(lines))):
        fh.write("%5d| %s\n" % (i + 1, lines[i]))
print("wrote", out, lo, hi)
