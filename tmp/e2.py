"""e2.py - splices the DBU TES-support retreat branch to the RetreatManager."""
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

P = "game/defence/defence_lvl_1/defensive_behaviour_unified.py"
s = open(P, encoding="utf-8", errors="replace").read()

start_marker = "retreat_x = _anchor_m(world)"
end_marker = "d.frozen = False"

i = s.find(start_marker)
if i == -1:
    print("TES start marker not found")
    sys.exit(1)
j = s.find(end_marker, i)
if j == -1:
    print("TES end marker not found")
    sys.exit(1)
j = j + len(end_marker)  # include the assignment

new_body = (
    "                    # TES SUPPORT ROLE: STAGE 3B delegated to the\n"
    "                    # RetreatManager (sole retreat authority).\n"
    "                    apply_tes_support_retreat(\n"
    "                        world, d, self.tuners,\n"
    "                        governor=self.line_governor,\n"
    "                    )"
)

s2 = s[:i] + new_body + s[j:]

open(P, "w", encoding="utf-8").write(s2)
print("DBU TES-support retreat replaced OK")
