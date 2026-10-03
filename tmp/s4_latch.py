import ast
import re

PATH = "game/defence/defence_lvl_1/fullback_role.py"
src = open(PATH, encoding="utf-8").read()
tree = ast.parse(src)

for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if "latch" in node.name.lower():
            print("### %s (line %d)" % (node.name, node.lineno))
            print(ast.get_source_segment(src, node))
            print()

print("--- lines mentioning _fb_break / latch attr ---")
for i, line in enumerate(src.splitlines(), 1):
    if re.search(r"latch", line, re.I):
        print("%5d| %s" % (i, line))
