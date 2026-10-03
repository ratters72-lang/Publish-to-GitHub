import os
import ast
from collections import defaultdict

ROOT = r"C:\Users\User\Desktop\rlm"

definitions = defaultdict(list)
calls = defaultdict(list)
imports = defaultdict(list)
files_seen = set()
duplicates = defaultdict(list)

TARGETS = {
    "check_for_catch",
    "predict_catch_point"
}

def analyze_file(path):
    with open(path, "r", encoding="utf-8") as f:
        try:
            tree = ast.parse(f.read(), filename=path)
        except SyntaxError:
            return

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            definitions[node.name].append((path, node.lineno))
            self.generic_visit(node)

        def visit_Call(self, node):
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr

            if name:
                calls[name].append((path, node.lineno))
            self.generic_visit(node)

        def visit_Import(self, node):
            for n in node.names:
                imports[path].append((n.name, node.lineno))
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            module = node.module
            for n in node.names:
                imports[path].append((f"{module}.{n.name}", node.lineno))
            self.generic_visit(node)

    Visitor().visit(tree)


def main():
    for root, dirs, files in os.walk(ROOT):
        for fn in files:
            if fn.endswith(".py"):
                full = os.path.join(root, fn)
                analyze_file(full)
                files_seen.add(full)
                duplicates[fn].append(full)

    print("\n=== DUPLICATE FILES ===")
    for fn, paths in duplicates.items():
        if len(paths) > 1:
            print(f"\nDuplicate: {fn}")
            for p in paths:
                print(f"  {p}")

    print("\n=== TARGET CALLS ===")
    for t in TARGETS:
        if t in calls:
            print(f"\n{t} is CALLED at:")
            for path, line in calls[t]:
                print(f"  {path}:{line}")
        else:
            print(f"\n{t} is NOT called anywhere.")

    print("\n=== UNUSED FUNCTIONS ===")
    for func, defs in definitions.items():
        if func not in calls:
            for path, line in defs:
                print(f"  {func} defined at {path}:{line} but never called")

    print("\n=== IMPORTS ===")
    for path, imps in imports.items():
        print(f"\nFile: {path}")
        for mod, line in imps:
            print(f"  imports {mod} at line {line}")

    print("\n=== CALL GRAPH ===")
    for func, locs in calls.items():
        print(f"\nFunction: {func}")
        for path, line in locs:
            print(f"  called at {path}:{line}")

if __name__ == "__main__":
    main()
