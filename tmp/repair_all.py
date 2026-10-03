"""Repair open_play_engine.py by extracting the three fixes and applying them to the ORIGINAL file.
This script reads the original source that was fully read during exploration."""

# The original file content can be found in the exploration output.
# Let me apply the three targeted fixes directly.

with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# ============================================================
# FIX 1: Replace the tackle trigger with pre-tackle version
# ============================================================
old_trigger = (
    '        # Tackle trigger (preserved from old code)\n'
    '        # \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\n'
    '        for p in defenders:\n'
    '            if p is bc:\n'
    '                continue\n'
    '            dist = math.hypot(bc.x - p.x, bc.y - p.y)\n'
    '            if dist < TACKLE_RADIUS_M:\n'
    '                print(f"[TACKLE] defender {p.player_id} hit BC at dist={dist:.2f}")\n'
    '                self._start_tackle(p, bc)\n'
    '                return\n'
)

new_trigger = (
    '        # \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\n'
    '        # Tackle trigger with pre-tackle check (MISS / ARM_GRAB / CONTACT)\n'
    '        # \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\n'
    '        for p in defenders:\n'
    '            if p is bc:\n'
    '                continue\n'
    '            dist = math.hypot(bc.x - p.x, bc.y - p.y)\n'
    '            if dist < TACKLE_RADIUS_M:\n'
    '                pre_result = self._check_pre_tackle(bc, p)\n'
    '                if pre_result == "MISS":\n'
    '                    print(f"[PRE_TACKLE] MISS - defender {p.player_id} whiffs")\n'
    '                    continue\n'
    '                elif pre_result == "ARM_GRAB":\n'
    '                    print(f"[PRE_TACKLE] ARM_GRAB - defender {p.player_id} grabs, BC slowed")\n'
    '                    bc.vx *= 0.85\n'
    '                    bc.vy *= 0.85\n'
    '                    continue\n'
    '                else:\n'
    '                    print(f"[TACKLE] defender {p.player_id} hit BC at dist={dist:.2f}")\n'
    '                    self._start_tackle(p, bc)\n'
    '                    return\n'
)

# Use simpler matching - find by comment text
if 'Tackle trigger (preserved from old code)' in content:
    print("FIX 1: Found old trigger")
    # Find the exact range
    idx_start = content.find('# Tackle trigger (preserved from old code)')
    # Find the blank line after "return" before the next def
    idx_end = content.find('\n\n    def _update_defenders_tes_phases', idx_start)
    content = content[:idx_start] + '        # \xe2\x95\x90' + content[idx_start+9:idx_end] + '\n' + content[idx_end:] 
    # This won't work either due to encoding
    print("Encoding issue - aborting this approach")

# Instead, let's just use a pure string approach with ASCII-only matching
# Find the positions
idx_tackle_comment = content.find('# Tackle trigger (preserved from old code)')
print(f"idx_tackle_comment={idx_tackle_comment}")

# Show the line
end_of_line = content.find('\n', idx_tackle_comment)
print(f"Line: {content[idx_tackle_comment:end_of_line]}")
