"""Find current line numbers and apply all 5 patches."""
with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the 5 methods by their def signatures
method_lines = {}
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    for name in ['_prepare_release', '_update_release', '_update_ptb_prep', '_update_ptb_exec', '_update_ptb']:
        if stripped.startswith(f'def {name}('):
            method_lines[name] = i

print("Current method positions:")
for name, line_no in sorted(method_lines.items(), key=lambda x: x[1]):
    print(f"  {name}: line {line_no}")

# Now find each docstring close (""") after each def
import re
patches = []

for name, ins_key in [
    ('_prepare_release', 'after_return'),
    ('_update_release', 'after_docstring'),
    ('_update_ptb_prep', 'after_docstring'),
    ('_update_ptb_exec', 'after_docstring'),
    ('_update_ptb', 'after_docstring'),
]:
    start_line = method_lines[name]
    # Scan from start_line to find docstring close (""") 
    # The def line itself is start_line
    # Usually the docstring is multi-line """ ... """
    found_open = False
    found_close = False
    for i in range(start_line, min(start_line + 50, len(lines) + 1)):
        idx = i - 1
        raw = lines[idx]
        if '"""' in raw:
            if not found_open:
                found_open = True
                # Check if docstring closes on same line (single-line docstring)
                if raw.count('"""') >= 2:
                    found_close = True
                    doc_end = i
                    break
            else:
                found_close = True
                doc_end = i
                break
    
    if not found_close:
        print(f"ERROR: Could not find docstring end for {name}")
        continue
    
    if name == '_prepare_release':
        # For _prepare_release, insert after the 'return' guard line that follows the docstring
        # Lines after doc_end: bc = self.ball_carrier; if bc is None: return
        scan_i = doc_end + 1
        while scan_i < len(lines) + 1:
            raw = lines[scan_i - 1].strip()
            if raw == 'return':
                # Insert after this return
                patches.append((name, scan_i, '        bc.attack_role = "ball_carrier"\n'))
                break
            scan_i += 1
    else:
        # For the other 4, insert after the docstring close """, before any code
        # For _update_release: the line after docstring close is print(...)
        # For PTB methods: the line after docstring close is code
        # Insert a blank line and then the assignment
        indent = '        '
        if name == '_update_release':
            insert = indent + 'bc = self.world.ball_carrier\n' + indent + 'bc.attack_role = "ball_carrier"\n'
        else:
            insert = indent + 'bc = self.world.ball_carrier\n' + indent + 'if bc is not None:\n' + indent + '    bc.attack_role = "ball_carrier"\n'
        patches.append((name, doc_end, insert))

print("\nPatches to apply:")
for name, line_no, text in patches:
    print(f"  After line {line_no} ({name}): {text.strip()[:60]}...")

# Apply in reverse
patches.sort(key=lambda x: x[1], reverse=True)
for name, line_no, text in patches:
    lines.insert(line_no, text)
    print(f"  Applied: {name} after line {line_no}")

with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("\nDone! All patches applied.")
