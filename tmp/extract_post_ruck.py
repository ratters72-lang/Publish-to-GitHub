with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()
lines = content.split('\n')
total = len(lines)
print(f'Total lines: {total}')
print(f'Last line: {total}')
for i in range(2310, min(2370, total)):
    print(f'{i+1}: {lines[i]}')
