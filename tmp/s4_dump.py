import re

PATH = "game/defence/defence_lvl_1/fullback_controller.py"
src = open(PATH, encoding="utf-8").read()
lines = src.splitlines()

start = 0
for i, line in enumerate(lines):
    if "STAGE 4" in line.upper():
        start = max(0, i - 4)
        break

print("TOTAL LINES", len(lines))
print("START", start)
for i in range(start, len(lines)):
    print("%5d| %s" % (i + 1, lines[i]))
