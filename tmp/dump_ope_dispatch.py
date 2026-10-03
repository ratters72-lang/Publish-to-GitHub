import io, sys
src = io.open('world/open_play_engine.py', encoding='utf-8', errors='replace')
lines = src.readlines()
src.close()
start, end = 1230, 1360
out = io.open('tmp/ope_dispatch.txt', 'w', encoding='utf-8')
for i in range(start-1, min(end, len(lines))):
    out.write(f"{i+1}: {lines[i]}")
out.close()
print(f"dumped {min(end,len(lines))-start+1} lines ({start}..{min(end,len(lines))})")
