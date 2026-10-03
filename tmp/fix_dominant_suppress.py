"""Add DOMINANT_DET_SUPPRESS to engine import and DOMINANT branch; mirror in harness."""
import re

# --- world/open_play_engine.py ---
path = "world/open_play_engine.py"
with open(path, encoding="utf-8") as f:
    text = f.read()

# 1. Import
OLD_IMPORT = "    DRIVE_DETERMINISTIC_BOOST,\n    compute_drive_force,"
NEW_IMPORT = "    DRIVE_DETERMINISTIC_BOOST,\n    DOMINANT_DET_SUPPRESS,\n    compute_drive_force,"
assert OLD_IMPORT in text, "import anchor missing"
text = text.replace(OLD_IMPORT, NEW_IMPORT, 1)

# 2. DOMINANT branch
OLD_BRANCH = (
    "        if ctx.dominance_result == \"DOMINANT\":\n"
    "            dx_step = -ctx.drive_step_mps * dt\n"
)
NEW_BRANCH = (
    "        if ctx.dominance_result == \"DOMINANT\":\n"
    "            dx_step = -ctx.drive_step_mps * dt\n"
    "            # Defender-win suppression (spec Step 5: defender wins).\n"
    "            dx_step *= DOMINANT_DET_SUPPRESS\n"
)
assert OLD_BRANCH in text, "branch anchor missing"
text = text.replace(OLD_BRANCH, NEW_BRANCH, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(text)

# --- tmp/tune_drive_distribution.py ---
path = "tmp/tune_drive_distribution.py"
with open(path, encoding="utf-8") as f:
    text = f.read()

OLD_H_IMPORT = "    DRIVE_DETERMINISTIC_BOOST,\n    DRIVE_DECAY,"
NEW_H_IMPORT = "    DRIVE_DETERMINISTIC_BOOST,\n    DOMINANT_DET_SUPPRESS,\n    DRIVE_DECAY,"
assert OLD_H_IMPORT in text, "harness import anchor missing"
text = text.replace(OLD_H_IMPORT, NEW_H_IMPORT, 1)

OLD_H_DET = (
    "    if dom == \"DOMINANT\":\n"
    "        det_step_mps = -drive_step_mps\n"
)
NEW_H_DET = (
    "    if dom == \"DOMINANT\":\n"
    "        det_step_mps = -drive_step_mps * DOMINANT_DET_SUPPRESS\n"
)
assert OLD_H_DET in text, "harness det anchor missing"
text = text.replace(OLD_H_DET, NEW_H_DET, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(text)

print("dominant suppress wired (engine + harness)")
