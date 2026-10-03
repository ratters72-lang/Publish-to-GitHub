import io, sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
src = open("game/defence/defence_lvl_1/defensive_behaviour_unified.py", encoding="utf-8").read().splitlines()
def dump(a, b, label):
    print(f"=== {label} (lines {a}-{b}) ===")
    for i in range(a-1, min(b, len(src))):
        print(f"{i+1}: {src[i]}")
    print()
dump(1640, 1770, "FIRST REBAND SITE (unified path) / select_tacklers")
dump(3000, 3130, "SECOND REBAND SITE (structured path) / select_tacklers")
