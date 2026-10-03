with open('world/open_play_engine.py','r',encoding='utf-8') as f:
    content = f.read()

# Find ALL apply_vector calls
import re
for m in re.finditer(r'apply_vector\((\w+),', content):
    line = content[:m.start()].count('\n') + 1
    context_start = max(0, content[:m.start()].rfind('\n', 0, m.start()-500))
    context = content[context_start:m.start()+100]
    func_name = "???"
    # find which function we're in
    for func_match in re.finditer(r'def _(\w+)', context):
        func_name = func_match.group(1)
    print(f"apply_vector at line {line} (near function _{func_name})")
    print(f"  Context: {m.group()}")
