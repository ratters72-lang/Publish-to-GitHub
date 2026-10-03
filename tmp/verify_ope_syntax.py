import ast
with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    source = f.read()
try:
    ast.parse(source)
    print("SYNTAX OK!")
except SyntaxError as e:
    print(f"Syntax error at line {e.lineno}: {e.msg}")
    # Show context
    lines = source.split('\n')
    for i in range(max(0, e.lineno-3), min(len(lines), e.lineno+3)):
        indicator = " >>>" if i+1 == e.lineno else "    "
        print(f"{indicator} {i+1}: {lines[i].rstrip()}")
