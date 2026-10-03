import os
for root, dirs, files in os.walk('.'):
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            try:
                with open(path) as fh:
                    content = fh.read()
                    if 'RUCK_FORMING' in content:
                        print(f'{path}: {content.count("RUCK_FORMING")}')
            except:
                pass
