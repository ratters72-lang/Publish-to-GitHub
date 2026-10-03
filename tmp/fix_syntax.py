"""Fix syntax error in open_play_engine.py - the disabled approach block may be unclosed."""

with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the problem area - search for the pattern around line 1029
# The disabled block is a multi-line comment but may have been broken
idx = content.find('def _check_break_tackle')
if idx > 0:
    before = content[idx-600:idx]
    # Check if the disabled block is properly closed
    disabled_start = before.rfind('# [STEP-02A-01]')
    disabled_end = before.rfind('# ]')
    if disabled_start >= 0 and disabled_end < disabled_start:
        # The disabled block may not be closed
        print("Found unclosed disabled block")
        # Find the end of the disabled block (it should end with a blank line before def)
        # The block was: # def _enter_tackle_approach ... #     bc.vy = ny * speed
        # We need to find where it ends
        before_fix = content[:disabled_start]
        after_fix = content[disabled_start:]
        # The disabled block strings need closing
        # Actually the disabled block is a set of "# " comments, so it can't be unclosed
        # The real issue might be a different syntax error nearby
        pass

# Use a simpler approach: compile each small section to find the exact error
import ast
lines = content.split('\n')
print(f"Total lines: {len(lines)}")
for i in range(1020, min(len(lines), 1040)):
    line = lines[i]
    if line.strip() and not line.strip().startswith('#'):
        print(f'{i+1}: {line.rstrip()}')
