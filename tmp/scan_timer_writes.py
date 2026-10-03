import os, re

NL = chr(10)
out = []

roots = [
    r"c:/Users/User/Desktop/RLM/world",
    r"c:/Users/User/Desktop/RLM/game",
    r"c:/Users/User/Desktop/RLM/player",
]

pats = [
    ("_phase_timer write", re.compile(r"_phase_timer\s*=[^=]")),
    ("drive_elapsed_s write", re.compile(r"drive_elapsed_s\s*=[^=]")),
    ("_set_phase TACKLE_DRIVE", re.compile(r"_set_phase\(.{0,3}TACKLE_DRIVE")),
    ("tes_active write", re.compile(r"\.tes_active\s*=")),
    ("tes_ctx None", re.compile(r"tes_ctx\s*=\s*None")),
]

for root in roots:
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            for i, ln in enumerate(lines, 1):
                for label, pat in pats:
                    if pat.search(ln):
                        out.append("%s:%d [%s] %s" % (os.path.relpath(p, r"c:/Users/User/Desktop/RLM"), i, label, ln.strip()))

dest = r"c:/Users/User/Desktop/RLM/timer_writes_scan.txt"
with open(dest, "w", encoding="utf-8") as f:
    f.write(NL.join(out))
print("OK wrote", dest, len(out), "hits")
