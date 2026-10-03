with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Show lines 1286-1300
for i in range(1286, min(len(lines), 1300)):
    print(f"{i+1}: {repr(lines[i][:120])}")
