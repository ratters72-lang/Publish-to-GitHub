path = "world/open_play_engine.py"
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

ranges = [
    (1684, 2170, "_start_tackle -> _prepare_release start"),
]
for start, end, label in ranges:
    print(f"===== {label} (lines {start}-{end}) =====")
    for i in range(start - 1, min(end, len(lines))):
        print(f"{i+1}: {lines[i]}", end="")
    print()
