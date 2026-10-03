with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# The duplicate if at 2373-2374 makes the code: if X: if X: print(...)
# Delete line 2373 (the first if, which has no body, and the second if acts as its body)
# Actually simpler: just delete one of the if lines

# Line 2373 has the first if, 2374 has the second if  
# After deletion, 2374 becomes the new 2373, and 2375 becomes 2374 (the print)
# We want:
#   if bc_momentum > def_resistance * 1.4 or bc_speed > 7.0:
#       print(...)

# Just delete line 2373 (the first "if"), keeping line 2374 (second if, but now it's the only if)
# Actually the cleanest is to replace lines 2373-2374 with a single if
lines[2373] = '        if bc_momentum > def_resistance * 1.4 or bc_speed > 7.0:\n'
# Now delete line 2374 (the duplicate)
del lines[2374]

with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

import ast
try:
    ast.parse(''.join(lines))
    print("SYNTAX OK!")
except SyntaxError as e:
    print(f"Error at line {e.lineno}: {e.msg}")
