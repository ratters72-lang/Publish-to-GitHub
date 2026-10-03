import re

# File 1
with open('game/defence/defence_lvl_1/defensive_behaviour_unified.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove the ROLES dump block
lines = content.split('\n')
new_lines = []
skip_roles = False

for line in lines:
    if line.strip() == '# ROLE DUMP':
        skip_roles = True
        continue
    if skip_roles:
        if '--- END ROLES ---' in line:
            skip_roles = False
        continue
    new_lines.append(line)

content = '\n'.join(new_lines)

# 2. Remove the TARGET_WRITER diagnostic
lines = content.split('\n')
new_lines = []
skip_target = False
target_count = 0

for line in lines:
    if 'TARGET SOURCE DIAGNOSTIC -- every frame' in line:
        skip_target = True
        target_count = 0
        continue
    if skip_target:
        target_count += 1
        if target_count >= 5:
            skip_target = False
        continue
    new_lines.append(line)

content = '\n'.join(new_lines)

# 3. Remove the DEFENDER TRACE dump block
lines = content.split('\n')
new_lines = []
skip_trace = False

for line in lines:
    if 'PER-DEFENDER TRACE DUMP' in line:
        skip_trace = True
        continue
    if skip_trace:
        if '--- END DEFENDER TRACE ---' in line:
            skip_trace = False
        continue
    new_lines.append(line)

content = '\n'.join(new_lines)

with open('game/defence/defence_lvl_1/defensive_behaviour_unified.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done: unified.py -- removed 3 debug blocks')

# File 2
with open('game/defence/defence_lvl_1/defensive_behaviour_lvl1.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove all 7 [BREAK_TACKLE] print statements
lines = content.split('\n')
new_lines = []
skip_print = False

for line in lines:
    # Check if line starts a [BREAK_TACKLE] print
    stripped = line.strip()
    if stripped.startswith('print(f"[BREAK_TACKLE]') and ('# speed check' in stripped or 
       'def #{defender.player_id} speed=' in stripped or
       'def #{defender.player_id} side-on angle=' in stripped or
       'def #{defender.player_id} rear angle=' in stripped or
       'def #{defender.player_id} momentum=' in stripped or
       'def #{defender.player_id} random fail' in stripped or
       'def #{defender.player_id} BC direction change' in stripped or
       'BC #{bc.player_id} speed=' in stripped):
        skip_print = True
        continue
    
    # Check for continuation lines (after print( that ends with no closing paren)
    if skip_print:
        # If this line closes the print statement or is a continuation
        if ')' in line and not stripped.startswith('print('):
            skip_print = False
            continue
        # Check if it's a continuation (indented, no print, might have closing paren)
        if stripped and 'return False' in stripped:
            skip_print = False
            # This is the return False line - we keep it
            new_lines.append(line)
            continue
        if stripped.endswith(')'):
            skip_print = False
            continue
        continue
    
    new_lines.append(line)

content = '\n'.join(new_lines)

with open('game/defence/defence_lvl_1/defensive_behaviour_lvl1.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done: lvl1.py -- removed all [BREAK_TACKLE] prints')