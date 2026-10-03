"""The new methods were inserted inside _check_break_tackle's body. 
Fix: Find where _check_break_tackle ends and move the misplaced methods after it."""

with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the first misplaced method
misplaced_start = None
misplaced_end = None
check_break_tackle_end = None

for i, line in enumerate(lines):
    if 'def _check_pre_tackle' in line:
        misplaced_start = i
    if misplaced_start is not None and misplaced_end is None:
        # Find where the misplaced block ends - the next def or end of file
        if i > misplaced_start and ('def ' in line and '_check_broken' not in line and '_handle_broken' not in line):
            misplaced_end = i
            break
    # Find the real end of _check_break_tackle (before the misplaced methods)
    if 'def _check_break_tackle' in line:
        check_break_tackle_start = i

# Actually find the true end of _check_break_tackle
# It has a docstring and ends with the chase_radius block
for i in range(1007, 1030):
    if i < len(lines):
        print(f"{i+1}: {lines[i].rstrip()}")
