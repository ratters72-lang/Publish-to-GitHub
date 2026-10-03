with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i in range(1110, min(len(lines), 1130)):
    print(f"{i+1}: {repr(lines[i][:120])}")
