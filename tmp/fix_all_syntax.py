import re

with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace commented-out triple-quoted strings inside # comments with regular comments
# Pattern: #     """some text""" or similar inside #-prefixed lines
# These cause SyntaxError because Python sees the """ as starting a string
count = 0

lines = content.split('\n')
new_lines = []
for line in lines:
    stripped = line.strip()
    # If this is a comment line that contains """ (a commented-out docstring)
    if stripped.startswith('#') and '"""' in stripped:
        # Replace the """ with nothing, keeping the comment text
        line = line.replace('"""', '')
    new_lines.append(line)

new_content = '\n'.join(new_lines)

with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Done - removed all triple-quote artifacts from comment lines")
