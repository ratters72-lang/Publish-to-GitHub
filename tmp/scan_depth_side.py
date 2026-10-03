import io, os, re, glob

patterns = [
    r'\.depth_side\s*=[^=\n]',      # assignment (avoid ==)
    r'depth_side\s*=',              # any depth_side assignment
    r'\.depth_side\b',
]

def scan(path):
    hits = []
    for root, dirs, files in os.walk(path):
        for f in files:
            if f.endswith('.py'):
                p = os.path.join(root, f)
                try:
                    with io.open(p, encoding='utf-8', errors='replace') as fh:
                        for i, line in enumerate(fh, 1):
                            if re.search(r'\.depth_side\s*=', line) and not re.search(r'==|!=|<=|>=', line):
                                hits.append((p, i, line.rstrip()))
                except Exception:
                    pass
    return hits

for p in ['game/defence', 'game/managers', 'world']:
    for (path, ln, text) in scan(p):
        print(f"{path}:{ln}: {text.strip()}")
