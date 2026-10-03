"""Remove the stale line from old corrupted _check_break_tackle that survived the replacement."""
import ast

with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# The problematic line 1120 is a stale continuation from the old _check_break_tackle
# Find all occurrences of the problematic fragment
stale_line = '                  f"def_resistance={def_resistance:.1f} x 1.4 -- BREAK!")'

if stale_line in content:
    print(f"Found stale line, removing it")
    content = content.replace(stale_line + '\n', '', 1)

try:
    ast.parse(content)
    print("SYNTAX OK!")
    with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
        f.write(content)
except SyntaxError as e:
    print(f"Still has syntax error at line {e.lineno}: {e.msg}")
    lines = content.split('\n')
    for i in range(max(0, e.lineno-5), min(len(lines), e.lineno+5)):
        indicator = " >>>" if i+1 == e.lineno else "    "
        print(f"{indicator} {i+1}: {repr(lines[i][:120])}")
