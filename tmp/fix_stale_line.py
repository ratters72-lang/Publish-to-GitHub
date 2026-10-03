"""Remove stale line 1120 from the corrupted _check_break_tackle."""
with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print(f"Total lines: {len(lines)}")
# Line 1120 is the stale continuation (0-indexed: 1119)
if len(lines) > 1119:
    problematic = lines[1119]
    print(f"Line 1120: {repr(problematic[:80])}")
    
    # Check if it's the stale break_tackle continuation
    if 'def_resistance' in problematic:
        print("Found stale line, removing...")
        del lines[1119]
        
        with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
            f.writelines(lines)
        print("Removed!")
    else:
        print("Not the expected stale line, showing context:")
        for i in range(1110, min(len(lines), 1130)):
            print(f"{i+1}: {repr(lines[i][:100])}")
else:
    print("File has fewer than 1120 lines")
    for i in range(len(lines)-5, len(lines)):
        print(f"{i+1}: {repr(lines[i][:100])}")
