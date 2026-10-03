"""f3.py - removes CATCH-time band formation + legacy target writes."""
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

NL = chr(10)
P = "world/catch_system.py"
s = open(P, encoding="utf-8", errors="replace").read()

# 1. Drop the now-unused defence-module imports.
drop_prefixes = (
    "from game.defence.defence_lvl_1.defensive_width import",
    "from game.defence.defence_lvl_1.defensive_pipeline import",
)
lines = s.splitlines()
kept = []
for ln in lines:
    st = ln.strip()
    if any(st.startswith(pfx) for pfx in drop_prefixes):
        continue
    kept.append(ln)
s = NL.join(kept)

# 2. Drop the governed-width line (its consumer is removed below).
lines = s.splitlines()
kept = []
for ln in lines:
    if ln.strip().startswith("_line_width = governed_width_m("):
        continue
    kept.append(ln)
s = NL.join(kept)

# 3. Replace the force-reseed + per-defender target-write block with a
#    momentum-only stamp (NO band formation, NO target writes on CATCH).
start_marker = "force_reseed_depth_roles(world, sorted_defs)"
end_marker = "KICKOFF_CATCH"
i = s.find(start_marker)
if i == -1:
    print("catch start marker not found")
    sys.exit(1)
j = s.find(end_marker, i)
if j == -1:
    print("catch end marker not found")
    sys.exit(1)
line_start = s.rfind(NL, 0, j) + 1

new_block_lines = [
    "            # STAGE 3B: NO band formation and NO target writes on CATCH.",
    "            # Band formation (A/B/C ruck-pivot) fires ONLY when TES",
    "            # enters TACKLE_INIT; defender positioning is owned by the",
    "            # DefenceManager pipeline.  Keep pre-catch momentum only.",
    "            for d in sorted_defs:",
    "                if getattr(d, 'fullback_role_active', False):",
    "                    continue",
    "                d.move_speed = d.max_speed_mps",
    "",
]
new_block = NL.join(new_block_lines) + NL

s2 = s[:i] + new_block + s[line_start:]

open(P, "w", encoding="utf-8").write(s2)
print("catch_system cleaned OK")
