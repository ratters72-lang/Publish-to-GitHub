import os

terms = ["tes_attached", "attach_slot", "total_tackler", "drive_strength",
         "dominance_result", "attach_radius", "attach_offsets", "ATTACH_RADIUS"]
roots = ["game", "world", "player"]
for root in roots:
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    low = line.lower()
                    for t in terms:
                        if t.lower() in low:
                            print(f"{path}:{i}: {line.rstrip()}")
                            break
