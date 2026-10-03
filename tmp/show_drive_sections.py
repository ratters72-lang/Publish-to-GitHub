path = "world/open_play_engine.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()

def show(start, end, label):
    print(f"\n===== {label} (lines {start}-{end}) =====")
    for i in range(start, end + 1):
        print(f"{i:5d}  {lines[i-1].rstrip()}")

# Contact tackle state machine region
show(1830, 1920, "TACKLE STATE MACHINE / tackle_result")
# dominance_result None region
show(2540, 2625, "PRE-CONTACT dominance_result")
# Contact handler
show(2631, 2712, "_update_tackle_contact")
# Drive handler
show(2742, 3030, "_update_tackle_drive")
