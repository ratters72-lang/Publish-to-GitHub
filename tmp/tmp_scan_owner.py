"""Temp: locate the TES recovery-ownership implementation (ASCII-safe)."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = [
    os.path.join(ROOT, "game", "defence", "defence_lvl_1"),
    os.path.join(ROOT, "game", "managers"),
    os.path.join(ROOT, "world"),
]
NEEDLES = re.compile(
    r"tes_slot_ownership_enabled|slot_ownership|SLOT_OWN|ownership|"
    r"ladder|census|recovery_target|SLOT_SIDE|slot_side",
    re.IGNORECASE,
)

out_lines = []
for base in DIRS:
    if not os.path.isdir(base):
        continue
    for name in sorted(os.listdir(base)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(base, name)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        for i, line in enumerate(lines):
            if NEEDLES.search(line):
                rel = os.path.relpath(path, ROOT)
                text = f"{rel}:{i + 1}: {line.rstrip()}"
                out_lines.append(text.encode("ascii", "replace").decode("ascii"))

out_lines.append(f"-- {len(out_lines)} hit(s)")

dest = os.path.join(ROOT, "tmp", "owner_scan.txt")
with open(dest, "w", encoding="ascii", errors="replace") as fh:
    fh.write("\n".join(out_lines) + "\n")
print(f"wrote {dest} ({len(out_lines)} lines)")
