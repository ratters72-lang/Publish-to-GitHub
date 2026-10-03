Copilot: Enable Workspace Indexingimport re, os

pattern = re.compile(r"spacing_y|frame_spacing_y|spacing_target_y")
for root, _, fs in os.walk("game"):
    for f in fs:
        if not f.endswith(".py"):
            continue
        p = os.path.join(root, f)
        for i, l in enumerate(open(p, "r", encoding="utf-8", errors="ignore"), 1):
            if pattern.search(l):
                print(f"{p}:{i}:{l.rstrip()}")
