import os

patterns = [
    'marker_controller.update',
    'marker_controller.hold_positions',
    'marker_controller.release',
    'marker_controller.assign(',
    'MarkerController().update',
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
