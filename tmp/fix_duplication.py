"""Fix duplicated content: remove lines 693-2501 which is the duplicated _set_phase and everything else."""
with open("world/open_play_engine.py", encoding="utf-8") as f:
    lines = f.readlines()

# The original file had update() ending around line 690, and _set_phase starting at 693
# The duplicate region is lines 693-2501 (everything between the two _set_phase defs)

# Check: line 693 starts the first _set_phase, and line 2502 starts the second _set_phase
print(f"Line 693: {lines[692].rstrip()[:80]}")
print(f"Line 2502: {lines[2501].rstrip()[:80]}")

# We need to remove lines 693 through 2501 (inclusive)
# That's the duplicated _set_phase and everything between it and the second copy

lines_fixed = lines[:693] + lines[2502:]
print(f"Original: {len(lines)} lines")
print(f"Fixed: {len(lines_fixed)} lines")

with open("world/open_play_engine.py", "w", encoding="utf-8") as f:
    f.writelines(lines_fixed)

print("Done - duplication removed")
