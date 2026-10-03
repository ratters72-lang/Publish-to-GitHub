"""pd.py - dump DBU lane_x_of call sites + step-9 retreat region."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

s = open("game/defence/defence_lvl_1/defensive_behaviour_unified.py",
         encoding="utf-8", errors="replace").read()
for m in re.finditer(r"lane_x_of\(", s):
    st = max(0, m.start() - 180)
    print("--- at", m.start(), "---")
    print(s[st:m.start() + 400])
    print()

# Step-9 block
i = s.find("# 9. MANDATORY RETREAT")
j = s.find("# 10. STRUCTURED OPEN PLAY: SAFE CLOSING")
print("========== STEP-9 REGION ==========")
print(s[i:i + 1800])
