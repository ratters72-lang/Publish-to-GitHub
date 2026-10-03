with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Line 1120 has wrong indent - it's a continuation of a print in _check_break_tackle
# Show context
for i in range(1110, 1140):
    if i < len(lines):
        print(f"{i+1}: {repr(lines[i].rstrip())}")
