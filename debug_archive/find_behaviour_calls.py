import os

# Check for calls to DefensiveBehaviour.apply (L1) vs DefensiveBehaviour.apply (L2)
# and any direct target_x assignment in old code paths
patterns = [
    'DefensiveBehaviour(',
    'def_behaviour.apply',
    'l1_def_behaviour',
]

for root, dirs, files in os.walk('.'):
    if 'tmp' in root or '.git' in root or '__pycache__' in root:
        continue
    for f in files:
        if not f.endswith('.py'):
            continue
        path = os.path.join(root, f)
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
                for i, line in enumerate(fh, 1):
                    for p in patterns:
                        if p in line:
                            print(f'{path}:{i}: {line.rstrip()}')
        except Exception:
            pass
