import sys

path = sys.argv[1]
with open(path, encoding="utf-8", errors="replace") as handle:
    lines = handle.read().splitlines()

print("lines:", len(lines))
for index, line in enumerate(lines):
    up = line.upper()
    if "FAIL" in up or "RESULT" in up or "CHECKS" in up:
        print(index, line)
