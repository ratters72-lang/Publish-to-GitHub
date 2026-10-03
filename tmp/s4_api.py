import ast
import sys

sys.path.insert(0, ".")

PATH = "game/defence/defence_lvl_1/fullback_controller.py"
src = open(PATH, encoding="utf-8").read()
tree = ast.parse(src)

names = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        names.append(("def", node.name, node.lineno))
    elif isinstance(node, ast.ClassDef):
        names.append(("class", node.name, node.lineno))
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                names.append(("var", t.id, node.lineno))

print("LINES", len(src.splitlines()))
for kind, name, line in names:
    if "locomotion" in name.lower() or "LOCOMOTION" in name or kind == "class":
        print("%5d %-6s %s" % (line, kind, name))
