"""Diagnose the corruption in open_play_engine.py"""
lines = open("world/open_play_engine.py", encoding="utf-8").readlines()
print(f"Total lines: {len(lines)}")

# Find all 'def _apply_defence_sdm' occurrences
for i, l in enumerate(lines):
    if "def _apply_defence_sdm" in l or "def update" in l or "def _set_phase" in l:
        print(f"  {i+1}: {l.rstrip()[:80]}")

# Find how many times update() appears
update_starts = [i for i, l in enumerate(lines) if l.strip() == "def update(self, dt: float) -> None:"]
print(f"\nupdate() appears {len(update_starts)} times")

# Check if _apply_defence_sdm appears after the role movement section
for i, l in enumerate(lines):
    if "self._apply_defence_sdm(dt)" in l:
        print(f"\nSDM call at line {i+1}")
        # Show context
        for j in range(max(0,i-2), min(len(lines), i+3)):
            print(f"  {j+1}: {lines[j].rstrip()}")
