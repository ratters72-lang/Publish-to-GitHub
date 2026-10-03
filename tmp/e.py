"""e.py - splices the DBU unified-mode retreat block into a manager delegation."""
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

P = "game/defence/defence_lvl_1/defensive_behaviour_unified.py"
s = open(P, encoding="utf-8", errors="replace").read()

start_marker = "# RETREAT COMPLIANCE - runs AFTER classification"
end_marker = "# Now process only compliant defenders"
i = s.find(start_marker)
j = s.find(end_marker, i)
if i == -1 or j == -1 or j <= i:
    print("MARKERS NOT FOUND i=%d j=%d" % (i, j))
    sys.exit(1)

new_block = (
    "# -----------------------------------------------------------\n"
    "# RETREAT COMPLIANCE - runs AFTER classification\n"
    "# STAGE 3B: delegated to the DefenceManager-owned RetreatManager\n"
    "# (game/managers/retreat_manager.py).  Sole forced-retreat writer:\n"
    "#   X <- anchor authority, Y <- spacing authority (frame dict),\n"
    "#   TES freeze contracts enforced (frozen players never move).\n"
    "# -----------------------------------------------------------\n"
    "        compliant, _non_compliant = apply_forced_retreat(\n"
    "            world, line_defenders, self.tuners,\n"
    "            governor=self.line_governor,\n"
    "        )\n"
    "\n"
)

s2 = s[:i] + new_block + s[j:]

open(P, "w", encoding="utf-8").write(s2)
print("DBU unified retreat replaced OK")
