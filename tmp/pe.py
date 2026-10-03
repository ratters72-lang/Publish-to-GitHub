"""pe.py - dump step-9 loop body + all ruck+10/compliance X writes in DBU."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

s = open("game/defence/defence_lvl_1/defensive_behaviour_unified.py",
         encoding="utf-8", errors="replace").read()

i = s.find("# 9. MANDATORY RETREAT")
j = s.find("# 10. STRUCTURED OPEN PLAY: SAFE CLOSING")
print("========== STEP-9 FULL BODY ==========")
print(s[i:j])
print()
print("========== retreat_to_lane / compliance writes anywhere ==========")
for pat in ("retreat_to_lane", "compliance", "ruck_x_m") :
    print(f"--- {pat} ---")
    for m in re.finditer(pat, s):
        st = s.rfind(chr(10), 0, m.start()) + 1
        en = s.find(chr(10), m.end())
        print(f"{m.start():8d}: {s[st:en].strip()[:110]}")
