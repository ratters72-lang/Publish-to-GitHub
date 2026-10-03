with open("world/open_play_engine.py", encoding="utf-8") as f:
    lines = f.readlines()
for i in range(2925, 3005):
    print(f"{i:5d}  {lines[i-1].rstrip()}")
