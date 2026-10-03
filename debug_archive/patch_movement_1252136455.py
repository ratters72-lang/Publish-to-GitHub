"""Apply two targeted edits to world/open_play_engine.py."""

path = "world/open_play_engine.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# --- Edit 1: Add MovementSystem.update() method ---
# Find the exact text around the KICK CHASE / TACKLE INITIATOR boundary
marker1_start = '            p.state = PlayerState.CHASE_BALL\n\n'
marker1_end = '\n# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\n# TACKLE INITIATOR'

# Build edit 1: insert a new section between the KICK CHASE section and TACKLE INITIATOR
new_method = (
    '            p.state = PlayerState.CHASE_BALL\n'
    '\n'
    '    # \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\n'
    '    # MAIN UPDATE ENTRY POINT\n'
    '    # \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\n'
    '\n'
    '    def update(self, dt: float) -> None:\n'
    '        """Main update entry point - called every frame from OpenPlayEngine.\n'
    '\n'
    '        Delegates to attack/defence/marker target assignment methods\n'
    '        based on current game state.\n'
    '        """\n'
    '        print("[DEBUG] MovementSystem.update running")\n'
    '\n'
    '\n'
    '# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\n'
    '# TACKLE INITIATOR'
)

old_text = marker1_start + marker1_end.lstrip('\n')
search1 = '            p.state = PlayerState.CHASE_BALL\n' + '\n' + '\n' + '# \u2550' * 69 + '\n# TACKLE INITIATOR'

idx1 = content.find(search1)
if idx1 >= 0:
    content = content[:idx1] + new_method + content[idx1 + len(search1):]
    print("Edit 1: OK")
else:
    print("Edit 1: marker NOT FOUND")
    idx = content.find("TACKLE INITIATOR")
    if idx >= 0:
        print("  Found TACKLE INITIATOR at", idx)
        print("  Before:", repr(content[idx-200:idx]))
    raise SystemExit(1)

# --- Edit 2: Add self.movement.update(dt) in OpenPlayEngine.update() ---
search2 = '        assert id(self.world) == self._world_id'
if search2 in content:
    repl2 = search2 + '\n\n        # Call MovementSystem.update() each frame for debug visibility\n        self.movement.update(dt)'
    content = content.replace(search2, repl2, 1)
    print("Edit 2: OK")
else:
    print("Edit 2: marker NOT FOUND")
    raise SystemExit(1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("\nVerification:")
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()
for i, l in enumerate(lines):
    if "MovementSystem" in l or "DEBUG" in l:
        print(f"  Line {i+1}: {l.rstrip()}")
print(f"Total lines: {len(lines)}")
