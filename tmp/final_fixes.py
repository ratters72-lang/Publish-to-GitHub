"""
Apply remaining movement authority fixes (Fixes 3-7) to OPE by
reading the whole file and doing targeted replacements.
"""
import sys

with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

changes = 0
total = len(lines)

# Locate all sections
for i, l in enumerate(lines):
    # FIX 3/4: Remove attack_controller.apply_vector from _update_post_ruck_open_play
    if 'self.attack_controller.apply_vector' in l and 'self._phase' not in l:
        # Check context - find which function we're in
        # Look at previous lines for the method name
        ctx = ''.join(lines[max(0, i-5):i+3])
        if '_update_post_ruck_open_play' in ctx:
            print(f"FIX 3: Removing apply_vector from post_ruck at line {i+1}")
            # Replace this apply_vector call with pass
            indent = l[:len(l) - len(l.lstrip())]
            lines[i] = f"{indent}pass  # DRA is sole movement owner\n"
            changes += 1
    
    # FIX 3 also: Remove the bc.update(dt) that follows apply_vector in post_ruck
    if 'bc.update(dt)' in l:
        ctx_before = ''.join(lines[max(0, i-5):i])
        ctx_after = ''.join(lines[i:i+5])
        if '_update_post_ruck_open_play' in ctx_before + ctx_after:
            # Check if we just removed the apply_vector above it
            for j in range(max(0, i-5), i):
                if 'pass  # DRA is sole' in lines[j]:
                    indent = l[:len(l) - len(l.lstrip())]
                    lines[i] = f"{indent}pass  # DRA handles BC movement\n"
                    print(f"FIX 3b: Removing bc.update from post_ruck at line {i+1}")
                    changes += 1
                    break

    # FIX 4: Remove _move_toward for defenders in post_ruck
    if '_move_toward(d, target_x, d.y, 6.0, dt)' in l:
        indent = l[:len(l) - len(l.lstrip())]
        lines[i] = f"{indent}# _move_toward removed - DRA owns movement\n"
        print(f"FIX 4: Removing _move_toward from post_ruck at line {i+1}")
        changes += 1

    # FIX 5: Remove P7 retreat from _update_release
    if '_d.update(dt)  # P4: apply retreat movement' in l:
        # Check context - find which phase
        ctx = ''.join(lines[max(0, i-6):i+2])
        if 'RELEASE' in ctx:
            # Replace the whole P7 block
            indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            # Set target lines to skip the _d.update call but keep a comment
            lines[i] = f"{indent}# RELEASE: release_controller owns movement\n"
            print(f"FIX 5: Removing P7 retreat _d.update from RELEASE at line {i+1}")
            changes += 1

    # FIX 6: Remove P7 retreat _d.update from PTB phases
    if '_d.update(dt)  # P4: apply movement since _update_defence is skipped' in l:
        ctx = ''.join(lines[max(0, i-6):i+2])
        indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
        lines[i] = f"{indent}# PTB: PTBManager owns movement\n"
        print(f"FIX 6: Removing P7 retreat from PTB at line {i+1}")
        changes += 1

    # FIX 7: Remove attack_controller.apply_vector from _update_ptb
    if 'self.attack_controller.apply_vector(dh, self.world, dt)' in l:
        ctx = ''.join(lines[max(0, i-5):i+3])
        if '_update_ptb' in ctx:
            indent = l[:len(l) - len(l.lstrip())]
            # Remove this line and the following dh.update(dt)
            lines[i] = f"{indent}# PTBManager is sole movement owner during PTB\n"
            print(f"FIX 7: Removing apply_vector from PTB at line {i+1}")
            changes += 1
            # Also suppress dh.update(dt) that follows
            if i+1 < total and 'dh.update(dt)' in lines[i+1]:
                indent2 = lines[i+1][:len(lines[i+1]) - len(lines[i+1].lstrip())]
                lines[i+1] = f"{indent2}# dh.update suppressed - PTBManager owns movement\n"
                print(f"FIX 7b: Removing dh.update from PTB at line {i+2}")
                changes += 1

if changes == 0:
    print("WARNING: No changes were applied!")
else:
    print(f"\n{changes} changes applied. Writing file...")
    with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print("File written successfully!")
