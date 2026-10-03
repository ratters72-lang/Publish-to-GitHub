# --- tes_context.py: TESContext fields ---
path = "game/tes/tes_context.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()
print(f"===== {path} (total {len(lines)} lines) =====")
for i, line in enumerate(lines, 1):
    if "dominance_result" in line or "class TESContext" in line or "drive_elapsed" in line or "drive_variation" in line:
        print(f"{i:5d}  {line.rstrip()}")

# --- tes_dominance.py: compute_dominance + dominance_result ---
path = "game/tes/tes_dominance.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()
print(f"\n===== {path} (total {len(lines)} lines) =====")
for i, line in enumerate(lines, 1):
    if "def compute_dominance" in line or "dominance_result" in line or "tackle_result" in line:
        print(f"{i:5d}  {line.rstrip()}")

# --- tes.py: full _make_ctx body (lines 225-295) ---
path = "game/tes/tes.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()
print(f"\n===== game/tes/tes.py _make_ctx (lines 225-294) =====")
for i in range(225, 295):
    print(f"{i:5d}  {lines[i-1].rstrip()}")

# --- world_state.py: GameSubState values ---
path = "world/world_state.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()
print(f"\n===== world/world_state.py GameSubState (scan) =====")
in_gss = False
for i, line in enumerate(lines, 1):
    if "class GameSubState" in line:
        in_gss = True
    if in_gss:
        print(f"{i:5d}  {line.rstrip()}")
    if in_gss and line.strip() and not line.strip().startswith(("#", "class", '"')):
        if "=" in line or "Enum" in line:
            pass
    if in_gss and "class " in line and "GameSubState" not in line:
        break
