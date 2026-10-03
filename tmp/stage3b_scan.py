"""stage3b_scan.py — locate ruck-pivot, CATCH-triggered band formation,
retreat/spacing fallback writers, and TES freeze contract sites."""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

ROOTS = ["game", "world", "player", "kick_off"]

PATTERNS = {
    "PIVOT": re.compile(r"pivot", re.IGNORECASE),
    "TACKLE_INIT": re.compile(r"TACKLE_INIT"),
    "CATCH_BAND": re.compile(r"(CATCH|ball_caught|catch).*(band|depth_role|assign_depth|A-band|BAND)", re.IGNORECASE | re.DOTALL),
    "FREEZE": re.compile(r"freeze\(|\.frozen\b|freeze_timer|tes.*freeze|freeze.*tes", re.IGNORECASE),
    "RETREAT_WIDTH": re.compile(r"retreat.*(width|spacing)|width.*retreat|spacing.*retreat", re.IGNORECASE),
    "SPACING_FALLBACK": re.compile(r"frame_spacing_targets\.get\(id\(d\),\s*d\.y\)|spacing_targets\.get\(id\(d\),\s*d\.y\)"),
    "WIDTH_FALLBACK": re.compile(r"width_targets\.get\(id\(d\),|lane_x_of"),
    "RUCK_X_WRITE": re.compile(r"\.ruck_x_m\s*="),
}

for root in ROOTS:
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
            except OSError:
                continue
            src = "".join(lines)
            for name, pat in PATTERNS.items():
                for i, ln in enumerate(lines, 1):
                    if pat.search(ln):
                        print(f"[{name}] {path}:{i}: {ln.strip()[:120]}")
print("SCAN DONE")
