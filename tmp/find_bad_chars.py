with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find all lines that have non-ASCII chars and aren't purely comments
import re
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    # Skip blank lines and pure comment lines
    if not stripped or stripped.startswith('#'):
        continue
    # Skip docstrings (triple-quoted strings)
    if '"""' in stripped:
        continue
    # Check for non-ASCII
    non_ascii = [(j, c) for j, c in enumerate(stripped) if ord(c) > 127]
    if non_ascii:
        positions = [(pos, char, hex(ord(char))) for pos, char in non_ascii]
        print(f"Line {i}: {positions[:5]}")
        print(f"  Content: {stripped[:120]}")
