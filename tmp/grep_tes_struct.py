import os, re

PAT = re.compile(r"tes_drive_structure|apply_tes_drive_structure|clear_tes_drive_structure|is_tes_structure_hold")
ROOT = "."
SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv", "debug_archive"}

for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP]
    for fn in filenames:
        if not fn.endswith(".py"):
            continue
        p = os.path.join(dirpath, fn)
        try:
            with open(p, "r", encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    if PAT.search(line):
                        print(f"{p}:{i}: {line.rstrip()}")
        except Exception as e:
            print(f"ERR {p}: {e}")
