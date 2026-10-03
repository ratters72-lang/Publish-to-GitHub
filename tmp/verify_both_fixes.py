"""Verify both fixes applied and syntax is valid."""
with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Check fix 1: tes_defenders.add(j) in _start_tackle
if 'self.tes_defenders.add(j)  # TES participant lifecycle:' in content:
    print("FIX 1 PASS: tes_defenders.add(j) in _start_tackle")
else:
    print("FIX 1 FAIL: missing tes_defenders.add(j)")

# Check fix 2: TACKLER role clearing in _clear_tes_artefacts
if 'DefenderRole.TACKLER_1' in content and 'p.defender_role = DefenderRole.DEF_RETREAT_10' in content:
    print("FIX 2 PASS: TACKLER role clearing in _clear_tes_artefacts")
else:
    print("FIX 2 FAIL: missing TACKLER role clearing")

# Count tes_defenders.add calls (should be 3)
count = content.count('self.tes_defenders.add(')
print(f"tes_defenders.add calls: {count} (expected 3+)")

# Syntax check
try:
    compile(content, 'world/open_play_engine.py', 'exec')
    print("SYNTAX: PASS")
except SyntaxError as e:
    print(f"SYNTAX FAIL: line {e.lineno}: {e.msg}")
