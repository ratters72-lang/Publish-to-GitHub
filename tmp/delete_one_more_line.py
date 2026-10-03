with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Line 1120 (index 1119) is a stale line with wrong indent
del lines[1119]

with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

# Verify
import ast
try:
    ast.parse(''.join(lines))
    print("SYNTAX OK!")
except SyntaxError as e:
    print(f"Still error at line {e.lineno}: {e.msg}")
    for i in range(max(0, e.lineno-3), min(len(lines), e.lineno+3)):
        print(f"  {i+1}: {repr(lines[i][:100])}")
