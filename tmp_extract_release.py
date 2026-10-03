"""tmp_extract_release.py — print the RELEASE/PTB exit contract from the engine.

Slices the named methods out of world/open_play_engine.py verbatim so the
stall's exit condition can be read rather than guessed, and prints the field
bounds the carrier's clamp uses.
"""

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ENGINE = "world/open_play_engine.py"
OUT = "tmp_release_extract.txt"

with open(ENGINE, encoding="utf-8", errors="replace") as fh:
    src = fh.read()

WANTED = [
    "def _update_release",
    "def _prepare_release",
    "def _check_dh_ready_for_ptb",
    "def _get_markers",
    "def _get_marker_target",
    "def _set_phase",
]

lines = []
lines.append("ENGINE FILE: %s  (total lines %d)" % (ENGINE, src.count("\n") + 1))

# The module-level field-bound / constant block, for the clamp owner.
lines.append("")
lines.append("=" * 90)
lines.append("MODULE-LEVEL CONSTANTS (first 120 lines)")
lines.append("=" * 90)
sl = src.splitlines()
for ln in sl[:120]:
    lines.append(ln)

# Each wanted method body, up to the next 4-space def/decorator.
for name in WANTED:
    idx = src.find(name)
    lines.append("")
    lines.append("=" * 90)
    lines.append("METHOD: %s   (found at char %d)" % (name, idx))
    lines.append("=" * 90)
    if idx == -1:
        lines.append("  NOT FOUND")
        continue
    rest = src[idx:]
    end = len(rest)
    for marker in ("\n    def ", "\n    @", "\nclass "):
        j = rest.find(marker, 1)
        if j != -1 and j < end:
            end = j
    lines.append(rest[:end])

# The field bounds the carrier's clamp uses.
lines.append("")
lines.append("=" * 90)
lines.append("FIELD BOUNDS (player/player.py)")
lines.append("=" * 90)
with open("player/player.py", encoding="utf-8", errors="replace") as fh:
    pl = fh.read()
for ln in pl.splitlines():
    if "FIELD_MIN_X" in ln or "FIELD_MAX_X" in ln or "FIELD_MIN_Y" in ln or "FIELD_MAX_Y" in ln:
        lines.append(ln)

# enforce_bc_backward_invariant, the RELEASE velocity clamp.
lines.append("")
lines.append("=" * 90)
lines.append("BC BACKWARD INVARIANT (world/rules/movement_contracts.py)")
lines.append("=" * 90)
with open("world/rules/movement_contracts.py", encoding="utf-8", errors="replace") as fh:
    mc = fh.read()
i = mc.find("def enforce_bc_backward_invariant")
if i != -1:
    rest = mc[i:]
    j = rest.find("\n# ", 1)
    k = rest.find("\ndef ", 1)
    end = min(x for x in (j, k, len(rest)) if x > 0)
    lines.append(rest[:end])
else:
    lines.append("  NOT FOUND")

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")

print("WROTE %s (%d lines)" % (OUT, len(lines)))
