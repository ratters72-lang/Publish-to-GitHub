with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Lines 2373-2374 have stale print that's incomplete
# Show more to understand the full method
for i in range(2373, min(len(lines), 2395)):
    print(f"{i+1}: {repr(lines[i][:120])}")

# The print on 2374 is broken - the parenthesized f-string is never closed
# Fix: either complete the print or remove it
# Let's replace lines 2373-2374 with a working version
lines[2373] = '        if bc_momentum > def_resistance * 1.4 or bc_speed > 7.0:\n'
lines[2374] = '            print(f"[BREAK_TACKLE] BC #{bc.player_id} momentum={bc_momentum:.1f} > def_resistance={def_resistance:.1f} x 1.4 -- BREAK!")\n'

with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

import ast
try:
    ast.parse(''.join(lines))
    print("\nSYNTAX OK!")
except SyntaxError as e:
    print(f"\nStill error at line {e.lineno}: {e.msg}")
    for i in range(max(0, e.lineno-3), min(len(lines), e.lineno+3)):
        print(f"  {i+1}: {repr(lines[i][:100])}")
