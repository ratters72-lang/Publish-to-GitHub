import ast

PATH = "game/defence/defence_lvl_1/fullback_controller.py"
src = open(PATH, encoding="utf-8").read()
tree = ast.parse(src)

for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == "FullbackController":
        print("class", node.name, "line", node.lineno)
        for sub in node.body:
            if isinstance(sub, ast.FunctionDef):
                args = [a.arg for a in sub.args.args]
                print("  %5d %s(%s)" % (sub.lineno, sub.name, ", ".join(args)))
            elif isinstance(sub, ast.Assign):
                for t in sub.targets:
                    if isinstance(t, ast.Name):
                        print("  %5d .%s" % (sub.lineno, t.id))
