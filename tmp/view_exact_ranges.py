src = open("world/open_play_engine.py", "r", encoding="utf-8", errors="replace").read().splitlines()
ranges = [(2030, 2095), (3090, 3125)]
for lo, hi in ranges:
    print(f"\n{'='*70}\n### lines {lo}-{hi} ###\n{'='*70}")
    for i in range(lo - 1, min(hi, len(src))):
        print(f"{i+1}: {src[i]}")
