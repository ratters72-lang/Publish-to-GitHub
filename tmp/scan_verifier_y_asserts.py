"""Scan verifiers for Y-position assertions against the old percentage/spacing formula."""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

PATTERNS = [
    (r"ruck_y \s*[+-] \s*side \s*\*\s*offset", "old single-offset Y formula"),
    (r"role_width_offset\(", "role_width_offset in Y context"),
    (r"0\.12|\b0\.34\b|\b0\.56\b|\b0\.72\b", "legacy percentage fractions"),
]

hits = []
for root, dirs, files in os.walk("."):
    if "__pycache__" in root:
        continue
    for fn in files:
        if not (fn.startswith("verify_") or fn.startswith("audit_")):
            continue
        if not fn.endswith(".py"):
            continue
        path = os.path.join(root, fn)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue
        for lineno, line in enumerate(src.splitlines(), 1):
            for pat, label in PATTERNS:
                if re.search(pat, line):
                    hits.append((path, lineno, label, line.strip()[:120]))

for path, lineno, label, text in hits:
    print(f"{path}:{lineno} [{label}] {text}")
print(f"TOTAL HITS: {len(hits)}")
