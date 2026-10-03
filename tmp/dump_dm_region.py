import io
with io.open("game/managers/defence_manager.py", "r", encoding="utf-8") as f:
    lines = f.readlines()
# Print lines 600-770 (1-indexed) with explicit markers
for i in range(599, min(770, len(lines))):
    print(f"{i+1:5d}|{lines[i]!r}")
