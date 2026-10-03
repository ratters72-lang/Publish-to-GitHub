"""tmp_check_consts.py — the numbers the RELEASE deadlock turns on.

Prints only the constants and the invariant bodies, so the claims
"the recovery target lands ahead of a carrier pinned at the field edge"
and "forward velocity is then zeroed" are read from source, not inferred.
"""

import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT = "tmp_consts.txt"
out = []


def emit(s):
    out.append(str(s))


def slice_method(text, marker, next_markers=("\n    def ", "\ndef ", "\n# "),
                 cap=4000):
    i = text.find(marker)
    if i == -1:
        return "  NOT FOUND: %s" % marker
    rest = text[i:]
    end = min(len(rest), cap)
    for nm in next_markers:
        j = rest.find(nm, 1)
        if j != -1 and j < end:
            end = j
    return rest[:end]


# ── 1. Field bounds + the clamp (player/player.py) ──
emit("=" * 88)
emit("FIELD BOUNDS  (player/player.py)")
emit("=" * 88)
with open("player/player.py", encoding="utf-8", errors="replace") as fh:
    pl = fh.read()
for m in re.finditer(r"^\s*(FIELD_(?:MIN|MAX)_[XY])\s*=\s*([^\n#]+)", pl, re.M):
    emit("  %-16s = %s" % (m.group(1), m.group(2).strip()))
for m in re.finditer(r"^\s*(PITCH_(?:MIN|MAX)_[XY])\s*=\s*([^\n#]+)", pl, re.M):
    emit("  %-16s = %s" % (m.group(1), m.group(2).strip()))
emit("")
emit("--- _clamp_position ---")
emit(slice_method(pl, "def _clamp_position"))

# ── 2. RELEASE constants (world/open_play_engine.py) ──
emit("")
emit("=" * 88)
emit("RELEASE CONSTANTS  (world/open_play_engine.py)")
emit("=" * 88)
with open("world/open_play_engine.py", encoding="utf-8", errors="replace") as fh:
    en = fh.read()
for name in ("RECOVERY_BACKWARD_DISTANCE_M", "RELEASE_RECOVERY_SPEED_MPS",
             "RELEASE_WATCHDOG_FRAMES", "RELEASE_MIN_CONVERGENCE_S"):
    hit = False
    for m in re.finditer(r"^%s\s*=\s*([^\n#]+)" % name, en, re.M):
        emit("  %-32s = %s" % (name, m.group(1).strip()))
        hit = True
    if not hit:
        emit("  %-32s = (not a module constant here)" % name)

# ── 3. The release gate ──
emit("")
emit("=" * 88)
emit("RELEASE GATE  (world/rules/release_gate.py)")
emit("=" * 88)
with open("world/rules/release_gate.py", encoding="utf-8", errors="replace") as fh:
    rg = fh.read()
for m in re.finditer(r"^([A-Z][A-Z0-9_]+)\s*=\s*([^\n#]+)", rg, re.M):
    emit("  %-34s = %s" % (m.group(1), m.group(2).strip()))
emit("")
emit("--- release_convergence ---")
emit(slice_method(rg, "def release_convergence",
                  next_markers=("\ndef ",), cap=5000))

# ── 4. The backward invariant ──
emit("")
emit("=" * 88)
emit("BC BACKWARD INVARIANT  (world/rules/movement_contracts.py)")
emit("=" * 88)
with open("world/rules/movement_contracts.py", encoding="utf-8", errors="replace") as fh:
    mc = fh.read()
emit(slice_method(mc, "def enforce_bc_backward_invariant",
                  next_markers=("\ndef ", "\n@"), cap=2500))

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("WROTE %s (%d lines)" % (OUT, len(out)))
