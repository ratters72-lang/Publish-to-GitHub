import io
src = io.open('world/open_play_engine.py', encoding='utf-8', errors='replace')
lines = src.readlines()
src.close()
out = io.open('tmp/ope_flip.txt', 'w', encoding='utf-8')
for i in range(4405, min(4445, len(lines))):
    out.write(f"{i+1}: {lines[i]}")
out.close()
print("done")
