with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if 'def _update_tackle_drive' in line:
        # Print first 15 lines of the method
        for j in range(i, i + 20):
            print(f'{j}: {lines[j].rstrip()}')
        break
