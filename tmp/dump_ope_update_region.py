with open("world/open_play_engine.py", "r", encoding="utf-8") as f:
    lines = f.readlines()
out = []
for i in range(1104, min(1142, len(lines))):
    out.append(f"{i+1}: {lines[i].rstrip()}")
with open("tmp/ope_update_region.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("done")
