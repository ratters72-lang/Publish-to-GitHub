"""pb.py - stamp ruck_established=True into post-pivot verifier fixtures.

Stage 3B: band formation now fires only after TES.TACKLE_INIT
(world.ruck_established).  Verifiers that model a post-pivot band /
role-assignment scenario must stamp the flag on their WorldState fixture.
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

TARGETS = {
    "verify_one_a_per_side.py": " = WorldState()",
    "verify_tackle_state_gate.py": " = WorldState()",
    "verify_defensive_pipeline.py": " = WorldState()",
    "verify_preamble_reset_audit.py": " = WorldState()",
    "verify_tes_drive_depth_shadow.py": " = WorldState()",
    "verify_fb_determinism.py": " = WorldState()",
}

for path, needle in TARGETS.items():
    s = open(path, encoding="utf-8", errors="replace").read()
    # Match the FIRST assignment line of WorldState().
    m = re.search(r"^(\s*)(\w+)\s*=\s*WorldState\(\)\s*$", s, re.M)
    if not m:
        print("MISS", path)
        continue
    indent, var = m.group(1), m.group(2)
    add = (
        f"{indent}{var}.ruck_established = True"
        f"  # STAGE 3B: post-TACKLE_INIT band/role scenario\n"
    )
    s2 = s[: m.end()] + "\n" + add + s[m.end():]
    open(path, "w", encoding="utf-8").write(s2)
    print("OK", path)
