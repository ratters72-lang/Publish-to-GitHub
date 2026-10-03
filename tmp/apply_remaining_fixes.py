"""
Apply remaining movement authority fixes using function detection for robustness.
"""
with open('world/open_play_engine.py','r',encoding='utf-8') as f:
    lines = f.readlines()

changes = 0

# FIX 5: Remove _d.update(dt) P4 from RELEASE section
for i, l in enumerate(lines):
    if '_d.update(dt)' in l and 'P4: apply retreat' in l:
        # check if we're in RELEASE by looking upward
        in_release = False
        for j in range(i-1, max(0, i-30), -1):
            stripped = lines[j].strip()
            if stripped.startswith('def _update_release'):
                in_release = True
                break
            if stripped.startswith('def _update'):
                break
        if in_release:
            indent = l[:len(l)-len(l.lstrip())]
            lines[i] = f'{indent}# release_controller owns RELEASE movement\n'
            changes += 1
            print(f'FIX 5: line {i+1} - removed _d.update from RELEASE')

# FIX 3 & 2393: Remove apply_vector(bc) from _update_post_ruck_open_play
in_post_ruck = False
for i, l in enumerate(lines):
    if l.strip().startswith('def _update_post_ruck_open_play'):
        in_post_ruck = True
        continue
    if in_post_ruck and l.strip().startswith('def _update_'):
        in_post_ruck = False
        continue
    if in_post_ruck and 'self.attack_controller.apply_vector(bc, self.world, dt)' in l:
        indent = l[:len(l)-len(l.lstrip())]
        lines[i] = f'{indent}pass  # DRA sole movement owner for POST_RUCK_OPEN_PLAY\n'
        changes += 1
        print(f'FIX 3: line {i+1} - removed apply_vector(bc) from post_ruck')
        # Remove following bc.update(dt) if any
        if i+1 < len(lines) and 'bc.update(dt)' in lines[i+1]:
            indent2 = lines[i+1][:len(lines[i+1])-len(lines[i+1].lstrip())]
            lines[i+1] = f'{indent2}pass  # DRA handles BC movement\n'
            changes += 1
            print(f'FIX 3b: line {i+2} - removed bc.update from post_ruck')

# FIX 7: Remove apply_vector(dh) from _update_ptb
in_ptb = False
for i, l in enumerate(lines):
    if l.strip().startswith('def _update_ptb'):
        in_ptb = True
        continue
    if in_ptb and l.strip().startswith('def _update_'):
        in_ptb = False
        continue
    if in_ptb and 'self.attack_controller.apply_vector(dh, self.world, dt)' in l:
        indent = l[:len(l)-len(l.lstrip())]
        lines[i] = f'{indent}pass  # PTBManager sole movement owner for PTB\n'
        changes += 1
        print(f'FIX 7: line {i+1} - removed apply_vector(dh) from PTB')
        # Remove following dh.update(dt) if any
        if i+1 < len(lines) and 'dh.update(dt)' in lines[i+1]:
            indent2 = lines[i+1][:len(lines[i+1])-len(lines[i+1].lstrip())]
            lines[i+1] = f'{indent2}# PTBManager handles DH movement\n'
            changes += 1
            print(f'FIX 7b: line {i+2} - removed dh.update from PTB')

print(f'\nTotal changes: {changes}')
if changes:
    with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print('File written successfully.')
else:
    print('No changes needed.')

# Final verification
c = ''.join(lines)
checks = [
    ('post_ruck apply_vector(bc)', 'self.attack_controller.apply_vector(bc, self.world, dt)'),
    ('ptb apply_vector(dh)', 'self.attack_controller.apply_vector(dh, self.world, dt)'),
    ('release _d.update P4', '_d.update(dt)  # P4: apply retreat movement'),
]
all_ok = True
for label, pat in checks:
    idx = c.find(pat)
    if idx >= 0:
        # Find which function
        func_start = c.rfind('def ', 0, idx)
        if func_start >= 0:
            func_line = c[func_start:c.find('\n', func_start)]
        else:
            func_line = '???'
        print(f'WARNING: {label} still present! ({func_line})')
        all_ok = False
    else:
        print(f'OK: {label} removed')

if all_ok:
    print('\nAll movement authority fixes applied successfully!')
