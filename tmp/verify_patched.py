import py_compile
try:
    py_compile.compile('world/open_play_engine.py', doraise=True)
    print("SYNTAX OK: world/open_play_engine.py compiles without errors")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
