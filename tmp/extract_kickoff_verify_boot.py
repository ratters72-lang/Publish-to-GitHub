import re, io
src = open("verify_kickoff_fullback.py", encoding="utf-8").read()
# Print any function that mentions kickoff_engine / _force_open_play / while True
lines = src.splitlines()
out = []
capture = False
depth = 0
for i, ln in enumerate(lines, start=1):
    if re.match(r"\s*def\s+(boot_engine|_force_open_play|main|run)\b", ln) or "kickoff_engine" in ln or "while" in ln or "_force_open_play" in ln:
        out.append(f"{i}: {ln}")
print("\n".join(out))
