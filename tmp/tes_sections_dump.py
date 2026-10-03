path = "world/open_play_engine.py"
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

ranges = [
    (2381, 2530),   # _update_release
]
with open("tmp/tes_sections_c.txt", "w", encoding="utf-8") as out:
    for start, end in ranges:
        out.write(f"===== lines {start}-{end} =====\n")
        for i in range(start - 1, min(end, len(lines))):
            out.write(f"{i+1}: {lines[i]}")
        out.write("\n")
print("written tmp/tes_sections_c.txt")
