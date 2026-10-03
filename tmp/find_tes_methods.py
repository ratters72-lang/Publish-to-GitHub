import re

path = "world/open_play_engine.py"
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

patterns = (
    "_start_tackle", "_maybe_add_joiners", "_update_tackle_init",
    "_update_tackle_contact", "_update_tackle_drive",
    "_update_tackle_dominance", "_prepare_release", "_update_release",
    "_reset_tackle_state", "_check_break_tackle", "_update_open_play",
    "_check_pre_tackle", "_handle_line_break",
    "_check_broken_tackle_scoring", "_handle_broken_tackle",
    "_prepare_bc_dh_for_tackle",
)
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    for pat in patterns:
        if stripped.startswith("def " + pat + "("):
            print(f"{i}: {stripped}")
print(f"TOTAL LINES: {len(lines)}")
