import re

path = "world/open_play_engine.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()

targets = [
    "_update_tackle_drive",
    "_update_tackle_contact",
    "_update_tackle_dominance",
    "tackle_result",
    "TACKLE_DRIVE",
    "variation_step",
    "drive_variation_x",
    "dominance_result",
    "drive_elapsed_s",
    "DRIVE_VARIATION_ATTACKER_BOOST",
    "_clamp_position",
    "tes_active",
    "CONTACT -> TACKLE_DRIVE",
    "TesOutcome",
]
for i, line in enumerate(lines, 1):
    for t in targets:
        if t in line:
            print(f"{i:5d}  {line.rstrip()}")
            break
