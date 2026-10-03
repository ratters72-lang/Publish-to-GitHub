"""Check the update() method's pipeline order."""
lines = open("world/open_play_engine.py", encoding="utf-8").readlines()

# Find update()
update_start = None
for i, l in enumerate(lines):
    if l.strip() == "def update(self, dt: float) -> None:":
        update_start = i
        break

# Find _set_phase (next method after update)
set_phase_start = None
for i, l in enumerate(lines[update_start+1:], update_start+1):
    if l.strip().startswith("def _set_phase"):
        set_phase_start = i
        break

print(f"update(): line {update_start+1}")
print(f"_set_phase(): line {set_phase_start+1}")
print(f"update() body: {set_phase_start - update_start} lines")

# Show the critical pipeline section (last ~60 lines of update())
start = max(update_start, set_phase_start - 55)
for i in range(start, set_phase_start):
    print(f"  {i+1:4d}: {lines[i].rstrip()}")
