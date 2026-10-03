"""Extract _update_tackle_drive and _update_tackle_dominance from open_play_engine.py"""
with open("world/open_play_engine.py", encoding="utf-8") as f:
    lines = f.readlines()

in_method = False
depth = 0
for i, line in enumerate(lines):
    stripped = line.lstrip()
    if stripped.startswith("def _update_tackle_drive") or stripped.startswith("def _update_tackle_dominance"):
        in_method = True
        depth = 0
        print(f"\n\n=== {stripped.rstrip()} (line {i+1}) ===")
    if in_method:
        print(line, end="")
        if stripped and not stripped.startswith("#"):
            depth += line.count("    ") - line.rstrip().count("    ")
        if "):" in stripped and not stripped.startswith("#") and depth <= 0:
            break
