import ast, sys
try:
    with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
        source = f.read()
    ast.parse(source)
    print("SYNTAX OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR at line {e.lineno}: {e.msg}")
    print(f"Text: {e.text}")
    sys.exit(1)
