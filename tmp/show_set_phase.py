path = "world/open_play_engine.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    s = line.strip()
    if s.startswith("def _set_phase") or s.startswith("def _phase_timer"):
        print(f"FOUND {i}: {line.rstrip()}")

# show _set_phase body
for i, line in enumerate(lines, 1):
    if line.strip().startswith("def _set_phase"):
        start = i
        for j in range(start, min(start + 40, len(lines) + 1)):
            print(f"{j:5d}  {lines[j-1].rstrip()}")
        break
