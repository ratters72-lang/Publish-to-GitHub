import re

PATH = "world/open_play_engine.py"

KEYS = [
    "run_frame_preamble",
    "update_tes_drive",
    "apply_phase_invariants",
    "def_behaviour.apply",
    "_update_defence",
    "drive_end_authoritative",
    "update_open_play",
    "update_tackle_gate",
    "enforce_ruck_pin",
]

with open(PATH, "r", encoding="utf-8") as f:
    lines = f.readlines()

print(f"TOTAL LINES: {len(lines)}")
print("=" * 70)
print("DEF/CLASS INDEX")
print("=" * 70)
for i, ln in enumerate(lines, 1):
    if re.match(r"^\s*(def|class) ", ln):
        print(f"{i}: {ln.rstrip()}")
print("=" * 70)
print("KEY CALL SITES (with 12 lines context)")
print("=" * 70)
for i, ln in enumerate(lines, 1):
    hit = None
    for k in KEYS:
        if k in ln:
            hit = k
            break
    if hit is None:
        continue
    lo = max(0, i - 1 - 12)
    hi = min(len(lines), i + 12)
    print(f"--- match '{hit}' at line {i} ---")
    for j in range(lo, hi):
        print(f"{j+1}: {lines[j].rstrip()}")
    print()
