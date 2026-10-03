import re

# --- tes.py: find _make_ctx and dominance_result ---
path = "game/tes/tes.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()
print(f"===== {path} (total {len(lines)} lines) =====")
for i, line in enumerate(lines, 1):
    if "_make_ctx" in line or "dominance_result" in line or "def " in line:
        print(f"{i:5d}  {line.rstrip()}")

# --- player.py: find _clamp_position and tes_active ---
path = "player/player.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()
print(f"\n===== {path} (total {len(lines)} lines) =====")
for i, line in enumerate(lines, 1):
    if "_clamp_position" in line or "tes_active" in line or "TACKLE_DRIVE" in line or "TACKLE_DOMINANCE" in line:
        print(f"{i:5d}  {line.rstrip()}")
