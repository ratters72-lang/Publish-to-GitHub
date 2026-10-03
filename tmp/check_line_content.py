with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i in range(1020, min(len(lines), 1050)):
    text = lines[i].rstrip()
    if text:
        print(f'{i+1}: {text}')
