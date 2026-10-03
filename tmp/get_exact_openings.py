with open('world/open_play_engine.py','r',encoding='utf-8') as f:
    lines = f.readlines()

# _prepare_release at line 1538 (0-indexed 1537)
print("=== _prepare_release (lines 1537-1555) ===")
print(''.join(lines[1537:1555]))

# _update_release at line 1740 (0-indexed 1739)
print("=== _update_release (lines 1739-1760) ===")
print(''.join(lines[1739:1760]))

# _update_ptb_prep at line 1932 (0-indexed 1931)
print("=== _update_ptb_prep (lines 1931-1960) ===")
print(''.join(lines[1931:1960]))

# _update_ptb at line 2118 (0-indexed 2117)
print("=== _update_ptb (lines 2117-2140) ===")
print(''.join(lines[2117:2140]))
