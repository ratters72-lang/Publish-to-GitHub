import os
results = []
for root, dirs, files in os.walk('.'):
    for f in files:
        if f.endswith('.py') and 'node_modules' not in root and '__pycache__' not in root:
            path = os.path.join(root, f)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
                    lines = fh.readlines()
                for i, line in enumerate(lines, 1):
                    if '_update_defence' in line:
                        results.append(f'{path}:{i}: {line.rstrip()}')
            except:
                pass
for r in results:
    print(r)
