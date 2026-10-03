"""Apply BC attack_role = "ball_carrier" patches at exact line numbers."""
import re

with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

patches = []

# Patch 1: _prepare_release (line 1537). After bc guard at line 1563 (0-indexed 1562)
# Context: lines 1560-1563 are:
#   bc = self.ball_carrier
#   if bc is None:
#       return
# Insert new line after line 1563 (0-indexed 1563 = return line)
patches.append((1563, '        bc.attack_role = "ball_carrier"\n'))

# Patch 2: _update_release (line 1738). Insert after docstring close (""").
# The docstring ends at line 1750. Line 1751 is: print("_update_release FIRING...")
# Insert after line 1750 (the """ line), before the print
patches.append((1750, '        bc = self.world.ball_carrier\n        bc.attack_role = "ball_carrier"\n'))

# Patch 3: _update_ptb_prep (line 1927). Docstring ends at line 1937.
# Line 1938 starts: "# PTB gate safety override..."
# Insert after line 1937 (""")
patches.append((1937, '        bc = self.world.ball_carrier\n        if bc is not None:\n            bc.attack_role = "ball_carrier"\n'))

# Patch 4: _update_ptb_exec (line 1996). Docstring ends at line 2005.
# Line 2006 starts: "# PTB gate safety override..."
# Insert after line 2005 (""")
patches.append((2005, '        bc = self.world.ball_carrier\n        if bc is not None:\n            bc.attack_role = "ball_carrier"\n'))

# Patch 5: _update_ptb (line 2107). Docstring ends at line 2119.
# Line 2120 starts: "dh = self.dh"
# Insert after line 2119 (""")
patches.append((2119, '        bc = self.world.ball_carrier\n        if bc is not None:\n            bc.attack_role = "ball_carrier"\n'))

# Apply patches in reverse order (so line numbers don't shift)
patches.sort(key=lambda x: x[0], reverse=True)

for line_num, insert_text in patches:
    idx = line_num - 1  # 0-indexed
    # Verify the line we're inserting after
    print(f"Line {line_num}: {lines[idx].rstrip()}")
    lines.insert(idx + 1, insert_text)
    print(f"  -> Inserted after line {line_num}")

with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("\nAll patches applied successfully!")
