"""Restructure update() pipeline order: SDM -> role-movement -> freeze."""
import sys

with open("world/open_play_engine.py", encoding="utf-8") as f:
    lines = f.readlines()

# Step 1: find the exact line ranges we need to reorder
# We need to find the update() method's post-dispatch section.
# Lines around 620-680 contain the three blocks.
# Strategy: identify by code content not comment content.

# Find "self._apply_defence_sdm(dt)" line — this is the anchor
sdm_call_idx = None
freeze_start_idx = None
role_start_idx = None
role_end_idx = None

for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped == "self._apply_defence_sdm(dt)":
        sdm_call_idx = i
    if stripped.startswith("for p in self.world.players:"):
        freeze_start_idx = i
    if "apply_role_movement" in stripped:
        role_start_idx = i - 6  # the if-self._phase not in line
    if role_start_idx and i > role_start_idx and stripped.startswith("for d in self.world.defending_team:"):
        role_end_idx = i + 5  # the last line of the for loop + else block

if not all(x is not None for x in [sdm_call_idx, role_start_idx, role_end_idx, freeze_start_idx]):
    print(f"Could not find all markers: sdm={sdm_call_idx}, role_start={role_start_idx}, role_end={role_end_idx}, freeze={freeze_start_idx}")
    sys.exit(1)

print(f"sdm_call={sdm_call_idx}, role_start={role_start_idx}, role_end={role_end_idx}, freeze={freeze_start_idx}")

# The current order (lines inclusive):
#   role_start .. role_end      = role-movement pipeline
#   (gap) sdm_call              = SDM call
#   freeze_start .. (next blank) = freeze enforcement
#
# Desired order:
#   sdm_call                     = SDM call first
#   role_start .. role_end       = role-movement pipeline second
#   freeze_start .. (next blank) = freeze enforcement third

# Extract the blocks
role_block = lines[role_start_idx:role_end_idx+1]
sdm_block = lines[sdm_call_idx:sdm_call_idx+1]
freeze_block_start = freeze_start_idx
# Find the end of freeze block (next blank line or next section)
freeze_end_idx = freeze_start_idx
for j in range(freeze_start_idx, min(freeze_start_idx + 10, len(lines))):
    if lines[j].strip() == "" and j > freeze_start_idx:
        freeze_end_idx = j
        break
freeze_block = lines[freeze_start_idx:freeze_end_idx]

print(f"role_block: lines {role_start_idx+1}-{role_end_idx+1} ({len(role_block)} lines)")
print(f"sdm_block: line {sdm_call_idx+1} (1 line)")
print(f"freeze_block: lines {freeze_start_idx+1}-{freeze_end_idx} ({len(freeze_block)} lines)")

# Now we need to understand the actual content of each
print("\nRole block first 3 lines:")
for l in role_block[:3]:
    print(f"  {l.rstrip()}")
print("\nSDM call line:")
print(f"  {sdm_block[0].rstrip()}")
print("\nFreeze block:")
for l in freeze_block:
    print(f"  {l.rstrip()}")

# The comments before each block also need to move
# The role-movement block starts with the comment about "Role-based movement pipeline"
# preceded by a blank line. The SDM call has a comment block before it.
# The freeze block has a comment block before it.
# We need to find the exact start of each comment block.

# Let's navigate upward to capture full comment blocks
def find_block_start(start_idx, min_idx):
    """Walk up from start_idx to find the first blank-line boundary or comment block start."""
    idx = start_idx
    while idx > min_idx:
        prev = lines[idx - 1].strip()
        if prev == "" or prev.startswith("#"):
            idx -= 1
        else:
            break
    return idx

role_full_start = find_block_start(role_start_idx, 0)
sdm_full_start = find_block_start(sdm_call_idx, role_full_start)
freeze_full_start = find_block_start(freeze_start_idx, sdm_full_start)

print(f"\nFull blocks after expansion:")
print(f"role_full: lines {role_full_start+1}-{role_end_idx+1}")
print(f"sdm_full: lines {sdm_full_start+1}-{sdm_call_idx+1}")
print(f"freeze_full: lines {freeze_full_start+1}-{freeze_end_idx+1}")

# Extract full blocks
role_full = lines[role_full_start:role_end_idx+1]
sdm_full = lines[sdm_full_start:sdm_call_idx+1]
freeze_full = lines[freeze_full_start:freeze_end_idx]

# Reconstruct: keep everything before the first block, then reorder
before = lines[:role_full_start]
after = lines[freeze_end_idx:]

# Ensure no blank line overlap — check if 'after' starts with blank and trim
while after and after[0].strip() == "":
    after = after[1:]

# Build new order: [old SDM block] + [old role block] + [old freeze block]
reordered = sdm_full + [""] + role_full + [""] + freeze_full

# But wait — we also need to update the comments to reflect new order.
# For now, let's just change the comments.

# Simpler approach: just reassign the order
# Remove the last blank line from each block to avoid double-blanks
while role_full and role_full[-1].strip() == "":
    role_full = role_full[:-1]
while sdm_full and sdm_full[-1].strip() == "":
    sdm_full = sdm_full[:-1]
while freeze_full and freeze_full[-1].strip() == "":
    freeze_full = freeze_full[:-1]

# Simplify: just cut and paste the whole region
total_replaced = lines[role_full_start:freeze_end_idx]
print(f"\nTotal region to replace: lines {role_full_start+1}-{freeze_end_idx} ({len(total_replaced)} lines)")

new_region = sdm_full + [""] + role_full + [""] + freeze_full + [""]
print(f"New region: {len(new_region)} lines")

new_lines = before + new_region + after

with open("world/open_play_engine.py", "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print("FIX 1 applied: SDM -> role-movement -> freeze order")
