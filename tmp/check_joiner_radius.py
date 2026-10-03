with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

if 'JOINER_RADIUS_M' in content:
    print("JOINER_RADIUS_M found in file")
    idx = content.index('JOINER_RADIUS_M')
    start = max(0, idx - 40)
    end = min(len(content), idx + 80)
    print(content[start:end])
else:
    print("JOINER_RADIUS_M NOT found - needs to be added")
