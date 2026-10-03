"""find_mmdl_writer.py - locate every real mmdl_* target_source write."""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

roots = ["game", "player", "world"]
pat = re.compile(r'mmdl_|target_source\s*=\s*f?"')
for root in roots:
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                s = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for m in re.finditer(r'.*mmdl_[A-Z_]+.*', s):
                ln = s[:m.start()].count(chr(10)) + 1
                line = m.group(0).strip()
                if "#" == line[0]:
                    continue  # skip pure comment lines
                print(f"{p}:{ln}: {line[:120]}")
