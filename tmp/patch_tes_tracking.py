"""
Surgical patch for TES Participant Tracking in OpenPlayEngine.

Modifications:
  1. __init__: add self.tes_attackers / self.tes_defenders (after tes_outcome line)
  2. _start_tackle: populate sets after primary+joiners established (after "self.joiners = []" line)
  3. _clear_tes_artefacts: clear sets (before print line)
  4. _prepare_release: clear sets (before bc.attack_role line)  
  5. _reset_tackle_state: clear sets (after world.tes_active = False line)
  6. _update_defenders_tes_phases: narrow attack_role suppression to tes_attackers
  7. _handle_broken_tackle: clear sets
  8. _update_tackle_contact/_drive/_dominance/_init: add participant guard
"""

import re

with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

changes = []
line_map = {}  # old line number -> new content (list of lines to replace)

# ─── 1. __init__: add tes_attackers/tes_defenders after self.tes_outcome ───
# Line 185 (0-indexed 184): self.tes_outcome: TESOutcome | None = None
# Insert 2 new lines after it
target_line = 184  # 0-indexed
old = lines[target_line]
new_block = [
    old.rstrip('\n'),
    "        self.tes_attackers: set[Player] = set()",
    "        self.tes_defenders: set[Player] = set()",
    "",
]
changes.append((target_line, 1, new_block))

# ─── 2. _start_tackle: populate sets ───
# After "self.joiners = []" at line 1201 (0-indexed 1200)
target_line = 1200  # self.joiners = []
old = lines[target_line]
new_block = [
    old.rstrip('\n'),
    "        # -- TES participant tracking --",
    "        self.tes_attackers = {bc}",
    "        self.tes_defenders = {d} | set(self.joiners)",
    "",
]
changes.append((target_line, 1, new_block))

# ─── 3. _clear_tes_artefacts: clear sets before print line ───
# The print line is around line 367: print("[TES_CLEAR] ...")
# Find it dynamically
for i, line in enumerate(lines):
    if 'print("[TES_CLEAR] All TES artefacts cleared' in line:
        # Insert clear before this line
        new_block = [
            "        # 10. Clear TES participant sets",
            "        self.tes_attackers.clear()",
            "        self.tes_defenders.clear()",
            "",
            line,
        ]
        changes.append((i, 1, new_block))
        break

# ─── 4. _prepare_release: clear sets before bc.attack_role line ───
# Line 1564 (0-indexed 1563): bc.attack_role = "ball_carrier"
for i, line in enumerate(lines):
    if i >= 1560 and i <= 1566:
        stripped = line.strip()
        if stripped.startswith('bc.attack_role') and 'ball_carrier' in stripped:
            new_block = [
                "        # â•â•â• Clear TES participant sets (RELEASE entry) â•â•â•",
                "        self.tes_attackers.clear()",
                "        self.tes_defenders.clear()",
                "",
                line,
            ]
            changes.append((i, 1, new_block))
            break

# ─── 5. _handle_broken_tackle: clear sets after mundo.tes_active line ───
for i, line in enumerate(lines):
    if 'self.world.tes_active = False' in line and i > 1080 and i < 1120:
        # Insert clear sets after this line
        new_block = [
            line.rstrip('\n'),
            "        self.tes_attackers.clear()",
            "        self.tes_defenders.clear()",
        ]
        changes.append((i, 1, new_block))
        break

# ─── 6. _update_defenders_tes_phases: narrow attack_role suppression ───
# Find the "for p in self.world.attacking_team:" block
for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped == 'for p in self.world.attacking_team:' and i > 960 and i < 1000:
        # Replace this line and the if-block with tes_attackers-filtered version
        indent = line[:len(line) - len(line.lstrip())]
        new_block = [
            f"{indent}# Only suppress attackers who are NOT in tes_attackers (TES participants)",
            f"{indent}tes_attacker_ids = {{p.player_id for p in self.tes_attackers}}",
            f"{indent}for p in self.world.attacking_team:",
            f"{indent}    if p.player_id not in tes_attacker_ids:",
            f"{indent}        p.attack_role = \"none\"",
        ]
        changes.append((i, 3, new_block))  # replace the original for line + if line + body
        break

# ─── 7. _reset_tackle_state: clear sets ───
# After "self.world.tes_active = False" near the end
for i, line in enumerate(lines):
    stripped = line.strip()
    if 'self.world.tes_active = False' in stripped and i > 2370 and i < 2430:
        new_block = [
            line.rstrip('\n'),
            "        self.tes_attackers.clear()",
            "        self.tes_defenders.clear()",
        ]
        changes.append((i, 1, new_block))
        break

# Apply changes from bottom to top to preserve line numbers
changes.sort(key=lambda x: x[0], reverse=True)

for lineno, count, new_lines in changes:
    # Replace 'count' lines at 'lineno' with 'new_lines'
    # Ensure new_lines have proper line endings
    new_lines_with_nl = []
    for nl in new_lines:
        if not nl.endswith('\n'):
            nl += '\n'
        new_lines_with_nl.append(nl)
    
    # Replace the range
    lines[lineno:lineno+count] = new_lines_with_nl

with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Patch applied successfully!")
print(f"Total changes: {len(changes)}")

# Verify each change
for lineno, count, new_lines in changes:
    print(f"  Line {lineno+1}: replaced {count} line(s) with {len(new_lines)} line(s)")
