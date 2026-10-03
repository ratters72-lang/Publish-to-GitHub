with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Show context
for i in range(2370, min(len(lines), 2385)):
    print(f"{i+1}: {repr(lines[i][:120])}")

# The issue: line 2373 has the if, line 2374 has the same if (duplicate from the fix script)
# The original print was on 2374 with unclosed paren
# Fix: delete line 2373 (the first if), keep line 2374 (the fixed print)
# Actually let me just rewrite lines 2373-2374 properly

# Remove the duplicate if at line 2373
# Line 2373 is the "if" from the original 
# Line 2374 is the "if" from the fix script (which should be the print)
# We need to keep line 2373 (the if), and line 2374 should be the print (indented)

# Actually let's just check what happened:
# Original: 2373='if bc_momentum...', 2374='            print(f"[...] > "\n'
# My fix wrote: lines[2373]='if ...' (same), lines[2374]='            print(f"[...] BREAK!")\n'
# But the file shows line 2374='if bc_momentum...' - that means the script ran, but maybe wrote 
# the wrong thing. Let me just delete line 2374 (the duplicate if) and make 2375 the print.

# Delete the duplicate if at line 2374
del lines[2373]  # This is the duplicate "if" - keep the first one at 2373

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
