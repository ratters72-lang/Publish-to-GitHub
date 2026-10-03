"""Delete stale block from line 1120 to the next blank-line-separated method."""
with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Lines 1120-? are stale code from old _check_break_tackle that survived replacement
# Find the end of the stale block 
stale_start = 1119  # 0-indexed
stale_end = stale_start

for i in range(stale_start + 1, min(len(lines), 1200)):
    line = lines[i]
    # Check if we've reached a blank line followed by a method def  
    if line.strip() == '' and i + 1 < len(lines) and '    def ' in lines[i + 1]:
        stale_end = i
        break
    # Also check if we hit a top-level comment (## border)
    if line.strip().startswith('#') and i > stale_start + 5:
        # Found section header before stale code ends
        stale_end = i
        break
    # Check if the stale block is just indented code followed by a completely different section
    if line.strip() == '' and i > stale_start + 35:
        # Too much stale code - just delete up to next blank-line separated section
        stale_end = i
        break

# Show what we're deleting
print(f"Deleting lines {stale_start+2} to {stale_end+1} ({stale_end - stale_start -1} lines)")
for i in range(stale_start + 1, stale_end):
    print(f"  {i+1}: {repr(lines[i][:80])}")

# Delete the stale lines
if stale_end > stale_start + 1:
    del lines[stale_start + 1:stale_end]
    print(f"Deleted {stale_end - stale_start -1} stale lines")
else:
    print("No stale lines found to delete")

with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
print("File saved")
