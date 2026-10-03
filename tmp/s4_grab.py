import ast
import sys

PATH = "game/defence/defence_lvl_1/fullback_controller.py"
src = open(PATH, encoding="utf-8").read()
tree = ast.parse(src)

WANT = set(sys.argv[1:])
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
        print("### %s (line %d)" % (node.name, node.lineno))
        print(ast.get_source_segment(src, node))
        print()
