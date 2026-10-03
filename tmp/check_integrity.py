"""Check if open_play_engine.py has the correct structure (all key methods)."""
import ast, sys

with open("world/open_play_engine.py", encoding="utf-8") as f:
    text = f.read()

# Check key methods exist
for name in ["_update_tackle_drive", "_update_tackle_dominance", "_apply_defence_sdm",
             "update", "_set_phase", "_update_release", "_prepare_release",
             "_assign_markers_for_new_tackle", "_clear_tes_artefacts"]:
    if f"def {name}" in text:
        print(f"  OK: {name}")
    else:
        print(f"  MISSING: {name}")

# Parse AST
try:
    tree = ast.parse(text)
    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    methods = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    print(f"\nClasses: {classes}")
    print(f"\nTotal methods: {len(methods)}")
    print("Syntax OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR: {e}")
    sys.exit(1)

# Check size
import os
print(f"\nFile size: {os.path.getsize('world/open_play_engine.py')} bytes")
print(f"Expected: ~126000 bytes")
