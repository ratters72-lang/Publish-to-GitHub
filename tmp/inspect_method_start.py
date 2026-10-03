with open("world/open_play_engine.py", encoding="utf-8") as f:
    lines = f.readlines()

for i in range(2750, 2810):
    s = lines[i - 1]
    nonws = s.lstrip()
    n = len(s) - len(nonws)
    if nonws.strip():
        print(f"{i:5d} [{n:2d}] {nonws.rstrip()}")
