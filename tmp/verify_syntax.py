import ast
import sys
with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    source = f.read()
try:
    ast.parse(source)
    print("SYNTAX OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR: {e}")
    sys.exit(1)

# Verify the gate exists
if "if self._phase != \"OPEN_PLAY\":\n            return" in source:
    print("GATE FOUND in _update_defenders_tes_phases")
else:
    print("WARNING: Gate not found!")
    sys.exit(1)
