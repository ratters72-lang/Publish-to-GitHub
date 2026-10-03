import py_compile
import sys

files = [
    'world/rules/release_controller.py',
    'world/open_play_engine.py',
]
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f'{f}: OK')
    except py_compile.PyCompileError as e:
        print(f'{f}: FAIL - {e}')
        sys.exit(1)
