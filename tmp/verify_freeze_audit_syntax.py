"""Verify freeze_audit.py syntax."""
import py_compile
try:
    py_compile.compile('game/debug/freeze_audit.py', doraise=True)
    print("SYNTAX: PASS")
except py_compile.PyCompileError as e:
    print(f"SYNTAX FAIL: {e}")
