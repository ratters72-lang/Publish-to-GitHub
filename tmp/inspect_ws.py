with open("world/open_play_engine.py", encoding="utf-8") as f:
    lines = f.readlines()

for i in range(2918, 2962):
    s = lines[i - 1]
    nonws = s.lstrip()
    n = len(s) - len(nonws)
    print(f"{i:5d} [{n:2d}] {nonws.rstrip()}")
