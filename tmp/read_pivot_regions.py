import sys
out = open("tmp/pivot_dump.txt", "w", encoding="utf-8")
src = open("world/open_play_engine.py", encoding="utf-8").read().splitlines()
for i in range(3020, min(3140, len(src))):
    out.write(f"{i+1}: {src[i]}\n")
out.close()
