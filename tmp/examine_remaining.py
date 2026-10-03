with open('world/open_play_engine.py','r',encoding='utf-8') as f:
    lines=f.readlines()

total = len(lines)

# Find the enclosing function for each target line
def find_function(line_num):
    for i in range(line_num-1, max(0,line_num-40), -1):
        if lines[i].strip().startswith('def '):
            return lines[i].strip()
    return "???"

targets = [794, 2253, 2335, 2393]
for ln in targets:
    fn_name = find_function(ln)
    print(f"Line {ln}: {fn_name}")
    print(f"  Content: {lines[ln-1].rstrip()}")
    print()

# Also find the release _d.update lines
print("=== _d.update(dt) lines ===")
for i,l in enumerate(lines):
    if '_d.update(dt)' in l:
        print(f"Line {i+1}: {l.rstrip()}")
