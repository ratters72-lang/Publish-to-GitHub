# --- tes_drive_variation.py: full contents ---
path = "game/tes/tes_drive_variation.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()
print(f"===== {path} (total {len(lines)} lines) =====")
for i, line in enumerate(lines, 1):
    print(f"{i:5d}  {line.rstrip()}")
