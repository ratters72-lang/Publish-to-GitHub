"""Print exact OPE sections needed for TES wiring edits."""

path = "world/open_play_engine.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()

# 1) Constants block lines containing RECOVERY_BACKWARD_DISTANCE_M
print("=== RECOVERY constant ===")
for i, line in enumerate(lines, 1):
    if "RECOVERY_BACKWARD_DISTANCE_M" in line:
        print(f"{i}: {line.rstrip()}")

# 2) _start_tackle method (capture until 'def' at col 4)
print("\n=== _start_tackle ===")
start = None
for i, line in enumerate(lines, 1):
    if line.startswith("    def _start_tackle"):
        start = i - 1
        break
if start is not None:
    for j in range(start, len(lines)):
        if j > start and lines[j].startswith("    def "):
            break
        print(lines[j].rstrip())

# 3) DOMINANCE->RELEASE boundary (_prepare_release call in _update_tackle_dominance tail)
print("\n=== DOMINANCE tail (prepare_release vicinity) ===")
for i, line in enumerate(lines, 1):
    if "_prepare_release()" in line or "_just_entered_release = True" in line:
        lo = max(0, i - 4)
        hi = min(len(lines), i + 3)
        for k in range(lo, hi):
            print(f"{k+1}: {lines[k].rstrip()}")
        print("---")

# 4) _update_release first-frame / just_entered block
print("=== _update_release ===")
start = None
for i, line in enumerate(lines, 1):
    if line.startswith("    def _update_release"):
        start = i - 1
        break
if start is not None:
    for j in range(start, min(start + 60, len(lines))):
        print(lines[j].rstrip())
