"""Fix the misplaced methods - they were placed inside _check_break_tackle.
Extract them and place them correctly before it."""

with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find key lines
check_pre_line = None
check_break_line = None
broken_check_line = None
handle_broken_line = None
next_def_after_misplaced = None

for i, line in enumerate(lines):
    if 'def _check_pre_tackle' in line:
        check_pre_line = i
    if 'def _check_break_tackle' in line:
        check_break_line = i
    if 'def _check_broken_tackle_scoring' in line:
        broken_check_line = i
    if 'def _handle_broken_tackle' in line:
        handle_broken_line = i

# Find where the misplaced methods end (next def after _handle_broken)
for i in range(handle_broken_line + 1, min(handle_broken_line + 200, len(lines))):
    if 'def _start_tackle' in line or 'def _prepare_bc' in line:
        next_def_after_misplaced = i
        break

print(f"_check_pre_tackle at line {check_pre_line}")
print(f"_check_break_tackle at line {check_break_line}")
print(f"_check_broken_tackle_scoring at {broken_check_line}")
print(f"_handle_broken_tackle at {handle_broken_line}")
print(f"Next def at {next_def_after_misplaced}")

# The misplaced methods start at check_pre_line and go to next_def_after_misplaced - 1
misplaced_lines = lines[check_pre_line:next_def_after_misplaced]

# Show first and last
print(f"\nFirst misplaced line: {repr(misplaced_lines[0].rstrip())}")
print(f"Last misplaced line: {repr(misplaced_lines[-1].rstrip())}")

# Remove the misplaced methods from their current location
del lines[check_pre_line:next_def_after_misplaced]

# Insert them right before _check_break_tackle (which shifted by the delete)
# After deletion, _check_break_tackle is at check_break_line
# Insert at check_break_line (which is now earlier since we removed lines before it)
new_break_line = check_break_line  # stays same since we deleted after it
insert_at = new_break_line - 1  # before the blank line

# Actually we need to find where # [STEP-02A-01] block ends
# and insert after it, before _check_break_tackle
# The comment block is between line 986-area
comment_block_end = None
for i in range(new_break_line - 20, new_break_line):
    if i >= 0 and lines[i].strip() == '' and 'def _check_break_tackle' in lines[i+1]:
        comment_block_end = i + 1  # blank line before def

# Insert after the blank line before _check_break_tackle
for i in range(new_break_line - 10, new_break_line + 1):
    print(f"{i}: {repr(lines[i].rstrip())}")

print(f"\nInserting {len(misplaced_lines)} lines back before _check_break_tackle")

# Actually let's find the proper insertion point - after the disabled comment block
# and before the _check_break_tackle method
for i in range(new_break_line - 5, new_break_line):
    if lines[i].strip() == '' and lines[i+1].strip() == '':
        # Two blank lines - insert between them
        insert_point = i + 1
        break
else:
    insert_point = new_break_line  # just before def

# Insert with proper indentation 
# But the misplaced methods already have correct indentation (4 spaces)
# They just need a blank line before them
misplaced_lines.insert(0, '\n')  # blank line separator

lines[insert_point:insert_point] = misplaced_lines

with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Fixed! Methods extracted and re-inserted at correct position.")
