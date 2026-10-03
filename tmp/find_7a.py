"""find_7a.py - locate the step-7a engaged-non-lane chase writer in DBU."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

s = open("game/defence/defence_lvl_1/defensive_behaviour_unified.py",
         encoding="utf-8", errors="replace").read()

pats = ["7a", "non-lane", "non_lane", "ENGAGED-NON", "engaged", "CHASE WRITER",
        "_is_carrier_past_anchor"]
seen = set()
for pat in pats:
    for m in re.finditer(re.escape(pat), s):
        ln = s[:m.start()].count(chr(10)) + 1
        if ln not in seen and ln > 1200:   # skip the import/header mentions
            seen.add(ln)
            print(f"--- {pat} @ line {ln}")
            print(s[m.start() - 100:m.start() + 400].replace("\r", ""))
            print()
